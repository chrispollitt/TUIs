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
                 setup=None, slow=None, log_serve=False):
        self.fixture = fixture
        self.fail_save_draft = fail_save_draft
        self.slow = slow or {}      # {backend command: seconds it is held up}
        self.log_serve = log_serve  # keep every request the serve co-process gets in ~/serve.log
        super().__init__(opts, env=env, setup=setup)

    def prepare(self):
        # the shell preamble puts ~/bin first: an installed (older) backend
        # there would win over the one under test, so put ours there
        bindir = self.bindir
        real = bindir / "tvmail-backend"
        shutil.copy2(os.path.join(self.opts.backend_dir, "tvmail-backend"), real)
        # Floyd's shared tools, found beside the backend
        for d in (HERE.parent.parent / "tvcommon" / "py", HERE.parent / "tvcommon" / "py"):
            if (d / "tvfloyd.py").exists():
                shutil.copy2(d / "tvfloyd.py", bindir / "tvfloyd.py")
                break
        if self.fail_save_draft or self.slow or self.log_serve:
            # save-draft, delete and the like never go through the serve
            # co-process, so a shim can fail one on demand (save-draft, while
            # ~/fail-save-draft exists) or hold one up: a cold Python start is
            # slow and uneven, and a test must wait for its result, not a time
            real = real.rename(bindir / "tvmail-backend.real")
            shim = bindir / "tvmail-backend"
            body = '#!/bin/sh\nfor a in "$@"; do\n  :\n'
            if self.fail_save_draft:
                body += ('  if [ "$a" = save-draft ] && [ -e "$HOME/fail-save-draft" ]; then\n'
                         '    cat >/dev/null; echo "tvmail-backend: disk full (test)"; exit 1\n'
                         '  fi\n')
            for cmd, secs in self.slow.items():
                body += '  [ "$a" = %s ] && sleep %s\n' % (cmd, secs)
            body += 'done\n'
            if self.log_serve:
                # the co-process reads its requests from stdin: tee them to a file for the test to count
                body += ('for a in "$@"; do\n  [ "$a" = serve ] && { tee -a "$HOME/serve.log" | '
                         '"$HOME/bin/tvmail-backend.real" "$@"; exit $?; }\ndone\n')
            body += 'exec "$HOME/bin/tvmail-backend.real" "$@"\n'
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


@case()
def search_messages_looks_inside_the_folder(opts):
    # Edit > Search messages (Ctrl-F): a word that is only in a message's text narrows the list to that message
    # (the type-to-filter sees only From / Subject); Esc brings the others back; no hit leaves the list alone
    tv = Tv(opts)
    c = Case(tv)
    started(tv, c)
    tv.send("", settle=1.0)
    c.check(tv.wait("Search messages", 4), "Ctrl-F did not open the search dialog")
    tv.send("FINDME", "Enter", settle=2.0)
    c.check(tv.wait('search: "FINDME"', 6) and tv.find("SUBJ1") is not None
            and tv.find("SUBJ2 plain") is None and tv.find("SUBJ3 wide sender") is None,
            "the search did not narrow the list to the message whose text has the word")
    tv.send("\x1b", settle=1.0)                                  # Esc: the list has the focus after a search
    c.check(tv.wait("SUBJ2 plain", 5) and tv.find("SUBJ3 wide sender") is not None and not tv.has("search:"),
            "Esc did not end the search")
    tv.send("", settle=1.0)
    tv.send("zebra", "Enter", settle=0.3)
    c.check(tv.wait("Nothing found for", 6), "a search with no hit did not say so")
    return tv


@case(7)
def esc_closes_windows(opts):
    def setup(t):
        (t.home / ".mailrc").write_text("alias bobby bob@example.com\n")
    tv = Tv(opts, setup=setup)
    c = Case(tv)
    started(tv, c)
    for name, keys, title in (
            ("Help", ["F1"], "THE PANES"),
            ("View source", [alt("m"), "i", "v"], "Source of msg"),
            ("Parts", [alt("m"), "i", "p"], "Parts of msg"),
            ("Address book", ["F4"], "Address book")):
        tv.send(*keys)
        c.check(tv.wait(title, 5), "#7 %s did not open" % name)
        tv.send("\x1b")
        c.check(tv.wait(lambda: not tv.has(title), 3),
                "#7 Esc did not close %s" % name)
    c.check(tv.alive(), "#7 tvmail exited")
    return tv


INVITE_FIXTURE = "".join([
    _msg("Carol <carol@example.org>", "Mon, 01 Sep 2026 09:00:00 -0700", "Chat invitation",
         "Carol invites you to chat.\n\nhttps://i.delta.chat/#ABC123&v=3&a=carol%40example.org&n=\n"),
    _msg("Bob <bob@example.com>", "Mon, 01 Sep 2026 11:00:00 -0700", "SUBJ2 plain", "hello")])


def chat_link_tv(opts, fixture=FIXTURE):
    """A tvmail with a stand-in tvchat-backend on PATH (the link is optional: it only
    needs the program to exist) and its request directory in the temp HOME."""
    def setup(t):
        stub = t.bindir / "tvchat-backend"
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
        t.requests = t.tmp / "chat-requests"
        t.env["TVCHAT_REQUESTS"] = str(t.requests)
    return Tv(opts, fixture=fixture, setup=setup)


def requests_of(tv):
    d = tv.requests
    return sorted(f.read_text().strip() for f in d.glob("*.req")) if d.exists() else []


@case()
def chat_with_sender_leaves_a_request(opts):
    # the optional tvmail -> tvchat link: Message > Chat with sender writes one
    # line into the directory tvchat's backend watches
    tv = chat_link_tv(opts)
    c = Case(tv)
    started(tv, c)
    tv.send(alt("m"), "t", settle=0.8)
    c.check(tv.wait("Asked tvchat to chat with", 5) and tv.has("bob@example.com."),
            "Message > Chat with sender did not say it asked tvchat")
    c.check(requests_of(tv) == ["chat bob@example.com"],
            "the request file is wrong: %r" % requests_of(tv))
    return tv


@case()
def share_in_chat_leaves_a_request(opts):
    # Message > Share in chat asks tvchat to say sender and subject (never the body); tvchat asks you
    tv = chat_link_tv(opts)
    c = Case(tv)
    started(tv, c)
    tv.send(alt("m"), "e", "c", settle=0.8)
    c.check(tv.wait("Asked tvchat to share", 5), "Message > Share in chat did not say it asked tvchat")
    c.check(requests_of(tv) == ["share tvmail: Bob - SUBJ2 plain"],
            "the request file is wrong: %r" % requests_of(tv))
    return tv


