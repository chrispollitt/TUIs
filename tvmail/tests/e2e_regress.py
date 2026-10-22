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
                 setup=None):
        self.fixture = fixture
        self.fail_save_draft = fail_save_draft
        super().__init__(opts, env=env, setup=setup)

    def prepare(self):
        # the shell preamble puts ~/bin first: an installed (older) backend
        # there would win over the one under test, so put ours there
        bindir = self.bindir
        real = bindir / "tvmail-backend"
        shutil.copy2(os.path.join(self.opts.backend_dir, "tvmail-backend"), real)
        if self.fail_save_draft:
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


# ------------------------------------------------------------------- chat ---
# tvmail with a tvchat.conf aimed at a fake IRC server (tests/fake_irc.py):
# account "fake" (display "Fake", nick "tester") autoconnects and joins #e2e.
def chat_tv(opts):
    from fake_irc import FakeIrcd
    srv = FakeIrcd()
    srv.start()

    def setup(t):
        shutil.copy2(os.path.join(opts.backend_dir, "tvchat-backend"),
                     t.home / "bin" / "tvchat-backend")
        conf = t.tmp / "tvchat.conf"
        conf.write_text(srv.conf())
        t.env["TVCHAT_CONF"] = str(conf)
        t.cleanups.append(srv.stop)
    try:
        tv = Tv(opts, setup=setup)
    except Exception:
        srv.stop()
        raise
    return tv, srv


def chat_heading(tv, name="#e2e — Fake"):
    """The row of the chat pane's heading showing name, focused ("[ name ]")
    or not ("  name  "), or None."""
    pat = re.compile(r"─(?:\[ |  )%s(?: \]|  )─" % re.escape(name))
    return next((y for y, l in enumerate(tv.lines()) if pat.search(l)), None)


def heading_active(tv, label):
    """The heading of that pane shows it has the focus."""
    return tv.has("[ %s" % label)


def in_chat_window(tv):
    return tv.has("tvmail chat") and tv.has("Servers")


def open_chat(tv, c, srv):
    """Alt-C, then the #e2e conversation of account Fake, the input focused."""
    c.check(srv.wait_joined("#e2e"), "the chat backend never joined #e2e")
    tv.send(alt("c"))
    c.check(tv.wait(lambda: in_chat_window(tv) and re.search(r"Fake\b.*connected", tv.text()), 5),
            "Alt-C did not bring up the chat window with Fake connected")
    c.check(heading_active(tv, "Servers"), "the chat window did not open on the server pane")
    tv.send("Enter")                              # Enter on a server: its conversations
    c.check(tv.wait(lambda: heading_active(tv, "Conversations"), 3) and
            tv.wait("* server messages", 3) and tv.has("#e2e"),
            "Enter on the server did not go to its conversations (server messages first)")
    tv.send("Down", "Enter")
    c.check(tv.wait(lambda: chat_heading(tv) is not None and heading_active(tv, "#e2e"), 5),
            "Down, Enter in the conversations did not open #e2e in the chat pane")


def log_line(tv, text):
    """(row, column) of text in the chat log, and that row."""
    p = tv.find(text)
    return (p, tv.line(p[0])) if p else (None, "")


