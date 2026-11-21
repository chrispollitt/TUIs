#!/usr/bin/env python3
"""tvmail-backend's Usenet service, against tvcommon's fake NNTP server.

    python3 -m unittest discover -s tests -p 'test_news.py'
"""
import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / "backend" / "tvmail-backend"
_spec = importlib.util.spec_from_file_location("test_tvnntp", str(HERE.parent.parent / "tvcommon" / "tests" / "test_tvnntp.py"))
tn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tn)

sys.path.insert(0, str(HERE))
from test_backend import Base          # noqa: E402  (the temp HOME / XDG_DATA_HOME / mail fixture)


class NewsBase(Base):
    def setUp(self):
        super().setUp()
        self.fake = tn.FakeNNTP(tn.sample())
        # a group with no gaps in its numbers, where the unread count is exact (with gaps it is an upper bound)
        self.fake.groups["lan.dense"] = {1: tn.A1, 2: tn.A2, 3: tn.A3, 4: tn.A1}
        self.addCleanup(self.fake.stop)
        self.conf = self.tmp / "tvmail.conf"
        self.write_conf()
        os.environ["TVMAIL_CONF"] = str(self.conf)
        self.newsrc = self.xdg / "tvmail" / "newsrc-127.0.0.1"

    def write_conf(self, extra=""):
        self.conf.write_text("[services]\norder = mail, news\n[news]\nhost = 127.0.0.1\nport = %d\n"
                             "subscribed = lan.test, lan.empty, lan.dense\n%s" % (self.fake.port, extra))

    def news(self, *args, stdin=None):
        return self.be("--service", "news", *args, stdin=stdin)

    def out(self, *args, stdin=None):
        p = self.news(*args, stdin=stdin)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.decode("utf-8")

    def rows(self, *args):
        return [l.split("\t") for l in self.out(*args).splitlines()]


