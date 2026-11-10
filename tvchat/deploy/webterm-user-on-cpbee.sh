#!/bin/bash
# RUN ON: the laptop (Cygwin):  /cygdrive/c/Users/chris/OneDrive/DB/src/Claude/TO_BE_RUN/webterm-user-on-cpbee.sh
# WHY:    the five web services on cpbee (ttyd for maze4, bidet, cpvax, cpvax console; and waxweb) run as YOU, from your
#         private home, holding the VMS password.  Anything that broke out of one of them would be you.  This moves them to a
#         dedicated system user, `webterm`: no shell, no login, no home, a read-only copy of the programs, hardened units.
# WHAT:   on cpbee (ssh with a terminal; sudo asks once):
#         1. checks the sources and tools (g++, make, python3 venv, ~/bin/ttyd, ~/.config/waxweb.env mode 600);
#         2. creates the system user webterm (nologin) if missing;
#         3. installs /opt/webterm (root-owned, read-only to webterm): ttyd, the four launchers, bidet with a fresh Python
#            environment (numpy, Pillow from pip), maze4 BUILT there (it was never built on cpbee: its web terminal could not
#            have worked), the waxweb code;
#         4. /etc/webterm/waxweb.env (root:webterm 640): a copy of your waxweb.env (the original stays until you clean up);
#         5. /etc/sudoers.d/vax-web gets a line for webterm (power the VAX on, nothing else); your line stays for now;
#            the old file is kept as vax-web.bak.<time> (ignored by sudo);
#         6. system units /etc/systemd/system/web-ttyd-{maze4,bidet,cpvax,cpvax-console,waxweb}.service, same ports and paths
#            as before (nginx is untouched), hardened (no home, read-only system, no privileges except where sudo-to-power-on
#            is needed);
#         7. CUTOVER: stops your five user units, starts the system ones, checks each answers and runs as webterm.  If ANY
#            check fails it puts your user units back and stops the new ones: you are where you started.
#         8. on success, disables your user units from starting at boot (the unit files stay for rollback).
# CHANGES on cpbee: user webterm; /opt/webterm; /etc/webterm; /etc/sudoers.d/vax-web; five unit files in /etc/systemd/system;
#         your five user units disabled.  Nothing in your home is deleted.
# UNDO:   sudo systemctl disable --now web-ttyd-{maze4,bidet,cpvax,cpvax-console,waxweb}; systemctl --user enable --now
#         web-ttyd-{maze4,bidet,cpvax,cpvax-console,waxweb}.   (To remove webterm entirely: sudo userdel webterm; sudo rm -r /opt/webterm /etc/webterm.)
# NEXT (separate, once you are happy): delete the user units, your sudoers line, and ~/.config/waxweb.env.
# SAFE:   re-runnable.  Moves itself to DONE/ only on success.  DRY_RUN=1 shows the plan.
set -euo pipefail

HOST=cpbee
here=$(cd "$(dirname "$0")" && pwd)
name=$(basename "$0")
say() { printf '%s\n' "$*"; }
die() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }

if [[ ${DRY_RUN:-0} == 1 ]]; then
  say "DRY RUN: would, on $HOST: create user webterm, install /opt/webterm (ttyd, launchers, bidet + venv, maze4 built, waxweb),"
  say "/etc/webterm/waxweb.env, a sudoers line, five hardened system units; stop your user units, start the system ones, check"
  say "each, and roll back to your user units if any check fails. Nothing was changed."
  exit 0
fi

if [[ ${RUN_ON_REMOTE:-0} != 1 && $(hostname | cut -d. -f1) != "$HOST" ]]; then
  ssh -o BatchMode=yes "$HOST" true || die "cannot ssh to $HOST"
  scp -q "$here/$name" "$HOST:/tmp/$name"
  rc=0
  ssh -t -o ServerAliveInterval=10 "$HOST" "RUN_ON_REMOTE=1 bash /tmp/$name; rc=\$?; rm -f /tmp/$name; exit \$rc" || rc=$?
  [[ $rc == 0 ]] || die "it did not finish on $HOST (exit $rc); paste the output above to Claude."
  mv -- "$here/$name" "$here/DONE/$name" 2>/dev/null || true
  exit 0
