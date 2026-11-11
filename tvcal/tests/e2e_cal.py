#!/usr/bin/env python3
"""UI tests for tvcal, read off a pyte screen model.

Drives the built tvcal in a pty on its own temp HOME with a local calendar file, and checks
the cells on screen: the three panes, a new event through the dialog showing in the agenda, a
to-do added and ticked off, a request from tvmail arriving as "incoming" and being added on a
yes, and a reminder reaching tvmail's inbox.  The harness is ../../tvcommon/tests/tvscreen.py.

    python3 tests/e2e_cal.py --bin build/tvcal [--backend-dir backend] [-k NAME] [-v]

Exit codes:  0 pass, 1 fail, 77 skip (no pty, or pyte not installed).
"""
import datetime as dt
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tvcommon" / "tests"))      # a source tarball
sys.path.insert(0, str(HERE.parent.parent / "tvcommon" / "tests"))
from tvscreen import *            # noqa: E402,F401,F403
import tvscreen                   # noqa: E402


class Tv(tvscreen.Tv):
    prefix = "tvcal-regress-"
    drop_env = ("COLORTERM", "TVCAL_CONF", "TVCAL_DATA", "TVCAL_STATE", "TVCAL_REQUESTS", "TVCAL_BACKEND_LOG")

    def prepare(self):
        shutil.copy2(os.path.join(self.opts.backend_dir, "tvcal-backend"), self.bindir / "tvcal-backend")
        for d in (HERE.parent.parent / "tvcommon" / "py", HERE.parent / "tvcommon" / "py"):    # Floyd's words, ours
            if (d / "tvfloyd.py").exists():
                shutil.copy2(d / "tvfloyd.py", self.bindir / "tvfloyd.py")
                break
        self.env["TVCAL_DATA"] = str(self.tmp / "cal.ics")
        self.env["TVCAL_STATE"] = str(self.tmp / "state")
        self.env["TVCAL_CONF"] = str(self.tmp / "no-tvcal.conf")
        self.env["TV_SPOOL_DIR"] = str(self.tmp / "spool")
        self.env["XDG_STATE_HOME"] = str(self.tmp / "xdg-state")      # Floyd's input history
        self.env["XDG_DATA_HOME"] = str(self.tmp / "xdg-data")        # and his memory


def backend(tv, *argv):
    import subprocess
    p = subprocess.run([str(tv.bindir / "tvcal-backend")] + list(argv), env=tv.env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, universal_newlines=True)
    return p.stdout


def started(tv, c):
    c.check(tv.wait(lambda: tv.has("Agenda from") and tv.has("Services"), 15), "tvcal never showed its window")


def tvcal(opts, setup=None):
    tv = Tv(opts, setup=setup)
    return tv, Case(tv)


@case()
def panes(opts):
    tv, c = tvcal(opts)
    started(tv, c)
    today = dt.date.today()
    c.check(tv.has(today.strftime("%B %Y")), "the month heading is missing")
    c.check(tv.has("Mo Tu We Th Fr Sa Su"), "the weekday row is missing")
    c.check(tv.has("[ Services"), "the service pane does not show it has the focus")
    c.check(tv.has("Calendar") and tv.has("To-do"), "the services are not listed")
    c.check(tv.has("(nothing planned)"), "the agenda does not say today is empty")
    c.check(tv.has("Day"), "the detail pane has no heading")
    tv.send("Tab")
    c.check(tv.wait(lambda: tv.has("[ " + today.strftime("%B")), 3), "Tab did not move to the month")
    tv.send("Tab")
    c.check(tv.wait(lambda: tv.has("[ Agenda"), 3), "Tab did not move to the agenda")
    tv.send("Tab")
    c.check(tv.wait(lambda: tv.has("[ Day") or tv.has("[ Event"), 3), "Tab did not move to the detail pane")
    tv.send("Tab")
    c.check(tv.wait(lambda: tv.has("[ Services"), 3), "Tab did not come back to the services")
    tv.send("Down")
    c.check(tv.wait(lambda: tv.has("To-do (0)") and not tv.has("Mo Tu We"), 5), "Down did not show the to-do service")
    tv.send("Tab")
    c.check(tv.wait(lambda: tv.has("[ To-do"), 3), "Tab did not move to the to-do list")
    tv.send("\x18" if False else alt("x"))


