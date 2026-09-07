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
                "TVMAIL_IMAP_PASS", "TVMAIL_SMTP_PASS")
        self._saved = {k: os.environ.get(k) for k in keys}
        os.environ.update(HOME=str(self.home), XDG_DATA_HOME=str(self.xdg),
                          MAIL=str(self.spool), USER="tester")
        for k in ("MAILRC", "DEAD", "SENDMAIL", "TVMAIL_CONF", "TVMAIL_MODE",
                  "TVMAIL_IMAP_PASS", "TVMAIL_SMTP_PASS"):
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


# --------------------------------------------------------------------------
# remote (IMAP/SMTP client) mode
# --------------------------------------------------------------------------
class TestMode(Base):
    def _write_conf(self, text):
        p = self.tmp / "tvmail.conf"
        p.write_text(textwrap.dedent(text))
        os.environ["TVMAIL_CONF"] = str(p)

    def test_mode_auto_defaults_local_without_imap(self):
        self.assertEqual(be._mode(), "local")

    def test_mode_auto_remote_when_imap_present(self):
        self._write_conf("[imap]\nhost = x\n")
        self.assertEqual(be._mode(), "remote")

    def test_mode_explicit_local_wins_over_imap(self):
        self._write_conf("[service]\nmode = local\n[imap]\nhost = x\n")
        self.assertEqual(be._mode(), "local")

    def test_mode_master_hostname_forces_local(self):
        host = __import__("socket").gethostname().split(".")[0]
        self._write_conf("[service]\nmaster = %s ; other\n[imap]\nhost = x\n" % host)
        self.assertEqual(be._mode(), "local")

    def test_mode_env_override(self):
        os.environ["TVMAIL_MODE"] = "remote"
        self.assertEqual(be._mode(), "remote")

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


class FakeIMAP:
    """Just enough IMAP for the backend's remote paths."""
    def __init__(self):
        self.folders = {}          # name -> [[uid, {flags}, raw], ...]
        self._uid = 1
        self.cur = None
        self.copied = []
        self.appended = []

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
                    m = email.message_from_bytes(raw)
                    hdr = ("".join("%s: %s\r\n" % (h, m.get(h, ""))
                                   for h in ("Date", "From", "Subject"))
                           ).encode() + b"\r\n"
                    meta = ("%d (UID %d FLAGS (%s) BODY[] {%d}"
                            % (uid, uid, fl, len(hdr))).encode()
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
            for r in self._rows():
                if r[0] == uid:
                    self.folders.setdefault(dest, []).append(
                        [self._uid, set(r[1]), r[2]])
                    self._uid += 1
                    self.copied.append((uid, dest))
            return ("OK", [b"ok"])
        return ("OK", [b""])

    def expunge(self):
        self.folders[self.cur] = [r for r in self._rows()
                                  if "\\Deleted" not in r[1]]
        return ("OK", [b"1"])

    def append(self, mailbox, flags, date, message):
        name = mailbox.strip('"')
        raw = message if isinstance(message, bytes) else message.encode()
        self.folders.setdefault(name, []).append([self._uid, set(), raw])
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

    def test_show_over_imap(self):
        out = self._run(be.cmd_show, self.ns(idx=1)).decode()
        self.assertIn("From:", out)
        self.assertIn("second body", out)

    def test_mark_over_imap(self):
        be.cmd_mark(self.ns(idx=1, state="read"))
        rows = self._run(be.cmd_list, self.ns()).decode().splitlines()
        self.assertEqual(rows[1].split("\t")[1], ".")

    def test_delete_over_imap_moves_to_trash(self):
        self._run(be.cmd_delete, self.ns(idx=[0], mbox="spool", trash="trash"))
        self.assertEqual(len(self.imap.folders["INBOX"]), 2)
        self.assertEqual([d for _, d in self.imap.copied], ["Trash"])

    def _feed_stdin(self, data):
        class _In:
            buffer = io.BytesIO(data)
        self._old_stdin, sys.stdin = sys.stdin, _In()
        self.addCleanup(lambda: setattr(sys, "stdin", self._old_stdin))

    def test_save_draft_over_imap_appends(self):
        self._feed_stdin(b"To: x@y\nSubject: d\n\nbody\n")
        self._run(be.cmd_save_draft, self.ns())
        self.assertEqual([n for n, _ in self.imap.appended], ["Drafts"])

    def test_send_over_imap_uses_smtp(self):
        seen = {}
        real, be._smtp_send = be._smtp_send, \
            lambda m, r: (seen.setdefault("rcpts", r), True)[1]
        self.addCleanup(lambda: setattr(be, "_smtp_send", real))
        self._feed_stdin(b"To: a@b\nSubject: s\n\nhi\n")
        out = self._run(be.cmd_send, self.ns(getfrom=None, to=[], subject=None))
        self.assertIn(b"sent", out)
        self.assertEqual(seen["rcpts"], ["a@b"])


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
            self._m("Cron Daemon <root@cmpi>", "Cron <root@cmpi> check", old, "c1")
            + self._m("Alice <a@x>", "hi", new, "hello")
            + self._m("Cron Daemon <root@cmpi>", "Cron <root@cmpi> gravity", new, "c2")
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

    def test_purge_expunge_skips_trash(self):
        self.be("purge", "--from", "Cron Daemon", "--expunge")
        self.assertEqual(self.n(), 2)
        self.assertEqual(self.n("trash"), 0)

    def test_purge_needs_a_filter(self):
        p = self.be("purge")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b"no filter", p.stdout + p.stderr)


class TestPurgeIMAP(Base):
    def setUp(self):
        super().setUp()
        os.environ["TVMAIL_MODE"] = "remote"
        self.imap = FakeIMAP()
        old = email.utils.formatdate(time.time() - 40 * 86400)
        new = email.utils.formatdate(time.time() - 1 * 86400)
        self.imap.add("INBOX", _msg("Cron Daemon <r@cmpi>", "Cron A", "o", date=old))
        self.imap.add("INBOX", _msg("Alice <a@x>", "hi", "b", date=new))
        self.imap.add("INBOX", _msg("Cron Daemon <r@cmpi>", "Cron B", "o", date=new))
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
        self.assertNotIn(b"orig@y", sent["data"])

    def test_smtp_send_no_section_returns_false(self):
        os.environ["TVMAIL_CONF"] = str(self.tmp / "none.conf")
        self.assertFalse(be._smtp_send(email.message.Message(), ["a@b"]))

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


if __name__ == "__main__":
    unittest.main()