@case()
def chat_window_three_panes(opts):
    tv, srv = chat_tv(opts)
    c = Case(tv)
    started(tv, c)
    c.check(srv.wait_joined("#e2e"), "the chat backend never joined #e2e")
    srv.say(":alice!a@h PRIVMSG #e2e :hello before the window")
    c.check(tv.wait(lambda: tv.line(ROWS - 1).rstrip().endswith("1 new"), 5),
            "an unread chat message did not show as \"1 new\" on the status line")
    tv.send(alt("c"))
    c.check(tv.wait(lambda: in_chat_window(tv) and tv.has("Fake (1)"), 5),
            "the server pane does not count the unread message: Fake (1)")
    c.check(tv.has("Alt-C Mail") and not tv.has("^D Del"),
            "the chat window has the mail window's status-line keys")
    tv.send("Enter")
    c.check(tv.wait("#e2e (1)", 3), "the conversation pane does not show #e2e (1)")
    # moving onto it shows it, but does not read it: as in mail, it is read
    # once the chat pane has the focus
    tv.send("Down", settle=0.8)
    p, row = log_line(tv, "<alice> hello before the window")
    c.check(p and re.search(r"\d\d:\d\d <alice> hello before the window", row),
            "the chat pane did not show the message from before")
    c.check(tv.has("the e2e channel"), "the topic is not in the log")
    tv.pump(0.8)
    c.check(tv.has("#e2e (1)"), "just moving onto the conversation read it")
    tv.send("Enter")
    c.check(tv.wait(lambda: "new" not in tv.line(ROWS - 1) and not tv.has("#e2e (1)"), 4),
            "with the chat pane focused, the unread count stayed")
    srv.say(":alice!a@h PRIVMSG #e2e :and after it opened",
            ":alice!a@h PRIVMSG #e2e :\x01ACTION waves\x01")
    c.check(tv.wait("<alice> and after it opened", 4),
            "a message arriving with the conversation shown did not show")
    c.check(tv.wait("* alice waves", 3), "/me is not shown as * nick action")
    c.check(tv.wait(lambda: "new" not in tv.line(ROWS - 1), 3),
            "a message arriving while you read counted as new")
    # Tab: servers -> conversations -> input, and round
    tv.send("Tab")
    c.check(tv.wait(lambda: heading_active(tv, "Servers"), 2), "Tab from the input did not go to Servers")
    tv.send("Tab")
    c.check(tv.wait(lambda: heading_active(tv, "Conversations"), 2), "Tab did not go on to Conversations")
    tv.send("Tab")
    c.check(tv.wait(lambda: heading_active(tv, "#e2e"), 2), "Tab did not go on to the chat pane")
    # Alt-C again: back to mail; there a new message counts, in the chat
    # window's title too, which Window > List shows
    tv.send(alt("c"), settle=0.6)
    c.check(tv.wait(lambda: tv.has("Folders") and not tv.has("Servers"), 3),
            "Alt-C in the chat window did not go back to mail")
    srv.say(":alice!a@h PRIVMSG #e2e :while you look elsewhere")
    c.check(tv.wait(lambda: tv.line(ROWS - 1).rstrip().endswith("1 new"), 4),
            "a message with the chat window behind is not \"1 new\"")
    tv.send(alt("0"))
    c.check(tv.wait("Window list", 3) and tv.has("tvmail chat (1)"),
            "Window > List does not show the chat window with its unread count")
    top = tv.find("Window list")[0]               # its rows, the first one current
    names = [l for l in tv.lines()[top + 1:] if re.search(r"tvmail - |tvmail chat", l)]
    down = next(i for i, l in enumerate(names) if "tvmail chat (1)" in l)
    tv.send(*(["Down"] * down + ["Enter"]), settle=0.6)
    c.check(tv.wait(lambda: chat_heading(tv) is not None and "new" not in tv.line(ROWS - 1), 4),
            "back in the chat window, on #e2e with the chat pane focused, the count stayed")
    # Esc goes back to mail too; Alt-C comes back to the same conversation
    tv.send("\x1b", settle=0.6)
    c.check(tv.wait(lambda: tv.has("Folders") and not tv.has("Servers"), 3),
            "Esc in the chat window did not go back to mail")
    tv.send(alt("c"))
    c.check(tv.wait(lambda: chat_heading(tv) is not None, 3) and tv.text().count("Servers") == 1,
            "Alt-C did not bring back the one chat window on #e2e")
    c.check(tv.alive(), "tvmail exited")
    return tv


@case()
def chat_history_pgup(opts):
    tv, srv = chat_tv(opts)
    c = Case(tv)
    started(tv, c)
    c.check(srv.wait_joined("#e2e"), "the chat backend never joined #e2e")
    srv.say(*[":alice!a@h PRIVMSG #e2e :backlog %02d" % i for i in range(80)])
    c.check(tv.wait(lambda: tv.line(ROWS - 1).rstrip().endswith("80 new"), 6),
            "the backlog did not arrive")
    open_chat(tv, c, srv)
    c.check(tv.has("backlog 79") and not tv.has("backlog 05"),
            "the chat pane did not open on the newest messages")
    for _ in range(16):                           # page up, past the top
        if tv.has("backlog 00"):
            break
        tv.send("PgUp", settle=0.3)
    c.check(tv.has("backlog 00"),
            "PgUp past the top did not fetch the older messages")
    # the first page fetched lands where it belongs: oldest at the top, in order
    rows = [l for l in tv.lines() if "backlog" in l]
    nums = [int(re.search(r"backlog (\d\d)", l).group(1)) for l in rows]
    c.check(nums == sorted(nums) and nums[0] == 0,
            "older messages are out of order: %s" % nums)
    tv.send("PgUp", settle=0.6)
    c.check(tv.has("No older messages"), "nothing says the history has run out")
    return tv


