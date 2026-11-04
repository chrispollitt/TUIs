#!/bin/bash
# RUN ON: the laptop (Cygwin).  It copies files to cpbee and runs the root part there with a terminal, so sudo asks
#         for your password once.  Takes about a minute.  # allowlist secret
# WHY:    so the central Floyd on cpbee (tvchat-floyd) can answer his admins' !todo, !cal and !event from the calendar
#         that tvcal shows - the same list, on the same Radicale server.
# WHAT:   1. installs the current tvchat-backend (Floyd's owner-only calendar commands) and tvfloyd.py, and installs
#            tvcal-backend with Floyd's words beside it (old files kept as .bak.<time>);
#         2. Radicale: gives Floyd his OWN login ("floyd", a long random password nobody types) with read-write on  # allowlist secret
#            chris's calendar ONLY (/chris/calendar/) and nothing else of chris's - done by the "share" mode of
#            configure-radicale.sh, which checks it and puts the old configuration back if the check fails;
#         3. writes the service user's ~floyd/.config/tvcal/tvcal.conf and ~floyd/.mu-tickets (mode 600; the password  # allowlist secret
#            is there and in Radicale's hashed user list, nowhere else);
#         4. proves it: as floyd, adds a to-do, reads it back, asks Floyd "!todo", deletes it;
#         5. restarts tvchat-floyd and shows its state.
# CHANGES on cpbee: /usr/local/bin/{tvchat-backend,tvcal-backend}, /usr/local/share/{tvchat,tvcal}/tvfloyd.py,
#         /etc/radicale/{config,rights,shares,users} (backups beside them while the check runs),
#         /home/floyd/.config/tvcal/, /home/floyd/.mu-tickets.  Only admins of Floyd (account tag from the network) get
#         the calendar: other people's !todo stay in Floyd's own small files.
# UNDO:   remove the "chris floyd" line from /etc/radicale/shares and re-run `configure-radicale.sh server`'s rights
#         step (or restore /etc/radicale/rights.bak.*); delete ~floyd/.mu-tickets' https line.
# SAFE:   re-runnable (a new random password each time).  Moves itself to DONE/ only on success.  DRY_RUN=1 shows the plan.  # allowlist secret
set -euo pipefail

HOST=cpbee
OWNER=chris
repo=/home/chris/gh_cp/TUIs
here=$(cd "$(dirname "$0")" && pwd)
name=$(basename "$0")
say() { printf '%s\n' "$*"; }
die() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }

if [[ ${DRY_RUN:-0} == 1 ]]; then
  say "DRY RUN: would copy tvchat-backend, tvcal-backend, tvfloyd.py and configure-radicale.sh to $HOST, run there with a"
  say "terminal (sudo asks once), and then: install them; share $OWNER's calendar with a new login 'floyd';"
  say "write ~floyd's tvcal.conf and .mu-tickets; test as floyd; restart tvchat-floyd.  Nothing was changed."
  exit 0
fi

if [[ ${RUN_ON_REMOTE:-0} != 1 && $(hostname -s) != "$HOST" ]]; then
  for f in tvchat/backend/tvchat-backend tvcal/backend/tvcal-backend tvcommon/py/tvfloyd.py tvcal/configure/configure-radicale.sh; do
    [[ -f $repo/$f ]] || die "$repo/$f is not here"
  done
  grep -q make_notes "$repo/tvchat/backend/tvchat-backend" || die "the repo's tvchat-backend lacks the calendar commands"
  ssh -o BatchMode=yes "$HOST" true || die "cannot ssh to $HOST"
  ssh "$HOST" 'rm -rf /tmp/tvcal-floyd && mkdir -m 700 /tmp/tvcal-floyd'
  scp -q "$here/$name" "$repo/tvchat/backend/tvchat-backend" "$repo/tvcal/backend/tvcal-backend" \
        "$repo/tvcommon/py/tvfloyd.py" "$repo/tvcal/configure/configure-radicale.sh" "$HOST:/tmp/tvcal-floyd/"
  rc=0
  ssh -t -o ServerAliveInterval=10 "$HOST" \
    "RUN_ON_REMOTE=1 bash /tmp/tvcal-floyd/$name; rc=\$?; rm -rf /tmp/tvcal-floyd; exit \$rc" || rc=$?
  [[ $rc == 0 ]] || die "it did not finish on $HOST (exit $rc); nothing was moved. Paste the output above to Claude."
  mv -- "$here/$name" "$here/DONE/$name" 2>/dev/null || true
  say "Done."
  exit 0
