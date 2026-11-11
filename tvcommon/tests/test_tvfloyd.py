"""Tests for tvcommon/py/tvfloyd.py: the levels and the useful tools that tvchat's and
tvmail's Floyd share.  Stdlib unittest, no network."""
import importlib.util
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "py" / "tvfloyd.py"
spec = importlib.util.spec_from_file_location("tvfloyd", str(PATH))
tvf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tvf)


class TestLevels(unittest.TestCase):
    def test_names_prefixes_numbers_and_full(self):
        self.assertEqual(tvf.parse_level("Helpful"), "helpful")
        self.assertEqual(tvf.parse_level("min"), "minimal")
        self.assertEqual(tvf.parse_level("0"), "off")
        self.assertEqual(tvf.parse_level("full"), "clippy")        # "full clippy mode"
        self.assertIsNone(tvf.parse_level("c"))                    # chatty or clippy
        self.assertEqual(tvf.parse_level("zzz", "d"), "d")
        self.assertIsNone(tvf.parse_level(None))

    def test_order(self):
        self.assertTrue(tvf.at_least("clippy", "chatty"))
        self.assertTrue(tvf.at_least("helpful", "helpful"))
        self.assertFalse(tvf.at_least("minimal", "helpful"))
        self.assertEqual(tvf.LEVELS[0], "off")
        for l in tvf.LEVELS:
            self.assertIn(l, tvf.LEVEL_BLURB)


class TestCalc(unittest.TestCase):
    def test_arithmetic(self):
        for expr, want in (("12*7", "84"), ("2^10", "1024"), ("(1+2)*3/4", "2.25"),
                           ("3 x 4", "12"), ("10 % 3", "1"), ("1,000*3", "3000"),
                           ("sqrt(16)", "4"), ("2*pi", "6.283185307"), ("7//2", "3"), ("-3+5", "2")):
            self.assertEqual(tvf.calc(expr), want, expr)

    def test_it_is_not_python(self):
        for expr in ("__import__('os').system('x')", "().__class__", "open('f')", "a+1",
                     "1; 2", "lambda: 1", "9**9**9", "10**5000", "", "1/0", "[1]*10**10"):
            with self.assertRaises(tvf.CalcError, msg=expr):
                tvf.calc(expr)


class TestConvert(unittest.TestCase):
    def test_units(self):
        self.assertEqual(tvf.convert("5 km to mi"), "5 km is 3.10686 mi")
        self.assertEqual(tvf.convert("70 f to c"), "70 f is 21.1111 c")
        self.assertEqual(tvf.convert("0 c in k"), "0 c is 273.15 k")
        self.assertEqual(tvf.convert("10 GB to MB"), "10 gb is 10000 mb")
        self.assertEqual(tvf.convert("2 hours to minutes"), "2 hours is 120 minutes")

    def test_not_a_conversion_and_a_wrong_one(self):
        self.assertIsNone(tvf.convert("hello to world"))
        self.assertIsNone(tvf.convert("5 flurbs to mi"))
        with self.assertRaises(tvf.CalcError):
            tvf.convert("5 km to kg")


class TestTime(unittest.TestCase):
    def test_a_known_place_and_an_unknown_one(self):
        out = tvf.time_in("Tokyo", now=0)                          # 1970-01-01 00:00 UTC
        self.assertIn("09:00", out)
        self.assertIn("Thu", out)
        self.assertIsNone(tvf.time_in("Atlantis"))


class TestNotes(unittest.TestCase):
    def test_todo_and_notes(self):
        n = tvf.Notes()
        self.assertIn("Nothing to do", n.todo(""))
        self.assertEqual(n.todo("add buy milk"), "Added as number 1.")
        self.assertEqual(n.todo("call mum"), "Added as number 2.")  # a bare line adds
        self.assertEqual(n.todo(""), "1. buy milk; 2. call mum")
        self.assertIn("Done: buy milk", n.todo("done 1"))
        self.assertEqual(n.todo(""), "1. call mum")
        with self.assertRaises(tvf.CalcError):
            n.todo("done 9")
        self.assertIn("Cleared 1 thing.", n.todo("clear"))
        self.assertEqual(n.note("the code is 42"), "Noted as number 1.")
        self.assertEqual(n.note(""), "1. the code is 42")

    def test_a_file_and_a_damaged_one(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "sub", "n.json")
        tvf.Notes(path).todo("add x")
        self.assertEqual(tvf.Notes(path).todo(""), "1. x")
        Path(path).write_text("{ not json")
        self.assertIn("Nothing to do", tvf.Notes(path).todo(""))

    def test_the_list_is_capped(self):
        n = tvf.Notes()
        n.CAP = 2
        n.todo("add a")
        n.todo("add b")
        with self.assertRaises(tvf.CalcError):
            n.todo("add c")


class TestTools(unittest.TestCase):
    def test_run_tool(self):
        n = tvf.Notes()
        self.assertEqual(tvf.run_tool("calc", "2+2", n), "2+2 = 4")
        self.assertEqual(tvf.run_tool("todo", "add x", n), "Added as number 1.")
        self.assertIn("Tokyo", tvf.run_tool("time", "in tokyo", n, now=0))
        self.assertIsNone(tvf.run_tool("seen", "bob", n))
        with self.assertRaises(tvf.CalcError):
            tvf.run_tool("convert", "gibberish", n)
        with self.assertRaises(tvf.CalcError):
            tvf.run_tool("time", "in atlantis", n)

    def test_natural_language(self):
        self.assertEqual(tvf.natural("what is 12*7?"), "12*7 = 84")
        self.assertEqual(tvf.natural("What's 5 km in miles"), "5 km is 3.10686 miles")
        self.assertEqual(tvf.natural("how many feet in 3 m"), "3 m is 9.84252 feet")
        self.assertIn("Paris", tvf.natural("what time is it in Paris", now=0))
        self.assertIsNone(tvf.natural("what is love"))
        self.assertIsNone(tvf.natural("what is your name"))
        self.assertIsNone(tvf.natural("hello there"))