@case()
def new_event_shows_in_the_agenda(opts):
    tv, c = tvcal(opts)
    started(tv, c)
    tv.send("CtrlN")
    c.check(tv.wait("New event", 5), "Ctrl-N did not open the New event dialog")
    tv.send("Lunch with Ann")
    tv.send("Tab", "Tab", "12:30")
    tv.send("Enter")
    c.check(tv.wait(lambda: tv.has("Lunch with Ann"), 8), "the new event is not in the agenda")
    c.check(tv.has("12:30"), "the event's time is missing")
    c.check(tv.has("Calendar") and tv.has("1"), "the service list does not count the day's event")
    tv.send("Tab", "Tab", "Down")                        # month, agenda, the event under the day's heading
    c.check(tv.wait(lambda: tv.has("When:") and tv.has("12:30"), 5), "the detail pane does not show the event")
    out = backend(tv, "range", dt.date.today().isoformat(), dt.date.today().isoformat())
    c.check("Lunch with Ann" in out, "the backend does not hold the event: " + out)
    c.check(tv.has("*"), "the month does not mark the day as busy")


@case()
def todo_add_and_tick(opts):
    tv, c = tvcal(opts)
    started(tv, c)
    tv.send("\x14")                                      # Ctrl-T
    c.check(tv.wait("New to-do", 5), "Ctrl-T did not open the New to-do dialog")
    tv.send("Water the plants", "Enter")
    c.check(tv.wait(lambda: tv.has("[ ] Water the plants"), 8), "the to-do is not listed")
    c.check(tv.has("To-do (1)"), "the to-do heading does not count it")
    c.check(tv.has("Status:") and tv.has("not done"), "the detail pane does not show the to-do")
    tv.send("Tab", " ")
    c.check(tv.wait(lambda: tv.has("To-do (0)"), 5), "space did not tick the to-do off")
    c.check("COMPLETED" in open(tv.tmp / "cal.ics").read(), "the file does not record it as completed")


@case()
def event_and_todo_notes_are_several_lines(opts):
    tv, c = tvcal(opts)
    started(tv, c)
    tv.send("CtrlN")
    c.check(tv.wait("New event", 5), "Ctrl-N did not open the New event dialog")
    tv.send("Planning day", *["Tab"] * 10)                 # down to the text box
    tv.send("agenda one", "Enter", "agenda two", "Enter", "agenda three")
    tv.send("\x1b" + "o")                                # Alt-O: OK
    c.check(tv.wait(lambda: tv.has("Planning day"), 8), "the event is not in the agenda")
    tv.send("Tab", "Tab", "Down")                            # month, agenda, the event
    c.check(tv.wait(lambda: tv.has("agenda one") and tv.has("agenda two") and tv.has("agenda three"), 5),
            "the details pane does not show the event's notes whole")
    out = backend(tv, "range", dt.date.today().isoformat(), dt.date.today().isoformat())
    uid = out.split("\t")[3]
    c.check("agenda one\\nagenda two\\nagenda three" in backend(tv, "show", uid), "the backend lost the new lines")
    tv.send("Enter")                                         # edit it: the lines are in the box
    c.check(tv.wait("Edit event", 5), "Enter did not open the edit dialog")
    c.check(tv.has("agenda two"), "the edit dialog does not hold the notes")
    tv.send("\x1b")
    tv.send("\x14")                                         # Ctrl-T
    c.check(tv.wait("New to-do", 5), "Ctrl-T did not open the New to-do dialog")
    tv.send("Pack the car", *["Tab"] * 4)
    tv.send("tent", "Enter", "stove")
    tv.send("\x1b" + "o")
    c.check(tv.wait(lambda: tv.has("tent") and tv.has("stove"), 8), "the to-do's notes are not shown whole")


