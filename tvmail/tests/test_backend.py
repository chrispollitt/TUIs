#!/usr/bin/env python3
"""Unit + integration tests for tvmail-backend.  Stdlib unittest, no deps.

    python3 -m unittest discover -s tests        # this layer alone
    ./test.sh                                    # this + the C++/ctest layers
"""
import email
import importlib.machinery
import importlib.util
import io
import os
import shutil
import socket
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
                "USER", "LOGNAME", "SENDMAIL", "TVMAIL_CONF", "TVMAIL_MODE",
                "TVMAIL_IMAP_PASS", "TVMAIL_SMTP_PASS",
                "TVMAIL_MU_CONF", "TVMAIL_MU_TICKETS", "TVMAIL_PULL")
        self._saved = {k: os.environ.get(k) for k in keys}
        os.environ.update(HOME=str(self.home), XDG_DATA_HOME=str(self.xdg),
                          MAIL=str(self.spool), USER="tester")
        for k in ("MAILRC", "DEAD", "SENDMAIL", "TVMAIL_CONF", "TVMAIL_MODE",
                  "TVMAIL_IMAP_PASS", "TVMAIL_SMTP_PASS",
                  "TVMAIL_MU_CONF", "TVMAIL_MU_TICKETS", "TVMAIL_PULL"):
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
        self.assertTrue(be.mbox_path("sent").endswith(os.sep + "sent"))
        self.assertEqual(be.mbox_path("spam"), str(self.home / "Mail" / "Junk"))
        self.assertEqual(be.mbox_path("~/x"), str(self.home / "x"))

    def test_mbox_path_sent_honours_record(self):
        (self.home / ".mailrc").write_text("set folder=%s/Mail\nset record=+Kept\n"
                                           % self.home)
        self.assertEqual(be.mbox_path("sent"), str(self.home / "Mail" / "Kept"))

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
        lock_temp = base + ".lock.1788888888.host.222"    # lock() hitching post
        flush_temp = base + ".1788888888.host.222"        # flush() rewrite temp
        fresh = base + ".lock.1799999999.host.888"
        bare = base + ".lock"
        keep = base + ".bak"                              # unrelated -> never touched
        old = time.time() - 10_000
        for p in (lock_temp, flush_temp, fresh, bare, keep):
            Path(p).write_text("")
        for p in (lock_temp, flush_temp, bare, keep):
            os.utime(p, (old, old))
        be._sweep_locks(base, older_than=300)
        self.assertFalse(os.path.exists(lock_temp))   # aged lock temp  -> swept
        self.assertFalse(os.path.exists(flush_temp))  # aged flush temp -> swept
        self.assertTrue(os.path.exists(fresh))        # recent temp     -> kept
        self.assertFalse(os.path.exists(bare))        # aged bare .lock -> swept
        self.assertTrue(os.path.exists(keep))         # .bak            -> untouched
        Path(lock_temp).write_text("")
        Path(bare).write_text("")
        os.utime(lock_temp, (old, old))
        os.utime(bare, (old, old))
        be._sweep_locks(base, older_than=300, temps_only=True)
        self.assertTrue(os.path.exists(bare))         # temps_only keeps the .lock
        self.assertFalse(os.path.exists(lock_temp))


class TestComposeTemplate(Base):
    def setUp(self):
        super().setUp()
        self.spool.write_text(
            "From alice@x Mon Sep  1 10:00:00 2026\n"
            "From: Alice <alice@x>\n"
            "To: Bob <bob@x>, carl@x\n"
            "Cc: dana@x\n"
            "Date: Mon, 01 Sep 2026 10:00:00 -0700\n"
            "Subject: Hello\n"
            "\n"
            "hi there\n"
            "\n"
            "From eve@x Mon Sep  1 11:00:00 2026\n"
            "From: eve@x\n"
            "To: me@x\n"
            "Date: Mon, 01 Sep 2026 11:00:00 -0700\n"
            "Subject: Fwd: already forwarded\n"
            "\n"
            "second body\n"
            "\n"
        )

    def ns(self, **kw):
        import argparse
        d = dict(getfrom=None, to=[], subject=None, in_reply_to=None,
                 forward=None, reply_all=False, mbox="spool")
        d.update(kw)
        return argparse.Namespace(**d)

    def _run(self, **kw):
        cap = be._CapStream()
        old, sys.stdout = sys.stdout, cap
        try:
            be.cmd_compose_template(self.ns(**kw))
        finally:
            sys.stdout = old
        return cap.buffer.getvalue().decode()

    def test_forward_prefixes_subject_and_quotes_original(self):
        out = self._run(forward=0)
        self.assertIn("Subject: Fwd: Hello", out)
        self.assertIn("\nTo: \n", out)             # forward leaves To: blank
        self.assertIn("---------- Forwarded message ----------", out)
        self.assertIn("From: Alice <alice@x>", out)
        self.assertIn("hi there", out)

    def test_forward_does_not_double_prefix(self):
        out = self._run(forward=1)
        self.assertIn("Subject: Fwd: already forwarded", out)
        self.assertNotIn("Fwd: Fwd:", out)

    def test_reply_all_cc_excludes_new_to(self):
        out = self._run(in_reply_to=0, reply_all=True)
        self.assertIn("To: Alice <alice@x>", out)
        cc_line = [ln for ln in out.splitlines() if ln.startswith("Cc:")][0]
        self.assertIn("bob@x", cc_line)
        self.assertIn("carl@x", cc_line)
        self.assertIn("dana@x", cc_line)
        self.assertNotIn("alice@x", cc_line)        # already in To, not duplicated

    def test_reply_without_reply_all_has_no_cc(self):
        out = self._run(in_reply_to=0)
        self.assertNotIn("Cc:", out)


class TestParts(Base):
    def setUp(self):
        super().setUp()
        self.spool.write_text(
            "From alice@x Mon Sep  1 10:00:00 2026\n"
            "From: Alice <alice@x>\n"
            "To: me@x\n"
            "Date: Mon, 01 Sep 2026 10:00:00 -0700\n"
            "Subject: Report attached\n"
            "Content-Type: multipart/mixed; boundary=b\n"
            "\n"
            "--b\n"
            "Content-Type: text/plain\n"
            "\n"
            "see attached report\n"
            "--b\n"
            "Content-Type: application/octet-stream\n"
            "Content-Disposition: attachment; filename=\"don't panic.txt\"\n"
            "\n"
            "hello world attachment bytes\n"
            "--b--\n"
            "\n"
        )

    def _parts(self):
        p = self.be("parts", "0")
        return [ln.split("\t") for ln in p.stdout.decode().splitlines() if ln]

    def test_parts_lists_leaf_parts_with_walk_indices(self):
        # partno counts the multipart container itself (skipped, not printed),
        # so leaf parts start at 1 here - not 0.
        rows = self._parts()
        self.assertEqual([r[0] for r in rows], ["1", "2"])
        self.assertEqual(rows[0][1], "text/plain")
        self.assertEqual(rows[1][2], "don't panic.txt")

    def test_save_uses_the_same_index_parts_printed(self):
        # Regression: cmd_save used to index a separately-built 0-based
        # leaf-only list, disagreeing with cmd_parts' walk()-based numbering
        # as soon as a message has a multipart container - so "save" on the
        # partno "parts" printed would fail or grab the wrong part.
        rows = self._parts()
        dest_body = self.tmp / "body.txt"
        dest_att = self.tmp / "att.txt"
        p1 = self.be("save", "0", rows[0][0], str(dest_body))
        p2 = self.be("save", "0", rows[1][0], str(dest_att))
        self.assertEqual(p1.returncode, 0, p1.stderr)
        self.assertEqual(p2.returncode, 0, p2.stderr)
        self.assertIn("see attached report", dest_body.read_text())
        self.assertIn("hello world attachment bytes", dest_att.read_text())

    def test_save_out_of_range_errors(self):
        p = self.be("save", "0", "99", str(self.tmp / "x.txt"))
        self.assertNotEqual(p.returncode, 0)


