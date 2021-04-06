#!/usr/bin/env python3
import sys
import curses
import time
import string
from socket import (socket, AF_INET, SOCK_STREAM, IPPROTO_TCP)
from socket import timeout as socket_timeout
from socket import error as socket_error
from errno import EAGAIN
import json


B = 'B'
X = 'X'
O = 'O'



# ---------------------------------------------------------------------------
# The actual game
# ---------------------------------------------------------------------------

class TicTacToe (object):
  def __init__ (self):
    self.ready = False
    self.i_am = X
    self.my_turn = False
    self.selected = [1,1]
    self.new_game = False
    self.name = None # No name set yet
    self.spectate = False

    self.board = B * 9

    self.set(1,1,X)
    self.set(0,0,O)
    self.set(2,2,O)

  def get (self, x, y):
    return self.board[y*3 + x]

  def set (self, x, y, v):
    p = y * 3 + x
    self.board = self.board[:p] + v + self.board[p+1:]

  def check3 (self, t):
    assert len(t) == 3
    if t.count(X) == 3: return X
    if t.count(O) == 3: return O
    return None

  def check_win (self):
    for i in range(3):
      t = self.check3(self.board[i*3:i*3+3])
      if t: return t
      t = self.check3(self.board[i::3])
      if t: return t

    t = self.check3("".join(self.board[x] for x in (0,4,8)))
    if t: return t
    t = self.check3("".join(self.board[x] for x in (2,4,6)))
    if t: return t

    #add_hist(self.board, self.board.count(B))
    if self.board.count(B) == 0: return B
    return None

  def reset (self):
    #self.ready = False
    self.my_turn = False
    self.selected = [1,1]
    self.new_game = False
    self.clear()

  def clear (self):
    for x in range(3):
      for y in range(3):
        self.set(x,y, B)

  def handle_msg (self, private, sender, spectator, kind, msg):
    if kind == 'CHAT':
      add_hist(sender + ": " + msg['msg'])
    elif kind == 'BOARD':
      if self.spectate:
        self.board = msg['board']
    elif kind == 'MOVE':
      if spectator: return
      if self.spectate:
        self.board = msg['board']
        self.set(msg['pos'][0], msg['pos'][1], msg['player'])
        w = self.check_win()
        if w in (X,O):
          add_hist(w, "won!")
        return
      other = X if self.i_am == O else O
      if self.my_turn:
        assert sender == self.name
        piece = self.i_am
        self.my_turn = False
      else:
        if self.new_game: self.clear()
        self.new_game = False
        piece = other
        self.my_turn = True
      x,y = msg['pos']
      self.set(x,y, piece)

      w = self.check_win()
      if w is not None:
        #self.reset()
        was_mine = self.my_turn
        self.my_turn = False
        #self.ready = True
        self.new_game = True
        if w == self.i_am:
          add_hist("You win!")
          add_hist("New game; they go first.")
        elif w == other:
          add_hist("You lose!")
          add_hist("New game; you go first.")
          add_hist("Press Enter to start.")
          self.my_turn = True
        else:
          self.my_turn = not was_mine
          add_hist("It's a draw!")
          add_hist("New game;", "your" if self.my_turn else "their", "turn.")
          if self.my_turn: add_hist("Press Enter to start.")
    else:
      add_hist("Unhandled DATA:", msg)

  def process (self, msgs):
    for msg in msgs:
      try:
        if msg['TYPE'] == 'CONNECT':
          self.reset()
        elif msg['TYPE'] == 'DISCONNECT':
          pass
        elif msg['TYPE'] == 'HELLO':
          net.send(dict(TYPE="HELLO", name=self.name, gamename="ttt"))
        elif msg['TYPE'] == 'ERROR':
          if msg.get("ERR") == "BADNAME":
            self.name = None
            add_hist("That name isn't allowed.  Try another.")
          else:
            add_hist("Disconnecting due to an error. (type: " +
                      str(msg.get('ERR', "Unknnown")) + ")")
          net.close()
        elif msg['TYPE'] == 'WELCOME':
          if self.spectate:
            net.send(dict(TYPE="SPECTATE_GAME"))
          else:
            net.send(dict(TYPE="JOIN_GAME", size=2, allow_spectators=True))
        elif msg['TYPE'] == 'DATA' or msg['TYPE'] == 'PRIV':
          data = msg['msg']
          spectator = msg['SPECTATOR']
          self.handle_msg(msg['TYPE'] == 'PRIV', msg['SENDER'], spectator,
                          data['type'], data)
        elif msg['TYPE'] == 'LEAVE':
          add_hist(msg['user'], "left the game.")
        elif msg['TYPE'] == 'JOIN':
          if msg['user'] != self.name:
            add_hist(msg['user'], "joined.")
        elif msg['TYPE'] == 'SPEC_LEAVE':
          add_hist("Spectator", msg['user'], "left the game.")
        elif msg['TYPE'] == 'SPEC_JOIN':
          if msg['user'] != self.name:
            add_hist("Spectator", msg['user'], "joined.")
            #add_hist(str(msg))
            if msg['YOU_LEAD']:
              # I'm the leader, so I'll send the board so the spectator can
              # see it.
              net.send(dict(TYPE='PRIV', msg=dict(type='BOARD',
                                                  player=self.i_am,
                                                  board=self.board,
                                                  dest=msg['user'])))
          else:
            add_hist("You're now spectating.")
        elif msg['TYPE'] == 'ROOM_STATUS':
          was_ready = self.ready
          self.ready = msg['is_ready']
          if msg['is_ready']:
            if not was_ready:
              self.reset()
              if self.spectate:
                add_hist("The game is afoot!")
              elif msg['YOU_LEAD']:
                add_hist("You're X and you go first!")
                self.i_am = X
                self.my_turn = True
              else:
                add_hist("You're O and you go second.")
                self.i_am = O
                self.my_turn = False
          else:
            add_hist("Waiting for other players...")

        else:
          add_hist("Got unknown message: " + str(msg))
      except Exception as e:
        #add_hist("Error handling network message: " + str(e))
        add_hist("Error handling network message:")
        import traceback
        add_hist(traceback.format_exc())
        net.close()

  def make_move (self):
    if self.new_game and self.my_turn:
      self.new_game = False
      self.clear()
      return
    if self.my_turn:
      if self.get(*self.selected) != B:
        add_hist("You can't move there!")
        return
      net.send(dict(TYPE='DATA', msg=dict(type='MOVE', pos=self.selected,
                                          player=self.i_am,
                                          board=self.board)))


