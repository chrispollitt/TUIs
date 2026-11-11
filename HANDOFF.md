# Handoff

Where the work stands, for whoever picks it up next (Claude or Chris). Updated
Nov 11, 2026 (tvcal section below; the rest is from Oct 27). Facts about the code live in the code and in the files listed
under "Where things are"; this file is what they do not say: the state, what
is next, what was decided, and the live setup.

## State

- **`main`** holds everything built so far: tvmail, tvchat (IRC, talk(1),
  Delta Chat), `tvcommon/`, the table-style panes, the aligned menus and key
  lines, the app-named `--debug` / `--trace` logs. **Pushed** (Oct 24), as is
  chrispollitt/POSIX with `mail-setup/scripts/configure-delta.sh` and
  `chat-setup/scripts/configure-tvchat-delta.sh`.
- **Installed on cmlaptop** (`/usr/local/bin`): `main` of Oct 23, **without**
  Delta Chat. Reinstall with `./build.sh && ./install.sh` in `tvchat/` and
  `tvmail/` (and add the Delta Chat account: see below).
- **Tests:** tvchat 5/5 layers pass on Cygwin (Oct 24, with the real core in
  `$TVCHAT_TEST_DC_RPC`), mail-setup shell tests 232, chat-setup 110. tvmail's
  7/7 last ran Oct 23, before this work (it was not touched).
- **Branches** merged into `main` and safe to delete: `claude/delta-chat-c713de`
  and the older `claude/tvchat-servers-table-83c863`, `claude/ui-align-2d1c4d`,
  `menu-order`, `log-names`, `tvmail-dead-count`, `flaky-e2e`, `docs-tidy`. The
  old `tvmail-v1.2.0` tag marks the last state before chat.
- Chris's untracked `tvcommon/third_party/POSIX.stale-20261021` (an old
  mail-setup checkout, everything in it already rebased into POSIX) is safe to
  delete.

## Next: the clean install on cmdesktop (client side only)

Chris's rule (Oct 24): **these apps need a lot of setup, so everything should be
automated and painless.** Chris does a clean install on cmdesktop to test the
whole path, client side only: the master (cpbee) already has the Delta Chat
mailboxes `delta@` and `delta2@cpbee.cmnet` (Unix users; passwords in
`/root/delta-passwords` on cpbee). On cmdesktop that means: get the TUIs and
POSIX repos, `./build.sh && ./install.sh` in `tvchat/`, then
`chat-setup/scripts/configure-tvchat-delta.sh --addr delta@cpbee.cmnet --host cpbee --self-signed --autoconnect` (installs the core, writes
`tvchat.conf`, asks for the password, connects once). Then Chat > Invite... on
one side and Chat > Chat with... on the other (the keys are swapped once).

Every manual step Chris has to take there is a bug in a script: fix the script,
not the notes. Things the live runs could not show: `pip install --user` putting
the core off `PATH` on Linux (the script then writes `rpc_server =` with the
path), the OS cmdesktop runs, a firewall that drops 587 on the master (cpbee:
`setup-router.sh fw` reads `/etc/router-setup/lan0-tcp-ports`).

How Delta Chat works: tvchat/DESIGN.md "Delta Chat adapter"; the server side
is mail-setup's `configure-delta.sh` (also a step of the `mail-setup.sh`
wizard) - cpbee still runs the temporary script's setup, re-running
`configure-delta.sh` there is safe. Lesson from that script: a `umask` that
leaks made a root-owned Dovecot file unreadable and stopped all local delivery
for minutes - check file modes after `sudo` scripts.

