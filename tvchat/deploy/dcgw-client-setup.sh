#!/bin/bash
# RUN AS THE PERSON THE GATEWAY ACCOUNT BELONGS TO, in her own shell, on the machine where she runs tvchat:
#     dcgw-client-setup --from cpbee          (cmlaptop, cmdesktop, or cpbee itself:  dcgw-client-setup)
# WHY:    dcgw-add-user-on-cpbee.sh left three files in her home on cpbee: dcgw-NAME.token, tvchat-delta.snippet, dcgw-ca.pem.
#         This fetches them with HER OWN cpbee login (ssh asks for her cpbee password: she types it; the token never passes
#         through anyone else) and puts them to use, so her tvchat shows her Delta Chat account, held on cpbee behind the gateway.
# WHAT:   0. with --from HOST: copies the three files from her home on HOST (scp, her login) to a private temporary folder;
#         1. puts the public certificate at ~/.config/tvchat/dcgw-ca.pem;
#         2. adds the [account:delta_NAME] section from the snippet to ~/.config/tvchat/tvchat.conf (made, mode 600, if she has none;
#            the old one is kept in ~/.config/tvchat/BAK/ first) and adds the account to autoconnect;
#         3. connects once, as her, through the gateway and shows how it answers (the token is never printed);
#         4. on success files the token, the snippet and the certificate in ~/.config/tvchat/dcgw/ (mode 600): no loose copy.
# OPTIONS: --from HOST   fetch the files from HOST (without it: they are in her home here)
#          --remove-remote   after success, delete the three files from her home on HOST (the token also stays in the gateway)
#          --no-test         steps 0-2 only          --keep   leave the files in her home instead of filing them
#          DRY_RUN=1         show the plan
# SAFE:   re-runnable: nothing is added twice.  Needs tvchat-backend (installed beside tvchat) and python3.
set -euo pipefail

TEST=1; KEEP=0; FROM=""; RMREMOTE=0
while [[ $# -gt 0 ]]; do
  case $1 in
    --no-test) TEST=0 ;;
    --keep) KEEP=1 ;;
    --remove-remote) RMREMOTE=1 ;;
    --from) shift; FROM=${1:-}; [[ -n $FROM ]] || { echo "--from needs a host name" >&2; exit 2; } ;;
    *) echo "usage: $0 [--from HOST] [--remove-remote] [--no-test] [--keep]" >&2; exit 2 ;;
  esac
  shift
done
say() { printf '%s\n' "$*"; }
die() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }

me=$(id -un)
cfgdir=$HOME/.config/tvchat
snippet=$HOME/tvchat-delta.snippet
cert=$HOME/dcgw-ca.pem
token_file=""
tvfloyd_default=/usr/local/share/tvchat/tvfloyd.py
TVFLOYD=${TVFLOYD_PY:-$tvfloyd_default}

if [[ ${DRY_RUN:-0} == 1 ]]; then
  [[ -n $FROM ]] && say "DRY RUN as $me: would fetch the three files from $FROM:~ with scp (your login there)," \
                  || say "DRY RUN as $me: would use the three files in $HOME,"
  say "put the certificate at $cfgdir/dcgw-ca.pem, add the account to $cfgdir/tvchat.conf (backup first) and autoconnect,"
  say "connect once through the gateway to test, then file the files under $cfgdir/dcgw/. Nothing was changed."
  exit 0
fi

tmp=""
if [[ -n $FROM ]]; then
  say "step 0/4: fetching your three files from $FROM (type your cpbee password if ssh asks) ..."
  tmp=$(mktemp -d)
  chmod 700 "$tmp"
  trap 'rm -rf "$tmp"' EXIT
  scp -q "$FROM:tvchat-delta.snippet" "$FROM:dcgw-ca.pem" "$tmp/" || die "could not fetch the files from $FROM (is the gateway script done for you, and is your login right?)"
  scp -q "$FROM:dcgw-*.token" "$tmp/" || die "could not fetch the token file from $FROM"
  snippet=$tmp/tvchat-delta.snippet
  cert=$tmp/dcgw-ca.pem
