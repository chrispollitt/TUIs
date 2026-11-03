#!/bin/bash
# RUN ON: cpbee.  Started on the laptop it copies itself to cpbee and runs there with a terminal (it asks for sudo once).  A few seconds.
# WHY:    Joy now runs ON the web host (the Live Helper Chat extension, tvchat/lhc-joy).  The old Joy - the `pro` and `lhc_pro` accounts
#         in Floyd's daemon here - must be OFF whenever the extension answers, or every visitor gets two answers.
# ACTION: off     take the [account:pro] and [account:lhc_pro] sections out of /etc/tvchat/floyd.conf (kept in
#                 /etc/tvchat/joy-pro-accounts.saved), check the config, restart tvchat-floyd.  The pro site's key stays in floyd.env.
#         on      put them back (the way back, if the extension has to be switched off).     status   which of the two is there.
# ORDER:  going to the host:   joy-webhost ACTION=install (she is silent) -> THIS with ACTION=off -> joy-webhost ACTION=enable.
#         going back:          joy-webhost ACTION=disable -> THIS with ACTION=on.
# SAFE:   re-runnable.  The old config is kept as floyd.conf.bak.<time>; a config that fails `check-config` is not installed.
set -euo pipefail

HOST=cpbee
CONF=/etc/tvchat/floyd.conf
SAVED=/etc/tvchat/joy-pro-accounts.saved
ACTION=${ACTION:-status}
here=$(cd "$(dirname "$0")" && pwd)
name=$(basename "$0")
say() { printf '%s\n' "$*"; }
die() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }
case $ACTION in off|on|status) ;; *) die "ACTION must be off, on or status";; esac

if [[ ${RUN_ON_REMOTE:-0} != 1 && $(hostname -s) != "$HOST" ]]; then
  scp -q "$here/$name" "$HOST:/tmp/$name"
  rc=0
  ssh -t -o ServerAliveInterval=10 "$HOST" "RUN_ON_REMOTE=1 ACTION='$ACTION' DRY_RUN='${DRY_RUN:-0}' bash /tmp/$name; rc=\$?; rm -f /tmp/$name; exit \$rc" || rc=$?
  [[ $rc == 0 ]] || die "it did not finish on $HOST (exit $rc). Paste the output above to Claude."
  exit 0
fi

# ---- on cpbee ----
PY=$(command -v python3) || die "python3 is not installed here"
TARGET=$(command -v tvchat-backend 2>/dev/null || echo /usr/local/bin/tvchat-backend)
have() { grep -q "^\[account:$1\]" "$CONF"; }
if [[ $ACTION == status ]]; then
  for a in pro lhc_pro; do if have $a; then say "[account:$a]: IN the config (the old Joy is on)"; else say "[account:$a]: not in the config"; fi; done
  test -f "$SAVED" && say "saved sections: $SAVED" || say "no saved sections"
  systemctl is-active tvchat-floyd; exit 0
fi
stamp=$(date +%Y%m%d-%H%M%S)
tmp=$(mktemp)
cat "$CONF" > "$tmp"
if [[ $ACTION == off ]]; then
  "$PY" - "$tmp" "$tmp.saved" <<'PYEOF'
import re, sys
path, saved = sys.argv[1:3]
lines = open(path, encoding="utf-8").read().split("\n")
out, taken, skip = [], [], False
for l in lines:
    m = re.match(r"^\[account:([^\]]+)\]", l)
    if m:
        skip = m.group(1) in ("pro", "lhc_pro")
    if skip:
        taken.append(l)
    else:
        out.append(l)
if not taken:
    print("  the old Joy is not in the config: nothing to take out"); open(saved, "w").write("")
else:
    open(saved, "w", encoding="utf-8").write("\n".join(taken).rstrip("\n") + "\n")
    open(path, "w", encoding="utf-8").write("\n".join(out).rstrip("\n") + "\n")
    print("  took out %d lines (the sections of pro and lhc_pro)" % len(taken))
PYEOF
else
  test -s "$SAVED" || die "$SAVED is not there: nothing to put back"
  if have pro || have lhc_pro; then say "  the old Joy is already in the config: nothing to put back"; rm -f "$tmp" "$tmp.saved"; exit 0; fi
  { cat "$tmp"; echo; sudo cat "$SAVED"; } > "$tmp.new"; mv "$tmp.new" "$tmp"; : > "$tmp.saved"
fi
out=$("$TARGET" check-config "$tmp" 2>&1) || { say "$out"; rm -f "$tmp" "$tmp.saved"; die "the new config does not check out; $CONF is as it was"; }
say "  $(printf '%s' "$out" | tail -1)"
if [[ ${DRY_RUN:-0} == 1 ]]; then say "DRY RUN: nothing was changed"; rm -f "$tmp" "$tmp.saved"; exit 0; fi
sudo cp -a "$CONF" "$CONF.bak.$stamp"
if [[ $ACTION == off && -s $tmp.saved ]]; then sudo install -m 640 -o root -g root "$tmp.saved" "$SAVED"; fi
sudo install -m "$(stat -c %a "$CONF")" -o "$(stat -c %U "$CONF")" -g "$(stat -c %G "$CONF")" "$tmp" "$CONF"
rm -f "$tmp" "$tmp.saved"
sudo systemctl restart tvchat-floyd
sleep 6
systemctl is-active tvchat-floyd || { sudo journalctl -u tvchat-floyd -n 20 --no-pager; die "tvchat-floyd is not running; the old config is $CONF.bak.$stamp"; }
for a in pro lhc_pro; do if have $a; then say "[account:$a]: IN the config"; else say "[account:$a]: not in the config"; fi; done
say "done: ACTION=$ACTION"
