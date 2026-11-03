#!/bin/bash
# RUN ON: the LAPTOP (Cygwin).  It logs in to the cPanel web host by itself with ssh-wwl (the MFA helper).  No sudo.
# WHY:    Put Joy's chat (the pro Live Helper Chat site, department 2) on https://universal-laughter.com/.
# WHAT:   Every page of the site ends with  <?php include 'footer.php'; ?>  so ONE edit covers them all: it adds the Live Helper
#         Chat widget code to the end of /home/whatwelo/www/ul/www/footer.php, between BEGIN/END markers (so a re-run replaces it
#         and MODE=off removes it).  footer.php is copied to /home/whatwelo/BAK/footer.php.<time> first, and checked with `php -l`.
#         Facebook Messenger: nothing to disable (you took it out): the script LOOKS for leftovers (customerchat, fb-customer-chat,
#         fb-root, connect.facebook.net in the site's .php files) and only warns.
# MODE:   beta (default)  the widget loads ONLY for a browser that has visited a page once with  ?chat=1  (it remembers that; ?chat=0
#                         forgets it).  So the public sees nothing yet.  Try:  https://universal-laughter.com/?chat=1
#         live            the widget loads for everybody.        off   removes the widget code again.
#         To go live later, run this script again with MODE=live (your own ad hoc tool: TO_BE_RUN/REUSABLE/ has a stub for it; the real script is in the repo, tvchat/deploy).
# OTHER:  BASE=chat.universal-laughter.com (the chat site's host)   DEPT=2 (department number, comma separated)   DRY_RUN=1 (a diff only)
# SAFE:   re-runnable.  Only footer.php changes, and only between the markers.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
name=$(basename "$0")
say() { printf '%s\n' "$*"; }
die() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }
command -v ssh-wwl >/dev/null || die "ssh-wwl is not on the PATH (it is /home/chris/bin/ssh-wwl)"
MODE=${MODE:-beta}
case $MODE in beta|live|off) ;; *) die "MODE must be beta, live or off";; esac

read -r -d '' REMOTE <<'REMOTE_EOF' || true
set -euo pipefail
SITE=/home/whatwelo/www/ul/www
F=$SITE/footer.php
BASE=${BASE:-chat.universal-laughter.com}
DEPT=${DEPT:-2}
MODE=${MODE:-beta}
stamp=$(date +%Y%m%d-%H%M%S)
[ -f "$F" ] || { echo "$F is not there" >&2; exit 1; }

