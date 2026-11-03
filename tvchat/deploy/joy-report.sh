#!/bin/bash
# RUN ON: the laptop.  A REPORT, not a deployment: your own ad hoc tool: TO_BE_RUN/REUSABLE/ has a stub, the real script is in the repo (tvchat/deploy); run it whenever you like.  Read-only.
# WHAT:   asks cpbee (ssh -t, sudo password once) for Joy's questions report: how many questions in the last DAYS days (default 7),
#         how many she answered and missed, the NOT ANSWERED questions (most asked first, with the closest FAQ entry), the WEAK
#         answers (little of the question was explained: worth a K: keyword), and which FAQ entries are used most.
# USE:    ./joy-report.sh          ./joy-report.sh 30        (days)       DAYS=1 ./joy-report.sh
# THEN:   paste the report to Claude: the misses become FAQ entries or keywords in /etc/tvchat/faq.txt on cpbee (she re-reads it when it
#         changes: no restart), and a copy goes in the repo's configure/faq.example.txt.
# NEEDS:  joy-questions-log-on-cpbee.sh run first (the new backend and the log).
set -euo pipefail
DAYS=${1:-${DAYS:-7}}
[[ $DAYS =~ ^[0-9]+$ ]] || { echo "days must be a number" >&2; exit 1; }
exec ssh -t -o ServerAliveInterval=10 cpbee "sudo -u floyd -H tvchat-backend faq-report --days $DAYS"
