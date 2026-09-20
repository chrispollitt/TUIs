#!/usr/bin/env bash
#
# setup.sh - post-build install + first-run configuration wizard for tvmail.
#
# Run this after ./build.sh.  It:
#   * asks whether to install for just you (~/.local) or system-wide
#     (/usr/local), then runs  cmake --install
#   * offers to write ~/.config/tvmail/tvmail.conf (local vs. remote mode;
#     IMAP/SMTP host if you don't already have GNU Mailutils' ~/.mail set up
#     via ./configure.sh - tvmail-backend falls back to that automatically)
#   * offers a couple of smoke tests (ping/mode, then a real inbox listing)
#
# Re-runnable; every step is a yes/no offer, so declining one just skips it.
#
# Usage:
#   ./setup.sh [options]
#
# Options:
#   --prefix PATH   skip the local/system-wide question, install straight here
#   -y, --yes       accept every "offer to ..." prompt (unattended)
#   --assume-no     decline every "offer to ..." prompt (install only)
#   -h, --help      this text
#
set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BUILD="$here/build"

PREFIX=""
ASSUME=""

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix)    PREFIX="${2:?}"; shift 2 ;;
    --prefix=*)  PREFIX="${1#--prefix=}"; shift ;;
    -y|--yes)    ASSUME=yes; shift ;;
    --assume-no) ASSUME=no; shift ;;
    -h|--help)   sed -n '2,/^set -euo pipefail/p' "$0" | sed 's/^# \{0,1\}//; $d'; exit 0 ;;
    *) echo "setup.sh: unknown argument: $1" >&2; exit 2 ;;
  esac
done

log()  { printf '  %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
step() { echo; echo "== $* =="; }

ask() {
  [ "$ASSUME" = yes ] && { log "$1 -> yes (--yes)"; return 0; }
  [ "$ASSUME" = no ]  && { log "$1 -> no (--assume-no)"; return 1; }
  def="${2:-Y}"; suf="[Y/n]"; [ "$def" = N ] && suf="[y/N]"
  printf '%s %s ' "$1" "$suf"
  ans=""; read -r ans || true; : "${ans:=$def}"
  case "$ans" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

readval() {
  printf '%s [%s]: ' "$1" "$2" >&2
  ans=""; read -r ans || true; : "${ans:=$2}"
  printf '%s\n' "$ans"
}

readsecret() {
  printf '%s: ' "$1" >&2
  stty -echo 2>/dev/null || true
  ans=""; read -r ans || true
  stty echo 2>/dev/null || true
  echo >&2
  printf '%s\n' "$ans"
}

[ -e "$BUILD/CMakeCache.txt" ] || die "no build/ found - run ./build.sh first"
command -v cmake >/dev/null 2>&1 || die "cmake not found - run ./configure.sh first"

os=$(uname -s 2>/dev/null || echo unknown)
IS_CYGWIN=0; case "$os" in CYGWIN*|MSYS*|MINGW*) IS_CYGWIN=1 ;; esac
SUDO=""
[ "$IS_CYGWIN" = 0 ] && [ "$(id -u)" != 0 ] && command -v sudo >/dev/null 2>&1 && SUDO=sudo

# --------------------------------------------------------------------------
# Install location
# --------------------------------------------------------------------------
step "Install location"
if [ -z "$PREFIX" ]; then
  echo "Install tvmail for just you (~/.local) or system-wide (/usr/local)?"
  choice=$(readval "local or system" "local")
  case "$choice" in s|S|system|sys|Sys*) PREFIX=/usr/local ;; *) PREFIX="$HOME/.local" ;; esac
fi
log "prefix: $PREFIX"

# --------------------------------------------------------------------------
# Install
# --------------------------------------------------------------------------
step "Install tvmail"
target="$PREFIX"; [ -e "$target" ] || target=$(dirname "$PREFIX")
if [ -w "$target" ]; then
  cmake --install "$BUILD" --prefix "$PREFIX"
elif [ -n "$SUDO" ]; then
  log "no write access to $PREFIX - using sudo"
  $SUDO cmake --install "$BUILD" --prefix "$PREFIX"
else
  warn "no write access to $PREFIX and no sudo - trying anyway"
  cmake --install "$BUILD" --prefix "$PREFIX"
fi
BIN_DIR="$PREFIX/bin"
log "installed tvmail, tvmail-backend, pop-pull -> $BIN_DIR"

case ":$PATH:" in
  *":$BIN_DIR:"*) : ;;
  *) warn "$BIN_DIR is not on \$PATH - add  export PATH=\"$BIN_DIR:\$PATH\"  to your shell rc" ;;
esac

