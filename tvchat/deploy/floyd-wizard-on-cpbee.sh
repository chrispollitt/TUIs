#!/bin/bash
# RUN ON: the laptop (Cygwin).  Copies files to cpbee and runs the root part there with a terminal (sudo asks once).
# WHY:    Floyd as a setup wizard (`!setup` in any Floyd window): a new machine needs only the master's name and token, then
#         Floyd compares it with what the master knows and fixes the difference.  And a second Delta Chat device is offered
#         through the master (no code to copy).  The master needs the new code and a "manifest" of the setup.
# WHAT:   1. builds the manifest here from your WORKING configs (the reference): tvcal's calendar URL and user, tvchat's
#            LAN group chat and talk accounts, the Delta Chat account (chris@cpbee.cmnet), and the two public CA
#            certificates; no passwords anywhere in it;
#         2. installs tvchat-backend, tvcal-backend, tvmail-backend, tvfloyd.py on cpbee (old ones kept as .bak.<time>);
#         3. installs the manifest as /etc/tvchat/floyd-setup.json and the certificates as /etc/tvchat/floyd-ca-*.pem
#            (root:floyd, 640 / 644);
#         4. restarts tvchat-floyd (briefly away from IRC) and checks the `setup` answer on cpbee and from here;
#         5. shows what `!setup` would say on THIS machine (a read-only look; it changes nothing).
# CHANGES on cpbee: the programs, the three files above.  No config of Floyd itself changes.
# UNDO:   sudo rm /etc/tvchat/floyd-setup.json /etc/tvchat/floyd-ca-*.pem; the .bak.<time> files restore the old programs.
# SAFE:   re-runnable (rebuilds the manifest each time).  Moves itself to DONE/ only on success.  DRY_RUN=1 shows the plan.
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
  say "DRY RUN: would build the manifest from ~/.config/tvcal and ~/.config/tvchat, install it with the certificates and the new"
  say "programs on $HOST, restart tvchat-floyd, check the setup answer. Nothing was changed."
  exit 0
fi

if [[ ${RUN_ON_REMOTE:-0} != 1 && $(hostname -s) != "$HOST" ]]; then
  for f in tvchat/backend/tvchat-backend tvcal/backend/tvcal-backend tvmail/backend/tvmail-backend tvcommon/py/tvfloyd.py; do
    [[ -f $repo/$f ]] || die "$repo/$f is not here"
  done
  grep -q "^def wizard_command" "$repo/tvcommon/py/tvfloyd.py" || die "the repo's tvfloyd.py has no wizard (commit first?)"
  cal=$HOME/.config/tvcal/tvcal.conf; chatc=$HOME/.config/tvchat/tvchat.conf
  [[ -f $cal && -f $chatc ]] || die "need $cal and $chatc (the reference configs)"
  ssh -o BatchMode=yes "$HOST" true || die "cannot ssh to $HOST"

  say "step 1/5: the manifest, from your working configs ..."
  tmp=$(mktemp -d)
  py=$(command -v python3 || command -v python) || die "no python here"
  "$py" - "$cal" "$chatc" "$tmp" <<'PYEOF' || die "could not build the manifest"
import configparser, json, os, sys, shutil
cal, chatc, out = sys.argv[1:4]
c = configparser.ConfigParser(interpolation=None, strict=False)
c.read(cal, encoding="utf-8")
url, user, ca_cal = c.get("server", "url"), c.get("server", "user", fallback=""), os.path.expanduser(c.get("server", "ca", fallback=""))
t = configparser.ConfigParser(interpolation=None, strict=False)
t.read(chatc, encoding="utf-8")
accounts = []
ca_chat = ""
for ident, proto in (("cpbee_chat", "irc"), ("cpbee_talk", "talk")):
    sect = "account:" + ident
    if not t.has_section(sect):
        continue
    opts = dict(t.items(sect))
    for k in ("protocol", "display"):
        opts.pop(k, None)
    if opts.get("cafile"):
        ca_chat = os.path.expanduser(opts["cafile"])
        opts["cafile"] = "@CA:chat@"
    accounts.append({"id": ident, "protocol": t.get(sect, "protocol"), "display": t.get(sect, "display", fallback=ident),
                     "autoconnect": True, "opts": opts})
accounts.append({"id": "delta_chris", "protocol": "deltachat", "display": "Delta Chat (chris)", "autoconnect": True,
                 "opts": {"addr": "chris@cpbee.cmnet", "imap_user": "chris", "imap_server": "cpbee", "smtp_user": "chris",
                          "smtp_server": "cpbee", "smtp_port": "587", "verify": "false", "rpc_server": "@RPC@"}})
cas = {}
for key, src in (("cal", ca_cal), ("chat", ca_chat)):
    if src and os.path.isfile(src):
        shutil.copyfile(src, os.path.join(out, "floyd-ca-%s.pem" % key))
        cas[key] = {"file": "/etc/tvchat/floyd-ca-%s.pem" % key, "name": "cpbee-ca.pem"}
manifest = {"version": 1, "calendar": {"url": url, "user": user}, "cas": cas, "delta_owner": "cpbee", "accounts": accounts}
with open(os.path.join(out, "floyd-setup.json"), "w", encoding="utf-8") as fh:
    json.dump(manifest, fh, indent=1)
for a in accounts:                      # a manifest never carries a secret: refuse if any key looks like one  # allowlist secret: not a secret
    for k in a["opts"]:
        if "pass" in k.lower():
            sys.exit("a password-like key (%s) is in the manifest: refusing" % k)
