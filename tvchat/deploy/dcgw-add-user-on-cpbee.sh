#!/bin/bash
# RUN ON: the laptop (Cygwin).  Usage: dcgw-add-user-on-cpbee.sh [NAME]   (default michelle)
#         Runs the root part on cpbee with a terminal: sudo asks for your password, and ONE prompt asks for NAME's mail
#         (her cpbee login: Dovecot checks it with PAM).  You type that; nothing here reads or stores it
#         except the Delta Chat core on cpbee, which keeps it in her account's database.
# WHY:    each person's Delta Chat account lives on cpbee behind dcgw (tvchat/DELTA-GATEWAY.md); this adds a person.
# WHAT:   1. checks that NAME is a user on cpbee (her mailbox and login are the existing ones) and the gateway runs;
#         2. adds [user:NAME] (address NAME@cpbee.cmnet) with a token to /etc/dcgw/dcgw.conf (kept if it exists);
#         3. stops dcgw for a minute and gives her account its mail login with `dcgw setup NAME ...` (the secret prompt;
#            the service stops because two cores cannot share a folder; chris's Delta Chat is paused meanwhile);
#         4. writes her files into her home on cpbee, mode 600, owned by her: dcgw-NAME.token (her token) and
#            tvchat-delta.snippet (the [account:...] lines for her tvchat.conf, token included) and dcgw-ca.pem (the public
#            certificate); the token is never printed;
#         5. starts dcgw and checks, as her, over TLS: her token is accepted and her account is configured.
# CHANGES on cpbee: /etc/dcgw/dcgw.conf (one section), /var/lib/dcgw/NAME (her account), three files in her home.
# NOTE:   her keys and messages live on cpbee, so whoever administers cpbee could read them.  She should know that.
# UNDO:   remove [user:NAME] from /etc/dcgw/dcgw.conf, `sudo rm -r /var/lib/dcgw/NAME`, `sudo systemctl restart dcgw`.
# SAFE:   re-runnable (the token is kept; the secret prompt runs again, or SKIP_SETUP=1 skips it).  Moves itself to DONE/ only on success.
#         DRY_RUN=1 shows the plan.
set -euo pipefail

HOST=cpbee
PORT=7078
NAME=${1:-michelle}
[[ $NAME =~ ^[a-z][a-z0-9_-]{0,30}$ ]] || { echo "FAILED: '$NAME' is not a plausible user name" >&2; exit 1; }
ADDR=$NAME@cpbee.cmnet
here=$(cd "$(dirname "$0")" && pwd)
name=$(basename "$0")
say() { printf '%s\n' "$*"; }
die() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }

if [[ ${DRY_RUN:-0} == 1 ]]; then
  say "DRY RUN: would add $NAME ($ADDR) to the gateway on $HOST, ask for her mail password once (you type it), set up her"
  say "account, put her token, a tvchat config snippet and the certificate into her home (mode 600), and check it over TLS."
  say "Nothing was changed."
  exit 0
fi

if [[ ${RUN_ON_REMOTE:-0} != 1 && $(hostname -s) != "$HOST" ]]; then
  ssh -o BatchMode=yes "$HOST" true || die "cannot ssh to $HOST"
  scp -q "$here/$name" "$HOST:/tmp/$name"
  rc=0
  ssh -t -o ServerAliveInterval=10 "$HOST" "RUN_ON_REMOTE=1 SKIP_SETUP=${SKIP_SETUP:-0} bash /tmp/$name $NAME; rc=\$?; rm -f /tmp/$name; exit \$rc" || rc=$?
  [[ $rc == 0 ]] || die "it did not finish on $HOST (exit $rc); paste the output above to Claude."
  mv -- "$here/$name" "$here/DONE/$name" 2>/dev/null || true
  say ""
  say "Done.  $NAME's files are in her home on cpbee (dcgw-$NAME.token, tvchat-delta.snippet, dcgw-ca.pem)."
  say "She puts the snippet into her tvchat.conf, adds the account to autoconnect, and starts tvchat (or you do it on her machine)."
  exit 0
fi

# ------------------------------------------------------------------ on cpbee ---
CONF=/etc/dcgw/dcgw.conf
id "$NAME" >/dev/null 2>&1 || die "$NAME is not a user on cpbee: make her account first (her mailbox and login are that account)"
home=$(getent passwd "$NAME" | cut -d: -f6)
[[ -d $home ]] || die "no home directory for $NAME ($home)"
systemctl is-active dcgw >/dev/null || die "dcgw is not running: run dcgw-on-cpbee.sh first"

