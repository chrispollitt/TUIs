"""Tests for tvcommon/py/tvfloyd.py: the levels and the useful tools that tvchat's and
tvmail's Floyd share.  Stdlib unittest, no network."""
import importlib.util
import os
import tempfile
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


if __name__ == "__main__":
    unittest.main()
