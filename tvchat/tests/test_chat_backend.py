#!/usr/bin/env python3
"""Unit + integration tests for tvchat-backend.  Stdlib unittest, no deps,
no network and no real account.

    python3 -m unittest discover -s tests -p test_chat_backend.py
    ./test.sh -R chat_unit
"""
import base64
import importlib.machinery
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
import unittest
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


class TestIrcHardening(IrcBase):
    """Milestone 5: keepalive, reconnect with backoff, flood control, typed
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
        self.assertEqual(cb._normalize_cli(["--debug", "serve"]), ["--debug=debug.log", "serve"])
        self.assertEqual(cb._normalize_cli(["--debug", "x.log", "serve"]), ["--debug", "x.log", "serve"])
        self.assertEqual(cb._normalize_cli(["--trace"]), ["--trace=trace.log"])
        a = cb.build_parser().parse_args(["irc-tail", "--raw", "libera"])
        self.assertTrue(a.raw)

    def test_serve_debug_logs_requests_not_idle_polls(self):
        self.conf("[account:libera]\nprotocol = irc\nhost = 127.0.0.1\n")
        log = self.tmp / "debug.chat.log"
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


if __name__ == "__main__":
    unittest.main()
