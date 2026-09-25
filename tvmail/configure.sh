#!/usr/bin/env bash
#
# configure.sh - pre-build environment wizard for tvmail.
#
# Sets up everything OUTSIDE the tvmail source tree that it (and plain
# mail(1)) needs, before you ./build.sh:
#
#   * dev tools to build tvmail (cmake, a C++ compiler, make, git, python3)
#   * the mail system itself - delegated to mail-setup, a separate project
#     (github.com/chrispollitt/POSIX, folder mail-setup) that this script
#     fetches into third_party/POSIX/mail-setup the way build.sh fetches
#     tvision, then runs its wizard:
#       master  - Postfix (smarthost relay), Dovecot (IMAP), a puller
#                 (pop-pull or getmail, run by F3 via mail-pull)
#       client  - a /usr/sbin/sendmail shim that forwards to the master
#       both    - GNU Mailutils + ~/.mail / ~/.mu-tickets (tvmail reads those too)
#
# Re-runnable; every step is a yes/no offer, so declining one just skips it.
#
# Usage:
#   ./configure.sh [options]
#
# Options:
#   --role master|client   skip the role question       (passed to mail-setup)
#   --puller pop-pull|getmail|none                      (passed to mail-setup)
#   -y, --yes              accept every "offer to ..." prompt (unattended)
#   --assume-no            decline every "offer to ..." prompt (checks only)
#   -h, --help             this text
#
# Environment:
#   MAIL_SETUP_DIR    use this mail-setup checkout instead (e.g. your own
#                     ../../POSIX/mail-setup while working on it)
#   MAIL_SETUP_REPO   clone from here instead of github.com/chrispollitt/POSIX
#
set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

PASS=()          # forwarded to mail-setup.sh
ASSUME=""

while [ $# -gt 0 ]; do
  case "$1" in
    --role|--puller) PASS+=("$1" "${2:?}"); shift 2 ;;
    --role=*|--puller=*) PASS+=("$1"); shift ;;
    -y|--yes)    ASSUME=yes; PASS+=(--yes); shift ;;
    --assume-no) ASSUME=no;  PASS+=(--assume-no); shift ;;
    -h|--help)   sed -n '2,/^set -euo pipefail/p' "$0" | sed 's/^# \{0,1\}//; $d'; exit 0 ;;
    *) echo "configure.sh: unknown argument: $1" >&2; exit 2 ;;
  esac
done

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

log "OS: $os   package manager: ${PKG:-none found}"

# --------------------------------------------------------------------------
# Dev tools to build tvmail
# --------------------------------------------------------------------------
step "Development tools"
PY3=$(command -v python3 2>/dev/null || true)
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
  else
    warn "build.sh will fail without:$need"
  fi
else
  log "cmake, git, a C++ compiler, make, python3: all found"
fi

# --------------------------------------------------------------------------
# mail-setup (third_party/POSIX/mail-setup), like tvision in build.sh
# --------------------------------------------------------------------------
step "mail-setup (Postfix / Dovecot / pop-pull or getmail / Mailutils)"
POSIX_DIR="$here/third_party/POSIX"
MAIL_SETUP_DIR="${MAIL_SETUP_DIR:-$POSIX_DIR/mail-setup}"
MAIL_SETUP_REPO="${MAIL_SETUP_REPO:-https://github.com/chrispollitt/POSIX}"

if [ ! -f "$MAIL_SETUP_DIR/mail-setup.sh" ]; then
  [ "$MAIL_SETUP_DIR" = "$POSIX_DIR/mail-setup" ] \
    || die "MAIL_SETUP_DIR=$MAIL_SETUP_DIR has no mail-setup.sh"
  command -v git >/dev/null 2>&1 || die "git not found - needed to fetch mail-setup"
  echo ">> cloning $MAIL_SETUP_REPO (mail-setup only) ..."
  rm -rf "$POSIX_DIR"
  # sparse + blobless: just the mail-setup folder; plain shallow clone if the
  # local git or the server can't do that
  if git clone --depth 1 --filter=blob:none --sparse "$MAIL_SETUP_REPO" "$POSIX_DIR" \
     && git -C "$POSIX_DIR" sparse-checkout set mail-setup; then
    :
  else
    rm -rf "$POSIX_DIR"
    git clone --depth 1 "$MAIL_SETUP_REPO" "$POSIX_DIR"
  fi
  [ -f "$MAIL_SETUP_DIR/mail-setup.sh" ] || die "cloned $MAIL_SETUP_REPO but found no mail-setup/mail-setup.sh"
fi
log "mail-setup: $MAIL_SETUP_DIR"

bash "$MAIL_SETUP_DIR/mail-setup.sh" --caller tvmail ${PASS[@]+"${PASS[@]}"}

echo
log "Done.  Next:   ./build.sh   then   ./setup.sh"
