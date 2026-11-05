#!/bin/bash
# RUN ON: the laptop (Cygwin).  Copies files to cpbee and runs the root part there with a terminal (sudo asks once).
# WHY:    phase 2 of the master Floyd (tvcommon/FLOYD-MASTER.md, part 2): Floyd watches the server and the outside
#         hosts its mail depends on, and every machine's toasts say when something goes down or comes back, when
#         the mail queue is stuck, when the disk is nearly full, and when cpbee is about to shut down or reboot.
# WHAT:   1. installs tvchat-backend, tvcal-backend, tvmail-backend and tvfloyd.py on cpbee (old ones kept as .bak.<time>);
#         2. adds `sentinel = yes` to [account:floyd] in /etc/tvchat/floyd.conf (backup kept; nothing else changes - the
#            defaults watch ergo, talkd, dovecot, postfix, radicale, u-l.ca:465 and :993, the mail queue and the disk);
#         3. restarts tvchat-floyd (his IRC presence drops for a few seconds) and checks that it runs and the gate
#            answers, and that the journal shows no sentinel error;
#         4. from the laptop: the hub still answers over the LAN.
# CHANGES on cpbee: the files above and /etc/tvchat/floyd.conf.  Nothing is stopped or started except tvchat-floyd.
# UNDO:   remove the sentinel line from /etc/tvchat/floyd.conf (or copy floyd.conf.bak.<time> back) and
#         `sudo systemctl restart tvchat-floyd`.
# TRY IT: on cpbee `sudo systemctl stop radicale; sleep 75; sudo systemctl start radicale` - after about a minute every
#         machine with a relay shows "calendar server (Radicale) is down", and "... is back" after the start.
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
  say "DRY RUN: would copy the three backends and tvfloyd.py to $HOST, install them (backups kept), add sentinel = yes to"
  say "/etc/tvchat/floyd.conf, restart tvchat-floyd and check it. Nothing was changed."
  exit 0
fi

if [[ ${RUN_ON_REMOTE:-0} != 1 && $(hostname -s) != "$HOST" ]]; then
  for f in tvchat/backend/tvchat-backend tvcal/backend/tvcal-backend tvmail/backend/tvmail-backend tvcommon/py/tvfloyd.py; do
    [[ -f $repo/$f ]] || die "$repo/$f is not here"
  done
  grep -q "^class Sentinel" "$repo/tvcommon/py/tvfloyd.py" || die "the repo's tvfloyd.py has no Sentinel (commit first?)"
  ssh -o BatchMode=yes "$HOST" true || die "cannot ssh to $HOST"
  ssh "$HOST" 'rm -rf /tmp/floyd-sentinel && mkdir -m 700 /tmp/floyd-sentinel'
  scp -q "$here/$name" "$repo/tvchat/backend/tvchat-backend" "$repo/tvcal/backend/tvcal-backend" \
        "$repo/tvmail/backend/tvmail-backend" "$repo/tvcommon/py/tvfloyd.py" "$HOST:/tmp/floyd-sentinel/"
  rc=0
  ssh -t -o ServerAliveInterval=10 "$HOST" \
    "RUN_ON_REMOTE=1 bash /tmp/floyd-sentinel/$name; rc=\$?; rm -rf /tmp/floyd-sentinel; exit \$rc" || rc=$?
  [[ $rc == 0 ]] || die "it did not finish on $HOST (exit $rc); paste the output above to Claude."

  say ""
  say "step 4/4: from here, over the LAN ..."
  token=$(sed -n 's/^token *= *//p' "$HOME/.config/tvcal/tvcal.conf" | head -1)   # the file, not ssh: no terminal title noise
  [[ -n $token ]] || die "no token = line in ~/.config/tvcal/tvcal.conf"
  py=$(command -v python3 || command -v python) || die "no python on this machine"
  FLOYD_TOKEN=$token "$py" - "$repo/tvcommon/py/tvfloyd.py" "$LAN:$PORT" <<'PYEOF' || die "the hub did not answer from the laptop"
