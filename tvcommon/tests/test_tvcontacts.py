#!/usr/bin/env python3
"""Tests for tvcommon/py/tvcontacts.py against a small fake CardDAV server.

    python3 -m unittest tvcommon.tests.test_tvcontacts      (or: python3 tvcommon/tests/test_tvcontacts.py)
"""
import base64
import http.server
import importlib.util
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("tvcontacts", str(HERE.parent / "py" / "tvcontacts.py"))
tc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tc)

ALICE = ("BEGIN:VCARD\r\nVERSION:3.0\r\nUID:u-alice\r\nFN:Alice Archer\r\nN:Archer;Alice;;;\r\n"
         "EMAIL;TYPE=INTERNET:alice@example.org\r\nNICKNAME:Al,Ally\r\nX-CUSTOM;FOO=1:keep me\r\nEND:VCARD\r\n")
BOB = ("BEGIN:VCARD\r\nVERSION:3.0\r\nUID:u-bob\r\nFN:Bob Baker\r\nitem1.EMAIL;TYPE=work:bob@example.org\r\n"
       "EMAIL:bobby@home.example\r\nEND:VCARD\r\n")
FAMILY = ("BEGIN:VCARD\r\nVERSION:3.0\r\nUID:u-family\r\nFN:Family\r\nKIND:group\r\nMEMBER:urn:uuid:u-alice\r\n"
          "MEMBER:urn:uuid:u-bob\r\nMEMBER:urn:uuid:u-nobody\r\nEND:VCARD\r\n")
ALL = ("BEGIN:VCARD\r\nVERSION:3.0\r\nUID:u-all\r\nFN:Everyone\r\nKIND:group\r\nMEMBER:urn:uuid:u-family\r\n"
       "MEMBER:urn:uuid:u-alice\r\nEND:VCARD\r\n")


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _auth(self):
        want = "Basic " + base64.b64encode(b"chris:secret").decode()
        if self.headers.get("Authorization") != want:
            self.send_response(401)
            self.end_headers()
            return False
        return True

    def do_PROPFIND(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))      # the request body, read: no reset on close
        if not self._auth():
            return
        srv = self.server
        srv.log.append("PROPFIND")
        rows = '<response><href>/c/</href><propstat><prop><resourcetype/></prop></propstat></response>'
        for name, (text, etag) in sorted(srv.cards.items()):
            rows += '<response><href>/c/%s</href><propstat><prop><getetag>"%s"</getetag></prop></propstat></response>' % (name, etag)
        body = ('<?xml version="1.0"?><multistatus xmlns="DAV:">%s</multistatus>' % rows).encode()
        self.send_response(207)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._auth():
            return
        name = self.path.rsplit("/", 1)[-1]
        self.server.log.append("GET " + name)
        if name not in self.server.cards:
            self.send_response(404)
            self.end_headers()
            return
        body = self.server.cards[name][0].encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", '"%s"' % self.server.cards[name][1])
        self.end_headers()
        self.wfile.write(body)


    def _body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_PUT(self):
        body = self._body()
        if not self._auth():
            return
        srv = self.server
        name = self.path.rsplit("/", 1)[-1]
        srv.log.append("PUT " + name)
        have = srv.cards.get(name)
        if self.headers.get("If-None-Match") == "*" and have is not None:
            self.send_response(412)
            self.end_headers()
            return
        im = self.headers.get("If-Match")
        if im is not None and (have is None or '"%s"' % have[1] != im):
            self.send_response(412)
            self.end_headers()
            return
        etag = str(int(have[1]) + 1) if have else "1"
        srv.cards[name] = (body.decode(), etag)
        self.send_response(204 if have else 201)
        self.send_header("ETag", '"%s"' % etag)
        self.end_headers()

    def do_DELETE(self):
        self._body()
        if not self._auth():
            return
        srv = self.server
        name = self.path.rsplit("/", 1)[-1]
        srv.log.append("DELETE " + name)
        have = srv.cards.get(name)
        im = self.headers.get("If-Match")
        if have is not None and im is not None and '"%s"' % have[1] != im:
            self.send_response(412)
            self.end_headers()
            return
        srv.cards.pop(name, None)
        self.send_response(204 if have else 404)
        self.end_headers()

    def do_MKCOL(self):
        self._body()
        if not self._auth():
            return
        self.server.log.append("MKCOL")
        self.send_response(201)
        self.end_headers()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tvcontacts-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        self.srv.cards = {"alice.vcf": (ALICE, "1"), "bob.vcf": (BOB, "1"), "family.vcf": (FAMILY, "1"), "all.vcf": (ALL, "1")}
        self.srv.log = []
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.url = "http://127.0.0.1:%d/c/" % self.srv.server_address[1]

    def book(self, **kw):
        kw.setdefault("password", "secret")
        return tc.Book(self.url, "chris", cache=str(self.tmp / "cache"), **kw)


