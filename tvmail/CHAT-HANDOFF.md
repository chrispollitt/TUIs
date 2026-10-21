# tvmail chat add-on — Claude Code handoff

Started Oct 20, 2026 · @Chris Pollitt · updated Oct 21, 2026 after milestone 3

This is the living version of the original handoff: the plan, what has been
built, and the decisions made since. Where the code and this file disagree,
the code wins - and this file should be fixed.

## Status

| # | Milestone | State | Commit |
| --- | --- | --- | --- |
| 1 | Backend skeleton | done | 050add8 |
| 2 | IRC from the command line | done, except the live Libera check (needs Chris's nick/channel; the build sandbox has no DNS) | 78e5e45 |
| – | Split `src/tvmail.cpp` (approved by Chris) | done | 73060b6 |
| 3 | C++ plumbing | done | 62347cf |
| 4 | Chat windows | **next** | |
| 5 | IRC hardening | | |
| 6 | Delta Chat adapter | | |
| 7 | Docs | | |

All work is on branch `ccr-b4320bfb-x90pzx`. `./test.sh` passes every layer.
Stop for Chris's review after each milestone.

## Purpose and scope

Add instant messaging to tvmail (repo `chrispollitt/TUIs`, directory `tvmail/`) as two protocol backends behind one chat UI: IRC with IRCv3 extensions, and Delta Chat (encrypted chat carried over email). IRC first; Delta Chat second, through the same abstraction.

Decisions already made by Chris — do not reopen:

- **No WhatsApp, Teams or Messenger.** WhatsApp unofficial clients risk banning his real mobile number; Teams Graph chat APIs reject personal Microsoft accounts; Messenger has no personal-inbox API.
- **Extend tvmail, not a new app.** Same Turbo Vision look, same C++ front end, same Python-helper split.
- **Two protocols, one shared chat model.** In the spirit of Pidgin: one client, a thin adapter per protocol.
- **No server work required for v1.** IRC is developed against Libera.Chat; a self-hosted Ergo server is a later, separate project. Delta Chat uses a dedicated address on Chris's existing Postfix/Dovecot mail master.
- **`src/tvmail.cpp` may be split** (answered Oct 20) - done, see below.

Out of scope: file transfer over IRC (DCC), voice or video, a Matrix or XMPP backend, hosting any server, threading or reactions UI.

## tvmail architecture to respect

tvmail (v1.2.0) is a thin C++17 Turbo Vision face over stdlib-only Python helpers.

- **C++ sources, one file per concern** (`src/`, all in `CMakeLists.txt`):

  | file | contents |
  | --- | --- |
  | `tvmail.h` | `Uses_*`, includes, command ids, colours, shared declarations |
  | `views.h` | views several windows share: `TMailText` (the chat log should derive from it), `TTextView`, `TViewerWindow`, `TMailViewWindow`, `TBodyEditor`, `TFieldLine` |
  | `main.cpp` | command line, `--selftest`, `main()` |
  | `app.h` / `app.cpp` | `TVMailApp`: menus, status line, commands, Window list, Out of office, Move, Purge, Empty trash, background pull, idle ticks |
  | `mailwin.cpp` | mail data + the 3-pane window, driven through `mailFocusedRow()`, `mailMarkFocused()`, `mailReload()`, `mailViewChanged()`, `mailSetWrap()` |
  | `compose.cpp`, `addrbook.cpp`, `parts.cpp`, `signature.cpp`, `help.cpp` | one window each, created through a factory (`openCompose()`, `newPartsWindow()`, ...) |
  | `dialogs.cpp`, `util.cpp`, `shell.cpp` | Find/Replace and save prompts; quoting, UTF-8 columns, clipboard, status flash; `--debug`/`--trace` and the sh plumbing |
  | `coprocess.h/.cpp` | `CoProcess`: one helper in `serve` mode over two pipes; `mailBackend()`, `chatBackend()` |
  | `chat.h/.cpp` | `ChatClient`: tvmail's side of `tvchat-backend` |

  Command ids start at `cmPull = 2000`; **chat owns 2100–2149**. The Window ▸ List dialog (Alt-0) lists every `TWindow` by title.
- **Python helpers** (`backend/`), plain `#!/usr/bin/env python3` scripts (not sh/python polyglots, as the first handoff said), Python ≥ 3.6, stdlib only. `tvmail-backend` (mail, ~2,250 lines) and `tvchat-backend` (chat). All protocol and format work lives there, never in C++.
- **`CoProcess`** forks `<program> serve` through the shell preamble, waits for a `0 5\nready` banner, sends one shell-quoted request line per call, reads `<status> <nbytes>\n` + body, polls 8 s for the first byte, restarts a dead child up to three times in a row, and bumps `generation()` on every successful start. Every pipe step retries on `EINTR` (bug #43). `quit`/EOF ends the child with no reply.
- **Idle work:** `TVMailApp::idle()` keeps a list of `IdleTick`s; it only posts a due tick's command (one per idle round - `putEvent` holds one event) and the work happens in `handleEvent`. The background F3 pull is reaped there too. **TO-DO item 14** (periodic pull, quiet "N new") should be another tick.
- **Windows/mingw build has no `fork()`:** `CoProcess::call()` returns false and `ChatClient::configured()` is false, so chat is off; say so in the menu (milestone 4). The mingw build has not been compiled since the split - nobody had the cross compiler at hand.
- **Credentials never live in the repo or a config file.**

## Architecture as built

```text
 tvmail (C++, Turbo Vision)
   |-- mailBackend() -- pipe ---> tvmail-backend serve     (mail, unchanged)
   '-- chatBackend() -- pipe ---> tvchat-backend serve     (stdlib Python)
         ^ ChatClient                |-- IrcAdapter -------- TLS socket --> irc.libera.chat:6697
                                     '-- DeltaChatAdapter -- (milestone 6: stdio JSON-RPC
                                                              --> deltachat-rpc-server --> cpbee)
```

- **`tvchat-backend`** runs only when a chat config exists (`$TVCHAT_CONF` or `~/.config/tvmail/tvchat.conf`). One `selectors` loop (`Loop`) multiplexes stdin, every socket/pipe, a timer heap on an injectable clock, and `run_in_thread()` - used only for `getaddrinfo`, the one call with no non-blocking form. Requests reply at once; connecting reports progress as `state` events. A broken config never kills the process before its banner: each verb that needs it answers status 1 with the reason.
- **Event queue:** normalized events with a monotonically increasing `seq`, **starting at the wall clock in milliseconds** so numbers keep climbing across a backend restart. `events --after SEQ` acknowledges and drops everything up to SEQ. Capped at 10,000 (oldest dropped).
- **`ChatClient` (C++)** starts the backend in the app constructor, then `sync()`s: drain `events` first, then `accounts` and `convs` (so no event can fall between snapshot and queue). It polls `events --after` every **250 ms while any account is connecting/connected, 2 s otherwise**, keeps accounts / conversations / unread counts, and notices a restart by `generation()` changing - then re-syncs. The `cmChatEvents` handler broadcasts each event to the desktop as **`cmChatEvent`** (`infoPtr` = `const ChatEvent *`) and a restart as **`cmChatReset`**; milestone 4's windows listen for those.
- **Shared chat model:** account; conversation (`id` = `ACCOUNT/target`, target lower-cased per the server's casemapping; kind `server`, `channel`, `direct`, `group`); contact (display separate from address); message (id, conversation, sender, timestamp, text, flags). Each IRC account has a **`server` conversation, target `*`** (MOTD, notices, SASL results). Conversations outlive a disconnect so their windows stay.

## Wire protocol

Same framing as the mail backend. Status 0 = success, 1 = user-facing error (body is the message), 2 = internal error (bad request, unknown verb). Reply bodies are TAB-separated lines; inside a field **TAB, newline, CR and backslash are escaped as `\t` `\n` `\r` `\\`** (backslash too, so a typed `\n` is not a newline). Nothing terminal-specific ever appears in a reply.

| Verb | Arguments | Reply body |
| --- | --- | --- |
| `accounts` | — | `id`, `protocol`, `state`, `display` |
| `connect` | `ACCOUNT` | empty; progress arrives as events |
| `disconnect` | `ACCOUNT` | empty |
| `convs` | `[ACCOUNT]` (all accounts if omitted) | `conv`, `kind`, `unread`, `title` |
| `join` | `ACCOUNT TARGET` | the conv id (channel, or a nick for a query) |
| `part` | `CONV` | empty (a `server` conv refuses) |
| `send` | `CONV TEXT` | the local message id; with `echo-message` the server's copy arrives as an event, else ours is inserted at once |
| `history` | `CONV [--before MSGID] [--limit N]` (default 50) | message lines, oldest first (this session's ring, 2,000 per conv) |
| `members` | `CONV` | `address`, `display`, `role` (owner, admin, op, halfop, voice, member), by rank then name |
| `mark-read` | `CONV [MSGID]` | empty; emits `unread CONV 0` |
| `events` | `[--after SEQ]` | event lines, oldest first |
| `ping` | — | `pong` |
| `quit` | — | closes connections (IRC QUIT), exits, no reply |

Debug sub-command, not allowed in `serve`: `tvchat-backend irc-tail ACCOUNT` prints live events in a terminal; typed lines go to the newest channel, `/join #x` and `/quit` work.

Message line: `msgid`, `conv`, `utc-iso8601`, `sender-address`, `sender-display`, `flags`, `text`. Flags: `s` self, `n` notice/system, `a` action (`/me`), `e` encrypted, `r` read. IRC msgids are the server's `msgid` tag, else `ACCOUNT:N`.

Event line: `seq`, `type`, then: `state ACCOUNT STATE DETAIL` (`connecting`, `connected`, `disconnected`, `error`) · `msg` + message line · `conv-new` / `conv-gone` + conversation line · `members CONV` · `topic CONV TEXT` · `unread CONV COUNT`.

## IRC adapter (milestone 2 - built)

Hand-written, stdlib `socket` + `ssl`, on the shared loop.

- **Transport:** non-blocking connect and TLS handshake; TLS on 6697 by default with verification on; `verify = false` or `cafile =` for a LAN server; `tls = false` (port then defaults to 6667). A 60 s timeout covers connect + TLS + registration.
- **Registration:** `CAP LS 302` (multi-line LS handled), `NICK`, `USER`; `CAP REQ` for whichever of `sasl server-time message-tags echo-message batch chathistory draft/chathistory away-notify account-tag multi-prefix labeled-response` the server offers; SASL PLAIN only with `sasl =` set and a password found (and PLAIN offered); `CAP END`. Nick in use → `nick_`. ISUPPORT `CASEMAPPING`, `CHANTYPES`, `PREFIX` read. A failed SASL is reported in the server window and registration continues.
- **Traffic:** PING answered; JOIN/PART/KICK/QUIT/NICK/MODE/TOPIC/332/353/366 keep members and topic; PRIVMSG/NOTICE with `server-time`, `msgid`, `/me`, CTCP VERSION answered, other CTCP ignored, STATUSMSG targets, mIRC colours stripped; numerics and MOTD go to the server conversation. A server `ERROR` that arrives with the hang-up is still read, so its reason reaches the `state` event.
- **Sending:** split at 400 bytes on a UTF-8 boundary, one line per newline.
- **Passwords:** `$TVCHAT_PASS_<ACCOUNT>` (e.g. `$TVCHAT_PASS_LIBERA`), then `~/.mu-tickets` (borrowed from the `tvmail-backend` installed beside it - chat may lean on mail, never the reverse), then `~/.netrc`. This is the mail backend's real order (environment first). No password with `sasl =` set → `connect` answers status 1 saying where to put it.

### Still to do in milestone 5

- **Keepalive and reconnect:** our own `PING` after 90 s of silence, dead after 30 s more; reconnect with backoff (2, 4, 8 … capped at 300 s), keep emitting `state`, rejoin open channels (`on_welcome` already rejoins every open channel conversation). `IrcAdapter._lost()` is the hook. Keep the account `connecting` during backoff so tvmail keeps its 250 ms poll.
- **History:** with `chathistory`, `history --before` issues `CHATHISTORY BEFORE` and returns the batch; fetch the latest 50 on join. Without it, the in-memory ring (as now).
- **Flood control:** at most one line per 500 ms outbound, queued.
- **Commands typed in a chat window:** `/me`, `/join`, `/part`, `/msg NICK TEXT`, `/nick`, `/topic`, `/quote RAW`; any other `/word` is an error event, not sent. Today `send` sends text as-is.

## Delta Chat adapter (milestone 6 - not started)

Do not reimplement Delta Chat. Spawn the official `deltachat-rpc-server` as a child of `tvchat-backend` and speak JSON-RPC 2.0 to it, one JSON object per line over stdin/stdout. The core handles Autocrypt/OpenPGP, encryption, IMAP IDLE, SMTP and its own database. Today `DeltaChatAdapter` is a placeholder whose `connect` answers "not supported yet".

- **Dependency:** `deltachat-rpc-server` is the one non-stdlib piece - a separate executable, not a Python import. Find it on `PATH` or via `rpc_server =`; if missing, the account reports `state … error` with an install hint and IRC keeps working. Document installation in `INSTALL.md` (check the current recommended method; a PyPI wheel and release binaries have both existed).
- **API names:** verify against the installed server version before coding (the `deltachat-rpc-client` Python package source is the best reference). Expected shape: account management (`add_account`, `set_config`, `configure`), `start_io`, chat list and message fetch, a text-send call, mark-noticed/seen, and a long-poll `get_next_event`.
- **Event pump:** exactly one `get_next_event` outstanding at all times, read by the same `selectors` loop (`Loop.watch` on the child's stdout). Translate incoming-message, chat-modified and connectivity events into the normalized events above.
- **Accounts directory:** `~/.local/share/tvmail/deltachat/` (respect `XDG_DATA_HOME`), mode 0700. Never touch the core's database directly.
- **Mailbox:** a dedicated address on cpbee, not the main inbox - the core moves chat messages into its own folder and may delete them from the server. `configure` with that address and its password (same lookup as IRC).
- **Mapping:** chat id → `conv` (`ACCOUNT/<chat id>`); contact email → `sender-address`, display name → `sender-display`; encrypted → flag `e`; groups `kind=group`, one-to-one `kind=direct`.
- **Out of scope for v1:** attachments (show `[attachment: NAME]`), verification QR codes, multi-device setup, invite links.

## UI in Turbo Vision (milestone 4 - next)

One window per conversation in the existing cyan style, reached through a new **Chat** menu and Window ▸ List.

- **Wiring that already exists:** `ChatClient::instance()` (`accounts()`, `convs()`, `unreadTotal()`, `request()`), the `cmChatEvent` / `cmChatReset` broadcasts, and `chatFields()` to split a reply line. A window sends verbs with `ChatClient::instance().request("send " + shq(conv) + " " + shq(text), body, status)` - `shq` quoting is understood by the backend's `shlex`.
- **Chat menu:** Connect… (pick an account), Disconnect, Conversations… (Alt-C), Join… (channel or nick/address), Leave, plus the account list with each state. Greyed out entirely when `ChatClient::configured()` is false (always on mingw).
- **`TConversationsWindow`:** a `TListViewer` of all conversations across accounts - protocol tag, title, unread count - modelled on `TFolderPane` (now in `mailwin.cpp`). Enter opens or focuses that conversation's window.
- **`TChatWindow`** (a `TWindow`): a scrolling log on top, a one-line `TFieldLine` input at the bottom. Derive the log from `TMailText` (`views.h`) - UTF-8 columns, wrapping and the `»` marker that `e2e_regress` checks. Enter sends; PgUp past the top requests `history --before`. On open: `history CONV`; then apply `msg` events for its conv; on `cmChatReset`, re-read history.
- **Log line format:** `HH:MM <nick> text`, `* nick action` for `/me`, system lines (flag `n`) dimmed, own lines (flag `s`) in a distinct colour, `[e]` for encrypted Delta Chat messages. Every colour must keep the ≥ 3:1 contrast `e2e_regress` enforces.
- **Title and unread:** `#channel — Libera` or the contact's display name; unread in the Conversations list and in the title when unfocused. Focusing a window sends `mark-read`.
- **Status line:** a quiet `N new` (from `unreadTotal()`) in the spot TO-DO 14 plans for mail; never a modal box.
- **Window List:** check `collectWindow` / `WinEntry` in `app.cpp` show chat windows with their titles.
- **Unsaved input:** text in the input line makes close / Alt-X ask, as the compose window does (`valid(cmClose|cmQuit)`).
- **Done when** Chris can hold a conversation in his channel from tvmail, and `e2e_regress` cases cover opening a chat window, Esc closing it, and UTF-8 log columns - pointed at a fake IRC server via `$TVCHAT_CONF` (`tests/e2e_chat.py`'s `FakeIrc` is a starting point).

## Configuration and credentials

`~/.config/tvmail/tvchat.conf` (INI, `$TVCHAT_CONF` overrides; no file = no chat, no co-process). Shipped as `configure/tvchat.conf.example`, installed with the docs. No interpolation, so `%` is literal. `[account:NAME]`: NAME may not contain `/`, `\` or whitespace; `protocol = irc | deltachat` required; `display =` defaults to NAME. `[chat] autoconnect` must name existing accounts; they connect when `serve` starts.

```ini
[chat]
autoconnect = libera

[account:libera]
protocol = irc
display  = Libera
host     = irc.libera.chat
port     = 6697
tls      = true
verify   = true
#cafile  = /etc/ssl/certs/my-ircd.pem
nick     = yournick
realname = Your Name
sasl     = yournick          # omit to connect unauthenticated
channels = #tvmail-test

[account:delta]
protocol   = deltachat
display    = Delta Chat
addr       = chat@example.org
rpc_server = deltachat-rpc-server
```

Values are placeholders - the nick, channel and Delta Chat address are Chris's to choose. IRC history is not persisted to disk in v1; Delta Chat's lives in the core's database.

## Testing

Eight layers under `./test.sh` (see `tests/README.md`); no test needs network access or a real account.

- **`chat_unit`** (`tests/test_chat_backend.py`, 53 cases): config, escaping, event queue, the loop and its timers on a fake clock, `serve` framing; the IRC parser, colour stripping, UTF-8 splitting; a scripted **fake IRC server** on 127.0.0.1 (CAP LS 302, SASL PLAIN and its failure, nick-in-use, NAMES/TOPIC, server-time, echo-message, PING, queries, part, ERROR + hang-up); TLS with a `cafile` and a refused untrusted cert (`openssl` makes a throwaway cert; skipped without it); autoconnect through a real `serve` process.
- **`backend_pipe`** (`tvmail --selftest`): the mail pipe and `spawnLogged`, then `tvchat-backend` (start, `accounts` from `tests/tvchat-selftest.conf`, a poll, `ping`). Both lines are required.
- **`e2e_chat`** (`tests/e2e_chat.py`): tvmail in a pty against a fake IRC server - the backend is polled while idle (a message sent 1.5 s after the join arrives, seen in `--debug`), a killed `tvchat-backend` is restarted and re-read, Alt-X makes it QUIT.
- **`e2e_regress`** case 43 (bug #43) guards the `EINTR` fix.
- Still to add: a fake Delta Chat RPC server (a stub speaking JSON-RPC lines), plus a real-binary test that exits 77 when `deltachat-rpc-server` is not installed.
- **Conventions:** stdlib only in Python; Python ≥ 3.6 syntax (no walrus, no `match`, no f-strings so far); C++17 across `src/`; comments in the existing explanatory style; no credentials in code, tests or examples; every squashed bug gets a `BUGS.md` entry (next free number: check the whole file - both tables) and a regression case.
- **Builds:** Linux, the Pi, macOS and Cygwin; mingw compiles with chat disabled (unverified since the split).

## Bugs found along the way

- **#43 (fixed, 62347cf):** a signal during a backend round trip - `SIGWINCH` from any terminal resize - made `poll()`/`fgetc()` fail with `EINTR`, which was taken for a dead backend: the call fell back to the slow shell path, and during a start the co-process was given up for the session. Pre-dates chat; found by `e2e_chat` flaking.

## Open questions for Chris

- [ ] IRC nick, whether it is registered with Libera's NickServ (for SASL), and which test channel - needed for milestone 2's live check and milestone 4's "done when".
- [x] ~~Split `src/tvmail.cpp`?~~ Yes (Oct 20) - done.
- [ ] Dedicated Delta Chat address on cpbee: which one, and is the mailbox created by mail-setup (a change in the `chrispollitt/POSIX` repo, not yet attached to the session) or by hand? (milestone 6)
- [ ] Should IRC logs persist to disk between sessions, or stay in memory as v1 specifies?
- [ ] Later, separate project: self-host an Ergo IRC server on cpbee, and how to keep it reachable if cpbee moves to a phone-tethered connection.

## Future wish list

Not part of this work, but it constrains the design: a later installable web app (PWA) that looks and feels like the terminal version. The `tvchat-backend` protocol stays free of anything terminal-specific (no escape codes, widths or colours in replies or events) so a browser front end can reuse it unchanged.

- **Browsers cannot open raw sockets**, so a web app needs a server-side WebSocket gateway relaying the same verbs and sequenced events to `tvchat-backend`.
- **Option A — the real app in a browser:** xterm.js plus a pty over WebSocket (ttyd-style). Identical look, almost no new code, but a server-side session and account per visitor - a hosting and security project.
- **Option B — a Turbo Vision look-alike:** a CSS character grid with the cyan desktop, menus and shadowed dialogs, talking to the gateway. More work, feels native, installable as a PWA.
- **IRC-only shortcut:** a self-hosted Ergo server accepts WebSocket clients directly; Delta Chat still needs the gateway.
- **Hosting:** static front end on GitHub Pages; the gateway on cpbee or a small VPS.
