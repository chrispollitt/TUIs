# tvmail history

What changed, release by release. (Why tvmail exists is in [WHY.md](WHY.md).)
Releases are tagged `tvmail-vX.Y.Z`; `git log` has the rest.

## Unreleased

- **Fixes for daily use (BUGS.md #6, #33; #15 turned out to be done already):** Ctrl-D, Ctrl-R and Ctrl-N are the
  editor's own keys in a compose or signature window (cursor right, page up) and no longer delete, reply or start a
  new message there; and a backend that answers too slowly is given up on after 8 s (not waited for to the end, then
  asked again): the next action starts a fresh one.  tvchat shares the second fix.

- **Contacts window and Send to > Contacts:** File > Contacts... (shared with tvchat and tvcal) adds, edits and deletes contacts and groups in the address book on the server; Message > Send to > Contacts adds the sender once.

- **Address book on the server:** the Address book window and the To: expansion also use your contacts and groups kept in
  Radicale (CardDAV), through tvcommon's `tvcontacts.py` ([contacts] in tvmail.conf, or the one beside tvcal's calendar).
  A group name in To: expands to its members; the server being down just leaves the copy or the mailrc entries.

- **Message > Share in chat:** asks tvchat to say the sender and subject (not the body) of the message
  in its LAN channel (`[chat] share_to` in tvchat.conf). tvchat shows it and says it only when you
  agree there, where you can change the text first.

- **Toasts from tvchat, and yours for it:** errors, and new mail in the inbox, are left for tvchat as
  small files (`tvcommon/src/spool.cpp`); tvchat's errors, messages written to you and invitations
  show here as a toast "tvchat: ...". `[ui] share = error | warn | info | off` (default `error`) in
  tvmail.conf says how much goes on its own.

- **Cygwin is the only Windows.** The native (mingw) build is gone: `build.sh --mingw`, the
  `mingw_compile` test and every `_WIN32` branch (in tvcommon too) are removed; CMake refuses a
  Windows target and says to use Cygwin. That also ended the failing periodic-pull code in
  the mingw syntax check.

- **Floyd in tvmail** (File > Floyd..., F7; `tvmail-backend floyd`): an assistant that reads the
  unread subjects, triages unread mail from real people, finds mail by sender, subject and date,
  archives read inbox mail older than N days, empties the trash and switches the out-of-office
  reply off - asking with a count before it deletes or moves anything - plus a calculator, unit
  and time-zone conversion, and to-dos and notes that outlive the session. Levels `off`,
  `minimal`, `helpful`, `chatty`, `clippy` (`[floyd] level`); at clippy he offers "Floyd not
  clippy but ..." tips after a pull and when his window opens.

## 1.3.0 - October 2026

tvmail and tvchat now share one version (`../VERSION`, set with `../version.sh`).

- **File > Edit configuration...** opens tvmail.conf in a window: F9 verifies it (nothing is
  connected), F8 verifies, saves and has the backend start afresh on it.
- **Toasts instead of pop-ups** (tvcommon): "Message sent", "Saved to ..." and errors are a note
  at the bottom right that goes away by itself; Window > Notification history keeps them.
  Questions (delete? save changes?) are still boxes.
- **An optional link to tvchat:** Message > Chat with sender, and Message > Open chat invite for a
  Delta Chat invite link in a message. Neither app needs the other.

- **Chat moved out.** tvmail is mail only again: the chat client is its own
  app, [tvchat](../tvchat/README.md), and what both share is in
  [`../tvcommon`](../tvcommon/README.md).
- **The Folders pane is a table:** `Folder | Total | New` with faint rules
  between the columns - how many messages each folder holds and how many are
  unread (dead.letter counts 1, or 0 with no file).
- **Menus and key line match tvchat's:** one File / Edit / View / Window / Help
  built from shared code (Window gains Zoom, Tile and Cascade), one About box.
- **Ctrl-L** (Window > Redraw screen) repaints the screen; while tvmail runs
  its terminal takes no messages (`mesg n`; `[ui] mesg = y` keeps taking them),
  so `talk` rings and `wall` do not land on top of the windows.
- **Log names carry the app:** `--debug` writes `debug.tvmail.log` and the
  backend's `debug.tvmail.backend.log`; `--trace` writes `trace.tvmail.log` and
  `trace.tvmail.backend.log`.
- Fixed: a signal (a terminal resize) during a backend call was taken for a
  dead backend; the mingw build did not compile; an open menu closed by itself
  while chat polled (BUGS.md #43-#45).

## 1.2.0 - October 2026

- **Out of office:** File > Out of office... edits a Sieve vacation script over
  ManageSieve.
- **No more lost unsent work:** Alt-X asks about unsent compose windows and an
  unsaved signature; edits to To / Cc / Bcc / Subject count as changes; a
  failed Save draft keeps the window.
- **Text:** UTF-8-safe columns (CJK, accents), wrapped bodies, Find, and
  copy / paste of selected lines.
- **Colours:** exact VGA colours, readable dialogs and highlights.
- **Mail:** Sent copies are dated and signed, and Sent / Drafts list the
  recipient; sent mail carries `X-Mailer`, `X-Origin-User/Host` and
  `X-Original-From`; no ghost row after deleting over IMAP; the About box is
  sized to its text with the repo link.
- **Tests:** `e2e_regress`, a pty + pyte regression case per squashed bug.

## 1.1.0

See `git log tvmail-v1.1.0`.