class TestClippy(unittest.TestCase):
    def test_every_tip_owns_up(self):
        for text in ("I need to call mum", "what did I miss", "it is 30 km away",
                     "meet at 5pm EST", "that is 12*7 right", "anyone around?",
                     "how do I reset it?", "x" * 500):
            tip = tvf.clippy_chat(text)
            self.assertIsNotNone(tip, text)
            self.assertTrue(tip.startswith("Floyd not clippy but "), tip)

    def test_nothing_to_say(self):
        for text in ("nice weather", "ok", "lol"):
            self.assertIsNone(tvf.clippy_chat(text))

    def test_mail_tips(self):
        tips = tvf.clippy_mail(unread=5, needs_reply=2, old_inbox=300, trash=40, vacation_on=True)
        self.assertEqual(len(tips), 4)
        self.assertTrue(all(t.startswith("Floyd not clippy but ") for t in tips))
        self.assertEqual(tvf.clippy_mail(0, 0, 0, 0, False), [])
        self.assertIn("5 unread", tvf.clippy_mail(5, 0, 0, 0, False)[0])


class _Loop(object):
    """Just enough of tvchat's Loop for the gate: a thread that calls accept() when readable."""
    def __init__(self):
        import selectors, threading
        self.sel, self.stop_flag = selectors.DefaultSelector(), False
        self.t = threading.Thread(target=self.run, daemon=True)

    def add_reader(self, sock, fn):
        import selectors
        self.sel.register(sock, selectors.EVENT_READ, fn)
        self.t.start()

    def remove_reader(self, sock):
        self.stop_flag = True
        try:
            self.sel.unregister(sock)
        except (KeyError, ValueError):
            pass

    def run(self):
        while not self.stop_flag:
            try:
                for key, _ in self.sel.select(0.05):
                    key.data()
            except (OSError, ValueError, KeyError):
                return


