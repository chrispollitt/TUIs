#!/usr/bin/env sh
# Build tvchat.
#
#   ./build.sh            native build: Linux (incl. Raspberry Pi / WSL),
#                         macOS, the BSDs, Cygwin.  tvchat has no native
#                         Windows build: its backend is a co-process (fork()).
#
# Needs: cmake, a C++17 compiler, ncurses(w) headers, git, python3.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# the C++ shared with tvmail: ../tvcommon, or ./tvcommon in a source tarball;
# Turbo Vision lives there too, one checkout for both apps
common="$here/tvcommon"; [ -d "$common" ] || common="$here/../tvcommon"
tv="$common/third_party/tvision"

need() { command -v "$1" >/dev/null 2>&1 || { echo "build.sh: '$1' not found" >&2; exit 1; }; }
need cmake
need git
os=$(uname -s 2>/dev/null || echo unknown)
# CPU count, portably
jobs=$( (nproc 2>/dev/null) || (sysctl -n hw.ncpu 2>/dev/null) \
        || (getconf _NPROCESSORS_ONLN 2>/dev/null) || echo 4 )

# tvcommon/third_party/, shared with tvmail: Turbo Vision, fetched and
# patched (read timeout over ssh, CJK trail cells, Cygwin's FIONREAD &c);
# POSIX (chat-setup: an IRC server of your own) isn't needed to compile, so
# a failed fetch of it only warns
sh "$common/third_party.sh" tvision
sh "$common/third_party.sh" posix \
  || echo "build.sh: WARNING - couldn't fetch chrispollitt/POSIX (only chat-setup needs it)" >&2

echo ">> native build ($os)"
cmake -S "$here" -B "$here/build" -DCMAKE_BUILD_TYPE=Release -DTVISION_DIR="$tv"
cmake --build "$here/build" -j"$jobs"
bin="$here/build/tvchat"; [ -e "$bin.exe" ] && bin="$bin.exe"
echo
echo ">> built: $bin"
echo ">> try:      PATH=\"$here/backend:\$PATH\"  \"$bin\""
echo ">> install:  ./install.sh   (or: cmake --install \"$here/build\" --prefix ~/.local)"
