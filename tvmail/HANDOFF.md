# tvmail chat add-on — Claude Code handoff

Started Oct 20, 2026 · @Chris Pollitt · updated Oct 21, 2026 after milestone 4 · Oct 20: milestones 1-3 merged to `main`, web hosting notes (cp1)

This is the living version of the original handoff: the plan, what has been
built, and the decisions made since. Where the code and this file disagree,
the code wins - and this file should be fixed.

## Status

| # | Milestone | State | Commit |
| --- | --- | --- | --- |
| 1 | Backend skeleton | done | 050add8 |
| 2 | IRC from the command line | done; live check passed on Chris's machine (`tvchat-backend irc-tail libera`) | 78e5e45 |
| – | Split `src/tvmail.cpp` (approved by Chris) | done | 73060b6 |
| 3 | C++ plumbing | done | 62347cf |
| 4 | Chat windows | done, waiting for Chris's live check in `#tvmail-test` | 611efde |
| – | mingw compiles again (bug #45) | done | 1e43880 |
| 5 | IRC hardening | **next** | |
| 6 | Delta Chat adapter | | |
| 7 | Docs | | |

Milestones 1-3 were built on branch `ccr-b4320bfb-x90pzx`, merged into `main` (fast-forward to 69d442c) and the branch deleted on Oct 20. Milestone 4 was built on branch `claude/sweet-thompson-nzz0zz` from `main` at cee1cc5 (Oct 21); `./test.sh` passes all 9 layers on Linux (`mingw_compile` is new, see Testing) - not yet run on Cygwin. The last pre-chat state is tagged `tvmail-v1.2.0`.
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
  | `chatwin.cpp` | the chat UI: the Chat menu, `TConversationsWindow`, `TChatWindow` (milestone 4) |

  Command ids start at `cmPull = 2000`; **chat owns 2100–2149**. The Window ▸ List dialog (Alt-0) lists every `TWindow` by title.
- **Python helpers** (`backend/`), plain `#!/usr/bin/env python3` scripts (not sh/python polyglots, as the first handoff said), Python ≥ 3.6, stdlib only. `tvmail-backend` (mail, ~2,250 lines) and `tvchat-backend` (chat). All protocol and format work lives there, never in C++.
- **`CoProcess`** forks `<program> serve` through the shell preamble, waits for a `0 5\nready` banner, sends one shell-quoted request line per call, reads `<status> <nbytes>\n` + body, polls 8 s for the first byte, restarts a dead child up to three times in a row, and bumps `generation()` on every successful start. Every pipe step retries on `EINTR` (bug #43). `quit`/EOF ends the child with no reply.
- **Idle work:** `TVMailApp::idle()` keeps a list of `IdleTick`s; it only posts a due tick's command (one per idle round - `putEvent` holds one event) and the work happens in `handleEvent`. The background F3 pull is reaped there too. **Nothing is posted while a menu or dialog is up** (`TopView() != this`): any command ends a tvision menu, so the 250 ms chat poll shut menus at once (bug #44), and a dialog swallows commands it does not know. Chat events wait in the backend's queue meanwhile (Chris: no polling under a dialog is fine). **TO-DO item 14** (periodic pull, quiet "N new") should be another tick; chat's "N new" already sits at the right end of the status line.
- **Windows/mingw build has no `fork()`:** `CoProcess::call()` returns false and `ChatClient::configured()` is false, so chat is off and the Chat menu says so, greyed. It did not compile after all (`setenv()`, bug #45, pre-chat); fixed, and the `mingw_compile` test layer now syntax-checks every source with the mingw-w64 cross g++ (skipped where there is none - Cygwin's `mingw64-x86_64-gcc-g++` provides it). A full cross build with `build.sh --mingw`'s cmake settings also links (on Linux, Oct 21); the `.exe` has not been run on Windows since the split.
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

Debug sub-command, not allowed in `serve`: `tvchat-backend irc-tail [--raw] ACCOUNT` prints live events in a terminal (`--raw`: also every IRC line, `<<` in and `>>` out); typed lines go to the newest channel, `/join #x` and `/quit` work.

### Diagnostics

- **`tvchat-backend --debug [FILE]`** (default `debug.log`; before the sub-command, as with `tvmail-backend`) logs every `serve` request with its status, size and time (an `events` poll only when it brought something), account state changes, every IRC line in (`ACCOUNT << ...`) and out (`ACCOUNT >> ...`, SASL `AUTHENTICATE` payloads as `<redacted>`), the connect address, the TLS version and cipher, and why a connection ended - phase, error, seconds up, seconds since the server last sent anything, bytes in / out / unsent. `--trace [FILE]` writes cProfile data on exit.
- **`tvmail --debug FILE`** passes it on: the chat backend logs to `FILE.chat.ext` (`debug.log` -> `debug.chat.log`), the mail backend to `debug.backend.log` as before (`CoProcess`'s diagnostics tag; `backendFlags(tag)` in `shell.cpp`). tvmail's own log gets one line per chat request (status, size, ms) besides the events it applies. `$TVCHAT_BACKEND_LOG` still names a file for the backend's stderr.
- **A handler bug is not fatal:** an exception in an IRC line's handler is logged with its traceback and the next line is read; any loop callback (socket, timer, DNS result) that raises is logged and the loop carries on - before, it ended `serve` and with it every account's connection.
- Added Oct 21 to chase Chris's first live check: `Connection reset by peer` right after his first `send` in `#tvmail-test`, with no `ERROR` line from Libera first.

Message line: `msgid`, `conv`, `utc-iso8601`, `sender-address`, `sender-display`, `flags`, `text`. Flags: `s` self, `n` notice/system, `a` action (`/me`), `e` encrypted, `r` read. IRC msgids are the server's `msgid` tag, else `ACCOUNT:N`.

Event line: `seq`, `type`, then: `state ACCOUNT STATE DETAIL` (`connecting`, `connected`, `disconnected`, `error`) · `msg` + message line · `conv-new` / `conv-gone` + conversation line · `members CONV` · `topic CONV TEXT` · `unread CONV COUNT`.

## IRC adapter (milestone 2 - built)

Hand-written, stdlib `socket` + `ssl`, on the shared loop.

- **Transport:** non-blocking connect and TLS handshake; TLS on 6697 by default with verification on; `verify = false` or `cafile =` for a LAN server; `tls = false` (port then defaults to 6667). A 60 s timeout covers connect + TLS + registration.
- **Registration:** `CAP LS 302` (multi-line LS handled), `NICK`, `USER`; `CAP REQ` for whichever of `sasl server-time message-tags echo-message batch chathistory draft/chathistory away-notify account-tag multi-prefix labeled-response` the server offers; SASL PLAIN only with `sasl =` set and a password found (and PLAIN offered); `CAP END`. Nick in use → `nick_`. ISUPPORT `CASEMAPPING`, `CHANTYPES`, `PREFIX` read. A failed SASL is reported in the server window and registration continues.
- **Traffic:** PING answered; JOIN/PART/KICK/QUIT/NICK/MODE/TOPIC/332/353/366 keep members and topic; PRIVMSG/NOTICE with `server-time`, `msgid`, `/me`, CTCP VERSION answered, other CTCP ignored, STATUSMSG targets, mIRC colours stripped; numerics and MOTD go to the server conversation. A server `ERROR` that arrives with the hang-up is still read, so its reason reaches the `state` event.
- **Sending:** split at 400 bytes on a UTF-8 boundary, one line per newline.
- **Passwords:** `$TVCHAT_PASS_<ACCOUNT>` (e.g. `$TVCHAT_PASS_LIBERA`), then `~/.mu-tickets` (borrowed from the `tvmail-backend` installed beside it - chat may lean on mail, never the reverse), then `~/.netrc`. This is the mail backend's real order (environment first). In both files only an entry for the SASL login counts (bug #46: the mail lookup's any-user fallback sent the mail password); a failed login says where its password came from (`find_password_where()`, which `chat-setup`'s `configure-tvchat.sh` also calls). No password with `sasl =` set → `connect` answers status 1 saying where to put it.

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

## UI in Turbo Vision (milestone 4 - built)

One window per conversation in the existing cyan style, reached through a new **Chat** menu, Alt-C and Window ▸ List. All of it is `src/chatwin.cpp`; it talks to the backend only through `ChatClient` and learns of changes only from the `cmChatEvent` / `cmChatReset` broadcasts, so nothing in it knows the protocol.

- **Chat menu** (`Ch~a~t`, Alt-A - Alt-C is Conversations): Connect… (radio list of the accounts not connected; no dialog when there is one), Disconnect… (the same for live ones), Conversations… (Alt-C), Join…, Leave, then one item per account with its state on the right (`Libera   connected`); picking an account opens its server window, connecting it first if needed. The account items are rebuilt after every poll that brought a `state` event or a reset (never while a menu is open - see bug #44).
- **Greyed by hand:** tvision only tracks commands 0-255 (`commandEnabled()` is always true above), and every command-set change resets every menu item's `disabled` flag. `TVMailMenuBar` sets the chat items' flags before and after each event it sees; `chatCommand()` checks again, because Enter can still pick a greyed item. No `tvchat.conf` (or mingw, or a backend that stopped answering): everything greyed and a note saying why, e.g. `(no chat: no tvchat.conf)`. Leave is greyed unless a chat window is the current one.
- **Alt-C** is not a menu-bar key - the bar pre-processes keys and would take Alt-C from the compose window's Cc label (bug #11's case). `TVMailApp::handleEvent` takes it after the focused window had its turn.
- **`TConversationsWindow`** (one at a time; Alt-C again brings it forward): a `TListViewer` like `TFolderPane` - `[irc]  #tvmail-test — Libera  (3)`, server window first per account, then channels, groups, queries, by name. Enter or a double click opens or focuses the window. Esc closes. Re-read on every chat broadcast.
- **`TChatWindow`**: a `TChatLog` (a `TTextView`, so `TMailText`: UTF-8 columns, wrap (on by default; View ▸ Wrap long lines works from the input line too), Shift-select + Ctrl-Ins copy, Find) over `> ` and a `TFieldLine` (2,000 bytes). The input has the focus; typing in the log moves it there. Enter sends `send CONV TEXT`; a refused send (not connected, the server window) becomes a dim note in the log and the text stays. After a send the app polls at once, so your line shows without waiting for the tick. PgUp / PgDn / Up / Down / Ctrl-PgUp / Ctrl-PgDn scroll the log from the input; PgUp or Up at the top asks `history CONV --before OLDEST` (50 at a time) and keeps your place; "No older messages" on the status line when the ring is exhausted.
- **Opening:** `history CONV` (the last 50), then the channel topic as a dim line (`ChatConv::topic`, kept by `ChatClient` from `topic` events - `convs` does not carry it), then `msg` events for the conversation. History and the event queue overlap, so lines are kept once per msgid. `cmChatReset` reads the history afresh (a restarted backend's ring is all there is). `state` events of the account and `conv-gone` add a dim note (`-- disconnected: ...`).
- **Log lines:** `HH:MM <nick> text`, `HH:MM * nick action`, `HH:MM -nick- notice` (flag `n`, dim), `HH:MM -- note` for tvmail's own; `[e]` after the time for an encrypted message; continuation lines of a multi-line message indented under the text. Local time from the UTC stamp by hand (no `timegm` on every libc). Colours, all scored by `e2e_regress`: others black on cyan, own lines (flag `s`) blue on cyan (4.6:1), system lines `#444444` on cyan (3.4:1).
- **Title and unread:** `#channel — Libera`, the nick for a query, `Libera (server)`; `(N)` appended while it is not the window you are in - Window ▸ List shows the same title. Becoming the front window, or a message arriving while it is, sends `mark-read`; the count drops with the backend's `unread CONV 0` on the next poll.
- **Status line:** `N new` (`unreadTotal()`, server windows and your own lines never count) right-aligned on the status line, redrawn when the total changes; never a box. On a terminal narrower than the hints it covers the end of them.
- **Leave** parts the focused window's conversation (`part`), then closes the window. A server window refuses (the backend says why).
- **Unsent input:** Esc / Alt-F3 / Alt-X with text in the input asks "Discard the message you have not sent to …?" (Yes / No); No keeps the window and the text, and for Alt-X calls the quit off.
- **Not in milestone 4:** typed `/commands` are sent as text (milestone 5 adds `/me`, `/join`, `/msg`, …); no member list view (the `members` verb exists); a pasted line ending in a newline is pasted, not sent (tvision delivers a fast burst of keys as a paste); new queries do not open a window by themselves - they show in Conversations and in "N new".

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
# omit sasl to connect unauthenticated (no inline comments: they become part of the value)
sasl     = yournick
channels = #tvmail-test

[account:delta]
protocol   = deltachat
display    = Delta Chat
addr       = chat@example.org
rpc_server = deltachat-rpc-server
```

Values are placeholders - the nick, channel and Delta Chat address are Chris's to choose. IRC history is not persisted to disk in v1; Delta Chat's lives in the core's database.

## Testing

Nine layers under `./test.sh` (see `tests/README.md`); no test needs network access or a real account.

- **`chat_unit`** (`tests/test_chat_backend.py`, 58 cases): `--debug` (wire, state and end logged, SASL redacted; a handler bug logged with the connection alive; `serve` requests logged, empty polls not); config, escaping, event queue, the loop and its timers on a fake clock, `serve` framing; the IRC parser, colour stripping, UTF-8 splitting; a scripted **fake IRC server** on 127.0.0.1 (CAP LS 302, SASL PLAIN and its failure, nick-in-use, NAMES/TOPIC, server-time, echo-message, PING, queries, part, ERROR + hang-up); TLS with a `cafile` and a refused untrusted cert (`openssl` makes a throwaway cert; skipped without it); autoconnect through a real `serve` process.
- **`backend_pipe`** (`tvmail --selftest`): the mail pipe and `spawnLogged`, then `tvchat-backend` (start, `accounts` from `tests/tvchat-selftest.conf`, a poll, `ping`). Both lines are required.
- **`e2e_chat`** (`tests/e2e_chat.py`): tvmail in a pty against a fake IRC server - the backend is polled while idle (a message sent 1.5 s after the join arrives, seen in `--debug`); Alt-C, Enter opens the channel's window on that message; a line typed there reaches the server and the server's answer shows in the window, and both are in `debug.chat.log`; a killed `tvchat-backend` is restarted and re-read, the open window with it; Alt-X makes it QUIT.
- **`e2e_regress`** case 43 (bug #43) guards the `EINTR` fix; case 44 (bug #44) keeps menus open while chat polls. The chat UI cases (tagged `--`) use **`tests/fake_irc.py`**, a scripted IRC server (registers, confirms JOIN with a topic and NAMES, records what it is sent, says what the test hands it): `chat_window_opens_and_esc_closes` (history, topic, live message, `/me`, unread in the title / Window ▸ List / "N new", mark-read on focus, Esc closes, Alt-C brings the list back), `chat_log_utf8_columns` (CJK and two-byte nicks and text, aligned stamps, word wrap, no split characters), `chat_history_pgup`, `chat_join_and_leave`, `chat_unsent_input_asks`, `chat_colours_readable` (own / system / other in three colours, every cell >= 3:1) and `chat_menu_greyed_without_config`.
- **`mingw_compile`** (`tests/mingw_compile.sh`, new): every `src/*.cpp` syntax-checked by the mingw-w64 cross g++, `-Wall -Wextra -Werror`, against the tvision headers; exits 77 (skipped) without the cross compiler. Guards bug #45.
- Still to add: a fake Delta Chat RPC server (a stub speaking JSON-RPC lines), plus a real-binary test that exits 77 when `deltachat-rpc-server` is not installed.
- **Conventions:** stdlib only in Python; Python ≥ 3.6 syntax (no walrus, no `match`, no f-strings so far); C++17 across `src/`; comments in the existing explanatory style; no credentials in code, tests or examples; every squashed bug gets a `BUGS.md` entry (next free number: check the whole file - both tables) and a regression case.
- **Builds:** Linux, the Pi, macOS and Cygwin; mingw compiles with chat disabled (`mingw_compile`, and a full cross build linked on Oct 21). `-Wall -Wextra` is clean on Linux and mingw (pass `-DCMAKE_CXX_FLAGS="-Wall -Wextra"`; the CMakeLists does not set it).
- **`mail_setup`** runs the suites of whatever `chrispollitt/POSIX` checkout `third_party.sh` fetched. On Oct 21 a fresh clone failed one of its shell checks ("sasl_passwd is mode 600": `configure-sendmail-relay.sh` now leaves it 0640 for the pullers) - a mail-setup inconsistency, nothing in tvmail; fixed in POSIX 53a32b1 (the test and docs now expect 0640).

## Bugs found along the way

- **#46 (fixed):** chat SASL sent the mail password - `~/.mu-tickets`' `*://chris:...@cpbee` matched before the right `~/.netrc` entry, because the borrowed mail lookup falls back to any user on the host. Found in the first live check on cpbee's Ergo (Oct 21), which otherwise passed: no reset after "hello" there.
- **#44 (fixed, 611efde):** with an account connected, every menu closed by itself within 250 ms - `idle()` posted the chat poll as a command, and any command ends a tvision menu. `idle()` now posts nothing while `TopView()` is not the app (a menu or a dialog is up); the backend queue holds the events. Found building the Chat menu.
- **#45 (fixed, 1e43880):** the mingw build did not compile - `main()`'s `setenv("ESCDELAY", ...)` (668ea6b, pre-chat). Now POSIX only (ESCDELAY is an ncurses setting; the Windows build reads the Win32 console). Found by the first mingw compile since the split.
- **#43 (fixed, 62347cf):** a signal during a backend round trip - `SIGWINCH` from any terminal resize - made `poll()`/`fgetc()` fail with `EINTR`, which was taken for a dead backend: the call fell back to the slow shell path, and during a start the co-process was given up for the session. Pre-dates chat; found by `e2e_chat` flaking.

## Open questions for Chris

- [x] ~~IRC nick, NickServ registration, test channel?~~ Libera.Chat, nick `chris-pollitt`, registered (SASL user `chris-pollitt`), channel `#tvmail-test`, autoconnect on (Oct 21). His `tvchat.conf` and SASL password live on his machine only; `irc-tail libera` works there. Milestone 4's live check (a conversation in `#tvmail-test` from tvmail) is his.
- [x] ~~Split `src/tvmail.cpp`?~~ Yes (Oct 20) - done.
- [ ] Dedicated Delta Chat address on cpbee: which one, and is the mailbox created by mail-setup (a change in the `chrispollitt/POSIX` repo, not yet attached to the session) or by hand? (milestone 6)
- [x] ~~Should IRC logs persist to disk between sessions?~~ No - in memory for v1 (Oct 21). The window's log is the backend's ring (2,000 per conversation); gone when tvmail exits. Worth asking again if an always-on backend (the web app) ever happens.
- [x] ~~Must chat keep polling while a modal dialog is open?~~ No (Oct 21): `cmChatEvents` is simply not posted under a menu or dialog (bug #44); nothing is lost - the backend queue holds every event until the next `events --after`.
- [ ] Later, separate project: self-host an Ergo IRC server on cpbee, and how to keep it reachable if cpbee moves to a phone-tethered connection.

## Future wish list

Not part of this work, but it constrains the design: a later installable web app (PWA) that looks and feels like the terminal version. The `tvchat-backend` protocol stays free of anything terminal-specific (no escape codes, widths or colours in replies or events) so a browser front end can reuse it unchanged.

- **Browsers cannot open raw sockets**, so a web app needs a server-side WebSocket gateway relaying the same verbs and sequenced events to `tvchat-backend`.
- **Option A — the real app in a browser:** xterm.js plus a pty over WebSocket (ttyd-style). Identical look, almost no new code, but a server-side session and account per visitor - a hosting and security project.
- **Option B — a Turbo Vision look-alike:** a CSS character grid with the cyan desktop, menus and shadowed dialogs, talking to the gateway. More work, feels native, installable as a PWA.
- **IRC-only shortcut:** a self-hosted Ergo server accepts WebSocket clients directly; Delta Chat still needs the gateway.
- **Hosting:** static front end on GitHub Pages or cp1; the gateway (with `tvchat-backend` and `tvmail-backend` behind it) on cpbee or a small VPS - **not cp1**, see below.
- **What the server needs:** the backends running permanently (one per logged-in user, holding that user's connections and credentials), a small WebSocket gateway (login, spawn, relay), a reverse proxy (nginx or Caddy: TLS via Let's Encrypt, WebSocket upgrade, rate limiting) and service units. On cpbee that also means an inbound 443 in the nftables input chain (policy drop today), and a tunnel (Cloudflare Tunnel, Tailscale Funnel) or a VPS relay if cpbee loses its public address. It is a public login page in front of mail and chat credentials - auth, TLS only, lockout. An always-on backend is also an IRC bouncer, which argues for persisting IRC logs (open question above). Server-side setup would be a new tool in `chrispollitt/POSIX` beside `mail-setup` (`web-setup`, or `chat-setup` if it also covers Ergo - both need the port, the certificate and the reachability work).

### cp1 (u-l.ca, the cPanel webhost) - checked Oct 20, 2026

cp1 can serve the static front end but cannot host the gateway or the backends:

| Check | Result |
| --- | --- |
| Platform | CloudLinux, kernel 5.14 el9; shared cPanel account `whatwelo` |
| Outbound IRC, irc.libera.chat 6697 / 6667 | **refused** over IPv4 (IPv6: network unreachable) - the host's outbound firewall |
| Outbound to irc.libera.chat 443 | times out (Libera has no IRC on 443 anyway) |
| Outbound HTTPS (www.google.com 443) | ok - only non-web ports are blocked |
| Python | 3.7 default; 2.7, 3.3-3.13 via `cloudlinux-selector` |
| App hosting | CloudLinux selector (Passenger), Node 6-22 - request-driven apps that are stopped when idle, not daemons; per-account LVE limits |

So even with the IRC ports opened by the host, a permanently connected backend fights Passenger's idle shutdown and the LVE limits, and the credentials would sit on a shared server.

Non-interactive `ssh whatwelove.org` sits behind a per-session MFA gate: until Chris approves a fresh token every command prints only `token: Error` (exit status 0) and runs nothing. Claude does not generate or enter the code.