class TestCli(Base):
    def test_diagnostics_help_and_version_flags(self):
        help_out = self.be("--help")
        self.assertEqual(help_out.returncode, 0)
        self.assertIn(b"--trace", help_out.stdout)
        self.assertEqual(self.be("--version").stdout.strip(), b"tvmail-backend 1.2.0")
        debug = self.tmp / "debug.log"
        trace = self.tmp / "trace.log"
        p = self.be("--debug", str(debug), "--trace", str(trace), "ping")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout, b"pong\n")
        self.assertTrue(debug.stat().st_size)
        self.assertTrue(trace.stat().st_size)

    def test_list_rows_flags_subject(self):
        r = self.rows(self.spool)
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0].split("\t")[0], "0")
        self.assertEqual(r[0].split("\t")[1], ".")     # Status: RO -> seen
        self.assertEqual(r[1].split("\t")[1], "N")     # no Status  -> new
        self.assertIn("\xa1Hola!", r[1])

    def test_list_raw_utf8_and_rfc2047_from(self):
        # BUGS.md #35: a raw 8-bit UTF-8 From crashed `list` (the folder
        # looked empty), and RFC 2047 sender names were shown encoded
        self.spool.write_bytes(
            "From z@example.org Mon Sep  1 10:00:00 2026\n"
            "From: Zoë Ünïcödé <z@example.org>\n"
            "Date: Mon, 01 Sep 2026 10:00:00 -0700\n"
            "Subject: raw\n\nbody\n\n"
            "From y@example.jp Mon Sep  1 11:00:00 2026\n"
            "From: =?utf-8?b?5bGx55Sw5aSq6YOO?= <y@example.jp>\n"
            "Date: Mon, 01 Sep 2026 11:00:00 -0700\n"
            "Subject: encoded\n\nbody\n\n".encode("utf-8"))
        p = self.be("list", str(self.spool))
        self.assertEqual(p.returncode, 0, p.stderr)
        rows = [r.split("\t") for r in p.stdout.decode("utf-8").splitlines()]
        self.assertEqual([r[3] for r in rows], ["Zoë Ünïcödé", "山田太郎"])
        self.assertEqual([r[4] for r in rows], ["raw", "encoded"])
        # a reply's quote line names the raw-UTF-8 sender the same way
        p = self.be("compose-template", "--in-reply-to", "0", str(self.spool))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Zoë Ünïcödé wrote:", p.stdout.decode("utf-8"))

    def test_counts_total_and_unseen(self):
        # the folder pane's "inbox (3)" numbers: name, total, unseen
        p = self.be("counts", str(self.spool), "drafts")
        self.assertEqual(p.returncode, 0, p.stderr)
        lines = p.stdout.decode().splitlines()
        self.assertEqual(lines[0], "%s\t2\t1" % self.spool)   # one RO, one new
        self.assertEqual(lines[1], "drafts\t0\t0")            # not created yet
        self.be("mark", "1", "read", str(self.spool))
        self.assertTrue(self.be("counts", str(self.spool)).stdout.decode()
                        .endswith("\t2\t0\n"))

    def test_counts_default_is_every_folder_but_dead(self):
        names = [l.split("\t")[0] for l in
                 self.be("counts").stdout.decode().splitlines()]
        self.assertEqual(names, ["spool", "drafts", "sent", "mbox", "spam", "trash"])

    def test_counts_dead_letter_when_named(self):
        dead = self.home / "dl"
        os.environ["DEAD"] = str(dead)
        self.addCleanup(os.environ.pop, "DEAD", None)
        self.assertEqual(self.be("counts", "dead").stdout.decode(), "dead\t0\t0\n")  # no file
        dead.write_text("From: a@b\nSubject: s\n\nbody\n")
        self.assertEqual(self.be("counts", "dead").stdout.decode(), "dead\t1\t0\n")

    def test_show_does_not_mark_read(self):
        # the UI previews with plain show and marks read separately, so a
        # message you only arrowed past stays unread
        self.be("show", "1", str(self.spool))
        self.assertEqual(self.rows(self.spool)[1].split("\t")[1], "N")

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

    def test_show_mark_read_merges_into_one_call(self):
        # Regression: show --mark-read used to open the local mbox TWICE in
        # one call (once to read, once more inside a separate mark step) -
        # a second handle rewriting-and-renaming the file while the first is
        # still open broke outright on some filesystems. This runs the real
        # CLI against a real file, the same way the crash actually happened.
        p = self.be("show", "1", str(self.spool), "--mark-read")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn(b"Traceback", p.stderr)
        self.assertIn(b"Second body, unread.", p.stdout)
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

    def test_delete_in_readonly_dir_is_one_line_not_traceback(self):
        ro = self.tmp / "ro"
        ro.mkdir()
        mb = ro / "spool.mbox"
        mb.write_text(MBOX_1)
        os.chmod(ro, 0o500)
        try:
            p = self.be("delete", "0", "--mbox", str(mb))
        finally:
            os.chmod(ro, 0o700)                 # so tearDown can rmtree
        if p.returncode == 0:
            self.skipTest("directory permissions not enforced here")
        self.assertNotIn(b"Traceback", p.stderr)
        self.assertIn(b"tvmail-backend:", p.stderr)


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


# --------------------------------------------------------------------------
# remote (IMAP/SMTP client) mode
# --------------------------------------------------------------------------
class TestMode(Base):
    def _write_conf(self, text):
        p = self.tmp / "tvmail.conf"
        p.write_text(textwrap.dedent(text))
        os.environ["TVMAIL_CONF"] = str(p)

    def test_mode_defaults_local_when_unset(self):
        self.assertEqual(be._mode(), "local")

    def test_mode_stays_local_with_imap_present_but_unset(self):
        self._write_conf("[imap]\nhost = x\n")
        self.assertEqual(be._mode(), "local")

    def test_mode_explicit_remote(self):
        self._write_conf("[service]\nmode = remote\n[imap]\nhost = x\n")
        self.assertEqual(be._mode(), "remote")

    def test_mode_explicit_local(self):
        self._write_conf("[service]\nmode = local\n[imap]\nhost = x\n")
        self.assertEqual(be._mode(), "local")

    def test_mode_invalid_value_errors(self):
        self._write_conf("[service]\nmode = bogus\n")
        with self.assertRaises(SystemExit):
            be._mode()

    def test_mode_env_override(self):
        os.environ["TVMAIL_MODE"] = "remote"
        self.assertEqual(be._mode(), "remote")

    def test_puller_override_env_then_conf(self):
        self._write_conf("[local]\npull = /opt/x/getmail --getmaildir '/a b'\n")
        self.assertEqual(be._puller(), ["/opt/x/getmail", "--getmaildir", "/a b"])
        os.environ["TVMAIL_PULL"] = "my-pull -q"
        try:
            self.assertEqual(be._puller(), ["my-pull", "-q"])
        finally:
            del os.environ["TVMAIL_PULL"]

    def test_puller_prefers_mail_pull_over_pop_pull(self):
        bindir = self.tmp / "bin"
        bindir.mkdir()
        for n in ("pop-pull", "mail-pull"):
            (bindir / n).write_text("#!/bin/sh\n")
            (bindir / n).chmod(0o755)
        saved = os.environ["PATH"]
        os.environ["PATH"] = str(bindir)
        try:
            self.assertEqual(be._puller(), [str(bindir / "mail-pull"), "-v"])
            (bindir / "mail-pull").unlink()
            self.assertEqual(be._puller(), [str(bindir / "pop-pull"), "-v"])
            (bindir / "pop-pull").unlink()
            self.assertIsNone(be._puller())
        finally:
            os.environ["PATH"] = saved

    def test_pull_without_puller_says_so(self):
        env_path = os.environ["PATH"]
        os.environ["PATH"] = str(self.tmp)          # no mail-pull / pop-pull
        try:
            r = self.be("pull")
        finally:
            os.environ["PATH"] = env_path
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(b"no mail puller found", r.stderr)

    def test_mode_subcommand_prints_word(self):
        self.assertEqual(self.be("mode").stdout.strip(), b"local")
        os.environ["TVMAIL_MODE"] = "remote"
        self.assertEqual(self.be("mode").stdout.strip(), b"remote")

    def test_imap_folder_mapping_and_override(self):
        self.assertEqual(be._imap_folder("spool"), "INBOX")
        self.assertEqual(be._imap_folder(None), "INBOX")
        self.assertEqual(be._imap_folder("mbox"), "Archive")
        self.assertEqual(be._imap_folder("trash"), "Trash")
        self.assertEqual(be._imap_folder("drafts"), "Drafts")
        self.assertEqual(be._imap_folder("sent"), "Sent")
        self.assertEqual(be._imap_folder("spam"), "Junk")
        self.assertEqual(be._imap_folder("Weird/Name"), "Weird/Name")
        self._write_conf("[folders]\nsaved = Kept\n")
        self.assertEqual(be._imap_folder("mbox"), "Kept")

    def test_cred_env_then_netrc_then_error(self):
        os.environ["TVMAIL_IMAP_PASS"] = "fromenv"
        self.assertEqual(be._cred("h", "u", "TVMAIL_IMAP_PASS"), "fromenv")
        os.environ.pop("TVMAIL_IMAP_PASS")
        (self.home / ".netrc").write_text("machine h login u password fromnetrc\n")
        os.chmod(self.home / ".netrc", 0o600)
        self.assertEqual(be._cred("h", "u", "TVMAIL_IMAP_PASS"), "fromnetrc")
        with self.assertRaises(SystemExit):
            be._cred("nope", "u", "TVMAIL_IMAP_PASS")


