#!/usr/bin/env python3
"""Tests for tvcommon/py/tvnntp.py against a small fake NNTP server.

    python3 -m unittest tvcommon.tests.test_tvnntp      (or: python3 tvcommon/tests/test_tvnntp.py)

FakeNNTP is also what tvmail's tests use for the Usenet service (import it from this file).
"""
import email
import importlib.util
import os
import shutil
import socketserver
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("tvnntp", str(HERE.parent / "py" / "tvnntp.py"))
tn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tn)


def article(frm, date, subj, body, msgid, refs="", groups="lan.test"):
    h = "From: %s\nDate: %s\nNewsgroups: %s\nSubject: %s\nMessage-ID: %s\n" % (frm, date, groups, subj, msgid)
    if refs:
        h += "References: %s\n" % refs
    return (h + "\n" + body + "\n").encode("utf-8")


class Handler(socketserver.StreamRequestHandler):
    def reply(self, line):
        self.wfile.write(line.encode("utf-8") + b"\r\n")

    def block(self, lines):
        for ln in lines:
            if isinstance(ln, str):
                ln = ln.encode("utf-8")
            if ln.startswith(b"."):
                ln = b"." + ln
            self.wfile.write(ln + b"\r\n")
        self.wfile.write(b".\r\n")

    def handle(self):
        srv = self.server.fake
        srv.connections += 1
        self.reply("201 fake NNTP, posting prohibited" if srv.readonly else "200 fake NNTP ready")
        cur = None
        authed = not srv.password
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode("utf-8", "replace").strip()
            srv.log.append(line)
            word = line.split(" ")[0].upper()
            arg = line[len(word):].strip()
            if srv.drop_after and srv.connections_commands() >= srv.drop_after:
                srv.drop_after = 0
                return                                  # the connection dies in the middle of a session
            if word == "QUIT":
                self.reply("205 bye")
                return
            if word == "MODE":
                self.reply("201 reader" if srv.readonly else "200 reader")
            elif word == "AUTHINFO":
                sub, _, val = arg.partition(" ")
                if sub.upper() == "USER":
                    self.reply("381 password")
                elif val == srv.password:
                    authed = True
                    self.reply("281 ok")
                else:
                    self.reply("481 no")
            elif srv.password and not authed:
                self.reply("480 authentication required")
            elif word == "GROUP":
                g = srv.groups.get(arg)
                if g is None:
                    self.reply("411 no such group")
                else:
                    cur = arg
                    nums = sorted(g)
                    self.reply("211 %d %d %d %s" % (len(nums), nums[0] if nums else 1, nums[-1] if nums else 0, arg))
            elif word == "LIST":
                what = arg.split(" ")[0].upper()
                if what == "OVERVIEW.FMT":
                    self.reply("215 fields")
                    self.block(["Subject:", "From:", "Date:", "Message-ID:", "References:", ":bytes", ":lines"])
                elif what == "NEWSGROUPS":
                    if srv.no_descriptions:
                        self.reply("503 not supported")
                    else:
                        self.reply("215 descriptions")
                        self.block(["%s\t%s" % (n, srv.descriptions.get(n, "")) for n in sorted(srv.groups)])
                else:
                    self.reply("215 active")
                    rows = []
                    for n in sorted(srv.groups):
                        nums = sorted(srv.groups[n])
                        rows.append("%s %d %d %s" % (n, nums[-1] if nums else 0, nums[0] if nums else 1,
                                                    "n" if n in srv.readonly_groups else "y"))
                    self.block(rows)
            elif word in ("OVER", "XOVER"):
                if word == "OVER" and srv.no_over:
                    self.reply("500 unknown command")
                    continue
                if cur is None:
                    self.reply("412 no group selected")
                    continue
                a, _, b = arg.partition("-")
                lo, hi = int(a), int(b or a)
                nums = [n for n in sorted(srv.groups[cur]) if lo <= n <= hi]
                if not nums:
                    self.reply("423 no articles")
                    continue
                self.reply("224 overview")
                rows = []
                for n in nums:
                    m = email.message_from_bytes(srv.groups[cur][n])
                    rows.append("\t".join([str(n), (m["Subject"] or "").replace("\t", " "), m["From"] or "", m["Date"] or "",
                                           m["Message-ID"] or "", m["References"] or "",
                                           str(len(srv.groups[cur][n])), str(srv.groups[cur][n].count(b"\n"))]))
                self.block(rows)
            elif word == "ARTICLE":
                if cur is None:
                    self.reply("412 no group selected")
                    continue
                n = int(arg) if arg.isdigit() else 0
                data = srv.groups[cur].get(n)
                if data is None:
                    self.reply("423 no such article")
                    continue
                self.reply("220 %d article" % n)
                self.block(data.decode("utf-8", "replace").split("\n")[:-1])
            elif word == "POST":
                if srv.readonly:
                    self.reply("440 posting not permitted")
                    continue
                self.reply("340 send it")
                lines = []
                while True:
                    ln = self.rfile.readline().rstrip(b"\r\n")
                    if ln == b".":
                        break
                    lines.append(ln[1:] if ln.startswith(b"..") else ln)
                data = b"\n".join(lines) + b"\n"
                if srv.refuse_post:
                    self.reply("441 posting failed: " + srv.refuse_post)
                    continue
                srv.posted.append(data)
                m = email.message_from_bytes(data)
                for g in (m["Newsgroups"] or "").split(","):
                    g = g.strip()
                    if g in srv.groups:
                        n = max(srv.groups[g] or [0]) + 1
                        srv.groups[g][n] = data
                self.reply("240 article received")
            else:
                self.reply("500 unknown command")


