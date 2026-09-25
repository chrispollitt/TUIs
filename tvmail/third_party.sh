#!/usr/bin/env sh
# Fetch tvmail's third-party trees into third_party/.  Idempotent: a tree
# that's already there is left alone (use --update to git pull it).
#
#   ./third_party.sh                  both
#   ./third_party.sh tvision          magiblot/tvision        -> third_party/tvision
#   ./third_party.sh mail-setup       chrispollitt/POSIX, just its mail-setup/
#                                     folder (sparse clone)   -> third_party/POSIX
#   ./third_party.sh --update [...]   git pull the existing checkouts
#
# build.sh runs this for tvision (required) and mail-setup (optional - it's
# not needed to compile); configure.sh runs it for mail-setup.
#
# Environment:
#   TVISION_REPO      default https://github.com/magiblot/tvision
#   MAIL_SETUP_REPO   default https://github.com/chrispollitt/POSIX
#   MAIL_SETUP_DIR    use this mail-setup checkout instead - nothing is fetched
#
# Exit 0 = every requested tree is present, 1 = something couldn't be fetched.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
tp="$here/third_party"
TVISION_REPO="${TVISION_REPO:-https://github.com/magiblot/tvision}"
MAIL_SETUP_REPO="${MAIL_SETUP_REPO:-https://github.com/chrispollitt/POSIX}"

update=0
[ "${1:-}" = "--update" ] && { update=1; shift; }
[ $# -gt 0 ] || set -- tvision mail-setup

command -v git >/dev/null 2>&1 || { echo "third_party.sh: git not found" >&2; exit 1; }
mkdir -p "$tp"

pull() {   # pull DIR - fast-forward an existing checkout
  echo ">> updating $1"
  git -C "$1" pull --ff-only --depth 1 || echo "third_party.sh: couldn't update $1 (local changes?)" >&2
}

fetch_tvision() {
  d="$tp/tvision"
  if [ -e "$d/CMakeLists.txt" ]; then
    [ "$update" = 1 ] && pull "$d"
    return 0
  fi
  echo ">> cloning $TVISION_REPO ..."
  rm -rf "$d"
  git clone --depth 1 "$TVISION_REPO" "$d"
}

fetch_mail_setup() {
  if [ -n "${MAIL_SETUP_DIR:-}" ]; then
    [ -f "$MAIL_SETUP_DIR/mail-setup.sh" ] && return 0
    echo "third_party.sh: MAIL_SETUP_DIR=$MAIL_SETUP_DIR has no mail-setup.sh" >&2
    return 1
  fi
  d="$tp/POSIX"
  if [ -f "$d/mail-setup/mail-setup.sh" ]; then
    [ "$update" = 1 ] && pull "$d"
    return 0
  fi
  echo ">> cloning $MAIL_SETUP_REPO (mail-setup only) ..."
  rm -rf "$d"
  # sparse + blobless: just the mail-setup folder; plain shallow clone if the
  # local git or the server can't do that
  if git clone --depth 1 --filter=blob:none --sparse "$MAIL_SETUP_REPO" "$d" \
     && git -C "$d" sparse-checkout set mail-setup; then
    :
  else
    rm -rf "$d"
    git clone --depth 1 "$MAIL_SETUP_REPO" "$d" || return 1
  fi
  if [ ! -f "$d/mail-setup/mail-setup.sh" ]; then
    echo "third_party.sh: $MAIL_SETUP_REPO has no mail-setup/mail-setup.sh (not pushed yet?)" >&2
    rm -rf "$d"
    return 1
  fi
}

rc=0
for what in "$@"; do
  case "$what" in
    tvision)    fetch_tvision    || rc=1 ;;
    mail-setup) fetch_mail_setup || rc=1 ;;
    *) echo "third_party.sh: unknown tree '$what' (tvision, mail-setup)" >&2; exit 2 ;;
  esac
done
exit "$rc"
