#!/usr/bin/env bash
#
# uninstall.sh - remove an installed tvmail (the reverse of install.sh / setup.sh).
#
# Looks in ~/.local and /usr/local (or just --prefix) for what
# `cmake --install` put there: bin/tvmail[.exe], bin/tvmail-backend, the two
# man pages and share/doc/tvmail/ - plus anything else build/install_manifest.txt
# lists under that prefix.  Shows the list, asks, then removes it (with sudo
# if the prefix isn't writable).
#
# Also offered, never by default:
#   * <prefix>/bin/pop-pull that tvmail itself installed before the mail setup
#     moved to mail-setup - skipped if a mail-pull wrapper still runs it
#   * --purge: your ~/.config/tvmail/ (tvmail.conf)
#
# Left alone: the mail system mail-setup set up (Postfix, Dovecot, pullers,
# ~/.mail, ~/.mu-tickets, ~/.netrc) - mail(1) uses those too.
#
# Usage:
#   ./uninstall.sh [options]
#
# Options:
#   --prefix PATH   only this prefix
#   --purge         also offer to remove ~/.config/tvmail/
#   -y, --yes       don't ask (the pop-pull and --purge offers still need -y
#                   to be accepted, and then are)
#   -n, --dry-run   just show what would be removed
#   -h, --help      this text
#
set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

PREFIXES=()
PURGE=0
YES=0
DRY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix)   PREFIXES+=("${2:?}"); shift 2 ;;
    --prefix=*) PREFIXES+=("${1#--prefix=}"); shift ;;
    --purge)    PURGE=1; shift ;;
    -y|--yes)   YES=1; shift ;;
    -n|--dry-run) DRY=1; shift ;;
    -h|--help)  sed -n '2,/^set -euo pipefail/p' "$0" | sed 's/^# \{0,1\}//; $d'; exit 0 ;;
    *) echo "uninstall.sh: unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ ${#PREFIXES[@]} -gt 0 ] || PREFIXES=("$HOME/.local" /usr/local)

log()  { printf '  %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }

ask() {   # ask "question" [default Y|N]
  [ "$YES" = 1 ] && { log "$1 -> yes (--yes)"; return 0; }
  def="${2:-Y}"; suf="[Y/n]"; [ "$def" = N ] && suf="[y/N]"
  printf '%s %s ' "$1" "$suf"
  ans=""; read -r ans || true; : "${ans:=$def}"
  case "$ans" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

os=$(uname -s 2>/dev/null || echo unknown)
IS_CYGWIN=0; case "$os" in CYGWIN*|MSYS*|MINGW*) IS_CYGWIN=1 ;; esac

# rm_paths PREFIX PATH... - remove files, then the doc dir if now empty
rm_paths() {
  pfx=$1; shift
  sudo=""
  if [ ! -w "$pfx" ] && [ "$IS_CYGWIN" = 0 ] && [ "$(id -u)" != 0 ]; then
    command -v sudo >/dev/null 2>&1 && sudo=sudo
  fi
  for f in "$@"; do
    if [ "$DRY" = 1 ]; then log "would remove $f"; continue; fi
    $sudo rm -rf -- "$f" && log "removed $f" || warn "couldn't remove $f"
  done
  [ "$DRY" = 1 ] || $sudo rmdir "$pfx/share/doc/tvmail" 2>/dev/null || true
}

found_any=0
for P in "${PREFIXES[@]}"; do
  P="${P%/}"
  files=()
  add() {   # add PATH - once, and not if a directory already listed covers it
    [ -e "$1" ] || return 0
    for g in ${files[@]+"${files[@]}"}; do
      case "$1" in "$g"|"$g"/*) return 0 ;; esac
    done
    files+=("$1")
  }
  # (on Cygwin "bin/tvmail" also "exists" when only tvmail.exe does)
  if [ -e "$P/bin/tvmail.exe" ]; then add "$P/bin/tvmail.exe"; else add "$P/bin/tvmail"; fi
  for f in bin/tvmail-backend \
           share/man/man1/tvmail.1 share/man/man1/tvmail-backend.1 \
           share/doc/tvmail; do
    add "$P/$f"
  done
  # whatever the last `cmake --install` into this prefix recorded
  man="$here/build/install_manifest.txt"
  if [ -r "$man" ]; then
    while IFS= read -r f; do
      case "$f" in "$P"/*) : ;; *) continue ;; esac
      case "$f" in */bin/pop-pull) continue ;; esac      # decided below
      add "$f"
    done < "$man"
  fi

  if [ ${#files[@]} -gt 0 ]; then
    found_any=1
    echo; echo "tvmail in $P:"
    printf '    %s\n' "${files[@]}"
    if [ "$DRY" = 1 ] || ask "Remove these?"; then rm_paths "$P" "${files[@]}"; fi
  fi

  # pop-pull that tvmail's own CMake used to install
  pp="$P/bin/pop-pull"
  if [ -e "$pp" ]; then
    users=""
    for w in $(command -v -a mail-pull 2>/dev/null || true) "$HOME/bin/mail-pull" "$HOME/.local/bin/mail-pull"; do
      [ -f "$w" ] && grep -qF "$pp" "$w" && users="$users $w"
    done
    if [ -n "$users" ]; then
      log "keeping $pp - mail-pull runs it:$users"
    else
      found_any=1
      echo; echo "$pp: installed by older tvmail (now mail-setup's job)."
      if [ "$DRY" = 1 ]; then log "would offer to remove $pp"
      elif [ "$YES" = 1 ] || ask "Remove it too?" N; then rm_paths "$P" "$pp"; fi
    fi
  fi
done

cfg="$HOME/.config/tvmail"
if [ "$PURGE" = 1 ] && [ -e "$cfg" ]; then
  found_any=1
  echo; echo "your tvmail settings: $cfg"
  if [ "$DRY" = 1 ]; then log "would offer to remove $cfg"
  elif [ "$YES" = 1 ] || ask "Remove it?" N; then rm -rf -- "$cfg" && log "removed $cfg"; fi
fi

echo
if [ "$found_any" = 0 ]; then
  log "no installed tvmail found in: ${PREFIXES[*]}"
else
  log "done.  (mail-setup's mail system, ~/.mail, ~/.mu-tickets and ~/.netrc were left alone)"
fi
