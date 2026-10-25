#!/usr/bin/env python3
"""Regression and UI tests for tvchat, read off a pyte screen model.

Drives the built tvchat in a pty against a scripted IRC server
(fake_irc.py) and checks the cells actually on screen - columns and colours
- instead of regexes over the output stream.  The harness (Tv, Case, the
screen and colour helpers) is ../../tvcommon/tests/tvscreen.py, shared with
tvmail.

    python3 tests/e2e_regress.py --bin build/tvchat [--backend-dir backend]
                                 [-k 44 | -k CASE_NAME] [-v]

Every case starts its own tvchat on a fresh temp HOME, with a tvchat.conf
whose account "fake" (display "Fake", nick "tester") autoconnects and joins
#e2e.  Exit codes:  0 pass, 1 fail, 77 skip (no pty, or pyte not installed).
"""
import os
import re
import shutil
import signal
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# the pty + pyte harness tvmail and tvchat share
sys.path.insert(0, str(HERE.parent / "tvcommon" / "tests"))      # a source tarball
sys.path.insert(0, str(HERE.parent.parent / "tvcommon" / "tests"))
from tvscreen import *            # noqa: E402,F401,F403
import tvscreen                   # noqa: E402


class Tv(tvscreen.Tv):
    """One tvchat in a pty on its own temp HOME; srv: the fake IRC server
    its tvchat.conf points at (None: no tvchat.conf at all)."""

    prefix = "tvchat-regress-"
    drop_env = ("COLORTERM", "TVCHAT_CONF", "TVMAIL_MU_TICKETS", "TVCHAT_BACKEND_LOG")

    def __init__(self, opts, srv=None, env=None, setup=None):
        self.srv = srv
        super().__init__(opts, env=env, setup=setup)

    def prepare(self):
        # the shell preamble puts ~/bin first: an installed (older) backend
        # there would win over the one under test, so put ours there
        shutil.copy2(os.path.join(self.opts.backend_dir, "tvchat-backend"),
                     self.bindir / "tvchat-backend")
        self.env["TVMAIL_MU_TICKETS"] = str(self.tmp / "no-tickets")
        if self.srv:
            conf = self.tmp / "tvchat.conf"
            conf.write_text(self.srv.conf())
            self.env["TVCHAT_CONF"] = str(conf)
            self.cleanups.append(self.srv.stop)
        else:
            self.env["TVCHAT_CONF"] = str(self.tmp / "no-tvchat.conf")


def chat_tv(opts):
    from fake_irc import FakeIrcd
    srv = FakeIrcd()
    srv.start()
    try:
        tv = Tv(opts, srv)
    except Exception:
        srv.stop()
        raise
    return tv, srv


def started(tv, c):
    c.check(tv.wait(lambda: tv.has("Servers") and tv.has("tvchat"), 15),
            "tvchat never showed its window")


def chat_heading(tv, name="#e2e — Fake"):
    """The row of the chat pane's heading showing name, focused ("[ name ]")
    or not ("  name  "), or None."""
    pat = re.compile(r"─(?:\[ |  )%s(?: \]|  )─" % re.escape(name))
    return next((y for y, l in enumerate(tv.lines()) if pat.search(l)), None)


def heading_active(tv, label):
    """The heading of that pane shows it has the focus."""
    return tv.has("[ %s" % label)


def open_chat(tv, c, srv):
    """The #e2e conversation of account Fake, the input focused."""
    c.check(srv.wait_joined("#e2e"), "the chat backend never joined #e2e")
    c.check(tv.wait(lambda: re.search(r"Fake\b.*\bYes\b", tv.text()), 5),
            "the server pane does not show Fake as connected (Yes)")
    c.check(heading_active(tv, "Servers"), "tvchat did not start on the server pane")
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


def new_count(tv):
    return tv.line(ROWS - 1).rstrip()


