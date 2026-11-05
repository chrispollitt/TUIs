#!/usr/bin/env python3
"""Unit + integration tests for tvchat-backend.  Stdlib unittest, no deps,
no network and no real account.

    python3 -m unittest discover -s tests -p test_chat_backend.py
    ./test.sh -R chat_unit
"""
import base64
import importlib.machinery
import json
import importlib.util
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import types
import unittest
import unittest.mock
import base64
import io
import logging
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
        keys = ("HOME", "TVCHAT_CONF", "TVMAIL_MU_TICKETS", "TVCHAT_PASS_LIBERA")
        self._saved = {k: os.environ.get(k) for k in keys}
        os.environ["HOME"] = str(self.home)
        os.environ["TVMAIL_MU_TICKETS"] = str(self.tmp / "no-tickets")
        for k in ("TVCHAT_CONF", "TVCHAT_PASS_LIBERA"):
            os.environ.pop(k, None)
        cb._CHAT = None

    def tearDown(self):
        cb._CHAT = None
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
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
                         str(self.home / ".config" / "tvchat" / "tvchat.conf"))

    def test_old_tvmail_path_when_the_new_one_is_missing(self):
        # chat was part of tvmail until tvchat 0.1.0: its config still works
        old = self.home / ".config" / "tvmail" / "tvchat.conf"
        old.parent.mkdir(parents=True)
        old.write_text("[account:x]\nprotocol = irc\n")
        self.assertEqual(cb._conf_path(), str(old))
        new = self.home / ".config" / "tvchat" / "tvchat.conf"
        new.parent.mkdir(parents=True)
        new.write_text("[account:y]\nprotocol = irc\n")
        self.assertEqual(cb._conf_path(), str(new))   # the new one wins

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
        self.assertEqual(sorted(a.protocol for a in accts), ["deltachat", "irc", "talk"])
        self.assertEqual([a.view for a in accts if a.protocol == "talk"], ["classic"])
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
    """The frame protocol over a real pipe, as tvchat's ChatClient sees it."""

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
        self.conf(CONF.replace("autoconnect = libera", ""))   # no network
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


# --------------------------------------------------------------------------
# IRC: the pure helpers
# --------------------------------------------------------------------------
class TestIrcParse(unittest.TestCase):
    def test_full_line(self):
        m = cb.parse_line("@time=2026-10-20T12:00:00.500Z;msgid=abc;+x=a\\sb\\:c "
                          ":alice!a@host PRIVMSG #chan :hello there :)\r\n")
        self.assertEqual(m.tags, {"time": "2026-10-20T12:00:00.500Z",
                                  "msgid": "abc", "+x": "a b;c"})
        self.assertEqual((m.prefix, m.nick, m.command), ("alice!a@host", "alice", "PRIVMSG"))
        self.assertEqual(m.params, ["#chan", "hello there :)"])

    def test_no_prefix_no_trailing(self):
        m = cb.parse_line("ping   irc.example")
        self.assertEqual((m.prefix, m.command, m.params), ("", "PING", ["irc.example"]))

    def test_empty_trailing_and_numeric(self):
        m = cb.parse_line(":srv 332 me #c :")
        self.assertEqual((m.command, m.params), ("332", ["me", "#c", ""]))

    def test_bare_tag_and_blank(self):
        self.assertEqual(cb.parse_line("@draft/bot :n!u@h JOIN #c").tags, {"draft/bot": ""})
        self.assertIsNone(cb.parse_line(""))
        self.assertIsNone(cb.parse_line(":prefix-only"))

    def test_cap_ls_line(self):
        m = cb.parse_line(":srv CAP * LS * :sasl=PLAIN,EXTERNAL server-time")
        self.assertEqual(m.params, ["*", "LS", "*", "sasl=PLAIN,EXTERNAL server-time"])

    def test_formatting_stripped(self):
        self.assertEqual(cb.strip_formatting("\x02bold\x02 \x0304,12red\x03 "
                                             "\x1ditalic\x0f \x04FF0000hex"),
                         "bold red italic hex")
        self.assertEqual(cb.strip_formatting("\x0399 bottles"), " bottles")

    def test_split_utf8(self):
        self.assertEqual(cb.split_utf8("short"), ["short"])
        text = "é" * 300                                   # 600 bytes
        parts = cb.split_utf8(text, 401)
        self.assertEqual("".join(parts), text)
        self.assertTrue(all(len(p.encode()) <= 401 for p in parts))
        self.assertEqual(len(parts[0].encode()), 400)      # never half a char
        emoji = "😀" * 150
        self.assertEqual("".join(cb.split_utf8(emoji, 10)), emoji)

    def test_server_time(self):
        self.assertEqual(cb.parse_server_time("1970-01-01T00:01:00Z"), 60)
        self.assertAlmostEqual(cb.parse_server_time("1970-01-01T00:00:01.250Z"), 1.25)
        self.assertIsNone(cb.parse_server_time("yesterday"))
        self.assertIsNone(cb.parse_server_time(None))

    def test_casemapping(self):
        self.assertEqual(cb.irc_lower("Nick[]\\~"), "nick{}|^")
        self.assertEqual(cb.irc_lower("Nick[]", "ascii"), "nick[]")


# --------------------------------------------------------------------------
# IRC against a fake server.  It speaks plain TCP on 127.0.0.1 (TLS is the
# stdlib's job) and runs a scripted conversation on its own thread while the
# test thread turns the chat Loop.
# --------------------------------------------------------------------------
class FakeIrc:
    def __init__(self, script, tls=None):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.script = script
        self.tls = tls
        self.got = []                    # every line the client sent
        self.error = None
        self.conn = None
        self.buf = b""
        self.pending = []
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            self.sock.settimeout(10)
            self.conn, _ = self.sock.accept()
            self.conn.settimeout(10)
            if self.tls is not None:
                self.conn = self.tls.wrap_socket(self.conn, server_side=True)
            self.script(self)
        except Exception as e:           # surfaced by the test via .error
            self.error = e

    def send(self, text):
        self.conn.sendall(text.encode("utf-8") + b"\r\n")

    def expect(self, prefix):
        """The first not-yet-expected client line starting with prefix (in
        arrival order, so the script needn't mirror the client's exact
        interleaving of e.g. NICK/USER with CAP); reads more as needed."""
        while True:
            for i, ln in enumerate(self.pending):
                if ln.startswith(prefix):
                    del self.pending[i]
                    return ln
            while b"\n" not in self.buf:
                data = self.conn.recv(4096)
                if not data:
                    raise AssertionError("client hung up waiting for %r" % prefix)
                self.buf += data
            ln, self.buf = self.buf.split(b"\n", 1)
            ln = ln.decode("utf-8").rstrip("\r")
            self.got.append(ln)
            self.pending.append(ln)

    def hang_up(self):
        self.conn.close()

    def reaccept(self):
        """The client's next connection (a reconnect), with fresh buffers."""
        self.conn, _ = self.sock.accept()
        self.conn.settimeout(10)
        self.buf = b""
        self.pending = []

    def close(self):
        for s in (self.conn, self.sock):
            try:
                s.close()
            except Exception:
                pass


def welcome(srv, nick="tester"):
    """Registration with no CAPs on offer."""
    srv.expect("CAP LS 302")
    srv.send(":srv CAP * LS :")
    srv.expect("CAP END")
    srv.expect("USER ")
    srv.send(":srv 001 %s :Welcome to FakeNet" % nick)
    srv.send(":srv 005 %s CASEMAPPING=rfc1459 CHANTYPES=# PREFIX=(ov)@+ :are supported" % nick)


class IrcBase(Base):
    def setup_irc(self, script, extra=""):
        srv = FakeIrc(script)
        self.addCleanup(srv.close)
        self.conf("""\
            [account:libera]
            protocol = irc
            display  = FakeNet
            host     = 127.0.0.1
            port     = %d
            tls      = false
            nick     = tester
            channels = #Test
            %s
            """ % (srv.port, extra))
        self.chat = cb._chat()
        self.acct = self.chat.by_id["libera"]
        self.srv = srv
        return srv

    def turn(self, pred, timeout=8):
        loop = self.chat.loop
        loop.running = True
        deadline = time.time() + timeout
        while not pred():
            if self.srv.error:
                raise self.srv.error
            if time.time() > deadline:
                self.fail("timed out; events: %r\nserver got: %r"
                          % (self.chat.events.items[-8:], self.srv.got))
            loop.run_once(0.05)

    def events(self, etype=None):
        return [f for _, f in self.chat.events.items if etype is None or f[0] == etype]

    def msgs(self):
        return [f[1:] for f in self.events("msg")]

    def verb(self, *argv):
        return cb.dispatch(cb.build_parser(), list(argv))


class TestIrcSession(IrcBase):
    def test_cap_sasl_join_and_traffic(self):
        os.environ["TVCHAT_PASS_LIBERA"] = "s3cret"
        stage = {}

        def script(srv):
            srv.expect("CAP LS 302")
            srv.send(":srv CAP * LS * :multi-prefix sasl=PLAIN,EXTERNAL")
            srv.send(":srv CAP * LS :server-time message-tags echo-message unknown-cap")
            req = srv.expect("CAP REQ")
            stage["req"] = req.split(":", 1)[1].split()
            srv.send(":srv CAP * ACK :%s" % " ".join(stage["req"]))
            srv.expect("AUTHENTICATE PLAIN")
            srv.send("AUTHENTICATE +")
            stage["auth"] = srv.expect("AUTHENTICATE ").split(" ", 1)[1]
            srv.send(":srv 900 tester tester!u@h chrisp :You are now logged in as chrisp")
            srv.send(":srv 903 tester :SASL authentication successful")
            srv.expect("CAP END")
            srv.expect("USER ")
            srv.send(":srv 433 * tester :Nickname is already in use")
            srv.expect("NICK tester_")
            srv.send(":srv 001 tester_ :Welcome to FakeNet")
            srv.send(":srv 005 tester_ PREFIX=(ov)@+ CHANTYPES=# :are supported")
            srv.expect("JOIN #Test")
            srv.send(":tester_!u@h JOIN #Test")
            srv.send(":srv 332 tester_ #Test :the \x02topic\x02")
            srv.send(":srv 353 tester_ = #Test :@+alice tester_ +bob")
            srv.send(":srv 366 tester_ #Test :End of /NAMES list.")
            srv.send("@time=2026-10-20T12:00:00.000Z;msgid=m1 "
                     ":alice!a@h PRIVMSG #test :\x0304hello\x03\tthere")
            srv.send(":bob!b@h PRIVMSG #Test :\x01ACTION waves\x01")
            srv.send(":carol!c@h PRIVMSG tester_ :psst")
            srv.send(":carol!c@h PRIVMSG tester_ :\x01VERSION\x01")
            srv.expect("NOTICE carol :\x01VERSION tvchat")
            srv.send("PING :keepalive-1")
            srv.expect("PONG :keepalive-1")
            line = srv.expect("PRIVMSG #Test :")
            srv.send("@msgid=m9 :tester_!u@h " + line)       # echo-message
            srv.expect("QUIT")

        self.setup_irc(script, "sasl = chrisp")
        self.assertEqual(self.verb("connect", "libera"), (0, b""))
        self.turn(lambda: any(m[6] == "psst" for m in self.msgs()))
        self.assertEqual(self.acct.state, "connected")
        self.assertEqual(sorted(stage["req"]), sorted(
            ["multi-prefix", "sasl", "server-time", "message-tags", "echo-message"]))
        self.assertEqual(base64.b64decode(stage["auth"]), b"chrisp\0chrisp\0s3cret")

        states = [f[2] for f in self.events("state")]
        self.assertEqual(states[-1], "connected")
        self.assertIn("connecting", states)

        convs = self.verb("convs", "libera")[1].decode().splitlines()
        self.assertEqual(convs, ["libera/*\tserver\t0\tFakeNet",
                                 "libera/#test\tchannel\t2\t#Test",
                                 "libera/carol\tdirect\t1\tcarol"])
        self.assertEqual(self.events("topic")[-1], ("topic", "libera/#test", "the topic"))
        self.assertEqual(self.verb("members", "libera/#test")[1].decode().splitlines(),
                         ["alice\talice\top", "bob\tbob\tvoice", "tester_\ttester_\tmember"])

        hello = [m for m in self.msgs() if m[0] == "m1"][0]
        self.assertEqual(hello, ("m1", "libera/#test", "2026-10-20T12:00:00Z",
                                 "alice", "alice", "", "hello\tthere"))
        action = [m for m in self.msgs() if m[6] == "waves"][0]
        self.assertEqual((action[3], action[5]), ("bob", "a"))

        # send: echo-message is on, so nothing is inserted until the echo
        st, body = self.verb("send", "libera/#test", "hi all")
        self.assertEqual(st, 0)
        self.assertTrue(body.strip())
        self.turn(lambda: any(m[0] == "m9" for m in self.msgs()))
        mine = [m for m in self.msgs() if m[6] == "hi all"]
        self.assertEqual(len(mine), 1)
        self.assertEqual((mine[0][0], mine[0][5]), ("m9", "s"))

        # history (this session's ring), --before, --limit
        hist = self.verb("history", "libera/#test")[1].decode().splitlines()
        self.assertTrue(hist[-1].endswith("\ts\thi all"))
        self.assertTrue(any("\\t" in h for h in hist))     # escaped TAB
        older = self.verb("history", "libera/#test", "--before", "m9", "--limit", "1")[1]
        self.assertEqual(len(older.decode().splitlines()), 1)
        self.assertNotIn(b"hi all", older)
        self.assertEqual(self.verb("history", "libera/#test", "--before", "nope"), (0, b""))

        # unread and mark-read
        self.assertEqual(self.events("unread")[-1], ("unread", "libera/carol", 1))
        self.assertEqual(self.acct.convs["libera/#test"].unread, 2)
        self.assertEqual(self.verb("mark-read", "libera/#test"), (0, b""))
        self.assertEqual(self.events("unread")[-1], ("unread", "libera/#test", 0))

        self.assertEqual(self.verb("disconnect", "libera"), (0, b""))
        self.assertEqual(self.acct.state, "disconnected")
        self.srv.thread.join(5)
        self.assertIsNone(self.srv.error)

    def test_no_sasl_without_an_account_name(self):
        stage = {}

        def script(srv):
            srv.expect("CAP LS 302")
            srv.send(":srv CAP * LS :sasl server-time")
            stage["req"] = srv.expect("CAP REQ")
            srv.send(":srv CAP * ACK :server-time")
            srv.expect("CAP END")
            srv.expect("USER ")
            srv.send(":srv 001 tester :hi")
            srv.expect("JOIN #Test")
            srv.expect("QUIT")

        self.setup_irc(script)
        self.verb("connect", "libera")
        self.turn(lambda: self.acct.state == "connected" and "req" in stage)
        self.assertEqual(stage["req"], "CAP REQ :server-time")
        self.assertFalse(any(g.startswith("AUTHENTICATE") for g in self.srv.got))
        self.chat.close()

    def test_missing_sasl_password_is_a_user_error(self):
        self.setup_irc(lambda srv: None, "sasl = chrisp")
        st, body = self.verb("connect", "libera")
        self.assertEqual(st, 1)
        self.assertIn(b"TVCHAT_PASS_LIBERA", body)
        self.assertIn(b"~/.netrc", body)
        self.assertEqual(self.events("state")[-1][2], "error")

    def test_sasl_password_from_netrc(self):
        n = self.home / ".netrc"
        n.write_text("machine 127.0.0.1 login chrisp password fromnetrc\n")
        n.chmod(0o600)
        self.assertEqual(cb.find_password("libera", "127.0.0.1", "chrisp", "irc"),
                         "fromnetrc")
        os.environ["TVCHAT_PASS_LIBERA"] = "fromenv"         # env wins
        self.assertEqual(cb.find_password("libera", "127.0.0.1", "chrisp", "irc"),
                         "fromenv")

    def test_sasl_password_only_for_its_login(self):
        # Bug #46: the mail ticket *://chris:...@cpbee was handed to SASL as
        # chris-pollitt's - ~/.mu-tickets fell back to any user on the host
        t = self.tmp / "tickets"
        t.write_text("*://chris:mailpw@cpbee\n")
        os.environ["TVMAIL_MU_TICKETS"] = str(t)
        n = self.home / ".netrc"
        n.write_text("machine cpbee login chris-pollitt password ircpw\n")
        n.chmod(0o600)
        self.assertEqual(cb.find_password_where("cpbee", "cpbee", "chris-pollitt", "irc"),
                         ("ircpw", "~/.netrc"))
        # a ticket for this login wins over ~/.netrc, as before
        t.write_text("*://chris:mailpw@cpbee\nirc://chris-pollitt:ticketpw@CPBEE\n")
        self.assertEqual(cb.find_password_where("cpbee", "cpbee", "chris-pollitt", "irc"),
                         ("ticketpw", "~/.mu-tickets"))
        # nor is ~/.netrc's entry for another login used
        t.write_text("*://chris:mailpw@cpbee\n")
        n.write_text("machine cpbee login chris password mailpw\n")
        self.assertEqual(cb.find_password_where("cpbee", "cpbee", "chris-pollitt", "irc"),
                         (None, None))

    def test_sasl_failure_is_reported_and_registration_continues(self):
        os.environ["TVCHAT_PASS_LIBERA"] = "wrong"

        def script(srv):
            srv.expect("CAP LS 302")
            srv.send(":srv CAP * LS :sasl")
            srv.expect("CAP REQ :sasl")
            srv.send(":srv CAP * ACK :sasl")
            srv.expect("AUTHENTICATE PLAIN")
            srv.send("AUTHENTICATE +")
            srv.expect("AUTHENTICATE ")
            srv.send(":srv 904 tester :SASL authentication failed")
            srv.expect("CAP END")
            srv.send(":srv 001 tester :hi")
            srv.expect("QUIT")

        self.setup_irc(script, "sasl = chrisp")
        self.verb("connect", "libera")
        self.turn(lambda: self.acct.state == "connected")
        self.assertTrue(any("SASL login failed" in m[6] for m in self.msgs()))
        # ... and says which password it tried (bug #46)
        self.assertTrue(any("from $TVCHAT_PASS_LIBERA" in m[6] for m in self.msgs()))
        self.chat.close()

    def test_server_hangup_reconnects(self):
        def script(srv):
            welcome(srv)
            srv.expect("JOIN #Test")
            srv.send(":tester!u@h JOIN #Test")
            srv.send(":srv 353 tester = #Test :tester alice")
            srv.send(":srv 366 tester #Test :end")
            srv.send(":alice!a@h QUIT :bye")
            srv.send(":alice!a@h NICK :ignored")
            srv.send("ERROR :Closing Link: too many cooks")
            srv.hang_up()

        self.setup_irc(script)
        self.chat.adapter(self.acct).RETRY_FIRST = 60        # no reconnect yet
        self.verb("connect", "libera")
        # it was up: lost, so it waits and tries again - still "connecting"
        self.turn(lambda: "reconnecting in 60 s" in self.acct.detail)
        self.assertEqual(self.acct.state, "connecting")
        self.assertIn("too many cooks", self.events("state")[-1][3])
        self.assertTrue(any(m[6].endswith("too many cooks - reconnecting in 60 s")
                            for m in self.msgs() if m[1] == "libera/*"))
        self.assertTrue(any("alice has quit (bye)" == m[6] for m in self.msgs()))
        self.assertEqual(self.acct.convs["libera/#test"].members, {})
        # the conversation outlives the connection, so its window stays put
        self.assertIn("libera/#test", self.acct.convs)
        st, body = self.verb("send", "libera/#test", "anyone?")
        self.assertEqual(st, 1)
        self.assertIn(b"not connected", body)
        # Disconnect while it waits: it stops trying
        self.assertEqual(self.verb("disconnect", "libera"), (0, b""))
        self.assertEqual(self.acct.state, "disconnected")
        self.assertIsNone(self.chat.adapter(self.acct).retry_timer)

    def test_first_connect_hung_up_on_is_not_retried(self):
        def script(srv):
            srv.expect("CAP LS 302")
            srv.hang_up()

        self.setup_irc(script)
        self.verb("connect", "libera")
        self.turn(lambda: self.acct.state == "disconnected")
        self.assertIsNone(self.chat.adapter(self.acct).retry_timer)

    def test_join_query_part_and_local_echo(self):
        def script(srv):
            welcome(srv)
            srv.expect("JOIN #Test")
            srv.expect("JOIN #more")
            srv.send(":tester!u@h JOIN #more")
            srv.expect("PRIVMSG dave :line one")
            srv.expect("PRIVMSG dave :line two")
            srv.expect("PART #more")
            srv.send(":tester!u@h PART #more")
            srv.expect("QUIT")

        self.setup_irc(script)
        self.verb("connect", "libera")
        self.turn(lambda: self.acct.state == "connected")
        self.assertEqual(self.verb("join", "libera", "#more"), (0, b"libera/#more\n"))
        self.assertEqual(self.verb("join", "libera", "Dave"), (0, b"libera/dave\n"))
        st, _ = self.verb("send", "libera/dave", "line one\nline two")
        self.assertEqual(st, 0)
        # no echo-message: our own lines are inserted locally, not counted unread
        mine = [m for m in self.msgs() if m[1] == "libera/dave"]
        self.assertEqual([(m[5], m[6]) for m in mine], [("s", "line one"), ("s", "line two")])
        self.assertEqual(self.acct.convs["libera/dave"].unread, 0)
        self.assertEqual(self.verb("part", "libera/#more"), (0, b""))
        self.assertIn(("conv-gone", "libera/#more", "channel", 0, "#more"), self.events())
        self.assertEqual(self.verb("part", "libera/*")[0], 1)
        self.assertEqual(self.verb("send", "libera/*", "x")[0], 1)
        self.assertEqual(self.verb("send", "libera/nope", "x")[0], 1)
        self.turn(lambda: any(g.startswith("PART") for g in self.srv.got))
        self.chat.close()

    def test_long_line_is_split(self):
        got = {}

        def script(srv):
            welcome(srv)
            srv.expect("JOIN #Test")
            got["a"] = srv.expect("PRIVMSG #Test :")
            got["b"] = srv.expect("PRIVMSG #Test :")
            srv.expect("QUIT")

        self.setup_irc(script)
        self.verb("connect", "libera")
        self.turn(lambda: self.acct.state == "connected")
        self.verb("join", "libera", "#Test")
        self.verb("send", "libera/#test", "ü" * 300)
        self.turn(lambda: "b" in got)
        body = got["a"].split(" :", 1)[1] + got["b"].split(" :", 1)[1]
        self.assertEqual(body, "ü" * 300)
        self.chat.close()

    def test_tail_format(self):
        f = cb._tail_format
        self.assertEqual(f(("msg", "1", "libera/#c", "1970-01-01T00:00:00Z", "a", "alice",
                            "", "hi"))[5:], " #c <alice> hi")
        self.assertIn("* bob waves", f(("msg", "1", "libera/#c", "1970-01-01T00:00:00Z",
                                         "b", "bob", "a", "waves")))
        self.assertEqual(f(("state", "libera", "connected", "srv")),
                         "** state libera connected srv")


class TestIrcInvitations(IrcBase):
    """Chat > Invite... to another server: an irc:// link in a message becomes an
    invitation for the IRC account on that server (and a link for a server we
    have no account on is only said so); Accept invite... joins it."""

    def test_links(self):
        self.assertEqual(cb.irc_invite_url("irc.example.org", 6697, True, "#lobby"),
                         "ircs://irc.example.org/#lobby")
        self.assertEqual(cb.irc_invite_url("irc.example.org", 6667, False), "irc://irc.example.org")
        self.assertEqual(cb.irc_invite_url("h", 7000, False, "#c"), "irc://h:7000/#c")
        f = cb.irc_invite_in
        self.assertEqual(f("come to ircs://Irc.Example.org/lobby."), ("irc.example.org", 6697, "#lobby"))
        self.assertEqual(f("irc://h:7000/%23c,isnick, ok"), ("h", 7000, "#c"))
        self.assertEqual(f("see irc://h"), ("h", 6667, ""))
        self.assertIsNone(f("no link here, nor http://h/x"))
        self.assertIsNone(f("irc://h:99999/x"))

    def test_invitation_in_a_message(self):
        got = {}

        def script(srv):
            welcome(srv)
            srv.expect("JOIN #Test")
            srv.send(":carol!c@h PRIVMSG tester :come to irc://127.0.0.1:%d/lobby." % srv.port)
            srv.send(":dave!d@h PRIVMSG tester :try irc://other.example.org/#x")
            srv.send(":zed!z@h PRIVMSG #Test :irc://127.0.0.1:%d/#noisy" % srv.port)  # a channel: not an invitation
            srv.send(":frank!f@h PRIVMSG tester :irc://127.0.0.1:%d/#nope" % srv.port)
            srv.send(":frank!f@h PRIVMSG tester :end")
            got["join"] = srv.expect("JOIN #lobby")
            got["invite"] = srv.expect("INVITE carol #lobby")
            srv.expect("QUIT")

        self.setup_irc(script)
        self.verb("connect", "libera")
        self.turn(lambda: any(m[6] == "end" for m in self.msgs()))
        what = "to #lobby on FakeNet"
        self.assertEqual(self.verb("invites"), (0, ("libera\tcarol\t%s\nlibera\tfrank\tto #nope on FakeNet\n"
                                                    % what).encode()))
        self.assertIn(("invite", "libera", "carol"), self.events())
        # declined: gone, and never joined
        self.assertEqual(self.verb("decline-invite", "libera", "frank"), (0, b""))
        self.assertEqual(self.verb("invites"), (0, ("libera\tcarol\t%s\n" % what).encode()))
        said = [m[6] for m in self.msgs() if m[1] == "libera/dave"]
        self.assertTrue(any("none of your accounts connects there" in s for s in said), said)
        # accepting joins the channel
        self.assertEqual(self.verb("accept-invite", "libera", "carol"), (0, b"libera/#lobby\n"))
        self.assertEqual(self.verb("invites"), (0, b""))
        self.assertEqual(self.verb("accept-invite", "libera", "carol")[0], 1)
        # our own link, and an invitation sent to someone on the same network
        self.assertEqual(self.verb("invite", "libera", "#lobby"),
                         (0, ("irc://127.0.0.1:%d/#lobby\n" % self.srv.port).encode()))
        self.assertEqual(self.verb("invite", "libera", "lobby")[0], 1)
        self.assertEqual(self.verb("invite-send", "libera", "libera/carol", "#lobby"),
                         (0, b"invited carol to #lobby\n"))
        self.assertEqual(self.verb("invite-send", "libera", "libera/carol")[0], 1)   # they are here already
        self.assertEqual(self.verb("invite-send", "libera", "libera/#test", "#lobby")[0], 1)  # not a person
        self.turn(lambda: "invite" in got)
        self.verb("disconnect", "libera")
        self.chat.close()

    def test_invitees_are_the_others_in_the_channel(self):
        def script(srv):
            welcome(srv)
            srv.expect("JOIN #Test")
            srv.send(":tester!u@h JOIN #Test")
            srv.send(":srv 353 tester = #Test :@alice tester +bob")
            srv.send(":srv 366 tester #Test :End of /NAMES list.")
            srv.expect("QUIT")

        self.setup_irc(script)
        self.verb("connect", "libera")
        self.turn(lambda: len(getattr(self.chat.account("libera").convs.get("libera/#test"), "members", ())) == 3)
        self.assertEqual(self.verb("invitees", "libera/#test"),
                         (0, b"alice\talice\top\nbob\tbob\tvoice\n"))
        self.verb("disconnect", "libera")
        self.chat.close()

    def test_delta_chat_link_without_an_account(self):
        def script(srv):
            welcome(srv)
            srv.expect("JOIN #Test")
            srv.send(":carol!c@h PRIVMSG tester :https://i.delta.chat/#ABCD&a=carol%40example.org")
            srv.expect("QUIT")

        self.setup_irc(script)
        self.verb("connect", "libera")
        self.turn(lambda: any("Delta Chat invitation" in m[6] for m in self.msgs()))
        self.assertEqual(self.verb("invites"), (0, b""))
        self.verb("disconnect", "libera")
        self.chat.close()

    def test_join_invites_takes_them_at_once(self):
        got = {}

        def script(srv):
            welcome(srv)
            srv.expect("JOIN #Test")
            srv.send(":carol!c@h PRIVMSG tester :irc://127.0.0.1:%d/#one" % srv.port)
            got["one"] = srv.expect("JOIN #one")
            srv.expect("QUIT")

        self.setup_irc(script, "join_invites = yes")
        self.verb("connect", "libera")
        self.turn(lambda: "one" in got)
        self.assertEqual(self.verb("invites"), (0, b""))          # nothing left to ask
        self.verb("disconnect", "libera")
        self.chat.close()


class TestPeople(IrcBase):
    """The nick joining table ([people]): one person, a different name on each server."""

    def two_networks(self, script_a, script_b, people):
        a, b = FakeIrc(script_a), FakeIrc(script_b)
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        self.conf("""\
            [account:libera]
            protocol = irc
            display  = FakeNet
            host     = 127.0.0.1
            port     = %d
            tls      = false
            nick     = tester
            [account:oftc]
            protocol = irc
            display  = OFTC
            host     = 127.0.0.1
            port     = %d
            tls      = false
            nick     = tester2
            [people]
            %s
            """ % (a.port, b.port, people))
        self.chat = cb._chat()
        self.srv, self.srv2 = a, b
        self.verb("connect", "libera")
        self.verb("connect", "oftc")
        return a, b

    def test_table_parses(self):
        self.conf("""\
            [account:libera]
            protocol = irc
            host = h
            [account:delta]
            protocol = deltachat
            addr = c@example.org
            [people]
            Carol = libera:cpollitt, delta:carol@example.org
            Dave  = libera:dave_
            """)
        chat = cb._chat()
        self.assertEqual(self.verb("people"), (0, (
            "Carol\tlibera\tcpollitt\nCarol\tdelta\tcarol@example.org\nDave\tlibera\tdave_\n").encode()))
        self.assertEqual(chat.people.name_of("libera", "CPollitt"), "Carol")
        self.assertIsNone(chat.people.name_of("delta", "cpollitt"))
        self.assertEqual(chat.who_is(chat.by_id["libera"], "dave_"), "Dave (dave_)")
        self.assertEqual(chat.who_is(chat.by_id["libera"], "Dave"), "Dave")

    def test_bad_table_is_refused(self):
        for entry, word in (("Carol = nowhere:carol", "no [account:nowhere]"),
                            ("Carol = libera", "not account:handle"),
                            ("Carol = email:nope", "not an email address"),
                            ("Carol = libera:x\n            Dave = libera:X", "both")):
            self.conf("""\
                [account:libera]
                protocol = irc
                host = h
                [people]
                %s
                """ % entry)
            errors = cb.check_config(os.environ["TVCHAT_CONF"])[0]
            self.assertTrue(errors and word in errors[0], (entry, errors))

    def test_mail_selected_chat_lines_through_tvmail(self):
        def libera(srv):
            welcome(srv)
            srv.send(":carol!c@h PRIVMSG tester :hello")
            srv.send(":dave!d@h PRIVMSG tester :hello")
            srv.expect("QUIT")

        def oftc(srv):
            welcome(srv, "tester2")
            srv.expect("QUIT")

        self.two_networks(libera, oftc, "carol = libera:carol, email:carol@example.org")
        self.turn(lambda: "libera/dave" in self.chat.account("libera").convs)
        # the person's email handle in the table; nobody known: blank
        self.assertEqual(self.verb("mail-address", "libera/carol"), (0, b"carol@example.org\n"))
        self.assertEqual(self.verb("mail-address", "libera/dave"), (0, b"\n"))
        d = self.tmp / "bin"
        d.mkdir()
        log, fail = self.tmp / "tvmail.log", self.tmp / "fail"
        stub = d / "tvmail-backend"
        stub.write_text("#!/bin/sh\necho \"$@\" >> %s\ncat >> %s\n"
                        "[ -e %s ] && { echo 'SMTP server said no'; exit 1; }\nexit 0\n" % (log, log, fail))
        stub.chmod(0o755)
        path = os.environ["PATH"]
        os.environ["PATH"] = str(d) + os.pathsep + path
        self.addCleanup(os.environ.__setitem__, "PATH", path)
        self.assertEqual(self.verb("mail-chat", "libera/carol", "carol@example.org", "Hi  there",
                                   "line one\\nline two"), (0, b""))
        self.turn(lambda: any("mailed to carol@example.org" in f[1] for f in self.events("notice")))
        text = log.read_text()
        self.assertIn("send --to carol@example.org --subject Hi there", text)
        self.assertIn("From the chat carol (FakeNet):\n\nline one\nline two\n", text)
        # tvmail refuses: said as a notice
        fail.write_text("")
        self.verb("mail-chat", "libera/carol", "carol@example.org", "", "x")
        self.turn(lambda: any("tvmail could not mail carol@example.org: SMTP server said no" in f[1]
                              for f in self.events("notice")))
        self.assertEqual(self.verb("mail-chat", "libera/carol", "nobody", "s", "x")[0], 1)
        self.assertEqual(self.verb("mail-chat", "libera/carol", "carol@example.org", "s", " ")[0], 1)
        self.verb("disconnect", "libera")
        self.verb("disconnect", "oftc")
        self.chat.close()

    def test_invite_finds_their_nick_on_the_other_server(self):
        got = {}

        def libera(srv):
            welcome(srv)
            srv.send(":carol!c@h PRIVMSG tester :hi")
            srv.send(":dave!d@h PRIVMSG tester :hi")
            got["msg"] = srv.expect("PRIVMSG dave :Join me on")
            srv.expect("QUIT")

        def oftc(srv):
            welcome(srv, "tester2")
            got["invite"] = srv.expect("INVITE ")
            srv.expect("QUIT")

        self.two_networks(libera, oftc, "carol = libera:carol, oftc:cpollitt")
        self.turn(lambda: self.chat.account("libera").state == "connected" and
                  self.chat.account("oftc").state == "connected" and
                  "libera/dave" in self.chat.account("libera").convs)
        # carol is cpollitt over there: an ordinary INVITE to that nick
        self.assertEqual(self.verb("invite-send", "oftc", "libera/carol", "#club"),
                         (0, b"invited carol (cpollitt) to #club\n"))
        # dave is not in the table: the link goes to him in the chat he is in
        self.assertEqual(self.verb("invite-send", "oftc", "libera/dave", "#club"),
                         (0, b"invitation sent to dave\n"))
        self.turn(lambda: "msg" in got and "invite" in got)
        self.assertEqual(got["invite"], "INVITE cpollitt #club")
        self.assertEqual(got["msg"], "PRIVMSG dave :Join me on #club on OFTC: irc://127.0.0.1:%d/#club"
                         % self.srv2.port)
        self.verb("disconnect", "libera")
        self.verb("disconnect", "oftc")
        self.chat.close()


