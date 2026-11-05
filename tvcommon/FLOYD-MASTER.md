# Master Floyd link (design 2026-11-04; approved, built for tvmail and tvcal)

**Status:** built and deployed for tvmail, tvcal and tvchat.  Gate: `FloydGate` in tvfloyd.py, started by
`tvchat-backend floyd-daemon` (`gate`, `gate_listen`, `gate_token_file` on the floyd account).  Client: `master_*` in
tvfloyd.py used by `persona()`, `claude_start/poll` and `!status` (tvmail, tvcal), and by tvchat's local Floyd
(`_master_say`, `_master_claude`; keys `server`, `token`, `server_timeout` on its `protocol = floyd` account; its
`!claude` for YOUR OWN lines goes to the master, other people's stay admin-only).  Deploy: `tvchat/deploy/floyd-gate-on-cpbee.sh`
(opens the port with POSIX/net-utils/lan-port).  Servers page card: `cpbee-servers-add-floyd-gate.sh`.
Decisions taken: token over plain LAN TCP, port 7077.

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

---

# Part 2: the master as the suite's event hub (design 2026-11-04; phase 1 built, deploy: tvchat/deploy/floyd-hub-on-cpbee.sh)

**Phase 2 as built:** `Sentinel` and the checks in tvfloyd.py, wired into `floyd-daemon` (`sentinel = yes` and the
`sentinel_*` keys, tvchat/configure/floyd.conf.example); deploy: tvchat/deploy/floyd-sentinel-on-cpbee.sh.  Defaults watch
ergo, talkd, dovecot, postfix, radicale (unit and port), u-l.ca:465 (cpbee's outgoing mail relay) and :993, the Postfix
queue and the disk, and announce a scheduled or begun shutdown (also from the daemon's SIGTERM when the system is
stopping).  Answers to the open questions were not needed: u-l.ca is cpbee's smarthost (`relayhost = [u-l.ca]:465`), so
those two ports are what matter; change them with `sentinel_hosts`.

**Phase 1 as built:** `EventHub` and the gate ops `publish` / `events` (tvfloyd.py), the relay (`python tvfloyd.py --relay`,
started by the backends through `ensure_relay`), `!send MACHINE|all text`, the lost-contact toast.  One deviation from the text
below: the relay *short-polls every 3 s* (a toast arrives within about 3 s) instead of long-polling, because a parked
connection would need loop timers in the single-threaded gate; long-polling can replace it later without a protocol change
(`events` just gains a `wait`).  The relay ends after 10 minutes with no tv* app running (`/proc` scan).  Setting:
`[floyd] machine = NAME` (default the host name).  Tests: tvcommon/tests/test_tvfloyd.py (TestEventHub, TestRelay).

Chris asked for: server issues relayed to the clients as toasts (cpbee about to go down, u-l.ca unreachable,
a cpbee service down), toasts relayed between LAN machines (tvmail on cmdesktop -> tvcal on cmlaptop), and
whatever else is worth centralizing.  Same rules as part 1: all of it optional, a solo app or an unreachable
master changes nothing, and no feature needs the master to be up for the local parts to work.

## The shape: an event hub on the gate, one small relay per machine, the existing spool as the last hop
The apps already show toasts from `~/.local/state/tvcommon/spool/<app>/` (tvcommon/README.md, "Toasts for the
other app").  That spool stays the only thing the C++ apps read, so **no C++ change is needed to receive**.

- **Hub (cpbee, inside tvchat-floyd, same single-thread loop as the gate).** New ops: `publish` (an event in)
  and `events` (a client asks for everything after its sequence number; long-poll up to 25 s so a toast
  arrives in about a second without a busy loop).  A ring of the last 200 events is kept in memory; events
  with a `key` ("svc:radicale") are *sticky*: a machine that connects later is told what is down right now.
- **Event:** `{seq, ts, kind, level, text, origin, to, key, ttl}`.  kind: outage | recover | shutdown | mail |
  reminder | note | digest.  level: info | warn | error (the toast colours).  to: "all" or a machine name.
  Text is plain, control characters stripped, 200 characters, as the spool already does.
- **Relay (each machine, `tvfloyd-relay`, a ~100-line stdlib Python program, started on demand).**  It long-polls
  the hub with that machine's id and token, keeps its last sequence number in `~/.local/state/tvfloyd/relay`, and
  writes each event as a spool note for tvmail, tvchat and tvcal, with `from` = "cpbee".  Started by the
  backends (tvmail, tvcal, tvchat) when `[floyd] server` is set; one copy per user thanks to a lock file; it
  exits when no app has run for an hour.  It also *detects losing cpbee*: a relay that had contact and now has
  none for 60 s writes a local "lost contact with cpbee" toast (a power cut or a dead network, as opposed to an
  announced shutdown).  Without `server` there is no relay.
- **Sending from a machine.**  The relay also publishes: the apps drop what they want shared into
  `~/.local/state/tvcommon/outbox/` and the relay posts it.  This is the one small C++ change (spool.cpp's
  `spoolSend` writes an outbox note too when `[ui] share_remote = yes`; default off) and is only needed for the
  cross-machine part.  A note that arrived via the hub is never sent back (an `origin` check plus the spool's
  existing "a note that came in is never sent on"), and the spool's 30 s same-text rule removes duplicates.

## Server issues (the sentinel, a timer on the hub's loop; checks run on threads, never blocking the loop)
- **Services:** every 30 s `systemctl is-active` for ergo, talkd, dovecot, postfix, radicale, and the gate's own
  unit; the list is `[sentinel] services =` in floyd.conf.  A service must fail twice in a row to be "down"
  (one `outage` event, sticky) and one `recover` event when it returns; still down after 30 min -> a reminder.
  A TCP check on the service's port runs beside it, so "active but not answering" is caught too.
- **cpbee about to go down:** `/run/systemd/shutdown/scheduled` (a `shutdown +10` has a time in it) and a queued
  shutdown/reboot job in `systemctl list-jobs`, polled every 5 s: `shutdown` event "cpbee goes down in 10
  minutes" (warn), then the final "cpbee is shutting down now" from the daemon's stop handler when the system is
  stopping.  A plain `systemctl restart tvchat-floyd` sends nothing.  The relay's lost-contact toast covers the
  case where no warning was possible.
- **u-l.ca unreachable (external mail):** two checks, so the toast says which part is broken: (1) the mail path:
  TCP to the domain's MX on 25 (and, for reading, the IMAP host on 993) from cpbee; (2) cpbee's own queue:
  `postqueue -j` counting deferred messages for u-l.ca (postqueue lists to anyone by default).  "u-l.ca mail is
  unreachable (3 messages waiting on cpbee)" is more useful than a bare port failure.  *Needs from Chris:* the
  host names and ports that mean "u-l.ca is up", and whether mail to/from it goes through cpbee's Postfix.
- **Also cheap, same machinery:** the internet (the net_telus / net_host probes of the servers page), disk over 90 %,
  a certificate (Radicale, Dovecot, the CA) expiring in 14 days, a failed systemd unit, an old backup
  (backup-cpbee.sh), the VAX state, and LibreNMS alerts (the SNMP rollout exists; lnms can list alerts).  Each is
  one function returning (key, ok?, text).

## Toasts across LAN machines
- Machine ids: `[floyd] machine = cmlaptop` (default: the host name).  Events carry `origin`; `to = all` means
  every other machine, `to = cmdesktop` one.  Nothing is delivered back to its origin.
- What crosses by default: tvmail's *new mail* and tvchat's *direct messages and invitations* (the notes the apps
  already mark `always`), and tvcal's due reminders.  Errors stay on the machine they happened on.  Per-kind
  switches in `[floyd] share_out = mail, chat, reminder` and `share_in = ...`, plus **quiet hours**
  (`quiet = 22:00-07:00`: the hub holds non-urgent events and delivers a count in the morning).
- The same mailbox read on two machines would toast twice; the spool's same-text rule hides the second.  The
  better fix is in the next section: the hub detects mail once and tells everyone.

## Other things worth centralizing (in the order I would do them)
1. **Reminders from the server, not from whichever tvcal is open:** the hub reads Radicale (it already can, for
   `!todo`/`!cal`) and publishes "Dentist in 15 min" to every machine.  This closes tvcal/TO-DO.txt 9
   (reminders fire only while some tvcal runs).
2. **New-mail watcher:** the hub keeps one IMAP IDLE (or a 30 s poll) on Dovecot for each configured mailbox and
   publishes "mail from X: subject" once, instead of N clients each polling.
3. **Morning digest** (`kind = digest`, once a day at the first contact after 07:00): unread, today's events,
   open to-dos, anything down, backup age.  `!digest` asks for it any time.
4. **`!send MACHINE text`:** push a line or a URL to another machine's toasts (a poor man's clipboard between
   cmdesktop and cmlaptop), and `!where`: which machines have a relay up right now (presence).
5. **Per-machine tokens** instead of one shared secret, so a lost laptop is revoked alone
   (`/etc/tvchat/floyd-gate.tokens`: `machine token`), and **TLS** on the gate if it ever leaves the LAN.
6. **One memory of settings:** Floyd's level per user, quiet hours and share switches held on the hub, so a new
   machine only needs `server` and `token` to behave like the others.

## Phases
1. Hub + relay: `publish`/`events` ops, ring and sticky keys, `tvfloyd-relay`, auto-start from the backends,
   lost-contact toast.  Tests: hub with a fake client; relay against the hub writing a temp spool; two relays
   (two machine ids) exchanging a note; kill the hub -> lost contact.
2. Sentinel on cpbee: services, shutdown, u-l.ca, internet, disk, certs.  Deploy script like floyd-gate-on-cpbee.sh.
3. Cross-machine: the outbox in spool.cpp (C++), the `share_*` switches, quiet hours.
4. Server-side watchers: Radicale reminders, Dovecot new mail, digest, `!send`, `!where`, LibreNMS alerts.
5. Per-machine tokens.

## Open questions for Chris
1. Machine names to expect: cmlaptop, cmdesktop - any others (a phone is out of scope for now)?
2. Which events should cross machines by default: new mail, direct chat messages, reminders - more or fewer?
3. u-l.ca: which hosts/ports mean "up", and does its mail go through cpbee's Postfix?
4. Quiet hours wanted?  Per-machine tokens now (phase 1) or later (phase 5)?

---

# Part 3: Floyd as setup wizard, and the second Delta Chat device (2026-11-04)

Chris asked for the back-and-forth and the configuration to be hidden and automated as far as possible, and for Floyd to
have "setup wizard" powers.

## `!setup` (tvfloyd.py `wizard_command`, in the Floyd window of tvcal and tvmail, and of a local floyd account in tvchat)
- **Once per machine:** `!setup join HOST TOKEN` checks the token against the master, writes `[floyd] server / token /
  machine` into that app's conf, and shows the report.  The line is never kept in the input history (`TRecallLine`).
- **After that:** `!setup` compares this machine with the master's *manifest* (`/etc/tvchat/floyd-setup.json`, served by the
  gate's `setup` op together with the public CA certificates) and lists steps as `ok`, `todo` (Floyd can fix it) or `you`
  (only the user can: a missing password).  `!setup N` does one step, `!setup all` every todo.
- **What it checks:** every tv* app's link to the master (and copies server/token/machine into the confs that lack them);
  tvcal's calendar URL, user and CA file; tvchat's accounts from the manifest (appended with the CA file placed beside the
  conf, `autoconnect` extended); the Delta Chat account (not on the owner machine; the step says to use Chat > Use account
  from another device); the notification relay; and whether `~/.netrc` / `~/.mu-tickets` hold a password for each account
  (only the fact is looked at, never the value; the wizard never handles a password).
- **Safe:** conf edits keep comments, add a missing section, and copy the old file to `BAK/` first (once per run); a
  second `!setup all` finds nothing to change.
- **The manifest** is built from Chris's working configs by `tvchat/deploy/floyd-wizard-on-cpbee.sh` (no passwords; refuses
  a key that looks like one).  Machine names: `[floyd] machine`, default the host name; `delta_owner` names the machine that
  runs the account as it is (cpbee).

## Second Delta Chat device
- tvchat: Chat > Add second device... (on the machine that has the account) offers it with the core's `provide_backup`;
  Chat > Use account from another device... (on the new machine) takes it with `get_backup`; the new machine's old Delta data
  is moved to `deltachat/<account>.replaced-<time>`, never deleted, after a confirmation.  `bcc_self` is switched on on both.
- **No copy and paste:** the offer is published to the master hub as a sticky `device` event whose code travels in the event's
  hidden `data` (never in the toast text, never written to the spool); the other machine's blank-code "Use" finds it there.
  The event ends with the offer (a `recover` with the same key).  The code is a one-time secret that rides the same plain LAN
  TCP as the gate's token.  Without a master it is shown in the dialog to type by hand.
- The core stops its mail traffic while an offer stands; Done in the dialog ends the offer and restarts it.