# --------------------------------------------------------------------------
# GNU Mailutils interop:  ~/.mail  and  ~/.mu-tickets
# --------------------------------------------------------------------------
class TestMailutilsInterop(Base):
    MAIL_CONF = """\
        # ~/.mail

        mailbox {
            mailbox-pattern "imap://chris@cpbee:143/INBOX";
        };

        mailer {
            url "smtp://cpbee:25";
        };
        """

    def _write_mail(self, text):
        (self.home / ".mail").write_text(textwrap.dedent(text))

    def _write_tickets(self, text):
        (self.home / ".mu-tickets").write_text(textwrap.dedent(text))

    def test_mu_url_parses_plain_and_wildcard_and_decodes(self):
        u = be._mu_url("imap://chris@cpbee:143/INBOX")
        self.assertEqual(u, {"scheme": "imap", "user": "chris", "pass": None,
                             "host": "cpbee", "port": 143, "path": "INBOX"})
        u = be._mu_url("smtp://cpbee:25")
        self.assertEqual(u["scheme"], "smtp")
        self.assertEqual(u["host"], "cpbee")
        self.assertEqual(u["port"], 25)
        self.assertIsNone(u["user"])
        u = be._mu_url("*://chris:zxc%40zxc@cpbee")
        self.assertEqual(u["scheme"], "*")
        self.assertEqual(u["user"], "chris")
        self.assertEqual(u["pass"], "zxc@zxc")           # percent-decoded
        self.assertIsNone(be._mu_url(""))
        self.assertIsNone(be._mu_url("not a url"))

    def test_mu_conf_parses_mailbox_and_mailer_blocks(self):
        self._write_mail(self.MAIL_CONF)
        box = be._mu_mailbox()
        self.assertEqual(box["scheme"], "imap")
        self.assertEqual(box["user"], "chris")
        self.assertEqual(box["host"], "cpbee")
        self.assertEqual(box["port"], 143)
        mailer = be._mu_mailer()
        self.assertEqual(mailer["scheme"], "smtp")
        self.assertEqual(mailer["host"], "cpbee")
        self.assertEqual(mailer["port"], 25)

    def test_mu_conf_missing_file_is_empty(self):
        self.assertEqual(be._mu_conf(), {})
        self.assertIsNone(be._mu_mailbox())
        self.assertIsNone(be._mu_mailer())

    def test_mode_ignores_mail_file_imap_pattern_defaults_local(self):
        self._write_mail(self.MAIL_CONF)
        self.assertEqual(be._mode(), "local")

    def test_mode_stays_local_when_mailbox_pattern_is_not_imap(self):
        self._write_mail("""\
            mailbox {
                mailbox-pattern "/var/mail/chris";
            };
            """)
        self.assertEqual(be._mode(), "local")

    def test_cred_from_mu_tickets_wildcard_scheme(self):
        self._write_tickets("*://chris:s3cr3t@cpbee\n")
        self.assertEqual(be._cred("cpbee", "chris", "TVMAIL_IMAP_PASS", "imap"),
                         "s3cr3t")

    def test_cred_mu_tickets_percent_decoded_and_host_matched(self):
        self._write_tickets("imap://chris:p%40ss@cpbee.lan\n"
                            "imap://someone:other@otherhost\n")
        self.assertEqual(
            be._cred("cpbee.lan", "chris", "TVMAIL_IMAP_PASS", "imap"), "p@ss")

    def test_cred_mu_tickets_scheme_mismatch_falls_through_to_netrc(self):
        self._write_tickets("pop3://chris:wrongproto@cpbee\n")
        (self.home / ".netrc").write_text(
            "machine cpbee login chris password fromnetrc\n")
        os.chmod(self.home / ".netrc", 0o600)
        self.assertEqual(
            be._cred("cpbee", "chris", "TVMAIL_IMAP_PASS", "imap"), "fromnetrc")

    def test_cred_prefers_env_over_mu_tickets(self):
        self._write_tickets("*://chris:fromticket@cpbee\n")
        os.environ["TVMAIL_IMAP_PASS"] = "fromenv"
        self.assertEqual(
            be._cred("cpbee", "chris", "TVMAIL_IMAP_PASS", "imap"), "fromenv")

    def test_cred_mu_tickets_prefers_matching_user(self):
        self._write_tickets("*://alice:apass@cpbee\n*://chris:cpass@cpbee\n")
        self.assertEqual(
            be._cred("cpbee", "chris", "TVMAIL_IMAP_PASS", "imap"), "cpass")

    def test_smtp_send_host_falls_back_to_mailer_url(self):
        self._write_mail(self.MAIL_CONF)
        self._write_tickets("*://chris:s3cr3t@cpbee\n")
        logged = []

        class FakeSMTP:
            def __init__(s, host, port, timeout=None):
                s.host, s.port = host, port
            def ehlo(s):
                pass
            def starttls(s, context=None):
                pass
            def login(s, u, p):
                logged.append((u, p))
            def sendmail(s, frm, rcpts, data):
                pass
            def quit(s):
                pass

        import smtplib
        real, smtplib.SMTP = smtplib.SMTP, FakeSMTP
        try:
            ok = be._smtp_send(
                email.message_from_string("From: chris@cpbee\n\nx\n"), ["a@b"])
        finally:
            smtplib.SMTP = real
        self.assertTrue(ok)
        self.assertEqual([p for _, p in logged], ["s3cr3t"])


class FakeIMAP:
    """Just enough IMAP for the backend's remote paths."""
    def __init__(self):
        self.folders = {}          # name -> [[uid, {flags}, raw], ...]
        self._uid = 1
        self.cur = None
        self.copied = []
        self.appended = []
        self.created = []
        self.internaldate = "19-Oct-2026 18:25:29 -0700"   # when "filed"

    def add(self, folder, raw, flags=()):
        self.folders.setdefault(folder, []).append([self._uid, set(flags), raw])
        self._uid += 1

    def noop(self):
        return ("OK", [b"NOOP"])

    def logout(self):
        return ("BYE", [b"bye"])

    def select(self, mailbox, readonly=False):
        self.cur = mailbox.strip('"')
        self.folders.setdefault(self.cur, [])
        return ("OK", [str(len(self.folders[self.cur])).encode()])

    def _rows(self):
        return self.folders.get(self.cur, [])

    def uid(self, command, *args):
        c = command.upper()
        if c == "SEARCH":
            terms = [a for a in args if a not in (None, "ALL")]

            def keep(row):
                _uid, flags, raw = row
                m = email.message_from_bytes(raw)
                i = 0
                while i < len(terms):
                    t = terms[i].upper()
                    if t in ("FROM", "SUBJECT", "TO"):
                        want = terms[i + 1].strip('"').lower()
                        if want not in (m.get(t.capitalize(), "") or "").lower():
                            return False
                        i += 2
                    elif t == "SEEN":
                        if "\\Seen" not in flags:
                            return False
                        i += 1
                    elif t == "UNSEEN":
                        if "\\Seen" in flags:
                            return False
                        i += 1
                    elif t == "BEFORE":
                        import time as _t
                        cut = _t.strptime(terms[i + 1], "%d-%b-%Y")
                        dt = email.utils.parsedate_to_datetime(m.get("Date", ""))
                        if not dt or dt.timetuple() >= cut:
                            return False
                        i += 2
                    else:
                        i += 1
                return True

            uids = " ".join(str(r[0]) for r in self._rows() if keep(r))
            return ("OK", [uids.encode()])
        if c == "FETCH":
            seq = args[0].decode() if isinstance(args[0], bytes) else str(args[0])
            want = {int(x) for x in seq.replace(",", " ").split()}
            spec, out = args[1], []
            for uid, flags, raw in self._rows():
                if uid not in want:
                    continue
                fl = " ".join(sorted(flags))
                if "HEADER.FIELDS" in spec:
                    import re
                    m = email.message_from_bytes(raw)
                    # the fields asked for, and only those the message has -
                    # as a real server answers HEADER.FIELDS
                    names = re.search(r"HEADER\.FIELDS \(([^)]*)\)", spec).group(1).split()
                    hdr = ("".join("%s: %s\r\n" % (h.capitalize(), m.get(h))
                                   for h in names if m.get(h) is not None)
                           ).encode() + b"\r\n"
                    idate = (' INTERNALDATE "%s"' % self.internaldate
                             if "INTERNALDATE" in spec else "")
                    meta = ("%d (UID %d FLAGS (%s)%s BODY[] {%d}"
                            % (uid, uid, fl, idate, len(hdr))).encode()
                    out += [(meta, hdr), b")"]
                else:
                    meta = ("%d (UID %d BODY[] {%d}"
                            % (uid, uid, len(raw))).encode()
                    out += [(meta, raw), b")"]
            return ("OK", out)
        if c == "STORE":
            uid, op, spec = int(args[0]), args[1], args[2]
            fs = {f for f in spec.strip("()").split() if f}
            for r in self._rows():
                if r[0] == uid:
                    r[1] = (r[1] | fs) if op[0] == "+" else (r[1] - fs)
            return ("OK", [b"ok"])
        if c == "COPY":
            uid, dest = int(args[0]), args[1].strip('"')
            if dest not in self.folders:
                # a real server refuses COPY to a mailbox that doesn't exist
                # yet - this is what _imap_copy_to()'s create-then-retry is for
                return ("NO", [b"[TRYCREATE] no such mailbox"])
            for r in self._rows():
                if r[0] == uid:
                    self.folders[dest].append([self._uid, set(r[1]), r[2]])
                    self._uid += 1
                    self.copied.append((uid, dest))
            return ("OK", [b"ok"])
        return ("OK", [b""])

    def status(self, mailbox, what):
        name = mailbox.strip('"')
        if name not in self.folders:
            return ("NO", [b"no such mailbox"])
        rows = self.folders[name]
        unseen = sum(1 for r in rows if "\\Seen" not in r[1])
        return ("OK", [('"%s" (MESSAGES %d UNSEEN %d)' % (name, len(rows), unseen)).encode()])

    def create(self, name):
        name = name.strip('"')
        self.folders.setdefault(name, [])
        self.created.append(name)
        return ("OK", [b"ok"])

    def expunge(self):
        self.folders[self.cur] = [r for r in self._rows()
                                  if "\\Deleted" not in r[1]]
        return ("OK", [b"1"])

    def append(self, mailbox, flags, date, message):
        name = mailbox.strip('"')
        if name not in self.folders:
            # a real server refuses APPEND to a mailbox that doesn't exist
            # yet, same as COPY above - this is what cmd_save_draft's
            # create-then-retry is for
            return ("NO", [b"[TRYCREATE] no such mailbox"])
        raw = message if isinstance(message, bytes) else message.encode()
        self.folders[name].append([self._uid, set(), raw])
        self._uid += 1
        self.appended.append((name, raw))
        return ("OK", [b"ok"])


def _msg(frm, subj, body, date="Mon, 01 Sep 2026 10:00:00 -0700"):
    return ("From: %s\r\nDate: %s\r\nSubject: %s\r\n\r\n%s\r\n"
            % (frm, date, subj, body)).encode()


