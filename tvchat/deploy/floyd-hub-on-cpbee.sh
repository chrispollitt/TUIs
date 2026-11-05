#!/bin/bash
# RUN ON: the laptop (Cygwin).  Copies files to cpbee and runs the root part there with a terminal (sudo asks once).
# WHY:    phase 1 of the master Floyd's event hub (tvcommon/FLOYD-MASTER.md, part 2): the gate gets `publish` and
#         `events`, so a toast sent on one machine (`!send cmlaptop text` in F7) shows up on the others, and the
#         relay on each machine (started by tvcal / tvmail / tvchat) turns hub events into toasts.
# WHAT:   1. installs tvchat-backend, tvcal-backend, tvmail-backend and tvfloyd.py on cpbee (old ones kept as .bak.<time>);
#         2. restarts tvchat-floyd (its Floyd is away a few seconds) and checks on cpbee that the gate answers the hub
#            ops (publishes a probe note, reads it back);
#         3. from the laptop: the same check over the LAN, with the token your tvcal.conf already has.
# CHANGES on cpbee: /usr/local/bin/{tvchat,tvcal,tvmail}-backend, /usr/local/share/{tvchat,tvcal,tvmail}/tvfloyd.py.
#         No config change, no new port.  The hub keeps its events in memory only.
# UNDO:   copy the .bak.<time> files back and `sudo systemctl restart tvchat-floyd`.
# SAFE:   re-runnable.  Moves itself to DONE/ only on success.  DRY_RUN=1 shows the plan.
set -euo pipefail

HOST=cpbee
LAN=192.168.10.1
PORT=7077
repo=/home/chris/gh_cp/TUIs
here=$(cd "$(dirname "$0")" && pwd)
name=$(basename "$0")
say() { printf '%s\n' "$*"; }
die() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }

if [[ ${DRY_RUN:-0} == 1 ]]; then
  say "DRY RUN: would copy the three backends and tvfloyd.py to $HOST, install them (backups kept), restart tvchat-floyd,"
  say "check the hub ops on cpbee and from here. Nothing was changed."
  exit 0
fi

if [[ ${RUN_ON_REMOTE:-0} != 1 && $(hostname -s) != "$HOST" ]]; then
  for f in tvchat/backend/tvchat-backend tvcal/backend/tvcal-backend tvmail/backend/tvmail-backend tvcommon/py/tvfloyd.py; do
    [[ -f $repo/$f ]] || die "$repo/$f is not here"
  done
  grep -q "^class EventHub" "$repo/tvcommon/py/tvfloyd.py" || die "the repo's tvfloyd.py has no EventHub (commit first?)"
  ssh -o BatchMode=yes "$HOST" true || die "cannot ssh to $HOST"
  ssh "$HOST" 'rm -rf /tmp/floyd-hub && mkdir -m 700 /tmp/floyd-hub'
  scp -q "$here/$name" "$repo/tvchat/backend/tvchat-backend" "$repo/tvcal/backend/tvcal-backend" \
        "$repo/tvmail/backend/tvmail-backend" "$repo/tvcommon/py/tvfloyd.py" "$HOST:/tmp/floyd-hub/"
  rc=0
  ssh -t -o ServerAliveInterval=10 "$HOST" \
    "RUN_ON_REMOTE=1 bash /tmp/floyd-hub/$name; rc=\$?; rm -rf /tmp/floyd-hub; exit \$rc" || rc=$?
  [[ $rc == 0 ]] || die "it did not finish on $HOST (exit $rc); paste the output above to Claude."

  say ""
  say "step 3/3: from here, over the LAN ..."
  token=$(sed -n 's/^token *= *//p' "$HOME/.config/tvcal/tvcal.conf" | head -1)   # the file, not ssh: no terminal title noise
  [[ -n $token ]] || die "no token = line in ~/.config/tvcal/tvcal.conf"
  py=$(command -v python3 || command -v python) || die "no python on this machine"
  FLOYD_TOKEN=$token "$py" - "$repo/tvcommon/py/tvfloyd.py" "$LAN:$PORT" <<'PYEOF' || die "the hub did not answer from the laptop"
