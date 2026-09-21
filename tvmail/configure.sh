#!/usr/bin/env bash
#
# configure.sh - pre-build environment wizard for tvmail.
#
# Sets up everything OUTSIDE the tvmail source tree that it (and plain
# mail(1)) needs, before you ./build.sh:
#
#   * dev tools to build tvmail (cmake, a C++ compiler, make, git, python3)
#   * GNU Mailutils, so mail(1) and tvmail can share ~/.mail / ~/.mu-tickets
#   * depending on this box's role -
#       master  - the one host that owns /var/mail: check/install Postfix,
#                 offer to configure it as a smarthost relay, offer to set
#                 up the pop-pull puller that fetches mail from your ISP
#       client  - a thin box that just talks IMAP/SMTP to a master: offer
#                 to install backend/sendmail as a /usr/sbin/sendmail shim
#                 that forwards straight to the master
#
# Re-runnable; every step is a yes/no offer, so declining one just skips it.
# The heavy MTA/puller lifting is delegated to the scripts already in
# configure/ (configure-sendmail-relay.sh, configure-mail-pull.sh) - this
# script is the wizard that decides which of them to run, and fills in the
# GNU Mailutils + sendmail-shim + dev-tools pieces they don't cover.
#
# Usage:
#   ./configure.sh [options]
#
# Options:
#   --role master|client   skip the role question
#   -y, --yes              accept every "offer to ..." prompt (unattended)
#   --assume-no            decline every "offer to ..." prompt (checks only)
#   -h, --help              this text
#
set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CFGDIR="$here/configure"
BACKEND="$here/backend"

ROLE=""
ASSUME=""        # "" = ask each time / "yes" / "no"

while [ $# -gt 0 ]; do
  case "$1" in
    --role)      ROLE="${2:?}"; shift 2 ;;
    --role=*)    ROLE="${1#--role=}"; shift ;;
    -y|--yes)    ASSUME=yes; shift ;;
    --assume-no) ASSUME=no; shift ;;
    -h|--help)   sed -n '2,/^set -euo pipefail/p' "$0" | sed 's/^# \{0,1\}//; $d'; exit 0 ;;
    *) echo "configure.sh: unknown argument: $1" >&2; exit 2 ;;
  esac
done
case "$ROLE" in ""|master|client) : ;; *) echo "configure.sh: --role must be master or client" >&2; exit 2 ;; esac

log()  { printf '  %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
step() { echo; echo "== $* =="; }

# ask "question" [default Y|N] -> 0 (yes) / 1 (no).  -y/--assume-no short-circuit.
ask() {
  [ "$ASSUME" = yes ] && { log "$1 -> yes (--yes)"; return 0; }
  [ "$ASSUME" = no ]  && { log "$1 -> no (--assume-no)"; return 1; }
  def="${2:-Y}"; suf="[Y/n]"; [ "$def" = N ] && suf="[y/N]"
  printf '%s %s ' "$1" "$suf"
  ans=""; read -r ans || true; : "${ans:=$def}"
  case "$ans" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

# readval "prompt" "default" -> echoes the chosen value (stdout only).
readval() {
  printf '%s [%s]: ' "$1" "$2" >&2
  ans=""; read -r ans || true; : "${ans:=$2}"
  printf '%s\n' "$ans"
}

readsecret() {   # readsecret "prompt" -> echoes the typed value (stdout only)
  printf '%s: ' "$1" >&2
  stty -echo 2>/dev/null || true
  ans=""; read -r ans || true
  stty echo 2>/dev/null || true
  echo >&2
  printf '%s\n' "$ans"
}

# --------------------------------------------------------------------------
# OS / package manager detection
# --------------------------------------------------------------------------
os=$(uname -s 2>/dev/null || echo unknown)
IS_CYGWIN=0; case "$os" in CYGWIN*|MSYS*|MINGW*) IS_CYGWIN=1 ;; esac
IS_MACOS=0;  [ "$os" = Darwin ] && IS_MACOS=1

PKG=""
if   [ "$IS_CYGWIN" = 1 ];                     then PKG=cygwin
elif [ "$IS_MACOS" = 1 ] && command -v brew >/dev/null 2>&1; then PKG=brew
elif command -v apt-get >/dev/null 2>&1;       then PKG=apt
elif command -v dnf     >/dev/null 2>&1;       then PKG=dnf
elif command -v yum     >/dev/null 2>&1;       then PKG=yum
elif command -v pacman  >/dev/null 2>&1;       then PKG=pacman
fi

SUDO=""
if [ "$IS_CYGWIN" = 0 ] && [ "$(id -u)" != 0 ] && command -v sudo >/dev/null 2>&1; then
  SUDO=sudo
fi

cygwin_install() {   # cygwin_install pkg1 pkg2 ...
  setup=""
  for c in "$(command -v setup-x86_64.exe 2>/dev/null || true)" \
           /cygdrive/c/cygwin64/setup-x86_64.exe /cygdrive/c/cygwin/setup-x86_64.exe; do
    [ -n "$c" ] && [ -x "$c" ] && { setup="$c"; break; }
  done
  if [ -z "$setup" ]; then
    warn "can't find setup-x86_64.exe - install these packages by hand: $*"
    return 1
  fi
  pkgs=$(IFS=,; echo "$*")
  log "running: $setup -q -P $pkgs"
  "$setup" -q -P "$pkgs"
}

pkg_install() {   # pkg_install pkg1 pkg2 ...
  [ $# -gt 0 ] || return 0
  case "$PKG" in
    apt)    $SUDO apt-get update -qq || true
            $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y "$@" ;;
    dnf)    $SUDO dnf install -y "$@" ;;
    yum)    $SUDO yum install -y "$@" ;;
    pacman) $SUDO pacman -Sy --noconfirm "$@" ;;
    brew)   brew install "$@" ;;
    cygwin) cygwin_install "$@" ;;
    *) die "no known package manager - install manually: $*" ;;
  esac
}

