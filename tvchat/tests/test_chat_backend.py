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


class TestIrcHardening(IrcBase):
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
            srv.send("STATE %s ringing miche on pts/1" % cid)
            srv.send("CONNECT %s tok1" % cid)
            s, first = srv.data("tok1")
            # tvchat's three edit characters (in the ATTACH write, or after it)
            stage["edit"] = (first if len(first) >= 3 else first + recv_some(s, b"\x17"))[:3]
            # miche's talk: ^H as her erase; a line, then half of one
            s.sendall(b"\x08\x15\x17hix\x08 there\nhalf")
            # the keys raw, erase and all (the receiver applies it), then lines
            stage["lines"] = recv_some(s, b"line two\n")
            s.close()                            # she hangs up
            srv.expect("QUIT-never")

        self.setup_talk(script)
        self.verb("connect", "talk")
        self.turn(lambda: self.acct.state == "connected")
        self.assertEqual(self.acct.detail, "chris@cpbee")
        self.assertEqual(self.verb("join", "talk", "miche"), (0, b"talk/miche\n"))
        self.turn(lambda: "call" in stage)             # the fake runs on its own thread
        self.assertTrue(stage["call"].endswith(" miche"))
        self.turn(lambda: ("connected", "") in self.talks("talk/miche"))
        self.assertIn(("ringing", "ringing miche on pts/1"), self.talks("talk/miche"))
        self.turn(lambda: "edit" in stage)
        self.assertEqual(stage["edit"], b"\x7f\x15\x17")       # ours, first
        self.turn(lambda: ("typing", "talk/miche", "half") in self.events("typing"))
        theirs = [m for m in self.msgs() if m[1] == "talk/miche" and m[5] == ""]
        self.assertEqual([(m[3], m[6]) for m in theirs], [("miche", "hi there")])
        self.assertEqual(self.acct.convs["talk/miche"].unread, 1)
        # classic: keystrokes go as typed; a finished line is ours in the log
        self.assertEqual(self.verb("keys", "talk/miche", "ab\x7fc\n"), (0, b""))
        # irc mode: whole lines
        self.assertEqual(self.verb("send", "talk/miche", "line one\nline two")[0], 0)
        self.turn(lambda: "lines" in stage)
        self.assertEqual(stage["lines"], b"ab\x7fc\nline one\nline two\n")
        mine = [m[6] for m in self.msgs() if m[1] == "talk/miche" and m[5] == "s"]
        self.assertEqual(mine, ["ac", "line one", "line two"])
        # she hangs up: her unfinished line still counts
        self.turn(lambda: self.acct.convs["talk/miche"].state == "ended")
        self.assertEqual(self.talks("talk/miche")[-1], ("ended", "miche hung up"))
        self.assertIn("half", [m[6] for m in self.msgs() if m[3] == "miche"])
        self.assertEqual(self.events("typing")[-1], ("typing", "talk/miche", ""))
        st, body = self.verb("send", "talk/miche", "anyone?")
        self.assertEqual(st, 1)
        self.assertIn(b"not talking to miche now", body)
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
            srv.send("RING t2 miche@cpbee")
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
        # a call from talkd's own host is just the user: talk/miche
        self.turn(lambda: "talk/miche" in self.acct.convs)
        self.assertEqual(self.verb("part", "talk/miche"), (0, b""))
        self.turn(lambda: "declined" in stage)
        self.assertEqual(self.acct.convs["talk/miche"].state, "ended")
        # with no call, part leaves the conversation
        self.assertEqual(self.verb("part", "talk/miche"), (0, b""))
        self.assertNotIn("talk/miche", self.acct.convs)
        self.assertEqual(self.verb("answer", "talk/alice@cmlaptop")[0], 1)
        self.chat.close()

    def test_refusals(self):
        def script(srv):
            srv.expect("HELLO chris")
            srv.send("ERR HELLO needs a user name")

        self.setup_talk(script)
        self.assertEqual(self.verb("join", "talk", "miche")[0], 1)        # not connected
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
            [account:miche]
            protocol = talk
            host = 127.0.0.1
            port = %d
            user = miche
            talk_view = irc
            """ % (port, port))
        self.chat = cb._chat()
        self.srv = type("S", (), {"error": None, "got": []})()
        c, m = self.chat.by_id["chris"], self.chat.by_id["miche"]
        self.verb("connect", "chris")
        self.verb("connect", "miche")
        self.turn(lambda: c.state == m.state == "connected")
        self.assertEqual((c.detail, m.detail), ("chris@cpbee", "miche@cpbee"))
        self.verb("join", "chris", "miche")
        self.turn(lambda: "miche/chris" in m.convs and m.convs["miche/chris"].state == "incoming")
        self.verb("answer", "miche/chris")
        self.turn(lambda: c.convs["chris/miche"].state == m.convs["miche/chris"].state == "connected")
        self.verb("keys", "chris/miche", "hellp\x7fo miche\nhalf")
        self.verb("send", "miche/chris", "hi chris")
        self.turn(lambda: ("typing", "miche/chris", "half") in self.events("typing") and
                  any(x[1] == "chris/miche" and x[6] == "hi chris" for x in self.msgs()))
        self.assertIn(("miche/chris", "chris", "", "hello miche"),
                      [(x[1], x[3], x[5], x[6]) for x in self.msgs()])
        self.verb("part", "chris/miche")
        self.turn(lambda: m.convs["miche/chris"].state == "ended")
        self.chat.close()


class FakeMail:
    """A mail server on 127.0.0.1 for the invitation tests: plain SMTP (any
    login) that keeps what it is sent in .sent, and a minimal IMAP over .inbox
    - [{"uid", "raw", "deleted"}] - enough for imaplib's login, select, uid
    search / fetch / store and expunge."""

    def __init__(self):
        self.sent = []
        self.inbox = []
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
                                    m["deleted"] = True
                        w("%s OK done\r\n" % tag)
                    else:
                        w("%s BAD\r\n" % tag)
                elif cmd == "EXPUNGE":
                    w("%s OK done\r\n" % tag)
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
        self.assertEqual(self.verb("send", "delta/10", "/me waves")[0], 1)
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


class TestDeltaInvitations(DeltaBase):
    """Joining an address you hold no key for mails an invitation; one found in
    the INBOX is offered, accepted or declined."""

    def setUp(self):
        super().setUp()
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

    def test_invite_helpers(self):
        self.assertEqual(cb.invite_names("https://i.delta.chat/#X&v=3&a=a%2Bb%40ex.org&n="), "a+b@ex.org")
        self.assertTrue(cb.invite_ok("A@EX.org", "https://i.delta.chat/#X&a=a%40ex.org"))
        self.assertFalse(cb.invite_ok("b@ex.org", "https://i.delta.chat/#X&a=a%40ex.org"))
        self.assertFalse(cb.invite_ok("a@ex.org", "https://i.delta.chat/#X"))     # names nobody


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


if __name__ == "__main__":
    unittest.main()
