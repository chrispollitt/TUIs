#!/usr/bin/env python3
"""End-to-end smoke test: drive the built tvmail in a real pty and assert on
what it paints.  Stdlib only.

    python3 tests/e2e_tui.py --bin build/tvmail

Exit codes:  0 pass, 1 fail, 77 skip (no usable terminal / not POSIX).
ctest maps 77 to "skipped" (SKIP_RETURN_CODE).
"""
import argparse
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

SKIP = 77


def skip(msg):
    print("SKIP:", msg)
    raise SystemExit(SKIP)


def fail(msg):
    print("FAIL:", msg)
    raise SystemExit(1)


try:
    import fcntl
    import pty
    import select
    import signal
    import struct
    import termios
except Exception as e:                       # non-POSIX, or a cut-down python
    skip("no pty support: %s" % e)

_ANSI = re.compile(
    r"\x1b\[[0-9;?]*[A-Za-z]"
    r"|\x1b[()][A-Z0-9]"
    r"|\x1b[=>]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
)


def deansi(s):
    return _ANSI.sub("", s)


# One message, far taller than any pane.  Distinct sentinels near the top and
# deep down so a scroll is observable even though tvision only repaints the
# cells that actually change (a shared line prefix would never re-emit).
def _body():
    rows = []
    for i in range(1, 61):
        if i == 2:
            rows.append("HEADSENTINEL cornerstone of the body pane")
        elif i == 49:
            rows.append("TAILSENTINEL buried deep past the fold")
        else:
            rows.append("filler prose number %02d wanders along here" % i)
    return "\n".join(rows) + "\n"


FIXTURE = (
    "From a@b Mon Sep  1 10:00:00 2026\n"
    "From: Alice <alice@example.com>\n"
    "Date: Mon, 01 Sep 2026 10:00:00 -0700\n"
    "Subject: E2E fixture message\n"
    "\n" + _body() + "\n"
)


