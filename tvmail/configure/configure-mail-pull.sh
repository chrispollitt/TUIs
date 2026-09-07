#!/usr/bin/env bash
#
# configure-mail-pull.sh
#
# Set up a pull of remote mail (POP3S) into the local mailbox.  Portable:
# Linux (incl. Raspberry Pi / WSL), macOS, the BSDs, and Cygwin.
#
# Installs a small, dependency-free Python puller ("pop-pull") that:
#   * connects to the incoming server over implicit TLS (port 995)
#   * reads the mailbox password LIVE from passwd.client (/etc/exim4 or /etc/exim)
#   * hands each message to sendmail/exim as a local submission -> /var/mail/<user>
#   * by default KEEPs mail on the server and remembers what it has already
#     fetched (UIDL list in ~/.local/state/mailpull.seen); --delete removes it
#
# On-demand by default: run 'pop-pull' (or 'pull-mail') when you want mail.
# --timer N installs a systemd --user timer that pulls every N minutes.
#
# Usage:
#   ./configure-mail-pull.sh --relay-file /path/to/smtp-relay.txt [options]
#
# Options:
#   --relay-file FILE   cPanel-style config text -> Incoming Server / Username.
#   --pop-user NAME     remote mailbox login  (default: from file / passwd.client)
#   --pwfile PATH       file holding  <host>:<login>:<password>  (for boxes with
#                       no exim passwd.client, e.g. a Postfix master).  0600.
#   --local-user NAME   deliver into this account            (default: you)
#   --delete           delete messages from the server after delivery
#   --no-verify        skip TLS certificate verification
#   --test             run one fetch right after configuring
#   -h | --help        show this header
#
set -euo pipefail

RELAY_FILE=""
POP_USER=""
LOCAL_USER="$(id -un)"
KEEP=1
VERIFY=1
DO_TEST=0
PWFILE_OPT=""

TIMER_MIN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --relay-file) RELAY_FILE="${2:?}"; shift 2 ;;
    --pop-user)   POP_USER="${2:?}";   shift 2 ;;
    --pwfile)     PWFILE_OPT="${2:?}"; shift 2 ;;
    --local-user) LOCAL_USER="${2:?}"; shift 2 ;;
    --delete)     KEEP=0; shift ;;
    --no-verify)  VERIFY=0; shift ;;
    --test)       DO_TEST=1; shift ;;
    --timer)      TIMER_MIN="${2:?}"; shift 2 ;;   # systemd --user pull every N min
    -h|--help)    sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//; $d'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

log()  { printf '  %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

case "$(uname -s 2>/dev/null)" in
  Linux|CYGWIN*|MSYS*|MINGW*|Darwin|*BSD|DragonFly|SunOS) : ;;
  *) warn "untested OS - continuing anyway" ;;
esac

PY="$(command -v python3 || command -v python3.12 || command -v python3.11 \
      || command -v python3.10 || command -v python3.9 || command -v python3.8 || true)"
[ -n "$PY" ] && "$PY" -c 'import ssl,poplib' 2>/dev/null \
  || die "need a python3 with ssl+poplib
    Debian/Pi:  sudo apt install python3
    Cygwin:     setup-x86_64.exe -q -P python39"

# local delivery agent (the puller pipes each fetched message to it)
MDA=""
for m in /usr/sbin/sendmail /usr/lib/sendmail /usr/sbin/exim4 /usr/bin/exim \
         "$(command -v sendmail 2>/dev/null)" "$(command -v exim4 2>/dev/null)"; do
  [ -n "$m" ] && [ -x "$m" ] && { MDA="$m"; break; }
done
[ -n "$MDA" ] || die "no sendmail/exim MDA found - run configure-sendmail-relay.sh first"

# where the SMTP/POP password lives (shared with the send side)
PWFILE=""
if [ -n "$PWFILE_OPT" ]; then
  PWFILE="$PWFILE_OPT"
  [ -r "$PWFILE" ] || warn "--pwfile $PWFILE is not readable yet"
else
  for f in /etc/exim4/passwd.client /etc/exim/passwd.client; do
    [ -r "$f" ] && { PWFILE="$f"; break; }
  done
  [ -n "$PWFILE" ] || { PWFILE="/etc/exim4/passwd.client"
    warn "no readable passwd.client ($PWFILE) - run configure-sendmail-relay.sh, pass --pwfile, or add one by hand"; }
fi

# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------
IN_SERVER=""
if [ -n "$RELAY_FILE" ] && [ -f "$RELAY_FILE" ]; then
  IN_SERVER="$(sed -n 's/^Incoming Server:[[:space:]]*//p' "$RELAY_FILE" | head -1 | tr -d ' \r')"
  [ -n "$POP_USER" ] || POP_USER="$(sed -n 's/^Username:[[:space:]]*//p' "$RELAY_FILE" | head -1 | tr -d ' \r')"
fi
: "${IN_SERVER:=u-l.ca}"
: "${POP_USER:=cwp@u-l.ca}"

[ -r "$PWFILE" ] && { grep -q ":${POP_USER}:" "$PWFILE" \
  || warn "no line for '${POP_USER}' in $PWFILE - pop-pull will fail until one exists"; }

CA=""
for c in /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/certs/ca-bundle.crt \
         /etc/ssl/certs/ca-certificates.crt; do
  [ -f "$c" ] && { CA="$c"; break; }
done
[ "$VERIFY" = 1 ] || CA=""

log "python      : $PY"
log "remote      : ${POP_USER} @ ${IN_SERVER}  POP3S:995"
log "deliver to  : ${LOCAL_USER}  ->  /var/mail/${LOCAL_USER}   (via ${MDA})"
log "server copy : $( [ "$KEEP" = 1 ] && echo 'KEEP + remember seen UIDs' || echo 'DELETE after delivery' )"
log "TLS verify  : $( [ "$VERIFY" = 1 ] && echo "yes (${CA:-system})" || echo 'NO' )"

# --------------------------------------------------------------------------
# ~/.config/mailpull.conf
# --------------------------------------------------------------------------
CFG="$HOME/.config/mailpull.conf"
install -d -m 0755 "$(dirname "$CFG")"
[ -e "$CFG" ] && cp -p "$CFG" "$CFG.bak.$(date +%Y%m%d-%H%M%S)"
cat > "$CFG" <<EOF
# ~/.config/mailpull.conf - generated $(date) by configure-mail-pull.sh
[mailpull]
server     = ${IN_SERVER}
port       = 995
user       = ${POP_USER}
local_user = ${LOCAL_USER}
keep       = $( [ "$KEEP" = 1 ] && echo true || echo false )
verify     = $( [ "$VERIFY" = 1 ] && echo true || echo false )
cafile     = ${CA}
exim       = ${MDA}
pwfile     = ${PWFILE}
EOF
log "wrote $CFG"

# --------------------------------------------------------------------------
# ~/bin/pop-pull  (the puller)
# --------------------------------------------------------------------------
BIN="$HOME/bin"; [ -d "$BIN" ] || BIN="/usr/local/bin"
POP="$BIN/pop-pull"
cat > "$POP" <<PYEOF
#!${PY}
"""pop-pull - on-demand POP3S fetch into the local mailbox via exim.

Config: ~/.config/mailpull.conf   Password: read live from pwfile (host:user:pass).
Seen-UID cache (keep mode): ~/.local/state/mailpull.seen
"""
import os, sys, ssl, poplib, argparse, configparser, subprocess

CFG  = os.path.expanduser("~/.config/mailpull.conf")
SEEN = os.path.expanduser("~/.local/state/mailpull.seen")

def die(m, code=2):
    print("pop-pull: " + m, file=sys.stderr); sys.exit(code)