class TestRemoteIMAP(Base):
    def setUp(self):
        super().setUp()
        os.environ["TVMAIL_MODE"] = "remote"
        self.imap = FakeIMAP()
        self.imap.add("INBOX", _msg("alice@x", "Hello", "first body"),
                      flags=("\\Seen",))
        self.imap.add("INBOX", _msg("bob@x", "Second", "second body"))
        self.imap.add("INBOX", _msg("carl@x", "Third", "third body"))
        self._real_imap = be._imap
        be._imap = lambda: self.imap

    def tearDown(self):
        be._imap = self._real_imap
        be._IMAP = None
        super().tearDown()

    def _run(self, fn, ns):
        cap = be._CapStream()
        old = sys.stdout
        sys.stdout = cap
        try:
            fn(ns)
        finally:
            sys.stdout = old
        return cap.buffer.getvalue()

    def ns(self, **kw):
        import argparse as _a
        kw.setdefault("mbox", "spool")
        return _a.Namespace(**kw)

    def test_list_over_imap(self):
        out = self._run(be.cmd_list, self.ns()).decode()
        rows = [r for r in out.splitlines() if r]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].split("\t")[1], ".")   # \Seen
        self.assertEqual(rows[1].split("\t")[1], "N")
        self.assertIn("Second", rows[1])

    def test_list_after_another_connection_expunged(self):
        # Regression: Ctrl-D runs `delete` as a one-shot backend - its own
        # IMAP connection - while the serve backend still has INBOX selected.
        # A server need not tell that connection about the EXPUNGE until a
        # command lets it (NOOP; UID SEARCH / FETCH needn't), so SEARCH kept
        # listing the gone UID, FETCH returned nothing for it, and the list
        # grew a blank "N  (no subject)" ghost where the message had been.
        imap = self.imap
        view = {}
        orig_select, orig_uid = imap.select, imap.uid

        def select(mailbox, readonly=False):
            r = orig_select(mailbox, readonly)
            view["uids"] = [row[0] for row in imap._rows()]
            return r

        def noop():
            view["uids"] = [row[0] for row in imap._rows()]
            return ("OK", [b"NOOP"])

        def uid(command, *args):
            if command.upper() == "SEARCH":   # this session's view, not live
                return ("OK", [" ".join(map(str, view["uids"])).encode()])
            return orig_uid(command, *args)

        imap.select, imap.noop, imap.uid = select, noop, uid
        self._run(be.cmd_list, self.ns())        # the serve connection opens INBOX
        imap.folders["INBOX"].pop(1)             # another connection deletes "Second"
        rows = [r for r in self._run(be.cmd_list, self.ns()).decode().splitlines() if r]
        self.assertEqual(len(rows), 2, rows)
        self.assertNotIn("(no subject)", "\n".join(rows))
        self.assertIn("Hello", rows[0])
        self.assertIn("Third", rows[1])

    def test_counts_over_imap_uses_status_not_select(self):
        selected = []
        orig = self.imap.select
        self.imap.select = lambda *a, **kw: (selected.append(a), orig(*a, **kw))[1]
        out = self._run(be.cmd_counts, self.ns(mboxes=["spool", "trash"])).decode()
        # INBOX has 3 messages, 2 unseen; Trash doesn't exist yet -> 0 0
        self.assertEqual(out.splitlines(), ["spool\t3\t2", "trash\t0\t0"])
        self.assertEqual(selected, [])    # STATUS leaves the open folder alone

    def test_show_over_imap(self):
        out = self._run(be.cmd_show, self.ns(idx=1)).decode()
        self.assertIn("From:", out)
        self.assertIn("second body", out)

    def test_mark_over_imap(self):
        be.cmd_mark(self.ns(idx=1, state="read"))
        rows = self._run(be.cmd_list, self.ns()).decode().splitlines()
        self.assertEqual(rows[1].split("\t")[1], ".")

    def test_show_mark_read_over_imap_merges_select_and_search(self):
        # Regression guard: a separate show + mark used to cost 6 IMAP round
        # trips per keystroke (2x SELECT+SEARCH, plus FETCH, plus STORE) -
        # over a slow link (a shared webhost vs. the LAN master) every one of
        # those was felt. --mark-read must fold marking into the SELECT+
        # SEARCH the fetch already paid for (down to 4), and a second show in
        # the same folder+mode must skip re-selecting entirely (down to 3).
        calls = {"select": 0, "uid": 0}
        orig_select, orig_uid = self.imap.select, self.imap.uid

        def counted_select(*a, **kw):
            calls["select"] += 1
            return orig_select(*a, **kw)

        def counted_uid(*a, **kw):
            calls["uid"] += 1
            return orig_uid(*a, **kw)

        self.imap.select, self.imap.uid = counted_select, counted_uid

        out = self._run(be.cmd_show, self.ns(idx=1, mark_read=True)).decode()
        self.assertIn("second body", out)
        self.assertIn("\\Seen", self.imap.folders["INBOX"][1][1])
        self.assertEqual(calls["select"], 1)
        self.assertEqual(calls["uid"], 3)          # SEARCH + FETCH + STORE

        calls["select"] = calls["uid"] = 0
        out2 = self._run(be.cmd_show, self.ns(idx=2, mark_read=True)).decode()
        self.assertIn("third body", out2)
        self.assertIn("\\Seen", self.imap.folders["INBOX"][2][1])
        self.assertEqual(calls["select"], 0)       # same folder+mode: no re-SELECT
        self.assertEqual(calls["uid"], 3)

    def test_delete_over_imap_moves_to_trash(self):
        self._run(be.cmd_delete, self.ns(idx=[0], mbox="spool", trash="trash"))
        self.assertEqual(len(self.imap.folders["INBOX"]), 2)
        self.assertEqual([d for _, d in self.imap.copied], ["Trash"])

    def test_delete_over_imap_moves_to_the_requested_folder_not_trash(self):
        # Regression: the remote path used to hard-code the copy destination
        # to _imap_folder("trash") regardless of what --trash asked for, so
        # "move to spam" silently moved to Trash instead (or nowhere, if
        # Trash didn't exist either - see the auto-create test below).
        self._run(be.cmd_delete, self.ns(idx=[0], mbox="spool", trash="spam"))
        self.assertEqual(len(self.imap.folders["INBOX"]), 2)
        self.assertEqual([d for _, d in self.imap.copied], ["Junk"])
        self.assertNotIn("Trash", self.imap.folders)

    def test_delete_over_imap_auto_creates_a_missing_destination_folder(self):
        # Regression: COPY to a folder that doesn't exist on the server
        # (e.g. a fresh Dovecot with only INBOX) used to fail silently - the
        # code never checked COPY's result - and the message got expunged
        # from INBOX anyway, vanishing outright instead of moving anywhere.
        self.assertNotIn("Trash", self.imap.folders)
        self._run(be.cmd_delete, self.ns(idx=[0], mbox="spool", trash="trash"))
        self.assertIn("Trash", self.imap.created)
        self.assertEqual(len(self.imap.folders["Trash"]), 1)
        self.assertEqual(len(self.imap.folders["INBOX"]), 2)

    def test_delete_over_imap_does_not_lose_the_message_if_copy_keeps_failing(self):
        # If COPY still fails after CREATE (permission denied, quota, a
        # server that doesn't support TRYCREATE, ...), the message must stay
        # put rather than being expunged with nowhere to land.
        real_uid = self.imap.uid
        def dead_copy(command, *args):
            return ("NO", [b"boom"]) if command.upper() == "COPY" else real_uid(command, *args)
        self.imap.uid = dead_copy
        with self.assertRaises(SystemExit):
            self._run(be.cmd_delete, self.ns(idx=[0], mbox="spool", trash="trash"))
        self.assertEqual(len(self.imap.folders["INBOX"]), 3)   # nothing expunged

    def test_delete_over_imap_reports_store_failure(self):
        real_uid = self.imap.uid
        def dead_store(command, *args):
            return ("NO", [b"boom"]) if command.upper() == "STORE" else real_uid(command, *args)
        self.imap.uid = dead_store
        with self.assertRaises(SystemExit):
            self._run(be.cmd_delete, self.ns(idx=[0], mbox="spool", trash="drafts"))
        self.assertEqual(len(self.imap.folders["INBOX"]), 3)   # nothing expunged

    def _feed_stdin(self, data):
        class _In:
            buffer = io.BytesIO(data)
        self._old_stdin, sys.stdin = sys.stdin, _In()
        self.addCleanup(lambda: setattr(sys, "stdin", self._old_stdin))

    def test_save_draft_over_imap_appends(self):
        self._feed_stdin(b"To: x@y\nSubject: d\n\nbody\n")
        self._run(be.cmd_save_draft, self.ns())
        self.assertEqual([n for n, _ in self.imap.appended], ["Drafts"])

    def test_save_draft_over_imap_auto_creates_a_missing_drafts_folder(self):
        # Regression: a Dovecot with no Drafts folder configured (real-world
        # case: cpbee has Sent/Archive/Junk/Trash/INBOX but no Drafts) made
        # APPEND fail - the code never checked the result, so it printed
        # "draft saved" and the draft vanished instead of landing anywhere.
        self.assertNotIn("Drafts", self.imap.folders)
        self._feed_stdin(b"To: x@y\nSubject: d\n\nbody\n")
        out = self._run(be.cmd_save_draft, self.ns())
        self.assertIn(b"draft saved", out)
        self.assertIn("Drafts", self.imap.created)
        self.assertEqual([n for n, _ in self.imap.appended], ["Drafts"])

    def test_save_draft_over_imap_reports_append_failure_instead_of_lying(self):
        # If APPEND still fails after CREATE (permission denied, quota, a
        # server that doesn't support TRYCREATE, ...), cmd_save_draft must
        # say so rather than printing "draft saved" with nothing saved.
        real_append = self.imap.append
        def dead_append(*a, **kw):
            return ("NO", [b"boom"])
        self.imap.append = dead_append
        self._feed_stdin(b"To: x@y\nSubject: d\n\nbody\n")
        with self.assertRaises(SystemExit):
            self._run(be.cmd_save_draft, self.ns())
        self.imap.append = real_append
        self.assertEqual(self.imap.appended, [])

    def test_send_over_imap_uses_smtp_and_files_a_copy(self):
        seen = {}
        real, be._smtp_send = be._smtp_send, \
            lambda m, r: (seen.setdefault("rcpts", r), True)[1]
        self.addCleanup(lambda: setattr(be, "_smtp_send", real))
        self.imap.create("Sent")
        self._feed_stdin(b"To: a@b\nSubject: s\n\nhi\n")
        out = self._run(be.cmd_send, self.ns(getfrom=None, to=[], subject=None))
        self.assertIn(b"sent", out)
        self.assertEqual(seen["rcpts"], ["a@b"])
        self.assertIn("Sent", [n for n, _ in self.imap.appended])   # copy filed


    def test_send_dates_numbers_and_signs_the_sent_copy(self):
        # Regression: send left Date / Message-ID to the server, which adds
        # them only to the delivered copy - the Sent copy had neither (and no
        # From), so the Sent list showed blank date and sender columns.
        real, be._smtp_send = be._smtp_send, lambda m, r: "chris@cpbee"
        self.addCleanup(lambda: setattr(be, "_smtp_send", real))
        self.imap.create("Sent")
        self._feed_stdin(b"X-Mailer: tvmail\nTo: a@b\nSubject: s\n\nhi\n")
        self._run(be.cmd_send, self.ns(getfrom=None, to=[], subject=None))
        copy = email.message_from_bytes([raw for n, raw in self.imap.appended if n == "Sent"][-1])
        self.assertTrue(email.utils.parsedate_to_datetime(copy["Date"]))
        self.assertRegex(copy["Message-ID"], r"^<.+@.+>$")
        self.assertEqual(copy["From"], "chris@cpbee")    # the envelope sender

    def test_send_does_not_invent_a_from_without_an_address(self):
        real, be._smtp_send = be._smtp_send, lambda m, r: "chris"   # bare login
        self.addCleanup(lambda: setattr(be, "_smtp_send", real))
        self.imap.create("Sent")
        self._feed_stdin(b"To: a@b\nSubject: s\n\nhi\n")
        self._run(be.cmd_send, self.ns(getfrom=None, to=[], subject=None))
        copy = email.message_from_bytes([raw for n, raw in self.imap.appended if n == "Sent"][-1])
        self.assertIsNone(copy["From"])

    def test_sent_list_shows_recipient_and_filed_date(self):
        # Sent / Drafts list who it went to; a copy with no Date: header
        # (filed before send stamped one) shows when the server filed it.
        import imaplib
        self.imap.add("Sent", b"To: Bob Jones <bob@x>\r\nSubject: to bob\r\n\r\nhi\r\n")
        row = self._run(be.cmd_list, self.ns(mbox="sent")).decode().splitlines()[0].split("\t")
        want = time.strftime("%Y-%m-%d %H:%M", imaplib.Internaldate2tuple(
            b'INTERNALDATE "%s"' % self.imap.internaldate.encode()))
        self.assertEqual(row[2], want)
        self.assertEqual(row[3], "to Bob Jones")
        self.assertEqual(row[4], "to bob")


