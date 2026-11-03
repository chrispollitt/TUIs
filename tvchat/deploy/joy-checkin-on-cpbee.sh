#!/bin/bash
# RUN ON: cpbee.  Started on the laptop it copies itself to cpbee and runs there with a terminal (it asks for sudo once).  A few seconds.
# WHY:    Floyd asks Joy "How's it going?" once a day: he reads her chat site (the Live Helper Chat API, with the pro key he already
#         has in /etc/tvchat/floyd.env) and mails Chris only if visitors went unanswered, answers were slow, or the site could not be
#         reached; on one weekday (default Monday) a short all-well note.  Nothing new is opened on the web host.
# WHAT:   1. adds a [joy-checkin] section to /etc/tvchat/floyd.conf (the url and user are lifted from the old Joy's lhc_pro section,
#            kept in /etc/tvchat/joy-pro-accounts.saved; the mailbox is Floyd's mail account; the config must still check out)
#         2. installs tvchat-joy-checkin.service + .timer (daily, 09:30 local, +-10 min), enabled
#         3. runs one check now with --dry-run (it talks to the site, it mails nothing) and shows what Floyd and Joy said
# NEEDS:  the backend with `joy-checkin` on cpbee (deploy/joy-update-on-cpbee.sh installs it) and $TVCHAT_PASS_LHC_PRO in floyd.env.
# USE:    TO=you@example.org WEEKLY=Mon ./joy-checkin-on-cpbee.sh          (TO default chris@universal-laughter.com; WEEKLY=none for no note)
#         ACTION=off stops and removes the timer (the config section stays).   DRY_RUN=1 changes nothing.
# SAFE:   re-runnable.  The old config is kept as floyd.conf.bak.<time>.
set -euo pipefail

HOST=cpbee
CONF=/etc/tvchat/floyd.conf
SAVED=/etc/tvchat/joy-pro-accounts.saved
ENVF=/etc/tvchat/floyd.env
ACTION=${ACTION:-on}
TO=${TO:-chris@universal-laughter.com}
WEEKLY=${WEEKLY:-Mon}
here=$(cd "$(dirname "$0")" && pwd)
name=$(basename "$0")
say() { printf '%s\n' "$*"; }
die() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }
case $ACTION in on|off) ;; *) die "ACTION must be on or off";; esac
[[ $TO =~ ^[^[:space:]@]+@[^[:space:]@]+$ ]] || die "TO must be an address"
[[ $WEEKLY =~ ^(none|Mon|Tue|Wed|Thu|Fri|Sat|Sun)$ ]] || die "WEEKLY must be none or Mon..Sun"

if [[ ${RUN_ON_REMOTE:-0} != 1 && $(hostname -s) != "$HOST" ]]; then
  scp -q "$here/$name" "$HOST:/tmp/$name"
  rc=0
  ssh -t -o ServerAliveInterval=10 "$HOST" "RUN_ON_REMOTE=1 ACTION='$ACTION' TO='$TO' WEEKLY='$WEEKLY' DRY_RUN='${DRY_RUN:-0}' bash /tmp/$name; rc=\$?; rm -f /tmp/$name; exit \$rc" || rc=$?
  [[ $rc == 0 ]] || die "it did not finish on $HOST (exit $rc). Paste the output above to Claude."
  exit 0
fi

# ---- on cpbee ----
TARGET=$(command -v tvchat-backend 2>/dev/null || echo /usr/local/bin/tvchat-backend)
UNIT=/etc/systemd/system/tvchat-joy-checkin
if [[ $ACTION == off ]]; then
  [[ ${DRY_RUN:-0} == 1 ]] && { say "DRY RUN: would stop and remove the timer"; exit 0; }
  sudo systemctl disable --now tvchat-joy-checkin.timer 2>/dev/null || true
  sudo rm -f "$UNIT.service" "$UNIT.timer"
  sudo systemctl daemon-reload
  say "done: the daily check-in is off (the [joy-checkin] section is still in $CONF)"
  exit 0
fi