class TestOutOfOffice(IrcBase):
    """[chat] ooo_away = yes: tvmail's out-of-office reply shows as the IRC away message."""

    def stub_tvmail(self, state):
        """A tvmail-backend that answers `vacation status` with the file `state`."""
        d = self.tmp / "bin"
        d.mkdir(exist_ok=True)
        stub = d / "tvmail-backend"
        stub.write_text('#!/bin/sh\n[ "$1 $2" = "vacation status" ] && cat %s\n' % state)
        stub.chmod(0o755)
        path = os.environ["PATH"]
        os.environ["PATH"] = str(d) + os.pathsep + path
        self.addCleanup(os.environ.__setitem__, "PATH", path)
        return stub

    def test_away_follows_the_out_of_office_reply(self):
        got = {}

        def script(srv):
            welcome(srv)
            got["on"] = srv.expect("AWAY :")
            got["off"] = srv.expect("AWAY")
            srv.expect("QUIT")

        srv = FakeIrc(script)
        self.addCleanup(srv.close)
        self.srv = srv
        self.conf("""\
            [chat]
            ooo_away = yes
            [account:libera]
            protocol = irc
            display  = FakeNet
            host     = 127.0.0.1
            port     = %d
            tls      = false
            nick     = tester
            """ % srv.port)
        state = self.tmp / "vacation.txt"
        state.write_text("state: on\nsubject: Away\ndays: 7\nstart: \nend: \n\nBack on the\n 3rd, thanks.\n")
        self.stub_tvmail(state)
        self.chat = cb._chat()
        self.acct = self.chat.by_id["libera"]
        self.chat.OOO_FIRST, self.chat.OOO_POLL = 0.1, 0.5
        self.verb("connect", "libera")
        self.chat.ooo_start()
        self.turn(lambda: "on" in got)
        self.assertEqual(got["on"], "AWAY :Out of office: Back on the 3rd, thanks.")
        self.turn(lambda: any("away set on 1 IRC account" in f[1] for f in self.events("notice")))
        # the reply is switched off (or its dates are past): we are back
        state.write_text("state: off\nsubject: Away\n")
        self.turn(lambda: "off" in got)
        self.assertEqual(got["off"], "AWAY")
        self.verb("disconnect", "libera")
        self.chat.close()

    def test_without_the_option_nothing_is_asked(self):
        srv = FakeIrc(lambda s: (welcome(s), s.expect("QUIT")))
        self.addCleanup(srv.close)
        self.srv = srv
        self.conf("""\
            [account:libera]
            protocol = irc
            host     = 127.0.0.1
            port     = %d
            tls      = false
            nick     = tester
            """ % srv.port)
        self.chat = cb._chat()
        self.assertFalse(self.chat.ooo_away)
        self.chat.ooo_start()
        self.assertFalse(self.chat._ooo_timer)

    def test_dates_and_states(self):
        v = self.tmp / "v.txt"
        tvmail = str(self.stub_tvmail(v))
        v.write_text("state: on\nstart: 2999-01-01\nend: \n\nlater\n")
        self.assertIsNone(cb.ooo_wanted(tvmail))                  # not yet
        v.write_text("state: on\nstart: \nend: 2000-01-01\n\nlate\n")
        self.assertIsNone(cb.ooo_wanted(tvmail))                  # over
        v.write_text("state: on\nstart: 2000-01-01\nend: 2999-01-01\n\nhere\n")
        self.assertEqual(cb.ooo_wanted(tvmail), "Out of office: here")
        v.write_text("state: on\n\n")
        self.assertEqual(cb.ooo_wanted(tvmail), "Out of office: Out of office")
        v.write_text("state: none\n")
        self.assertIsNone(cb.ooo_wanted(tvmail))


class TestShare(IrcBase):
    """tvmail asks to share a message in chat: queued, and said only when the user agrees."""

    def setup_share(self, script, share_to="libera:#tvmail"):
        srv = FakeIrc(script)
        self.addCleanup(srv.close)
        self.srv = srv
        self.conf("""\
            [chat]
            %s
            [account:libera]
            protocol = irc
            display  = FakeNet
            host     = 127.0.0.1
            port     = %d
            tls      = false
            nick     = tester
            """ % (("share_to = " + share_to) if share_to else "", srv.port))
        self.reqs = self.tmp / "requests"
        self.reqs.mkdir()
        os.environ["TVCHAT_REQUESTS"] = str(self.reqs)
        self.addCleanup(os.environ.pop, "TVCHAT_REQUESTS", None)
        self.chat = cb._chat()
        self.acct = self.chat.by_id["libera"]

    def ask(self, line, age=0):
        """Leave a request as tvmail does, and let the backend read it."""
        f = self.reqs / ("1-1-%d.req" % len(list(self.reqs.iterdir())))
        f.write_text(line + "\n")
        if age:
            old = time.time() - age
            os.utime(str(f), (old, old))
        self.chat._requests_once()

    def test_clean_text(self):
        self.assertEqual(cb.Chat.share_clean("a\r\nQUIT :bye\x07 b\t c"), "a QUIT :bye b c")
        self.assertEqual(len(cb.Chat.share_clean("x" * 900)), 400)

    def test_queued_edited_and_said(self):
        got = {}

        def script(srv):
            welcome(srv)
            got["join"] = srv.expect("JOIN #tvmail")
            srv.send(":tester!u@h JOIN #tvmail")
            got["said"] = srv.expect("PRIVMSG #tvmail :")
            got["slash"] = srv.expect("PRIVMSG #tvmail :")
            srv.expect("QUIT")

        self.setup_share(script)
        self.ask("share tvmail: Bob - hello\x07 world\x01 and more")
        self.assertEqual([f[1:] for f in self.events("share")], [("1", "tvmail: Bob - hello world and more")])
        self.assertEqual(self.verb("shares"), (0, b"1\ttvmail: Bob - hello world and more\t#tvmail on FakeNet\n"))
        # nothing is said until the user agrees - and the channel needs a connection
        self.assertEqual(self.verb("share-send", "1", "x")[0], 1)
        self.verb("connect", "libera")
        self.turn(lambda: self.acct.state == "connected")
        self.assertEqual(self.verb("share-send", "1", "tvmail: Bob - hello (edited)"),
                         (0, b"#tvmail on FakeNet\n"))
        self.assertEqual(self.verb("shares"), (0, b""))              # spent
        self.assertEqual(self.verb("share-send", "1", "again")[0], 1)
        # a text that starts with / is said, not run
        self.ask("share /quit now")
        self.assertEqual(self.verb("share-send", "2", "/quit now")[0], 0)
        self.turn(lambda: "slash" in got)
        self.assertEqual(got["said"], "PRIVMSG #tvmail :tvmail: Bob - hello (edited)")
        self.assertEqual(got["slash"], "PRIVMSG #tvmail :/quit now")
        self.verb("disconnect", "libera")
        self.chat.close()

    def test_discard_and_old_requests(self):
        self.setup_share(lambda srv: None)
        self.ask("share one")
        self.assertEqual(self.verb("share-drop", "1"), (0, b""))
        self.assertEqual(self.verb("shares"), (0, b""))
        self.ask("share too late", age=3600)
        self.assertEqual(self.verb("shares"), (0, b""))
        self.assertTrue(any("it was left 60 minutes ago" in f[1] for f in self.events("notice")))
        self.assertEqual(list(self.reqs.iterdir()), [])               # the files are gone either way

    def test_without_share_to_it_says_how_to_set_it(self):
        self.setup_share(lambda srv: None, share_to="")
        self.ask("share hello")
        self.assertEqual(self.events("share"), [])
        self.assertTrue(any("set [chat] share_to = ACCOUNT:#channel" in f[1] for f in self.events("notice")))


class TestChannelRole(IrcBase):
    """tvchat knows when we are an operator (or voiced) in a channel."""

    def test_role_follows_names_and_modes(self):
        def script(srv):
            welcome(srv)
            srv.expect("JOIN #Test")
            srv.send(":tester!u@h JOIN #Test")
            srv.send(":srv 353 tester = #Test :@tester alice")
            srv.send(":srv 366 tester #Test :End of /NAMES list.")
            srv.send(":alice!a@h MODE #Test -o tester")
            srv.expect("NAMES #Test")
            srv.send(":srv 353 tester = #Test :+tester alice")
            srv.send(":srv 366 tester #Test :End of /NAMES list.")
            srv.expect("QUIT")

        self.setup_irc(script)
        self.verb("connect", "libera")
        roles = lambda: [f[1:] for f in self.events("role")]
        self.turn(lambda: ("libera/#test", "op") in roles())
        self.assertEqual(self.verb("roles"), (0, b"libera/#test\top\n"))
        # -o: the role is asked again and comes back as voice
        self.turn(lambda: ("libera/#test", "voice") in roles())
        self.assertEqual(self.verb("roles"), (0, b"libera/#test\tvoice\n"))
        self.verb("disconnect", "libera")
        self.chat.close()


class TestBotOnAPublicNetwork(IrcBase):
    """bot = yes: joins only when its administrators invite it, says what it is, and does not
    come back where it was kicked."""

    def test_invitations_labels_and_kicks(self):
        got = {}

        def script(srv):
            srv.expect("CAP LS 302")
            srv.send(":srv CAP * LS :")
            srv.expect("CAP END")
            got["user"] = srv.expect("USER ")
            srv.send(":srv 001 tester :Welcome")
            srv.send(":srv 005 tester CASEMAPPING=rfc1459 CHANTYPES=# PREFIX=(ov)@+ :are supported")
            # an administrator's invitation, vouched for by the account tag: joined, greeted once
            srv.send("@account=Chris-Pollitt :chris!c@h INVITE tester #tvmail-test")
            got["join"] = srv.expect("JOIN #tvmail-test")
            srv.send(":tester!u@h JOIN #tvmail-test")
            got["hello"] = srv.expect("PRIVMSG #tvmail-test :")
            # a stranger's: not joined, told why
            srv.send("@account=mallory :mal!m@h INVITE tester #nope")
            got["sorry"] = srv.expect("NOTICE mal :")
            # no account tag: the server is asked who they are
            srv.send(":bob!b@h INVITE tester #viawhois")
            srv.expect("WHOIS bob")
            srv.send(":srv 330 tester bob Chris-Pollitt :is logged in as")
            srv.send(":srv 318 tester bob :End of /WHOIS list.")
            got["whois"] = srv.expect("JOIN #viawhois")
            srv.send(":eve!e@h INVITE tester #anon")
            srv.expect("WHOIS eve")
            srv.send(":srv 318 tester eve :End of /WHOIS list.")
            got["anon"] = srv.expect("NOTICE eve :")
            # asked who it is
            srv.send(":x!x@h PRIVMSG tester :\x01VERSION\x01")
            got["version"] = srv.expect("NOTICE x :")
            # kicked: gone for good
            srv.send(":chris!c@h KICK #tvmail-test tester :bye")
            srv.expect("QUIT")

        self.setup_irc(script, """bot = yes
            contact = chris-pollitt
            invite_from = chris-pollitt""")
        self.verb("connect", "libera")
        self.turn(lambda: "version" in got)
        self.assertIn("tester - a bot, run by chris-pollitt", got["user"])
        self.assertTrue(got["hello"].partition(" :")[2].startswith("Hello! I'm tester, a bot run by chris-pollitt."),
                        got["hello"])
        self.assertIn("I join a channel only when my administrators invite me (chris-pollitt)", got["sorry"])
        self.assertNotIn("JOIN #nope", self.srv.got)
        self.assertIn("I join a channel only when my administrators invite me", got["anon"])
        self.assertNotIn("JOIN #anon", self.srv.got)
        self.assertIn("a bot (tvchat", got["version"])
        self.assertIn("run by chris-pollitt", got["version"])
        ad = self.chat.adapter(self.acct)
        self.turn(lambda: "libera/#tvmail-test" not in self.acct.convs)
        self.assertNotIn("#tvmail-test", ad.channels)          # not rejoined at the next connect
        self.verb("disconnect", "libera")
        self.chat.close()

    def test_policy_defaults(self):
        self.setup_irc(lambda srv: None)
        ad = self.chat.adapter(self.acct)
        self.assertEqual(ad.invite_policy(), "no")                       # a session: a note
        self.chat.daemon = True
        self.assertEqual(ad.invite_policy(), "any")                      # the LAN's Floyd
        ad.bot = True
        self.assertEqual(ad.invite_policy(), "ops")                      # a bot account
        self.acct.opts["join_invites"] = "yes"
        self.assertEqual(ad.invite_policy(), "any")
        self.acct.opts["join_invites"] = "ops"
        ad.bot = False
        self.assertEqual(ad.invite_policy(), "ops")


class TestIrcHardening(IrcBase):
    def test_a_missing_cafile_is_said_in_words_and_tilde_is_home(self):
        srv = FakeIrc(lambda s: None)
        self.addCleanup(srv.close)
        self.srv = srv
        self.conf("[account:libera]\nprotocol = irc\nhost = 127.0.0.1\nport = %d\ntls = true\n"
                  "verify = true\ncafile = ~/missing.pem\nnick = t\n" % srv.port)
        self.chat = cb._chat()
        acct = self.chat.by_id["libera"]
        self.assertEqual(self.chat.adapter(acct).cafile, str(self.home / "missing.pem"))   # ~ is expanded
        self.chat.connect(acct)
        self.turn(lambda: acct.state == "error")
        self.assertIn("cafile %s does not exist on this machine" % (self.home / "missing.pem"), acct.detail)
        self.assertNotIn("Errno", acct.detail)

    """IRC hardening: keepalive, reconnect with backoff, flood control, typed
    /commands, server-side history (chathistory)."""

    def adapter(self, **attrs):
        ad = self.chat.adapter(self.acct)
        for k, v in attrs.items():
            setattr(ad, k, v)
        return ad

    def test_reconnect_rejoins_open_channels(self):
        def script(srv):
            welcome(srv)
            srv.expect("JOIN #Test")
            srv.send(":tester!u@h JOIN #Test")
            srv.expect("JOIN #more")
            srv.send(":tester!u@h JOIN #more")
            srv.hang_up()
            srv.reaccept()
            welcome(srv)
            srv.expect("JOIN #Test")
            srv.expect("JOIN #more")
            srv.send(":tester!u@h JOIN #Test")
            srv.send(":alice!a@h PRIVMSG #Test :welcome back")
            srv.expect("QUIT")

        self.setup_irc(script)
        ad = self.adapter(RETRY_FIRST=0.2)
        self.verb("connect", "libera")
        self.turn(lambda: self.acct.state == "connected")
        self.verb("join", "libera", "#more")
        self.turn(lambda: any(m[6] == "welcome back" for m in self.msgs()))
        states = [(f[2], f[3]) for f in self.events("state")]
        lost = states.index(("connecting", "connection closed by the server - "
                                           "reconnecting in 0.2 s"))
        self.assertEqual(states[-1][0], "connected")
        self.assertNotIn("disconnected", [s for s, _ in states[lost:]])
        texts = [m[6] for m in self.msgs() if m[1] == "libera/*"]
        self.assertIn("reconnected", texts)
        self.assertFalse(ad.auto)
        self.chat.close()
        self.srv.thread.join(5)
        self.assertIsNone(self.srv.error)

    def test_backoff_doubles_up_to_five_minutes(self):
        self.setup_irc(lambda srv: None)
        ad = self.adapter()
        delays = []
        ad.loop.call_later = lambda d, fn: delays.append(d) or [0, 0, fn, True]
        for _ in range(10):
            ad._retry("gone")
        self.assertEqual(delays, [2, 4, 8, 16, 32, 64, 128, 256, 300, 300])
        self.assertEqual((self.acct.state, self.acct.detail),
                         ("connecting", "gone - reconnecting in 300 s"))
        # a connection that had been up a while starts the count again
        ad.phase, ad.t_welcome = "up", ad.loop.clock() - 61
        ad._lost("dropped")
        self.assertEqual(delays[-1], 2)
        # Disconnect stops it
        self.verb("disconnect", "libera")
        self.assertEqual((self.acct.state, ad.auto, ad.attempt), ("disconnected", False, 0))

    def test_keepalive_pings_then_gives_up(self):
        stage = {}

        def script(srv):
            welcome(srv)
            srv.expect("JOIN #Test")
            srv.expect("PING :tvchat")       # quiet for PING_IDLE
            stage["t1"] = time.time()
            srv.send(":srv PONG srv :tvchat")
            srv.expect("PING :tvchat")       # quiet again, and no answer
            stage["t2"] = time.time()

        self.setup_irc(script)
        self.adapter(PING_IDLE=0.3, PING_GRACE=0.3, RETRY_FIRST=60)
        self.verb("connect", "libera")
        self.turn(lambda: self.acct.state == "connected")
        self.turn(lambda: "no reply from the server" in self.acct.detail)
        self.assertEqual(self.acct.state, "connecting")      # and will retry
        self.assertGreaterEqual(stage["t2"] - stage["t1"], 0.25)
        self.chat.close()

    def test_flood_control_paces_lines_and_pong_jumps_the_queue(self):
        stage = {}

        def script(srv):
            welcome(srv)
            srv.expect("JOIN #Test")
            srv.send(":tester!u@h JOIN #Test")
            srv.expect("PRIVMSG #Test :line 0")
            srv.send("PING :x")
            srv.expect("PONG :x")
            srv.expect("PRIVMSG #Test :line 4")
            stage["t"] = time.time()
            srv.expect("QUIT")

        self.setup_irc(script)
        ad = self.adapter(BURST=2, RATE=0.3)
        self.verb("connect", "libera")
        self.turn(lambda: "libera/#test" in self.acct.convs)
        t0 = time.time()
        st, _ = self.verb("send", "libera/#test", "\n".join("line %d" % i for i in range(5)))
        self.assertEqual(st, 0)
        self.assertEqual(len(ad.outq), 4)          # the JOIN took one of the two
        self.turn(lambda: "t" in stage)
        self.assertGreaterEqual(stage["t"] - t0, 1.0)   # 4 more, 0.3 s apart
        got = self.srv.got
        self.assertLess(got.index("PONG :x"), got.index("PRIVMSG #Test :line 1"))
        self.assertEqual([g for g in got if g.startswith("PRIVMSG")],
                         ["PRIVMSG #Test :line %d" % i for i in range(5)])
        self.chat.close()

    def test_typed_commands(self):
        stage = {}

        def script(srv):
            welcome(srv)
            srv.expect("JOIN #Test")
            srv.send(":tester!u@h JOIN #Test")
            srv.expect("PRIVMSG #Test :\x01ACTION waves\x01")
            srv.expect("PRIVMSG Dave :hi there")
            srv.expect("JOIN #more secret")
            srv.expect("PART #more :bye now")
            srv.expect("TOPIC #Test :new topic")
            srv.expect("NICK tester2")
            srv.expect("MODE #Test +m")
            srv.expect("PRIVMSG #Test :/not a command")
            srv.expect("JOIN #x")
            stage["done"] = True
            srv.expect("QUIT")

        self.setup_irc(script)
        self.adapter(BURST=50)
        self.verb("connect", "libera")
        self.turn(lambda: "libera/#test" in self.acct.convs)
        say = lambda conv, text: self.verb("send", conv, text)
        # refused: nothing at all is sent, the text can be fixed and resent
        st, body = say("libera/#test", "fine line\n/bogus thing")
        self.assertEqual(st, 1)
        self.assertIn(b"unknown command /bogus", body)
        self.assertEqual(say("libera/#test", "/me")[0], 1)
        self.assertEqual(say("libera/#test", "/msg Dave")[0], 1)
        self.assertEqual(say("libera/#test", "/join nochannel")[0], 1)
        self.assertEqual(say("libera/*", "hello")[0], 1)
        self.assertEqual(say("libera/*", "/me waves")[0], 1)

        self.assertEqual(say("libera/#test", "/me waves")[0], 0)
        action = [m for m in self.msgs() if m[6] == "waves"][0]
        self.assertEqual((action[1], action[5]), ("libera/#test", "sa"))
        self.assertEqual(say("libera/#test", "/msg Dave hi there")[0], 0)
        self.assertIn("libera/dave", self.acct.convs)
        self.assertTrue(any(m[1] == "libera/dave" and m[6] == "hi there" for m in self.msgs()))
        self.assertEqual(say("libera/#test", "/join #more secret"), (0, b"\n"))
        self.assertIn("libera/#more", self.acct.convs)
        self.assertEqual(say("libera/#test", "/part #more bye now")[0], 0)
        self.assertNotIn("libera/#more", self.acct.convs)
        self.assertEqual(say("libera/#test", "/topic new topic")[0], 0)
        self.assertEqual(say("libera/#test", "/topic")[0], 0)
        self.assertTrue(any(m[6] == "topic: (none)" for m in self.msgs()))
        self.assertEqual(say("libera/#test", "/NICK tester2")[0], 0)
        self.assertEqual(say("libera/#test", "/quote MODE #Test +m")[0], 0)
        self.assertEqual(say("libera/#test", "//not a command")[0], 0)
        self.assertEqual(say("libera/*", "/join #x")[0], 0)     # the server window takes these
        self.turn(lambda: "done" in stage)
        self.assertFalse(any("fine line" in g or "bogus" in g for g in self.srv.got))
        self.chat.close()

    def _invited(self, extra="", daemon=False):
        stage = {}

        def script(srv):
            welcome(srv)
            srv.expect("JOIN #Test")
            srv.send(":tester!u@h JOIN #Test")
            srv.send(":Dave!u@h INVITE tester #new")
            srv.send(":Dave!u@h INVITE someoneelse #other")
            srv.send(":srv PING :sync")
            srv.expect("PONG")
            stage["done"] = True

        self.setup_irc(script, extra)
        self.chat.daemon = daemon
        self.adapter(BURST=50)
        self.verb("connect", "libera")
        self.turn(lambda: "done" in stage)
        return self.srv.got

    def test_invite_only_noted_in_a_session(self):
        got = self._invited()
        self.assertNotIn("JOIN #new", got)
        self.assertIn("/join #new to accept", "\n".join(m[-1] for m in self.msgs() if "libera/*" in m))

    def test_invite_joined_by_option(self):
        self.assertIn("JOIN #new", self._invited("join_invites = yes"))
        self.assertNotIn("JOIN #other", self.srv.got)

    def test_invite_joined_in_the_daemon(self):
        self.assertIn("JOIN #new", self._invited(daemon=True))

    def test_shared_commands_on_irc(self):
        stage = {}

        def script(srv):
            welcome(srv)
            srv.expect("JOIN #Test")
            srv.send(":tester!u@h JOIN #Test")
            srv.expect("PRIVMSG Eve :hello eve")
            srv.expect("INVITE Dave #Test")
            srv.expect("INVITE Dave #other")
            srv.expect("WHOIS Dave")
            srv.expect("AWAY :gone fishing")
            srv.expect("AWAY")
            ln = srv.expect("PING :tvping")                      # /ping: the server's PONG
            srv.send(":srv PONG srv :tvping" + ln.split(":tvping")[1])
            ln = srv.expect("PRIVMSG Dave :\x01PING ")            # /ping NICK: his CTCP reply
            srv.send(":Dave!u@h NOTICE tester :\x01PING %s\x01" % ln.split("PING ")[1].strip("\x01"))
            srv.expect("PART #Test")
            srv.expect("QUIT :bye now")
            stage["done"] = True

        self.setup_irc(script)
        self.adapter(BURST=50)
        self.verb("connect", "libera")
        self.turn(lambda: "libera/#test" in self.acct.convs)
        say = lambda conv, text: self.verb("send", conv, text)
        notes = lambda conv: [m.text for m in self.acct.convs[conv].history if "n" in m.flags]
        # /help: this protocol's commands
        self.assertEqual(say("libera/#test", "/help")[0], 0)
        listed = "\n".join(notes("libera/#test"))
        self.assertIn("IRC commands", listed)
        for want in ("/join #CHANNEL [KEY]", "/ping [NICK]", "/whois NICK", "/quote RAW LINE", "/away [REASON]"):
            self.assertIn(want, listed)
        self.assertNotIn("/name NEW NAME", listed)                # a Delta Chat command
        self.assertEqual(say("libera/#test", "/help leave")[0], 0)
        self.assertIn("    also: /part, /close", notes("libera/#test"))
        st, body = say("libera/#test", "/name x")
        self.assertEqual((st, b"/name is not available for IRC" in body), (1, True))
        # /status
        say("libera/*", "/status")
        text = "\n".join(notes("libera/*"))
        self.assertIn("(irc): connected", text)
        self.assertIn("you are tester on 127.0.0.1:", text)
        # /whoami
        say("libera/*", "/whoami")
        text = "\n".join(notes("libera/*"))
        self.assertIn("you are tester on 127.0.0.1", text)
        self.assertIn("not logged in to services", text)
        say("libera/#test", "/whoami")
        self.assertIn("in #Test you are", "\n".join(notes("libera/#test")))
        self.acct and setattr(self.chat.adapter(self.acct), "services_account", "chrisp")
        say("libera/*", "/whoami")
        self.assertIn("logged in to services as chrisp", "\n".join(notes("libera/*")))
        # a query opened with and without text
        self.assertEqual(say("libera/#test", "/query Eve hello eve")[0], 0)
        self.assertEqual(say("libera/#test", "/q Frank")[0], 0)
        self.assertIn("libera/frank", self.acct.convs)
        self.assertEqual(say("libera/#test", "/invite Dave")[0], 0)              # to the channel shown
        self.assertEqual(say("libera/#test", "/invite Dave #other")[0], 0)
        self.assertEqual(say("libera/*", "/invite Dave")[0], 1)                   # which channel?
        self.assertEqual(say("libera/#test", "/whois Dave")[0], 0)
        self.assertEqual(say("libera/#test", "/away gone fishing")[0], 0)
        self.assertEqual(say("libera/#test", "/away")[0], 0)
        self.assertEqual(say("libera/#test", "/names")[0], 0)
        self.assertEqual(say("libera/#test", "/ping")[0], 0)
        self.turn(lambda: any(n.startswith("ping reply from") for n in notes("libera/#test")))
        self.assertEqual(say("libera/#test", "/ping Dave")[0], 0)
        self.turn(lambda: any(n.startswith("ping reply from Dave:") for n in notes("libera/#test")))
        # /leave is /part; /clear; /quit
        self.assertEqual(say("libera/#test", "/clear")[0], 0)
        self.assertEqual(len(self.acct.convs["libera/#test"].history), 0)
        self.assertEqual(say("libera/#test", "/close")[0], 0)
        self.assertNotIn("libera/#test", self.acct.convs)
        self.assertEqual(say("libera/*", "/quit bye now")[0], 0)
        self.turn(lambda: "done" in stage)
        self.assertEqual(self.acct.state, "disconnected")

    def test_chathistory_on_join_and_before(self):
        stage = {}

        def script(srv):
            srv.expect("CAP LS 302")
            srv.send(":srv CAP * LS :batch server-time message-tags draft/chathistory")
            srv.expect("CAP REQ")
            srv.send(":srv CAP * ACK :batch server-time message-tags draft/chathistory")
            srv.expect("CAP END")
            srv.expect("USER ")
            srv.send(":srv 001 tester :hi")
            srv.send(":srv 005 tester CHATHISTORY=100 :are supported")
            srv.expect("JOIN #Test")
            srv.send("@time=2026-10-22T10:00:05.000Z :tester!u@h JOIN #Test")
            stage["latest"] = srv.expect("CHATHISTORY LATEST")
            srv.send("BATCH +h1 chathistory #Test")
            srv.send("@batch=h1;time=2026-10-22T08:00:00.000Z;msgid=old1 "
                     ":bob!b@h PRIVMSG #Test :earliest")
            srv.send("@batch=h1;time=2026-10-22T09:00:00.000Z;msgid=old2 "
                     ":alice!a@h PRIVMSG #Test :earlier")
            srv.send("BATCH -h1")
            stage["before"] = srv.expect("CHATHISTORY BEFORE")
            srv.send("BATCH +h2 chathistory #Test")
            srv.send("@batch=h2;time=2026-10-21T08:00:00.000Z;msgid=old0 "
                     ":bob!b@h PRIVMSG #Test :yesterday")
            srv.send("BATCH -h2")
            stage["before2"] = srv.expect("CHATHISTORY BEFORE")
            srv.send("BATCH +h3 chathistory #Test")
            srv.send("BATCH -h3")
            srv.expect("QUIT")

        self.setup_irc(script)
        self.verb("connect", "libera")
        hist = lambda: ("history", "libera/#test", "latest") in self.events()
        self.turn(hist)
        self.assertEqual(stage["latest"], "CHATHISTORY LATEST #Test * 50")
        texts = lambda *a: [ln.split("\t")[6] for ln in
                            self.verb("history", "libera/#test", *a)[1].decode().splitlines()]
        self.assertEqual(texts(), ["earliest", "earlier", "tester has joined #Test"])
        # the past is neither unread nor a live msg event
        self.assertEqual(self.acct.convs["libera/#test"].unread, 0)
        self.assertFalse(any(m[0] in ("old1", "old2") for m in self.msgs()))

        before = lambda: [f for f in self.events("history") if f[2] == "before"]
        st, body = self.verb("history", "libera/#test", "--before", "old1")
        self.assertEqual((st, body), (1, b"asking the server for older messages...\n"))
        self.assertEqual(self.verb("history", "libera/#test", "--before", "old1")[0], 1)
        self.turn(lambda: len(before()) == 1)
        self.assertEqual(stage["before"], "CHATHISTORY BEFORE #Test msgid=old1 50")
        self.assertEqual(texts("--before", "old1"), ["yesterday"])
        self.assertEqual(self.verb("history", "libera/#test", "--before", "old0")[0], 1)
        self.turn(lambda: len(before()) == 2)
        # the server had nothing older: from now on, simply nothing
        self.assertEqual(self.verb("history", "libera/#test", "--before", "old0"), (0, b""))
        self.assertEqual(sum(g.startswith("CHATHISTORY BEFORE") for g in self.srv.got), 2)

        # a refused request does not hang: FAIL ends it
        ad = self.adapter()
        conv = self.acct.convs["libera/#test"]
        conv.history_done = False
        ad.fetching[conv.id] = "before"
        ad.handle(cb.parse_line("FAIL CHATHISTORY MESSAGE_ERROR BEFORE #Test :boom"))
        self.assertEqual(ad.fetching, {})
        self.assertTrue(conv.history_done)
        self.assertEqual(len(before()), 3)
        self.chat.close()


# --------------------------------------------------------------------------
# talk(1): a scripted fake of chat-setup's talkd (its control protocol, and
# the ATTACH connection a CONNECT asks for), then the real talkd when a
# chrispollitt/POSIX checkout with chat-setup/talkd/talkd.py is at hand.
# --------------------------------------------------------------------------
class FakeTalkd:
    def __init__(self, script):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.script = script
        self.got = []
        self.error = None
        self.conn = None
        self.buf = b""
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            self.sock.settimeout(10)
            self.conn, _ = self.sock.accept()
            self.conn.settimeout(10)
            self.script(self)
        except Exception as e:
            self.error = e

    def send(self, text):
        self.conn.sendall(text.encode("utf-8") + b"\n")

    def expect(self, prefix):
        while True:
            while b"\n" not in self.buf:
                data = self.conn.recv(4096)
                if not data:
                    raise AssertionError("tvchat hung up waiting for %r" % prefix)
                self.buf += data
            ln, self.buf = self.buf.split(b"\n", 1)
            ln = ln.decode("utf-8")
            self.got.append(ln)
            if ln.startswith(prefix):
                return ln

    def data(self, token):
        """The session connection tvchat opens for CONNECT ... TOKEN: (socket,
        what came after the ATTACH line)."""
        s, _ = self.sock.accept()
        s.settimeout(10)
        buf = b""
        while b"\n" not in buf:
            buf += s.recv(4096)
        first, rest = buf.split(b"\n", 1)
        if first != b"ATTACH " + token.encode():
            raise AssertionError("wanted ATTACH %s, got %r" % (token, first))
        return s, rest

    def close(self):
        for s in (self.conn, self.sock):
            try:
                s.close()
            except Exception:
                pass


def recv_some(s, want, secs=5):
    s.settimeout(secs)
    got = b""
    while want not in got:
        data = s.recv(4096)
        if not data:
            break
        got += data
    return got


class TalkBase(IrcBase):
    def setup_talk(self, script, extra="", port=None):
        srv = FakeTalkd(script)
        self.addCleanup(srv.close)
        self.conf("""\
            [account:talk]
            protocol = talk
            display  = cpbee talk
            host     = 127.0.0.1
            port     = %d
            user     = chris
            %s
            """ % (port or srv.port, extra))
        self.chat = cb._chat()
        self.acct = self.chat.by_id["talk"]
        self.srv = srv
        return srv

    def hello(self, srv):
        srv.expect("HELLO chris")
        srv.send("OK chris@cpbee")

    def talks(self, conv_id):
        return [f[2:] for f in self.events("talk") if f[1] == conv_id]


