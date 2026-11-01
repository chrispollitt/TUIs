# tvchat history

## Unreleased

- **Floyd answers talk(1) calls, and finds the real fortune:** the central Floyd now connects
  `protocol = talk` accounts, picks up an incoming call by himself (`auto_answer = no` to stop that;
  tvchat sessions still wait for you) and says hello (`greeting = TEXT`, empty for none); give him
  `[account:talk]` with `host = 127.0.0.1`, `user = floyd` and `talk/*` in `attach =`, then
  `talk floyd@cpbee`. `!fortune` looks in `/usr/games` as well as the PATH: Debian's fortune-mod
  installs there, which a systemd service's PATH does not include, so he had been using his own few.

- **`!status` for Floyd:** at every level, five short lines - his version, how long he has been up (the
  daemon's process) and on which networks, his level in that conversation, whether Claude is available
  to the asker (and if not, why: the network does not allow language models, `claude = no`, not on the
  server, program not found, admins only), whether he has the system `fortune` program or only his own,
  and what he remembers (quotes, facts, tells waiting, people seen, conversations he listens in), plus
  who runs him.

- **Invite people (or bots) to another server:** Chat > Invite... now takes an IRC account as well as
  a Delta Chat one (a channel is optional), and when the chat shown is with a person or group on any
  IRC or Delta Chat account it can send the invitation right there (`irc://host[:port][/#channel]` or
  the Delta Chat link; on the same network with a channel it is an ordinary `/invite`). The other
  tvchat spots the link in what it is told and Chat > Accept invite... offers it ("carol invites you to
  #lobby on Libera") and joins; `join_invites = yes` accepts at once. New backend verb `invite-send`,
  `invite` takes a channel, `invites` lines may carry a third field.

- **tvmail messages in the LAN channel, on your yes:** tvmail's Message > Share in chat asks tvchat to
  say "tvmail: <sender> - <subject>" (never the body) in the channel `[chat] share_to = ACCOUNT:#channel`
  names. tvchat only queues it and shows a toast; Chat > Share from tvmail... shows the text, which you
  can change, and Send says it as you (Discard forgets it, Later keeps it, up to 30 minutes; a request
  left more than 10 minutes is dropped). Control characters are removed, so a subject cannot start
  an IRC command. New backend verbs `shares`, `share-send`, `share-drop`; the requests directory is now
  read whenever tvchat runs, not only with a Delta Chat account.

- **What Floyd mails is filed in his Sent folder:** after the SMTP send, a copy goes to the mailbox with
  IMAP APPEND (marked read) - the folder the server marks as Sent, else `Sent` / `INBOX.Sent` / `Sent Items`,
  else one is made. `save_sent = no` turns it off, `sent_folder = NAME` names the folder. A copy that cannot
  be filed is only logged: the mail has gone. Also, a NickServ confirmation now works through a short
  connection of its own when his IRC account is not up (the first try lost a race: at startup the mail
  account read Libera's mail 8 s before the IRC account was connected). Each outcome is also logged (INFO).

- **Floyd confirms NickServ's registration mail himself, and is `floyd-bot`:** a mail account with
  `verify_nick = ACCOUNT` (his IRC account) reads the network's "/msg NickServ VERIFY REGISTER account
  code" mail and sends that one command to NickServ, only when it comes from `verify_from` (default
  libera.chat), carries a signature the mail server verified (`verify_dkim = no` relaxes that), and
  names the nick or `sasl =` of that IRC account; each code once. The nick defaults in the example
  configs, `configure-floyd.sh` (POSIX) and its test are now `floyd-bot` (Libera has a Floyd), and he answers
  to `floyd-bot:` as well as `floyd:` in a channel.

- **tvchat knows when you are a channel operator:** the conversation list and the heading show `@`
  in front of a channel you operate (`%` half-op, `+` voice), and a note says when it changes (new
  `role` event, `roles` verb; it follows NAMES and mode changes).

- **Floyd on a public network, by its rules (Libera.Chat):** an IRC account with `bot = yes` joins a
  channel only when one of his administrators invites him (`invite_from =`, default the admins of the
  Floyd account), checked by the services account the server vouches for (the IRCv3 account tag, else
  a WHOIS), and refuses anyone else politely; says what he is on joining (a bot, who runs it, `!leave`,
  `!level off`); answers CTCP VERSION and carries a realname as a bot run by `contact =`; goes away on `!leave`
  from the channel's operators (or his admins) and never comes back where he was kicked or sent away.
  `llm = no` on the account keeps `!claude` off there (Libera does not want bots that use language
  models); `level =` on the account sets how much he does there. New for everyone: `!about` says what
  Floyd is, and his old "not allowed on the real servers" line now says he goes only where his people ask
  and the channel's people say yes. `join_invites = ops` is the policy by itself.

- **Notes between tvmail and tvchat:** the two are full-screen apps, so one is usually out of
  sight. Errors, and what you would want to know anyway (a message written to you, an invitation to
  answer, new mail in tvmail), are left for the other app as small files (`tvcommon/src/spool.cpp`,
  `~/.local/state/tvcommon/spool/`) and show there as a toast "tvchat: carol invites you ..." - also
  if that app starts later, within 15 minutes. `[ui] share = error | warn | info | off` in either
  app's conf (default `error`) sets how much goes on its own.

- **tvchat and tvmail, more of each other:** Chat > Mail selection... mails the lines selected in the
  log (Shift-arrows or a drag) through tvmail's account, to the person's address if it is known (an
  `email:` handle in `[people]`, or their Delta Chat address), else one you type; the outcome is a
  toast. With `[chat] ooo_away = yes`, tvmail's out-of-office reply also shows as the away message
  on the connected IRC accounts (checked every 5 minutes, only inside the reply's dates, cleared when
  it goes off). New backend verbs `mail-address` and `mail-chat`.

- **Invite is easier in a channel:** with a channel shown, Chat > Invite... starts with that
  channel filled in and, after OK, asks which user in the channel to send the invitation to (Skip
  shows the link instead). **Copy link** puts the link on the clipboard, in the dialog and in the one
  that shows it. New backend verb `invitees CONV`.

- **A nick joining table:** a `[people]` section in tvchat.conf (`carol = libera:cpollitt,
  oftc:carol_, delta:carol@example.org`) says who is who across servers. Inviting someone who is in
  the table to a server they are on now invites their nick there (IRC INVITE, or a Delta Chat chat /
  mailed invitation) instead of sending a link; an invitation from them is shown as "carol (cpollitt)".
  `check-config` and `reload-config` know the section; new verb `people`.

- **Classic talk says why the arrow keys do nothing:** Left / Right flash a hint (talk sends
  characters, not cursor moves; Ctrl-T for the IRC view, where arrow keys edit) instead of being
  ignored.

- **Admins can give orders by mail:** a mail counts as from an admin only when the sender is in
  `known =` and the receiving mail server verified their DKIM signature for the From domain
  (`DKIM_VALID_AU` in the topmost `X-Spam-Report` / `X-Ham-Report`); the sender's `known =` name is
  then their account name, so it must be in `admins =` (`!claude`, `!whoami`). A forged From line,
  an unsigned mail or a signed mail from someone not in `known =` is refused as before. This relies on
  the mail host adding that header.

- **Mail Floyd only talks to people he knows:** `known = name=address, ...` on the mail account
  limits whom he reads and answers by mail; anyone else is dropped silently (the mailbox's own
  domain still counts). `!claude` stays admin-only, and a mail is never from an admin.

- **Mail Floyd and he mails back:** a new `protocol = mail` account (`floyd-daemon` starts it) reads
  a mailbox every 30 s; each sender is a conversation Floyd listens in (`attach = mail/*`), and what
  he says comes back as one reply mail in the same thread (`Re:` subject, `In-Reply-To`,
  `Auto-Submitted: auto-replied`). Robots, lists, bounces, `noreply` and Delta Chat invitations are
  never answered; one address gets at most 5 replies an hour (60 in all). A mail is never from an
  admin (a From line proves nothing), so `!claude` is refused by mail.

- **The central Floyd reads his Delta Chat inbox for invitations:** `floyd-daemon` now starts
  `protocol = deltachat` accounts too and accepts every chat invitation that arrives by mail
  (`/invite floyd@host`), as he joins any IRC channel he is invited to. In a session it is
  still a question; `accept_invites = yes` on the account accepts without asking. The example
  config has the account (commented out) and says it needs `delta/*` in `attach =`.

- **Floyd answers an unknown `!command`** ("Floyd doesn't know !foo ... Try !help") instead of
  staying silent, in channels and chats he listens in. `!!!` and a lone `!` are still ignored.

- **`/invite Floyd #newchannel` works:** an incoming IRC `INVITE` was ignored; the central Floyd now
  joins any channel he is invited to, and a session shows a note in the server window (`/join` to
  accept, or `join_invites = yes` on the account to join at once).
- **Input history:** Up / Down in the chat input line recall what you sent (one list for every
  conversation, kept between sessions in `~/.local/state/tvchat/input-history`, never a NickServ,
  OPER or `/pass` line); the log scrolls with PgUp / PgDn and Shift-Up / Shift-Down instead.
- **Floyd's Claude has no connectors:** the CLI that `!claude` runs is cut off from MCP servers, the
  login's claude.ai connectors (Google Drive, GitHub ...) and skills, as well as from every built-in
  tool but Read, Grep and Glob.

- **Cygwin is the only Windows** (tvchat never had a native build; the shared `_WIN32` branches in
  tvcommon, and the mingw test and build option in tvmail, are gone).

- **Floyd's levels** (`level = off | minimal | helpful | chatty | clippy`, `!level NAME` per
  conversation): from deaf to "full clippy mode", where he offers tips introduced "Floyd not
  clippy but ...". At *minimal* he only answers `!commands` and what is said to him; *helpful* adds
  plain-words questions, karma and `!tell`s; *chatty* is the old Floyd; talking by himself needs it.
- **Useful things:** `!calc`, `!convert`, `!time in CITY`, `!todo`, `!note`, and `!catchup` (who said
  what since you last spoke, what mentioned you, the questions asked). Shared with tvmail's Floyd
  in `tvcommon/py/tvfloyd.py`, which each app installs.

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
