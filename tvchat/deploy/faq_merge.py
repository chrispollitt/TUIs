#!/usr/bin/env python3
"""faq_merge.py LIVE EXAMPLE: prints LIVE with everything of EXAMPLE it has not got: every entry (Q: ... A: ... K: ...) whose
QUESTION is not in LIVE, and every stock line (Hello, Fallback, Thanks, Warning, Goodbye, Lost, Help, About) LIVE lacks.  Entries
and lines LIVE already has are never touched (the owner's edits stay) and nothing is removed.  What was added goes to stderr."""
import re
import sys
import time

STOCK = ("Hello", "Fallback", "Thanks", "Warning", "Goodbye", "Lost", "Help", "About")


def blocks(text):
    """[(question, block text)]: a block runs from a Q: line to the next Q:, section header or stock line."""
    out, cur, q = [], None, None
    for line in text.split("\n"):
        if line.startswith("Q:"):
            if cur is not None:
                out.append((q, "\n".join(cur).rstrip()))
            cur, q = [line], re.sub(r"\s+", " ", line[2:]).strip().lower()
        elif cur is not None and re.match(r"^(# ----|%s):?" % "|".join(STOCK), line) and (line.startswith("# ----") or re.match(r"^(%s):" % "|".join(STOCK), line)):
            out.append((q, "\n".join(cur).rstrip()))
            cur, q = None, None
        elif cur is not None:
            cur.append(line)
    if cur is not None:
        out.append((q, "\n".join(cur).rstrip()))
    return out


def main():
    live = sys.stdin.read() if sys.argv[1] == "-" else open(sys.argv[1], encoding="utf-8").read()    # "-": the live FAQ on stdin
    ex = open(sys.argv[2], encoding="utf-8").read()
    have = set(q for q, _ in blocks(live))
    new = [(q, b) for q, b in blocks(ex) if q not in have]
    stock = []
    for key in STOCK:
        m = re.search(r"^%s:.*$" % key, ex, re.M)
        if m and not re.search(r"^%s:" % key, live, re.M):
            stock.append(m.group(0))
    for q, _ in new:
        sys.stderr.write("  + %s\n" % q)
    for s in stock:
        sys.stderr.write("  + stock line %s\n" % s.split(":")[0])
    if not new and not stock:
        sys.stdout.write(live)
        return
    add = ("\n\n# ---- added from the repo's faq.example.txt (%s) ----\n\n" % time.strftime("%Y-%m-%d")
           + "\n".join(stock) + ("\n\n" if stock and new else "") + "\n\n".join(b for _, b in new) + "\n")
    sys.stdout.write(live.rstrip("\n") + add)


if __name__ == "__main__":
    main()
