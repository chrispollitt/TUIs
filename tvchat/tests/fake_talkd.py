"""A scripted stand-in for chat-setup's talkd on 127.0.0.1, for the talk UI
tests (e2e_regress's talk cases).  Stdlib only, no network.

It greets whoever says HELLO, records every control line, rings the client
when a test says ring(), answers CALL with STATE, and CONNECTs a call when a
test says connect() (or the client ACCEPTs a ring) - then plays the other
side of the session: peer() writes their keystrokes, heard() is what the
client typed.  Their edit characters are ^H ^U ^W, as many a real talk's.
"""
import socket
import threading
import time

PEER_EDIT = b"\x08\x15\x17"


class FakeTalkd(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.sock.settimeout(0.5)
        self.port = self.sock.getsockname()[1]
        self.got = []                    # every control line the client sent
        self.conn = None                 # the control connection
        self.data = None                 # the session's connection, once attached
        self.heard_buf = b""             # what came over the session from the client
        self.lock = threading.Lock()
        self.stopping = False
        self.error = None
        self._n = 0

    def conf(self, user="chris", view=None):
        """A tvchat.conf that autoconnects one talk account, `talk`, to us."""
        return ("[chat]\nautoconnect = talk\n\n[account:talk]\nprotocol = talk\n"
                "display = Talk\nhost = 127.0.0.1\nport = %d\nuser = %s\n%s"
                % (self.port, user, "talk_view = %s\n" % view if view else ""))

    # -- the test's side --------------------------------------------------
    def wait(self, pred, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return True
            time.sleep(0.05)
        return False

    def greeted(self, timeout=10):
        return self.wait(lambda: any(l.startswith("HELLO ") for l in self.got), timeout)

    def sent(self, prefix):
        return [l for l in list(self.got) if l.startswith(prefix)]

    def say(self, line):
        with self.lock:
            self.conn.sendall(line.encode("utf-8") + b"\n")

    def ring(self, who="miche@cpbee", ident="t1"):
        self.say("RING %s %s" % (ident, who))

    def connect(self, ident):
        """CONNECT the call `ident`; the session is ours once it attaches."""
        self._n += 1
        self.say("CONNECT %s tok%d" % (ident, self._n))

    def attached(self, timeout=10):
        return self.wait(lambda: self.data is not None, timeout)

    def peer(self, keys):
        """The other side types: raw bytes, their edit characters and all."""
        self.data.sendall(keys if isinstance(keys, bytes) else keys.encode("utf-8"))

    def heard(self):
        """What the client sent over the session, after its edit characters."""
        return self.heard_buf[3:]

    def hang_up(self):
        with self.lock:
            if self.data is not None:
                self.data.close()
                self.data = None

    def stop(self):
        self.stopping = True
        for s in (self.data, self.conn, self.sock):
            try:
                if s is not None:
                    s.close()
            except OSError:
                pass

    # -- the server -------------------------------------------------------
    def run(self):
        while not self.stopping:
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            # the first line says what it is: HELLO (control) or ATTACH (a session)
            conn.settimeout(5)
            buf = b""
            try:
                while b"\n" not in buf:
                    d = conn.recv(4096)
                    if not d:
                        break
                    buf += d
            except OSError:
                continue
            first, _, rest = buf.partition(b"\n")
            if first.startswith(b"ATTACH "):
                threading.Thread(target=self.session, args=(conn, rest), daemon=True).start()
            else:
                with self.lock:
                    self.conn = conn
                threading.Thread(target=self.control, args=(conn, first + b"\n" + rest),
                                 daemon=True).start()

    def control(self, conn, buf):
        try:
            conn.settimeout(None)
            while not self.stopping:
                while b"\n" not in buf:
                    d = conn.recv(4096)
                    if not d:
                        return
                    buf += d
                raw, buf = buf.split(b"\n", 1)
                ln = raw.decode("utf-8", "replace").rstrip("\r")
                self.got.append(ln)
                word, _, rest = ln.partition(" ")
                if word == "HELLO":
                    self.say("OK %s@cpbee" % rest)
                elif word == "CALL":
                    cid, _, target = rest.partition(" ")
                    self.say("STATE %s ringing %s on pts/1" % (cid, target))
                elif word == "ACCEPT":
                    self.connect(rest)
                elif word == "PING":
                    self.say("PONG")
        except Exception as e:
            if not self.stopping:
                self.error = e

    def session(self, conn, first):
        conn.settimeout(None)
        self.heard_buf = first
        with self.lock:
            self.data = conn
        conn.sendall(PEER_EDIT)
        try:
            while not self.stopping:
                d = conn.recv(4096)
                if not d:
                    return
                self.heard_buf += d
        except OSError:
            return