@case()
def note_with_several_lines(opts):
    tv, c = tvcal(opts)
    started(tv, c)
    c.check(tv.has("Notes"), "the services do not list Notes")
    tv.send("\x1b" + "n")                                # Alt-N: the Notes menu
    c.check(tv.wait("New note...", 3), "there is no Notes menu")
    tv.send("n")
    c.check(tv.wait("New note", 5), "New note... did not open the dialog")
    c.check(tv.has("Enter starts a new line"), "the dialog does not say how the text box works")
    tv.send("Shopping", "Tab", "home", "Tab")                 # title, categories, then the text box
    tv.send("milk", "Enter", "bread", "Enter", "eggs")
    tv.send("\x1b" + "o")                                # Alt-O: OK
    c.check(tv.wait(lambda: tv.has("Notes (1)"), 8), "the note is not listed")
    c.check(tv.wait(lambda: tv.has("milk") and tv.has("bread") and tv.has("eggs"), 5), "the details pane does not show all the lines")
    c.check(tv.has("Categories: home"), "the categories are missing")
    out = backend(tv, "show", backend(tv, "notes").split("\t")[0])
    c.check("milk\\nbread\\neggs" in out, "the backend does not hold the lines: " + out)
    tv.send("Tab", "Enter")                                  # the list, then Enter edits
    c.check(tv.wait("Edit note", 5), "Enter did not open the edit dialog")
    c.check(tv.has("bread"), "the dialog does not hold the text")
    tv.send("\x1b")


@case()
def incoming_request_is_added_on_a_yes(opts):
    tv, c = tvcal(opts)
    started(tv, c)
    req = tv.tmp / "state" / "requests"
    req.mkdir(parents=True, exist_ok=True)
    (req / "1.req").write_text("todo@tvmail Reply to Bob\n")
    c.check(tv.wait(lambda: tv.has("1 incoming"), 10), "the status line does not say something is incoming")
    c.check("Reply to Bob" not in open(tv.tmp / "cal.ics").read() if (tv.tmp / "cal.ics").exists() else True,
            "it was added without a yes")
    c.check(tv.wait(lambda: tv.has("Inbox") and tv.has("1"), 5), "the service list does not count the Inbox")
    tv.send("\x1b" + "c")                                # Alt-C: the Calendar menu
    c.check(tv.wait("Inbox (from", 3), "the Calendar menu has no Inbox item")
    tv.send("i")
    c.check(tv.wait(lambda: tv.has("[ Inbox") and tv.has("Reply to Bob"), 5), "the Inbox does not list the request")
    c.check(tv.wait(lambda: tv.has("not in your calendar until you say yes"), 5), "the details do not say it is waiting")
    tv.send("Enter")                                     # look at it
    c.check(tv.wait("From tvmail", 5), "Enter did not open the request")
    c.check(tv.has("Reply to Bob"), "the dialog is not filled in")
    tv.send("Enter")                                     # Add
    c.check(tv.wait(lambda: not tv.has("incoming"), 8), "the incoming count did not go")
    tv.send("Tab", "Tab", "Up", "Up")                    # details, services, then To-do
    c.check(tv.wait(lambda: tv.has("[ ] Reply to Bob"), 8), "the to-do was not added")
    # a second one is discarded from the list
    (req / "2.req").write_text("todo@tvmail Not wanted\n")
    c.check(tv.wait(lambda: tv.has("1 incoming"), 10), "the second request did not arrive")
    tv.send("Down", "Down")                              # Notes, Inbox
    c.check(tv.wait(lambda: tv.has("Not wanted"), 5), "the Inbox does not list the second request")
    tv.send("Tab", "Delete")
    c.check(tv.wait("Discard", 5), "Del did not ask")
    tv.send("Enter")                                     # Yes
    c.check(tv.wait(lambda: not tv.has("incoming") and not tv.has("Not wanted"), 8), "the request was not discarded")
    c.check("Not wanted" not in backend(tv, "todos"), "a discarded request was added")


