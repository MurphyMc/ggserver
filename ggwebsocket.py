"""
This module starts a webserver and handles websockets
"""

import posixpath
import base64
import hashlib
import struct
import os
import errno
import threading
import logging

log = logging.getLogger("web")
log.setLevel(logging.INFO)




_base_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "web")

try:
  from SimpleHTTPServer import SimpleHTTPRequestHandler
  from BaseHTTPServer import HTTPServer
  from SocketServer import ThreadingMixIn
  import urllib
  url_unquote = urllib.unquote
except:
  from http.server import SimpleHTTPRequestHandler
  from http.server import HTTPServer
  from socketserver import ThreadingMixIn
  import urllib.parse
  url_unquote = urllib.parse.unquote


try:
  _ = b' '[0] + 1
  # Python3
  _ord = lambda x:x
  _chr = lambda x:bytes([x])
except Exception:
  # Python2
  _ord = ord
  _chr = chr


WS_CONTINUE = 0
WS_TEXT = 1
WS_BINARY = 2
WS_CLOSE = 8
WS_PING = 9
WS_PONG = 10

"""
  def _send_real (self, msg):
    try:
      self.wfile.write(msg)
      self.wfile.flush()
    except Exception:
      try:
        self.server._disconnect()
      except Exception:
        pass
      self._websocket_open = False
      #TODO: reopen?
      pass
"""



def _frame (opcode, msg):
  def encode_len (l):
    if l <= 0x7d:
      return struct.pack("!B", l)
    elif l <= 0xffFF:
      return struct.pack("!BH", 0x7e, l)
    elif l <= 0x7FFFFFFFFFFFFFFF:
      return struct.pack("!BQ", 0x7f, l)
    else:
      raise RuntimeError("Bad length")

  op_flags = 0x80 | (opcode & 0x0F) # 0x80 = FIN
  hdr = struct.pack("!B", op_flags) + encode_len(len(msg))

  return hdr + msg




def _deframer (process, send):
  data = b''
  old_op = None
  hdr = b''
  finished = False
  while not finished:
    while len(hdr) < 2:
      newdata = yield True
      if newdata: hdr += newdata

    flags_op,len1 = struct.unpack_from("!BB", hdr, 0)
    op = flags_op & 0x0f
    flags = flags_op >> 4
    fin = flags & 0x8
    if (len1 & 0x80) == 0: raise RuntimeError("No mask set")
    len1 &= 0x7f
    hdr = hdr[2:]

    while True:
      if len1 <= 0x7d:
        length = len1
        break
      elif len1 == 0x7e and len(hdr) >= 2:
        length = struct.unpack_from("!H", hdr, 0)[0]
        hdr = hdr[2:]
        break
      elif len1 == 0x7f and len(hdr) >= 8:
        length = struct.unpack_from("!Q", hdr, 0)[0]
        hdr = hdr[8:]
        break
      else:
        raise RuntimeError("Bad length")
      hdr += yield True

    while len(hdr) < 4:
      hdr += yield True

    mask = [_ord(x) for x in hdr[:4]]
    hdr = hdr[4:]

    while len(hdr) < length:
      hdr += yield True

    d = hdr[:length]
    hdr = hdr[length:]

    d = b"".join(_chr(_ord(c) ^ mask[i % 4]) for i,c in enumerate(d))

    if not fin:
      if op == WS_CONTINUE:
        if old_op is None: raise RuntimeError("Continuing unknown opcode")
      else:
        if old_op is not None: raise RuntimeError("Discarded partial message")
        old_op = op
      data += d
    else: # fin
      if op == WS_CONTINUE:
        if old_op is None: raise RuntimeError("Can't continue unknown frame")
        op = old_op
      d = data + d
      old_op = None
      data = b''
      if op == WS_TEXT: d = d.decode('utf8')

      if op in (WS_TEXT, WS_BINARY):
        if d: process(op, d)
      elif op == WS_PING:
        msg = _frame(WS_PONG, d)
        send(msg)
      elif op == WS_CLOSE:
        process(op, None)
        if not finished:
          finished = True
          #TODO: Send close frame?
      elif op == WS_PONG:
        pass
      else:
        pass # Do nothing for unknown type