@case()
def toast_appears_goes_away_and_is_kept(opts):
    # a message that needs no answer is a toast at the bottom right, not a box: it takes no
    # key, goes away by itself, and stays in Window > Notification history
    tv = chat_link_tv(opts)
    c = Case(tv)
    started(tv, c)
    tv.send(alt("m"), "t", settle=0.8)   # Chat with sender asks tvchat: a toast
    c.check(tv.wait("Asked tvchat to chat with", 5), "no toast after Chat with sender")
    p = tv.find("Asked tvchat to chat with")
    c.check(p and p[1] > 40 and p[0] > 18, "the toast is not at the bottom right: %r" % (p,))
    # nothing modal: a key goes to the window behind it (Tab moves to the message pane)
    tv.send("Tab", settle=0.5)
    c.check(tv.has("[ Messages ]") or tv.has("Messages"), "the toast took the key")
    c.check(tv.wait(lambda: not tv.has("Asked tvchat to chat with"), 9), "the toast did not go away by itself")
    tv.send(alt("w"), "h", settle=1.0)
    c.check(tv.wait("Notification history", 4) and tv.has("Asked tvchat to chat with"),
            "the history does not hold the toast")
    return tv


@case()
def spool_shows_tvchats_notes_as_toasts(opts):
    # tvchat leaves a note in tvmail's inbox of the shared spool (an invitation, a message
    # to answer): it is a toast here, "tvchat: ...", and the file is gone once read
    def setup(t):
        t.spool = t.tmp / "spool"
        (t.spool / "tvmail").mkdir(parents=True)
        t.env["TV_SPOOL_DIR"] = str(t.spool)
    tv = Tv(opts, setup=setup)
    c = Case(tv)
    started(tv, c)
    name = "%d-1-1.note" % (int(time.time()) + 600)
    tmp = tv.spool / "tvmail" / (".tmp-" + name)
    tmp.write_text("1\ttvchat\tinfo\tcarol invites you - tvchat: Chat > Accept invite...\n")
    os.rename(tmp, tv.spool / "tvmail" / name)
    c.check(tv.wait("tvchat: carol invites you", 6), "a note from tvchat did not show as a toast")
    c.check(tv.wait(lambda: not list((tv.spool / "tvmail").glob("*.note")), 3),
            "the note was not removed once read")
    return tv


@case()
def chat_invite_in_a_message_goes_to_tvchat(opts):
    tv = chat_link_tv(opts, INVITE_FIXTURE)
    c = Case(tv)
    started(tv, c)
    tv.send("Tab", "Up", settle=0.8)               # the invitation (the older one)
    c.check(tv.wait("Chat invitation", 4), "could not select the invitation")
    tv.send(alt("m"), "t", settle=0.8)   # Chat with sender: an invite link in the message wins
    c.check(tv.wait("Handed the invitation to tvchat.", 6),
            "Message > Chat with sender did not hand the invite link over")
    c.check(requests_of(tv) == ["invite https://i.delta.chat/#ABC123&v=3&a=carol%40example.org&n="],
            "the request file is wrong: %r" % requests_of(tv))
    # a message without a link: the same entry opens a chat with the sender instead
    tv.send("Down", settle=0.6)
    tv.send(alt("m"), "t", settle=0.8)
    c.check(tv.wait("Asked tvchat to chat with", 5), "a message without a link did not fall back to Chat with sender")
    c.check(len(requests_of(tv)) == 2 and "chat bob@example.com" in requests_of(tv),
            "the second request is wrong: %r" % requests_of(tv))
    return tv


@case()
def config_editor_verify_and_apply(opts):
    # File > Edit configuration...: a new file opens as the sample, F9 checks the text, F8
    # saves it and has the backend start afresh on it; a bad edit is reported, not saved
    def setup(t):
        t.conf = t.tmp / "conf" / "tvmail.conf"
        t.env["TVMAIL_CONF"] = str(t.conf)
        sample = t.tmp / "sample.conf"
        sample.write_text("# sample\n[service]\nmode = local\n")
        t.env["TVMAIL_CONFIG_SAMPLE"] = str(sample)
    tv = Tv(opts, setup=setup)
    c = Case(tv)
    started(tv, c)
    tv.send(alt("f"), "c", settle=1.0)
    c.check(tv.wait("[service]", 5) and tv.has("F9 verify"), "File > Edit configuration... did not open the sample")
    tv.send("F9", settle=1.2)
    c.check(tv.wait("checks out", 6), "Verify did not pass the sample")
    c.check(not tv.conf.exists(), "Verify saved the file")
    tv.send("F8", settle=1.5)
    c.check(tv.wait("Applied: local", 8), "Save + apply did not say it applied")
    c.check(tv.conf.exists() and "mode = local" in tv.conf.read_text(), "the file was not written")
    tv.send("CtrlEnd", "Enter", "[imap]", "Enter", "port = 99999", "Enter", settle=0.6)
    tv.send("F8", settle=1.5)
    c.check(tv.wait("must be a port", 6), "a bad port was not reported")
    c.check("99999" not in tv.conf.read_text(), "a file with an error was saved")
    tv.send("AltF3", settle=0.6)
    c.check(tv.wait("Save changes to", 4), "closing the edited window did not ask")
    tv.send("n", settle=0.6)
    c.check(tv.alive(), "tvmail exited")
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


def served(tv, verb):
    """How many `verb` requests the serve co-process has been sent (see Tv(log_serve=True))."""
    try:
        lines = (tv.home / "serve.log").read_text(errors="replace").splitlines()
    except OSError:
        return 0
    return sum(1 for l in lines if re.search(r"(^|\s)%s(\s|$)" % re.escape(verb), l.split(" --")[0].strip()[:40]))


@case(17)
def keys_in_a_row_ask_the_backend_once(opts):
    # BUGS.md #17 / issue #4: the highlight moves at once, but a run of arrow keys (or typed filter letters) in the
    # folder or message pane costs one round trip for where it stops, not one for each row it passed
    tv = Tv(opts, fixture=FIXTURE_TALL, log_serve=True)
    c = Case(tv)
    started(tv, c)
    tv.send("Tab", settle=0.8)                       # the Messages pane, on the newest message
    before = served(tv, "show")
    c.check(before >= 1, "no 'show' request was logged for the first message (the test's own counting is off)")
    tv.send("Up", "Up", "Up", "Up", "Up", settle=1.0)  # 80 ms apart: well inside the debounce
    c.check(tv.wait("filler body 11", 5), "the body of the message the keys stopped on never showed")
    n = served(tv, "show") - before
    c.check(1 <= n <= 2, "five Ups in a row fetched %d message texts, not 1 (or 2 if the keys stalled)" % n)
    # typing a filter: the text of the row it lands on, once
    before = served(tv, "show")
    tv.send("f", "i", "l", "l", "e", "r", settle=1.0)
    c.check(tv.wait("filter: filler", 4), "the filter text did not show")
    n = served(tv, "show") - before
    c.check(n <= 2, "six filter letters fetched %d message texts" % n)
    # the folder pane: three Downs, one list
    tv.send("", "ShiftTab", settle=0.8)
    before = served(tv, "list")
    tv.send("Down", "Down", "Down", settle=1.0)
    n = served(tv, "list") - before
    c.check(1 <= n <= 2, "three Downs in the folder pane read %d folders, not 1 (or 2 if the keys stalled)" % n)
    return tv


