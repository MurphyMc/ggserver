#!/usr/bin/env python3
from socket import *
from socket import error as socketerror
import json

from select import select
import logging
import time
import random

from collections import defaultdict

from ggwebsocket import WebInterface


"""
TODO
----
* Ability to change size of room dynamically / "close" the room.
  Maybe this just means letting the leader flip the ready state?
* Room names / joining specific rooms.
* Room browsing.
* Structure things by gamename.
* Lots of performance fixes and cleanup.
* Better logging.
* Replies include opaque data from client (XID).
* Random bag / escrow anti-cheat features.
* Get rid of PRIV, use DATA with 'user' like, e.g., CHOOSE does?
* Allow PRIV/CHOOSE/etc. to send to a *group* of users?
"""

BIND_PORT = 9876
MAX_CONNECTIONS = 1024

logging.basicConfig()
log = logging.getLogger("main")
log.setLevel(logging.DEBUG)


def do_listen (bind_addr):
  global listener
  listener = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP)
  listener.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
  listener.bind(bind_addr)
  listener.setblocking(False)
  listener.listen(5)
  log.info("Listening on %s:%s", *listener.getsockname())

def init (bind_addr=("", BIND_PORT)):
  log.info("Starting up...")
  global waker, wakewriter
  waker,wakewriter = socketpair()
  waker.setblocking(False)
  wakewriter.setblocking(False)

  do_listen(bind_addr)


def wake ():
  wakewriter.send(b' ')


connections = set()


class ShortError (RuntimeError):
  """
  An exception which shouldn't log a whole stack trace
  """
  #TODO: Actually implement this. :)
  pass


def Msg (TYPE, **kw):
  """
  Craft a message
  """
  m = kw
  m['TYPE'] = TYPE
  return m


class ConnectionBase (object):
  MAX_TIME = 60 * 60 # An hour
  IDLE_TIME = 5 * 60 # 5 minutes

  def __init__ (self, sock):
    sock.setblocking(False)
    self.created_time = time.time()
    self.activity_time = self.created_time
    self.sock = sock
    self.out_buffer = b''
    self.in_buffer = b''
    self.has_quit = False

    if len(connections) >= MAX_CONNECTIONS:
      # Hack to try sending and immediately exit.
      self.send(Msg("ERROR", ERR="TOOMANYCONNECTIONS"))
      try:
        self.do_send()
        self.sock.close()
      except Exception:
        pass
      return

    self.initialize()
    connections.add(self)

  @property
  def is_expired (self):
    t = time.time()
    if t - self.created_time > self.MAX_TIME: return True
    if t - self.activity_time > self.IDLE_TIME: return True
    return False

  def fileno (self):
    # This way we can select() on this Connection
    return self.sock.fileno()

  def send (self, msg):
    return self._send(json.dumps(msg).encode("utf8") + b'\n')

  def _send (self, data):
    if isinstance(data, str):
      data = data.encode("utf8")

    self.out_buffer += data

    if not self.out_buffer: return

    # Try to send now...
    try:
      self.do_send()
    except socketerror:
      pass

    if self.out_buffer: wake()

  def do_send (self):
    d = self.sock.send(self.out_buffer)
    assert d >= 0
    self.out_buffer = self.out_buffer[d:]

  def quit (self, msg = None):
    if self.has_quit: return
    self.has_quit = True
    connections.discard(self)
    if self.sock: self.sock.close()
    self.sock = None
    self.deinitialize()

  def initialize (self):
    pass

  def deinitialize (self):
    pass

  def process (self, data):
    self.activity_time = time.time()
    msg = json.loads(data)
    t = msg["TYPE"]
    f = getattr(self, "_handle_" + t, None)
    if not callable(f):
      raise ShortError("No handler for message type '%s'" % (t,))
    f(msg)

  def do_recv (self):
    data = self.sock.recv(4*1024)
    if not data:
      self.quit()
      return

    self.in_buffer += data
    while b'\n' in self.in_buffer:
      line,self.in_buffer = self.in_buffer.split(b'\n', 1)
      try:
        self.process(line)
      except Exception:
        log.exception("While handling input")
        self.quit()

  def __del__ (self):
    self.quit()
    if connections:
      assert self not in connections