# python3, for the ~/.mu-tickets percent-encoder below (falls back to sed).
PY3=$(command -v python3 2>/dev/null || true)
enc() {   # enc STRING -> percent-encoded STRING (matches tvmail-backend's decoder)
  if [ -n "$PY3" ]; then
    "$PY3" -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"
  else
    printf '%s' "$1" | sed -e 's/%/%25/g' -e 's/@/%40/g' -e 's/:/%3A/g' -e 's/#/%23/g' \
                            -e 's/&/%26/g' -e 's/(/%28/g' -e 's/)/%29/g' -e 's#/#%2F#g' \
                            -e 's/?/%3F/g' -e 's/ /%20/g'
  fi
}

log "OS: $os   package manager: ${PKG:-none found}"

# --------------------------------------------------------------------------
# Dev tools to build tvmail
# --------------------------------------------------------------------------
step "Development tools"
need=""
command -v cmake >/dev/null 2>&1 || need="$need cmake"
command -v git   >/dev/null 2>&1 || need="$need git"
{ command -v g++ >/dev/null 2>&1 || command -v c++ >/dev/null 2>&1; } || need="$need g++"
command -v make  >/dev/null 2>&1 || need="$need make"
[ -n "$PY3" ] || need="$need python3"

if [ -n "$need" ]; then
  log "missing:$need"
  if ask "Install them now?"; then
    case "$PKG" in
      apt)     pkg_install cmake git g++ make python3 libncursesw5-dev ;;
      dnf|yum) pkg_install cmake git gcc-c++ make python3 ncurses-devel ;;
      pacman)  pkg_install cmake git gcc make python3 ncurses ;;
      brew)    pkg_install cmake git python3 ;;   # Xcode CLT gives g++/make; ncurses is in the base OS
      cygwin)  pkg_install cmake git gcc-g++ make python3 libncursesw-devel ;;
      *) warn "no known package manager - install by hand: cmake git g++ make python3 ncurses(w)-dev" ;;
    esac
    PY3=$(command -v python3 2>/dev/null || true)
  else
    warn "build.sh will fail without:$need"
  fi
else
  log "cmake, git, a C++ compiler, make, python3: all found"
fi

# --------------------------------------------------------------------------
# Role: master (owns the mailstore) or client (thin IMAP/SMTP box)
# --------------------------------------------------------------------------
step "Role"
if [ -z "$ROLE" ]; then
  echo "Is this the mail MASTER (owns /var/mail, runs the real MTA + pop-pull)"
  echo "or a CLIENT (a thin box that talks IMAP/SMTP to a master)?"
  ans=$(readval "master or client" "client")
  case "$ans" in m|M|master|Master) ROLE=master ;; *) ROLE=client ;; esac
fi
log "role: $ROLE"

MASTER_HOST=""
MASTER_PORT=""