fi

# ------------------------------------------------------------------ on cpbee ---
SRC=$HOME/gh_cp
OPT=/opt/webterm
ETC=/etc/webterm
APPS="maze4 bidet cpvax cpvax-console waxweb"
now=$(date +%Y%m%d-%H%M%S)

say "step 1/8: checks ..."
for f in Games/maze4/ttyd/run-ttyd.sh Games/maze4/src/Makefile CWPutils/BIDeT/4D/ttyd/run-ttyd.sh CWPutils/BIDeT/4D/bidet.py \
         CWPutils/BIDeT/4D/requirements.txt VAX/simh/ttyd/run-ttyd.sh VAX/simh/ttyd/cpvax-term.sh VAX/apps/waxweb/waxweb.py; do
  [[ -f $SRC/$f ]] || die "missing: $SRC/$f (git pull in ~/gh_cp?)"
done
[[ -x $HOME/bin/ttyd ]] || die "no ~/bin/ttyd"
[[ -f $HOME/.config/waxweb.env && $(stat -c %a "$HOME/.config/waxweb.env") == 600 ]] || die "~/.config/waxweb.env must exist, mode 600"
command -v g++ >/dev/null && command -v make >/dev/null || die "g++ and make are needed to build maze4 (sudo apt install build-essential)"
python3 -c 'import venv, ensurepip' 2>/dev/null || die "python3-venv is missing (sudo apt install python3-venv)"
for a in $APPS; do systemctl --user is-active --quiet "web-ttyd-$a" || say "  (note: your user unit web-ttyd-$a is not running now)"; done

say "step 2/8: the user webterm (sudo password) ..."
if id webterm >/dev/null 2>&1; then
  say "  exists"
else
  sudo useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin webterm
  say "  created"
fi

say "step 3/8: /opt/webterm ..."
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
# maze4: built in a scratch copy, so ~/gh_cp stays clean
cp -r "$SRC/Games/maze4/src" "$tmp/maze4-src"
make -C "$tmp/maze4-src" WERROR=0 all > "$tmp/maze4-build.log" 2>&1 || { tail -20 "$tmp/maze4-build.log"; die "maze4 did not build (log above)"; }
[[ -x $tmp/maze4-src/maze4 ]] || die "the maze4 build made no maze4 program"
sudo install -d -m 755 "$OPT" "$OPT/bin" "$OPT/maze4/ttyd" "$OPT/maze4/src" "$OPT/vax/ttyd" "$OPT/waxweb"
sudo install -m 755 "$HOME/bin/ttyd" "$OPT/bin/ttyd"
sudo install -m 755 "$SRC/Games/maze4/ttyd/run-ttyd.sh" "$OPT/maze4/ttyd/run-ttyd.sh"
sudo install -m 755 "$tmp/maze4-src/maze4" "$OPT/maze4/src/maze4"
sudo install -m 755 "$SRC/VAX/simh/ttyd/run-ttyd.sh" "$SRC/VAX/simh/ttyd/cpvax-term.sh" "$OPT/vax/ttyd/"
# bidet: the program files, not the gallery / samples / tests
sudo rm -rf "$OPT/bidet.new"
sudo install -d -m 755 "$OPT/bidet.new"
( cd "$SRC/CWPutils/BIDeT/4D" && tar cf - --exclude=./gallery --exclude=./out --exclude=./samples --exclude=./tmp \
      --exclude=./tests --exclude=./testdata --exclude=./BAK --exclude=__pycache__ --exclude=./images . ) \
  | sudo tar xf - -C "$OPT/bidet.new" --no-same-owner