# Rooms waiting to be filled.  Usually will just have 0 or 1 room.
waiting = defaultdict(set)

def clean_waiting (gn=None):
  """
  Clean up waiting list
  """
  if gn:
    if gn in waiting and not waiting[gn]:
      log.info("Discarding waiting rooms for game %s", gn)
      waiting.pop(gn)
    return

  dead = []
  for gn in waiting.keys():
    if not waiting[gn]:
      dead.append(gn)
  for gn in dead:
    waiting.pop(gn)


def get_all_rooms (gamename):
  # Sloppy
  if not gamename: return iter(())
  return iter(set(c.room for c in connections if c.gamename == gamename and c.room))


class Room (object):
  def __init__ (self, gamename, size, allow_spectators):
    self.gamename = gamename
    waiting[gamename].add(self)
    self.members = set()
    self.spectators = set()
    self.room_size = size
    self.allow_spectators = allow_spectators
    self.leaderstate = None

    self.seq = 0
    self.leader = None
    self.was_ready = False

  @property
  def players_needed (self):
    return self.room_size - len(self.members)

  def _update_leader (self, member, join):
    if join:
      if self.leader is None:
        self.leader = member
        log.debug("%s became leader", member.name)
        return True
      return False

    # Leave

    if member != self.leader:
      return False

    old_leader = self.leader.name if self.leader else "None"

    m = [x for x in self.members if x is not member]
    if not m:
      self.leader = None
      log.debug("Leaderless room")
    else:
      self.leader = random.choice(m)
      log.debug("%s became leader because %s left", member.name, old_leader)

    return True

  def join (self, connection):
    assert connection.room is None
    connection.room = self
    self.members.add(connection)
    self._update_leader(connection, True)
    # Tell everyone else this connection joined
    self.send(Msg("JOIN", user=connection.name,
                  leader=connection is self.leader),
              )#ignore=connection)

    # Tell this connection about everyone already here
    # This is a hack and probably isn't necessary since the status
    # message tells you...
    for m in self.members:
      if m is connection: continue
      connection.send(Msg("JOIN", user=m.name, SEQ=self.seq,
                          leader=m is self.leader, initial=True,
                          YOU_LEAD=connection is self.leader))
      self.seq += 1
    for m in self.spectators:
      if m is connection: continue
      connection.send(Msg("SPEC_JOIN", user=m.name, SEQ=self.seq,
                          leader=m is self.leader, initial=True,
                          YOU_LEAD=connection is self.leader))
      self.seq += 1

    if len(self.members) >= self.room_size:
      #self.send("READY")
      waiting[self.gamename].discard(self)
      log.info("Room ready (%s waiting rooms)", len(waiting[self.gamename]))
      clean_waiting(self.gamename)

    self.sendstatus()

  def spectate (self, connection):
    assert connection.room is None
    connection.room = self
    self.spectators.add(connection)

    # Tell everyone else this connection joined
    self.send(Msg("SPEC_JOIN", user=connection.name,
                  leader=connection is self.leader),
              )#ignore=connection)

    # Tell this connection about everyone already here
    # This is a hack and probably isn't necessary since the status
    # message tells you...
    for m in self.members:
      if m is connection: continue
      connection.send(Msg("JOIN", user=m.name, SEQ=self.seq,
                          leader=m is self.leader, initial=True,
                          YOU_LEAD=connection is self.leader))
      self.seq += 1
    for m in self.spectators:
      if m is connection: continue
      connection.send(Msg("SPEC_JOIN", user=m.name, SEQ=self.seq,
                          leader=m is self.leader, initial=True,
                          YOU_LEAD=connection is self.leader))
      self.seq += 1

    self.sendstatus()

  def leave (self, connection):
    if connection in self.spectators:
      self.spectators.discard(connection)
      self.send(Msg("SPEC_LEAVE", user=connection.name,
                    leader=connection is self.leader), ignore=connection)
      self.sendstatus()
      return

    assert connection in self.members
    self.members.discard(connection)
    self._update_leader(connection, False)

    self.send(Msg("LEAVE", user=connection.name,
                  leader=connection is self.leader), ignore=connection)
    if len(self.members) == 0:
      clean_waiting(self.gamename)
    elif len(self.members) < self.room_size:
      # There's SOMEONE here, but it's not full
      if self not in waiting[self.gamename]:
        waiting[self.gamename].add(self)
        log.info("Room going back to waiting (%s waiting rooms)",
                 len(waiting[self.gamename]))

    self.sendstatus()

  @property
  def is_ready (self):
    return len(self.members) >= self.room_size

  def sendstatus (self):
    self.send(Msg("ROOM_STATUS",
                  users = [m.name for m in self.members],
                  is_ready = self.is_ready,
                  was_ready = self.was_ready,
                  leader = self.leader.name if self.leader else None,
                  allow_spectators = self.allow_spectators,
                  spectators = [m.name for m in self.spectators],
                  size = self.room_size))
    self.was_ready = self.is_ready

  def send (self, msg, ignore=None, sender=None, target="SP", dest=None):
    msg = msg.copy()
    msg['SEQ'] = self.seq
    if sender:
      msg['SENDER'] = sender.name
      msg['SPECTATOR'] = sender in self.spectators

    self.seq += 1
    if dest is not None:
      # This is very bad/hacky... ideally, we'd not have private messages
      # go through the room, but we want the SEQ number.  If nothing else,
      # we need to be able in look up members quickly, so it should be a
      # dict, not a set.  But it should be keyed on a real user ID, not
      # the hacky 'name' we have now, so we should take care of that first...
      everyone = set(self.members)
      everyone.update(self.spectators)
      everyone = {x.name:x for x in everyone}
      dest = dest.get(dest)
      if dest:
        msg['YOU_LEAD'] = m is self.leader
        msg['ECHO'] = m is sender
        dest.send(msg)
      return

    def do_sends (group):
      for m in group:
        if m is ignore: continue

        msg['YOU_LEAD'] = m is self.leader
        msg['ECHO'] = m is sender

        # This is a very gross hack
        if msg['TYPE'] == 'ROOM_STATUS':
          if m is self.leader:
            msg['leaderstate'] = self.leaderstate
          else:
            msg['leaderstate'] = None

        m.send(msg)

    if "P" in target: do_sends(self.members)
    if "S" in target: do_sends(self.spectators)