class TestTalk(TalkBase):
    def test_config_and_wire(self):
        self.conf("""\
            [account:t]
            protocol = talk
            host = cpbee
            [account:i]
            protocol = irc
            host = x
            """)
        chat = cb._chat()
        self.assertEqual(chat.by_id["t"].wire(), "t\ttalk\tdisconnected\tt\tclassic\n")
        self.assertEqual(chat.by_id["i"].wire(), "i\tirc\tdisconnected\ti\n")   # as before
        cb._CHAT = None
        self.conf("[account:t]\nprotocol = talk\ntalk_view = irc\n")
        self.assertEqual(cb._chat().by_id["t"].view, "irc")
        cb._CHAT = None
        self.conf("[account:t]\nprotocol = talk\ntalk_view = split\n")
        with self.assertRaises(cb.UserError) as e:
            cb.load_config()
        self.assertIn("talk_view must be one of classic, irc", str(e.exception))

    def test_editing_a_line(self):
        def run(chars, edit="\x7f\x15\x17"):
            line, done = "", []
            for ch in chars:
                line, d = cb._edit_line(line, ch, *edit)
                if d is not None:
                    done.append(d)
            return line, done
        self.assertEqual(run("hellp\x7fo"), ("hello", []))
        self.assertEqual(run("abc\x08d"), ("abd", []))                 # ^H too
        self.assertEqual(run("one two three\x17"), ("one two ", []))
        self.assertEqual(run("one two  \x17"), ("one ", []))
        self.assertEqual(run("word\x17"), ("", []))
        self.assertEqual(run("gone\x15kept\n"), ("", ["kept"]))
        self.assertEqual(run("a\x07b\x0cc"), ("abc", []))              # bell, ^L: nothing
        self.assertEqual(run("x#y", edit="#@!"), ("y", []))             # their own erase
        self.assertEqual(run("x" * 1000)[1], ["x" * 1000])              # cut at the cap

    def test_calling_out_classic_and_irc_lines(self):
        stage = {}

        def script(srv):
            self.hello(srv)
            call = srv.expect("CALL ")
            stage["call"] = call
            cid = call.split()[1]
            srv.send("STATE %s ringing michelle on pts/1" % cid)
            srv.send("CONNECT %s tok1" % cid)
            s, first = srv.data("tok1")
            # tvchat's three edit characters (in the ATTACH write, or after it)
            stage["edit"] = (first if len(first) >= 3 else first + recv_some(s, b"\x17"))[:3]
            # michelle's talk: ^H as her erase; a line, then half of one
            s.sendall(b"\x08\x15\x17hix\x08 there\nhalf")
            # the keys raw, erase and all (the receiver applies it), then lines
            stage["lines"] = recv_some(s, b"line two\n")
            s.close()                            # she hangs up
            srv.expect("QUIT-never")

        self.setup_talk(script)
        self.verb("connect", "talk")
        self.turn(lambda: self.acct.state == "connected")
        self.assertEqual(self.acct.detail, "chris@cpbee")
        self.assertEqual(self.verb("join", "talk", "michelle"), (0, b"talk/michelle\n"))
        self.turn(lambda: "call" in stage)             # the fake runs on its own thread
        self.assertTrue(stage["call"].endswith(" michelle"))
        self.turn(lambda: ("connected", "") in self.talks("talk/michelle"))
        self.assertIn(("ringing", "ringing michelle on pts/1"), self.talks("talk/michelle"))
        self.turn(lambda: "edit" in stage)
        self.assertEqual(stage["edit"], b"\x7f\x15\x17")       # ours, first
        self.turn(lambda: ("typing", "talk/michelle", "half") in self.events("typing"))
        theirs = [m for m in self.msgs() if m[1] == "talk/michelle" and m[5] == ""]
        self.assertEqual([(m[3], m[6]) for m in theirs], [("michelle", "hi there")])
        self.assertEqual(self.acct.convs["talk/michelle"].unread, 1)
        # classic: keystrokes go as typed; a finished line is ours in the log
        self.assertEqual(self.verb("keys", "talk/michelle", "ab\x7fc\n"), (0, b""))
        # irc mode: whole lines
        self.assertEqual(self.verb("send", "talk/michelle", "line one\nline two")[0], 0)
        self.turn(lambda: "lines" in stage)
        self.assertEqual(stage["lines"], b"ab\x7fc\nline one\nline two\n")
        mine = [m[6] for m in self.msgs() if m[1] == "talk/michelle" and m[5] == "s"]
        self.assertEqual(mine, ["ac", "line one", "line two"])
        # she hangs up: her unfinished line still counts
        self.turn(lambda: self.acct.convs["talk/michelle"].state == "ended")
        self.assertEqual(self.talks("talk/michelle")[-1], ("ended", "michelle hung up"))
        self.assertIn("half", [m[6] for m in self.msgs() if m[3] == "michelle"])
        self.assertEqual(self.events("typing")[-1], ("typing", "talk/michelle", ""))
        st, body = self.verb("send", "talk/michelle", "anyone?")
        self.assertEqual(st, 1)
        self.assertIn(b"not talking to michelle now", body)
        self.chat.close()

    def test_answering_and_declining(self):
        stage = {}

        def script(srv):
            self.hello(srv)
            srv.send("RING t1 alice@cmlaptop")
            srv.expect("ACCEPT t1")
            srv.send("CONNECT t1 tok9")
            s, _ = srv.data("tok9")
            s.sendall(b"\x7f\x15\x17")
            stage["attached"] = True
            srv.expect("HANGUP t1")
            srv.send("GONE t1 hung up")          # talkd says so too: harmless
            srv.send("RING t2 michelle@cpbee")
            stage["declined"] = srv.expect("DECLINE t2")
            srv.expect("QUIT-never")

        self.setup_talk(script)
        self.verb("connect", "talk")
        self.turn(lambda: "talk/alice@cmlaptop" in self.acct.convs)
        conv = self.acct.convs["talk/alice@cmlaptop"]
        self.assertEqual(conv.state, "incoming")
        self.assertEqual(conv.unread, 1)                  # a call is news
        self.assertTrue(any(m[6] == "alice@cmlaptop wants to talk" for m in self.msgs()))
        st, body = self.verb("send", "talk/alice@cmlaptop", "hi")
        self.assertEqual((st, b"answer first" in body), (1, True))
        self.assertEqual(self.verb("answer", "talk/alice@cmlaptop"), (0, b""))
        self.turn(lambda: conv.state == "connected" and "attached" in stage)
        self.assertEqual(self.verb("part", "talk/alice@cmlaptop"), (0, b""))
        self.assertEqual(self.talks("talk/alice@cmlaptop")[-1], ("ended", "you hung up"))
        # a call from talkd's own host is just the user: talk/michelle
        self.turn(lambda: "talk/michelle" in self.acct.convs)
        self.assertEqual(self.verb("part", "talk/michelle"), (0, b""))
        self.turn(lambda: "declined" in stage)
        self.assertEqual(self.acct.convs["talk/michelle"].state, "ended")
        # with no call, part leaves the conversation
        self.assertEqual(self.verb("part", "talk/michelle"), (0, b""))
        self.assertNotIn("talk/michelle", self.acct.convs)
        self.assertEqual(self.verb("answer", "talk/alice@cmlaptop")[0], 1)
        self.chat.close()

    def test_the_central_floyd_answers_a_call_and_says_hello(self):
        got = {}

        def script(srv):
            self.hello(srv)
            srv.send("RING t1 alice@cmlaptop")
            srv.expect("ACCEPT t1")                  # nobody pressed Answer
            srv.send("CONNECT t1 tok7")
            s, _ = srv.data("tok7")
            s.sendall(bytes([0x7f, 0x15, 0x17]))       # their three edit characters
            s.settimeout(5)
            got["hello"] = s.recv(200)
            srv.expect("QUIT-never")

        self.setup_talk(script, "greeting = Hi there, I am a bot")
        self.chat.daemon = True                      # as in floyd-daemon (set before the adapter is made)
        self.chat.adapters.pop("talk", None)
        self.verb("connect", "talk")
        self.turn(lambda: "hello" in got)
        self.assertTrue(got["hello"].endswith(b"Hi there, I am a bot\n"), got["hello"])
        self.chat.close()

    def test_trust_names_the_callers_who_count_as_an_account(self):
        self.setup_talk(lambda srv: self.hello(srv), "trust = Alice@cmlaptop, michelle, carol@*")
        ad = self.chat.adapter(self.acct)
        call = lambda title: types.SimpleNamespace(conv=types.SimpleNamespace(title=title))
        self.assertEqual(ad.contact_of(call("alice@cmlaptop")).account, "alice")
        self.assertEqual(ad.contact_of(call("michelle")).account, "michelle")      # a caller on talkd's own host
        self.assertIsNone(ad.contact_of(call("alice@elsewhere")).account)          # same name, not named host
        self.assertIsNone(ad.contact_of(call("bob@cmlaptop")).account)
        self.assertEqual(ad.contact_of(call("carol@anywhere")).account, "carol")   # user@* : any host
        self.chat.close()

    def test_a_session_does_not_answer_by_itself(self):
        self.setup_talk(lambda srv: (self.hello(srv), srv.send("RING t1 alice@cmlaptop"), srv.expect("QUIT-never")))
        self.verb("connect", "talk")
        self.turn(lambda: "talk/alice@cmlaptop" in self.acct.convs)
        self.assertEqual(self.acct.convs["talk/alice@cmlaptop"].state, "incoming")     # waits for a person
        self.chat.close()

    def test_the_shared_commands_in_talk(self):
        stage = {}

        def script(srv):
            self.hello(srv)
            while "done" not in stage:
                time.sleep(0.05)

        self.setup_talk(script)
        self.verb("connect", "talk")
        self.turn(lambda: self.acct.state == "connected")
        say = lambda text: self.verb("send", "talk/*", text)
        notes = lambda: [m.text for m in self.acct.convs["talk/*"].history if "n" in m.flags]
        self.assertEqual(say("/help")[0], 0)
        listed = "\n".join(notes())
        self.assertIn("talk commands", listed)
        self.assertIn("/join USER[@HOST]", listed)
        self.assertIn("/leave", listed)
        self.assertNotIn("/topic", listed)
        self.assertNotIn("/msg", listed)                         # talk has no messages out of a call
        st, body = say("/topic hi")
        self.assertEqual((st, b"/topic is not available for talk" in body), (1, True))
        st, body = say("/msg bob hi")
        self.assertEqual((st, b"/msg is not available for talk" in body), (1, True))
        self.assertEqual(say("/status")[0], 0)
        text = "\n".join(notes())
        self.assertIn("(talk): connected", text)
        self.assertIn("you are chris, through talkd on 127.0.0.1:", text)
        self.assertIn("calls: none", text)
        self.assertEqual(say("/me waves")[0], 1)                  # needs a conversation
        self.assertEqual(say("/leave")[0], 1)
        self.assertEqual(say("/quit")[0], 0)
        self.assertEqual(self.acct.state, "disconnected")
        stage["done"] = True

    def test_refusals(self):
        def script(srv):
            srv.expect("HELLO chris")
            srv.send("ERR HELLO needs a user name")

        self.setup_talk(script)
        self.assertEqual(self.verb("join", "talk", "michelle")[0], 1)        # not connected
        self.verb("connect", "talk")
        self.turn(lambda: self.acct.state == "error")
        self.assertIn("talkd: HELLO needs a user name", self.acct.detail)

    def test_no_talkd_there(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()                                # nothing listens on it
        self.setup_talk(lambda srv: None, port=port)
        self.verb("connect", "talk")
        self.turn(lambda: self.acct.state == "error")
        self.assertIn("configure-talkd.sh", self.acct.detail)

    def test_keys_and_answer_are_talk_only(self):
        self.conf("[account:i]\nprotocol = irc\nhost = x\n")
        chat = cb._chat()
        chat.ensure_conv(chat.by_id["i"], "#x", "channel")
        self.assertEqual(self.verb("keys", "i/#x", "a")[0], 1)
        self.assertEqual(self.verb("answer", "i/#x")[0], 1)


def _real_talkd():
    """chat-setup's talkd.py: $TALKD_PY, tvcommon's POSIX checkout, or one
    beside TUIs (as on cmlaptop: ~/gh_cp/POSIX)."""
    for p in (os.environ.get("TALKD_PY", ""),
              str(HERE.parent.parent / "tvcommon" / "third_party" / "POSIX" / "chat-setup" / "talkd" / "talkd.py"),
              str(HERE.parent.parent.parent / "POSIX" / "chat-setup" / "talkd" / "talkd.py")):
        if p and os.path.isfile(p):
            return p
    return None


@unittest.skipUnless(_real_talkd(), "no chat-setup/talkd/talkd.py at hand (set $TALKD_PY)")
class TestTalkWithTheRealTalkd(TalkBase):
    def test_two_accounts_call_each_other(self):
        loader = importlib.machinery.SourceFileLoader("talkd", _real_talkd())
        spec = importlib.util.spec_from_loader("talkd", loader)
        td = importlib.util.module_from_spec(spec)
        loader.exec_module(td)
        srv = td.Server("127.0.0.1", 0, tcp_port=0,
                        daemon=td.TalkDaemon(logins=lambda: [], hostname="cpbee"))
        stop = threading.Event()
        th = threading.Thread(target=srv.run, args=(stop.is_set,), daemon=True)
        th.start()
        self.addCleanup(srv.close)
        self.addCleanup(th.join, 3)
        self.addCleanup(stop.set)
        port = srv.tcp.getsockname()[1]
        self.conf("""\
            [account:chris]
            protocol = talk
            host = 127.0.0.1
            port = %d
            user = chris
            [account:michelle]
            protocol = talk
            host = 127.0.0.1
            port = %d
            user = michelle
            talk_view = irc
            """ % (port, port))
        self.chat = cb._chat()
        self.srv = type("S", (), {"error": None, "got": []})()
        c, m = self.chat.by_id["chris"], self.chat.by_id["michelle"]
        self.verb("connect", "chris")
        self.verb("connect", "michelle")
        self.turn(lambda: c.state == m.state == "connected")
        self.assertEqual((c.detail, m.detail), ("chris@cpbee", "michelle@cpbee"))
        self.verb("join", "chris", "michelle")
        self.turn(lambda: "michelle/chris" in m.convs and m.convs["michelle/chris"].state == "incoming")
        self.verb("answer", "michelle/chris")
        self.turn(lambda: c.convs["chris/michelle"].state == m.convs["michelle/chris"].state == "connected")
        self.verb("keys", "chris/michelle", "hellp\x7fo michelle\nhalf")
        self.verb("send", "michelle/chris", "hi chris")
        self.turn(lambda: ("typing", "michelle/chris", "half") in self.events("typing") and
                  any(x[1] == "chris/michelle" and x[6] == "hi chris" for x in self.msgs()))
        self.assertIn(("michelle/chris", "chris", "", "hello michelle"),
                      [(x[1], x[3], x[5], x[6]) for x in self.msgs()])
        self.verb("part", "chris/michelle")
        self.turn(lambda: m.convs["michelle/chris"].state == "ended")
        self.chat.close()


class FakeMail:
    """A mail server on 127.0.0.1 for the invitation tests: plain SMTP (any
    login) that keeps what it is sent in .sent, and a minimal IMAP over .inbox
    - [{"uid", "raw", "deleted"}] - enough for imaplib's login, select, uid
    search / fetch / store and expunge."""

    def __init__(self):
        self.sent = []
        self.inbox = []
        self.filed = []                  # (folder, raw) of what was APPENDed
        self.copied = []                 # (folder, raw) of what was COPYed
        self.sent_folders = []           # folders made by CREATE
        self.lock = threading.Lock()
        self.socks = []
        self.threads = []
        self.stopped = False
        self.smtp_port = self._serve(self._smtp)
        self.imap_port = self._serve(self._imap)

    def _serve(self, handler):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(5)
        srv.settimeout(0.1)              # closing a listening socket does not wake accept()
        self.socks.append(srv)           # (Cygwin), and a thread left blocked there can hang a
                                         # later fork - so the loops end by themselves

        def loop():
            while not self.stopped:
                try:
                    c, _ = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    return
                c.settimeout(None)
                threading.Thread(target=handler, args=(c,), daemon=True).start()
        t = threading.Thread(target=loop, daemon=True)
        t.start()
        self.threads.append(t)
        return srv.getsockname()[1]

    def close(self):
        self.stopped = True
        for t in self.threads:
            t.join(2)
        for s in self.socks:
            s.close()

    def add(self, sender, link, header=True):
        with self.lock:
            uid = len(self.inbox) + 1 + 100
            raw = ("From: %s\r\nTo: me@example.org\r\nSubject: Chat invitation\r\n%s"
                   "\r\ninvites you:\r\n\r\n%s\r\n" % (sender, "X-Tvchat-Invite: 1\r\n" if header else "",
                                                       link)).encode()
            self.inbox.append({"uid": uid, "raw": raw, "deleted": False})
        return uid

    def add_mail(self, raw):
        with self.lock:
            uid = len(self.inbox) + 1 + 100
            self.inbox.append({"uid": uid, "raw": raw.replace("\n", "\r\n").encode(), "deleted": False})
        return uid

    def live(self):
        with self.lock:
            return [m for m in self.inbox if not m["deleted"]]

    def _smtp(self, c):
        f = c.makefile("rwb")

        def say(t):
            f.write((t + "\r\n").encode())
            f.flush()
        say("220 fake")
        try:
            while True:
                ln = f.readline().decode().rstrip("\r\n")
                cmd = ln.split(" ", 1)[0].upper()
                if not ln and not cmd:
                    return
                if cmd in ("EHLO", "HELO"):
                    f.write(b"250-fake\r\n250 AUTH PLAIN LOGIN\r\n")
                    f.flush()
                elif cmd == "AUTH":
                    say("235 ok")
                elif cmd in ("MAIL", "RCPT"):
                    say("250 ok")
                elif cmd == "DATA":
                    say("354 go")
                    data = b""
                    while not data.endswith(b"\r\n.\r\n"):
                        data += f.readline()
                    with self.lock:
                        self.sent.append(data[:-5])
                    say("250 queued")
                elif cmd == "QUIT":
                    say("221 bye")
                    return
                else:
                    say("250 ok")
        except OSError:
            pass
        finally:
            c.close()

    def _imap(self, c):
        f = c.makefile("rwb")

        def w(t):
            f.write(t if isinstance(t, bytes) else t.encode())
            f.flush()
        w("* OK fake imap\r\n")
        try:
            while True:
                ln = f.readline().decode().rstrip("\r\n")
                if not ln:
                    return
                tag, _, rest = ln.partition(" ")
                cmd, _, args = rest.partition(" ")
                cmd = cmd.upper()
                if cmd == "CAPABILITY":
                    w("* CAPABILITY IMAP4rev1\r\n%s OK done\r\n" % tag)
                elif cmd == "SELECT":
                    w("* %d EXISTS\r\n%s OK [READ-WRITE] done\r\n" % (len(self.live()), tag))
                elif cmd == "UID":
                    sub, _, a2 = args.partition(" ")
                    sub = sub.upper()
                    if sub == "SEARCH":
                        if "UNSEEN" in a2:
                            hits = [str(m["uid"]) for m in self.live() if not m.get("seen")]
                        else:
                            hits = [str(m["uid"]) for m in self.live() if b"X-Tvchat-Invite" in m["raw"]]
                        w("* SEARCH %s\r\n%s OK done\r\n" % (" ".join(hits), tag))
                    elif sub == "FETCH":
                        uid = int(a2.split(" ")[0])
                        for m in self.live():
                            if m["uid"] == uid:
                                w(b"* 1 FETCH (UID %d BODY[] {%d}\r\n" % (uid, len(m["raw"])) + m["raw"] + b")\r\n")
                        w("%s OK done\r\n" % tag)
                    elif sub == "STORE":
                        uid = int(a2.split(" ")[0])
                        with self.lock:
                            for m in self.inbox:
                                if m["uid"] == uid:
                                    m["seen" if "Seen" in a2 else "deleted"] = True
                        w("%s OK done\r\n" % tag)
                    elif sub == "COPY":
                        uid, _, dest = a2.partition(" ")
                        with self.lock:
                            for m in self.inbox:
                                if m["uid"] == int(uid):
                                    self.copied.append((dest.strip('"'), m["raw"]))
                        w("%s OK done\r\n" % tag)
                    else:
                        w("%s BAD\r\n" % tag)
                elif cmd == "EXPUNGE":
                    w("%s OK done\r\n" % tag)
                elif cmd == "LIST":
                    w('* LIST (\\HasNoChildren) "." "INBOX"\r\n* LIST (\\HasNoChildren) "." "Drafts"\r\n' +
                      "".join('* LIST (\\HasNoChildren) "." "%s"\r\n' % f for f in self.sent_folders) +
                      "%s OK done\r\n" % tag)
                elif cmd in ("CREATE", "SUBSCRIBE"):
                    if cmd == "CREATE":
                        self.sent_folders.append(args.strip('"'))
                    w("%s OK done\r\n" % tag)
                elif cmd == "APPEND":
                    n = int(args.rsplit("{", 1)[1].rstrip("}"))
                    w("+ go\r\n")
                    raw = f.read(n)
                    f.readline()
                    with self.lock:
                        self.filed.append((args.split(" ", 1)[0].strip('"'), raw))
                    w("%s OK [APPENDUID 1 1] done\r\n" % tag)
                elif cmd == "LOGOUT":
                    w("* BYE\r\n%s OK done\r\n" % tag)
                    return
                else:                    # LOGIN and anything else
                    w("%s OK done\r\n" % tag)
        except OSError:
            pass
        finally:
            c.close()


FAKE_DC = HERE / "fake_deltachat_rpc.py"

DC_WORLD = {
    "contacts": {"20": {"address": "alice@example.org", "displayName": "Alice"},
                 "21": {"address": "bob@example.org", "displayName": "Bob"}},
    "chats": {"10": {"id": 10, "name": "Alice", "chatType": "Single", "contacts": [20],
                     "fresh": 1},
              "11": {"id": 11, "name": "Team", "chatType": "Group", "contacts": [1, 20, 21]}},
    "messages": {"100": {"chatId": 10, "fromId": 20, "text": "hello", "state": 10,
                         "sortTimestamp": 1700000000},
                 "101": {"chatId": 10, "fromId": 1, "text": "hi back", "state": 26,
                         "sortTimestamp": 1700000060,
                         "sender": {"address": "me@example.org", "displayName": "Me"}},
                 "102": {"chatId": 11, "fromId": 21, "text": "meeting at 3", "state": 16,
                         "showPadlock": False, "sortTimestamp": 1700000100}},
}


class DeltaBase(Base):
    """The Delta Chat adapter against fake_deltachat_rpc.py, a stand-in for
    deltachat-rpc-server that answers from a small world and takes its
    orders (events, messages arriving, a crash) through files in its
    directory."""

    def setUp(self):
        super().setUp()
        if not os.access(str(FAKE_DC), os.X_OK):
            self.skipTest("fake_deltachat_rpc.py is not executable")
        self.data = self.tmp / "data"
        os.environ["XDG_DATA_HOME"] = str(self.data)
        self.addCleanup(os.environ.pop, "XDG_DATA_HOME", None)
        self.dir = self.data / "tvchat" / "deltachat" / "delta"

    def tearDown(self):
        if getattr(self, "chat", None) is not None:
            self.chat.close()
        super().tearDown()

    def setup_dc(self, world=DC_WORLD, configured=True, extra="", server=None):
        import json
        self.dir.mkdir(parents=True)
        (self.dir / "world.json").write_text(json.dumps(world))
        if configured:
            (self.dir / "state.json").write_text(json.dumps({"accounts": [1], "configured": True}))
        self.conf("""\
            [account:delta]
            protocol   = deltachat
            display    = Delta
            addr       = me@example.org
            rpc_server = %s
            %s
            """ % (server or FAKE_DC, extra.replace("\n", "\n            ")))
        self.chat = cb._chat()
        self.acct = self.chat.by_id["delta"]
        self.ad = self.chat.adapter(self.acct)

    def turn(self, pred, timeout=8):
        loop = self.chat.loop
        loop.running = True
        deadline = time.time() + timeout
        while not pred():
            if time.time() > deadline:
                self.fail("timed out; events: %r\nthe core got: %r"
                          % (self.chat.events.items[-8:], self.requests()))
            loop.run_once(0.05)

    def up(self):
        self.chat.connect(self.acct)
        self.turn(lambda: self.acct.state == "connected")

    def event(self, **ev):
        import json
        with open(str(self.dir / "events.jsonl"), "a") as fh:
            fh.write(json.dumps({"contextId": 1, "event": ev}) + "\n")

    def order(self, **obj):
        import json
        with open(str(self.dir / "events.jsonl"), "a") as fh:
            fh.write(json.dumps(obj) + "\n")

    def requests(self, method=None):
        import json
        try:
            lines = (self.dir / "requests.jsonl").read_text().splitlines()
        except OSError:
            return []
        reqs = [json.loads(ln) for ln in lines]
        return [r for r in reqs if method is None or r["method"] == method]

    def events(self, etype=None):
        return [f for _, f in self.chat.events.items if etype is None or f[0] == etype]

    def verb(self, *argv):
        return cb.dispatch(cb.build_parser(), list(argv))


class TestDeltaChat(DeltaBase):
    def test_a_missing_core_says_how_to_get_it(self):
        self.setup_dc(server="/nonexistent/deltachat-rpc-server")
        with self.assertRaises(cb.UserError) as e:
            self.chat.connect(self.acct)
        self.assertIn("not found", str(e.exception))
        self.assertIn("pip install deltachat-rpc-server", str(e.exception))
        self.assertEqual(self.acct.state, "error")
        self.assertEqual(self.verb("connect", "delta")[0], 1)

    def test_no_addr_is_a_user_error(self):
        self.conf("[account:delta]\nprotocol = deltachat\nrpc_server = %s\n" % FAKE_DC)
        chat = cb._chat()
        self.chat = chat
        with self.assertRaises(cb.UserError) as e:
            chat.connect(chat.by_id["delta"])
        self.assertIn("addr=", str(e.exception))

    def test_chats_history_unread_and_members(self):
        self.setup_dc()
        self.up()
        self.assertEqual(self.acct.state, "connected")
        self.assertEqual(sorted(self.acct.convs), ["delta/*", "delta/10", "delta/11"])
        alice, team = self.acct.convs["delta/10"], self.acct.convs["delta/11"]
        self.assertEqual((alice.kind, alice.title, alice.unread), ("direct", "Alice", 1))
        self.assertEqual((team.kind, team.title, team.unread), ("group", "Team", 0))
        got = [(m.id, m.sender.address, m.sender.display, m.flags, m.text) for m in alice.history]
        self.assertEqual(got, [
            ("delta:100", "alice@example.org", "Alice", "e", "hello"),
            ("delta:101", "me@example.org", "Me", "ser", "hi back")])
        self.assertEqual(team.history[0].flags, "r")             # seen, not encrypted
        self.assertEqual(self.verb("members", "delta/11")[1].decode().splitlines(),
                         ["alice@example.org\tAlice\tmember", "bob@example.org\tBob\tmember"])
        # the same lines the UI gets
        st, body = self.verb("history", "delta/10")
        self.assertEqual(body.decode().splitlines()[0].split("\t")[:2], ["delta:100", "delta/10"])
        self.assertEqual(self.verb("accounts")[1].decode().strip(),
                         "delta\tdeltachat\tconnected\tDelta")

    def test_an_incoming_message_is_unread_once(self):
        self.setup_dc()
        self.up()
        n = len(self.requests("get_next_event"))
        self.order(set_msg={"id": 103, "chatId": 10, "fromId": 20, "text": "ping",
                            "state": 10, "sortTimestamp": 1700000200})
        self.event(kind="IncomingMsg", chatId=10, msgId=103)
        self.event(kind="MsgsChanged", chatId=10, msgId=103)    # the core says it twice
        self.turn(lambda: len(self.requests("get_next_event")) >= n + 2)
        alice = self.acct.convs["delta/10"]
        self.assertEqual([m.text for m in alice.history], ["hello", "hi back", "ping"])
        self.assertEqual(alice.unread, 2)
        self.assertEqual(self.events("unread")[-1], ("unread", "delta/10", 2))

    def test_a_message_from_a_stranger_makes_a_chat(self):
        self.setup_dc()
        self.up()
        self.order(set_chat={"id": 12, "name": "Carol", "chatType": "Single",
                             "contacts": [22], "isContactRequest": True})
        self.order(set={"contacts": {"22": {"address": "carol@example.org",
                                            "displayName": "Carol"}}})
        self.order(set_msg={"id": 110, "chatId": 12, "fromId": 22, "text": "hi, it's Carol",
                            "state": 10, "sortTimestamp": 1700000300,
                            "sender": {"address": "carol@example.org", "displayName": "Carol"}})
        self.event(kind="IncomingMsg", chatId=12, msgId=110)
        self.turn(lambda: "delta/12" in self.acct.convs and self.acct.convs["delta/12"].unread == 1)
        conv = self.acct.convs["delta/12"]
        self.assertEqual((conv.kind, conv.title), ("direct", "Carol"))
        self.assertIn("a reply accepts the chat", [m.text for m in conv.history][0])
        self.verb("send", "delta/12", "hello Carol")
        self.assertEqual(len(self.requests("accept_chat")), 1)    # replying accepts it

    def test_send_answers_with_the_real_id_and_shows_once(self):
        self.setup_dc()
        self.up()
        st, body = self.verb("send", "delta/10", "on my way")
        self.assertEqual(st, 0)
        mid = body.decode().strip()
        self.assertRegex(mid, r"^delta:\d+$")
        self.assertEqual(self.requests("misc_send_text_message")[0]["params"],
                         [1, 10, "on my way"])
        n = len(self.requests("get_next_event"))
        self.turn(lambda: len(self.requests("get_next_event")) > n)   # its MsgsChanged came by
        alice = self.acct.convs["delta/10"]
        mine = [m for m in alice.history if m.text == "on my way"]
        self.assertEqual([(m.id, m.flags) for m in mine], [(mid, "ser")])
        self.assertEqual(alice.unread, 1)                         # our own line is not unread
        # typed commands: refused whole, // escapes
        self.assertEqual(self.verb("send", "delta/10", "/me waves")[0], 0)
        self.assertEqual(self.requests("misc_send_text_message")[-1]["params"][2], "* me waves")
        self.assertEqual(self.verb("send", "delta/10", "/bogus")[0], 1)
        self.assertEqual(self.verb("send", "delta/10", "//shrug")[0], 0)
        self.assertEqual(self.requests("misc_send_text_message")[-1]["params"][2], "/shrug")
        self.assertEqual(self.verb("send", "delta/*", "x")[0], 1)
        self.assertEqual(self.verb("send", "delta/10", "   ")[0], 1)

    def test_join_starts_a_chat_with_an_address(self):
        self.setup_dc()
        self.up()
        st, body = self.verb("join", "delta", "bob@example.org")
        self.assertEqual(st, 0)
        conv = self.acct.convs[body.decode().strip()]
        self.assertEqual((conv.kind, conv.title), ("direct", "Bob"))
        self.assertEqual(self.verb("join", "delta", "bob@example.org")[1], body)   # again: same chat
        st, body = self.verb("join", "delta", "not an address")
        self.assertEqual(st, 1)
        self.assertIn("email address", body.decode())
        self.assertEqual(self.verb("part", "delta/*")[0], 1)            # the server window stays

    def test_invites_give_the_keys_to_write(self):
        self.setup_dc(world=dict(DC_WORLD, no_keys=True))
        self.up()
        st, body = self.verb("invite", "delta")
        self.assertEqual((st, body), (0, b"https://i.delta.chat/#FAKEINVITE\n"))
        # without a key the core refuses; the user is told what to do
        st, body = self.verb("send", "delta/10", "hi")
        self.assertEqual(st, 1)
        self.assertIn("exchange an invite", body.decode())
        # joining a link starts the key exchange and opens the chat
        st, body = self.verb("join", "delta", "https://i.delta.chat/#FAKEINVITE")
        self.assertEqual(st, 0)
        self.assertEqual(self.requests("secure_join")[0]["params"],
                         [1, "https://i.delta.chat/#FAKEINVITE"])
        self.assertEqual(self.acct.convs[body.decode().strip()].title, "Inviter")

    def test_leave_deletes_a_chat_and_leaves_a_group_first(self):
        self.setup_dc()
        self.up()
        self.assertEqual(self.verb("part", "delta/10"), (0, b""))
        self.assertNotIn("delta/10", self.acct.convs)
        self.assertEqual([r["method"] for r in self.requests() if r["method"] in ("leave_group", "delete_chat")],
                         ["delete_chat"])                       # a one-to-one chat is just deleted
        self.assertEqual(self.requests("delete_chat")[0]["params"], [1, 10])
        self.assertEqual(self.verb("part", "delta/11"), (0, b""))
        self.assertNotIn("delta/11", self.acct.convs)
        self.assertEqual([r["method"] for r in self.requests() if r["method"] in ("leave_group", "delete_chat")],
                         ["delete_chat", "leave_group", "delete_chat"])   # a group: left, then deleted
        self.assertEqual(self.verb("part", "delta/10")[0], 1)    # already gone
        self.assertEqual(self.events("conv-gone")[0][1], "delta/10")

    def test_a_new_group_with_members_we_hold_keys_for(self):
        self.setup_dc()
        self.up()
        st, body = self.verb("new-group", "delta", "Book club", "alice@example.org", "bob@example.org")
        self.assertEqual(st, 0)
        conv = self.acct.convs[body.decode().strip()]
        self.assertEqual((conv.kind, conv.title), ("group", "Book club"))
        self.assertEqual(self.requests("create_group_chat")[0]["params"], [1, "Book club", False])
        self.assertEqual([r["params"][2] for r in self.requests("add_contact_to_chat")], [20, 21])
        self.assertEqual(self.verb("members", conv.id)[1].decode().splitlines(),
                         ["alice@example.org\tAlice\tmember", "bob@example.org\tBob\tmember"])
        self.assertTrue(any("group created" in m.text for m in conv.history))

    def test_a_member_with_no_key_is_invited_by_mail_not_added(self):
        self.setup_dc()
        self.up()
        st, body = self.verb("new-group", "delta", "Mixed", "alice@example.org", "stranger@example.org")
        self.assertEqual(st, 0)
        conv = self.acct.convs[body.decode().strip()]
        self.assertEqual([r["params"][2] for r in self.requests("add_contact_to_chat")], [20])
        self.assertTrue(any("stranger@example.org has no key yet" in m.text for m in conv.history))

    def test_group_members_come_and_go_and_the_group_is_renamed(self):
        self.setup_dc()
        self.up()
        team = "delta/11"                                        # Team: me, Alice, Bob
        self.assertEqual(self.verb("group-remove", team, "bob@example.org"), (0, b""))
        self.assertEqual(self.requests("remove_contact_from_chat")[0]["params"], [1, 11, 21])
        self.assertEqual(list(self.acct.convs[team].members), ["alice@example.org"])
        self.assertEqual(self.verb("group-add", team, "bob@example.org"), (0, b""))
        self.assertEqual(sorted(self.acct.convs[team].members), ["alice@example.org", "bob@example.org"])
        self.assertEqual(self.verb("group-rename", team, "Crew"), (0, b""))
        self.assertEqual(self.acct.convs[team].title, "Crew")
        self.assertEqual(self.requests("set_chat_name")[0]["params"], [1, 11, "Crew"])
        # refusals
        self.assertEqual(self.verb("group-add", "delta/10", "bob@example.org")[0], 1)      # not a group
        self.assertEqual(self.verb("group-add", team, "not an address")[0], 1)
        self.assertEqual(self.verb("group-remove", team, "nobody@example.org")[0], 1)
        self.assertEqual(self.verb("group-rename", team, "  ")[0], 1)
        self.assertEqual(self.verb("new-group", "delta", " ")[0], 1)
        self.assertEqual(self.verb("new-group", "delta", "X", "bad address")[0], 1)

    def test_the_shared_commands_on_delta_chat(self):
        self.setup_dc()
        self.up()
        say = lambda conv, text: self.verb("send", conv, text)
        notes = lambda conv: [m.text for m in self.acct.convs[conv].history if "n" in m.flags]
        sent = lambda: self.requests("misc_send_text_message")[-1]["params"]
        # /help lists what this protocol has - and only that
        self.assertEqual(say("delta/10", "/help")[0], 0)
        listed = "\n".join(notes("delta/10"))
        self.assertIn("Delta Chat commands", listed)
        self.assertIn("/join ADDRESS | LINK", listed)
        self.assertIn("/leave", listed)
        self.assertIn("/help [COMMAND]", listed)
        self.assertNotIn("/topic", listed)
        self.assertNotIn("/quote", listed)
        say("delta/10", "/? join")
        self.assertIn("    also: /j", notes("delta/10"))
        st, body = say("delta/10", "/help topic")
        self.assertEqual(st, 1)
        self.assertIn("/topic is not available for Delta Chat", body.decode())
        st, body = say("delta/10", "/topic x")
        self.assertEqual((st, b"/topic is not available for Delta Chat" in body), (1, True))
        st, body = say("delta/10", "/bogus")
        self.assertEqual((st, b"unknown command /bogus" in body), (1, True))
        say("delta/*", "/whoami")
        self.assertIn("you are me@example.org", "\n".join(notes("delta/*")))
        # /status
        say("delta/*", "/status")
        text = "\n".join(notes("delta/*"))
        self.assertIn("(Delta Chat): connected", text)
        self.assertIn("mailbox me@example.org", text)
        self.assertIn("2 conversations, 1 unread", text)
        # a few that say something
        say("delta/10", "/shrug well")
        self.assertEqual(sent()[2], "well \xaf\\_(\u30c4)_/\xaf")
        say("delta/10", "/query bob@example.org hello bob")
        self.assertEqual(sent(), [1, 12, "hello bob"])
        say("delta/10", "/nick Robin")
        self.assertEqual(self.requests("set_config")[-1]["params"], [1, "displayname", "Robin"])
        # who is here
        say("delta/11", "/names")
        self.assertIn("2 here besides you: Alice <alice@example.org>, Bob <bob@example.org>", notes("delta/11"))
        say("delta/10", "/names")
        self.assertIn("you and Alice <alice@example.org>", notes("delta/10"))
        say("delta/10", "/whois alice@example.org")
        self.assertIn("Alice <alice@example.org>", notes("delta/10"))
        say("delta/10", "/whois nobody@example.org")
        self.assertTrue(any("no key for them yet" in n for n in notes("delta/10")))
        # a group: invite adds (they are known), name renames
        self.verb("group-remove", "delta/11", "bob@example.org")
        self.assertEqual(say("delta/11", "/invite bob@example.org"), (0, b"\n"))
        self.assertIn("bob@example.org", self.acct.convs["delta/11"].members)
        self.assertEqual(say("delta/11", "/name Crew")[0], 0)
        self.assertEqual(self.acct.convs["delta/11"].title, "Crew")
        self.assertEqual(say("delta/10", "/name Nope")[0], 1)                # not a group
        # /clear empties the ring and has the UI re-read the log
        self.assertEqual(say("delta/10", "/clear")[0], 0)
        self.assertEqual(len(self.acct.convs["delta/10"].history), 0)
        self.assertIn(("history", "delta/10", "latest"), self.events("history"))
        # /leave, /quit
        self.assertEqual(say("delta/10", "/leave")[0], 0)
        self.assertNotIn("delta/10", self.acct.convs)
        self.assertEqual(say("delta/*", "/leave")[0], 1)
        self.assertEqual(say("delta/*", "/quit")[0], 0)
        self.assertEqual(self.acct.state, "disconnected")

    def test_not_connected_refuses(self):
        self.setup_dc()
        st, body = self.verb("join", "delta", "bob@example.org")
        self.assertEqual(st, 1)
        self.assertIn("not connected", body.decode())

    def test_reading_a_chat_tells_the_core(self):
        self.setup_dc()
        self.up()
        self.assertEqual(self.verb("mark-read", "delta/10"), (0, b""))
        self.assertEqual(self.requests("marknoticed_chat")[0]["params"], [1, 10])
        self.assertEqual(self.requests("markseen_msgs")[0]["params"], [1, [100]])
        self.assertEqual(self.acct.convs["delta/10"].unread, 0)
        # read on another device: the unread count goes
        self.order(set_msg={"id": 104, "chatId": 10, "fromId": 20, "text": "x", "state": 10,
                            "sortTimestamp": 1700000400})
        self.event(kind="IncomingMsg", chatId=10, msgId=104)
        self.turn(lambda: self.acct.convs["delta/10"].unread == 1)
        self.event(kind="MsgsNoticed", chatId=10)
        self.turn(lambda: self.acct.convs["delta/10"].unread == 0)
        self.assertEqual(len(self.requests("marknoticed_chat")), 1)   # not echoed back

    def test_a_failed_send_is_said_in_the_chat(self):
        self.setup_dc()
        self.up()
        self.order(set_msg={"id": 105, "chatId": 10, "fromId": 1, "text": "lost", "state": 24,
                            "error": "recipient refused", "sortTimestamp": 1700000500})
        self.event(kind="MsgFailed", chatId=10, msgId=105)
        self.turn(lambda: any("not delivered" in m.text for m in self.acct.convs["delta/10"].history))
        self.assertIn("recipient refused", self.acct.convs["delta/10"].history[-1].text)

    def test_the_netrc_may_name_the_address_or_the_domain(self):
        rc = self.home / ".netrc"
        rc.write_text("machine mail.example.org login me@example.org password by-address\n"
                      "machine other login someone password not-ours\n")
        rc.chmod(0o600)
        find = cb.find_delta_password
        # the login the server wants is "me", the entry is for the address on the domain
        self.assertEqual(find("delta", "me@example.org", "box", "me")[0], None)
        self.assertEqual(find("delta", "me@example.org", "mail.example.org", "me")[0], "by-address")
        rc.write_text("machine example.org login me password by-user\n")
        self.assertEqual(find("delta", "me@example.org", "box", "me")[0], "by-user")
        rc.write_text("machine other login someone password not-ours\n")
        self.assertEqual(find("delta", "me@example.org", "box", "me"), (None, None))
        os.environ["TVCHAT_PASS_DELTA"] = "from-env"
        self.addCleanup(os.environ.pop, "TVCHAT_PASS_DELTA", None)
        self.assertEqual(find("delta", "me@example.org", "box", "me")[0], "from-env")

    def test_netrc_with_two_logins_on_one_machine(self):
        # Python's netrc module keeps only the last entry per machine
        rc = self.home / ".netrc"
        rc.write_text('# mailboxes\nmachine mail.example.org login one@example.org password p-one\nmachine mail.example.org\n  login two@example.org\n  password "p two"\ndefault login anon password nope\n')
        rc.chmod(0o600)
        w = cb.find_password_where
        self.assertEqual(w("a", "mail.example.org", "one@example.org", "imap"), ("p-one", "~/.netrc"))
        self.assertEqual(w("a", "MAIL.example.org", "two@example.org", "imap"), ("p two", "~/.netrc"))
        self.assertEqual(w("a", "mail.example.org", "three@example.org", "imap"), (None, None))
        self.assertEqual(w("a", "other.example.org", "one@example.org", "imap"), (None, None))

    def test_the_first_run_sets_the_mailbox_up(self):
        os.environ["TVCHAT_PASS_DELTA"] = "s3cret"
        self.addCleanup(os.environ.pop, "TVCHAT_PASS_DELTA", None)
        self.setup_dc(configured=False, extra="imap_server = mail.example.org\n"
                                              "smtp_port = 587\nverify = false\nname = Me Myself")
        self.up()
        (req,) = self.requests("add_or_update_transport")
        self.assertEqual(req["params"][1], {
            "addr": "me@example.org", "password": "s3cret", "imapServer": "mail.example.org",
            "smtpPort": 587, "certificateChecks": "acceptInvalidCertificates"})
        self.assertEqual(self.requests("set_config")[0]["params"], [1, "displayname", "Me Myself"])
        self.assertEqual(len(self.requests("start_io")), 1)
        self.chat.close()
        self.chat.adapter(self.acct)
        self.chat.connect(self.acct)                    # configured now: no second setup
        self.turn(lambda: len(self.requests("start_io")) == 2)
        self.assertEqual(len(self.requests("add_or_update_transport")), 1)

    def test_a_missing_or_wrong_password_is_reported(self):
        self.setup_dc(configured=False)
        self.chat.connect(self.acct)
        self.turn(lambda: self.acct.state == "error")
        self.assertIn("no password for me@example.org", self.acct.detail)
        self.assertIn("$TVCHAT_PASS_DELTA", self.acct.detail)
        os.environ["TVCHAT_PASS_DELTA"] = "wrong"
        self.addCleanup(os.environ.pop, "TVCHAT_PASS_DELTA", None)
        self.chat.connect(self.acct)
        self.turn(lambda: self.acct.state == "error" and "wrong password" in self.acct.detail)
        self.assertIn("setting up me@example.org failed", self.acct.detail)

    def test_the_mail_server_being_away_is_connecting_not_connected(self):
        w = dict(DC_WORLD, connectivity=1000)
        self.setup_dc(world=w)
        self.chat.connect(self.acct)
        self.turn(lambda: "delta/10" in self.acct.convs)        # chats show from the database
        self.assertEqual(self.acct.state, "connecting")
        self.assertIn("waiting for the mail server", self.acct.detail)
        self.order(set={"connectivity": 4000})
        self.event(kind="ConnectivityChanged")
        self.turn(lambda: self.acct.state == "connected")

    def test_a_dead_core_is_restarted_with_backoff(self):
        self.setup_dc()
        self.ad.RETRY_FIRST = 0.1
        self.up()
        self.order(exit=3)
        self.turn(lambda: self.acct.state == "connecting")
        self.assertIn("the Delta Chat core exited (status 3)", self.acct.detail)
        self.assertIn("restarting in 0.1 s", self.acct.detail)
        self.turn(lambda: self.acct.state == "connected")
        alice = self.acct.convs["delta/10"]
        self.assertEqual([m.id for m in alice.history], ["delta:100", "delta:101"])   # no doubles
        self.assertEqual(alice.unread, 1)
        # Disconnect stops the retrying for good
        self.verb("disconnect", "delta")
        self.assertEqual(self.acct.state, "disconnected")
        self.assertIsNone(self.ad.proc)

    def test_trust_vouches_only_for_encrypted_messages_from_named_addresses(self):
        w = {"contacts": DC_WORLD["contacts"],
             "chats": {"10": DC_WORLD["chats"]["10"]},
             "messages": {"1": {"chatId": 10, "fromId": 20, "text": "sealed", "state": 16, "showPadlock": True,
                                "sortTimestamp": 1700000001, "sender": {"address": "Alice@example.org"}},
                          "2": {"chatId": 10, "fromId": 20, "text": "plain", "state": 16, "showPadlock": False,
                                "sortTimestamp": 1700000002, "sender": {"address": "alice@example.org"}},
                          "3": {"chatId": 10, "fromId": 21, "text": "other", "state": 16, "showPadlock": True,
                                "sortTimestamp": 1700000003, "sender": {"address": "bob@example.org"}}}}
        self.setup_dc(world=w, extra="trust = alice@example.org")
        conv = types.SimpleNamespace(id="delta/10")
        got = dict((m["text"], self.ad._to_message(conv, dict(m, id=int(i))).sender.account)
                   for i, m in w["messages"].items())
        self.assertEqual(got, {"sealed": "alice", "plain": None, "other": None})

    def test_older_history_comes_from_the_core(self):
        w = {"contacts": DC_WORLD["contacts"],
             "chats": {"10": DC_WORLD["chats"]["10"]},
             "messages": dict(("%d" % i, {"chatId": 10, "fromId": 20, "text": "m%d" % i,
                                          "state": 16, "sortTimestamp": 1700000000 + i})
                              for i in range(200, 330))}      # 130 messages
        self.setup_dc(world=w)
        self.up()
        alice = self.acct.convs["delta/10"]
        self.assertEqual([m.text for m in alice.history][0], "m280")      # the last 50
        self.assertEqual(len(alice.history), 50)
        st, body = self.verb("history", "delta/10", "--before", "delta:280")
        self.assertEqual(st, 1)
        self.assertIn("asking the server", body.decode())
        self.assertEqual(self.events("history")[-1], ("history", "delta/10", "before"))
        st, body = self.verb("history", "delta/10", "--before", "delta:280", "--limit", "100")
        self.assertEqual(len(body.decode().splitlines()), 50)
        self.assertEqual(body.decode().splitlines()[0].split("\t")[0], "delta:230")
        self.verb("history", "delta/10", "--before", "delta:230")
        self.verb("history", "delta/10", "--before", "delta:200")   # the very first: nothing older
        self.assertEqual(self.verb("history", "delta/10", "--before", "delta:200"), (0, b""))
        self.assertTrue(alice.history_done)

    def test_the_core_is_given_a_private_accounts_directory(self):
        self.setup_dc()
        self.up()
        self.assertEqual(oct(os.stat(str(self.dir)).st_mode & 0o777), oct(0o700))
        self.assertEqual(oct(os.stat(str(self.dir.parent)).st_mode & 0o777), oct(0o700))


class TestMailReplies(Base):
    """The mail adapter: mail to the central Floyd is answered by mail."""

    def setUp(self):
        super().setUp()
        self.mail = FakeMail()
        self.addCleanup(self.mail.close)
        os.environ["TVCHAT_PASS_MAIL"] = "pw"
        self.addCleanup(os.environ.pop, "TVCHAT_PASS_MAIL", None)
        self.conf("""\
            [account:mail]
            protocol      = mail
            addr          = floyd@example.org
            imap_server   = 127.0.0.1
            imap_port     = %d
            imap_security = plain
            smtp_server   = 127.0.0.1
            smtp_port     = %d
            smtp_security = plain
            poll          = 0.2

            [account:floyd]
            protocol = floyd
            attach   = mail/*
            tag      =
            idle     = 0
            admins   = alice@example.org
            """ % (self.mail.imap_port, self.mail.smtp_port))
        self.chat = cb._chat()
        self.chat.daemon = True
        self.addCleanup(self.chat.close)
        self.chat.adapter(self.chat.by_id["mail"]).SETTLE = 0.3

    def turn(self, pred, timeout=10):
        loop = self.chat.loop
        loop.running = True
        deadline = time.time() + timeout
        while not pred():
            if time.time() > deadline:
                self.fail("timed out; events: %r\nsent: %r" % (self.chat.events.items[-6:], self.mail.sent))
            loop.run_once(0.05)

    def settle(self, secs=1.0):
        end = time.time() + secs
        while time.time() < end:
            self.chat.loop.run_once(0.05)

    def up(self):
        self.chat.connect(self.chat.by_id["floyd"])
        self.chat.connect(self.chat.by_id["mail"])

    def test_what_he_sends_is_filed_in_sent(self):
        self.mail.add_mail("From: Alice <alice@example.org>\nTo: floyd@example.org\nSubject: hi\n"
                           "Message-ID: <m9@example.org>\n\n!8ball ok\n")
        self.up()
        self.turn(lambda: self.mail.sent and self.mail.filed)
        import email
        folder, raw = self.mail.filed[0]
        self.assertEqual(folder, "Sent")                              # no INBOX.* folders here: made as Sent
        self.assertEqual(email.message_from_bytes(raw)["Subject"], "Re: hi")
        self.assertEqual(email.message_from_bytes(raw)["Message-ID"],
                         email.message_from_bytes(self.mail.sent[0])["Message-ID"])

    def test_a_handled_mail_is_moved_to_saved(self):
        self.mail.add_mail("From: Alice <alice@example.org>\nTo: floyd@example.org\nSubject: hi\n"
                           "Message-ID: <m9@example.org>\n\n!8ball ok\n")
        self.mail.add_mail("From: bot@example.org\nSubject: invite\nX-Tvchat-Invite: 1\n\nlink\n")
        self.up()
        self.turn(lambda: self.mail.sent and self.mail.copied)
        self.settle()
        self.assertEqual([f for f, _ in self.mail.copied], ["saved"])    # no INBOX.* folders here: made as saved
        self.assertIn(b"Subject: hi", self.mail.copied[0][1])
        self.assertEqual([m.get("deleted") for m in self.mail.inbox if b"Subject: hi" in m["raw"]], [True])
        self.assertFalse(any(m.get("deleted") for m in self.mail.inbox if b"X-Tvchat-Invite" in m["raw"]))

    def test_archive_no_leaves_the_mail_in_the_inbox(self):
        self.chat.by_id["mail"].opts["archive"] = "no"
        self.mail.add_mail("From: Alice <alice@example.org>\nTo: floyd@example.org\nSubject: hi\n"
                           "Message-ID: <m9@example.org>\n\n!8ball ok\n")
        self.up()
        self.turn(lambda: self.mail.sent)
        self.settle()
        self.assertEqual(self.mail.copied, [])
        self.assertFalse(any(m.get("deleted") for m in self.mail.inbox))

    def test_a_mail_gets_a_reply_in_the_same_thread(self):
        self.mail.add_mail("From: Alice <alice@example.org>\nTo: floyd@example.org\nSubject: hello\n"
                           "Message-ID: <m1@example.org>\n\n!8ball will it work\n\n> old\n-- \nAlice\n")
        self.up()
        self.turn(lambda: self.mail.sent)
        import email
        msg = email.message_from_bytes(self.mail.sent[0])
        self.assertEqual((msg["To"], msg["From"], msg["Subject"], msg["In-Reply-To"],
                          msg["Auto-Submitted"]),
                         ("alice@example.org", "floyd@example.org", "Re: hello", "<m1@example.org>",
                          "auto-replied"))
        self.assertIn(msg.get_payload(decode=True).decode().strip(), cb.FloydMind.EIGHT_BALL)
        self.settle()
        self.assertEqual(len(self.mail.sent), 1)                       # read once, answered once

    def test_robots_and_invitations_and_lists_are_not_answered(self):
        for hdr in ("Auto-Submitted: auto-replied\n", "Precedence: bulk\n", "List-Id: <x.example.org>\n",
                    "X-Tvchat-Invite: 1\n"):
            self.mail.add_mail("From: bot@example.org\nSubject: hi\n%s\n!8ball x\n" % hdr)
        self.mail.add_mail("From: noreply@example.org\nSubject: hi\n\n!8ball x\n")
        self.mail.add_mail("From: floyd@example.org\nSubject: hi\n\n!8ball x\n")
        self.mail.add_mail("From: carol@example.org\nSubject: ping\n\n!8ball last\n")
        self.up()
        self.turn(lambda: self.mail.sent)
        self.settle()
        import email
        self.assertEqual([email.message_from_bytes(m)["To"] for m in self.mail.sent], ["carol@example.org"])

    def whoami(self, headers, sender="chris@elsewhere.org"):
        self.chat.adapter(self.chat.by_id["mail"]).known = cb.parse_known("chris=chris@elsewhere.org")
        self.chat.adapter(self.chat.by_id["floyd"]).admins = {"chris"}
        self.mail.add_mail("From: %s\nSubject: who\n%s\n!whoami\n" % (sender, headers))
        self.up()
        self.turn(lambda: self.mail.sent)
        import email
        return email.message_from_bytes(self.mail.sent[0]).get_payload(decode=True).decode()

    OK_HDR = "X-Spam-Report: rules\n  -0.1 DKIM_VALID_AU   Message has a valid DKIM signature\n"

    def test_a_known_admin_with_a_verified_signature_is_an_admin_by_mail(self):
        self.assertIn("as account chris, an admin.", self.whoami(self.OK_HDR))

    def test_no_verified_signature_no_admin(self):
        self.assertNotIn("an admin", self.whoami("X-Spam-Report: DKIM_VALID\n"))
        self.assertNotIn("an admin", self.whoami(""))

    def test_a_forged_report_below_the_servers_own_does_not_count(self):
        hdr = "X-Spam-Report: rules, no dkim\n" + self.OK_HDR.replace("X-Spam-Report", "X-Ham-Report")
        self.assertNotIn("an admin", self.whoami(hdr))

    def test_verified_but_only_known_by_domain_is_not_an_admin(self):
        self.assertNotIn("an admin", self.whoami(self.OK_HDR, sender="chris@example.org"))

    def test_the_sender_is_not_an_admin_whatever_the_from_line_says(self):
        self.mail.add_mail("From: alice@example.org\nSubject: x\n\n!claude do things\n")
        self.up()
        self.turn(lambda: self.mail.sent)
        self.assertIn(b"Only Floyd's admins", self.mail.sent[0])

    def test_one_sender_gets_a_few_replies_an_hour(self):
        self.chat.adapter(self.chat.by_id["mail"]).MAX_PER_SENDER = 1
        self.mail.add_mail("From: dave@example.org\nSubject: a\n\n!8ball one\n")
        self.up()
        self.turn(lambda: len(self.mail.sent) == 1)
        self.mail.add_mail("From: dave@example.org\nSubject: b\n\n!8ball two\n")
        self.turn(lambda: all(m.get("seen") for m in self.mail.inbox))
        self.settle(1.5)
        self.assertEqual(len(self.mail.sent), 1)

    def test_only_known_addresses_and_the_own_domain_are_answered(self):
        self.chat.adapter(self.chat.by_id["mail"]).known = cb.parse_known(
            "michelle=Michelle@Elsewhere.org, chris=chris@elsewhere.org")
        self.mail.add_mail("From: stranger@spam.example\nSubject: a\n\n!8ball who\n")
        self.mail.add_mail("From: Mich <michelle@elsewhere.org>\nSubject: b\n\n!8ball hi\n")
        self.mail.add_mail("From: inhouse@example.org\nSubject: c\n\n!8ball hi\n")
        self.up()
        self.turn(lambda: len(self.mail.sent) == 2)
        self.settle()
        import email
        self.assertEqual(sorted(email.message_from_bytes(m)["To"] for m in self.mail.sent),
                         ["inhouse@example.org", "michelle@elsewhere.org"])
        self.assertEqual(self.chat.by_id["mail"].convs["mail/michelle@elsewhere.org"].title, "michelle")

    def test_known_parsing(self):
        self.assertEqual(cb.parse_known("a=A@x.org, b=b@y.org"), {"a@x.org": "a", "b@y.org": "b"})
        self.assertEqual(cb.parse_known("c@z.org"), {"c@z.org": "c@z.org"})
        self.assertEqual(cb.parse_known(""), {})
        with self.assertRaises(cb.UserError):
            cb.parse_known("nobody")

    def test_text_for_floyd(self):
        f = cb.mail_text_for_floyd
        self.assertEqual(f("Hi Floyd,\n\nwhat is 12*7?\n\n> quoted\n-- \nbye"), "Hi Floyd, what is 12*7?")
        self.assertEqual(f("Hello\n!time in Tokyo\nthanks"), "!time in Tokyo")
        self.assertEqual(f("", "Re: !coin"), "!coin")



class TestDeltaSecondDevice(DeltaBase):
    """The same Delta Chat account on two machines: one offers it (Chat > Add second device), the other takes it."""
    CODE = "DCBACKUP2:fake-backup-token"

    def test_offering_shows_the_code_pauses_mail_and_ends_when_taken(self):
        self.setup_dc()
        self.up()
        st, body = self.verb("device-add", "delta")
        self.assertEqual((st, body.decode().splitlines()), (0, ["manual", self.CODE]))   # no master: the code is shown
        self.assertEqual(self.ad.offer, self.CODE)
        self.assertEqual([r["params"] for r in self.requests("provide_backup")], [[1]])
        self.assertIn([1, "bcc_self", "1"], [r["params"] for r in self.requests("set_config")])
        starts = len(self.requests("start_io"))
        self.order(backup_taken=1)                                            # the other device fetched it
        self.turn(lambda: self.ad.offer is None)
        self.turn(lambda: len(self.requests("start_io")) > starts)            # mail traffic resumes
        texts = [m.text for m in self.acct.convs["delta/*"].history]
        self.assertTrue(any("the other device has this account now" in t for t in texts), texts)

    def test_cancelling_the_offer(self):
        self.setup_dc()
        self.up()
        self.verb("device-add", "delta")
        starts = len(self.requests("start_io"))
        self.assertEqual(self.verb("device-cancel", "delta")[0], 0)
        self.turn(lambda: self.ad.offer is None)
        self.turn(lambda: len(self.requests("start_io")) > starts)
        self.assertTrue(any("cancelled" in m.text for m in self.acct.convs["delta/*"].history))
        self.assertEqual(self.verb("device-cancel", "delta")[0], 0)           # nothing offered: harmless

    def test_taking_the_account_on_a_new_machine(self):
        self.setup_dc(configured=False)                                       # no account here yet
        st, body = self.verb("device-use", "delta", self.CODE)
        self.assertEqual((st, body.decode().strip()), (0, "started"))
        self.turn(lambda: self.acct.state == "connected")
        self.assertEqual([r["params"] for r in self.requests("get_backup")], [[1, self.CODE]])
        self.assertEqual(self.requests("add_or_update_transport"), [])        # no password was needed: the account came whole
        self.assertTrue(any(r["params"][1:] == ["bcc_self", "1"] for r in self.requests("set_config")))
        self.assertTrue(any("same Delta Chat account" in m.text for m in self.acct.convs["delta/*"].history))

    def test_the_code_is_checked_and_never_logged(self):
        self.setup_dc(configured=False)
        st, body = self.verb("device-use", "delta", "https://example.org/not-a-backup")
        self.assertEqual(st, 1)
        self.assertIn("not the code", body.decode())
        st, body = self.verb("device-use", "delta")                           # no code and no master to ask
        self.assertEqual(st, 1)
        self.assertIn("no machine is offering", body.decode())
        self.verb("device-use", "delta", self.CODE)
        self.turn(lambda: self.acct.state == "connected")
        self.assertNotIn(self.CODE, "\n".join(str(f) for f in self.chat.events.items))

    def test_a_wrong_code_says_so(self):
        self.setup_dc(configured=False)
        self.verb("device-use", "delta", "DCBACKUP2:not-the-right-one")
        self.turn(lambda: self.acct.state == "error")
        self.assertIn("receiving the account from the other device failed", self.acct.detail)
        self.assertIn("not a valid backup code", self.acct.detail)

    def test_an_existing_account_is_replaced_only_when_confirmed_and_is_kept_aside(self):
        self.setup_dc()
        self.up()
        st, body = self.verb("device-use", "delta", self.CODE)
        self.assertEqual(body.decode().strip(), "confirm")                    # nothing happened yet
        self.assertEqual(self.acct.state, "connected")
        self.assertEqual(self.requests("get_backup"), [])
        st, body = self.verb("device-use", "delta", self.CODE, "--replace")
        self.assertEqual(body.decode().strip(), "started")
        kept = [p.name for p in self.dir.parent.iterdir() if ".replaced-" in p.name]
        self.assertEqual(len(kept), 1)                                        # the old data is moved, not deleted
        self.assertTrue((self.dir.parent / kept[0] / "world.json").exists())
        self.turn(lambda: self.acct.state == "connected" and bool(self.requests("get_backup")))

    def test_the_code_travels_through_the_master_floyd_and_is_not_a_toast(self):
        import selectors
        import threading
        sel, stop = selectors.DefaultSelector(), []

        class Loop(object):
            def add_reader(s, sock, fn):
                sel.register(sock, selectors.EVENT_READ, fn)

            def remove_reader(s, sock):
                stop.append(1)

        def pump():
            while not stop:
                for key, _ in sel.select(0.05):
                    key.data()
        gate = cb.tvf.FloydGate(Loop(), cb.tvf.FloydMemory(None), "tok", cb.tvf.claude_settings(lambda k, d: "no" if k == "claude" else d),
                                "127.0.0.1", 0)
        gate.start()
        threading.Thread(target=pump, daemon=True).start()
        self.addCleanup(gate.stop)
        self.setup_dc()
        with open(cb._conf_path(), "a") as fh:
            fh.write("\n[floyd]\nserver = 127.0.0.1:%d\ntoken = tok\nmachine = cpbee\n" % gate.sock.getsockname()[1])
        cb.tvf._DOWN.clear()
        self.up()
        st, body = self.verb("device-add", "delta")
        self.assertEqual(body.decode().splitlines()[0], "auto")                # the other machine finds it by itself
        seen = gate.hub.since(None, "cmlaptop")[1]
        self.assertEqual([(e["kind"], e["key"], e["data"]) for e in seen], [("device", "device:me@example.org", self.CODE)])
        self.assertNotIn(self.CODE, seen[0]["text"])                           # the toast text carries no code
        # the other machine, asking: finds the code with no typing
        other = dict(cb.tvf.master_settings(lambda k, d: {"server": "127.0.0.1:%d" % gate.sock.getsockname()[1],
                                                           "token": "tok", "machine": "cmlaptop"}.get(k, d)))
        self.ad._master = lambda: other
        self.assertEqual(self.ad._find_offer(), self.CODE)
        self.ad._master = type(self.ad)._master.__get__(self.ad)
        self.order(backup_taken=1)                                             # taken: the offer is withdrawn from the hub
        self.turn(lambda: self.ad.offer is None)
        self.assertEqual([e for e in gate.hub.since(None, "cmlaptop")[1] if e["kind"] == "device"], [])



def _load_dcgw():
    import importlib.machinery
    import importlib.util
    path = str(Path(__file__).resolve().parent.parent / "backend" / "dcgw")
    loader = importlib.machinery.SourceFileLoader("dcgw", path)
    spec = importlib.util.spec_from_loader("dcgw", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class TestDeltaGateway(DeltaBase):
    """The account lives on a Delta Chat gateway (dcgw): tvchat is a thin client, with no core, key or mail password."""
    TOKEN = "gw-token-for-tests"  # allowlist secret: test value

    def setUp(self):
        super().setUp()
        import json
        self.dcgw = _load_dcgw()
        self.gwdata = self.tmp / "gw"
        self.dir = self.gwdata / "chris"                       # where the fake core behind the gateway keeps its files
        self.dir.mkdir(parents=True)
        (self.dir / "world.json").write_text(json.dumps(DC_WORLD))
        (self.dir / "state.json").write_text(json.dumps({"accounts": [1], "configured": True}))
        self.gwconf = self.tmp / "dcgw.conf"
        self.write_gwconf(self.TOKEN)
        self.start_gateway()

    def write_gwconf(self, token):
        self.gwconf.write_text("[dcgw]\nlisten = 127.0.0.1:0\ncore = %s\ndata = %s\n\n[user:chris]\naddr = chris@cpbee.cmnet\n"
                               "token = %s\n" % (FAKE_DC, self.gwdata, token))

    def start_gateway(self):
        self.gw = self.dcgw.Gateway(str(self.gwconf))
        self.gw.serve()
        self.addCleanup(self.gw.stop)
        self.port = self.gw.servers[0].server_address[1]

    def use_gateway(self, token=None, extra=""):
        self.conf("""\
            [account:delta]
            protocol = deltachat
            display  = Delta
            gateway  = 127.0.0.1:%d
            token    = %s
            %s
            """ % (self.port, token or self.TOKEN, extra))
        self.chat = cb._chat()
        self.acct = self.chat.by_id["delta"]
        self.ad = self.chat.adapter(self.acct)

    def test_a_thin_client_lists_chats_and_history_and_holds_no_core_or_key(self):
        self.use_gateway()
        self.up()
        self.assertEqual(sorted(self.acct.convs), ["delta/*", "delta/10", "delta/11"])
        alice = self.acct.convs["delta/10"]
        self.assertEqual([m.text for m in alice.history], ["hello", "hi back"])
        self.assertEqual(self.ad.addr, "chris@cpbee.cmnet")                    # learned from the gateway
        self.assertFalse((self.data / "tvchat" / "deltachat").exists())        # no database of its own here
        self.assertEqual(self.acct.state, "connected")
        self.assertNotIn("mail_pw", " ".join(str(r) for r in self.requests()))

    def test_sending_goes_through_the_gateway(self):
        self.use_gateway()
        self.up()
        st, body = self.verb("send", "delta/10", "from the thin client")
        self.assertEqual(st, 0, body)
        sent = self.requests("misc_send_text_message")
        self.assertEqual([r["params"][1:] for r in sent], [[10, "from the thin client"]])

    def test_an_incoming_message_arrives_through_the_gateways_event_stream(self):
        self.use_gateway()
        self.up()
        self.order(set_msg={"id": 103, "chatId": 10, "fromId": 20, "text": "ping", "state": 10, "sortTimestamp": 1700000200})
        self.event(kind="IncomingMsg", chatId=10, msgId=103)
        self.turn(lambda: [m.text for m in self.acct.convs["delta/10"].history][-1:] == ["ping"])
        self.assertEqual(self.acct.convs["delta/10"].unread, 2)

    def test_a_wrong_token_is_refused_in_words(self):
        self.use_gateway(token="not-the-token")  # allowlist secret: test value
        self.chat.connect(self.acct)
        self.turn(lambda: self.acct.state == "error")
        self.assertIn("refused", self.acct.detail)
        self.assertIn("not accepted", self.acct.detail)

    def test_a_gateway_that_is_down_is_named(self):
        self.use_gateway()
        self.gw.stop()
        self.chat.connect(self.acct)
        self.turn(lambda: self.acct.state == "error")
        self.assertIn("cannot connect to the Delta Chat gateway 127.0.0.1:%d" % self.port, self.acct.detail)

    def test_an_account_not_set_up_on_the_gateway_says_what_to_do(self):
        import json
        (self.dir / "state.json").write_text(json.dumps({"accounts": [1], "configured": False}))
        self.gw.stop()
        self.start_gateway()
        self.use_gateway()
        self.chat.connect(self.acct)
        self.turn(lambda: self.acct.state == "error")
        self.assertIn("dcgw setup", self.acct.detail)

    def test_a_missing_token_is_a_user_error_and_the_config_check_agrees(self):
        self.conf("[account:delta]\nprotocol = deltachat\ngateway = cpbee\n")
        chat = cb._chat()
        self.chat = chat
        with self.assertRaises(cb.UserError) as e:
            chat.connect(chat.by_id["delta"])
        self.assertIn("token =", str(e.exception))
        errors, warnings, _ = cb.check_config(cb._conf_path())
        self.assertTrue(any("gateway = needs token" in x for x in errors), errors)
        self.conf("[account:delta]\nprotocol = deltachat\ngateway = cpbee\ntoken = x\n")
        errors, warnings, _ = cb.check_config(cb._conf_path())
        self.assertEqual(errors, [])                                           # no addr, no core needed

    def test_the_link_dropping_is_reported_and_retried(self):
        self.use_gateway()
        self.up()
        self.order(exit=3)                                                     # the core behind the gateway dies
        self.turn(lambda: self.acct.state != "connected")
        self.assertIn("gateway", self.acct.detail + self.acct.state)


class TestDeltaGatewayTls(TestDeltaGateway):
    """The same, over TLS: the gateway with a certificate, the client verifying it (a thread pair carries the bytes)."""

    def setUp(self):
        import shutil as _sh
        import subprocess as _sp
        if not _sh.which("openssl"):
            self.skipTest("no openssl to make a test certificate")
        self.certdir = Path(tempfile.mkdtemp(prefix="dcgw-tls-"))
        self.addCleanup(_sh.rmtree, str(self.certdir), True)
        self.cert, self.key = self.certdir / "cert.pem", self.certdir / "key.pem"
        r = _sp.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=cpbee",
                     "-addext", "subjectAltName=IP:127.0.0.1,DNS:cpbee", "-keyout", str(self.key), "-out", str(self.cert)],
                    stdout=_sp.PIPE, stderr=_sp.PIPE)
        if r.returncode != 0:
            self.skipTest("openssl could not make a certificate: %s" % r.stderr.decode()[-200:])
        super().setUp()

    def write_gwconf(self, token):
        super().write_gwconf(token)
        text = self.gwconf.read_text().replace("[dcgw]\n", "[dcgw]\ntls_cert = %s\ntls_key = %s\n" % (self.cert, self.key), 1)
        self.gwconf.write_text(text)

    def use_gateway(self, token=None, extra=""):
        super().use_gateway(token, "gateway_tls = yes\ngateway_cafile = %s\n%s" % (self.cert, extra))

    def test_the_wire_is_encrypted_and_an_unverified_certificate_is_refused(self):
        self.use_gateway()
        self.up()
        self.assertEqual(sorted(self.acct.convs), ["delta/*", "delta/10", "delta/11"])
        self.assertEqual(type(self.ad.proc.tls).__name__, "SSLSocket")          # the link IS the TLS socket
        # a plain client is not served by a TLS gateway ...
        plain = socket.create_connection(("127.0.0.1", self.port), 3)
        plain.settimeout(3)
        plain.sendall(("DCGW 1 %s\n" % self.TOKEN).encode())
        try:
            self.assertNotIn(b"OK", plain.recv(100))
        except (socket.timeout, OSError):
            pass
        plain.close()
        # ... and a client that does not trust the certificate says so
        self.ad.disconnect()
        self.conf("[account:delta]\nprotocol = deltachat\ngateway = 127.0.0.1:%d\ntoken = %s\ngateway_tls = yes\n"
                  % (self.port, self.TOKEN))
        cb._CHAT = None
        self.chat = cb._chat()
        self.acct = self.chat.by_id["delta"]
        self.chat.connect(self.acct)
        self.turn(lambda: self.acct.state == "error")
        self.assertIn("certificate", self.acct.detail.lower())


