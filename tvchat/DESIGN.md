# tvchat design

How tvchat is built, for whoever changes it next. What it does for a user is
in [README.md](README.md); the backend's verbs and options are in
`man tvchat-backend`; what is still to do is in [TO-DO.txt](TO-DO.txt). Where
the code and this file disagree, the code wins - and this file should be
fixed.

## Shape

```text
 tvchat (C++, Turbo Vision)
   '-- chatBackend() -- pipe ---> tvchat-backend serve    (stdlib Python)
         ^ ChatClient                |-- IrcAdapter ---- TLS socket --> an IRC server
                                     |-- TalkAdapter --- UDP/TCP -----> talkd on the LAN master
                                     '-- DeltaChatAdapter - pipes --> deltachat-rpc-server (JSON-RPC)
```

- **Thin C++ face, Python helper.** All protocol work (sockets, TLS, SASL,
  parsing, ntalk) lives in `backend/tvchat-backend`; the C++ draws windows.
  Python >= 3.6, stdlib only (no walrus, no `match`, no f-strings so far).
- **Nothing terminal-specific in the protocol** (no escape codes, widths or
  colours in replies or events), so another front end - a web gateway, say -
  can reuse it unchanged.
- **Credentials never live in the repo or in `tvchat.conf`.**
- The code both apps share (`CoProcess`, the views, `TTvApp`, the menu and
  key-line builders, the test harness) is in [`../tvcommon`](../tvcommon/README.md).

## The backend

- **`tvchat-backend`** runs only when a config exists (`$TVCHAT_CONF` or
  `~/.config/tvchat/tvchat.conf`, falling back to the old
  `~/.config/tvmail/tvchat.conf`). One `selectors` loop (`Loop`) multiplexes
  stdin, every socket and pipe, a timer heap on an injectable clock, and
  `run_in_thread()` - used only for `getaddrinfo`, the one call with no
  non-blocking form. Requests reply at once; connecting reports progress as
  `state` events. A broken config never kills the process before its banner:
  each verb that needs it answers status 1 with the reason.
- **A handler bug is not fatal:** an exception in a protocol line's handler or
  in any loop callback is logged with its traceback and the loop carries on.
- **Event queue:** normalized events with a monotonically increasing `seq`,
  **starting at the wall clock in milliseconds** so numbers keep climbing
  across a backend restart. `events --after SEQ` acknowledges and drops
  everything up to SEQ. Capped at 10,000 (oldest dropped).
- **Shared chat model:** account; conversation (`id` = `ACCOUNT/target`, the
  target lower-cased per the server's casemapping; kind `server`, `channel`,
  `direct`, `group`); contact (display kept apart from address); message (id,
  conversation, sender, timestamp, text, flags). Each IRC account has a
  **`server` conversation, target `*`** (MOTD, notices, SASL results).
  Conversations outlive a disconnect, so they stay listed.
- **Wire framing** is the mail backend's: one shell-quoted request line in,
  `<status> <nbytes>\n` + body out; status 0 = success, 1 = a message for the
  user, 2 = internal error. Reply bodies are TAB-separated lines; inside a
  field **TAB, newline, CR and backslash are escaped as `\t` `\n` `\r` `\\`**
  (backslash too, so a typed `\n` is not a newline). The verbs are in
  `man tvchat-backend`.
- **Message line:** `msgid`, `conv`, `utc-iso8601`, `sender-address`,
  `sender-display`, `flags`, `text`. Flags: `s` self, `n` notice/system, `a`
  action (`/me`), `e` encrypted, `r` read. IRC msgids are the server's
  `msgid` tag, else `ACCOUNT:N`.
- **Event line:** `seq`, `type`, then: `state ACCOUNT STATE DETAIL`
  (`connecting`, `connected`, `disconnected`, `error`) - `msg` + message line -
  `conv-new` / `conv-gone` + conversation line - `members CONV` -
  `topic CONV TEXT` - `unread CONV COUNT` - `history CONV latest|before` -
  for talk, `typing CONV TEXT` and `talk CONV STATE DETAIL`.
- **Debug sub-command** (not allowed in `serve`):
  `tvchat-backend irc-tail [--raw] ACCOUNT` prints live events in a terminal
  (`--raw`: every IRC line too, `<<` in and `>>` out); typed lines go to the
  newest channel, `/join #x` and `/quit` work.
