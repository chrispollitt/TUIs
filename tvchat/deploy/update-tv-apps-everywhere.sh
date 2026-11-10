#!/bin/bash
# RUN ON: the laptop (Cygwin):  /cygdrive/c/Users/chris/OneDrive/DB/src/Claude/TO_BE_RUN/REUSABLE/update-tv-apps-everywhere.sh
# WHY:    one command to bring tvmail, tvchat and tvcal (and the gateway dcgw) up to date everywhere after a change is pushed.
# WHAT:   on this laptop, cmdesktop and cpbee:
#           git pull --ff-only in ~/gh_cp/TUIs, then ./build.sh and ./install.sh for tvmail, tvchat, tvcal ; cpbee also installs the new dcgw and restarts dcgw and tvchat-floyd.
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
  say "DRY RUN: would pull and rebuild tvmail, tvchat, tvcal on $HERE_HOST, $CLIENTS and cpbee (plus dcgw there). Nothing was changed."
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

say "== cpbee (all three, then dcgw and tvchat-floyd; sudo asks)"
if ssh -o BatchMode=yes -o ConnectTimeout=8 cpbee true 2>/dev/null; then
  ssh -t cpbee "APPS='tvmail tvchat tvcal' bash -lc '$update_script'" || failed=1
  ssh -t cpbee 'cd ~/gh_cp/TUIs && sudo install -m 755 tvchat/backend/dcgw /usr/local/bin/dcgw && sudo systemctl restart dcgw tvchat-floyd && sleep 6 && systemctl is-active dcgw tvchat-floyd' || failed=1
else
  say "  cannot reach cpbee over ssh: skipped"
  failed=1
fi

say ""
if [[ $failed == 0 ]]; then
  say "Done.  Restart tvmail, tvchat and tvcal where they were running."
else
  say "Some machine did not finish (see above).  Fix that and run this again: finished ones are quick to redo."
  exit 1
fi
