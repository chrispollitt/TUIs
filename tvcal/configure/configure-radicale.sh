#!/usr/bin/env bash
#
# configure-radicale.sh
#
# The calendar server for tvcal, in the same two halves as the mail and chat setups:
#
#   server   (root, on the mail / chat host - cpbee)
#            installs Radicale (a small CalDAV server), makes it listen on TLS with the
#            host's certificate (or a self-signed one it makes), adds USER to its password
#            file, makes USER's calendar collection, starts it at boot and opens the port
#            for the LAN when net-utils' lan-port is there.
#
#   share    (root, on the same host) lets a second user work on the owner's calendar - the central
#            Floyd's !todo / !cal / !event: that user gets its own random password (from $CAL_SHARE_PW, or made
#            here and printed once) and read-write on /OWNER/calendar/ only, nothing else of the owner's.
#            Checked afterwards (the sharer gets in, cannot see the owner's other collections); if the check
#            fails the old configuration is put back.  Needs the server half to have run once.
#
#   client   (as yourself, on each machine that runs tvcal - no root)
#            writes [server] into ~/.config/tvcal/tvcal.conf ($TVCAL_CONF), puts the
#            password in ~/.mu-tickets (mode 600), fetches the CA if the server's certificate is
#            self-signed, and checks that the login works with tvcal-backend.
#
# Re-runnable.  Passwords never go into tvcal.conf or onto a command line ($CAL_SETUP_PASS or
# asked for).  Radicale keeps the calendars as plain files, one .ics per item, in
# /var/lib/radicale/collections: back that directory up with the mail.
#
# Usage:
#   sudo ./configure-radicale.sh server --user chris [options]
#   sudo ./configure-radicale.sh share --user chris --with floyd
#   ./configure-radicale.sh client --user chris [options]
#
# Server options:
#   --user NAME      the calendar's owner (required); more users: run again
#   --port N         TLS port (default: 5232)
#   --bind ADDR      address to listen on (default: 0.0.0.0; 127.0.0.1 = this host only, which is
#                    all the central Floyd on the same host needs)
#   --cert FILE --key FILE   the certificate to serve (default: made here, self-signed, in
#                    /etc/radicale/tls, valid 10 years, for the host's name)
#   --name HOST      the name clients use (default: the host's short name)
# Share options:
#   --user OWNER     whose calendar          --with USER   who may work on it (a login of its own)
# Client options:
#   --host HOST      the server (default: cpbee)       --port N   (default: 5232)
#   --ca FILE        a local copy of the CA / certificate (default: scp HOST:/etc/radicale/tls/cert.pem)
#   --no-check       skip the login check (offline)
#   -h | --help      show this header
#
set -euo pipefail

mode="${1:-}"; [ $# -gt 0 ] && shift
USER_NAME=""; WITH=""; PORT=5232; BIND=0.0.0.0; CERT=""; KEY=""; NAME=""; HOST=cpbee; CA=""; CHECK=1

log()  { printf '  %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --user)     USER_NAME="${2:?}"; shift 2 ;;
    --with)     WITH="${2:?}"; shift 2 ;;
    --port)     PORT="${2:?}"; shift 2 ;;
    --bind)     BIND="${2:?}"; shift 2 ;;
    --cert)     CERT="${2:?}"; shift 2 ;;
    --key)      KEY="${2:?}"; shift 2 ;;
    --name)     NAME="${2:?}"; shift 2 ;;
    --host)     HOST="${2:?}"; shift 2 ;;
    --ca)       CA="${2:?}"; shift 2 ;;
    --no-check) CHECK=0; shift ;;
    -h|--help)  usage; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
done
case "$mode" in -h|--help|"") usage; exit 0 ;; esac
[ -n "$USER_NAME" ] || { usage >&2; die "--user is required"; }
case "$USER_NAME" in *[!A-Za-z0-9._-]*|"") die "--user: letters, digits . _ - only" ;; esac

secret() {   # the password: $CAL_SETUP_PASS, else asked for (twice)
  if [ -n "${CAL_SETUP_PASS:-}" ]; then printf '%s' "$CAL_SETUP_PASS"; return; fi
  local a b
  read -r -s -p "  Password for $USER_NAME on the calendar server: " a < /dev/tty; echo >&2
  read -r -s -p "  Again: " b < /dev/tty; echo >&2
  [ "$a" = "$b" ] || die "the passwords differ"
  [ -n "$a" ] || die "an empty password"
  printf '%s' "$a"
}

