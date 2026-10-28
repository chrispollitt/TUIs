#!/usr/bin/env python3
"""Regenerate images/tvchat.png: tvchat on a scripted IRC server (cpbee), with
Libera and a talk account idle beside it, drawn from the pyte screen
(tvscreen.render_png).  Needs Pillow and Consolas.

    python3 tests/screenshot.py --bin build/tvchat [--backend-dir backend]
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import e2e_regress as r                    # noqa: E402
from fake_irc import FakeIrcd              # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default="build/tvchat")
    ap.add_argument("--backend-dir", default="backend")
    ap.add_argument("--out", default="images/tvchat.png")
    opts = ap.parse_args()
    srv = FakeIrcd()
    srv.start()
    conf = srv.conf(channels="#test", display="cpbee", nick="chris-pollitt")
    conf = conf.replace("autoconnect = fake", "autoconnect = fake") + (
        "\n[account:libera]\nprotocol = irc\ndisplay = Libera\nhost = 127.0.0.1\nport = 1\n"
        "tls = false\nnick = chris-pollitt\n"
        "\n[account:cpbeetalk]\nprotocol = talk\ndisplay = cpbee talk\nhost = 127.0.0.1\n"
        "port = 1\nuser = chris\n")
    srv.conf = lambda **kw: conf
    tv = r.Tv(opts, srv)
    try:
        c = r.Case(tv)
        r.started(tv, c)
        srv.wait_joined("#test")
        tv.wait("Yes", 8)
        tv.send("Down", "Down", "Up", "Up", settle=0.2)   # (stays on cpbee, the first row)
        srv.say(":tester2!t@h PRIVMSG #test :hi chris",
                ":tester2!t@h PRIVMSG #test :anyone tried the new servers table?")
        tv.wait("tester2", 5)
        tv.send("Enter", "Down", "Enter", settle=1.0)     # servers > #test > its input
        tv.send("hello tester2", "Enter", settle=1.0)
        tv.send("Tab", settle=0.8)                        # round to the servers
        tv.pump(0.5)
        r.render_png(tv, opts.out)
        print("wrote", opts.out, "\n" + tv.dump())
    finally:
        tv.close()


main()