@case()
def chat_join_and_leave(opts):
    tv, srv = chat_tv(opts)
    c = Case(tv)
    started(tv, c)
    c.check(srv.wait_joined("#e2e"), "the chat backend never joined #e2e")
    tv.send(alt("a"), "j")
    c.check(tv.wait("nick / address to talk to", 3), "Chat > Join... did not ask")
    tv.send("#Other", "Enter", settle=0.8)
    c.check(srv.wait_joined("#other") and srv.sent("JOIN #Other"),
            "Join did not join the channel")
    c.check(tv.wait(lambda: chat_heading(tv, "#Other — Fake") is not None, 4),
            "Join did not show the channel in the chat window")
    c.check(tv.wait(lambda: re.search(r"│ ?#Other\b", tv.text()), 3),
            "the channel joined is not in the conversation list")
    tv.send(alt("a"), "l", settle=0.8)
    c.check(srv.sent("PART #Other"), "Chat > Leave did not part the channel")
    c.check(tv.wait(lambda: chat_heading(tv, "#Other — Fake") is None and
                    not re.search(r"│ ?#Other\b", tv.text()), 4),
            "the channel left is still shown or listed")
    c.check(tv.alive(), "tvmail exited")
    return tv


@case()
def chat_log_utf8_columns(opts):
    tv, srv = chat_tv(opts)
    c = Case(tv)
    started(tv, c)
    open_chat(tv, c, srv)
    long = "WRAPSTART " + " ".join("wörd%02d" % i for i in range(30)) + " WRAPEND"
    srv.say(":山田!y@h PRIVMSG #e2e :日本語のテキスト end",
            ":Zoë!z@h PRIVMSG #e2e :Ünïcödé after a two-byte nick",
            ":alice!a@h PRIVMSG #e2e :" + long)
    c.check(tv.wait("WRAPEND", 5), "the messages did not arrive")
    # wide characters take two cells each, no gaps, nothing after them shifted
    y, x = tv.find("日本語のテキスト")
    row = tv.screen.buffer[y]
    for i, ch in enumerate("日本語のテキスト"):
        c.check(row[x + 2 * i].data == ch and row[x + 2 * i + 1].data == "",
                "a gap after %r in the chat log" % ch)
    c.check(tv.line(y)[tv.line(y).index("テキスト") + 4:].startswith(" end"),
            "the text after the CJK run is out of place")
    ny, nx = tv.find("<山田>")
    c.check(ny == y and nx + 2 + 4 + 1 == x,
            "the CJK nick and its text do not line up: %r %r" % ((ny, nx), (y, x)))
    # every message's time stamp starts in the same column
    stamps = {tv.find(s)[1] - 6 for s in ("<山田>", "<Zoë>", "<alice> WRAPSTART")}
    c.check(len(stamps) == 1, "the time stamps do not line up: %s" % stamps)
    c.check(tv.has("<Zoë> Ünïcödé after"), "the two-byte nick line is wrong")
    # a long line wraps inside the pane on word boundaries
    sy, _ = tv.find("WRAPSTART")
    ey, _ = tv.find("WRAPEND")
    c.check(ey > sy, "the long message did not wrap")
    for yy in range(sy, ey + 1):
        c.check(not re.search(r"wörd\d\d[^\s║│▲▼▒■]", tv.line(yy)) and "»" not in tv.line(yy),
                "a wrapped row is cut mid-word or marked: %r" % tv.line(yy))
    c.check("\ufffd" not in tv.text(), "a UTF-8 character was split")
    return tv