@case()
def chat_window_three_panes(opts):
    tv, srv = chat_tv(opts)
    c = Case(tv)
    started(tv, c)
    c.check(srv.wait_joined("#e2e"), "the chat backend never joined #e2e")
    srv.say(":alice!a@h PRIVMSG #e2e :hello before you looked")
    c.check(tv.wait(lambda: new_count(tv).endswith("1 new"), 5),
            "an unread chat message did not show as \"1 new\" on the status line")
    c.check(tv.wait("Fake (1)", 3), "the server pane does not count the unread message: Fake (1)")
    c.check(tv.has("Tab Pane") and tv.has("Enter Send") and not tv.has("Alt-C") and
            not tv.has("^D Del"), "the status line is not tvchat's own")
    tv.send("Enter")
    c.check(tv.wait("#e2e (1)", 3), "the conversation pane does not show #e2e (1)")
    # moving onto it shows it, but does not read it: it is read once the
    # chat pane has the focus
    tv.send("Down", settle=0.8)
    p, row = log_line(tv, "<alice> hello before you looked")
    c.check(p and re.search(r"\d\d:\d\d <alice> hello before you looked", row),
            "the chat pane did not show the message from before")
    c.check(tv.has("the e2e channel"), "the topic is not in the log")
    tv.pump(0.8)
    c.check(tv.has("#e2e (1)"), "just moving onto the conversation read it")
    tv.send("Enter")
    c.check(tv.wait(lambda: "new" not in new_count(tv) and not tv.has("#e2e (1)"), 4),
            "with the chat pane focused, the unread count stayed")
    srv.say(":alice!a@h PRIVMSG #e2e :and after you looked",
            ":alice!a@h PRIVMSG #e2e :\x01ACTION waves\x01")
    c.check(tv.wait("<alice> and after you looked", 4),
            "a message arriving with the conversation shown did not show")
    c.check(tv.wait("* alice waves", 3), "/me is not shown as * nick action")
    c.check(tv.wait(lambda: "new" not in new_count(tv), 3),
            "a message arriving while you read counted as new")
    # Tab: servers -> conversations -> input, and round
    tv.send("Tab")
    c.check(tv.wait(lambda: heading_active(tv, "Servers"), 2), "Tab from the input did not go to Servers")
    tv.send("Tab")
    c.check(tv.wait(lambda: heading_active(tv, "Conversations"), 2), "Tab did not go on to Conversations")
    tv.send("Tab")
    c.check(tv.wait(lambda: heading_active(tv, "#e2e"), 2), "Tab did not go on to the chat pane")
    # with Help in front, a new message counts - in the chat window's
    # title too, which Window > List shows - and the window comes back read
    tv.send("F1")
    c.check(tv.wait("THE THREE PANES", 3), "F1 did not open Help")
    srv.say(":alice!a@h PRIVMSG #e2e :while you read Help")
    c.check(tv.wait(lambda: new_count(tv).endswith("1 new"), 4),
            "a message with Help in front is not \"1 new\"")
    tv.send(alt("0"))
    c.check(tv.wait("Window list", 3) and tv.has("tvchat (1)"),
            "Window > List does not show the chat window with its unread count")
    top = tv.find("Window list")[0]               # its rows, the first one current
    names = [l for l in tv.lines()[top + 1:] if re.search(r"Help|tvchat", l)]
    down = next(i for i, l in enumerate(names) if "tvchat (1)" in l)
    tv.send(*(["Down"] * down + ["Enter"]), settle=0.6)
    c.check(tv.wait(lambda: chat_heading(tv) is not None and "new" not in new_count(tv), 4),
            "back in the chat window, on #e2e with the chat pane focused, the count stayed")
    # Esc goes nowhere: the chat window stays (Alt-X is the way out)
    tv.send("\x1b", settle=0.6)
    c.check(chat_heading(tv) is not None and tv.alive(), "Esc did something to the chat window")
    return tv


