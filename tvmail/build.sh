#!/usr/bin/env bash
# Build tvmail.
#
#   ./build.sh            on Cygwin: native ncurses build (small tvision patch
#                         for the missing FIONREAD; runs great in mintty).
#                         on Linux/macOS: plain native build.
#   ./build.sh --mingw    on Cygwin: cross-compile a static native tvmail.exe
#                         instead (needs mingw64-x86_64-gcc-g++; use this only
#                         if you run tvmail from a real Windows console).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
tv="$here/third_party/tvision"
mode="native"; [ "${1:-}" = "--mingw" ] && mode="mingw"

command -v cmake >/dev/null || {
  echo "cmake not found:  D:/cygwin/packages/setup-x86_64.exe -q -P cmake" >&2; exit 1; }

if [ ! -e "$tv/CMakeLists.txt" ]; then
  echo ">> cloning magiblot/tvision ..."
  git clone --depth 1 https://github.com/magiblot/tvision "$tv"
fi

is_cygwin=0
[ "$(uname -o 2>/dev/null)" = "Cygwin" ] && is_cygwin=1

if [ "$is_cygwin" = 1 ]; then
  echo ">> applying Cygwin compatibility patch to tvision"
  python3 "$here/patches/cygwin_patch.py" "$tv"
fi

cmargs=(-S "$here" -B "$here/build" -DCMAKE_BUILD_TYPE=Release -DTVISION_DIR="$tv")

if [ "$is_cygwin" = 1 ] && [ "$mode" = "mingw" ]; then
  cxx=x86_64-w64-mingw32-g++
  command -v "$cxx" >/dev/null || {
    echo "$cxx not found:  D:/cygwin/packages/setup-x86_64.exe -q -P mingw64-x86_64-gcc-g++" >&2
    exit 1; }
  echo ">> cross-compiling for mingw-w64 (static .exe)"
  cmargs+=(
    -DCMAKE_SYSTEM_NAME=Windows
    -DCMAKE_C_COMPILER=x86_64-w64-mingw32-gcc
    -DCMAKE_CXX_COMPILER=x86_64-w64-mingw32-g++
    -DCMAKE_RC_COMPILER=x86_64-w64-mingw32-windres
    -DTVMAIL_BASH="$(cygpath -m /bin/bash.exe)"
  )
else
  echo ">> native build"
fi

cmake "${cmargs[@]}"
cmake --build "$here/build" -j"$(nproc 2>/dev/null || echo 4)"

bin="$here/build/tvmail"; [ -e "$bin.exe" ] && bin="$bin.exe"
echo
echo ">> built: $bin"
echo ">> try:      PATH=\"$here/backend:\$PATH\"  \"$bin\""
echo ">> install:  cmake --install \"$here/build\" --prefix ~/.local    # -> ~/.local/bin"