echo "== Facebook Messenger leftovers (warning only)"
if grep -l -i -E 'customerchat|fb-customer-chat|fb-root|connect\.facebook\.net' "$SITE"/*.php 2>/dev/null; then
  echo "  ^ these files still mention Facebook's chat plugin: look at them"
else
  echo "  none found in $SITE/*.php"
fi
for p in index home about services events commendations; do
  grep -q "include 'footer.php'" "$SITE/$p.php" || echo "  WARNING: $p.php does not include footer.php: no chat on that page"
done

DEPTJS=$(printf '%s' "$DEPT" | sed 's/[^0-9,]//g; s/,/","/g')
tmp=$(mktemp)
# the file without any old block
awk '/<!-- BEGIN Live Helper Chat/{skip=1} !skip{print} /<!-- END Live Helper Chat/{skip=0}' "$F" > "$tmp"
# no trailing blank lines before our block
sed -i -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$tmp"

if [ "$MODE" != off ]; then
  if [ "$MODE" = beta ]; then GATE_OPEN='var on = false; try { if (/[?&]chat=1(&|$)/.test(location.search)) localStorage.setItem("ulchat", "1"); if (/[?&]chat=0(&|$)/.test(location.search)) localStorage.removeItem("ulchat"); on = localStorage.getItem("ulchat") === "1"; } catch (e) {}
  if (!on) return;'
  else GATE_OPEN=''; fi
  {
    printf '\n<!-- BEGIN Live Helper Chat (Joy) [%s]: added by ul-chat-widget-on-whatwelove.sh; MODE=off removes it -->\n' "$MODE"
    sed -e "s#__BASE__#$BASE#g" -e "s#__DEPT__#$DEPTJS#g" <<'SNIP_EOF'
<script type="text/javascript">
var LHC_API = LHC_API||{};
LHC_API.args = {mode:'widget',lhc_base_url:'//__BASE__/index.php/',wheight:450,wwidth:350,pheight:520,pwidth:500,leaveamessage:true,department:["__DEPT__"],check_messages:false};
(function() {
  __GATE__
  var po = document.createElement('script'); po.type = 'text/javascript'; po.setAttribute('crossorigin','anonymous'); po.async = true;
  var date = new Date();
  po.src = '//__BASE__/design/defaulttheme/js/widgetv2/index.js?'+(""+date.getFullYear() + date.getMonth() + date.getDate());
  var s = document.getElementsByTagName('script')[0]; s.parentNode.insertBefore(po, s);
})();
</script>
SNIP_EOF
    printf '<!-- END Live Helper Chat (Joy) -->\n'
  } > "$tmp.block"
  # the gate goes where __GATE__ is (nothing in live mode)
  awk -v gate="$GATE_OPEN" '{ if (index($0, "__GATE__")) { if (gate != "") print "  " gate } else print }' "$tmp.block" >> "$tmp"
  rm -f "$tmp.block"
fi

php -l "$tmp" >/dev/null || { echo "the new footer.php does not pass php -l; nothing was changed" >&2; php -l "$tmp" >&2 || true; exit 1; }

if cmp -s "$F" "$tmp"; then
  echo "footer.php already has exactly this (MODE=$MODE): nothing to change"
elif [ "${DRY_RUN:-0}" = 1 ]; then
  echo "DRY RUN: the change to $F:"; diff -u "$F" "$tmp" | head -60 || true
  echo "Nothing was changed."
else
  mkdir -p /home/whatwelo/BAK
  cp -p "$F" "/home/whatwelo/BAK/footer.php.$stamp"
  cat "$tmp" > "$F"
  echo "footer.php updated (MODE=$MODE); the old one is /home/whatwelo/BAK/footer.php.$stamp"
fi
rm -f "$tmp"

if [ "${DRY_RUN:-0}" != 1 ]; then
  html=$(curl -s --max-time 25 "https://universal-laughter.com/?chat=1" || true)
  if [ "$MODE" = off ]; then
    printf '%s' "$html" | grep -q 'Live Helper Chat' && { echo "the page still shows the chat code" >&2; exit 1; } || echo "the live page has no chat code"
  else
    printf '%s' "$html" | grep -q "$BASE" || { echo "the live page does not contain $BASE (is footer.php really the page's footer?)" >&2; exit 1; }
    echo "the live page carries the chat code"
  fi
fi
echo "RESULT: OK"
REMOTE_EOF

b64=$(printf '%s\n' "$REMOTE" | base64 -w0)
say "connecting to the web host with ssh-wwl (MODE=$MODE) ..."
out=$(timeout 240 ssh-wwl "echo $b64 | base64 -d | MODE='$MODE' BASE='${BASE:-chat.universal-laughter.com}' DEPT='${DEPT:-2}' DRY_RUN='${DRY_RUN:-0}' bash" 2>&1 | grep -v '^spawn' | tr -d '\r') || true
printf '%s\n' "$out"
printf '%s\n' "$out" | grep -q '^RESULT: OK$' || die "it did not finish on the web host; nothing was moved. Paste the output above to Claude."
[[ ${DRY_RUN:-0} == 1 ]] && exit 0
say
case $MODE in
  beta) say "Beta: open https://universal-laughter.com/?chat=1 once in a browser; from then on the chat button shows for that browser. (?chat=0 forgets it.) To go live: MODE=live $here/$name";;
  live) say "Live: the chat button now shows for everyone.";;
  off)  say "The chat code is removed.";;
esac