"$TARGET" joy-checkin --help >/dev/null 2>&1 || die "$TARGET has no joy-checkin yet: run deploy/joy-update-on-cpbee.sh first"
sudo grep -q '^TVCHAT_PASS_LHC_PRO=' "$ENVF" || die "TVCHAT_PASS_LHC_PRO is not in $ENVF (the pro site's API key)"

tmp=$(mktemp)
cat "$CONF" > "$tmp"
if grep -q '^\[joy-checkin\]' "$tmp"; then
  say "  [joy-checkin] is already in the config: left as it is"
else
  sect=$(sudo cat "$SAVED" 2>/dev/null | awk '/^\[account:/{on=($0 ~ /^\[account:lhc_pro\]/)} on')
  [[ -n $sect ]] || die "no [account:lhc_pro] in $SAVED to take the site's url and user from"
  url=$(printf '%s\n' "$sect" | sed -n 's/^url[[:space:]]*=[[:space:]]*//p' | head -1)
  usr=$(printf '%s\n' "$sect" | sed -n 's/^user[[:space:]]*=[[:space:]]*//p' | head -1)
  dep=$(printf '%s\n' "$sect" | sed -n 's/^department[[:space:]]*=[[:space:]]*//p' | head -1)
  [[ -n $url && -n $usr ]] || die "the saved lhc_pro section has no url= or user="
  grep -q '^\[account:mail\]' "$tmp" || die "there is no [account:mail] in $CONF to send from"
  {
    echo
    echo "# Floyd's daily \"How's it going?\" with Joy (tvchat-backend joy-checkin, tvchat-joy-checkin.timer): mail only when she is not well."
    echo "[joy-checkin]"
    echo "url          = $url"
    echo "user         = $usr"
    echo "key_account  = lhc_pro"
    echo "department   = ${dep:-2}"
    echo "mail_account = mail"
    echo "to           = $TO"
    [[ $WEEKLY == none ]] || echo "weekly       = $WEEKLY"
  } >> "$tmp"
  say "  added [joy-checkin]: $usr on $url, mail to $TO${WEEKLY:+, all-well note on $WEEKLY}"
fi
out=$("$TARGET" check-config "$tmp" 2>&1) || { say "$out"; rm -f "$tmp"; die "the new config does not check out; $CONF is as it was"; }
say "  $(printf '%s' "$out" | tail -1)"
if [[ ${DRY_RUN:-0} == 1 ]]; then say "DRY RUN: nothing was changed"; rm -f "$tmp"; exit 0; fi
if ! cmp -s "$tmp" "$CONF"; then
  sudo cp -a "$CONF" "$CONF.bak.$(date +%Y%m%d-%H%M%S)"
  sudo install -m "$(stat -c %a "$CONF")" -o "$(stat -c %U "$CONF")" -g "$(stat -c %G "$CONF")" "$tmp" "$CONF"
fi
rm -f "$tmp"

sudo tee "$UNIT.service" >/dev/null <<EOF
[Unit]
Description=Floyd asks Joy how it is going (mails the owner only if she is not well)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=floyd
Environment=TVCHAT_CONF=$CONF
EnvironmentFile=-$ENVF
ExecStart=$TARGET joy-checkin
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
EOF
sudo tee "$UNIT.timer" >/dev/null <<EOF
[Unit]
Description=Floyd's daily check-in with Joy

[Timer]
OnCalendar=*-*-* 09:30:00
RandomizedDelaySec=600
Persistent=true

[Install]
WantedBy=timers.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now tvchat-joy-checkin.timer >/dev/null 2>&1
say "  timer: $(systemctl list-timers tvchat-joy-checkin.timer --no-pager 2>/dev/null | sed -n 2p | cut -c1-60)"

say "--- one check now (dry run: nothing is mailed) ---"
sudo -u floyd -H env TVCHAT_CONF="$CONF" bash -c "set -a; . $ENVF; exec $TARGET joy-checkin --dry-run" || die "the check failed (paste the output above to Claude)"
say "done. Later:  sudo systemctl start tvchat-joy-checkin.service   (a real check, mails if she is not well);  journalctl -u tvchat-joy-checkin"
