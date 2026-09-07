#!/usr/bin/env bash
#
# configure-mail-pull.sh
#
# Set up an ON-DEMAND pull of remote mail (POP3S) into the local mailbox.
# Installs a small, dependency-free Python puller ("pop-pull") that:
#   * connects to the incoming server over implicit TLS (port 995)
#   * reads the mailbox password LIVE from /etc/exim/passwd.client
#     (nothing new stores the secret)
#   * hands each message to exim as a local submission, so it lands in
#     /var/mail/<user> via the same local_delivery transport as everything else
#   * by default KEEPs mail on the server and remembers what it has already
#     fetched (UIDL list in ~/.local/state/mailpull.seen); --delete removes it
#
# No daemon.  Run 'pop-pull' (or 'pull-mail') whenever you want mail.
#
# Usage (from a Cygwin shell):
#   ./configure-mail-pull.sh --relay-file /path/to/smtp-relay.txt [options]
#
# Options:
#   --relay-file FILE   cPanel-style config text -> Incoming Server / Username.
#   --pop-user NAME     remote mailbox login  (default: from file / passwd.client)
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

while [ $# -gt 0 ]; do
  case "$1" in
    --relay-file) RELAY_FILE="${2:?}"; shift 2 ;;
    --pop-user)   POP_USER="${2:?}";   shift 2 ;;
    --local-user) LOCAL_USER="${2:?}"; shift 2 ;;
    --delete)     KEEP=0; shift ;;
    --no-verify)  VERIFY=0; shift ;;
    --test)       DO_TEST=1; shift ;;
    -h|--help)    sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//; $d'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

log()  { printf '  %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(uname -o 2>/dev/null)" = "Cygwin" ] || die "run this inside Cygwin"

PY="$(command -v python3 || command -v python3.9 || command -v python3.8 || true)"
[ -n "$PY" ] && "$PY" -c 'import ssl,poplib' 2>/dev/null \
  || die "need a python3 with ssl+poplib - install the 'python39' Cygwin package"

EXIM="$(command -v exim || echo /usr/bin/exim)"
[ -x "$EXIM" ] || die "exim not found - run configure-sendmail-relay.sh first"
[ -r /etc/exim/passwd.client ] || die "/etc/exim/passwd.client not readable - run configure-sendmail-relay.sh first"

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

grep -q ":${POP_USER}:" /etc/exim/passwd.client \
  || warn "no line for '${POP_USER}' in /etc/exim/passwd.client - pop-pull will fail until one exists"

CA=""
for c in /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/certs/ca-bundle.crt \
         /etc/ssl/certs/ca-certificates.crt; do
  [ -f "$c" ] && { CA="$c"; break; }
done
[ "$VERIFY" = 1 ] || CA=""

log "python      : $PY"
log "remote      : ${POP_USER} @ ${IN_SERVER}  POP3S:995"
log "deliver to  : ${LOCAL_USER}  ->  /var/mail/${LOCAL_USER}   (via ${EXIM})"
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
exim       = ${EXIM}
pwfile     = /etc/exim/passwd.client
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
