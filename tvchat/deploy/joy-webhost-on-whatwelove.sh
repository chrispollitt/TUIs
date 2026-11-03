#!/bin/bash
# RUN ON: the LAPTOP (Cygwin).  It logs in to the cPanel web host by itself with ssh-wwl (the MFA helper).  No sudo.
# WHY:    Joy, the pro chat bot, runs ON the web host as a Live Helper Chat extension (tvchat/lhc-joy): she answers the instant a
#         visitor writes, inside the site's own request - no daemon, no polling, nothing on cpbee.
# ACTION: install (default)  put the extension on the pro site, her FAQ and settings in /home/whatwelo/joy/, switch the extension on
#                            in the site's settings file, mark her operator "always online" - but leave her SILENT (the kill file
#                            /home/whatwelo/joy/DISABLED exists), so that she and the old cpbee Joy never both answer.
#         enable             remove the kill file: she answers.        disable   create it: she is silent.
#         status             what is installed and switched on.        uninstall take 'joy' out of the site's extension list.
# FAQ:    her FAQ is fetched from cpbee (/etc/tvchat/faq.txt, the live one) and put on the host ONLY IF there is none yet
#         (FORCE_FAQ=1 replaces it).  From then on the copy on the host is the one to edit: /home/whatwelo/joy/faq.txt.
# OTHER:  DEP_ID=2  OPERATOR_ID=2  (her department and operator numbers on the pro site)   DRY_RUN=1  changes nothing.
# SAFE:   re-runnable.  The site's settings file is copied first to /home/whatwelo/BAK/ (mode 600: it holds the database password),
#         checked with `php -l`, and put back if that fails.  Her code catches every error and does nothing: it cannot break the chat.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/.." && pwd)
say() { printf '%s\n' "$*"; }
die() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }
command -v ssh-wwl >/dev/null || die "ssh-wwl is not on the PATH (it is /home/chris/bin/ssh-wwl)"
ACTION=${ACTION:-install}
case $ACTION in install|enable|disable|status|uninstall) ;; *) die "ACTION must be install, enable, disable, status or uninstall";; esac

TAR_B64=""; FAQ_B64=""
if [[ $ACTION == install ]]; then
  [[ -f $root/lhc-joy/extension/joy/bootstrap/bootstrap.php ]] || die "$root/lhc-joy/extension/joy is not here"
  TAR_B64=$(tar czf - -C "$root/lhc-joy/extension" --exclude='settings/settings.ini.php' joy | base64 -w0)
  FAQ_TXT=$(ssh cpbee 'cat /etc/tvchat/faq.txt' 2>/dev/null) || FAQ_TXT=""
  [[ -n $FAQ_TXT ]] || FAQ_TXT=$(cat "$root/configure/faq.example.txt")
  FAQ_B64=$(printf '%s\n' "$FAQ_TXT" | base64 -w0)
fi

read -r -d '' REMOTE <<'REMOTE_EOF' || true
set -euo pipefail
ROOT=/home/whatwelo/www/ul/chat
EXT=$ROOT/extension/joy
JOY=/home/whatwelo/joy
SET=$ROOT/settings/settings.ini.php
DEP_ID=${DEP_ID:-2}; OPERATOR_ID=${OPERATOR_ID:-2}
dry() { [ "${DRY_RUN:-0}" = 1 ]; }
on_list() { php -r '$c = include $argv[1]; $f = function($a) use (&$f) { foreach ($a as $k => $v) { if ($k === "extensions" && is_array($v)) return $v; if (is_array($v) && ($r = $f($v)) !== null) return $r; } return null; }; $e = $f($c); echo $e === null ? "NOLIST" : implode(",", $e);' "$SET"; }