@case(18)
def one_shot_backend_calls_show_working(opts):
    # BUGS.md #18 / issue #5: delete (a cold backend, held up 2 s here) says "Working..." while it runs
    tv = Tv(opts, slow={"delete": 2})
    c = Case(tv)
    started(tv, c)
    tv.send("Tab", "CtrlD")
    c.check(tv.wait('Move "SUBJ2 plain" to the trash?', 4), "the delete prompt did not come")
    tv.send("y", settle=0.1)
    c.check(tv.wait("Working...", 3), "no 'Working...' while the delete ran")
    c.check(tv.wait(lambda: not tv.has("Working..."), 6) and not tv.has("SUBJ2 plain"),
            "the 'Working...' line stayed, or the delete did not happen")
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


@case(6)
def editor_keeps_ctrl_d_r_n(opts):
    # Ctrl-D / Ctrl-R / Ctrl-N are Delete / Reply / New in the menu bar and status line, but in an editor they
    # are cursor right, page up, ...: while a compose window is active the editor gets them
    tv = Tv(opts)
    c = Case(tv)
    started(tv, c)
    open_compose(tv, c)
    tv.send("Tab", "Tab", "Tab", "Tab", "ab", "Home", settle=0.5)       # To, Cc, Bcc, Subject, then the body
    tv.send("CtrlD", "Z", settle=0.5)                                    # right one character, then type
    c.check(tv.wait(lambda: tv.has("aZb"), 3),
            "#6 Ctrl-D in the compose body did not move the cursor right")
    c.check(not tv.has("to the trash?"), "#6 Ctrl-D in the compose body asked to delete a message")
    tv.send("CtrlN", settle=0.7)
    c.check(sum(1 for l in tv.lines() if " Compose " in l) == 1,
            "#6 Ctrl-N in the compose body opened another compose window")
    # and the keys are the app's again once the window is gone
    tv.send("AltF3")                                                     # close it, discarding the draft
    c.check(tv.wait("Unsent message", 3), "#6 closing the modified compose window did not ask")
    tv.send("d", settle=0.5)
    c.check(tv.wait(lambda: not compose_open(tv), 5), "#6 the compose window did not close")
    tv.send("Tab", "CtrlD")
    c.check(tv.wait("to the trash?", 4),
            "#6 Ctrl-D did not ask to delete after the compose window was closed")
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
    tv.send(alt("m"), "i", "v")
    c.check(tv.wait("Source of msg", 4), "View source did not open")
    tv.send("CtrlIns", settle=0.6)
    c.check(tv.has("Copied the whole text"), "#29 no copy in View source")
    tv.send("\x1b", "F1")
    c.check(tv.wait("THE PANES", 4), "Help did not open")
    tv.send("CtrlIns", settle=0.6)
    c.check(tv.has("Copied the whole text"), "#29 no copy in Help")
    return tv


@case(30)
def help_has_no_lr_column(opts):
    tv = Tv(opts)
    c = Case(tv)
    started(tv, c)
    tv.send("F1")
    c.check(tv.wait("THE PANES", 4), "Help did not open")
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


@case()
def contacts_window_adds_edits_and_deletes(opts):
    # File > Contacts... (tvcommon's window, the same in tvmail, tvchat and tvcal): the address book on the server,
    # against a fake CardDAV server in this process; every change goes through tvcontacts.py
    import http.server
    import importlib.util
    import threading
    spec = importlib.util.spec_from_file_location(
        "tvcontacts_tests", str(HERE.parent.parent / "tvcommon" / "tests" / "test_tvcontacts.py"))
    fake = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fake)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), fake.Fake)
    srv.cards = {"alice.vcf": (fake.ALICE, "1"), "family.vcf": (fake.FAMILY, "1"), "bob.vcf": (fake.BOB, "1")}
    srv.log = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ours = lambda needle: [k for k, v in srv.cards.items() if needle in v[0]]

    def setup(t):
        t.env["TVCONTACTS_URL"] = "http://127.0.0.1:%d/c/" % srv.server_address[1]
        t.env["TVCONTACTS_USER"] = "chris"
        t.env["TVCONTACTS_PASS"] = "secret"                      # allowlist secret: test value
        t.env["TVCONTACTS_PY"] = str(HERE.parent.parent / "tvcommon" / "py" / "tvcontacts.py")
        t.cleanups.append(srv.shutdown)
    tv = Tv(opts, setup=setup)
    c = Case(tv)
    started(tv, c)
    tv.send(alt("f"), "n", settle=1.5)                          # File > Contacts...
    c.check(tv.wait("Alice Archer", 8) and tv.has("Bob Baker") and tv.has("[G] Family"),
            "the Contacts window does not list the address book")
    c.check(tv.has("(Al,Ally)") and tv.has("alice@example.org"), "nicknames and addresses are not shown")
    # a new contact
    tv.send("n", settle=0.8)
    c.check(tv.wait("New contact", 3), "n did not open the new-contact dialog")
    tv.send("Carol Carter", "Tab", "Caz", "Tab", "carol@example.org", "Enter", settle=2.0)
    c.check(tv.wait("Carol Carter", 6), "the new contact is not in the list")
    c.check(len(ours("carol@example.org")) == 1, "the server did not get the new contact")
    # change her address (the list is Alice, Bob, Carol, then the group)
    tv.send("Down", "Down", "e", settle=0.8)
    c.check(tv.wait("Edit contact", 3) and tv.has("carol@example.org"), "e did not open Carol for editing")
    tv.send("Tab", "Tab", "carol@new.example", "Enter", settle=2.0)
    c.check(tv.wait("carol@new.example", 6), "the changed address is not shown")
    c.check(ours("carol@new.example") and not ours("carol@example.org"), "the server did not take the change")
    # delete her
    tv.send("Delete", settle=0.8)
    c.check(tv.wait("Delete the contact", 3), "Del did not ask")
    tv.send("y", settle=2.0)
    c.check(tv.wait(lambda: not tv.has("Carol Carter"), 6) and not ours("Carol Carter"), "Carol was not deleted")
    # a group from names
    tv.send("g", settle=0.8)
    c.check(tv.wait("New group", 3), "g did not open the new-group dialog")
    tv.send("Pals", "Tab", "Alice Archer, Bob Baker", "Enter", settle=2.0)
    c.check(tv.wait("[G] Pals", 6) and ours("Pals"), "the new group is not there")
    # a member that is not in the book is refused in words, and nothing is written
    tv.send("g", settle=0.8)
    tv.send("Nope", "Tab", "Zed Zoe", "Enter", settle=2.0)
    c.check(tv.wait("not in the address book: Zed Zoe", 4) and not ours("Nope"), "an unknown member was not refused")
    # Message > Send to > Contacts: the sender of the message shown goes into the book (once)
    tv.send("", settle=0.6)                                  # close the Contacts window
    tv.send(alt("m"), "e", "s", settle=2.5)
    c.check(tv.wait("added Bob <bob@example.com>", 6) and ours("bob@example.com"), "the sender was not added to the book")
    tv.send(alt("m"), "e", "s", settle=2.5)
    c.check(tv.wait("already in the address book", 6) and len(ours("bob@example.com")) == 1, "the sender was added twice")
    return tv


