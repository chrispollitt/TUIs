#!/bin/bash
# check-clean-install.sh [HOST]   - does a fresh clone of TUIs build and install, on this machine or on HOST (over ssh)?
# WHY:    the "automated and painless setup" claim, tested the way a new machine would meet it: nothing from an existing
#         checkout, nothing from the live install.
# WHAT:   on the machine: git clone --depth 1 of origin/main into a temp dir, then for tvmail, tvchat and tvcal: ./build.sh
#         (this fetches Turbo Vision and the POSIX helpers from GitHub, like a first install), cmake --install into a temp
#         prefix, and check that the program and its backend are in the prefix and the Python backends compile.  Then the
#         temp dir is removed (KEEP=1 keeps it).
# CHANGES: nothing but the temp dir.  It never touches ~/gh_cp, /usr/local, a config file or a running app (so it can run while
#         tvmail, tvchat and tvcal are open).  What it cannot show is the PART that needs a person: the Setup Wizard asking
#         for a password.
# RUN:    tvchat/deploy/check-clean-install.sh            (this machine)
#         tvchat/deploy/check-clean-install.sh cmdesktop  (ssh key login; the script is piped over, nothing is copied)
#         REPO=url overrides the clone URL; APPS="tvmail tvcal" limits the apps; DRY_RUN=1 shows the plan.
set -uo pipefail

if [[ -n ${1:-} ]]; then
  host=$1
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$host" true 2>/dev/null || { echo "cannot reach $host over ssh (key login)"; exit 1; }
  exec ssh -o BatchMode=yes "$host" "env REPO='${REPO:-}' APPS='${APPS:-}' KEEP='${KEEP:-}' DRY_RUN='${DRY_RUN:-}' bash -l -s" < "$0"
fi

REPO="${REPO:-https://github.com/chrispollitt/TUIs}"
APPS="${APPS:-tvmail tvchat tvcal}"
say() { printf '%s\n' "$*"; }
if [[ ${DRY_RUN:-} == 1 ]]; then
  say "DRY RUN: would clone $REPO into a temp dir on $(hostname | cut -d. -f1), build and install-to-a-temp-prefix: $APPS. Nothing was changed."
  exit 0
fi
case "$(uname -s)" in CYGWIN*) export PATH=/usr/bin:$PATH ;; esac
for t in git cmake python3; do command -v $t >/dev/null || { say "FAIL: $t is not installed on $(hostname | cut -d. -f1)"; exit 1; }; done

work=$(mktemp -d)
[[ ${KEEP:-} == 1 ]] || trap 'rm -rf "$work"' EXIT
fail=0
step() { say "== $*"; }
bad() { say "  FAIL: $*"; fail=$((fail + 1)); }

step "$(hostname | cut -d. -f1): fresh clone of $REPO"
git clone -q --depth 1 "$REPO" "$work/TUIs" 2>"$work/clone.log" || { tail -3 "$work/clone.log"; say "FAIL: clone"; exit 1; }
say "  at: $(cd "$work/TUIs" && git log --oneline | head -1)"
prefix="$work/prefix"

for app in $APPS; do
  step "$app: build"
  start=$(date +%s)
  if ! ( cd "$work/TUIs/$app" && ./build.sh > "$work/$app-build.log" 2>&1 ); then
    tail -15 "$work/$app-build.log"; bad "$app did not build"; continue
  fi
  say "  built in $(( $(date +%s) - start )) s"
  step "$app: install into a temp prefix"
  if ! ( cmake --install "$work/TUIs/$app/build" --prefix "$prefix" > "$work/$app-install.log" 2>&1 ); then
    tail -10 "$work/$app-install.log"; bad "$app did not install"; continue
  fi
  exe="$prefix/bin/$app"; [[ -e $exe ]] || exe="$exe.exe"
  [[ -e $exe ]] && say "  ok: $(basename "$exe") installed" || bad "no $app in $prefix/bin"
  [[ -e $prefix/bin/$app-backend ]] && say "  ok: $app-backend installed" || bad "no $app-backend in $prefix/bin"
  [[ -x $exe ]] || bad "$(basename "$exe") is not executable"
  if head -1 "$prefix/bin/$app-backend" 2>/dev/null | grep -q python; then
    python3 -I -c "import ast,sys; ast.parse(open(sys.argv[1], encoding='utf-8').read())" "$prefix/bin/$app-backend" \
      && say "  ok: $app-backend parses" || bad "$app-backend does not parse"
  fi
done

say ""
if [[ $fail == 0 ]]; then say "Clean install OK on $(hostname | cut -d. -f1): $APPS built and installed from a fresh clone."; else say "$fail check(s) failed."; exit 1; fi