class Connection (ConnectionBase):
  ECHO_SELF = True

  def initialize (self):
    self.room = None
    self.name = None
    self.gamename = None

    #NOTE: name and gamename should always either both be None or both set

    self.send(Msg("HELLO"))

  def deinitialize (self):
    if self.room:
      self.room.leave(self)
      self.room = None

    if self.gamename:
      clean_waiting(self.gamename)

    n = self.name
    if not n: n = "UnknownUser"
    log.info("%s disconnected", n)

  def room_send (self, msg):
    if self.room is None: return
    self.room.send(data, ignore=self)

  def _handle_HELLO (self, msg):
    if self.name: raise ShortError("Already connected")

    gn = msg['gamename']

    data = str(msg.get("name", " ")).split()
    if len(data) != 1:
      self.send(Msg("ERROR", ERR="BADNAME"))
      return
    data = data[0]
    if not data:
      self.send(Msg("ERROR", ERR="BADNAME"))
      return

    # Make sure it's not a duplicate
    if data in [c.name for c in connections]:
      self.send(Msg("ERROR", ERR="BADNAME"))
      return

    self.name = data
    self.gamename = gn
    self.send(Msg("WELCOME"))
    log.info("User %s joined from %s", self.name, self.sock.getpeername())

  def _handle_JOIN_GAME (self, msg):
    if not self.name: raise ShortError("Say HELLO first")

    spectators_ok = msg.get("allow_spectators", False)

    gn = self.gamename
    wait_rooms = waiting[gn]

    size = msg['size']
    if isinstance(size, str):
      size = size.split("-")
      if len(size) == 2:
        lo,hi = size
      elif len(size) == 1:
        lo=hi=size
      else:
        raise ShortError("Bad room size")
      lo = int(lo)
      hi = int(hi)
    else:
      lo=hi=size

    def new_room ():
      r = Room(gamename=self.gamename, size=lo, allow_spectators=spectators_ok)
      log.info("Created new room (%s waiting rooms)", len(wait_rooms))
      r.join(self)

    if not wait_rooms:
      # No waiting rooms; create one of minimum size
      new_room()
      return

    # Filter rooms
    wait_rooms = [r for r in wait_rooms
                  if r.room_size >= lo and r.room_size <= hi]
    wait_rooms = [r for r in wait_rooms
                  if r.allow_spectators == spectators_ok]

    # Find rooms with minimum number of waiting players
    mpn = min(r.players_needed for r in wait_rooms)
    wait_rooms = [r for r in wait_rooms if r.players_needed == mpn]

    if not wait_rooms:
      # No filters matched -- create a new room
      new_room()
      return

    # Pick a random room to join
    room = random.choice(wait_rooms)
    room.join(self)

  def _handle_SPECTATE_GAME (self, msg):
    if not self.name: raise ShortError("Say HELLO first")
    gn = self.gamename

    #TODO: Let you choose/filter rooms

    rooms = get_all_rooms(gn)
    #rooms = list(rooms)
    #print(rooms)
    rooms = [r for r in rooms if r.allow_spectators]
    #print(rooms)

    if not rooms:
      self.send(Msg("ERROR", ERR="NOGAMES"))
      return

    rooms.sort(key=lambda r: r.players_needed)
    mpn = rooms[0].players_needed
    rooms = [r for r in rooms if r.players_needed == mpn]
    room = random.choice(rooms)

    room.spectate(self)

  def _handle_PING (self, msg):
    omsg = msg.copy()
    omsg['TYPE'] = 'PONG'
    self.send(omsg)

  def _handle_PRIV (self, msg):
    if not self.room: raise ShortError("Not in a room")

    ignore = None if self.ECHO_SELF else self

    self.room.send( Msg("PRIV", msg=msg['msg']),
                    ignore=ignore, sender=self, dest=msg.get("user") )

  def _handle_DATA (self, msg):
    # Already in a room
    if not self.room: raise ShortError("Not in a room")

    ignore = None if self.ECHO_SELF else self
    target = msg.get("target", "SP").upper()

    self.room.send( Msg("DATA", msg=msg['msg']),
                    ignore=ignore, sender=self, target=target)

  def _handle_CHOOSE (self, msg):
    # Already in a room
    if not self.room: raise ShortError("Not in a room")

    ignore = None if msg.get("echo", True) else self
    target = msg.get("target", "SP").upper()
    user = msg.get("user", None)
    if 'opts' in msg:
      opts = msg['opts']
    else:
      opts = [m.name for m in self.room.members]

    choice = random.choice(opts)

    omsg = Msg("CHOOSE", echo = msg.get("echo", True))
    if 'msg' in msg: omsg['msg'] = msg['msg'] # msg
    if msg.get("show", False): omsg['opts'] = opts
    omsg['result'] = choice

    self.room.send(omsg, ignore=ignore, sender=self, target=target, dest=user)

  def _handle_RANDINT (self, msg):
    # Already in a room
    if not self.room: raise ShortError("Not in a room")

    ignore = None if msg.get("echo", True) else self
    target = msg.get("target", "SP").upper()
    user = msg.get("user", None)

    lo = msg.get('lo', 0)
    hi = msg['hi']
    count = msg.get("count", 0)
    if not count:
      result = random.randint(lo, hi)
    else:
      count = min(count, 256)
      result = [random.randint(lo, hi) for _ in range(count)]

    omsg = Msg("RANDINT", lo=lo, hi=hi, echo=msg.get("echo", True))
    if 'msg' in msg: omsg['msg'] = msg['msg'] # msg
    omsg['result'] = result

    self.room.send(omsg, ignore=ignore, sender=self, target=target, dest=user)

  def _handle_LEADERSTATE (self, msg):
    # Already in a room
    if not self.room: raise ShortError("Not in a room")

    if self.room.leader is not self: raise ShortError("Not the leader")

    self.room.leaderstate = msg.get("leaderstate")



