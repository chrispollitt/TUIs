#!/usr/bin/env bash
#
# configure-sendmail-relay.sh
#
# Configure the system MTA as a send-only sendmail: local mail -> /var/mail,
# everything else -> an authenticated TLS smarthost, with locally generated
# senders rewritten to the real mailbox (this box can't receive replies).
#
#   Cygwin  - hand-written /etc/exim.conf, no daemon.
#   Linux   - configures whatever real MTA is installed: Postfix (in place,
#             no apt) or Debian/Ubuntu/Pi exim4 (via update-exim4.conf).  If
#             nothing is installed it apt-installs Postfix.  You're asked
#             whether to run it as a service (systemd / sysv / none).
#
# Re-runnable.
#
# Usage:
#   sudo ./configure-sendmail-relay.sh --relay-file smtp-relay.txt [options]
#
# Options:
#   --relay-file FILE   cPanel-style "Mail Client Configuration" text to read the
#                       smarthost host / port / username (and password) from.
#   --user NAME         local account that receives root's mail   (default: you)
#   --users "A B ..."   Cygwin: every local account that should be able to send
#                       mail.  More than one switches on multi-user mode: the
#                       mail spool / queue / logs and the smarthost credential
#                       file become group-owned by --mail-group, and mailboxes
#                       are group-writable.  (default: just --user)
#   --mail-group NAME   Cygwin: the shared group for multi-user mode
#                       (default: Administrators - exim's compiled CONFIGURE_GROUP)
#   --mta KIND         Linux: force  postfix | exim4  (default: use whatever is
#                       installed; install Postfix if nothing is)
#   --service KIND      Linux: systemd | sysv | none   (default: ask)
#   --smtp-port N       override the smarthost submission port
#   --cron              add a 15-minute queue-runner cron entry (send-only mode)
#   --test ADDR         after configuring, send a test message to ADDR and you
#   -h | --help         show this header.
#
set -euo pipefail

RELAY_FILE=""
ADMIN_USER="$(id -un)"
ADD_CRON=0
TEST_ADDR=""
SERVICE=""
SMTP_PORT=""
MTA_OPT=""
USERS_STR=""
MAIL_GRP="Administrators"

while [ $# -gt 0 ]; do
  case "$1" in
    --relay-file)  RELAY_FILE="${2:?}"; shift 2 ;;
    --user)        ADMIN_USER="${2:?}"; shift 2 ;;
    --users)       USERS_STR="${2:?}";  shift 2 ;;
    --mail-group)  MAIL_GRP="${2:?}";   shift 2 ;;
    --service)     SERVICE="${2:?}";    shift 2 ;;
    --smtp-port)   SMTP_PORT="${2:?}";  shift 2 ;;
    --mta)         MTA_OPT="${2:?}";    shift 2 ;;
    --rewrite-all) shift ;;   # deprecated no-op: outbound From is always rewritten now
    --cron)        ADD_CRON=1; shift ;;
    --test)        TEST_ADDR="${2:?}"; shift 2 ;;
    -h|--help)     sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//; $d'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# Cygwin multi-user: --users lists every account that may send.  >1 flips on
# group-shared perms (mail spool/queue/logs + credential file) so exim - which
# runs unprivileged as whoever invoked sendmail - can still deliver and relay.
[ -n "$USERS_STR" ] || USERS_STR="$ADMIN_USER"
# shellcheck disable=SC2206
MAIL_USERS=($USERS_STR)
MULTI=0
[ "${#MAIL_USERS[@]}" -gt 1 ] && MULTI=1

log()  { printf '  %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# ==========================================================================
# Linux: configure whatever real MTA is installed (Postfix or Debian exim4);
# install Postfix only if nothing is there.
# ==========================================================================
_enable_service() {   # $1 = service name; uses $svc / $SUDO
    case "$svc" in
      systemd) $SUDO systemctl enable --now "$1"
               log "$1: enabled + running (systemd)" ;;
      sysv)    $SUDO update-rc.d "$1" defaults >/dev/null 2>&1 || true
               $SUDO service "$1" restart
               log "$1: enabled + running (sysv init)" ;;
      *)       $SUDO systemctl disable --now "$1" 2>/dev/null \
                 || $SUDO service "$1" stop 2>/dev/null || true
               log "$1: not run as a service" ;;
    esac
}

