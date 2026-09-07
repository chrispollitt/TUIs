#!/usr/bin/env python3
"""Unit + integration tests for tvmail-backend.  Stdlib unittest, no deps.

    python3 -m unittest discover -s tests        # this layer alone
    ./test.sh                                    # this + the C++/ctest layers
"""
import email
import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / "backend" / "tvmail-backend"


def _load_backend():
    """Import tvmail-backend as a module (its sh/python polyglot first line is
    a harmless string literal to Python, and nothing runs at import time)."""
    loader = importlib.machinery.SourceFileLoader("tvmail_backend", str(BACKEND))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


be = _load_backend()

# two messages: [0] seen (Status: RO), [1] unread, RFC2047 subject
MBOX_1 = (
    "From alice@example.com Mon Sep  1 10:00:00 2026\n"
    "From: Alice <alice@example.com>\n"
    "To: me@example.com\n"
    "Date: Mon, 01 Sep 2026 10:00:00 -0700\n"
    "Subject: Hello\n"
    "Status: RO\n"
    "\n"
    "Hi there, first body.\n"
    "\n"
    "From bob@example.com Mon Sep  1 11:00:00 2026\n"
    "From: bob@example.com\n"
    "Date: Mon, 01 Sep 2026 11:00:00 -0700\n"
    "Subject: =?utf-8?B?wqFIb2xhIQ==?=\n"
    "\n"
    "Second body, unread.\n"
    "\n"
)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tvmail-t-"))
        self.home = self.tmp / "home"
        self.xdg = self.tmp / "xdg"
        self.home.mkdir()
        self.xdg.mkdir()
        self.spool = self.tmp / "spool.mbox"
        self.spool.write_text(MBOX_1)
        keys = ("HOME", "XDG_DATA_HOME", "MAIL", "MAILRC", "DEAD",
                "USER", "LOGNAME", "SENDMAIL")
        self._saved = {k: os.environ.get(k) for k in keys}
        os.environ.update(HOME=str(self.home), XDG_DATA_HOME=str(self.xdg),
                          MAIL=str(self.spool), USER="tester")
        for k in ("MAILRC", "DEAD", "SENDMAIL"):
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def be(self, *args, stdin=None):
        env = dict(os.environ)
        env["PATH"] = str(BACKEND.parent) + os.pathsep + env.get("PATH", "")
        return subprocess.run([sys.executable, str(BACKEND), *args],
                              input=stdin, capture_output=True, env=env,
                              timeout=30)

    def rows(self, mbox):
        out = self.be("list", str(mbox) if os.sep in str(mbox) else mbox).stdout
        return [r for r in out.decode().splitlines() if r]


class TestHelpers(Base):
    def test_mbox_path_shorthands(self):
        self.assertEqual(be.mbox_path("spool"), str(self.spool))
        self.assertEqual(be.mbox_path(None), str(self.spool))
        self.assertEqual(be.mbox_path("mbox"), str(self.home / "mbox"))
        self.assertEqual(be.mbox_path("trash"),
                         str(self.xdg / "tvmail" / "trash.mbox"))
        self.assertTrue(be.mbox_path("drafts").endswith(os.sep + "drafts"))
        self.assertEqual(be.mbox_path("~/x"), str(self.home / "x"))

    def test_mailrc_folder_and_dead(self):
        (self.home / ".mailrc").write_text(
            "set folder=%s/Mail\nset DEAD=%s/dl\n" % (self.home, self.home))
        self.assertEqual(be._maildir(), str(self.home / "Mail"))
        self.assertEqual(be._dead_path(), str(self.home / "dl"))

    def test_aliases_recursive_and_cycle_safe(self):
        (self.home / ".mailrc").write_text(textwrap.dedent("""\
            alias dev  ann@x bo@x
            group team dev cy@x
            alias loopa loopb
            alias loopb loopa
        """))
        al = be._aliases()
        self.assertEqual(sorted(al["team"]), ["ann@x", "bo@x", "cy@x"])
        self.assertEqual(be._expand_recipients(["team", "zed@x"]),
                         ["ann@x", "bo@x", "cy@x", "zed@x"])
        self.assertIn("loopa", al)          # a cycle must terminate, not hang

    def test_best_body_prefers_plain_then_strips_html(self):
        alt = email.message_from_string(
            "Content-Type: multipart/alternative; boundary=b\n\n"
            "--b\nContent-Type: text/plain\n\nplain wins\n"
            "--b\nContent-Type: text/html\n\n<p>nope</p>\n--b--\n")
        self.assertIn("plain wins", be._best_body(alt))
        html = email.message_from_string(
            "Content-Type: text/html\n\n<h1>Hi</h1><p>bye</p>\n")
        out = be._best_body(html)
        self.assertIn("Hi", out)
        self.assertNotIn("<", out)

    def test_clean_and_hdr_decode(self):
        self.assertEqual(be._clean("a\tb\x07c"), "a b c")
        self.assertTrue(be._clean("x" * 50, 10).endswith("…"))
        m = email.message_from_string("Subject: =?utf-8?B?wqFIb2xhIQ==?=\n\n")
        self.assertEqual(be._hdr(m, "Subject"), "\xa1Hola!")

    def test_sweep_locks(self):
        base = str(self.spool)
        stale = base + ".lock.111.host.222"
        fresh = base + ".lock.999.host.888"
        bare = base + ".lock"
        old = time.time() - 10_000
        for p in (stale, fresh, bare):
            Path(p).write_text("")
        os.utime(stale, (old, old))
        os.utime(bare, (old, old))
        be._sweep_locks(base, older_than=300)
        self.assertFalse(os.path.exists(stale))   # aged temp -> swept
        self.assertTrue(os.path.exists(fresh))    # recent temp -> kept
        self.assertFalse(os.path.exists(bare))    # aged bare .lock -> swept
        Path(stale).write_text("")
        Path(bare).write_text("")
        os.utime(stale, (old, old))
        os.utime(bare, (old, old))
        be._sweep_locks(base, older_than=300, temps_only=True)
        self.assertTrue(os.path.exists(bare))     # temps_only keeps the .lock
        self.assertFalse(os.path.exists(stale))


