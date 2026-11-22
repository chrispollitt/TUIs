# tvmail history

What changed, release by release. (Why tvmail exists is in [WHY.md](WHY.md).)
Releases are tagged `tvmail-vX.Y.Z`; `git log` has the rest.

## Unreleased

- **LAN certificates from your own CA** (POSIX #7).  `cafile` in `[imap]`, `[smtp]` and `[sieve]` now takes a leading `~`, so
  one CA file (`~/.config/tvmail/cpbee-ca.pem`) replaces `verify = false` once the servers' certificates come from your CA.
  The deploy scripts for Radicale and the Delta Chat gateway use the LAN's Simple Inc certificate when it is there and hand
  out the one CA file instead of the server's own certificate (tvcal and tvchat docs say the same).

- **`password_cmd`: passwords from a vault** (TUIs #43 / POSIX#8, tvmail's part).  `[auth] password_cmd = auth get {scheme}
  {host} {user}` in tvmail.conf makes tvmail-backend ask that command for the IMAP, SMTP, ManageSieve and NNTP passwords
  before it reads `~/.mu-tickets` and `~/.netrc` (which stay the fallback, so tvmail still runs alone; off by default).  A
  `password_cmd` in `[imap]`, `[smtp]`, `[sieve]` or `[news]` overrides it per service, `$TVMAIL_PASSWORD_CMD` for one
  run.  Same shape as tvcontacts', tvchat's and tvcal's.

- **Quicker keys:** a run of arrow keys in the folder or message pane, or of letters in the type-to-filter, asks the
  backend once, for where it stops, instead of once per row (the highlight still moves at once; 150 ms, or
  `TV_DEBOUNCE_MS`, 0 = off).  Send, delete, move, purge, empty trash and save part / message now show "Working..." as
  the other backend calls do, and F3 Pull is no longer "greyed" (tvision cannot grey commands above 255).  BUGS.md #17,
  #18 and issue 73.

## 1.4.0 - November 2026

The services release: tvmail's window gets a fourth pane, **Services**, and three things to read in it: Mail, mailing
**Lists** and **Usenet**, each with a server you can run on your own LAN (`lists-setup` and `news-setup` in
chrispollitt/POSIX; [SERVICES-DESIGN.md](SERVICES-DESIGN.md) has the whole design).  Also in this release: searching
inside a folder, the contacts window, Floyd, and the fixes below.  tvchat takes the same version number.

- **Lists in tvmail** (step 4 of [SERVICES-DESIGN.md](SERVICES-DESIGN.md)).  With `[lists] address = listserv@HOST` in
  tvmail.conf, and remote mode (list mail is read over IMAP, with the Mail login), the Services pane lists **Lists**.  Its
  folders are the lists you are in: the IMAP folders `Lists/NAME`, which the server files list mail into, so they read,
  search, delete and move like any mailbox.  Ctrl-R and Ctrl-N write to the *list* (its `List-Post`, else `NAME@domain`),
  not to the author.  Message > Lists asks the list server, which is a mail address: **Available lists...** mails `LIST` and
  shows what is offered (Enter subscribes), **Subscribe to a list...**, **Sign off from this list** and **Digest or every
  message...** mail `SUBSCRIBE`, `SIGNOFF` and `SET ... DIGEST`; the answer mail comes back as a one-line notice, never a
  box.  tvmail-backend gains `lists-available`, `lists-subscribe`, `lists-signoff`, `lists-digest` and the `--service lists`
  forms of the mail verbs.  The contract with the server (the answer is a mail to the sender, In-Reply-To the command, its
  first line `OK` or `ERROR`) is written down in SERVICES-DESIGN.md.  Tested against a real IMAP server written for the
  tests (`tests/fake_imap_server.py`) and a stand-in list server; also run against the real `listserv` and, live, against
  mlmmj on the LAN's mail master.

- **Search messages** (Edit menu, **Ctrl-F** in the mail window).  Every word you give must be in From, To, Subject or the
  text (`"quotes"` for a phrase); the list then shows only the hits (`search: "foo"  (3 of 120, Esc clears)`) and Esc
  brings the rest back.  Over IMAP the server searches (UTF-8 for non-ASCII words), in a local mailbox tvmail reads it, and
  in Usenet the headers are matched at once and the texts of the newest 500 articles are fetched, for up to 30 s, saying how
  far it got if that ran out.  The type-to-filter still only sees the From and Subject of the list.  Backend verb
  `search MBOX "words"`.  (Ctrl-F lives in the mail window and not in the menu bar: the configuration editor's F8 and
  compose's Alt-S would have lost their keys to a menu hot key.)

- **Usenet groups in alphabetical order** by default; `[news] sort = newsrc` keeps the order you subscribed in.

- **Usenet in tvmail** (step 2 of [SERVICES-DESIGN.md](SERVICES-DESIGN.md)).  A `[news] host` in tvmail.conf adds
  a Usenet service to the Services pane: the groups you subscribe to are its folders, an article's number is its
  index, and the list is threaded by References (View > Threads for flat).  The list holds the newest `window`
  articles (500); Message > Usenet > Load older adds more.  What you have read is a `.newsrc`-style file
  (`~/.local/share/tvmail/newsrc-HOST`).  Ctrl-R is a followup, Ctrl-N a new article, F2 posts (a Newsgroups /
  Followup-To / Distribution compose window); Message > Usenet subscribes, unsubscribes, lists every group and
  catches up.  The NNTP client is tvcommon's `tvnntp.py` (Python's nntplib is gone in 3.13); tvmail-backend
  gains `news-groups`, `news-subscribe`, `news-unsubscribe`, `news-catchup` and the `--service news` forms of
  list, show, raw, mark, counts, folders, compose-template and send.  Tested against a fake NNTP server.
  Not yet: replying to an author by mail, the unread count is an upper bound where a group's numbers have gaps.

- **A fourth pane, Services** (step 1 of [SERVICES-DESIGN.md](SERVICES-DESIGN.md): mail, listserv and usenet in one window).
  Services sit above Folders; choosing one fills Folders with its folders.  Only Mail exists so far, so the
  pane is hidden unless `[services] order` names more than one or you pick View > Services pane, and the window is
  the three panes it was.  The folders and their counts now come from the backend (`tvmail-backend folders`,
  `services`, and a global `--service NAME`) instead of a table in the C++.

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