if [ "$ROLE" = master ]; then
  # ------------------------------------------------------------------------
  step "Postfix (master MTA)"
  # ------------------------------------------------------------------------
  if command -v postfix >/dev/null 2>&1; then
    log "postfix: found ($(command -v postfix))"
  else
    log "postfix: not found"
    if ask "Install Postfix now?"; then
      case "$PKG" in
        apt|dnf|yum|pacman) pkg_install postfix ;;
        brew) log "macOS ships Postfix already (/usr/sbin/postfix) - nothing to install" ;;
        cygwin) warn "Cygwin has no Postfix package - the master role needs a Linux/Pi box." ;;
        *) warn "no known package manager - install postfix by hand" ;;
      esac
    fi
  fi

  # ------------------------------------------------------------------------
  step "Configure Postfix (smarthost relay + local delivery)"
  # ------------------------------------------------------------------------
  if command -v postfix >/dev/null 2>&1; then
    if ask "Run configure-sendmail-relay.sh now?"; then
      relay_file=$(readval "cPanel-style relay-info file (blank = enter host/user/pass when asked)" "")
      test_addr=$(readval "send a live test message to (blank = skip)" "")
      set -- "$CFGDIR/configure-sendmail-relay.sh"
      [ -n "$relay_file" ] && set -- "$@" --relay-file "$relay_file"
      [ -n "$test_addr" ]  && set -- "$@" --test "$test_addr"
      log "running: $*"
      "$@" || warn "configure-sendmail-relay.sh exited non-zero - see above"
    fi
  else
    warn "no MTA to configure yet - install Postfix first, or answer 'y' above"
  fi
  MASTER_HOST=$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo localhost)
  MASTER_PORT=25

  # ------------------------------------------------------------------------
  step "pop-pull (fetch mail from your ISP into /var/mail)"
  # ------------------------------------------------------------------------
  if ask "Run configure-mail-pull.sh now?"; then
    relay_file=$(readval "same relay-info file (blank = defaults / prompts)" "${relay_file:-}")
    do_test=""; ask "Run one fetch right after configuring?" N && do_test=1
    set -- "$CFGDIR/configure-mail-pull.sh"
    [ -n "$relay_file" ] && set -- "$@" --relay-file "$relay_file"
    [ -n "$do_test" ]    && set -- "$@" --test
    log "running: $*"
    "$@" || warn "configure-mail-pull.sh exited non-zero - see above"
  fi

else
  # ------------------------------------------------------------------------
  step "sendmail shim (forwards straight to the master, no local queue)"
  # ------------------------------------------------------------------------
  if ask "Install backend/sendmail as /usr/sbin/sendmail?"; then
    MASTER_HOST=$(readval "master hostname" "cmpi")
    MASTER_PORT=$(readval "master SMTP port (25 = trusts this LAN via mynetworks, no auth)" "25")
    dest="/usr/sbin/sendmail"
    tmp=$(mktemp)
    sed -e "s/^RELAY_HOST = .*/RELAY_HOST = \"$MASTER_HOST\"  # set by configure.sh/" \
        -e "s/^RELAY_PORT = .*/RELAY_PORT = $MASTER_PORT                     # set by configure.sh/" \
        "$BACKEND/sendmail" > "$tmp"
    chmod 755 "$tmp"
    if [ -e "$dest" ]; then
      $SUDO cp -p "$dest" "$dest.orig.$(date +%Y%m%d-%H%M%S)" 2>/dev/null \
        && log "backed up existing $dest"
    fi
    $SUDO install -m 0755 "$tmp" "$dest" && rm -f "$tmp"
    $SUDO chown root:root "$dest" 2>/dev/null || true
    log "installed $dest -> relays to $MASTER_HOST:$MASTER_PORT"
  fi
fi

# --------------------------------------------------------------------------
# GNU Mailutils
# --------------------------------------------------------------------------
step "GNU Mailutils (mail(1))"

cygwin_build_mailutils() {
  ver="${MAILUTILS_VERSION:-3.17}"
  url="https://ftp.gnu.org/gnu/mailutils/mailutils-${ver}.tar.gz"
  work=$(mktemp -d)
  log "fetching $url"
  if ( cd "$work" \
       && curl -fLO "$url" \
       && tar xf "mailutils-${ver}.tar.gz" \
       && cd "mailutils-${ver}" \
       && ./configure --prefix=/usr/local \
       && make -j"$(nproc 2>/dev/null || echo 2)" \
       && make install ); then
    rm -rf "$work"
    hash -r
    log "installed GNU Mailutils $ver to /usr/local"
  else
    warn "automated build failed - left the source tree in $work"
    warn "check the version list at https://ftp.gnu.org/gnu/mailutils/ and re-run with"
    warn "  MAILUTILS_VERSION=<ver> ./configure.sh --role $ROLE"
    return 1
  fi
}

HAVE_MU=0
if command -v mail >/dev/null 2>&1 && mail --version 2>/dev/null | grep -qi mailutils; then
  HAVE_MU=1
  log "GNU Mailutils: found ($(mail --version 2>/dev/null | head -1))"