fi

# ------------------------------------------------------------------ on cpbee ---
D=/tmp/tvcal-floyd
stamp=$(date +%Y%m%d-%H%M%S)
as_floyd() { sudo -u floyd env HOME=/home/floyd "$@"; }

say "step 1/5: installing the programs (sudo password) ..."
for t in tvchat-backend:/usr/local/bin tvcal-backend:/usr/local/bin; do
  f=${t%%:*}; dir=${t##*:}
  if [[ -f $dir/$f ]] && ! cmp -s "$D/$f" "$dir/$f"; then sudo cp -a "$dir/$f" "$dir/$f.bak.$stamp"; fi
  sudo install -m 755 "$D/$f" "$dir/$f"
done
for dir in /usr/local/share/tvchat /usr/local/share/tvcal; do
  sudo install -d -m 755 "$dir"
  sudo install -m 644 "$D/tvfloyd.py" "$dir/tvfloyd.py"
done

say "step 2/5: Radicale: a login for Floyd on $OWNER's calendar only ..."
pw=$(openssl rand -base64 24 | tr -d '/+=\n' | cut -c1-24)
CAL_SHARE_PW="$pw" sudo --preserve-env=CAL_SHARE_PW bash "$D/configure-radicale.sh" share --user "$OWNER" --with floyd

say "step 3/5: Floyd's own tvcal configuration ..."
sudo -u floyd install -d -m 700 /home/floyd/.config/tvcal
sudo -u floyd tee /home/floyd/.config/tvcal/tvcal.conf >/dev/null <<EOF
[server]
url = https://localhost:5232/$OWNER/calendar/
user = floyd
ca = /etc/radicale/tls/cert.pem
EOF
# the password goes in on stdin, never on a command line; an older line of his is replaced  # allowlist secret
printf 'https://floyd:%s@localhost:5232\n' "$pw" | sudo -u floyd bash -c '  # allowlist secret
  umask 077; f=/home/floyd/.mu-tickets; touch "$f"
  { grep -v "^https://floyd:[^@]*@localhost" "$f" || true; cat; } > "$f.new" && mv "$f.new" "$f"'  # allowlist secret

say "step 4/5: as floyd: add a to-do, read it back, ask Floyd, delete it ..."
uid=$(as_floyd /usr/local/bin/tvcal-backend add-todo -- "floyd calendar test - delete me") || die "floyd could not add a to-do"
as_floyd /usr/local/bin/tvcal-backend todos | grep -q "floyd calendar test" || die "floyd's to-do did not come back"
as_floyd /usr/local/bin/tvcal-backend floyd '!todo' | grep -q "floyd calendar test" || die "Floyd's !todo does not show it"
as_floyd /usr/local/bin/tvcal-backend rm "$uid" || die "floyd could not delete the test to-do"
say "  works: add, read, !todo, delete"

say "step 5/5: restarting tvchat-floyd ..."
sudo systemctl restart tvchat-floyd
sleep 6
systemctl is-active tvchat-floyd || { sudo journalctl -u tvchat-floyd -n 25 --no-pager; die "tvchat-floyd is not running; the journal is above"; }
sudo journalctl -u tvchat-floyd -n 6 --no-pager || true
say "Floyd's admins can now say  !todo  !todo add TEXT  !cal week  !event fri 14:00 Dentist  to him."
