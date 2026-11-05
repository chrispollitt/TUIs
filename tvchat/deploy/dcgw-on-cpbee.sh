#!/bin/bash
# RUN ON: the laptop (Cygwin).  Copies files to cpbee and runs the root part there with a terminal (sudo asks once).
# WHY:    Chris decided (2026-11-04): ONE Delta Chat account/device per person, on cpbee ("the one server to rule them all");
#         tvchat on the other machines is a thin client with a token - no key, no database, no mail login there.
#         dcgw (tvchat/backend/dcgw) is the gateway; this installs it and moves Chris's account onto it.
# WHAT:   1. the system user dcgw; /usr/local/bin/dcgw; the official Delta Chat core in /opt/dcgw/venv (pip, needs internet);
#         2. a TLS certificate /etc/dcgw/cert.pem + key (made if missing; names cpbee, cpbee.cmnet, 192.168.10.1);
#         3. /etc/dcgw/dcgw.conf (root:dcgw 640) with [user:chris]; his token goes to ~/dcgw-chris.token on cpbee (mode 600);
#         4. ADOPTS the Delta Chat account tvchat already has on cpbee (~/.local/share/tvchat/deltachat/delta_chris): its key,
#            chats and mail login are copied to /var/lib/dcgw/chris (the original is left in place).  Quit tvchat on cpbee first;
#         5. dcgw.service (enabled, started), the firewall port 7078 to the LAN (POSIX/net-utils/lan-port);
#         6. Floyd: the public certificate and the new Delta Chat account for the setup wizard (floyd-setup.json), and dcgw in
#            the sentinel's service list; tvchat-floyd restarts briefly;
#         7. cpbee's own tvchat.conf: delta_chris becomes a gateway account (the old file is kept in ~/.config/tvchat/BAK);
#         8. checks the gateway over TLS on cpbee and from here;
#         9. then, on this machine and the machines in CLIENTS: Floyd's wizard (`!setup all`, `!setup delta TOKEN`) adds the
#            gateway account to tvchat.conf (backups kept).  The token travels over your ssh login, never on a command line.
# CHANGES on cpbee: the user, /opt/dcgw, /etc/dcgw, /var/lib/dcgw, /usr/local/bin/{dcgw,tvchat-backend,tvcal-backend,
#         tvmail-backend}, tvfloyd.py, the unit, a firewall port, /etc/tvchat/floyd-*, chris's tvchat.conf.  Passwords: none are
#         read or typed here.  If cpbee has no Delta Chat account to adopt, the script says how to set one up (dcgw setup).
# UNDO:   sudo systemctl disable --now dcgw; restore ~/.config/tvchat/BAK/tvchat.conf.<time>; the .bak.<time> program files.
# SAFE:   re-runnable.  Moves itself to DONE/ only on success.  DRY_RUN=1 shows the plan.
set -euo pipefail

HOST=cpbee
LAN=192.168.10.1
PORT=7078
CLIENTS="cmdesktop"                  # other machines to run the wizard on (this machine is always done)
repo=/home/chris/gh_cp/TUIs
here=$(cd "$(dirname "$0")" && pwd)
name=$(basename "$0")
say() { printf '%s\n' "$*"; }
die() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }

if [[ ${DRY_RUN:-0} == 1 ]]; then
  say "DRY RUN: would install dcgw on $HOST (user, core venv, certificate, config, unit, firewall port $PORT), adopt chris's"
  say "Delta Chat account from ~/.local/share/tvchat/deltachat/delta_chris, update Floyd's setup, switch cpbee's tvchat to the"
  say "gateway, then run the wizard on this machine and: $CLIENTS. Nothing was changed."
  exit 0
fi

