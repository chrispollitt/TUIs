#!/usr/bin/env bash
#
# configure-sendmail-relay.sh
#
# Configure Cygwin's Exim MTA as a *send-only* system sendmail:
#
#   * local recipients        -> /var/mail/<user>       (mbox, appendfile)
#   * every other recipient    -> authenticated implicit-TLS smarthost
#   * locally generated senders (user@thisbox, *.localdomain, ...) have their
#     envelope-from, From:, Sender: and Reply-To: rewritten to the smarthost
#     mailbox, and a Reply-To: is added when the message has none - this box
#     cannot receive mail, so replies and bounces must go somewhere real.
#
# There is NO SMTP listener and NO service.  /usr/sbin/sendmail just works and
# delivers immediately when called.
#
# Re-runnable.  Anything it overwrites is copied to /etc/BAK/ first.
#
# Usage (from a Cygwin shell):
#   ./configure-sendmail-relay.sh --relay-file /path/to/smtp-relay.txt [options]
#
# Options:
#   --relay-file FILE   cPanel-style "Mail Client Configuration" text to read the
#                       smarthost host / port / username (and password) from.
#   --user NAME         local account that receives root's mail   (default: you)
#   --cron              also add a 15-minute "exim -q" crontab entry so deferred
#                       mail is retried automatically (needs the 'cron' package).
#   --test ADDR         after configuring, send a test message to ADDR and to the
#                       local admin user, printing the SMTP conversation.
#   -h | --help         show this header.
#
set -euo pipefail

RELAY_FILE=""
ADMIN_USER="$(id -un)"
ADD_CRON=0
TEST_ADDR=""

while [ $# -gt 0 ]; do
  case "$1" in
    --relay-file)  RELAY_FILE="${2:?}"; shift 2 ;;
    --user)        ADMIN_USER="${2:?}"; shift 2 ;;
    --rewrite-all) shift ;;   # deprecated no-op: outbound From is always rewritten now
    --cron)        ADD_CRON=1; shift ;;
    --test)        TEST_ADDR="${2:?}"; shift 2 ;;
    -h|--help)     sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//; $d'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

log()  { printf '  %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(uname -o 2>/dev/null)" = "Cygwin" ] || die "run this inside Cygwin"

# --- locate the REAL exim binary -----------------------------------------
# Cygwin ships /usr/bin/exim as a bootstrap symlink to 'exim-config', a stub
# that only prints "You must run exim-config" (exit 127) until repaired. We do
# the single repair we need here instead of running the interactive exim-config
# (which would also write its own config and offer to install a daemon).
REAL_EXIM=""
for c in /usr/bin/exim-*.exe /usr/sbin/exim.exe /usr/sbin/exim \
         /usr/libexec/exim /usr/exim/bin/exim; do
  [ -f "$c" ] && [ -x "$c" ] || continue
  case "${c##*/}" in exim-config) continue ;; esac
  if "$c" -bV 2>/dev/null | grep -qi '^Exim version'; then REAL_EXIM="$c"; break; fi
  [ -z "$REAL_EXIM" ] && REAL_EXIM="$c"
done
if [ -z "$REAL_EXIM" ]; then
  p="$(command -v exim 2>/dev/null || true)"
  rp="$(readlink -f "$p" 2>/dev/null || echo "$p")"
  case "${rp##*/}" in
    ''|exim-config) : ;;
    *) [ -x "$rp" ] && REAL_EXIM="$rp" ;;
  esac
fi
[ -n "$REAL_EXIM" ] || die "cannot find the real exim binary (looked for /usr/bin/exim-*.exe).
  Install / repair the 'exim' package:
    D:\\cygwin\\packages\\setup-x86_64.exe -q -P exim"

# repair the Cygwin bootstrap symlink so bare 'exim', 'mailq', 'runq' also work
if [ -L /usr/bin/exim ] && \
   [ "$(readlink -f /usr/bin/exim 2>/dev/null || true)" != "$(readlink -f "$REAL_EXIM")" ]; then
  ln -sf "$REAL_EXIM" /usr/bin/exim && log "repaired bootstrap symlink: /usr/bin/exim -> $REAL_EXIM"
