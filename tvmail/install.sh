#!/usr/bin/env bash
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

PREFIX="/usr/local"
BUILD="$here/build"

os=$(uname -s 2>/dev/null || echo unknown)
IS_CYGWIN=0; case "$os" in CYGWIN*|MSYS*|MINGW*) IS_CYGWIN=1 ;; esac
SUDO=""
[ "$IS_CYGWIN" = 0 ] && [ "$(id -u)" != 0 ] && command -v sudo >/dev/null 2>&1 && SUDO=sudo

target="$PREFIX"; [ -e "$target" ] || target=$(dirname "$PREFIX")
if [ -w "$target" ]; then
  cmake --install "$BUILD" --prefix "$PREFIX"
elif [ -n "$SUDO" ]; then
  log "no write access to $PREFIX - using sudo"
  $SUDO cmake --install "$BUILD" --prefix "$PREFIX"
else
  warn "no write access to $PREFIX and no sudo - trying anyway"
  cmake --install "$BUILD" --prefix "$PREFIX"
fi