# ------------------------------------------------------------------ server ---
# /etc/radicale/rights: everyone has their own calendars (what owner_only did) and, for each line
# "OWNER WITH" of /etc/radicale/shares, WITH may read and write OWNER/calendar and nothing else of OWNER's.
write_rights() {
  if [ -f /etc/radicale/rights ] && ! grep -q '^# written by tvcal configure-radicale' /etc/radicale/rights; then
    cp -p /etc/radicale/rights "/etc/radicale/rights.bak.$(date +%Y%m%d%H%M%S)"
  fi
  {
    cat <<'RIGHTS'
# written by tvcal configure-radicale.sh (the "share" mode adds to /etc/radicale/shares; re-run to rebuild)
[root]
user: .+
collection:
permissions: R

[principal]
user: .+
collection: {user}
permissions: RW

[calendars]
user: .+
collection: {user}/[^/]+
permissions: rw
RIGHTS
    n=0
    if [ -f /etc/radicale/shares ]; then
      while read -r owner with _; do
        case "$owner" in ""|\#*) continue ;; esac
        n=$((n + 1))
        printf '\n[share-%s]\nuser: %s\ncollection: %s/calendar\npermissions: rw\n' "$n" "$with" "$owner"
      done < /etc/radicale/shares
    fi
  } > /etc/radicale/rights
  chmod 0644 /etc/radicale/rights
}

