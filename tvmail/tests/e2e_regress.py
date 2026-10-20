#!/usr/bin/env python3
"""Regression tests for tvmail's squashed UX bugs (BUGS.md, by #number).

Drives the built tvmail in a pty, like e2e_tui.py, but feeds the output to a
pyte screen model, so each check looks at the cells actually on screen - with
their columns and colours - instead of regexes over the output stream.

    python3 tests/e2e_regress.py --bin build/tvmail [--backend-dir backend]
                                 [-k 27] [-v]

Every case starts its own tvmail on a fresh temp HOME and fixture mbox.
Exit codes:  0 pass, 1 fail, 77 skip (no pty, or pyte not installed).
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

SKIP = 77
HERE = Path(__file__).resolve().parent


def skip(msg):
    print("SKIP:", msg)
    raise SystemExit(SKIP)


try:
    import fcntl
    import pty
    import select
    import signal
    import struct
    import termios
except Exception as e:                       # non-POSIX, or a cut-down python
    skip("no pty support: %s" % e)


def _import_pyte():
    try:
        import pyte
        return pyte
    except ImportError:
        pass
    # a `pip install --prefix /usr/local` the system python doesn't search
    ver = "python%d.%d" % sys.version_info[:2]
    for d in ("/usr/local/lib/%s/site-packages" % ver,
              os.path.expanduser("~/.local/lib/%s/site-packages" % ver)):
        if os.path.isdir(d) and d not in sys.path:
            sys.path.append(d)
    try:
        import pyte
        return pyte
    except ImportError:
        skip("pyte is not installed (pip install pyte)")


pyte = _import_pyte()

ROWS, COLS = 32, 100

# ----------------------------------------------------------------- colours --
# Colour numbers 0-15 (SGR 30-37 / 90-97 / 40-47 / 100-107 and 38;5;0-15) are
# themed by the terminal; resolve them through Windows Terminal's Dark+, the
# theme #36 was found under.  16-255 are xterm's fixed cube and grey ramp,
# and 24-bit colour is what it says - pyte already stores those as RGB.
DARK_PLUS = ["000000", "CD3131", "0DBC79", "E5E510", "2472C8", "BC3FBC",
             "11A8CD", "E5E5E5", "666666", "F14C4C", "23D18B", "F5F543",
             "3B8EEA", "D670D6", "29B8DB", "E5E5E5"]          # ANSI order
DARK_PLUS_FG, DARK_PLUS_BG = "CCCCCC", "1E1E1E"


def _rgb(h):
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def _lum(h):
    c = [x / 255 for x in _rgb(h)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(fg, bg):
    """WCAG contrast ratio of two RRGGBB colours (1 .. 21)."""
    a, b = _lum(fg), _lum(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


class ThemedScreen(pyte.Screen):
    """A pyte screen whose 16 themed colours come out as Dark+ RGB."""

    def select_graphic_rendition(self, *attrs, **kw):
        out, a = [], list(attrs)
        while a:
            p = a.pop(0)
            idx = None
            if 30 <= p <= 37 or 40 <= p <= 47:
                idx, bg = p % 10, p >= 40
            elif 90 <= p <= 97 or 100 <= p <= 107:
                idx, bg = p % 10 + 8, p >= 100
            elif p in (38, 48) and len(a) >= 2 and a[0] == 5 and a[1] < 16:
                idx, bg = a[1], p == 48
                del a[:2]
            elif p in (38, 48) and a and a[0] == 5:
                out += [p] + a[:2]
                del a[:2]
                continue
            elif p in (38, 48) and a and a[0] == 2:
                out += [p] + a[:4]
                del a[:4]
                continue
            if idx is None:
                out.append(p)
            else:
                out += [48 if bg else 38, 2] + _rgb(DARK_PLUS[idx])
        super().select_graphic_rendition(*out, **kw)

    def set_margins(self, *args, private=False, **kw):
        # CSI ? Pm r (restore private modes, sent on exit) is not DECSTBM;
        # pyte 0.8 passes private= and set_margins doesn't take it
        if not private:
            super().set_margins(*args, **kw)


def cell_colours(c):
    fg = DARK_PLUS_FG if c.fg == "default" else c.fg.upper()
    bg = DARK_PLUS_BG if c.bg == "default" else c.bg.upper()
    if not re.fullmatch(r"[0-9A-F]{6}", fg):      # a pyte colour name
        fg = DARK_PLUS_FG
    if not re.fullmatch(r"[0-9A-F]{6}", bg):
        bg = DARK_PLUS_BG
    return (bg, fg) if c.reverse else (fg, bg)


# ------------------------------------------------------------------- keys ---
K = dict(
    F1="\x1bOP", F2="\x1bOQ", F3="\x1bOR", F4="\x1b[14~", F5="\x1b[15~",
    F6="\x1b[17~", End="\x1b[F",
    AltF3="\x1b[1;3R", Up="\x1b[A", Down="\x1b[B", PgDn="\x1b[6~",
    Home="\x1b[H", ShiftTab="\x1b[Z", ShiftDown="\x1b[1;2B",
    CtrlIns="\x1b[2;5~", ShiftIns="\x1b[2;2~", Tab="\t", Enter="\r",
    CtrlN="\x0e", CtrlD="\x04", CtrlR="\x12",
)


def alt(ch):
    return "\x1b" + ch


# --------------------------------------------------------------- fixtures ---
LONG = "PARASTART " + " ".join("word%02d" % i for i in range(40)) + " PARAEND"
BODY1 = "\n".join(
    ["first line", LONG, "TAB1\ta\tbb\tccc", "CJKLINE 日本語のテキスト end"]
    + ["filler %02d" % i for i in range(40)]
    + ["FINDME sentinel here", "tail"]) + "\n"


def _msg(frm, date, subj, body):
    return ("From x@y %s\nFrom: %s\nDate: %s\nSubject: %s\n\n%s\n"
            % (time.strftime("%a %b %d %H:%M:%S %Y",
                             time.strptime(date[:25], "%a, %d %b %Y %H:%M:%S")),
               frm, date, subj, body))


# Oldest first: SUBJ1 (the long body) is the first row, SUBJ2 the newest
# and the last.
FIXTURE = "".join([
    # a raw 8-bit UTF-8 From (#35), an RFC 2047 CJK subject
    _msg("Zoë Ünïcödé Lastname Longname <z@example.org>",
         "Mon, 01 Sep 2026 09:00:00 -0700",
         "SUBJ1 =?utf-8?b?5pel5pys6Kqe?=", BODY1),
    _msg("山田太郎 <yamada@example.jp>", "Mon, 01 Sep 2026 10:00:00 -0700",
         "SUBJ3 wide sender", "konnichiwa"),
    _msg("Bob <bob@example.com>", "Mon, 01 Sep 2026 11:00:00 -0700",
         "SUBJ2 plain", "hello")])

# 14 older fillers in front push the newest past the bottom of the list
# pane: #12 is that it is still the one in view.
FIXTURE_TALL = "".join(
    _msg("Filler <f@example.com>", "Mon, 01 Sep 2026 08:%02d:00 -0700" % i,
         "FILLER %02d" % i, "filler body %02d" % i)
    for i in range(14)) + FIXTURE


# ---------------------------------------------------------------- harness ---
class CheckFailed(Exception):
    pass


class Tv:
    """One tvmail in a pty, on its own temp HOME, read into a pyte screen."""

    last = None          # the newest one, for the runner to close

    def __init__(self, opts, fixture=FIXTURE, env=None, fail_save_draft=False,
                 setup=None):
        Tv.last = self
        self.pid = None
        self.cleanups = []
        self.tmp = Path(tempfile.mkdtemp(prefix="tvmail-regress-"))
        self.home = self.tmp / "home"
        bindir = self.home / "bin"
        bindir.mkdir(parents=True)
        # the shell preamble puts ~/bin first: an installed (older) backend
        # there would win over the one under test, so put ours there
        real = bindir / "tvmail-backend"
        shutil.copy2(os.path.join(opts.backend_dir, "tvmail-backend"), real)
        if fail_save_draft:
            # save-draft never goes through the serve co-process, so a shim
            # can fail it on demand (while ~/fail-save-draft exists)
            real = real.rename(bindir / "tvmail-backend.real")
            shim = bindir / "tvmail-backend"
            shim.write_text(
                '#!/bin/sh\n'
                'for a in "$@"; do\n'
                '  if [ "$a" = save-draft ] && [ -e "$HOME/fail-save-draft" ]; then\n'
                '    cat >/dev/null; echo "tvmail-backend: disk full (test)"; exit 1\n'
                '  fi\n'
                'done\n'
                'exec "$HOME/bin/tvmail-backend.real" "$@"\n')
            shim.chmod(0o755)
        self.spool = self.tmp / "spool.mbox"
        self.spool.write_bytes(fixture.encode("utf-8"))
        self.env = dict(os.environ)
        for k in ("COLORTERM", "TVMAIL_CONF", "TVCHAT_CONF", "TVMAIL_IMAP_PASS", "MAILRC",
                  "TVMAIL_SIEVE_PASS", "TVMAIL_MU_CONF", "TVMAIL_MU_TICKETS"):
            self.env.pop(k, None)
        self.env.update(
            HOME=str(self.home), XDG_DATA_HOME=str(self.tmp / "xdg"),
            MAIL=str(self.spool), TERM="xterm-256color", LANG="C.UTF-8",
            LC_ALL="C.UTF-8", TVMAIL_MODE="local", USER="tester",
            TVMAIL_MU_TICKETS=str(self.tmp / "no-tickets"),
            PATH=str(bindir) + os.pathsep + os.environ.get("PATH", ""))
        self.env.update(env or {})
        if setup:
            setup(self)
        self.screen = ThemedScreen(COLS, ROWS)
        self.stream = pyte.ByteStream(self.screen)
        self.exited = None
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            try:
                os.execvpe(opts.bin, [opts.bin], self.env)
            finally:
                os._exit(127)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", ROWS, COLS, 0, 0))
        time.sleep(0.1)
        try:
            os.kill(self.pid, signal.SIGWINCH)
        except OSError:
            pass

    # -- backend, for setting up and inspecting mailboxes --
    def backend(self, *args, stdin=None):
        p = subprocess.run([str(self.home / "bin" / "tvmail-backend"), *args],
                           input=stdin, capture_output=True, env=self.env,
                           timeout=30)
        return p.stdout.decode("utf-8", "replace")

    # -- the screen --
    def pump(self, t=0.3):
        end = time.monotonic() + t
        while time.monotonic() < end:
            r, _, _ = select.select([self.fd], [], [], 0.05)
            if not r:
                continue
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                data = b""
            if not data:
                self._reap(block=True)
                return
            self.stream.feed(data)

    def _reap(self, block=False):
        if self.exited is None:
            try:
                pid, st = os.waitpid(self.pid, 0 if block else os.WNOHANG)
                if pid:
                    self.exited = st
            except ChildProcessError:
                self.exited = -1
        return self.exited

    def alive(self):
        self.pump(0.1)
        return self._reap() is None

    def line(self, y):
        """Row y as text, wide characters once (their trail cell dropped)."""
        row = self.screen.buffer[y]
        return "".join(row[x].data for x in range(COLS))

    def lines(self):
        return [self.line(y) for y in range(ROWS)]

    def text(self):
        return "\n".join(self.lines())

    def find(self, s):
        """(row, column) of the first cell where s is drawn, or None."""
        for y in range(ROWS):
            i = self.line(y).find(s)
            if i >= 0:
                return y, self.col(y, i)
        return None

    def col(self, y, i):
        """Screen column of character i of line(y)."""
        row, n = self.screen.buffer[y], 0
        for x in range(COLS):
            if row[x].data == "":
                continue
            if n == i:
                return x
            n += 1
        return COLS

    def has(self, s):
        return s in self.text()

    def wait(self, cond, timeout=8.0):
        """Pump until cond (a string on screen, or a predicate) holds."""
        pred = (lambda: self.has(cond)) if isinstance(cond, str) else cond
        end = time.monotonic() + timeout
        while True:
            self.pump(0.15)
            if pred():
                self.pump(0.25)       # let the rest of that repaint land
                return True
            if time.monotonic() > end or self.exited is not None:
                return False

    def send(self, *keys, settle=0.35):
        for k in keys:
            k = K.get(k, k)
            os.write(self.fd, k.encode("utf-8"))
            # ESCDELAY is 100 ms: a lone Esc needs a gap before the next key
            self.pump(0.3 if k == "\x1b" else 0.08)
        self.pump(settle)

    def dump(self):
        return "\n".join("%2d|%s|" % (y, l) for y, l in enumerate(self.lines()))

    def close(self):
        for f in self.cleanups:
            f()
        self.cleanups = []
        if self.pid is None:                      # setup failed before the fork
            shutil.rmtree(self.tmp, ignore_errors=True)
            return
        if self._reap() is None:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.kill(self.pid, sig)
                except OSError:
                    pass
                for _ in range(10):
                    if self._reap() is not None:
                        break
                    time.sleep(0.05)
                if self.exited is not None:
                    break
            self._reap(block=True)
        try:
            os.close(self.fd)
        except OSError:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)


class Case:
    def __init__(self, tv):
        self.tv = tv

    def check(self, cond, what):
        if not cond:
            raise CheckFailed("%s\n--- screen\n%s" % (what, self.tv.dump()))


CASES = []


def case(*bugs):
    def deco(fn):
        CASES.append((bugs, fn))
        return fn
    return deco


def started(tv, c):
    c.check(tv.wait("SUBJ2 plain", 15) and tv.wait("hello", 5),
            "tvmail never showed the fixture folder")


def body_rows(tv):
    """The body pane's text, row by row, below its "Message" rule (without
    the scroll bar, which only a focused pane draws)."""
    top = next((y for y, l in enumerate(tv.lines())
                if "Message " in l and "──" in l), 13)
    x0 = tv.line(top).index("│") + 1          # the folder pane is ASCII
    return [tv.line(y)[x0:].rstrip(" ║│▲▼▓▒■") for y in range(top + 1, ROWS - 2)]


def open_compose(tv, c):
    tv.send("CtrlN")
    c.check(tv.wait("Compose", 5) and tv.wait("Subject", 2),
            "Ctrl-N did not open a compose window")


def field(tv, label):
    """The text in a compose header field (To / Cc / Bcc / Subject)."""
    for l in tv.lines():
        m = re.search(r"║\s+%s\s+(.*?)\s*║" % re.escape(label), l)
        if m:
            return m.group(1)
    return None


def compose_open(tv):
    return tv.has(" Compose ")


# ------------------------------------------------------------------ cases ---
@case(12)
def opens_on_newest(opts):
    tv = Tv(opts, fixture=FIXTURE_TALL)
    c = Case(tv)
    started(tv, c)
    c.check(tv.find("SUBJ2 plain") is not None
            and "hello" in "".join(body_rows(tv)),
            "#12 the folder did not open on its newest message")
    # ... and it is the current row: drawn unlike the rows above it
    y2, x2 = tv.find("SUBJ2 plain")
    y1, x1 = tv.find("SUBJ1")
    c.check(cell_colours(tv.screen.buffer[y2][x2])
            != cell_colours(tv.screen.buffer[y1][x1]),
            "#12 the newest row is not the highlighted one")
    return tv


@case(7)
def esc_closes_windows(opts):
    def setup(t):
        (t.home / ".mailrc").write_text("alias bobby bob@example.com\n")
    tv = Tv(opts, setup=setup)
    c = Case(tv)
    started(tv, c)
    for name, keys, title in (
            ("Help", ["F1"], "THE THREE PANES"),
            ("View source", [alt("m"), "v"], "Source of msg"),
            ("Parts", [alt("m"), "p"], "Parts of msg"),
            ("Address book", ["F4"], "Address book")):
        tv.send(*keys)
        c.check(tv.wait(title, 5), "#7 %s did not open" % name)
        tv.send("\x1b")
        c.check(tv.wait(lambda: not tv.has(title), 3),
                "#7 Esc did not close %s" % name)
    c.check(tv.alive(), "#7 tvmail exited")
    return tv


@case(22)
def delete_prompt_names_subject(opts):
    tv = Tv(opts)
    c = Case(tv)
    started(tv, c)
    tv.send("Tab", "CtrlD")
    c.check(tv.wait('Move "SUBJ2 plain" to the trash?', 4),
            "#22 the delete prompt does not name the subject")
    c.check(tv.has("Yes") and tv.has("No") and not tv.has("Cancel"),
            "#22 the delete prompt is not a plain Yes / No")
    tv.send("y")
    c.check(tv.wait(lambda: not tv.has("SUBJ2 plain"), 5),
            "#22 Yes did not delete the message")
    # from the trash the prompt says it is for good
    tv.send("ShiftTab", "Down", "Down", "Down", "Down", "Down", settle=0.8)
    c.check(tv.wait("SUBJ2 plain", 5),
            "#22 the deleted message is not in the trash")
    tv.send("Tab", "CtrlD")
    c.check(tv.wait('Permanently delete "SUBJ2 plain"?', 4)
            and tv.has("This cannot be undone."),
            "#22 deleting from the trash does not say it is permanent")
    tv.send("n")
    c.check(tv.wait(lambda: not tv.has("Permanently"), 3)
            and tv.has("SUBJ2 plain"), "#22 No still deleted it")
    return tv


@case(8)
def up_on_first_folder_keeps_filter(opts):
    tv = Tv(opts)
    c = Case(tv)
    started(tv, c)
    tv.send("Tab", "bob")
    c.check(tv.wait("filter: bob_", 4), "#8 typing did not filter the list")
    tv.send("ShiftTab", "Up", settle=1.0)
    c.check(tv.has("filter: bob_") and not tv.has("SUBJ1"),
            "#8 Up on the first folder reloaded it and dropped the filter")
    return tv


@case(9, 14)
def body_scroll_survives(opts):
    tv = Tv(opts)
    c = Case(tv)
    started(tv, c)
    tv.send("Tab", "Home")                        # SUBJ1, the first row
    c.check(tv.wait("PARASTART", 4), "SUBJ1's body did not show")
    tv.send("Tab", "PgDn", "PgDn", settle=0.6)
    before = body_rows(tv)
    c.check(not any("PARASTART" in l for l in before)
            and any("filler" in l for l in before),
            "PgDn did not scroll the body")
    # #9: Up on the first message (no move) must not re-fetch it
    tv.send("ShiftTab", "Up", settle=1.0)
    c.check(body_rows(tv) == before,
            "#9 Up on the first message scrolled the body back to the top")
    # #14: nor does a reload
    tv.send("Tab", "F5", settle=1.5)
    c.check(body_rows(tv) == before, "#14 F5 scrolled the body back to line 1")
    return tv


@case(11)
def compose_label_hotkeys(opts):
    tv = Tv(opts)
    c = Case(tv)
    started(tv, c)
    open_compose(tv, c)
    for key, label, text in (("s", "Subject", "QWZXS"), ("b", "Bcc", "QWZXB"),
                             ("c", "Cc", "QWZXC"), ("t", "To", "QWZXT")):
        tv.send(alt(key), text, settle=0.5)
        c.check(field(tv, label) == text,
                "#11 Alt-%s did not move to the %s field" % (key.upper(), label))
    return tv


@case(27, 35)
def utf8_columns_align(opts):
    tv = Tv(opts)
    c = Case(tv)
    started(tv, c)
    pos = {s: tv.find(s) for s in ("SUBJ1", "SUBJ2", "SUBJ3")}
    c.check(all(pos.values()), "a fixture row is missing from the list")
    c.check(len({x for _, x in pos.values()}) == 1,
            "#27 the subject column does not line up: %s" % pos)
    # #35: a raw 8-bit UTF-8 From is listed, decoded
    c.check(tv.has("Zoë Ünïcödé") and tv.has("山田太郎"),
            "#35 a raw UTF-8 sender name is not listed")
    c.check("\ufffd" not in tv.text(), "#27 a UTF-8 character was split")
    for y, _ in pos.values():                     # nothing pushed off the frame
        c.check(tv.screen.buffer[y][COLS - 1].data == "║",
                "#27 row %d runs over the window frame" % y)
    return tv


@case(28)
def wrap_marker_and_tabs(opts):
    tv = Tv(opts)
    c = Case(tv)
    started(tv, c)
    tv.send("Tab", "Home")
    c.check(tv.wait("PARASTART", 4), "SUBJ1's body did not show")
    rows = body_rows(tv)
    a = next((i for i, l in enumerate(rows) if "PARASTART" in l), None)
    b = next((i for i, l in enumerate(rows) if "PARAEND" in l), None)
    c.check(a is not None and b is not None and b > a,
            "#28 the long line was not wrapped")
    words = " ".join(rows[a:b + 1]).split()
    c.check(all("word%02d" % i in words for i in range(40)),
            "#28 wrapping lost or split a word")
    tl = next((l for l in rows if "TAB1" in l), "")
    c.check(tl[tl.find("TAB1"):].startswith("TAB1    a       bb      ccc"),
            "#28 tabs are not expanded to 8-column stops: %r" % tl)
    tv.send("Tab", alt("v"), "w", settle=1.0)     # View > Wrap long lines: off
    rows = body_rows(tv)
    pl = next((l for l in rows if "PARASTART" in l), "")
    c.check("»" in pl and not any("PARAEND" in l for l in rows),
            "#28 an unwrapped long line has no >> marker: %r" % pl)
    tv.send(alt("v"), "w", settle=1.0)            # and back on
    c.check(any("PARAEND" in l for l in body_rows(tv)),
            "#28 wrapping did not come back on")
    return tv


@case(29)
def find_copy_paste(opts):
    tv = Tv(opts)
    c = Case(tv)
    started(tv, c)
    tv.send("Tab", "Home")
    c.check(tv.wait("PARASTART", 4), "SUBJ1's body did not show")
    tv.send("Tab", alt("e"), "f")
    c.check(tv.wait("Find", 3), "#29 Edit > Find did not open")
    tv.send("FINDME", "Enter", settle=1.0)
    c.check(any("FINDME sentinel" in l for l in body_rows(tv)),
            "#29 Find did not scroll to the match")
    tv.send("ShiftDown", settle=0.6)
    # the selected line: the one body row drawn in other colours
    top = next(y for y, l in enumerate(tv.lines()) if "Message " in l and "──" in l)
    x0 = tv.line(top).index("│") + 1
    xr = x0 + 40                                  # past the text: no find match
    looks = {}
    for y in range(top + 1, ROWS - 2):
        looks.setdefault(cell_colours(tv.screen.buffer[y][xr]), []).append(y)
    odd = [ys for ys in looks.values() if len(ys) == 1]
    c.check(len(looks) == 2 and odd, "#29 Shift-Down did not select one line")
    picked = tv.line(odd[0][0])[x0:].rstrip(" ║│▲▼▓▒■")
    tv.send("CtrlIns", settle=0.6)
    c.check(tv.has("Copied 1 line"), "#29 Ctrl-Ins did not copy the selection")
    open_compose(tv, c)
    tv.send("Tab", "Tab", "Tab", "Tab", "ShiftIns", settle=0.8)
    c.check(any(picked in l for l in tv.lines()[8:]) and picked,
            "#29 Shift-Ins did not paste %r into the compose body" % picked)
    tv.send("AltF3")
    c.check(tv.wait("Unsent message", 3), "closing the compose window did not ask")
    tv.send("d")
    c.check(tv.wait(lambda: not compose_open(tv), 3), "Discard did not close it")
    # View source and Help copy too (the whole text, when nothing is selected)
    tv.send(alt("m"), "v")
    c.check(tv.wait("Source of msg", 4), "View source did not open")
    tv.send("CtrlIns", settle=0.6)
    c.check(tv.has("Copied the whole text"), "#29 no copy in View source")
    tv.send("\x1b", "F1")
    c.check(tv.wait("THE THREE PANES", 4), "Help did not open")
    tv.send("CtrlIns", settle=0.6)
    c.check(tv.has("Copied the whole text"), "#29 no copy in Help")
    return tv


@case(30)
def help_has_no_lr_column(opts):
    tv = Tv(opts)
    c = Case(tv)
    started(tv, c)
    tv.send("F1")
    c.check(tv.wait("THE THREE PANES", 4), "Help did not open")
    seen, last = [], None
    for _ in range(60):                           # page to the end
        rows = tv.lines()[2:30]
        if rows == last:
            break
        seen += rows
        last = rows
        tv.send("PgDn", settle=0.25)
    text = "\n".join(seen)
    c.check("LOCAL / REMOTE" in text and "FOLDERS" in text,
            "could not read the Help text")
    c.check(not re.search(r"\bL\s*/\s*R\b", text) and "R column" not in text,
            "#30 Help still describes an L / R column")
    return tv


@case(31)
def send_menu_label(opts):
    tv = Tv(opts)
    c = Case(tv)
    started(tv, c)
    tv.send(alt("m"))
    c.check(tv.wait("Address book", 3), "the Message menu did not open")
    c.check(re.search(r"│ Send\s+F2 │", tv.text()) and not tv.has("Send draft"),
            "#31 the F2 menu item is not labelled Send")
    return tv


@case(41)
def about_box_whole(opts):
    tv = Tv(opts)
    c = Case(tv)
    started(tv, c)
    tv.send(alt("h"), "a")
    c.check(tv.wait("github.com/chrispollitt/TUIs", 4)
            and tv.has("See HISTORY.md"),
            "#41 Help > About is cut off or lacks the repo link")
    c.check(not tv.has("for a local mailbox"),
            "#41 About still calls tvmail a local-mailbox client")
    tv.send("Enter")
    c.check(tv.wait(lambda: not tv.has("See HISTORY.md"), 3),
            "Enter did not close the About box")
    return tv


@case(37)
def cjk_without_gaps(opts):
    tv = Tv(opts)
    c = Case(tv)
    started(tv, c)

    def solid(s, what):
        p = tv.find(s)
        c.check(p is not None, "#37 %r is not drawn as one run (%s)" % (s, what))
        y, x = p
        row = tv.screen.buffer[y]
        for i, ch in enumerate(s):                # each one: 2 cells, no gap
            c.check(row[x + 2 * i].data == ch and row[x + 2 * i + 1].data == "",
                    "#37 %r has a gap after %r (%s)" % (s, ch, what))
    solid("日本語", "subject column")
    solid("山田太郎", "sender column")
    tv.send("Tab", "Home")
    c.check(tv.wait("CJKLINE", 4), "SUBJ1's body did not show")
    solid("日本語のテキスト", "body")
    # a reply: the Subject input line and the compose editor
    tv.send("CtrlR")
    c.check(tv.wait("Re: SUBJ1", 5), "Ctrl-R did not open a reply")
    tv.pump(0.5)
    c.check("Re: SUBJ1 日本語" in (field(tv, "Subject") or ""),
            "#37 gaps in the reply's Subject: %r" % field(tv, "Subject"))
    solid("日本語のテキスト", "quoted in the reply")
    return tv


def box_around(tv, text):
    """(y0, x0, y1, x1) of the double-framed box that text is drawn in."""
    y, x = tv.find(text)
    buf = tv.screen.buffer
    for y0 in range(y, -1, -1):
        x0 = next((i for i in range(x, -1, -1) if buf[y0][i].data == "╔"), None)
        if x0 is not None:
            break
    else:
        return None
    x1 = next(i for i in range(x0 + 1, COLS) if buf[y0][i].data == "╗")
    y1 = next(i for i in range(y + 1, ROWS) if buf[i][x0].data == "╚")
    return y0, x0, y1, x1


def low_contrast(tv, limit=3.0, box=None):
    """The painted text cells (in box, or anywhere) whose fg / bg contrast
    is under limit."""
    plain = set(" ─│═║╔╗╚╝┌┐└┘├┤┬┴┼╟╢╤╧▒▓░■▲▼◄►↑↓▄▀█")
    y0, x0, y1, x1 = box or (0, 0, ROWS - 1, COLS - 1)
    bad = {}
    for y in range(y0, y1 + 1):
        row = tv.screen.buffer[y]
        for x in range(x0, x1 + 1):
            ch = row[x].data
            if not ch or ch in plain:
                continue
            fg, bg = cell_colours(row[x])
            r = contrast(fg, bg)
            if r < limit:
                bad.setdefault((y, fg, bg, round(r, 2)), []).append(ch)
    return ["row %d %r: #%s on #%s = %.2f:1" % (y, "".join(chs), fg, bg, r)
            for (y, fg, bg, r), chs in sorted(bad.items())]


@case(36)
def readable_colours(opts):
    tv = Tv(opts)
    c = Case(tv)
    started(tv, c)
    tv.pump(0.5)
    # (name, keys, ready-marker, score: whole screen / "box" only / not):
    # the screens #36 fixed.  The compose window is only the way to the
    # unsent-message box - its header labels are still under 3:1 (#42).
    for what, keys, ready, score in (
            ("main window, Folders focused", [], "[ Folders ]", True),
            ("main window, Messages focused", ["Tab"], "[ Messages ]", True),
            ("main window, body focused", ["Tab"], "[ Message ]", True),
            ("Window list", [alt("0")], "Window list", True),
            ("Message menu", ["\x1b", alt("m")], "Address book", True),
            ("delete message box", ["\x1b", "ShiftTab", "CtrlD"],
             "to the trash?", True),
            ("compose window", ["n", "CtrlN"], " Compose ", False),
            ("unsent message box", ["x", "AltF3"], "Unsent message", "box")):
        for k in keys:
            tv.send(k, settle=0.5)
        c.check(tv.wait(ready, 4), "could not open the %s" % what)
        tv.pump(0.3)
        bad = (low_contrast(tv, box=box_around(tv, ready)) if score == "box"
               else low_contrast(tv) if score else [])
        c.check(not bad, "#36 text below 3:1 contrast in the %s:\n  %s"
                % (what, "\n  ".join(bad)))
    return tv


@case(1)
def quit_asks_about_unsent_compose(opts):
    tv = Tv(opts)
    c = Case(tv)
    started(tv, c)
    open_compose(tv, c)
    tv.send("someone@example.com", settle=0.5)
    tv.send(alt("x"))
    c.check(tv.wait("Unsent message", 3),
            "#1 Alt-X did not ask about the unsent message")
    tv.send("\x1b", settle=0.8)                   # Cancel: no quit
    c.check(tv.alive() and field(tv, "To") == "someone@example.com",
            "#1 cancelling the quit lost the compose window")
    tv.send(alt("x"))
    c.check(tv.wait("Unsent message", 3), "#1 a second Alt-X did not ask again")
    tv.send("s")                                  # Save draft, then quit
    c.check(tv.wait(lambda: not tv.alive(), 6),
            "#1 Save draft did not go on to quit")
    c.check("someone@example.com" in tv.backend("show", "0", "drafts"),
            "#1 quitting with Save draft did not save the draft")
    return tv


@case(1, 26)
def quit_asks_about_unsaved_signature(opts):
    tv = Tv(opts)
    c = Case(tv)
    started(tv, c)
    tv.send(alt("f"), "g")
    c.check(tv.wait("Signature", 4), "File > Edit signature did not open")
    tv.send("SIGTEXT one", settle=0.5)
    tv.send(alt("x"))
    c.check(tv.wait("Save changes to ~/.signature?", 3),
            "#1 Alt-X did not ask about the unsaved signature")
    tv.send("\x1b", settle=0.8)
    c.check(tv.alive() and tv.has("SIGTEXT one"),
            "#1 cancelling the quit lost the signature")
    # #26: closing with Yes saves, with no extra box to dismiss
    tv.send("AltF3")
    c.check(tv.wait("Save changes to ~/.signature?", 3),
            "Alt-F3 did not ask to save the signature")
    tv.send("y", settle=1.0)
    c.check(not tv.has("SIGTEXT") and not tv.has("Signature saved.")
            and not tv.has("Information"),
            "#26 saving the signature on close showed an extra box")
    sig = tv.home / ".signature"
    c.check(sig.exists() and "SIGTEXT one" in sig.read_text(),
            "#26 the signature was not saved")
    # with nothing unsaved, a quit asks nothing
    tv.send(alt("f"), "g")
    c.check(tv.wait("SIGTEXT one", 4), "the signature did not reopen")
    tv.send(alt("x"))
    c.check(tv.wait(lambda: not tv.alive(), 5),
            "#1 Alt-X with nothing unsaved did not quit")
    return tv


@case(2)
def header_edits_count_as_changes(opts):
    tv = Tv(opts)
    c = Case(tv)
    started(tv, c)
    for key, label in (("s", "Subject"), ("c", "Cc"), ("b", "Bcc"), ("t", "To")):
        open_compose(tv, c)
        tv.send(alt(key), "x@example.com", settle=0.4)
        tv.send("AltF3")
        c.check(tv.wait("Unsent message", 3),
                "#2 closing after an edit to %s alone did not ask" % label)
        tv.send("d")
        c.check(tv.wait(lambda: not compose_open(tv), 3),
                "Discard did not close the compose window")
    return tv


@case(3)
def failed_save_keeps_window(opts):
    tv = Tv(opts, fail_save_draft=True)
    c = Case(tv)
    started(tv, c)
    flag = tv.home / "fail-save-draft"
    flag.write_text("")
    open_compose(tv, c)
    tv.send(alt("s"), "KEEPME draft", settle=0.4)
    tv.send("AltF3")
    c.check(tv.wait("Unsent message", 3), "closing did not ask")
    tv.send("s")
    c.check(tv.wait("Draft not saved", 5) and tv.has("disk full (test)"),
            "#3 a failed save did not say so (with the backend's error)")
    tv.send("Enter", settle=0.8)
    c.check(compose_open(tv) and field(tv, "Subject") == "KEEPME draft",
            "#3 a failed Save draft still closed the window")
    flag.unlink()
    tv.send("AltF3")
    c.check(tv.wait("Unsent message", 3), "closing again did not ask")
    tv.send("s")
    c.check(tv.wait(lambda: not compose_open(tv), 5),
            "a good save did not close the window")
    c.check("KEEPME draft" in tv.backend("list", "drafts"),
            "the draft was not saved")
    return tv


@case(4)
def source_draft_found_by_content(opts):
    def setup(t):
        for i, n in enumerate("ABC"):
            t.backend("save-draft", stdin=(
                "To: x@example.com\nSubject: DRAFT-%s\n"
                "Date: Mon, 01 Sep 2026 1%d:00:00 -0700\n\nbody %s\n"
                % (n, i, n)).encode())
    tv = Tv(opts, setup=setup)
    c = Case(tv)

    def subjects():
        return [l.split("\t")[4] for l in
                tv.backend("list", "drafts").splitlines() if "\t" in l]
    started(tv, c)
    c.check(subjects() == ["DRAFT-A", "DRAFT-B", "DRAFT-C"],
            "drafts not set up: %s" % subjects())
    tv.send("Down")                               # the drafts folder
    c.check(tv.wait("DRAFT-C", 5), "the drafts folder did not open")
    tv.send("Tab", "Up", "Enter")                 # edit DRAFT-B (index 1)
    c.check(tv.wait(" Compose ", 5) and tv.wait(
        lambda: field(tv, "Subject") == "DRAFT-B", 3),
        "Enter did not open DRAFT-B for editing")
    # meanwhile DRAFT-A goes: DRAFT-B is index 0 now, and index 1 DRAFT-C
    tv.send("F6", settle=0.8)
    tv.send("Up", "CtrlD")
    c.check(tv.wait('Move "DRAFT-A" to the trash?', 4), "could not delete DRAFT-A")
    tv.send("y", settle=1.5)
    c.check(subjects() == ["DRAFT-B", "DRAFT-C"], "DRAFT-A was not deleted")
    tv.send("F6", settle=0.8)
    c.check(field(tv, "Subject") == "DRAFT-B", "F6 did not go back to the draft")
    tv.send(alt("s"), "End", " v2", settle=0.4)
    tv.send("AltF3")
    c.check(tv.wait("Unsent message", 3), "closing the edited draft did not ask")
    tv.send("s", settle=1.5)
    c.check(sorted(subjects()) == ["DRAFT-B v2", "DRAFT-C"],
            "#4 saving deleted the wrong source draft: %s" % subjects())
    return tv


@case(5)
def out_of_office_keeps_entries(opts):
    sys.path.insert(0, str(HERE))
    from test_backend import FakeSieve
    sieve = FakeSieve()
    sieve.scripts["other"] = "keep;\n"
    sieve.active = "other"

    def setup(t):
        conf = t.tmp / "tvmail.conf"
        conf.write_text("[sieve]\nhost = 127.0.0.1\nport = %d\nuser = tester\n"
                        "tls = none\n" % sieve.port)
        t.env.update(TVMAIL_CONF=str(conf), TVMAIL_SIEVE_PASS="pw",
                     TVMAIL_POSTFIX_GENERIC=str(t.tmp / "no-such-generic"))
        t.cleanups.append(sieve.close)
    tv = Tv(opts, setup=setup)
    c = Case(tv)
    started(tv, c)
    tv.send(alt("f"), "o")
    c.check(tv.wait("Automatic reply", 5), "File > Out of office did not open")
    tv.send(" ", "Tab", "AWAYTEST", settle=0.4)  # ON, and a subject
    tv.send("Enter")
    c.check(tv.wait("is active", 5),
            "no warning about the other active Sieve script")
    tv.send("n", settle=1.0)                      # No: keep that one
    c.check(tv.has("Automatic reply") and tv.has("AWAYTEST") and tv.has("[X]"),
            "#5 No to replacing the other script threw away what was typed")
    sieve.reject_put = "quota exceeded"
    tv.send("Enter")
    c.check(tv.wait("is active", 5), "the warning did not come back")
    tv.send("y")
    c.check(tv.wait("quota exceeded", 5), "the server's refusal was not shown")
    tv.send("Enter", settle=1.0)
    c.check(tv.has("Automatic reply") and tv.has("AWAYTEST") and tv.has("[X]"),
            "#5 a rejected change threw away what was typed")
    sieve.reject_put = None
    tv.send("Enter")
    c.check(tv.wait("Out-of-office reply is ON", 5),
            "saving again did not switch it on")
    c.check("AWAYTEST" in sieve.scripts.get("tvmail-vacation", ""),
            "the saved script lacks the typed subject")
    return tv


@case(43)
def resize_keeps_backend(opts):
    # Resizes rain on tvmail while a co-process starts.  Each one used to
    # fail the pipe read (EINTR) as if the backend had died, and a failed
    # start gave the co-process up for the session.  The chat backend shows
    # it: it starts inside the running app (the mail one starts before the
    # resize handler exists).  A shim holds its banner back a second and
    # logs what tvmail asks it - the event polls only come if it survived.
    def setup(t):
        b = t.home / "bin"
        shutil.copy2(os.path.join(opts.backend_dir, "tvchat-backend"),
                     b / "tvchat-backend.real")
        shim = b / "tvchat-backend"
        shim.write_text(
            '#!/bin/sh\n'
            '[ "$1" = serve ] && sleep 1\n'
            'tee -a "$HOME/chat-requests" | "$HOME/bin/tvchat-backend.real" "$@"\n')
        shim.chmod(0o755)
        conf = t.tmp / "tvchat.conf"
        conf.write_text("[account:idle]\nprotocol = irc\nhost = 127.0.0.1\n")
        t.env["TVCHAT_CONF"] = str(conf)
    tv = Tv(opts, setup=setup)
    c = Case(tv)
    end = time.monotonic() + 3
    while time.monotonic() < end:
        try:
            os.kill(tv.pid, signal.SIGWINCH)
        except OSError:
            break
        tv.pump(0.03)
    started(tv, c)
    reqs = tv.home / "chat-requests"
    polls = lambda: [l for l in reqs.read_text().split("\n")
                     if l.startswith("events")] if reqs.exists() else []
    c.check(tv.wait(lambda: len(polls()) >= 3, 8),   # sync's, then the idle polls
            "#43 a resize while the chat backend started lost it: tvmail "
            "never polled (requests: %r)"
            % (reqs.read_text() if reqs.exists() else None))
    return tv


# ------------------------------------------------------------------- main ---
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True, help="path to the built tvmail")
    ap.add_argument("--backend-dir",
                    default=os.environ.get("TVMAIL_BACKEND_DIR", ""))
    ap.add_argument("-k", action="append", default=[],
                    help="only the cases for this bug number or case name")
    ap.add_argument("-v", action="store_true",
                    help="dump the final screen of every case")
    a = ap.parse_args()
    if os.name != "posix":
        skip("posix only")
    a.bin = str(Path(a.bin).resolve())
    if not os.path.exists(a.bin):
        skip("tvmail not built at %s" % a.bin)
    a.backend_dir = a.backend_dir or str(Path(a.bin).parent.parent / "backend")

    todo = [(b, fn) for b, fn in CASES if not a.k or any(
        k in [str(x) for x in b] + [fn.__name__] for k in a.k)]
    if not todo:
        print("no case matches", a.k)
        raise SystemExit(1)
    failed = []
    for bugs, fn in todo:
        tag = "%s %s" % ("/".join("#%d" % b for b in bugs), fn.__name__)
        t0 = time.monotonic()
        Tv.last = None
        try:
            fn(a)
            print("ok   %-46s %4.1fs" % (tag, time.monotonic() - t0), flush=True)
            if a.v and Tv.last:
                print(Tv.last.dump())
        except CheckFailed as e:
            failed.append(tag)
            print("FAIL %s: %s" % (tag, e), flush=True)
        except Exception:
            failed.append(tag)
            print("FAIL %s: crashed\n%s" % (tag, traceback.format_exc()), flush=True)
        finally:
            if Tv.last:
                Tv.last.close()
    if failed:
        print("FAILED %d of %d: %s" % (len(failed), len(todo), ", ".join(failed)))
        raise SystemExit(1)
    print("PASS: %d cases" % len(todo))


if __name__ == "__main__":
    main()