class TestNewsService(NewsBase):
    def test_news_is_a_service_only_once_it_has_a_host(self):
        self.assertEqual(self.be("services").stdout.decode(), "mail\tmail\tMail\nnews\tusenet\tUsenet\n")
        self.conf.write_text("[services]\norder = mail, news\n")
        self.assertEqual(self.be("services").stdout.decode(), "mail\tmail\tMail\n")
        p = self.news("folders")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b"not set up", p.stderr)

    def test_folders_are_the_subscribed_groups_with_counts_in_alphabetical_order(self):
        self.assertEqual(self.rows("folders"), [["lan.dense", "lan.dense", "4", "4"], ["lan.empty", "lan.empty", "0", "0"],
                                                ["lan.test", "lan.test", "3", "3"]])

    def test_sort_newsrc_keeps_the_order_you_subscribed_in(self):
        self.write_conf("sort = newsrc\n")
        self.assertEqual([r[0] for r in self.rows("folders")], ["lan.test", "lan.empty", "lan.dense"])
        self.write_conf("sort = alpha\n")
        self.assertEqual([r[0] for r in self.rows("folders")], ["lan.dense", "lan.empty", "lan.test"])

    def test_list_rows_are_the_overview(self):
        r = self.rows("list", "lan.test")
        self.assertEqual([x[0] for x in r], ["1", "2", "5"])               # article numbers are the indexes
        self.assertEqual([x[1] for x in r], ["N", "N", "N"])
        self.assertEqual(r[0][3:5], ["Alice", "Hello"])
        self.assertEqual(r[1][3:5], ["Bob", "Re: Hello"])
        self.assertEqual(r[1][5:], ["<a2@x>", "<a1@x>"])                    # message-id, references: for the threads
        self.assertRegex(r[0][2], r"^2026-09-0\d \d\d:\d\d$")

    def test_list_shows_the_newest_window_only(self):
        self.assertEqual([x[0] for x in self.rows("list", "lan.test", "--limit", "1")], ["5"])
        self.write_conf("window = 4\n")
        self.assertEqual([x[0] for x in self.rows("list", "lan.test")], ["2", "5"])    # the newest four numbers, 2-5: no 3 or 4
        self.assertEqual(self.out("list", "lan.empty"), "")

    def test_old_articles_with_unreadable_dates_still_list(self):
        old = tn.article("utzoo!henry", "Sat 7 Mar 87 25:99 PST", "net.jokes: stuff", "x", "<old@x>", groups="lan.test")
        self.fake.groups["lan.test"][6] = old
        r = self.rows("list", "lan.test")
        self.assertEqual(r[-1][0], "6")
        self.assertEqual(r[-1][4], "net.jokes: stuff")

    def test_show_and_mark_read(self):
        text = self.out("show", "2", "lan.test")
        self.assertIn("Subject:    Re: Hello", text)
        self.assertIn("Newsgroups: lan.test", text)
        self.assertIn("second body", text)
        self.assertEqual(self.rows("folders")[0][:4], ["lan.dense", "lan.dense", "4", "4"])   # showing is not reading
        self.assertEqual(self.out("mark", "2", "read", "lan.test"), "marked\n")
        self.assertEqual(self.newsrc.read_text(), "lan.test: 2\nlan.empty:\nlan.dense:\n")
        self.assertEqual([x[1] for x in self.rows("list", "lan.test")], ["N", ".", "N"])
        self.news("mark", "3", "read", "lan.dense")
        self.assertEqual(self.rows("folders")[0][3], "3")                   # exact where the numbers have no gaps
        self.news("mark", "3", "unread", "lan.dense")
        self.assertEqual(self.rows("folders")[0][3], "4")
        self.news("mark", "2", "unread", "lan.test")
        self.assertEqual([x[1] for x in self.rows("list", "lan.test")], ["N", "N", "N"])
        self.out("show", "5", "lan.test", "--mark-read")
        self.assertEqual([x[1] for x in self.rows("list", "lan.test")], ["N", "N", "."])

    def test_raw_is_the_article(self):
        raw = self.news("raw", "1", "lan.test").stdout
        self.assertTrue(raw.startswith(b"From: alice@example.org (Alice)\n"))
        self.assertIn(b"first body", raw)
        p = self.news("show", "3", "lan.test")                              # there is no article 3
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b"no such article", p.stderr)

    def test_search_finds_headers_at_once_and_text_by_fetching(self):
        def found(*q):
            return self.out("search", "lan.test", *q).split()
        self.assertEqual(found("alice"), ["1"])                 # From (the overview)
        self.assertEqual(found("second"), ["2"])                # the text of article 2 only
        self.assertEqual(found("body"), ["1", "2", "5"])        # numbers are the article numbers, as in list
        self.assertEqual(found("second body"), ["2"])
        self.assertEqual(found('"second body" bob'), ["2"])     # a quoted phrase, and a second word
        self.assertEqual(found("zebra"), [])

    def test_search_that_runs_out_of_time_says_how_far_it_got(self):
        out = self.out("search", "lan.test", "zebra", "--budget", "0")
        self.assertEqual(out, "# searched the newest 0 of 3 articles (time ran out; use more specific words)\n")

    def test_counts_for_named_groups(self):
        self.assertEqual(self.out("counts", "lan.test"), "lan.test\t3\t3\n")

    def test_subscribe_unsubscribe_catchup(self):
        self.fake.groups["lan.other"] = {1: tn.A1, 2: tn.A2}
        self.assertIn("subscribed lan.other", self.out_plain("news-subscribe", "lan.other"))
        self.assertEqual([r[0] for r in self.rows("folders")], ["lan.dense", "lan.empty", "lan.other", "lan.test"])
        self.assertIn("caught up", self.out_plain("news-catchup", "lan.other"))
        self.assertEqual(self.rows("folders")[2][2:], ["2", "0"])
        self.assertIn("unsubscribed", self.out_plain("news-unsubscribe", "lan.empty"))
        self.assertEqual([r[0] for r in self.rows("folders")], ["lan.dense", "lan.other", "lan.test"])
        p = self.be("news-subscribe", "no.such.group")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b"no such group", p.stderr)
        self.assertNotIn("no.such.group", self.newsrc.read_text())

    def out_plain(self, *args):
        p = self.be(*args)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.decode("utf-8")

    def test_all_groups_marks_the_ones_you_have(self):
        self.fake.descriptions = {"lan.test": "Testing things"}
        rows = [l.split("\t") for l in self.out_plain("news-groups").splitlines()]
        self.assertEqual(rows[0], ["lan.dense", "1", "4", "y", "*", ""])
        self.assertEqual(rows[1], ["lan.empty", "1", "0", "y", "*", ""])
        self.assertEqual(rows[2], ["lan.test", "1", "5", "y", "*", "Testing things"])
        self.fake.groups["lan.new"] = {}
        self.assertEqual(self.out_plain("news-groups").splitlines()[2].split("\t")[:5], ["lan.new", "1", "0", "y", ""])   # not yet subscribed

    def test_verbs_a_newsgroup_does_not_have(self):
        for verb in (("delete", "1", "--mbox", "lan.test"), ("purge", "lan.test", "--from", "x"), ("empty-trash",)):
            p = self.news(*verb)
            self.assertNotEqual(p.returncode, 0, verb)
            self.assertIn(b"not available for the Usenet service", p.stderr)

    def test_a_server_that_is_down_is_an_error_not_an_empty_list(self):
        self.write_conf()
        self.conf.write_text(self.conf.read_text().replace("port = %d" % self.fake.port, "port = 1"))
        p = self.news("folders")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b"news server", p.stderr)