fi

EXIM="$REAL_EXIM"
"$EXIM" -bV >/dev/null 2>&1 || warn "'$EXIM -bV' reports problems (probably just the old config) - continuing; this script rewrites it"
log "exim binary : $EXIM"

# --------------------------------------------------------------------------
# 1. Smarthost parameters (from the relay file, with prompts as fallback)
# --------------------------------------------------------------------------
field() { sed -n "s/^$1:[[:space:]]*//p" "$RELAY_FILE" 2>/dev/null | head -1 | tr -d ' \r'; }

SMARTHOST_HOST="" ; SMARTHOST_USER="" ; SMARTHOST_PASS="" ; SMARTHOST_PORT=""
if [ -n "$RELAY_FILE" ] && [ -f "$RELAY_FILE" ]; then
  log "reading smarthost settings from $RELAY_FILE"
  SMARTHOST_HOST="$(field 'Outgoing Server')"
  SMARTHOST_USER="$(field 'Username')"
  SMARTHOST_PASS="$(field 'Password')"
  SMARTHOST_PORT="$(sed -n 's/.*SMTP Port:[[:space:]]*\([0-9]\{1,\}\).*/\1/p' "$RELAY_FILE" | head -1 | tr -d ' \r')"
fi

: "${SMARTHOST_HOST:=u-l.ca}"
: "${SMARTHOST_PORT:=465}"
: "${SMARTHOST_USER:=cwp@u-l.ca}"

reuse_pw=0
if [ -z "$SMARTHOST_PASS" ] || [ "$SMARTHOST_PASS" = "SECRET" ]; then
  if [ -n "${SMARTHOST_PASS_ENV:-}" ]; then
    SMARTHOST_PASS="$SMARTHOST_PASS_ENV"
  elif [ -f /etc/exim/passwd.client ]; then
    reuse_pw=1
    log "no password given - keeping existing /etc/exim/passwd.client"
  else
    printf 'SMTP password for %s (host %s): ' "$SMARTHOST_USER" "$SMARTHOST_HOST" >&2
    read -rs SMARTHOST_PASS; echo >&2
  fi
fi
[ "$reuse_pw" = 1 ] || [ -n "$SMARTHOST_PASS" ] || die "no SMTP password supplied"

HOSTN="$(hostname | tr 'A-Z' 'a-z')"
REWRITE_TO="$SMARTHOST_USER"
if [ "$SMARTHOST_PORT" = "465" ]; then PROTO="smtps"; else PROTO="smtp"; fi
GRP="$(id -gn "$ADMIN_USER" 2>/dev/null || id -gn)"

log "smarthost   : ${SMARTHOST_HOST}:${SMARTHOST_PORT}  (${PROTO}, AUTH LOGIN/PLAIN)"
log "auth user   : ${SMARTHOST_USER}"
log "local host  : ${HOSTN}"
log "rewrite     : outbound relay only -> From: \"<user> via <host>\" <${REWRITE_TO}>  (local mail untouched)"
log "root's mail  : ${ADMIN_USER}"

# --------------------------------------------------------------------------
# 2. Directories
# --------------------------------------------------------------------------
install -d -m 1777 /var/mail
install -d -m 1777 /var/spool/exim
install -d -m 1777 /var/log/exim
install -d -m 0755 /etc/exim
install -d -m 0755 /etc/BAK

# A mailbox left by a broken earlier run can carry an ACL that Cygwin maps to a
# non-writable mode (e.g. 564), which exim then refuses ("wrong mode ..."). If
# it is empty, drop it so exim recreates it cleanly; if it has mail, just warn.
mb="/var/mail/${ADMIN_USER}"
if [ -f "$mb" ] && [ ! -s "$mb" ] && \
   { [ ! -w "$mb" ] || [ "$(stat -c %a "$mb" 2>/dev/null)" != "600" ]; }; then
  rm -f "$mb" && log "removed stale empty $mb (exim recreates it at mode 600)"
