#!/usr/bin/env sh
# The third-party trees tvmail and tvchat share, in tvcommon/third_party/.
# Idempotent: a tree that's already there is left alone (--update: git pull
# it), and so is a patch already in place.
#
#   tvcommon/third_party.sh                   both
#   tvcommon/third_party.sh tvision           magiblot/tvision -> third_party/tvision,
#                                             then patched (below); both apps
#                                             need it to compile
#   tvcommon/third_party.sh posix             chrispollitt/POSIX, just mail-setup/
#                                             and chat-setup/ (sparse clone)
#                                             -> third_party/POSIX
#                                             ("mail-setup" is the old name for it)
#   tvcommon/third_party.sh --update [...]    git pull the existing checkouts
#
# Each app's build.sh runs it for tvision (required) and POSIX (optional:
# not needed to compile); tvmail's configure.sh runs it for mail-setup.
#
# Environment:
#   TVISION_REPO      default https://github.com/magiblot/tvision
#   POSIX_REPO        default https://github.com/chrispollitt/POSIX
#                     ($MAIL_SETUP_REPO, its old name, still works)
#   MAIL_SETUP_DIR    use this mail-setup checkout instead - POSIX is not fetched
#
# The tvision patches:
# 1. ncurses input read timeout 10 -> 50 ms (ssh from Windows Terminal);
# 2. wide-char trail cells kept (CJK text without gaps);
# 3. on Cygwin, patches/cygwin_patch.py (FIONREAD &c) - needs python3 >= 3.6
#    ($PYTHON names one).
#
# Exit 0 = every requested tree is present, 1 = something couldn't be fetched.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
tp="$here/third_party"
TVISION_REPO="${TVISION_REPO:-https://github.com/magiblot/tvision}"
POSIX_REPO="${POSIX_REPO:-${MAIL_SETUP_REPO:-https://github.com/chrispollitt/POSIX}}"

update=0
[ "${1:-}" = "--update" ] && { update=1; shift; }
[ $# -gt 0 ] || set -- tvision posix

command -v git >/dev/null 2>&1 || { echo "third_party.sh: git not found" >&2; exit 1; }
mkdir -p "$tp"

pull() {   # pull DIR - fast-forward an existing checkout
  echo ">> updating $1"
  git -C "$1" pull --ff-only --depth 1 || echo "third_party.sh: couldn't update $1 (local changes?)" >&2
}

fetch_tvision() {
  tv="$tp/tvision"
  if [ -e "$tv/CMakeLists.txt" ]; then
    [ "$update" = 1 ] && pull "$tv"
  else
    echo ">> cloning $TVISION_REPO ..."
    rm -rf "$tv"
    git clone --depth 1 "$TVISION_REPO" "$tv" || return 1
  fi
  patch_tvision "$tv"
}

patch_tvision() {
  tv=$1
  # tvision's ncurses input gives up on a byte after readTimeoutMs (10 ms).  Over ssh from
  # Windows Terminal, win32-input-mode key records arrive cut in two, the rest 16-33 ms
  # later, and the cut-off half shows on screen as "[68;32;100;1;0;1_".  Wait longer.
  # (Needed on every platform, since the failing side is the remote one.)
  ncin="$tv/include/tvision/internal/ncursinp.h"
  if grep -q 'readTimeoutMs = 10 ' "$ncin" 2>/dev/null; then
    echo ">> tvision: input read timeout 10 ms -> 50 ms (ssh from Windows Terminal)"
    sed 's/readTimeoutMs = 10 /readTimeoutMs = 50 /' "$ncin" > "$ncin.new" && mv "$ncin.new" "$ncin"
  fi

  # Older tvision's DisplayBuffer::validateCell turns a cell whose text starts with '\0'
  # into a space - and a wide-char trail cell is exactly that, so the trail is lost before
  # the flush looks at it.  Every CJK character then gets a space written over its right
  # half ("日 本 語", one extra column per character).  Upstream fixed it later
  # (ensureCharacterIsPrintable returns early for trails); do the same here.  (All
  # platforms.)
  dbuf="$tv/source/platform/dispbuff.cpp"
  if grep -q 'DisplayBuffer::validateCell' "$dbuf" 2>/dev/null \
     && ! grep -q 'isWideCharTrail()) {} else if' "$dbuf"; then
    echo ">> tvision: keep wide-char trail cells in validateCell (CJK gaps)"
    sed "s/^    if (c == '\\\\0')\$/    if (cell.character.isWideCharTrail()) {} else if (c == '\\\\0')/" \
      "$dbuf" > "$dbuf.new" && mv "$dbuf.new" "$dbuf"
  fi

  case "$(uname -s 2>/dev/null || echo unknown)" in
    CYGWIN*|MINGW*|MSYS*)
      py="${PYTHON:-}"
      if [ -z "$py" ]; then
        for c in python3 python3.12 python3.11 python3.10 python3.9 python3.8 python3.7; do
          command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys;exit(sys.version_info<(3,6))' \
            2>/dev/null && { py="$c"; break; }
        done
      fi
      [ -n "$py" ] || { echo "third_party.sh: no working python3 (>=3.6) found" >&2; return 1; }
      echo ">> applying Cygwin compatibility patch to tvision (with $py)"
      "$py" "$here/patches/cygwin_patch.py" "$tv"
      ;;
  esac
}

# chrispollitt/POSIX, sparse: mail-setup (tvmail's mail system) and
# chat-setup (an IRC server for tvchat, and the script that points tvchat
# at it)
fetch_posix() {
  if [ -n "${MAIL_SETUP_DIR:-}" ]; then
    [ -f "$MAIL_SETUP_DIR/mail-setup.sh" ] && return 0
    echo "third_party.sh: MAIL_SETUP_DIR=$MAIL_SETUP_DIR has no mail-setup.sh" >&2
    return 1
  fi
  d="$tp/POSIX"
  if [ -f "$d/mail-setup/mail-setup.sh" ]; then
    # an older checkout had mail-setup only
    if [ ! -d "$d/chat-setup" ] && git -C "$d" sparse-checkout list >/dev/null 2>&1; then
      git -C "$d" sparse-checkout set mail-setup chat-setup ||
        echo "third_party.sh: couldn't add chat-setup to $d" >&2
    fi
    [ "$update" = 1 ] && pull "$d"
    return 0
  fi
  echo ">> cloning $POSIX_REPO (mail-setup and chat-setup only) ..."
  rm -rf "$d"
  # sparse + blobless: just the two folders; plain shallow clone if the
  # local git or the server can't do that
  if git clone --depth 1 --filter=blob:none --sparse "$POSIX_REPO" "$d" \
     && git -C "$d" sparse-checkout set mail-setup chat-setup; then
    :
  else
    rm -rf "$d"
    git clone --depth 1 "$POSIX_REPO" "$d" || return 1
  fi
  if [ ! -f "$d/mail-setup/mail-setup.sh" ]; then
    echo "third_party.sh: $POSIX_REPO has no mail-setup/mail-setup.sh (not pushed yet?)" >&2
    rm -rf "$d"
    return 1
  fi
}

rc=0
for what in "$@"; do
  case "$what" in
    tvision)          fetch_tvision || rc=1 ;;
    posix|mail-setup) fetch_posix || rc=1 ;;
    *) echo "third_party.sh: unknown tree '$what' (tvision, posix)" >&2; exit 2 ;;
  esac
done
exit "$rc"
