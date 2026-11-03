#!/bin/bash
# RUN ON: the laptop.  A REPORT, not a deployment: your own ad hoc tool (TO_BE_RUN/REUSABLE/ has a stub, the real script is in the repo,
#         tvchat/deploy); run it whenever you like.  Read-only.
# WHAT:   Joy's questions report: how many questions in the last DAYS days (default 7), how many she answered and missed, the NOT ANSWERED
#         questions (most asked first, with the closest FAQ entry), the WEAK answers (little of the question was explained: worth a K:
#         keyword), which FAQ entries are used most, and the chats she ended (abuse, lost).
# WHERE:  Joy runs on the web host now, so the log is /home/whatwelo/joy/questions.log THERE: this fetches it with ssh-wwl (no sudo) and runs
#         `tvchat-backend faq-report` on it here.  SRC=cpbee reads the old Joy's log on cpbee instead (ssh -t, it asks for sudo once).
# USE:    ./joy-report.sh          ./joy-report.sh 30        (days)       DAYS=1 ./joy-report.sh       SRC=cpbee ./joy-report.sh
# THEN:   paste the report to Claude: the misses become FAQ entries or keywords in /home/whatwelo/joy/faq.txt on the host (she re-reads it each
#         time: no restart), and a copy goes in the repo's configure/faq.example.txt.
set -euo pipefail
DAYS=${1:-${DAYS:-7}}
[[ $DAYS =~ ^[0-9]+$ ]] || { echo "days must be a number" >&2; exit 1; }
if [[ ${SRC:-host} == cpbee ]]; then
  exec ssh -t -o ServerAliveInterval=10 cpbee "sudo -u floyd -H tvchat-backend faq-report --days $DAYS"
fi
command -v ssh-wwl >/dev/null || { echo "ssh-wwl is not on the PATH (it is /home/chris/bin/ssh-wwl)" >&2; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
ssh-wwl "cat /home/whatwelo/joy/questions.log.1 /home/whatwelo/joy/questions.log 2>/dev/null" 2>&1 | grep -v '^spawn' | tr -d '\r' \
  | python3 "$here/../backend/tvchat-backend" faq-report /dev/stdin --days "$DAYS"