_cfg_exim4() {   # Debian/Ubuntu/Pi exim4 via update-exim4.conf  (no apt needed)
    $SUDO tee /etc/exim4/update-exim4.conf.conf >/dev/null <<EOF
# generated $(date) by tvmail configure-sendmail-relay.sh
dc_eximconfig_configtype='smarthost'
dc_other_hostnames=''
dc_local_interfaces='127.0.0.1 ; ::1'
dc_readhost='${H}'
dc_relay_domains=''
dc_minimaldns='false'
dc_relay_nets=''
dc_smarthost='${H}::${PORT}'
CFILEMODE='644'
dc_use_split_config='false'
dc_hide_mailname='true'
dc_mailname_in_oh='true'
dc_localdelivery='mail_spool'
EOF
    if [ "$reuse" = 0 ]; then
        printf '*:%s:%s\n' "$U" "$P" | $SUDO tee /etc/exim4/passwd.client >/dev/null
        $SUDO chmod 640 /etc/exim4/passwd.client
        $SUDO chgrp Debian-exim /etc/exim4/passwd.client 2>/dev/null || true
    fi
    $SUDO grep -qs REMOTE_SMTP_SMARTHOST_HOSTS_REQUIRE_TLS /etc/exim4/exim4.conf.localmacros \
      || echo "REMOTE_SMTP_SMARTHOST_HOSTS_REQUIRE_TLS = *" \
         | $SUDO tee -a /etc/exim4/exim4.conf.localmacros >/dev/null
    $SUDO touch /etc/email-addresses
    $SUDO grep -qs "^${ADMIN_USER}:" /etc/email-addresses \
      || printf '%s: %s\n' "$ADMIN_USER" "$U" | $SUDO tee -a /etc/email-addresses >/dev/null
    $SUDO grep -qs '^root:' /etc/aliases \
      || printf 'root: %s\n' "$ADMIN_USER" | $SUDO tee -a /etc/aliases >/dev/null
    command -v newaliases >/dev/null 2>&1 && $SUDO newaliases 2>/dev/null || true
    $SUDO update-exim4.conf
    log "exim4: /etc/exim4/{update-exim4.conf.conf, passwd.client, exim4.conf.localmacros} + /etc/email-addresses"
    _enable_service exim4
    if [ "$svc" != systemd ] && [ "$svc" != sysv ] && [ "$ADD_CRON" = 1 ]; then
        printf '*/15 * * * * root exim4 -q\n' | $SUDO tee /etc/cron.d/tvmail-eximq >/dev/null
        log "added /etc/cron.d/tvmail-eximq (15-min queue runner)"
    fi
}

_cfg_postfix() {   # configure an already-installed Postfix as a smarthost relay
    hn="$(hostname 2>/dev/null || echo localhost)"
    fqdn="$(hostname -f 2>/dev/null || echo "$hn")"
    $SUDO postconf -e \
      "relayhost = [${H}]:${PORT}" \
      "smtp_sasl_auth_enable = yes" \
      "smtp_sasl_password_maps = hash:/etc/postfix/sasl_passwd" \
      "smtp_sasl_security_options = noanonymous" \
      "smtp_sasl_tls_security_options = noanonymous" \
      "smtp_tls_security_level = encrypt" \
      "smtp_use_tls = yes" \
      "smtp_generic_maps = hash:/etc/postfix/generic" \
      "inet_interfaces = loopback-only" \
      "mydestination = \$myhostname, localhost.\$mydomain, localhost"
    if [ "$reuse" = 0 ]; then
        printf '[%s]:%s %s:%s\n' "$H" "$PORT" "$U" "$P" | $SUDO tee /etc/postfix/sasl_passwd >/dev/null
        $SUDO chmod 600 /etc/postfix/sasl_passwd
    fi
    $SUDO postmap /etc/postfix/sasl_passwd
    # one-way rewrite: every locally originated address -> the real mailbox,
    # applied only on outbound (smtp_generic_maps)
    $SUDO tee /etc/postfix/generic >/dev/null <<EOF
@${hn}                  ${U}
@${fqdn}                ${U}
@localhost              ${U}
@localhost.localdomain  ${U}
root@localhost          ${U}
${ADMIN_USER}@localhost ${U}
EOF
    $SUDO postmap /etc/postfix/generic
    $SUDO grep -qs '^root:' /etc/aliases \
      || printf 'root: %s\n' "$ADMIN_USER" | $SUDO tee -a /etc/aliases >/dev/null
    $SUDO newaliases 2>/dev/null || true
    log "postfix: relayhost=[${H}]:${PORT}, SASL+TLS, generic rewrite -> ${U}, loopback-only"
    log "  (for LAN delivery later: inet_interfaces=all + mynetworks=<your /24>)"
    _enable_service postfix
}