@case()
def f7_asks_floyd(opts):
    tv, c = tvcal(opts)
    started(tv, c)
    tv.send("\x1b[18~")                                  # F7
    c.check(tv.wait("ask him anything", 5), "F7 did not open Floyd's window")
    c.check(tv.wait(lambda: tv.has("Floyd helps with"), 10), "Floyd's greeting did not appear")
    c.check(tv.has("[type here]"), "the empty input line does not say where to type")
    tv.send("!todo add Feed the cat", "Enter")
    c.check(tv.wait(lambda: tv.has("you:   !todo add Feed the cat"), 10), "what was typed is not in the transcript")
    c.check(tv.wait(lambda: "Feed the cat" in backend(tv, "todos"), 10), "Floyd did not add the to-do")
    tv.send("\x1b[A", "Enter")                           # Up brings the line back; Enter says it again
    c.check(tv.wait(lambda: backend(tv, "todos").count("Feed the cat") == 2, 10),
            "Up did not recall the line that was sent")
    c.check("!todo add Feed the cat" in open(tv.tmp / "xdg-state" / "tvfloyd" / "input-history").read(),
            "the history file was not written")


@case()
def floyd_asks_claude_and_the_answer_arrives_by_itself(opts):
    def setup(t):
        fake = t.tmp / "claude"
        fake.write_text('#!/bin/sh\ncat > /dev/null\nsleep 2\necho "four"\n')
        fake.chmod(0o755)
        (t.tmp / "tvcal.conf").write_text("[floyd]\nclaude_cmd = %s\n" % fake)
        t.env["TVCAL_CONF"] = str(t.tmp / "tvcal.conf")
    tv, c = tvcal(opts, setup=setup)
    started(tv, c)
    tv.send("\x1b[18~")                                  # F7
    c.check(tv.wait("ask him anything", 5), "F7 did not open Floyd's window")
    tv.send("!claude what is 2+2?", "Enter")
    c.check(tv.wait(lambda: tv.has("will ask Claude"), 8), "he did not say he would ask")
    c.check(tv.wait(lambda: tv.has("Claude: four"), 20), "Claude's answer did not arrive in the window")
    tv.send("!status", "Enter")
    c.check(tv.wait(lambda: tv.has("Calendar: a local file"), 8), "!status did not answer")


@case()
def reminders_service_lists_what_fires_next(opts):
    tv, c = tvcal(opts)
    started(tv, c)
    day = (dt.date.today() + dt.timedelta(days=2)).isoformat()
    backend(tv, "add-event", "--date", day, "--start", "09:00", "--remind", "30", "Dentist")
    backend(tv, "add-todo", "--due", day, "Send the form")
    tv.send("F5")                                        # read it all again
    tv.send("Down", "Down", "Down", "Down")              # To-do, Notes, Inbox, Reminders
    c.check(tv.wait(lambda: tv.has("[ Services") and tv.has("Reminders (2)"), 8), "the Reminders list is not shown")
    c.check(tv.has("Dentist") and tv.has("Send the form"), "the reminders are not listed")
    c.check(tv.has(day[5:] + " 08:30"), "the event's reminder time is wrong")
    c.check(tv.has("due"), "the to-do is not marked as due")
    tv.send("Tab", "Down", "Tab")                       # the list, the event (the to-do is first), the details
    c.check(tv.wait(lambda: tv.has("Reminds you") and tv.has("30 min before"), 5), "the details do not say what reminds")
    tv.send("ShiftTab", "Enter")                         # back to the list, go to it
    c.check(tv.wait(lambda: tv.has("[ Agenda") and tv.has("Agenda from"), 5), "Enter did not go to the event")


@case()
def reminder_reaches_tvmail(opts):
    now = dt.datetime.now()
    if now.hour >= 23 or now.hour < 1:
        skip("too near midnight")
    tv, c = tvcal(opts)
    started(tv, c)
    start = (now + dt.timedelta(minutes=3)).strftime("%H:%M")
    backend(tv, "add-event", "--date", "today", "--start", start, "--remind", "10", "Dentist")
    spool = tv.tmp / "spool" / "tvmail"
    c.check(tv.wait(lambda: spool.is_dir() and any(spool.iterdir()), 45), "no reminder note reached tvmail's inbox")
    note = next(spool.iterdir()).read_text()
    c.check("Dentist" in note and note.startswith("1\ttvcal\t"), "the note is wrong: " + note)
    c.check(tv.has("Dentist"), "tvcal itself did not show the reminder")
    c.check((tv.tmp / "spool" / "tvchat").is_dir(), "tvchat's inbox got none")


if __name__ == "__main__":
    run("tvcal")