gs = TicTacToe()



# ---------------------------------------------------------------------------
# Generic network/sockets stuff to connect to server
# ---------------------------------------------------------------------------

class Network (object):
  SERVER_ADDRESS = (sys.argv[1] if len(sys.argv)>1 else "127.0.0.1", 9876)

  def __init__ (self):
    self.sock = None
    self.outbuf = b''
    self.inbuf = b''
    self.msgs = []

  @property
  def is_connected (self):
    return self.sock is not None

  def connect (self):
    self.close()

    self.sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP)
    self.sock.settimeout(10)
    add_hist("Attempting to connect...")

    try:
      self.sock.connect(self.SERVER_ADDRESS)
      self.msgs.append(dict(TYPE=u"CONNECT"))
    except Exception:
      add_hist("Couldn't connect.")
      self.close()

  def send (self, data):
    if isinstance(data, dict):
      data = json.dumps(data).encode("utf8") + b"\n"
    if not self.sock:
      self.connect()
      self.outbuf = b''
      return

    self.outbuf += data

  def close (self):
    if not self.sock: return
    try:
      add_hist("Connection closed.")
      self.msgs.append(dict(TYPE=u"DISCONNECT"))
      self.sock.close()
    except Exception:
      pass
    self.sock = None

  def do_communication (self):
    if not self.sock: return []

    msgs = self.msgs
    self.msgs = []

    if self.outbuf:
      self.sock.settimeout(1)
      try:
        sent = self.sock.send(self.outbuf)
        self.outbuf = self.outbuf[sent:]
      except socket_timeout:
        pass
      except socket_error as e:
        if e.errno != EAGAIN: raise
      except Exception:
        add_hist("Socket send error")
        self.close()

    self.sock.settimeout(0)
    try:
      data = self.sock.recv(1024*4)
      #add_hist("Got " + str(data))
      if not data:
        self.close()
        add_hist("Server disconnected.")
      else:
        self.inbuf += data
        while b"\n" in self.inbuf:
          msg,self.inbuf = self.inbuf.split(b"\n", 1)
          msg = json.loads(msg)
          msgs.append(msg)
    except socket_error as e:
      if e.errno != EAGAIN: raise
    except socket_timeout:
      pass
    except Exception as e:
      add_hist("Socket receive error: " + str(e))
      self.close()

    return msgs

