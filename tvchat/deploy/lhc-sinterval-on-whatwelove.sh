#!/bin/bash
# RUN ON: the LAPTOP (Cygwin).  It logs in to the cPanel web host by itself with ssh-wwl (the MFA helper).  No sudo.  Re-runnable; the REAL script is in the repo (tvchat/deploy), Claude puts a stub in TO_BE_RUN when it is wanted.
# WHY:    A visitor's chat window asks the server for new messages every `chat_message_sinterval` seconds (default 3.5): that is
#         most of the wait for an answer, and what decides whether "Joy is typing" is ever seen.  It is NOT in the admin menus: it is
#         a line in the site's  settings/settings.ini.php  :   'chat_message_sinterval' => 3.5,
# WHAT:   sets that number in the PRO site's settings file (SITE=pro, the default) or the fun site's (SITE=fun):
#           pro  /home/whatwelo/www/ul/chat/settings/settings.ini.php        fun  /home/whatwelo/www/u-l/chat/settings/settings.ini.php
#         The file is copied first to /home/whatwelo/BAK/settings.ini.php.<site>.<time> (mode 600: it holds the database password),
#         the change is checked with `php -l` and put back if that fails.  Nothing else in the file changes, and the file is never
#         printed except the one changed line.
# VALUE:  VALUE=1.5 (default; seconds; between 0.5 and 10).  To undo: VALUE=3.5.
# SAFE:   DRY_RUN=1 shows the line as it is and as it would be.  Whether the embedded widget (the one on universal-laughter.com) reads this
#         number as well as the classic chat page is being tested: tell Claude what you see.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
name=$(basename "$0")
say() { printf '%s\n' "$*"; }
die() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }
command -v ssh-wwl >/dev/null || die "ssh-wwl is not on the PATH (it is /home/chris/bin/ssh-wwl)"
SITE=${SITE:-pro}
VALUE=${VALUE:-1.5}
case $SITE in pro|fun) ;; *) die "SITE must be pro or fun";; esac
[[ $VALUE =~ ^[0-9]+(\.[0-9]+)?$ ]] || die "VALUE must be a number of seconds, like 1.5"
awk -v v="$VALUE" 'BEGIN { exit !(v >= 0.5 && v <= 10) }' || die "VALUE must be between 0.5 and 10"

read -r -d '' REMOTE <<'REMOTE_EOF' || true
set -euo pipefail
case "$SITE" in pro) F=/home/whatwelo/www/ul/chat/settings/settings.ini.php;; *) F=/home/whatwelo/www/u-l/chat/settings/settings.ini.php;; esac
[ -f "$F" ] || { echo "$F is not there" >&2; exit 1; }
cur=$(grep -n "'chat_message_sinterval'" "$F" | head -1 || true)
[ -n "$cur" ] || { echo "no 'chat_message_sinterval' line in $F" >&2; exit 1; }
echo "now:    $cur"
tmp=$(mktemp)
sed "s/\('chat_message_sinterval' *=> *\)[0-9.]\+/\1$VALUE/" "$F" > "$tmp"
new=$(grep -n "'chat_message_sinterval'" "$tmp" | head -1)
echo "will be: $new"
if cmp -s "$F" "$tmp"; then echo "already $VALUE: nothing to change"; rm -f "$tmp"; echo "RESULT: OK"; exit 0; fi
if [ "${DRY_RUN:-0}" = 1 ]; then rm -f "$tmp"; echo "DRY RUN: nothing was changed."; echo "RESULT: OK"; exit 0; fi
php -l "$tmp" >/dev/null || { echo "the edited file does not pass php -l; nothing was changed" >&2; rm -f "$tmp"; exit 1; }
mkdir -p /home/whatwelo/BAK
bak=/home/whatwelo/BAK/settings.ini.php.$SITE.$(date +%Y%m%d-%H%M%S)
( umask 077; cp -p "$F" "$bak" )
cat "$tmp" > "$F"
rm -f "$tmp"
php -l "$F" >/dev/null || { cp -p "$bak" "$F"; echo "the new file failed php -l: the old one is back" >&2; exit 1; }
echo "changed (the old file: $bak)"
echo "RESULT: OK"
REMOTE_EOF

b64=$(printf '%s\n' "$REMOTE" | base64 -w0)
say "connecting to the web host with ssh-wwl (SITE=$SITE, VALUE=$VALUE) ..."
out=$(timeout 120 ssh-wwl "echo $b64 | base64 -d | SITE='$SITE' VALUE='$VALUE' DRY_RUN='${DRY_RUN:-0}' bash" 2>&1 | grep -v '^spawn' | tr -d '\r') || true
printf '%s\n' "$out"
printf '%s\n' "$out" | grep -q '^RESULT: OK$' || die "it did not finish on the web host. Paste the output above to Claude."
[[ ${DRY_RUN:-0} == 1 ]] && exit 0
say "Now open the chat (the pro site's chat page, or the widget on universal-laughter.com?chat=1) in a FRESH tab and see whether answers arrive sooner."
