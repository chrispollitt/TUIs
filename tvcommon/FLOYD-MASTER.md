# Master Floyd link (design 2026-11-04; approved, built for tvmail and tvcal)

**Status:** gate (`FloydGate`, tvfloyd.py + `floyd-daemon` keys `gate`, `gate_listen`, `gate_token_file`) and client
(`master_*`, used by `persona()`, `claude_start/poll`, `!status`) are written and tested (tvcommon, tvcal, tvmail tests);
`tvchat/deploy/floyd-gate-on-cpbee.sh` deploys it (token, conf, restart, LAN check) - NOT yet run.  Still open: tvchat's
own Floyd window using the master as a fallback chain, and a "Floyd gate" card on the servers page.  Decisions taken:
token over plain LAN TCP, port 7077.

Rule: every suite app (tvmail, tvchat, tvcal) has
1. a **local Floyd** (tvfloyd.py `persona()`, `claude_*()`), always present, and
2. a **link to the master Floyd** (tvchat-floyd on cpbee) used when cpbee answers.

Solo installs must keep working: no `[floyd] server` set, or cpbee out of reach, means the local Floyd answers, silently.

## What "master" adds
One memory (quotes, facts, `!learn`), the IRC-side knowledge, and Claude run on the server (so other machines need no Claude CLI).

## Transport (proposal): "floyd-gate", a line protocol inside tvchat-floyd
- tvchat-floyd (the IRC bot daemon) starts one extra thread listening on the lan0 address only (default port 7077, TCP).
  It lives in the SAME process as the bot so there is one FloydMind and one memory file (no two writers).
- Requests are one JSON line, replies one JSON line:
  - `{"v":1,"token":T,"who":"chris","app":"tvcal","level":"chatty","text":"!quote"}` -> `{"ok":true,"lines":["..."]}`
  - Claude: `{"op":"claude","text":"..."}` -> `{"ok":true,"job":"123"}`; then `{"op":"poll","job":"123"}` -> `{"ok":true,"done":false}` or `{"ok":true,"done":true,"lines":[...]}`. Same async shape as today's local `claude_start/claude_poll`, so the windows' polling timers need no change.
  - `{"op":"hello"}` -> version, level, memory counts (used by `!status` and as the reachability probe).
- Auth: a shared token in `[floyd] token` (client) and in the bot's config (server). LAN only, no TLS (same trust level as the existing LAN services; revisit if it ever leaves the LAN). Wrong token: `{"ok":false}`, logged.
- IRC-only commands (`!seen !tell karma !leave`) stay refused through the gate, as in the local Floyd.

## Client side (all in tvcommon/py/tvfloyd.py, so tvcal and tvmail get it for free)
- Settings in `[floyd]`: `server = cpbee:7077` (empty = local only), `token = ...`, `server_timeout = 2`.
- `persona()` / `claude_start()` / `claude_poll()` / `status_lines()` try the master first; any connect error or timeout within `server_timeout` seconds -> use the local code path. A failed probe is remembered for ~60 s so a down cpbee costs one short delay, not one per line.
- `!status` says which Floyd answered ("master on cpbee" / "local, cpbee not reachable").
- Replies get no marker in the chat window, except the first line after a switch ("Floyd is back on cpbee" / "Floyd is on his own now").
- tvchat: its own account Floyd already IS on IRC next to the master; the gate gives its F-window/local helper the same fallback logic. Keep its per-account memory as the local one.

## Tests
- Client with no server set, with a dead port, with a slow server (timeout), with a wrong token: all fall back to local.
- Gate unit tests against a FloydMind with a temp memory; e2e: tvcal F7 with a fake gate on 127.0.0.1.
- tvcal/tvmail tests keep `TVFLOYD_SERVER=none`.

## Deployment
Extend `tvchat/deploy/floyd-voice-on-cpbee.sh` (or a sibling script): install new tvfloyd.py + tvchat-backend, write the token to the bot's config, open the port (Apache/nginx are not involved), restart tvchat-floyd; add a "Floyd gate" probe to cmpi-router's servers page.

## Alternatives considered
- ssh to cpbee running a helper: reuses ssh keys, but every machine needs a key; fails the "solo or elsewhere" test.
- IRC/Delta Chat DM through tvchat's backend: needs tvchat running and logged in; slow and chatty for a one-line question.
- A separate daemon reading the same memory file: two writers; rejected for the in-process gate.

## Open decisions for Chris
1. Token over plain LAN TCP (proposed) vs ssh.
2. Port number (7077 proposed).
