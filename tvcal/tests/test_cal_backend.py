#!/usr/bin/env python3
"""Unit and verb tests for tvcal-backend: iCalendar round trip, repeats, to-dos,
reminders, the requests directory and the serve framing.  No network; every test
runs on its own temp data, state and config.

    python3 -m unittest discover -s tests -p 'test_cal_backend.py'
"""
import datetime as dt
import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(HERE, "..", "backend", "tvcal-backend")


def load():
    loader = importlib.machinery.SourceFileLoader("tvcal_backend", BACKEND)
    spec = importlib.util.spec_from_loader("tvcal_backend", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


tb = load()
D = dt.date


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tvcal-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = dict(os.environ, TVCAL_DATA=os.path.join(self.tmp, "cal.ics"),
                        TVCAL_STATE=os.path.join(self.tmp, "state"),
                        TVCAL_CONF=os.path.join(self.tmp, "tvcal.conf"),
                        TVCAL_REQUESTS=os.path.join(self.tmp, "state", "requests"))
        for k in ("TVCAL_DATA", "TVCAL_STATE", "TVCAL_CONF", "TVCAL_REQUESTS"):
            os.environ[k] = self.env[k]
            self.addCleanup(os.environ.pop, k, None)

    def run_be(self, *argv, stdin=None, check=True):
        p = subprocess.run([sys.executable, BACKEND] + list(argv), env=self.env, input=stdin,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        if check and p.returncode != 0:
            self.fail("tvcal-backend %s -> %d: %s%s" % (" ".join(argv), p.returncode, p.stdout, p.stderr))
        return p

    def lines(self, *argv):
        out = self.run_be(*argv).stdout
        return [l.split("\t") for l in out.splitlines() if l]


class Parsing(unittest.TestCase):
    def test_fold_roundtrip_and_escapes(self):
        c = tb.Comp("VEVENT")
        long = "x" * 200 + "é,;\\ and more"
        c.set("SUMMARY", tb.esc(long))
        text = tb.comp_text(c)
        self.assertTrue(all(len(l.encode()) <= 75 for l in text.split("\r\n")))
        back = tb.parse_ics(text)[0]
        self.assertEqual(back.text("SUMMARY"), long)

    def test_multiline_note(self):
        self.assertEqual(tb.unesc(tb.esc("a\nb;c,d\\e")), "a\nb;c,d\\e")

    def test_dates(self):
        base = D(2026, 11, 3)                       # a Tuesday
        self.assertEqual(tb.norm_date("tomorrow", base), D(2026, 11, 4))
        self.assertEqual(tb.norm_date("+10", base), D(2026, 11, 13))
        self.assertEqual(tb.norm_date("fri", base), D(2026, 11, 6))
        self.assertEqual(tb.norm_date("tue", base), D(2026, 11, 10))      # the next one, not today
        self.assertEqual(tb.norm_date("1-5", base), D(2027, 1, 5))
        self.assertEqual(tb.norm_date("2026-12-25", base), D(2026, 12, 25))
        for bad in ("2026-02-30", "soon", "13-45"):
            with self.assertRaises(tb.UserError):
                tb.norm_date(bad, base)

    def test_times(self):
        self.assertEqual(tb.norm_time("9:05"), "09:05")
        self.assertEqual(tb.norm_time("1430"), "14:30")
        with self.assertRaises(tb.UserError):
            tb.norm_time("25:00")

    def test_utc_converted_to_local(self):
        d, t, allday = tb.parse_ics_when(["DTSTART", "", "20261103T120000Z"])
        want = dt.datetime(2026, 11, 3, 12, tzinfo=dt.timezone.utc).astimezone()
        self.assertEqual((d, t, allday), (want.date(), want.strftime("%H:%M"), False))

    def test_triggers(self):
        self.assertEqual(tb.trigger_minutes("-PT15M"), 15)
        self.assertEqual(tb.trigger_minutes("-P1D"), 1440)
        self.assertEqual(tb.trigger_minutes("-PT1H30M"), 90)
        self.assertEqual(tb.trigger_minutes("PT0S"), 0)


def ev_from(rrule, start="20261103", exdate=None, allday=True):
    c = tb.Comp("VEVENT")
    c.set("UID", "t")
    c.set("SUMMARY", "t")
    if allday:
        c.set("DTSTART", start, ";VALUE=DATE")
    else:
        c.set("DTSTART", start)
    if rrule:
        c.set("RRULE", rrule)
    if exdate:
        c.set("EXDATE", exdate, ";VALUE=DATE")
    return tb.Item(c).read_event()


class Repeats(unittest.TestCase):
    def test_single(self):
        e = ev_from(None)
        self.assertEqual(tb.occurrences(e, D(2026, 11, 1), D(2026, 11, 30)), [D(2026, 11, 3)])
        self.assertEqual(tb.occurrences(e, D(2026, 11, 4), D(2026, 11, 30)), [])

    def test_daily_interval_far_window(self):
        e = ev_from("FREQ=DAILY;INTERVAL=3")
        got = tb.occurrences(e, D(2027, 3, 1), D(2027, 3, 10))
        self.assertTrue(got and all((d - D(2026, 11, 3)).days % 3 == 0 for d in got))
        self.assertEqual(len(got), 3)

    def test_daily_count_and_until(self):
        self.assertEqual(len(tb.occurrences(ev_from("FREQ=DAILY;COUNT=4"), D(2026, 1, 1), D(2030, 1, 1))), 4)
        got = tb.occurrences(ev_from("FREQ=DAILY;UNTIL=20261106"), D(2026, 1, 1), D(2030, 1, 1))
        self.assertEqual(got[-1], D(2026, 11, 6))

    def test_weekly_byday(self):
        e = ev_from("FREQ=WEEKLY;BYDAY=MO,WE,FR")          # starts Tue 3 Nov
        got = tb.occurrences(e, D(2026, 11, 1), D(2026, 11, 14))
        self.assertEqual(got, [D(2026, 11, 4), D(2026, 11, 6), D(2026, 11, 9), D(2026, 11, 11), D(2026, 11, 13)])

    def test_weekly_biweekly_plain(self):
        e = ev_from("FREQ=WEEKLY;INTERVAL=2")
        got = tb.occurrences(e, D(2026, 11, 1), D(2026, 12, 31))
        self.assertEqual(got, [D(2026, 11, 3), D(2026, 11, 17), D(2026, 12, 1), D(2026, 12, 15), D(2026, 12, 29)])

    def test_weekly_far_window_keeps_phase(self):
        e = ev_from("FREQ=WEEKLY;INTERVAL=2")
        got = tb.occurrences(e, D(2028, 1, 1), D(2028, 2, 28))
        self.assertTrue(got)
        self.assertTrue(all((d - D(2026, 11, 3)).days % 14 == 0 for d in got))

    def test_monthly_skips_short_months(self):
        e = ev_from("FREQ=MONTHLY", start="20260131")
        got = tb.occurrences(e, D(2026, 1, 1), D(2026, 6, 30))
        self.assertEqual(got, [D(2026, 1, 31), D(2026, 3, 31), D(2026, 5, 31)])

    def test_yearly_leap_day(self):
        e = ev_from("FREQ=YEARLY", start="20240229")
        got = tb.occurrences(e, D(2024, 1, 1), D(2032, 12, 31))
        self.assertEqual(got, [D(2024, 2, 29), D(2028, 2, 29), D(2032, 2, 29)])

    def test_exdate(self):
        e = ev_from("FREQ=DAILY;COUNT=3", exdate="20261104")
        self.assertEqual(tb.occurrences(e, D(2026, 11, 1), D(2026, 11, 30)), [D(2026, 11, 3), D(2026, 11, 5)])


class Verbs(Env):
    def test_event_lifecycle(self):
        uid = self.run_be("add-event", "--date", "2026-11-05", "--start", "14:00", "--end", "15:00",
                          "--location", "Room 4", "--remind", "10", "Dentist").stdout.strip()
        r = self.lines("range", "2026-11-01", "2026-11-30")
        self.assertEqual(r[0][:7], ["2026-11-05", "14:00", "15:00", uid, "Dentist", "Room 4", "none"])
        self.assertEqual(r[0][7], "10")
        show = self.lines("show", uid)[0]
        self.assertEqual(show[:7], ["event", uid, "Dentist", "Room 4", "2026-11-05", "14:00", "15:00"])
        self.run_be("set-event", uid, "--date", "2026-11-06", "--repeat", "weekly", "--interval", "2", "Dentist again")
        r = self.lines("range", "2026-11-01", "2026-12-31")
        self.assertEqual([x[0] for x in r], ["2026-11-06", "2026-11-20", "2026-12-04", "2026-12-18"])
        self.assertEqual(r[0][4], "Dentist again")
        self.run_be("set-event", uid, "--repeat", "none")
        self.assertEqual(len(self.lines("range", "2026-11-01", "2026-12-31")), 1)
        self.run_be("rm", uid)
        self.assertEqual(self.lines("range", "2026-11-01", "2026-12-31"), [])

    def test_all_day_spanning_days(self):
        self.run_be("add-event", "--date", "2026-12-24", "--days", "3", "Holiday")
        r = self.lines("range", "2026-12-20", "2026-12-31")
        self.assertEqual([x[0] for x in r], ["2026-12-24", "2026-12-25", "2026-12-26"])
        self.assertEqual(r[0][1], "")
        r = self.lines("range", "2026-12-26", "2026-12-26")     # a window starting inside the span
        self.assertEqual(len(r), 1)

    def test_bad_input(self):
        p = self.run_be("add-event", "--date", "2026-02-30", "x", check=False)
        self.assertEqual(p.returncode, 1)
        p = self.run_be("add-event", "--start", "10:00", "--end", "09:00", "x", check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("before it starts", p.stderr)
        p = self.run_be("add-event", check=False)
        self.assertIn("title", p.stderr)

    def test_todos(self):
        a = self.run_be("add-todo", "--due", "2026-11-09", "--prio", "2", "Write report").stdout.strip()
        b = self.run_be("add-todo", "--due", "2026-11-04", "--cats", "home,garden", "Mow lawn").stdout.strip()
        c = self.run_be("add-todo", "No date at all").stdout.strip()
        t = self.lines("todos")
        self.assertEqual([x[0] for x in t], [b, a, c])                 # by due date, undated last
        self.assertEqual(t[0][1:], ["open", "0", "2026-11-04", "Mow lawn", "home,garden"])
        self.run_be("done", b)
        self.assertEqual([x[0] for x in self.lines("todos")], [a, c])
        self.assertEqual([x[0] for x in self.lines("todos", "--all")][-1], b)       # done ones last
        self.run_be("done", b, "--undo")
        self.assertEqual(len(self.lines("todos")), 3)
        self.run_be("set-todo", a, "--prio", "1", "--due", "", "--summary", "Write the report")
        row = [x for x in self.lines("todos") if x[0] == a][0]
        self.assertEqual(row[2:5], ["1", "", "Write the report"])
        self.run_be("rm", c)
        self.assertEqual(len(self.lines("todos")), 2)

    def test_file_is_standard_ics(self):
        self.run_be("add-event", "--date", "2026-11-05", "--start", "09:30", "Meet; with, odd\\chars")
        self.run_be("add-todo", "--due", "2026-11-06", "Call")
        text = open(self.env["TVCAL_DATA"], "rb").read().decode()
        self.assertTrue(text.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"))
        for want in ("BEGIN:VEVENT", "DTSTART:20261105T093000", "BEGIN:VALARM", "TRIGGER:-PT15M",
                     "BEGIN:VTODO", "DUE;VALUE=DATE:20261106", "STATUS:NEEDS-ACTION", "SUMMARY:Meet\\; with\\, odd\\\\chars"):
            self.assertIn(want, text)
        self.assertTrue(text.rstrip().endswith("END:VCALENDAR"))

    def test_unknown_properties_survive_an_edit(self):
        open(self.env["TVCAL_DATA"], "w").write(
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:x1\r\nSUMMARY:Imported\r\n"
            "DTSTART;TZID=Europe/London:20261110T100000\r\nATTENDEE;CN=Bob:mailto:bob@example.com\r\n"
            "X-CUSTOM:keep me\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
        self.assertEqual(self.lines("range", "2026-11-10", "2026-11-10")[0][:2], ["2026-11-10", "10:00"])
        self.run_be("set-event", "x1", "--location", "Pub")
        text = open(self.env["TVCAL_DATA"]).read()
        self.assertIn("ATTENDEE;CN=Bob:mailto:bob@example.com", text)
        self.assertIn("X-CUSTOM:keep me", text)
        self.assertIn("LOCATION:Pub", text)

    def test_import_replaces_by_uid_and_export(self):
        src = os.path.join(self.tmp, "in.ics")
        open(src, "w").write("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u1\r\nSUMMARY:One\r\nDTSTART:20261201T080000\r\n"
                             "END:VEVENT\r\nBEGIN:VTODO\r\nUID:u2\r\nSUMMARY:Task\r\nDUE:20261202\r\nEND:VTODO\r\n"
                             "END:VCALENDAR\r\n")
        self.assertEqual(self.run_be("import", src).stdout.strip(), "imported 2")
        self.assertEqual(self.run_be("import", src).stdout.strip(), "imported 2")
        self.assertEqual(len(self.lines("range", "2026-12-01", "2026-12-01")), 1)      # not doubled
        self.assertEqual(len(self.lines("todos")), 1)
        one = self.run_be("ics", "u1").stdout
        self.assertIn("SUMMARY:One", one)
        self.assertNotIn("Task", one)

    def test_find_and_describe(self):
        t = D.today().isoformat()
        uid = self.run_be("add-event", "--date", t, "--start", "11:00", "--location", "Cafe", "Lunch with Ann").stdout.strip()
        self.run_be("add-todo", "Buy ann a card")
        hits = self.lines("find", "ANN")
        self.assertEqual(sorted(h[0] for h in hits), ["event", "todo"])
        d = self.run_be("describe", uid).stdout
        self.assertIn("Lunch with Ann", d)
        self.assertIn("11:00", d)
        self.assertIn("(Cafe)", d)

    def test_due_tells_each_reminder_once(self):
        now = dt.datetime.now()
        if now.hour == 23 or now.hour == 0:
            self.skipTest("too near midnight for a same-day reminder")
        start = (now + dt.timedelta(minutes=5)).strftime("%H:%M")
        uid = self.run_be("add-event", "--date", "today", "--start", start, "--remind", "10", "Soon").stdout.strip()
        far = (now + dt.timedelta(hours=3)).strftime("%H:%M")
        if (now + dt.timedelta(hours=3)).date() == now.date():
            self.run_be("add-event", "--date", "today", "--start", far, "--remind", "10", "Later")
        tid = self.run_be("add-todo", "--due", "yesterday", "Overdue thing").stdout.strip()
        first = self.lines("due")
        kinds = sorted((r[0], r[1]) for r in first)
        self.assertEqual(kinds, sorted([("event", uid), ("todo", tid)]))
        self.assertEqual(self.lines("due"), [])

    def test_remind_off(self):
        now = dt.datetime.now() + dt.timedelta(minutes=2)
        if now.date() != dt.date.today():
            self.skipTest("near midnight")
        self.run_be("add-event", "--date", "today", "--start", now.strftime("%H:%M"), "--remind", "off", "Quiet")
        self.assertEqual(self.lines("due"), [])

    def test_default_remind_from_config(self):
        open(self.env["TVCAL_CONF"], "w").write("[cal]\nremind = 30\n")
        uid = self.run_be("add-event", "--date", "2026-11-05", "x").stdout.strip()
        self.assertEqual(self.lines("range", "2026-11-05", "2026-11-05")[0][7], "30")
        open(self.env["TVCAL_CONF"], "w").write("[cal]\nremind = off\n")
        uid2 = self.run_be("add-event", "--date", "2026-11-06", "y").stdout.strip()
        self.assertEqual(self.lines("range", "2026-11-06", "2026-11-06")[0][7], "")

    def test_check_config(self):
        good = os.path.join(self.tmp, "g.conf")
        open(good, "w").write("[cal]\nremind = 10\nweek_start = sun\n[ui]\nshare = error\n")
        self.assertIn("ok:", self.run_be("check-config", good).stdout)
        bad = os.path.join(self.tmp, "b.conf")
        open(bad, "w").write("[cal]\nremind = soon\nweek_start = wed\nwhat = 1\n")
        p = self.run_be("check-config", bad, check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("remind", p.stdout)
        self.assertIn("week_start", p.stdout)
        self.assertIn("warning: [cal] what", p.stdout)


class Notes(Env):
    """Notes are VJOURNALs: a title, free text of several lines, categories."""

    def test_note_lifecycle(self):
        uid = self.run_be("add-note", "--cats", "home,ideas", "--notesx", "line one\\nline two", "Shopping").stdout.strip()
        row = self.lines("notes")[0]
        self.assertEqual([row[0], row[2], row[3]], [uid, "Shopping", "home,ideas"])
        self.assertEqual(row[4], "line one line two")                   # the list shows the text on one line
        show = self.run_be("show", uid).stdout.rstrip("\n").split("\t")
        self.assertEqual(show[0], "note")
        self.assertEqual(show[2], "Shopping")
        self.assertEqual(show[5], "line one\\nline two")                  # the whole text, a new line as \\n
        self.run_be("set-note", uid, "--summary", "Groceries", "--notesx", "a\\\\b\\nc")
        show = self.run_be("show", uid).stdout.rstrip("\n").split("\t")
        self.assertEqual(show[2], "Groceries")
        self.assertEqual(show[5], "a\\\\b\\nc")                           # a backslash stays one, a new line stays one
        self.assertEqual(show[4], "home,ideas")                        # what was not named is left alone
        self.run_be("rm", uid)
        self.assertEqual(self.lines("notes"), [])

    def test_the_first_line_is_the_title_when_there_is_none(self):
        self.run_be("add-note", "--notesx", "Call Ann about the roof\\nbefore Friday")
        self.assertEqual(self.lines("notes")[0][2], "Call Ann about the roof")
        p = self.run_be("add-note", check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("title or some text", p.stderr)

    def test_the_file_is_standard_icalendar(self):
        self.run_be("add-note", "--notesx", "x\\ny", "Plain")
        text = open(self.env["TVCAL_DATA"]).read()
        self.assertIn("BEGIN:VJOURNAL", text)
        self.assertIn("SUMMARY:Plain", text)
        self.assertIn("DESCRIPTION:x\\ny", text)
        self.assertIn("DTSTART;VALUE=DATE:", text)

    def test_notes_are_not_events_or_to_dos(self):
        uid = self.run_be("add-note", "Just a note").stdout.strip()
        self.run_be("add-event", "--date", "2026-11-05", "An event")
        self.run_be("add-todo", "A to-do")
        self.assertEqual(len(self.lines("todos")), 1)
        self.assertEqual(len(self.lines("range", "2026-01-01", "2026-12-31")), 1)
        for verb in (["set-event", uid, "x"], ["set-todo", uid, "--summary", "x"], ["done", uid]):
            p = self.run_be(*verb, check=False)
            self.assertEqual(p.returncode, 1, verb)
            self.assertIn("note", p.stderr)
        todo = self.lines("todos")[0][0]
        p = self.run_be("set-note", todo, "--summary", "x", check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("not a note", p.stderr)

    def test_find_looks_in_notes(self):
        self.run_be("add-note", "--notesx", "the plumber is Mr Gray", "House")
        r = self.lines("find", "plumber")
        self.assertEqual([x[0] for x in r], ["note"])
        self.assertEqual(r[0][3], "House")

    def test_newest_first(self):
        old = self.run_be("add-note", "Old one").stdout.strip()
        new = self.run_be("add-note", "New one").stdout.strip()
        with open(self.env["TVCAL_DATA"]) as fh:
            text = fh.read()
        # the Old one's block: make its LAST-MODIFIED years ago (a note's own block holds its summary)
        blocks = text.split("BEGIN:VJOURNAL")
        blocks = [bl.replace("LAST-MODIFIED:20", "LAST-MODIFIED:19", 1) if "Old one" in bl else bl for bl in blocks]
        with open(self.env["TVCAL_DATA"], "w") as fh:
            fh.write("BEGIN:VJOURNAL".join(blocks))
        self.assertEqual([r[0] for r in self.lines("notes")], [new, old])

    def test_import_and_export_keep_notes(self):
        self.run_be("add-note", "--notesx", "kept", "Travels")
        ics = self.run_be("ics").stdout
        other = os.path.join(self.tmp, "other.ics")
        open(other, "w").write(ics)
        os.unlink(self.env["TVCAL_DATA"])
        self.run_be("import", other)
        self.assertEqual(self.lines("notes")[0][2], "Travels")

    def test_serve_takes_a_note_on_one_line(self):
        p = self.run_be("serve", stdin="add-note --notesx 'a\\nb' 'Via serve'\nnotes\nquit\n")
        self.assertIn("Via serve", p.stdout)
        self.assertEqual(self.lines("notes")[0][4], "a b")


class Upcoming(Env):
    """`upcoming`: what will remind you, soonest first, without marking anything as reported."""

    def test_events_and_todos_in_order(self):
        today = dt.date.today()
        day = lambda n: (today + dt.timedelta(days=n)).isoformat()
        far = self.run_be("add-event", "--date", day(3), "--remind", "10", "Later").stdout.strip()
        near = self.run_be("add-event", "--date", day(1), "--start", "10:00", "--remind", "30", "Soon").stdout.strip()
        self.run_be("add-event", "--date", day(1), "--remind", "off", "No reminder")
        self.run_be("add-event", "--date", day(-3), "--start", "10:00", "--remind", "5", "Over")
        self.run_be("add-event", "--date", day(40), "--remind", "5", "Too far")
        self.run_be("add-todo", "--due", day(-2), "Overdue one")
        self.run_be("add-todo", "--due", day(2), "Due soon")
        self.run_be("add-todo", "--due", day(60), "Much later")
        done = self.run_be("add-todo", "--due", day(2), "Done already").stdout.strip()
        self.run_be("done", done)
        rows = self.lines("upcoming")
        self.assertEqual([r[5] for r in rows], ["Overdue one", "Soon", "Due soon", "Later"])
        self.assertEqual(rows[0][1:2] + rows[0][6:], ["todo", "overdue"])
        self.assertEqual((rows[1][0], rows[1][6]), (day(1) + " 09:30", "30"))     # fires 30 minutes before
        self.assertEqual(rows[3][2], far)
        self.assertEqual(rows[1][2], near)
        self.assertEqual([r[5] for r in self.lines("upcoming", "--days", "60")][-1], "Much later")
        self.assertEqual(self.lines("upcoming"), rows)                          # nothing was used up


class Requests(Env):
    def drop(self, name, line):
        d = self.env["TVCAL_REQUESTS"]
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, name + ".req"), "w").write(line + "\n")

    def test_todo_and_event_wait_for_a_yes(self):
        self.drop("a", "todo@tvmail Reply to  Bob")
        self.drop("b", "event@tvchat 2026-12-01 14:00-15:30 Planning call")
        self.drop("c", "nonsense")
        out = self.lines("requests")
        self.assertEqual(len(out), 3)
        self.assertTrue(all(o[0] == "notice" for o in out))
        self.assertIn("tvmail asks to add a to-do", out[0][1])
        self.assertEqual(self.lines("todos"), [])                   # nothing added by itself
        self.assertEqual(self.lines("range", "2026-12-01", "2026-12-01"), [])
        pend = self.lines("pending")
        self.assertEqual([p[1:4] for p in pend], [["todo", "tvmail", "Reply to Bob"],
                                                  ["event", "tvchat", "Planning call"]])
        got = self.lines("pending-get", pend[1][0])[0]
        self.assertEqual(got[:7], ["event", pend[1][0], "Planning call", "", "2026-12-01", "14:00", "15:30"])
        self.run_be("pending-drop", pend[0][0])
        self.assertEqual(len(self.lines("pending")), 1)
        self.assertEqual(self.lines("requests"), [])                # consumed

    def test_eml_with_a_calendar_part(self):
        eml = os.path.join(self.tmp, "m.eml")
        open(eml, "w").write(
            "From: Ann <ann@example.com>\nTo: me@example.com\nSubject: Lunch?\nDate: Mon, 02 Nov 2026 10:00:00 +0000\n"
            "MIME-Version: 1.0\nContent-Type: multipart/mixed; boundary=B\n\n--B\nContent-Type: text/plain\n\nSee invite.\n"
            "--B\nContent-Type: text/calendar; method=REQUEST\n\nBEGIN:VCALENDAR\nMETHOD:REQUEST\nBEGIN:VEVENT\n"
            "UID:inv1\nSUMMARY:Team lunch\nLOCATION:Canteen\nDTSTART:20261120T120000\nDTEND:20261120T130000\n"
            "END:VEVENT\nEND:VCALENDAR\n--B--\n")
        self.drop("e", "eml@tvmail " + eml)
        self.lines("requests")
        pend = self.lines("pending")
        self.assertEqual(pend[0][1:5], ["event", "tvmail", "Team lunch", "2026-11-20"])
        got = self.lines("pending-get", pend[0][0])[0]
        self.assertEqual((got[3], got[5], got[6]), ("Canteen", "12:00", "13:00"))

    def test_eml_without_one_uses_the_subject(self):
        eml = os.path.join(self.tmp, "n.eml")
        open(eml, "w").write("From: Bob <bob@example.com>\nSubject: Quarterly figures\n"
                             "Date: Fri, 01 Jan 2021 10:00:00 +0000\n\nbody\n")
        self.drop("e", "eml@tvmail " + eml)
        self.lines("requests")
        p = self.lines("pending")[0]
        self.assertEqual((p[1], p[3], p[4]), ("event", "Quarterly figures", D.today().isoformat()))   # a past date is not used
        got = self.lines("pending-get", p[0])[0]
        self.assertIn("bob@example.com", got[12])

    def test_a_request_for_a_missing_file_is_reported(self):
        self.drop("x", "ics /no/such/file.ics")
        out = self.lines("requests")
        self.assertIn("failed", out[0][1])
        self.assertEqual(self.lines("pending"), [])


class Serve(Env):
    def test_framing(self):
        p = subprocess.Popen([sys.executable, BACKEND, "serve"], env=self.env, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        def frame():
            head = p.stdout.readline().decode().split()
            return int(head[0]), p.stdout.read(int(head[1])).decode()

        try:
            self.assertEqual(frame(), (0, "ready"))
            p.stdin.write(b"ping\n"); p.stdin.flush()
            self.assertEqual(frame(), (0, "pong\n"))
            p.stdin.write(b"add-event --date 2026-11-05 'Quoted  title'\n"); p.stdin.flush()
            st, body = frame()
            self.assertEqual(st, 0)
            uid = body.strip()
            p.stdin.write(("range 2026-11-05 2026-11-05\n").encode()); p.stdin.flush()
            st, body = frame()
            self.assertIn("Quoted  title", body)
            p.stdin.write(b"show nosuchuid\n"); p.stdin.flush()
            st, body = frame()
            self.assertEqual(st, 1)
            p.stdin.write(b"bogus-verb\n"); p.stdin.flush()
            self.assertEqual(frame()[0], 2)
            p.stdin.write(b"ping\n"); p.stdin.flush()               # still alive
            self.assertEqual(frame(), (0, "pong\n"))
        finally:
            p.stdin.close()
            p.wait(5)


class FakeCalDav:
    """The bits of a CalDAV collection tvcal uses: GET of the whole collection, PUT / DELETE of
    one item, basic auth."""

    def __init__(self):
        import http.server, threading, base64
        self.items = {}                 # file name -> VEVENT/VTODO text
        self.log = []
        self.auth = "Basic " + base64.b64encode(b"me:secret").decode()
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _ok(self):
                if self.headers.get("Authorization") != outer.auth:
                    self.send_response(401)
                    self.end_headers()
                    return False
                return True

            def do_GET(self):
                if not self._ok():
                    return
                outer.log.append("GET")
                body = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "".join(outer.items.values()) + "END:VCALENDAR\r\n"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body.encode())))
                self.end_headers()
                self.wfile.write(body.encode())

            def do_PUT(self):
                if not self._ok():
                    return
                n = int(self.headers.get("Content-Length", 0))
                text = self.rfile.read(n).decode()
                inner = text.split("VERSION:2.0\r\n", 1)[1].rsplit("END:VCALENDAR", 1)[0]
                outer.items[self.path] = inner
                outer.log.append("PUT " + self.path)
                self.send_response(201)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_DELETE(self):
                if not self._ok():
                    return
                outer.log.append("DELETE " + self.path)
                self.send_response(204 if outer.items.pop(self.path, None) is not None else 404)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d/me/cal/" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class RemoteMode(Env):
    def setUp(self):
        super().setUp()
        self.dav = FakeCalDav()
        self.addCleanup(self.dav.close)
        self.env.pop("TVCAL_DATA")
        os.environ.pop("TVCAL_DATA", None)
        self.env["TVCAL_PASS"] = "secret"
        with open(self.env["TVCAL_CONF"], "w") as fh:
            fh.write("[server]\nurl = %s\nuser = me\n" % self.dav.url)

    def test_items_go_to_the_server_and_come_back(self):
        uid = self.run_be("add-event", "--date", "2026-11-05", "--start", "10:00", "Standup").stdout.strip()
        self.assertEqual(len(self.dav.items), 1)
        self.assertIn("SUMMARY:Standup", list(self.dav.items.values())[0])
        # another machine's view: no cache, same server
        shutil.rmtree(self.env["TVCAL_STATE"])
        self.assertEqual(self.lines("range", "2026-11-05", "2026-11-05")[0][4], "Standup")
        self.run_be("add-todo", "Water plants")
        self.assertEqual(len(self.dav.items), 2)
        self.dav.log.clear()
        self.run_be("set-event", uid, "--location", "Room 1")
        self.assertEqual([l for l in self.dav.log if l != "GET"].__len__(), 1)      # only the changed item is sent
        self.run_be("rm", uid)
        self.assertTrue(any(l.startswith("DELETE") for l in self.dav.log))
        self.assertEqual(len(self.dav.items), 1)

    def test_a_note_is_stored_on_the_server_like_the_rest(self):
        uid = self.run_be("add-note", "--notesx", "a\\nb", "Server note").stdout.strip()
        self.assertEqual(len(self.dav.items), 1)
        self.assertIn("BEGIN:VJOURNAL", list(self.dav.items.values())[0])
        shutil.rmtree(self.env["TVCAL_STATE"])                    # another machine
        self.assertEqual(self.lines("notes")[0][2], "Server note")
        self.run_be("rm", uid)
        self.assertEqual(len(self.dav.items), 0)

    def test_reads_use_a_fresh_copy_and_survive_the_server_going(self):
        self.run_be("add-event", "--date", "2026-11-05", "Kept")
        self.dav.close()
        self.assertEqual(self.lines("range", "2026-11-05", "2026-11-05")[0][4], "Kept")   # the copy
        os.utime(os.path.join(self.env["TVCAL_STATE"], "cache.ics"), (1, 1))                 # stale: it tries the server
        self.assertEqual(self.lines("range", "2026-11-05", "2026-11-05")[0][4], "Kept")   # ... and falls back
        p = self.run_be("add-event", "--date", "2026-11-06", "Lost", check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("nothing was saved", p.stderr)

    def test_wrong_password(self):
        self.env["TVCAL_PASS"] = "nope"
        p = self.run_be("todos", check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("refused the login", p.stderr)

    def test_netrc_matches_host_and_login_exactly(self):
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        with open(os.path.join(home, ".netrc"), "w") as fh:
            fh.write("machine 127.0.0.1 login me-too password wrong1\n"  # allowlist secret
                     "machine 127.0.0.1 login other\n  password wrong2\n"  # allowlist secret
                     "machine 127.0.0.1:%d login me password secret\n" % self.dav.srv.server_address[1])  # allowlist secret
        self.env.pop("TVCAL_PASS")
        self.env["HOME"] = home
        os.environ["HOME"], old = home, os.environ.get("HOME")
        self.addCleanup(os.environ.__setitem__, "HOME", old)
        self.assertEqual(self.lines("todos"), [])                 # logged in with the "me" line only

    def test_mu_tickets_come_first_and_match_the_user(self):
        port = self.dav.srv.server_address[1]
        tix = os.path.join(self.tmp, "tickets")
        with open(tix, "w") as fh:
            fh.write("imaps://me:wrong@127.0.0.1\nhttps://other:wrong2@127.0.0.1:%d\n"  # allowlist secret
                     "https://me:s%%65cret@127.0.0.1:%d\n" % (port, port))      # allowlist secret
        self.env.pop("TVCAL_PASS")
        self.env["TVMAIL_MU_TICKETS"] = tix
        self.env["HOME"] = os.path.join(self.tmp, "nohome")
        self.assertEqual(self.lines("todos"), [])                 # logged in: the https line for "me", percent-decoded

    def test_mu_tickets_host_may_be_bare_or_qualified(self):
        tix = os.path.join(self.tmp, "tickets2")
        with open(tix, "w") as fh:
            fh.write("https://me:from-ticket@cpbee.cmnet:5232\nhttps://me:other-host@cpvax.cmnet\n")  # allowlist secret
        old_home = os.environ.get("HOME", "")
        self.addCleanup(os.environ.__setitem__, "HOME", old_home)
        os.environ["TVMAIL_MU_TICKETS"] = tix
        os.environ["HOME"] = os.path.join(self.tmp, "nohome")
        os.environ.pop("TVCAL_PASS", None)
        self.addCleanup(os.environ.pop, "TVMAIL_MU_TICKETS", None)
        r = tb.Remote("https://cpbee:5232/me/cal/", "me")
        self.assertEqual(r.password(), "from-ticket")                      # bare in the url, qualified in the ticket
        self.assertEqual(tb.Remote("https://cpbee.cmnet:5232/me/cal/", "me").password(), "from-ticket")
        self.assertEqual(tb.Remote("https://cpbee:5232/me/cal/", "someone").password(), "")
        self.assertEqual(tb.Remote("https://cpbee.other:5232/me/cal/", "me").password(), "")

    def test_settings_say_remote(self):
        rows = {r[0]: r[1] for r in self.lines("settings")}
        self.assertEqual(rows["mode"], "remote")
        self.assertEqual(rows["file"], self.dav.url)


class Floyd(Env):
    """Floyd on the calendar: the `floyd` verb, and tvfloyd's CalNotes - which is what tvmail's and
    tvchat's Floyd use for their owner - working on the same list and calendar."""

    def say(self, text):
        return self.run_be("floyd", text).stdout.strip()

    def test_todo_is_the_calendars_list(self):
        self.assertIn("Added as number 1", self.say("!todo add Buy milk"))
        self.assertIn("Added as number 2", self.say("!todo Call Bob"))
        self.assertEqual(self.say("!todo"), "1. Buy milk; 2. Call Bob")
        self.assertEqual([t[4] for t in self.lines("todos")], ["Buy milk", "Call Bob"])     # tvcal's list
        self.assertIn("Done: Buy milk", self.say("!todo done 1"))
        self.assertEqual(self.say("!todo"), "1. Call Bob")
        self.assertIn("which number", self.say("!todo done 9"))
        self.assertIn("Removed: Call Bob", self.say("!todo rm 1"))
        self.assertIn("Nothing to do", self.say("!todo"))

    def test_notes_are_the_calendars_notes(self):
        self.assertIn("No notes", self.say("!notes"))
        self.assertIn("Noted as number 1", self.say("!note Ring the plumber"))
        self.assertIn("Noted as number 2", self.say("!note Buy a ladder"))
        self.assertEqual({n[2] for n in self.lines("notes")}, {"Ring the plumber", "Buy a ladder"})       # tvcal's list
        self.assertIn("1. ", self.say("!notes"))
        self.assertIn("Ring the plumber", self.say("!notes 1") + self.say("!notes 2"))
        self.assertIn("which number", self.say("!notes 9"))
        self.assertIn("will not wipe", self.say("!notes clear"))
        self.assertEqual(len(self.lines("notes")), 2)

    def test_an_old_notes_file_moves_over(self):
        import json
        state = os.path.join(self.tmp, "state")
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(state, "notes.json"), "w") as fh:
            json.dump({"todo": [], "notes": ["from the old file"]}, fh)
        self.assertIn("from the old file", self.say("!notes"))
        self.assertEqual(self.lines("notes")[0][2], "from the old file")
        with open(os.path.join(state, "notes.json")) as fh:
            self.assertEqual(json.load(fh)["notes"], [])
        self.assertEqual(len(self.lines("notes")), 1)                  # not again on the next call
        self.say("!notes")
        self.assertEqual(len(self.lines("notes")), 1)

    def test_event_and_cal(self):
        self.assertIn("Dentist", self.say("!event 2026-11-05 14:00-15:00 Dentist"))
        r = self.lines("range", "2026-11-05", "2026-11-05")
        self.assertEqual((r[0][1], r[0][2], r[0][4]), ("14:00", "15:00", "Dentist"))
        self.assertIn("14:00 Dentist", self.say("!cal 2026-11-05"))
        self.assertIn("Nothing on", self.say("!cal 2026-12-24"))
        self.run_be("add-event", "--date", "today", "Standup")
        self.assertIn("Standup", self.say("!cal week"))
        self.assertIn("needs a title", self.say("!event tomorrow"))

    def test_the_tools_still_work(self):
        self.assertIn("84", self.say("!calc 12*7"))
        self.assertIn("Floyd", self.say("help") + "Floyd")

    def test_his_own_commands_and_memory(self):
        self.env["XDG_DATA_HOME"] = os.path.join(self.tmp, "xdg")          # his memory file, not the real one
        self.assertIn("B-19-7", self.say("!about"))
        self.assertIn("learned", self.say("!learn tea is hot"))
        self.assertIn("tea is hot", self.say("!tea"))                       # remembered between runs
        for irc in ("!seen bob", "!tell bob hi", "bob++", "!karma"):        # channel things are not offered here
            self.assertIn("Say help", self.say(irc), irc)
        self.assertIn("Say help", self.say("!nosuchthing"))
        self.assertIn("Repair Room", self.say("look"))                      # the parser game (chatty by default)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "xdg", "tvfloyd", "memory.json")))

    def test_status(self):
        self.env["XDG_DATA_HOME"] = os.path.join(self.tmp, "xdg")
        out = self.say("!status")
        for word in ("Floyd in tvcal is at level", "Claude:", "Fortunes:", "Calendar: a local file"):
            self.assertIn(word, out)

    def _master(self):
        """A master Floyd's gate on a free local port, with its own memory; [floyd] server/token point at it."""
        import selectors
        import threading
        sel = selectors.DefaultSelector()
        stop = []

        class Loop(object):
            def add_reader(s, sock, fn):
                sel.register(sock, selectors.EVENT_READ, fn)

            def remove_reader(s, sock):
                stop.append(1)

        def pump():
            while not stop:
                for key, _ in sel.select(0.05):
                    key.data()
        tvf = tb._load_tvfloyd()
        mem = tvf.FloydMemory(os.path.join(self.tmp, "master.json"))
        gate = tvf.FloydGate(Loop(), mem, "tok", tvf.claude_settings(lambda k, d: "no" if k == "claude" else d),
                                "127.0.0.1", 0)
        gate.start()
        threading.Thread(target=pump, daemon=True).start()
        self.addCleanup(gate.stop)
        return gate, mem

    def test_the_master_answers_and_the_local_floyd_is_the_fallback(self):
        self.env["XDG_DATA_HOME"] = os.path.join(self.tmp, "xdg")          # the LOCAL memory: empty
        gate, mem = self._master()
        mem.data["facts"]["tea"] = "master tea is hot"
        port = gate.sock.getsockname()[1]
        conf = self.env["TVCAL_CONF"]
        open(conf, "w").write("[floyd]\nserver = 127.0.0.1:%d\ntoken = tok\n" % port)
        self.assertIn("master tea is hot", self.say("!tea"))               # only the master knows it
        st = self.say("!status")
        self.assertIn("answers", st)
        self.assertIn("Fortunes: the master's ", st)                        # his fortunes come from the master too
        self.assertIn("answers (0 quotes, 1 facts)", st)                    # the master's numbers ...
        self.assertIn("He remembers 0 quotes and 1 facts (the master's", st)   # ... in both places, not the local file's
        open(conf, "w").write("[floyd]\nserver = 127.0.0.1:%d\ntoken = wrong\n" % port)
        out = self.say("!status")
        self.assertIn("refused", out)                                      # wrong token: local Floyd, and it says so
        self.assertNotIn("master tea", self.say("!tea"))
        gate.stop()
        open(conf, "w").write("[floyd]\nserver = 127.0.0.1:%d\ntoken = tok\nserver_timeout = 0.5\n" % port)
        self.assertIn("not reachable", self.say("!status"))                # cpbee gone: still answers
        self.assertIn("B-19-7", self.say("!about"))

    def _fake_claude(self, body="the fake answer"):
        path = os.path.join(self.tmp, "claude")
        open(path, "w").write('#!/bin/sh\ncat > "%s/asked"\necho "%s"\n' % (self.tmp, body))
        os.chmod(path, 0o755)
        open(self.env["TVCAL_CONF"], "w").write("[floyd]\nclaude_cmd = %s\n" % path)
        self.env["XDG_STATE_HOME"] = os.path.join(self.tmp, "xdg-state")

    def test_claude_is_asked_in_the_background_and_polled(self):
        self._fake_claude()
        self.assertIn("will ask Claude", self.say("!claude what is 2+2?"))
        answer = ""
        for _ in range(50):
            answer = self.run_be("floyd-poll").stdout.strip()
            if answer:
                break
            time.sleep(0.2)
        self.assertEqual(answer, "Claude: the fake answer")
        self.assertEqual(open(os.path.join(self.tmp, "asked")).read(), "what is 2+2?")      # on stdin, not argv
        self.assertEqual(self.run_be("floyd-poll").stdout.strip(), "")                      # said once

    def test_claude_when_it_is_off_or_missing(self):
        open(self.env["TVCAL_CONF"], "w").write("[floyd]\nclaude = no\n")
        self.assertIn("asleep", self.say("!claude hi"))
        open(self.env["TVCAL_CONF"], "w").write("[floyd]\nclaude_cmd = /nonexistent/claude\n")
        self.assertIn("not found", self.say("!claude hi"))
        self.assertIn("Claude: no - the program", self.say("!status"))

    def test_his_voice_is_quiet_at_minimal(self):
        self.env["XDG_DATA_HOME"] = os.path.join(self.tmp, "xdg")
        self.env["TVCAL_FLOYD_LEVEL"] = "minimal"
        self.assertIn("B-19-7", self.say("!about"))                         # !commands still answer
        self.assertIn("Floyd doesn't know", self.say("hello"))              # no banter

    def test_off(self):
        open(self.env["TVCAL_CONF"], "w").write("[floyd]\nlevel = off\n")
        self.assertIn("Floyd is off", self.say("!todo"))

    def test_calnotes_only_for_the_owner(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("tvfloyd", os.path.join(HERE, "..", "..", "tvcommon", "py", "tvfloyd.py"))
        tvf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tvf)
        plain = tvf.make_notes(None, calendar=False)
        self.assertNotIsInstance(plain, tvf.CalNotes)
        old = os.environ.get("PATH", "")
        os.environ["PATH"] = os.path.dirname(os.path.abspath(BACKEND)) + os.pathsep + old
        self.addCleanup(os.environ.__setitem__, "PATH", old)
        owner = tvf.make_notes(None, calendar=True)
        self.assertIsInstance(owner, tvf.CalNotes)
        self.assertIn("Added", tvf.run_tool("todo", "add Write tests", owner))
        self.assertEqual(self.lines("todos")[0][4], "Write tests")
        self.assertIn("Added", tvf.run_tool("todo", "add Mine only", plain))     # a stranger's list is JSON
        self.assertEqual(len(self.lines("todos")), 1)
        self.assertIsNone(tvf.run_tool("cal", "", plain))                       # and has no calendar


if __name__ == "__main__":
    unittest.main()