@case()
def contacts_window_shows_and_saves_in_the_family_book(opts):
    # a shared book beside the person's own: its entries are tagged [family], a new entry asks where to be saved, and
    # an old one is changed in its own book
    import http.server
    import importlib.util
    import threading
    spec = importlib.util.spec_from_file_location(
        "tvcontacts_tests2", str(HERE.parent.parent / "tvcommon" / "tests" / "test_tvcontacts.py"))
    fake = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fake)
    mine = http.server.ThreadingHTTPServer(("127.0.0.1", 0), fake.Fake)
    mine.cards = {"alice.vcf": (fake.ALICE, "1")}
    mine.log = []
    fam = http.server.ThreadingHTTPServer(("127.0.0.1", 0), fake.Fake)
    fam.cards = {"gran.vcf": (fake.TestLibrary.GRAN, "1")}
    fam.log = []
    for srv in (mine, fam):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    in_ = lambda srv, needle: [k for k, v in srv.cards.items() if needle in v[0]]

    def setup(t):
        t.env["TVCONTACTS_URL"] = "http://127.0.0.1:%d/c/" % mine.server_address[1]
        t.env["TVCONTACTS_SHARED"] = "http://127.0.0.1:%d/c/" % fam.server_address[1]
        t.env["TVCONTACTS_USER"] = "chris"
        t.env["TVCONTACTS_PASS"] = "secret"                      # allowlist secret: test value
        t.env["TVCONTACTS_PY"] = str(HERE.parent.parent / "tvcommon" / "py" / "tvcontacts.py")
        t.cleanups += [mine.shutdown, fam.shutdown]
    tv = Tv(opts, setup=setup)
    c = Case(tv)
    started(tv, c)
    tv.send(alt("f"), "n", settle=1.5)
    c.check(tv.wait("Alice Archer", 8) and tv.has("[family] Grandma Gray"), "the shared book's entries are not tagged [family]")
    tv.send("n", settle=0.8)
    c.check(tv.wait("New contact", 3) and tv.has("Save in") and tv.has("(shared)"), "a new entry does not ask which book")
    tv.send("Cousin Cy", "Tab", "", "Tab", "cy@example.org", "Tab", "f", "Enter", settle=2.5)
    c.check(tv.wait("[family] Cousin Cy", 6), "the new entry is not shown in the family book")
    c.check(len(in_(fam, "cy@example.org")) == 1 and not in_(mine, "cy@example.org"), "it was not saved in the family book")
    # the one for the person alone goes in her own book (the default)
    tv.send("n", settle=0.8)
    tv.send("Dee Dunn", "Tab", "", "Tab", "dee@example.org", "Enter", settle=2.5)
    c.check(len(in_(mine, "dee@example.org")) == 1 and not in_(fam, "dee@example.org"), "the default did not go to her own book")
    return tv


@case()
def group_name_in_to_expands_on_send(opts):
    # a group of the server's address book typed in To: is sent to its members: the whole chain from the compose window
    # through tvmail-backend send and tvcontacts.py, against a fake CardDAV server and a fake sendmail that logs its
    # arguments (the group's member Alice and Bob; the third member is not in the book and is skipped)
    import http.server
    import importlib.util
    import threading
    spec = importlib.util.spec_from_file_location(
        "tvcontacts_tests3", str(HERE.parent.parent / "tvcommon" / "tests" / "test_tvcontacts.py"))
    fake = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fake)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), fake.Fake)
    srv.cards = {"alice.vcf": (fake.ALICE, "1"), "family.vcf": (fake.FAMILY, "1"), "bob.vcf": (fake.BOB, "1")}
    srv.log = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def setup(t):
        t.env["TVCONTACTS_URL"] = "http://127.0.0.1:%d/c/" % srv.server_address[1]
        t.env["TVCONTACTS_USER"] = "chris"
        t.env["TVCONTACTS_PASS"] = "secret"                      # allowlist secret: test value
        t.env["TVCONTACTS_PY"] = str(HERE.parent.parent / "tvcommon" / "py" / "tvcontacts.py")
        sm = t.tmp / "fake-sendmail"
        sm.write_text('#!/bin/sh\necho "$@" >> "%s"\ncat > /dev/null\n' % (t.tmp / "sendmail.log"))
        sm.chmod(0o755)
        t.env["SENDMAIL"] = str(sm)
        t.cleanups.append(srv.shutdown)
    tv = Tv(opts, setup=setup)
    c = Case(tv)
    started(tv, c)
    open_compose(tv, c)
    tv.send(alt("t"), "Family", settle=0.5)
    tv.send(alt("s"), "Group test", settle=0.5)
    c.check(field(tv, "To") == "Family", "the group name was not typed in To:")
    tv.send("F2", settle=2.5)
    log = tv.tmp / "sendmail.log"
    c.check(tv.wait(lambda: log.exists(), 8), "nothing was handed to sendmail: the mail was not sent")
    args = log.read_text() if log.exists() else ""
    c.check("alice@example.org" in args and "bob@example.org" in args,
            "the group was not expanded to its members: sendmail got %r" % args.strip())
    c.check("Family" not in args and "nobody" not in args, "sendmail was given the group name itself: %r" % args.strip())
    return tv


