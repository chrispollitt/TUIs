#!/usr/bin/env python3
"""End-to-end: tvmail drives the chat co-process.  Runs the built tvmail in a
pty with a tvchat.conf that autoconnects to a fake IRC server on 127.0.0.1,
then checks, through tvmail's --debug log, that the idle loop keeps polling
tvchat-backend (a message sent well after start-up still arrives); then,
through a chat window (Alt-C, Enter), that a line typed there reaches the
server and the server's answer shows in the window; that a killed backend is
restarted, its state re-read and the window's log with it; and that Alt-X
shuts chat down cleanly (the server sees QUIT).  Stdlib only, no network.

    python3 tests/e2e_chat.py --bin build/tvmail

Exit codes:  0 pass, 1 fail, 77 skip (no usable terminal / not POSIX).
"""
import argparse
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from e2e_tui import Term, FIXTURE, skip, fail   # noqa: E402  (skips without a pty)


class FakeIrc(threading.Thread):
    """Serves `sessions` connections in turn.  Each: welcome, confirm the
    JOIN, then - after a pause, so it can only reach tvmail through the
    idle-time poll - one channel message naming the session.  Answers each
    channel message with bob saying "got: <it>".  Records every line the
    client sent, per session."""

    def __init__(self, sessions):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(2)
        self.port = self.sock.getsockname()[1]
        self.sessions = sessions
        self.got = []                    # one list of lines per session
        self.error = None

    def run(self):
        try:
            self.sock.settimeout(30)
            for n in range(1, self.sessions + 1):
                conn, _ = self.sock.accept()
                self.got.append([])
                self.session(conn, n)
        except Exception as e:
            self.error = e

    def session(self, conn, n):
        conn.settimeout(30)
        buf = b""
        while True:
            while b"\n" not in buf:
                data = conn.recv(4096)
                if not data:                     # killed backend: next session
                    conn.close()
                    return
                buf += data
            ln, buf = buf.split(b"\n", 1)
            ln = ln.decode().rstrip("\r")
            self.got[-1].append(ln)
            if ln.startswith("CAP LS"):
                conn.sendall(b":srv CAP * LS :\r\n")
            elif ln.startswith("USER "):
                conn.sendall(b":srv 001 e2e :Welcome\r\n")
            elif ln.startswith("JOIN #e2e"):
                conn.sendall(b":e2e!u@h JOIN #e2e\r\n")
                time.sleep(1.5)
                conn.sendall(b":alice!a@h PRIVMSG #e2e :late hello %d\r\n" % n)
            elif ln.startswith("PRIVMSG #e2e :"):
                conn.sendall(b":bob!b@h PRIVMSG #e2e :got: "
                             + ln.split(" :", 1)[1].encode() + b"\r\n")
            elif ln.startswith("QUIT"):
                conn.close()
                return


def chat_backend_pid(parent):
    """The tvchat-backend serve process tvmail forked (via sh -c exec)."""
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % d) as f:
                ppid = int(f.read().rsplit(")", 1)[1].split()[1])
            with open("/proc/%s/cmdline" % d, "rb") as f:
                cmd = f.read().replace(b"\0", b" ")
        except (OSError, ValueError, IndexError):
            continue
        if ppid == parent and b"tvchat-backend" in cmd and b"serve" in cmd:
            return int(d)
    return None


def wait_log(t, log, want, timeout=15):
    end = time.monotonic() + timeout
    text = ""
    while time.monotonic() < end:
        t.read(timeout=0.3)                       # keep the pty drained
        text = log.read_text() if log.exists() else ""
        if all(re.search(w, text) for w in want):
            return text
    for w in want:
        if not re.search(w, text):
            fail("not in the debug log: %s\n---\n%s" % (w, text[-2500:]))
    return text


def flat(s):
    """The pty stream with every space gone: a redraw that skips cells with
    cursor moves leaves no spaces in deansi()'s text."""
    return re.sub(r"\s+", "", s)