linux_setup() {
    SUDO=""
    if [ "$(id -u)" != 0 ]; then
        command -v sudo >/dev/null 2>&1 || die "run as root (no sudo found)"
        SUDO="sudo"
    fi

    _rv() { sed -n "s/^$1:[[:space:]]*//p" "$RELAY_FILE" 2>/dev/null | head -1 | tr -d ' \r'; }
    H=""; U=""; P=""; PORT=""
    if [ -n "$RELAY_FILE" ] && [ -f "$RELAY_FILE" ]; then
        H="$(_rv 'Outgoing Server')"; U="$(_rv Username)"; P="$(_rv Password)"
        PORT="$(sed -n 's/.*SMTP Port:[[:space:]]*\([0-9]\{1,\}\).*/\1/p' "$RELAY_FILE" | head -1 | tr -d ' \r')"
    fi
    : "${H:=u-l.ca}"; : "${U:=cwp@u-l.ca}"
    case "$PORT" in 465|"") PORT=587 ;; esac    # STARTTLS submission (465 needs extra wiring)
    [ -n "$SMTP_PORT" ] && PORT="$SMTP_PORT"

    # --- which MTA is here? ---
    MTA="$MTA_OPT"
    if [ -z "$MTA" ]; then
        if   command -v postfix >/dev/null 2>&1; then MTA=postfix
        elif command -v exim4 >/dev/null 2>&1 && command -v update-exim4.conf >/dev/null 2>&1; then MTA=exim4
        elif command -v exim  >/dev/null 2>&1 || command -v exim4 >/dev/null 2>&1; then MTA=exim
        elif [ -f /etc/mail/sendmail.mc ]; then MTA=sendmail
        elif command -v msmtp >/dev/null 2>&1; then MTA=msmtp
        else MTA=none
        fi
    fi

    case "$MTA" in
      none)
        command -v apt-get >/dev/null 2>&1 || die \
"no MTA installed and no apt to add one.
  Install postfix (or exim4), then re-run this script."
        log "no MTA found - installing Postfix ..."
        $SUDO apt-get update -qq || true
        $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y postfix >/dev/null || die \