FIXTURE_UNORDERED = "".join([
    _msg("Late <late@example.com>", "Mon, 01 Sep 2026 12:00:00 -0700", "SUBJ-LATE", "l"),
    _msg("Early <early@example.com>", "Mon, 01 Sep 2026 08:00:00 -0700", "SUBJ-EARLY", "e"),
    _msg("Middle <mid@example.com>", "Mon, 01 Sep 2026 10:00:00 -0700", "SUBJ-MID", "m")])


@case()
def sort_by_date_is_date_order_not_mailbox_order(opts):
    # issue #2 (BUGS.md #13): mail that was moved or pulled in lands out of date order in the mbox; View > Sort by date
    # (the default) must still list it oldest first, and a second choice of it reverses the order
    tv = Tv(opts, fixture=FIXTURE_UNORDERED)
    c = Case(tv)
    c.check(tv.wait("SUBJ-MID", 15), "tvmail never showed the fixture folder")
    ys = {s: tv.find(s)[0] for s in ("SUBJ-EARLY", "SUBJ-MID", "SUBJ-LATE") if tv.find(s)}
    c.check(len(ys) == 3 and ys["SUBJ-EARLY"] < ys["SUBJ-MID"] < ys["SUBJ-LATE"],
            "the list is not in date order (mailbox order is late, early, middle): %r" % ys)
    c.check(tv.wait(lambda: "Subject: SUBJ-LATE" in tv.text(), 5), "the folder did not open on the newest message by date")
    return tv


@case()
def purge_preview_window_goes_away_after_each_answer(opts):
    # issue 7 (BUGS.md #7): File > Purge... left the "Purge preview" window open after the confirmation (yes, no) and after
    # "Nothing matches that filter."
    tv = Tv(opts)
    c = Case(tv)
    started(tv, c)
    tv.send(alt("f"), "u", settle=1.2)
    c.check(tv.wait("Older than", 4), "File > Purge... did not open its dialog")
    tv.send("nomatch-zzz", "Enter", settle=2.0)
    c.check(tv.wait("Nothing matches", 6), "no 'Nothing matches that filter' for a filter that matches nothing")
    c.check(not tv.has("Purge preview"), "the Purge preview window stayed open after 'Nothing matches'")
    tv.send(alt("f"), "u", settle=1.2)
    tv.send("Bob", "Enter", settle=2.0)
    c.check(tv.wait("Purge 1 message", 6) and tv.has("Purge preview"), "no preview and question for a filter matching one message")
    tv.send("n", settle=1.0)                                       # No
    c.check(tv.wait(lambda: not tv.has("Purge preview") and not tv.has("Purge 1 message"), 4),
            "the Purge preview window stayed open after answering No")
    c.check(tv.has("SUBJ2 plain"), "the message was purged although the answer was No")
    return tv


@case()
def address_book_adds_to_the_compose_window_you_were_in(opts):
    # issue 8 (BUGS.md #8): the address book put the address into the most recently OPENED compose window, and said "open a
    # compose window first" once that one was closed although another was open
    def setup(t):
        (t.home / ".mailrc").write_text("alias zed zed@example.com\n")
        t.env["MAILRC"] = str(t.home / ".mailrc")
    tv = Tv(opts, setup=setup)
    c = Case(tv)
    started(tv, c)
    open_compose(tv, c)                                            # A
    tv.send(alt("s"), "AAA", settle=0.4)
    tv.send(alt("m"), "n", settle=1.0)                             # B (the newest): Message > New message (Ctrl-N is the editor's key in A)
    c.check(tv.wait(lambda: field(tv, "Subject") == "", 4), "Message > New message did not open a second compose window")
    tv.send(alt("s"), "BBB", settle=0.4)
    tv.send("AltF3", "d", settle=0.8)                              # close B (discard): only A is left
    c.check(tv.wait(lambda: not tv.has("Unsent message") and field(tv, "Subject") == "AAA", 5), "B did not close, or A is not in front")
    tv.send("F4", settle=1.2)
    c.check(tv.wait("zed", 4), "F4 did not open the address book")
    tv.send("Enter", settle=1.0)
    c.check(not tv.has("Open a compose window first"), "the address book said 'open a compose window first' with A still open")
    tv.send("\x1b", settle=0.6)                                    # close the address book
    c.check(compose_open(tv) and field(tv, "Subject") == "AAA", "compose window A is not in front again")
    c.check(tv.wait(lambda: "zed@example.com" in (field(tv, "To") or ""), 4),
            "the address was not added to compose window A: To = %r" % field(tv, "To"))
    return tv


@case()
def unread_first_keeps_the_highlight_on_the_message_you_read(opts):
    # issue 3 (BUGS.md #3): with View > Unread first, reading a message re-sorts the list; the highlight (and the body shown)
    # must stay on that message, which moves down, not stay on the row it left
    tv = Tv(opts)
    c = Case(tv)
    started(tv, c)
    tv.send(alt("v"), "u", settle=1.0)                             # View > Unread first
    c.check(tv.wait("sort: unread first", 4), "View > Unread first did not take effect")
    tv.send("Tab", "Up", "Up", settle=0.8)                         # the Messages pane, on the top row (SUBJ1)
    c.check(tv.wait(lambda: "Subject: SUBJ1" in tv.text(), 4), "the top row (SUBJ1) is not the one shown")
    y_before = tv.find("SUBJ1")[0]
    tv.send("Tab", settle=1.5)                                     # into the body: SUBJ1 is read, so it sorts below the unread ones
    c.check(tv.wait(lambda: tv.find("SUBJ1") is not None and tv.find("SUBJ1")[0] > y_before, 5),
            "SUBJ1 did not move down the list when it was read")
    c.check("Subject: SUBJ1" in tv.text(), "the body no longer shows the message that was read (the highlight stayed on the row)")
    return tv


@case()
def a_failing_list_is_an_error_not_an_empty_folder(opts):
    # issue #10 (BUGS.md #25): the backend's error for `list` used to be dropped, so a folder that could not be read looked
    # empty.  Here the inbox is a directory, which the backend cannot read.
    def setup(t):
        (t.tmp / "notamailbox").mkdir()
        t.env["MAIL"] = str(t.tmp / "notamailbox")
    tv = Tv(opts, setup=setup)
    c = Case(tv)
    c.check(tv.wait("Cannot list", 15), "no error toast: a folder that cannot be listed looks empty")
    c.check(tv.has("Is a directory") or tv.has("directory"), "the toast does not say what went wrong")
    return tv