class TestSentFolderLocal(Base):
    def test_local_sent_list_shows_recipient_and_from_line_date(self):
        p = Path(be.mbox_path("sent"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("From tester Mon Oct 19 18:25:29 2026\n"
                     "To: Bob Jones <bob@x>\nSubject: to bob\n\nhi\n\n")
        row = self.rows("sent")[0].split("\t")
        self.assertEqual(row[2], "2026-10-19 18:25")
        self.assertEqual(row[3], "to Bob Jones")

    def test_inbox_still_shows_the_sender(self):
        row = self.rows("spool")[0].split("\t")
        self.assertFalse(row[3].startswith("to "))


class TestDispatchReconnect(Base):
    """_dispatch() drops a stale cached IMAP connection and retries the
    command once, instead of _imap() paying for a noop() liveness check
    before every request (see trace.sh / DEVNOTES)."""

    def setUp(self):
        super().setUp()
        os.environ["TVMAIL_MODE"] = "remote"
        self.good = FakeIMAP()
        self.good.add("INBOX", _msg("alice@x", "Hello", "body"))
        self._real_imap = be._imap
        self.parser = be.build_parser()
        self.addCleanup(lambda: setattr(be, "_imap", self._real_imap))
        self.addCleanup(lambda: setattr(be, "_IMAP", None))

    def test_retries_once_after_a_stale_connection(self):
        # _imap() is a plain cache of be._IMAP (see the real function) - mimic
        # that here so every call site within one attempt sees the same
        # object, the way they would against the real connection.
        reconnects = []

        class Dead:
            def select(self, *a, **kw):
                raise OSError("stale connection")

        def fake_imap():
            if be._IMAP is None:
                reconnects.append(1)
                be._IMAP = self.good
            return be._IMAP

        be._IMAP = Dead()             # a previous request's now-stale connection
        be._imap = fake_imap

        status, body = be._dispatch(self.parser, ["list", "spool"])
        self.assertEqual(status, 0)
        self.assertEqual(len(reconnects), 1)        # reconnected exactly once
        self.assertIs(be._IMAP, self.good)
        rows = [r for r in body.decode().splitlines() if r]
        self.assertEqual(len(rows), 1)
        self.assertIn("Hello", rows[0])

    def test_does_not_retry_a_second_failure(self):
        class AlwaysDead:
            def select(self, *a, **kw):
                raise OSError("stale connection")

        be._IMAP = object()
        be._imap = lambda: AlwaysDead()

        status, body = be._dispatch(self.parser, ["list", "spool"])
        self.assertEqual(status, 2)
        self.assertIn(b"OSError", body)

    def test_does_not_retry_when_nothing_was_cached_yet(self):
        # _IMAP is None here - a failure is a real (first) connect failure,
        # not staleness, so it must surface immediately, not loop.
        calls = []

        def boom():
            calls.append(1)
            raise OSError("no route to host")

        be._IMAP = None
        be._imap = boom

        status, body = be._dispatch(self.parser, ["list", "spool"])
        self.assertEqual(status, 2)
        self.assertEqual(len(calls), 1)

    def test_ordinary_command_failure_is_not_retried(self):
        # A non-connection error (e.g. a real IMAP protocol failure) must not
        # be treated as staleness - retrying it would just fail the same way
        # twice and mask the real error behind a generic OSError catch.
        calls = []

        class Picky:
            def select(self, *a, **kw):
                calls.append(1)
                raise ValueError("not a connection problem")

        be._IMAP = object()
        be._imap = lambda: Picky()

        status, body = be._dispatch(self.parser, ["list", "spool"])
        self.assertEqual(status, 2)
        self.assertEqual(len(calls), 1)
        self.assertIn(b"ValueError", body)


class TestPurgeLocal(Base):
    @staticmethod
    def _m(frm, subj, date, body):
        return ("From x Mon Sep  1 00:00:00 2026\n"
                "From: %s\nDate: %s\nSubject: %s\n\n%s\n\n"
                % (frm, date, subj, body))

    def setUp(self):
        super().setUp()
        old = email.utils.formatdate(time.time() - 40 * 86400, localtime=True)
        new = email.utils.formatdate(time.time() - 1 * 86400, localtime=True)
        self.spool.write_text(
            self._m("Cron Daemon <root@cpbee>", "Cron <root@cpbee> check", old, "c1")
            + self._m("Alice <a@x>", "hi", new, "hello")
            + self._m("Cron Daemon <root@cpbee>", "Cron <root@cpbee> gravity", new, "c2")
            + self._m("Bob <b@x>", "old note", old, "stale"))

    def n(self, mbox="spool"):
        return len([r for r in self.be("list", str(self.spool) if mbox == "spool"
                                       else mbox).stdout.decode().splitlines() if r])

    def test_purge_by_from_moves_to_trash(self):
        p = self.be("purge", "--from", "Cron Daemon")
        self.assertEqual(p.returncode, 0)
        self.assertIn(b"purged 2", p.stdout)
        self.assertEqual(self.n(), 2)                       # Alice + Bob remain
        self.assertEqual(self.n("trash"), 2)

    def test_purge_older_than(self):
        self.be("purge", "--older-than", "7")
        self.assertEqual(self.n(), 2)                       # the two 40-day msgs go

    def test_purge_and_of_filters(self):
        self.be("purge", "--from", "Cron", "--older-than", "30")
        self.assertEqual(self.n(), 3)                       # only the old cron one

    def test_purge_dry_run_changes_nothing(self):
        p = self.be("purge", "-n", "--from", "Cron")
        self.assertIn(b"would purge 2", p.stdout)
        self.assertEqual(self.n(), 4)

    def test_purge_to_folder(self):
        self.be("purge", "--from", "Cron Daemon", "--to-folder", "spam")
        self.assertEqual(self.n(), 2)
        self.assertEqual(self.n("spam"), 2)

    def test_purge_expunge_skips_trash(self):
        self.be("purge", "--from", "Cron Daemon", "--expunge")
        self.assertEqual(self.n(), 2)
        self.assertEqual(self.n("trash"), 0)

    def test_purge_needs_a_filter(self):
        p = self.be("purge")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b"no filter", p.stdout + p.stderr)

    def test_purge_all_refused_outside_trash(self):
        p = self.be("purge", "--all")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b"only applies to the trash", p.stdout + p.stderr)
        self.assertEqual(self.n(), 4)

    def test_empty_trash_deletes_everything_in_trash_only(self):
        self.be("purge", "--from", "Cron Daemon")           # 2 msgs -> trash
        self.assertEqual(self.n("trash"), 2)
        p = self.be("empty-trash")
        self.assertEqual(p.returncode, 0)
        self.assertIn(b"purged 2", p.stdout)
        self.assertEqual(self.n("trash"), 0)
        self.assertEqual(self.n(), 2)                       # inbox untouched

    def test_empty_trash_dry_run_changes_nothing(self):
        self.be("purge", "--from", "Cron Daemon")
        p = self.be("empty-trash", "-n")
        self.assertIn(b"would purge 2", p.stdout)
        self.assertEqual(self.n("trash"), 2)

    def test_empty_trash_when_already_empty(self):
        p = self.be("empty-trash", "-n")
        self.assertEqual(p.returncode, 0)
        self.assertIn(b"would purge 0", p.stdout)
        p = self.be("empty-trash")
        self.assertEqual(p.returncode, 0)
        self.assertIn(b"purged 0", p.stdout)


