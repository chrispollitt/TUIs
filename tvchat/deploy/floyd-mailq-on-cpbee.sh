#!/bin/bash
# RUN ON: the laptop (Cygwin).  Copies files to cpbee and runs the root part there with a terminal (sudo asks once).
# WHY:    the sentinel's mail-queue check could not run: tvchat-floyd has NoNewPrivileges=yes, which stops the setgid
#         `postqueue` from gaining the postdrop group ("fatal: Connect to the Postfix showq service: Permission
#         denied" in the journal), so a stuck queue would never have been reported.
# WHAT:   1. installs the new tvfloyd.py (the check now says so once in the journal when it cannot read the queue);
#         2. adds a systemd drop-in /etc/systemd/system/tvchat-floyd.service.d/mailq.conf with SupplementaryGroups=postdrop
#            (only that one service gets the group; the floyd user itself is not changed, NoNewPrivileges stays);
#         3. proves that group is enough (`sudo -u floyd -g postdrop postqueue -p`), reloads systemd, restarts
#            tvchat-floyd and checks that the journal no longer complains.
# CHANGES on cpbee: /usr/local/share/{tvchat,tvcal,tvmail}/tvfloyd.py, the drop-in file.
#         The postdrop group can also hand mail to Postfix's maildrop queue; that is the one thing it adds.
# UNDO:   sudo rm /etc/systemd/system/tvchat-floyd.service.d/mailq.conf && sudo systemctl daemon-reload && sudo systemctl restart tvchat-floyd
# SAFE:   re-runnable.  Moves itself to DONE/ only on success.  DRY_RUN=1 shows the plan.
set -euo pipefail

HOST=cpbee
repo=/home/chris/gh_cp/TUIs
here=$(cd "$(dirname "$0")" && pwd)
name=$(basename "$0")
say() { printf '%s\n' "$*"; }
die() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }

if [[ ${DRY_RUN:-0} == 1 ]]; then
  say "DRY RUN: would install tvfloyd.py on $HOST, add the drop-in SupplementaryGroups=postdrop for tvchat-floyd,"
  say "test postqueue as floyd with that group, restart tvchat-floyd. Nothing was changed."
  exit 0
fi

if [[ ${RUN_ON_REMOTE:-0} != 1 && $(hostname -s) != "$HOST" ]]; then
  [[ -f $repo/tvcommon/py/tvfloyd.py ]] || die "$repo/tvcommon/py/tvfloyd.py is not here"
  grep -q "cannot read the mail queue" "$repo/tvcommon/py/tvfloyd.py" || die "the repo's tvfloyd.py is not the newest (commit first?)"
  ssh -o BatchMode=yes "$HOST" true || die "cannot ssh to $HOST"
  ssh "$HOST" 'rm -rf /tmp/floyd-mailq && mkdir -m 700 /tmp/floyd-mailq'
  scp -q "$here/$name" "$repo/tvcommon/py/tvfloyd.py" "$HOST:/tmp/floyd-mailq/"
  rc=0
  ssh -t -o ServerAliveInterval=10 "$HOST" \
    "RUN_ON_REMOTE=1 bash /tmp/floyd-mailq/$name; rc=\$?; rm -rf /tmp/floyd-mailq; exit \$rc" || rc=$?
  [[ $rc == 0 ]] || die "it did not finish on $HOST (exit $rc); paste the output above to Claude."
  mv -- "$here/$name" "$here/DONE/$name" 2>/dev/null || true
  say "Done."
  exit 0
fi

# ------------------------------------------------------------------ on cpbee ---
D=/tmp/floyd-mailq
DROP=/etc/systemd/system/tvchat-floyd.service.d
stamp=$(date +%Y%m%d-%H%M%S)

say "step 1/3: tvfloyd.py (sudo password) ..."
for dir in /usr/local/share/tvchat /usr/local/share/tvcal /usr/local/share/tvmail; do
  sudo install -d -m 755 "$dir"
  if [[ -f $dir/tvfloyd.py ]] && ! cmp -s "$D/tvfloyd.py" "$dir/tvfloyd.py"; then
    sudo cp -a "$dir/tvfloyd.py" "$dir/tvfloyd.py.bak.$stamp"
  fi
  sudo install -m 644 "$D/tvfloyd.py" "$dir/tvfloyd.py"
done

say "step 2/3: does the postdrop group let floyd read the queue? ..."
out=$(sudo -u floyd -g postdrop /usr/sbin/postqueue -p 2>&1) || die "even with the postdrop group floyd cannot read the queue: $out"
say "  yes: $(tail -1 <<<"$out")"

say "step 3/3: the drop-in and a restart ..."
sudo install -d -m 755 "$DROP"
printf '[Service]\n# the sentinel reads the mail queue with postqueue -p; NoNewPrivileges blocks the setgid program, so the service gets the group\nSupplementaryGroups=postdrop\n' \
  | sudo tee "$DROP/mailq.conf" >/dev/null
sudo systemctl daemon-reload
sudo systemctl restart tvchat-floyd
sleep 25
systemctl is-active tvchat-floyd || { sudo journalctl -u tvchat-floyd -n 25 --no-pager; die "tvchat-floyd is not running; the journal is above"; }
if sudo journalctl -u tvchat-floyd --since "-30s" --no-pager | grep -qE "showq service|cannot read the mail queue"; then
  sudo journalctl -u tvchat-floyd -n 30 --no-pager; die "the queue is still unreadable for the service; the journal is above"
fi
say "  the service reads the mail queue now"