@case()
def send_to_calendar_and_todo_leave_requests_for_tvcal(opts):
    # Message > Send to > Calendar / To-do: a request file for tvcal (which asks the person before adding anything).
    # An event hands over the whole message as an .eml; a to-do is "Subject (from Name)".  tvcal itself need not run:
    # its side (the Inbox that reads these files) is tested in tvcal/tests/e2e_cal.py.
    def setup(t):
        t.env["TVCAL_REQUESTS"] = str(t.tmp / "reqs")
        t.env["TVCAL_STATE"] = str(t.tmp / "calstate")
    tv = Tv(opts, setup=setup)
    c = Case(tv)
    started(tv, c)
    reqs = lambda: sorted((tv.tmp / "reqs").glob("*.req")) if (tv.tmp / "reqs").is_dir() else []
    tv.send(alt("m"), "e", "a", settle=2.0)                       # Message > Send to > Calendar
    c.check(tv.wait("Asked tvcal to add this as an event", 6), "no toast after Send to > Calendar")
    c.check(tv.wait(lambda: len(reqs()) == 1, 4), "Send to > Calendar left no request file for tvcal")
    line = reqs()[0].read_text().strip() if reqs() else ""
    c.check(line.startswith("eml@tvmail "), "the event request is not an eml@tvmail line: %r" % line)
    eml = Path(line.split(" ", 1)[1]) if line.startswith("eml@tvmail ") else None
    c.check(eml is not None and eml.exists() and "Subject: SUBJ2 plain" in eml.read_text(errors="replace"),
            "the .eml handed to tvcal is not the selected message: %r" % line)
    tv.send(alt("m"), "e", "t", settle=2.0)                       # Message > Send to > To-do
    c.check(tv.wait("Asked tvcal to add this as a to-do", 6), "no toast after Send to > To-do")
    c.check(tv.wait(lambda: len(reqs()) == 2, 4), "Send to > To-do left no request file for tvcal")
    lines = [r.read_text().strip() for r in reqs()]
    c.check("todo@tvmail SUBJ2 plain (from Bob)" in lines, "the to-do request is wrong: %r" % lines)
    return tv


@case(31)
def send_menu_label(opts):
    tv = Tv(opts)
    c = Case(tv)
    started(tv, c)
    tv.send(alt("m"))
    tv.send("c")
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
            ("Message menu", ["\x1b", alt("m")], "Chat with sender", True),
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
def floyd_window_answers_and_asks_before_deleting(opts):
    # File > Floyd... (F7): a transcript over an input line, driven by `tvmail-backend floyd`
    def setup(t):
        t.env["TVMAIL_FLOYD_LEVEL"] = "chatty"
    tv = Tv(opts, setup=setup)
    c = Case(tv)
    started(tv, c)
    tv.send(chr(27) + "[18~", settle=1.0)         # F7
    c.check(tv.wait("Floyd - ask him anything", 6) and tv.wait("Floyd is at level chatty", 6),
            "F7 did not open Floyd, or he did not greet")
    c.check(tv.has("[type here]"), "the empty input line does not say where to type")
    tv.send("calc 6*7", "Enter", settle=1.0)
    c.check(tv.wait("6*7 = 42", 6) and tv.has("you:   calc 6*7"),
            "he did not answer a sum")
    tv.send("unread", "Enter", settle=1.0)
    c.check(tv.wait("unread of 3", 6) or tv.wait("Nothing unread", 6) or tv.wait("Inbox:", 6),
            "he did not read the inbox")
    tv.send("empty trash", "Enter", settle=1.0)
    c.check(tv.wait("empty already", 6) or tv.wait("no undo", 6),
            "he did not answer 'empty trash'")
    up = chr(27) + "[A"
    tv.send(up, up, up, "Enter", settle=1.0)      # Up x3 recalls "calc 6*7"; Enter sends it again
    c.check(tv.wait(lambda: tv.text().count("you:   calc 6*7") == 2, 6),
            "Up did not recall an earlier line")
    tv.send(chr(27), settle=0.8)                  # Esc
    c.check(tv.wait(lambda: not tv.has("Floyd - ask him anything"), 5),
            "Esc did not close Floyd")
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