elif [ -f "$mb" ] && [ ! -w "$mb" ]; then
  chmod 600 "$mb" 2>/dev/null || true
  [ -w "$mb" ] || warn "$mb is not writable - fix by hand:  chmod 600 $mb"
fi

backup() {
  [ -e "$1" ] || return 0
  cp -p "$1" "/etc/BAK/$(basename "$1").$(date +%Y%m%d-%H%M%S)"
  log "backed up $1 -> /etc/BAK/"
}

# --------------------------------------------------------------------------
# 3. Smarthost credentials  (0600, the only file that holds the password)
# --------------------------------------------------------------------------
if [ "$reuse_pw" = 0 ]; then
  case "$SMARTHOST_PASS" in
    *:*) warn "password contains ':' - handled, but keep an eye on auth failures" ;;
  esac
  backup /etc/exim/passwd.client
  ( umask 077
    cat > /etc/exim/passwd.client <<EOF
# <smarthost or *> : <login> : <password>      -- keep this file mode 0600
${SMARTHOST_HOST}:${SMARTHOST_USER}:${SMARTHOST_PASS}
*:${SMARTHOST_USER}:${SMARTHOST_PASS}
EOF
  )
  chmod 600 /etc/exim/passwd.client
  log "wrote /etc/exim/passwd.client (0600)"
fi

# --------------------------------------------------------------------------
# 4. Aliases + mailname
# --------------------------------------------------------------------------
if [ ! -f /etc/aliases ]; then
  cat > /etc/aliases <<EOF
# keep the standard role accounts pointed at a human
postmaster:     root
mailer-daemon:  root
abuse:          root
nobody:         root
root:           ${ADMIN_USER}
EOF
  log "created /etc/aliases (root -> ${ADMIN_USER})"
elif ! grep -Eq '^[[:space:]]*root:' /etc/aliases; then
  backup /etc/aliases
  printf 'root: %s\n' "$ADMIN_USER" >> /etc/aliases
  log "appended 'root: ${ADMIN_USER}' to existing /etc/aliases"
fi
echo "$SMARTHOST_HOST" > /etc/mailname

# --------------------------------------------------------------------------
# 5. exim.conf
# --------------------------------------------------------------------------
CONF="$({ "$EXIM" -bV 2>/dev/null || true; } | sed -n 's/^Configuration file is //p' | head -1 || true)"
: "${CONF:=/etc/exim.conf}"
log "config file : $CONF"
backup "$CONF"

cat > "$CONF" <<EOF
######################################################################
#  $CONF
#  send-only smarthost relay + local /var/mail delivery
#  generated $(date) by configure-sendmail-relay.sh  (re-run to refresh)
######################################################################

# ---- smarthost / rewrite parameters --------------------------------
SMARTHOST_HOST = ${SMARTHOST_HOST}
SMARTHOST_PORT = ${SMARTHOST_PORT}
REWRITE_TO     = ${REWRITE_TO}

# credentials are read from this 0600 file, never stored in this config
PWLINE    = \${lookup{\$host}lsearch{/etc/exim/passwd.client}{\$value}fail}
AUTH_USER = \${extract{1}{:}{PWLINE}}
AUTH_PASS = \${sg{PWLINE}{^[^:]*:}{}}

# ---- main ---------------------------------------------------------
primary_hostname = ${HOSTN}
domainlist local_domains    = @ : localhost : localhost.localdomain : ${HOSTN} : ${HOSTN}.localdomain
domainlist relay_to_domains =
hostlist   relay_from_hosts = <; 127.0.0.1 ; ::1

qualify_domain    = ${HOSTN}
qualify_recipient = ${HOSTN}