else
  log "GNU Mailutils: not found"
  if ask "Install GNU Mailutils now?"; then
    case "$PKG" in
      apt)    pkg_install mailutils ;;
      dnf|yum) pkg_install mailutils ;;
      pacman) pkg_install mailutils || warn "not in the official repos - try the AUR: yay -S mailutils" ;;
      brew)   pkg_install mailutils || warn "no stock brew formula - see https://mailutils.org" ;;
      cygwin)
        warn "Cygwin has no mailutils package - it needs a from-source build:"
        warn "  curl -LO https://ftp.gnu.org/gnu/mailutils/mailutils-<ver>.tar.gz"
        warn "  tar xf mailutils-*.tar.gz && cd mailutils-*"
        warn "  ./configure --prefix=/usr/local && make && make install"
        if ask "Attempt that build now (needs gcc, make, curl - can take a while)?" N; then
          cygwin_build_mailutils || true
        fi ;;
      *) warn "no known package manager - see https://mailutils.org" ;;
    esac
    command -v mail >/dev/null 2>&1 && HAVE_MU=1
  fi
fi
[ "$HAVE_MU" = 1 ] || warn "mail(1) still not on \$PATH - the settings below will be ready for it, just not usable yet"

# --------------------------------------------------------------------------
# Configure GNU Mailutils: ~/.mail + ~/.mu-tickets (tvmail-backend reads
# these too, as a fallback behind tvmail.conf - see README.md).
# --------------------------------------------------------------------------
step "Configure GNU Mailutils (~/.mail, ~/.mu-tickets)"
if ask "Write ~/.mail and ~/.mu-tickets now?"; then
  if [ "$ROLE" = master ]; then
    def_host="${MASTER_HOST:-localhost}"; def_port_imap=143; def_port_smtp=25
  else
    def_host="${MASTER_HOST:-cmpi}"; def_port_imap=143; def_port_smtp="${MASTER_PORT:-25}"
  fi
  imap_host=$(readval "IMAP host" "$def_host")
  imap_port=$(readval "IMAP port" "$def_port_imap")
  imap_user=$(readval "IMAP user" "$(id -un)")
  smtp_host=$(readval "SMTP host" "$def_host")
  smtp_port=$(readval "SMTP port" "$def_port_smtp")

  mail_cfg="$HOME/.mail"
  [ -e "$mail_cfg" ] && cp -p "$mail_cfg" "$mail_cfg.bak.$(date +%Y%m%d-%H%M%S)" \
    && log "backed up existing $mail_cfg"
  cat > "$mail_cfg" <<EOF
# ~/.mail - generated $(date) by configure.sh

mailbox {
    mailbox-pattern "imap://${imap_user}@${imap_host}:${imap_port}/INBOX";
    # base URL for mail(1)'s "+name" folder shorthand (folder +Trash, mail -f +Junk, ...)
    folder "imap://${imap_user}@${imap_host}:${imap_port}/";
};

mailer {
    url "smtp://${smtp_host}:${smtp_port}";
};
EOF
  log "wrote $mail_cfg"

  if ask "Add a ~/.mu-tickets credential for ${imap_user}@${imap_host}?"; then
    pw=$(readsecret "password for ${imap_user}@${imap_host}")
    tickets="$HOME/.mu-tickets"
    [ -e "$tickets" ] && cp -p "$tickets" "$tickets.bak.$(date +%Y%m%d-%H%M%S)" \
      && log "backed up existing $tickets"
    tmp=$(mktemp)
    [ -e "$tickets" ] && grep -v "@${imap_host}\$" "$tickets" > "$tmp" || : > "$tmp"
    printf '*://%s:%s@%s\n' "$(enc "$imap_user")" "$(enc "$pw")" "$imap_host" >> "$tmp"
    mv "$tmp" "$tickets"
    chmod 600 "$tickets"
    log "wrote $tickets (mode 600)"
  fi
fi

# --------------------------------------------------------------------------
# Test mail(1) and sendmail
# --------------------------------------------------------------------------
step "Test mail(1) and sendmail"
if ask "Send a test message to yourself via mail(1) now?"; then
  if command -v mail >/dev/null 2>&1; then
    if echo "tvmail configure.sh test, $(date)" | mail -s "tvmail configure.sh test" "$(id -un)"; then
      log "sent - check your inbox (mail(1), or tvmail once built)"
    else
      warn "mail(1) send failed - check ~/.mail / ~/.mu-tickets and the MTA above"
    fi
  else
    warn "mail(1) not found - install GNU Mailutils first"
  fi
fi

if ask "Send a test message via /usr/sbin/sendmail now?"; then
  if [ -x /usr/sbin/sendmail ]; then
    if printf 'To: %s\nSubject: tvmail configure.sh sendmail test\n\nhi from configure.sh\n' "$(id -un)" \
         | /usr/sbin/sendmail -t; then
      log "sendmail accepted the message (exit 0)"
    else
      warn "/usr/sbin/sendmail exited non-zero"
    fi
  else
    warn "/usr/sbin/sendmail not found/executable"
  fi
fi

echo
log "Done.  Next:   ./build.sh   then   ./setup.sh"
