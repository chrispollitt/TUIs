#!/usr/bin/env python3
"""Regenerate images/tvmail.png: tvmail on a small made-up mailbox, drawn
from the pyte screen (tvscreen.render_png).  Needs Pillow and Consolas.

    python3 tests/screenshot.py --bin build/tvmail [--backend-dir backend]
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import e2e_regress as r                    # noqa: E402

MAILS = [   # oldest first; Status: RO = read
    ("Alice Smith <alice@example.org>", "Mon, 01 Sep 2026 09:12:00 -0700", "Lunch on Thursday?",
     "RO", "Are you free on Thursday?\nThe new place on 5th has a good menu.\n\n-- Alice"),
    ("Bob Jones <bob@example.com>", "Tue, 02 Sep 2026 14:30:00 -0700", "Build 412 is green",
     "RO", "All layers passed on the laptop.\nI'll tag it after lunch."),
    ("Carol Diaz <carol@example.net>", "Wed, 03 Sep 2026 08:05:00 -0700", "Re: the Turbo Vision port",
     "", "The table view looks good.\nCould the same layout work for the folder list?\n\n> Name | Proto | Conn\n> Libera | IRC | No\n\nThanks!"),
    ("Dave Lee <dave@example.org>", "Thu, 04 Sep 2026 17:45:00 -0700", "Meeting notes",
     "", "Notes are in the wiki.\nAction items are at the bottom."),
]


def msg(frm, date, subj, status, body):
    import time
    head = "From x@y %s\nFrom: %s\nDate: %s\nSubject: %s\n" % (
        time.strftime("%a %b %d %H:%M:%S %Y", time.strptime(date[:25], "%a, %d %b %Y %H:%M:%S")),
        frm, date, subj)
    if status:
        head += "Status: %s\n" % status
    return head + "\n" + body + "\n\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default="build/tvmail")
    ap.add_argument("--backend-dir", default="backend")
    ap.add_argument("--out", default="images/tvmail.png")
    opts = ap.parse_args()
    tv = r.Tv(opts, fixture="".join(msg(*m) for m in MAILS))
    try:
        tv.wait("Meeting notes", 15)
        tv.send("Down", "Down", "Up", "Up", settle=0.3)  # on the inbox, Folders focused
        tv.pump(0.5)
        r.render_png(tv, opts.out)
        print("wrote", opts.out)
    finally:
        tv.close()


main()
