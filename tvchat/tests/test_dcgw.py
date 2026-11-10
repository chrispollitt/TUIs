#!/usr/bin/env python3
"""Tests for tvchat/backend/dcgw, the Delta Chat gateway, against fake_deltachat_rpc.py (a stand-in for the core).

    python3 -m unittest tests.test_dcgw
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import socket
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
FAKE = HERE / "fake_deltachat_rpc.py"
BACKEND = HERE.parent / "backend" / "dcgw"


def load():
    loader = importlib.machinery.SourceFileLoader("dcgw", str(BACKEND))
    spec = importlib.util.spec_from_loader("dcgw", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


dcgw = load()
WORLD = {"contacts": {"20": {"address": "alice@example.org", "displayName": "Alice"}},
         "chats": {"10": {"id": 10, "name": "Alice", "chatType": "Single", "contacts": [20]}},
         "messages": {}}


class Conn(object):
    """A client of the gateway, speaking JSON-RPC lines."""

    def __init__(self, port, token, expect="OK"):
        self.sock = socket.create_connection(("127.0.0.1", port), 5)
        self.sock.settimeout(8)
        self.f = self.sock.makefile("rb")
        self.sock.sendall(("DCGW 1 %s\n" % token).encode())
        self.hello = self.f.readline().decode().strip()
        self.n = 0

    def send(self, method, *params, rid=None):
        self.n += 1
        rid = rid if rid is not None else self.n
        self.sock.sendall((json.dumps({"jsonrpc": "2.0", "method": method, "params": list(params), "id": rid}) + "\n").encode())
        return rid

    def read(self):
        raw = self.f.readline()
        return json.loads(raw.decode()) if raw else None

    def rpc(self, method, *params):
        rid = self.send(method, *params)
        while True:
            m = self.read()
            self.assertion = m
            if m is None or m.get("id") == rid:
                return m

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class GatewayCase(unittest.TestCase):
    def setUp(self):
        if not os.access(str(FAKE), os.X_OK):
            self.skipTest("fake_deltachat_rpc.py is not executable")
        self.tmp = Path(tempfile.mkdtemp(prefix="dcgw-test-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.data = self.tmp / "data"
        self.conf = self.tmp / "dcgw.conf"
        self.make_users({"chris": ("chris@cpbee.cmnet", "tok-chris"), "michelle": ("michelle@cpbee.cmnet", "tok-michelle")})
        for name, who in (("chris", "Alice"), ("michelle", "Bob")):
            d = self.data / name
            d.mkdir(parents=True)
            w = json.loads(json.dumps(WORLD))
            w["chats"]["10"]["name"] = who + " (%s's chat)" % name
            (d / "world.json").write_text(json.dumps(w))
            (d / "state.json").write_text(json.dumps({"accounts": [1], "configured": True}))
        self.gw = dcgw.Gateway(str(self.conf))
        self.gw.serve()
        self.addCleanup(self.gw.stop)
        self.port = self.gw.servers[0].server_address[1]
        self.conns = []

    def make_users(self, users):
        lines = ["[dcgw]", "listen = 127.0.0.1:0", "core = %s" % FAKE, "data = %s" % self.data, ""]
        for name, (addr, tok) in users.items():
            lines += ["[user:%s]" % name, "addr = %s" % addr, "token = %s" % tok, ""]
        self.conf.write_text("\n".join(lines))

    def conn(self, token="tok-chris"):  # allowlist secret: test value
        c = Conn(self.port, token)
        self.conns.append(c)
        self.addCleanup(c.close)
        return c

    def requests(self, name="chris"):
        try:
            return [json.loads(l) for l in (self.data / name / "requests.jsonl").read_text().splitlines()]
        except OSError:
            return []

    def order(self, name, **obj):
        with open(str(self.data / name / "events.jsonl"), "a") as fh:
            fh.write(json.dumps(obj) + "\n")


class TestAccess(GatewayCase):
    def test_a_token_is_needed_and_names_the_person(self):
        self.assertEqual(self.conn().hello, "OK 1 chris chris@cpbee.cmnet")
        self.assertEqual(self.conn("tok-michelle").hello, "OK 1 michelle michelle@cpbee.cmnet")
        bad = Conn(self.port, "not-a-token")
        self.assertEqual(bad.hello, "ERR not accepted")
        self.assertEqual(Conn(self.port, "").hello, "ERR not accepted")

    def test_each_person_sees_only_their_own_account(self):
        a, b = self.conn("tok-chris"), self.conn("tok-michelle")
        self.assertEqual(a.rpc("get_all_account_ids")["result"], [1])
        names = lambda c: [x["name"] for x in c.rpc("get_chatlist_items_by_entries", 1, [10])["result"].values()]
        self.assertEqual(names(a), ["Alice (chris's chat)"])
        self.assertEqual(names(b), ["Bob (michelle's chat)"])

    def test_only_the_allowed_calls_reach_the_core(self):
        c = self.conn()
        forbidden = [("add_or_update_transport", 1, {"addr": "x", "password": "y"}), ("provide_backup", 1),
                     ("get_backup", 1, "DCBACKUP2:x"), ("export_backup", 1, "/tmp/x", None), ("import_backup", 1, "/x", None),
                     ("add_account",), ("remove_account", 1), ("export_self_keys", 1, "/tmp", None),
                     ("get_config", 1, "mail_pw"), ("set_config", 1, "mail_pw", "x"), ("set_config", 1, "addr", "x"),
                     ("get_message_ids", 2, 10, False, False), ("is_configured", 99), ("anything_else",)]
        for call in forbidden:
            r = c.rpc(*call)
            self.assertIn("error", r, call)
        sent = [r["method"] for r in self.requests()]
        for m in ("add_or_update_transport", "provide_backup", "get_backup", "export_backup", "import_backup",
                  "remove_account", "export_self_keys", "anything_else"):
            self.assertNotIn(m, sent)
        self.assertNotIn("mail_pw", json.dumps(self.requests()))
        self.assertEqual(c.rpc("get_config", 1, "displayname")["result"], "")          # a safe setting works
        self.assertEqual(c.rpc("set_config", 1, "bcc_self", "1")["result"], None)

    def test_stopping_the_io_is_not_passed_on_and_the_core_keeps_running(self):
        c = self.conn()
        self.assertIsNone(c.rpc("stop_io_for_all_accounts")["result"])
        self.assertIsNone(c.rpc("stop_io", 1)["result"])
        sent = [r["method"] for r in self.requests()]
        self.assertNotIn("stop_io", sent)
        self.assertNotIn("stop_io_for_all_accounts", sent)

    def test_two_clients_using_the_same_request_numbers_get_their_own_answers(self):
        a, b = self.conn(), self.conn()
        ra = a.send("get_chat_securejoin_qr_code", 1, None, rid=7)
        rb = b.send("get_system_info", rid=7)
        ma, mb = a.read(), b.read()
        self.assertEqual((ma["id"], ma["result"]), (7, "https://i.delta.chat/#FAKEINVITE"))
        self.assertEqual(mb["id"], 7)
        self.assertEqual(mb["result"]["deltachat_core_version"], "fake-1")


class TestEvents(GatewayCase):
    def test_every_client_of_a_person_gets_every_event_and_nobody_else_does(self):
        a, b, other = self.conn(), self.conn(), self.conn("tok-michelle")
        for c in (a, b, other):
            c.send("get_next_event")
        self.order("chris", contextId=1, event={"kind": "IncomingMsg", "chatId": 10, "msgId": 5})
        ea, eb = a.read(), b.read()
        self.assertEqual(ea["result"]["event"]["msgId"], 5)
        self.assertEqual(eb["result"]["event"]["msgId"], 5)
        other.sock.settimeout(0.7)
        with self.assertRaises(socket.timeout):
            other.read()                                          # michelle's client heard nothing of chris's
        # a second event waits for the next request; a queued event is answered at once
        self.order("chris", contextId=1, event={"kind": "MsgsChanged", "chatId": 10, "msgId": 6})
        time.sleep(0.4)
        a.send("get_next_event")
        self.assertEqual(a.read()["result"]["event"]["msgId"], 6)

    def test_a_message_sent_by_one_client_is_seen_by_the_other(self):
        a, b = self.conn(), self.conn()
        b.send("get_next_event")
        r = a.rpc("misc_send_text_message", 1, 10, "hello from a")
        self.assertIsInstance(r["result"], int)
        ev = b.read()["result"]["event"]
        self.assertEqual((ev["kind"], ev["chatId"]), ("MsgsChanged", 10))


class TestLifecycle(GatewayCase):
    def test_a_dead_core_drops_its_clients_and_the_next_connection_restarts_it(self):
        c = self.conn()
        self.assertEqual(c.rpc("get_system_info")["result"]["deltachat_core_version"], "fake-1")
        self.order("chris", exit=3)
        deadline = time.time() + 8
        gone = False
        while time.time() < deadline:
            try:
                c.sock.settimeout(1)
                if c.read() is None:
                    gone = True
                    break
            except socket.timeout:
                continue
            except OSError:
                gone = True
                break
        self.assertTrue(gone, "the client was not dropped when the core exited")
        again = Conn(self.port, "tok-chris")
        self.conns.append(again)
        self.assertEqual(again.hello.split()[0], "OK")
        self.assertEqual(again.rpc("get_system_info")["result"]["deltachat_core_version"], "fake-1")

    def test_a_person_without_a_configured_account_is_told_so_by_the_core(self):
        (self.data / "chris" / "state.json").write_text(json.dumps({"accounts": [1], "configured": False}))
        self.gw.stop()
        self.gw = dcgw.Gateway(str(self.conf))
        self.gw.serve()
        self.addCleanup(self.gw.stop)
        self.port = self.gw.servers[0].server_address[1]
        c = self.conn()
        self.assertFalse(c.rpc("is_configured", 1)["result"])


class TestAdmin(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dcgw-admin-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.conf = str(self.tmp / "dcgw.conf")

    def run_cli(self, *argv):
        import contextlib
        import io
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = dcgw.main(["--conf", self.conf] + list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_add_user_makes_a_private_token_once_and_token_shows_it(self):
        rc, out, _ = self.run_cli("add-user", "chris", "--addr", "chris@cpbee.cmnet")
        self.assertEqual(rc, 0)
        token = out.split("token: ")[1].strip()
        self.assertEqual(len(token), 32)
        self.assertEqual(self.run_cli("token", "chris")[1].strip(), token)
        self.assertIn("exists", self.run_cli("add-user", "chris", "--addr", "x@y.z")[1])
        self.assertEqual(self.run_cli("token", "chris")[1].strip(), token)           # unchanged
        self.assertNotEqual(self.run_cli("add-user", "michelle", "--addr", "m@cpbee.cmnet")[1].split("token: ")[1].strip(), token)
        self.assertEqual(self.run_cli("token", "nobody")[0], 1)
        if os.name != "nt":
            self.assertEqual(os.stat(self.conf).st_mode & 0o077, 0)                   # the file with tokens is private

    def test_adopt_takes_an_existing_account_folder_and_refuses_to_overwrite(self):
        self.run_cli("add-user", "chris", "--addr", "chris@cpbee.cmnet")
        src = self.tmp / "old" / "uuid"
        src.mkdir(parents=True)
        (src / "dc.db").write_text("db")
        (self.tmp / "old" / "accounts.toml").write_text("x")
        c = dcgw.configparser.ConfigParser(interpolation=None)
        c.read(self.conf)
        c.set("dcgw", "data", str(self.tmp / "data"))
        dcgw._write_conf(self.conf, c)
        rc, out, _ = self.run_cli("adopt", "chris", str(self.tmp / "old"))
        self.assertEqual(rc, 0, out)
        self.assertTrue((self.tmp / "data" / "chris" / "uuid" / "dc.db").exists())
        self.assertEqual(self.run_cli("adopt", "chris", str(self.tmp / "old"))[0], 1)       # not overwritten
        self.assertEqual(self.run_cli("adopt", "nobody", str(self.tmp / "old"))[0], 1)
        self.assertIn("chris", self.run_cli("list")[1])
        self.assertIn("has data", self.run_cli("list")[1])


class FakeImap(object):
    """Just enough IMAP for sweep_chat_mail: INBOX holds uid -> (flags, internaldate epoch); `chatty` says which
    of them match the chat search; COPY/STORE/UID EXPUNGE act on it."""
    def __init__(self, inbox, chatty, caps=("UIDPLUS",)):
        self.inbox, self.chatty, self.after_login = inbox, chatty, caps
        self.capabilities = ("IMAP4REV1",)      # before login: no UIDPLUS
        self.folders, self.deleted, self.log = {}, set(), []

    def capability(self):                 # after login, as Dovecot: the answer has what m.capabilities (pre-login) lacks;
        return "OK", [" ".join(("IMAP4rev1",) + tuple(self.after_login)).encode()]   # and, like imaplib, it does not update it

    def select(self, name):
        return "OK", [b"1"]

    def uid(self, cmd, *a):
        self.log.append((cmd,) + a)
        if cmd == "SEARCH":
            want_seen = "SEEN" in a
            u = [str(k) for k, (fl, _) in sorted(self.inbox.items())
                 if k in self.chatty and (not want_seen or "\\Seen" in fl)]
            return "OK", [" ".join(u).encode()]
        if cmd == "FETCH":
            import time
            out = []
            for k in a[0].split(","):
                t = time.strftime("%d-%b-%Y %H:%M:%S +0000", time.gmtime(self.inbox[int(k)][1]))
                out.append(('%s (UID %s INTERNALDATE "%s")' % (k, k, t)).encode())
            return "OK", out
        if cmd == "COPY":
            self.folders.setdefault(a[1], []).append(int(a[0]))
            return "OK", [b""]
        if cmd == "STORE":
            self.deleted |= set(int(x) for x in a[0].split(","))
            return "OK", [b""]
        if cmd == "EXPUNGE":
            for x in a[0].split(","):
                if int(x) in self.deleted:
                    self.inbox.pop(int(x), None)
            return "OK", [b""]
        raise AssertionError(cmd)

    def create(self, name):
        return "OK", [b""]

    def subscribe(self, name):
        return "OK", [b""]

    def logout(self):
        pass


class TestSweep(unittest.TestCase):
    NOW = 1_800_000_000

    def run_sweep(self, inbox, chatty, **kw):
        f = FakeImap(inbox, chatty, **kw)
        n = dcgw.sweep_chat_mail({}, "ChatMail", min_age=300, connect=lambda tp: f, now=self.NOW)
        return n, f

    def test_old_seen_chat_mail_moves_and_nothing_else_does(self):
        inbox = {1: ("\\Seen", self.NOW - 3600),    # chat, read, old: moves
                 2: ("", self.NOW - 3600),            # chat but unread: the core has not had it - stays
                 3: ("\\Seen", self.NOW - 10),      # chat, read, brand new: stays a while
                 4: ("\\Seen", self.NOW - 3600)}    # ordinary mail (not chatty): stays
        n, f = self.run_sweep(inbox, {1, 2, 3})
        self.assertEqual(n, 1)
        self.assertEqual(f.folders, {"ChatMail": [1]})
        self.assertEqual(sorted(f.inbox), [2, 3, 4])

    def test_the_search_is_for_seen_chat_mail_in_both_shapes(self):
        n, f = self.run_sweep({1: ("\\Seen", 0)}, {1})
        search = [x for x in f.log if x[0] == "SEARCH"][0]
        self.assertIn("SEEN", search)
        self.assertIn("Chat-Version", search)
        self.assertTrue(any("multipart/encrypted" in str(x) and "hidden-recipients" in str(x) for x in search))

    def test_without_uidplus_nothing_is_touched(self):
        n, f = self.run_sweep({1: ("\\Seen", 0)}, {1}, caps=())
        self.assertEqual((n, f.log), (0, []))


class FakeInviteImap(object):
    """INBOX holds uid -> (raw message bytes); only messages with the invitation header answer the SEARCH."""
    def __init__(self, inbox):
        self.inbox, self.deleted, self.log = inbox, set(), []

    def select(self, name):
        return "OK", [b"1"]

    def uid(self, cmd, *a):
        self.log.append((cmd,) + a)
        if cmd == "SEARCH":
            a = a[1:] if a and a[0] is None else a            # the charset slot
            u = [k for k, raw in sorted(self.inbox.items()) if dcgw.INVITE_HEADER.encode() in raw and
                 (a[0] != "UID" and "UNDELETED" in a or a[0] == "UID" and str(k) == a[1])]
            return "OK", [" ".join(str(k) for k in u).encode()]
        if cmd == "FETCH":
            return "OK", [(b"1 (UID 1 BODY[] {1}", self.inbox[int(a[0])]), b")"]
        if cmd == "STORE":
            self.deleted.add(int(a[0]))
            return "OK", [b""]
        raise AssertionError(cmd)

    def expunge(self):
        for k in self.deleted:
            self.inbox.pop(k, None)
        return "OK", [b""]

    def logout(self):
        pass


def invite_mail(sender, link, header=True):
    return ("From: %s\nTo: me@cpbee.cmnet\nSubject: Chat invitation from %s\n%sContent-Type: text/plain\n\n"
            "Open this link:\n\n%s\n" % (sender, sender, ("%s: 1\n" % dcgw.INVITE_HEADER) if header else "", link)).encode()


class TestInviteMail(unittest.TestCase):
    LINK = "https://i.delta.chat/#ABC123&v=3&a=carol%40example.org&n=Carol"

    def test_only_mail_with_the_invitation_header_is_listed(self):
        f = FakeInviteImap({5: invite_mail("Carol <carol@example.org>", self.LINK),
                            6: invite_mail("dave@example.org", self.LINK, header=False)})
        self.assertEqual(dcgw.scan_invite_mail({}, lambda tp: f), [["5", "carol@example.org", self.LINK]])

    def test_a_mail_without_a_link_is_listed_with_an_empty_link(self):
        f = FakeInviteImap({7: invite_mail("eve@example.org", "no link here")})
        self.assertEqual(dcgw.scan_invite_mail({}, lambda tp: f), [["7", "eve@example.org", ""]])

    def test_dropping_removes_only_invitation_mail_and_only_numeric_uids(self):
        f = FakeInviteImap({5: invite_mail("carol@example.org", self.LINK), 6: invite_mail("x@y.z", "q", header=False)})
        dcgw.drop_invite_mail({}, ["5", "6", "5;rm", "../x"], lambda tp: f)
        self.assertEqual(sorted(f.inbox), [6])          # 6 has no header: not ours to delete


class TestInvitesThroughTheGateway(GatewayCase):
    LINK = TestInviteMail.LINK

    def test_clients_ask_the_gateway_for_the_invitations_and_answer_them(self):
        core = self.gw.core_for("chris")
        core.invites = [["5", "carol@example.org", self.LINK]]
        a, b = self.conn("tok-chris"), self.conn("tok-michelle")
        self.assertEqual(a.rpc("dcgw_invites", 1)["result"], [["5", "carol@example.org", self.LINK]])
        self.assertEqual(b.rpc("dcgw_invites", 1)["result"], [])             # michelle has her own list
        self.assertEqual(a.rpc("dcgw_invites", 2)["error"]["message"], "that is not your account")
        self.assertEqual(a.rpc("dcgw_drop_invites", 1, ["5"])["result"], True)
        self.assertEqual(a.rpc("dcgw_invites", 1)["result"], [])
        self.assertIn("5", core.dropped)
        # once answered it is not offered again, even if the mail is still there on the next look
        core.invites = [i for i in [["5", "carol@example.org", self.LINK]] if i[0] not in core.dropped]
        self.assertEqual(core.invites, [])


class TestCommandLine(unittest.TestCase):
    def test_conf_is_accepted_before_and_after_the_subcommand(self):
        """The systemd unit says `dcgw serve --conf FILE`; an admin may say `dcgw --conf FILE list`."""
        import contextlib
        import io
        tmp = Path(tempfile.mkdtemp(prefix="dcgw-cli-"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        conf = str(tmp / "dcgw.conf")
        for argv in (["--conf", conf, "add-user", "a", "--addr", "a@x.y"], ["add-user", "b", "--addr", "b@x.y", "--conf", conf],
                     ["list", "--conf", conf], ["--conf", conf, "list"]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(dcgw.main(argv), 0, argv)
        text = Path(conf).read_text()
        self.assertIn("[user:a]", text)
        self.assertIn("[user:b]", text)
        # `serve` parses too (the unit's exact form): only the parsing is checked, so stop at a conf with no users
        err = io.StringIO()
        empty = str(tmp / "empty.conf")
        with contextlib.redirect_stderr(err):
            self.assertEqual(dcgw.main(["serve", "--conf", empty]), 1)
        self.assertIn("has no [user:NAME]", err.getvalue())


if __name__ == "__main__":
    unittest.main()
