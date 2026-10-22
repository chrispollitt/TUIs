#!/usr/bin/env bash
# The mingw (native Windows) build still compiles: every tvmail source,
# syntax-checked with the mingw-w64 cross g++ against the Turbo Vision
# headers, warnings as errors.  Nothing is linked and tvision itself is not
# built, so it takes seconds; `./build.sh --mingw` does the real build.
# That build has no fork() - chat is off there - and nobody had the cross
# compiler at hand for a while after the src/ split; this keeps it honest.
#
#   tests/mingw_compile.sh TVISION_INCLUDE_DIR [TVCOMMON_DIR]
#
# tvcommon's sources (../tvcommon by default) are checked too: they are part
# of the mingw tvmail.
#
# Exit 0 = compiles, 1 = does not, 77 = no mingw-w64 g++ here (ctest: skipped).
set -u
here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
inc=${1:?usage: mingw_compile.sh TVISION_INCLUDE_DIR [TVCOMMON_DIR]}
common=${2:-$here/../tvcommon}

cxx=
for c in x86_64-w64-mingw32-g++ i686-w64-mingw32-g++; do
  command -v "$c" >/dev/null 2>&1 && { cxx=$c; break; }
done
[ -n "$cxx" ] || { echo "SKIP: no mingw-w64 g++ (x86_64-w64-mingw32-g++)"; exit 77; }
[ -f "$inc/tvision/tv.h" ] || { echo "SKIP: no Turbo Vision headers in $inc"; exit 77; }

jobs=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)
log=$(mktemp "${TMPDIR:-/tmp}/tvmail-mingw.XXXXXX")
trap 'rm -f "$log"' EXIT
# shellcheck disable=SC2016
ls "$here"/src/*.cpp "$common"/src/*.cpp | xargs -P "$jobs" -I{} sh -c \
  '"$0" -std=c++17 -fsyntax-only -Wall -Wextra -Werror -isystem "$1" -I"$2/src" -I"$3/src" "{}" || echo "FAILED: {}"' \
  "$cxx" "$inc" "$here" "$common" >"$log" 2>&1
cat "$log"
if grep -q '^FAILED: ' "$log"; then
  echo "FAIL: the mingw build does not compile ($cxx)"
  exit 1
fi
echo "PASS: every source compiles for mingw ($cxx)"