class TestCard(unittest.TestCase):
    def test_a_card_is_read_and_unknown_properties_survive_a_round_trip(self):
        c = tc.Card.parse(ALICE)
        self.assertEqual((c.uid, c.name, c.emails, c.nicknames), ("u-alice", "Alice Archer", ["alice@example.org"], ["Al", "Ally"]))
        again = tc.Card.parse(c.text())
        self.assertIn(["X-CUSTOM", "FOO=1", "keep me"], again.props)
        self.assertEqual(again.emails, ["alice@example.org"])

    def test_apple_style_prefixes_and_a_name_without_fn(self):
        c = tc.Card.parse(BOB)
        self.assertEqual(c.emails, ["bob@example.org", "bobby@home.example"])
        c = tc.Card.parse("BEGIN:VCARD\r\nN:Carter;Carol;;;\r\nEMAIL:c@x.y\r\nEND:VCARD\r\n")
        self.assertEqual(c.name, "Carol Carter")

    def test_folded_lines_escapes_and_long_utf8_values(self):
        c = tc.Card.new("Zoë, \"Z\"; Q", ["z@example.org"])
        c.set_values("NOTE", ["line one\nline two, with ééé " + "x" * 200])
        text = c.text()
        self.assertTrue(all(len(l.encode("utf-8")) <= 75 for l in text.split("\r\n")))
        back = tc.Card.parse(text)
        self.assertEqual(back.name, "Zoë, \"Z\"; Q")
        self.assertTrue(back.get("NOTE").startswith("line one\nline two, with ééé x"))
        self.assertEqual(len(back.get("NOTE")), len("line one\nline two, with ééé ") + 200)

    def test_a_group_lists_its_members_by_uid(self):
        g = tc.Card.parse(FAMILY)
        self.assertTrue(g.is_group)
        self.assertEqual(g.members, ["u-alice", "u-bob", "u-nobody"])
        self.assertTrue(tc.Card.new("G", group_members=["a", "b"]).is_group)

    def test_garbage_is_refused(self):
        with self.assertRaises(tc.ContactsError):
            tc.Card.parse("hello")