fi
token_file=$(ls "${tmp:-$HOME}"/dcgw-*.token 2>/dev/null | head -1 || true)

[[ -f $snippet ]] || die "$snippet is not here (dcgw-add-user-on-cpbee.sh makes it; use --from HOST to fetch it)"
[[ -f $cert ]] || die "$cert is not here"
[[ -f $TVFLOYD ]] || die "$TVFLOYD is not installed (tvchat's helper module)"
grep -q '^\[account:delta_' "$snippet" || die "$snippet has no [account:delta_...] section"

say "step 1/4: the certificate ..."
install -d -m 700 "$cfgdir"
install -m 644 "$cert" "$cfgdir/dcgw-ca.pem"
say "  $cfgdir/dcgw-ca.pem"

say "step 2/4: the account in tvchat.conf ..."
SNIPPET=$snippet TVFLOYD=$TVFLOYD python3 - <<'PYEOF'
import configparser, importlib.util, os, sys
spec = importlib.util.spec_from_file_location("tvfloyd", os.environ["TVFLOYD"])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
c = configparser.ConfigParser(interpolation=None)
c.read(os.environ["SNIPPET"], encoding="utf-8")
sect = next(s for s in c.sections() if s.startswith("account:delta_"))
ident = sect.split(":", 1)[1]
path = os.path.expanduser("~/.config/tvchat/tvchat.conf")
items = list(c.items(sect))
added = m.conf_add_account(path, ident, items)
if not added:                                     # there already: make sure its gateway settings are the snippet's
    for k, v in items:
        m.conf_set(path, sect, k, v)
auto = m.conf_autoconnect_add(path, ident)
print("  %s: account %s %s; autoconnect %s" % (path, ident, "added" if added else "updated",
                                                "extended" if auto else "already had it"))
PYEOF
chmod 600 "$cfgdir/tvchat.conf"

ident=$(sed -n 's/^\[account:\(delta_[^]]*\)\].*/\1/p' "$snippet" | head -1)
ok=1
if [[ $TEST == 1 ]]; then
  say "step 3/4: connecting once, as $me, through the gateway ..."
  command -v tvchat-backend >/dev/null || die "tvchat-backend is not installed here"
  out=$( (printf 'connect %s\n' "$ident"; sleep 12; printf 'events\n'; sleep 1; printf 'quit\n') \
         | timeout 40 tvchat-backend serve 2>&1 | grep -a "$ident" | grep -a $'\tstate\t' | cut -f4- | tail -3 || true)
  say "$out" | sed 's/^/  /'
  grep -q "connected" <<<"$out" || die "the account did not connect (see the lines above; is the gateway up, is the token right?)"
else
  say "step 3/4: skipped (--no-test)"
  ok=0
fi

say "step 4/4: filing the files ..."
if [[ $KEEP == 1 ]]; then
  [[ -n $tmp ]] && cp -f "$snippet" "$cert" $token_file "$HOME/"
  say "  the files are in $HOME"
elif [[ $ok == 1 ]]; then
  install -d -m 700 "$cfgdir/dcgw"
  for f in "$snippet" "$cert" $token_file; do
    [[ -f $f ]] || continue
    if [[ -n $tmp ]]; then cp -f "$f" "$cfgdir/dcgw/"; else mv -f "$f" "$cfgdir/dcgw/"; fi
  done
  chmod 600 "$cfgdir"/dcgw/* 2>/dev/null || true
  say "  filed under $cfgdir/dcgw/ (mode 600)"
  if [[ $RMREMOTE == 1 && -n $FROM ]]; then
    ssh "$FROM" 'rm -f ~/tvchat-delta.snippet ~/dcgw-ca.pem ~/dcgw-*.token' && say "  removed the three files from your home on $FROM"
  elif [[ -n $FROM ]]; then
    say "  (the three files are still in your home on $FROM; --remove-remote deletes them there)"
  fi
else
  say "  (not filed: the connection test was skipped)"
fi
say ""
say "Done.  Start tvchat: the Delta Chat account is on the list; it has no chats until you and the other person swap keys"
say "(Chat > Invite... on one side, Chat > Join... on the other)."