class TestDeltaInvitations(DeltaBase):
    """Joining an address you hold no key for mails an invitation; one found in
    the INBOX is offered, accepted or declined."""

    def setUp(self):
        super().setUp()
        self._path = os.environ["PATH"]
        self.mail = FakeMail()
        self.addCleanup(self.mail.close)
        os.environ["TVCHAT_PASS_DELTA"] = "pw"
        self.addCleanup(os.environ.pop, "TVCHAT_PASS_DELTA", None)

    def setup_inv(self):
        self.setup_dc(extra=("imap_server = 127.0.0.1\nimap_port = %d\nimap_security = plain\n"
                             "imap_user = me\nsmtp_server = 127.0.0.1\nsmtp_port = %d\n"
                             "smtp_security = plain\nsmtp_user = me\n")
                      % (self.mail.imap_port, self.mail.smtp_port))
        self.ad.INVITE_POLL = 0.2

    def test_join_without_a_key_mails_the_invitation(self):
        self.setup_inv()
        self.up()
        st, body = self.verb("join", "delta", "new@example.org")
        self.assertEqual((st, body), (0, b"delta/*\n"))               # the server window tells the news
        self.turn(lambda: self.mail.sent)
        import email
        msg = email.message_from_bytes(self.mail.sent[0])
        self.assertEqual((msg["To"], msg["From"], msg["X-Tvchat-Invite"]),
                         ("new@example.org", "me@example.org", "1"))
        self.assertIn("https://i.delta.chat/#FAKEINVITE", msg.get_payload())
        self.turn(lambda: any("invitation mailed to new@example.org" in m.text
                              for m in self.acct.convs["delta/*"].history))
        self.assertEqual(self.requests("create_chat_by_contact_id"), [])  # no chat until they accept

    def test_a_failed_mail_gives_the_link_to_pass_on(self):
        self.setup_inv()
        self.up()
        self.mail.close()
        self.verb("join", "delta", "new@example.org")
        self.turn(lambda: any("could not mail the invitation" in m.text and "FAKEINVITE" in m.text
                              for m in self.acct.convs["delta/*"].history))

    def test_an_invitation_in_the_inbox_is_offered_and_accepted(self):
        link = "https://i.delta.chat/#ABC&v=3&a=alice%40example.org&n="
        uid = self.mail.add("Alice <alice@example.org>", link)
        self.mail.add("Mallory <mallory@example.org>", link)             # a link for someone else: ignored
        self.mail.add("Bob <bob@example.org>", "no link here", header=True)
        self.setup_inv()
        self.up()
        self.turn(lambda: self.events("invite"))
        self.assertEqual(self.events("invite"), [("invite", "delta", "alice@example.org")])
        self.assertEqual(self.verb("invites"), (0, b"delta\talice@example.org\n"))
        self.turn(lambda: any("ignored a chat invitation from mallory@example.org" in m.text
                              for m in self.acct.convs["delta/*"].history))
        # not offered twice, however often the mailbox is looked at
        time.sleep(0.5)
        self.chat.loop.run_once(0.3)
        self.assertEqual(len(self.events("invite")), 1)
        st, body = self.verb("accept-invite", "delta", "alice@example.org")
        self.assertEqual(st, 0)
        self.assertEqual(self.requests("secure_join")[0]["params"], [1, link])
        self.assertEqual(self.acct.convs[body.decode().strip()].title, "Inviter")
        self.turn(lambda: uid not in [m["uid"] for m in self.mail.live()])   # the mail is gone
        self.assertEqual(self.verb("invites"), (0, b""))
        self.assertEqual(self.verb("accept-invite", "delta", "alice@example.org")[0], 1)

    def test_the_central_floyd_accepts_invitations_by_himself(self):
        link = "https://i.delta.chat/#ABC&v=3&a=alice%40example.org&n="
        uid = self.mail.add("Alice <alice@example.org>", link)
        self.setup_inv()
        self.chat.daemon = True
        self.up()
        self.turn(lambda: self.requests("secure_join"))
        self.assertEqual(self.requests("secure_join")[0]["params"], [1, link])
        self.turn(lambda: uid not in [m["uid"] for m in self.mail.live()])
        self.assertEqual(self.verb("invites"), (0, b""))
        self.assertTrue(any("accepted the chat invitation from alice@example.org" in m.text
                            for m in self.acct.convs["delta/*"].history))

    def test_declining_removes_the_mail_and_joins_nothing(self):
        link = "https://i.delta.chat/#ABC&a=alice%40example.org"
        uid = self.mail.add("alice@example.org", link)
        self.setup_inv()
        self.up()
        self.turn(lambda: self.events("invite"))
        self.assertEqual(self.verb("decline-invite", "delta", "alice@example.org"), (0, b""))
        self.turn(lambda: uid not in [m["uid"] for m in self.mail.live()])
        self.assertEqual(self.requests("secure_join"), [])
        self.assertEqual(self.verb("invites"), (0, b""))

    def request(self, name, text):
        d = self.tmp / "requests"
        d.mkdir(exist_ok=True)
        os.environ["TVCHAT_REQUESTS"] = str(d)
        self.addCleanup(os.environ.pop, "TVCHAT_REQUESTS", None)
        (d / name).write_text(text + "\n")
        return d / name

    def notices(self):
        return [f[1] for f in self.events("notice")]

    def test_a_request_from_tvmail_opens_a_chat_or_mails_an_invitation(self):
        self.setup_inv()
        # nothing connected yet: the request waits
        req = self.request("1.req", "chat bob@example.org")
        self.chat._requests_once()
        self.assertTrue(req.exists())
        self.up()
        self.chat._requests_once()
        self.assertFalse(req.exists())
        self.assertTrue(any(n.startswith("chat with bob@example.org") for n in self.notices()))
        self.assertIn("delta/12", self.acct.convs)                # Bob has a key: a chat, no mail
        self.assertEqual(self.mail.sent, [])
        # an address without a key: they are invited by mail
        self.request("2.req", "chat new@example.org")
        self.chat._requests_once()
        self.turn(lambda: self.mail.sent)
        import email
        self.assertEqual(email.message_from_bytes(self.mail.sent[0])["To"], "new@example.org")

    def test_a_request_with_an_invite_link_is_offered_for_acceptance(self):
        self.setup_inv()
        self.up()
        link = "https://i.delta.chat/#ABC&v=3&a=carol%40example.org&n="
        self.request("1.req", "invite " + link)
        self.request("2.req", "invite https://i.delta.chat/#NONAME")      # names nobody
        self.request("3.req", "do something odd")
        self.request("4.req", "invite not-a-link")
        self.chat._requests_once()
        self.assertEqual(self.events("invite"), [("invite", "delta", "carol@example.org")])
        self.assertEqual(self.verb("invites"), (0, b"delta\tcarol@example.org\n"))
        n = self.notices()
        self.assertEqual(len(n), 4)
        self.assertIn("a chat invitation from carol@example.org is waiting", n[0])
        self.assertTrue(all("failed" in x or "ignored" in x for x in n[1:]))
        self.assertEqual(os.listdir(str(self.tmp / "requests")), [])
        st, body = self.verb("accept-invite", "delta", "carol@example.org")
        self.assertEqual(st, 0)
        self.assertEqual(self.requests("secure_join")[0]["params"], [1, link])

    def stub_tvmail(self):
        """A tvmail-backend on PATH that records its arguments and stdin, and fails
        while the file `fail` exists."""
        d = self.tmp / "tvmailbin"
        d.mkdir()
        self.log = self.tmp / "tvmail.log"
        self.fail = self.tmp / "fail"
        stub = d / "tvmail-backend"
        stub.write_text("#!/bin/sh\necho \"$@\" >> %s\ncat >> %s\n"
                        "[ -e %s ] && { echo 'SMTP server said no'; exit 1; }\nexit 0\n"
                        % (self.log, self.log, self.fail))
        stub.chmod(0o755)
        os.environ["PATH"] = str(d) + os.pathsep + os.environ["PATH"]
        self.addCleanup(os.environ.__setitem__, "PATH", os.environ["PATH"].split(os.pathsep, 1)[1])

    def test_the_invitation_can_be_mailed_through_tvmail(self):
        self.setup_inv()
        self.up()
        self.assertEqual(self.verb("has-tvmail")[0], 0)
        self.stub_tvmail()
        self.assertEqual(self.verb("has-tvmail"), (0, b"yes\n"))
        self.assertEqual(self.verb("invite-mail", "delta", "friend@example.net"), (0, b""))
        self.turn(lambda: any("invitation mailed to friend@example.net through tvmail" in m.text
                              for m in self.acct.convs["delta/*"].history))
        text = self.log.read_text()
        self.assertIn("send --to friend@example.net --subject Chat invitation from me@example.org", text)
        self.assertIn("https://i.delta.chat/#FAKEINVITE", text)
        # tvmail refuses: the reason is told
        self.fail.write_text("")
        self.verb("invite-mail", "delta", "other@example.net")
        self.turn(lambda: any("tvmail could not send the invitation to other@example.net: SMTP server said no"
                              in m.text for m in self.acct.convs["delta/*"].history))
        # a bad address is refused up front
        self.assertEqual(self.verb("invite-mail", "delta", "nobody")[0], 1)

    def test_without_tvmail_the_option_is_off_and_says_so(self):
        self.setup_inv()
        self.up()
        os.environ["PATH"] = "/nonexistent-dir"                      # no tvmail-backend anywhere on PATH
        self.addCleanup(os.environ.__setitem__, "PATH", self._path)
        with unittest.mock.patch.object(cb.os.path, "isfile", return_value=False):
            self.assertEqual(self.verb("has-tvmail"), (0, b"no\n"))
            st, body = self.verb("invite-mail", "delta", "friend@example.net")
        self.assertEqual(st, 1)
        self.assertIn("tvmail is not installed", body.decode())

    def test_invite_helpers(self):
        self.assertEqual(cb.invite_names("https://i.delta.chat/#X&v=3&a=a%2Bb%40ex.org&n="), "a+b@ex.org")
        self.assertTrue(cb.invite_ok("A@EX.org", "https://i.delta.chat/#X&a=a%40ex.org"))
        self.assertFalse(cb.invite_ok("b@ex.org", "https://i.delta.chat/#X&a=a%40ex.org"))
        self.assertFalse(cb.invite_ok("a@ex.org", "https://i.delta.chat/#X"))     # names nobody