class TestPurgeIMAP(Base):
    def setUp(self):
        super().setUp()
        os.environ["TVMAIL_MODE"] = "remote"
        self.imap = FakeIMAP()
        old = email.utils.formatdate(time.time() - 40 * 86400)
        new = email.utils.formatdate(time.time() - 1 * 86400)
        self.imap.add("INBOX", _msg("Cron Daemon <r@cpbee>", "Cron A", "o", date=old))
        self.imap.add("INBOX", _msg("Alice <a@x>", "hi", "b", date=new))
        self.imap.add("INBOX", _msg("Cron Daemon <r@cpbee>", "Cron B", "o", date=new))
        self._real, be._imap = be._imap, lambda: self.imap

    def tearDown(self):
        be._imap = self._real
        be._IMAP = None
        super().tearDown()

    def ns(self, **kw):
        import argparse
        d = dict(mbox="spool", getfrom=None, subject=None, to=None,
                 older_than=None, seen=False, unseen=False, trash="trash",
                 expunge=False, dry_run=False)
        d.update(kw)
        return argparse.Namespace(**d)

    def _purge(self, **kw):
        cap = be._CapStream()
        old = sys.stdout
        sys.stdout = cap
        try:
            be.cmd_purge(self.ns(**kw))
        finally:
            sys.stdout = old
        return cap.buffer.getvalue()

    def test_purge_from_over_imap(self):
        self._purge(getfrom="Cron Daemon")
        self.assertEqual(len(self.imap.folders["INBOX"]), 1)
        self.assertEqual([d for _, d in self.imap.copied], ["Trash", "Trash"])

    def test_purge_before_over_imap(self):
        self._purge(older_than=7)
        self.assertEqual(len(self.imap.folders["INBOX"]), 2)     # only the 40-day one

    def test_purge_dry_run_over_imap(self):
        out = self._purge(getfrom="Cron Daemon", dry_run=True)
        self.assertIn(b"would purge 2", out)
        self.assertEqual(len(self.imap.folders["INBOX"]), 3)

    def test_purge_no_filter_over_imap(self):
        with self.assertRaises(SystemExit):
            be.cmd_purge(self.ns())

    def test_purge_to_folder_over_imap_auto_creates_and_uses_the_right_folder(self):
        self.assertNotIn("Junk", self.imap.folders)
        self._purge(getfrom="Cron Daemon", to_folder="spam")
        self.assertIn("Junk", self.imap.created)
        self.assertEqual(len(self.imap.folders["Junk"]), 2)
        self.assertNotIn("Trash", self.imap.folders)

    def test_empty_trash_over_imap(self):
        self.imap.add("Trash", _msg("Old <o@x>", "junk 1", "b"))
        self.imap.add("Trash", _msg("Old <o@x>", "junk 2", "b"))
        self.imap.add("Trash", _msg("Old <o@x>", "junk 3", "b"))
        be.cmd_empty_trash(self.ns(dry_run=True))
        self.assertEqual(len(self.imap.folders["Trash"]), 3)      # dry run
        be.cmd_empty_trash(self.ns())
        self.assertEqual(len(self.imap.folders["Trash"]), 0)
        self.assertEqual(len(self.imap.folders["INBOX"]), 3)      # inbox untouched
        self.assertEqual(self.imap.copied, [])                     # deleted, not moved

    def test_purge_all_refused_outside_trash_over_imap(self):
        with self.assertRaises(SystemExit):
            be.cmd_purge(self.ns(all=True))                        # mbox=spool
        self.assertEqual(len(self.imap.folders["INBOX"]), 3)

    def test_purge_over_imap_does_not_lose_messages_if_copy_keeps_failing(self):
        real_uid = self.imap.uid
        def dead_copy(command, *args):
            return ("NO", [b"boom"]) if command.upper() == "COPY" else real_uid(command, *args)
        self.imap.uid = dead_copy
        with self.assertRaises(SystemExit):
            self._purge(getfrom="Cron Daemon")
        self.assertEqual(len(self.imap.folders["INBOX"]), 3)   # nothing expunged


class TestSpamSweep(Base):
    def _m(self, subj):
        return ("From x Mon Sep  1 00:00:00 2026\n"
                "From: whoever <w@x>\nDate: %s\nSubject: %s\n\nbody\n\n"
                % (email.utils.formatdate(localtime=True), subj))

    def _list(self, mbox):
        return [r for r in self.be("list", mbox).stdout.decode().splitlines() if r]

    def test_local_sweep_moves_tagged(self):
        self.spool.write_text(self._m("hi there")
                              + self._m("***SPAM*** cheap watches")
                              + self._m("real mail")
                              + self._m("***SPAM*** you won"))
        self.assertEqual(be._sweep_spam(), 2)
        self.assertEqual(len(self._list(str(self.spool))), 2)
        self.assertEqual(len(self._list("spam")), 2)

    def test_sweep_disabled_by_blank_folder(self):
        conf = self.tmp / "c.conf"
        conf.write_text("[folders]\nspam =\n")
        os.environ["TVMAIL_CONF"] = str(conf)
        self.spool.write_text(self._m("***SPAM*** x"))
        self.assertEqual(be._sweep_spam(), 0)

    def test_custom_tag(self):
        conf = self.tmp / "c.conf"
        conf.write_text("[spam]\nsubject = [JUNK]\n")
        os.environ["TVMAIL_CONF"] = str(conf)
        self.spool.write_text(self._m("[JUNK] hello") + self._m("normal"))
        self.assertEqual(be._sweep_spam(), 1)

    def test_remote_sweep_moves_to_junk(self):
        os.environ["TVMAIL_MODE"] = "remote"
        fake = FakeIMAP()
        fake.add("INBOX", _msg("a@x", "normal", "b"))
        fake.add("INBOX", _msg("s@x", "***SPAM*** cheap", "b"))
        real, be._imap = be._imap, lambda: fake
        self.addCleanup(lambda: setattr(be, "_imap", real))
        self.addCleanup(lambda: setattr(be, "_IMAP", None))
        self.assertEqual(be._sweep_spam(), 1)
        self.assertEqual(len(fake.folders["INBOX"]), 1)
        self.assertIn("Junk", [d for _, d in fake.copied])


class TestOriginHeaders(Base):
    """loud and proud: every sent message names its tool and its origin"""

    def _sent(self, stdin):
        out = self.tmp / "sent.eml"
        mta = self.tmp / "mta"
        mta.write_text("#!%s\nimport sys\nopen(%r,'wb').write(sys.stdin.buffer.read())\n"
                       % (sys.executable, str(out)))
        mta.chmod(0o755)
        os.environ["SENDMAIL"] = str(mta)
        os.environ["TVMAIL_CONF"] = str(self.tmp / "none.conf")
        p = self.be("send", "--to", "a@b", stdin=stdin)
        self.assertEqual(p.returncode, 0, p.stderr)
        return email.message_from_bytes(out.read_bytes())

    def test_plain_text_gets_tool_user_and_host(self):
        m = self._sent(b"hello\n")
        self.assertEqual(m["X-Mailer"], "tvmail-backend 1.2.0")
        self.assertEqual(m["X-Origin-User"], "tester")
        self.assertEqual(m["X-Origin-Host"], socket.gethostname().replace(" ", "_"))

    def test_the_tui_keeps_its_own_x_mailer(self):
        m = self._sent(b"X-Mailer: tvmail 1.2.0\nTo: a@b\nSubject: s\n\nhi\n")
        self.assertEqual(m.get_all("X-Mailer"), ["tvmail 1.2.0"])
        self.assertEqual(m["X-Origin-User"], "tester")

    def test_an_existing_origin_survives_a_resend(self):
        m = self._sent(b"To: a@b\nX-Origin-User: orig\n"
                       b"X-Origin-Host: elsewhere\nSubject: s\n\nhi\n")
        self.assertEqual(m.get_all("X-Origin-User"), ["orig"])
        self.assertEqual(m.get_all("X-Origin-Host"), ["elsewhere"])