class TestBook(Base):
    def test_sync_fetches_everything_once_then_only_what_changed(self):
        b = self.book()
        self.assertTrue(b.sync())
        self.assertEqual(sorted(x.name for x in b.contacts()), ["Alice Archer", "Bob Baker"])
        self.assertEqual(sorted(g.name for g in b.groups()), ["Everyone", "Family"])
        self.assertEqual(len([l for l in self.srv.log if l.startswith("GET")]), 4)
        self.srv.log.clear()
        self.assertTrue(b.sync())                                       # nothing changed: a listing, no GET
        self.assertEqual(self.srv.log, ["PROPFIND"])
        self.srv.cards["bob.vcf"] = (BOB.replace("Bob Baker", "Robert Baker"), "2")
        self.srv.cards.pop("all.vcf")
        self.srv.log.clear()
        b.sync()
        self.assertEqual(self.srv.log, ["PROPFIND", "GET bob.vcf"])
        self.assertEqual(sorted(x.name for x in b.contacts()), ["Alice Archer", "Robert Baker"])
        self.assertEqual(sorted(g.name for g in b.groups()), ["Family"])

    def test_the_copy_serves_a_new_start_and_an_unreachable_server(self):
        self.book().sync()
        self.srv.shutdown()
        self.srv.server_close()
        b = self.book(timeout=2)                                         # a fresh object: reads the copy at once
        self.assertEqual(len(b.contacts()), 2)
        self.assertFalse(b.sync())
        self.assertTrue(b.offline)
        self.assertEqual(len(b.contacts()), 2)

    def test_complete_by_name_nickname_and_address_and_groups_expand(self):
        b = self.book()
        b.sync()
        self.assertEqual(b.complete("ali"), [("Alice Archer", "alice@example.org")])
        self.assertEqual(b.complete("ally"), [("Alice Archer", "alice@example.org")])
        self.assertEqual(b.complete("bake"), [("Bob Baker", "bob@example.org")])
        self.assertEqual(b.complete("@home"), [("Bob Baker", "bob@example.org")])
        self.assertEqual(b.complete("fam"), [("Family (group)", "alice@example.org, bob@example.org")])
        self.assertEqual(b.expand("family"), ["alice@example.org", "bob@example.org"])        # u-nobody is skipped
        self.assertEqual(b.expand("Everyone"), ["alice@example.org", "bob@example.org"])      # nested, each address once
        self.assertEqual(b.expand("nosuch"), [])
        self.assertEqual(b.complete(""), [])

    def test_save_a_new_contact_and_a_changed_one_and_delete(self):
        b = self.book()
        b.sync()
        card = tc.Card.new("Carol Carter", ["carol@example.org"])
        b.save(card)
        self.assertEqual(card.href, "/c/%s.vcf" % card.uid)
        self.assertIn("%s.vcf" % card.uid, self.srv.cards)
        self.assertEqual(card.etag, '"1"')
        card.set_values("EMAIL", ["carol@work.example", "carol@example.org"], "TYPE=INTERNET")
        b.save(card)                                                    # If-Match: its ETag
        self.assertEqual(card.etag, '"2"')
        fresh = self.book()                                            # what a new start reads from the copy
        self.assertEqual([c.emails for c in fresh.contacts() if c.name == "Carol Carter"],
                         [["carol@work.example", "carol@example.org"]])
        b.delete(card)
        self.assertNotIn("%s.vcf" % card.uid, self.srv.cards)
        self.assertEqual([c.name for c in self.book().contacts()], ["Alice Archer", "Bob Baker"])

    def test_a_card_changed_by_someone_else_is_refused_not_overwritten(self):
        b = self.book()
        b.sync()
        alice = next(c for c in b.contacts() if c.name == "Alice Archer")
        self.srv.cards["alice.vcf"] = (ALICE.replace("Alice Archer", "Alice Other"), "2")       # somebody else
        alice.set_values("NICKNAME", ["Lissie"])
        with self.assertRaises(tc.ContactsError) as e:
            b.save(alice)
        self.assertIn("changed by someone else", str(e.exception))
        self.assertIn("Alice Other", self.srv.cards["alice.vcf"][0])
        with self.assertRaises(tc.ContactsError):
            b.delete(alice)
        b.sync()                                                         # reload, then it can be changed
        alice = next(c for c in b.contacts() if c.name == "Alice Other")
        alice.set_values("NICKNAME", ["Lissie"])
        b.save(alice)

    def test_writes_are_refused_while_the_server_is_down(self):
        b = self.book(timeout=2)
        b.sync()
        self.srv.shutdown()
        self.srv.server_close()
        with self.assertRaises(tc.ContactsError) as e:
            b.save(tc.Card.new("Dan", ["dan@example.org"]))
        self.assertIn("read-only until it is back", str(e.exception))
        self.assertEqual(len(b.contacts()), 2)                         # reading still works

    def test_make_creates_the_collection(self):
        self.book().make()
        self.assertEqual(self.srv.log, ["MKCOL"])

    def test_import_from_mailrc_makes_contacts_and_groups_once(self):
        rc = self.tmp / "mailrc"
        rc.write_text("alias mum mum@example.org\nalias dad dad@example.org\ngroup parents mum dad\n"
                      "alias both a@x.y b@x.y\nalias Bob bob@elsewhere.example\n")
        b = self.book()
        b.sync()
        out = tc.import_mailrc(b, [str(rc)], dry=True)
        self.assertEqual(len(self.srv.cards), 4)                         # a dry run writes nothing
        self.assertIn("added group parents (2 members)", out)
        out = tc.import_mailrc(b, [str(rc)])
        b.sync()
        self.assertEqual(sorted(g.name for g in b.groups()), ["Everyone", "Family", "both", "parents"])
        self.assertEqual(b.expand("parents"), ["mum@example.org", "dad@example.org"])
        self.assertEqual(sorted(b.expand("both")), ["a@x.y", "b@x.y"])
        again = tc.import_mailrc(b, [str(rc)])
        self.assertTrue(all(l.startswith("kept") for l in again), again)   # once only

    def test_resolve_names_nicknames_groups_and_addresses(self):
        b = self.book()
        b.sync()
        self.assertEqual(b.resolve("Ally, bob@elsewhere.example; Family"),
                         ["alice@example.org", "bob@elsewhere.example", "bob@example.org"])
        self.assertEqual(b.resolve("carol@x.y"), ["carol@x.y"])
        with self.assertRaises(tc.ContactsError) as e:
            b.resolve("Ally, Zed Zoe")
        self.assertIn("not in the address book: Zed Zoe", str(e.exception))

    def test_a_wrong_password_and_a_missing_book_are_said_in_words(self):
        with self.assertRaises(tc.ContactsError) as e:
            self.book(password="wrong").sync()
        self.assertIn("refused the login", str(e.exception))

    def test_the_password_comes_from_mu_tickets_or_netrc_for_this_host_and_user(self):
        home = self.tmp / "home"
        home.mkdir()
        (home / ".mu-tickets").write_text("https://other:nope@cpbee.cmnet\nhttps://chris:fromtickets@cpbee.cmnet\n")  # allowlist secret: test value
        (home / ".netrc").write_text("machine cpbee login chris password fromnetrc\n")  # allowlist secret: test value
        old = {k: os.environ.get(k) for k in ("HOME", "TVMAIL_MU_TICKETS", "TVCONTACTS_PASS")}
        os.environ.pop("TVCONTACTS_PASS", None)
        os.environ["HOME"] = str(home)
        os.environ.pop("TVMAIL_MU_TICKETS", None)
        try:
            self.assertEqual(tc.find_password("https://cpbee.cmnet/radicale/chris/contacts/", "chris"), "fromtickets")
            (home / ".mu-tickets").unlink()
            self.assertEqual(tc.find_password("https://cpbee.cmnet/x/", "chris"), "fromnetrc")
            self.assertEqual(tc.find_password("https://cpbee.cmnet/x/", "michelle"), "")
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class TestCommandLine(Base):
    def run_cli(self, *args):
        import contextlib
        import io
        env = {"TVCONTACTS_URL": self.url, "TVCONTACTS_USER": "chris", "TVCONTACTS_PASS": "secret",    # allowlist secret: test value
               "XDG_STATE_HOME": str(self.tmp / "st")}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = tc.main(list(args))
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return rc, out.getvalue(), err.getvalue()

    def test_list_save_show_delete(self):
        rc, out, _ = self.run_cli("list")
        rows = [l.split("\t") for l in out.splitlines()]
        self.assertEqual([(r[0], r[2]) for r in rows],
                         [("c", "Alice Archer"), ("c", "Bob Baker"), ("g", "Everyone"), ("g", "Family")])
        self.assertEqual(rows[2][4], "Family,Alice Archer")
        rc, uid, _ = self.run_cli("save", "--name", "Eve Evans", "--nicks", "Evie, E",
                                  "--emails", "eve@example.org, eve@work.example")
        self.assertEqual(rc, 0)
        uid = uid.strip()
        rc, out, _ = self.run_cli("show", uid)
        self.assertEqual(out.strip().split("\t")[2:5], ["Eve Evans", "Evie,E", "eve@example.org,eve@work.example"])
        rc, _, _ = self.run_cli("save", "--uid", uid, "--name", "Eve Evans", "--emails", "eve@new.example")
        rc, out, _ = self.run_cli("show", uid)
        self.assertEqual(out.strip().split("\t")[4], "eve@new.example")
        rc, _, err = self.run_cli("save", "--name", "Bad", "--emails", "not-an-address")
        self.assertEqual((rc, "not an email address" in err), (1, True))
        rc, out, _ = self.run_cli("delete", uid)
        self.assertEqual((rc, out.strip()), (0, "deleted Eve Evans"))

    def test_a_group_by_member_names(self):
        rc, uid, _ = self.run_cli("save", "--group", "--name", "Pals", "--members", "Alice Archer, bob@example.org")
        self.assertEqual(rc, 0)
        rc, out, _ = self.run_cli("show", uid.strip())
        self.assertEqual(out.strip().split("\t")[:5], ["g", uid.strip(), "Pals", "", "Alice Archer,Bob Baker"])
        rc, _, err = self.run_cli("save", "--group", "--name", "Nope", "--members", "Zed Zoe")
        self.assertEqual((rc, "not in the address book: Zed Zoe" in err), (1, True))
        rc, out, _ = self.run_cli("complete", "pal")
        self.assertEqual(out.strip(), "Pals (group)\talice@example.org, bob@example.org")

    def test_resolve_on_the_command_line(self):
        rc, out, _ = self.run_cli("resolve", "Alice Archer, Bob Baker")
        self.assertEqual((rc, out.strip()), (0, "alice@example.org, bob@example.org"))
        rc, _, err = self.run_cli("resolve", "Nobody")
        self.assertEqual((rc, "not in the address book: Nobody" in err), (1, True))

    def test_no_book_configured_says_so(self):
        import contextlib
        import io
        os.environ.pop("TVCONTACTS_URL", None)
        home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.tmp / "nohome")
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                rc = tc.main(["list"])
        finally:
            os.environ["HOME"] = home
        self.assertEqual(rc, 2)
        self.assertIn("no address book is configured", err.getvalue())


