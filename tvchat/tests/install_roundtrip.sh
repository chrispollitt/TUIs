#!/usr/bin/env bash
# install_roundtrip.sh BUILD_DIR - cmake --install into a temp prefix, then
# ./uninstall.sh it: every installed file must be gone, and the real
# build/install_manifest.txt must be untouched.
set -uo pipefail
here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
build="${1:-$here/build}"
man="$build/install_manifest.txt"

fail() { echo "FAIL: $*"; exit 1; }
tmp=$(mktemp -d)
saved=""; [ -e "$man" ] && { saved="$tmp/manifest.saved"; cp -p "$man" "$saved"; }
cleanup() {
  if [ -n "$saved" ]; then cp -p "$saved" "$man"; else rm -f "$man"; fi
  rm -rf "$tmp"
}
trap cleanup EXIT
P="$tmp/prefix"

cmake --install "$build" --prefix "$P" >/dev/null || fail "cmake --install"
[ -x "$P/bin/tvchat-backend" ] || fail "tvchat-backend not installed"
[ -e "$P/bin/tvchat" ] || [ -e "$P/bin/tvchat.exe" ] || fail "tvchat not installed"
[ -e "$P/share/man/man1/tvchat.1" ] || fail "no man page"
[ ! -e "$P/bin/tvmail-backend" ] || fail "tvchat installed tvmail's backend"
n=$(find "$P" -type f | wc -l)

mkdir -p "$tmp/home"
HOME="$tmp/home" "$here/uninstall.sh" -n --prefix "$P" >/dev/null || fail "uninstall -n"
[ "$(find "$P" -type f | wc -l)" = "$n" ] || fail "--dry-run removed something"

HOME="$tmp/home" "$here/uninstall.sh" -y --prefix "$P" > "$tmp/out" 2>&1 || { cat "$tmp/out"; fail "uninstall -y"; }
left=$(find "$P" -type f)
[ -z "$left" ] || fail "left behind: $left"

echo "install_roundtrip: ok ($n files installed and removed)"
