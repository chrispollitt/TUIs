# Handoff

Where the work stands, for whoever picks it up next (Claude or Chris). Updated
Oct 23, 2026. Facts about the code live in the code and in the files listed
under "Where things are"; this file is what they do not say: the state, what
is next, what was decided, and the live setup.

## State

- **`main`** holds everything built so far: tvmail, tvchat (IRC, talk(1)),
  `tvcommon/`, the table-style Servers and Folders panes, the aligned menus and
  key lines, the app-named `--debug` / `--trace` logs, and the fix for the
  flaky e2e case (tvmail BUGS #47). **Not pushed** (`main` is ahead of
  `origin/main`; Chris pushes after his own manual testing).
- **Installed on cmlaptop** (`/usr/local/bin`): the current `main` (Oct 23, after
  the UI alignment and the docs tidy-up). Reinstall with `./build.sh &&
  ./install.sh` in `tvchat/` and `tvmail/`.
- **Tests:** tvchat 5/5 and tvmail 7/7 layers pass on Cygwin (tvchat's
  `e2e_regress` ~2 min, tvmail's ~3 min). Both full suites passed on this `main` before the install.
- **Branches** merged into `main` and safe to delete: `claude/tvchat-servers-
  table-83c863`, `claude/ui-align-2d1c4d`, `menu-order`, `log-names`,
  `tvmail-dead-count`, `flaky-e2e`, `docs-tidy`. The old `tvmail-v1.2.0` tag
  marks the last state before chat.
- Chris's untracked `tvcommon/third_party/POSIX.stale-20261021` (an old
  mail-setup checkout, everything in it already rebased into POSIX) is safe to
  delete.

## Next: Delta Chat

Chris, Oct 23: talk(1) is done, **Delta Chat is next**. The plan is in
[tvchat/TO-DO.txt](tvchat/TO-DO.txt) item 1: spawn the official
`deltachat-rpc-server` as a child of `tvchat-backend` and speak JSON-RPC to it;
`DeltaChatAdapter` in `tvchat/backend/tvchat-backend` is a placeholder today.
Build it in steps and stop for Chris's review after each, as the earlier ones
were.

- **Needed from Chris first:** which dedicated address on cpbee, and whether
  chrispollitt/POSIX's mail-setup creates the mailbox or it is made by hand.
- The Servers pane already takes any protocol name in its `Proto` column, and
  `protocol = deltachat` is already accepted in the config.

After that: tvchat/TO-DO.txt (small things), tvmail/TO-DO.txt (periodic pull
first), and the open bugs in tvmail/BUGS.md.

## Where things are