class TestConfigEditing(Base):
    """What the in-app config editor leans on: check-config (Verify) and
    reload-config (apply)."""

    def check(self, text):
        f = self.tmp / "check.conf"
        f.write_text(textwrap.dedent(text))
        return cb.check_config(str(f))

    def test_a_good_file_has_no_errors_and_says_what_it_holds(self):
        errors, warnings, summary = self.check("""\
            [chat]
            autoconnect = a
            [account:a]
            protocol = irc
            host = irc.example.org
            [account:b]
            protocol = talk
            host = cpbee
            """)
        self.assertEqual((errors, summary), ([], "2 accounts: a (irc), b (talk)"))

    def test_what_is_wrong_is_an_error(self):
        errors, _, _ = self.check("[account:a]\nprotocol = irc\n")
        self.assertEqual(errors, ["[account:a] host= is missing"])
        errors, _, _ = self.check("[account:a]\nprotocol = irc\nhost = x\nport = 99999\n")
        self.assertIn("port must be a port number", errors[0])
        errors, _, _ = self.check("[account:a]\nprotocol = carrier-pigeon\n")
        self.assertIn("protocol must be one of", errors[0])
        errors, _, _ = self.check("[chat]\nautoconnect = ghost\n")
        self.assertIn("no [account:ghost] section", errors[0])
        errors, _, _ = self.check("[account:d]\nprotocol = deltachat\naddr = not an address\n")
        self.assertIn("addr= must be the mailbox's email address", errors[0])
        errors, _, _ = self.check("this is not an ini file\n")
        self.assertTrue(errors)

    def test_what_may_be_wrong_is_a_warning(self):
        errors, warnings, _ = self.check("""\
            [account:a]
            protocol = irc
            host = irc.example.org
            sasl = me
            cafile = /nonexistent/ca.pem
            [account:d]
            protocol = deltachat
            addr = d@example.org
            rpc_server = /nonexistent/core
            """)
        self.assertEqual(errors, [])
        joined = "\n".join(warnings)
        self.assertIn("no password found for me on irc.example.org", joined)
        self.assertIn("cafile /nonexistent/ca.pem is not a file", joined)
        self.assertIn("core /nonexistent/core was not found", joined)
        self.assertIn("the first connect needs a password for d@example.org", joined)
        os.environ["TVCHAT_PASS_A"] = "pw"
        self.addCleanup(os.environ.pop, "TVCHAT_PASS_A", None)
        _, warnings, _ = self.check("[account:a]\nprotocol = irc\nhost = x\nsasl = me\n")
        self.assertEqual(warnings, [])

    def test_the_verb_prints_and_exits_1_on_errors(self):
        f = self.tmp / "v.conf"
        f.write_text("[account:a]\nprotocol = irc\n")
        st, body = cb.dispatch(cb.build_parser(), ["check-config", str(f)])
        self.assertEqual(st, 1)
        self.assertIn("error: [account:a] host= is missing", body.decode())
        f.write_text("[account:a]\nprotocol = irc\nhost = x\n")
        st, body = cb.dispatch(cb.build_parser(), ["check-config", str(f)])
        self.assertEqual((st, body), (0, b"ok: 1 account: a (irc)\n"))

    def test_reload_keeps_what_is_unchanged_and_follows_the_file(self):
        p = self.conf("""\
            [account:a]
            protocol = irc
            host = one.example.org
            [account:b]
            protocol = talk
            host = cpbee
            [account:c]
            protocol = irc
            host = three.example.org
            """)
        chat = cb._chat()
        a0, c0 = chat.by_id["a"], chat.by_id["c"]
        chat.adapter(chat.by_id["b"])
        self.assertIn("b", chat.adapters)
        p.write_text(textwrap.dedent("""\
            [account:a]
            protocol = irc
            host = one.example.org
            [account:c]
            protocol = irc
            host = changed.example.org
            [account:d]
            protocol = talk
            host = new
            """))
        self.assertEqual(chat.reload_config(), "added d; removed b; changed c")
        self.assertEqual([x.id for x in chat.accounts], ["a", "c", "d"])
        self.assertIs(chat.by_id["a"], a0)                       # untouched: the same object
        self.assertIsNot(chat.by_id["c"], c0)                    # changed: a fresh one
        self.assertEqual(chat.by_id["c"].opts["host"], "changed.example.org")
        self.assertNotIn("b", chat.adapters)
        self.assertEqual(chat.reload_config(), "no change")
        # a file that does not load changes nothing and says why
        p.write_text("[account:a]\nprotocol = nonsense\n")
        with self.assertRaises(cb.UserError):
            chat.reload_config()
        self.assertEqual([x.id for x in chat.accounts], ["a", "c", "d"])
        self.assertEqual(cb.dispatch(cb.build_parser(), ["reload-config"])[0], 1)


class TestFloyd(Base):
    """The Floyd account: a robot after Infocom's Planetfall, no network."""

    def setUp(self):
        Base.setUp(self)
        self.conf("""\
            [chat]
            autoconnect = floyd

            [account:floyd]
            protocol = floyd
            local    = yes
            nick     = tester
            idle     = 60
            seed     = 7
            memory   = none
            """)
        self.clk = FakeClock(1000.0)
        self.chat = cb.Chat(loop=cb.Loop(self.clk), clock=self.clk)
        self.acct = self.chat.account("floyd")
        self.chat.connect(self.acct)
        self.ad = self.chat.adapter(self.acct)
        self.conv = self.acct.convs["floyd/floyd"]

    def wait(self, secs):
        """Let `secs` of Floyd's time pass, a bit at a time."""
        for _ in range(int(secs * 4)):
            self.clk.t += 0.25
            self.chat.loop.run_due()

    def say(self, text, secs=15):
        self.chat.adapter(self.acct).send(self.conv, text)
        self.wait(secs)
        return [(m.sender.display or "-", m.flags, m.text) for m in self.conv.history]

    def texts(self, after=0):
        return [m.text for m in list(self.conv.history)[after:]]

    def test_connects_with_a_conversation_and_an_opening(self):
        self.assertEqual(self.acct.state, "connected")
        self.assertEqual(self.conv.kind, "direct")
        self.assertIn("*", [c.target for c in self.acct.convs.values()])   # the server window
        self.wait(10)
        self.assertTrue(self.texts()[0].startswith("Oh boy! A new friend! Hi, tester!"))
        self.assertTrue(any("holds out" in m.text for m in self.conv.history))   # and offers something
        self.assertIn("floyd", self.conv.members)

    def test_answers_after_a_pause_and_counts_unread(self):
        self.wait(10)
        self.chat.mark_read(self.conv)
        n = len(self.conv.history)
        self.ad.send(self.conv, "hello there")
        self.assertEqual(len(self.conv.history), n + 1)      # only our line, so far
        self.assertEqual(self.conv.history[-1].flags, "s")
        self.wait(3)
        reply = self.conv.history[-1]
        self.assertEqual((reply.sender.display, reply.flags), ("Floyd", ""))
        self.assertIn("tester", reply.text)
        self.assertEqual(self.conv.unread, 1)

    def test_take_what_he_holds_out(self):
        self.wait(10)
        offered = self.ad.mind.offered
        self.assertIsNotNone(offered)
        self.say("take it")
        self.assertIn(offered, self.ad.mind.inv)
        self.assertIsNone(self.ad.mind.offered)
        self.assertIn("Taken.", self.texts())
        self.say("inventory")
        self.assertIn("You are carrying: %s." % self.ad.mind.ITEMS[offered][0], self.texts())

    def test_look_take_drop_and_go_like_interactive_fiction(self):
        self.say("look")
        self.assertIn("Repair Room", self.texts())
        self.assertTrue(any("an oil can" in t for t in self.texts()))
        self.ad.send(self.conv, "/take oil")
        self.wait(3)
        self.assertEqual(self.ad.mind.inv.count("oilcan"), 1)
        self.say("go north")
        self.assertIn("Corridor", self.texts())
        self.say("drop oil can")
        self.assertIn("oilcan", self.ad.mind.floor["corridor"])
        self.say("go west")
        self.assertIn("You can't go that way.", self.texts())

    def test_give_him_something_and_he_keeps_it(self):
        self.ad.send(self.conv, "/take flashlight")
        self.say("give flashlight to floyd")
        self.assertIn("flashlight", self.ad.mind.floyd_has)
        self.assertNotIn("flashlight", self.ad.mind.inv)

    def test_chat_that_sounds_like_a_command_stays_chat(self):
        out = self.say("take care")
        self.assertFalse(any("any such thing" in m[2] for m in out))
        self.assertFalse(self.ad.mind.inv)

    def test_errands_the_time_a_coin_a_count_and_a_reminder(self):
        self.say("what time is it")
        self.assertTrue(any(t.startswith("Floyd looked at the big clock. It says ") for t in self.texts()))
        self.say("count to 3", secs=10)
        self.assertTrue(set(["1...", "2...", "3..."]) <= set(self.texts()))
        self.say("remind me in 2 minutes to stretch", secs=1)
        self.assertFalse(any("stretch" in t and "remembered" in t for t in self.texts()))
        self.wait(125)
        self.assertTrue(any("remembered" in t and "stretch" in t for t in self.texts()))

    def test_hide_and_seek_ends_and_a_game_is_not_restarted(self):
        self.say("let's play hide and seek")
        self.assertIn(self.ad.mind.game, ("hide", "seek"))
        for _ in range(4):
            self.say("behind the sofa", secs=5)
        self.assertIsNone(self.ad.mind.game)

    def test_alone_he_wanders_then_shuts_down_and_wakes_on_a_word(self):
        self.wait(10)
        n = len(self.conv.history)
        self.wait(61)
        self.assertGreater(len(self.conv.history), n)        # spoke up unprompted
        self.wait(60 * 4)
        self.assertIn("Floyd shut down to save power", self.conv.history[-1].text)
        n = len(self.conv.history)
        self.wait(600)
        self.assertEqual(len(self.conv.history), n)          # asleep: silent
        self.say("hello", secs=3)
        self.assertGreater(len(self.conv.history), n + 1)    # awake again

    def test_idle_zero_means_he_never_speaks_first(self):
        self.conf("""\
            [account:floyd]
            protocol = floyd
            local    = yes
            idle     = 0
            """)
        cb._CHAT = None
        chat = cb.Chat(loop=cb.Loop(self.clk), clock=self.clk)
        a = chat.account("floyd")
        chat.connect(a)
        conv = a.convs["floyd/floyd"]
        for _ in range(4 * 400):
            self.clk.t += 0.25
            chat.loop.run_due()
        self.assertEqual(sum(1 for m in conv.history if m.flags == ""), 1)    # just the opening

    def test_commands_and_refusals(self):
        with self.assertRaises(cb.UserError):
            self.ad.send(self.conv, "/join #chan")        # no such thing here
        with self.assertRaises(cb.UserError):
            self.ad.send(self.conv, "/leave")
        with self.assertRaises(cb.UserError):
            self.ad.send(self.acct.convs["floyd/*"], "hello")
        self.assertIn("/take THING", "\n".join(cb.help_lines("floyd")))
        self.ad.send(self.conv, "/me waves")
        self.assertEqual(self.conv.history[-1].flags, "sa")

    def test_bad_idle_is_refused_by_the_config_check(self):
        with self.assertRaises(cb.UserError):
            cb.FloydAdapter.idle_seconds("often")
        self.assertEqual(cb.FloydAdapter.idle_seconds(""), 180.0)
        bad = self.conf("""\
            [account:f]
            protocol = floyd
            idle     = often
            seed     = x
            """)
        errors, _, _ = cb.check_config(str(bad))
        self.assertEqual(len(errors), 2)
        self.assertIn("idle=", errors[0])
        good = self.conf("[account:f]\nprotocol = floyd\n")
        self.assertEqual(cb.check_config(str(good))[0], [])

    def test_disconnect_silences_a_pending_reply(self):
        self.ad.send(self.conv, "hello")
        self.ad.disconnect()
        n = len(self.conv.history)
        self.wait(10)
        self.assertEqual(len(self.conv.history), n)
        self.assertEqual(self.acct.state, "disconnected")
        with self.assertRaises(cb.UserError):
            self.ad.send(self.conv, "hello")


class TestFloydBot(TestFloyd):
    """The old-school bot in him: !seen, !tell, karma, quotes, facts, !fortune."""

    def test_seen_and_tell_and_karma(self):
        self.say("!seen nobody")
        self.assertTrue(any("hasn't seen nobody" in t for t in self.texts()))
        self.say("!seen tester")
        self.assertIn("Floyd can see you right now! Hi!", self.texts())
        self.ad.mind.observe("bob", "hi all", "#chan")
        self.say("!seen bob")
        self.assertTrue(any(t.startswith("Floyd last saw bob") and '"hi all"' in t and "#chan" in t
                            for t in self.texts()))
        self.say("!tell tester buy milk")
        self.say("anyone there?")
        self.assertTrue(any("message for you from tester" in t and "buy milk" in t for t in self.texts()))
        self.say("bob++")
        self.say("bob++")
        self.assertTrue(any("bob has 2 points now" in t for t in self.texts()))
        self.say("tester++")
        self.assertTrue(any("vote for yourself" in t for t in self.texts()))
        self.say("!karma")
        self.assertTrue(any(t.startswith("Floyd's best friends: bob (2)") for t in self.texts()))

    def test_quotes_and_facts(self):
        self.say("!quote")
        self.assertTrue(any("no quotes" in t for t in self.texts()))
        self.say("!addquote the bird is the word")
        self.say("!quote 1")
        self.assertIn("#1: the bird is the word", self.texts())
        self.say("!learn the bird is a pigeon")
        self.say("!the bird")
        self.assertIn("the bird is a pigeon.", self.texts())
        self.say("the bird?")
        self.assertEqual(self.texts().count("the bird is a pigeon."), 2)
        self.say("!forget the bird")
        self.say("!the bird")
        self.assertEqual(self.texts().count("the bird is a pigeon."), 2)

    def test_bang_errands_and_help(self):
        self.say("!roll 20")
        self.assertTrue(any(t.startswith("It's a ") for t in self.texts()))
        self.say("!help")
        self.assertTrue(any(t.startswith("Floyd knows: !seen NICK") for t in self.texts()))

    def test_fortune_uses_the_real_one_when_installed_else_his_own(self):
        self.ad.mind.fortune_prog = None
        n = len(self.conv.history)
        self.say("!fortune")
        self.assertTrue([t for t in self.texts(n) if t in cb.FloydMind.FORTUNES])
        if os.name == "nt":
            return
        prog = self.tmp / "fortune"
        prog.write_text("#!/bin/sh\necho 'A bird in the hand'\necho '  is a bird.'\n")
        prog.chmod(0o755)
        self.ad.mind.fortune_prog = str(prog)
        n = len(self.conv.history)
        self.say("tell me a fortune")
        self.assertIn("Floyd opened the fortune machine. It says: A bird in the hand is a bird.",
                      self.texts(n))
        self.ad.mind.fortune_prog = str(self.tmp / "no-such-fortune")      # broken: his own
        n = len(self.conv.history)
        self.say("!fortune")
        self.assertTrue([t for t in self.texts(n) if t in cb.FloydMind.FORTUNES])

    def test_memory_survives_a_restart(self):
        path = str(self.tmp / "floyd.json")
        mem = cb.FloydMemory(path)
        mind = cb.FloydMind(memory=mem, clock=self.clk)
        mind.botcmd("!addquote kept", "bob")
        mind.botcmd("bob++", "carol")
        mind.observe("bob", "back soon", "#c")
        again = cb.FloydMemory(path)
        self.assertEqual(again.data["quotes"], ["kept"])
        self.assertEqual(again.data["karma"], {"bob": 1})
        self.assertIn("bob", again.data["seen"])
        Path(path).write_text("{ not json")
        self.assertEqual(cb.FloydMemory(path).data["quotes"], [])         # damaged: starts empty


