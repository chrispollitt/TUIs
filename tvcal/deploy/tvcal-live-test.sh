#!/bin/bash
# RUN ON: the laptop (Cygwin).  It reaches cpbee over ssh; sudo on cpbee asks for your password.
# WHAT:   the first live run of tvcal:
#   1. builds and installs tvcal, and tvmail + tvchat again (they got the calendar menu items and
#      Floyd's !todo / !cal / !event), into /usr/local on this machine;
#   2. on cpbee: installs and starts Radicale (the calendar server, TLS on port 5232) and makes
#      your calendar.  You are asked for the sudo password, then for the calendar password (twice; any  # allowlist secret
#      password you like - it is stored hashed on cpbee and asked for once more here, for ~/.mu-tickets);  # allowlist secret
#      nothing is written to a file of this repo;
#   3. here: writes [server] into ~/.config/tvcal/tvcal.conf, fetches cpbee's certificate, checks the login;
#   4. proves the round trip: adds a test to-do and a test event through the server, reads them back
#      with the local copy thrown away, and deletes them again.
# CHANGES on cpbee: package radicale (+ apache2-utils, python3-bcrypt, python3-passlib), /etc/radicale/,
#         /var/lib/radicale/, the radicale service, and port 5232 opened for the LAN if lan-port is there.
#         Nothing of mail, chat or the web pages is touched.  A changed /etc/radicale/config is backed up first.
# UNDO:   on cpbee: sudo systemctl disable --now radicale; sudo apt-get remove radicale
#         here: delete ~/.config/tvcal/ and the https://chris:...@cpbee:5232 line in ~/.mu-tickets  # allowlist secret
# SAFE:   re-runnable (every step skips what is already done).  DRY_RUN=1 only shows the plan.
#         Moves itself to TO_BE_RUN/DONE only when called through the stub and it succeeded.
set -euo pipefail

HOST=cpbee
CALUSER=${CALUSER:-chris}
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)     # .../TUIs
say() { printf '\n== %s\n' "$*"; }
die() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }

if [[ ${DRY_RUN:-0} == 1 ]]; then
  cat <<EOF
DRY RUN.  Would:
  1. build.sh + install.sh in $repo/tvcal, tvmail and tvchat
  2. scp tvcal/configure/configure-radicale.sh to $HOST; ssh -t $HOST sudo ... server --user $CALUSER
  3. configure-radicale.sh client --user $CALUSER --host $HOST
  4. tvcal-backend: add a to-do and an event, read them back without the cache, delete them
Nothing was changed.
EOF
  exit 0
fi

ssh -o BatchMode=yes "$HOST" true || die "cannot ssh to $HOST"

say "1/4 build and install (tvcal, tvmail, tvchat)"
for app in tvcal tvmail tvchat; do
  echo "-- $app"
  ( cd "$repo/$app" && ./build.sh >/tmp/build-$app.log 2>&1 && ./install.sh >>/tmp/build-$app.log 2>&1 ) \
    || { tail -20 "/tmp/build-$app.log"; die "$app did not build / install (log: /tmp/build-$app.log)"; }
done
command -v tvcal >/dev/null && command -v tvcal-backend >/dev/null || die "tvcal is not on PATH after the install"

say "2/4 the calendar server on $HOST (sudo asks for your password there)"  # allowlist secret
scp -q "$repo/tvcal/configure/configure-radicale.sh" "$HOST:/tmp/configure-radicale.sh"
ssh -t "$HOST" "sudo bash /tmp/configure-radicale.sh server --user $CALUSER; rc=\$?; rm -f /tmp/configure-radicale.sh; exit \$rc"

say "3/4 this machine as a client"
"$repo/tvcal/configure/configure-radicale.sh" client --user "$CALUSER" --host "$HOST"

say "4/4 the round trip through the server"
state=${XDG_STATE_HOME:-$HOME/.local/state}/tvcal
t=$(tvcal-backend add-todo -- "tvcal live test - delete me")
e=$(tvcal-backend add-event --date today --start 23:00 --remind off -- "tvcal live test - delete me")
rm -f "$state/cache.ics"                                   # throw the local copy away: read it from cpbee
tvcal-backend todos | grep -q "tvcal live test" || die "the to-do did not come back from the server"
tvcal-backend range "$(date +%F)" "$(date +%F)" | grep -q "tvcal live test" || die "the event did not come back from the server"
echo "both came back from $HOST"
tvcal-backend rm "$t"; tvcal-backend rm "$e"
rm -f "$state/cache.ics"
tvcal-backend todos | grep -q "tvcal live test" && die "the test to-do is still on the server"
echo "and are deleted again"

cat <<EOF

DONE.  The calendar now lives on $HOST.
EOF
# the stub (TO_BE_RUN/tvcal-live-test.sh) moves itself to DONE after this exits 0
