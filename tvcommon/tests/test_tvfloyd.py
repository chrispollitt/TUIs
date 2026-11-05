"""Tests for tvcommon/py/tvfloyd.py: the levels and the useful tools that tvchat's and
tvmail's Floyd share.  Stdlib unittest, no network."""
import importlib.util
import os
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


if __name__ == "__main__":
    unittest.main()