# --------------------------------------------------------------------------
# Configure tvmail
# --------------------------------------------------------------------------
step "Configure tvmail"
if ask "Write ~/.config/tvmail/tvmail.conf now?"; then
  echo "Local mode (this box owns the mailstore) or remote mode (talk IMAP/SMTP to a master)?"
  mode=$(readval "local or remote" "remote")
  conf_dir="$HOME/.config/tvmail"
  mkdir -p "$conf_dir"
  conf="$conf_dir/tvmail.conf"
  [ -e "$conf" ] && cp -p "$conf" "$conf.bak.$(date +%Y%m%d-%H%M%S)" && log "backed up existing $conf"

  case "$mode" in
    l|L|local|Local)
      cat > "$conf" <<EOF
# ~/.config/tvmail/tvmail.conf - generated $(date) by setup.sh
[service]
mode = local
EOF
      log "wrote $conf (local mode - no IMAP/SMTP needed)"
      ;;
    *)
      if [ -e "$HOME/.mail" ]; then
        log "found ~/.mail - tvmail-backend will read the IMAP/SMTP host from there"
        cat > "$conf" <<EOF
# ~/.config/tvmail/tvmail.conf - generated $(date) by setup.sh
# host/port/user come from ~/.mail (GNU Mailutils, see ./configure.sh);
# this just forces remote mode explicitly.
[service]
mode = remote
EOF
        log "wrote $conf"
      else
        warn "no ~/.mail found - run ./configure.sh first if you want to share its"
        warn "settings with mail(1), or answer the questions below to set tvmail up alone."
        master=$(readval "master hostname" "cmpi")
        imap_port=$(readval "IMAP port" "993")
        imap_ssl=$(readval "IMAP uses TLS (true/false)" "true")
        smtp_port=$(readval "SMTP port" "587")
        smtp_starttls=$(readval "SMTP uses STARTTLS (true/false)" "true")
        user=$(readval "username" "$(id -un)")
        cat > "$conf" <<EOF
# ~/.config/tvmail/tvmail.conf - generated $(date) by setup.sh
[service]
mode = remote

[imap]
host = ${master}
port = ${imap_port}
ssl  = ${imap_ssl}
user = ${user}

[smtp]
host     = ${master}
port     = ${smtp_port}
starttls = ${smtp_starttls}
user     = ${user}
EOF
        log "wrote $conf"
        if ask "Add the password to ~/.netrc now (mode 600)?"; then
          pw=$(readsecret "password for ${user}@${master}")
          netrc="$HOME/.netrc"
          tmp=$(mktemp)
          [ -e "$netrc" ] && grep -v "^machine ${master} login ${user} " "$netrc" > "$tmp" || : > "$tmp"
          printf 'machine %s login %s password %s\n' "$master" "$user" "$pw" >> "$tmp"
          mv "$tmp" "$netrc"
          chmod 600 "$netrc"
          log "wrote $netrc (mode 600)"
        else
          log "no password stored - set \$TVMAIL_IMAP_PASS / \$TVMAIL_SMTP_PASS, add one to"
          log "$HOME/.netrc yourself, or run ./configure.sh to set up ~/.mu-tickets on this box."
        fi
      fi
      ;;
  esac
  log "every option (spam tag, folder overrides, ...): $here/configure/tvmail.conf.example"
fi

# --------------------------------------------------------------------------
# Test tvmail
# --------------------------------------------------------------------------
step "Test tvmail"
BACKEND_BIN="$BIN_DIR/tvmail-backend"
[ -x "$BACKEND_BIN" ] || BACKEND_BIN="$here/backend/tvmail-backend"

if ask "Run a quick smoke test (ping + mode) now?"; then
  out=$("$BACKEND_BIN" ping 2>&1)  && log "ping -> $out"  || warn "ping failed: $out"
  out=$("$BACKEND_BIN" mode 2>&1)  && log "mode -> $out"  || warn "mode failed: $out"
fi

if ask "Attempt a live connection (list your inbox) now?" N; then
  if out=$("$BACKEND_BIN" list spool 2>&1 | head -5); then
    log "looks alive:"
    printf '%s\n' "$out" | sed 's/^/    /'
  else
    warn "could not list the inbox - check tvmail.conf / ~/.mail / ~/.netrc"
    printf '%s\n' "$out" | sed 's/^/    /'
  fi
fi

tv="$BIN_DIR/tvmail"; [ -e "$tv.exe" ] && tv="$tv.exe"
if [ -x "$tv" ]; then
  log "launch it with:  PATH=\"$BIN_DIR:\$PATH\"  tvmail"
else
  warn "tvmail binary not found in $BIN_DIR - did the install step above succeed?"
fi

echo
log "Done."