import importlib.util, os, sys
s = importlib.util.spec_from_file_location("tvfloyd", sys.argv[1])
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
cfg = {"server": sys.argv[2], "token": os.environ["FLOYD_TOKEN"], "timeout": 3.0}
r = m.master_call(cfg, {"op": "hello"})
assert r and r.get("ok") and "fortune" in r, "hello: %r" % (r,)
e = m.master_call(cfg, {"op": "events", "since": None, "machine": "probe-laptop"})
assert e and e.get("ok"), "events: %r" % (e,)
print("  hub answers; still-true events right now: %s" % ([x["text"] for x in e["events"]] or "none (all is well)"))
PYEOF
  mv -- "$here/$name" "$here/DONE/$name" 2>/dev/null || true
  say ""
  say "Done.  Floyd now watches the server.  To see it work (on cpbee):"
  say "    sudo systemctl stop radicale; sleep 75; sudo systemctl start radicale"
  say "A toast should appear in tvcal / tvmail / tvchat on every machine whose relay is running."
  exit 0
fi

# ------------------------------------------------------------------ on cpbee ---
D=/tmp/floyd-sentinel
CONF=/etc/tvchat/floyd.conf
stamp=$(date +%Y%m%d-%H%M%S)

say "step 1/4: installing the programs (sudo password) ..."
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
sys.exit(0 if hasattr(m, 'Sentinel') and hasattr(m, 'EventHub') else 1)" || die "the installed tvfloyd.py has no Sentinel"
/usr/local/bin/tvchat-backend --help >/dev/null 2>&1 || die "tvchat-backend does not start"
for b in tvcal-backend tvmail-backend; do
  out=$(XDG_DATA_HOME=$(mktemp -d) /usr/local/bin/$b floyd '!about' 2>&1) || die "$b floyd failed: $out"
  grep -q "B-19-7" <<<"$out" || die "$b did not answer !about: $out"
done
say "  programs ok"

say "step 2/4: sentinel = yes in $CONF ..."
[[ -f $CONF ]] || die "$CONF is not here"
sudo grep -q '^gate *=' "$CONF" || die "$CONF has no gate = line: run floyd-gate-on-cpbee.sh first"
if sudo grep -q '^sentinel *=' "$CONF"; then
  say "  already there; left alone"
else
  sudo cp -a "$CONF" "$CONF.bak.$stamp"
  sudo python3 - "$CONF" <<'PYEOF'
import sys
path = sys.argv[1]
lines = open(path, encoding="utf-8").read().split("\n")
out, inside, done = [], False, False
for l in lines:
    out.append(l)
    if l.strip().startswith("["):
        inside = l.strip() == "[account:floyd]"
    elif inside and not done and l.strip().startswith("gate ") and "=" in l and not l.strip().startswith("gate_"):
        out.append("sentinel = yes")
        done = True
if not done:
    sys.exit("no gate = line in [account:floyd] of " + path)
open(path, "w", encoding="utf-8").write("\n".join(out))
PYEOF
  say "  added (backup: $CONF.bak.$stamp)"
fi

say "step 3/4: restarting tvchat-floyd ..."
sudo systemctl restart tvchat-floyd
sleep 8
systemctl is-active tvchat-floyd || { sudo journalctl -u tvchat-floyd -n 25 --no-pager; die "tvchat-floyd is not running; the journal is above"; }
ss -ltn | grep -q "$LAN:$PORT" || { sudo journalctl -u tvchat-floyd -n 25 --no-pager; die "nothing listens on $LAN:$PORT; the journal is above"; }
python3 - "/usr/local/share/tvchat/tvfloyd.py" "$LAN:$PORT" "$(cat "$HOME/floyd-gate.token")" <<'PYEOF' || die "the gate does not answer on cpbee itself"
import importlib.util, sys
s = importlib.util.spec_from_file_location("tvfloyd", sys.argv[1])
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
cfg = {"server": sys.argv[2], "token": sys.argv[3], "timeout": 3.0}
r = m.master_call(cfg, {"op": "hello"})
assert r and r.get("ok"), "hello: %r" % (r,)
print("  gate answers (%s quotes, %s facts)" % (r.get("quotes"), r.get("facts")))
PYEOF
sleep 20     # the first sentinel round is after 10 s
if sudo journalctl -u tvchat-floyd --since "-40s" --no-pager | grep -qiE "sentinel: checks failed|Traceback"; then
  sudo journalctl -u tvchat-floyd -n 30 --no-pager; die "the sentinel reported an error; the journal is above"
fi
say "  no sentinel error in the journal"
sudo journalctl -u tvchat-floyd -n 6 --no-pager || true
