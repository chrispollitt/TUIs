#!/usr/bin/env sh
# Remove what build.sh / test.sh / dist.sh generate.
#
#   ./clean.sh          build/, dist/, __pycache__/
#   ./clean.sh --all    ...plus ../tvcommon/third_party/ (Turbo Vision and
#                       POSIX, shared with tvmail), so the next ./build.sh
#                       re-fetches (and re-patches) them; a POSIX checkout
#                       with changes of your own in it is kept
#   ./clean.sh -n       just show what would go
#
# Never touches your files, or anything installed (that's ./uninstall.sh).
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$here"

all=0; dry=0
for a in "$@"; do
  case "$a" in
    --all)        all=1 ;;
    -n|--dry-run) dry=1 ;;
    -h|--help)    sed -n '2,/^set -eu/p' "$0" | sed 's/^# \{0,1\}//; $d'; exit 0 ;;
    *) echo "clean.sh: unknown argument: $a" >&2; exit 2 ;;
  esac
done

rmv() {   # rmv PATH... - remove the ones that exist
  for p in "$@"; do
    [ -e "$p" ] || [ -L "$p" ] || continue
    if [ "$dry" = 1 ]; then echo "would remove $p"; else rm -rf "$p"; echo "removed $p"; fi
  done
}

rmv build dist _inst
find . -path ./third_party -prune -o -type d -name __pycache__ -print |
  while read -r d; do rmv "$d"; done

if [ "$all" = 1 ]; then
  # tvision's only local changes are the patches third_party.sh re-applies
  common="$here/tvcommon"; [ -d "$common" ] || common="$here/../tvcommon"
  rmv "$common/third_party/tvision"
  p="$common/third_party/POSIX"
  if [ -d "$p/.git" ] && [ -n "$(git -C "$p" status --porcelain 2>/dev/null)" ]; then
    echo "kept $p - it has local changes:" >&2
    git -C "$p" status --short >&2
  else
    rmv "$p"
  fi
  [ "$dry" = 1 ] || rmdir "$common/third_party" 2>/dev/null || true
fi