class TestCli(Base):
    def test_list_rows_flags_subject(self):
        r = self.rows(self.spool)
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0].split("\t")[0], "0")
        self.assertEqual(r[0].split("\t")[1], ".")     # Status: RO -> seen
        self.assertEqual(r[1].split("\t")[1], "N")     # no Status  -> new
        self.assertIn("\xa1Hola!", r[1])

    def test_list_missing_mbox_is_empty_ok(self):
        p = self.be("list", str(self.tmp / "nope.mbox"))
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), b"")

    def test_show_headers_and_body(self):
        out = self.be("show", "0", str(self.spool)).stdout.decode()
        self.assertIn("From:", out)
        self.assertIn("Hi there, first body.", out)

    def test_mark_read_flips_flag(self):
        self.assertEqual(self.be("mark", "1", "read", str(self.spool)).returncode, 0)
        self.assertEqual(self.rows(self.spool)[1].split("\t")[1], ".")

    def test_delete_moves_to_trash_and_backs_up(self):
        p = self.be("delete", "0", "--mbox", str(self.spool), "--trash", "trash")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(len(self.rows(self.spool)), 1)
        self.assertEqual(len(self.rows("trash")), 1)
        self.assertTrue(list((self.xdg / "tvmail" / "backup").glob("*.bak")))

    def test_save_draft_appends(self):
        self.be("save-draft", stdin=b"To: x@y\nSubject: draft\n\nbody\n")
        r = self.rows("drafts")
        self.assertEqual(len(r), 1)
        self.assertIn("draft", r[0])

    def test_aliases_cli_sorted(self):
        (self.home / ".mailrc").write_text("alias b b@x\nalias a a@x y@x\n")
        out = self.be("aliases").stdout.decode().splitlines()
        self.assertEqual(out[0].split("\t")[0], "a")
        self.assertEqual(out[1].split("\t")[0], "b")
        self.assertIn("a@x, y@x", out[0])

    def test_ping(self):
        self.assertEqual(self.be("ping").stdout.strip(), b"pong")


class TestServe(Base):
    def _serve(self):
        env = dict(os.environ)
        env["PATH"] = str(BACKEND.parent) + os.pathsep + env.get("PATH", "")
        p = subprocess.Popen([sys.executable, str(BACKEND), "serve"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, env=env)
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

    def _req(self, p, line):
        p.stdin.write(line.encode() + b"\n")
        p.stdin.flush()
        return self._frame(p.stdout)

    def test_handshake_banner(self):
        p = self._serve()
        self.assertEqual(self._frame(p.stdout), (0, b"ready"))

    def test_list_and_show_over_pipe(self):
        p = self._serve()
        self._frame(p.stdout)
        st, body = self._req(p, 'list "%s"' % self.spool)
        self.assertEqual(st, 0)
        self.assertEqual(len([x for x in body.decode().splitlines() if x]), 2)
        st, body = self._req(p, 'show 0 "%s"' % self.spool)
        self.assertEqual(st, 0)
        self.assertIn(b"Hi there, first body.", body)

    def test_bad_request_and_blocked_verbs(self):
        p = self._serve()
        self._frame(p.stdout)
        self.assertEqual(self._req(p, "wobble")[0], 2)
        st, body = self._req(p, "send x")
        self.assertEqual(st, 2)
        self.assertIn(b"serve mode", body)

    def test_quit_exits_clean(self):
        p = self._serve()
        self._frame(p.stdout)
        p.stdin.write(b"quit\n")
        p.stdin.flush()
        self.assertEqual(p.wait(timeout=5), 0)


if __name__ == "__main__":
    unittest.main()
