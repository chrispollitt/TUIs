#!/usr/bin/env sh
# Build tvmail.
#
#   ./build.sh            native build.  On Cygwin it first applies a small
#                         tvision compatibility patch (FIONREAD &c); on Linux
#                         (incl. Raspberry Pi / WSL), macOS and the BSDs it
#                         just builds against the system ncurses.
#   ./build.sh --mingw    on Cygwin only: cross-compile a static native
#                         tvmail.exe (needs the mingw64 g++), for use from a
#                         real Windows console rather than a Unix terminal.
#
# Needs: cmake, a C++17 compiler, ncurses(w) headers, git, python3.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
tv="$here/third_party/tvision"
mode=native
[ "${1:-}" = "--mingw" ] && mode=mingw

need() { command -v "$1" >/dev/null 2>&1 || { echo "build.sh: '$1' not found" >&2; exit 1; }; }
need cmake
need git

os=$(uname -s 2>/dev/null || echo unknown)
is_cygwin=0
case "$os" in CYGWIN*|MINGW*|MSYS*) is_cygwin=1 ;; esac

# CPU count, portably
jobs=$( (nproc 2>/dev/null) || (sysctl -n hw.ncpu 2>/dev/null) \
        || (getconf _NPROCESSORS_ONLN 2>/dev/null) || echo 4 )

if [ ! -e "$tv/CMakeLists.txt" ]; then
  echo ">> cloning magiblot/tvision ..."
  git clone --depth 1 https://github.com/magiblot/tvision "$tv"
fi

if [ "$is_cygwin" = 1 ]; then
  py="${PYTHON:-}"
  if [ -z "$py" ]; then
    for c in python3 python3.12 python3.11 python3.10 python3.9 python3.8 python3.7; do
      command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys;exit(sys.version_info<(3,6))' \
        2>/dev/null && { py="$c"; break; }
    done
  fi
  [ -n "$py" ] || { echo "build.sh: no working python3 (>=3.6) found" >&2; exit 1; }
  echo ">> applying Cygwin compatibility patch to tvision (with $py)"
  "$py" "$here/patches/cygwin_patch.py" "$tv"
fi

set -- -S "$here" -B "$here/build" -DCMAKE_BUILD_TYPE=Release -DTVISION_DIR="$tv"

if [ "$is_cygwin" = 1 ] && [ "$mode" = mingw ]; then
  need x86_64-w64-mingw32-g++
  echo ">> cross-compiling for mingw-w64 (static .exe)"
  set -- "$@" \
    -DCMAKE_SYSTEM_NAME=Windows \
    -DCMAKE_C_COMPILER=x86_64-w64-mingw32-gcc \
    -DCMAKE_CXX_COMPILER=x86_64-w64-mingw32-g++ \
    -DCMAKE_RC_COMPILER=x86_64-w64-mingw32-windres \
    -DTVMAIL_SH="$(cygpath -m /bin/bash.exe)"
else
  echo ">> native build ($os)"
fi

cmake "$@"
cmake --build "$here/build" -j"$jobs"

bin="$here/build/tvmail"; [ -e "$bin.exe" ] && bin="$bin.exe"
echo
echo ">> built: $bin"
echo ">> try:      PATH=\"$here/backend:\$PATH\"  \"$bin\""
echo ">> install:  cmake --install \"$here/build\" --prefix ~/.local"