def password(pwfile, login):
    try:
        for ln in open(pwfile):
            ln = ln.rstrip("\\n")
            if not ln or ln.startswith("#"):
                continue
            p = ln.split(":", 2)
            if len(p) == 3 and p[1] == login:
                return p[2]
    except OSError as e:
        die("cannot read %s: %s" % (pwfile, e))
    die("no password for %s in %s" % (login, pwfile))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("-n", "--dry-run", action="store_true")
    a = ap.parse_args()

    c = configparser.ConfigParser()
    if not c.read(CFG):
        die("missing config %s (run configure-mail-pull.sh)" % CFG)
    s = c["mailpull"]
    server, login = s["server"], s["user"]
    port = s.getint("port", 995)
    local_user = s["local_user"]
    keep = s.getboolean("keep", True)
    verify = s.getboolean("verify", True)
    cafile = s.get("cafile", "").strip()
    exim = s.get("exim", "/usr/bin/exim")
    pw = password(s.get("pwfile", "/etc/exim/passwd.client"), login)

    ctx = ssl.create_default_context()
    if cafile and os.path.exists(cafile):
        ctx.load_verify_locations(cafile)
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    seen = set()
    if keep and os.path.exists(SEEN):
        seen = set(open(SEEN).read().split())

    try:
        M = poplib.POP3_SSL(server, port, context=ctx, timeout=45)
    except Exception as e:
        die("connect %s:%d failed: %s" % (server, port, e))
    try:
        M.user(login); M.pass_(pw)
        n = M.stat()[0]
        uidl = {}
        for line in M.uidl()[1]:
            i, u = line.decode().split(None, 1)
            uidl[int(i)] = u
        got = 0; new = set(seen)
        for i in range(1, n + 1):
            u = uidl.get(i, str(i))
            if keep and u in seen:
                continue
            raw = b"\\r\\n".join(M.retr(i)[1]) + b"\\r\\n"
            if a.dry_run:
                if a.verbose:
                    print("[dry-run] msg %d uid %s (%d bytes)" % (i, u, len(raw)))
            else:
                r = subprocess.run([exim, "-oi", "-oMr", "pop-pull",
                                    "-bm", "--", local_user], input=raw)
                if r.returncode != 0:
                    print("pop-pull: exim delivery failed on msg %d" % i, file=sys.stderr)
                    break
                if a.verbose:
                    print("delivered msg %d uid %s (%d bytes)" % (i, u, len(raw)))
            got += 1
            if keep:
                new.add(u)
            elif not a.dry_run:
                M.dele(i)
        if keep and not a.dry_run:
            os.makedirs(os.path.dirname(SEEN), exist_ok=True)
            open(SEEN, "w").write("\\n".join(sorted(new)) + "\\n")
        M.quit()
    except Exception as e:
        try: M.quit()
        except Exception: pass
        die(str(e))

    if got:
        print("pop-pull: %d new message(s) -> /var/mail/%s" % (got, local_user))
        sys.exit(0)
    print("pop-pull: no new mail")
    sys.exit(1)

if __name__ == "__main__":
    main()
PYEOF
chmod +x "$POP"
log "wrote $POP"

# keep the earlier name working too
ln -sf pop-pull "$BIN/pull-mail" 2>/dev/null && log "linked $BIN/pull-mail -> pop-pull" || true

# --------------------------------------------------------------------------
# optional: a systemd --user timer that pulls mail every N minutes
# --------------------------------------------------------------------------
if [ "$TIMER_MIN" != 0 ] && [ "$TIMER_MIN" -gt 0 ] 2>/dev/null; then
  if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    ud="$HOME/.config/systemd/user"; install -d -m 0700 "$ud"
    cat > "$ud/tvmail-pull.service" <<EOF
[Unit]
Description=tvmail: fetch remote mail (pop-pull)
[Service]
Type=oneshot
ExecStart=$POP
EOF
    cat > "$ud/tvmail-pull.timer" <<EOF
[Unit]
Description=tvmail: pull mail every ${TIMER_MIN} min
[Timer]
OnBootSec=2min
OnUnitActiveSec=${TIMER_MIN}min
Persistent=true
[Install]
WantedBy=timers.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now tvmail-pull.timer
    log "systemd --user timer: tvmail-pull.timer (every ${TIMER_MIN} min)"
    log "  status:  systemctl --user list-timers tvmail-pull.timer"
    log "  (needs 'loginctl enable-linger $USER' to run while you're logged out)"
  else
    warn "--timer given but no systemd --user available; run 'pop-pull' from cron instead"
  fi
fi

# --------------------------------------------------------------------------
# optional test
# --------------------------------------------------------------------------
if [ "$DO_TEST" = 1 ]; then
  echo
  log "running one fetch ..."
  before="$(wc -c < "/var/mail/${LOCAL_USER}" 2>/dev/null || echo 0)"
  "$PY" "$POP" -v || true
  after="$(wc -c < "/var/mail/${LOCAL_USER}" 2>/dev/null || echo 0)"
  log "mailbox /var/mail/${LOCAL_USER}: ${before} -> ${after} bytes"
  log "read it with:  mail"
fi

echo
log "Done.  Fetch on demand:   pop-pull -v      (dry run: pop-pull -n -v)"
log "Config:  $CFG      Seen-UID cache:  ~/.local/state/mailpull.seen"
log "Switch keep<->delete or verify: re-run this script with/without --delete / --no-verify"
