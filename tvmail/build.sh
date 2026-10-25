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
# Cygwin: cmake finds its modules from the path it was run as; a PATH with /bin but
# not /usr/bin (a bare ssh login) makes it look in /share and fail with "Could not
# find CMAKE_ROOT"
case "$(uname -s 2>/dev/null)" in CYGWIN*) PATH=/usr/bin:$PATH; export PATH ;; esac
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# the C++ shared with tvchat: ../tvcommon, or ./tvcommon in a source tarball;
# Turbo Vision lives there too, one checkout for both apps
common="$here/tvcommon"; [ -d "$common" ] || common="$here/../tvcommon"
tv="$common/third_party/tvision"
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

# tvcommon/third_party/, shared with tvchat: Turbo Vision, fetched and
# patched (read timeout over ssh, CJK trail cells, Cygwin's FIONREAD &c);
# POSIX (mail-setup, configure.sh's mail wizard) isn't needed to compile, so
# a failed fetch of it only warns
sh "$common/third_party.sh" tvision
sh "$common/third_party.sh" posix \
  || echo "build.sh: WARNING - couldn't fetch mail-setup (only ./configure.sh needs it)" >&2

set -- -S "$here" -B "$here/build" -DCMAKE_BUILD_TYPE=Release -DTVISION_DIR="$tv"

if [ "$is_cygwin" = 1 ] && [ "$mode" = mingw ]; then
  need x86_64-w64-mingw32-g++
  echo ">> cross-compiling for mingw-w64 (static .exe)"
  set -- "$@" \
    -DCMAKE_SYSTEM_NAME=Windows \
    -DCMAKE_C_COMPILER=x86_64-w64-mingw32-gcc \
    -DCMAKE_CXX_COMPILER=x86_64-w64-mingw32-g++ \
    -DCMAKE_RC_COMPILER=x86_64-w64-mingw32-windres \
    -DTV_SH="$(cygpath -m /bin/bash.exe)"
else
  echo ">> native build ($os)"
fi

cmake "$@"
cmake --build "$here/build" -j"$jobs"

bin="$here/build/tvmail"; [ -e "$bin.exe" ] && bin="$bin.exe"
echo
echo ">> built: $bin"
echo ">> try:      PATH=\"$here/backend:\$PATH\"  \"$bin\""
echo ">> install:  ./setup.sh   (or: cmake --install \"$here/build\" --prefix DIR)"