exim_path       = ${EXIM}
exim_user       = ${ADMIN_USER}
exim_group      = ${GRP}
trusted_users   = ${ADMIN_USER}
spool_directory = /var/spool/exim
log_file_path   = /var/log/exim/%slog
log_selector    = +smtp_confirmation +tls_peerdn +received_recipients

acl_smtp_rcpt = acl_check_rcpt
acl_smtp_data = acl_check_data

message_size_limit         = 50M
timeout_frozen_after       = 7d
ignore_bounce_errors_after = 2d
host_lookup   =
rfc1413_hosts =
# silence exim's "purging the environment" warning on every invocation
keep_environment =

# TLS as a client to the smarthost. Verification is attempted but not
# required; set 'tls_verify_hosts = *' to make a bad cert fatal.
tls_verify_certificates = system
tls_try_verify_hosts    = *

# ---- ACL (only local / authenticated submission is accepted) ------
begin acl

acl_check_rcpt:
  accept  hosts = :
  deny    message     = restricted characters in address
          domains     = +local_domains
          local_parts = ^[.] : ^.*[@%!/|]
  accept  domains = +local_domains
          endpass
          verify  = recipient
  accept  hosts = +relay_from_hosts
  accept  authenticated = *
  deny    message = relay not permitted

acl_check_data:
  accept

# ---- client authenticators (this box -> smarthost) ---------------
begin authenticators

smarthost_login:
  driver           = plaintext
  public_name      = LOGIN
  hide client_send = <; ; AUTH_USER ; AUTH_PASS

smarthost_plain:
  driver           = plaintext
  public_name      = PLAIN
  hide client_send = <; ^AUTH_USER^AUTH_PASS

# ---- routers ----------------------------------------------------
begin routers

system_aliases:
  driver         = redirect
  domains        = +local_domains
  data           = \${lookup{\$local_part}lsearch{/etc/aliases}}
  file_transport = address_file
  pipe_transport = address_pipe
  allow_defer
  allow_fail

local_user:
  driver         = accept
  domains        = +local_domains
  check_local_user
  transport      = local_delivery
  cannot_route_message = no such local mailbox: \$local_part

smarthost:
  driver           = manualroute
  domains          = ! +local_domains
  transport        = smarthost_smtp
  route_list       = * SMARTHOST_HOST::SMARTHOST_PORT
  host_find_failed = defer
  no_more

# ---- transports -----------------------------------------------
begin transports

local_delivery:
  driver            = appendfile
  # \$local_part_data is the untainted copy set by check_local_user on the
  # router; Exim 4.95 forbids tainted \$local_part in a file path.
  file              = /var/mail/\${local_part_data}
  create_file       = anywhere
  delivery_date_add
  envelope_to_add
  return_path_add
  mode              = 0600
  mode_fail_narrower = false

smarthost_smtp:
  driver             = smtp
  port               = SMARTHOST_PORT
  protocol           = ${PROTO}
  hosts_require_auth = *
  hosts_require_tls  = *
  tls_sni            = SMARTHOST_HOST
  # Rewrite the sender identity for OUTBOUND RELAY ONLY - locally delivered
  # mail (appendfile to /var/mail) never reaches this transport and keeps its
  # original headers. The box can't receive mail, so From:/Reply-To:/envelope
  # point at the real mailbox while the original sender stays visible.
  return_path        = REWRITE_TO
  headers_remove     = from : sender : reply-to
  headers_add        = From: "\${sender_address_local_part} via \${sender_address_domain}" <REWRITE_TO>\nReply-To: REWRITE_TO

address_file:
  driver            = appendfile
  delivery_date_add
  envelope_to_add
  return_path_add

address_pipe:
  driver            = pipe
  return_fail_output

# ---- retry (so a smarthost blip defers instead of bouncing) ------
begin retry
*   *   F,2h,15m; G,16h,1h,1.5; F,4d,6h

# No global 'begin rewrite' section on purpose: sender rewriting happens only
# on the smarthost_smtp transport, so locally delivered mail is left as-is.
EOF

chmod 644 "$CONF"
log "wrote $CONF"

