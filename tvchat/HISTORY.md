# tvchat history

## Unreleased

- **Floyd lives on the server.** `tvchat-backend floyd-daemon` runs him as a persistent, headless
  IRC bot (his own nick, logged in to services; systemd unit and `configure/floyd.conf.example`
  included); a Floyd account in a tvchat session now refuses to start unless it says
  `local = yes`.
- **Admins.** `admins = ACCOUNT, ...` on the Floyd account names the services accounts (the IRCv3
  account tag, so a nick cannot fake it) allowed to give orders; `!whoami` says who he thinks you are.
- **Floyd can ask Claude** for an admin (`claude = yes`): `!claude QUESTION`, or `claude QUESTION`
  in a private message. The Claude CLI runs as the service user with read-only tools.

## 1.3.0 - October 2026

tvmail and tvchat now share one version (`../VERSION`, set with `../version.sh`; a test fails if
any place that records it drifts), so tvchat jumps from 0.1.0 to 1.3.0 to meet tvmail.

- **Floyd** (`protocol = floyd`): a robot after Infocom's Planetfall to talk to, offline. He
  chats in the third person, speaks up when left alone, plays Hider and Seeker, holds things
  out, and understands interactive-fiction verbs (`look`, `take`, `drop`, `go`, `give`) and
  small errands (the time, a coin, dice, counting, reminders). He is an old-school bot too
  (`!seen`, `!tell`, karma, quotes, learned facts, `!8ball`, `!fortune` through the real
  fortune(6) when installed), remembering between sessions, and `/floyd` has him listen in any
  IRC, talk or Delta Chat conversation, answering as you behind a `[Floyd]` tag.
- **Typed /commands are one vocabulary** for IRC, talk and Delta Chat: `/help` (made from one
  table, per protocol), `/status`, `/me`, `/shrug`, `/join`, `/query`, `/msg`, `/leave`
  (`/part`, `/close`), `/invite`, `/names`, `/whois`, `/nick`, `/name`, `/topic`, `/away`,
  `/ping`, `/clear`, `/quit`, `/quote`. One that means nothing for the protocol is refused
  with a pointer to `/help`.
- **Toasts instead of pop-ups** (tvcommon): a note that needs no answer sits at the bottom right
  and goes away by itself; Window > Notification history keeps them.
- **File > Edit configuration...** (F9 verify, F8 save + apply): `reload-config` keeps the
  accounts you did not touch connected.
- **Delta Chat groups** (New group, Add / Remove member, Rename) and **Leave**; invitations
  by mail (Join an address, the other side is asked); an optional link with tvmail (Invite can
  mail the link through tvmail; tvmail's Message > Chat with sender / Open chat invite).

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