- **Diagnostics:** `--debug [FILE]` logs every `serve` request with its status,
  size and time (an `events` poll only when it brought something), account
  state changes, every IRC line in (`ACCOUNT << ...`) and out (`ACCOUNT >>
  ...`, SASL `AUTHENTICATE` payloads as `<redacted>`), the TLS version and
  cipher, and why a connection ended - phase, error, seconds up, seconds since
  the server last sent anything, bytes in / out / unsent. `--trace [FILE]`
  writes cProfile data on exit. From the C++ side `tvchat --debug FILE`
  passes both on: the backend's goes beside it (`debug.tvchat.log` ->
  `debug.tvchat.backend.log`). `$TVCHAT_BACKEND_LOG` names a file for the
  backend's stderr.

## The C++ side

- **`ChatClient`** (`src/chat.cpp`) starts the backend in the app constructor,
  then `sync()`s: drain `events` first, then `accounts` and `convs` (so no
  event can fall between snapshot and queue). It polls `events --after` every
  **250 ms while any account is connecting or connected, 2 s otherwise**, keeps
  accounts, conversations and unread counts, and notices a restart by
  `generation()` changing - then re-syncs. Each event is broadcast to the
  desktop as `cmChatEvent` (`infoPtr` = `const ChatEvent *`), a restart as
  `cmChatReset`; the window listens for those and nothing in it knows the
  protocol.