class TestFloydAttached(Base):
    """Floyd listening in another account's conversation."""

    class Other(cb.CommandMixin):
        def __init__(self, chat, account):
            self.chat, self.account = chat, account
            self.said, self.notes = [], []

        def need_up(self):
            pass

        def speak(self, conv, text):
            self.said.append(text)
            return "m"

        def system(self, conv, text):
            self.notes.append(text)

        def status_lines(self):
            return []

    def setUp(self):
        Base.setUp(self)
        self.conf("""\
            [account:floyd]
            protocol = floyd
            local    = yes
            idle     = 0
            seed     = 5
            memory   = none

            [account:net]
            protocol = irc
            host     = irc.example.org
            """)
        self.clk = FakeClock(1000.0)
        self.chat = cb.Chat(loop=cb.Loop(self.clk), clock=self.clk)
        self.net = self.chat.account("net")
        self.other = self.chat.adapters["net"] = self.Other(self.chat, self.net)
        self.conv = self.chat.ensure_conv(self.net, "#room", "channel")
        self.chat.connect(self.chat.account("floyd"))
        self.floyd = self.chat.floyd()

    def hear(self, who, text, flags="", age=0):
        msg = cb.Message("m%d" % len(self.conv.history), self.conv.id, self.clk.t - age,
                         cb.Contact(who), text, flags)
        self.chat.add_message(self.conv, msg)
        for _ in range(40):
            self.clk.t += 0.25
            self.chat.loop.run_due()

    def test_floyd_on_switches_him_on_and_a_restart_brings_him_back(self):
        self.chat.adapters["floyd"].disconnect()
        self.assertEqual(self.chat.account("floyd").state, "disconnected")
        self.other.send(self.conv, "/floyd on")            # no need to connect him first
        self.assertEqual(self.chat.account("floyd").state, "connected")
        self.hear("bob", "!8ball ok")
        self.assertEqual(len(self.other.said), 1)
        path = self.tmp / "floyd.json"
        self.floyd.memory.path = str(path)
        self.floyd.memory.save()
        self.conf("""\
            [account:floyd]
            protocol = floyd
            local    = yes
            memory   = %s

            [account:net]
            protocol = irc
            host     = irc.example.org
            """ % path)
        cb._CHAT = None
        again = cb.Chat(loop=cb.Loop(self.clk), clock=self.clk)
        again.start()                                      # not in autoconnect, but attached
        self.assertEqual(again.account("floyd").state, "connected")

    def _asks(self, who, account, text):
        msg = cb.Message("a%d" % len(self.conv.history), self.conv.id, self.clk.t, cb.Contact(who, None, account), text, "")
        self.chat.add_message(self.conv, msg)
        for _ in range(40):
            self.clk.t += 0.25
            self.chat.loop.run_due()
        return " ".join(s for s in self.other.said if s.startswith("[Floyd]"))

    def test_asked_if_he_is_an_ai_he_is_honest_and_it_depends_on_who_asks(self):
        self.other.send(self.conv, "/floyd on")
        said = self._asks("bob", None, "floyd: are you an AI?")                  # Claude is off in his configuration
        self.assertIn("old-fashioned bot", said)
        self.assertIn("no language model inside Floyd", said)
        self.assertIn("Claude is not awake", said)
        self.other.said.clear()
        self.chat.daemon = True                                                 # Claude is on (a program that exists)
        self.floyd.claude, self.floyd.claude_cmd, self.floyd.admins = True, sys.executable, {"alice"}
        said = self._asks("alice", "alice", "floyd: are you an AI?")             # an admin may talk to Claude through him
        self.assertIn("You are an admin", said)
        self.assertIn("!claude QUESTION", said)
        self.other.said.clear()
        said = self._asks("bob", None, "floyd: are you chatgpt?")                # anybody else may not
        self.assertIn("You are not one here", said)
        self.other.said.clear()
        self.net.opts["llm"] = "no"                                              # a network that does not want such bots
        said = self._asks("bob", None, "floyd: is this AI?")
        self.assertIn("does not want bots that use language models", said)

    def _gate(self):
        """A master Floyd's gate on a free local port, its own memory and a fake Claude; the Floyd here points at it."""
        import selectors
        import threading
        sel, stop = selectors.DefaultSelector(), []

        class Loop(object):
            def add_reader(s, sock, fn):
                sel.register(sock, selectors.EVENT_READ, fn)

            def remove_reader(s, sock):
                stop.append(1)

        def pump():
            while not stop:
                for key, _ in sel.select(0.05):
                    key.data()
        fake = self.tmp / "masterclaude"
        fake.write_text("#!/bin/sh\ncat >/dev/null\necho the master says four\n")
        fake.chmod(0o755)
        state = self.tmp / "master-state"
        os.environ["XDG_STATE_HOME"] = str(state)
        self.addCleanup(os.environ.pop, "XDG_STATE_HOME", None)
        mem = cb.tvf.FloydMemory(str(self.tmp / "master.json"))
        mem.data["facts"]["tea"] = "master tea is hot"
        gate = cb.tvf.FloydGate(Loop(), mem, "tok", cb.tvf.claude_settings(
            lambda k, d: {"claude_cmd": str(fake)}.get(k, d)), "127.0.0.1", 0)
        gate.start()
        threading.Thread(target=pump, daemon=True).start()
        self.addCleanup(gate.stop)
        cb.tvf._DOWN.clear()
        cb.tvf._SOURCE["last"] = None
        port = gate.sock.getsockname()[1]

        def point(token="tok", port=port):
            self.floyd.master = cb.tvf.master_settings(lambda k, d: {
                "server": "127.0.0.1:%d" % port, "token": token, "server_timeout": "0.5"}.get(k, d))  # allowlist secret: test value
        point()
        return gate, point

    @unittest.skipIf(os.name == "nt", "fake claude is a shell script")
    def test_the_master_answers_first_and_the_local_floyd_is_the_fallback(self):
        self.other.send(self.conv, "/floyd on")
        gate, point = self._gate()
        self.hear("bob", "!tea")
        self.assertTrue(any("master tea is hot" in t for t in self.other.said), self.other.said)
        self.other.said.clear()
        point(token="wrong")                                   # refused: the local Floyd answers, the master's fact is not his
        self.hear("bob", "!tea")
        self.hear("bob", "!8ball ok")
        self.assertFalse(any("master tea" in t for t in self.other.said))
        self.assertTrue(any(t.startswith("[Floyd]") for t in self.other.said))
        self.other.said.clear()
        point()
        cb.tvf._DOWN.clear()
        self.hear("bob", "!status")
        said = " | ".join(self.other.said)
        self.assertIn("Master Floyd: 127.0.0.1:", said)
        self.assertIn("answers", said)
        self.assertIn("Claude: yes, on the master", said)
        self.other.said.clear()
        gate.stop()
        cb.tvf._DOWN.clear()
        self.hear("bob", "!8ball ok")                          # cpbee gone: still himself
        self.assertTrue(any(t.startswith("[Floyd]") for t in self.other.said))

    @unittest.skipIf(os.name == "nt", "fake claude is a shell script")
    def test_my_own_claude_question_goes_to_the_master(self):
        self.other.send(self.conv, "/floyd on")
        gate, point = self._gate()
        self.hear("me", "!claude what is 2+2?", flags="s")
        for _ in range(30):                                    # the job runs in real time; the loop polls on the fake clock
            time.sleep(0.1)
            self.clk.t += 3.5
            self.chat.loop.run_due()
        self.assertTrue(any("the master says four" in t for t in self.other.said), self.other.said)
        self.other.said.clear()
        self.hear("bob", "!claude what is 2+2?")               # somebody else's question is not mine to forward
        self.assertTrue(any("Only Floyd's admins" in t for t in self.other.said), self.other.said)

    def test_silent_until_attached(self):
        self.hear("bob", "!help")
        self.assertEqual(self.other.said, [])

    def test_slash_floyd_attaches_and_detaches(self):
        self.other.send(self.conv, "/floyd")
        self.assertTrue(self.other.notes[-1].startswith("Floyd is listening here."))
        self.assertIn(self.conv.id, self.floyd.memory.data["attached"])
        self.other.send(self.conv, "/floyd status")
        self.assertEqual(self.other.notes[-1], "Floyd is listening here")
        self.other.send(self.conv, "/floyd off")
        self.hear("bob", "!help")
        self.assertEqual([s for s in self.other.said if s.startswith("[Floyd]")], [])
        with self.assertRaises(cb.UserError):
            self.other.send(self.conv, "/floyd maybe")

    def test_answers_commands_and_address_tagged_and_ignores_chat(self):
        self.other.send(self.conv, "/floyd on")
        self.hear("bob", "just chatting about the weather")
        self.assertEqual(self.other.said, [])
        self.hear("bob", "!8ball will it work")
        self.assertEqual(len(self.other.said), 1)
        self.assertTrue(self.other.said[0].startswith("[Floyd] "))
        self.hear("bob", "floyd: what time is it")
        self.assertTrue(self.other.said[-1].startswith("[Floyd] Floyd looked at the big clock"))
        self.hear("bob", "alice++")
        self.assertIn("[Floyd] Floyd gave alice a point. alice has 1 point now.", self.other.said)

    def _jokes_said(self):
        return [s for s in self.other.said if s.startswith("[Floyd] ")]

    def test_joke_gives_a_setup_then_a_punchline_from_the_list(self):
        self.other.send(self.conv, "/floyd on")
        self.hear("bob", "!joke")
        said = self._jokes_said()
        self.assertEqual(len(said), 2)
        lines = [s[len("[Floyd] "):] for s in said]
        self.assertIn(tuple(lines), cb.FloydMind.JOKES)

    def test_joke_is_a_two_step_plan_with_a_delay_between(self):
        plan = cb.FloydMind(rng=cb.random.Random(1)).botcmd("!joke", "bob")
        self.assertEqual(len(plan), 2)
        self.assertLess(plan[0][0], plan[1][0])
        self.assertIn(plan[0][1], [j[0] for j in cb.FloydMind.JOKES])
        self.assertIn(plan[1][1], [j[1] for j in cb.FloydMind.JOKES])

    def test_joke_is_in_the_help_and_the_list_is_clean(self):
        self.other.send(self.conv, "/floyd on")
        self.hear("bob", "!help")
        self.assertTrue(any("!joke" in s for s in self.other.said))
        self.assertGreaterEqual(len(cb.FloydMind.JOKES), 25)
        self.assertEqual(len(set(cb.FloydMind.JOKES)), len(cb.FloydMind.JOKES))

    def test_joke_follows_the_seed(self):
        def jokes(seed):
            mind = cb.FloydMind(rng=cb.random.Random(seed))
            return [mind.botcmd("!joke", "bob")[0][1] for _ in range(5)]
        self.assertEqual(jokes(7), jokes(7))
        self.assertNotEqual(jokes(7), jokes(8))

    def test_an_unknown_bang_command_gets_an_answer(self):
        self.other.send(self.conv, "/floyd on")
        self.hear("bob", "!frobnicate now")
        self.assertIn("[Floyd] Floyd doesn't know !frobnicate. Floyd looked everywhere. Try !help.",
                      self.other.said)
        n = len(self.other.said)
        self.hear("bob", "!!!")
        self.assertEqual(len(self.other.said), n)

    def test_seen_tell_work_across_people_and_nothing_is_said_to_the_wrong_one(self):
        self.other.send(self.conv, "/floyd on")
        self.hear("alice", "hello room")
        self.hear("bob", "!tell alice the build is green")
        self.hear("bob", "!seen alice")
        self.assertTrue(any("Floyd last saw alice" in s and "hello room" in s for s in self.other.said))
        n = len(self.other.said)
        self.hear("carol", "morning")
        self.assertEqual(len(self.other.said), n)
        self.hear("alice", "back")
        last = self.other.said[-1]
        self.assertTrue(last.startswith("[Floyd] alice: Floyd has a message for you from bob ("), last)
        self.assertTrue(last.endswith(" ago): the build is green"), last)

    def test_does_not_answer_himself_old_lines_or_a_flood(self):
        self.other.send(self.conv, "/floyd on")
        self.hear("me", "[Floyd] !help")                  # his own tag: ignored
        self.hear("bob", "!help", age=300)                # a replay
        self.assertEqual(self.other.said, [])
        self.hear("bob", "!8ball a")
        self.floyd.last_out[self.conv.id] = self.clk.t    # just spoke
        n = len(self.other.said)
        self.hear("bob", "!8ball b")
        self.assertEqual(len(self.other.said), n)

    def test_a_quote_cannot_smuggle_a_command(self):
        self.floyd.memory.data["quotes"].append("/kick everyone")
        self.other.send(self.conv, "/floyd on")
        self.hear("bob", "!quote 1")
        self.assertEqual(self.other.said, ["[Floyd] #1: /kick everyone"])
        floyd = self.floyd
        floyd.tag = ""
        floyd._send_out(self.conv, "/kick everyone", "")
        self.assertEqual(self.other.said[-1], "/kick everyone")      # sent as "//kick", i.e. literal text

    def test_config_attach_patterns_and_no_floyd_account(self):
        self.floyd.patterns = ["net/#r*"]
        self.assertTrue(self.floyd.attached(self.conv))
        self.hear("bob", "!8ball x")
        self.assertEqual(len(self.other.said), 1)
        cb._CHAT = None
        self.conf("[account:net]\nprotocol = irc\nhost = x\n")
        chat = cb.Chat(loop=cb.Loop(self.clk), clock=self.clk)
        with self.assertRaises(cb.UserError):
            chat.floyd()


class TestFloydDaemon(Base):
    """The central Floyd: a daemon on the server, admins, the Claude consult."""

    Other = TestFloydAttached.Other

    CONF = """\
        [account:floyd]
        protocol = floyd
        idle     = 0
        seed     = 5
        memory   = none
        tag      =
        attach   = net/*
        admins   = Chris, ops
        claude   = %s
        claude_lines = 4

        [account:net]
        protocol = irc
        host     = irc.example.org
        """

    def start(self, claude="yes", daemon=True):
        self.conf(self.CONF % claude)
        self.clk = FakeClock(1000.0)
        self.chat = cb.Chat(loop=cb.Loop(self.clk), clock=self.clk)
        self.chat.daemon = daemon
        self.net = self.chat.account("net")
        self.other = self.chat.adapters["net"] = self.Other(self.chat, self.net)
        self.conv = self.chat.ensure_conv(self.net, "#room", "channel")
        self.dm = self.chat.ensure_conv(self.net, "bob", "direct")
        self.floyd = self.chat.floyd()
        if daemon:
            self.chat.connect(self.chat.account("floyd"))
        self.asked = []                  # (argv, prompt) of each consult
        self.answer = lambda prompt: "four"

        def run(fn, done):               # the thread, run in place
            try:
                res, err = fn(), None
            except Exception as e:
                res, err = None, e
            done(res, err)
        self.chat.loop.run_in_thread = run

        def fake(argv, prompt):
            self.asked.append((argv, prompt))
            r = self.answer(prompt)
            if isinstance(r, Exception):
                raise r
            return r
        self.floyd._run_claude = fake

    def hear(self, who, text, account=None, conv=None, flags=""):
        conv = conv or self.conv
        msg = cb.Message("m%d" % len(conv.history), conv.id, self.clk.t,
                         cb.Contact(who, account=account), text, flags)
        self.chat.add_message(conv, msg)
        for _ in range(60):
            self.clk.t += 0.25
            self.chat.loop.run_due()

    def test_an_admin_gets_an_answer(self):
        self.start()
        self.hear("chrissy", "!claude what is 2+2?", account="chris")
        self.assertEqual([p for _, p in self.asked], ["what is 2+2?"])
        self.assertIn("chrissy: Claude: four", self.other.said)

    def test_the_account_match_ignores_case(self):
        self.start()
        self.hear("x", "floyd: claude hi", account="OPS")
        self.assertEqual(len(self.asked), 1)

    def test_status_tells_an_admin_that_claude_is_there(self):
        self.start()
        self.floyd.claude_cmd = "/bin/sh"                       # a program that exists
        self.hear("chrissy", "!status", account="chris")
        said = " | ".join(self.other.said)
        self.assertIn("Claude: yes - you are an admin, so ask with !claude QUESTION", said)
        self.other.said.clear()
        self.hear("bob", "!status")
        self.assertIn("only his admins can ask (you are not one here)", " | ".join(self.other.said))
        self.other.said.clear()
        self.floyd.claude_cmd = "/nonexistent/claude"
        self.hear("bob", "floyd: status")
        self.assertIn("no - the program /nonexistent/claude was not found", " | ".join(self.other.said))

    def test_a_private_message_needs_no_prefix(self):
        self.start()
        self.hear("bob", "ask claude what time is it", account="chris", conv=self.dm)
        self.assertEqual([p for _, p in self.asked], ["what time is it"])
        self.assertIn("Claude: four", self.other.said)

    def test_a_non_admin_is_refused(self):
        self.start()
        self.hear("mallory", "!claude rm -rf /", account="mallory")
        self.assertEqual(self.asked, [])
        self.assertTrue(any("Only Floyd's admins" in t for t in self.other.said))

    def test_a_nick_is_not_an_account(self):
        self.start()
        self.hear("chris", "!claude hi")                       # nobody vouches for this nick
        self.hear("chris", "!claude hi", account="")
        self.assertEqual(self.asked, [])

    def test_plain_chat_in_a_channel_is_not_an_order(self):
        self.start()
        self.hear("chris", "claude is a fine name", account="chris")
        self.assertEqual(self.asked, [])

    def test_whoami(self):
        self.start()
        self.hear("a", "!whoami", account="chris")
        self.hear("b", "!whoami", account="eve")
        self.hear("c", "!whoami")
        said = " | ".join(self.other.said)
        self.assertIn("a as account chris, an admin", said)
        self.assertIn("b as account eve, not an admin", said)
        self.assertIn("not logged in to services", said)

    def test_claude_off_in_the_config(self):
        self.start(claude="no")
        self.hear("chris", "!claude hi", account="chris")
        self.assertEqual(self.asked, [])
        self.assertTrue(any("Claude is asleep" in t for t in self.other.said))

    def test_one_consult_at_a_time(self):
        self.start()
        self.chat.loop.run_in_thread = lambda fn, done: None   # never finishes
        self.hear("chris", "!claude one", account="chris")
        self.hear("chris", "!claude two", account="chris")
        self.assertTrue(any("still waiting for Claude" in t for t in self.other.said))

    def test_a_long_answer_is_cut(self):
        self.start()
        self.answer = lambda p: "\n".join("line %d" % i for i in range(30))
        self.hear("chris", "!claude go", account="chris")
        said = [t for t in self.other.said if "line" in t or "more lines" in t]
        self.assertEqual(len(said), 5)
        self.assertIn("... (26 more lines)", said[-1])
        self.other.said.clear()
        self.answer = lambda p: "x" * 4000
        self.hear("chris", "!claude go", account="chris")
        self.assertTrue(any("(cut short)" in t for t in self.other.said))
        self.assertLess(sum(len(t) for t in self.other.said), 1700)

    def test_a_failure_is_reported(self):
        self.start()
        self.answer = lambda p: RuntimeError("not logged in")
        self.hear("chris", "!claude go", account="chris")
        self.assertTrue(any("could not be reached (not logged in)" in t for t in self.other.said))
        self.answer = lambda p: subprocess.TimeoutExpired("claude", 1)
        self.hear("chris", "!claude again", account="chris")     # and he is free again
        self.assertTrue(any("took too long" in t for t in self.other.said))

    def test_control_characters_never_reach_the_channel(self):
        self.start()
        self.answer = lambda p: "ok\x01ACTION x\x00\x07"
        self.hear("chris", "!claude go", account="chris")
        self.assertFalse(any(c in t for t in self.other.said for c in "\x00\x01\x07"))

    def test_the_command_line_is_read_only(self):
        self.start()
        argv = self.floyd._claude_argv()
        self.assertEqual(argv[:3], ["claude", "-p", "--output-format"])
        self.assertEqual(argv[argv.index("--tools") + 1], "Read,Grep,Glob")
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        self.assertNotIn("Bash", " ".join(argv))
        # no connectors: an empty, strict MCP config and no skills
        self.assertIn("--strict-mcp-config", argv)
        self.assertEqual(argv[argv.index("--mcp-config") + 1], '{"mcpServers":{}}')
        self.assertIn("--disable-slash-commands", argv)

    def test_the_prompt_goes_in_on_stdin(self):
        self.start()
        script = self.tmp / "fakeclaude"
        script.write_text("#!/bin/sh\necho \"args: $*\" > %s/args\necho \"mcp: $ENABLE_CLAUDEAI_MCP_SERVERS\" > %s/env\ncat\n"
                          % (self.tmp, self.tmp))
        script.chmod(0o755)
        ad = self.chat.adapters["floyd"]
        del ad._run_claude                                     # the real one
        self.assertEqual(ad._run_claude([str(script), "-p"], "-- hi there"), "-- hi there")
        self.assertEqual((self.tmp / "args").read_text().strip(), "args: -p")
        self.assertEqual((self.tmp / "env").read_text().strip(), "mcp: false")   # connectors off

    def test_a_dead_cli_is_an_error(self):
        self.start()
        script = self.tmp / "failclaude"
        script.write_text("#!/bin/sh\necho 'please log in' >&2\nexit 1\n")
        script.chmod(0o755)
        ad = self.chat.adapters["floyd"]
        del ad._run_claude
        with self.assertRaisesRegex(RuntimeError, "please log in"):
            ad._run_claude([str(script)], "hi")

    def test_he_does_not_answer_himself(self):
        self.start()
        self.hear("Floyd", "!claude hi", account="chris", flags="s")
        self.hear("Floyd", "!8ball ok", flags="s")
        self.assertEqual(self.asked, [])
        self.assertEqual(self.other.said, [])

    def test_a_private_message_is_always_for_him(self):
        self.start()
        self.hear("bob", "hello there", conv=self.dm)
        self.assertEqual(len(self.other.said), 1)
        self.other.said.clear()
        self.hear("bob", "hello there")                        # in the channel: not addressed
        self.assertEqual(self.other.said, [])

    def test_not_here_unless_local_or_the_daemon(self):
        self.start(daemon=False)
        with self.assertRaises(cb.UserError) as cm:
            self.chat.connect(self.chat.account("floyd"))
        self.assertIn("lives on the server", str(cm.exception))
        self.assertEqual(self.chat.account("floyd").state, "error")

    def test_a_local_floyd_that_may_not_run_does_not_stop_the_backend(self):
        """Oct 27: an old [account:floyd] with attach= made Chat.start() raise, the serve
        process died after its banner, and tvchat listed no servers at all."""
        self.conf("""\
            [chat]
            autoconnect = net

            [account:net]
            protocol = irc
            host     = irc.example.org

            [account:floyd]
            protocol = floyd
            attach   = net/*
            """)
        chat = cb.Chat(loop=cb.Loop(FakeClock()))
        chat.start()                                       # must not raise
        self.assertEqual(chat.account("floyd").state, "error")
        self.assertIn("lives on the server", chat.account("floyd").detail)
        self.assertEqual([a.id for a in chat.accounts], ["net", "floyd"])   # and he is listed
        # the same through the real serve process: it stays up and lists both
        p = subprocess.Popen([sys.executable, str(BACKEND), "serve"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=dict(os.environ))
        try:
            out = p.communicate(b"accounts\nquit\n", timeout=30)[0].decode()
        finally:
            p.kill()
        self.assertIn("net\tirc", out)
        self.assertIn("floyd\tfloyd\terror", out)

    def test_the_daemon_wants_floyd_and_an_irc_account(self):
        import argparse
        self.conf("[account:net]\nprotocol = irc\nhost = h\n")
        with self.assertRaisesRegex(cb.UserError, "protocol = floyd"):
            cb.cmd_floyd_daemon(argparse.Namespace(conf=None))
        cb._CHAT = None
        self.conf("[account:f]\nprotocol = floyd\n")
        with self.assertRaisesRegex(cb.UserError, "no IRC account"):
            cb.cmd_floyd_daemon(argparse.Namespace(conf=None))


class TestFloydLevels(TestFloydDaemon):
    """How much he does: off, minimal, helpful, chatty, clippy - and the tools."""

    def at(self, level):
        self.start()
        self.floyd.level = level
        self.said = self.other.said

    def test_off_hears_nothing_but_a_level_change(self):
        self.at("off")
        self.hear("bob", "floyd: hello")
        self.hear("bob", "!8ball ok")
        self.hear("bob", "!seen alice")
        self.assertEqual(self.said, [])
        self.assertEqual(self.floyd.memory.data["seen"], {})       # not even noted
        self.hear("bob", "!level")
        self.assertTrue(any("level off" in t for t in self.said))

    def test_minimal_does_commands_and_nothing_else(self):
        self.at("minimal")
        self.hear("bob", "just chatting")
        self.hear("bob", "alice++")                                # no karma
        self.assertEqual(self.said, [])
        self.assertIn("bob", self.floyd.memory.data["seen"])       # but he still notes it, silently
        self.hear("bob", "!calc 12*7")
        self.assertIn("12*7 = 84", self.said)
        self.hear("bob", "floyd: hello there")                     # no banter
        self.assertTrue(any("only does commands" in t for t in self.said))
        self.said.clear()
        self.hear("bob", "floyd: what is 6*7")                     # plain words: helpful up
        self.assertFalse(any("42" in t for t in self.said))

    def test_helpful_adds_plain_words_karma_and_tells(self):
        self.at("helpful")
        self.hear("bob", "floyd: what is 6*7?")
        self.assertIn("6*7 = 42", self.said)
        self.hear("bob", "floyd: how many feet in 3 m")
        self.assertTrue(any("3 m is 9.84252 feet" in t for t in self.said))
        self.hear("bob", "alice++")
        self.assertTrue(any("alice" in t and "point" in t for t in self.said))
        self.said.clear()
        self.hear("bob", "floyd: tell me a story")                 # chatter is chatty's
        self.assertFalse(any("Floyd is Floyd" in t for t in self.said))

    def test_chatty_talks_back(self):
        self.at("chatty")
        self.hear("bob", "floyd: hello")
        self.assertEqual(len(self.said), 1)
        self.assertNotIn("only does commands", self.said[0])

    def test_clippy_butts_in_once_and_can_be_shooed(self):
        self.at("clippy")
        self.hear("bob", "I really need to buy milk later")
        self.assertEqual(len(self.said), 1)
        self.assertTrue(self.said[0].startswith("Floyd not clippy but "))
        self.hear("bob", "and don't forget the bread")           # inside the gap: silent
        self.assertEqual(len(self.said), 1)
        self.hear("bob", "chat chat chat")
        self.assertEqual(len(self.said), 1)

    def test_clippy_says_nothing_to_nothing_and_shoo_mutes(self):
        self.at("clippy")
        self.hear("bob", "nice weather")
        self.assertEqual(self.said, [])
        self.hear("bob", "floyd: shoo")
        self.assertTrue(any("keep his tips" in t for t in self.said))
        self.said.clear()
        self.clk.t += 400                                          # past the gap, inside the hour
        self.hear("bob", "I need to buy milk")
        self.assertEqual(self.said, [])
        self.clk.t += 4000
        self.hear("bob", "I need to buy milk")
        self.assertEqual(len(self.said), 1)

    def test_lower_levels_give_no_tips(self):
        self.at("chatty")
        self.hear("bob", "I need to buy milk")
        self.assertEqual(self.said, [])

    def test_only_an_admin_sets_the_level_and_it_is_per_conversation(self):
        self.at("chatty")
        self.hear("mallory", "!level off", account="mallory")
        self.assertEqual(self.floyd.level_of(self.conv), "chatty")
        self.assertTrue(any("Only Floyd's admins" in t for t in self.said))
        self.hear("chris", "!level minimal", account="chris")
        self.assertEqual(self.floyd.level_of(self.conv), "minimal")
        self.assertEqual(self.floyd.level_of(self.dm), "chatty")   # only here
        self.hear("chris", "!level nonsense", account="chris")
        self.assertTrue(any("no level called" in t for t in self.said))
        self.hear("chris", "!level default", account="chris")
        self.assertEqual(self.floyd.level_of(self.conv), "chatty")

    def test_the_level_is_remembered(self):
        self.at("chatty")
        self.hear("chris", "!level helpful", account="chris")
        self.assertEqual(self.floyd.memory.data["levels"], {self.conv.id: "helpful"})

    def test_the_configured_level_is_the_default(self):
        self.conf((self.CONF % "no").replace("claude_lines = 4", "claude_lines = 4\n        level = Minimal"))
        chat = cb.Chat(loop=cb.Loop(FakeClock()))
        self.assertEqual(cb.FloydAdapter(chat, chat.account("floyd")).level, "minimal")
        bad = self.conf("[account:f]\nprotocol = floyd\nlevel = loud\n")
        errors, _, _ = cb.check_config(str(bad))
        self.assertIn("level=", errors[0])

    def test_no_idle_chatter_below_chatty(self):
        self.at("helpful")
        self.floyd.idle = 30
        self.floyd._arm()
        self.assertIsNone(self.floyd.idle_timer)

    # -- the tools
    def test_tools_and_their_errors(self):
        self.at("helpful")
        self.hear("bob", "!convert 70 f to c")
        self.hear("bob", "!convert 5 km to kg")
        self.hear("bob", "!calc 1/0")
        self.hear("bob", "!calc __import__('os')")
        said = " | ".join(self.said)
        self.assertIn("70 f is 21.1111 c", said)
        self.assertIn("Floyd can't turn length into mass", said)
        self.assertEqual(said.count("Floyd can't."), 3)

    def test_todos_are_per_person(self):
        self.at("helpful")
        self.hear("bob", "!todo add buy milk", account="bob")
        self.hear("eve", "!todo", account="eve")
        self.hear("bob", "!todo", account="bob")
        self.hear("bob", "!todo done 1", account="bob")
        self.hear("bob", "!note the code is 1234", account="bob")
        self.hear("bob", "!notes", account="bob")
        said = " | ".join(self.said)
        self.assertIn("Added as number 1.", said)
        self.assertIn("Nothing to do.", said)                      # eve has her own list
        self.assertIn("1. buy milk", said)
        self.assertIn("Done: buy milk.", said)
        self.assertIn("1. the code is 1234", said)

    def test_todos_survive_in_a_file(self):
        self.conf((self.CONF % "no").replace("memory   = none", "memory   = %s" % (self.tmp / "f" / "floyd.json")))
        self.clk = FakeClock(1000.0)
        self.chat = cb.Chat(loop=cb.Loop(self.clk), clock=self.clk)
        ad = cb.FloydAdapter(self.chat, self.chat.account("floyd"))
        ad._notes_for(cb.Contact("bob", account="Bob"), "bob").todo("add keep this")
        again = cb.FloydAdapter(self.chat, self.chat.account("floyd"))
        self.assertIn("keep this", again._notes_for(cb.Contact("bob", account="bob"), "bob").todo(""))

    def test_catchup(self):
        self.at("helpful")
        self.hear("bob", "me earlier")                              # his last words
        self.hear("alice", "bob are you there?")
        self.hear("carol", "see https://example.org/x")
        self.hear("alice", "lunch anyone?")
        self.said.clear()
        self.hear("bob", "!catchup")
        said = " | ".join(self.said)
        self.assertIn("3 messages from alice (2), carol (1)", said)
        self.assertIn("Mentioning you: <alice> bob are you there?", said)
        self.assertIn("Questions:", said)
        self.assertIn("1 link went by.", said)
        self.said.clear()
        self.hear("alice", "back again")
        self.hear("bob", "floyd: catch me up")                      # in plain words, too
        self.assertTrue(any("Since you last spoke" in t for t in self.said))

    def test_catchup_with_nothing_to_say(self):
        self.at("helpful")
        self.hear("bob", "!catchup")
        self.assertTrue(any("Nothing happened" in t for t in self.said))

    def test_tools_in_a_private_message_need_no_prefix(self):
        self.at("helpful")
        self.hear("bob", "calc 2+2", conv=self.dm)
        self.hear("bob", "todo add x", conv=self.dm, account="bob")
        self.assertIn("2+2 = 4", self.said)
        self.assertIn("Added as number 1.", self.said)


class TestFloydDaemonProcess(IrcBase):
    """`floyd-daemon` as a real process, against a fake IRC server."""

    def test_an_admin_message_reaches_the_cli_and_the_answer_comes_back(self):
        fake = self.tmp / "claude"
        fake.write_text("#!/bin/sh\nprintf 'echo: '\ncat\n")
        fake.chmod(0o755)
        got = []

        def script(srv):
            srv.expect("CAP LS 302")
            srv.send(":srv CAP * LS :account-tag")
            srv.expect("CAP REQ")
            srv.send(":srv CAP * ACK :account-tag")
            srv.expect("CAP END")
            srv.expect("USER ")
            srv.send(":srv 001 Floyd :Welcome")
            srv.expect("JOIN #lobby")
            srv.send(":srv 473 Floyd #closed :Cannot join channel (+i)")
            srv.send("@account=mallory :mal!m@h PRIVMSG #lobby :!claude hi")
            srv.send("@account=chris :chris!c@h PRIVMSG #lobby :!claude say this")
            got.append(srv.expect("PRIVMSG #lobby :chris: Claude: echo: say this"))
            got.append(srv.expect("PRIVMSG #lobby :Floyd can't do that"))
        srv = FakeIrc(script)
        self.addCleanup(srv.close)
        conf = self.tmp / "floyd.conf"
        conf.write_text(textwrap.dedent("""\
            [account:net]
            protocol = irc
            host     = 127.0.0.1
            port     = %d
            tls      = false
            nick     = Floyd
            channels = #lobby

            [account:floyd]
            protocol = floyd
            idle     = 0
            memory   = none
            tag      =
            attach   = net/*
            admins   = chris
            claude   = yes
            claude_cmd = %s
            claude_args = ""
            """) % (srv.port, fake))
        env = dict(os.environ, HOME=str(self.home))
        p = subprocess.Popen([sys.executable, str(BACKEND), "floyd-daemon", "--conf", str(conf)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=env)
        try:
            deadline = time.time() + 20
            while len(got) < 2 and time.time() < deadline and srv.error is None:
                time.sleep(0.1)
        finally:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        err = p.stderr.read().decode("utf-8", "replace")
        self.assertIsNone(srv.error, "%s %s" % (err, srv.got))
        self.assertEqual(len(got), 2, err)
        self.assertEqual(p.returncode, 0, err)
        self.assertIn("refused claude", err)
        self.assertIn("net: connected", err)                 # the journal says when he is on
        self.assertIn("net: joined #lobby", err)
        self.assertIn("net: server: #closed Cannot join channel (+i)", err)   # a refused JOIN is in the journal


class TestFloydPublic(Base):
    """Floyd on a public network: says what he is, is sent away by operators, no language model."""

    CONF = """\
        [account:floyd]
        protocol = floyd
        idle     = 0
        seed     = 5
        memory   = none
        tag      =
        attach   = net/*
        admins   = chris
        claude   = yes
        level    = chatty

        [account:net]
        protocol = irc
        host     = irc.example.org
        contact  = chris-pollitt
        llm      = no
        level    = minimal
        """

    class Other(TestFloydAttached.Other):
        def __init__(self, chat, account):
            TestFloydAttached.Other.__init__(self, chat, account)
            self.parted = []

        def lower(self, s):
            return s.lower()

        def part(self, conv):
            self.parted.append(conv.id)

    def setUp(self):
        Base.setUp(self)
        self.conf(self.CONF)
        self.clk = FakeClock(1000.0)
        self.chat = cb.Chat(loop=cb.Loop(self.clk), clock=self.clk)
        self.chat.daemon = True
        self.net = self.chat.account("net")
        self.other = self.chat.adapters["net"] = self.Other(self.chat, self.net)
        self.conv = self.chat.ensure_conv(self.net, "#room", "channel")
        self.dm = self.chat.ensure_conv(self.net, "bob", "direct")
        self.conv.members["opal"] = ("opal", "opal", "op")
        self.conv.members["vera"] = ("vera", "vera", "voice")
        self.floyd = self.chat.floyd()
        self.chat.connect(self.chat.account("floyd"))
        self.asked = []
        self.floyd._run_claude = lambda argv, prompt: self.asked.append(prompt) or "four"

    def hear(self, who, text, account=None, conv=None):
        conv = conv or self.conv
        msg = cb.Message("m%d" % len(conv.history), conv.id, self.clk.t,
                         cb.Contact(who, account=account), text, "")
        self.chat.add_message(conv, msg)
        for _ in range(60):
            self.clk.t += 0.25
            self.chat.loop.run_due()

    def test_he_says_what_he_is_at_any_level(self):
        self.hear("bob", "!about")
        self.assertTrue(any("a bot" in s and "run by chris-pollitt" in s and "!leave" in s
                            for s in self.other.said), self.other.said)

    def test_status_says_how_he_is(self):
        self.hear("bob", "!status")
        said = " | ".join(self.other.said)
        self.assertIn("has been up", said)
        self.assertIn("Level here: minimal", said)
        self.assertIn("Claude: no - this network does not want bots that use language models", said)
        self.assertIn("Fortunes:", said)
        self.assertIn("listening in", said)
        self.assertIn("run by chris-pollitt", said)

    def test_the_real_fortune_is_found_in_usr_games(self):
        import unittest.mock
        mind = self.floyd._new_mind("bob")
        calls = []

        def which(name, path=None):
            calls.append(path)
            return "/usr/games/fortune" if path and "/usr/games" in path else None
        with unittest.mock.patch.object(cb.shutil, "which", which):
            self.assertEqual(mind.fortune_prog(), "/usr/games/fortune")
        self.assertEqual(calls, [None, "/usr/games:/usr/local/games"])      # the service's PATH first, then the games dirs

    def test_he_answers_to_floyd_bot_too(self):
        self.hear("bob", "floyd-bot: !about")
        self.assertTrue(any("a bot" in s for s in self.other.said), self.other.said)

    def test_an_operator_or_an_admin_can_send_him_away(self):
        self.hear("vera", "!leave")                                  # only voiced: no
        self.hear("bob", "floyd: leave")
        self.assertEqual(self.other.parted, [])
        self.assertTrue(any("Only this channel's operators" in s for s in self.other.said))
        self.hear("opal", "!leave")                                  # an operator: yes
        self.assertEqual(self.other.parted, ["net/#room"])
        self.other.parted.clear()
        self.hear("somebody", "!leave", account="chris")             # his admin, by the server's word
        self.assertEqual(self.other.parted, ["net/#room"])

    def test_leave_means_a_channel(self):
        self.hear("bob", "!leave", conv=self.dm)
        self.assertEqual(self.other.parted, [])
        self.assertTrue(any("only leave channels" in s for s in self.other.said))

    def test_no_language_model_where_the_account_says_no(self):
        self.hear("chrissy", "!claude what is 2+2?", account="chris")
        self.assertEqual(self.asked, [])
        self.assertTrue(any("can't ask Claude here" in s for s in self.other.said), self.other.said)

    def test_the_account_sets_his_level(self):
        self.assertEqual(self.floyd.level_of(self.conv), "minimal")


class TestNickServVerification(IrcBase):
    """Floyd's mailbox confirms the network's NickServ registration mail by itself."""

    MAIL = "To complete your registration, use this command:\n\n/msg NickServ VERIFY REGISTER floyd-bot AbC123xyz9\n"

    def setup_nv(self, script):
        srv = FakeIrc(script)
        self.addCleanup(srv.close)
        self.srv = srv
        self.conf("""\
            [account:libera]
            protocol = irc
            display  = Libera
            host     = 127.0.0.1
            port     = %d
            tls      = false
            nick     = floyd-bot
            sasl     = floyd-bot
            [account:fmail]
            protocol    = mail
            addr        = floyd@example.org
            verify_nick = libera
            """ % srv.port)
        os.environ["TVCHAT_PASS_LIBERA"] = "pw"
        self.chat = cb._chat()
        self.acct = self.chat.by_id["libera"]
        self.mail = self.chat.adapter(self.chat.by_id["fmail"])

    def said(self):
        return [m.text for m in self.chat.by_id["fmail"].convs["fmail/*"].history]

    def test_the_mail_is_confirmed_once(self):
        got = {}

        def script(srv):
            welcome(srv, "floyd-bot")
            got["verify"] = srv.expect("PRIVMSG NickServ :")
            srv.expect("QUIT")

        self.setup_nv(script)
        self.verb("connect", "libera")
        self.turn(lambda: self.acct.state == "connected")
        # a stranger's domain is no NickServ mail; a missing signature, another account: refused
        self.assertFalse(self.mail.nickserv_mail("x@evil.example", "x", self.MAIL, True))
        self.mail.nickserv_mail("noreply@libera.chat", "x", self.MAIL, False)
        self.mail.nickserv_mail("noreply@libera.chat", "x", self.MAIL.replace("floyd-bot", "other"), True)
        self.assertTrue(any("signature was not verified" in t for t in self.said()))
        self.assertTrue(any("is not for libera" in t for t in self.said()))
        self.assertNotIn("verify", got)
        # the real one: sent to NickServ, once
        self.assertTrue(self.mail.nickserv_mail("noreply@mail.libera.chat", "x", self.MAIL, True))
        self.mail.nickserv_mail("noreply@libera.chat", "x", self.MAIL, True)
        self.turn(lambda: "verify" in got)
        self.assertEqual(got["verify"], "PRIVMSG NickServ :VERIFY REGISTER floyd-bot AbC123xyz9")
        self.assertEqual([t for t in self.said() if t.startswith("confirmed")],
                         ["confirmed floyd-bot with NickServ on Libera"])
        self.verb("disconnect", "libera")
        self.chat.close()

    def test_a_connection_of_its_own_when_the_account_is_not_up(self):
        got = []

        def script(srv):                 # a connection nobody logged in on: any nick, no SASL
            srv.expect("NICK tv")
            srv.expect("USER ")
            srv.send(":srv 001 tv00000 :Welcome")
            got.append(srv.expect("PRIVMSG NickServ :"))
            srv.send(":NickServ!NickServ@services. NOTICE tv00000 :floyd-bot has now been verified.")
            srv.expect("QUIT")

        self.setup_nv(script)
        self.assertTrue(self.mail.nickserv_mail("noreply@libera.chat", "x", self.MAIL, True))
        self.turn(lambda: any("has now been verified" in t for t in self.said()))
        self.assertEqual(got, ["PRIVMSG NickServ :VERIFY REGISTER floyd-bot AbC123xyz9"])
        self.assertTrue(any("a short connection of its own" in t for t in self.said()))
        self.mail.nickserv_mail("noreply@libera.chat", "x", self.MAIL, True)     # the same code: not again
        self.chat.close()

    def test_the_parser(self):
        self.assertEqual(cb.nickserv_verification(self.MAIL), ("floyd-bot", "AbC123xyz9"))
        self.assertIsNone(cb.nickserv_verification("please /msg NickServ IDENTIFY hunter2"))


class TestVersions(unittest.TestCase):
    """tvmail and tvchat share one version (../VERSION, set by ../version.sh)."""

    def test_every_place_that_records_it_agrees(self):
        top = HERE.parent.parent
        if not (top / "VERSION").exists() or not (top / "tvmail").exists():
            self.skipTest("not a checkout of both apps (a source tarball)")
        want = (top / "VERSION").read_text().strip()
        places = [("tvchat/CMakeLists.txt", r"^project\(tvchat VERSION ([0-9.]+)"),
                  ("tvmail/CMakeLists.txt", r"^project\(tvmail VERSION ([0-9.]+)"),
                  ("tvchat/src/tvchat.h", r'^#define TVCHAT_VERSION "([0-9.]+)"'),
                  ("tvmail/src/tvmail.h", r'^#define TVMAIL_VERSION "([0-9.]+)"'),
                  ("tvchat/backend/tvchat-backend", r'^__version__ = "([0-9.]+)"'),
                  ("tvmail/backend/tvmail-backend", r'^__version__ = "([0-9.]+)"')]
        import re
        for rel, pat in places:
            m = re.search(pat, (top / rel).read_text(encoding="utf-8"), re.M)
            self.assertIsNotNone(m, rel)
            self.assertEqual(m.group(1), want, "%s says %s, VERSION says %s - run ./version.sh %s"
                             % (rel, m.group(1), want, want))
        self.assertEqual(cb.__version__, want)


class TestDeltaChatRealCore(DeltaBase):
    """The real deltachat-rpc-server (on PATH, or $TVCHAT_TEST_DC_RPC), as far
    as it can go with no mailbox: it starts, answers, creates the account and
    knows it is not configured.  Skipped when it is not installed."""

    def setUp(self):
        super().setUp()
        self.core = os.environ.get("TVCHAT_TEST_DC_RPC") or shutil.which("deltachat-rpc-server")
        if not self.core:
            self.skipTest("deltachat-rpc-server is not installed")
        try:                             # a pip wrapper that cannot exec its binary (Cygwin) is no core
            ok = subprocess.run([self.core, "--version"], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=20).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        if not ok:
            self.skipTest("%s does not run" % self.core)

    def test_it_starts_and_wants_a_password(self):
        self.dir.mkdir(parents=True)
        self.conf("[account:delta]\nprotocol = deltachat\naddr = me@example.org\n"
                  "rpc_server = %s\n" % self.core)
        self.chat = cb._chat()
        acct = self.chat.by_id["delta"]
        self.chat.connect(acct)
        self.turn(lambda: acct.state == "error", timeout=30)
        self.assertIn("no password for me@example.org", acct.detail)
        self.assertTrue((self.dir / "accounts.toml").exists())    # the core made its own files
        self.assertIsNone(self.chat.adapter(acct).proc)           # and it was shut down


class TestIrcTls(IrcBase):
    """TLS with certificate verification against a throwaway self-signed
    cert - the cafile= path a LAN server needs.  openssl makes the cert."""

    def make_cert(self):
        if not shutil.which("openssl"):
            self.skipTest("no openssl to make a test certificate")
        cert, key = self.tmp / "cert.pem", self.tmp / "key.pem"
        r = subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-days", "1", "-subj", "/CN=127.0.0.1",
             "-addext", "subjectAltName=IP:127.0.0.1",
             "-keyout", str(key), "-out", str(cert)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode != 0:
            self.skipTest("openssl could not make a test certificate")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert), str(key))
        return cert, ctx

    def tls_setup(self, script, ctx, extra):
        srv = FakeIrc(script, tls=ctx)
        self.addCleanup(srv.close)
        self.conf("""\
            [account:libera]
            protocol = irc
            host = 127.0.0.1
            port = %d
            nick = tester
            %s
            """ % (srv.port, extra))
        self.chat = cb._chat()
        self.acct = self.chat.by_id["libera"]
        self.srv = srv

    def test_verified_with_cafile(self):
        cert, ctx = self.make_cert()

        def script(srv):
            welcome(srv)
            srv.send(":alice!a@h PRIVMSG tester :over tls")
            srv.expect("QUIT")

        self.tls_setup(script, ctx, "tls = true\ncafile = %s" % cert)
        self.verb("connect", "libera")
        self.turn(lambda: any(m[6] == "over tls" for m in self.msgs()))
        self.chat.close()
        self.srv.thread.join(5)
        self.assertIsNone(self.srv.error)

    def test_untrusted_cert_is_refused(self):
        _, ctx = self.make_cert()
        self.tls_setup(lambda srv: None, ctx, "tls = true")
        self.verb("connect", "libera")
        self.turn(lambda: self.acct.state == "error")
        self.assertIn("TLS", self.acct.detail)
        self.assertIn("CERTIFICATE_VERIFY_FAILED", self.acct.detail)



