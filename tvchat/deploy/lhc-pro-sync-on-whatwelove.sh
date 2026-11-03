#!/bin/bash
# RUN ON: whatwelove.org (the cPanel web host, user whatwelo; log in with ssh-wwl).  NOT on cpbee.  Your own ad hoc tool (for upgrades): TO_BE_RUN/REUSABLE/ has a stub, the real script is in the repo (tvchat/deploy).
# WHY:    chat.u-l.ca (fun and testing) and chat.universal-laughter.com (pro only) are two SEPARATE Live Helper Chat installs:
#         same code, own database, own operators, own API key, own settings, cache and uploads, so nothing can bleed across.
#         This copies the code of the fun site into the pro site's folder, and does it again after an upgrade.
# WHAT:   SRC (the fun site)  /home/whatwelo/www/u-l/chat   ->   DST (the pro site)  /home/whatwelo/www/ul/chat
#         Copied: all code (lib, design, modules, translations, ...).  NEVER copied, never deleted, in DST:
#           settings/settings.ini.php (the pro site's own database and settings), cache/, var/ (generated and uploaded files),
#           error_log, php.ini, .user.ini, cgi-bin/, .well-known/ (cPanel's and the certificate's), and .htaccess (cPanel's block there is
#           kept; the Live Helper Chat lines - CORS headers, wasm - are APPENDED once).
#         First run only: makes DST/cache/* and DST/var/* (empty skeletons).
#         Last, it refuses (and says so loudly) if the two sites turn out to use the same database.
# FIRST TIME, after this script, by hand:
#         1. cPanel > MySQL Databases: make a NEW database and user for the pro site (not the fun site's).
#         2. Open https://chat.universal-laughter.com/index.php/install/install and give it that database.
#         3. In its admin: an operator `joy` (the pro bot; REST API use + chat permissions), a REST API key for her (IP restriction: your
#            ISP block, as for the fun site), a department.  Then on cpbee: floyd-lhc-pro-on-cpbee.sh.
# UPGRADES: upgrade the fun site as usual, run this script again, then System > Update (database) in BOTH admins.
# SAFE:   re-runnable.  DRY_RUN=1 lists what would be copied and changes nothing.
set -euo pipefail

SRC=${SRC:-/home/whatwelo/www/u-l/chat}
DST=${DST:-/home/whatwelo/www/ul/chat}

say() { printf '%s\n' "$*"; }
die() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }

[[ -f $SRC/index.php && -f $SRC/settings/settings.ini.php ]] || die "$SRC does not look like the fun site's Live Helper Chat (no index.php / settings.ini.php)"
[[ -d $DST ]] || die "$DST does not exist: make the subdomain in cPanel first"
[[ $(cd "$SRC" && pwd -P) != $(cd "$DST" && pwd -P) ]] || die "SRC and DST are the same folder"
command -v rsync >/dev/null || die "rsync is not installed here"

RS=(rsync -a --delete --itemize-changes
    --exclude=/settings/settings.ini.php --exclude=/cache/ --exclude=/var/ --exclude=/error_log
    --exclude=/.htaccess --exclude=/.user.ini --exclude=/php.ini --exclude=/cgi-bin/ --exclude=/.well-known/ --exclude=/.git/)

if [[ ${DRY_RUN:-0} == 1 ]]; then
  say "DRY RUN: what would change in $DST (the first lines):"
  "${RS[@]}" -n "$SRC/" "$DST/" | head -40
  say "  ... ($("${RS[@]}" -n "$SRC/" "$DST/" | wc -l) changes in all)"
  [[ -d $DST/cache ]] || say "  first run: would make $DST/cache/* and $DST/var/*"
  grep -q 'application/wasm' "$DST/.htaccess" 2>/dev/null || say "  would append the Live Helper Chat lines to $DST/.htaccess"
  say "Nothing was changed."
  exit 0
fi

say "copying the code $SRC -> $DST ..."
"${RS[@]}" "$SRC/" "$DST/" | tail -5
say "  done ($(du -sh "$DST" | cut -f1) in $DST)"

if [[ ! -d $DST/cache ]]; then
  say "first run: making the cache and var skeletons ..."
  for d in $(cd "$SRC/cache" && ls -d */ 2>/dev/null); do mkdir -p "$DST/cache/$d"; done
  rsync -a --exclude='/autoloads/*' "$SRC/var/" "$DST/var/"
  mkdir -p "$DST/var/autoloads"
fi

if ! grep -q 'application/wasm' "$DST/.htaccess" 2>/dev/null; then
  { printf '\n# Live Helper Chat (copied from the fun site)\n'; cat "$SRC/.htaccess"; printf '\n'; } >> "$DST/.htaccess"
  say "appended the Live Helper Chat lines to $DST/.htaccess"
fi

dbname() { php -r '$c = @include $argv[1]; echo is_array($c) ? ($c["settings"]["db"]["database"] ?? "") : "";' "$1" 2>/dev/null || true; }
if [[ -f $DST/settings/settings.ini.php ]]; then
  a=$(dbname "$SRC/settings/settings.ini.php"); b=$(dbname "$DST/settings/settings.ini.php")
  say "database of the fun site: ${a:-?}    database of the pro site: ${b:-?}"
  if [[ -n $a && $a == "$b" ]]; then
    die "THE TWO SITES USE THE SAME DATABASE ($a). Stop: give the pro site its own in $DST/settings/settings.ini.php (or reinstall it) before anyone uses it."
  fi
  say "OK: separate databases. After an upgrade: System > Update (database) in both admins."
else
  say "the pro site is not installed yet: do the first-time steps in this script's header (new database, then the web installer)."
fi