class Deframer (object):
  def __init__ (self, sock):
    self.sock = sock

  def init (self, process, send):
    self.d = _deframer(process, send)

  def start (self, buffered = None):
    try:
      self.d.send(None)
    except StopIteration:
      pass # PEP 479?

    if buffered: self.d.send(buffered)

  @staticmethod
  def frame (data, opcode = WS_TEXT):
    return _frame(opcode, data)

  def fileno (self):
    return self.sock.fileno()

  def feed (self, data):
    self.d.send(data)



class NullSocket (object):
  closed = True
  @staticmethod
  def shutdown (how=None):
    pass

  @staticmethod
  def flush ():
    pass

  @staticmethod
  def close ():
    pass

  @staticmethod
  def send (self, bytes, flags=None):
    return bytes

  @staticmethod
  def recv (bufsize, flags=None):
    return b''

  @staticmethod
  def read (bufsize):
    return b''

  @staticmethod
  def readline (bufsize):
    return b''

  @staticmethod
  def write (buf):
    return len(buf)

  @staticmethod
  def fileno ():
    return -1



class NullableSocket (object):
  def __init__ (self, socket):
    self._realsocket = socket

  def _make_null (self):
    self._real_socket = NullSocket()

  def __getattr__ (self, name):
    return getattr(self._realsocket, name)



class WebHandler (SimpleHTTPRequestHandler):
  protocol_version = "HTTP/1.1"

  on_new_websocket = None # Set to a function to call

  base_path = _base_path

  serve_files = False

  def translate_path (self, path):
    """
    Translate a web path to a local filesystem path

    This is substantially similar to the one in the base class, but it
    doesn't have an unhealthy relationship with the current working
    directory.
    """
    out_path = self.base_path
    path = path.split('?',1)[0].split('#',1)[0].strip()
    has_trailing_slash = path.endswith('/')
    parts = posixpath.normpath(url_unquote(path)).split('/')
    for part in parts:
      if not part.replace('.',''): continue
      if os.path.dirname(part): continue
      if part == os.curdir: continue
      if part == os.pardir: continue
      out_path = os.path.join(out_path, part)
    if has_trailing_slash: out_path += '/'
    return out_path

  def log_message (self, format, *args):
    log.debug(format, *args)

  def _serve_websocket (self):
    self.close_connection = 0

    log.debug("Upgrading to websocket")
    self.send_response(101, "Switching Protocols")
    k = self.headers.get("Sec-WebSocket-Key", "")
    k += "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    k = k.encode("UTF-8")
    k = base64.b64encode(hashlib.sha1(k).digest())
    k = k.decode("UTF-8")
    self.send_header("Sec-WebSocket-Accept", k)
    self.send_header("Upgrade", "websocket")
    self.send_header("Connection", "Upgrade")
    self.end_headers()
    self.wfile.flush()
    self.close_connection = True

    # I couldn't swear that rfile doesn't have some input buffered.  *Try*
    # to read it out.  When it fails, switch to reading from connection.
    self.connection.settimeout(0)
    buffered = b''
    while True:
      try:
        buffered += self.rfile.read(1)
      except Exception:
        break

    d = Deframer(self.connection._realsocket)

    self.on_new_websocket(d)
    d.start(buffered)

    self.connection._realsocket = NullSocket()
    self.rfile = self.wfile = self.connection

  def do_GET (self):
    if self.headers.get("Upgrade") == "websocket":
      return self._serve_websocket()
    elif self.serve_files:
      return super(WebHandler,self).do_GET()
    else:
      self.send_error(404)



class WebInterface (ThreadingMixIn, HTTPServer):
  def __init__ (self, bind_address, on_new_websocket, base_path = None,
                serve_files = False):
    if base_path is None: base_path = _base_path
    bp = base_path
    onws = on_new_websocket
    sp = serve_files

    class handler (WebHandler):
      base_path = bp
      on_new_websocket = onws
      serve_files = sp

    try:
      HTTPServer.__init__(self, bind_address, handler)

    except OSError as e:
      if e.errno == errno.EADDRINUSE:
        log.error("The webserver could not be started because the listening "
                  "port\nis already in use. "
                  "Try setting a different bind port.")
        raise
      raise

    self.daemon_threads = True

    self.thread = threading.Thread(target = self._start)
    self.thread.daemon = True
    self.thread.start()

    laddr = self.socket.getsockname()
    log.info("Webserver running at http://%s:%s",
             laddr[0],
             laddr[1])

  def get_request (self):
    sock,addr = self.socket.accept()
    return (NullableSocket(sock),addr)

  def _start (self):
    self.serve_forever()