class FakeNNTP(object):
    """An NNTP server in this process: .groups = {name: {number: raw article bytes}}, .posted = what was POSTed."""

    def __init__(self, groups=None, readonly=False, password=None):  # allowlist secret: test fake
        self.groups = groups or {}
        self.readonly = readonly
        self.password = password  # allowlist secret: test fake
        self.descriptions = {}
        self.readonly_groups = set()
        self.no_over = False
        self.no_descriptions = False
        self.refuse_post = ""
        self.drop_after = 0
        self.posted = []
        self.log = []
        self.connections = 0
        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.srv.daemon_threads = True
        self.srv.fake = self
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def connections_commands(self):
        return len(self.log)

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


A1 = article("alice@example.org (Alice)", "Mon, 01 Sep 2026 10:00:00 +0000", "Hello", "first body", "<a1@x>")
A2 = article("Bob <bob@example.org>", "Mon, 01 Sep 2026 11:00:00 +0000", "Re: Hello", "second body\n.dot line\n..two", "<a2@x>",
             refs="<a1@x>")
A3 = article("carol@example.org", "Tue, 02 Sep 2026 09:30:00 +0000", "Other thread", "third body", "<a3@x>")


def sample():
    return {"lan.test": {1: A1, 2: A2, 5: A3}, "lan.empty": {}}


class TestClient(unittest.TestCase):
    def setUp(self):
        self.fake = FakeNNTP(sample())
        self.addCleanup(self.fake.stop)

    def conn(self, **kw):
        c = tn.NNTP("127.0.0.1", self.fake.port, **kw)
        self.addCleanup(c.close)
        return c

    def test_group_counts_and_missing_group(self):
        c = self.conn()
        self.assertEqual(c.group("lan.test"), (3, 1, 5))
        with self.assertRaises(tn.NNTPError) as e:
            c.group("no.such")
        self.assertEqual(e.exception.code, 411)

    def test_overview_rows(self):
        c = self.conn()
        c.group("lan.test")
        rows = c.over(1, 5)
        self.assertEqual([r["number"] for r in rows], [1, 2, 5])
        self.assertEqual(rows[1]["subject"], "Re: Hello")
        self.assertEqual(rows[1]["references"], "<a1@x>")
        self.assertEqual(rows[2]["message-id"], "<a3@x>")
        self.assertEqual(c.over(3, 4), [])                      # 423: nothing there

    def test_xover_is_the_fallback(self):
        self.fake.no_over = True
        c = self.conn()
        c.group("lan.test")
        self.assertEqual(len(c.over(1, 5)), 3)
        self.assertIn("XOVER 1-5", self.fake.log)

    def test_article_is_unstuffed_and_lf(self):
        c = self.conn()
        c.group("lan.test")
        raw = c.article(2)
        self.assertIn(b"\n.dot line\n..two\n", raw)             # the leading dot was stuffed on the wire, and unstuffed
        self.assertNotIn(b"\r", raw)
        m = email.message_from_bytes(raw)
        self.assertEqual(m["Message-ID"], "<a2@x>")
        with self.assertRaises(tn.NNTPError):
            c.article(3)

    def test_list_active_and_descriptions(self):
        self.fake.descriptions = {"lan.test": "Testing"}
        self.fake.readonly_groups = {"lan.empty"}
        c = self.conn()
        act = dict((r[0], r) for r in c.list_active())
        self.assertEqual(act["lan.test"], ("lan.test", 5, 1, "y"))
        self.assertEqual(act["lan.empty"][3], "n")
        self.assertEqual(c.list_newsgroups()["lan.test"], "Testing")
        self.fake.no_descriptions = True
        self.assertEqual(c.list_newsgroups(), {})

    def test_post_and_refusal(self):
        c = self.conn()
        self.assertTrue(c.can_post)
        c.post(b"Newsgroups: lan.test\nSubject: s\n\n.leading dot\nbody\n")
        self.assertEqual(self.fake.posted[0], b"Newsgroups: lan.test\nSubject: s\n\n.leading dot\nbody\n")
        self.assertIn(5 + 1, self.fake.groups["lan.test"])
        self.fake.refuse_post = "no way"
        with self.assertRaises(tn.NNTPError) as e:
            c.post(b"Newsgroups: lan.test\nSubject: s\n\nbody\n")
        self.assertEqual(e.exception.code, 441)

    def test_read_only_server_cannot_post(self):
        self.fake.readonly = True
        c = self.conn()
        self.assertFalse(c.can_post)
        with self.assertRaises(tn.NNTPError) as e:
            c.post(b"Newsgroups: lan.test\nSubject: s\n\nbody\n")
        self.assertEqual(e.exception.code, 440)

    def test_authinfo(self):
        self.fake.password = "pw"
        c = self.conn(user="chris", password="pw")
        self.assertEqual(c.group("lan.test")[0], 3)
        with self.assertRaises(tn.NNTPError):
            tn.NNTP("127.0.0.1", self.fake.port, user="chris", password="bad")

    def test_a_dropped_connection_is_an_abort(self):
        c = self.conn()
        self.fake.drop_after = len(self.fake.log) + 1
        with self.assertRaises(tn.NNTPAbort):
            c.group("lan.test")


