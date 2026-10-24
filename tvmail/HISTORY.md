# tvmail history

What changed, release by release. (Why tvmail exists is in [WHY.md](WHY.md).)
Releases are tagged `tvmail-vX.Y.Z`; `git log` has the rest.

## Unreleased

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