Also Oct 25: tvchat's Invite can mail the link through tvmail (the reverse link);
**File > Edit configuration...** in both apps (F9 verify, F8 save + apply: tvcommon's
configwin.cpp, `check-config` in both backends, `reload-config` in tvchat's); and
**toasts** (tvcommon/notify.cpp: `notify()` instead of an OK box for anything that needs no
answer; Window > Notification history). Left there: forms for the config editor and a Verify
that tries the logins (TO-DO tvchat 13 / tvmail 24); history is in memory only.

Done Oct 25 (all on main): joining an address mails the invitation and the other
tvchat asks to accept; the optional tvmail link (Message > Chat with sender / Open
chat invite, via `~/.local/state/tvchat/requests`); Chat > This chat... > Leave for Delta Chat
(confirmed first); groups (New group, Add / Remove member, Rename). tvchat/PRIMER.md
is the how-to for the three protocols. Left of Delta Chat (tvchat/TO-DO.txt item 1):
attachments, a member picker for groups, tvchat sending an invite through tvmail
(item 12), and the in-app config editor for both apps (tvchat 13, tvmail 24).

After that: tvchat/TO-DO.txt (small things), tvmail/TO-DO.txt (periodic pull
first), and the open bugs in tvmail/BUGS.md.

## Nov 11: tvcal (calendar, to-dos, notes, contacts) - state for the next session

**On `main` (e715f55, pushed):** tvcal 0.1.0 plus the three-pane redesign, steps 1-5 of
`tvcal/UI-REDESIGN.md`: window (Services / list / details), Notes (VJOURNAL, multi-line box, Floyd's
`!note`), Inbox + Reminders, and Contacts as a service (`TContactsPane` now lives in
`tvcommon/src/contactspane.h`, shared with tvmail's contacts window). Calendar is CalDAV (Radicale on
cpbee) with a local fallback; it works with tvmail (Message > Add to calendar / to-do) and tvchat
(Chat > Add selection) and Floyd (`!todo`, `!cal`, `!event`, `!note`).
**Tested Nov 11:** tvcal e2e 11/11 and backend tests; tvmail contacts e2e (both cases) pass; tvchat
builds. tvmail's `contacts_window_adds_edits_and_deletes` failed once right after the refactor and
passed 4 times since (not reproduced; if it fails again, check `contactswin.cpp` - reload() now also
calls moved(), focusItem is overridden). Builds need cmake, C++17, ncurses headers, python3; the tests
need `pip install pyte`.

**Installed on all three machines** (Nov 11, `update-tv-apps-everywhere.sh`, run by Chris): the laptop, cmdesktop and cpbee
were at `6d92b9f` when checked; the script pulls `origin/main`, so anything not merged and pushed is not deployed (quit the
apps first, see its header). Later commits on `main` are the update script's pull retry, docs and issue forms: no app code. Restart tvmail, tvchat and tvcal where
they were running.
**Branch** `tvcal-contacts-service` is merged into `main` but Chris is still using it for the contacts smoke test: do NOT
delete it and do not append it to `/home/chris/gh_cp/tmp-branches.txt` until he says. The repo is `chrispollitt/TUIs`
(GitHub redirects the old `tuis` name).

**Waiting on Chris:**
- `tvcal-floyd-on-cpbee.sh` (installs backend + Floyd on cpbee, makes the `floyd` Radicale user with rights on
  `/chris/calendar/` only): its stub is in `TO_BE_RUN/DONE` (Nov 3) and `/usr/local/share/tvcal` on cpbee dates from then,
  so it ran.  Not checked from here (floyd's files are root-only): ask Floyd `!todo` on IRC to see the round trip works.
  Still to do: add the `floyd` calendar login to the `identity` registry.
- Hands-on smoke test: New event, F7 for Floyd, tvmail Add to calendar, tvchat Add selection.
- Contacts service, hands-on (Nov 11, built tvcal against the real cpbee book): opens, the list and card,
  new contact and new group worked. Not yet reported: edit, the tvmail round trip (the group expands in
  To:), and deleting the two test entries `Zz Smoketest` and `Zz Smokegroup` from the real book.
  After a terminal resize the right frame can show holes: that is the terminal not repainting (Redraw
  screen fixes it), not the pane; a fresh start and a shrink both draw clean in the e2e harness.
- POSIX repo: `netrc-to-tickets` (run with `--move` to migrate `~/.netrc` to `~/.mu-tickets`) and
  `identity` (phase 1, read-only audit; list active at `~/.config/identities`) are committed and pushed
  (net-utils/, with tests and README rows); only running `netrc-to-tickets --move` is left for Chris.

**Later (tvcal/TO-DO.txt):** time zones, editing one occurrence of a repeating event, RSVP, man page
(tvcal.1, tvcal-backend.1), offline write queue, week view.

**Cloud-session notes:** clone with `--depth 1` is single-branch, so `origin/main` goes stale and the
"unpushed commits" stop hook is a false alarm - check with `git ls-remote origin main`. Moving a cloud
session to cmlaptop is not possible; start a new session there after `git pull`.

## Oct 27: Floyd - central bot (merged), levels, useful tools, Floyd in tvmail (all merged since: `floyd-levels` ca0d9ee, `floyd-service-user` 757c608)

**Merged to `main` (71b0c4e): the central Floyd.** `tvchat-backend floyd-daemon` runs him on cpbee as his
own IRC nick (`configure/floyd.conf.example`, `tvchat-floyd.service`, INSTALL.md "The central
Floyd"); admins = services accounts (IRCv3 account tag); `!claude QUESTION` asks the Claude CLI
read-only as the service user; a Floyd account in a tvchat session needs `local = yes`. Design:
tvchat/DESIGN.md "The central Floyd".

**Were on branch `floyd-levels` (merged into `main`, ca0d9ee; Chris asked for these three):**
1. *Levels* `off | minimal | helpful | chatty | clippy` ("full clippy mode" = clippy: tips introduced
   "Floyd not clippy but ..."). tvchat: `level =` per account, `!level NAME` per conversation (admins
   on the server). tvmail: `[floyd] level`, `level NAME` in his window, kept in
   `~/.local/share/tvmail/floyd/state.json`, `$TVMAIL_FLOYD_LEVEL` beats both.
2. *Floyd in tvmail:* File > Floyd (F7), `src/floydwin.cpp` (transcript + input line) over the new
   `tvmail-backend floyd [--tips|--status] TEXT` verb (`MailFloyd`). It drives the existing verbs.
3. *Useful things:* shared `tvcommon/py/tvfloyd.py` (calc by `ast`, units, time zones, per-person
   to-dos/notes, the tips; each app installs its own copy to `share/<app>/`, the backends look beside
   themselves, in `../../tvcommon/py`, `../share/<app>`). tvchat: `!calc !convert !time in !todo !note
   !catchup` (+ plain words from helpful). tvmail: unread, triage (heuristic: not noreply/bulk), find
   (sender/subject/date/folder), **archive** (READ inbox mail older than N days, default 90, to the
   archive; unread never), **empty trash**, **unset ooo** (`vacation off`); archive and empty trash show
   a count and wait for `yes` (5 minutes, kept in the `serve` process).

**Tests:** tvcommon `test_tvfloyd` 15; tvchat `chat_unit` 213 (`TestFloydDaemon`, `TestFloydLevels`,
`TestFloydDaemonProcess`); tvmail `test_backend` 153 incl. `TestFloyd`; e2e case
`floyd_window_answers_and_asks_before_deleting`; both `install_roundtrip` pass. (tvmail's
`mingw_compile` failure seen then is moot: the mingw build was dropped, see below.)

**Not verified / to do:**
1. Live on cpbee: register the `Floyd` NickServ account, install the unit (INSTALL.md), check Ergo sends
   `account-tag` to the daemon and that `claude -p` takes the prompt on stdin with
   `--tools/--allowedTools/--permission-mode dontAsk` there (flags checked only against
   `claude --help` on Windows; tests use a fake script).
2. **Done (Oct 27, branches `floyd-service-user` in TUIs and `floyd-daemon-setup` in POSIX; TUIs side merged, 757c608):**
   POSIX `chat-setup/scripts/configure-floyd.sh` now also writes `/etc/tvchat/floyd.conf`, installs
   `tvchat-floyd.service` and starts it, and the service runs as the **floyd** user (not chris), so the
   Claude consult only reads what floyd can. Chris must still log floyd in to Claude once:
   `sudo -u floyd -H bash -l` then `claude` (floyd has no login shell). `--service-user chris` undoes it.
   tvchat INSTALL.md "The central Floyd" rewritten (its old `install /dev/null floyd.env` step would have
   emptied the script's file). Tests: chat-setup 124 (`--print-config` / `--print-unit`). Not run on cpbee yet.
3. tvchat e2e case `chat_history_pgup` FAILED before this work too (a toast covers the log).
4. tvmail's Floyd has no live clippy tips on message selection (only on window open and after a pull);
   no "draft a reply / summarise" (that would be the Claude CLI; tvchat's admin-only consult is the
   model). Mail `find` scans the folder listing (headers), not bodies.
5. Decide: the old "speaks as you" attach (`/floyd on`) needs `local = yes` now; remove it later?
6. DONE: `floyd-levels` is merged into `main`; the branch no longer exists (the `tmp-branches.txt` rule was applied).  Was: add `TUIs floyd-levels` to
   `/home/chris/gh_cp/tmp-branches.txt` (Chris's rule).

**Cautions:** the Claude consult runs as chris and `Read` is not confined to a directory, hence
read-only plus admin-only; keep `admins` short. (`DEV-NOTES.txt`, with plaintext passwords, sits in
`gh_cp/` outside any repo and is not tracked by git: checked Nov 11, nothing to do.)

Env notes: Git Bash has no `python3`; use
`/c/Users/chris/AppData/Local/Programs/Python/Python311/python`, or Cygwin's python through
`D:\cygwin\bin\bash.exe -lc` (the unit and e2e tests need Cygwin). Windows Python writes CRLF
unless `newline=""`. In Bash tool commands a doubled backslash arrives as one, and a raw ESC can end
up in a file: write scripts with the Write tool. e2e `-k` takes a bug number or the full case name.
`git` root is `TUIs/`.

## Oct 27: native Windows (mingw) support dropped (branch `drop-mingw`)

Chris: "for chat and mail, drop support for direct Windows/mingw and only support Cygwin." Removed:
every `#ifdef/#ifndef _WIN32` branch (tvcommon, tvchat, tvmail; resolved as if `_WIN32` were never
defined), `tvmail/build.sh --mingw`, `tvmail/tests/mingw_compile.sh` and its ctest entry, the
`WIN32` static-link block in `tvcommon.cmake`, the `MINGW*|MSYS*` shell-script detection (only
`CYGWIN*` counts), and the docs for it. Both CMakeLists refuse a `WIN32` target. Kept: Cygwin
handling, WSL, and mentions of Windows Terminal / the Windows build of the Delta Chat core (that is
the core's own build, run under Cygwin).

## Oct 27 (evening): Floyd is live on cpbee

Running as the `floyd` user under systemd (`tvchat-floyd`), connected to Ergo as `Floyd` (SASL), admin
`chris`, `!claude` works (floyd's own Claude login; read-only tools; cannot read chris's files). Lessons
the setup hit, now handled by POSIX `chat-setup/scripts/configure-floyd.sh` (which now does every step:
tvchat build+install, Claude CLI install for floyd, clones into `~floyd/src`, floyd.conf with `host` =
the address Ergo listens on, `claude_cmd` = the full path, the unit, start, Claude login + a test
question): (1) on cpbee the name `cpbee` is loopback but Ergo's TLS port is LAN-only, so use the LAN
address; (2) Ergo refuses a registered nick to an unauthenticated connection (re-runs saw "in use");
(3) systemd's PATH lacks `~floyd/.local/bin`, so `claude_cmd` must be absolute; (4) running `claude`
from chris's directory makes it try chris's `.claude` settings - run it from floyd's home. The daemon
now logs connects, joins and server refusals to the journal. Not done: `#test` membership was not
confirmed (the probe saw no members; maybe secret), and no one-line `--status` for the daemon.

## Versions

tvmail and tvchat share one version: `VERSION` at the top, set with `./version.sh X.Y.Z` (it shows
every place that records it, and fails if they disagree; tvchat's `chat_unit` checks it too).
Release by bumping with that script, a HISTORY entry in each app, the tests, then three annotated
tags at that commit - `vX.Y.Z` (the whole repo), `tvmail-vX.Y.Z`, `tvchat-vX.Y.Z` - and
`git push --tags` after the live test. Tags only: no binary releases; a tag is just a stable
name to fetch (`git clone --branch v1.3.0 --depth 1 <repo>`). Oct 25: both are 1.3.0 (tvchat was 0.1.0).

## Where things are

| file | holds |
| --- | --- |
| `README.md` (root, and each app's) | what it is and how to use it - nothing about the work's history |
| `tvmail/INSTALL.md`, `tvchat/INSTALL.md` | building and installing |
| `tvchat/PRIMER.md` | the chat protocols (IRC, talk, Delta Chat) and the steps to get each running: server, then client |
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
  passed Oct 22-23: Chris and michelle talking through it, both views.
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

- **Installs go to /usr/local by default, never ~/.local** (Chris, Oct 25) - but
  `--prefix ~/.local` (tvchat/tvmail install, tvmail `setup.sh`,
  `configure-tvchat-delta.sh`) still works, and `MAIL_SETUP_BINDIR` moves
  mail-pull. **PATH:** `/etc/profile.d/path.sh` on every LAN machine (cmlaptop,
  cmdesktop, cpbee; not cppi) keeps `$HOME/.local/bin` in the directories it
  appends - other programs put things there - and on Cygwin its `PATH=` line
  has `/usr/bin`, not `/bin` (cmake 4.x needs it). The build / install / test
  scripts and `mail-setup/lib/common.sh` also put `/usr/bin` first on Cygwin,
  because a bare ssh login has neither it nor `/usr/local/bin`.

## How to work here

- **Builds and tests run under Cygwin** (`D:\cygwin\bin\bash.exe -lc 'cd
  /home/chris/github/chrispollitt/TUIs/<worktree>/tvchat && ./test.sh'`); the
  pty tests need Cygwin's python3 + pyte. `./build.sh`, `./test.sh`,
  `./install.sh` in each app. Linux, the Pi, macOS and Cygwin build. **There is no native
  Windows/mingw build** (Chris, Oct 27): Cygwin is the only Windows; no `_WIN32` code, no
  `build.sh --mingw`, no `mingw_compile` test.
- **A branch per task; stop for Chris's review;** Chris tests by hand and
  pushes himself. Commits end with the `Co-Authored-By` trailer.
- **Every squashed bug gets a BUGS.md row and a regression case.** A flaky
  pty test is a test that waits a fixed time: wait for a condition instead
  (tvcommon/README.md).
- **File modes (git).** Two gits share these trees: Cygwin git, and Git for Windows (the Bash tool's
  `git`, the desktop app's worktrees). NTFS has no Unix execute bit, so with `core.fileMode=true` the
  tree showed dozens of `mode change 100755 => 100644` and Cygwin even made plain files look
  executable. Fix (Oct 27), outside the repos in `~/gh_cp/git-hooks/`: **every repo has
  `core.fileMode=false`** (`fix-filemode.sh`, rerun after cloning a new repo; `third_party.sh` does it
  for its clones), and the pre-commit stub runs `pre-commit-modes`, which marks every staged file
  that starts with `#!` as executable in the index (what Linux/cpbee checks out) and re-sets
  fileMode. So: a script needs a `#!` line, not a chmod; `chmod +x` alone is invisible to git.
  `test-hooks.sh` tests it (run it with both gits). Never `git checkout -f` to clear mode noise.
- **Keep these files true:** when something here, in a README or in TO-DO
  changes, fix it in the same commit.
