#!/bin/bash
# RUN ON: the laptop (Cygwin).  It copies files to cpbee and runs the root part there with a terminal, so sudo asks
#         for your password once.  Takes about a minute.  Run it INSTEAD of floyd-voice-on-cpbee.sh (it installs the
#         same files, newer).
# WHY:    tvmail, tvcal and tvchat should each have a local Floyd AND a link to the master Floyd on cpbee
#         (tvcommon/FLOYD-MASTER.md).  The master's door is the "gate": a small listener inside tvchat-floyd.
# WHAT:   1. installs tvchat-backend, tvcal-backend, tvmail-backend (/usr/local/bin) and tvfloyd.py
#            (/usr/local/share/{tvchat,tvcal,tvmail}); old files kept as .bak.<time>;
#         2. makes the shared secret /etc/tvchat/floyd-gate.token (root:floyd 640; kept if it exists) and a copy for you
#            in ~/floyd-gate.token on cpbee (mode 600);
#         3. adds gate / gate_listen / gate_token_file to [account:floyd] in /etc/tvchat/floyd.conf (backup kept);
#         4. restarts tvchat-floyd, checks that it listens on 192.168.10.1:7077 and answers (!about, hello);
#         5. from the laptop: asks the gate over the LAN with the repo's client, then prints the [floyd] lines for
#            tvcal.conf and tvmail.conf.  It changes nothing on the laptop.
# CHANGES on cpbee: the files above, /etc/tvchat/floyd.conf, /etc/tvchat/floyd-gate.token, ~/floyd-gate.token.
#         Floyd's memory files are not touched.  Listens on the LAN address only; wrong tokens are refused and logged
#         (journalctl -u tvchat-floyd).
# UNDO:   remove the three gate lines from /etc/tvchat/floyd.conf (or copy back floyd.conf.bak.<time>) and
#         `sudo systemctl restart tvchat-floyd`; the .bak.<time> program files restore the old code.
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
  say "DRY RUN: would copy the three backends and tvfloyd.py to $HOST, install them (backups kept), create the gate token,"
  say "add gate = $PORT (listening on $LAN) to /etc/tvchat/floyd.conf, restart tvchat-floyd, test the gate, print client lines."
  say "Nothing was changed."
  exit 0
fi

if [[ ${RUN_ON_REMOTE:-0} != 1 && $(hostname -s) != "$HOST" ]]; then
  for f in tvchat/backend/tvchat-backend tvcal/backend/tvcal-backend tvmail/backend/tvmail-backend tvcommon/py/tvfloyd.py; do
    [[ -f $repo/$f ]] || die "$repo/$f is not here"
  done
  grep -q "^class FloydGate" "$repo/tvcommon/py/tvfloyd.py" || die "the repo's tvfloyd.py has no FloydGate (pull/commit first?)"
  lanport=/home/chris/gh_cp/POSIX/net-utils/lan-port
  [[ -f $lanport ]] || die "$lanport is not here (POSIX/net-utils)"
  ssh -o BatchMode=yes "$HOST" true || die "cannot ssh to $HOST"
  ssh "$HOST" 'rm -rf /tmp/floyd-gate && mkdir -m 700 /tmp/floyd-gate'
  scp -q "$here/$name" "$repo/tvchat/backend/tvchat-backend" "$repo/tvcal/backend/tvcal-backend" \
        "$repo/tvmail/backend/tvmail-backend" "$repo/tvcommon/py/tvfloyd.py" "$lanport" "$HOST:/tmp/floyd-gate/"
  rc=0
  ssh -t -o ServerAliveInterval=10 "$HOST" \
    "RUN_ON_REMOTE=1 bash /tmp/floyd-gate/$name; rc=\$?; rm -rf /tmp/floyd-gate; exit \$rc" || rc=$?
  [[ $rc == 0 ]] || die "it did not finish on $HOST (exit $rc); paste the output above to Claude."

  say ""
  say "step 5/5: from here, over the LAN ..."
  token=$(ssh "$HOST" 'cat ~/floyd-gate.token') || die "cannot read ~/floyd-gate.token on $HOST"
  py=$(command -v python3 || command -v python) || die "no python on this machine"
  FLOYD_TOKEN=$token "$py" - "$repo/tvcommon/py/tvfloyd.py" "$LAN:$PORT" <<'PYEOF' || die "the gate did not answer from the laptop (firewall?)"
