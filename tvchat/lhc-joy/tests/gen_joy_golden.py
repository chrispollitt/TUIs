#!/usr/bin/env python3
"""Writes lhc-joy/tests/golden.json: what the REAL Python Floyd-pro (the reference) says to a set of conversations, for
tests/joy_golden.php to hold the PHP port to, answer for answer.  It drives FloydAdapter through the test suite's fake
Live Helper Chat site.  Run from the tvchat directory:  python3 lhc-joy/tests/gen_joy_golden.py   (about 3 minutes)"""
import json
import os
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
root = os.path.dirname(os.path.dirname(here))
sys.path.insert(0, os.path.join(root, "tests"))
import test_chat_backend as T                                   # noqa: E402

WORDS = ["zork", "blarg", "quux", "frobnicate", "xyzzy", "plugh", "wibble", "gronk"]
SCENARIOS = [
    ("basic questions", ["What should I wear?", "do I need a yogga mat", "how much does it cost", "Does Chris do ZOOM sessions?",
                         "is he certified?", "Is there a wellness app?", "are you an AI?", "are you a human?", "who is chris",
                         "what services do you offer", "how long is a session", "where are you based",
                         "I am pregnent, is it safe?", "can children join", "arer you a human?", "is there something free I can try",
                         "Is there an example video of him leading?", "how do I become a certified leader"]),
    ("commands", ["/help", "!about", "!frobnicate", "❤️\U0001F923‼️", "!help", "help", "?", "what can you do", "/start",
                  "!!", "/"]),
    ("greet thanks joke", ["hello", "thanks!", "!joke", "good morning there", "thank you so much"]),
    ("lost after five misses", WORDS[:7]),
    ("an answer resets the count", WORDS[:5] + ["What should I wear?"] + WORDS[:5] + ["how much does it cost"]),
    ("abuse then ended", ["you stupid f*cking bot", "What should I wear?", "this is shit", "What should I wear?", "hello"]),
    ("typos", ["whta should I wear?", "how much dose it cost", "wheelchiar access", "what is laughtr yoga", "is ther a club nere me",
               "do you have an app for my phone", "can a wheelchiar user join", "Are yuo human?"]),
    ("nonsense and edge", ["how much is a ticket to mars", "what is the weather today", "tell me a jokee", "can someone call about the moon",
                           "SELECT * FROM users WHERE username = '' OR '1'='1' --", "x" * 400, "   ", "123456", "pat@example.org my number is 604 555 1234"]),
    ("non-ascii", ["¿Qué es el yoga de la risa?", "日本語", "naïve question about yoga", "WHAT SHOULD I WEAR???"]),
]
# each of these in a chat of its own (no state from a neighbour)
ALONE = ["bugger off", "bite me", "you suck!!!!", "idiot!", "fsck off", "you're stupid!", "shut up", "your service is terrible",
         "this sucks", "is that a stupid question?", "Dickens", "my assistant", "shiitake mushrooms", "I am not an idiot-proof person",
         "How much does it cost?", "hello", "thanks", "!joke", "/help", "what is laughter yoga", "Who is Chris? Who runs Universal Laughter?",
         "do you do yoga poses", "can I watch him teach", "are you qualified", "is there a video", "can you lead a virtual workshop",
         "Are there any laughter clubs near me?", "where are the clubs"]
for w in ALONE:
    SCENARIOS.append(("alone: " + w[:30], [w]))


def main():
    t = T.TestFloydPro("test_check_config_wants_a_faq_with_entries")
    t.setUp()
    t.faq.write_text(open(os.path.join(root, "configure", "faq.example.txt"), encoding="utf-8").read(), encoding="utf-8")
    ad = t.chat.adapter(t.chat.by_id["lhc"])
    ad.MAX_PER_CHAT = ad.MAX_TOTAL = 10 ** 6            # (his own cap of lines an hour is not what is compared)
    out = []
    cid = 100
    only = os.environ.get("GOLDEN_ONLY", "")                    # e.g. GOLDEN_ONLY=joke: only those conversations, merged into golden.json
    try:
        for name, lines in SCENARIOS:
            cid += 1
            if only and only not in name and not any(only in l for l in lines):
                continue
            steps = []
            for i, text in enumerate(lines):
                before = len(t.lhc.posted)
                if i == 0:
                    t.lhc.chat(cid, "Pat", text, ip="10.0.0.%d" % (cid % 250))
                else:
                    t.lhc.say(cid, text)
                try:
                    t.turn(lambda: len(t.lhc.posted) > before, timeout=2.5)
                except AssertionError:
                    pass
                quiet, seen, deadline = time.time(), len(t.lhc.posted), time.time() + 12
                window = 4.2 if text.strip().lower() == "!joke" else 1.8       # (a joke's punchline comes 3 s after the setup)
                while time.time() - quiet < window and time.time() < deadline:    # until he has said all of it
                    t.settle(0.2)
                    if len(t.lhc.posted) != seen:
                        quiet, seen = time.time(), len(t.lhc.posted)
                replies = [q["msg"] for q in t.lhc.posted[before:] if q["chat_id"] == str(cid)]
                if any(("ending this chat" in r or "You seem to be having an issue" in r) for r in replies):
                    t.settle(3.2)                                  # (he closes the chat a moment after saying goodbye)
                steps.append({"in": text, "out": replies, "closed": cid in t.lhc.closed})
            out.append({"name": name, "steps": steps})
            print("%-40s %d steps" % (name, len(steps)), flush=True)
    finally:
        t.doCleanups()
        t.tearDown()
    path = os.path.join(here, "golden.json")
    if only and os.path.exists(path):                           # merge: replace the conversations just redone, keep the rest
        old = json.load(open(path, encoding="utf-8"))
        redone = {s["name"]: s for s in out}
        out = [redone.pop(s["name"], s) for s in old] + list(redone.values())
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("wrote %s (%d scenarios)" % (path, len(out)))


if __name__ == "__main__":
    main()
