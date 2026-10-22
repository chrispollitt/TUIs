"""A scripted IRC server on 127.0.0.1 for the chat UI tests (e2e_regress's
chat cases).  Stdlib only, no network.

It registers whoever connects (no IRCv3 capabilities, so tvchat-backend
inserts our own lines itself rather than waiting for an echo), confirms a
JOIN with a topic and a NAMES list, records every line the client sends,
and says whatever a test hands to say().  One connection at a time; a new
one replaces the old (a restarted backend).
"""
import socket
import threading
import time


class FakeIrcd(threading.Thread):
    def __init__(self, topic="the e2e channel"):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(2)
        self.sock.settimeout(0.5)
        self.port = self.sock.getsockname()[1]
        self.topic = topic
        self.got = []                    # every line the client sent
        self.joined = set()              # channels the client is in
        self.nick = "?"
        self.conn = None
        self.lock = threading.Lock()
        self.stopping = False
        self.error = None

    def conf(self, channels="#e2e", display="Fake", nick="tester"):
        """A tvchat.conf that autoconnects one account, `fake`, to us."""
        return ("[chat]\nautoconnect = fake\n\n[account:fake]\nprotocol = irc\n"
                "display = %s\nhost = 127.0.0.1\nport = %d\ntls = false\n"
                "nick = %s\nchannels = %s\n" % (display, self.port, nick, channels))

    # -- the test's side --------------------------------------------------
    def say(self, *lines):
        """Send raw IRC lines to the client (waits up to 10 s for one)."""
        end = time.monotonic() + 10
        while self.conn is None and time.monotonic() < end:
            time.sleep(0.05)
        with self.lock:
            if self.conn is None:
                raise RuntimeError("no client connected")
            self.conn.sendall(b"".join(l.encode("utf-8") + b"\r\n" for l in lines))

    def wait_joined(self, chan, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if chan in self.joined:
                return True
            time.sleep(0.05)
        return False

    def sent(self, prefix):
        """The lines the client sent that start with prefix."""
        return [l for l in list(self.got) if l.startswith(prefix)]

    def stop(self):
        self.stopping = True
        with self.lock:
            if self.conn is not None:
                try:
                    self.conn.close()
                except OSError:
                    pass
        self.sock.close()

    # -- the server -------------------------------------------------------
    def run(self):
        while not self.stopping:
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self.lock:
                self.conn = conn
            try:
                self.session(conn)
            except Exception as e:       # the test looks at .error
                if not self.stopping:
                    self.error = e
            with self.lock:
                if self.conn is conn:
                    self.conn = None
                    self.joined = set()

    def send(self, conn, *lines):
        with self.lock:
            conn.sendall(b"".join(l.encode("utf-8") + b"\r\n" for l in lines))

    def session(self, conn):
        buf = b""
        while not self.stopping:
            while b"\n" not in buf:
                data = conn.recv(4096)
                if not data:
                    return
                buf += data
            raw, buf = buf.split(b"\n", 1)
            ln = raw.decode("utf-8", "replace").rstrip("\r")
            self.got.append(ln)
            cmd = ln.split(" ", 1)[0].upper()
            arg = ln.split(" ", 1)[1] if " " in ln else ""
            if cmd == "CAP":
                self.send(conn, ":srv CAP * LS :")
            elif cmd == "NICK":
                self.nick = arg.strip()
            elif cmd == "USER":
                self.send(conn, ":srv 001 %s :Welcome to the fake network" % self.nick,
                          ":srv 376 %s :End of MOTD" % self.nick)
            elif cmd == "JOIN":
                for ch in arg.split(" ")[0].split(","):
                    self.send(conn, ":%s!u@h JOIN %s" % (self.nick, ch),
                              ":srv 332 %s %s :%s" % (self.nick, ch, self.topic),
                              ":srv 353 %s = %s :%s alice" % (self.nick, ch, self.nick),
                              ":srv 366 %s %s :End of NAMES" % (self.nick, ch))
                    self.joined.add(ch.lower())
            elif cmd == "PING":
                self.send(conn, ":srv PONG srv :%s" % arg.lstrip(":"))
            elif cmd == "QUIT":
                return