import importlib.util, os, sys
s = importlib.util.spec_from_file_location("tvfloyd", sys.argv[1])
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
cfg = {"server": sys.argv[2], "token": os.environ["FLOYD_TOKEN"], "timeout": 3.0}
r = m.master_call(cfg, {"op": "hello"})
assert r and r.get("ok"), "hello: %r" % (r,)
print("  hello: ok (%s quotes, %s facts)" % (r.get("quotes"), r.get("facts")))
print("  !about: " + " / ".join(m.persona("!about", "chatty", path=None, master=cfg) or ["?"])[:100])
bad = m.master_call(dict(cfg, token="wrong"), {"op": "hello"})
assert bad and not bad.get("ok"), "a wrong token must be refused: %r" % (bad,)
print("  wrong token: refused, as it should be")  # allowlist secret
PYEOF
  mv -- "$here/$name" "$here/DONE/$name" 2>/dev/null || true
  say ""
  say "Done.  To switch the apps on, put this in the [floyd] section of tvcal.conf and tvmail.conf"
  say "(and nothing else changes; without it, or when cpbee is away, the local Floyd answers):"
  say ""
  say "    server = $HOST"
  say "    token  = $token"
  say ""
  say "Then in F7: !status should say \"Master Floyd: $HOST:$PORT answers\"."
  exit 0
fi

# ------------------------------------------------------------------ on cpbee ---
D=/tmp/floyd-gate
CONF=/etc/tvchat/floyd.conf
TOKEN=/etc/tvchat/floyd-gate.token  # allowlist secret: a path
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
sys.exit(0 if hasattr(m, 'FloydGate') and hasattr(m, 'persona') else 1)" || die "the installed tvfloyd.py has no FloydGate"
/usr/local/bin/tvchat-backend --help >/dev/null 2>&1 || die "tvchat-backend does not start"
for b in tvcal-backend tvmail-backend; do
  out=$(XDG_DATA_HOME=$(mktemp -d) /usr/local/bin/$b floyd '!about' 2>&1) || die "$b floyd failed: $out"
  grep -q "B-19-7" <<<"$out" || die "$b did not answer !about: $out"
done
say "  programs ok"

say "step 2/4: the shared secret ..."
[[ -f $CONF ]] || die "$CONF is not here (is Floyd set up on this machine?)"
if [[ ! -s $TOKEN ]]; then
  openssl rand -hex 16 | sudo tee "$TOKEN" >/dev/null
fi
sudo chown root:floyd "$TOKEN" && sudo chmod 640 "$TOKEN"
sudo cat "$TOKEN" > "$HOME/floyd-gate.token"
chmod 600 "$HOME/floyd-gate.token"
say "  $TOKEN (root:floyd 640); a copy for you in ~/floyd-gate.token"

say "step 3/4: the gate lines in $CONF ..."
if sudo grep -q '^gate *=' "$CONF"; then
  say "  already there; left alone"
else
  sudo cp -a "$CONF" "$CONF.bak.$stamp"
  sudo python3 - "$CONF" "$LAN" "$PORT" "$TOKEN" <<'PYEOF'
import sys
path, lan, port, tok = sys.argv[1:5]
lines = open(path, encoding="utf-8").read().split("\n")
out, inside, done = [], False, False
for l in lines:
    out.append(l)
    if l.strip().startswith("["):
        inside = l.strip() == "[account:floyd]"
    elif inside and not done and l.strip().startswith("protocol") and "floyd" in l:
        out += ["gate = %s" % port, "gate_listen = %s" % lan, "gate_token_file = %s" % tok]
        done = True
if not done:
    sys.exit("no [account:floyd] with protocol = floyd in " + path)
open(path, "w", encoding="utf-8").write("\n".join(out))
PYEOF
  say "  added (backup: $CONF.bak.$stamp)"
fi

say "  opening $PORT to the LAN with lan-port (the firewall tool: lists it, reloads once, confirms it is open) ..."
bash "$D/lan-port" add "$PORT" "Floyd gate (tvchat-floyd; tvcal/tvmail ask the master Floyd)" || die "lan-port add $PORT failed"

say "step 4/4: restarting tvchat-floyd ..."
sudo systemctl restart tvchat-floyd
sleep 6
systemctl is-active tvchat-floyd || { sudo journalctl -u tvchat-floyd -n 25 --no-pager; die "tvchat-floyd is not running; the journal is above"; }
ss -ltn | grep -q "$LAN:$PORT" || { sudo journalctl -u tvchat-floyd -n 25 --no-pager; die "nothing listens on $LAN:$PORT; the journal is above"; }
python3 - "/usr/local/share/tvchat/tvfloyd.py" "$LAN:$PORT" "$(cat "$HOME/floyd-gate.token")" <<'PYEOF' || die "the gate does not answer on cpbee itself"
import importlib.util, sys
s = importlib.util.spec_from_file_location("tvfloyd", sys.argv[1])
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
cfg = {"server": sys.argv[2], "token": sys.argv[3], "timeout": 3.0}
r = m.master_call(cfg, {"op": "hello"})
assert r and r.get("ok"), "hello: %r" % (r,)
print("  gate answers: %s quotes, %s facts" % (r.get("quotes"), r.get("facts")))
PYEOF
sudo journalctl -u tvchat-floyd -n 4 --no-pager || true