class TestNewsrc(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tvnntp-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.path = str(self.tmp / "sub" / "newsrc-x")

    def test_seed_when_there_is_no_file_then_the_file_rules(self):
        rc = tn.Newsrc(self.path, seed=["a.b", "c.d"])
        self.assertEqual(rc.subscribed(), ["a.b", "c.d"])
        rc.unsubscribe("c.d")
        rc.save()
        rc2 = tn.Newsrc(self.path, seed=["zzz"])                # the file exists: the seed is not used again
        self.assertEqual(rc2.subscribed(), ["a.b"])
        self.assertEqual(rc2.groups, ["a.b", "c.d"])

    def test_ranges_merge_and_round_trip(self):
        rc = tn.Newsrc(self.path)
        for n in (5, 3, 4, 9, 1):
            rc.mark("g", n)
        self.assertEqual(rc.read["g"], [[1, 1], [3, 5], [9, 9]])
        rc.mark_range("g", 6, 8)
        self.assertEqual(rc.read["g"], [[1, 1], [3, 9]])
        rc.save()
        self.assertEqual(Path(self.path).read_text(), "g: 1,3-9\n")
        again = tn.Newsrc(self.path)
        self.assertTrue(again.is_read("g", 7))
        self.assertFalse(again.is_read("g", 2))

    def test_unmark_splits_a_range(self):
        rc = tn.Newsrc(self.path)
        rc.mark_range("g", 1, 10)
        rc.mark("g", 4, False)
        self.assertEqual(rc.read["g"], [[1, 3], [5, 10]])
        rc.mark("g", 1, False)
        rc.mark("g", 10, False)
        self.assertEqual(rc.read["g"], [[2, 3], [5, 9]])

    def test_classic_file_is_read(self):
        os.makedirs(os.path.dirname(self.path))
        Path(self.path).write_text("comp.os.linux: 1-4000,4010\nalt.old! 1-5\n\nbroken line\n")
        rc = tn.Newsrc(self.path)
        self.assertEqual(rc.groups, ["comp.os.linux", "alt.old"])
        self.assertEqual(rc.subscribed(), ["comp.os.linux"])
        self.assertTrue(rc.is_read("comp.os.linux", 4010))
        self.assertFalse(rc.is_read("comp.os.linux", 4011))

    def test_unread_estimate(self):
        rc = tn.Newsrc(self.path)
        self.assertEqual(rc.unread_estimate("g", 3, 1, 5), 3)           # nothing read: the server's count
        rc.mark("g", 1)
        self.assertEqual(rc.unread_estimate("g", 3, 1, 5), 3)           # 4 numbers unread, but only 3 articles exist
        rc.mark_range("g", 1, 5)
        self.assertEqual(rc.unread_estimate("g", 3, 1, 5), 0)           # caught up
        self.assertEqual(rc.unread_estimate("g", 0, 1, 0), 0)           # an empty group
        rc.catch_up("h", 5000)
        self.assertEqual(rc.unread_estimate("h", 100, 4901, 5000), 0)


if __name__ == "__main__":
    unittest.main()
