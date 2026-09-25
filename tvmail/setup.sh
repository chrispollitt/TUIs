#!/usr/bin/env bash
#
# setup.sh - post-build install + first-run configuration wizard for tvmail.
#
# Run this after ./build.sh.  It:
#   * asks whether to install for just you (~/.local) or system-wide
#     (/usr/local), then runs  cmake --install
#   * offers to write ~/.config/tvmail/tvmail.conf:
#       master - Dovecot is installed here: remote mode against this box's own
#                Dovecot (IMAP 993) + Postfix (localhost:25), never local mode
#                beside Dovecot (!WARNINGS.txt #3); warns if no pull timer
#       remote - a client: the master's IMAP/SMTP (or just mode=remote when
#                GNU Mailutils' ~/.mail from ./configure.sh has the hosts)
#       local  - this box owns /var/mail and nothing else touches it
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

write_netrc() {   # write_netrc HOST USER - ask for the password, (re)place its line
  pw=$(readsecret "password for $2@$1")
  netrc="$HOME/.netrc"
  tmp=$(mktemp)
  [ -e "$netrc" ] && grep -v "^machine $1 login $2 " "$netrc" > "$tmp" || : > "$tmp"
  printf 'machine %s login %s password %s\n' "$1" "$2" "$pw" >> "$tmp"
  mv "$tmp" "$netrc"
  chmod 600 "$netrc"
  log "wrote $netrc (mode 600)"
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
log "installed tvmail, tvmail-backend -> $BIN_DIR"

case ":$PATH:" in
  *":$BIN_DIR:"*) : ;;
  *) warn "$BIN_DIR is not on \$PATH - add  export PATH=\"$BIN_DIR:\$PATH\"  to your shell rc" ;;
esac

# --------------------------------------------------------------------------
# Configure tvmail
# --------------------------------------------------------------------------
step "Configure tvmail"
if ask "Write ~/.config/tvmail/tvmail.conf now?"; then
  # A box running Dovecot is the mail master.  tvmail there must read through
  # Dovecot too, not edit /var/mail behind its back (!WARNINGS.txt, Warning 3).
  have_dovecot=0
  { command -v doveconf >/dev/null 2>&1 || [ -x /usr/sbin/doveconf ] || [ -d /etc/dovecot ]; } \
    && have_dovecot=1
  if [ "$have_dovecot" = 1 ]; then
    echo "Dovecot is installed here, so this is the mail master.  tvmail should read"
    echo "through Dovecot on this box (remote mode, IMAP on localhost) rather than edit"
    echo "/var/mail itself - see !WARNINGS.txt, Warning 3."
    mode=$(readval "master (via this box's Dovecot), local or remote" "master")
    case "$mode" in
      l|L|local|Local)
        warn "local mode next to Dovecot: both edit /var/mail/\$USER, with different locking"
        ask "Use local mode anyway?" N || mode=master ;;
    esac
  else
    echo "Local mode (this box owns the mailstore) or remote mode (talk IMAP/SMTP to a master)?"
    mode=$(readval "local or remote" "remote")
  fi
  conf_dir="$HOME/.config/tvmail"
  mkdir -p "$conf_dir"
  conf="$conf_dir/tvmail.conf"
  [ -e "$conf" ] && cp -p "$conf" "$conf.bak.$(date +%Y%m%d-%H%M%S)" && log "backed up existing $conf"

  case "$mode" in
    m|M|master|Master)
      # IMAP host: whatever ~/.mail names (so its ~/.mu-tickets entry matches),
      # else localhost.  SMTP: straight into Postfix on loopback, no password.
      mu_host=""; mu_user=""
      if [ -e "$HOME/.mail" ]; then
        mu_url=$(sed -n 's|.*mailbox-pattern *"imap[s]*://\([^"]*\)".*|\1|p' "$HOME/.mail" | head -1)
        case "$mu_url" in *@*) mu_user=${mu_url%%@*}; mu_url=${mu_url#*@} ;; esac
        mu_host=${mu_url%%[:/]*}
      fi
      imap_host=${mu_host:-localhost}
      user=${mu_user:-$(id -un)}
      cat > "$conf" <<EOF
# ~/.config/tvmail/tvmail.conf - generated $(date) by setup.sh
# This box is the mail master: read through its own Dovecot, send through its
# own Postfix.  Mail arrives via mail-setup's mail-pull on a timer (F3 here
# only files spam).
[service]
mode = remote

[imap]
host   = ${imap_host}
port   = 993
ssl    = true
user   = ${user}
# Dovecot's stock self-signed cert
verify = false

[smtp]
host     = localhost
port     = 25
starttls = false
# Postfix trusts loopback - no password needed
auth     = false
EOF
      log "wrote $conf (master: Dovecot on ${imap_host}:993, Postfix on localhost:25)"
      if [ -e "$HOME/.mu-tickets" ] && grep -q "@${imap_host}\$" "$HOME/.mu-tickets"; then
        log "password: ~/.mu-tickets already has ${imap_host}"
      elif ask "Add your login password for ${user}@${imap_host} to ~/.netrc (mode 600)?"; then
        write_netrc "$imap_host" "$user"
      else
        log "no password stored - add one to ~/.mu-tickets or ~/.netrc, or set \$TVMAIL_IMAP_PASS"
      fi
      # remote mode's F3 doesn't pull - make sure something does
      if ! { crontab -l 2>/dev/null | grep -q mail-pull; } \
         && ! systemctl --user is-enabled mail-pull.timer >/dev/null 2>&1; then
        warn "nothing pulls your mail on a schedule yet (F3 won't here) - run"
        warn "  ./configure.sh --role master   (it asks how often), or"
        warn "  third_party/POSIX/mail-setup/scripts/configure-mail-pull.sh --timer 5"
      fi
      ;;
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
          write_netrc "$master" "$user"
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
    case "$out" in
      *CERTIFICATE_VERIFY_FAILED*self*signed*)
        warn "that's a self-signed cert on a LAN mail server (mail(1) doesn't check"
        warn "certs either, so this can work there and still fail here) - add to"
        warn "$HOME/.config/tvmail/tvmail.conf:"
        warn "  [imap]"
        warn "  verify = false"
        warn "and the same under [smtp] if sending hits the same error." ;;
    esac
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