if [[ ${RUN_ON_REMOTE:-0} != 1 && $(hostname -s) != "$HOST" ]]; then
  lanport=/home/chris/gh_cp/POSIX/net-utils/lan-port
  for f in tvchat/backend/dcgw tvchat/backend/tvchat-backend tvcal/backend/tvcal-backend tvmail/backend/tvmail-backend \
           tvcommon/py/tvfloyd.py tvchat/configure/dcgw.service; do
    [[ -f $repo/$f ]] || die "$repo/$f is not here"
  done
  [[ -f $lanport ]] || die "$lanport is not here (POSIX/net-utils)"
  grep -q "^class Gateway" "$repo/tvchat/backend/dcgw" || die "the repo's dcgw is not the gateway (commit first?)"
  ssh -o BatchMode=yes "$HOST" true || die "cannot ssh to $HOST"
  ssh "$HOST" 'rm -rf /tmp/dcgw-deploy && mkdir -m 700 /tmp/dcgw-deploy'
  scp -q "$here/$name" "$repo/tvchat/backend/dcgw" "$repo/tvchat/backend/tvchat-backend" "$repo/tvcal/backend/tvcal-backend" \
        "$repo/tvmail/backend/tvmail-backend" "$repo/tvcommon/py/tvfloyd.py" "$repo/tvchat/configure/dcgw.service" \
        "$lanport" "$HOST:/tmp/dcgw-deploy/"
  rc=0
  ssh -t -o ServerAliveInterval=10 "$HOST" \
    "RUN_ON_REMOTE=1 bash /tmp/dcgw-deploy/$name; rc=\$?; rm -rf /tmp/dcgw-deploy; exit \$rc" || rc=$?
  [[ $rc == 0 ]] || die "it did not finish on $HOST (exit $rc); paste the output above to Claude."

  say ""
  say "step 8/9: from here, over TLS ..."
  py=$(command -v python3 || command -v python) || die "no python on this machine"
  token=$(ssh "$HOST" 'cat ~/dcgw-chris.token') || die "cannot read ~/dcgw-chris.token on $HOST"
  ssh "$HOST" 'cat /etc/dcgw/cert.pem' > "$HOME/.dcgw-cert.tmp"
  DCGW_TOKEN=$token "$py" - "$HOME/.dcgw-cert.tmp" "$HOST" "$PORT" <<'PYEOF' || die "the gateway did not answer over TLS from here"
import os, socket, ssl, sys
ctx = ssl.create_default_context(cafile=sys.argv[1])
s = ctx.wrap_socket(socket.create_connection((sys.argv[2], int(sys.argv[3])), 8), server_hostname=sys.argv[2])
s.sendall(("DCGW 1 %s\n" % os.environ["DCGW_TOKEN"]).encode())
line = b""
while not line.endswith(b"\n"):
    ch = s.recv(1)
    if not ch:
        break
    line += ch
assert line.startswith(b"OK 1 chris"), "answer: %r" % line
print("  gateway answers over TLS (verified certificate): " + line.decode().strip())
PYEOF
  rm -f "$HOME/.dcgw-cert.tmp"

  say ""
  say "step 9/9: the wizard on the client machines (adds the gateway account to tvchat.conf; backups kept) ..."
  wiz=$(mktemp)
  cat > "$wiz" <<'PYEOF'
import configparser, importlib.util, os, sys
s = importlib.util.spec_from_file_location("tvfloyd", sys.argv[1])
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
token = sys.stdin.readline().strip()
got = None
for app in ("tvcal", "tvmail", "tvchat"):
    c = configparser.ConfigParser(interpolation=None, strict=False)
    c.read(os.path.expanduser("~/.config/%s/%s.conf" % (app, app)), encoding="utf-8")
    if c.has_option("floyd", "server"):
        got = m.master_settings(lambda k, d: c.get("floyd", k, fallback=d))
        break
if not got:
    print("  no [floyd] server in tvcal / tvmail / tvchat conf: say  !setup join cpbee TOKEN  in F7 first, then run this again")
    sys.exit(0)
for cmd in ("!setup all", "!setup delta " + token):
    for line in m.wizard_command(cmd, "tvcal", got):
        print("  " + line.replace(token, "(token)"))
