# tvchat history

## Unreleased (October 2026)

- **Delta Chat adapter** (`protocol = deltachat`), first step: the official
  `deltachat-rpc-server` runs as a child of the backend and `DeltaChatAdapter`
  speaks JSON-RPC to it - setting up the mailbox, the chats with their last 50
  messages and unread counts, incoming and sent messages, contact requests,
  read marks, older history from the core, and a restart if the core dies. Invite
  links (`invite` verb; `join` takes a link) exchange the keys the core needs.
  Chat > Invite... shows the link (also in the server window), Chat > Join... takes an
  address or a link. Tried against two real mailboxes on cpbee. Set-up scripts:
  mail-setup's `configure-delta.sh` (the mailboxes) and chat-setup's
  `configure-tvchat-delta.sh` (a tvchat account, in one command).

- **talk(1)** (`protocol = talk`): through chat-setup's talkd on the LAN
  master, so a plain `talk you@cpbee` from any shell rings you in tvchat. Join
  calls, Enter (or Chat > Answer) answers, Leave hangs up. Two views, View >
  Talk view (Ctrl-T): classic - talk's split screen, every keystroke sent (the
  default, `talk_view`) - or IRC - the log, whole lines, "... is typing".
- **Reconnect:** a connection that was up and is lost is made again after
  2, 4, 8 ... 300 s, the open channels rejoined; the account stays
  `connecting` meanwhile and Chat > Disconnect stops it. A first connection
  that fails is not retried. A quiet server is pinged after 90 s; no answer in
  30 s more and the connection counts as lost.
- **Flood control:** once registered, a burst of 5 lines, then one every
  0.5 s; PONG, our PING and QUIT jump the queue.
- **Typed commands:** `/me`, `/join`, `/part`, `/msg`, `/nick`, `/topic`,
  `/quote` (`//` for a line starting with `/`); any other `/word` is
  refused and stays in the input line. The server messages take commands.
- **Server history:** with IRCv3 `chathistory` (Ergo), joining fetches the
  latest 50, and PgUp past what is kept asks the server for 50 more.
- **The Servers pane is a table:** `Name | Proto | Conn`, faint rules between
  the columns (tvmail's Folders pane is the same: `Folder | Total | New`).
- **Same menus and key line as tvmail:** File > Reload (F5), Chat on Alt-C
  (it was Alt-A), a Window menu with Zoom, Tile and Cascade, one About box.
  Join starts on the account picked in the Servers pane.
- **Ctrl-L** (Window > Redraw screen) repaints the screen; while tvchat runs
  its terminal takes no messages (`mesg n`; `[ui] mesg = y` keeps taking them).
- **Log names carry the app:** `--debug` writes `debug.tvchat.log` and the
  backend's `debug.tvchat.backend.log` (every IRC line, SASL redacted; why a
  connection ended); `--trace` writes `trace.tvchat.log` and
  `trace.tvchat.backend.log`.

## 0.1.0 - October 2026

The first tvchat. It was built inside tvmail as its chat add-on (Oct 20-21, 2026), then split out into an app
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
- **Diagnostics:** `--debug` wrote `debug.log` and the backend's
  `debug.chat.log` (every IRC line, SASL redacted; why a connection ended);
  `--trace` profiles both.
- **Bugs fixed on the way:** tvmail #44 (menus closed by the 250 ms poll) and
  #46 (SASL sent the mail password); see [BUGS.md](BUGS.md).