@case()
def chat_history_pgup(opts):
    tv, srv = chat_tv(opts)
    c = Case(tv)
    started(tv, c)
    c.check(srv.wait_joined("#e2e"), "the chat backend never joined #e2e")
    srv.say(*[":alice!a@h PRIVMSG #e2e :backlog %02d" % i for i in range(80)])
    c.check(tv.wait(lambda: new_count(tv).endswith("80 new"), 6),
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
    tv.send(alt("c"), "j")
    c.check(tv.wait("invite link", 3), "Chat > Join... did not ask")
    tv.send("#Other", "Enter", settle=0.8)
    c.check(srv.wait_joined("#other") and srv.sent("JOIN #Other"),
            "Join did not join the channel")
    c.check(tv.wait(lambda: chat_heading(tv, "#Other — Fake") is not None, 4),
            "Join did not show the channel in the chat window")
    c.check(tv.wait(lambda: re.search(r"│ ?#Other\b", tv.text()), 3),
            "the channel joined is not in the conversation list")
    tv.send(alt("c"), "l", settle=0.8)
    c.check(srv.sent("PART #Other"), "Chat > Leave did not part the channel")
    c.check(tv.wait(lambda: chat_heading(tv, "#Other — Fake") is None and
                    not re.search(r"│ ?#Other\b", tv.text()), 4),
            "the channel left is still shown or listed")
    c.check(tv.alive(), "tvchat exited")
    return tv


@case()
def chat_typed_commands(opts):
    tv, srv = chat_tv(opts)
    c = Case(tv)
    started(tv, c)
    open_chat(tv, c, srv)
    tv.send("/me dances", "Enter", settle=0.8)
    c.check(tv.wait(lambda: srv.sent("PRIVMSG #e2e :\x01ACTION dances\x01"), 3),
            "/me was not sent as a CTCP ACTION")
    c.check(tv.wait("* tester dances", 3), "/me is not in the log as * nick action")
    # an unknown command: refused in the log, kept in the input, never sent
    tv.send("/bogus x", "Enter", settle=0.8)
    c.check(tv.wait("not sent: unknown command /bogus", 3),
            "an unknown /command was not refused in the log")
    c.check(re.search(r">\s*/bogus x", tv.text()), "the refused line left the input")
    c.check(not srv.sent("BOGUS") and not srv.sent("PRIVMSG #e2e :/bogus"),
            "an unknown /command went to the server")
    tv.send(*["\x7f"] * 8)
    # the server messages take /commands: Tab, Tab to the list, Up to them
    tv.send("Tab", "Tab", "Up", "Enter", settle=0.8)
    c.check(tv.wait(lambda: re.search(r"\[ .*server messages", tv.text()), 3),
            "could not get to the server messages' input")
    tv.send("/join #cmds", "Enter", settle=0.8)
    c.check(tv.wait(lambda: srv.sent("JOIN #cmds"), 3),
            "/join in the server messages did not join")
    c.check(tv.wait(lambda: re.search(r"│ ?#cmds\b", tv.text()), 3),
            "the channel /joined is not in the conversation list")
    c.check(tv.alive(), "tvchat exited")
    return tv


@case()
def delta_invite_join_and_send(opts):
    # Delta Chat against tests/fake_deltachat_rpc.py: Chat > Invite shows the
    # link, Chat > Join takes one (the key exchange opens the chat), a line
    # typed there reaches the core
    import json
    core = HERE / "fake_deltachat_rpc.py"

    def setup(t):
        d = t.tmp / "xdg" / "tvchat" / "deltachat" / "delta"
        d.mkdir(parents=True)
        (d / "world.json").write_text(json.dumps({"contacts": {}, "chats": {}, "messages": {}}))
        (d / "state.json").write_text(json.dumps({"accounts": [1], "configured": True}))
        conf = t.tmp / "tvchat.conf"
        conf.write_text("[chat]\nautoconnect = delta\n\n[account:delta]\nprotocol = deltachat\n"
                        "display = Delta\naddr = me@example.org\nrpc_server = %s\n" % core)
        t.env["TVCHAT_CONF"] = str(conf)
        t.core = d
        t.tvmail_log = t.tmp / "tvmail.log"
        stub = t.bindir / "tvmail-backend"
        stub.write_text("#!/bin/sh\necho \"$@\" >> %s\ncat >> %s\nexit 0\n" % (t.tvmail_log, t.tvmail_log))
        stub.chmod(0o755)
    tv = Tv(opts, setup=setup)
    c = Case(tv)
    started(tv, c)
    c.check(tv.wait(lambda: re.search(r"Delta\b.*\bYes\b", tv.text()), 15),
            "the Delta Chat account did not connect (Yes in the server pane)")
    tv.send(alt("c"), "i", settle=0.8)
    c.check(tv.wait("https://i.delta.chat/#FAKEINVITE", 5),
            "Chat > Invite... did not show the invite link")
    # the optional link to tvmail: the same dialog can mail the link through it
    c.check(tv.has("mail it to"), "the Invite dialog has no 'mail it' field although tvmail is there")
    tv.send("friend@example.net", "Enter", settle=1.0)

    def mailed():
        try:
            return "send --to friend@example.net" in tv.tvmail_log.read_text()
        except OSError:
            return False
    c.check(tv.wait(mailed, 6), "the invitation was not handed to tvmail-backend send")
    c.check("FAKEINVITE" in tv.tvmail_log.read_text(), "the mail sent through tvmail does not hold the link")
    tv.send(alt("c"), "v", settle=0.8)
    c.check(tv.wait("No chat invitations are waiting.", 4), "Chat > Accept invite... did not say there are none")
    tv.send("Enter", settle=0.5)
    tv.send(alt("c"), "j")
    c.check(tv.wait("invite link", 3), "Chat > Join... does not mention an invite link")
    tv.send("https://i.delta.chat/#FAKEINVITE", "Enter", settle=1.0)
    c.check(tv.wait(lambda: chat_heading(tv, "Inviter") is not None, 6),
            "joining the invite did not open the chat")
    tv.send("hello over delta", "Enter", settle=1.0)
    c.check(tv.wait("hello over delta", 4), "the sent line is not in the log")

    def sent():
        try:
            return "hello over delta" in (t_core / "requests.jsonl").read_text()
        except OSError:
            return False
    t_core = tv.core
    c.check(tv.wait(sent, 4), "the line did not reach the Delta Chat core")
    c.check(tv.alive(), "tvchat exited")
    return tv


@case()
def delta_group_rename_and_leave(opts):
    # Chat > New group..., Rename group... and Leave (which asks first) against
    # tests/fake_deltachat_rpc.py
    import json
    core = HERE / "fake_deltachat_rpc.py"

    def setup(t):
        d = t.tmp / "xdg" / "tvchat" / "deltachat" / "delta"
        d.mkdir(parents=True)
        (d / "world.json").write_text(json.dumps({"contacts": {}, "chats": {}, "messages": {}}))
        (d / "state.json").write_text(json.dumps({"accounts": [1], "configured": True}))
        conf = t.tmp / "tvchat.conf"
        conf.write_text("[chat]\nautoconnect = delta\n\n[account:delta]\nprotocol = deltachat\n"
                        "display = Delta\naddr = me@example.org\nrpc_server = %s\n" % core)
        t.env["TVCHAT_CONF"] = str(conf)
        t.core = d
    tv = Tv(opts, setup=setup)
    c = Case(tv)
    started(tv, c)
    c.check(tv.wait(lambda: re.search(r"Delta\b.*\bYes\b", tv.text()), 15),
            "the Delta Chat account did not connect")
    tv.send(alt("c"), "g", settle=0.8)
    c.check(tv.wait("Group name", 4), "Chat > New group... did not ask for a name")
    tv.send("Crew", "Enter", settle=1.0)
    c.check(tv.wait(lambda: chat_heading(tv, "Crew — Delta") is not None, 6), "the new group did not open")
    tv.send(alt("c"), "n", settle=0.8)
    c.check(tv.wait("Rename group", 4), "Chat > Rename group... did not ask")
    tv.send("\x15", "Pirates", "Enter", settle=1.0)       # Ctrl-U clears the old name first
    c.check(tv.wait(lambda: chat_heading(tv, "Pirates — Delta") is not None, 6), "the group was not renamed")
    tv.send(alt("c"), "l", settle=0.8)
    c.check(tv.wait("Leave the group", 4) and tv.has("This cannot be undone."),
            "Leave did not ask before deleting")
    tv.send("n", settle=0.6)
    c.check(chat_heading(tv, "Pirates — Delta") is not None, "No still left the group")
    tv.send(alt("c"), "l", settle=0.8)
    c.check(tv.wait("Leave the group", 4), "Leave did not ask the second time")
    tv.send("y", settle=1.2)
    c.check(tv.wait(lambda: chat_heading(tv, "Pirates — Delta") is None and "Pirates" not in tv.text(), 6),
            "the group is still shown after leaving")

    def called(name):
        try:
            return name in (tv.core / "requests.jsonl").read_text()
        except OSError:
            return False
    c.check(tv.wait(lambda: called("delete_chat") and called("leave_group"), 4),
            "the core was not told to leave and delete the group")
    c.check(tv.alive(), "tvchat exited")
    return tv


def talk_tv(opts, view=None):
    from fake_talkd import FakeTalkd
    srv = FakeTalkd()
    srv.start()
    conf = srv.conf(view=view)
    srv.conf = lambda: conf              # Tv writes srv.conf() as tvchat.conf
    try:
        tv = Tv(opts, srv)
    except Exception:
        srv.stop()
        raise
    return tv, srv


def talk_rule(tv, text):
    """The row of the classic view's rule holding text, or None."""
    return next((y for y, l in enumerate(tv.lines()) if "─ " + text in l), None)


@case()
def talk_classic_call_in(opts):
    tv, srv = talk_tv(opts)                      # talk_view not set: classic
    c = Case(tv)
    started(tv, c)
    c.check(srv.greeted(), "tvchat-backend never said HELLO to talkd")
    c.check(tv.wait(lambda: re.search(r"Talk\b.*\bYes\b", tv.text()), 5),
            "the server pane does not show Talk as connected (Yes)")
    srv.ring("miche@cpbee", "t1")
    c.check(tv.wait("miche is calling", 4), "a talk call did not say so on the status line")
    tv.send("Enter")                             # Servers -> Conversations
    c.check(tv.wait(lambda: re.search(r"miche.*- calling you", tv.text()), 4),
            "the conversation list does not show miche calling")
    tv.send("Down", "Enter", settle=0.8)         # onto miche, into the chat pane
    c.check(tv.wait("talk: calling you - Enter answers", 3),
            "the heading does not say how to answer")
    tv.send("Enter", settle=0.8)                 # answers
    c.check(srv.wait(lambda: srv.sent("ACCEPT t1")), "Enter did not ACCEPT the call")
    c.check(srv.attached(), "the session never attached")
    c.check(tv.wait(lambda: talk_rule(tv, "miche — connected") is not None, 4),
            "classic: no rule saying miche - connected")
    # the rule is faint but seen: its words and its line (live check, Oct 22:
    # drawn cyan on cyan, it was not there at all)
    ry = talk_rule(tv, "miche")
    bad = low_contrast(tv, box=(ry, 24, ry, COLS - 2))
    line = [x for x in range(24, COLS - 2) if tv.screen.buffer[ry][x].data == "─"]
    c.check(not bad, "classic: the rule's words are hard to read: %s" % bad)
    c.check(line and contrast(*cell_colours(tv.screen.buffer[ry][line[0]])) >= 2.5,
            "classic: the rule's line cannot be seen")
    # theirs: as typed, their own erase (^H) applied; an unfinished line live
    srv.peer("hi chrix\x08s\nhalf a li")
    c.check(tv.wait("half a li", 4), "classic: their unfinished line does not show as typed")
    rule = talk_rule(tv, "miche")
    lines = tv.lines()
    below = "\n".join(lines[rule + 1:])
    c.check(rule is not None and "hi chris" in below and "hi chrix" not in below and
            "half a li" in below, "classic: their text is not below the rule, erase applied")
    # ours: every keystroke at once, Backspace as our erase
    tv.send("y", "o", "u", "\x7f", settle=0.6)
    c.check(srv.wait(lambda: srv.heard() == b"you\x7f", 3),
            "classic: keystrokes were not sent as typed: %r" % srv.heard())
    above = "\n".join(tv.lines()[:talk_rule(tv, "miche")])
    c.check("yo" in above and "you" not in above, "classic: our line is not above the rule as typed")
    tv.send("Enter", settle=0.6)
    c.check(srv.wait(lambda: srv.heard() == b"you\x7f\n", 3), "classic: Enter did not send a newline")
    srv.hang_up()
    c.check(tv.wait(lambda: talk_rule(tv, "miche — hung up") is not None, 4),
            "the rule does not say the call ended")
    c.check(tv.alive(), "tvchat exited")
    return tv


@case()
def talk_irc_mode_call_out_and_toggle(opts):
    tv, srv = talk_tv(opts, view="irc")
    c = Case(tv)
    started(tv, c)
    c.check(srv.greeted(), "tvchat-backend never said HELLO to talkd")
    c.check(tv.wait(lambda: re.search(r"Talk\b.*\bYes\b", tv.text()), 5),
            "the server pane does not show Talk as connected (Yes)")
    tv.send(alt("c"), "j")
    c.check(tv.wait("invite link", 3), "Chat > Join... did not ask")
    tv.send("miche", "Enter", settle=0.8)
    c.check(srv.wait(lambda: srv.sent("CALL ")), "Join did not CALL")
    cid = srv.sent("CALL ")[0].split()[1]
    c.check(tv.wait("talk: ringing...", 4), "the heading does not say it is ringing")
    srv.connect(cid)
    c.check(srv.attached(), "the session never attached")
    c.check(tv.wait("talk: connected", 4), "the heading does not say connected")
    # IRC mode: nothing goes until Enter, then the whole line
    tv.send("hello there", settle=0.6)
    c.check(srv.heard() == b"", "IRC mode sent keystrokes before Enter: %r" % srv.heard())
    tv.send("Enter", settle=0.6)
    c.check(srv.wait(lambda: srv.heard() == b"hello there\n", 3),
            "IRC mode: the line did not go on Enter: %r" % srv.heard())
    c.check(tv.wait("<chris> hello there", 3), "IRC mode: our line is not in the log")
    srv.peer("typ")
    c.check(tv.wait("miche is typing...", 4), "IRC mode: no 'miche is typing...'")
    srv.peer("ed a line\n")
    c.check(tv.wait("<miche> typed a line", 4), "IRC mode: their finished line is not in the log")
    c.check(tv.wait(lambda: not tv.has("is typing..."), 3), "'is typing...' stayed after the line ended")
    # Ctrl-T: classic for this conversation, and back
    tv.send("\x14", settle=0.8)
    c.check(tv.wait(lambda: talk_rule(tv, "miche — connected") is not None, 3),
            "Ctrl-T did not switch to the classic view")
    tv.send("x", settle=0.5)
    c.check(srv.wait(lambda: srv.heard().endswith(b"\nx"), 3), "classic after Ctrl-T: a keystroke did not go at once")
    tv.send("\x14", settle=0.8)
    c.check(tv.wait(lambda: talk_rule(tv, "miche") is None and tv.has("<miche> typed a line"), 3),
            "Ctrl-T again did not come back to the log")
    c.check(tv.alive(), "tvchat exited")
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
    # picking another conversation keeps it as #e2e's draft
    tv.send("ShiftTab", "Up", settle=0.8)         # the server messages
    c.check(tv.wait(lambda: not tv.has("> half a thought") and
                    chat_heading(tv, "Fake - server messages") is not None, 3),
            "another conversation shows the text typed for #e2e")
    tv.send("Down", settle=0.8)
    c.check(tv.wait("> half a thought", 3), "back on #e2e, the unsent text is gone")
    # so does a trip to Help
    tv.send("F1")
    c.check(tv.wait("THE THREE PANES", 3), "F1 did not open Help")
    tv.send("\x1b", settle=0.6)
    c.check(tv.wait("> half a thought", 3), "the unsent text was lost going to Help and back")
    tv.send(alt("x"))
    c.check(tv.wait("Discard the chat message", 3),
            "Alt-X did not ask about the unsent chat message")
    tv.send("n", settle=0.8)                      # No: keep it, and tvchat
    c.check(tv.alive() and tv.wait("> half a thought", 3),
            "No to the discard question lost tvchat or the text")
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
    c.check(not bad, "chat text below 3:1 contrast:\n  %s" % "\n  ".join(bad))
    return tv


@case(44)
def menus_stay_open_while_chat_polls(opts):
    # tvmail #44: connected, the app polls the chat backend every 250 ms,
    # which shut any open menu within a quarter second
    tv, srv = chat_tv(opts)
    c = Case(tv)
    started(tv, c)
    c.check(srv.wait_joined("#e2e"), "the chat backend never joined #e2e")
    for key, item in (("f", "Exit"), ("c", "Join...")):
        tv.send(alt(key))
        c.check(tv.wait(item, 3), "Alt-%s did not open its menu" % key.upper())
        tv.pump(1.5)
        c.check(tv.has(item), "#44 the menu closed by itself while chat polled")
        tv.send("\x1b", "\x1b", settle=0.5)
    # the Chat menu lists the account with its state
    tv.send(alt("c"))
    c.check(tv.wait(lambda: re.search(r"Fake\s+connected", tv.text()), 3),
            "the Chat menu does not list the account as connected")
    tv.send("\x1b", "\x1b", settle=0.5)
    c.check(tv.alive(), "tvchat exited")
    return tv


@case()
def chat_menu_greyed_without_config(opts):
    tv = Tv(opts)                                 # no tvchat.conf
    c = Case(tv)
    started(tv, c)
    c.check(tv.has("no chat accounts"), "the chat pane does not say there are no accounts")
    tv.send(alt("f"))
    c.check(tv.wait("Exit", 3), "the File menu did not open")
    y, x = tv.find("Exit")
    enabled = cell_colours(tv.screen.buffer[y][x + 1])[0]
    tv.send("\x1b", "\x1b", alt("c"))
    c.check(tv.wait("no tvchat.conf", 3), "the Chat menu does not say chat is off")
    for item in ("Connect...", "Join...", "Leave"):
        y, x = tv.find(item)
        c.check(cell_colours(tv.screen.buffer[y][x + 1])[0] != enabled,
                "Chat > %s is not greyed without a tvchat.conf" % item)
    tv.send("\x1b", "\x1b", settle=0.5)
    c.check(tv.alive(), "tvchat exited")
    return tv


@case(43)
def resize_keeps_backend(opts):
    # tvmail #43, in tvcommon's CoProcess: resizes rain on the app while its
    # co-process starts.  Each one used to fail the pipe read (EINTR) as if
    # the backend had died, and a failed start gave the co-process up for the
    # session.  A shim holds the backend's banner back a second and logs what
    # tvchat asks it - the event polls only come if it survived.
    def setup(t):
        b = t.bindir
        (b / "tvchat-backend").rename(b / "tvchat-backend.real")
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
            "#43 a resize while the chat backend started lost it: tvchat "
            "never polled (requests: %r)"
            % (reqs.read_text() if reqs.exists() else None))
    return tv


@case()
def redraw_clears_scribbles(opts):
    # tvcommon: Ctrl-L / Window > Redraw screen repaints what wall wrote over
    # the windows; mesg n while tvchat runs, put back on exit
    def setup(t):
        t.tty_mode = 0o620
    tv = Tv(opts, setup=setup)
    redraw_and_mesg(tv, Case(tv), started)
    return tv


@case()
def mesg_y_leaves_tty(opts):
    # [ui] mesg = y: tvchat leaves the terminal taking messages
    def setup(t):
        t.tty_mode = 0o620
        conf = t.tmp / "tvchat.conf"
        conf.write_text("[ui]\nmesg = y\n")
        t.env["TVCHAT_CONF"] = str(conf)
    tv = Tv(opts, setup=setup)
    c = Case(tv)
    started(tv, c)
    c.check(tty_mode(tv) == 0o620,
            "[ui] mesg = y, yet tvchat changed the terminal's mode (%o)" % tty_mode(tv))
    return tv


if __name__ == "__main__":
    run("tvchat")