case "$ACTION" in
disable) dry || { mkdir -p $JOY; touch $JOY/DISABLED; }; echo "she is silent (the kill file exists)";;
enable)  dry || rm -f $JOY/DISABLED; echo "she answers (no kill file)";;
status)
  echo "extension files:   $( [ -f $EXT/bootstrap/bootstrap.php ] && echo present || echo MISSING )"
  echo "settings file:     $( [ -f $EXT/settings/settings.ini.php ] && echo present || echo MISSING )"
  echo "FAQ:               $( [ -f $JOY/faq.txt ] && echo "$(grep -c '^Q:' $JOY/faq.txt) entries" || echo MISSING )"
  echo "listed in site:    $(on_list)"
  echo "silenced (kill file): $( [ -f $JOY/DISABLED ] && echo YES || echo no )"
  echo "questions log:     $( [ -f $JOY/questions.log ] && echo "$(wc -l < $JOY/questions.log) lines" || echo none yet )"
  n=$(grep -c '\[joy\]' /home/whatwelo/logs/php.error.log 2>/dev/null || true); echo "php errors about her: ${n:-0}";;
uninstall)
  dry || { cp -p "$SET" "/home/whatwelo/BAK/settings.ini.php.joy-off.$(date +%Y%m%d-%H%M%S)"; }
  php -r '$f = $argv[1]; $c = include $f; $g = function(&$a) use (&$g) { foreach ($a as $k => &$v) { if ($k === "extensions" && is_array($v)) { $v = array_values(array_diff($v, ["joy"])); return true; } if (is_array($v) && $g($v)) return true; } return false; }; $g($c); if (getenv("DRY_RUN") !== "1") file_put_contents($f, "<?php\n return " . var_export($c, true) . ";\n?>"); echo "joy taken out of the extension list\n";' "$SET";;