def main_loop ():
  while True:
    for c in list(connections):
      if c.is_expired:
        try:
          c.quit("Timed out")
        except Exception:
          pass
        connections.discard(c)

    rx_socks = [waker] + [listener] + list(connections)
    w_socks = [c for c in connections if c.out_buffer]
    rr,ww,xx = select(rx_socks, w_socks, rx_socks, 2)

    errored = set()

    for s in xx:
      if s is listener or s is waker:
        log.error("Listen/waker socket error!")
        break
      try:
        s.quit()
      except Exception:
        log.exception("While trying to quit exceptional socket")
        errored.add(s)

    for s in rr:
      if s is listener:
        Connection(s.accept()[0])
      elif s is waker:
        s.recv(4096)
      elif s in connections and s not in errored:
        try:
          s.do_recv()
        except Exception:
          log.exception("While trying to recv")
          errored.add(s)

    for s in ww:
      try:
        s.do_send()
      except Exception:
        log.exception("While trying to send")
        errored.add(s)

    for s in errored:
      try:
        s.quit()
      except Exception:
        pass
      try:
        s.sock.close()
      except Exception:
        pass
      connections.discard(s)



class WSConnection (Connection):
  def __init__ (self, deframer):
    self.deframer = deframer
    deframer.init(self.wsprocess, self._send)

    super(WSConnection, self).__init__(self.deframer.sock)

  def send (self, msg):
    return self._send(self.deframer.frame(json.dumps(msg).encode("utf8") + b'\n'))

  def wsprocess (self, opcode, data):
    if not data: return
    self.process(data.strip())

  def do_recv (self):
    data = self.sock.recv(4*1024)
    if not data:
      self.quit()
      return

    self.deframer.feed(data)



