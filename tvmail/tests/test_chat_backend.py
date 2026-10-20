#!/usr/bin/env python3
"""Unit + integration tests for tvchat-backend.  Stdlib unittest, no deps,
no network and no real account.

    python3 -m unittest discover -s tests -p test_chat_backend.py
    ./test.sh -R chat_unit
"""
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / "backend" / "tvchat-backend"
EXAMPLE = HERE.parent / "configure" / "tvchat.conf.example"


def _load_backend():
    """Import tvchat-backend as a module (nothing runs at import time)."""
    loader = importlib.machinery.SourceFileLoader("tvchat_backend", str(BACKEND))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


cb = _load_backend()

CONF = """\
[chat]
autoconnect = libera

[account:libera]
protocol = irc
display  = Libera
host     = irc.libera.chat
nick     = tester

[account:delta]
protocol = DeltaChat
addr     = chat@example.org
"""


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tvchat-t-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._saved = {k: os.environ.get(k) for k in ("HOME", "TVCHAT_CONF")}
        os.environ["HOME"] = str(self.home)
        os.environ.pop("TVCHAT_CONF", None)
        cb._CHAT = None

    def tearDown(self):
        cb._CHAT = None
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import shutil
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def conf(self, text):
        p = self.tmp / "tvchat.conf"
        p.write_text(textwrap.dedent(text))
        os.environ["TVCHAT_CONF"] = str(p)
        return p


# --------------------------------------------------------------------------
class TestEscaping(unittest.TestCase):
    def test_round_trip(self):
        for s in ("plain", "a\tb", "two\nlines", "cr\r", "back\\slash",
                  "literal \\n is not a newline", "", "naïve 日本"):
            self.assertEqual(cb.unesc(cb.esc(s)), s)

    def test_no_raw_separators_survive(self):
        out = cb.esc("a\tb\nc\rd")
        self.assertNotIn("\t", out)
        self.assertNotIn("\n", out)
        self.assertNotIn("\r", out)

    def test_backslash_is_not_ambiguous(self):
        # a typed backslash-n and a real newline must come out different
        self.assertNotEqual(cb.esc("\\n"), cb.esc("\n"))

    def test_line_is_tab_separated(self):
        self.assertEqual(cb.line("a", 1, "b\tc"), "a\t1\tb\\tc\n")


# --------------------------------------------------------------------------
class TestConfig(Base):
    def test_missing_file_is_no_accounts(self):
        os.environ["TVCHAT_CONF"] = str(self.tmp / "nope.conf")
        self.assertEqual(cb.load_config(), ([], []))

    def test_default_path(self):
        self.assertEqual(cb._conf_path(),
                         str(self.home / ".config" / "tvmail" / "tvchat.conf"))

    def test_accounts_in_file_order(self):
        self.conf(CONF)
        accts, auto = cb.load_config()
        self.assertEqual([a.id for a in accts], ["libera", "delta"])
        self.assertEqual([a.protocol for a in accts], ["irc", "deltachat"])
        self.assertEqual(accts[0].display, "Libera")
        self.assertEqual(accts[1].display, "delta")   # defaults to the name
        self.assertEqual(accts[0].opts["nick"], "tester")
        self.assertEqual(auto, ["libera"])

    def test_bad_protocol(self):
        self.conf("[account:x]\nprotocol = whatsapp\n")
        with self.assertRaisesRegex(cb.UserError, "protocol must be"):
            cb.load_config()

    def test_bad_account_name(self):
        self.conf("[account:a/b]\nprotocol = irc\n")
        with self.assertRaisesRegex(cb.UserError, "account name"):
            cb.load_config()

    def test_autoconnect_unknown_account(self):
        self.conf("[chat]\nautoconnect = ghost\n")
        with self.assertRaisesRegex(cb.UserError, "ghost"):
            cb.load_config()

    def test_unparseable_file(self):
        self.conf("this is not ini\n")
        with self.assertRaises(cb.UserError):
            cb.load_config()

    def test_percent_is_literal(self):
        # no interpolation: a realname like "100% Chris" must just work
        self.conf("[account:x]\nprotocol = irc\nrealname = 100% Chris\n")
        self.assertEqual(cb.load_config()[0][0].opts["realname"], "100% Chris")

    def test_shipped_example_loads(self):
        os.environ["TVCHAT_CONF"] = str(EXAMPLE)
        accts, auto = cb.load_config()
        self.assertEqual(sorted(a.protocol for a in accts), ["deltachat", "irc"])
        for a in accts:
            self.assertNotIn("password", a.opts)    # never in the example
        self.assertTrue(set(auto) <= set(a.id for a in accts))