PYEOF
  failed=0
  say "== this machine ($(hostname -s))"
  printf '%s\n' "$token" | "$py" "$wiz" "$repo/tvcommon/py/tvfloyd.py" || failed=1
  for h in $CLIENTS; do
    say "== $h"
    if ! ssh -o BatchMode=yes -o ConnectTimeout=8 "$h" true 2>/dev/null; then
      say "  cannot reach $h over ssh (key login): skipped.  There, in F7:  !setup all  then  !setup delta TOKEN  (your token is in ~/dcgw-chris.token on cpbee)"
      failed=1
      continue
    fi
    ssh -o BatchMode=yes "$h" 'mkdir -p /tmp/dcgw-wiz && cat > /tmp/dcgw-wiz/tvfloyd.py' < "$repo/tvcommon/py/tvfloyd.py"
    ssh -o BatchMode=yes "$h" 'cat > /tmp/dcgw-wiz/wiz.py' < "$wiz"
    printf '%s\n' "$token" | ssh -o BatchMode=yes "$h" 'python3 /tmp/dcgw-wiz/wiz.py /tmp/dcgw-wiz/tvfloyd.py; rm -rf /tmp/dcgw-wiz' || failed=1
  done
  rm -f "$wiz"
  mv -- "$here/$name" "$here/DONE/$name" 2>/dev/null || true
  say ""
  say "Done.  Restart tvchat on each machine: the Delta Chat account is now the one on cpbee."
  say "(Without a key login to a client machine the wizard line above was skipped: do it there in F7.)"
  [[ $failed == 0 ]] || say "Some clients need attention (see above); nothing is broken on cpbee."
  exit 0
fi

# ------------------------------------------------------------------ on cpbee ---
D=/tmp/dcgw-deploy
CONF=/etc/dcgw/dcgw.conf
stamp=$(date +%Y%m%d-%H%M%S)
me=$(id -un)

say "step 1/9: the user, the program and the Delta Chat core (sudo password) ..."
id dcgw >/dev/null 2>&1 || sudo useradd --system --home-dir /var/lib/dcgw --create-home --shell /usr/sbin/nologin dcgw
sudo install -d -m 700 -o dcgw -g dcgw /var/lib/dcgw
if [[ -f /usr/local/bin/dcgw ]] && ! cmp -s "$D/dcgw" /usr/local/bin/dcgw; then sudo cp -p /usr/local/bin/dcgw "/usr/local/bin/dcgw.bak.$stamp"; fi
sudo install -m 755 "$D/dcgw" /usr/local/bin/dcgw
sudo install -d -m 755 -o dcgw -g dcgw /opt/dcgw
if [[ ! -x /opt/dcgw/venv/bin/deltachat-rpc-server ]]; then
  sudo -u dcgw python3 -m venv /opt/dcgw/venv || die "python3 -m venv failed (is python3-venv installed?)"
  sudo -u dcgw /opt/dcgw/venv/bin/pip install --quiet deltachat-rpc-server || die "pip could not install deltachat-rpc-server"
fi
say "  core: $(sudo -u dcgw /opt/dcgw/venv/bin/deltachat-rpc-server --version 2>&1 | head -1)"
for f in tvchat-backend tvcal-backend tvmail-backend; do
  if [[ -f /usr/local/bin/$f ]] && ! cmp -s "$D/$f" "/usr/local/bin/$f"; then sudo cp -a "/usr/local/bin/$f" "/usr/local/bin/$f.bak.$stamp"; fi
  sudo install -m 755 "$D/$f" "/usr/local/bin/$f"
done
for dir in /usr/local/share/tvchat /usr/local/share/tvcal /usr/local/share/tvmail; do
  sudo install -d -m 755 "$dir"
  if [[ -f $dir/tvfloyd.py ]] && ! cmp -s "$D/tvfloyd.py" "$dir/tvfloyd.py"; then sudo cp -a "$dir/tvfloyd.py" "$dir/tvfloyd.py.bak.$stamp"; fi
  sudo install -m 644 "$D/tvfloyd.py" "$dir/tvfloyd.py"
done
/usr/local/bin/tvchat-backend --help >/dev/null 2>&1 || die "tvchat-backend does not start"

say "step 2/9: the TLS certificate ..."
sudo install -d -m 755 -o root -g dcgw /etc/dcgw          # the key (640) and the config (640) stay private; the certificate is public
if [[ ! -f /etc/dcgw/cert.pem ]]; then
  sudo openssl req -x509 -newkey rsa:3072 -nodes -days 3650 -subj "/CN=cpbee" \
       -addext "subjectAltName=DNS:cpbee,DNS:cpbee.cmnet,DNS:cpbee.lan,IP:$LAN" \
       -keyout /etc/dcgw/key.pem -out /etc/dcgw/cert.pem 2>/dev/null || die "openssl could not make the certificate"