class TestSmtpSend(Base):
    def test_smtp_send_forces_from_and_envelope(self):
        conf = self.tmp / "c.conf"
        conf.write_text("[smtp]\nhost=h\nport=25\nstarttls=false\n"
                        "from=Bot <bot@x>\n")
        os.environ["TVMAIL_CONF"] = str(conf)
        os.environ["TVMAIL_SMTP_PASS"] = "x"
        sent = {}

        class FakeSMTP:
            def __init__(s, host, port, timeout=None):
                pass
            def ehlo(s):
                pass
            def starttls(s, context=None):
                pass
            def login(s, u, p):
                pass
            def sendmail(s, frm, rcpts, data):
                sent.update(frm=frm, rcpts=rcpts, data=data)
            def quit(s):
                pass

        import smtplib
        real, smtplib.SMTP = smtplib.SMTP, FakeSMTP
        try:
            m = email.message_from_string("From: orig@y\nSubject: s\n\nhi\n")
            ok = be._smtp_send(m, ["a@b"])
        finally:
            smtplib.SMTP = real
        self.assertTrue(ok)
        self.assertEqual(sent["frm"], "bot@x")               # envelope sender
        self.assertIn(b"From: Bot <bot@x>", sent["data"])
        self.assertNotRegex(sent["data"], rb"(?m)^From: orig@y")
        # ...but the rewrite doesn't lose where it came from
        self.assertIn(b"X-Original-From: orig@y", sent["data"])

    def test_smtp_send_keeps_an_earlier_x_original_from(self):
        conf = self.tmp / "c.conf"
        conf.write_text("[smtp]\nhost=h\nport=25\nstarttls=false\nauth=false\n"
                        "from=Bot <bot@x>\n")
        os.environ["TVMAIL_CONF"] = str(conf)
        sent = {}

        class FakeSMTP:
            def __init__(s, host, port, timeout=None):
                pass
            def ehlo(s):
                pass
            def sendmail(s, frm, rcpts, data):
                sent["data"] = data
            def quit(s):
                pass

        import smtplib
        real, smtplib.SMTP = smtplib.SMTP, FakeSMTP
        try:
            m = email.message_from_string(
                "From: orig@y\nX-Original-From: first@z\nSubject: s\n\nhi\n")
            be._smtp_send(m, ["a@b"])
        finally:
            smtplib.SMTP = real
        self.assertEqual(sent["data"].count(b"X-Original-From:"), 1)
        self.assertIn(b"X-Original-From: first@z", sent["data"])

    def test_smtp_send_no_section_returns_false(self):
        os.environ["TVMAIL_CONF"] = str(self.tmp / "none.conf")
        self.assertFalse(be._smtp_send(email.message.Message(), ["a@b"]))

    def test_smtp_send_verify_false_disables_cert_checking(self):
        # Regression: [smtp] had no verify/cafile at all, so a self-signed
        # cert on STARTTLS could only fail - there was no way to trust it,
        # unlike [imap]'s existing verify/cafile.
        conf = self.tmp / "c.conf"
        conf.write_text("[smtp]\nhost=h\nport=25\nauth=false\nverify=false\n")
        os.environ["TVMAIL_CONF"] = str(conf)
        seen = {}

        class FakeSMTP:
            def __init__(s, host, port, timeout=None):
                pass
            def ehlo(s):
                pass
            def starttls(s, context=None):
                seen["context"] = context
            def sendmail(s, frm, rcpts, data):
                pass
            def quit(s):
                pass

        import smtplib
        real, smtplib.SMTP = smtplib.SMTP, FakeSMTP
        try:
            ok = be._smtp_send(
                email.message_from_string("From: a@b\n\nx\n"), ["c@d"])
        finally:
            smtplib.SMTP = real
        self.assertTrue(ok)
        ctx = seen["context"]
        self.assertFalse(ctx.check_hostname)
        import ssl
        self.assertEqual(ctx.verify_mode, ssl.CERT_NONE)

    def test_smtp_send_verify_default_true_checks_hostname(self):
        conf = self.tmp / "c.conf"
        conf.write_text("[smtp]\nhost=h\nport=25\nauth=false\n")
        os.environ["TVMAIL_CONF"] = str(conf)
        seen = {}

        class FakeSMTP:
            def __init__(s, host, port, timeout=None):
                pass
            def ehlo(s):
                pass
            def starttls(s, context=None):
                seen["context"] = context
            def sendmail(s, frm, rcpts, data):
                pass
            def quit(s):
                pass

        import smtplib
        real, smtplib.SMTP = smtplib.SMTP, FakeSMTP
        try:
            be._smtp_send(email.message_from_string("From: a@b\n\nx\n"), ["c@d"])
        finally:
            smtplib.SMTP = real
        self.assertTrue(seen["context"].check_hostname)

    def test_smtp_send_failed_quit_does_not_mask_the_real_error(self):
        # Regression: a failed STARTTLS left the connection in a state where
        # the cleanup's own S.quit() also raised, and since that happened in
        # a `finally` block it replaced the real error - so a cert failure
        # showed up to the user as an opaque "Server not connected".
        conf = self.tmp / "c.conf"
        conf.write_text("[smtp]\nhost=h\nport=25\nauth=false\n")
        os.environ["TVMAIL_CONF"] = str(conf)
        import smtplib

        class FakeSMTP:
            def __init__(s, host, port, timeout=None):
                pass
            def ehlo(s):
                pass
            def starttls(s, context=None):
                raise smtplib.SMTPException("certificate verify failed: boom")
            def quit(s):
                raise smtplib.SMTPServerDisconnected("please run connect() first")

        real, smtplib.SMTP = smtplib.SMTP, FakeSMTP
        try:
            with self.assertRaises(SystemExit) as cm:
                be._smtp_send(email.message_from_string("From: a@b\n\nx\n"), ["c@d"])
        finally:
            smtplib.SMTP = real
        self.assertIn("certificate verify failed: boom", str(cm.exception))
        self.assertNotIn("connect()", str(cm.exception))

    def test_save_sent_local_appends(self):
        m = email.message_from_string("To: a@b\nSubject: s\n\nhi\n")
        be._save_sent(m)
        r = self.rows("sent")
        self.assertEqual(len(r), 1)
        self.assertIn("s", r[0])

    def test_save_sent_disabled_by_blank_folder(self):
        conf = self.tmp / "c.conf"
        conf.write_text("[folders]\nsent =\n")
        os.environ["TVMAIL_CONF"] = str(conf)
        be._save_sent(email.message_from_string("To: a@b\nSubject: s\n\nx\n"))
        self.assertEqual(self.rows("sent"), [])

    def test_smtp_send_auth_false_needs_no_password(self):
        conf = self.tmp / "c.conf"
        conf.write_text("[smtp]\nhost=h\nport=25\nstarttls=false\nauth=false\n")
        os.environ["TVMAIL_CONF"] = str(conf)          # no netrc, no env pw
        logged = []

        class FakeSMTP:
            def __init__(s, host, port, timeout=None):
                pass
            def ehlo(s):
                pass
            def login(s, u, p):
                logged.append((u, p))
            def sendmail(s, frm, rcpts, data):
                pass
            def quit(s):
                pass

        import smtplib
        real, smtplib.SMTP = smtplib.SMTP, FakeSMTP
        try:
            ok = be._smtp_send(
                email.message_from_string("From: a@b\n\nx\n"), ["c@d"])
        finally:
            smtplib.SMTP = real
        self.assertTrue(ok)
        self.assertEqual(logged, [])                   # never attempted AUTH


class FakeSieve:
    """A just-enough ManageSieve server (RFC 5804) on a loopback port: login
    with PLAIN, LISTSCRIPTS / GETSCRIPT / PUTSCRIPT / SETACTIVE.  No TLS -
    `starttls=True` only *advertises* it, to test the client's refusal."""

    def __init__(self, user="tester", password="pw", starttls=False):
        import socket
        import threading
        self.user, self.password, self.starttls = user, password, starttls
        self.scripts = {}          # name -> script text (LF)
        self.active = ""
        self.reject_put = None     # error text to answer PUTSCRIPT with
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(5)
        self.srv.settimeout(0.1)
        self.port = self.srv.getsockname()[1]
        self.stopped = False
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def close(self):
        # stop the accept loop for real: on Cygwin a close() from another
        # thread leaves a blocked accept() listening, and the next test's
        # server can inherit its port - and answer with its stale state
        self.stopped = True
        self.thread.join(5)
        self.srv.close()

    def _accept(self):
        import socket
        import threading
        while not self.stopped:
            try:
                conn, _ = self.srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            conn.settimeout(None)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        import base64
        import re
        rf = conn.makefile("rb")
        send = lambda s: conn.sendall(s.encode() if isinstance(s, str) else s)
        send('"IMPLEMENTATION" "fake"\r\n"SASL" "PLAIN"\r\n'
             + ('"STARTTLS"\r\n' if self.starttls else "")
             + '"VERSION" "1.0"\r\nOK "ready"\r\n')
        authed = False
        while True:
            line = rf.readline()
            if not line:
                break
            cmd = line.decode().rstrip("\r\n")
            verb = cmd.split(" ", 1)[0].upper()
            if verb == "AUTHENTICATE":
                tok = base64.b64decode(re.search(r'"([^"]*)"\s*$', cmd).group(1)).decode()
                _, u, p = tok.split("\0")
                authed = (u, p) == (self.user, self.password)
                send('OK "welcome"\r\n' if authed else 'NO "Authentication failed."\r\n')
            elif verb == "LOGOUT":
                send('OK "bye"\r\n')
                break
            elif not authed:
                send('NO "Authenticate first."\r\n')
            elif verb == "LISTSCRIPTS":
                for n in self.scripts:
                    send('"%s"%s\r\n' % (n, " ACTIVE" if n == self.active else ""))
                send('OK "done"\r\n')
            elif verb == "GETSCRIPT":
                n = re.search(r'"([^"]*)"', cmd).group(1)
                if n not in self.scripts:
                    send('NO "no such script"\r\n')
                else:
                    raw = self.scripts[n].replace("\n", "\r\n").encode()
                    send(b"{%d}\r\n" % len(raw) + raw + b"\r\nOK \"done\"\r\n")
            elif verb == "PUTSCRIPT":
                m = re.search(r'"([^"]*)" \{(\d+)\+?\}', cmd)
                raw = rf.read(int(m.group(2)))
                rf.readline()                      # the CRLF after the literal
                if self.reject_put:
                    send('NO "%s"\r\n' % self.reject_put)
                else:
                    self.scripts[m.group(1)] = raw.decode().replace("\r\n", "\n")
                    send('OK "stored"\r\n')
            elif verb == "SETACTIVE":
                n = re.search(r'"([^"]*)"', cmd).group(1)
                if n and n not in self.scripts:
                    send('NO "no such script"\r\n')
                else:
                    self.active = n
                    send('OK "done"\r\n')
            else:
                send('NO "unknown command"\r\n')
        conn.close()