# --------------------------------------------------------------------------
class TestEventQueue(unittest.TestCase):
    def test_seq_increases_and_after_filters(self):
        q = cb.EventQueue(FakeClock(5.0))
        s1 = q.push("state", "libera", "connecting", "")
        s2 = q.push("state", "libera", "connected", "")
        self.assertEqual(s2, s1 + 1)
        self.assertEqual([e[0] for e in q.drain()], [s1, s2])
        self.assertEqual([e[0] for e in q.drain(s1)], [s2])

    def test_after_acknowledges(self):
        q = cb.EventQueue(FakeClock())
        s1 = q.push("a")
        s2 = q.push("b")
        q.drain(s2)
        self.assertEqual(q.drain(), [])          # never handed out twice
        s3 = q.push("c")
        self.assertEqual([e[0] for e in q.drain(s1)], [s3])

    def test_restart_keeps_climbing(self):
        # a stale --after from a lost backend must not hide a new one's events
        old = cb.EventQueue(FakeClock(1000.0))
        for _ in range(50):
            stale = old.push("x")
        new = cb.EventQueue(FakeClock(1001.0))
        s = new.push("state", "libera", "connected", "")
        self.assertEqual([e[0] for e in new.drain(stale)], [s])

    def test_cap_drops_oldest(self):
        q = cb.EventQueue(FakeClock(), cap=3)
        seqs = [q.push("e", str(i)) for i in range(5)]
        self.assertEqual([e[0] for e in q.drain()], seqs[2:])

    def test_wire(self):
        q = cb.EventQueue(FakeClock(0.0))
        s = q.push("topic", "libera/#t", "tabs\there")
        self.assertEqual(cb.EventQueue.wire(q.drain()[0]),
                         "%d\ttopic\tlibera/#t\ttabs\\there\n" % s)


class TestModel(unittest.TestCase):
    def test_conversation_and_message_lines(self):
        acct = cb.Account("libera", "irc", "Libera", {})
        conv = cb.Conversation(acct, "#tvmail-test", "channel")
        self.assertEqual(conv.wire(), "libera/#tvmail-test\tchannel\t0\t#tvmail-test\n")
        m = cb.Message("42", conv.id, 0, cb.Contact("bob!b@h", "bob"), "hi\nthere", "a")
        self.assertEqual(cb.line(*m.fields()),
                         "42\tlibera/#tvmail-test\t1970-01-01T00:00:00Z\t"
                         "bob!b@h\tbob\ta\thi\\nthere\n")


# --------------------------------------------------------------------------
class TestLoop(unittest.TestCase):
    def test_timers_fire_in_order_on_a_fake_clock(self):
        clk = FakeClock(0.0)
        loop = cb.Loop(clk)
        fired = []
        loop.call_later(2, lambda: fired.append("b"))
        loop.call_later(1, lambda: fired.append("a"))
        h = loop.call_later(1.5, lambda: fired.append("cancelled"))
        loop.cancel(h)
        self.assertEqual(loop.run_due(), 0)
        clk.t = 1.0
        loop.run_due()
        self.assertEqual(fired, ["a"])
        self.assertAlmostEqual(loop._timeout(), 1.0)
        clk.t = 5.0
        loop.run_due()
        self.assertEqual(fired, ["a", "b"])
        self.assertIsNone(loop._timeout())

    def test_reader_and_stop(self):
        loop = cb.Loop()
        r, w = os.pipe()
        self.addCleanup(os.close, r)
        self.addCleanup(os.close, w)
        got = []

        def on_read():
            got.append(os.read(r, 100))
            loop.stop()
        loop.add_reader(r, on_read)
        os.write(w, b"hello")
        loop.run()
        self.assertEqual(got, [b"hello"])
        loop.remove_reader(r)
        loop.remove_reader(r)                   # twice is harmless