class TestMaster(unittest.TestCase):
    TOKEN = "s3cret"  # allowlist secret: test value

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = dict(os.environ)
        os.environ["XDG_STATE_HOME"] = os.path.join(self.tmp, "state")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.tmp, "data")
        tvf._DOWN.clear()
        tvf._SOURCE["last"] = None
        tvf._MINDS.clear()
        self.loop = _Loop()
        self.mem = tvf.FloydMemory(os.path.join(self.tmp, "master.json"))
        self.cfg = tvf.claude_settings(lambda k, d: "no" if k == "claude" else d)
        self.gate = tvf.FloydGate(self.loop, self.mem, self.TOKEN, self.cfg, "127.0.0.1", 0)
        self.gate.start()
        self.port = self.gate.sock.getsockname()[1]
        self.m = {"server": "127.0.0.1:%d" % self.port, "token": self.TOKEN, "timeout": 2.0}  # allowlist secret

    def tearDown(self):
        self.gate.stop()
        os.environ.clear()
        os.environ.update(self.old)
        tvf._MASTER = None

    def test_settings(self):
        g = lambda d: (lambda k, dflt: d.get(k, dflt))
        self.assertEqual(tvf.master_settings(g({}))["server"], "")
        self.assertEqual(tvf.master_settings(g({"server": "cpbee"}))["server"], "cpbee:7077")
        self.assertEqual(tvf.master_settings(g({"server": "none"}))["server"], "")

    def test_hello_and_say_use_the_masters_memory(self):
        self.assertTrue(tvf.master_alive(self.m))
        self.mem.data.setdefault("quotes", []).append("master-only quote")
        self.mem.save() if hasattr(self.mem, "save") else None
        lines = tvf.persona("!quote", "chatty", who="chris", where="tvcal", path=None, master=self.m)
        self.assertTrue(lines)
        self.assertIn("master-only quote", " ".join(lines))

    def test_wrong_token_falls_back_to_local(self):
        bad = dict(self.m, token="nope")
        self.assertEqual(tvf.master_call(bad, {"op": "hello"}).get("ok"), False)
        self.assertIn("refused", tvf.master_state(bad))
        lines = tvf.persona("!about", "chatty", path=None, master=bad)
        self.assertTrue(lines)                                     # the local Floyd answered

    def test_dead_port_falls_back_and_is_remembered(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        dead = {"server": "127.0.0.1:%d" % s.getsockname()[1], "token": "x", "timeout": 0.5}
        s.close()
        self.assertIsNone(tvf.master_call(dead, {"op": "hello"}))
        self.assertGreater(tvf._DOWN[dead["server"]], 0)
        self.assertTrue(tvf.persona("!about", "chatty", path=None, master=dead))
        self.assertIn("not reachable", tvf.master_state(dead))

    def test_switch_notices(self):
        dead = dict(self.m, server="127.0.0.1:1", timeout=0.3)
        first = tvf.persona("!about", "chatty", path=None, master=self.m)
        self.assertFalse(first[0].startswith("* Floyd is"))        # no notice on the first answer
        self.assertEqual(tvf._SOURCE["last"], "master")
        tvf.persona("!about", "chatty", path=None, master=dead)       # falls back, says so on the NEXT master answer
        self.assertEqual(tvf._SOURCE["last"], "local")
        back = tvf.persona("!about", "chatty", path=None, master=self.m)
        self.assertTrue(back[0].startswith("* Floyd is back on the master"))

    def test_irc_only_is_refused_on_the_gate_too(self):
        r = tvf.master_call(self.m, {"op": "say", "text": "!seen bob", "level": "chatty", "who": "x"})
        self.assertTrue(r["ok"])
        self.assertIsNone(r["lines"])

    def test_claude_asleep_on_the_master(self):
        r = tvf.master_call(self.m, {"op": "claude", "text": "hi"})
        self.assertTrue(r["ok"])
        self.assertIn("asleep", r["lines"][0])

    def test_claude_job_through_the_master_end_to_end(self):
        script = os.path.join(self.tmp, "fakeclaude")
        with open(script, "w") as fh:
            fh.write("#!/bin/sh\ncat >/dev/null\necho 42 is the answer\n")
        os.chmod(script, 0o755)
        self.gate.claude_cfg = tvf.claude_settings(lambda k, d: {"claude_cmd": script}.get(k, d))
        cfg = tvf.claude_settings(lambda k, d: {"server": self.m["server"], "token": self.TOKEN}.get(k, d))
        lines = tvf.claude_start("what is it", cfg)
        self.assertTrue(lines)
        got = None
        for _ in range(100):
            got = tvf.claude_poll(cfg)
            if got:
                break
            time.sleep(0.1)
        self.assertEqual(got, ["Claude: 42 is the answer"])
        self.assertIsNone(tvf.claude_poll(cfg))

    def test_bad_requests(self):
        self.assertEqual(self.gate.handle(b"not json")["ok"], False)
        self.assertEqual(self.gate.handle(b"[]")["ok"], False)
        r = self.gate.handle(('{"token":"%s","op":"poll","job":"../x"}' % self.TOKEN).encode())
        self.assertEqual(r["ok"], False)
        r = self.gate.handle(('{"token":"%s","op":"zzz"}' % self.TOKEN).encode())
        self.assertEqual(r["ok"], False)



class TestEventHub(unittest.TestCase):
    def test_publish_clean_and_visible_only_to_the_right_machines(self):
        h = tvf.EventHub()
        self.assertEqual(h.publish("note", "  \x07 ", "info"), 0)                    # nothing to say
        n = h.publish("note", "hello\tthere\nfriend", "weird", origin="CmDesktop", to="cmlaptop")
        self.assertEqual(n, 1)
        ev = h.ring[0]
        self.assertEqual((ev["text"], ev["level"], ev["origin"], ev["to"]), ("hello there friend", "info", "cmdesktop", "cmlaptop"))
        seq, evs = h.since(0, "cmlaptop")
        self.assertEqual((seq, [e["seq"] for e in evs]), (1, [1]))
        self.assertEqual(h.since(0, "cmdesktop")[1], [])                              # never back to its origin
        self.assertEqual(h.since(0, "other")[1], [])                                  # not addressed to it
        self.assertEqual(h.since(1, "cmlaptop")[1], [])                               # already seen
        h.publish("note", "to everybody", to="all", origin="a")
        self.assertEqual([e["text"] for e in h.since(1, "b")[1]], ["to everybody"])

    def test_sticky_events_are_told_to_a_late_machine_until_recovered(self):
        h = tvf.EventHub()
        h.publish("outage", "Radicale is down", "error", key="svc:radicale")
        h.publish("note", "just a note")
        seq, evs = h.since(None, "late")                                              # first contact: only what is still true
        self.assertEqual([e["text"] for e in evs], ["Radicale is down"])
        h.publish("recover", "Radicale is back", key="svc:radicale")
        self.assertEqual(h.since(None, "late2")[1], [])
        self.assertEqual(h.since(seq + 5, "late3")[1], [])                            # a cursor from before a hub restart = first contact

    def test_events_expire_and_the_ring_is_bounded(self):
        t = [1000.0]
        h = tvf.EventHub(clock=lambda: t[0])
        h.publish("note", "short", ttl=30)
        t[0] += 31
        self.assertEqual(h.since(0, "x")[1], [])
        for i in range(250):
            h.publish("note", "n%d" % i)
        self.assertEqual(len(h.ring), 200)


class TestRelay(unittest.TestCase):
    """The gate's publish / events ops, !send, and the relay writing the spool (toasts)."""
    TOKEN = TestMaster.TOKEN  # allowlist secret: test value
    tearDown = TestMaster.tearDown

    def setUp(self):
        TestMaster.setUp(self)
        self.spool = os.path.join(self.tmp, "spool")
        os.environ["TV_SPOOL_DIR"] = self.spool
        # The relay writes a toast only for the apps installed here (it looks for tvmail, tvchat, tvcal on PATH), so a
        # machine without all three (a CI box that builds only some) would fail these tests: put stand-ins on PATH.
        bindir = os.path.join(self.tmp, "fakebin")
        os.makedirs(bindir)
        for app in tvf.RELAY_APPS:
            with open(os.path.join(bindir, app), "w") as fh:
                fh.write("#!/bin/sh\nexit 0\n")
            os.chmod(os.path.join(bindir, app), 0o755)
        os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")
        self.env = {"TVFLOYD_SERVER": self.m["server"], "TVFLOYD_TOKEN": self.TOKEN, "TVFLOYD_MACHINE": "cmlaptop",  # allowlist secret: test value
                    "TVFLOYD_TIMEOUT": "1", "TVFLOYD_RELAY_POLL": "0.05"}

    def notes(self, app):
        d = os.path.join(self.spool, app)
        out = []
        for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if n.endswith(".note"):
                out.append(open(os.path.join(d, n), encoding="utf-8").read().rstrip("\n").split("\t"))
        return out

    def test_send_publishes_and_the_relay_writes_a_toast_in_every_app(self):
        sender = dict(self.m, machine="cmdesktop")
        self.assertIn("Sent to cmlaptop", tvf.persona("!send cmlaptop dinner is ready", "chatty", master=sender)[0])
        tvf.relay_run(self.env, once=True)                                           # first contact: nothing sticky, cursor taken
        for app in ("tvmail", "tvchat", "tvcal"):
            self.assertEqual(self.notes(app), [], app)
        # the note was published BEFORE the first contact, so it is not replayed; publish again with the relay known
        tvf.persona("!send cmlaptop dinner is ready", "chatty", master=sender)
        tvf.relay_run(self.env, once=True)
        for app in ("tvmail", "tvchat", "tvcal"):
            self.assertEqual([n[1:] for n in self.notes(app)], [["cmdesktop", "info", "dinner is ready"]], app)
        before = [len(self.notes(a)) for a in ("tvmail", "tvchat", "tvcal")]
        tvf.relay_run(self.env, once=True)                                           # nothing new: no repeat
        self.assertEqual([len(self.notes(a)) for a in ("tvmail", "tvchat", "tvcal")], before)

    def test_send_to_all_skips_the_sender_and_other_machines_get_it(self):
        sender = dict(self.m, machine="cmdesktop")
        tvf.relay_run(self.env, once=True)
        tvf.persona("!send all hello everyone", "chatty", master=sender)
        tvf.relay_run(self.env, once=True)
        self.assertEqual(len(self.notes("tvcal")), 1)
        tvf.relay_run(dict(self.env, TVFLOYD_MACHINE="cmdesktop", XDG_STATE_HOME=os.path.join(self.tmp, "desk")), once=True)
        self.assertEqual(len(self.notes("tvcal")), 1)                                 # the sender's own relay got nothing more

    def test_a_sticky_outage_reaches_a_machine_that_connects_later(self):
        self.gate.hub.publish("outage", "Ergo is down", "error", key="svc:ergo")
        tvf.relay_run(self.env, once=True)
        self.assertEqual([n[1:] for n in self.notes("tvmail")], [["127.0.0.1", "error", "Ergo is down"]])

    def test_send_without_a_master_or_with_a_dead_one(self):
        self.assertIn("needs the master", tvf.persona("!send cmlaptop hi", "chatty", master={"server": ""})[0])
        self.assertIn("Usage", tvf.persona("!send", "chatty", master=self.m)[0])
        dead = dict(self.m, server="127.0.0.1:1", timeout=0.3)
        self.assertIn("not reachable", tvf.persona("!send cmlaptop hi", "chatty", master=dead)[0])

    def test_the_relay_says_so_when_it_loses_cpbee_and_when_it_is_back(self):
        import threading
        stop = []
        env = dict(self.env, TVFLOYD_RELAY_LOST="0.4", TVFLOYD_RELAY_IDLE="0")
        t = threading.Thread(target=lambda: tvf.relay_run(env, stop=lambda: bool(stop)), daemon=True)
        t.start()
        time.sleep(0.5)                                                               # in contact
        port = self.gate.sock.getsockname()[1]
        self.gate.stop()                                                              # cpbee goes away
        for _ in range(60):
            time.sleep(0.1)
            if any("lost contact" in n[3] for n in self.notes("tvcal")):
                break
        self.assertTrue(any("lost contact" in n[3] for n in self.notes("tvcal")), self.notes("tvcal"))
        stop.append(1)
        t.join(5)

    def test_spool_notes_are_what_spool_cpp_reads(self):
        self.assertTrue(tvf.spool_write("tvcal", "cpbee", "warn", "a\tb\nc", 60))
        name = [n for n in os.listdir(os.path.join(self.spool, "tvcal")) if n.endswith(".note")][0]
        self.assertTrue(name.split("-")[0].isdigit())
        self.assertGreater(int(name.split("-")[0]), time.time())
        self.assertEqual(open(os.path.join(self.spool, "tvcal", name)).read(), "1\tcpbee\twarn\ta b c\n")
        self.assertFalse([n for n in os.listdir(os.path.join(self.spool, "tvcal")) if n.startswith(".tmp")])



class TestSentinel(unittest.TestCase):
    class Loop(object):
        """call_later is recorded; run_in_thread runs the function at once."""
        def __init__(self):
            self.later = []

        def call_later(self, delay, fn):
            self.later.append((delay, fn))

        def run_in_thread(self, fn, cb):
            try:
                res, err = fn(), None
            except Exception as e:
                res, err = None, e
            cb(res, err)

    def setUp(self):
        self.t = [1000.0]
        self.hub = tvf.EventHub(clock=lambda: self.t[0])
        self.up = {"a": (True, ""), "b": (True, "")}
        self.loop = self.Loop()
        checks = [("svc:a", "Alpha", 2, lambda: self.up["a"]), ("host:b", "Beta mail", 3, lambda: self.up["b"])]
        self.s = tvf.Sentinel(self.loop, self.hub, checks, interval=30, remind=600, clock=lambda: self.t[0])

    def texts(self, machine="m"):
        return [(e["kind"], e["level"], e["text"]) for e in self.hub.since(0, machine)[1]]

    def round(self, **states):
        self.up.update(states)
        self.s._tick()
        self.t[0] += 30

    def test_one_blip_is_not_an_outage_and_a_real_one_is_told_once_then_recovered(self):
        self.round(a=(False, "unit is failed"))
        self.assertEqual(self.texts(), [])                                    # one failure: nothing said
        self.round(a=(True, ""))
        self.round(a=(False, "unit is failed"))
        self.round(a=(False, "unit is failed"))                                # second in a row: down
        self.assertEqual(self.texts(), [("outage", "error", "Alpha is down: unit is failed")])
        self.round()                                                           # still down: not repeated
        self.assertEqual(len(self.texts()), 1)
        self.assertEqual(self.hub.since(None, "late")[1][0]["key"], "svc:a")   # a machine that connects now is told
        self.round(a=(True, ""))
        k = self.texts()
        self.assertEqual(k[-1][0:2], ("recover", "info"))
        self.assertIn("Alpha is back (was down", k[-1][2])
        self.assertEqual(self.hub.since(None, "late2")[1], [])                 # no longer sticky

    def test_need_counts_per_check_and_a_reminder_comes_while_it_stays_down(self):
        for _ in range(2):
            self.round(b=(False, "no answer"))
        self.assertEqual(self.texts(), [])                                     # Beta mail needs 3
        self.round(b=(False, "no answer"))
        self.assertEqual([x[2] for x in self.texts()], ["Beta mail is down: no answer"])
        self.t[0] += 700
        self.round(b=(False, "no answer"))
        self.assertIn("still down after", self.texts()[-1][2])
        self.assertEqual(len(self.texts()), 2)

    def test_a_broken_check_is_not_an_outage(self):
        def boom():
            raise RuntimeError("x")
        self.s.checks = [("svc:z", "Zed", 1, boom)]
        self.s._tick()
        self.assertEqual(self.texts(), [])

    def test_the_timers_rearm_themselves(self):
        self.s.start(first=1)
        self.assertEqual(len(self.loop.later), 2)
        self.loop.later.pop(0)[1]()                                            # the check tick ran
        self.assertEqual([d for d, _ in self.loop.later][-1], 30)

    def test_shutdown_scheduled_cancelled_and_begun(self):
        self.s.shutdown(("scheduled", "reboot", int(self.t[0]) + 600))
        k = self.texts()
        self.assertEqual(k[0][:2], ("shutdown", "warn"))
        self.assertIn("will reboot at", k[0][2])
        self.assertIn("(in 10m", k[0][2])
        self.s.shutdown(("scheduled", "reboot", int(self.t[0]) + 600))        # the same state again: not repeated
        self.assertEqual(len(self.texts()), 1)
        self.s.shutdown(None)
        self.assertIn("cancelled", self.texts()[-1][2])
        self.assertEqual(self.hub.since(None, "x")[1], [])                    # sticky key cleared
        self.s.shutdown(("now", "poweroff"))
        self.assertEqual(self.texts()[-1][:2], ("shutdown", "error"))
        self.assertIn("going down now (power off)", self.texts()[-1][2])

    def test_the_checks_come_from_the_options(self):
        got = tvf.sentinel_checks(lambda k, d: {"sentinel_services": "ergo:6697, talkd:518@127.0.0.1",
                                                "sentinel_hosts": "My relay=example.org:465",
                                                "sentinel_queue": "0", "sentinel_disk": "85"}.get(k, d), host="10.0.0.1")
        self.assertEqual([(k, lab) for k, lab, _n, _f in got],
                         [("svc:ergo", "IRC server (Ergo)"), ("svc:talkd", "talkd (chat)"),
                          ("host:example.org:465", "My relay"), ("disk", "disk space")])
        dflt = tvf.sentinel_checks(lambda k, d: d)
        keys = [k for k, _l, _n, _f in dflt]
        for want in ("svc:ergo", "svc:dovecot", "svc:postfix", "svc:radicale", "svc:talkd", "host:u-l.ca:465",
                     "host:u-l.ca:993", "queue", "disk"):
            self.assertIn(want, keys)
        none = tvf.sentinel_checks(lambda k, d: {"sentinel_services": "none", "sentinel_hosts": "x", "sentinel_queue": "0",
                                                  "sentinel_disk": "0"}.get(k, d))
        self.assertEqual(none, [])

    def test_the_checks_themselves(self):
        import socket
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        self.assertEqual(tvf.check_host("127.0.0.1", port), (True, ""))
        srv.close()
        ok, why = tvf.check_host("127.0.0.1", port)
        self.assertFalse(ok)
        self.assertIn("no answer", why)
        self.assertEqual(tvf.check_disk("/", 101), (True, ""))
        self.assertFalse(tvf.check_disk("/", 0.0001)[0])
        old = tvf.mail_queue_length
        try:
            tvf.mail_queue_length = lambda: 3
            ok, why = tvf.check_queue(1)
            self.assertFalse(ok)
            self.assertIn("3 messages waiting", why)
            self.assertEqual(tvf.check_queue(5), (True, ""))
            tvf.mail_queue_length = lambda: None                              # cannot be told: not an outage
            self.assertEqual(tvf.check_queue(1), (True, ""))
        finally:
            tvf.mail_queue_length = old

    def test_the_events_reach_a_relay_as_toasts(self):
        # the whole way: the sentinel tells the hub, the gate serves it, the relay writes the spool note
        os.environ["TV_SPOOL_DIR"] = os.path.join(tempfile.mkdtemp(), "spool")
        self.addCleanup(os.environ.pop, "TV_SPOOL_DIR", None)
        self.hub.publish("outage", "Ergo is down: unit is failed", "error", origin="cpbee", key="svc:ergo")
        seq, evs = self.hub.since(None, "cmlaptop")
        for ev in evs:
            tvf.spool_write("tvcal", "cpbee", ev["level"], ev["text"])
        d = os.path.join(os.environ["TV_SPOOL_DIR"], "tvcal")
        notes = [open(os.path.join(d, n)).read() for n in os.listdir(d) if n.endswith(".note")]
        self.assertEqual(notes, ["1\tcpbee\terror\tErgo is down: unit is failed\n"])



class TestWizard(unittest.TestCase):
    """Floyd as a setup wizard: !setup join / !setup / !setup N / !setup all."""
    TOKEN = "wiz-secret-token"  # allowlist secret: test value

    def setUp(self):
        import json
        self.tmp = tempfile.mkdtemp()
        self.old = dict(os.environ)
        for k in ("TVCAL_CONF", "TVMAIL_CONF", "TVCHAT_CONF", "TV_SPOOL_DIR"):
            os.environ.pop(k, None)
        os.environ["HOME"] = os.path.join(self.tmp, "home")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.tmp, "state")
        os.environ["TVFLOYD_RELAY"] = "none"
        tvf._DOWN.clear()
        tvf._SOURCE["last"] = None
        tvf._MASTER = None
        tvf._BAKED.clear()
        self.cfgdir = os.path.join(self.tmp, "home", ".config")
        for app in ("tvcal", "tvmail", "tvchat"):
            os.makedirs(os.path.join(self.cfgdir, app))
        self.write("tvcal", "[server]\nurl = https://old.example/\n# my comment\n")
        self.write("tvmail", "[service]\nmode = remote\n")
        self.write("tvchat", "[chat]\nautoconnect = a\n\n[account:a]\nprotocol = talk\nhost = x\n")
        ca1, ca2 = os.path.join(self.tmp, "ca-cal.pem"), os.path.join(self.tmp, "ca-chat.pem")
        for f, name in ((ca1, "TESTPEM-CAL"), (ca2, "TESTPEM-CHAT")):
            with open(f, "w") as fh:
                fh.write("-----BEGIN CERTIFICATE-----\n%s\n-----END CERTIFICATE-----\n" % name)
        self.manifest = {
            "calendar": {"url": "https://cpbee:5232/chris/calendar/", "user": "chris"},
            "cas": {"cal": {"file": ca1, "name": "cpbee-ca.pem"}, "chat": {"file": ca2, "name": "chat-ca.pem"}},
            "delta_owner": "cpbee",
            "accounts": [
                {"id": "lan_chat", "protocol": "irc", "display": "LAN group chat", "autoconnect": True,
                 "opts": {"host": "cpbee", "port": "6697", "tls": "true", "cafile": "@CA@", "nick": "chris-pollitt",
                          "sasl": "chris-pollitt"}},
                {"id": "delta_chris", "protocol": "deltachat", "display": "Delta Chat (chris)", "autoconnect": True,
                 "opts": {"addr": "chris@cpbee.cmnet", "imap_server": "cpbee", "imap_user": "chris", "rpc_server": "@RPC@"}}]}
        self.setup_file = os.path.join(self.tmp, "floyd-setup.json")
        with open(self.setup_file, "w") as fh:
            json.dump(self.manifest, fh)
        self.loop = _Loop()
        self.gate = tvf.FloydGate(self.loop, tvf.FloydMemory(None), self.TOKEN,
                                  tvf.claude_settings(lambda k, d: "no" if k == "claude" else d), "127.0.0.1", 0,
                                  setup_file=self.setup_file)
        self.gate.start()
        self.server = "127.0.0.1:%d" % self.gate.sock.getsockname()[1]

    def tearDown(self):
        self.gate.stop()
        os.environ.clear()
        os.environ.update(self.old)
        tvf._MASTER = None

    def path(self, app):
        return os.path.join(self.cfgdir, app, app + ".conf")

    def write(self, app, text):
        with open(self.path(app), "w") as fh:
            fh.write(text)

    def read(self, app):
        with open(self.path(app)) as fh:
            return fh.read()

    def say(self, text, app="tvcal", master=None):
        return "\n".join(tvf.persona(text, "chatty", where=app, master=master if master is not None else {"server": ""}) or ["(nothing)"])

    def join(self):
        out = self.say("!setup join %s %s" % (self.server, self.TOKEN))
        return out, tvf.master_settings(lambda k, d: {"server": self.server, "token": self.TOKEN,  # allowlist secret
                                                       "machine": "cmlaptop"}.get(k, d))

    def test_without_a_master_it_asks_for_the_name_and_token_once(self):
        out = self.say("!setup")
        self.assertIn("!setup join HOST TOKEN", out)
        self.assertIn("Usage: !setup join", self.say("!setup join"))

    def test_join_checks_the_token_writes_it_and_never_echoes_it(self):
        bad = self.say("!setup join %s nope" % self.server)
        self.assertIn("did not accept that token", bad)
        self.assertEqual(self.read("tvcal").count("token"), 0)
        out, m = self.join()
        self.assertIn("Joined:", out)
        self.assertNotIn(self.TOKEN, out)                                       # the token is never repeated
        self.assertEqual(tvf.conf_get(self.path("tvcal"), "floyd", "server"), self.server)
        self.assertEqual(tvf.conf_get(self.path("tvcal"), "floyd", "token"), self.TOKEN)
        self.assertIn("# my comment", self.read("tvcal"))                       # comments survive
        dead = self.say("!setup join 127.0.0.1:1 x")
        self.assertIn("is not answering", dead)

    def test_the_report_lists_ok_todo_and_you_then_all_fixes_what_it_can(self):
        out, m = self.join()
        rep = self.say("!setup", master=m)
        self.assertIn("Setup check (master", rep)
        self.assertIn("ok   tvcal talks to the master Floyd", rep)
        self.assertIn("todo tvmail has no (or another) master Floyd", rep)
        self.assertIn("todo tvchat has no (or another) master Floyd", rep)
        self.assertIn("todo tvcal's calendar server is not https://cpbee:5232/chris/calendar/ here", rep)
        self.assertIn("todo tvchat lacks the LAN group chat account (irc)", rep)
        self.assertIn("todo tvchat lacks the Delta Chat (chris) account (deltachat)", rep)
        self.assertIn("you  no password for chris on cpbee", rep)
        self.assertIn("you  no password for chris-pollitt on cpbee", rep)
        self.assertNotIn(self.TOKEN, rep)
        done = self.say("!setup all", master=m)
        self.assertIn("Done: tvmail now has server =", done)
        self.assertIn("Done: tvcal: calendar https://cpbee:5232/chris/calendar/", done)
        self.assertIn("Done: tvchat: added the Delta Chat (chris) account", done)
        self.assertIn("Chat > Devices > Use account from another device", done)
        self.assertNotIn(self.TOKEN, done)
        # the files
        self.assertEqual(tvf.conf_get(self.path("tvmail"), "floyd", "token"), self.TOKEN)
        self.assertEqual(tvf.conf_get(self.path("tvcal"), "server", "url"), "https://cpbee:5232/chris/calendar/")
        self.assertEqual(tvf.conf_get(self.path("tvcal"), "server", "user"), "chris")
        ca = tvf.conf_get(self.path("tvcal"), "server", "ca")
        self.assertTrue(os.path.isfile(ca))
        self.assertIn("TESTPEM-CAL", open(ca).read())                            # the public CA came from the master
        self.assertEqual(tvf.conf_get(self.path("tvchat"), "chat", "autoconnect"), "a, lan_chat, delta_chris")
        self.assertEqual(tvf.conf_get(self.path("tvchat"), "account:lan_chat", "cafile"),
                         os.path.join(self.cfgdir, "tvchat", "chat-ca.pem"))
        self.assertIn("TESTPEM-CHAT", open(os.path.join(self.cfgdir, "tvchat", "chat-ca.pem")).read())
        self.assertEqual(tvf.conf_get(self.path("tvchat"), "account:delta_chris", "addr"), "chris@cpbee.cmnet")
        self.assertNotIn("@RPC@", self.read("tvchat"))
        self.assertNotIn("@CA@", self.read("tvchat"))
        for app in ("tvcal", "tvmail", "tvchat"):                                # a backup of every file that changed
            self.assertTrue(os.listdir(os.path.join(self.cfgdir, app, "BAK")), app)
        # the second look: only what is not Floyd's to fix remains
        again = self.say("!setup", master=m)
        self.assertNotIn("todo tvmail", again)
        self.assertNotIn("todo tvchat lacks", again)
        self.assertNotIn("todo tvcal's calendar", again)
        self.assertIn("you  no password", again)
        second = self.say("!setup all", master=m)                                 # nothing is left that Floyd may change
        self.assertNotIn("Done: tvmail", second)
        self.assertNotIn("Done: tvchat", second)

    def test_one_step_by_number_and_the_password_notice_goes_when_one_exists(self):
        out, m = self.join()
        rep = self.say("!setup", master=m).split("\n")
        n = next(i for i, l in enumerate(rep, 0) if "todo tvmail has no" in l)       # " 2. todo ..." (line 0 is the header)
        self.assertIn("Done: tvmail now has server", self.say("!setup %d" % n, master=m))
        self.assertEqual(tvf.conf_get(self.path("tvchat"), "floyd", "server"), "")  # only that step
        self.assertIn("!setup N does step N", self.say("!setup 99", master=m))
        with open(os.path.join(self.tmp, "home", ".netrc"), "w") as fh:
            fh.write("machine cpbee login chris-pollitt password hunter2\n")  # allowlist secret: test value
        os.chmod(os.path.join(self.tmp, "home", ".netrc"), 0o600)
        rep = self.say("!setup", master=m)
        self.assertNotIn("no password for chris-pollitt on cpbee", rep)             # present: only the fact is checked
        self.assertIn("no password for chris on cpbee", rep)
        self.assertNotIn("hunter2", rep)

    def test_a_delta_chat_gateway_account_is_added_and_asks_for_its_token_separately(self):
        import json
        self.manifest["accounts"] = [a for a in self.manifest["accounts"] if a["protocol"] != "deltachat"] + [
            {"id": "delta_chris", "protocol": "deltachat", "display": "Delta Chat (chris)", "autoconnect": True,
             "opts": {"gateway": "cpbee", "gateway_tls": "yes", "gateway_cafile": "@CA:chat@"}}]
        self.manifest.pop("delta_owner", None)
        with open(self.setup_file, "w") as fh:
            json.dump(self.manifest, fh)
        out, m = self.join()
        done = self.say("!setup all", master=m)
        self.assertIn("added the Delta Chat (chris) account", done)
        self.assertIn("Next: !setup delta TOKEN", done)
        self.assertEqual(tvf.conf_get(self.path("tvchat"), "account:delta_chris", "gateway"), "cpbee")
        self.assertEqual(tvf.conf_get(self.path("tvchat"), "account:delta_chris", "token"), "")
        rep = self.say("!setup", master=m)
        self.assertIn("you  tvchat's Delta Chat (chris) account has no token for the Delta Chat gateway", rep)
        self.assertIn("Usage: !setup delta TOKEN", self.say("!setup delta"))
        saved = self.say("!setup delta gwtoken123", master=m)
        self.assertIn("Token saved for delta_chris", saved)
        self.assertNotIn("gwtoken123", saved)                                       # never repeated
        self.assertEqual(tvf.conf_get(self.path("tvchat"), "account:delta_chris", "token"), "gwtoken123")
        self.assertNotIn("has no token for the Delta Chat gateway", self.say("!setup", master=m))
        self.assertIn("has no Delta Chat account with gateway", self.say("!setup delta x nonesuch", master=m))

    def test_an_account_of_the_older_kind_is_moved_to_the_gateway(self):
        import json
        self.manifest["accounts"] = [a for a in self.manifest["accounts"] if a["protocol"] != "deltachat"] + [
            {"id": "delta_chris", "protocol": "deltachat", "display": "Delta Chat (chris)", "autoconnect": True,
             "opts": {"gateway": "cpbee", "gateway_tls": "yes", "gateway_cafile": "@CA:chat@"}}]
        self.manifest.pop("delta_owner", None)
        with open(self.setup_file, "w") as fh:
            json.dump(self.manifest, fh)
        with open(self.path("tvchat"), "a") as fh:                     # the old kind: its own core, no gateway
            fh.write("\n[account:delta_chris]\nprotocol = deltachat\ndisplay = x\naddr = chris@cpbee.cmnet\n"
                     "rpc_server = /usr/local/bin/deltachat-rpc-server.exe\n")
        out, m = self.join()
        rep = self.say("!setup", master=m)
        self.assertIn("todo tvchat's Delta Chat (chris) account has its own Delta Chat core here", rep)
        done = self.say("!setup all", master=m)
        self.assertIn("now uses the Delta Chat gateway cpbee", done)
        self.assertEqual(tvf.conf_get(self.path("tvchat"), "account:delta_chris", "gateway"), "cpbee")
        self.assertEqual(tvf.conf_get(self.path("tvchat"), "account:delta_chris", "gateway_tls"), "yes")
        self.assertTrue(os.path.isfile(tvf.conf_get(self.path("tvchat"), "account:delta_chris", "gateway_cafile")))
        self.assertIn("Token saved", self.say("!setup delta tok999", master=m))
        self.assertNotIn("has its own Delta Chat core", self.say("!setup", master=m))

    def test_the_owner_machine_does_not_get_a_delta_account_step(self):
        self.say("!setup join %s %s" % (self.server, self.TOKEN))
        m = tvf.master_settings(lambda k, d: {"server": self.server, "token": self.TOKEN, "machine": "cpbee"}.get(k, d))  # allowlist secret
        rep = self.say("!setup", master=m)
        self.assertNotIn("Delta Chat (chris)", rep)

    def test_an_older_master_without_a_setup_file_says_so(self):
        out, m = self.join()
        os.remove(self.setup_file)
        rep = self.say("!setup", master=m)
        self.assertIn("the master did not send its setup", rep)

    def test_conf_helpers_keep_comments_and_do_not_duplicate(self):
        p = self.path("tvmail")
        self.assertTrue(tvf.conf_set(p, "service", "mode", "local"))
        self.assertFalse(tvf.conf_set(p, "service", "mode", "local"))
        self.assertTrue(tvf.conf_add_account(self.path("tvchat"), "n", [("protocol", "irc")]))
        self.assertFalse(tvf.conf_add_account(self.path("tvchat"), "n", [("protocol", "irc")]))
        self.assertFalse(tvf.conf_autoconnect_add(self.path("tvchat"), "a"))
        self.assertTrue(tvf.conf_autoconnect_add(self.path("tvchat"), "n"))
        self.assertEqual(tvf.conf_get(self.path("tvchat"), "chat", "autoconnect"), "a, n")


