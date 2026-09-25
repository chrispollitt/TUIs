#!/usr/bin/env bash
# install_roundtrip.sh BUILD_DIR - cmake --install into a temp prefix, then
# ./uninstall.sh it: every installed file must be gone, a mail-pull'd pop-pull
# must survive, and the real build/install_manifest.txt must be untouched.
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
[ -e "$P/bin/tvmail-backend" ] || fail "nothing installed"
n=$(find "$P" -type f | wc -l)

# a legacy pop-pull that a mail-pull wrapper still uses must be kept
mkdir -p "$tmp/home/bin"
echo '#!/bin/sh' > "$P/bin/pop-pull"
printf '#!/bin/sh\nexec "%s" "$@"\n' "$P/bin/pop-pull" > "$tmp/home/bin/mail-pull"

HOME="$tmp/home" "$here/uninstall.sh" -n --prefix "$P" >/dev/null || fail "uninstall -n"
[ "$(find "$P" -type f | wc -l)" = $((n + 1)) ] || fail "--dry-run removed something"

HOME="$tmp/home" "$here/uninstall.sh" -y --prefix "$P" > "$tmp/out" 2>&1 || { cat "$tmp/out"; fail "uninstall -y"; }
left=$(find "$P" -type f ! -path "$P/bin/pop-pull")
[ -z "$left" ] || fail "left behind: $left"
[ -e "$P/bin/pop-pull" ] || fail "removed a pop-pull that mail-pull still runs"
grep -q "keeping $P/bin/pop-pull" "$tmp/out" || fail "didn't say why pop-pull was kept"

rm "$tmp/home/bin/mail-pull"
HOME="$tmp/home" "$here/uninstall.sh" -y --prefix "$P" >/dev/null 2>&1
[ ! -e "$P/bin/pop-pull" ] || fail "unused legacy pop-pull not removed with -y"

echo "install_roundtrip: ok ($n files installed and removed)"