server() {
  [ "$(id -u)" = 0 ] || die "the server half needs root (sudo)"
  NAME="${NAME:-$(hostname -s)}"
  if ! command -v radicale >/dev/null 2>&1; then
    command -v apt-get >/dev/null 2>&1 || die "install Radicale and apache2-utils (htpasswd) first, then re-run"
    log "installing radicale and apache2-utils"
    DEBIAN_FRONTEND=noninteractive apt-get install -y radicale apache2-utils python3-bcrypt python3-passlib >/dev/null
  fi
  command -v htpasswd >/dev/null 2>&1 || die "htpasswd (apache2-utils) is needed to store the password"

  # 0755: the service user must read the config (0750 root:root made Radicale start with its defaults:
  # plain HTTP on localhost only); nothing secret is in there - the key is 0640 and the passwords 0640
  install -d -m 0755 /etc/radicale /etc/radicale/tls
  if [ -z "$CERT" ]; then
    CERT=/etc/radicale/tls/cert.pem; KEY=/etc/radicale/tls/key.pem
    if [ ! -s "$CERT" ]; then
      log "making a self-signed certificate for $NAME (10 years)"
      ( umask 077
        openssl req -x509 -newkey rsa:3072 -nodes -days 3650 -subj "/CN=$NAME" \
          -addext "subjectAltName=DNS:$NAME,DNS:localhost,IP:127.0.0.1" \
          -keyout "$KEY" -out "$CERT" >/dev/null 2>&1 ) || die "openssl failed"
    fi
  else
    [ -r "$CERT" ] && [ -r "$KEY" ] || die "--cert and --key must name readable files"
  fi
  # the service user must read the key (a umask that leaks, or one that is too tight, has
  # stopped a service before: check the modes after sudo scripts)
  rad_user=$(id -nu radicale 2>/dev/null || echo root)
  chown "$rad_user" "$KEY" 2>/dev/null || true
  chmod 0640 "$KEY" 2>/dev/null || true; chmod 0644 "$CERT" 2>/dev/null || true   # the certificate is public: clients fetch it

  if [ -f /etc/radicale/config ] && ! grep -q '^# written by tvcal configure-radicale' /etc/radicale/config; then
    cp -p /etc/radicale/config "/etc/radicale/config.bak.$(date +%Y%m%d%H%M%S)"
  fi
  cat > /etc/radicale/config <<EOF
# written by tvcal configure-radicale.sh (re-run it to change)
[server]
hosts = $BIND:$PORT
ssl = True
certificate = $CERT
key = $KEY

[auth]
type = htpasswd
htpasswd_filename = /etc/radicale/users
htpasswd_encryption = bcrypt

[storage]
filesystem_folder = /var/lib/radicale/collections

[rights]
type = from_file
file = /etc/radicale/rights
EOF
  chmod 0644 /etc/radicale/config

  write_rights
  pw=$(secret)
  touch /etc/radicale/users; chmod 0640 /etc/radicale/users; chown "root:$rad_user" /etc/radicale/users 2>/dev/null || true
  printf '%s\n' "$pw" | htpasswd -iB /etc/radicale/users "$USER_NAME" >/dev/null
  log "password stored (bcrypt) for $USER_NAME"

  install -d -o "$rad_user" -m 0750 /var/lib/radicale/collections 2>/dev/null || true
  systemctl enable radicale >/dev/null 2>&1 || true
  systemctl restart radicale || die "radicale did not start: journalctl -u radicale"
  url="https://localhost:$PORT/$USER_NAME/calendar/"
  code=000
  for _ in $(seq 1 20); do                      # up when it answers TLS at all
    code=$(curl -sk -u "$USER_NAME:$pw" -o /dev/null -w '%{http_code}' -X MKCALENDAR "$url" || true)
    [ "$code" != 000 ] && break
    sleep 1
  done
  case "$code" in
    201) log "made the calendar $url" ;;
    405|409) log "the calendar $url is already there" ;;
    000) journalctl -u radicale --no-pager -n 12 >&2 || true
         die "Radicale does not answer TLS on port $PORT (is /etc/radicale/config being read? ss -ltn | grep $PORT)" ;;
    401) die "Radicale refused $USER_NAME's password (the htpasswd file: /etc/radicale/users, bcrypt needs python3-bcrypt)" ;;
    *) warn "could not make the calendar (HTTP $code); try: curl -sk -u $USER_NAME -X MKCALENDAR $url" ;;
  esac
  if [ "$BIND" = 127.0.0.1 ] || [ "$BIND" = localhost ]; then
    log "listening on this host only: no LAN port opened"
  else
    lp=$(command -v lan-port || true)
    [ -n "$lp" ] || lp=$(ls /home/*/gh_cp/POSIX/net-utils/lan-port 2>/dev/null | head -1 || true)
    if [ -n "$lp" ]; then "$lp" add "$PORT" "Radicale calendar https (tvcal)" || warn "lan-port could not open $PORT"
    else log "open TCP $PORT for the LAN yourself (cpbee: add it to /etc/router-setup/lan0-tcp-ports, then setup-router.sh fw)"; fi
  fi
  echo
  echo "Server ready.  On each machine:  ./configure-radicale.sh client --user $USER_NAME --host $NAME"
  echo "The central Floyd on this host: run the client half as his service user with --host localhost."
}

# ------------------------------------------------------------------- share ---
share() {
  [ "$(id -u)" = 0 ] || die "the share mode needs root (sudo)"
  [ -n "$WITH" ] || die "--with USER is required (who may work on $USER_NAME's calendar)"
  case "$WITH" in *[!A-Za-z0-9._-]*) die "--with: letters, digits . _ - only" ;; esac
  [ "$WITH" != "$USER_NAME" ] || die "--with is the owner already"
  [ -f /etc/radicale/config ] && command -v htpasswd >/dev/null 2>&1 || die "run the server half first"
  rad_user=$(id -nu radicale 2>/dev/null || echo root)
  stamp=$(date +%Y%m%d%H%M%S)
  for f in config rights users shares; do [ -f "/etc/radicale/$f" ] && cp -p "/etc/radicale/$f" "/etc/radicale/$f.share-bak.$stamp"; done
  restore() {
    for f in config rights users shares; do
      if [ -f "/etc/radicale/$f.share-bak.$stamp" ]; then cp -p "/etc/radicale/$f.share-bak.$stamp" "/etc/radicale/$f"; else rm -f "/etc/radicale/$f"; fi
    done
    systemctl restart radicale || true
  }
  if grep -q '^type = owner_only' /etc/radicale/config; then      # a server half from before this mode
    sed -i 's|^type = owner_only|type = from_file\nfile = /etc/radicale/rights|' /etc/radicale/config
  fi
  grep -qxF "$USER_NAME $WITH" /etc/radicale/shares 2>/dev/null || echo "$USER_NAME $WITH" >> /etc/radicale/shares
  chmod 0644 /etc/radicale/shares
  write_rights
  pw="${CAL_SHARE_PW:-}"
  if [ -z "$pw" ]; then pw=$(openssl rand -base64 24 | tr -d '/+=\n' | cut -c1-24); echo "  made a password for $WITH: $pw   (shown once)"; fi
  printf '%s\n' "$pw" | htpasswd -iB /etc/radicale/users "$WITH" >/dev/null
  chown "root:$rad_user" /etc/radicale/users 2>/dev/null || true; chmod 0640 /etc/radicale/users
  systemctl restart radicale || { restore; die "radicale did not restart; the old configuration is back"; }
  base="https://localhost:$PORT"
  code=000
  for _ in $(seq 1 20); do
    code=$(curl -sk -u "$WITH:$pw" -o /dev/null -w '%{http_code}' "$base/$USER_NAME/calendar/" || true)
    [ "$code" != 000 ] && break
    sleep 1
  done
  other=$(curl -sk -u "$WITH:$pw" -o /dev/null -w '%{http_code}' "$base/$USER_NAME/" || true)
  if [ "$code" != 200 ]; then
    restore; die "$WITH could not read $USER_NAME/calendar/ (HTTP $code); the old configuration is back"
  fi
  case "$other" in 403|404) ;; *) restore; die "$WITH can see more of $USER_NAME than the calendar (HTTP $other on /$USER_NAME/); the old configuration is back" ;; esac
  log "$WITH can read and write /$USER_NAME/calendar/ (HTTP $code) and nothing else of $USER_NAME's (HTTP $other)"
  rm -f /etc/radicale/*.share-bak."$stamp"
}

# ------------------------------------------------------------------ client ---
client() {
  conf="${TVCAL_CONF:-$HOME/.config/tvcal/tvcal.conf}"
  mkdir -p "$(dirname "$conf")"
  cafile=""
  if [ -n "$CA" ] || [ "$HOST" != localhost ]; then
    cafile="$HOME/.config/tvcal/$HOST-ca.pem"
    if [ -n "$CA" ]; then cp "$CA" "$cafile"
    else scp -q "$HOST:/etc/radicale/tls/cert.pem" "$cafile" 2>/dev/null \
           || { warn "could not fetch the certificate from $HOST (a certificate from a real CA needs none: --ca /etc/ssl/certs/ca-certificates.crt)"; cafile=""; }
    fi
  fi
  url="https://$HOST:$PORT/$USER_NAME/calendar/"
  [ -f "$conf" ] && cp -p "$conf" "$conf.bak"
  # replace [server], keep every other section and comment
  tmp=$(mktemp)
  awk 'BEGIN{skip=0} /^\[server\]/{skip=1; next} /^\[/{skip=0} !skip{print}' "${conf}" 2>/dev/null > "$tmp" || true
  {
    echo "[server]"; echo "url = $url"; echo "user = $USER_NAME"
    [ -n "$cafile" ] && echo "ca = $cafile"
    echo
    cat "$tmp"
  } > "$conf"
  rm -f "$tmp"; chmod 0600 "$conf"
  log "wrote [server] in $conf"

  # the password: ~/.mu-tickets, the file tvmail, tvchat and tvcal share (it names the protocol, which  # allowlist secret
  # ~/.netrc cannot; the tool netrc-to-tickets in POSIX/net-utils moves old logins over)
  tickets="${TVMAIL_MU_TICKETS:-$HOME/.mu-tickets}"
  if grep -qsE "^https://$USER_NAME:[^@]*@$HOST:$PORT\$" "$tickets"; then  # allowlist secret
    log "$tickets already has the calendar login for $USER_NAME at $HOST:$PORT"
  else
    pw=$(secret)
    ( umask 077; CAL_PW="$pw" CAL_USER="$USER_NAME" CAL_HOST="$HOST" CAL_PORT="$PORT" python3 -c '
import os, urllib.parse as u
print("https://%s:%s@%s:%s" % (u.quote(os.environ["CAL_USER"], safe=""), u.quote(os.environ["CAL_PW"], safe=""), os.environ["CAL_HOST"], os.environ["CAL_PORT"]))' >> "$tickets" ) || die "could not write $tickets"  # allowlist secret
    chmod 0600 "$tickets"; log "added the calendar login to $tickets"
  fi
  if [ "$CHECK" = 1 ]; then
    command -v tvcal-backend >/dev/null 2>&1 || { warn "tvcal-backend is not installed here: skipping the check"; return; }
    if TVCAL_CONF="$conf" tvcal-backend todos >/dev/null; then log "the login works: tvcal can read the calendar on $HOST"
    else die "tvcal-backend could not read $url (see the message above)"; fi
  fi
}

case "$mode" in
  server) server ;;
  share)  [ -n "$USER_NAME" ] || die "--user OWNER is required"; share ;;
  client) client ;;
  -h|--help|"") usage ;;
  *) die "say 'server', 'share' or 'client' (try --help)" ;;
esac