# --------------------------------------------------------------------------
class TestVerbs(Base):
    def run_verb(self, *argv):
        return cb.dispatch(cb.build_parser(), list(argv))

    def test_accounts(self):
        self.conf(CONF)
        st, body = self.run_verb("accounts")
        self.assertEqual(st, 0)
        self.assertEqual(body.decode().splitlines(), [
            "libera\tirc\tdisconnected\tLibera",
            "delta\tdeltachat\tdisconnected\tdelta"])

    def test_bad_config_is_a_user_error(self):
        self.conf("[account:x]\nprotocol = nope\n")
        st, body = self.run_verb("accounts")
        self.assertEqual(st, 1)
        self.assertIn(b"protocol must be", body)

    def test_events(self):
        self.conf(CONF)
        chat = cb._chat()
        s = chat.events.push("state", "libera", "connecting", "")
        st, body = self.run_verb("events", "--after", str(s - 1))
        self.assertEqual((st, body.decode()),
                         (0, "%d\tstate\tlibera\tconnecting\t\n" % s))
        self.assertEqual(self.run_verb("events", "--after", str(s)), (0, b""))

    def test_unknown_verb_and_bad_args(self):
        st, body = self.run_verb("wobble")
        self.assertEqual(st, 2)
        self.assertIn(b"invalid choice", body)
        st, body = self.run_verb("events", "--after", "soon")
        self.assertEqual(st, 2)
        self.assertIn(b"invalid int", body)


# --------------------------------------------------------------------------
class TestServe(Base):
    """The frame protocol over a real pipe, as tvmail's ChatBackend sees it."""

    def _serve(self):
        p = subprocess.Popen([sys.executable, str(BACKEND), "serve"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, env=dict(os.environ))
        self.addCleanup(self._reap, p)
        return p

    @staticmethod
    def _reap(p):
        if p.poll() is None:
            try:
                p.stdin.write(b"quit\n")
                p.stdin.flush()
                p.wait(timeout=3)
            except Exception:
                p.kill()
                p.wait()
        for f in (p.stdin, p.stdout):
            try:
                f.close()
            except Exception:
                pass

    @staticmethod
    def _frame(f):
        hdr = f.readline()
        assert hdr, "EOF waiting for a reply frame"
        st, n = (int(x) for x in hdr.split())
        body = b""
        while len(body) < n:
            chunk = f.read(n - len(body))
            if not chunk:
                break
            body += chunk
        return st, body

    def _req(self, p, text):
        p.stdin.write(text.encode() + b"\n")
        p.stdin.flush()
        return self._frame(p.stdout)

    def test_handshake_banner(self):
        p = self._serve()
        self.assertEqual(self._frame(p.stdout), (0, b"ready"))

    def test_accounts_and_events_over_pipe(self):
        self.conf(CONF)
        p = self._serve()
        self._frame(p.stdout)
        st, body = self._req(p, "accounts")
        self.assertEqual(st, 0)
        self.assertEqual(len(body.decode().splitlines()), 2)
        self.assertEqual(self._req(p, "events"), (0, b""))
        self.assertEqual(self._req(p, "events --after 123"), (0, b""))

    def test_unknown_verb_keeps_serving(self):
        p = self._serve()
        self._frame(p.stdout)
        st, body = self._req(p, "wobble")
        self.assertEqual(st, 2)
        self.assertIn(b"wobble", body)
        self.assertEqual(self._req(p, "serve")[0], 2)            # no nesting
        self.assertEqual(self._req(p, 'ping "unterminated')[0], 2)
        self.assertEqual(self._req(p, "ping"), (0, b"pong\n"))   # still alive

    def test_bad_config_still_handshakes(self):
        self.conf("[account:x]\nprotocol = nope\n")
        p = self._serve()
        self.assertEqual(self._frame(p.stdout), (0, b"ready"))
        self.assertEqual(self._req(p, "accounts")[0], 1)
        self.assertEqual(self._req(p, "ping"), (0, b"pong\n"))

    def test_several_requests_in_one_write(self):
        # the selectors loop reads raw chunks - two lines in one read must
        # still be two requests, two frames, in order
        p = self._serve()
        self._frame(p.stdout)
        p.stdin.write(b"ping\nevents\n")
        p.stdin.flush()
        self.assertEqual(self._frame(p.stdout), (0, b"pong\n"))
        self.assertEqual(self._frame(p.stdout), (0, b""))

    def test_quit_exits_clean(self):
        p = self._serve()
        self._frame(p.stdout)
        p.stdin.write(b"quit\n")
        p.stdin.flush()
        self.assertEqual(p.wait(timeout=5), 0)
        self.assertEqual(p.stdout.read(), b"")   # quit sends no reply

    def test_eof_exits_clean(self):
        p = self._serve()
        self._frame(p.stdout)
        p.stdin.close()
        self.assertEqual(p.wait(timeout=5), 0)


if __name__ == "__main__":
    unittest.main()