"could not install postfix (EOL release / no network?).  Install an MTA by hand."
        MTA=postfix ;;
      sendmail)
        die "the Sendmail MTA is installed; automating its .mc is out of scope.
  Set SMART_HOST + AUTH in /etc/mail/sendmail.mc and /etc/mail/authinfo yourself,
  or 'apt install postfix' / 'exim4' and re-run with --mta." ;;
      exim)
        die "exim is installed but without Debian's update-exim4.conf.
  Configure /etc/exim/exim.conf for an authenticated smarthost by hand, or
  install exim4-daemon-light / postfix and re-run." ;;
      msmtp)
        warn "msmtp is a send-only relay with no local /var/mail delivery, which
      the tvmail 'inbox' folder needs.  'apt install postfix' for the full
      local+relay setup; msmtp is not configured here." ;;
    esac
    [ "$MTA" = postfix ] || [ "$MTA" = exim4 ] || die "no configurable MTA - see messages above."
    log "MTA       : $MTA"

    # --- password (per-MTA store) ---
    case "$MTA" in
      exim4)   PW_STORE=/etc/exim4/passwd.client ;;
      postfix) PW_STORE=/etc/postfix/sasl_passwd ;;
    esac
    reuse=0
    if [ -z "$P" ] || [ "$P" = SECRET ]; then
        if [ -n "${SMARTHOST_PASS_ENV:-}" ]; then P="$SMARTHOST_PASS_ENV"
        elif $SUDO test -r "$PW_STORE"; then reuse=1; log "reusing existing $PW_STORE"
        else printf 'SMTP password for %s (host %s): ' "$U" "$H" >&2
             read -rs P; echo >&2
        fi
    fi
    [ "$reuse" = 1 ] || [ -n "$P" ] || die "no SMTP password supplied"

    # --- run exim as a background service?  (Postfix always needs its daemon) ---
    have_sd=0; have_sv=0
    [ -d /run/systemd/system ] && have_sd=1
    { command -v service >/dev/null 2>&1 && [ -d /etc/init.d ]; } && have_sv=1
    svc="$SERVICE"
    if [ -z "$svc" ]; then
        def=none
        [ "$have_sv" = 1 ] && def=sysv
        [ "$have_sd" = 1 ] && def=systemd
        echo
        echo "Run the MTA as a background service (queue runner, deferred-mail retries)?"
        printf "  options: "
        [ "$have_sd" = 1 ] && printf "systemd "
        [ "$have_sv" = 1 ] && printf "sysv "
        printf "none\n  choice [%s]: " "$def"
        read -r svc || true; : "${svc:=$def}"
    fi
    if [ "$MTA" = postfix ] && [ "$svc" != systemd ] && [ "$svc" != sysv ]; then
        [ "$have_sd" = 1 ] && svc=systemd || { [ "$have_sv" = 1 ] && svc=sysv || svc=systemd; }
        warn "Postfix needs its daemon to accept local mail - using '$svc'"
    fi

    echo
    log "smarthost : ${H}::${PORT}  (STARTTLS, AUTH)"
    log "auth user : ${U}"
    log "rewrite   : local senders -> ${U}   (on outbound only)"
    log "service   : ${svc}"
    log "root mail : ${ADMIN_USER}"
    echo

    case "$MTA" in
      exim4)   _cfg_exim4 ;;
      postfix) _cfg_postfix ;;
    esac

    if [ -n "$TEST_ADDR" ]; then
        echo; log "test message to $TEST_ADDR and $ADMIN_USER ..."
        printf 'To: %s\nSubject: tvmail relay test\n\nsent %s from %s\n' \
            "$TEST_ADDR" "$(date)" "$(hostname)" \
          | /usr/sbin/sendmail -oi "$TEST_ADDR" "$ADMIN_USER"
        case "$MTA" in
          exim4)   log "check:  mail  |  sudo tail /var/log/exim4/mainlog  |  exim4 -bp" ;;
          postfix) log "check:  mail  |  sudo tail /var/log/mail.log       |  mailq" ;;
        esac
    fi
    echo; log "done."
}

case "$(uname -s 2>/dev/null)" in
  Linux)               linux_setup; exit $? ;;
  CYGWIN*|MSYS*|MINGW*) : ;;   # fall through to the Cygwin path below
  *) die "unsupported OS '$(uname -s)'.  Automated: Debian-family Linux, Cygwin." ;;
esac

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

# extra local_delivery lines for multi-user mode (unprivileged exim delivering
# to a mailbox it doesn't own)
LD_SHARE=""
[ "$MULTI" = 1 ] && LD_SHARE=$'  check_owner       = false\n  check_group       = false\n'

log "smarthost   : ${SMARTHOST_HOST}:${SMARTHOST_PORT}  (${PROTO}, AUTH LOGIN/PLAIN)"
log "auth user   : ${SMARTHOST_USER}"
log "local host  : ${HOSTN}"
log "rewrite     : outbound relay only -> From: \"<user> via <host>\" <${REWRITE_TO}>  (local mail untouched)"
log "root's mail  : ${ADMIN_USER}"

# --------------------------------------------------------------------------
# 2. Directories
# --------------------------------------------------------------------------
install -d -m 1777 /var/mail
install -d -m 2775 /var/spool/exim
install -d -m 2775 /var/log/exim
install -d -m 0755 /etc/exim
install -d -m 0755 /etc/BAK

MB_MODE=0600
if [ "$MULTI" = 1 ]; then
  MB_MODE=0660
  # exim runs unprivileged as the invoking user; group-own + setgid the spool,
  # queue and logs so every "$MAIL_GRP" member can write and new files inherit
  # the group (else exim-as-A can't tidy files exim-as-B left behind).
  for d in /var/mail /var/spool/exim /var/log/exim; do
    chgrp "$MAIL_GRP" "$d" 2>/dev/null \
      || warn "could not chgrp $d to $MAIL_GRP (run elevated / check the name)"
  done
  chmod g+s /var/mail 2>/dev/null || true            # 1777 -> 3777 (sticky+setgid)
  find /var/spool/exim /var/log/exim -exec chgrp "$MAIL_GRP" {} + 2>/dev/null || true
  find /var/spool/exim /var/log/exim -type d -exec chmod g+ws {} + 2>/dev/null || true
  find /var/spool/exim /var/log/exim -type f -exec chmod g+w  {} + 2>/dev/null || true
