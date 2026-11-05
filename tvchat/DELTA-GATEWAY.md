# The Delta Chat gateway (dcgw)

Decision of 2026-11-04: **one Delta Chat account per person, on one machine (cpbee)**, and tvchat everywhere else is a thin
client.  Nothing is synced between machines because there is only one account: the key, the database and the mail login
live on cpbee; a client holds a token.  (This replaces the "Add second device" backup transfer as the main path; that code
stays as a fallback for a machine that wants its own copy.)

```
cmlaptop tvchat --\                          one deltachat-rpc-server per person
cmdesktop tvchat --+-- TLS + token --> dcgw  (key, dc.db, mail login)  --> Dovecot / SMTP as before
cpbee tvchat -----/    on cpbee
```

## What it is
`backend/dcgw` is a small stdlib-Python daemon.  Per person (a `[user:NAME]` in `/etc/dcgw/dcgw.conf`) it runs the official
core (`deltachat-rpc-server`) with its own folder under `/var/lib/dcgw/NAME`.  A client connects (TCP, TLS when a certificate
is configured) and logs in with one line, `DCGW 1 TOKEN`; the answer is `OK 1 NAME ADDRESS` or `ERR ...`.  After that both
sides speak the core's own JSON-RPC 2.0, one object per line.  So the thin client **is** tvchat's existing Delta Chat adapter
with a socket where the core's pipes were (`gateway = HOST[:PORT]`, `token = ...` on the account; `_GatewayLink`).

- **Every request is checked.**  A short allow-list of the calls tvchat makes (chat list, history, send, groups, read marks,
  invite link...).  Not on it: anything that reads the mail login (`get_config` of other keys), touches a file on the
  server, makes or removes an account, exports a key, or transfers a backup.  The account id in a request must be the
  person's own; a token opens exactly one person's account.
- **Events are fanned out by the gateway.**  The core has one event queue per process, which two clients could not share, so
  the gateway reads it once and gives every connected client of that person every event (`get_next_event` is answered from a
  per-client queue).  A message sent from one client therefore shows on the others at once.
- **The gateway keeps the core's mail traffic running** even with no client connected (`start_io` is its own; a client's
  `start_io` / `stop_io` is acknowledged and ignored), so mail arrives while every tvchat is closed.
- **A dead core drops its clients** (they reconnect with their usual backoff, which restarts the core).
- **TLS:** `tls_cert` / `tls_key` on the server, `gateway_tls = yes` and `gateway_cafile = ...` on the client (certificate
  verified, host name checked).  With TLS the client's bytes go through a thread pair and a local socket pair, so the adapter
  still reads and writes plain descriptors.  Without a certificate the wire is plain TCP, like Floyd's gate: keep it for a
  trusted LAN only.

## People and tokens (multi-user)
`dcgw add-user NAME --addr ADDRESS` makes a person and a token (`dcgw token NAME` shows it again).  Michelle gets
`michelle@cpbee.cmnet` (her own Dovecot mailbox) as a second `[user:...]`; her token opens only her account, her data sits in
her own folder, and she can use any machine's tvchat with it.  **Privacy:** her keys live on cpbee, so whoever administers
cpbee could read her messages; she should know.

## Setting an account up
`dcgw adopt NAME DIR` copies an existing account folder (a tvchat `deltachat/<account>`: key, chats, mail login);
`dcgw setup NAME` instead gives a fresh account its mail login (asks for the password, with the service stopped).
`tvchat/deploy/dcgw-on-cpbee.sh` does it for Chris (adopting his account) and installs the service, the firewall port and the
certificate.

## On the clients
`[account:delta_chris]` with `protocol = deltachat`, `gateway = cpbee`, `token = ...`, `gateway_tls = yes`,
`gateway_cafile = ~/.config/tvchat/dcgw-ca.pem`; no `addr`, no `rpc_server`, no password.  Floyd's wizard adds the account from
the master's manifest (`!setup`) and saves the token (`!setup delta TOKEN`, never kept in the input history).

## Limits
- cpbee must be up: with it down, Delta Chat is down on every machine.
- Invitations that arrive *by mail* (the "Accept invite" scan) read the mailbox with the user's mail password, which a thin
  client does not have: that scan is off for gateway accounts (a candidate to move into dcgw).
- Attachments are not carried (tvchat shows `[attachment: NAME]`), as before.
- The Floyd daemon's own Delta Chat account (floyd@cpbee.cmnet) is separate and unchanged.

Tests: `tests/test_dcgw.py` (the gateway against the fake core), `TestDeltaGateway` / `TestDeltaGatewayTls` in
`tests/test_chat_backend.py` (the thin client, plain and TLS), and the screen case `delta_through_the_gateway` in
`tests/e2e_regress.py`.
