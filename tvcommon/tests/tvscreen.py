"""The pty + pyte harness tvmail's and tvchat's e2e_regress.py share.

An app runs in a pty on its own temp HOME; its output feeds a pyte screen
model, so each check looks at the cells actually on screen - with their
columns and colours - instead of regexes over the output stream.

    from tvscreen import *            # Tv, Case, case, run, keys, colours ...

A test module subclasses Tv for what its app needs (fixtures, helpers in
~/bin), registers its cases with @case(bug numbers...), and ends with
run("description").  Exit codes:  0 pass, 1 fail, 77 skip (no pty, or pyte
not installed).
"""
import argparse
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import traceback
from pathlib import Path

SKIP = 77


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
# theme tvmail's bug #36 was found under.  16-255 are xterm's fixed cube and
# grey ramp, and 24-bit colour is what it says - pyte already stores those as
# RGB.
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
    F6="\x1b[17~", End="\x1b[F", PgUp="\x1b[5~",
    AltF3="\x1b[1;3R", Up="\x1b[A", Down="\x1b[B", PgDn="\x1b[6~",
    Home="\x1b[H", ShiftTab="\x1b[Z", ShiftDown="\x1b[1;2B",
    CtrlIns="\x1b[2;5~", ShiftIns="\x1b[2;2~", Tab="\t", Enter="\r",
    CtrlN="\x0e", CtrlD="\x04", CtrlR="\x12",
)


def alt(ch):
    return "\x1b" + ch


# ---------------------------------------------------------------- harness ---
class CheckFailed(Exception):
    pass


class Tv:
    """One app in a pty, on its own temp HOME, read into a pyte screen.

    A subclass's prepare() fills the temp HOME (fixtures, helpers in
    self.bindir, self.env) before the app starts; setup, if given, runs
    after it, for what one case needs."""

    last = None          # the newest one, for run() to close
    # the pty's mode before the app starts (0o620: a login terminal that
    # takes messages); None leaves it as pty.fork() made it
    tty_mode = None
    prefix = "tv-regress-"
    # variables of the user's own setup that must not reach the app
    drop_env = ("COLORTERM",)

    def __init__(self, opts, env=None, setup=None, argv=None):
        Tv.last = self
        self.opts = opts
        self.argv = argv           # (prepare() may set it; default: just the binary)
        self.pid = None
        self.cleanups = []
        self.tmp = Path(tempfile.mkdtemp(prefix=self.prefix))
        self.home = self.tmp / "home"
        self.bindir = self.home / "bin"
        self.bindir.mkdir(parents=True)
        self.env = dict(os.environ)
        for k in self.drop_env:
            self.env.pop(k, None)
        self.env.update(
            HOME=str(self.home), XDG_DATA_HOME=str(self.tmp / "xdg"),
            TERM="xterm-256color", LANG="C.UTF-8", LC_ALL="C.UTF-8",
            USER="tester",
            # the shell preamble puts ~/bin first: an installed (older)
            # helper there would win over the one under test, so a
            # prepare() puts ours there
            PATH=str(self.bindir) + os.pathsep + os.environ.get("PATH", ""))
        self.prepare()
        self.env.update(env or {})
        if setup:
            setup(self)
        self.screen = ThemedScreen(COLS, ROWS)
        self.stream = pyte.ByteStream(self.screen)
        self.exited = None
        argv = self.argv or [opts.bin]
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            try:
                tty = os.ttyname(0)
                if self.tty_mode is not None:
                    os.chmod(tty, self.tty_mode)
                (self.tmp / "tty").write_text(tty)
                os.execvpe(argv[0], argv, self.env)
            finally:
                os._exit(127)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", ROWS, COLS, 0, 0))
        time.sleep(0.1)
        try:
            os.kill(self.pid, signal.SIGWINCH)
        except OSError:
            pass

    def prepare(self):
        """The app's own setup of the temp HOME (a subclass's)."""

    def tty(self):
        """The app's terminal: the pty's slave side."""
        return (self.tmp / "tty").read_text()

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


# --------------------------------------------------------------- checks ---
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


def tty_mode(tv):
    return stat.S_IMODE(os.stat(tv.tty()).st_mode)


def scribble(tv, text):
    """Write text onto the app's terminal from outside, as wall(1) does."""
    fd = os.open(tv.tty(), os.O_WRONLY | os.O_NOCTTY)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


