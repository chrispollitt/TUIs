#!/usr/bin/env bash
# backend_timeout.sh TVMAIL_BIN - BUGS.md #33: a backend that answers too slowly must not freeze tvmail.
#
# A fake tvmail-backend in a temp ~/bin greets like the real one, then takes 6 s per request.  With the wait
# cut to 0.3 s (TV_CALL_TIMEOUT_MS) `tvmail --selftest` must give up on it within a couple of seconds and say
# "served=0".  Before the fix the client closed the pipes and then waited for the old backend to finish
# (~6 s here, the rest of any slow reply in real life) and asked again (another 6 s).
set -uo pipefail
bin="${1:?usage: backend_timeout.sh TVMAIL_BIN}"
fail() { echo "FAIL: $*"; exit 1; }

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/home/bin"
cat > "$tmp/home/bin/tvmail-backend" <<'EOF'
#!/bin/sh
# the fake: the "serve" greeting, then six seconds for every request
case "$1" in
serve)
  printf '0 5\nready'
  while read -r line; do
    sleep 6
    printf '0 2\nok'
  done ;;
*) exit 0 ;;
esac
EOF
chmod +x "$tmp/home/bin/tvmail-backend"

start=$(date +%s)
out=$(HOME="$tmp/home" TVMAIL_MODE=local TV_CALL_TIMEOUT_MS=300 timeout 30 "$bin" --selftest 2>&1)
rc=$?
secs=$(( $(date +%s) - start ))

echo "$out" | grep -q "selftest: served=0" || fail "expected served=0 (rc=$rc): $out"
[ "$secs" -le 3 ] || fail "tvmail waited ${secs}s for a slow backend (more than 3 s with a 0.3 s limit)"
echo "ok: gave up on the slow backend in ${secs}s"