sudo chown -R root:root "$OPT/bidet.new"; sudo chmod -R go+rX,go-w "$OPT/bidet.new"
sudo chmod 755 "$OPT/bidet.new/ttyd/run-ttyd.sh" "$OPT/bidet.new/ttyd/bidet-web.sh"
sudo rm -rf "$OPT/bidet"; sudo mv "$OPT/bidet.new" "$OPT/bidet"
sudo python3 -m venv "$OPT/venv-bidet"
sudo "$OPT/venv-bidet/bin/pip" install --quiet -r "$OPT/bidet/requirements.txt" || die "pip could not install numpy and Pillow (internet?)"
sudo "$OPT/venv-bidet/bin/python" -c 'import numpy, PIL' || die "the bidet environment is broken"
# waxweb
sudo install -m 644 "$SRC"/VAX/apps/waxweb/*.py "$OPT/waxweb/"
sudo rm -rf "$OPT/waxweb/static"; sudo cp -r "$SRC/VAX/apps/waxweb/static" "$OPT/waxweb/static"
sudo chown -R root:root "$OPT/waxweb"; sudo chmod -R go+rX,go-w "$OPT/waxweb"
rm -f "$OPT/waxweb/fakevax.py" 2>/dev/null || sudo rm -f "$OPT/waxweb/fakevax.py" "$OPT/waxweb/run_demo.py"
say "  installed"

say "step 4/8: /etc/webterm/waxweb.env ..."
sudo install -d -m 755 "$ETC"
sudo install -m 640 -o root -g webterm "$HOME/.config/waxweb.env" "$ETC/waxweb.env"

say "step 5/8: sudoers (webterm may power the VAX on, nothing else) ..."
f=/etc/sudoers.d/vax-web
sudo test -f "$f" && sudo cp -p "$f" "$f.bak.$now"
t=$(mktemp)
{ sudo cat "$f" 2>/dev/null | grep -v '^# written by.*webterm' | grep -v '^webterm ' || true
  printf '# written by webterm-user-on-cpbee.sh: the web services' ; printf ' (user webterm) may power the VAX on, and nothing else\n'
  printf 'webterm ALL=(root) NOPASSWD: /usr/bin/systemctl start vax.service\n'; } > "$t"  # allowlist secret
sudo visudo -cf "$t" >/dev/null || die "the sudoers rule did not validate: nothing installed"
sudo install -m 440 -o root -g root "$t" "$f"
rm -f "$t"

say "step 6/8: the system units ..."
hard_common='User=webterm
Group=webterm
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=full
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK
RestrictRealtime=yes
LockPersonality=yes
SystemCallArchitectures=native
Restart=on-failure
RestartSec=5'
unit() {  # unit NAME DESCRIPTION NOPRIV(yes|no) ENV... -- EXEC
  local n=$1 d=$2 np=$3; shift 3
  local envs="" ; while [[ $1 != -- ]]; do envs+="Environment=$1"$'\n'; shift; done; shift
  local nn=""; [[ $np == yes ]] && nn="NoNewPrivileges=yes"
  printf '[Unit]\nDescription=%s\nAfter=network.target\n\n[Service]\n%s%s\n%s\nExecStart=%s\n\n[Install]\nWantedBy=multi-user.target\n' \
    "$d" "$envs" "$hard_common" "$nn" "$*" | sudo tee "/etc/systemd/system/web-ttyd-$n.service" >/dev/null
}
P=PATH=/usr/games:/usr/local/bin:/usr/bin:/bin
unit maze4 "maze4 in a browser tab (ttyd, read-only, 127.0.0.1:7681; user webterm)" yes $P BASE=/maze4/term TTYD=$OPT/bin/ttyd -- $OPT/maze4/ttyd/run-ttyd.sh
unit bidet "bidet in a browser tab (ttyd, SIXEL, 127.0.0.1:7682; user webterm)" yes $P BASE=/bidet/term TTYD=$OPT/bin/ttyd BIDET_PY=$OPT/venv-bidet/bin/python -- $OPT/bidet/ttyd/run-ttyd.sh
unit cpvax "cpvax (OpenVMS login) in a browser tab (ttyd, 127.0.0.1:7683; user webterm)" no $P BASE=/cpvax/term VAX_MODE=login TTYD=$OPT/bin/ttyd -- $OPT/vax/ttyd/run-ttyd.sh
unit cpvax-console "cpvax simh console in a browser tab (ttyd, 127.0.0.1:7684; user webterm)" no $P BASE=/cpvax-console/term VAX_MODE=console TTYD=$OPT/bin/ttyd -- $OPT/vax/ttyd/run-ttyd.sh
# waxweb: its own env file, a python program, and sudo to power the VAX on (so no NoNewPrivileges)
printf '[Unit]\nDescription=WAX & TAPE web front end for the cpvax (waxweb, 127.0.0.1:8088; user webterm)\nAfter=network.target\n\n[Service]\nEnvironmentFile=%s/waxweb.env\nEnvironment=WAXWEB_BIND=127.0.0.1\nEnvironment=WAXWEB_PORT=8088\nEnvironment=PYTHONDONTWRITEBYTECODE=1\nWorkingDirectory=%s/waxweb\n%s\nExecStart=/usr/bin/python3 %s/waxweb/waxweb.py\n\n[Install]\nWantedBy=multi-user.target\n' \
  "$ETC" "$OPT" "$hard_common" "$OPT" | sudo tee /etc/systemd/system/web-ttyd-waxweb.service >/dev/null
sudo systemctl daemon-reload
say "  written"

say "step 7/8: cutover (your user units stop, the system ones start) ..."
port_of() { case $1 in maze4) echo 7681;; bidet) echo 7682;; cpvax) echo 7683;; cpvax-console) echo 7684;; waxweb) echo 8088;; esac; }
base_of() { case $1 in maze4) echo /maze4/term/;; bidet) echo /bidet/term/;; cpvax) echo /cpvax/term/;; cpvax-console) echo /cpvax-console/term/;; waxweb) echo /;; esac; }
rollback() {
  say "  ROLLING BACK: the new units stop, your user units start again"
  for a in $APPS; do sudo systemctl disable --now "web-ttyd-$a" >/dev/null 2>&1 || true; done
  for a in $APPS; do systemctl --user start "web-ttyd-$a" >/dev/null 2>&1 || true; done
}
for a in $APPS; do systemctl --user stop "web-ttyd-$a" >/dev/null 2>&1 || true; done
sleep 1
bad=0
for a in $APPS; do
  sudo systemctl enable --now "web-ttyd-$a" >/dev/null 2>&1 || { say "  $a: would not start"; bad=1; continue; }
done
sleep 6
for a in $APPS; do
  pid=$(systemctl show -p MainPID --value "web-ttyd-$a")
  who=$(ps -o user= -p "$pid" 2>/dev/null | tr -d ' ')
  code=$(curl -s -o /dev/null -m 8 -w '%{http_code}' "http://127.0.0.1:$(port_of "$a")$(base_of "$a")" || true)
  if systemctl is-active --quiet "web-ttyd-$a" && [[ $who == webterm* && $code =~ ^[23] ]]; then
    say "  $a: ok (runs as $who, answers $code)"
  else
    say "  $a: PROBLEM (active: $(systemctl is-active "web-ttyd-$a"), user: ${who:-none}, answer: ${code:-none})"
    sudo journalctl -u "web-ttyd-$a" -n 8 --no-pager | sed 's/^/      /'
    bad=1
  fi
done
if (( bad )); then rollback; die "a check failed: your user units are back; the journal lines are above"; fi

say "step 8/8: your user units no longer start at boot (their files stay) ..."
for a in $APPS; do systemctl --user disable "web-ttyd-$a" >/dev/null 2>&1 || true; done
say ""
say "Done.  The five services now run as webterm.  Try them in the browser (https://192.168.10.1:8444/)."
say "Rollback, any time:  sudo systemctl disable --now web-ttyd-{maze4,bidet,cpvax,cpvax-console,waxweb};  systemctl --user enable --now web-ttyd-{maze4,bidet,cpvax,cpvax-console,waxweb}"