def redraw_and_mesg(tv, c, started):
    """Both apps' case for tvcommon's full redraw and mesg n.  tv: started
    with tty_mode 0o620; started(tv, c) waits for its first screen."""
    started(tv, c)
    c.check(tty_mode(tv) == 0o600,
            "the terminal still takes messages while the app runs (mode %o)"
            % tty_mode(tv))
    tv.pump(0.5)
    before = tv.lines()
    # root's wall, where the cursor is: it may scroll the whole screen
    scribble(tv, "\r\nBroadcast message from root: JUNKJUNK going down\r\n")
    c.check(tv.wait("JUNKJUNK", 3), "the scribble never reached the screen")
    tv.send("\x0c", settle=0.6)                   # Ctrl-L
    c.check(not tv.has("JUNKJUNK"), "Ctrl-L left the scribble on the screen")
    c.check(tv.lines() == before, "Ctrl-L did not repaint the screen as it was")
    # Window > Redraw screen, away from where the menu drops down
    scribble(tv, "\x1b[%d;%dHSCRIBBLE" % (ROWS - 6, COLS - 20))
    c.check(tv.wait("SCRIBBLE", 3), "the second scribble never reached the screen")
    tv.send(alt("w"))
    c.check(tv.wait("Redraw screen", 3), "Window > Redraw screen is not in the menu")
    tv.send("r", settle=0.6)
    c.check(not tv.has("SCRIBBLE"), "Window > Redraw screen left the scribble")
    c.check(tv.lines() == before, "Window > Redraw screen did not repaint as it was")
    tv.send(alt("x"))
    c.check(tv.wait(lambda: not tv.alive(), 5), "Alt-X did not end the app")
    c.check(tty_mode(tv) == 0o620,
            "the terminal's mode was not put back on exit (%o)" % tty_mode(tv))


# ------------------------------------------------------------------ run ---
def run(app):
    """The command line: --bin APP [--backend-dir DIR] [-k BUG|NAME] [-v];
    runs the registered cases.  app: the binary's name, for messages."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True, help="path to the built %s" % app)
    ap.add_argument("--backend-dir", default="",
                    help="where its backend helper is (default: ../backend)")
    ap.add_argument("-k", action="append", default=[],
                    help="only the cases for this bug number or case name")
    ap.add_argument("-v", action="store_true",
                    help="dump the final screen of every case")
    a = ap.parse_args()
    if os.name != "posix":
        skip("posix only")
    a.bin = str(Path(a.bin).resolve())
    if not os.path.exists(a.bin):
        skip("%s not built at %s" % (app, a.bin))
    a.backend_dir = a.backend_dir or str(Path(a.bin).parent.parent / "backend")

    todo = [(b, fn) for b, fn in CASES if not a.k or any(
        k in [str(x) for x in b] + [fn.__name__] for k in a.k)]
    if not todo:
        print("no case matches", a.k)
        raise SystemExit(1)
    failed = []
    for bugs, fn in todo:
        tag = "%s %s" % ("/".join("#%d" % b for b in bugs) or "--", fn.__name__)
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


# ------------------------------------------------------------ screenshots ---
def render_png(tv, path, size=18, font="/cygdrive/c/Windows/Fonts/consola.ttf"):
    """The app's screen as a PNG (for the READMEs): every cell's colours and
    character, in a monospace font.  Needs Pillow and the font file; the
    cursor and bold are not drawn."""
    from PIL import Image, ImageDraw, ImageFont
    f = ImageFont.truetype(font, size)
    cw = round(f.getlength("M"))
    ch = round(size * 1.25)
    img = Image.new("RGB", (cw * COLS, ch * ROWS))
    d = ImageDraw.Draw(img)
    for y in range(ROWS):
        row = tv.screen.buffer[y]
        for x in range(COLS):
            c = row[x]
            fg, bg = cell_colours(c)
            d.rectangle([x * cw, y * ch, (x + 1) * cw - 1, (y + 1) * ch - 1],
                        fill="#" + bg)
        for x in range(COLS):
            c = row[x]
            if c.data.strip():
                fg, bg = cell_colours(c)
                d.text((x * cw, y * ch), c.data, font=f, fill="#" + fg)
    img.save(path, optimize=True)