@case()
def chat_unsent_input_kept(opts):
    tv, srv = chat_tv(opts)
    c = Case(tv)
    started(tv, c)
    open_chat(tv, c, srv)
    tv.send("half a thought", settle=0.5)
    # Esc goes to mail and keeps it; so does picking another conversation
    tv.send("\x1b", settle=0.6)
    c.check(tv.wait(lambda: tv.has("Folders") and not tv.has("Servers"), 3) and
            not tv.has("Discard"), "Esc did not just go back to mail")
    tv.send(alt("c"))
    c.check(tv.wait("> half a thought", 3), "the unsent text was lost going to mail and back")
    tv.send("ShiftTab", "Up", settle=0.8)         # the server messages
    c.check(tv.wait(lambda: not tv.has("> half a thought") and
                    chat_heading(tv, "Fake - server messages") is not None, 3),
            "another conversation shows the text typed for #e2e")
    tv.send("Down", settle=0.8)
    c.check(tv.wait("> half a thought", 3), "back on #e2e, the unsent text is gone")
    tv.send(alt("x"))
    c.check(tv.wait("Discard the chat message", 3),
            "Alt-X did not ask about the unsent chat message")
    tv.send("n", settle=0.8)                      # No: keep it, and tvmail
    c.check(tv.alive() and tv.wait("> half a thought", 3),
            "No to the discard question lost tvmail or the text")
    tv.send(alt("x"))
    c.check(tv.wait("Discard the chat message", 3), "Alt-X did not ask again")
    tv.send("y")                                  # Yes: discard, and quit
    c.check(tv.wait(lambda: not tv.alive(), 6), "Yes did not go on to quit")
    c.check(not srv.sent("PRIVMSG"), "the unsent text was sent")
    return tv


@case()
def chat_colours_readable(opts):
    tv, srv = chat_tv(opts)
    c = Case(tv)
    started(tv, c)
    open_chat(tv, c, srv)
    srv.say(":alice!a@h PRIVMSG #e2e :someone else's line",
            ":alice!a@h NOTICE #e2e :a notice is a system line")
    tv.send("my own line", "Enter", settle=0.8)
    c.check(tv.wait("<tester> my own line", 4), "our own line did not show")
    c.check(srv.sent("PRIVMSG #e2e :my own line"), "Enter did not send the line")
    c.check(tv.wait("-alice- a notice", 3), "the notice did not show")
    cols = {}
    for what, text in (("other", "someone else's line"), ("own", "my own line"),
                       ("system", "a notice is a system")):
        y, x = tv.find(text)
        cols[what] = cell_colours(tv.screen.buffer[y][x])
    c.check(len({fg for fg, _ in cols.values()}) == 3,
            "own, system and other lines are not three colours: %s" % cols)
    bad = low_contrast(tv)
    c.check(not bad, "#36 chat text below 3:1 contrast:\n  %s" % "\n  ".join(bad))
    return tv


@case(44)
def menus_stay_open_while_chat_polls(opts):
    tv, srv = chat_tv(opts)
    c = Case(tv)
    started(tv, c)
    c.check(srv.wait_joined("#e2e"), "the chat backend never joined #e2e")
    # connected: tvmail polls the chat backend every 250 ms, which shut any
    # open menu within a quarter second
    for key, item in (("f", "Out of office"), ("a", "Chat window")):
        tv.send(alt(key))
        c.check(tv.wait(item, 3), "Alt-%s did not open its menu" % key.upper())
        tv.pump(1.5)
        c.check(tv.has(item), "#44 the menu closed by itself while chat polled")
        tv.send("\x1b", "\x1b", settle=0.5)
    # the Chat menu lists the account with its state
    tv.send(alt("a"))
    c.check(tv.wait(lambda: re.search(r"Fake\s+connected", tv.text()), 3),
            "the Chat menu does not list the account as connected")
    tv.send("\x1b", "\x1b", settle=0.5)
    c.check(tv.alive(), "tvmail exited")
    return tv


@case()
def chat_menu_greyed_without_config(opts):
    tv = Tv(opts)                                 # no tvchat.conf
    c = Case(tv)
    started(tv, c)
    tv.send(alt("f"))
    c.check(tv.wait("Out of office", 3), "the File menu did not open")
    y, x = tv.find("Reload")
    enabled = cell_colours(tv.screen.buffer[y][x + 1])[0]
    tv.send("\x1b", "\x1b", alt("a"))
    c.check(tv.wait("no tvchat.conf", 3), "the Chat menu does not say chat is off")
    for item in ("Connect...", "Chat window", "Join...", "Leave"):
        y, x = tv.find(item)
        c.check(cell_colours(tv.screen.buffer[y][x + 1])[0] != enabled,
                "Chat > %s is not greyed without a tvchat.conf" % item)
    tv.send("\x1b", "\x1b", alt("c"), settle=0.6)
    c.check(not tv.has("tvmail chat"), "Alt-C opened the chat window with chat off")
    c.check(tv.alive(), "tvmail exited")
    return tv


# ------------------------------------------------------------------- main ---
if __name__ == "__main__":
    run("tvmail")
