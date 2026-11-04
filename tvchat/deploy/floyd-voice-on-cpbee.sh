#!/bin/bash
# RUN ON: the laptop (Cygwin).  It copies files to cpbee and runs the root part there with a terminal, so sudo asks
#         for your password once.  Takes under a minute.
# WHY:    Floyd's personality (FloydMind: !seen !tell !quote !karma !8ball !fortune !joke !about, the parser game, his
#         chatter) and his memory moved from tvchat-backend into the shared tvfloyd.py, so tvmail and tvcal have the
#         same Floyd.  The tvchat-backend on cpbee now imports them from tvfloyd.py, so BOTH must be installed together
#         or the central Floyd (tvchat-floyd) cannot start.
# WHAT:   1. installs tvchat-backend, tvcal-backend, tvmail-backend into /usr/local/bin (old files kept as .bak.<time>);
#         2. installs tvfloyd.py into /usr/local/share/{tvchat,tvcal,tvmail};
#         3. proves it: tvfloyd.py loads and has FloydMind; tvcal-backend and tvmail-backend answer "!about";
#         4. restarts tvchat-floyd and shows its state and the last journal lines.
# CHANGES on cpbee: /usr/local/bin/{tvchat,tvcal,tvmail}-backend, /usr/local/share/{tvchat,tvcal,tvmail}/tvfloyd.py.
#         Floyd's memory files (the account's JSON under ~floyd/.local/share/tvchat/floyd/) are not touched.
# UNDO:   copy the .bak.<time> files back over the three backends, restore the old tvfloyd.py (it has a .bak too),
#         and `sudo systemctl restart tvchat-floyd`.  The old tvchat-backend does not need the new tvfloyd.py but
#         works with it.
# SAFE:   re-runnable.  Moves itself to DONE/ only on success.  DRY_RUN=1 shows the plan.
set -euo pipefail

HOST=cpbee
repo=/home/chris/gh_cp/TUIs
here=$(cd "$(dirname "$0")" && pwd)
name=$(basename "$0")
say() { printf '%s\n' "$*"; }
die() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }

if [[ ${DRY_RUN:-0} == 1 ]]; then
  say "DRY RUN: would copy tvchat-backend, tvcal-backend, tvmail-backend and tvfloyd.py to $HOST, run there with a"
  say "terminal (sudo asks once), install them (backups kept), test that Floyd's voice loads, restart tvchat-floyd."
  say "Nothing was changed."
  exit 0
fi

if [[ ${RUN_ON_REMOTE:-0} != 1 && $(hostname -s) != "$HOST" ]]; then
  for f in tvchat/backend/tvchat-backend tvcal/backend/tvcal-backend tvmail/backend/tvmail-backend tvcommon/py/tvfloyd.py; do
    [[ -f $repo/$f ]] || die "$repo/$f is not here"
  done
  grep -q "^class FloydMind" "$repo/tvcommon/py/tvfloyd.py" || die "the repo's tvfloyd.py has no FloydMind"
  ssh -o BatchMode=yes "$HOST" true || die "cannot ssh to $HOST"
  ssh "$HOST" 'rm -rf /tmp/floyd-voice && mkdir -m 700 /tmp/floyd-voice'
  scp -q "$here/$name" "$repo/tvchat/backend/tvchat-backend" "$repo/tvcal/backend/tvcal-backend" \
        "$repo/tvmail/backend/tvmail-backend" "$repo/tvcommon/py/tvfloyd.py" "$HOST:/tmp/floyd-voice/"
  rc=0
  ssh -t -o ServerAliveInterval=10 "$HOST" \
    "RUN_ON_REMOTE=1 bash /tmp/floyd-voice/$name; rc=\$?; rm -rf /tmp/floyd-voice; exit \$rc" || rc=$?
  [[ $rc == 0 ]] || die "it did not finish on $HOST (exit $rc); nothing was moved. Paste the output above to Claude."
  mv -- "$here/$name" "$here/DONE/$name" 2>/dev/null || true
  say "Done."
  exit 0
fi

# ------------------------------------------------------------------ on cpbee ---
D=/tmp/floyd-voice
stamp=$(date +%Y%m%d-%H%M%S)

say "step 1/4: installing the programs (sudo password) ..."
for f in tvchat-backend tvcal-backend tvmail-backend; do
  if [[ -f /usr/local/bin/$f ]] && ! cmp -s "$D/$f" "/usr/local/bin/$f"; then
    sudo cp -a "/usr/local/bin/$f" "/usr/local/bin/$f.bak.$stamp"
  fi
  sudo install -m 755 "$D/$f" "/usr/local/bin/$f"
done

say "step 2/4: installing tvfloyd.py beside each of them ..."
for dir in /usr/local/share/tvchat /usr/local/share/tvcal /usr/local/share/tvmail; do
  sudo install -d -m 755 "$dir"
  if [[ -f $dir/tvfloyd.py ]] && ! cmp -s "$D/tvfloyd.py" "$dir/tvfloyd.py"; then
    sudo cp -a "$dir/tvfloyd.py" "$dir/tvfloyd.py.bak.$stamp"
  fi
  sudo install -m 644 "$D/tvfloyd.py" "$dir/tvfloyd.py"
done

say "step 3/4: does his voice load? ..."
python3 -c "
import importlib.util, sys
s = importlib.util.spec_from_file_location('tvfloyd', '/usr/local/share/tvchat/tvfloyd.py')
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
sys.exit(0 if hasattr(m, 'FloydMind') and hasattr(m, 'persona') else 1)" || die "the installed tvfloyd.py has no FloydMind"
for b in tvcal-backend tvmail-backend; do
  out=$(XDG_DATA_HOME=$(mktemp -d) /usr/local/bin/$b floyd '!about' 2>&1) || die "$b floyd failed: $out"
  grep -q "B-19-7" <<<"$out" || die "$b did not answer !about: $out"
  say "  $b: ok"
done
/usr/local/bin/tvchat-backend --help >/dev/null 2>&1 || die "tvchat-backend does not start"

say "step 4/4: restarting tvchat-floyd ..."
sudo systemctl restart tvchat-floyd
sleep 6
systemctl is-active tvchat-floyd || { sudo journalctl -u tvchat-floyd -n 25 --no-pager; die "tvchat-floyd is not running; the journal is above"; }
sudo journalctl -u tvchat-floyd -n 6 --no-pager || true
say "Floyd is himself everywhere.  In tvmail and tvcal try: !about  !fortune  !joke  !tell NICK hi  bob++"
