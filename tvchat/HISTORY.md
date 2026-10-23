# tvchat history

## Unreleased - milestone 5, IRC hardening (October 2026)

- **Keepalive:** a PING after 90 s without a word from the server; no
  answer in 30 s more and the connection counts as lost.
- **Reconnect:** a connection that was up and is lost is made again after
  2, 4, 8 ... 300 s, the open channels rejoined; the account stays
  `connecting` meanwhile and Chat > Disconnect stops it. A first connection
  that fails is not retried.
- **Flood control:** once registered, a burst of 5 lines, then one every
  0.5 s; PONG, our PING and QUIT jump the queue.
- **Typed commands:** `/me`, `/join`, `/part`, `/msg`, `/nick`, `/topic`,
  `/quote` (`//` for a line starting with `/`); any other `/word` is
  refused and stays in the input line. The server messages take commands.
- **Server history:** with IRCv3 `chathistory` (Ergo), joining fetches the
  latest 50, and PgUp past what is kept asks the server for 50 more.

## 0.1.0 - October 2026

The first tvchat. It was built inside tvmail as its chat add-on (milestones
1-4 of [HANDOFF.md](HANDOFF.md), Oct 20-21, 2026), then split out into an app
of its own.

- **tvchat-backend:** one stdlib Python co-process; a `selectors` loop over
  stdin, the sockets and a timer heap; a sequenced event queue; the `serve`
  wire protocol (accounts, connect, convs, join, part, send, history,
  members, mark-read, events).
- **IRC:** TLS (with `cafile` for a LAN server), CAP LS 302, SASL PLAIN,
  server-time, message-tags, echo-message, batch, chathistory, away-notify,
  account-tag, multi-prefix, labeled-response; NAMES / TOPIC / MODE; CTCP
  VERSION; `/me`; mIRC colours stripped; lines split at 400 bytes on UTF-8
  boundaries.
- **The window:** one full-size window laid out like tvmail's: servers |
  conversations / the chat. Unread counts everywhere, mark-read once you
  look, drafts kept per conversation, PgUp for older lines, its own status
  line and a quiet `N new`.
- **Diagnostics:** `--debug` writes `debug.log` and the backend's
  `debug.chat.log` (every IRC line, SASL redacted; why a connection ended);
  `--trace` profiles both.
- **Bugs fixed on the way:** tvmail #44 (menus closed by the 250 ms poll) and
  #46 (SASL sent the mail password); see [BUGS.md](BUGS.md).