@case()
def usenet_service_reads_threads_and_posts(opts):
    # SERVICES-DESIGN.md step 2: with [news] configured the Services pane lists Usenet; picking it shows the
    # subscribed groups, their articles in threads, reads (the group's new count goes down), follows up with a
    # post to the news server, catches up and subscribes - against a fake NNTP server in this process
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "tvnntp_tests", str(HERE.parent.parent / "tvcommon" / "tests" / "test_tvnntp.py"))
    tn = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tn)
    fourth = tn.article("Dan <dan@example.org>", "Wed, 03 Sep 2026 12:00:00 +0000", "Third level", "deep", "<a4@x>",
                        refs="<a1@x> <a2@x>")
    fake = tn.FakeNNTP({"lan.test": {1: tn.A1, 2: tn.A2, 3: tn.A3, 4: fourth},
                        "lan.other": {1: tn.A1}})

    def setup(t):
        t.conf = t.tmp / "tvmail.conf"
        t.conf.write_text("[service]\nmode = local\n[services]\norder = mail, news\n[news]\nhost = 127.0.0.1\n"
                          "port = %d\nsubscribed = lan.test\n[smtp]\nfrom = Chris <chris@example.org>\n" % fake.port)
        t.env["TVMAIL_CONF"] = str(t.conf)
        t.env["TVNNTP_PY"] = str(HERE.parent.parent / "tvcommon" / "py" / "tvnntp.py")
        t.cleanups.append(fake.stop)
    tv = Tv(opts, setup=setup)
    c = Case(tv)
    started(tv, c)
    c.check(tv.has("Services") and tv.has("Usenet"), "Usenet is not in the Services pane although [news] is set")
    tv.send("ShiftTab", settle=0.5)
    c.check(tv.has("[ Services ]"), "Shift-Tab did not go to the Services pane")
    tv.send("Down", settle=1.5)
    c.check(tv.wait("lan.test", 8) and tv.has("Groups") and tv.has("Articles"),
            "picking Usenet did not show the groups")
    # the articles, in threads: a reply under its article, the other thread after
    c.check(tv.wait("Third level", 8), "the group's articles are not listed")
    where = {s: tv.find(s) for s in ("Hello", "Re: Hello", "Third level", "Other thread")}
    c.check(all(where.values()), "an article is missing from the list: %r" % where)
    if all(where.values()):
        order = sorted(where, key=lambda s: where[s][0])
        c.check(order == ["Hello", "Re: Hello", "Third level", "Other thread"], "not in thread order: %r" % order)
        c.check(where["Third level"][1] - where["Hello"][1] == 4 and where["Re: Hello"][1] - where["Hello"][1] == 2,
                "replies are not indented by depth: %r" % where)
    # reading the newest article: the body shows it, and the group's new count goes 4 -> 3
    tv.send("Tab", "Tab", "Tab", settle=1.0)                      # Services -> Folders -> Articles -> Article
    c.check(tv.wait("deep", 6), "the article body is not shown")
    row = lambda: next((l for l in tv.lines() if "lan.test" in l and "│" in l), "")
    c.check(tv.wait(lambda: re.search(r"lan\.test\s*│\s*4│\s*3", row()) is not None, 6),
            "reading did not take one off the group's new count: %r" % row())
    # a followup: Ctrl-R opens a post with the groups, the subject and the References filled in
    tv.send("CtrlR", settle=1.5)
    c.check(tv.wait("Post to Usenet", 6) and tv.has("Newsgroups") and tv.has("Re: Third level"),
            "Ctrl-R did not open a followup")
    tv.send("my followup", "F2", settle=2.5)
    c.check(tv.wait("Article posted", 8), "the followup was not posted")
    c.check(len(fake.posted) == 1, "the news server did not get the article")
    if fake.posted:
        txt = fake.posted[0].decode("utf-8", "replace")
        c.check("Newsgroups: lan.test" in txt and "References: <a1@x> <a2@x> <a4@x>" in txt and "my followup" in txt
                and "From: Chris <chris@example.org>" in txt, "the posted article is wrong:\n" + txt)
    # View > Threads: flat, in date order
    tv.send(alt("v"), "t", settle=1.0)
    where = {s: tv.find(s) for s in ("Hello", "Re: Hello", "Third level", "Other thread")}
    if all(where.values()):
        c.check(sorted(where, key=lambda s: where[s][0]) == ["Hello", "Re: Hello", "Other thread", "Third level"],
                "View > Threads did not flatten the list: %r" % where)
    # Edit > Search messages (Ctrl-F): "deep" is only in the text of one article, so the hit proves the texts are searched
    tv.send("", settle=1.0)
    c.check(tv.wait("Search messages", 4), "Ctrl-F did not open the search dialog")
    tv.send("deep", "Enter", settle=0.3)
    # (the followup posted above quotes "deep" too, but is not in the loaded list: only the one shown is counted)
    c.check(tv.wait("1 message found", 8), "the search did not say what it found")
    c.check(tv.wait('search: "deep"', 8) and tv.has("Third level") and not tv.has("Other thread"),
            "the search did not narrow the list to the article whose text has the word")
    tv.send(alt("v"), "c", settle=1.0)                            # View > Clear filter ends the search too
    c.check(tv.wait("Other thread", 6) and not tv.has('search: "deep"'), "clearing the filter did not end the search")
    # Catch up
    tv.send(alt("m"), "u", "c", settle=1.0)
    c.check(tv.wait("Mark every article in lan.test", 4), "Catch up did not ask")
    tv.send("y", settle=2.0)
    c.check(tv.wait(lambda: re.search(r"lan\.test\s*│\s*[45]│\s*0", row()) is not None, 8),
            "catching up did not zero the group: %r" % row())
    # Subscribe to another group by name
    tv.send(alt("m"), "u", "s", settle=1.0)
    c.check(tv.wait("Subscribe to newsgroup", 4), "Subscribe did not ask for a name")
    tv.send("lan.other", "Enter", settle=2.0)
    c.check(tv.wait("lan.other", 6) and tv.wait("subscribed lan.other", 6), "the new group was not subscribed")
    # an unknown group is refused in words
    tv.send(alt("m"), "u", "s", settle=1.0)
    tv.send("no.such", "Enter", settle=2.0)
    c.check(tv.wait("411 no such", 6), "an unknown group was not refused")
    # back to Mail: the folders, and Ctrl-R is a reply to mail again (the search left the focus on the list: two steps back)
    tv.send("ShiftTab", "ShiftTab", settle=0.6)
    tv.send("Up", settle=1.5)
    c.check(tv.wait("inbox", 6) and tv.has("Folders") and not tv.has("Groups"), "picking Mail did not bring the folders back")
    return tv


