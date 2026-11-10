#!/bin/bash
# RUN AS THE PERSON, in her own shell, on any machine that runs the tv* apps:   tv-passwords-setup
# WHY:    Floyd's Setup Wizard (!setup) writes the server names, tokens and certificates but never a password, so tvmail
#         (mail), tvcal and the address book (Radicale) cannot log in.  This asks for her cpbee password ONCE (hidden), tries
#         it on the real servers, and only if they accept it files it in ~/.mu-tickets (mode 600), where tvmail, tvchat and
#         tvcal look first.  Nothing else is written; the password is never printed, never on a command line, never in a
#         .conf.  (Meant as a stopgap until the LAN authentication work, POSIX/net-utils auth/identity, takes over.)
# WHAT:   1. asks for the password (twice); 2. tries it: IMAP over TLS on the mail host (her cpbee login is her mail login) and,  # allowlist secret: prose / format string, no secret
#            if there is one, Radicale (calendar and contacts); 3. adds tickets for the ones that accepted it
#            (imaps, smtp, https) - the old ~/.mu-tickets is kept as .bak-DATE; a ticket for the same host and login that is
#            there already is replaced, any other line is left alone.
# OPTIONS: --host HOST     the server (default: from her tvmail.conf [imap] host, else cpbee.cmnet)
#          --insecure      try the mail server even when its certificate is not one this machine knows (the password is
#                          then sent without being sure whose server it is: only on the home LAN, and only if you must)
#          --radicale-port N   (default 5232)
#          DRY_RUN=1       show the plan, ask nothing
# SAFE:   re-runnable (it replaces its own tickets).  Say yes to the Radicale line or not: it only files what logged in.
set -euo pipefail

HOST=""; RPORT=5232; INSECURE=0
while [[ $# -gt 0 ]]; do
  case $1 in
    --host) shift; HOST=${1:-} ;;
    --radicale-port) shift; RPORT=${1:-5232} ;;
    --insecure) INSECURE=1 ;;
    *) echo "usage: $0 [--host HOST] [--radicale-port N] [--insecure]" >&2; exit 2 ;;
  esac
  shift
done
me=$(id -un)
conf=$HOME/.config/tvmail/tvmail.conf
if [[ -z $HOST && -f $conf ]]; then
  HOST=$(sed -n '/^\[imap\]/,/^\[/{s/^[[:space:]]*host[[:space:]]*=[[:space:]]*\([^ #]*\).*/\1/p}' "$conf" | head -1)
fi
HOST=${HOST:-cpbee.cmnet}
# her own tvmail.conf says whether the mail server's certificate is checked ([imap] verify = false is what the LAN's
# self-signed one needs, and what tvmail then does with this very password anyway): follow it, so no --insecure is needed  # allowlist secret: prose, no secret
if [[ -f $conf && $INSECURE == 0 ]]; then
  v=$(sed -n '/^\[imap\]/,/^\[/{s/^[[:space:]]*verify[[:space:]]*=[[:space:]]*\([^ #]*\).*/\1/p}' "$conf" | head -1)
  case ${v,,} in false|no|0|off) INSECURE=1; echo "  (your tvmail.conf has [imap] verify = false: the mail server's certificate is not checked here either)" ;; esac
fi
if [[ ${DRY_RUN:-0} == 1 ]]; then
  echo "DRY RUN as $me: would ask for your cpbee password (hidden), try IMAPS and Radicale on $HOST, and file tickets for the"  # allowlist secret: prose / format string, no secret
  echo "ones that accept it in ~/.mu-tickets (mode 600, the old file kept). Nothing was changed."
  exit 0
fi
[[ -t 0 ]] || { echo "run this in a terminal: it asks for your password" >&2; exit 1; }
read -r -s -p "Your cpbee password ($me@$HOST): " pw < /dev/tty; echo >&2  # allowlist secret: prose / format string, no secret
read -r -s -p "Again: " pw2 < /dev/tty; echo >&2
[[ -n $pw && $pw == "$pw2" ]] || { echo "FAILED: empty, or the two did not match" >&2; exit 1; }
unset pw2

read -r -d '' code <<'PYEOF' || true
import imaplib, os, re, shutil, ssl, sys, time, urllib.parse, urllib.request, urllib.error, base64, glob

me, host, rport = os.environ["ME"], os.environ["HOST"], int(os.environ["RPORT"])
pw = sys.stdin.read()
home = os.path.expanduser("~")
cas = [p for p in glob.glob(os.path.join(home, ".config", "tv*", "*.pem"))]


def ctx_verified(extra=None):
    c = ssl.create_default_context()
    for p in ([extra] if extra else []) + cas:
        try:
            c.load_verify_locations(p)
        except (OSError, ssl.SSLError):
            pass
    return c