import importlib.util, os, sys
s = importlib.util.spec_from_file_location("tvfloyd", sys.argv[1])
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
cfg = {"server": sys.argv[2], "token": os.environ["FLOYD_TOKEN"], "timeout": 3.0}
r = m.master_call(cfg, {"op": "events", "since": None, "machine": "probe-laptop"})
assert r and r.get("ok") and "events" in r, "events: %r" % (r,)
p = m.master_call(cfg, {"op": "publish", "text": "hub probe from the laptop", "to": "probe-other", "machine": "probe-laptop", "ttl": 60})
assert p and p.get("ok"), "publish: %r" % (p,)
back = m.master_call(cfg, {"op": "events", "since": r["seq"], "machine": "probe-other"})
assert back and [e["text"] for e in back["events"]] == ["hub probe from the laptop"], "read back: %r" % (back,)
print("  publish and events: ok (seq %s)" % back["seq"])
PYEOF
  mv -- "$here/$name" "$here/DONE/$name" 2>/dev/null || true
  say ""
  say "Done.  Machines get toasts once their relay runs: it starts by itself when tvcal / tvmail / tvchat start with"
  say "[floyd] server set.  Try, in F7 on one machine:   !send cmdesktop hello from the laptop"
  say "(use that machine's name; [floyd] machine = NAME in the conf, default its host name)."
  exit 0
fi

# ------------------------------------------------------------------ on cpbee ---
D=/tmp/floyd-hub
stamp=$(date +%Y%m%d-%H%M%S)

say "step 1/3: installing the programs (sudo password) ..."
for f in tvchat-backend tvcal-backend tvmail-backend; do
  if [[ -f /usr/local/bin/$f ]] && ! cmp -s "$D/$f" "/usr/local/bin/$f"; then
    sudo cp -a "/usr/local/bin/$f" "/usr/local/bin/$f.bak.$stamp"
  fi
  sudo install -m 755 "$D/$f" "/usr/local/bin/$f"
done
for dir in /usr/local/share/tvchat /usr/local/share/tvcal /usr/local/share/tvmail; do
  sudo install -d -m 755 "$dir"
  if [[ -f $dir/tvfloyd.py ]] && ! cmp -s "$D/tvfloyd.py" "$dir/tvfloyd.py"; then
    sudo cp -a "$dir/tvfloyd.py" "$dir/tvfloyd.py.bak.$stamp"
  fi
  sudo install -m 644 "$D/tvfloyd.py" "$dir/tvfloyd.py"
done
python3 -c "
import importlib.util, sys
s = importlib.util.spec_from_file_location('tvfloyd', '/usr/local/share/tvchat/tvfloyd.py')
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
sys.exit(0 if hasattr(m, 'EventHub') and hasattr(m, 'FloydGate') else 1)" || die "the installed tvfloyd.py has no EventHub"
/usr/local/bin/tvchat-backend --help >/dev/null 2>&1 || die "tvchat-backend does not start"
for b in tvcal-backend tvmail-backend; do
  out=$(XDG_DATA_HOME=$(mktemp -d) /usr/local/bin/$b floyd '!about' 2>&1) || die "$b floyd failed: $out"
  grep -q "B-19-7" <<<"$out" || die "$b did not answer !about: $out"
done
say "  programs ok"

say "step 2/3: restarting tvchat-floyd ..."
sudo systemctl restart tvchat-floyd
sleep 6
systemctl is-active tvchat-floyd || { sudo journalctl -u tvchat-floyd -n 25 --no-pager; die "tvchat-floyd is not running; the journal is above"; }
ss -ltn | grep -q "$LAN:$PORT" || { sudo journalctl -u tvchat-floyd -n 25 --no-pager; die "nothing listens on $LAN:$PORT; the journal is above"; }
python3 - "/usr/local/share/tvchat/tvfloyd.py" "$LAN:$PORT" "$(cat "$HOME/floyd-gate.token")" <<'PYEOF' || die "the hub does not answer on cpbee itself"
import importlib.util, sys
s = importlib.util.spec_from_file_location("tvfloyd", sys.argv[1])
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
cfg = {"server": sys.argv[2], "token": sys.argv[3], "timeout": 3.0}
r = m.master_call(cfg, {"op": "events", "since": None, "machine": "probe-cpbee"})
assert r and r.get("ok") and "events" in r, "events: %r" % (r,)
p = m.master_call(cfg, {"op": "publish", "text": "hub probe on cpbee", "to": "probe-x", "machine": "probe-cpbee", "ttl": 60})
assert p and p.get("ok"), "publish: %r" % (p,)
print("  hub ops: ok")
PYEOF
sudo journalctl -u tvchat-floyd -n 4 --no-pager || true
