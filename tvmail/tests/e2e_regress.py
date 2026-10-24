#!/usr/bin/env python3
"""Regression tests for tvmail's squashed UX bugs (BUGS.md, by #number).

Drives the built tvmail in a pty, like e2e_tui.py, but feeds the output to a
pyte screen model, so each check looks at the cells actually on screen - with
their columns and colours - instead of regexes over the output stream.

    python3 tests/e2e_regress.py --bin build/tvmail [--backend-dir backend]
                                 [-k 27] [-v]

Every case starts its own tvmail on a fresh temp HOME and fixture mbox.
The harness (Tv, Case, the screen and colour helpers) is
../../tvcommon/tests/tvscreen.py, shared with tvchat.
Exit codes:  0 pass, 1 fail, 77 skip (no pty, or pyte not installed).
"""
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# the pty + pyte harness tvmail and tvchat share
sys.path.insert(0, str(HERE.parent / "tvcommon" / "tests"))      # a source tarball
sys.path.insert(0, str(HERE.parent.parent / "tvcommon" / "tests"))
from tvscreen import *            # noqa: E402,F401,F403
import tvscreen                   # noqa: E402


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
class Tv(tvscreen.Tv):
    """One tvmail in a pty, on a fixture mbox in its own temp HOME."""

    prefix = "tvmail-regress-"
    drop_env = ("COLORTERM", "TVMAIL_CONF", "TVCHAT_CONF", "TVMAIL_IMAP_PASS", "MAILRC",
                "TVMAIL_SIEVE_PASS", "TVMAIL_MU_CONF", "TVMAIL_MU_TICKETS")

    def __init__(self, opts, fixture=FIXTURE, env=None, fail_save_draft=False,
                 setup=None, slow=None):
        self.fixture = fixture
        self.fail_save_draft = fail_save_draft
        self.slow = slow or {}      # {backend command: seconds it is held up}
        super().__init__(opts, env=env, setup=setup)

    def prepare(self):
        # the shell preamble puts ~/bin first: an installed (older) backend
        # there would win over the one under test, so put ours there
        bindir = self.bindir
        real = bindir / "tvmail-backend"
        shutil.copy2(os.path.join(self.opts.backend_dir, "tvmail-backend"), real)
        if self.fail_save_draft or self.slow:
            # save-draft, delete and the like never go through the serve
            # co-process, so a shim can fail one on demand (save-draft, while
            # ~/fail-save-draft exists) or hold one up: a cold Python start is
            # slow and uneven, and a test must wait for its result, not a time
            real = real.rename(bindir / "tvmail-backend.real")
            shim = bindir / "tvmail-backend"
            body = '#!/bin/sh\nfor a in "$@"; do\n'
            if self.fail_save_draft:
                body += ('  if [ "$a" = save-draft ] && [ -e "$HOME/fail-save-draft" ]; then\n'
                         '    cat >/dev/null; echo "tvmail-backend: disk full (test)"; exit 1\n'
                         '  fi\n')
            for cmd, secs in self.slow.items():
                body += '  [ "$a" = %s ] && sleep %s\n' % (cmd, secs)
            body += 'done\nexec "$HOME/bin/tvmail-backend.real" "$@"\n'
            shim.write_text(body)
            shim.chmod(0o755)
        self.spool = self.tmp / "spool.mbox"
        self.spool.write_bytes(self.fixture.encode("utf-8"))
        self.env.update(MAIL=str(self.spool), TVMAIL_MODE="local",
                        TVMAIL_MU_TICKETS=str(self.tmp / "no-tickets"))

    # -- backend, for setting up and inspecting mailboxes --
    def backend(self, *args, stdin=None):
        p = subprocess.run([str(self.home / "bin" / "tvmail-backend"), *args],
                           input=stdin, capture_output=True, env=self.env,
                           timeout=30)
        return p.stdout.decode("utf-8", "replace")


def started(tv, c):
    c.check(tv.wait("SUBJ2 plain", 15) and tv.wait("hello", 5),
            "tvmail never showed the fixture folder")


def body_rows(tv):
    """The body pane's text, row by row, below its "Message" rule (without
    the scroll bar, which only a focused pane draws)."""
    top = next((y for y, l in enumerate(tv.lines())
                if "Message " in l and "──" in l), 13)
    x0 = tv.line(top).index("│", 20) + 1       # past the folder table's own rules
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
    x0 = tv.line(top).index("│", 20) + 1
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
    tv.send("y")
    sig = tv.home / ".signature"
    c.check(tv.wait(lambda: sig.exists() and not tv.has("SIGTEXT"), 5),
            "#26 the signature was not saved")
    tv.pump(0.8)                                  # an extra box would be up by now
    c.check(not tv.has("Signature saved.") and not tv.has("Information"),
            "#26 saving the signature on close showed an extra box")
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
    # delete and save-draft held up 2 s: the checks below must wait for them
    tv = Tv(opts, setup=setup, slow={"delete": 2, "save-draft": 2})
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
    # delete and save run a one-shot backend (a cold Python start, slow and
    # uneven under Cygwin): wait for the result, never a fixed time
    tv.send("y")
    c.check(tv.wait(lambda: subjects() == ["DRAFT-B", "DRAFT-C"], 15), "DRAFT-A was not deleted")
    tv.send("F6")
    c.check(tv.wait(lambda: field(tv, "Subject") == "DRAFT-B", 5),
            "F6 did not go back to the draft")
    tv.send(alt("s"), "End", " v2", settle=0.4)
    tv.send("AltF3")
    c.check(tv.wait("Unsent message", 3), "closing the edited draft did not ask")
    tv.send("s")
    c.check(tv.wait(lambda: sorted(subjects()) == ["DRAFT-B v2", "DRAFT-C"], 15),
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


@case()
def redraw_clears_scribbles(opts):
    # tvcommon: Ctrl-L / Window > Redraw screen repaints what wall wrote over
    # the windows; mesg n while tvmail runs, put back on exit
    def setup(t):
        t.tty_mode = 0o620
    tv = Tv(opts, setup=setup)
    redraw_and_mesg(tv, Case(tv), started)
    return tv


# (#43's case - a resize while a co-process starts - is tvchat's now: its
# fix is tvcommon's CoProcess, and only tvchat starts one inside the running
# app; tvmail's mail backend starts before the resize handler exists.)


# ------------------------------------------------------------------- main ---
if __name__ == "__main__":
    run("tvmail")
