"""A small IMAP4 server for the end-to-end tests: a socket in front of test_backend.FakeIMAP, so the backend's own imaplib
code talks to it for real (LOGIN, LIST, STATUS, SELECT, UID SEARCH / FETCH / STORE / COPY, CREATE, SUBSCRIBE).  Plain text, no
TLS ([imap] ssl = false); one thread per connection; every connection has its own "selected folder".

    srv = ImapServer(fake)          # fake: a test_backend.FakeIMAP with its folders filled in
    srv.port                        # where it listens, on 127.0.0.1
    srv.stop()
"""
import re
import socketserver
import threading

_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\([^)]*\)|\S+')


class _Handler(socketserver.StreamRequestHandler):
    def send(self, data):
        self.wfile.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def handle(self):
        srv = self.server.owner
        fake, lock = srv.fake, srv.lock
        cur = None
        self.send("* OK IMAP4rev1 fake server ready")
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            tag, _, rest = line.partition(" ")
            cmd, _, args = rest.partition(" ")
            cmd = cmd.upper()
            toks = _TOKEN.findall(args)
            with lock:
                fake.cur = cur
                if cmd == "CAPABILITY":
                    self.send("* CAPABILITY IMAP4rev1")
                    self.send("%s OK done" % tag)
                elif cmd == "LOGIN":
                    self.send("%s OK logged in" % tag)
                elif cmd in ("NOOP", "CHECK", "CLOSE", "UNSELECT"):
                    self.send("%s OK done" % tag)
                elif cmd == "LOGOUT":
                    self.send("* BYE")
                    self.send("%s OK bye" % tag)
                    return
                elif cmd in ("SELECT", "EXAMINE"):
                    cur = toks[0].strip('"')
                    fake.cur = cur
                    typ, data = fake.select(toks[0])
                    self.send("* %d EXISTS" % len(fake._rows()))
                    self.send("* 0 RECENT")
                    self.send("* FLAGS (\\Seen \\Deleted \\Answered)")
                    self.send("%s OK [READ-WRITE] selected" % tag)
                elif cmd == "STATUS":
                    typ, data = fake.status(toks[0], toks[1] if len(toks) > 1 else "")
                    if typ == "OK":
                        self.send(b"* STATUS " + data[0])
                        self.send("%s OK done" % tag)
                    else:
                        self.send("%s NO no such mailbox" % tag)
                elif cmd == "LIST":
                    typ, data = fake.list(toks[0], toks[1] if len(toks) > 1 else '"*"')
                    for d in data:
                        if d:
                            self.send(b"* LIST " + d)
                    self.send("%s OK done" % tag)
                elif cmd == "CREATE":
                    fake.create(toks[0])
                    self.send("%s OK created" % tag)
                elif cmd == "SUBSCRIBE":
                    fake.subscribe(toks[0])
                    self.send("%s OK subscribed" % tag)
                elif cmd == "EXPUNGE":
                    fake.expunge()
                    self.send("%s OK expunged" % tag)
                elif cmd == "UID":
                    sub, _, tail = args.partition(" ")
                    sub = sub.upper()
                    t = _TOKEN.findall(tail)
                    if sub == "SEARCH":
                        typ, data = fake.uid("SEARCH", *t)
                        self.send(b"* SEARCH " + data[0])
                    elif sub == "FETCH":
                        seq, _, spec = tail.partition(" ")
                        typ, out = fake.uid("FETCH", seq, spec)
                        for item in out:
                            if isinstance(item, tuple):
                                meta = re.sub(rb"^(\d+) ", rb"\1 FETCH ", item[0])      # "3 (UID 3 ..." -> "3 FETCH (UID 3 ..."
                                self.wfile.write(b"* " + meta + b"\r\n" + item[1])
                            else:
                                self.wfile.write(item + b"\r\n")
                        self.wfile.flush()
                    elif sub in ("STORE", "COPY"):
                        typ, data = fake.uid(sub, *t)
                        if typ != "OK":
                            self.send("%s NO [TRYCREATE] no such mailbox" % tag)
                            continue
                    self.send("%s OK UID done" % tag)
                else:
                    self.send("%s BAD unknown command" % tag)


class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class ImapServer:
    def __init__(self, fake):
        self.fake = fake
        self.lock = threading.RLock()
        self._srv = _Server(("127.0.0.1", 0), _Handler)
        self._srv.owner = self
        self.port = self._srv.server_address[1]
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def stop(self):
        self._srv.shutdown()
        self._srv.server_close()