say "step 1/5: the gateway user and token (sudo password) ..."
if ! sudo grep -q "^\[user:$NAME\]" "$CONF"; then
  sudo /usr/local/bin/dcgw --conf "$CONF" add-user "$NAME" --addr "$ADDR" | sed 's/ *token: .*$/ - the token is saved/'
else
  say "  $NAME is already in $CONF: token kept"
fi
sudo chown root:dcgw "$CONF"; sudo chmod 640 "$CONF"
token=$(sudo /usr/local/bin/dcgw --conf "$CONF" token "$NAME")
[[ ${#token} -ge 16 ]] || die "no token for $NAME"

if [[ ${SKIP_SETUP:-0} == 1 ]]; then
  say "step 2/5: skipped (SKIP_SETUP=1: her account's mail login was set up on an earlier run)"
else
  say "step 2/5: her mail login for the Delta Chat account (the service stops for a minute) ..."
  say "  Type $NAME's mail password at the prompt (her cpbee login password; nothing is echoed or kept here)."
  sudo systemctl stop dcgw
  trap 'sudo systemctl start dcgw >/dev/null 2>&1 || true' EXIT
  sudo -u dcgw /usr/local/bin/dcgw --conf "$CONF" setup "$NAME" \
       --imap-server cpbee --smtp-server cpbee --imap-user "$NAME" --smtp-user "$NAME" --smtp-port 587 --no-verify \
    || die "setting up $NAME's account failed (a wrong password? run this again)"
  sudo systemctl start dcgw
  trap - EXIT
  sleep 6
  systemctl is-active dcgw >/dev/null || { sudo journalctl -u dcgw -n 20 --no-pager; die "dcgw did not start again; the journal is above"; }
fi

say "step 3/5: her files in $home ..."
sudo install -m 600 -o "$NAME" -g "$(id -gn "$NAME")" /dev/null "$home/dcgw-$NAME.token"
printf '%s\n' "$token" | sudo tee "$home/dcgw-$NAME.token" >/dev/null
sudo install -m 644 -o "$NAME" -g "$(id -gn "$NAME")" /etc/dcgw/cert.pem "$home/dcgw-ca.pem"
{
  printf '# Delta Chat through the gateway on cpbee.  Add this to tvchat.conf, and add delta_%s to autoconnect in [chat]:\n' "$NAME"
  printf '#   [chat]\n#   autoconnect = ..., delta_%s\n' "$NAME"
  printf '# The certificate: copy dcgw-ca.pem to ~/.config/tvchat/dcgw-ca.pem (on another machine: scp it from cpbee).\n'
  printf '[account:delta_%s]\nprotocol       = deltachat\ndisplay        = Delta Chat (%s)\ngateway        = cpbee\n' "$NAME" "$NAME"
  printf 'token          = %s\ngateway_tls    = yes\ngateway_cafile = ~/.config/tvchat/dcgw-ca.pem\n' "$token"
} | sudo tee "$home/tvchat-delta.snippet" >/dev/null
sudo chown "$NAME:$(id -gn "$NAME")" "$home/tvchat-delta.snippet"
sudo chmod 600 "$home/tvchat-delta.snippet"
sudo ls -l "$home/dcgw-$NAME.token" "$home/tvchat-delta.snippet" "$home/dcgw-ca.pem" | sed 's/^/  /'   # sudo: her home is private to her

say "step 4/5: dcgw sees her account ..."
sudo journalctl -u dcgw -n 8 --no-pager | grep -E "$NAME|listening" | sed 's/^/  /' || true

say "step 5/5: as $NAME, over TLS ..."
DCGW_TOKEN=$token DCGW_NAME=$NAME python3 - <<'PYEOF' || die "$NAME could not use the gateway"
import json, os, socket, ssl
ctx = ssl.create_default_context(cafile="/etc/dcgw/cert.pem")
s = ctx.wrap_socket(socket.create_connection(("cpbee", 7078), 8), server_hostname="cpbee")
s.settimeout(20)
s.sendall(("DCGW 1 %s\n" % os.environ["DCGW_TOKEN"]).encode())
f = s.makefile("rb")
hello = f.readline().decode().strip()
assert hello.startswith("OK 1 " + os.environ["DCGW_NAME"]), "answer: %r" % hello
print("  " + hello)
def rpc(method, *params):
    s.sendall((json.dumps({"jsonrpc": "2.0", "method": method, "params": list(params), "id": 1}) + "\n").encode())
    return json.loads(f.readline().decode())
ids = rpc("get_all_account_ids")["result"]
conf = rpc("is_configured", ids[0])["result"]
assert conf, "the account is not configured: the password was not accepted or the mail server was not reachable"
print("  her account is configured (id %s)" % ids[0])
PYEOF
