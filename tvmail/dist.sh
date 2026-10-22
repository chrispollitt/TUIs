#!/usr/bin/env bash
# Make release tarballs for tvmail.
#
#   ./dist.sh              both, into ./dist/
#   ./dist.sh src          just the source tarball
#   ./dist.sh bin          just the binary tarball (needs a build/ already)
#
# The SOURCE tarball (with ../tvcommon carried as tvmail-VER/tvcommon) builds
# on Linux, macOS and Cygwin.
# The BINARY tarball is per-platform - a Cygwin build only runs under Cygwin,
# a Linux build only on Linux, etc.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
ver="$(sed -n 's/.*project(tvmail VERSION \([0-9.]*\).*/\1/p' "$here/CMakeLists.txt")"
: "${ver:=0.0.0}"
what="${1:-all}"
out="$here/dist"; mkdir -p "$out"

make_src() {
    local f="$out/tvmail-$ver.tar.gz" t
    if ( cd "$here" && git rev-parse --git-dir ) >/dev/null 2>&1; then
        t=$(mktemp -d)
        # "HEAD:" is the committed tree *at that directory*: tvmail itself,
        # and tvcommon beside it (carried as tvmail-VER/tvcommon)
        ( cd "$here" && git archive --format=tar --prefix="tvmail-$ver/" "HEAD:" ) > "$t/a.tar"
        ( cd "$here/../tvcommon" && git archive --format=tar --prefix="tvmail-$ver/tvcommon/" "HEAD:" ) > "$t/b.tar"
        ( cd "$t" && mkdir x && tar -xf a.tar -C x && tar -xf b.tar -C x &&
          tar -cf - -C x "tvmail-$ver" ) | gzip -9 > "$f"
        rm -rf "$t"
    else
        echo "not a git checkout - using cpack source packaging"
        ( cd "$here/build" && cpack --config CPackSourceConfig.cmake )
        mv "$here/build/tvmail-$ver.tar.gz" "$f" 2>/dev/null || true
    fi
    echo ">> $f"
}

make_bin() {
    [ -x "$here/build/tvmail" ] || [ -x "$here/build/tvmail.exe" ] || {
        echo "no build/ yet - run ./build.sh first" >&2; exit 1; }
    ( cd "$here/build" && cpack )
    mv "$here"/build/tvmail-"$ver"-*.tar.gz "$out"/ 2>/dev/null || true
    ls "$out"/tvmail-"$ver"-*.tar.gz | sed 's/^/>> /'
}

case "$what" in
    src) make_src ;;
    bin) make_bin ;;
    all) make_src; make_bin ;;
    *)   echo "usage: $0 [src|bin|all]" >&2; exit 2 ;;
esac