notes = []
accepted = []

# --- IMAP over TLS: her mail login
try:
    try:
        m = imaplib.IMAP4_SSL(host, 993, ssl_context=ctx_verified(), timeout=15)
    except ssl.SSLCertVerificationError:
        if os.environ.get("INSECURE") != "1":
            raise ssl.SSLError("its certificate is not one this machine knows, so the password was NOT sent; "
                               "run again with --insecure to send it anyway")
        c = ssl.create_default_context()
        c.check_hostname = False
        c.verify_mode = ssl.CERT_NONE
        m = imaplib.IMAP4_SSL(host, 993, ssl_context=c, timeout=15)
        notes.append("  (the mail server's certificate is not one this machine knows: the password was sent without checking it)")
    try:
        m.login(me, pw)
        m.logout()
        accepted += ["imaps", "smtp"]
        print("  mail (IMAP on %s): accepted" % host)
    except imaplib.IMAP4.error:
        print("  mail (IMAP on %s): the password was REFUSED" % host)
except (OSError, ssl.SSLError) as e:
    print("  mail (IMAP on %s): could not be reached (%s)" % (host, e))

# --- Radicale: calendar and contacts, if she has a login.  Its host name and certificate are the ones her own tvcal.conf
# names (the server's certificate is made for the short name, cpbee, and its own file is the CA).
import configparser
rhost, rca = "cpbee", None
cp = configparser.ConfigParser(interpolation=None)
try:
    cp.read(os.path.join(home, ".config", "tvcal", "tvcal.conf"), encoding="utf-8")
    u = urllib.parse.urlparse(cp.get("server", "url", fallback=""))
    if u.hostname:
        rhost = u.hostname
        rport = u.port or rport
    rca = os.path.expanduser(cp.get("server", "ca", fallback="").strip()) or None
except (configparser.Error, OSError):
    pass
rad_ok = False
try:
    tok = base64.b64encode(("%s:%s" % (me, pw)).encode("utf-8")).decode("ascii")
    req = urllib.request.Request("https://%s:%d/%s/" % (rhost, rport, urllib.parse.quote(me)),
                                 method="PROPFIND", headers={"Authorization": "Basic " + tok, "Depth": "0"})
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx_verified(rca)) as r:
            rad_ok = r.status in (200, 207)
    except urllib.error.HTTPError as e:
        rad_ok = False
        print("  calendar / contacts (Radicale on %s:%d): %s" % (
            rhost, rport, "no login for %s there, or another password" % me if e.code in (401, 403) else "answered %d" % e.code))
    else:
        if rad_ok:
            accepted.append("https")
            print("  calendar / contacts (Radicale on %s:%d): accepted" % (rhost, rport))
except (OSError, ssl.SSLError) as e:
    print("  calendar / contacts (Radicale on %s:%d): could not be reached (%s)" % (rhost, rport, e))

for n in notes:
    print(n)
if not accepted:
    print("Nothing was filed: no server accepted the password.  Run this again and check it.")
    sys.exit(1)

# --- file the tickets (mode 600; the old file kept)
path = os.path.join(home, ".mu-tickets")
lines = []
if os.path.exists(path):
    shutil.copy2(path, "%s.bak-%s" % (path, time.strftime("%Y%m%d-%H%M%S")))
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
enc = lambda s: urllib.parse.quote(s, safe="")
hosts = [host]
short = host.partition(".")[0]
if short != host:
    hosts.append(short)        # the same machine under the short name
mine = lambda ln, scheme, h: re.match(r"^%s://%s:[^@]*@%s(:\d+)?/?$" % (re.escape(scheme), re.escape(enc(me)), re.escape(h)), ln.strip())  # allowlist secret: prose / format string, no secret
for scheme in accepted:
    for h in ([rhost] + ([rhost.partition(".")[0]] if "." in rhost else [rhost + ".cmnet"]) if scheme == "https" else hosts):
        port = ":%d" % rport if scheme == "https" else ""
        lines = [ln for ln in lines if not mine(ln, scheme, h)]
        lines.append("%s://%s:%s@%s%s" % (scheme, enc(me), enc(pw), h, port))  # allowlist secret: prose / format string, no secret
old = os.umask(0o077)
with open(path, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")
os.umask(old)
os.chmod(path, 0o600)
print("Filed %d ticket line(s) for %s in ~/.mu-tickets (mode 600): %s." % (
    len(accepted) * len(hosts), me, ", ".join(accepted)))
PYEOF
printf '%s' "$pw" | ME=$me HOST=$HOST RPORT=$RPORT INSECURE=$INSECURE python3 -I -c "$code"
echo "Done.  Restart tvmail, tvchat and tvcal."
