#!/usr/bin/env bash
# mail_setup.sh - run mail-setup's sandboxed tests from tvmail's ctest.
#
# F3 runs mail-setup's mail-pull, so tvmail depends on it working.  This runs
# its unit, shell and pull-e2e suites (temp dirs only, ~30-40 s) - never
# e2e_master.sh, which sends real mail through a live Postfix/Dovecot; run
# that from mail-setup itself.
#
# Uses $MAIL_SETUP_DIR, else third_party/POSIX/mail-setup.  Exits 77 (ctest:
# skipped) when neither exists - ./third_party.sh mail-setup fetches it.
# Leave it out of a run with:  ./test.sh -LE mail_setup
set -uo pipefail
here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
M="${MAIL_SETUP_DIR:-$here/third_party/POSIX/mail-setup}"

if [ ! -f "$M/tests/run-tests.sh" ]; then
  echo "mail_setup: skipped - no mail-setup at $M (run ./third_party.sh mail-setup)"
  exit 77
fi
cd "$M" || exit 1

PY=$(command -v python3 || true)
[ -n "$PY" ] || { echo "mail_setup: skipped - no python3"; exit 77; }

rc=0
bash tests/run-tests.sh unit  || rc=1
bash tests/run-tests.sh shell || rc=1
if [ -f tests/e2e/test_pull_e2e.py ]; then
  echo; echo "=== e2e-pull ==="
  "$PY" -B -m unittest tests/e2e/test_pull_e2e.py -v || rc=1
fi
exit "$rc"