fi

# Provision a mailbox per sending user.  A file left 0-byte and unwritable by a
# broken earlier run (Cygwin ACL mapped to e.g. mode 564, which exim rejects)
# is dropped so it gets recreated cleanly; one with mail in it is left alone.
for u in "${MAIL_USERS[@]}"; do
  mb="/var/mail/$u"
  if [ -f "$mb" ] && [ ! -s "$mb" ] && [ ! -w "$mb" ]; then
    rm -f "$mb" && log "removed stale unwritable empty $mb"
  fi
  [ -e "$mb" ] || { : > "$mb" && log "provisioned $mb"; }
  [ "$MULTI" = 1 ] && chgrp "$MAIL_GRP" "$mb" 2>/dev/null || true
  chmod "$MB_MODE" "$mb" 2>/dev/null || true
  [ -w "$mb" ] || warn "$mb not writable - fix by hand:  chmod $MB_MODE $mb"
done

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

# In multi-user mode every sender's (unprivileged) exim has to read the
# credential file, so open it to the shared group.  Single-user stays 0600.
if [ "$MULTI" = 1 ] && [ -f /etc/exim/passwd.client ]; then
  chgrp "$MAIL_GRP" /etc/exim/passwd.client 2>/dev/null || true
  chmod 0640 /etc/exim/passwd.client
  warn "the smarthost password in /etc/exim/passwd.client is now readable by"
  warn "  the '$MAIL_GRP' group (required for other users to relay externally)."
elif [ -f /etc/exim/passwd.client ]; then
  chmod 0600 /etc/exim/passwd.client
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
  # mailbox mode ${MB_MODE}. Multi-user: 0660 + owner/group checks off so any
  # ${MAIL_GRP}-group account's (unprivileged) exim can deliver.  The router
  # restricts this to real local users and appendfile won't follow a symlink.
  mode              = ${MB_MODE}
  mode_fail_narrower = false
${LD_SHARE}  # single-host mbox: fcntl lock only, no '<mbox>.lock' dotfiles in /var/mail
  use_lockfile      = false
  use_fcntl_lock    = true

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

# Exim only trusts its runtime config if the file is owned by root or its
# compiled CONFIGURE_OWNER (SYSTEM, uid 18 on Cygwin) and is not group/world
# writable.  Owned by the invoking user also passes - which is why a chris-only
# setup works - but that breaks the moment another account runs sendmail
# ("Exim configuration file ... has the wrong owner, group, or mode").  Take
# SYSTEM ownership if we can; the chown needs an elevated shell.
chgrp "$MAIL_GRP" "$CONF" 2>/dev/null || true
chown 18 "$CONF" 2>/dev/null || chown SYSTEM "$CONF" 2>/dev/null || true
chmod 0644 "$CONF"
_co="$(stat -c %u "$CONF" 2>/dev/null || echo '?')"
if [ "$_co" != 18 ] && [ "$_co" != 0 ]; then
  if [ "$MULTI" = 1 ]; then
    warn "could NOT give $CONF to SYSTEM - other users' sendmail will still PANIC."
    warn "  Run this once from an ELEVATED shell, then re-run me:"
    warn "      chown 18:544 $CONF && chmod 0644 $CONF"
  else
    log "note: $CONF is owned by $ADMIN_USER (fine for a single user).  For"
    log "  multi-user, chown it to SYSTEM from an elevated shell:  chown 18:544 $CONF"
  fi
fi
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
if [ "$MULTI" = 1 ]; then
  log "Multi-user: ${MAIL_USERS[*]}  (shared group: $MAIL_GRP)"
  log "  Each user reads their own /var/mail/<user>.  Delivery still runs as"
  log "  whoever invoked sendmail, so a new mailbox is created owned by them;"
  log "  it's group '$MAIL_GRP' + mode $MB_MODE via the setgid spool dir."
else
  log "Single user.  For a second sender:  $0 --users \"$ADMIN_USER other\" ..."
fi