# --------------------------------------------------------------------------
# 6. Wire up the system sendmail interface
# --------------------------------------------------------------------------
for s in /usr/sbin/sendmail /usr/lib/sendmail /usr/bin/sendmail; do
  install -d "$(dirname "$s")"
  if [ -e "$s" ] && [ ! -L "$s" ]; then
    backup "$s"; rm -f "$s"
  fi
  ln -sf "$EXIM" "$s" && log "linked $s -> $EXIM"
done

# --------------------------------------------------------------------------
# 7. mail / mailx command-line client (GNU Mailutils)
# --------------------------------------------------------------------------
MAILBIN="$(command -v mail 2>/dev/null || true)"
if [ -n "$MAILBIN" ]; then
  mdir="$(dirname "$MAILBIN")"
  [ -e "$mdir/mailx" ] || { ln -sf mail "$mdir/mailx" && log "linked $mdir/mailx -> mail"; }
  if [ ! -e /etc/mailrc ] || ! grep -q '^set sendmail' /etc/mailrc 2>/dev/null; then
    backup /etc/mailrc
    echo 'set sendmail="/usr/sbin/sendmail"' >> /etc/mailrc
    log "set sendmail=/usr/sbin/sendmail in /etc/mailrc"
  fi
  log "mail client : $MAILBIN (reads /var/mail/\$USER, sends via sendmail)"
else
  warn "no 'mail' command found - install it with:  D:\\cygwin\\packages\\setup-x86_64.exe -q -P mailutils   then re-run this script"
fi

# --------------------------------------------------------------------------
# 8. Optional automatic retry of deferred mail (no listener, just a cron pass)
# --------------------------------------------------------------------------
if [ "$ADD_CRON" = 1 ]; then
  if command -v crontab >/dev/null 2>&1; then
    ( crontab -l 2>/dev/null | grep -v 'configure-sendmail-relay: exim -q'
      echo "*/15 * * * * ${EXIM} -q   # configure-sendmail-relay: exim -q" ) | crontab -
    log "added */15 'exim -q' crontab entry (ensure 'cron' service/cygrunsrv is running)"
  else
    warn "--cron requested but 'crontab' not found; install the 'cron' package"
  fi
fi

# --------------------------------------------------------------------------
# 9. Validate
# --------------------------------------------------------------------------
echo
log "syntax check ..."
if "$EXIM" -bV >/dev/null 2>&1; then
  log "  config parses OK"
else
  warn "exim -bV reported problems:"
  "$EXIM" -bV 2>&1 | sed 's/^/    /' || true
fi
log "route test  : local '${ADMIN_USER}'"
"$EXIM" -bt "$ADMIN_USER"     2>&1 | sed 's/^/    /' || true
log "route test  : external 'someone@example.net'"
"$EXIM" -bt someone@example.net 2>&1 | sed 's/^/    /' || true

# --------------------------------------------------------------------------
# 10. Optional live test
# --------------------------------------------------------------------------
if [ -n "$TEST_ADDR" ]; then
  echo
  log "sending test message to ${TEST_ADDR} and to ${ADMIN_USER} ..."
  printf 'Subject: exim smarthost relay test\nFrom: %s@%s\n\nSent %s via %s\n' \
         "$ADMIN_USER" "$HOSTN" "$(date)" "$SMARTHOST_HOST" \
    | "$EXIM" -v -i "$TEST_ADDR" "$ADMIN_USER" 2>&1 | sed 's/^/    /' || true
  log "local copy : /var/mail/${ADMIN_USER}"
  log "queue      : $("$EXIM" -bpc 2>/dev/null || echo '?') message(s) waiting"
fi

echo
log "Done. sendmail is: $(ls -l /usr/sbin/sendmail | sed 's/.*-> //')"
log "Logs : /var/log/exim/mainlog   (rejects: rejectlog, panics: paniclog)"
log "Queue: exim -bp     Flush: exim -qff -v     Local mail: /var/mail/<user>"
