#!/usr/bin/env python3
"""A stand-in for `deltachat-rpc-server`, for the tests: the same JSON-RPC 2.0
lines on stdin / stdout, the few methods tvchat-backend uses, and a small
world it answers from.  Stdlib only, no network.

The test drives it through the directory it is started in (DC_ACCOUNTS_PATH,
which is where the real core keeps its accounts too):

  world.json     read at start: {"chats": {"10": {name, chatType, contacts,
                 isContactRequest, fresh}}, "contacts": {"20": {address,
                 displayName}}, "messages": {"100": {chatId, fromId, text,
                 timestamp, state, sender, ...}}, "connectivity": 4000}
  events.jsonl   the test appends one JSON object per line; each is either
                 {"contextId": 1, "event": {...}} - handed to the next
                 get_next_event - or {"set_msg": {...}}, {"set_chat": {...}} or
                 {"exit": N} (die with status N), applied in order
  requests.jsonl every request received, appended by this process
  state.json     what survives a restart: accounts, configured

A password of "wrong" makes add_or_update_transport fail.
"""
import json
import os
import sys
import threading
import time

D = os.environ.get("DC_ACCOUNTS_PATH") or "."
LOCK = threading.Lock()
OUT = threading.Lock()


def path(name):
    return os.path.join(D, name)


