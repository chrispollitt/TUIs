# tvchat — Claude Code handoff

Started Oct 20, 2026 as tvmail's chat add-on · @Chris Pollitt · updated Oct 21, 2026 (night): chat split out of tvmail into its own app, tvchat (branch `split-tvchat`), with the shared code in `../tvcommon` · earlier Oct 21: milestone 4 + the 3-pane chat window merged to `main`, Ergo on cpbee, bug #46 · Oct 20: milestones 1-3 merged to `main`, web hosting notes (cp1)

**The split (Oct 21, Chris):** "these need to be separate apps: tvmail and tvchat". tvchat lives in `TUIs/tvchat/`; what both apps use is `TUIs/tvcommon/` (`src/`: views.h, util, shell, coprocess, dialogs, cli, tvapp - the application base -, textwin; `tvcommon.cmake`; `tests/tvscreen.py`, the pty + pyte harness; `third_party.sh`, which fetches Turbo Vision (patched) and chrispollitt/POSIX's mail-setup + chat-setup once into `tvcommon/third_party/`). This reverses "extend tvmail, not a new app" below. Much of this file still describes the code as it was inside tvmail (`tvmail.h`, `app.cpp`, "tvmail polls ..."): read tvmail there as "the app" - now tvchat (`src/tvchat.h`, `src/app.cpp`, `TVChatApp`). Config: `~/.config/tvchat/tvchat.conf`, falling back to the old `~/.config/tvmail/tvchat.conf`. tvchat-backend no longer imports tvmail-backend (its own `~/.mu-tickets` reader). tvchat's bugs: `BUGS.md` (from #1; tvmail #44 and #46 listed there).

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
| 4 | Chat windows | done; live check passed on cpbee's Ergo (Oct 21; Libera's reset after a send not seen again, not retested) | 611efde |
| – | Bug #46: SASL got the mail password from `~/.mu-tickets` | fixed | b97d83c |
| – | Chat as one 3-pane window like mail (asked by Chris, Oct 21) | done; Chris tried it and loves it | a945288 |
| – | Split into its own app, tvchat, with `../tvcommon` (asked by Chris, Oct 21) | done; independence checks passed; fast-forwarded into `main` and pushed Oct 22 | 4dcfd2a (tvcommon), 9d39d6e (the split) |
| – | mingw compiles again (bug #45) | done | 1e43880 |
| 5 | IRC hardening | done; live check passed on cpbee's Ergo (Chris and miche in #test, Oct 22); merged | ac53da8 |
| 6 | talk(1) adapter (asked by Chris, Oct 22) | **next** | |
| 7 | Delta Chat adapter | | |
| 8 | Docs | | |

Milestones 1-3 were built on branch `ccr-b4320bfb-x90pzx`, merged into `main` (fast-forward to 69d442c) and the branch deleted on Oct 20. Milestone 4 was built on branch `claude/sweet-thompson-nzz0zz` from `main` at cee1cc5 (Oct 21); with the diagnostics, bug #46 and the 3-pane window on top it was fast-forwarded into `main` (a945288), pushed, and the branch deleted on Oct 21. All 9 test layers pass on Cygwin and Linux. The last pre-chat state is tagged `tvmail-v1.2.0`.
Stop for Chris's review after each milestone.

### Where we stopped (Oct 21, evening)

- **Installed on cmlaptop** (`/usr/local/bin`): the `main` build - tvmail with the 3-pane chat window, and `tvchat-backend` with bug #46's fix.
- **cpbee runs Ergo 2.19.1** (`chrispollitt/POSIX` `chat-setup/scripts/configure-ergo.sh`): server `cpbee.cmnet`, TLS on 192.168.10.1:6697, NickServ accounts `chris-pollitt` and `tester2`. The laptop's `tvchat.conf` has `[account:cpbee]` (written by `chat-setup/scripts/configure-tvchat.sh`; not in `autoconnect`), its password in `~/.netrc`. `configure-ergo.sh` has not been re-run on cpbee since it started publishing `/opt/ergo/ca.pem` (POSIX bb1cc79) - the laptop got its CA another way.
- **Live checks:** on cpbee, connect, SASL, join `#test` and a sent line all work - no reset. Libera connects and SASL works, but **the `Connection reset by peer` right after a send there has not been retested**: send one line in `#tvmail-test` with `tvmail --debug` and read `debug.chat.log` (the end-of-connection line says phase, error and bytes unsent).
- **Next (as of the 3-pane window):** Chris tried it (loves it); the Libera reset is gone (`#tvmail-test` works).

### Where we stopped (Oct 21, night): the split

- Branch **`split-tvchat`** (from `main` 38266d7): 4dcfd2a extracted `tvcommon/` (all 9 tvmail layers passed); the next commit moves chat into `tvchat/` and takes it out of tvmail. tvchat: 5 layers (chat_unit, backend_pipe, e2e_chat, e2e_regress - the chat cases plus #43 and #44 -, install_roundtrip) pass on Cygwin.
- **Ready to merge (Oct 22):** POSIX 2a8103b (committed, not pushed): `configure-tvchat.sh` writes `~/.config/tvchat/` and copies an old `~/.config/tvmail/tvchat.conf` there first (chat-setup tests: 76 pass). On `split-tvchat`: tvchat's own `tests/README.md`; tvmail's test README and man page lose their chat parts; this file moved to the top of the repo. Independence checks passed: tvchat built and tested from a copy of TUIs without `tvmail/` (5/5); `dist.sh src` tarballs of tvchat (5/5) and tvmail (7/7; `e2e_regress` #4 `source_draft_found_by_content` flaked once, then passed 5 times in a row) each build and test alone. Both apps are installed on cmlaptop (fixperms run; the pre-split `share/doc/tvmail/tvchat.conf.example` removed). Chris's `tvchat.conf` is still in `~/.config/tvmail/` (read through the fallback; the next `configure-tvchat.sh` run copies it). **Merged (Oct 22, Chris approved):** `split-tvchat` fast-forwarded into `main`, the branch deleted, TUIs and POSIX pushed.
- `tvcommon/third_party/POSIX.stale-20261021`: Chris's old mail-setup checkout (13 commits that were all rebased into POSIX already; clean tree) - safe to delete.
- **Then:** milestone 5 (IRC hardening), in `tvchat/` - built Oct 22 on branch `m5-irc-hardening` (see its section below); stopped for Chris's review and live check. Next after that: milestone 6, talk(1).

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
  | `views.h` | views several windows share: `TMailText` (the chat log derives from it), `TTextView`, `TDivider` + `TPaneTitle` (the 3-pane rules and headings), `TViewerWindow`, `TMailViewWindow`, `TBodyEditor`, `TFieldLine` |
  | `main.cpp` | command line, `--selftest`, `main()` |
  | `app.h` / `app.cpp` | `TVMailApp`: menus, status line, commands, Window list, Out of office, Move, Purge, Empty trash, background pull, idle ticks |
  | `mailwin.cpp` | mail data + the 3-pane window, driven through `mailFocusedRow()`, `mailMarkFocused()`, `mailReload()`, `mailViewChanged()`, `mailSetWrap()` |
  | `compose.cpp`, `addrbook.cpp`, `parts.cpp`, `signature.cpp`, `help.cpp` | one window each, created through a factory (`openCompose()`, `newPartsWindow()`, ...) |
  | `dialogs.cpp`, `util.cpp`, `shell.cpp` | Find/Replace and save prompts; quoting, UTF-8 columns, clipboard, status flash; `--debug`/`--trace` and the sh plumbing |
  | `coprocess.h/.cpp` | `CoProcess`: one helper in `serve` mode over two pipes; `mailBackend()`, `chatBackend()` |
  | `chat.h/.cpp` | `ChatClient`: tvmail's side of `tvchat-backend` |
  | `chatwin.cpp` | the chat UI: the Chat menu and `TChatMainWindow`, the 3-pane chat window (milestone 4) |

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
                                     '-- DeltaChatAdapter -- (milestone 7: stdio JSON-RPC
                                                              --> deltachat-rpc-server --> cpbee)
```

- **`tvchat-backend`** runs only when a chat config exists (`$TVCHAT_CONF` or `~/.config/tvmail/tvchat.conf`). One `selectors` loop (`Loop`) multiplexes stdin, every socket/pipe, a timer heap on an injectable clock, and `run_in_thread()` - used only for `getaddrinfo`, the one call with no non-blocking form. Requests reply at once; connecting reports progress as `state` events. A broken config never kills the process before its banner: each verb that needs it answers status 1 with the reason.
- **Event queue:** normalized events with a monotonically increasing `seq`, **starting at the wall clock in milliseconds** so numbers keep climbing across a backend restart. `events --after SEQ` acknowledges and drops everything up to SEQ. Capped at 10,000 (oldest dropped).
- **`ChatClient` (C++)** starts the backend in the app constructor, then `sync()`s: drain `events` first, then `accounts` and `convs` (so no event can fall between snapshot and queue). It polls `events --after` every **250 ms while any account is connecting/connected, 2 s otherwise**, keeps accounts / conversations / unread counts, and notices a restart by `generation()` changing - then re-syncs. The `cmChatEvents` handler broadcasts each event to the desktop as **`cmChatEvent`** (`infoPtr` = `const ChatEvent *`) and a restart as **`cmChatReset`**; milestone 4's windows listen for those.
- **Shared chat model:** account; conversation (`id` = `ACCOUNT/target`, target lower-cased per the server's casemapping; kind `server`, `channel`, `direct`, `group`); contact (display separate from address); message (id, conversation, sender, timestamp, text, flags). Each IRC account has a **`server` conversation, target `*`** (MOTD, notices, SASL results). Conversations outlive a disconnect so they stay listed.

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

### Milestone 5 - IRC hardening (built Oct 22, branch `m5-irc-hardening`)

All in `IrcAdapter` (`tvchat/backend/tvchat-backend`); the tunables are class attributes (`PING_IDLE`, `RETRY_FIRST`, `BURST`, ...) so tests shrink them.

- **Keepalive:** `_keepalive()` (a timer while `up`): our `PING :tvchat` after `PING_IDLE` = 90 s without a byte from the server; any byte in clears it; nothing within `PING_GRACE` = 30 s more → `_lost("no reply from the server for N s")`.
- **Reconnect:** `_lost()` of a connection that was `up` (or of a reconnect attempt) → `_retry()`: 2, 4, 8 … 300 s (`attempt` resets once a connection had stayed up `RETRY_STABLE` = 60 s), a note in the server window and `state ACCOUNT connecting "WHY - reconnecting in N s"` - so the UI keeps its 250 ms poll and lists it under Disconnect. `_fail()` during reconnecting retries too. A *first* connect that fails or is hung up on is **not** retried (error / disconnected, as before - bad host, cert, ...). `disconnect` (Chat > Disconnect, quit) cancels the wait. `on_welcome` rejoins the open channels (as before) and notes "reconnected". A password gone missing stops the retries with `error`. Timers of a dead connection are no-ops (`_later()` checks `gen`); `retry_timer`/`attempt`/`auto` live outside `_reset()`.
- **Flood control:** once `up`, `send_raw()` queues (`outq`) behind a token bucket - a burst of `BURST` = 5, then one per `RATE` = 0.5 s (`_pump()`); PONG, our PING and QUIT are `urgent` and skip it; registration is never paced. Disconnect sends what is queued, then QUIT. Lines still queued when a connection is lost are dropped with a note in the server window ("N lines not sent").
- **Typed commands** (`send`): `/me`, `/join #CHAN [KEY]`, `/part [#CHAN] [REASON]`, `/msg NICK TEXT` (opens the conversation), `/nick`, `/topic [TEXT]` (none: shows it), `/quote` (alias `/raw`); `//x` sends `/x`. The whole text is checked first: an unknown `/word`, a bad argument or a plain line in the server window refuses it all (status 1, nothing sent) - the UI then shows "not sent: ..." and keeps the text. The server window now takes `/` lines (C++ `sendInput`). Reply: the last message's id, `""` for commands only.
- **Server history (IRCv3 `chathistory` / `draft/chathistory` + `batch`):** our own JOIN → `CHATHISTORY LATEST #chan * 50` (capped by ISUPPORT `CHATHISTORY=`); opening a query with `join` too. `history --before` that reaches the start of the ring → `CHATHISTORY BEFORE target msgid=OLDEST 50`, answered with status 1 `asking the server for older messages...` (the UI flashes it and does not mark "no older"). `on_BATCH` merges a `chathistory` batch into the ring in time order, once per msgid, never unread, no `msg` events, then pushes the **new event `history CONV latest|before`**; the chat pane reloads (latest) or calls `loadOlder()` again (before). An empty `before` batch, or `FAIL CHATHISTORY ...`, sets `history_done` - after that `--before` is simply empty ("No older messages"). Without the caps: the ring, as before. Libera has no chathistory; Ergo does.
- **Tests:** `chat_unit` 67 (new `TestIrcHardening`: reconnect + rejoin against a fake that `reaccept()`s, backoff sequence, keepalive, flood pacing with PONG first, commands and refusals, CHATHISTORY latest/before/FAIL; `test_server_hangup_reconnects`, `test_first_connect_hung_up_on_is_not_retried`); `e2e_regress` `chat_typed_commands`.
- **Live check (Chris):** on cpbee's Ergo - `/me`, `/msg tester2 hi`, `/join #other` from the server messages; restart Ergo (or pull the cable) and watch "reconnecting in 2 s" → "reconnected" and `#test` rejoined; PgUp past the top in `#test` after a restart of tvchat (Ergo's history).
- **Not done (later):** `/commands` that make the UI switch to the new conversation (`/join`, `/msg` only list it); persisting nothing across tvchat restarts (unchanged); `labeled-response` is requested but unused.

## talk(1) adapter (milestone 6 - not started)

**Decided (Chris, Oct 22, after milestone 5):**
- **The view is the user's choice:** a talk conversation shows either
  *classic* (talk's split screen: you on top, them below) or *IRC mode*
  (the usual log). A setting, plus a toggle in the window.  **Both live in
  the chat pane of the three-pane window** (Chris): classic splits the
  bottom-right pane in two - theirs over yours - and Servers and
  Conversations stay where they are; no separate window.
  **Default: classic** (Chris, Oct 22) - it is what a talk caller expects,
  and what a real `talk` at the other end shows; `talk_view = classic | irc`
  in tvchat.conf changes it (per account), the toggle per conversation.
  Only talk conversations have the choice; IRC ones are always the log.
- **What is sent follows the view** (Chris, Oct 22): in *classic* every
  keystroke goes as typed (erase / kill / word-erase too), as real talk
  does; in *IRC mode* whole lines, on Enter.  What the other side types
  arrives as typed either way - IRC mode shows it once their line ends.
- **IRC mode shows "NAME is typing..."** (Chris, Oct 22) while the other
  side has an unfinished line - the keystrokes arrive anyway, so it is free.
- **The daemon first**, then the tvchat adapter and UI on top of it.

**The wire (checked against FreeBSD's `protocols/talkd.h` and
`libexec/talkd/process.c`, Oct 22):** UDP 518, `CTL_MSG` 84 bytes =
vers(1) type(1) answer(1) pad(1) id_num(4) addr(16) ctl_addr(16) pid(4)
l_name(12) r_name(12) r_tty(16); `CTL_RESPONSE` 24 bytes = vers type answer
pad id_num addr.  **Everything multi-byte is big-endian, including the
sockaddr's `sa_family`** (talkd does `ntohs(sa_family)` and checks AF_INET =
2); a sockaddr is the 4.3BSD 16 bytes: family(2) port(2) IPv4(4) zero(8).
Types LEAVE_INVITE 0, LOOK_UP 1, DELETE 2, ANNOUNCE 3; answers SUCCESS 0,
NOT_HERE 1, FAILED 2, MACHINE_UNKNOWN 3, PERMISSION_DENIED 4,
UNKNOWN_REQUEST 5, BADVERSION 6, BADADDR 7, BADCTLADDR 8; TALK_VERSION 1;
invitations live MAX_LIFE 60 s, callers re-announce every RING_WAIT 30 s.
On the TCP connection each side first writes its 3 edit characters (erase,
kill, word-erase), then text.

**Plan (in order, each stopped for review):**
1. **talkd core** - done Oct 22, POSIX (see its commit); interop checked
   against inetutils `talk` on Cygwin (two real clients rang, looked up,
   connected, talked, deleted). (`chrispollitt/POSIX` `chat-setup/talkd/`, stdlib Python,
   one `selectors` loop like tvchat-backend): the codec, the invitation
   table, the four requests, and the classic tty announcement for users
   logged in on cpbee (utmp) - a drop-in for the stock talkd.  Tests: a fake
   talk client over UDP on a high port.
2. **tvchat users + relay** - done Oct 22 (POSIX; the protocol is in
   talkd.py's docstring: HELLO / CALL / RING / ACCEPT / DECLINE / HANGUP /
   CONNECT + a second connection `ATTACH TOKEN` carrying the raw bytes;
   TCP 518).  Interop with inetutils `talk` both ways through the relay.
   Was: a small line protocol over TCP (LAN only, no
   auth - like talk itself): tvchat registers its user; an ANNOUNCE for a
   registered user becomes an invitation event there instead of a tty
   message; talkd itself connects to the caller's address and splices that
   stream to tvchat's, so laptops take no inbound connections.  Outgoing:
   tvchat asks talkd to call user[@host]; talkd does the LEAVE_INVITE /
   ANNOUNCE ritual (to itself or to another host's talkd) with its own
   relay port as the address.
3. **`configure-talkd.sh`** - done; **live check passed Oct 22**: Chris and
   miche, both ssh'd into cpbee, talked with Debian's `talk` (netkit
   0.17) through our talkd.  Three fixes on the way (POSIX e574cdd,
   9b29f81, 47db1f9): the script stopped silently under `set -e` (now an
   ERR trap names the line); talkd answered 127.0.1.1 requests from
   127.0.0.1, which Debian's talk (it connect()s) never saw - now
   IP_PKTINFO; Debian 13 has no /run/utmp (`who` uses logind) - talkd
   now reads the terminals' owners where utmp is missing.  Not yet run:
   `--open-firewall` (needed before tvchat on a laptop can reach TCP 518).
   cmpi-router's `setup-router.sh` (master copy on cmlaptop,
   ~/src/Claude/cmpi-router, not git; copied to cpbee) now reads
   `/etc/router-setup/lan0-udp-ports` (Chris chose this over a live rule).
   talkd gained `--relay-ports` (a firewall-listable range for callees on
   other hosts) and `--allow` (loopback + lan0's subnet).  Was: a `talkd` system user, a
   systemd unit (CAP_NET_BIND_SERVICE for 518), the stock talkd/inetd entry
   off, UDP 518 opened on lan0 (cmpi-router's port lists are TCP only today
   - the same lossless way as Ergo's).  Interop check on cpbee with
   Debian's `talk` package.
4. **tvchat** - built Oct 22 on branch `m6-talk-tvchat`, **awaiting Chris's
   review and a live check**:
   - *backend* (`TalkAdapter`): one control connection to talkd per talk
     account; one conversation per person (kind direct; talkd's own host
     dropped from the name).  `join` calls, new verb `answer CONV`, `part`
     hangs up / declines (no call: leaves).  `send` = whole lines (IRC
     mode), new verb `keys CONV TEXT` = keystrokes (classic; TEXT escaped
     like reply fields, since a request is one line).  The other side's
     erase / kill / word-erase are applied as bytes arrive: finished lines
     are `msg` (unread), the unfinished one the new `typing CONV TEXT`
     event; call state on the new `talk CONV STATE DETAIL` (calling,
     ringing, incoming, connected, ended).  `accounts` gains a 5th field
     (talk_view) and `convs` / `conv-new` a 5th (call state) - only for
     talk, IRC lines unchanged.
   - *window*: `TTalkView` (classic: you on top, a rule "miche - connected",
     them below; every key via `keys`) replaces log + input for a classic
     talk conversation; IRC mode is the usual log plus a dim "miche is
     typing..." line.  View > Talk view (Ctrl-T) per conversation; Chat >
     Answer (or Enter) answers; "- calling you" in the list, a status-line
     flash for a call.  Gotcha fixed on the way: hiding the input line made
     tvision move the focus to the server pane (`applyView` restores it).
   - *tests*: chat_unit +8 (a fake talkd; the real talkd when
     chat-setup/talkd/talkd.py is at hand), e2e_regress `talk_classic_call_in`,
     `talk_irc_mode_call_out_and_toggle` (tests/fake_talkd.py).  All 5 layers
     pass.
   - *live check (Chris)*: on cpbee `configure-talkd.sh --open-firewall`
     (TCP 518 for the laptops); an `[account:talk]` (host = cpbee) in each
     tvchat.conf; chris and miche call each other from tvchat, and a real
     `talk chris@cpbee` from a cpbee shell rings tvchat.
   - *not yet*: reconnecting a lost talkd connection by itself (IRC's
     backoff is not reused yet); ^L / redraw of the peer's screen.

Chris, Oct 22: "talk(1)!!! tvchat *must* have it" - before Delta Chat. The
Unix `talk` / `ntalk` protocol: two users, a split screen, every keystroke
sent as typed (not lines). A third protocol behind the same adapter
interface, in stdlib Python.

- **Wire:** the ntalk protocol (talkd on UDP 518; `talk` 517 is the old one):
  LOOK_UP / ANNOUNCE / LEAVE_INVITE / DELETE messages to the peer's talkd,
  then a direct TCP connection between the two clients carrying the edit
  characters (erase, kill, word-erase - exchanged first) and the text.
  Check against a real `talk`/`ytalk` and the BSD `talkd` sources before
  coding; byte order and the sockaddr layout in the control message are
  the classic traps.
- **Decided (Chris, Oct 22): one talkd, on the master (cpbee)** - "unconventional
  but we'll make it work".  Not the stock talkd (it announces to a tty in
  utmp, which tvchat is not - and over ssh it would scribble on tvchat's
  screen), but our own small daemon, set up like Ergo by a
  `chat-setup/scripts/configure-talkd.sh`:
  1. speaks standard ntalk on UDP 518, so a real `talk chris@cpbee` from any
     shell still works;
  2. tvchat registers its user with it, so an invitation arrives in tvchat
     as an event; a user without tvchat still gets the classic tty
     announcement;
  3. relays the session: it gives its own address as the rendezvous and
     splices the two TCP streams, so only cpbee takes inbound connections
     (no Windows-firewall holes on the laptops).
  Catches: UDP 518 needs a UDP port list in cmpi-router (lan0-tcp-ports is
  TCP only); no auth in talk, so LAN only, like Ergo.
- **Both directions:** `talk user[@host] [tty]` from tvchat (an invitation
  through the peer's talkd), and answering an invitation addressed to us
  ("Message from Talk_Daemon@host ... respond with: talk you@host") - which
  needs a local talkd (inetd/xinetd `talkd`, or a tiny one in the backend?)
  and a way to hear about invitations: the daemon writes them to the tty,
  so this needs thought.
- **The model:** a talk session is a `direct` conversation, but the chat
  pane is line-based. Options: show the peer's text as it is typed (a live
  line at the bottom of the log, committed on newline), or a talk-style
  split pane for talk conversations only. Decide with Chris.
- **Accounts:** `[account:talk] protocol = talk` with the local user name
  and which hosts to allow; no passwords (talk has none - LAN only).
- **Tests:** a fake talkd (UDP) and a fake peer (TCP) in the unit tests; an
  interop check against the system `talk` where installed (exits 77 if not).
- **Wish list, "crazy talk" (Chris):** a gateway to OpenVMS `PHONE` - e.g.
  on the SIMH VAX (cpvax). PHONE speaks DECnet task-to-task (object 29), so
  it needs DECnet on both sides; noted, not planned.

## Delta Chat adapter (milestone 7 - not started)

Do not reimplement Delta Chat. Spawn the official `deltachat-rpc-server` as a child of `tvchat-backend` and speak JSON-RPC 2.0 to it, one JSON object per line over stdin/stdout. The core handles Autocrypt/OpenPGP, encryption, IMAP IDLE, SMTP and its own database. Today `DeltaChatAdapter` is a placeholder whose `connect` answers "not supported yet".

- **Dependency:** `deltachat-rpc-server` is the one non-stdlib piece - a separate executable, not a Python import. Find it on `PATH` or via `rpc_server =`; if missing, the account reports `state … error` with an install hint and IRC keeps working. Document installation in `INSTALL.md` (check the current recommended method; a PyPI wheel and release binaries have both existed).
- **API names:** verify against the installed server version before coding (the `deltachat-rpc-client` Python package source is the best reference). Expected shape: account management (`add_account`, `set_config`, `configure`), `start_io`, chat list and message fetch, a text-send call, mark-noticed/seen, and a long-poll `get_next_event`.
- **Event pump:** exactly one `get_next_event` outstanding at all times, read by the same `selectors` loop (`Loop.watch` on the child's stdout). Translate incoming-message, chat-modified and connectivity events into the normalized events above.
- **Accounts directory:** `~/.local/share/tvmail/deltachat/` (respect `XDG_DATA_HOME`), mode 0700. Never touch the core's database directly.
- **Mailbox:** a dedicated address on cpbee, not the main inbox - the core moves chat messages into its own folder and may delete them from the server. `configure` with that address and its password (same lookup as IRC).
- **Mapping:** chat id → `conv` (`ACCOUNT/<chat id>`); contact email → `sender-address`, display name → `sender-display`; encrypted → flag `e`; groups `kind=group`, one-to-one `kind=direct`.
- **Out of scope for v1:** attachments (show `[attachment: NAME]`), verification QR codes, multi-device setup, invite links.

## UI in Turbo Vision (milestone 4 - built; 3-pane window Oct 21)

One full-size chat window laid out like the mail window, in the same cyan style, reached through the **Chat** menu, Alt-C and Window ▸ List (Chris, Oct 21: "the chat part UI to follow the same three pane UI that the mail part does" - it replaced milestone 4's Conversations list and one floating window per conversation). All of it is `src/chatwin.cpp`; it talks to the backend only through `ChatClient` and learns of changes only from the `cmChatEvent` / `cmChatReset` broadcasts, so nothing in it knows the protocol.

```text
┌──────────────────────────── tvmail chat ─────────────────────────────┐
│[ Servers ]─────────│  Conversations - cpbee  ───────────────────────│
│ Libera   connected │ * server messages                               │
│ cpbee (2) connected│ #test (2)                                       │
│                    │ tester2                                         │
│                    │──  #test — cpbee  ─────────────────────────────│
│                    │18:17 <tester2> hi                               │
│                    │18:17 <chris-pollitt> hello                      │
│                    │> _                                              │
└──────────────────────────────────────────────────────────────────────┘
 Alt-C Mail  Tab Pane  Enter Send  PgUp Older  ^N New mail  F6 Next  ...
```

- **`TChatMainWindow`** (`gChatWin`, made the first time it is opened, never closed; no close / zoom / move, like `TMailWindow`): **Servers** (`TServerPane`: display, `(N)` unread outside its server messages, state on the right) │ **Conversations** of the highlighted account (`TConvPane`: `* server messages` first, then channels, groups, direct talk by name, `(N)` unread) / **the chat pane**: a heading with the conversation's name (`#test — cpbee`, `cpbee - server messages`), the log, and `> ` + the input line. The three-pane pieces (`TDivider`, `TPaneTitle`) moved from `mailwin.cpp` to `views.h`; pane sizes follow `TMailWindow::paneRects()` (22 columns on the left, the list 2/5 of the height).
- **Moving:** as in mail, a list's highlight moving shows what it points at - an account shows its conversation list and the conversation last shown there (else its server messages); a conversation shows its log. Enter on a server connects it if it is not, and goes to its conversations; Enter on a conversation goes to the input. **Tab** cycles servers → conversations → input (the log is not a stop; clicking it selects text; Tab from it goes on as from the input). The heading of the focused pane is reverse-video, as in mail.
- **Alt-C** brings the chat window to the front, and from it goes back to mail (`showMailWindow()`); **Esc** goes back to mail too. Alt-C is not a menu-bar key - the bar pre-processes keys and would take Alt-C from the compose window's Cc label (bug #11's case). `TVMailApp::handleEvent` takes it after the focused window had its turn.
- **Status line:** the window has `helpCtx = hcChatWin` and its own `TStatusDef`: `Alt-C Mail  Tab Pane  Enter Send  PgUp Older  ^N New mail  F6 Next  Alt-X Exit  F1 Help` (Tab / Enter / PgUp are hints, `cmChatOff`). Mail's `^D Del`, `^R Reply`, `F2 Send`, `F3`, `F5` are not there - they act on the mail window's highlighted message, which would be behind you. `N new` (`unreadTotal()`, server messages and your own lines never count) stays right-aligned on either status line, redrawn when the total changes; never a box.
- **Chat menu** (`Ch~a~t`, Alt-A - Alt-C is the window): Connect… (radio list of the accounts not connected; no dialog when there is one), Disconnect… (the same for live ones), Chat window (Alt-C), Join…, Leave, then one item per account with its state on the right (`Libera   connected`); picking an account shows it in the chat window, connecting it first if needed. Join… shows the conversation joined with the input focused (it is listed once its `conv-new` arrives). The account items are rebuilt after every poll that brought a `state` event or a reset (never while a menu is open - see bug #44).
- **Greyed by hand:** tvision only tracks commands 0-255 (`commandEnabled()` is always true above), and every command-set change resets every menu item's `disabled` flag. `TVMailMenuBar` sets the chat items' flags before and after each event it sees; `chatCommand()` checks again, because Enter can still pick a greyed item. No `tvchat.conf` (or mingw, or a backend that stopped answering): everything greyed and a note saying why, e.g. `(no chat: no tvchat.conf)`. Leave is greyed unless the chat window is in front showing a channel or person (not server messages).
- **The chat pane:** a `TChatLog` (a `TTextView`, so `TMailText`: UTF-8 columns, wrap (on by default; View ▸ Wrap long lines works from the input line too), Shift-select + Ctrl-Ins copy, Find) over a `TFieldLine` (2,000 bytes). Typing in the log moves the focus to the input. Enter sends `send CONV TEXT`; on server messages nothing is sent (a status-line note says why); a refused send (not connected) becomes a dim note in the log and the text stays. After a send the app polls at once, so your line shows without waiting for the tick. PgUp / PgDn / Up / Down / Ctrl-PgUp / Ctrl-PgDn scroll the log from the input; PgUp or Up at the top asks `history CONV --before OLDEST` (50 at a time) and keeps your place; "No older messages" on the status line when the ring is exhausted.
- **Showing a conversation:** `history CONV` (the last 50), then the channel topic as a dim line (`ChatConv::topic`, kept by `ChatClient` from `topic` events - `convs` does not carry it), then `msg` events for it. History and the event queue overlap, so lines are kept once per msgid. `cmChatReset` re-reads the lists and the history (a restarted backend's ring is all there is). `state` events of its account, `topic` and `conv-gone` add a dim note (`-- disconnected: ...`); a `conv-gone` of the conversation shown moves to what the list now highlights.
- **Log lines:** `HH:MM <nick> text`, `HH:MM * nick action`, `HH:MM -nick- notice` (flag `n`, dim), `HH:MM -- note` for tvmail's own; `[e]` after the time for an encrypted message; continuation lines of a multi-line message indented under the text. Local time from the UTC stamp by hand (no `timegm` on every libc). Colours, all scored by `e2e_regress`: others black on cyan, own lines (flag `s`) blue on cyan (4.6:1), system lines `#444444` on cyan (3.4:1).
- **Unread and read:** counts in both lists, in the window title (`tvmail chat (3)` while it is not the window you are in - Window ▸ List shows the same) and as `N new`. As in mail, showing a conversation does not read it: `mark-read` goes when the chat pane takes the focus (with the window in front), when the window comes to the front with the chat pane focused, and for a message arriving while you are there; the counts drop with the backend's `unread CONV 0` on the next poll. (tvision sets a group's `current` only after telling the view it has the focus, so the pane's own `cmPaneFocused` says which pane it is.)
- **Leave** parts the conversation shown (`part`); its `conv-gone` takes it out of the list. Server messages refuse (the backend says why).
- **Unsent input** stays with its conversation: picking another one keeps it as that conversation's draft and shows the other's; Esc and Alt-C keep it too. Alt-X with any draft asks "Discard the chat message(s) you have not sent?" (Yes / No); No calls the quit off and keeps them.
- **Not in milestone 4:** typed `/commands` are sent as text (milestone 5 adds `/me`, `/join`, `/msg`, …); no member list view (the `members` verb exists); a pasted line ending in a newline is pasted, not sent (tvision delivers a fast burst of keys as a paste); new queries are not shown by themselves - they appear in the list with their count and in "N new". Drafts are lost with tvmail (in memory, like the logs).

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
- **`e2e_regress`** case 43 (bug #43) guards the `EINTR` fix; case 44 (bug #44) keeps menus open while chat polls. The chat UI cases (tagged `--`) use **`tests/fake_irc.py`**, a scripted IRC server (registers, confirms JOIN with a topic and NAMES, records what it is sent, says what the test hands it): `chat_window_three_panes` (unread in the server and conversation lists, the chat window's own status line, history and topic on moving onto a conversation without reading it, read once the chat pane has the focus, live messages and `/me`, the Tab cycle, Alt-C back to mail and "N new" there, `tvmail chat (1)` in Window ▸ List, Esc back to mail, Alt-C back to the same conversation), `chat_log_utf8_columns` (CJK and two-byte nicks and text, aligned stamps, word wrap, no split characters), `chat_history_pgup`, `chat_join_and_leave`, `chat_unsent_input_kept` (a draft survives Esc and another conversation; Alt-X asks; No keeps it), `chat_colours_readable` (own / system / other in three colours, every cell >= 3:1) and `chat_menu_greyed_without_config`.
- **`mingw_compile`** (`tests/mingw_compile.sh`, new): every `src/*.cpp` syntax-checked by the mingw-w64 cross g++, `-Wall -Wextra -Werror`, against the tvision headers; exits 77 (skipped) without the cross compiler. Guards bug #45.
- Still to add: a fake Delta Chat RPC server (a stub speaking JSON-RPC lines), plus a real-binary test that exits 77 when `deltachat-rpc-server` is not installed.
- **Conventions:** stdlib only in Python; Python ≥ 3.6 syntax (no walrus, no `match`, no f-strings so far); C++17 across `src/`; comments in the existing explanatory style; no credentials in code, tests or examples; every squashed bug gets a `BUGS.md` entry (next free number: check the whole file - both tables) and a regression case.
- **Builds:** Linux, the Pi, macOS and Cygwin; mingw compiles with chat disabled (`mingw_compile`, and a full cross build linked on Oct 21). `-Wall -Wextra` is clean on Linux and mingw (pass `-DCMAKE_CXX_FLAGS="-Wall -Wextra"`; the CMakeLists does not set it).
- **`mail_setup`** runs the suites of whatever `chrispollitt/POSIX` checkout `third_party.sh` fetched. On Oct 21 a fresh clone failed one of its shell checks ("sasl_passwd is mode 600": `configure-sendmail-relay.sh` now leaves it 0640 for the pullers) - a mail-setup inconsistency, nothing in tvmail; fixed in POSIX 53a32b1 (the test and docs now expect 0640).

## Bugs found along the way

- **#46 (fixed, b97d83c):** chat SASL sent the mail password - `~/.mu-tickets`' `*://chris:...@cpbee` matched before the right `~/.netrc` entry, because the borrowed mail lookup falls back to any user on the host. Found in the first live check on cpbee's Ergo (Oct 21), which otherwise passed: no reset after "hello" there.
- **#44 (fixed, 611efde):** with an account connected, every menu closed by itself within 250 ms - `idle()` posted the chat poll as a command, and any command ends a tvision menu. `idle()` now posts nothing while `TopView()` is not the app (a menu or a dialog is up); the backend queue holds the events. Found building the Chat menu.
- **#45 (fixed, 1e43880):** the mingw build did not compile - `main()`'s `setenv("ESCDELAY", ...)` (668ea6b, pre-chat). Now POSIX only (ESCDELAY is an ncurses setting; the Windows build reads the Win32 console). Found by the first mingw compile since the split.
- **#43 (fixed, 62347cf):** a signal during a backend round trip - `SIGWINCH` from any terminal resize - made `poll()`/`fgetc()` fail with `EINTR`, which was taken for a dead backend: the call fell back to the slow shell path, and during a start the co-process was given up for the session. Pre-dates chat; found by `e2e_chat` flaking.

## Open questions for Chris

- [x] ~~IRC nick, NickServ registration, test channel?~~ Libera.Chat, nick `chris-pollitt`, registered (SASL user `chris-pollitt`), channel `#tvmail-test`, autoconnect on (Oct 21). His `tvchat.conf` and SASL password live on his machine only; `irc-tail libera` works there. Milestone 4's live check (a conversation in `#tvmail-test` from tvmail) is his.
- [x] ~~Split `src/tvmail.cpp`?~~ Yes (Oct 20) - done.
- [ ] Dedicated Delta Chat address on cpbee: which one, and is the mailbox created by mail-setup (a change in the `chrispollitt/POSIX` repo, not yet attached to the session) or by hand? (milestone 7)
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
