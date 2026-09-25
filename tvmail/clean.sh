#!/usr/bin/env sh
# Remove what build.sh / test.sh / all.sh / dist.sh generate.
#
#   ./clean.sh          build/, dist/, the *.log files all.sh writes, __pycache__/
#   ./clean.sh --all    ...plus third_party/ (tvision + mail-setup), so the next
#                       ./build.sh re-fetches (and on Cygwin re-patches) them
#   ./clean.sh -n       just show what would go
#
# Never touches your files: BAK/, tmp/, DEVNOTES.txt, anything installed
# (that's ./uninstall.sh), or a third_party/POSIX checkout with changes of
# your own in it (commit/push those first, or delete it by hand).
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
rmv build.log test.log install.log run.log trace.log trace.backend.log trace.result.log
find . -path ./third_party -prune -o -path '*/BAK' -prune -o \
     -type d -name __pycache__ -print | while read -r d; do rmv "$d"; done

if [ "$all" = 1 ]; then
  # tvision's only local changes are the Cygwin patch build.sh re-applies
  rmv third_party/tvision
  p=third_party/POSIX
  if [ -d "$p/.git" ] && [ -n "$(git -C "$p" status --porcelain 2>/dev/null)" ]; then
    echo "kept $p - it has local changes:" >&2
    git -C "$p" status --short >&2
  else
    rmv "$p"
  fi
  [ "$dry" = 1 ] || rmdir third_party 2>/dev/null || true
fi