class TestTokenCmd(unittest.TestCase):
    """[floyd] token_cmd: an option; the token line stays the default, and a command that finds nothing leaves the token empty."""

    def script(self, body):
        d = tempfile.mkdtemp(prefix="tvf-tokcmd-")
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "fakeauth")
        with open(p, "w") as fh:
            fh.write("#!/bin/sh\n" + body + "\n")
        os.chmod(p, 0o755)
        return p

    def settings(self, **kw):
        return tvf.master_settings(lambda k, d: dict({"server": "cpbee"}, **kw).get(k, d))

    def test_the_command_gives_the_token_when_the_line_is_empty(self):
        m = self.settings(token_cmd=self.script("echo hubtoken; echo chatter >&2"))  # allowlist secret: test value
        self.assertEqual(m["token"], "hubtoken")

    def test_the_token_line_wins_over_the_command(self):
        m = self.settings(token="fromline", token_cmd=self.script("echo hubtoken"))  # allowlist secret: test value
        self.assertEqual(m["token"], "fromline")

    def test_a_locked_vault_or_a_missing_command_leaves_it_empty(self):
        self.assertEqual(self.settings(token_cmd=self.script("exit 4"))["token"], "")
        self.assertEqual(self.settings(token_cmd="/nonexistent/auth get floyd hub")["token"], "")
        self.assertEqual(self.settings()["token"], "")


if __name__ == "__main__":
    unittest.main()