- **Idle work** is posted as commands from `idle()`, one per idle round
  (`putEvent` holds one event). **Nothing is posted while a menu or dialog is
  up** (`TopView() != this`): any command ends a tvision menu, so the 250 ms
  poll shut menus at once (BUGS tvmail #44), and a dialog swallows commands it
  does not know. Events wait in the backend's queue meanwhile.
- **`CoProcess`** (tvcommon) forks `<program> serve`, waits for a `0 5\nready`
  banner, sends one request line per call, polls 8 s for the first byte,
  restarts a dead child up to three times in a row, and bumps `generation()`.
  Every pipe step retries on `EINTR` (tvmail #43: a `SIGWINCH` was taken for a
  dead backend).
- **The window** (`src/chatwin.cpp`, `TChatMainWindow`): Servers | Conversations
  / the chat pane, laid out like tvmail's three panes (22 columns on the left,
  the list 2/5 of the height). Servers is the `Name | Proto | Conn` table
  (`drawTableList` in tvcommon's `views.h`). The window is made the first time
  it is opened and never closed.
  - *Moving:* a list's highlight moving shows what it points at; Enter on a
    server connects it if needed and goes to its conversations, Enter on a
    conversation goes to the input. **Tab** cycles servers -> conversations ->
    input (the log is not a stop). The focused pane's heading is reverse-video.
  - *Showing a conversation:* `history CONV` (the last 50), the topic as a dim
    line, then `msg` events. History and the event queue overlap, so lines are
    kept once per msgid. `cmChatReset` re-reads the lists and the history.
  - *Read and unread:* showing a conversation does not read it; `mark-read`
    goes when the chat pane takes the focus (with the window in front), and for
    a message arriving while you are there. (tvision sets a group's `current`
    only after telling the view it has the focus, so the pane's own
    `cmPaneFocused` says which pane it is.)
  - *The chat pane:* a `TChatLog` (a `TMailText`: UTF-8 columns, wrap, Shift-
    select, copy, Find) over a `TFieldLine` (2,000 bytes). Enter sends
    `send CONV TEXT`; a refused send (not connected, an unknown `/word`)
    becomes a dim note and the text stays. PgUp or Up at the top asks `history
    CONV --before OLDEST` (50 at a time) and keeps your place.
  - *Log lines:* `HH:MM <nick> text`, `HH:MM * nick action`, `HH:MM -nick-
    notice` (dim), `HH:MM -- note` for tvchat's own; `[e]` after the time for an
    encrypted message. Colours, all scored by `e2e_regress` for contrast: others
    black on cyan, own lines blue on cyan, system lines dark grey on cyan.
  - *Drafts:* unsent input stays with its conversation; Alt-X asks before it is
    thrown away. Drafts live in memory only.
  - *Greyed by hand:* tvision only tracks commands 0-255 and every command-set
    change resets every menu item's `disabled` flag, so `TVChatMenuBar` sets the
    chat items' flags before and after each event it sees, and `chatCommand()`
    checks again (Enter can still pick a greyed item). With no `tvchat.conf` (or
    a mingw build, or a backend that stopped answering) the chat items are
    greyed with a note saying why.
  - *Menus and key line* come from tvcommon's builders (`tvapp.cpp`), shared
    with tvmail: File (Reload F5, Exit), Chat (Alt-C), Edit, View, Window, Help.
    The Chat menu lists Connect / Disconnect (radio lists of the accounts that
    can be), Join, Leave, Answer, and one item per account with its state.
  - *Talk view:* a classic talk conversation replaces log + input with
    `TTalkView` (you on top, a rule "name - connected", them below; every key
    goes out through `keys`). Hiding the input line made tvision move the focus
    to the server pane; `applyView` puts it back.
- **Windows (mingw) build** has no `fork()`: `CoProcess::call()` returns false,
  `ChatClient::configured()` is false, and the chat items are greyed. The
  `mingw_compile` test layer syntax-checks every source with the mingw-w64
  cross g++.

## IRC adapter

Hand-written, stdlib `socket` + `ssl`, on the shared loop (`IrcAdapter`).

- **Transport:** non-blocking connect and TLS handshake; TLS on 6697 by
  default with verification on; `verify = false` or `cafile =` for a LAN
  server; `tls = false` (port then defaults to 6667). A 60 s timeout covers
  connect + TLS + registration.
- **Registration:** `CAP LS 302` (multi-line LS handled), `NICK`, `USER`;
  `CAP REQ` for whichever of `sasl server-time message-tags echo-message batch
  chathistory draft/chathistory away-notify account-tag multi-prefix
  labeled-response` the server offers; SASL PLAIN only with `sasl =` set and a
  password found; `CAP END`. Nick in use -> `nick_`. ISUPPORT `CASEMAPPING`,
  `CHANTYPES`, `PREFIX` and `CHATHISTORY` are read. A failed SASL is reported in
  the server window and registration continues.
- **Traffic:** PING answered; JOIN / PART / KICK / QUIT / NICK / MODE / TOPIC /
  332 / 353 / 366 keep members and topic; PRIVMSG / NOTICE with `server-time`,
  `msgid`, `/me`, CTCP VERSION answered, other CTCP ignored, STATUSMSG targets,
  mIRC colours stripped; numerics and MOTD go to the server conversation. A
  server `ERROR` that arrives with the hang-up is still read, so its reason
  reaches the `state` event.
- **Sending:** split at 400 bytes on a UTF-8 boundary, one line per newline.
- **Passwords:** `$TVCHAT_PASS_<ACCOUNT>`, then `~/.mu-tickets`, then
  `~/.netrc`; in both files only an entry for the SASL login counts (tvmail
  #46: the mail lookup's any-user fallback once sent the mail password). A
  failed login says where its password came from (`find_password_where()`,
  which chat-setup's `configure-tvchat.sh` also calls). No password with
  `sasl =` set -> `connect` answers status 1 saying where to put it.
- **Keepalive:** `_keepalive()` (a timer while up) sends `PING :tvchat` after
  `PING_IDLE` = 90 s of silence; nothing within `PING_GRACE` = 30 s more ->
  `_lost("no reply from the server for N s")`.
- **Reconnect:** `_lost()` of a connection that was up -> `_retry()`: 2, 4,
  8 ... 300 s (`attempt` resets once a connection stayed up `RETRY_STABLE` =
  60 s), a note in the server window and `state ACCOUNT connecting "WHY -
  reconnecting in N s"`, so the UI keeps its 250 ms poll. A *first* connect
  that fails is not retried. `disconnect` cancels the wait; `on_welcome`
  rejoins the open channels. A password gone missing stops the retries.
  Timers of a dead connection are no-ops (`_later()` checks `gen`).
- **Flood control:** once up, `send_raw()` queues behind a token bucket - a
  burst of `BURST` = 5, then one per `RATE` = 0.5 s; PONG, our PING and QUIT
  skip it; registration is never paced. Lines still queued when a connection is
  lost are dropped with a note ("N lines not sent"). The tunables are class
  attributes so tests shrink them.
- **Typed commands** (`send`): `/me`, `/join #CHAN [KEY]`, `/part [#CHAN]
  [REASON]`, `/msg NICK TEXT` (opens the conversation), `/nick`, `/topic
  [TEXT]`, `/quote` (alias `/raw`); `//x` sends `/x`. The whole text is checked
  first: an unknown `/word`, a bad argument or a plain line in the server
  window refuses it all (status 1, nothing sent).
- **Server history** (IRCv3 `chathistory` + `batch`): our own JOIN ->
  `CHATHISTORY LATEST #chan * 50`; `history --before` past the start of the
  in-memory ring (2,000 lines per conversation) -> `CHATHISTORY BEFORE`,
  answered with status 1 "asking the server for older messages..."; `on_BATCH`
  merges the batch in time order, once per msgid, never unread, then pushes the
  `history` event. An empty batch or `FAIL CHATHISTORY` sets `history_done`.
  Without the caps (Libera) the ring is all there is. Ergo has chathistory.
- IRC history is not persisted: the log is the backend's ring, gone with it.

## talk(1) adapter

`TalkAdapter`: ntalk through **one talkd on the LAN master** (chrispollitt/POSIX
`chat-setup/talkd/`, `configure-talkd.sh`), not the stock one - the stock talkd
announces to a tty in utmp, which tvchat is not. Our talkd speaks standard
ntalk on UDP 518 (so a plain `talk you@cpbee` from a shell rings tvchat), lets
tvchat register its user so an announcement arrives as an event, and relays the
session over TCP 518 so only the master takes inbound connections. No auth, like
talk itself: LAN only (`--allow`: loopback + the LAN's subnet).

- **The wire** (checked against FreeBSD's `protocols/talkd.h` and `talkd/
  process.c`): UDP 518; `CTL_MSG` 84 bytes = vers(1) type(1) answer(1) pad(1)
  id_num(4) addr(16) ctl_addr(16) pid(4) l_name(12) r_name(12) r_tty(16);
  `CTL_RESPONSE` 24 bytes. **Everything multi-byte is big-endian, including the
  sockaddr's `sa_family`**; a sockaddr is the 4.3BSD 16 bytes: family(2)
  port(2) IPv4(4) zero(8). Types LEAVE_INVITE 0, LOOK_UP 1, DELETE 2, ANNOUNCE
  3; answers SUCCESS 0 ... BADCTLADDR 8; TALK_VERSION 1; invitations live 60 s,
  callers re-announce every 30 s. On the TCP connection each side first writes
  its 3 edit characters (erase, kill, word-erase), then text.
- **tvchat <-> talkd** is a small line protocol over TCP (documented in
  `talkd.py`'s docstring): HELLO / CALL / RING / ACCEPT / DECLINE / HANGUP /
  CONNECT, and a second connection `ATTACH TOKEN` carrying the raw bytes.
- **In the backend:** one control connection to talkd per talk account; one
  conversation per person (kind `direct`; talkd's own host dropped from the
  name). `join` calls, `answer CONV` answers, `part` hangs up or declines.
  `send` = whole lines (IRC mode); `keys CONV TEXT` = keystrokes (classic; TEXT
  escaped like reply fields, since a request is one line). The other side's
  erase / kill / word-erase are applied as bytes arrive: finished lines are
  `msg` (unread), the unfinished one is the `typing` event; call state is the
  `talk` event (calling, ringing, incoming, connected, ended). `accounts` has a
  5th field (talk_view) and `convs` / `conv-new` a 5th (call state), for talk
  only.
- **Two views** per talk conversation: *classic* (talk's split screen, every
  keystroke sent as typed - the default; `talk_view = classic | irc` per
  account, View > Talk view (Ctrl-T) per conversation) and *IRC mode* (the log,
  whole lines on Enter, "NAME is typing..." while the other side has an
  unfinished line). IRC conversations are always the log.

## Delta Chat adapter

`DeltaChatAdapter`: not a reimplementation - the official core,
`deltachat-rpc-server` (chatmail/core), runs as a child of the backend, one per
account, and speaks JSON-RPC 2.0 (one object per line) on its stdin / stdout.
It does the Autocrypt / OpenPGP, IMAP IDLE, SMTP and its own database; the
adapter maps its chats onto the shared model. Written against 2.65.0 (the API
names were checked against `--openrpc` and the `deltachat-rpc-client` source).

- **Process:** `rpc_server =` (default `deltachat-rpc-server` on `PATH`) is
  started with `DC_ACCOUNTS_PATH` = `$XDG_DATA_HOME/tvchat/deltachat/ACCOUNT/`
  (mode 0700, default `~/.local/share`); its stderr goes to `ACCOUNT.log` beside
  it, whose last line explains a core that would not start. A missing core is a
  `UserError` with the install hint (the account goes to `error`; the others
  work). On Cygwin a `.exe` core gets its directory as a Windows path.
- **Two ways of asking.** Slow calls are asynchronous on the shared loop (the
  child's stdout is a `Loop.watch` reader, writes are buffered and non-blocking):
  the first `get_system_info` (30 s), `add_or_update_transport` (180 s), and
  `get_next_event`, of which **exactly one is outstanding at all times** - the
  next goes out after the previous event is handled. Whatever the user triggers
  (`send`, `join`, `history`, `mark-read`) and the work an event needs is a
  local database call, so `call()` writes the request and waits for its
  answer, holding any other lines for the loop (10 s limit). That is why `send`
  can answer with the message's real id.
- **Connecting:** `get_all_account_ids` (none: `add_account`) -> `is_configured`.
  Not configured: the password (`find_password`, the IRC places, login = the
  address, host = `imap_server` or the address's domain) -> `add_or_update_transport`
  with `addr` and `password` (the rest autoconfigured; `imap_server`, `imap_port`,
  `smtp_server`, `smtp_port`, `imap_user`, `smtp_user`, `verify = false` are
  passed on) - `ConfigureProgress` shows as the `connecting` detail. Then
  `name` -> `displayname`, `start_io`, the chat list. The state follows
  `get_connectivity`: `connected` at 3000 (working) or more, else `connecting`
  "waiting for the mail server" - so a laptop with no network still shows its chats.
- **Mapping:** chat id -> conversation `ACCOUNT/<chat id>` (kind `direct` for a
  one-to-one chat, `group` otherwise; plus the usual `*` server conversation for
  the core's errors and restart notes); message id -> `ACCOUNT:<core id>`;
  sender address and display name from the message's contact; flags `s` (from
  me), `n` (an info line), `e` (`showPadlock`), `r` (mine, or seen). A
  non-text message is `[attachment: NAME]` plus its text. The saved-messages
  and device chats are left out. Unread = the core's fresh counter, at connect.
- **Events** (`get_next_event`): `IncomingMsg` and `MsgsChanged` (a message
  not yet shown is fetched and added; ours, already shown by `send`, are not
  doubled), `MsgFailed` (a note in the chat), `ChatModified` (title, members),
  `ChatDeleted`, `MsgsNoticed` (read on another device: unread goes to 0 without
  telling the core again), `ConnectivityChanged`, `Error` (a note in the server
  window). Events of other core accounts are ignored.
- **Contact requests:** a message from someone new arrives in a chat the core
  marks as a request; it is listed with a note, and the first reply calls
  `accept_chat`.
- **Reading:** `mark-read` also tells the core (`marknoticed_chat`,
  `markseen_msgs`), which sends read receipts if the user's settings say so.
- **History:** the last 50 messages of each chat at connect; `history --before`
  past the ring loads 50 more from the core (the `history CONV before` event),
  so, unlike IRC's, Delta Chat's history persists.
- **Invites (SecureJoin):** the core refuses to send to an address it has no
  key for ("e2e encryption unavailable"), and a plain `join ADDRESS` makes no
  key. Keys are exchanged by an invite link: `tvchat-backend invite ACCOUNT`
  (`get_chat_securejoin_qr_code`) prints one, and `join ACCOUNT LINK`
  (`https://i.delta.chat/#...` or `OPENPGP4FPR:...`) runs `secure_join`, which
  opens the chat and finishes the handshake in the background (its progress
  arrives as info lines). After that both sides can write; a `send` that is
  refused for want of a key says so.
- **Invitations by mail** (so nobody carries a link between terminals): `join
  ADDRESS` for an address the core holds no key for (`isKeyContact` false) mails
  the invite link - a plain mail from the account over its own SMTP (port from
  `smtp_port`; `smtp_security` ssl | starttls | plain, by default from the port),
  header `X-Tvchat-Invite: 1` - and answers with the server conversation, where
  the progress is told; their chat appears by itself once they accept. The
  other side's adapter looks in its own INBOX every 30 s (`INVITE_POLL`; its own
  IMAP login, `imap_security` likewise, in a `run_in_thread` job: imaplib
  blocks) for that header. A mail counts only if the link's `a=` names the
  `From:` address; anything else is reported and ignored. Each new one is an
  `invite ACCOUNT SENDER` event and a note in the server window; `invites` lists
  those waiting, `accept-invite ACCOUNT SENDER` runs `secure_join` and removes
  the mail, `decline-invite` just removes it. The UI asks at once (Yes / No /
  Cancel = later) and Chat > Accept invite... asks again. Mails answered are
  remembered by UID so a scan already in flight cannot list them again. Needs
  the mail servers in tvchat.conf (or the address's domain, which usually is
  the IMAP / SMTP host). The core's own copy of that mail is ignored: it shows
  no classic emails.
- **The optional tvmail link** (the two apps share nothing but a directory):
  tvmail's Message > Chat with sender / Open chat invite write a one-line
  `*.req` file into `$TVCHAT_REQUESTS`, else `$XDG_STATE_HOME/tvchat/requests`
  (`~/.local/state/tvchat/requests`), written under a temp name and renamed.
  `Chat.requests_tick` (every `REQUEST_POLL` = 2 s while a Delta Chat account
  is configured) acts on `chat ADDRESS` (the adapter's `join`: a chat, or an
  invitation by mail when there is no key) and `invite LINK` (`add_invite`:
  offered for acceptance like a mailed one, from the address the link names),
  deletes the file and tells the UI with a `notice TEXT` event (the status
  line). With no connected Delta Chat account a request waits. A bad or odd
  request is reported and dropped. Neither program needs the other.
- **Mailing the invitation through tvmail** (the other direction, also optional):
  `invite-mail ACCOUNT ADDRESS` runs `tvmail-backend send --to ADDRESS --subject
  ...` (found on PATH, else /usr/local/bin, ~/bin, ~/.local/bin; a `run_in_thread`
  job) with the link in the body, so the mail goes out through the user's
  ordinary mail account and lands in the recipient's ordinary inbox, where their
  tvmail offers Message > Open chat invite. `has-tvmail` says whether it can be
  used; the Invite dialog shows its "mail it to" field only then. The result is
  told in the server window.
- **Leave:** `part` on a Delta Chat conversation leaves a group (`leave_group`; the
  others are told) and then `delete_chat`s it, and removes the conversation. The
  UI asks first (Yes / No), since the history goes.
- **Groups** (encrypted, key-contacts only): `new-group ACCOUNT NAME [ADDRESS...]`
  (`create_group_chat`), `group-add`, `group-remove`, `group-rename CONV ...`. Only a
  person we hold a key for (`get_contacts`, which lists key-contacts) can be put in
  a group; an address with no key yet is invited by mail instead, and is added later
  with `group-add`. The group reaches its members with the first message sent to it
  (the core's "unpromoted" group). The Chat menu greys Add / Remove / Rename unless
  a Delta Chat group is shown.
- **Typed commands:** none yet; `/word` is refused, `//x` sends `/x`. `join`
  takes an email address and opens (or creates) the one-to-one chat; `part` is
  refused.
- **Lost core:** an exit, a broken pipe or a core error while up -> `_lost()`
  -> restart after 2, 4 ... 300 s (like IRC's reconnect), a note in the server
  window; a start that fails the first time is not retried. Disconnect asks the
  core to stop its I/O, then closes its stdin, which ends it.

## Configuration

`~/.config/tvchat/tvchat.conf` (INI; `$TVCHAT_CONF` overrides; no file = no
chat, no co-process). `configure/tvchat.conf.example` is the commented
reference and is installed with the docs. No interpolation, so `%` is literal.
`[account:NAME]`: NAME may not contain `/`, `\` or whitespace; `protocol = irc |
talk | deltachat` is required; `display =` defaults
to NAME. `[chat] autoconnect` must name existing accounts; they connect when
`serve` starts. `[ui] mesg = n | y` (default n) is read by the C++.

## Tests

Five layers under `./test.sh`; see [tests/README.md](tests/README.md). None
needs network access or a real account. Conventions: stdlib only in Python;
C++17 across `src/`; comments in the existing explanatory style; no
credentials in code, tests or examples; every squashed bug gets a
[BUGS.md](BUGS.md) row and a regression case. Timing in the pty tests: wait for
a condition, never a fixed delay (tvmail BUGS #47).