fi
sudo chown root:dcgw /etc/dcgw/key.pem /etc/dcgw/cert.pem
sudo chmod 640 /etc/dcgw/key.pem
sudo chmod 644 /etc/dcgw/cert.pem
say "  /etc/dcgw/cert.pem: $(sudo openssl x509 -in /etc/dcgw/cert.pem -noout -enddate)"

say "step 3/9: the configuration and chris's token ..."
if [[ ! -f $CONF ]]; then
  printf '[dcgw]\nlisten = %s:%s\ncore = /opt/dcgw/venv/bin/deltachat-rpc-server\ndata = /var/lib/dcgw\ntls_cert = /etc/dcgw/cert.pem\ntls_key = /etc/dcgw/key.pem\n\n' "$LAN" "$PORT" \
    | sudo tee "$CONF" >/dev/null
fi
sudo chown root:dcgw "$CONF"; sudo chmod 640 "$CONF"
if ! sudo grep -q '^\[user:chris\]' "$CONF"; then
  sudo /usr/local/bin/dcgw --conf "$CONF" add-user chris --addr chris@cpbee.cmnet | sed 's/;* *token: .*$/ - the token is saved below/'
fi
sudo chown root:dcgw "$CONF"; sudo chmod 640 "$CONF"
( umask 077; sudo /usr/local/bin/dcgw --conf "$CONF" token chris > "$HOME/dcgw-chris.token" )
chmod 600 "$HOME/dcgw-chris.token"
token=$(cat "$HOME/dcgw-chris.token")
[[ ${#token} -ge 16 ]] || die "no token for chris"

say "step 4/9: chris's Delta Chat account: adopt the one tvchat has on cpbee ..."
SRC=$HOME/.local/share/tvchat/deltachat/delta_chris
sudo systemctl stop dcgw 2>/dev/null || true
have=$(sudo /usr/local/bin/dcgw --conf "$CONF" list)
if grep -q '^chris.*has data' <<<"$have"; then
  say "  the gateway already holds chris's account: left alone"
elif [[ -d $SRC ]] && python3 -c "import os,sys; sys.exit(0 if any('dc.db' in f for _,_,f in os.walk(sys.argv[1])) else 1)" "$SRC"; then
  if pgrep -u "$me" -f deltachat-rpc-server >/dev/null 2>&1; then
    die "a Delta Chat core of yours is running (tvchat on cpbee?): quit it, then run this again; nothing was copied"
  fi
  sudo /usr/local/bin/dcgw --conf "$CONF" adopt chris "$SRC"
  sudo chown -R dcgw:dcgw /var/lib/dcgw
  sudo chmod -R go-rwx /var/lib/dcgw
else
  say "  NOTE: no Delta Chat account to adopt ($SRC has none).  The gateway starts anyway; give chris's account its mail login with"
  say "        sudo systemctl stop dcgw; sudo -u dcgw /usr/local/bin/dcgw setup chris --imap-server cpbee --smtp-server cpbee --smtp-port 587 --no-verify; sudo systemctl start dcgw"
fi

say "step 5/9: the service and the firewall ..."
sudo install -m 644 "$D/dcgw.service" /etc/systemd/system/dcgw.service
sudo systemctl daemon-reload
bash "$D/lan-port" add "$PORT" "Delta Chat gateway (dcgw: tvchat clients on the LAN)" || die "lan-port add $PORT failed"
sudo systemctl enable --now dcgw
sleep 6
systemctl is-active dcgw || { sudo journalctl -u dcgw -n 25 --no-pager; die "dcgw is not running; the journal is above"; }
ss -ltn | grep -q "$LAN:$PORT" || { sudo journalctl -u dcgw -n 25 --no-pager; die "nothing listens on $LAN:$PORT; the journal is above"; }
sudo journalctl -u dcgw -n 5 --no-pager | sed 's/^/  /'

say "step 6/9: Floyd: the certificate, the setup wizard's manifest and the sentinel ..."
sudo install -m 644 -o root -g floyd /etc/dcgw/cert.pem /etc/tvchat/floyd-ca-dcgw.pem
if [[ -f /etc/tvchat/floyd-setup.json ]]; then
  sudo cp -p /etc/tvchat/floyd-setup.json "/etc/tvchat/floyd-setup.json.bak.$stamp"
  sudo python3 - <<'PYEOF'
import json
p = "/etc/tvchat/floyd-setup.json"
m = json.load(open(p))
m.pop("delta_owner", None)
m["accounts"] = [a for a in m.get("accounts", []) if a.get("id") != "delta_chris"] + [
    {"id": "delta_chris", "protocol": "deltachat", "display": "Delta Chat (chris)", "autoconnect": True,
     "opts": {"gateway": "cpbee", "gateway_tls": "yes", "gateway_cafile": "@CA:dcgw@"}}]
m.setdefault("cas", {})["dcgw"] = {"file": "/etc/tvchat/floyd-ca-dcgw.pem", "name": "dcgw-ca.pem"}
json.dump(m, open(p, "w"), indent=1)
print("  floyd-setup.json: the Delta Chat account is now a gateway account")
PYEOF
else
  say "  (no /etc/tvchat/floyd-setup.json: run floyd-wizard-on-cpbee.sh to make the setup manifest)"
fi
FCONF=/etc/tvchat/floyd.conf
if sudo grep -q '^sentinel *=' "$FCONF" && ! sudo grep -q '^sentinel_services *=' "$FCONF"; then
  sudo cp -p "$FCONF" "$FCONF.bak.$stamp"
  sudo sed -i 's/^\(sentinel *= *.*\)$/\1\nsentinel_services = ergo:6697, talkd:518@127.0.0.1, dovecot:993, postfix:25@127.0.0.1, radicale:5232, dcgw:7078/' "$FCONF"
  say "  sentinel: dcgw is watched now (a toast when it goes down)"
fi
sudo systemctl restart tvchat-floyd
sleep 6
systemctl is-active tvchat-floyd || { sudo journalctl -u tvchat-floyd -n 20 --no-pager; die "tvchat-floyd is not running"; }

say "step 7/9: cpbee's own tvchat becomes a gateway client ..."
install -d -m 700 "$HOME/.config/tvchat"
install -m 644 /etc/dcgw/cert.pem "$HOME/.config/tvchat/dcgw-ca.pem" 2>/dev/null || sudo cat /etc/dcgw/cert.pem > "$HOME/.config/tvchat/dcgw-ca.pem"
DCGW_TOKEN=$token python3 - <<'PYEOF'
import importlib.util, os
s = importlib.util.spec_from_file_location("tvfloyd", "/usr/local/share/tvchat/tvfloyd.py")
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
path = os.path.expanduser("~/.config/tvchat/tvchat.conf")
sec = "account:delta_chris"
if os.path.exists(path) and sec in m.conf_sections(path):
    for k, v in (("gateway", "cpbee"), ("gateway_tls", "yes"), ("gateway_cafile", "~/.config/tvchat/dcgw-ca.pem"),
                 ("token", os.environ["DCGW_TOKEN"])):
        m.conf_set(path, sec, k, v)
    print("  tvchat.conf: delta_chris is a gateway account now (old file in ~/.config/tvchat/BAK)")
else:
    print("  tvchat.conf has no [account:delta_chris]: nothing to switch (the wizard can add it)")
PYEOF

say "step 8/9 (on cpbee): the gateway answers over TLS ..."
DCGW_TOKEN=$token python3 - <<'PYEOF' || die "the gateway does not answer over TLS on cpbee"
import os, socket, ssl
ctx = ssl.create_default_context(cafile="/etc/dcgw/cert.pem")
s = ctx.wrap_socket(socket.create_connection(("cpbee", 7078), 8), server_hostname="cpbee")
s.sendall(("DCGW 1 %s\n" % os.environ["DCGW_TOKEN"]).encode())
line = b""
while not line.endswith(b"\n"):
    ch = s.recv(1)
    if not ch:
        break
    line += ch
assert line.startswith(b"OK 1 chris"), "answer: %r" % line
print("  " + line.decode().strip())
PYEOF
sudo /usr/local/bin/dcgw --conf "$CONF" list | sed 's/^/  /'
