#!/bin/bash
# RUN ON: cpbee, AS THE PERSON THE GATEWAY ACCOUNT BELONGS TO (michelle: `sudo -u michelle -H /tmp/dcgw-client-setup.sh`,
#         or she runs it herself).  It only touches her own home.
# WHY:    dcgw-add-user-on-cpbee.sh left three files in her home: dcgw-NAME.token, tvchat-delta.snippet, dcgw-ca.pem.
#         This puts them to use: her tvchat then shows her Delta Chat account, held on cpbee behind the gateway.
# WHAT:   1. copies the public certificate to ~/.config/tvchat/dcgw-ca.pem;
#         2. adds the [account:delta_NAME] section from the snippet to ~/.config/tvchat/tvchat.conf (the file is made, mode 600,
#            if she has none; the old one is kept in ~/.config/tvchat/BAK/ first) and adds the account to autoconnect;
#         3. connects once, as her, through the gateway and shows how the account answers (the token is never printed);
#         4. on success moves the three files into ~/.config/tvchat/dcgw/ (the token stays there, mode 600), so that her home
#            holds no loose copy of it.
# OPTIONS: --no-test (steps 1-2 only)   --keep (leave the three files where they are)   DRY_RUN=1 (show the plan)
# SAFE:   re-runnable: nothing is added twice.  Run it where tvchat and tvchat-backend are installed (cpbee has them).
set -euo pipefail

TEST=1; KEEP=0
for a in "$@"; do
  case $a in
    --no-test) TEST=0 ;;
    --keep) KEEP=1 ;;
    *) echo "usage: $0 [--no-test] [--keep]" >&2; exit 2 ;;
  esac
done
say() { printf '%s\n' "$*"; }
die() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }

me=$(id -un)
snippet=$HOME/tvchat-delta.snippet
cert=$HOME/dcgw-ca.pem
token_file=$(ls "$HOME"/dcgw-*.token 2>/dev/null | head -1 || true)
cfgdir=$HOME/.config/tvchat
TVFLOYD=${TVFLOYD_PY:-/usr/local/share/tvchat/tvfloyd.py}

if [[ ${DRY_RUN:-0} == 1 ]]; then
  say "DRY RUN as $me: would copy $cert to $cfgdir/dcgw-ca.pem, add the account from $snippet to $cfgdir/tvchat.conf (backup first)"
  say "and autoconnect, connect once through the gateway to test, then file the three files under $cfgdir/dcgw/. Nothing was changed."
  exit 0
fi

[[ -f $snippet ]] || die "$snippet is not here (dcgw-add-user-on-cpbee.sh makes it)"
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
if [[ $TEST == 1 ]]; then
  say "step 3/4: connecting once, as $me, through the gateway ..."
  command -v tvchat-backend >/dev/null || die "tvchat-backend is not installed here"
  out=$( (printf 'connect %s\n' "$ident"; sleep 12; printf 'events\n'; sleep 1; printf 'quit\n') \
         | timeout 40 tvchat-backend serve 2>&1 | grep -a "$ident" | grep -a $'\tstate\t' | cut -f4- | tail -3 || true)
  say "$out" | sed 's/^/  /'
  grep -q "connected" <<<"$out" || die "the account did not connect (see the lines above; is the gateway up, is the token right?)"
else
  say "step 3/4: skipped (--no-test)"
fi

say "step 4/4: tidying her home ..."
if [[ $KEEP == 1 || $TEST == 0 ]]; then
  say "  the three files stay where they are"
else
  install -d -m 700 "$cfgdir/dcgw"
  for f in "$snippet" "$cert" $token_file; do
    [[ -f $f ]] && mv -f "$f" "$cfgdir/dcgw/"
  done
  chmod 600 "$cfgdir"/dcgw/* 2>/dev/null || true
  say "  filed under $cfgdir/dcgw/ (the token stays there, mode 600)"
fi
say ""
say "Done.  Start tvchat: the Delta Chat account is on the list; it has no chats until you and the other person swap keys"
say "(Chat > Invite... on one side, Chat > Join... on the other)."
