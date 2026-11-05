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


if __name__ == "__main__":
    unittest.main()