print("  calendar %s (user %s); accounts: %s; certificates: %s" % (url, user, ", ".join(a["id"] for a in accounts), ", ".join(cas) or "none"))
PYEOF

  ssh "$HOST" 'rm -rf /tmp/floyd-wizard && mkdir -m 700 /tmp/floyd-wizard'
  scp -q "$here/$name" "$repo/tvchat/backend/tvchat-backend" "$repo/tvcal/backend/tvcal-backend" \
        "$repo/tvmail/backend/tvmail-backend" "$repo/tvcommon/py/tvfloyd.py" "$tmp"/floyd-setup.json "$tmp"/floyd-ca-*.pem \
        "$HOST:/tmp/floyd-wizard/"
  rm -rf "$tmp"
  rc=0
  ssh -t -o ServerAliveInterval=10 "$HOST" \
    "RUN_ON_REMOTE=1 bash /tmp/floyd-wizard/$name; rc=\$?; rm -rf /tmp/floyd-wizard; exit \$rc" || rc=$?
  [[ $rc == 0 ]] || die "it did not finish on $HOST (exit $rc); paste the output above to Claude."

  say ""
  say "step 5/5: from here, over the LAN, and what !setup says on this machine (read-only) ..."
  token=$(sed -n 's/^token *= *//p' "$HOME/.config/tvcal/tvcal.conf" | head -1)   # the file, not ssh: no terminal title noise
  [[ -n $token ]] || die "no token = line in ~/.config/tvcal/tvcal.conf"
  FLOYD_TOKEN=$token "$py" - "$repo/tvcommon/py/tvfloyd.py" "$LAN:$PORT" <<'PYEOF' || die "the setup answer did not come from the laptop"
import importlib.util, os, sys
s = importlib.util.spec_from_file_location("tvfloyd", sys.argv[1])
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
cfg = m.master_settings(lambda k, d: {"server": sys.argv[2], "token": os.environ["FLOYD_TOKEN"]}.get(k, d))
r = m.master_call(cfg, {"op": "setup"})
assert r and r.get("ok") and r.get("manifest"), "setup: %r" % (r,)
print("  the master serves its setup: calendar %s, %d accounts, %d certificates" % (
    r["manifest"]["calendar"]["url"], len(r["manifest"]["accounts"]), len(r.get("cas") or {})))
print()
for line in m.wizard_command("!setup", "tvcal", cfg):          # a report only: no number, no 'all', so nothing is written
    print("  " + line)
PYEOF
  mv -- "$here/$name" "$here/DONE/$name" 2>/dev/null || true
  say ""
  say "Done.  In F7 (tvcal or tvmail) on any machine:  !setup   - and  !setup all  to let Floyd fix what he can."
  say "On a brand-new machine:  !setup join cpbee TOKEN   (once)."
  exit 0
fi

# ------------------------------------------------------------------ on cpbee ---
D=/tmp/floyd-wizard
stamp=$(date +%Y%m%d-%H%M%S)

say "step 2/5: installing the programs (sudo password) ..."
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
sys.exit(0 if hasattr(m, 'wizard_command') and hasattr(m, 'Sentinel') else 1)" || die "the installed tvfloyd.py has no wizard"
/usr/local/bin/tvchat-backend --help >/dev/null 2>&1 || die "tvchat-backend does not start"
for b in tvcal-backend tvmail-backend; do
  out=$(XDG_DATA_HOME=$(mktemp -d) /usr/local/bin/$b floyd '!about' 2>&1) || die "$b floyd failed: $out"
  grep -q "B-19-7" <<<"$out" || die "$b did not answer !about: $out"
done
say "  programs ok"

say "step 3/5: the manifest and the certificates ..."
sudo install -d -m 755 /etc/tvchat
for f in "$D"/floyd-setup.json; do sudo install -m 640 -o root -g floyd "$f" /etc/tvchat/floyd-setup.json; done
for f in "$D"/floyd-ca-*.pem; do [[ -f $f ]] && sudo install -m 644 -o root -g floyd "$f" "/etc/tvchat/$(basename "$f")"; done
ls -l /etc/tvchat/floyd-setup.json /etc/tvchat/floyd-ca-*.pem

say "step 4/5: restarting tvchat-floyd ..."
sudo systemctl restart tvchat-floyd
sleep 8
systemctl is-active tvchat-floyd || { sudo journalctl -u tvchat-floyd -n 25 --no-pager; die "tvchat-floyd is not running; the journal is above"; }
ss -ltn | grep -q "$LAN:$PORT" || { sudo journalctl -u tvchat-floyd -n 25 --no-pager; die "nothing listens on $LAN:$PORT; the journal is above"; }
python3 - "/usr/local/share/tvchat/tvfloyd.py" "$LAN:$PORT" "$(cat "$HOME/floyd-gate.token")" <<'PYEOF' || die "the setup answer does not work on cpbee itself"
import importlib.util, sys
s = importlib.util.spec_from_file_location("tvfloyd", sys.argv[1])
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
cfg = {"server": sys.argv[2], "token": sys.argv[3], "timeout": 3.0}
r = m.master_call(cfg, {"op": "setup"})
assert r and r.get("ok"), "setup: %r" % (r,)
print("  setup answers: %d accounts, certificates: %s" % (len(r["manifest"]["accounts"]), ", ".join(r.get("cas") or {}) or "none"))
PYEOF
sudo journalctl -u tvchat-floyd -n 3 --no-pager || true