@case()
def lists_service_reads_replies_subscribes_and_signs_off(opts):
    # SERVICES-DESIGN.md step 4: with [lists] configured (and IMAP) the Services pane lists Lists; picking it shows the
    # lists you are in (the folders Lists/<name>) with their mail; Ctrl-R answers to the list; Available lists asks
    # listserv@ (a stand-in here answers every command by mail), Enter subscribes; Sign off and Digest are commands too
    import email as _email
    import threading
    from test_backend import FakeIMAP, LIST_MAIL, _msg as imap_msg
    from fake_imap_server import ImapServer
    fake = FakeIMAP()
    fake.add("INBOX", imap_msg("carol@example.org", "Hi from carol", "hello"))
    fake.add("Lists/alpha", LIST_MAIL.encode(), flags=("\\Seen",))
    fake.add("Lists/alpha", LIST_MAIL.replace("l1@", "l2@").replace("lunch?", "dinner?").encode())
    fake.add("Lists/beta", imap_msg("dan@example.org", "[beta] news", "news body"))
    srv = ImapServer(fake)
    commands = []                       # what the stand-in listserv was asked, in order
    stop = threading.Event()

    def setup(t):
        t.env.update(TVMAIL_MODE="remote", TVMAIL_IMAP_PASS="pw")                   # allowlist secret: test value
        t.conf = t.tmp / "tvmail.conf"
        t.conf.write_text("[service]\nmode = remote\n[services]\norder = mail, lists\n[imap]\nhost = 127.0.0.1\n"
                          "port = %d\nssl = false\nuser = tester\n[lists]\naddress = listserv@cpbee.cmnet\nwait = 10\n"
                          "[smtp]\nfrom = Tester <tester@cpbee.cmnet>\n" % srv.port)
        t.env["TVMAIL_CONF"] = str(t.conf)
        sent = t.tmp / "sent"
        sent.mkdir()
        sm = t.tmp / "fake-sendmail"
        sm.write_text('#!/bin/sh\nf=$(mktemp "%s/mail.XXXXXX")\ncat > "$f"\n' % sent)
        sm.chmod(0o755)
        t.env["SENDMAIL"] = str(sm)

        def answer():
            seen = set()
            while not stop.wait(0.2):
                for p in sorted(sent.glob("mail.*")):
                    raw = p.read_bytes()
                    if p.name in seen or b"\n\n" not in raw or b"Message-ID" not in raw:
                        continue                                                     # still being written
                    seen.add(p.name)
                    m = _email.message_from_bytes(raw)
                    subj = m["Subject"]
                    commands.append(subj)
                    if subj == "LIST":
                        body = "OK 2 lists\nalpha    the first list\ngamma    a list to join"
                    elif subj.startswith("SUBSCRIBE"):
                        body = "OK you are subscribed to " + subj.split()[1]
                    elif subj.startswith("SIGNOFF"):
                        body = "OK you are signed off from " + subj.split()[1]
                    else:
                        body = "OK done"
                    reply = ("From: listserv@cpbee.cmnet\r\nTo: %s\r\nSubject: Re: %s\r\nIn-Reply-To: %s\r\n\r\n%s\r\n"
                             % (m["From"], subj, m["Message-ID"], body))
                    with srv.lock:
                        fake.add("INBOX", reply.encode())
        threading.Thread(target=answer, daemon=True).start()
        t.cleanups.extend([stop.set, srv.stop])
    tv = Tv(opts, setup=setup)
    c = Case(tv)
    c.check(tv.wait("Hi from carol", 20) and tv.has("Services") and tv.has("Lists"),
            "Lists is not in the Services pane although [lists] is set (or the IMAP inbox did not load)")
    tv.send("ShiftTab", settle=0.5)
    tv.send("Down", settle=1.5)
    c.check(tv.wait("alpha", 8) and tv.has("beta") and tv.wait("[alpha] lunch?", 8),
            "picking Lists did not show the lists and the mail of the first one")
    # the badge: unread over all the lists (alpha 1, beta 1)
    c.check(re.search(r"Lists[\s│]+2\b", "\n".join(tv.lines())) is not None, "the Lists row has no unread count of 2")
    # Reply (Ctrl-R) goes to the list, not to Bob
    tv.send("Tab", "Tab", settle=0.8)
    tv.send("CtrlR", settle=1.5)
    c.check(tv.wait("Compose", 6) and field(tv, "To") == "alpha@cpbee.cmnet",
            "Reply in a list did not address the list: To = %r" % field(tv, "To"))
    tv.send("AltF3", settle=0.8)
    if tv.has("Unsent"):
        tv.send("d", settle=0.8)
    # Available lists: the answer to LIST, Enter on one subscribes
    tv.send(alt("m"), "l", "a", settle=1.5)
    c.check(tv.wait("Available lists", 10) and tv.has("gamma") and tv.has("* alpha"),
            "Available lists did not show what the server offers (with the ones you are in starred)")
    tv.send("Down", "Enter", settle=0.3)
    c.check(tv.wait("subscribed to gamma", 12), "subscribing to gamma did not say it worked")
    c.check(tv.wait(lambda: tv.find("gamma") is not None and "gamma" in "\n".join(tv.lines()[3:12]), 6),
            "the new list is not in the Lists pane")
    # Sign off from it (the folder and its mail stay), then set a digest
    tv.send(alt("m"), "l", "o", settle=1.0)
    c.check(tv.wait("Sign off from gamma", 4), "Sign off did not ask")
    tv.send("y", settle=0.3)
    c.check(tv.wait("signed off from gamma", 12), "signing off did not say it worked")
    tv.send(alt("m"), "l", "d", settle=1.0)
    c.check(tv.wait("Mail from gamma", 4), "Digest did not offer the two choices")
    tv.send("Down", "Enter", settle=1.5)
    c.check(commands == ["LIST", "SUBSCRIBE gamma tester", "SIGNOFF gamma", "SET gamma DIGEST"],
            "the listserv was asked the wrong things: %r" % commands)
    # back to Mail: the folders; the Lists commands are off there
    tv.send("ShiftTab", "ShiftTab", settle=0.6)
    tv.send("Up", settle=1.5)
    c.check(tv.wait("inbox", 6) and tv.has("Folders"), "picking Mail did not bring the folders back")
    return tv


@case()
def services_pane_is_the_fourth_pane(opts):
    # SERVICES-DESIGN.md step 1: with one service the window is the three panes it always was; View >
    # Services pane adds the fourth (Services over Folders, Mail with the inbox's new count) and takes
    # it away again.  Tab goes Services -> Folders -> Messages -> Message.
    tv = Tv(opts)
    c = Case(tv)
    started(tv, c)
    c.check(not tv.has("Services"), "a single service showed the Services pane")
    tv.send(alt("v"))
    tv.send("p", settle=0.8)
    c.check(tv.wait(lambda: tv.has("Services"), 5), "View > Services pane did not show it")
    ys = {name: next((y for y, l in enumerate(tv.lines()) if name in l), -1)
          for name in ("Services", "Folders")}
    c.check(0 < ys["Services"] < ys["Folders"], "Services is not above Folders: %r" % ys)
    mail = next((l for l in tv.lines()[ys["Services"] + 1:ys["Folders"]] if "Mail" in l), "")
    c.check(re.search(r"Mail[\s│]+3\b", mail) is not None,
            "the Mail row has no unread count (the fixture's inbox has three new messages): %r" % mail)
    # the Folders table under it is whole: its heading, then the inbox first
    head = next((l for l in tv.lines()[ys["Folders"] + 1:ys["Folders"] + 3] if "Total" in l), "")
    c.check("Folder" in head and "New" in head, "the Folders heading row is missing or stale: %r" % head)
    c.check(sum(l.lstrip("║ ").startswith("inbox") for l in tv.lines()) == 1, "the inbox row is not there once")
    c.check(tv.has("[ Folders ]"), "the Folders pane lost the focus")
    seen = []
    for _ in range(4):
        tv.send("Tab", settle=0.5)
        seen.append(next((n for n in ("Services", "Folders", "Messages", "Message")
                          if tv.has("[ %s ]" % n)), "?"))
    c.check(seen == ["Messages", "Message", "Services", "Folders"],
            "Tab went %r, not Messages, Message, Services, Folders" % seen)
    tv.send("Tab", "Tab", "Tab", settle=0.5)        # on Services
    c.check(tv.has("[ Services ]"), "could not tab to the Services pane")
    tv.send(alt("v"))
    tv.send("p", settle=0.8)
    c.check(tv.wait(lambda: not tv.has("Services"), 5), "View > Services pane did not hide it")
    c.check(tv.has("[ Folders ]"), "hiding Services while it had the focus left the focus nowhere")
    return tv


# (#43's case - a resize while a co-process starts - is tvchat's now: its
# fix is tvcommon's CoProcess, and only tvchat starts one inside the running
# app; tvmail's mail backend starts before the resize handler exists.)


# ------------------------------------------------------------------- main ---
if __name__ == "__main__":
    run("tvmail")