class TestNewsPosting(NewsBase):
    def test_a_followup_template(self):
        t = self.out("compose-template", "--in-reply-to", "2", "lan.test")
        head, _, body = t.partition("\n\n")
        h = dict(l.split(": ", 1) for l in head.splitlines())
        self.assertEqual(h["Newsgroups"], "lan.test")
        self.assertEqual(h["Subject"], "Re: Hello")                         # not "Re: Re: Hello"
        self.assertEqual(h["References"], "<a1@x> <a2@x>")
        self.assertIn("In article <a2@x>, Bob wrote:", body)
        self.assertIn("> second body", body)

    def test_followup_to_decides_the_groups_and_poster_means_mail(self):
        a = tn.article("dan@x", "Mon, 01 Sep 2026 12:00:00 +0000", "Question", "q", "<a4@x>", groups="lan.test,lan.other")
        a = a.replace(b"Subject:", b"Followup-To: lan.other\nSubject:")
        self.fake.groups["lan.test"][7] = a
        h = self.out("compose-template", "--in-reply-to", "7", "lan.test").split("\n\n")[0]
        self.assertIn("Newsgroups: lan.other", h)

    def test_a_new_article_template(self):
        self.assertEqual(self.out("compose-template", "lan.test"), "Newsgroups: lan.test\nSubject: \n\n")
        p = self.news("compose-template", "--forward", "1", "lan.test")
        self.assertNotEqual(p.returncode, 0)

    def test_send_posts_with_the_headers_a_news_server_wants(self):
        self.write_conf()
        self.conf.write_text(self.conf.read_text() + "[smtp]\nfrom = Chris <chris@cpbee.cmnet>\n")
        msg = (b"X-Mailer: tvmail 1.3.0\nNewsgroups: lan.test\nFollowup-To: \nDistribution: lan\n"
               b"References: <a1@x>\nSubject: Re: Hello\n\nBody \xc3\xa9\n.dotted\n")
        p = self.news("send", stdin=msg)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout, b"sent\n")
        import email
        got = email.message_from_bytes(self.fake.posted[0])
        self.assertEqual(got["Newsgroups"], "lan.test")
        self.assertEqual(got["From"], "Chris <chris@cpbee.cmnet>")
        self.assertEqual(got["Distribution"], "lan")
        self.assertIsNone(got["Followup-To"])                                # an empty header is not sent
        self.assertTrue(got["Message-ID"].endswith("@cpbee.cmnet>"))
        self.assertTrue(got["Date"])
        self.assertEqual(got["X-Mailer"], "tvmail 1.3.0")                    # the caller named itself first
        self.assertEqual(got["References"], "<a1@x>")
        self.assertEqual(got["Content-Type"], "text/plain; charset=utf-8")
        self.assertIn(b"\n.dotted\n", self.fake.posted[0])                   # dot-stuffing did not leak
        self.assertEqual(sorted(self.fake.groups["lan.test"])[-1], 6)       # the server now has it

    def test_send_refusals(self):
        p = self.news("send", stdin=b"Subject: s\n\nbody\n")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b"no newsgroup", p.stderr)
        self.fake.refuse_post = "no way"
        p = self.news("send", stdin=b"Newsgroups: lan.test\nSubject: s\n\nbody\n")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b"no way", p.stderr)
        self.fake.refuse_post = ""
        self.fake.readonly = True
        p = self.news("send", stdin=b"Newsgroups: lan.test\nSubject: s\n\nbody\n")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b"does not allow posting", p.stderr)


class TestNewsServe(NewsBase):
    def setUp(self):
        super().setUp()
        env = dict(os.environ)
        env["PATH"] = str(BACKEND.parent) + os.pathsep + env.get("PATH", "")
        self.p = subprocess.Popen([sys.executable, str(BACKEND), "serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, env=env)
        self.addCleanup(self._reap)
        self.frame()

    def _reap(self):
        try:
            self.p.stdin.write(b"quit\n")
            self.p.stdin.flush()
            self.p.wait(timeout=3)
        except Exception:
            self.p.kill()
        self.p.stdin.close()
        self.p.stdout.close()

    def frame(self):
        st, n = (int(x) for x in self.p.stdout.readline().split())
        return st, self.p.stdout.read(n)

    def req(self, line):
        self.p.stdin.write(line.encode() + b"\n")
        self.p.stdin.flush()
        return self.frame()

    def test_one_connection_serves_every_request(self):
        for _ in range(3):
            st, body = self.req("--service news list lan.test")
            self.assertEqual(st, 0)
            self.assertEqual(len(body.decode().splitlines()), 3)
        self.assertEqual(self.req("--service news folders")[0], 0)
        self.assertEqual(self.fake.connections, 1)

    def test_a_dropped_connection_is_opened_again(self):
        self.req("--service news list lan.test")
        self.fake.drop_after = len(self.fake.log) + 1
        st, body = self.req("--service news list lan.test")
        self.assertEqual(st, 0, body)
        self.assertEqual(len(body.decode().splitlines()), 3)
        self.assertEqual(self.fake.connections, 2)

    def test_mail_and_news_in_one_process(self):
        st, body = self.req("list " + str(self.spool))
        self.assertEqual(st, 0)
        self.assertEqual(len(body.decode().splitlines()), 2)                # the mbox: mail is still the default
        self.assertEqual(self.req("--service news show 1 lan.test")[0], 0)


if __name__ == "__main__":
    unittest.main()
