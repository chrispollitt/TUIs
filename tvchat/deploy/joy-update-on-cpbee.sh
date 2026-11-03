#!/bin/bash
# RUN ON: the laptop (it copies the backend and the FAQ to cpbee and works there; it asks for sudo once).  RE-RUNNABLE.  The REAL script is here in the repo
#         (tvchat/deploy); Claude puts a stub in TO_BE_RUN when there is something to deploy (it calls this and moves itself to
#         DONE on success).  Run it whenever Joy's code changed or FAQ entries were added.  Takes a few seconds.
# WHAT:   1. installs the repo's tvchat-backend if it differs from cpbee's (old one kept as .bak.<time>) and restarts tvchat-floyd
#            (only then: a FAQ change alone needs no restart);
#         2. adds to Joy's LIVE FAQ (/etc/tvchat/faq.txt) every entry of the repo's configure/faq.example.txt whose QUESTION is not in
#            it yet, and any stock line (Hello, Fallback, Thanks, Warning, Goodbye, Lost, Help) it has not got.  Entries already there are NEVER touched (your edits and re-worded answers stay) and nothing is removed; the old file is
#            kept as .bak.<time>.  Joy re-reads the file when it changes.
# NOT:    it does not change an entry that exists: edit /etc/tvchat/faq.txt by hand for that.
# SAFE:   DRY_RUN=1 says what it would install and add, and changes nothing.
set -euo pipefail

HOST=cpbee
REPO=/home/chris/gh_cp/TUIs/tvchat
REPO_BACKEND=$REPO/backend/tvchat-backend
REPO_FAQ=$REPO/configure/faq.example.txt
LIVE=/etc/tvchat/faq.txt

here=$(cd "$(dirname "$0")" && pwd)
name=$(basename "$0")
say() { printf '%s\n' "$*"; }
die() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }

if [[ ${RUN_ON_REMOTE:-0} != 1 && $(hostname -s) != "$HOST" ]]; then
  [[ -f $REPO_BACKEND && -f $REPO_FAQ ]] || die "$REPO_BACKEND or $REPO_FAQ is not here"
  scp -q "$here/$name" "$HOST:/tmp/$name"
  scp -q "$REPO_BACKEND" "$HOST:/tmp/tvchat-backend.new"
  scp -q "$REPO_FAQ" "$HOST:/tmp/faq.example.txt"
  rc=0
  ssh -t -o ServerAliveInterval=10 "$HOST" \
    "RUN_ON_REMOTE=1 DRY_RUN='${DRY_RUN:-0}' bash /tmp/$name; rc=\$?; rm -f /tmp/$name /tmp/tvchat-backend.new /tmp/faq.example.txt; exit \$rc" || rc=$?
  [[ $rc == 0 ]] || die "it did not finish on $HOST (exit $rc). Paste the output above to Claude."
  exit 0
fi

# ---- on cpbee ----
NEW=/tmp/tvchat-backend.new
[[ -f $NEW && -f /tmp/faq.example.txt ]] || die "the files to install are not here (run this from the laptop)"
PY=$(command -v python3) || die "python3 is not installed here"
TARGET=$(command -v tvchat-backend 2>/dev/null || echo /usr/local/bin/tvchat-backend)
stamp=$(date +%Y%m%d-%H%M%S)
restart=0

say "step 1/2: the backend ($TARGET) ..."
if [[ -f $TARGET ]] && cmp -s "$NEW" "$TARGET"; then say "  already this version"
elif [[ ${DRY_RUN:-0} == 1 ]]; then say "  DRY RUN: would install the new backend and restart tvchat-floyd"
else
  sudo cp -a "$TARGET" "$TARGET.bak.$stamp"
  sudo install -m 755 "$NEW" "$TARGET"
  say "  installed (the old one: $TARGET.bak.$stamp)"
  restart=1
fi

say "step 2/2: the FAQ ($LIVE) ..."
[[ -r $LIVE ]] || die "$LIVE is not there (or not readable): run floyd-pro-on-cpbee.sh first"
tmp=$(mktemp)
cat "$LIVE" > "$tmp"
"$PY" - "$tmp" /tmp/faq.example.txt <<'PYEOF'
import re, sys, time
live_path, ex_path = sys.argv[1:3]
live = open(live_path, encoding="utf-8").read()
ex = open(ex_path, encoding="utf-8").read()

def blocks(text):
    """[(question, block text)]: a block runs from a Q: line to the next Q:, section header or stock line."""
    out, cur, q = [], None, None
    for line in text.split("\n"):
        if line.startswith("Q:"):
            if cur is not None:
                out.append((q, "\n".join(cur).rstrip()))
            cur, q = [line], re.sub(r"\s+", " ", line[2:]).strip().lower()
        elif cur is not None and re.match(r"^(# ----|Hello:|Fallback:|Thanks:)", line):
            out.append((q, "\n".join(cur).rstrip()))
            cur, q = None, None
        elif cur is not None:
            cur.append(line)
    if cur is not None:
        out.append((q, "\n".join(cur).rstrip()))
    return out

have = set(q for q, _ in blocks(live))
new = [(q, b) for q, b in blocks(ex) if q not in have]
stock = []                               # his stock lines the live file has not got yet (never one it has)
for key in ("Hello", "Fallback", "Thanks", "Warning", "Goodbye", "Lost", "Help"):
    m = re.search(r"^%s:.*$" % key, ex, re.M)
    if m and not re.search(r"^%s:" % key, live, re.M):
        stock.append(m.group(0))
if not new and not stock:
    print("  the live FAQ already has every entry and stock line of the repo's: nothing to add")
    sys.exit(0)
for q, _ in new:
    print("  + " + q)
for s in stock:
    print("  + stock line " + s.split(":")[0])
add = ("\n\n# ---- added from the repo's faq.example.txt (%s) ----\n\n" % time.strftime("%Y-%m-%d")
       + "\n".join(stock) + ("\n\n" if stock and new else "") + "\n\n".join(b for _, b in new) + "\n")
open(live_path, "w", encoding="utf-8").write(live.rstrip("\n") + add)
open(live_path + ".added", "w").write("%d\n" % (len(new) + len(stock)))
PYEOF
if [[ -f $tmp.added ]]; then
  n=$(cat "$tmp.added"); rm -f "$tmp.added"
  if [[ ${DRY_RUN:-0} == 1 ]]; then
    say "  DRY RUN: $n entr$([[ $n == 1 ]] && echo y || echo ies) would be added."
  else
    sudo cp -a "$LIVE" "$LIVE.bak.$stamp"
    sudo install -m "$(sudo stat -c %a "$LIVE")" -o "$(sudo stat -c %U "$LIVE")" -g "$(sudo stat -c %G "$LIVE")" "$tmp" "$LIVE"
    say "  added $n entr$([[ $n == 1 ]] && echo y || echo ies) (the old file: $LIVE.bak.$stamp). Joy reads it again by herself."
  fi
fi
rm -f "$tmp"

if [[ $restart == 1 ]]; then
  say "restarting tvchat-floyd ..."
  sudo systemctl restart tvchat-floyd
  sleep 8
  systemctl is-active tvchat-floyd || { sudo journalctl -u tvchat-floyd -n 25 --no-pager; die "tvchat-floyd is not running; the journal is above. The old backend is $TARGET.bak.$stamp"; }
  sudo journalctl -u tvchat-floyd -n 10 --no-pager | grep -i -E "lhc|connected|error" || true
fi
[[ ${DRY_RUN:-0} == 1 ]] && say "Nothing was changed."
exit 0