| file | holds |
| --- | --- |
| `README.md` (root, and each app's) | what it is and how to use it - nothing about the work's history |
| `tvmail/INSTALL.md`, `tvchat/INSTALL.md` | building and installing |
| `tvchat/DESIGN.md` | how tvchat is built: backend, protocol, IRC and talk adapters, the window |
| `tvcommon/README.md` | the shared code, and the conventions both apps rely on |
| `*/BUGS.md` | defects, open and squashed (next free number: the highest in tvmail's and tvchat's tables plus one) |
| `*/TO-DO.txt` | features and wishes not done yet |
| `*/HISTORY.md` | what changed, release by release |
| `tvmail/WHY.md`, `tvmail/WARNINGS.txt` | why tvmail exists; caveats about mail setups (IMAP namespaces, local mode beside Dovecot, WAN latency) |
| `tvcommon/TERMINAL-HISTORY.md` | the essay behind the Windows Terminal / ssh fix (the short version is in `tvcommon/README.md`) |
| `*/tests/README.md` | what each test layer covers |
| man pages | `tvmail`, `tvmail-backend`, `tvchat`, `tvchat-backend` (the backend verbs are in the last) |

## The live setup

- **cpbee** (the LAN master, `cpbee.cmnet`) runs **Ergo 2.19.1**, installed by
  chrispollitt/POSIX `chat-setup/scripts/configure-ergo.sh`: TLS on
  192.168.10.1:6697, NickServ accounts `chris-pollitt` and `tester2`, channel
  `#test`. `configure-ergo.sh` has not been re-run since it started publishing
  `/opt/ergo/ca.pem` (POSIX bb1cc79); the laptop got its CA another way.
- **cpbee also runs our talkd** (`chat-setup/talkd/`, `configure-talkd.sh`
  `--open-firewall` opens TCP 518 for the laptops; UDP 518 on lan0). cmpi-router's
  `setup-router.sh` (master copy on cmlaptop, `~/src/Claude/cmpi-router`, not in
  git, copied to cpbee) reads `/etc/router-setup/lan0-udp-ports`. Live check
  passed Oct 22-23: Chris and miche talking through it, both views.
- **Libera.Chat:** nick `chris-pollitt`, registered, channel `#tvmail-test`.
  Chris's `tvchat.conf` and passwords live on his machine only, in
  `~/.config/tvchat/tvchat.conf` (written by `configure-tvchat.sh`; it copies
  an old `~/.config/tvmail/tvchat.conf` there first) with the passwords in
  `~/.netrc`. `tvchat-backend irc-tail libera` works there.
- **Credentials never go in the repo or the config file.**
- **Live checks pass** on cpbee: connect, SASL, join, send, reconnect after
  restarting Ergo, talk both ways. Libera connects and SASLs; the
  "Connection reset by peer" seen once after a send has not been seen again
  (tvchat/TO-DO.txt item 10).
- **chrispollitt/POSIX** is a separate repo: `chat-setup` (Ergo, talkd,
  `configure-tvchat.sh`) and `mail-setup`. `tvcommon/third_party.sh` fetches it
  for the tests. Its own tests (chat-setup: 76) should pass before it is pushed;
  check it for commits not yet pushed.

## Decisions (do not reopen)

- **Separate apps, shared code.** tvmail and tvchat are two apps; what both use
  is `tvcommon/` (Oct 21). tvchat's backend does not import tvmail's.
- **Two thin C++ faces over stdlib-only Python helpers;** all protocol and
  format work lives in the helper, never in C++.
- **Shared chat model, one adapter per protocol,** in the spirit of Pidgin.
- **No WhatsApp, Teams or Messenger;** no DCC, voice or video, Matrix or XMPP,
  hosting any public server, threading or reactions UI.
- **Delta Chat uses the official `deltachat-rpc-server`,** never a
  reimplementation, and a dedicated address - not Chris's main inbox.
- **talk runs through one talkd on the master (cpbee),** ours, not the stock
  one; LAN only, no auth, like talk. The talk view is the user's choice,
  *classic* by default; classic sends every keystroke, IRC mode whole lines.
- **IRC history is not persisted** (in memory, 2,000 lines per conversation).
- **No chat polling under a menu or dialog** is fine: the backend queue holds
  the events.
- **The UI follows tvmail's:** same panes, same menu order (File, Chat/Message,
  Edit, View, Window, Help), the same key-line tail, tables with faint rules.
- **The web app is a wish, not a plan** (tvchat/TO-DO.txt); the backend's
  protocol stays free of anything terminal-specific.

## How to work here

- **Builds and tests run under Cygwin** (`D:\cygwin\bin\bash.exe -lc 'cd
  /home/chris/github/chrispollitt/TUIs/<worktree>/tvchat && ./test.sh'`); the
  pty tests need Cygwin's python3 + pyte. `./build.sh`, `./test.sh`,
  `./install.sh` in each app. Linux, the Pi, macOS and Cygwin build; mingw
  compiles with chat disabled (`mingw_compile`).
- **A branch per task; stop for Chris's review;** Chris tests by hand and
  pushes himself. Commits end with the `Co-Authored-By` trailer.
- **Every squashed bug gets a BUGS.md row and a regression case.** A flaky
  pty test is a test that waits a fixed time: wait for a condition instead
  (tvcommon/README.md).
- **Keep these files true:** when something here, in a README or in TO-DO
  changes, fix it in the same commit.