class TestLibrary(Base):
    """The person's own book and the shared family book, asked as one."""

    GRAN = ("BEGIN:VCARD\r\nVERSION:3.0\r\nUID:u-gran\r\nFN:Grandma Gray\r\nEMAIL:gran@example.org\r\nEND:VCARD\r\n")
    CLASH = ("BEGIN:VCARD\r\nVERSION:3.0\r\nUID:u-alice2\r\nFN:Alice Archer\r\nEMAIL:alice@family.example\r\nEND:VCARD\r\n")

    def setUp(self):
        super().setUp()
        self.srv2 = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        self.srv2.cards = {"gran.vcf": (self.GRAN, "1"), "alice2.vcf": (self.CLASH, "1")}
        self.srv2.log = []
        threading.Thread(target=self.srv2.serve_forever, daemon=True).start()
        self.addCleanup(self.srv2.server_close)
        self.addCleanup(self.srv2.shutdown)
        self.url2 = "http://127.0.0.1:%d/c/" % self.srv2.server_address[1]

    def lib(self):
        mine = tc.Book(self.url, "chris", cache=str(self.tmp / "c1"), password="secret", label="mine")        # allowlist secret: test value
        fam = tc.Book(self.url2, "chris", cache=str(self.tmp / "c2"), password="secret", label="family")     # allowlist secret: test value
        return tc.Library([mine, fam])

    def test_both_books_are_asked_and_the_first_wins_a_clash(self):
        lib = self.lib()
        self.assertTrue(lib.refresh(0))
        self.assertEqual(lib.labels(), ["mine", "family"])
        names = sorted((c.name, c.book) for c in lib.contacts())
        self.assertEqual(names, [("Alice Archer", "family"), ("Alice Archer", "mine"), ("Bob Baker", "mine"), ("Grandma Gray", "family")])
        self.assertEqual(lib.resolve("Alice Archer, Grandma Gray"), ["alice@example.org", "gran@example.org"])   # mine first
        self.assertEqual(lib.complete("gran"), [("Grandma Gray", "gran@example.org")])
        self.assertEqual(len(lib.cards), 6)

    def test_a_new_card_goes_where_it_is_told_and_an_old_one_stays_in_its_book(self):
        lib = self.lib()
        lib.refresh(0)
        a = tc.Card.new("Uncle Ugo", ["ugo@example.org"])
        lib.save(a)                                                        # default: mine
        self.assertEqual(a.book, "mine")
        self.assertTrue(any("Ugo" in v[0] for v in self.srv.cards.values()))
        b = tc.Card.new("Cousin Cy", ["cy@example.org"])
        lib.save(b, "family")
        self.assertEqual(b.book, "family")
        self.assertTrue(any("Cy" in v[0] for v in self.srv2.cards.values()))
        self.assertFalse(any("Cy" in v[0] for v in self.srv.cards.values()))
        gran = next(c for c in lib.contacts() if c.name == "Grandma Gray")
        gran.set_values("NICKNAME", ["Gran"])
        lib.save(gran, "mine")                                             # the label is for NEW cards only
        self.assertIn("NICKNAME:Gran", self.srv2.cards["gran.vcf"][0])
        self.assertFalse(any("Gran" in v[0] for v in self.srv.cards.values()))
        lib.delete(gran)
        self.assertNotIn("gran.vcf", self.srv2.cards)
        with self.assertRaises(tc.ContactsError):
            lib.save(tc.Card.new("X", ["x@y.z"]), "nosuch")

    def test_a_group_in_the_shared_book_gets_its_members_into_that_book(self):
        lib = self.lib()
        lib.refresh(0)
        bob = next(c for c in lib.contacts() if c.name == "Bob Baker" and c.book == "mine")
        g = tc.Card.new("Clan", group_members=[bob.uid])
        lib.save(g, "family")
        self.assertEqual(g.book, "family")
        copies = [k for k, v in self.srv2.cards.items() if "bob@example.org" in v[0]]
        self.assertEqual(len(copies), 1)                                    # copied into the family book
        # somebody who has ONLY the family book can use the group
        only = tc.Library([tc.Book(self.url2, "chris", cache=str(self.tmp / "c3"), password="secret", label="family")])   # allowlist secret: test value
        only.refresh(0)
        self.assertEqual(only.expand("Clan"), ["bob@example.org"])
        # saving it again reuses the copy (a card with that address is there already)
        lib.refresh(0)
        g2 = next(c for c in lib.groups() if c.name == "Clan")
        lib.save(g2)
        self.assertEqual(len([k for k, v in self.srv2.cards.items() if "bob@example.org" in v[0]]), 1)
        # a group in MY book keeps pointing at the cards where they are
        mine = tc.Card.new("Mine", group_members=[bob.uid])
        lib.save(mine)
        self.assertEqual(len([k for k, v in self.srv2.cards.items() if "bob@example.org" in v[0]]), 1)

    def test_the_shared_book_being_down_leaves_my_own(self):
        lib = self.lib()
        lib.refresh(0)
        self.srv2.shutdown()
        self.srv2.server_close()
        lib2 = self.lib()                                                  # a new start: the copies
        self.assertFalse(lib2.refresh(0))
        self.assertTrue(lib2.offline)
        self.assertEqual(len(lib2.contacts()), 4)
        lib.books[1].timeout = 2
        with self.assertRaises(tc.ContactsError):
            lib2.save(tc.Card.new("Z", ["z@example.org"]), "family")

    def test_default_book_reads_the_shared_book_from_the_config_and_set_shared_writes_it(self):
        home = self.tmp / "home"
        (home / ".config" / "tvcal").mkdir(parents=True)
        conf = home / ".config" / "tvcal" / "tvcal.conf"
        conf.write_text("[server]\nurl = https://cpbee:5232/chris/calendar/\nuser = chris\n\n[floyd]\nserver = cpbee\n")
        old = {k: os.environ.get(k) for k in ("HOME", "TVCONTACTS_URL", "TVCONTACTS_SHARED")}
        os.environ["HOME"] = str(home)
        os.environ.pop("TVCONTACTS_URL", None)
        os.environ.pop("TVCONTACTS_SHARED", None)
        try:
            lib = tc.default_book()
            self.assertEqual(lib.labels(), ["mine"])
            self.assertEqual(lib.books[0].url, "https://cpbee:5232/chris/contacts/")
            tc.set_shared("https://cpbee:5232/chris/family/")
            text = conf.read_text()
            self.assertIn("[contacts]\nshared = https://cpbee:5232/chris/family/", text)
            self.assertIn("[floyd]\nserver = cpbee", text)                    # other sections untouched
            self.assertEqual(len(list((conf.parent / "BAK").iterdir())), 1)
            lib = tc.default_book()
            self.assertEqual(lib.labels(), ["mine", "family"])
            self.assertEqual((lib.books[1].url, lib.books[1].user), ("https://cpbee:5232/chris/family/", "chris"))
            tc.set_shared("https://cpbee:5232/chris/kin/")                      # replaced, not doubled
            self.assertEqual(conf.read_text().count("shared ="), 1)
            self.assertIn("/chris/kin/", conf.read_text())
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class TestLibraryCommandLine(Base):
    def run_cli(self, *args):
        os.environ["TVCONTACTS_SHARED"] = self.url2
        self.addCleanup(os.environ.pop, "TVCONTACTS_SHARED", None)
        return TestCommandLine.run_cli(self, *args)

    def setUp(self):
        super().setUp()
        self.srv2 = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        self.srv2.cards = {"gran.vcf": (TestLibrary.GRAN, "1")}
        self.srv2.log = []
        threading.Thread(target=self.srv2.serve_forever, daemon=True).start()
        self.addCleanup(self.srv2.server_close)
        self.addCleanup(self.srv2.shutdown)
        self.url2 = "http://127.0.0.1:%d/c/" % self.srv2.server_address[1]

    # the command line with a shared book present (rows have a 7th field: the book)
    def test_books_list_and_a_new_entry_in_the_family_book(self):
        rc, out, _ = self.run_cli("books")
        self.assertEqual(out.split(), ["mine", "family"])
        rc, out, _ = self.run_cli("list")
        rows = [l.split("\t") for l in out.splitlines()]
        self.assertIn(("Grandma Gray", "family"), [(r[2], r[6]) for r in rows])
        self.assertIn(("Alice Archer", "mine"), [(r[2], r[6]) for r in rows])
        rc, uid, _ = self.run_cli("save", "--name", "Cousin Cy", "--emails", "cy@example.org", "--book", "family")
        self.assertEqual(rc, 0)
        self.assertTrue(any("Cousin Cy" in v[0] for v in self.srv2.cards.values()))
        self.assertFalse(any("Cousin Cy" in v[0] for v in self.srv.cards.values()))
        rc, _, _ = self.run_cli("save", "--uid", uid.strip(), "--name", "Cousin Cy", "--emails", "cy@new.example")   # stays in family
        self.assertTrue(any("cy@new.example" in v[0] for v in self.srv2.cards.values()))
        rc, out, _ = self.run_cli("delete", uid.strip())
        self.assertEqual((rc, out.strip()), (0, "deleted Cousin Cy"))
        rc, _, err = self.run_cli("save", "--name", "N", "--emails", "n@x.y", "--book", "nosuch")
        self.assertEqual((rc, "no address book called nosuch" in err), (1, True))


if __name__ == "__main__":
    unittest.main()