net = Network()



# ---------------------------------------------------------------------------
# Below is the UI stuff
# ---------------------------------------------------------------------------

def makechar (s):
  s = s.strip().replace(".", " ").split("\n")
  return s

XC=makechar("""
XX...XX
.XX.XX.
..XXX..
.XX.XX.
XX...XX
""")

OC=makechar("""
.OOOOO.
OO...OO
OO...OO
OO...OO
.OOOOO.
""")

BC=makechar("""
.......
.......
.......
.......
.......
""")


# Map from char to the "graphics" for the char
chars = dict(X = XC, O = OC, B = BC)

# Character "graphics" width/height
CW = len(XC[0])
CH = len(XC)

# Used for tracking if stuff needs to be redraw, basically
all_dirty = False
oldsize = (-1,-1)
oldhist = []


def mvrz (win, nlines, ncols, begin_y, begin_x):
  """
  Move and resize a window
  """
  try:
    win.resize(1,1)
    win.mvderwin(begin_y, begin_x)
    win.mvwin(begin_y, begin_x)
    win.resize(nlines, ncols)
    #win.mvderwin(begin_y, begin_x)
    #win.mvwin(begin_y, begin_x)
  except Exception:
    pass


def addstr (win, *args, **kw):
  """
  Write text into window
  """
  try:
    return win.addstr(*args, **kw)
  except Exception:
    pass


def send_chat ():
  """
  Handle entry in the text input box
  """
  global entrytext

  if not net.is_connected and gs.name:
    net.connect()
    return

  if not entrytext:
    # Maybe make a move!
    gs.make_move()
    return

  if not gs.name:
    n = entrytext.strip()
    if n:
      if n.startswith("?"):
        n = n[1:].strip()
        gs.spectate = True
      gs.name = n
      add_hist("")
      add_hist("Hello, " + n + "!")
      net.connect()
  else:
    #add_hist(entrytext)
    try:
      net.send(dict(TYPE="DATA", msg=dict(type='CHAT', msg=entrytext)))
    except Exception:
      pass

  entrytext = ''


def add_hist (*msg):
  """
  Add to the chat log / history
  """
  msg = " ".join(str(s) for s in msg)
  if '\n' in msg:
    for m in msg.split("\n"):
      add_hist(m)
    return
  chat.append(msg)
  #debug(msg)
  while len(chat) > 100:
    del chat[0]


# How many times have they pressed ESC?
esc = 0

# Chat log
chat = []

# Text they're entering
entrytext = ''


add_hist("""
Welcome to Tic-Tac-Toe.
Start by entering your name in the chat entry
box and pressing Enter.  Start your name with
a question mark to spectate.

When connected to the server, use arrow keys
to select a square, and Enter to make a move.
Press the Escape key twice to exit.
""".rstrip())