def read_flat(t, want, timeout=8):
    end = time.monotonic() + timeout
    s = ""
    while time.monotonic() < end and want not in flat(s):
        s = t.read(timeout=0.3)
    return flat(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    ap.add_argument("--backend-dir", default="")
    a = ap.parse_args()
    binpath = Path(a.bin).resolve()
    if not binpath.exists():
        skip("tvmail not built at %s" % binpath)

    tmp = Path(tempfile.mkdtemp(prefix="tvmail-e2e-chat-"))
    srv = FakeIrc(sessions=2)
    srv.start()
    try:
        home = tmp / "home"
        home.mkdir()
        spool = tmp / "spool.mbox"
        spool.write_text(FIXTURE)
        conf = tmp / "tvchat.conf"
        conf.write_text("[chat]\nautoconnect = fake\n\n[account:fake]\n"
                        "protocol = irc\nhost = 127.0.0.1\nport = %d\ntls = false\n"
                        "nick = e2e\nchannels = #e2e\n" % srv.port)
        log = tmp / "debug.log"
        env = dict(os.environ)
        env.update(HOME=str(home), XDG_DATA_HOME=str(tmp / "xdg"), MAIL=str(spool),
                   TVCHAT_CONF=str(conf), TVMAIL_MODE="local")
        term = env.get("TERM", "")
        env["TERM"] = "xterm" if term in ("", "dumb") else term
        bdir = a.backend_dir or str(binpath.parent.parent / "backend")
        env["PATH"] = bdir + os.pathsep + env.get("PATH", "")

        t = Term([str(binpath), "--debug", str(log)], env)
        try:
            s = t.read(until=r"HEADSENTINEL", timeout=15)
            if "HEADSENTINEL" not in s:
                if len(t.buf) < 40:
                    skip("tvmail could not open a usable terminal")
                fail("mail panes never populated with chat configured\n---\n%s" % s[-1200:])

            want = [r"chat: tvchat-backend up",
                    r"chat event \d+: state fake",
                    r"chat event \d+: conv-new fake/#e2e",
                    r"chat event \d+: msg .*fake/#e2e.*alice.*late hello 1"]
            text = wait_log(t, log, want)

            # the chat window: Alt-C lists the conversations (the server
            # window first), Enter opens #e2e on what it has said so far
            t.clear()
            t.send("\x1bc", settle=0.6)
            t.send("\x1b[B", settle=0.3)
            t.send("\r")
            s = read_flat(t, "<alice>latehello1")
            if "<alice>latehello1" not in s or "#e2e" not in s:
                fail("Alt-C, Enter did not open #e2e on its history\n---\n%s" % s[-1500:])
            # a line typed there goes to the server; its answer comes back
            t.clear()
            t.send("hi from tvmail \u00e9", settle=0.4)   # (one write with the
            t.send("\r", settle=0.5)                     # \r is a paste)
            s = read_flat(t, "<bob>got:hifromtvmail")
            mine = [g for g in srv.got[-1] if g.startswith("PRIVMSG #e2e :")]
            if mine != ["PRIVMSG #e2e :hi from tvmail \u00e9"]:
                fail("the chat window did not send the typed line: %r\n---\n%s" % (srv.got[-1], s[-1500:]))
            if "<e2e>hifromtvmail" not in s or "<bob>got:hifromtvmail" not in s:
                fail("our line or the answer is not in the chat window\n---\n%s" % s[-1500:])
            # --debug reached the chat backend: its log beside tvmail's has
            # the wire both ways and the request; tvmail's has the request
            clog = tmp / "debug.chat.log"
            ctext = clog.read_text() if clog.exists() else ""
            for w in ("fake >> PRIVMSG #e2e :hi from tvmail \u00e9",
                      "fake << :bob!",
                      "request: send "):
                if w not in ctext:
                    fail("not in debug.chat.log: %s\n---\n%s" % (w, ctext[-2500:]))
            wait_log(t, log, [r"chat: send '?fake/#e2e.* -> 0"])

            # kill the chat co-process: the next poll restarts it, re-reads
            # the model, and the new one's autoconnect brings session 2
            pid = chat_backend_pid(t.pid) if os.path.isdir("/proc") else None
            if pid is not None:
                t.clear()
                os.kill(pid, 9)
                wait_log(t, log, [r"chat: tvchat-backend restarted",
                                  r"chat event \d+: msg .*late hello 2"])
                # the open window re-read its log from the new backend
                s = read_flat(t, "<alice>latehello2")
                if "<alice>latehello2" not in s:
                    fail("the chat window did not follow the restarted backend"
                         "\n---\n%s" % s[-1500:])
            else:
                srv.sessions = 1                 # no /proc: skip the restart leg

            # Alt-X: tvmail exits, closes the chat pipe, the backend says QUIT
            os.write(t.fd, b"\x1bx")
            for _ in range(50):
                pid, _ = os.waitpid(t.pid, os.WNOHANG)
                if pid:
                    t.pid = -1
                    break
                t.read(timeout=0.1)
            else:
                fail("tvmail did not exit on Alt-X")
            srv.join(10)
            if not srv.got or not any(g.startswith("QUIT") for g in srv.got[-1]):
                fail("the chat backend never sent QUIT on exit: %r" % srv.got)
            if srv.error:
                fail("fake server: %s" % srv.error)
        finally:
            if t.pid > 0:
                t.close()
        print("PASS: chat co-process started, polled while idle; a chat window "
              "sent and received; restarted when killed, shut down on exit")
        return 0
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