class Term:
    def __init__(self, argv, env, rows=32, cols=100):
        self.buf = ""
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            try:
                os.execvpe(argv[0], argv, env)
            finally:
                os._exit(127)
        try:
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0))
            time.sleep(0.1)
            os.kill(self.pid, signal.SIGWINCH)
        except OSError:
            pass

    def read(self, until=None, timeout=6.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            r, _, _ = select.select([self.fd], [], [], 0.2)
            if not r:
                continue
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            self.buf += chunk.decode("utf-8", "replace")
            if until and re.search(until, deansi(self.buf)):
                break
        return deansi(self.buf)

    def send(self, data, settle=0.35):
        os.write(self.fd, data.encode() if isinstance(data, str) else data)
        time.sleep(settle)

    def clear(self):
        self.buf = ""

    def close(self):
        try:
            os.write(self.fd, b"\x1bx")           # Alt-X
            time.sleep(0.3)
        except OSError:
            pass
        for step in (lambda: os.kill(self.pid, signal.SIGTERM),
                     lambda: os.kill(self.pid, signal.SIGKILL)):
            try:
                step()
                time.sleep(0.1)
            except OSError:
                pass
        try:
            os.waitpid(self.pid, 0)
        except OSError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True, help="path to the built tvmail")
    ap.add_argument("--backend-dir",
                    default=os.environ.get("TVMAIL_BACKEND_DIR", ""))
    a = ap.parse_args()

    if os.name != "posix":
        skip("posix only")
    binpath = Path(a.bin).resolve()
    if not binpath.exists():
        skip("tvmail not built at %s" % binpath)

    tmp = Path(tempfile.mkdtemp(prefix="tvmail-e2e-"))
    try:
        home = tmp / "home"
        home.mkdir()
        spool = tmp / "spool.mbox"
        spool.write_text(FIXTURE)

        env = dict(os.environ)
        env["HOME"] = str(home)
        env.pop("TVCHAT_CONF", None)   # no chat co-process from the host's config
        env["XDG_DATA_HOME"] = str(tmp / "xdg")
        env["MAIL"] = str(spool)
        term = env.get("TERM", "")
        env["TERM"] = "xterm" if term in ("", "dumb") else term
        bdir = a.backend_dir or str(binpath.parent.parent / "backend")
        env["PATH"] = bdir + os.pathsep + env.get("PATH", "")

        t = Term([str(binpath)], env)
        done = []
        try:
            # 1. launches; the first folder fills all three panes.  The body
            #    loads just after the list, so seeing a body line implies the
            #    list came up too.
            s = t.read(until=r"HEADSENTINEL", timeout=15)
            if not re.search(r"HEADSENTINEL", s):
                if len(t.buf) < 40 or re.search(
                        r"[Ee]rror opening terminal|not a terminal|Cannot", s):
                    skip("tvmail could not open a usable terminal")
                fail("panes never populated\n---\n%s" % s[-1500:])
            if not re.search(r"E2E fixture message", s):
                fail("message list never populated\n---\n%s" % s[-1500:])
            if not re.search(r"\[ Folders \]", s):
                fail("no active-pane marker on Folders at startup\n---\n%s"
                     % s[-1500:])
            if re.search(r"TAILSENTINEL", s):
                fail("body already scrolled past the fold at startup")
            done.append("launch+panes+active-marker")

            # 2. Tab walks the active-pane marker Folders -> Messages -> Message
            t.clear()
            t.send("\t")
            if not re.search(r"\[ Messages \]", t.read(until=r"\[ Messages \]", timeout=4)):
                fail("Tab did not activate the Messages pane")
            done.append("tab-activates-messages")

            # 2b. previewing a message (the list highlight showed its body at
            #     startup) must not mark it read - only looking at it does
            if "Status:" in spool.read_text():
                fail("showing a message in the preview marked it read")
            #     the folder pane carries the unread count
            #     (s is still the startup screen from step 1)
            if not re.search(r"inbox \(1\)", s):
                fail("no unread count on the inbox folder\n---\n%s" % s[-1500:])
            done.append("preview-leaves-unread+folder-count")

            # 2c. typing in the message list filters it (From / Subject);
            #     Esc puts everything back
            t.clear()
            t.send("zz")
            s = t.read(until=r"filter: zz_", timeout=4)
            if not re.search(r"filter: zz_\s+\(0 of 1\)", s):
                fail("typing did not filter the message list\n---\n%s" % s[-1200:])
            t.clear()
            t.send("\x1b", settle=0.8)                # Esc (ESCDELAY is 100 ms)
            t.send("alice")                           # a sender that does match
            s = t.read(until=r"\(1 of 1\)", timeout=4)  # tvision repaints only what changed
            if not re.search(r"\(1 of 1\)", s):
                fail("filter did not match the sender\n---\n%s" % s[-1200:])
            t.send("\x1b", settle=0.8)
            done.append("type-to-filter")

            t.clear()
            t.send("\t")
            if not re.search(r"\[ Message \]", t.read(until=r"\[ Message \]", timeout=4)):
                fail("Tab did not activate the body pane")
            # ... and now that the body pane has focus it IS read
            for _ in range(20):
                if re.search(r"^Status: [OR]*R", spool.read_text(), re.M):
                    break
                time.sleep(0.25)
            else:
                fail("focusing the body pane did not mark the message read")
            done.append("tab-cycles-active-pane+read-on-focus")

            # 3. the body pane now scrolls from the keyboard (it did not before)
            t.clear()
            for _ in range(6):
                t.send("\x1b[6~")                 # PgDn toward the bottom
            s = t.read(until=r"TAILSENTINEL", timeout=5)
            if not re.search(r"TAILSENTINEL", s):
                fail("PgDn did not scroll the body to the fold\n---\n%s"
                     % s[-1500:])
            done.append("body-scrolls-with-keys")

            # 4. F3 pulls in the background: a completion box appears, the TUI
            #    stays up, and the old blocking suspend path is never taken
            t.clear()
            t.send("\x1bOR")                      # F3 (SS3)
            t.send("\x1b[13~")                    # F3 (CSI ~) - whichever lands
            s = t.read(until=r"pop-pull|getmail|mail puller|Pull finished", timeout=15)
            if not re.search(r"pop-pull|getmail|mail puller|Pull finished", s):
                fail("no background-pull completion box\n---\n%s" % s[-1200:])
            done.append("f3-background-pull")
        finally:
            t.close()

        if "press Enter to return" in t.buf:
            fail("the blocking suspend path (shInteractive) was used")

        print("PASS:", ", ".join(done))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