def load(name, default):
    try:
        with open(path(name)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


STATE = load("state.json", {"accounts": [], "configured": False})
WORLD = load("world.json", {})
WORLD.setdefault("chats", {})
WORLD.setdefault("contacts", {})
WORLD.setdefault("messages", {})
QUEUE = []                               # events waiting for a get_next_event
WAITING = []                             # ids of get_next_event requests


def save_state():
    with open(path("state.json"), "w") as fh:
        json.dump(STATE, fh)


def reply(rid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": rid}
    if error is not None:
        msg["error"] = {"code": -32000, "message": error}
    else:
        msg["result"] = result
    with OUT:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()


def pump():
    """Hand queued events to waiting get_next_event requests."""
    with LOCK:
        while QUEUE and WAITING:
            reply(WAITING.pop(0), QUEUE.pop(0))


def control():
    """Follow events.jsonl."""
    try:                                 # a restarted core does not replay old orders
        pos = os.path.getsize(path("events.jsonl"))
    except OSError:
        pos = 0
    while True:
        try:
            with open(path("events.jsonl"), "rb") as fh:
                fh.seek(pos)
                data = fh.read()
        except OSError:
            data = b""
        end = data.rfind(b"\n") + 1
        pos += end
        for raw in data[:end].splitlines():
            try:
                obj = json.loads(raw.decode("utf-8"))
            except ValueError:
                continue
            with LOCK:
                if "set_msg" in obj:
                    m = obj["set_msg"]
                    WORLD["messages"][str(m["id"])] = m
                elif "set_chat" in obj:
                    c = obj["set_chat"]
                    WORLD["chats"][str(c["id"])] = c
                elif "set" in obj:
                    for k, v in obj["set"].items():
                        if isinstance(v, dict):
                            WORLD.setdefault(k, {}).update(v)
                        else:
                            WORLD[k] = v
                elif "exit" in obj:
                    os._exit(int(obj["exit"]))
                else:
                    QUEUE.append(obj)
            pump()
        time.sleep(0.02)


def message(mid):
    m = WORLD["messages"].get(str(mid))
    if m is None:
        return None
    out = {"kind": "message", "isInfo": False, "showPadlock": True, "state": 10,
           "viewType": "Text", "fromId": 20, "sortTimestamp": 1700000000,
           "timestamp": 1700000000, "file": None, "fileName": None, "text": ""}
    out.update(m)
    out["id"] = int(mid)
    if "sender" not in out:
        c = WORLD["contacts"].get(str(out["fromId"]), {"address": "", "displayName": ""})
        out["sender"] = c
    return out


def chat_info(cid):
    c = WORLD["chats"].get(str(cid))
    if c is None:
        return None
    return {"id": int(cid), "name": c.get("name", "chat %s" % cid),
            "chatType": c.get("chatType", "Single"),
            "isContactRequest": c.get("isContactRequest", False),
            "isSelfTalk": c.get("isSelfTalk", False),
            "isDeviceChat": c.get("isDeviceChat", False)}


def chat_ids(cid):
    return sorted(int(k) for k, m in WORLD["messages"].items() if m["chatId"] == cid)


def note(method, params):
    with open(path("requests.jsonl"), "a") as fh:
        fh.write(json.dumps({"method": method, "params": params}) + "\n")


def handle(rid, method, p):
    with LOCK:
        if method == "get_system_info":
            return reply(rid, {"deltachat_core_version": "fake-1"})
        if method == "get_all_account_ids":
            return reply(rid, list(STATE["accounts"]))
        if method == "add_account":
            STATE["accounts"].append(1)
            save_state()
            return reply(rid, 1)
        if method == "is_configured":
            return reply(rid, STATE["configured"])
        if method == "add_or_update_transport":
            if p[1].get("password") == "wrong":
                return reply(rid, error="Cannot login as %s: wrong password" % p[1].get("addr"))
            STATE["configured"] = True
            save_state()
            return reply(rid, None)
        if method == "create_group_chat":
            cid = max([int(k) for k in WORLD["chats"]] + [9]) + 1
            WORLD["chats"][str(cid)] = {"id": cid, "name": p[1], "chatType": "Group", "contacts": [1]}
            return reply(rid, cid)
        if method == "get_contacts":
            q = (p[2] or "").lower()
            return reply(rid, [dict(c, id=int(k)) for k, c in WORLD["contacts"].items()
                               if c.get("isKeyContact", True) and q in c["address"].lower()])
        if method == "add_contact_to_chat":
            ch = WORLD["chats"][str(p[1])]
            if p[2] not in ch.setdefault("contacts", []):
                ch["contacts"].append(p[2])
            return reply(rid, None)
        if method == "remove_contact_from_chat":
            ch = WORLD["chats"][str(p[1])]
            ch["contacts"] = [c for c in ch.get("contacts", []) if c != p[2]]
            return reply(rid, None)
        if method == "set_chat_name":
            WORLD["chats"][str(p[1])]["name"] = p[2]
            return reply(rid, None)
        if method == "leave_group":
            if WORLD["chats"].get(str(p[1]), {}).get("left"):
                return reply(rid, error="You are not a member of this group")
            WORLD["chats"].setdefault(str(p[1]), {})["left"] = True
            return reply(rid, None)
        if method == "delete_chat":
            WORLD["chats"].pop(str(p[1]), None)
            for k in [k for k, m in WORLD["messages"].items() if m["chatId"] == p[1]]:
                del WORLD["messages"][k]
            return reply(rid, None)
        if method in ("set_config", "start_io", "stop_io", "stop_io_for_all_accounts",
                      "accept_chat", "marknoticed_chat", "markseen_msgs"):
            return reply(rid, None)
        if method == "get_config":
            return reply(rid, WORLD.get("config", {}).get(p[1], ""))
        if method == "get_connectivity":
            return reply(rid, WORLD.get("connectivity", 4000))
        if method == "get_next_event":
            WAITING.append(rid)
            return None
        if method == "get_chatlist_entries":
            return reply(rid, sorted(int(k) for k in WORLD["chats"]))
        if method == "get_chatlist_items_by_entries":
            out = {}
            for cid in p[1]:
                c = WORLD["chats"][str(cid)]
                info = chat_info(cid)
                info.update(kind="ChatListItem", isDeviceTalk=info["isDeviceChat"],
                            freshMessageCounter=c.get("fresh", 0))
                out[str(cid)] = info
            return reply(rid, out)
        if method == "get_basic_chat_info":
            info = chat_info(p[1])
            return reply(rid, info) if info else reply(rid, error="no such chat")
        if method == "get_chat_contacts":
            return reply(rid, WORLD["chats"][str(p[1])].get("contacts", []))
        if method == "get_contact":
            return reply(rid, WORLD["contacts"][str(p[1])])
        if method == "get_message_ids":
            return reply(rid, chat_ids(p[1]))
        if method == "get_messages":
            return reply(rid, {str(i): message(i) for i in p[1] if message(i)})
        if method == "get_message":
            m = message(p[1])
            return reply(rid, m) if m else reply(rid, error="no such message")
        if method == "lookup_contact_id_by_addr":
            for k, c in WORLD["contacts"].items():
                if c["address"] == p[1]:
                    return reply(rid, int(k))
            return reply(rid, None)
        if method == "create_contact":
            cid = max([int(k) for k in WORLD["contacts"]] + [99]) + 1
            WORLD["contacts"][str(cid)] = {"address": p[1], "displayName": p[2] or p[1],
                                           "isKeyContact": False}
            return reply(rid, cid)
        if method == "create_chat_by_contact_id":
            for k, c in WORLD["chats"].items():
                if c.get("chatType", "Single") == "Single" and p[1] in c.get("contacts", []):
                    return reply(rid, int(k))
            cid = max([int(k) for k in WORLD["chats"]] + [9]) + 1
            ct = WORLD["contacts"][str(p[1])]
            WORLD["chats"][str(cid)] = {"id": cid, "name": ct["displayName"],
                                        "chatType": "Single", "contacts": [p[1]]}
            return reply(rid, cid)
        if method == "get_chat_securejoin_qr_code":
            return reply(rid, "https://i.delta.chat/#FAKEINVITE")
        if method == "secure_join":
            cid = max([int(k) for k in WORLD["chats"]] + [9]) + 1
            WORLD["chats"][str(cid)] = {"id": cid, "name": "Inviter", "chatType": "Single"}
            return reply(rid, cid)
        if method == "misc_send_text_message":
            if WORLD.get("no_keys"):
                return reply(rid, error="Failed to create send jobs: e2e encryption unavailable Msg#1")
            mid = max([int(k) for k in WORLD["messages"]] + [99]) + 1
            WORLD["messages"][str(mid)] = {
                "chatId": p[1], "fromId": 1, "text": p[2], "state": 26,
                "sortTimestamp": int(time.time()), "timestamp": int(time.time()),
                "sender": {"address": "me@example.org", "displayName": "Me"}}
            QUEUE.append({"contextId": 1, "event": {"kind": "MsgsChanged", "chatId": p[1],
                                                    "msgId": mid}})
            reply(rid, mid)
            return None
        return reply(rid, error="method not found: %s" % method)


def main():
    threading.Thread(target=control, daemon=True).start()
    for raw in sys.stdin:
        try:
            req = json.loads(raw)
        except ValueError:
            continue
        note(req["method"], req.get("params"))
        handle(req["id"], req["method"], req.get("params") or [])
        pump()


if __name__ == "__main__":
    main()