install)
  [ -d $ROOT/extension ] || { echo "$ROOT/extension is not there" >&2; exit 1; }
  echo "== PHP: $(php -v | head -1 | cut -c1-30) mbstring: $(php -m | grep -c -i mbstring)"
  if dry; then echo "DRY RUN: would put the extension in $EXT, her FAQ and settings in $JOY, list 'joy' in the site's extensions, mark operator $OPERATOR_ID always online, and leave her silent"; echo "RESULT: OK"; exit 0; fi
  mkdir -p $JOY /home/whatwelo/BAK; chmod 700 $JOY
  # 1. the code (never the settings file she may have)
  [ -f $EXT/settings/settings.ini.php ] && cp -p $EXT/settings/settings.ini.php $JOY/settings.keep || true
  rm -rf $EXT/bootstrap $EXT/classes
  mkdir -p $ROOT/extension
  echo "$TAR_B64" | base64 -d | tar xz -C $ROOT/extension
  [ -f $JOY/settings.keep ] && { cp -p $JOY/settings.keep $EXT/settings/settings.ini.php; rm -f $JOY/settings.keep; }
  for f in $EXT/bootstrap/bootstrap.php $EXT/classes/JoyBrain.php $EXT/classes/JoyJokes.php; do php -l $f >/dev/null || { echo "$f does not pass php -l" >&2; exit 1; }; done
  echo "code: installed in $EXT"
  # 2. her settings (written once: edit them there)
  if [ ! -f $EXT/settings/settings.ini.php ]; then
    sed -e "s/'dep_id'        => [0-9]*/'dep_id'        => $DEP_ID/" -e "s/'operator_id'   => [0-9]*/'operator_id'   => $OPERATOR_ID/" $EXT/settings/settings.example.php > $EXT/settings/settings.ini.php
    echo "settings: written ($EXT/settings/settings.ini.php): department $DEP_ID, operator $OPERATOR_ID"
  else echo "settings: kept as they are"; fi
  # 3. her FAQ (from cpbee, only if there is none)
  if [ ! -f $JOY/faq.txt ] || [ "${FORCE_FAQ:-0}" = 1 ]; then
    [ -f $JOY/faq.txt ] && cp -p $JOY/faq.txt $JOY/faq.txt.bak.$(date +%Y%m%d-%H%M%S)
    echo "$FAQ_B64" | base64 -d > $JOY/faq.txt; chmod 600 $JOY/faq.txt
    echo "FAQ: written ($(grep -c '^Q:' $JOY/faq.txt) entries) in $JOY/faq.txt"
  else echo "FAQ: kept ($JOY/faq.txt: $(grep -c '^Q:' $JOY/faq.txt) entries)"; fi
  # 4. silent until enabled
  [ -f $JOY/DISABLED ] || { [ -f $JOY/.installed ] || touch $JOY/DISABLED; }
  touch $JOY/.installed
  # 5. does the brain work on THIS php?
  php -r 'require $argv[1] . "/classes/JoyBrain.php"; require $argv[1] . "/classes/JoyJokes.php"; $f = JoyBrain::parseFaq(file_get_contents($argv[2])); $s = []; $r = JoyBrain::reply($f, $s, "whta should I wear?"); echo strpos(implode(" ", $r["say"]), "comfortable clothing") !== false ? "self-test: ok (a typo is mended, the FAQ answers)\n" : "self-test: FAILED\n";' "$EXT" "$JOY/faq.txt"
  # 6. her operator is always online (the site then shows the chat as online without anything reporting activity)
  php -r '$c = include $argv[1]; $d = $c["settings"]["db"]; try { $p = new PDO("mysql:host=" . $d["host"] . ";port=" . (isset($d["port"]) ? $d["port"] : 3306) . ";dbname=" . $d["database"], $d["user"], $d["password"]); $s = $p->prepare("UPDATE lh_userdep SET always_on = 1 WHERE user_id = ?"); $s->execute([(int)$argv[2]]); $q = $p->prepare("SELECT COUNT(*) FROM lh_userdep WHERE user_id = ? AND always_on = 1"); $q->execute([(int)$argv[2]]); echo "operator " . $argv[2] . ": always online in " . $q->fetchColumn() . " department(s)\n"; } catch (Throwable $e) { echo "always-online: FAILED (" . get_class($e) . ")\n"; }' "$SET" "$OPERATOR_ID"
  # 7. list the extension in the site's settings (backup, lint, rollback)
  if on_list | tr , '\n' | grep -qx joy; then echo "site settings: joy is already listed"
  else
    bak=/home/whatwelo/BAK/settings.ini.php.joy-on.$(date +%Y%m%d-%H%M%S); ( umask 077; cp -p "$SET" "$bak" )
    php -r '$f = $argv[1]; $c = include $f; $g = function(&$a) use (&$g) { foreach ($a as $k => &$v) { if ($k === "extensions" && is_array($v)) { $v[] = "joy"; return true; } if (is_array($v) && $g($v)) return true; } return false; }; if (!$g($c)) { fwrite(STDERR, "no extensions list found\n"); exit(1); } file_put_contents($f, "<?php\n return " . var_export($c, true) . ";\n?>");' "$SET" || { cp -p "$bak" "$SET"; echo "could not edit the site settings: the old file is back" >&2; exit 1; }
    php -l "$SET" >/dev/null || { cp -p "$bak" "$SET"; echo "the edited site settings failed php -l: the old file is back" >&2; exit 1; }
    echo "site settings: 'joy' listed (the old file: $bak)"
  fi
  echo "extensions now: $(on_list)"
  echo "she is $( [ -f $JOY/DISABLED ] && echo SILENT || echo ANSWERING ) (kill file: $JOY/DISABLED)";;
esac
echo "RESULT: OK"
REMOTE_EOF

b64=$(printf '%s\n' "$REMOTE" | base64 -w0)
say "connecting to the web host with ssh-wwl (ACTION=$ACTION) ..."
out=$(timeout 240 ssh-wwl "echo $b64 | base64 -d > /dev/shm/joy_remote.sh; ACTION='$ACTION' DEP_ID='${DEP_ID:-2}' OPERATOR_ID='${OPERATOR_ID:-2}' DRY_RUN='${DRY_RUN:-0}' FORCE_FAQ='${FORCE_FAQ:-0}' TAR_B64='$TAR_B64' FAQ_B64='$FAQ_B64' bash /dev/shm/joy_remote.sh; rc=\$?; rm -f /dev/shm/joy_remote.sh; exit \$rc" 2>&1 | grep -v '^spawn' | tr -d '\r') || true
printf '%s\n' "$out"
printf '%s\n' "$out" | grep -q '^RESULT: OK$' || die "it did not finish on the web host. Paste the output above to Claude."
