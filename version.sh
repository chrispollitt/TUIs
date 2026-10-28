#!/usr/bin/env sh
# version.sh - tvmail and tvchat share ONE version, kept in VERSION beside this file.
#
#   ./version.sh            show it, and check that every place that records it agrees
#   ./version.sh 1.4.0      set VERSION and every one of those places to 1.4.0
#
# The places: each app's CMakeLists.txt (project VERSION - what dist.sh and the
# package names use), the #define default in each app's header, and each backend's
# __version__ (what `tvmail-backend --version` and the X-Mailer header say).  A test
# (tvchat's chat_unit) fails if they drift apart.  A release is three git tags at one commit
# (vX.Y.Z, tvmail-vX.Y.Z, tvchat-vX.Y.Z) - tags only, no binaries.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$here"

# file | sed pattern that prints the version found | sed script that replaces it (@NEW@ = the new one)
places() {
  cat <<'EOF'
tvchat/CMakeLists.txt|s/^project(tvchat VERSION \([0-9.]*\).*/\1/p|s/^\(project(tvchat VERSION \)[0-9.]*/\1@NEW@/
tvmail/CMakeLists.txt|s/^project(tvmail VERSION \([0-9.]*\).*/\1/p|s/^\(project(tvmail VERSION \)[0-9.]*/\1@NEW@/
tvchat/src/tvchat.h|s/^#define TVCHAT_VERSION "\([0-9.]*\).*/\1/p|s/^\(#define TVCHAT_VERSION "\)[0-9.]*/\1@NEW@/
tvmail/src/tvmail.h|s/^#define TVMAIL_VERSION "\([0-9.]*\).*/\1/p|s/^\(#define TVMAIL_VERSION "\)[0-9.]*/\1@NEW@/
tvchat/backend/tvchat-backend|s/^__version__ = "\([0-9.]*\).*/\1/p|s/^\(__version__ = "\)[0-9.]*/\1@NEW@/
tvmail/backend/tvmail-backend|s/^__version__ = "\([0-9.]*\).*/\1/p|s/^\(__version__ = "\)[0-9.]*/\1@NEW@/
EOF
}

if [ $# -ge 1 ]; then
  new=$1
  case $new in
    [0-9]*.[0-9]*.[0-9]*) : ;;
    *) echo "version.sh: '$new' is not X.Y.Z" >&2; exit 2 ;;
  esac
  case $new in *[!0-9.]*) echo "version.sh: '$new' is not X.Y.Z" >&2; exit 2 ;; esac
  places | while IFS='|' read -r file _ repl; do
    sed -i "$(printf '%s' "$repl" | sed "s/@NEW@/$new/")" "$file"
  done
  printf '%s\n' "$new" > VERSION
fi

want=$(cat VERSION)
out=$(places | while IFS='|' read -r file find _; do
  got=$(sed -n "$find" "$file" | head -1)
  if [ "$got" = "$want" ]; then flag=ok; else flag=DIFFERS; fi
  printf '%-34s %-8s %s\n' "$file" "$got" "$flag"
done)
echo "VERSION                            $want"
echo "$out"
case $out in
  *DIFFERS*) echo "version.sh: the versions disagree - ./version.sh $want sets them all" >&2; exit 1 ;;
esac