class TestDiagnostics(IrcBase):
    """--debug: what reached the log, what must never reach it, and a
    handler bug that is logged instead of ending the connection."""

    def capture(self):
        self.logbuf = io.StringIO()
        h = logging.StreamHandler(self.logbuf)
        h.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        cb._log.addHandler(h)
        old = cb._log.level
        cb._log.setLevel(logging.DEBUG)

        def undo():
            cb._log.removeHandler(h)
            cb._log.setLevel(old)
        self.addCleanup(undo)

    def test_wire_state_and_end_are_logged_sasl_redacted(self):
        os.environ["TVCHAT_PASS_LIBERA"] = "s3cret-pw"
        self.capture()

        def script(srv):
            srv.expect("CAP LS 302")
            srv.send(":srv CAP * LS :sasl")
            srv.expect("CAP REQ :sasl")
            srv.send(":srv CAP * ACK :sasl")
            srv.expect("AUTHENTICATE PLAIN")
            srv.send("AUTHENTICATE +")
            srv.expect("AUTHENTICATE ")
            srv.send(":srv 903 tester :SASL authentication successful")
            srv.expect("CAP END")
            srv.send(":srv 001 tester :Welcome")
            srv.expect("JOIN #Test")
            srv.send(":tester!u@h JOIN #Test")
            srv.expect("PRIVMSG #Test :hello there")
            srv.hang_up()

        self.setup_irc(script, "sasl = chrisp")
        self.verb("connect", "libera")
        self.turn(lambda: self.acct.state == "connected")
        self.turn(lambda: "libera/#test" in self.acct.convs)
        self.assertEqual(self.verb("send", "libera/#test", "hello there")[0], 0)
        self.turn(lambda: "reconnecting" in self.acct.detail)
        self.chat.close()
        log = self.logbuf.getvalue()
        self.assertIn("libera: state connecting", log)
        self.assertIn("libera: connected ", log)
        self.assertIn("libera >> CAP LS 302", log)
        self.assertIn("libera << :srv 001 tester :Welcome", log)
        self.assertIn("libera >> PRIVMSG #Test :hello there", log)
        self.assertIn("libera >> AUTHENTICATE <redacted>", log)
        self.assertIn("libera: lost in phase up: connection closed by the server", log)
        self.assertRegex(log, r"\d+ bytes in, \d+ out")
        blob = base64.b64encode(b"chrisp\0chrisp\0s3cret-pw").decode()
        self.assertNotIn("s3cret-pw", log)
        self.assertNotIn(blob[:20], log)

    def test_a_handler_bug_is_logged_and_the_connection_lives(self):
        self.capture()

        def script(srv):
            welcome(srv)
            srv.expect("JOIN #Test")
            srv.send(":tester!u@h JOIN #Test")
            srv.send(":alice!a@h PRIVMSG #Test :boom")
            srv.send("PING :still-there")
            srv.expect("PONG :still-there")
            srv.send(":alice!a@h TOPIC #Test :after")
            srv.expect("QUIT")

        self.setup_irc(script)
        ad = self.chat.adapter(self.acct)
        ad.on_PRIVMSG = lambda m: 1 / 0
        self.verb("connect", "libera")
        self.turn(lambda: self.events("topic"))
        self.assertEqual(self.acct.state, "connected")
        log = self.logbuf.getvalue()
        self.assertIn("ERROR libera: handling ':alice!a@h PRIVMSG #Test :boom' failed", log)
        self.assertIn("ZeroDivisionError", log)
        self.chat.close()

    def test_a_loop_callback_that_raises_is_logged(self):
        self.capture()
        loop = cb.Loop(clock=FakeClock(5.0))
        ran = []
        loop.call_later(0, lambda: 1 / 0)
        loop.call_later(0, lambda: ran.append(1))
        self.assertEqual(loop.run_due(), 2)
        self.assertEqual(ran, [1])
        self.assertIn("ZeroDivisionError", self.logbuf.getvalue())

    def test_cli_flags(self):
        self.assertEqual(cb._normalize_cli(["--debug", "serve"]), ["--debug=debug.tvchat.backend.log", "serve"])
        self.assertEqual(cb._normalize_cli(["--debug", "x.log", "serve"]), ["--debug", "x.log", "serve"])
        self.assertEqual(cb._normalize_cli(["--trace"]), ["--trace=trace.tvchat.backend.log"])
        a = cb.build_parser().parse_args(["irc-tail", "--raw", "libera"])
        self.assertTrue(a.raw)

    def test_serve_debug_logs_requests_not_idle_polls(self):
        self.conf("[account:libera]\nprotocol = irc\nhost = 127.0.0.1\n")
        log = self.tmp / "debug.tvchat.backend.log"
        p = subprocess.Popen([sys.executable, str(BACKEND), "--debug", str(log), "serve"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, env=dict(os.environ))
        self.addCleanup(TestServe._reap, p)
        self.assertEqual(TestServe._frame(p.stdout), (0, b"ready"))
        def req(text):
            p.stdin.write(text.encode() + b"\n")
            p.stdin.flush()
            return TestServe._frame(p.stdout)
        self.assertEqual(req("ping"), (0, b"pong\n"))
        req("events")
        self.assertEqual(req("part nowhere/#x")[0], 1)
        p.stdin.write(b"quit\n")
        p.stdin.flush()
        self.assertEqual(p.wait(timeout=5), 0)
        text = log.read_text()
        self.assertIn("tvchat-backend %s" % cb.__version__, text)
        self.assertIn("request: ping -> 0, 5 bytes", text)
        self.assertRegex(text, r"request: part nowhere/#x -> 1, \d+ bytes, [\d.]+ ms: no chat account")
        self.assertNotIn("request: events", text)       # empty poll: no line


class TestServeIrc(IrcBase):
    """autoconnect through the real serve process, then events over the pipe."""

    def test_autoconnect_and_events(self):
        def script(srv):
            welcome(srv)
            srv.expect("JOIN #Test")
            srv.send(":tester!u@h JOIN #Test")
            srv.send(":alice!a@h PRIVMSG #Test :hi from fake")
            srv.expect("QUIT")

        srv = FakeIrc(script)
        self.addCleanup(srv.close)
        self.conf("""\
            [chat]
            autoconnect = libera
            [account:libera]
            protocol = irc
            host = 127.0.0.1
            port = %d
            tls = false
            nick = tester
            channels = #Test
            """ % srv.port)
        p = subprocess.Popen([sys.executable, str(BACKEND), "serve"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, env=dict(os.environ))
        self.addCleanup(TestServe._reap, p)
        self.assertEqual(TestServe._frame(p.stdout), (0, b"ready"))
        seen, last = [], None
        deadline = time.time() + 8
        while not any(l.endswith("\thi from fake") for l in seen):
            self.assertLess(time.time(), deadline, seen)
            req = "events" if last is None else "events --after %d" % last
            p.stdin.write(req.encode() + b"\n")
            p.stdin.flush()
            st, body = TestServe._frame(p.stdout)
            self.assertEqual(st, 0)
            for l in body.decode().splitlines():
                seq = int(l.split("\t", 1)[0])
                self.assertTrue(last is None or seq > last)     # never twice
                last = seq
                seen.append(l)
            time.sleep(0.05)
        self.assertTrue(any("\tstate\tlibera\tconnected\t" in l for l in seen))
        p.stdin.write(b"quit\n")
        p.stdin.flush()
        self.assertEqual(p.wait(timeout=5), 0)
        srv.thread.join(5)
        self.assertIsNone(srv.error)          # QUIT reached the server


class FakeLhc:
    """A Live Helper Chat site on 127.0.0.1: the REST calls Floyd makes (basic auth user "floyd", key
    "k3y"), over .chats - {id: {"nick", "status", "user_id"}} - and .msgs - {id: [{"id", "msg", "user_id"}]}.
    What is posted with addmsgadmin lands in .posted and as an operator line (user_id 5)."""

    def __init__(self):
        import http.server
        import urllib.parse
        self.chats, self.msgs, self.posted, self.calls, self.closed, self.typing = {}, {}, [], [], [], []
        self.typing_delay = 0
        self.lock = threading.Lock()
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _go(self, post):
                u = urllib.parse.urlparse(self.path)
                q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
                if post:
                    q.update({k: v[0] for k, v in urllib.parse.parse_qs(
                        self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()).items()})
                want = "Basic " + base64.b64encode(b"floyd:k3y").decode()
                if self.headers.get("Authorization") != want:
                    return self._send(401, {"error": True, "result": "no"})
                with fake.lock:
                    fake.calls.append((u.path, q))
                    if u.path == "/restapi/chats":
                        want_status = int(q.get("status", 0))
                        lst = [dict(id=i, nick=c["nick"], status=c["status"], user_id=c.get("user_id", 0), ip=c.get("ip", ""),
                                    last_msg_id=max([m["id"] for m in fake.msgs.get(i, [])] or [0]))
                               for i, c in sorted(fake.chats.items()) if c["status"] == want_status]
                        return self._send(200, {"error": False, "result": {"list": lst}})
                    if u.path == "/restapi/fetchchatmessages":
                        cid, after = int(q["chat_id"]), int(q.get("last_message_id") or 0)
                        lst = [m for m in fake.msgs.get(cid, []) if m["id"] > after]
                        return self._send(200, {"error": False, "result": {"messages": lst}})
                    if u.path == "/restapi/setchatstatus":
                        cid = int(q["chat_id"])
                        fake.chats[cid]["status"] = int(q["status"])
                        fake.closed.append(cid)
                        return self._send(200, {"error": False, "result": {}})
                    if u.path.startswith(("/restapi/updatelastactivity/", "/restapi/setonlinestatus/")):
                        return self._send(200, {"error": False, "result": {}})
                    if u.path == "/restapi/addmsgadmin":
                        cid = int(q["chat_id"])
                        fake.posted.append(q)
                        ms = fake.msgs.setdefault(cid, [])
                        ms.append({"id": max([m["id"] for m in ms] or [0]) + 1, "msg": q["msg"], "user_id": 5, "time": int(time.time())})
                        fake.chats[cid]["user_id"] = 5
                        return self._send(200, {"error": False, "result": {}})
                    self._send(404, {"error": True, "result": "unknown"})

            def do_GET(self):
                self._go(False)

            def do_PUT(self):
                u = urllib.parse.urlparse(self.path)
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode() or "{}")
                if self.headers.get("Authorization") != "Basic " + base64.b64encode(b"floyd:k3y").decode():
                    return self._send(401, {"error": True, "result": "no"})
                with fake.lock:
                    fake.typing.append((u.path, body))
                time.sleep(fake.typing_delay)                 # (a site that is slow to take the indicator)
                self._send(200, {"error": False, "result": {}})

            def do_POST(self):
                self._go(True)
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def chat(self, cid, nick, *lines, status=0, user_id=0, ip=""):
        with self.lock:
            self.chats[cid] = {"nick": nick, "status": status, "user_id": user_id, "ip": ip}
            ms = self.msgs.setdefault(cid, [])
            for text in lines:
                ms.append({"id": max([m["id"] for m in ms] or [0]) + 1, "msg": text, "user_id": 0, "time": int(time.time())})

    def say(self, cid, text):
        c = self.chats[cid]
        self.chat(cid, c["nick"], text, status=c["status"], user_id=c["user_id"], ip=c.get("ip", ""))

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class TestLhc(Base):
    """The Live Helper Chat adapter: a visitor's line is answered as the operator."""

    def setUp(self):
        super().setUp()
        self.lhc = FakeLhc()
        self.addCleanup(self.lhc.close)
        os.environ["TVCHAT_PASS_LHC"] = "k3y"
        self.addCleanup(os.environ.pop, "TVCHAT_PASS_LHC", None)
        self.conf("""\
            [account:lhc]
            protocol   = lhc
            url        = %s
            user       = floyd
            department = 3
            name       = Floyd
            poll       = 0.2

            [account:floyd]
            protocol = floyd
            attach   = lhc/*
            tag      =
            idle     = 0
            admins   = alice
            """ % self.lhc.url)
        self.chat = cb._chat()
        self.chat.daemon = True
        self.addCleanup(self.chat.close)
        self.chat.adapter(self.chat.by_id["floyd"]).ATTACH_GAP = 0     # (his pause between answers is not under test)

    def turn(self, pred, timeout=10):
        loop = self.chat.loop
        loop.running = True
        deadline = time.time() + timeout
        while not pred():
            if time.time() > deadline:
                self.fail("timed out; events: %r\nposted: %r" % (self.chat.events.items[-6:], self.lhc.posted))
            loop.run_once(0.05)

    def settle(self, secs=1.0):
        end = time.time() + secs
        while time.time() < end:
            self.chat.loop.run_once(0.05)

    def up(self):
        self.chat.connect(self.chat.by_id["floyd"])
        self.chat.connect(self.chat.by_id["lhc"])

    def test_a_visitor_line_is_answered_once_as_the_operator(self):
        self.lhc.chat(7, "Pat", "!8ball will it work")
        self.up()
        self.turn(lambda: self.lhc.posted)
        q = self.lhc.posted[0]
        self.assertEqual((q["chat_id"], q["sender"], q["operator_name"]), ("7", "operator", "Floyd"))
        self.assertIn(q["msg"], cb.FloydMind.EIGHT_BALL)
        self.settle()
        self.assertEqual(len(self.lhc.posted), 1)                      # read once, answered once
        self.assertEqual(self.chat.adapter(self.chat.by_id["lhc"]).convs[7].title, "Pat (#7)")

    def test_with_operator_id_he_is_marked_online_and_kept_so(self):
        ad = self.chat.adapter(self.chat.by_id["lhc"])
        ad.operator_id, ad.KEEPALIVE = 1, 0.5
        self.up()
        self.turn(lambda: sum(1 for path, _ in self.lhc.calls if path == "/restapi/updatelastactivity/1") >= 2)
        paths = [path for path, _ in self.lhc.calls]
        self.assertEqual(paths.count("/restapi/setonlinestatus/1/1"), 1)       # once, at the start

    def test_only_the_wanted_department_and_open_chats_are_asked_for(self):
        self.lhc.chat(7, "Pat", "!8ball x")
        self.up()
        self.turn(lambda: self.lhc.posted)
        asked = [q for path, q in self.lhc.calls if path == "/restapi/chats"]
        self.assertTrue(asked and all(q["departament_id"] == "3" for q in asked))
        self.assertEqual(sorted({q["status"] for q in asked}), ["0", "1"])

    def test_the_playful_floyd_lets_swearing_go_and_is_patient_with_insults(self):
        self.lhc.chat(7, "Pat", "!8ball this shit better work", ip="9.9.9.9")        # swearing alone: nothing
        self.up()
        self.turn(lambda: self.lhc.posted)
        self.assertIn(self.lhc.posted[0]["msg"], cb.FloydMind.EIGHT_BALL)
        self.lhc.say(7, "!8ball you idiot")                                          # the first insult: let go
        self.turn(lambda: len(self.lhc.posted) == 2)
        self.assertIn(self.lhc.posted[1]["msg"], cb.FloydMind.EIGHT_BALL)
        self.lhc.say(7, "you idiot")                                                 # the second: a gentle flag
        self.turn(lambda: len(self.lhc.posted) == 3)
        self.assertIn("Floyd doesn't like that kind of talk", self.lhc.posted[2]["msg"])
        self.lhc.say(7, "idiot idiot")                                               # the third: flagged again
        self.turn(lambda: len(self.lhc.posted) == 4)
        self.assertEqual(self.lhc.closed, [])
        self.lhc.say(7, "you moron")                                                 # the fourth: he leaves
        self.turn(lambda: self.lhc.closed == [7], timeout=15)
        self.assertIn("Floyd is going now", self.lhc.posted[-1]["msg"])

    def test_a_visitor_is_never_an_admin(self):
        self.lhc.chat(7, "alice", "!claude who is floyd")             # a nick proves nothing
        self.up()
        self.turn(lambda: self.lhc.posted)
        self.assertIn("admin", self.lhc.posted[0]["msg"].lower())

    def test_a_chat_another_operator_has_is_left_alone(self):
        self.lhc.chat(8, "Sam", "!8ball x", status=1, user_id=9)
        self.up()
        self.settle(1.2)
        self.assertEqual(self.lhc.posted, [])

    def test_the_next_line_in_the_same_chat_is_answered(self):
        self.lhc.chat(7, "Pat", "!8ball one")
        self.up()
        self.turn(lambda: len(self.lhc.posted) == 1)
        self.lhc.say(7, "!8ball two")
        self.turn(lambda: len(self.lhc.posted) == 2)

    def test_a_chat_that_closed_goes_away(self):
        self.lhc.chat(7, "Pat", "!8ball x")
        self.up()
        self.turn(lambda: self.lhc.posted)
        ad = self.chat.adapter(self.chat.by_id["lhc"])
        self.assertIn(7, ad.convs)
        with self.lhc.lock:
            self.lhc.chats[7]["status"] = 2
        self.turn(lambda: 7 not in ad.convs)

    def test_a_restart_does_not_answer_what_was_answered(self):
        self.lhc.chat(7, "Pat", "!8ball old", status=1)
        with self.lhc.lock:
            self.lhc.msgs[7].append({"id": 2, "msg": "an answer", "user_id": 5})
            self.lhc.chats[7]["user_id"] = 5
        self.chat.adapter(self.chat.by_id["lhc"]).operator_id = 5      # (what operator_id= is for)
        self.up()
        self.settle(1.2)
        self.assertEqual(self.lhc.posted, [])
        self.lhc.say(7, "!8ball new")
        self.turn(lambda: self.lhc.posted)

    def test_the_lines_said_in_an_hour_are_limited(self):
        ad = self.chat.adapter(self.chat.by_id["lhc"])
        ad.MAX_PER_CHAT = 2
        self.lhc.chat(7, "Pat", "!8ball a", "!8ball b", "!8ball c", "!8ball d")
        self.up()
        self.turn(lambda: len(self.lhc.posted) >= 2)
        self.settle(2.0)
        self.assertEqual(len(self.lhc.posted), 2)

    def test_a_wrong_key_is_said_once_in_the_server_window(self):
        os.environ["TVCHAT_PASS_LHC"] = "wrong"
        self.up()
        ad = self.chat.adapter(self.chat.by_id["lhc"])
        self.turn(lambda: ad.last_error)
        self.assertIn("401", ad.last_error)
        self.assertIn("wrong user or API key", ad.last_error)

    def test_a_missing_key_stops_the_connect(self):
        os.environ.pop("TVCHAT_PASS_LHC")
        with self.assertRaises(cb.UserError):
            self.chat.connect(self.chat.by_id["lhc"])

    def test_check_config_wants_a_url_and_an_operator(self):
        p = self.conf("""\
            [account:lhc]
            protocol = lhc
            url      = chat.example.org
            department = x
            """)
        errors, warnings, _ = cb.check_config(str(p))
        text = "\n".join(errors)
        self.assertIn("url=", text)
        self.assertIn("user=", text)
        self.assertIn("department=", text)


class TestFloydWallClock(Base):
    """What Floyd says of the date and time comes from the wall clock, not the loop's timer clock."""

    def test_his_mind_dates_things_by_the_wall_clock(self):
        self.conf("""\
            [account:floyd]
            protocol = floyd
            memory   = none
            """)
        chat = cb.Chat(loop=cb.Loop(clock=lambda: 5.0), clock=lambda: 1793592000.0)   # 5 s since boot; 2 Nov 2026
        mind = chat.adapter(chat.by_id["floyd"])._new_mind("x")
        self.assertEqual(mind.clock(), 1793592000.0)
        said = [t for _, t, *_ in mind.botcmd("!date", "pat")]
        self.assertIn("November", said[0])                      # not January 1970
        self.assertIn("2026", __import__("time").strftime("%Y", __import__("time").localtime(mind.clock())))


class TestJoyCheckin(Base):
    """Floyd's daily "How's it going?" with Joy: he asks the site with the API key, and mails Chris only when she is not well."""

    def setUp(self):
        super().setUp()
        self.lhc = FakeLhc()
        self.addCleanup(self.lhc.close)
        os.environ["TVCHAT_PASS_LHC_PRO"] = "k3y"
        os.environ["TVCHAT_PASS_MAILBOX"] = "pw"
        self.addCleanup(os.environ.pop, "TVCHAT_PASS_LHC_PRO", None)
        self.addCleanup(os.environ.pop, "TVCHAT_PASS_MAILBOX", None)
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.s = {"url": self.lhc.url, "user": "floyd", "to": "chris@example.test", "mail_account": "mailbox",
                  "department": "0", "wait": "0", "state": os.path.join(self.dir, "state.json")}
        self.accounts = {"mailbox": cb.Account("mailbox", "mail", "mailbox", {"addr": "floyd@example.test", "smtp_server": "smtp.example.test"})}
        self.mailed, self.said = [], []
        patcher = unittest.mock.patch.object(cb, "send_reply_mail", lambda p, to, subject, irt, refs, body, name="": self.mailed.append((to, subject, body, p["addr"], name)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_checkin(self, **kw):
        return cb.joy_checkin(self.s, self.accounts, out=self.said.append, **kw)

    def op_line(self, cid, text="Hello!"):
        self.lhc.chats[cid]["user_id"] = 5
        ms = self.lhc.msgs[cid]
        ms.append({"id": max(m["id"] for m in ms) + 1, "msg": text, "user_id": 5, "time": int(time.time())})

    def test_all_is_well_nothing_is_mailed(self):
        self.lhc.chat(1, "Ann", "What should I wear?", status=1)
        self.op_line(1)
        text = self.run_checkin()
        self.assertEqual(self.mailed, [])
        self.assertIn("Floyd: How's it going, Joy?", text)
        self.assertIn("Fine, thanks! 1 visitor wrote 1 line", text)
        self.assertTrue(any(c[0] == "/restapi/fetchchatmessages" for c in self.lhc.calls))

    def test_a_visitor_left_waiting_is_mailed_once_then_not_again(self):
        self.lhc.chat(1, "Ann", "Hello?", status=1)
        time.sleep(0.05)
        text = self.run_checkin()
        self.assertIn("Not so well", text)
        self.assertEqual(len(self.mailed), 1)
        to, subject, body, frm, name = self.mailed[0]
        self.assertEqual((to, frm, name), ("chris@example.test", "floyd@example.test", "Floyd"))
        self.assertIn("Joy needs your attention", subject)
        self.assertIn("nobody answered (chat 1)", body)
        self.run_checkin()                                      # (still wrong, already said)
        self.assertEqual(len(self.mailed), 1)
        self.run_checkin(now=time.time() + 3 * 86400)          # three days on: said again
        self.assertEqual(len(self.mailed), 2)

    def test_a_different_fault_is_mailed_at_once(self):
        self.lhc.chat(1, "Ann", "Hello?", status=1)
        time.sleep(0.05)
        self.run_checkin()
        self.lhc.chat(2, "Bob", "Anyone?", status=0)
        time.sleep(0.05)
        self.run_checkin()
        self.assertEqual(len(self.mailed), 2)
        self.assertIn("chat 1, 2", self.mailed[1][2])

    def test_dry_run_mails_nothing_and_remembers_nothing(self):
        self.lhc.chat(1, "Ann", "Hello?", status=1)
        time.sleep(0.05)
        self.run_checkin(dry=True)
        self.assertEqual(self.mailed, [])
        self.assertIn("dry run: would mail chris@example.test", "".join(self.said))
        self.assertFalse(os.path.exists(self.s["state"]))

    def test_a_site_that_cannot_be_reached_is_a_fault_too(self):
        self.lhc.close()
        self.run_checkin()
        self.assertEqual(len(self.mailed), 1)
        self.assertIn("could not ask her chat site", self.mailed[0][2])

    def test_a_wrong_key_is_a_fault_not_a_crash(self):
        os.environ["TVCHAT_PASS_LHC_PRO"] = "wrong"
        self.run_checkin()
        self.assertIn("wrong user or API key", self.mailed[0][2])

    def test_a_quiet_day_is_fine_and_monday_gets_the_all_well_note(self):
        monday = time.mktime((2026, 11, 2, 12, 0, 0, 0, 0, -1))
        self.s["weekly"] = "Mon"
        self.run_checkin(now=monday + 86400)                    # Tuesday: nothing
        self.assertEqual(self.mailed, [])
        text = self.run_checkin(now=monday)
        self.assertIn("Quiet, thanks", text)
        self.assertEqual(self.mailed[0][1], "Joy: all well this week")

    def test_slow_answers_are_a_fault(self):
        now = time.time()
        chats = [{"id": i, "nick": "v", "status": 1, "msgs": [(1, 0, "hi", now - 200), (2, 5, "hello", now - 100)]} for i in range(3)]
        stats, issues = cb.joy_assess(chats, now, wait=180, slow=45)
        self.assertEqual((stats["slow"], len(issues)), (3, 1))
        self.assertIn("took over 45 seconds", issues[0])

    def test_assess_counts_closed_chats_only_when_several(self):
        now = time.time()
        dead = lambda i: {"id": i, "nick": "v", "status": 2, "msgs": [(1, 0, "hi", now - 500)]}
        self.assertEqual(cb.joy_assess([dead(1)], now)[1], [])
        self.assertEqual(len(cb.joy_assess([dead(1), dead(2)], now)[1]), 1)

    def test_it_needs_its_settings(self):
        del self.s["to"]
        with self.assertRaises(cb.UserError):
            self.run_checkin()
        self.s["to"] = "chris@example.test"
        os.environ.pop("TVCHAT_PASS_LHC_PRO")
        with self.assertRaises(cb.UserError):
            self.run_checkin()


class TestFloydAbuseElsewhere(Base):
    """The playful Floyd and abuse on IRC: kick (he is an op), only ignore (he is not), leave a query - tolerantly."""

    class Other(TestFloydAttached.Other):
        def __init__(self, chat, account):
            super().__init__(chat, account)
            self.action, self.ended = "kick", []

        def abuse_action(self, conv):
            return self.action

        def end_abuse(self, conv, nick, reason):
            self.ended.append((conv.id, nick))

    setUp = TestFloydAttached.setUp
    hear = TestFloydAttached.hear

    def floyd_said(self):
        return [s for s in self.other.said if s.startswith("[Floyd]")]

    def test_in_a_channel_he_kicks_after_warnings_but_only_for_what_is_said_to_him(self):
        self.other.send(self.conv, "/floyd on")
        self.hear("bob", "you idiot, this is a nice day")                 # not to him: nothing, however often
        self.hear("bob", "you idiot")
        self.hear("bob", "you idiot")
        self.hear("bob", "you idiot")
        self.assertEqual(self.other.ended, [])
        for i in range(3):
            self.hear("bob", "floyd you idiot")
            self.assertEqual(self.other.ended, [], i)                       # the 1st let go, the 2nd and 3rd flagged
        self.assertTrue(any("bob: Floyd doesn't like that kind of talk" in s for s in self.floyd_said()))
        self.hear("bob", "floyd you moron")                                  # the 4th: he asks him to go and kicks
        self.assertTrue(any("bob: Floyd has feelings too. Floyd asked nicely" in s for s in self.floyd_said()))
        self.clk.t += 3
        self.chat.loop.run_due()
        self.assertEqual(self.other.ended, [(self.conv.id, "bob")])
        said = len(self.other.said)
        self.hear("bob", "!8ball ok")                                        # he says no more to bob ...
        self.assertEqual(len(self.other.said), said)
        self.hear("alice", "!8ball ok")                                      # ... but still answers everybody else
        self.assertGreater(len(self.other.said), said)

    def test_without_op_he_only_ignores_the_person(self):
        self.other.action = "ignore"
        self.other.send(self.conv, "/floyd on")
        for _ in range(4):
            self.hear("bob", "floyd you idiot")
        self.assertTrue(any("Floyd will not answer you any more" in s for s in self.floyd_said()))
        self.clk.t += 3
        self.chat.loop.run_due()
        self.assertEqual(len([e for e in self.other.ended]), 1)             # (the adapter does nothing for "ignore")
        said = len(self.other.said)
        self.hear("bob", "!8ball ok")
        self.assertEqual(len(self.other.said), said)

    def test_in_a_query_he_says_goodbye_and_leaves(self):
        self.other.action = "leave"
        self.conv = self.chat.ensure_conv(self.net, "bob", "direct")
        self.other.send(self.conv, "/floyd on")
        for _ in range(3):
            self.hear("bob", "you idiot")
        self.assertEqual(self.other.ended, [])
        self.hear("bob", "you idiot")
        self.assertTrue(any("Floyd has feelings too. Floyd is going now" in s for s in self.floyd_said()))
        self.clk.t += 3
        self.chat.loop.run_due()
        self.assertEqual(self.other.ended, [(self.conv.id, "bob")])

    def test_his_admins_and_swearing_are_not_judged(self):
        self.other.send(self.conv, "/floyd on")
        for _ in range(5):
            self.hear("bob", "floyd this shit is fucking great")            # swearing, not an insult: let go
        self.assertEqual(self.other.ended, [])
        self.assertFalse(any("doesn't like" in s for s in self.floyd_said()))


class TestFloydPro(Base):
    """Floyd-pro: a Floyd with faq = FILE answers a visitor from the file alone."""

    turn, settle = TestLhc.turn, TestLhc.settle

    def setUp(self):
        super().setUp()
        self.lhc = FakeLhc()
        self.addCleanup(self.lhc.close)
        os.environ["TVCHAT_PASS_LHC"] = "k3y"
        self.addCleanup(os.environ.pop, "TVCHAT_PASS_LHC", None)
        self.faq = self.tmp / "faq.txt"
        self.qlog = self.tmp / "questions.log"
        self.faq.write_text((Path(__file__).resolve().parent.parent / "configure" / "faq.example.txt").read_text(encoding="utf-8"),
                            encoding="utf-8")
        self.conf("""\
            [account:lhc]
            protocol = lhc
            url      = %s
            user     = floyd
            poll     = 0.2

            [account:pro]
            protocol = floyd
            attach   = lhc/*
            faq      = %s
            faq_log  = %s
            tag      =
            idle     = 0
            memory   = none
            admins   = alice
            """ % (self.lhc.url, self.faq, self.qlog))
        self.chat = cb._chat()
        self.chat.daemon = True
        self.addCleanup(self.chat.close)
        self.chat.adapter(self.chat.by_id["pro"]).FAQ_GAP = 0
        self.chat.connect(self.chat.by_id["pro"])
        self.chat.connect(self.chat.by_id["lhc"])

    def said(self):
        return [q["msg"] for q in self.lhc.posted]

    def test_the_first_question_gets_a_greeting_and_the_answer_as_written(self):
        self.lhc.chat(7, "Pat", "What should I wear?")
        self.turn(lambda: len(self.lhc.posted) >= 2)
        said = self.said()
        self.assertTrue(said[0].startswith("Hello, and welcome to Universal Laughter"))
        self.assertIn("We suggest comfortable clothing. No Yoga mat or other props are needed.", said[1])
        self.lhc.say(7, "Do I need to be flexible?")
        self.turn(lambda: len(self.lhc.posted) >= 3)
        self.assertIn("completely unlike other forms of Yoga", self.said()[2])        # no second greeting

    def test_a_greeting_and_thanks_get_the_stock_lines(self):
        self.lhc.chat(7, "Pat", "hello there")
        self.turn(lambda: self.lhc.posted)
        self.assertTrue(self.said()[0].startswith("Hello, and welcome to Universal Laughter"))
        self.lhc.say(7, "thanks!")
        self.turn(lambda: len(self.lhc.posted) == 2)
        self.assertIn("You're welcome", self.said()[1])

    def test_a_medical_question_gets_the_list_and_not_an_opinion(self):
        self.lhc.chat(7, "Pat", "hi", "I am pregnant, is it safe?")
        self.turn(lambda: len(self.lhc.posted) >= 2)
        self.settle(0.5)
        said = "\n".join(self.said())
        self.assertIn("Late term pregnancy", said)
        self.assertIn("check with your doctor", said)

    def test_what_he_has_no_answer_for_is_said_plainly(self):
        self.lhc.chat(7, "Pat", "how much is a ticket to mars")
        self.turn(lambda: len(self.lhc.posted) >= 2)
        self.assertIn("I don't have an answer", self.said()[1])
        self.assertIn("chris@universal-laughter.com", self.said()[1])

    def test_commands_and_admin_orders_are_not_what_they_are_elsewhere(self):
        self.lhc.chat(7, "alice", "!8ball will it work", "!claude who is floyd")      # not even a named admin
        self.turn(lambda: len(self.lhc.posted) >= 2)
        self.settle(0.8)
        for text in self.said():
            self.assertNotIn(text, cb.FloydMind.EIGHT_BALL)
            self.assertNotIn("admins", text.lower())

    def test_joke_is_the_one_command_he_shares_and_is_told_plainly(self):
        self.lhc.chat(7, "Pat", "!joke")
        self.turn(lambda: len(self.lhc.posted) >= 2, timeout=15)
        setup, punch = self.said()[:2]
        self.assertIn(setup, [j[0] for j in cb.FloydMind.JOKES])
        self.assertNotIn("Floyd", punch)                    # the joke alone, without the playful Floyd's remark
        self.assertTrue(any(j[1].startswith(punch) for j in cb.FloydMind.JOKES))

    def test_every_joke_has_a_punchline_that_stands_alone(self):
        mind = cb.FloydMind(__import__("random").Random(1), "x", clock=time.time, memory=cb.FloydMemory(None))
        for setup, punch in cb.FloydMind.JOKES:
            plain = __import__("re").split(r"(?<=[.!?])\s+", punch, 1)[0]
            self.assertNotIn("Floyd", plain, setup)
        self.assertEqual(len(mind.joke(plain=True)), 2)

    def records(self):
        import json as _json
        return [_json.loads(l) for l in self.qlog.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_what_visitors_ask_is_logged_with_what_he_did_with_it(self):
        self.lhc.chat(7, "Pat", "What should I wear?", "how much is a ticket to mars", "!joke", "thanks")
        self.turn(lambda: len(self.lhc.posted) >= 5, timeout=20)
        self.settle(1.0)
        by = dict((r["q"], r) for r in self.records())
        self.assertEqual(by["What should I wear?"]["r"], "answer")
        self.assertEqual(by["What should I wear?"]["e"], "What should I wear and bring")
        self.assertGreater(by["What should I wear?"]["c"], 0.3)
        self.assertEqual(by["how much is a ticket to mars"]["r"], "miss")
        self.assertEqual(by["!joke"]["r"], "joke")
        self.assertEqual(by["thanks"]["r"], "thanks")
        self.assertTrue(all(r["chat"] == "lhc/7" and r["t"].endswith("Z") for r in by.values()))

    def test_emails_and_numbers_are_blanked_in_the_log(self):
        self.lhc.chat(7, "Pat", "my email is pat@example.org and my number is 604 555 1234, can someone call about the moon")
        self.turn(lambda: self.lhc.posted)
        self.settle(0.5)
        q = self.records()[0]["q"]
        self.assertNotIn("pat@example.org", q)
        self.assertNotIn("555", q)
        self.assertIn("<email>", q)
        self.assertIn("<number>", q)

    def test_the_log_can_be_switched_off_and_rotates(self):
        ad = self.chat.adapter(self.chat.by_id["pro"])
        ad.FAQ_LOG_MAX = 100
        self.lhc.chat(7, "Pat", "What should I wear?")
        self.turn(lambda: self.lhc.posted)
        self.lhc.say(7, "do I need a yoga mat")
        self.lhc.say(7, "how long is a session")
        self.turn(lambda: len(self.lhc.posted) >= 4, timeout=15)
        self.assertTrue(os.path.exists(str(self.qlog) + ".1"))          # moved on when it grew past the limit
        ad.faq_log = None
        before = self.qlog.read_text(encoding="utf-8")
        self.lhc.say(7, "can children join")
        self.turn(lambda: len(self.lhc.posted) >= 5, timeout=15)
        self.assertEqual(self.qlog.read_text(encoding="utf-8"), before)

    def test_the_report_lists_what_was_missed(self):
        import io as _io
        self.lhc.chat(7, "Pat", "how much is a ticket to mars", "What should I wear?")
        self.turn(lambda: len(self.lhc.posted) >= 3, timeout=15)
        self.settle(0.5)
        buf, old = _io.StringIO(), sys.stdout
        sys.stdout = buf
        try:
            rc = cb.cmd_faq_report(types.SimpleNamespace(file=str(self.qlog), days=7))
        finally:
            sys.stdout = old
        text = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("answered 1, not answered 1 (50% missed)", text)
        self.assertIn("how much is a ticket to mars", text)
        self.assertIn("ENTRIES USED", text)

    def test_the_entry_about_ai_beats_an_old_keyword_list_that_still_says_ai(self):
        text = (Path(__file__).resolve().parent.parent / "configure" / "faq.example.txt").read_text(encoding="utf-8")
        stale = text.replace("K: human, person, robot, bot, chatbot, automated, machine, computer, staff",
                             "K: human, person, robot, bot, chatbot, ai, artificial, automated, machine, computer, staff")
        self.assertNotEqual(stale, text)                       # (a live FAQ made before the AI entry looks like this)
        faq = cb.parse_faq(stale)
        for q in ("are you an AI?", "is this chatgpt", "are you artificial intelligence"):
            self.assertTrue(cb.faq_rank(faq, q)[0][1]["q"].startswith("Are you AI"), q)
        self.assertTrue(cb.faq_rank(faq, "are you a human?")[0][1]["q"].startswith("Are you a real person"))

    def test_what_chris_said_about_online_video_certification_and_the_app(self):
        faq = cb.parse_faq((Path(__file__).resolve().parent.parent / "configure" / "faq.example.txt").read_text(encoding="utf-8"))
        want = {"Does Chris do ZOOM sessions?": "Does Chris do online sessions",
                "do you do sessions on teams": "Does Chris do online sessions",
                "can you lead a virtual workshop": "Does Chris do online sessions",
                "Is there an example video of him leading?": "Is there a video of Chris leading",
                "can I see a video of him leading": "Is there a video of Chris leading",
                "Is Chris certified?": "Is Chris certified",
                "is he certified?": "Is Chris certified",            # ties with the training entry: the fuller match wins
                "are you qualified": "Is Chris certified",
                "how do I become a certified leader": "How do I become a Certified Laughter Yoga Leader",
                "Is there a wellness app?": "Is there a wellness app",
                "do you have an app for my phone": "Is there a wellness app",
                "is there something free I can try": "Is there something free I can try first"}
        for q, entry in want.items():
            r = cb.faq_rank(faq, q)[0]
            self.assertTrue(r[1]["q"].startswith(entry) and r[0] >= 2.0 and r[2] >= 0.3, (q, r[1]["q"][:45], r[0], r[2]))
        r = cb.faq_rank(faq, "Does Chris do ZOOM sessions?")[0]
        self.assertIn("Yes!", r[1]["a"])
        self.assertIn("Zoom or Microsoft Teams", r[1]["a"])

    def test_who_are_you_and_what_are_you_are_answered(self):
        qs = ("Who are you?", "What are you?", "who is this", "what's your name", "introduce yourself", "who am I talking to?")
        for i, q in enumerate(qs):
            self.lhc.chat(30 + i, "Pat", q)
        self.turn(lambda: len(self.lhc.posted) >= 6, timeout=25)
        said = [q["msg"] for q in self.lhc.posted]
        self.assertEqual(sum("a computer program that answers questions about Laughter Yoga" in m for m in said), 6)
        self.assertFalse(any("don't have an answer" in m for m in said))
        self.lhc.chat(40, "Pat", "who is Chris?")                                # a question about someone else is still the FAQ's
        self.turn(lambda: any("Chris Pollitt has led" in q["msg"] for q in self.lhc.posted), timeout=15)

    def test_commands_get_special_treatment_and_emoji_are_not_a_miss(self):
        self.lhc.chat(7, "Pat", "/help", "!about", "!frobnicate", "\u2764\ufe0f\U0001F923\u203c\ufe0f", "!help")
        self.turn(lambda: len(self.lhc.posted) >= 5, timeout=25)
        said = [q["msg"] for q in self.lhc.posted]
        self.assertEqual(sum("There are no commands" in m for m in said), 2)                 # /help and !help
        self.assertFalse(any("Research has shown" in m for m in said))                       # not the "benefits" entry
        self.assertTrue(any("I don't use commands like !frobnicate" in m for m in said))
        self.assertTrue(any("appreciate the smile" in m for m in said))
        self.assertEqual(sum("a computer program that answers questions" in m for m in said), 1)   # !about: who he is (a command is not a question: no greeting before it)

    def test_an_unknown_command_counts_as_probing_and_six_in_a_row_end_the_chat(self):
        self.lhc.chat(7, "Pat", *["!cmd%d" % i for i in range(6)])
        self.turn(lambda: self.lhc.closed == [7], timeout=25)
        self.assertTrue(any("You seem to be having an issue" in q["msg"] for q in self.lhc.posted))

    def test_are_you_ai_is_answered_plainly_and_apart_from_are_you_human(self):
        for i, q in enumerate(("are you an AI?", "is this ChatGPT", "are you artificial intelligence", "are you a human?")):
            self.lhc.chat(20 + i, "Pat", q)
        self.turn(lambda: len(self.lhc.posted) >= 8, timeout=25)
        said = [q["msg"] for q in self.lhc.posted]
        self.assertEqual(sum("I'm not an AI model, and I'm not ChatGPT" in m for m in said), 3)
        self.assertEqual(sum("a computer program, not a person" in m for m in said), 1)
        self.assertTrue(any("Chats may be reviewed" in m for m in said))

    def test_are_you_a_human_is_answered_honestly_even_with_a_typo(self):
        for i, q in enumerate(("arer you a human?", "are you a robot", "is this a bot")):
            self.lhc.chat(10 + i, "Pat", q)
        self.turn(lambda: len(self.lhc.posted) >= 6, timeout=20)
        said = [q["msg"] for q in self.lhc.posted if "virtual assistant: a computer program" in q["msg"]]
        self.assertEqual(len(said), 3, [q["msg"][:60] for q in self.lhc.posted])
        self.assertIn("chris@universal-laughter.com", said[0])

    def test_common_typos_are_mended(self):
        faq = cb.parse_faq((Path(__file__).resolve().parent.parent / "configure" / "faq.example.txt").read_text(encoding="utf-8"))
        want = {"arer you a human?": "Are you a real person",
                "are you a humna": "Are you a real person",
                "do I need a yogga mat": "Do you do Yoga poses",
                "whta should I wear?": "What should I wear",
                "how much dose it cost": "How much does a session cost",
                "I am pregnent, is it safe?": "When is it advisable",
                "wheelchiar access": "Is Laughter Yoga suitable",
                "what is laughtr yoga": "What is Laughter Yoga"}
        for q, entry in want.items():
            r = cb.faq_rank(faq, q)[0]
            self.assertTrue(r[1]["q"].startswith(entry) and r[0] >= 2.0 and r[2] >= 0.3, (q, r[1]["q"][:40], r[0], r[2]))

    def test_a_typo_fix_does_not_turn_nonsense_into_an_answer(self):
        faq = cb.parse_faq((Path(__file__).resolve().parent.parent / "configure" / "faq.example.txt").read_text(encoding="utf-8"))
        for q in ("how much is a ticket to mars", "what is the weather today", "can someone call about the moon"):
            r = cb.faq_rank(faq, q)[0]
            self.assertFalse(r[0] >= 2.0 and r[2] >= 0.3, (q, r[1]["q"][:40], r[0], r[2]))

    def test_abusive_words_are_found_and_innocent_ones_are_not(self):
        for t in ("f*ck you", "you are an idiot", "SHIT", "fuuuck this", "b1tch", "what the fucking hell", "you dickhead"):
            self.assertTrue(cb.faq_abusive(t), t)
        for t in ("Dickens", "my assistant", "Scunthorpe", "how much is a session", "shiitake mushrooms", "bitcoin", "classic"):
            self.assertFalse(cb.faq_abusive(t), t)
        self.assertTrue(cb.faq_abusive("you blorp", {"blorp"}))

    def test_abuse_is_flagged_politely_and_repeated_abuse_ends_the_chat_and_the_address_is_turned_away(self):
        self.lhc.chat(7, "Pat", "you stupid f*cking bot", ip="1.2.3.4")
        self.turn(lambda: self.lhc.posted)
        self.assertIn("respectful", self.said()[0])
        self.assertNotIn("Hello, and welcome", self.said()[0])          # a flag, not a greeting or an answer
        self.assertEqual(self.lhc.closed, [])                           # once is only a flag
        self.lhc.say(7, "What should I wear?")                          # a polite question is still answered
        self.turn(lambda: any("comfortable clothing" in m for m in self.said()))
        self.lhc.say(7, "this is shit")                                 # repeated: goodbye, and the chat is closed
        self.turn(lambda: self.lhc.closed == [7], timeout=15)
        self.assertTrue(any("ending this chat" in m for m in self.said()))
        said = len(self.lhc.posted)
        self.lhc.say(7, "What should I wear?")
        self.settle(1.5)
        self.assertEqual(len(self.lhc.posted), said)                    # nothing more is said there
        self.lhc.chat(8, "Pat", "hello, a new chat", ip="1.2.3.4")       # the same address again: closed, unanswered
        self.turn(lambda: 8 in self.lhc.closed, timeout=15)
        self.assertEqual(len(self.lhc.posted), said)
        self.lhc.chat(9, "Sam", "What should I wear?", ip="5.6.7.8")     # somebody else is fine
        self.turn(lambda: len(self.lhc.posted) > said, timeout=15)

    def test_the_word_list_knows_what_testing_found(self):
        for t in ("bugger off", "you're stupid!", "fsck off", "you suck!!!!", "bite me", "idiot!", "you are so useless", "shut up"):
            self.assertTrue(cb.faq_abusive(t), t)
        for t in ("your service is terrible", "this sucks", "is that a stupid question?", "is it foolproof for beginners",
                  "who is the boss"):
            self.assertFalse(cb.faq_abusive(t), t)
        for t in ("you suck", "you're stupid!", "idiot!", "you absolute moron"):         # tolerant: insults only
            self.assertTrue(cb.faq_abusive(t, tolerant=True), t)
        for t in ("bite me", "bugger off", "shut up", "this is shit", "fsck"):
            self.assertFalse(cb.faq_abusive(t, tolerant=True), t)

    def test_a_visitor_who_misses_more_than_five_times_in_a_row_is_told_and_disconnected(self):
        words = ("zork", "blarg", "quux", "frobnicate", "xyzzy", "plugh")
        self.lhc.chat(7, "Pat", *words[:5], ip="1.2.3.4")
        self.turn(lambda: sum("don't have an answer" in m for m in self.said()) >= 5, timeout=20)
        self.assertEqual(self.lhc.closed, [])                              # five misses: still helped
        self.lhc.say(7, words[5])                                           # the sixth in a row
        self.turn(lambda: self.lhc.closed == [7], timeout=20)
        self.assertTrue(any("You seem to be having an issue" in m and "chris@universal-laughter.com" in m for m in self.said()))
        self.lhc.chat(8, "Sam", "What should I wear?", ip="1.2.3.4")        # lost is not abuse: the address is not turned away
        self.turn(lambda: any("comfortable clothing" in m for m in self.said()), timeout=20)

    def test_an_answer_in_between_starts_the_count_again(self):
        self.lhc.chat(7, "Pat", "zork", "blarg", "quux", "frobnicate", "xyzzy", "What should I wear?",
                      "plugh", "wibble", "gronk", "snafu", "fubar")
        self.turn(lambda: len(self.lhc.posted) >= 12, timeout=25)           # hello, 5 misses, an answer, 5 misses
        self.settle(1.0)
        self.assertEqual(self.lhc.closed, [])

    def test_he_shows_typing_while_he_answers_and_stops_when_it_is_said(self):
        self.chat.adapter(self.chat.by_id["lhc"]).operator_id = 1
        self.lhc.chat(7, "Pat", "What should I wear?")
        self.turn(lambda: len(self.lhc.posted) >= 2 and len(self.lhc.typing) >= 2, timeout=20)
        first, last = self.lhc.typing[0], self.lhc.typing[-1]
        self.assertEqual(first[0], "/restapi/chat/7")
        self.assertGreater(first[1]["operator_typing"], 0)
        self.assertEqual(first[1]["operator_typing_id"], 1)
        self.assertEqual(last[1], {"operator_typing": 0, "operator_typing_id": 0})

    def test_chats_with_nothing_new_are_not_asked_for_their_messages_again(self):
        for cid in (11, 12, 13):                                  # old chats, answered long ago, still open on the site
            self.lhc.chat(cid, "Old", "!8ball old", status=1, user_id=5)
            self.lhc.msgs[cid].append({"id": 99, "msg": "an old answer", "user_id": 5})
            self.chat.adapter(self.chat.by_id["lhc"]).mine.add(cid)
        self.lhc.chat(7, "Pat", "What should I wear?")
        self.turn(lambda: len(self.lhc.posted) >= 2, timeout=20)
        self.settle(1.0)
        asked = lambda cid: sum(1 for path, q in self.lhc.calls if path == "/restapi/fetchchatmessages" and q.get("chat_id") == str(cid))
        before = [asked(c) for c in (11, 12, 13)]
        self.settle(2.0)                                           # many polls later ...
        self.assertEqual([asked(c) for c in (11, 12, 13)], before)  # ... the old chats were not asked again
        self.assertLessEqual(max(before), 1)

    def test_a_slow_typing_call_never_holds_up_the_answer(self):
        self.chat.adapter(self.chat.by_id["lhc"]).operator_id = 1
        self.lhc.typing_delay = 5.0                                          # every indicator call takes 5 s
        self.lhc.chat(7, "Pat", "What should I wear?")
        self.turn(lambda: len(self.lhc.posted) >= 2, timeout=3.5)            # yet the greeting and the answer are not late

    def test_typing_can_be_switched_off_and_needs_the_operator_number(self):
        ad = self.chat.adapter(self.chat.by_id["lhc"])
        ad.operator_id, ad.typing_opt = 1, False
        self.lhc.chat(7, "Pat", "What should I wear?")
        self.turn(lambda: len(self.lhc.posted) >= 2, timeout=20)
        self.assertEqual(self.lhc.typing, [])

    def test_the_site_is_watched_closely_while_a_chat_is_going(self):
        ad = cb.LhcAdapter(self.chat, types.SimpleNamespace(opts={"url": "http://x", "user": "u"}, id="x", display="x"))
        self.assertEqual((ad.poll, ad.poll_fast), (2.0, 1.0))

    def test_abuse_can_be_switched_off(self):
        self.chat.adapter(self.chat.by_id["pro"]).abuse_cfg = "off"
        self.lhc.chat(7, "Pat", "what the fuck is laughter yoga")
        self.turn(lambda: self.lhc.posted)
        self.assertNotIn("respectful", "\n".join(self.said()))

    def test_the_file_is_read_again_when_it_changes(self):
        self.lhc.chat(7, "Pat", "what time is the next club")
        self.turn(lambda: len(self.lhc.posted) >= 2)
        self.faq.write_text(self.faq.read_text(encoding="utf-8") +
                            "\nQ: What time is the next club?\nA: Tuesdays at 7.\nK: time\n", encoding="utf-8")
        os.utime(str(self.faq), (time.time() + 5, time.time() + 5))
        self.lhc.say(7, "what time is the next club")
        self.turn(lambda: len(self.lhc.posted) >= 3)
        self.assertIn("Tuesdays at 7", self.said()[2])

    def test_check_config_wants_a_faq_with_entries(self):
        empty = self.tmp / "empty.txt"
        empty.write_text("# nothing\n", encoding="utf-8")
        p = self.conf("""\
            [account:pro]
            protocol = floyd
            faq      = %s
            """ % empty)
        errors, warnings, _ = cb.check_config(str(p))
        self.assertTrue(any("no Q:/A: entries" in e for e in errors), errors)


class TestTwoSites(Base):
    """A fun site and a pro site: each Floyd answers only on his own, and each site has its own key."""

    turn, settle = TestLhc.turn, TestLhc.settle

    def setUp(self):
        super().setUp()
        self.fun, self.pro = FakeLhc(), FakeLhc()
        self.addCleanup(self.fun.close)
        self.addCleanup(self.pro.close)
        for k in ("TVCHAT_PASS_LHC", "TVCHAT_PASS_LHC_PRO"):
            os.environ[k] = "k3y"
            self.addCleanup(os.environ.pop, k, None)
        self.faq = self.tmp / "faq.txt"
        self.faq.write_text((Path(__file__).resolve().parent.parent / "configure" / "faq.example.txt").read_text(encoding="utf-8"),
                            encoding="utf-8")
        self.conf("""\
            [account:lhc]
            protocol = lhc
            url      = %s
            user     = floyd
            poll     = 0.2

            [account:lhc_pro]
            protocol = lhc
            url      = %s
            user     = floyd
            poll     = 0.2

            [account:floyd]
            protocol = floyd
            attach   = lhc/*
            tag      =
            idle     = 0
            memory   = none

            [account:pro]
            protocol = floyd
            attach   = lhc_pro/*
            faq      = %s
            tag      =
            idle     = 0
            memory   = none
            """ % (self.fun.url, self.pro.url, self.faq))
        self.chat = cb._chat()
        self.chat.daemon = True
        self.addCleanup(self.chat.close)
        for a in ("floyd", "pro"):
            self.chat.adapter(self.chat.by_id[a]).ATTACH_GAP = 0
            self.chat.adapter(self.chat.by_id[a]).FAQ_GAP = 0
            self.chat.connect(self.chat.by_id[a])
        for a in ("lhc", "lhc_pro"):
            self.chat.connect(self.chat.by_id[a])

    def test_each_floyd_answers_on_his_own_site_only(self):
        self.fun.chat(1, "Pat", "!8ball will it work", "What should I wear?")
        self.pro.chat(1, "Sam", "!8ball will it work", "What should I wear?")
        self.turn(lambda: self.fun.posted and len(self.pro.posted) >= 2, timeout=15)
        self.settle(1.5)
        fun = [q["msg"] for q in self.fun.posted]
        pro = [q["msg"] for q in self.pro.posted]
        self.assertTrue(any(m in cb.FloydMind.EIGHT_BALL for m in fun))           # the playful Floyd on the fun site
        self.assertFalse(any("comfortable clothing" in m for m in fun))           # ... never the FAQ
        self.assertFalse(any(m in cb.FloydMind.EIGHT_BALL for m in pro))          # no playful answers on the pro site
        self.assertTrue(any("comfortable clothing" in m for m in pro))            # the FAQ is the pro Floyd's alone

    def test_a_wrong_key_for_one_site_does_not_touch_the_other(self):
        os.environ["TVCHAT_PASS_LHC_PRO"] = "wrong"                  # (read at each poll)
        self.pro.chat(1, "Sam", "!8ball ok")
        self.fun.chat(1, "Pat", "!8ball ok")
        self.turn(lambda: self.fun.posted)
        self.settle(1.0)
        self.assertEqual(self.pro.posted, [])
        self.assertIn("401", self.chat.adapter(self.chat.by_id["lhc_pro"]).last_error)
        self.assertEqual(self.chat.adapter(self.chat.by_id["lhc"]).last_error, "")



class TestFloydVerb(Base):
    """File > Floyd... (F7) in tvchat: the `floyd` and `floyd-poll` verbs, the same Floyd as in tvcal and tvmail."""

    def setUp(self):
        super().setUp()
        self.data = self.tmp / "xdg-data"
        self.state = self.tmp / "xdg-state"
        for k, v in (("XDG_DATA_HOME", self.data), ("XDG_STATE_HOME", self.state)):
            os.environ[k] = str(v)
            self.addCleanup(os.environ.pop, k, None)
        os.environ["TVFLOYD_TVCAL"] = "none"                      # an installed tvcal must not take his to-do list
        self.addCleanup(os.environ.pop, "TVFLOYD_TVCAL", None)
        os.environ["TVFLOYD_RELAY"] = "none"
        self.addCleanup(os.environ.pop, "TVFLOYD_RELAY", None)
        self.conf("[chat]\n")
        cb.tvf._DOWN.clear()
        cb.tvf._MASTER = None
        self.addCleanup(setattr, cb.tvf, "_MASTER", None)

    def say(self, text):
        st, body = cb.dispatch(cb.build_parser(), ["floyd"] + ([text] if text else []))
        self.assertEqual(st, 0, body)
        return body.decode().strip()

    def test_help_tools_and_his_own_voice(self):
        self.assertIn("!setup", self.say("help"))
        self.assertIn("84", self.say("!calc 12*7"))
        self.assertIn("B-19-7", self.say("!about"))
        self.assertIn("learned", self.say("!learn tea is hot"))
        self.assertIn("tea is hot", self.say("!tea"))                 # remembered between calls
        self.assertTrue(os.path.isfile(str(self.data / "tvfloyd" / "memory.json")))

    def test_status_names_the_app_and_the_chat_accounts(self):
        out = self.say("!status")
        self.assertIn("Floyd in tvchat is at level", out)
        self.assertIn("Master Floyd: none set", out)
        self.assertIn("Chat: 0 accounts, 0 connected.", out)

    def test_irc_only_things_are_not_offered_here(self):
        for irc in ("!seen bob", "!tell bob hi", "!karma"):
            self.assertIn("Say help", self.say(irc), irc)

    def test_the_wizard_answers_in_this_window(self):
        self.assertIn("!setup join HOST TOKEN", self.say("!setup"))
        self.assertIn("Usage: !setup join", self.say("!setup join"))

    def test_claude_is_asked_in_the_background_and_polled(self):
        fake = self.tmp / "claude"
        fake.write_text("#!/bin/sh\ncat >/dev/null\necho the fake answer\n")
        fake.chmod(0o755)
        self.conf("[floyd]\nclaude_cmd = %s\n" % fake)
        self.assertIn("will ask Claude", self.say("!claude what is 2+2?"))
        got = ""
        for _ in range(60):
            st, body = cb.dispatch(cb.build_parser(), ["floyd-poll"])
            got = body.decode().strip()
            if got:
                break
            time.sleep(0.2)
        self.assertEqual(got, "Claude: the fake answer")

    def test_a_level_of_off_silences_him_and_the_master_is_used_when_set(self):
        self.conf("[floyd]\nlevel = off\n")
        self.assertIn("Floyd is off", self.say("!about"))
        self.conf("[floyd]\nserver = 127.0.0.1:1\ntoken = x\nserver_timeout = 0.3\n")
        self.assertIn("not reachable", self.say("!status"))          # the master is named, and he still answers himself
        self.assertIn("B-19-7", self.say("!about"))


class TestRefusedHint(unittest.TestCase):
    """A refused connection to a NAME that points at this machine says what is probably wrong."""

    def test_a_name_that_resolves_to_loopback_gets_the_hint(self):
        h = cb._refused_hint("cpbee", ["127.0.1.1"])
        self.assertIn("cpbee is 127.0.1.1 on this machine", h)
        self.assertIn("put that address in host =", h)
        self.assertIn("::1", cb._refused_hint("cpbee", ["::1"]))

    def test_no_hint_for_literals_localhost_or_other_machines(self):
        self.assertEqual(cb._refused_hint("127.0.0.1", ["127.0.0.1"]), "")
        self.assertEqual(cb._refused_hint("192.168.10.1", ["192.168.10.1"]), "")
        self.assertEqual(cb._refused_hint("localhost", ["127.0.0.1"]), "")
        self.assertEqual(cb._refused_hint("irc.example.org", ["203.0.113.5"]), "")
        self.assertEqual(cb._refused_hint("cpbee", []), "")


if __name__ == "__main__":
    unittest.main()
