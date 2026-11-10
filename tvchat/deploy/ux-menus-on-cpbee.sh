#!/bin/bash
# RUN ON: the laptop (Cygwin):  /cygdrive/c/Users/chris/OneDrive/DB/src/Claude/TO_BE_RUN/ux-menus-on-cpbee.sh
# WHY:    deploys the 2026-11-09 menu overhaul on cpbee: the new Chat menu (tvchat), the tidier Message menu (tvmail), tvcal's
#         changed text, and the gateway's invitation-mail scan (dcgw), so gateway accounts get the Accept question.
# WHAT:   on cpbee (ssh with a terminal; sudo asks once):
#         1. git pull in ~/gh_cp/TUIs (fast-forward only);
#         2. builds tvmail, tvchat and tvcal there and installs them (/usr/local);
#         3. installs the new dcgw program (the old one is kept as dcgw.bak.<time>);
#         4. restarts dcgw (Delta Chat is away for a few seconds; cores restart) and tvchat-floyd;
#         5. checks both services are active.
# CHANGES on cpbee: the checkout, /usr/local/{bin,share}/tv*, /usr/local/bin/dcgw.  No config, no passwords.
# NOT HERE: your laptop and cmdesktop need the new programs too (build + install there; then restart tvchat/tvmail).
# UNDO:   sudo cp /usr/local/bin/dcgw.bak.<time> /usr/local/bin/dcgw && sudo systemctl restart dcgw; git checkout the old commit and reinstall.
# SAFE:   re-runnable.  Moves itself to DONE/ only on success.  DRY_RUN=1 shows the plan.
set -euo pipefail

HOST=cpbee
here=$(cd "$(dirname "$0")" && pwd)
name=$(basename "$0")
say() { printf '%s\n' "$*"; }
die() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }

if [[ ${DRY_RUN:-0} == 1 ]]; then
  say "DRY RUN: would pull ~/gh_cp/TUIs on $HOST, build and install tvmail, tvchat, tvcal, install dcgw, restart dcgw and"
  say "tvchat-floyd. Nothing was changed."
  exit 0
fi

if [[ ${RUN_ON_REMOTE:-0} != 1 && $(hostname -s) != "$HOST" ]]; then
  ssh -o BatchMode=yes "$HOST" true || die "cannot ssh to $HOST"
  scp -q "$here/$name" "$HOST:/tmp/$name"
  rc=0
  ssh -t -o ServerAliveInterval=10 "$HOST" "RUN_ON_REMOTE=1 bash /tmp/$name; rc=\$?; rm -f /tmp/$name; exit \$rc" || rc=$?
  [[ $rc == 0 ]] || die "it did not finish on $HOST (exit $rc); paste the output above to Claude."
  mv -- "$here/$name" "$here/DONE/$name" 2>/dev/null || true
  say ""
  say "Done on $HOST.  Next: the same new programs on this laptop and on cmdesktop."
  exit 0
fi

# ------------------------------------------------------------------ on cpbee ---
repo=$HOME/gh_cp/TUIs
cd "$repo"
say "step 1/5: git pull ..."
git pull --ff-only | tail -3
say "  now at: $(git log --oneline | head -1)"

for app in tvmail tvchat tvcal; do
  say "step 2/5: build + install $app (a few minutes) ..."
  ( cd "$repo/$app" && ./build.sh > "/tmp/$app-build.log" 2>&1 ) || { tail -20 "/tmp/$app-build.log"; die "$app did not build (log above)"; }
  ( cd "$repo/$app" && ./install.sh > "/tmp/$app-install.log" 2>&1 ) || { tail -20 "/tmp/$app-install.log"; die "$app did not install (log above)"; }
  say "  $app installed"
done

say "step 3/5: dcgw (sudo password) ..."
sudo cp -p /usr/local/bin/dcgw "/usr/local/bin/dcgw.bak.$(date +%s)"
sudo install -m 755 "$repo/tvchat/backend/dcgw" /usr/local/bin/dcgw
grep -q "def scan_invite_mail" /usr/local/bin/dcgw || die "the installed dcgw lacks the invitation scan"

say "step 4/5: restarting dcgw and tvchat-floyd ..."
sudo systemctl restart dcgw tvchat-floyd
sleep 8

say "step 5/5: checking ..."
for s in dcgw tvchat-floyd; do
  systemctl is-active "$s" >/dev/null || { sudo journalctl -u "$s" -n 20 --no-pager; die "$s is not running; the journal is above"; }
  say "  $s: active"
done
sudo journalctl -u dcgw -n 6 --no-pager | sed 's/^/  /'
