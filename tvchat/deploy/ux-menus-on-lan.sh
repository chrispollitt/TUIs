#!/bin/bash
# RUN ON: the laptop (Cygwin):  /cygdrive/c/Users/chris/OneDrive/DB/src/Claude/TO_BE_RUN/ux-menus-on-lan.sh
# WHY:    the 2026-11-09 menu overhaul (tvmail, tvchat, tvcal) was deployed on cpbee by ux-menus-on-cpbee.sh, which ran BEFORE
#         tvcal's menu change.  This brings the rest in line: this laptop, cmdesktop, and tvcal on cpbee.
# WHAT:   on each of: this laptop, cmdesktop, and (tvcal only) cpbee:
#           git pull --ff-only in ~/gh_cp/TUIs, then ./build.sh and ./install.sh for tvmail, tvchat, tvcal (cpbee: tvcal only).
#         install.sh also puts the new tvfloyd.py and backends (tvchat-backend, tvmail-backend, tvcal-backend) in place, which
#         cmdesktop needs for dcgw-client-setup and the Setup Wizard.
# CHANGES: the checkouts, /usr/local/{bin,share}/tv* on those machines.  No config, no passwords (cpbee asks for sudo once).
# NOTE:   a program that is RUNNING cannot be replaced on Cygwin: quit tvchat, tvmail and tvcal on the laptop and on cmdesktop
#         first (the script says so and stops for that machine if the install fails).  Restart them afterwards.
# UNDO:   git checkout the old commit in ~/gh_cp/TUIs and run build.sh / install.sh again.
# SAFE:   re-runnable.  Moves itself to DONE/ only when every machine worked.  DRY_RUN=1 shows the plan.
set -uo pipefail

HERE_HOST=$(hostname | cut -d. -f1)
CLIENTS="cmdesktop"
here=$(cd "$(dirname "$0")" && pwd)
name=$(basename "$0")
say() { printf '%s\n' "$*"; }

if [[ ${DRY_RUN:-0} == 1 ]]; then
  say "DRY RUN: would pull and rebuild tvmail, tvchat, tvcal on $HERE_HOST and: $CLIENTS; and tvcal on cpbee. Nothing was changed."
  exit 0
fi

# the same work, wherever it runs: $1 = the apps to do
update_script='
set -u
export PATH=/usr/local/bin:/usr/bin:$PATH
cd ~/gh_cp/TUIs || { echo "no ~/gh_cp/TUIs here"; exit 1; }
git pull --ff-only | tail -2 || exit 1
echo "  at: $(git log --oneline | head -1)"
for app in $APPS; do
  echo "  $app: build ..."
  ( cd $app && ./build.sh > /tmp/$app-build.log 2>&1 ) || { tail -15 /tmp/$app-build.log; echo "  $app did not build"; exit 1; }
  ( cd $app && ./install.sh > /tmp/$app-install.log 2>&1 ) || { tail -15 /tmp/$app-install.log; echo "  $app did not install (is it running? quit it and run this again)"; exit 1; }
  echo "  $app: installed"
done
'
failed=0

say "== this machine ($HERE_HOST)"
APPS="tvmail tvchat tvcal" bash -c "$update_script" || failed=1

for h in $CLIENTS; do
  say "== $h"
  if ! ssh -o BatchMode=yes -o ConnectTimeout=8 "$h" true 2>/dev/null; then
    say "  cannot reach $h over ssh (key login): skipped"
    failed=1
    continue
  fi
  ssh -o BatchMode=yes "$h" "APPS='tvmail tvchat tvcal' bash -lc '$update_script'" || failed=1
done

say "== cpbee (tvcal only; sudo asks once)"
if ssh -o BatchMode=yes -o ConnectTimeout=8 cpbee true 2>/dev/null; then
  ssh -t cpbee "APPS=tvcal bash -lc '$update_script'" || failed=1
else
  say "  cannot reach cpbee over ssh: skipped"
  failed=1
fi

say ""
if [[ $failed == 0 ]]; then
  mv -- "$here/$name" "$here/DONE/$name" 2>/dev/null || true
  say "Done.  Restart tvmail, tvchat and tvcal where they were running."
else
  say "Some machine did not finish (see above).  Fix that and run this again: finished ones are quick to redo."
  exit 1
fi