def on_new_ws (_, init):
  WSConnection(init)



def wsinit (bind_address, serve_files):
  global web
  web = WebInterface(bind_address, on_new_ws, serve_files=serve_files)



if __name__ == '__main__':
  import argparse

  p = argparse.ArgumentParser(description="Generic Game Server.")
  p.add_argument("--bind-port", type=int, help="TCP port to bind to",
                 default=BIND_PORT)
  p.add_argument("--bind-addr", type=str, help="IP address to bind to; leave "
                 + "empty to bind to all local addresses", default='')
  p.add_argument("--wsbind-port", type=int, help="TCP port to bind to",
                 default=BIND_PORT+1)
  p.add_argument("--wsbind-addr", type=str, help="IP address to bind to; leave"
                 + " empty to bind to all local addresses", default='')
  p.add_argument("--serve-files", action='store_true', help="If set, the "
                 "web server will serve files from web/ as well as websockets")
  p.add_argument("--log-level", type=str, help="Log level", default="INFO")

  args = p.parse_args()

  level = args.log_level.upper()
  if not level.startswith("_"):
    l = getattr(logging, level, None)
    if isinstance(l, int):
      log.setLevel(l)

  init((args.bind_addr,args.bind_port))
  wsinit((args.wsbind_addr,args.wsbind_port), serve_files=args.serve_files)

  try:
    main_loop()
  except KeyboardInterrupt:
    pass

  while connections:
    c = connections.pop()
    try:
      c.quit()
    except:
      pass

  log.info("Server finished")