class TestVacation(Base):
    def setUp(self):
        super().setUp()
        self.sieve = FakeSieve()
        self.conf = self.tmp / "tvmail.conf"
        self.write_conf()
        os.environ["TVMAIL_CONF"] = str(self.conf)
        os.environ["TVMAIL_IMAP_PASS"] = "pw"
        os.environ["TVMAIL_MODE"] = "remote"          # a client, not the master
        os.environ["TVMAIL_POSTFIX_GENERIC"] = str(self.tmp / "no-such-generic")
        self.addCleanup(os.environ.pop, "TVMAIL_POSTFIX_GENERIC", None)

    def tearDown(self):
        self.sieve.close()
        super().tearDown()

    def write_conf(self, extra="tls = none"):
        self.conf.write_text("[sieve]\nhost = 127.0.0.1\nport = %d\nuser = tester\n%s\n"
                             % (self.sieve.port, extra))

    def msgfile(self, text="I am away.\n"):
        p = self.tmp / "msg.txt"
        p.write_text(text)
        return str(p)

    def vac(self, *args):
        return self.be("vacation", *args)

    # -- the script text itself ------------------------------------------
    def test_script_without_dates_is_a_bare_vacation(self):
        s = be._vac_script("Away", 7, "", "", "Back soon.")
        self.assertIn('require ["vacation"];', s)
        self.assertNotIn("currentdate", s)
        self.assertIn('vacation :days 7 :subject "Away" text:\nBack soon.\n.\n;', s)

    def test_script_with_dates_tests_currentdate_inclusively(self):
        s = be._vac_script("Away", 3, "2026-12-24", "2026-12-31", "x")
        self.assertIn('require ["vacation", "date", "relational"];', s)
        self.assertIn('currentdate :value "ge" "date" "2026-12-24"', s)
        self.assertIn('currentdate :value "le" "date" "2026-12-31"', s)

    def test_script_round_trips_quotes_and_dot_lines(self):
        msg = 'She said "hi" \\ bye\n.\n..two dots\nlast line'
        s = be._vac_script('Out "of" office', 5, "2026-01-02", "", msg)
        info = be._vac_parse(s)
        self.assertEqual(info["subject"], 'Out "of" office')
        self.assertEqual(info["days"], "5")
        self.assertEqual(info["start"], "2026-01-02")
        self.assertNotIn("end", info)
        self.assertEqual(info["message"], msg)
        # nothing in the stuffed body can end the text: block early
        self.assertEqual(s.count("\n.\n"), 1)

    # -- the CLI against the fake server -----------------------------------
    def test_status_none_then_on_then_off(self):
        self.assertEqual(self.vac("status").stdout.decode().splitlines()[0], "state: none")
        p = self.vac("on", "--message-file", self.msgfile(), "--subject", "Gone",
                     "--days", "4", "--start", "2026-07-01", "--end", "2026-07-14")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.sieve.active, "tvmail-vacation")
        st = self.vac("status").stdout.decode()
        self.assertTrue(st.startswith("state: on\nsubject: Gone\ndays: 4\n"
                                      "start: 2026-07-01\nend: 2026-07-14\naddresses: \n\n"), st)
        self.assertTrue(st.endswith("I am away.\n"), st)
        self.assertEqual(self.vac("off").returncode, 0)
        self.assertEqual(self.sieve.active, "")
        self.assertIn("tvmail-vacation", self.sieve.scripts)   # kept for next time
        self.assertTrue(self.vac("status").stdout.decode().startswith("state: off\n"))

    def test_addresses_option_and_config_default(self):
        # the server only answers mail addressed to one of *my* addresses, and
        # mail pulled from a POP account is addressed to the POP one
        p = self.vac("on", "--message-file", self.msgfile(),
                     "--addresses", "cwp@u-l.ca, cpbee@u-l.ca")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(':addresses ["cwp@u-l.ca", "cpbee@u-l.ca"]',
                      self.sieve.scripts["tvmail-vacation"])
        self.assertIn("addresses: cwp@u-l.ca, cpbee@u-l.ca\n",
                      self.vac("status").stdout.decode())
        # no --addresses: the NAT table is the default ([sieve] addresses is
        # one of its sources), and status offers it before anything is saved
        self.sieve.scripts.clear()
        self.write_conf(extra="tls = none\naddresses = me@x.org")
        self.assertIn("addresses: me@x.org\n", self.vac("status").stdout.decode())
        self.vac("on", "--message-file", self.msgfile())
        self.assertIn(':addresses ["me@x.org"]', self.sieve.scripts["tvmail-vacation"])

    # -- the "NAT table": public address <-> local identity ----------------
    # cpbee fetches cwp@u-l.ca over POP and delivers it to a local user, and
    # Postfix rewrites every local sender back to that public address, so mail
    # arrives addressed to the public one and the vacation test must know it.
    def test_nat_table_gathers_public_and_local_identities(self):
        (self.home / ".mail-test").write_text(
            'mailer { url "smtp://cpbee%40u-l.ca@mail.u-l.ca:26"; };\n')
        os.environ["TVMAIL_MU_CONF"] = str(self.home / ".mail-test")
        self.conf.write_text(
            "[sieve]\nhost = cpbee\nuser = chris\naddresses = cwp@u-l.ca, CPBEE@u-l.ca\n"
            "[smtp]\nfrom = Chris Pollitt <cwp@u-l.ca>\n")
        got = be._own_addresses()
        self.assertEqual([a for a, _ in got],
                         ["cwp@u-l.ca", "CPBEE@u-l.ca", "chris@cpbee"])   # deduped, any case
        self.assertEqual(got[0][1], "[sieve] addresses")
        self.assertEqual(got[-1][1], "the master")

    def test_nat_table_reads_the_masters_postfix_generic_map(self):
        os.environ["TVMAIL_MODE"] = "local"
        gen = self.tmp / "generic"
        # two users on one master, plus the shared domain-wide rewrite: each
        # user's table gets only their own line, never the shared address or
        # the other user's
        gen.write_text("# comment\n@cpbee  shared@u-l.ca\nroot@localhost   shared@u-l.ca\n"
                       "tester@cpbee  tester@u-l.ca\nmiche@cpbee  miche@u-l.ca\n")
        os.environ["TVMAIL_POSTFIX_GENERIC"] = str(gen)       # USER=tester here
        out = self.vac("addresses").stdout.decode().splitlines()
        pub = [l.split("\t") for l in out if l.endswith("postfix generic map")]
        self.assertEqual([p[0] for p in pub], ["tester@u-l.ca"])
        self.assertTrue(any(l.endswith("this host") for l in out))       # local identity too
        # ... and `on` with no --addresses uses them
        self.vac("on", "--message-file", self.msgfile())
        script = self.sieve.scripts["tvmail-vacation"]
        self.assertIn('"tester@u-l.ca"', script)
        self.assertNotIn("miche@u-l.ca", script)
        self.assertNotIn("shared@u-l.ca", script)

    def test_plus_addressing_gives_each_user_a_tagged_public_address(self):
        # cwp+chris@u-l.ca and cwp+miche@u-l.ca both land in cwp@u-l.ca; the
        # tag says whose, so one public mailbox can serve several users
        def table(plus, user="chris", **kw):
            os.environ["USER"] = user
            self.conf.write_text(
                "[sieve]\nhost = cpbee\nuser = %s\nplus = %s\naddresses = cwp@u-l.ca, odd+x@u-l.ca\n"
                "%s" % (user, plus, "".join("%s = %s\n" % kv for kv in kw.items())))
            return be._own_addresses()
        got = table("true")
        self.assertEqual([a for a, _ in got],
                         ["cwp+chris@u-l.ca", "odd+x@u-l.ca", "chris@cpbee"])  # bare dropped
        self.assertEqual(got[0][1], "plus-address of cwp@u-l.ca")
        self.assertEqual([a for a, _ in table("true", "miche")][0], "cwp+miche@u-l.ca")
        self.assertEqual([a for a, _ in table("both")][:2], ["cwp@u-l.ca", "cwp+chris@u-l.ca"])
        self.assertEqual([a for a, _ in table("true", tag="home")][0], "cwp+home@u-l.ca")
        self.assertEqual([a for a, _ in table("false")][0], "cwp@u-l.ca")   # off by default

    def test_junk_address_is_refused(self):
        p = self.vac("on", "--message-file", self.msgfile(), "--addresses", 'a"b@x')
        self.assertNotEqual(p.returncode, 0)
        p = self.vac("on", "--message-file", self.msgfile(), "--addresses", "nobody")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.sieve.scripts, {})

    def test_on_refuses_to_replace_another_active_script(self):
        self.sieve.scripts["filters"] = 'require "fileinto";\n'
        self.sieve.active = "filters"
        self.assertIn("other_active: filters", self.vac("status").stdout.decode())
        p = self.vac("on", "--message-file", self.msgfile())
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b"filters", p.stdout + p.stderr)
        self.assertEqual(self.sieve.active, "filters")
        self.assertNotIn("tvmail-vacation", self.sieve.scripts)
        p = self.vac("on", "--message-file", self.msgfile(), "--force")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.sieve.active, "tvmail-vacation")
        self.assertIn("filters", self.sieve.scripts)           # not deleted

    def test_bad_input_is_refused_before_touching_the_server(self):
        for args in (("--start", "tomorrow"),
                     ("--start", "2026-07-14", "--end", "2026-07-01"),
                     ("--days", "0")):
            p = self.vac("on", "--message-file", self.msgfile(), *args)
            self.assertNotEqual(p.returncode, 0, args)
        p = self.vac("on", "--message-file", self.msgfile("  \n"))
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.sieve.scripts, {})

    def test_server_rejecting_the_script_says_why(self):
        self.sieve.reject_put = "line 4: unknown command 'vacatoin'"
        p = self.vac("on", "--message-file", self.msgfile())
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b"unknown command", p.stdout + p.stderr)
        self.assertEqual(self.sieve.active, "")

    def test_wrong_password_is_reported(self):
        os.environ["TVMAIL_IMAP_PASS"] = "nope"
        p = self.vac("status")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b"login", p.stdout + p.stderr)

    def test_no_server_says_how_to_enable_it(self):
        self.sieve.close()
        p = self.vac("status")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b"managesieve", p.stdout + p.stderr)

    def test_password_is_never_sent_without_tls_unless_told_to(self):
        self.write_conf(extra="")            # tls defaults to starttls
        p = self.vac("status")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b"STARTTLS", p.stdout + p.stderr)
        self.assertEqual(self.sieve.active, "")


if __name__ == "__main__":
    unittest.main()
