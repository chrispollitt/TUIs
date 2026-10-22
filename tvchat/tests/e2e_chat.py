#!/usr/bin/env python3
"""End-to-end: tvchat drives its co-process.  Runs the built tvchat in a
pty with a tvchat.conf that autoconnects to a fake IRC server on 127.0.0.1,
then checks, through tvchat's --debug log, that the idle loop keeps polling
tvchat-backend (a message sent well after start-up still arrives); then,
through the window (Enter, Down, Enter), that a line typed there reaches the
server and the server's answer shows in it; that a killed backend is
restarted, its state re-read and the chat pane's log with it; and that Alt-X
shuts chat down cleanly (the server sees QUIT).  Stdlib only, no network.

    python3 tests/e2e_chat.py --bin build/tvchat [--backend-dir backend]

Exit codes:  0 pass, 1 fail, 77 skip (no usable terminal / not POSIX).
"""
import argparse
import os
import re
import socket
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# the pty + pyte harness tvmail and tvchat share (it skips without a pty)
sys.path.insert(0, str(HERE.parent / "tvcommon" / "tests"))      # a source tarball
sys.path.insert(0, str(HERE.parent.parent / "tvcommon" / "tests"))
import tvscreen                   # noqa: E402
from tvscreen import skip         # noqa: E402


def fail(msg):
    print("FAIL:", msg)
    raise SystemExit(1)


class FakeIrc(threading.Thread):
    """Serves `sessions` connections in turn.  Each: welcome, confirm the
    JOIN, then - after a pause, so it can only reach tvchat through the
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
    """The tvchat-backend serve process tvchat forked (via sh -c exec)."""
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


def wait_log(tv, log, want, timeout=15):
    end = time.monotonic() + timeout
    text = ""
    while time.monotonic() < end:
        tv.pump(0.3)                              # keep the pty drained
        text = log.read_text() if log.exists() else ""
        if all(re.search(w, text) for w in want):
            return text
    for w in want:
        if not re.search(w, text):
            fail("not in the debug log: %s\n---\n%s" % (w, text[-2500:]))
    return text


class Tv(tvscreen.Tv):
    """tvchat --debug LOG, its tvchat.conf aimed at the fake server."""

    prefix = "tvchat-e2e-chat-"
    drop_env = ("COLORTERM", "TVCHAT_CONF", "TVMAIL_MU_TICKETS", "TVCHAT_BACKEND_LOG")

    def __init__(self, opts, port):
        self.port = port
        super().__init__(opts)

    def prepare(self):
        conf = self.tmp / "tvchat.conf"
        conf.write_text("[chat]\nautoconnect = fake\n\n[account:fake]\n"
                        "protocol = irc\nhost = 127.0.0.1\nport = %d\ntls = false\n"
                        "nick = e2e\nchannels = #e2e\n" % self.port)
        self.log = self.tmp / "debug.log"
        self.env.update(TVCHAT_CONF=str(conf),
                        TVMAIL_MU_TICKETS=str(self.tmp / "no-tickets"),
                        PATH=self.opts.backend_dir + os.pathsep + self.env["PATH"])
        self.argv = [self.opts.bin, "--debug", str(self.log)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    ap.add_argument("--backend-dir", default="")
    a = ap.parse_args()
    binpath = Path(a.bin).resolve()
    if not binpath.exists():
        skip("tvchat not built at %s" % binpath)
    a.bin = str(binpath)
    a.backend_dir = a.backend_dir or str(binpath.parent.parent / "backend")

    srv = FakeIrc(sessions=2)
    srv.start()
    tv = None
    try:
        tv = Tv(a, srv.port)
        log = tv.log
        c = tvscreen.Case(tv)
        c.check(tv.wait("Servers", 15), "tvchat never showed its window")

        wait_log(tv, log, [r"chat: tvchat-backend up",
                           r"chat event \d+: state fake",
                           r"chat event \d+: conv-new fake/#e2e",
                           r"chat event \d+: msg .*fake/#e2e.*alice.*late hello 1"])

        # Enter on the server goes to fake's conversations (its server
        # messages first), Down shows #e2e on what it has said so far, Enter
        # goes to its input
        tv.send("Enter", settle=0.5)
        tv.send("Down", settle=0.5)
        tv.send("Enter", settle=0.5)
        c.check(tv.wait("<alice> late hello 1", 8) and tv.has("#e2e"),
                "Enter, Down, Enter did not show #e2e on its history")
        # a line typed there goes to the server; its answer comes back
        tv.send("hi from tvchat é", settle=0.4)
        tv.send("Enter", settle=0.5)
        c.check(tv.wait("<bob> got: hi from tvchat", 8),
                "the server's answer is not in the chat pane")
        mine = [g for g in srv.got[-1] if g.startswith("PRIVMSG #e2e :")]
        c.check(mine == ["PRIVMSG #e2e :hi from tvchat é"],
                "the chat pane did not send the typed line: %r" % srv.got[-1])
        c.check(tv.has("<e2e> hi from tvchat"), "our own line is not in the chat pane")
        # --debug reached the chat backend: its log beside tvchat's has the
        # wire both ways and the request; tvchat's has the request
        clog = tv.tmp / "debug.chat.log"
        ctext = clog.read_text() if clog.exists() else ""
        for w in ("fake >> PRIVMSG #e2e :hi from tvchat é",
                  "fake << :bob!",
                  "request: send "):
            c.check(w in ctext, "not in debug.chat.log: %s\n---\n%s" % (w, ctext[-2500:]))
        wait_log(tv, log, [r"chat: send '?fake/#e2e.* -> 0"])

        # kill the chat co-process: the next poll restarts it, re-reads the
        # model, and the new one's autoconnect brings session 2
        pid = chat_backend_pid(tv.pid) if os.path.isdir("/proc") else None
        if pid is not None:
            os.kill(pid, 9)
            wait_log(tv, log, [r"chat: tvchat-backend restarted",
                               r"chat event \d+: msg .*late hello 2"])
            # the chat pane re-read its log from the new backend
            c.check(tv.wait("<alice> late hello 2", 8),
                    "the chat pane did not follow the restarted backend")
        else:
            srv.sessions = 1                 # no /proc: skip the restart leg

        # Alt-X: tvchat exits, closes the chat pipe, the backend says QUIT
        tv.send(tvscreen.alt("x"), settle=0.3)
        c.check(tv.wait(lambda: not tv.alive(), 6), "tvchat did not exit on Alt-X")
        srv.join(10)
        c.check(srv.got and any(g.startswith("QUIT") for g in srv.got[-1]),
                "the chat backend never sent QUIT on exit: %r" % srv.got)
        c.check(srv.error is None, "fake server: %s" % srv.error)
    except tvscreen.CheckFailed as e:
        fail(str(e))
    finally:
        if tv:
            tv.close()
    print("PASS: chat co-process started, polled while idle; the chat pane "
          "sent and received; restarted when killed, shut down on exit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
