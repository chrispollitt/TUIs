#!/usr/bin/env bash
# Make release tarballs for tvchat.
#
#   ./dist.sh              both, into ./dist/
#   ./dist.sh src          just the source tarball
#   ./dist.sh bin          just the binary tarball (needs a build/ already)
#
# The SOURCE tarball carries ../tvcommon as tvchat-VER/tvcommon, so it builds
# on its own (Linux, macOS, the BSDs, Cygwin).  The BINARY tarball is
# per-platform.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
ver="$(sed -n 's/.*project(tvchat VERSION \([0-9.]*\).*/\1/p' "$here/CMakeLists.txt")"
: "${ver:=0.0.0}"
what="${1:-all}"
out="$here/dist"; mkdir -p "$out"

make_src() {
    local f="$out/tvchat-$ver.tar.gz" t
    ( cd "$here" && git rev-parse --git-dir ) >/dev/null 2>&1 ||
        { echo "not a git checkout - can't make the source tarball" >&2; exit 1; }
    t=$(mktemp -d)
    # "HEAD:" is the committed tree *at that directory*: tvchat itself, and
    # tvcommon beside it
    ( cd "$here" && git archive --format=tar --prefix="tvchat-$ver/" "HEAD:" ) > "$t/a.tar"
    ( cd "$here/../tvcommon" && git archive --format=tar --prefix="tvchat-$ver/tvcommon/" "HEAD:" ) > "$t/b.tar"
    ( cd "$t" && mkdir x && tar -xf a.tar -C x && tar -xf b.tar -C x &&
      tar -cf - -C x "tvchat-$ver" ) | gzip -9 > "$f"
    rm -rf "$t"
    echo ">> $f"
}

make_bin() {
    [ -x "$here/build/tvchat" ] || [ -x "$here/build/tvchat.exe" ] || {
        echo "no build/ yet - run ./build.sh first" >&2; exit 1; }
    ( cd "$here/build" && cpack )
    mv "$here"/build/tvchat-"$ver"-*.tar.gz "$out"/ 2>/dev/null || true
    ls "$out"/tvchat-"$ver"-*.tar.gz | sed 's/^/>> /'
}

case "$what" in
    src) make_src ;;
    bin) make_bin ;;
    all) make_src; make_bin ;;
    *)   echo "usage: $0 [src|bin|all]" >&2; exit 2 ;;
esac
