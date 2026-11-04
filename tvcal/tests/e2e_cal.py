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
        self.env["TVCAL_DATA"] = str(self.tmp / "cal.ics")
        self.env["TVCAL_STATE"] = str(self.tmp / "state")
        self.env["TVCAL_CONF"] = str(self.tmp / "no-tvcal.conf")
        self.env["TV_SPOOL_DIR"] = str(self.tmp / "spool")


def backend(tv, *argv):
    import subprocess
    p = subprocess.run([str(tv.bindir / "tvcal-backend")] + list(argv), env=tv.env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, universal_newlines=True)
    return p.stdout


def started(tv, c):
    c.check(tv.wait(lambda: tv.has("Agenda from") and tv.has("To-do (0)"), 15), "tvcal never showed its window")


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
    c.check(tv.has("[ " + today.strftime("%B")), "the month pane does not show it has the focus")
    c.check(tv.has("(nothing planned)"), "the agenda does not say today is empty")
    tv.send("Tab")
    c.check(tv.wait(lambda: tv.has("[ Agenda"), 3), "Tab did not move to the agenda")
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
    tv.send("Tab", "Tab", " ")
    c.check(tv.wait(lambda: tv.has("To-do (0)"), 5), "space did not tick the to-do off")
    c.check("COMPLETED" in open(tv.tmp / "cal.ics").read(), "the file does not record it as completed")


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
    tv.send("\x1b" + "c")                                # Alt-C: the Calendar menu
    c.check(tv.wait("Incoming from", 3), "the Calendar menu has no Incoming item")
    tv.send("i")
    c.check(tv.wait("From tvmail", 5), "Incoming... did not open the request")
    c.check(tv.has("Reply to Bob"), "the dialog is not filled in")
    tv.send("Enter")                                     # Add
    c.check(tv.wait(lambda: tv.has("[ ] Reply to Bob"), 8), "the to-do was not added")
    c.check(not tv.has("incoming"), "the incoming count did not go")


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