def main (scr):
  history = scr.subwin(1,1)
  entry = scr.subwin(1,1)

  def handle_resize ():
    rows,cols = scr.getmaxyx()
    global alldirty, oldsize, oldhist
    if oldsize != (rows,cols):
      alldirty = True
      oldsize = (rows,cols)
      oldhist = []

    left = 3*(CW+2)+2+2

    mvrz(history, rows-3, cols-left, 0, left)

    mvrz(entry, 3, cols-left, rows-3, left)
    history.clear()
    entry.clear()
    history.border()
    entry.border()

  def draw ():
    rows,cols = scr.getmaxyx()
    #scr.clear()
    for y in range(3):
      for x in range(3):
        rest = ()
        if gs.my_turn and (gs.selected == [x,y]) and not gs.new_game:
          if divmod(time.time() * 2, 1)[1] > 0.5:
            rest = (curses.A_REVERSE,)
        ch = chars[gs.get(x, y)]
        for i,c in enumerate(ch):
          addstr(scr, y*(CH+3) + 1 + i, x*(CW+3) + 1, c, *rest)

    scr.attron(curses.A_REVERSE)
    scr.vline(0,   (CW+2), ' ', 4*CH+3)
    scr.vline(0, 2*(CW+2)+1, ' ', 4*CH+3)
    scr.hline((CH+2),   0, ' ', 3*(CW+2)+2)
    scr.hline(2*(CH+2)+1, 0, ' ', 3*(CW+2)+2)
    scr.attroff(curses.A_REVERSE)

    def drawhist ():
      # This could be done much better...
      rows,cols = history.getmaxyx()
      #history.clear()
      history.box()

      out = []
      def add (line, force=True):
        #if not line: return
        w = cols-2

        while len(line) > w:
          pre = line[:w]
          line = line[w:]
          add(pre, force=True)
        if line or force: out.append(line)

      data = chat[-(rows-1):]
      for line in data:
        add(line)

      global oldhist
      if out == oldhist: return

      oldhist = list(out)

      for i in range(rows-2):
        if i >= len(out): continue
        ch = out[-(i+1)][:cols-2]
        addstr(history, rows-i-2, 1, ch)

      history.noutrefresh()

    def drawchat ():
      wrows,wcols = entry.getmaxyx()
      #entry.clear()
      entry.box()
      y = rows - 2
      x = 3*(CW+2)+2+2+1

      l = wcols - 3
      if l >= 1:
        e = entrytext
        e = e[-l:]
        addstr(scr, y, x, e)
        if len(e) < l:
          addstr(scr, y, x+len(e), " " * (l-len(e)+1))
        try:
          scr.move(y,x+len(e))
        except Exception:
          pass
      #entry.move(1,len(entrytext))


    try:
      handle_resize()
    except Exception:
      pass

    drawhist()

    scr.noutrefresh()
    global alldirty
    if alldirty:
      alldirty = False
      history.noutrefresh()
    curses.doupdate()

    drawchat()

  scr.clear()
  scr.leaveok(False)
  curses.curs_set(1)
  curses.halfdelay(1)

  def handle_key (k):
    global entrytext
    global esc

    if k in ('\b', '\x7f', "KEY_BACKSPACE"):
      esc = 0
      entrytext = entrytext[:-1]
      return

    if k == '\n':
      esc = 0
      send_chat()
      return

    if len(k) == 1:
      if k == '\x1b':
        esc += 1
        return None if esc < 2 else False

      esc = 0

      if k in string.printable:
        entrytext += k
      return

    if k == "KEY_UP":
      gs.selected[1] -= 1
    elif k == "KEY_DOWN":
      gs.selected[1] += 1
    elif k == "KEY_LEFT":
      gs.selected[0] -= 1
    elif k == "KEY_RIGHT":
      gs.selected[0] += 1

    elif k == "KEY_RESIZE":
      scr.clear()
      history.clear()
      entry.clear()

    gs.selected[0] = min(max(gs.selected[0], 0),2)
    gs.selected[1] = min(max(gs.selected[1], 0),2)

  while True:
    gs.process(net.do_communication())
    try:
      global entrytext
      draw()
      if handle_key(scr.getkey()) is False: break

    except curses.error:
      pass
    except KeyboardInterrupt:
      break


if __name__ == '__main__':
  try:
    curses.wrapper(main)
  except Exception:
    raise
