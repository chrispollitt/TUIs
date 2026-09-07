# tvmail

A **Turbo Vision** mail client for a local Unix mailbox — dropdown menus,
dialog boxes with drop shadows, the cyan desktop, `F10` for the menu bar.
Built on [magiblot/tvision](https://github.com/magiblot/tvision), the modern C++
port of Borland's Turbo Vision.

See [HISTORY.md](HISTORY.md) for why this exists.

```
 File  Message  Edit  Window  Help
+----------------------------------------------------------------------+
| inbox  (/var/mail) | N 09-06 21:14  chris@cmlaptop   test2       ^  |
| drafts             | . 09-06 00:34  Mail Delivery..  failed      #  |
| saved  (~/mbox)    | . 09-06 19:38  Chris Pollitt    Re: test2   v  |
| trash              |------------------------------------------------- |
| dead.letter        | Date:    Sat, 06 Sep 2026 19:38 -0700       ^  |
|                    | From:    Chris Pollitt <chris.pollitt@..>      |
|                    | Subject: Re: test2                         #  |
|                    |                                               |
|                    | bye                                        v  |
+----------------------------------------------------------------------+
 F1 Help  F3 Pull  F5 Reload  ^R Reply  ^N New  F2 Send  ^D Del  F4 Book
```

## What it is

The **face** only. Every mail-format operation — mbox parsing, MIME decode,
compose, send, delete, drafts, address book — lives in a small **stdlib-only
Python helper**, `tvmail-backend`, that the C++ app shells out to. The TUI
stays tiny; the backend is useful on its own (see below).

It is a friendlier face on the **same mailbox `mail(1)` uses**, not a walled
garden — see *mail(1) interoperability* below.

## Plumbing

tvmail is the reader for a send-only mail setup on the same box (all in this
repo, under `configure/` and `backend/`):

- **exim** delivers local mail to `/var/mail/$USER` and relays outbound through
  an authenticated TLS smarthost, rewriting `From:` so replies reach a real
  mailbox. Set up with `configure/configure-sendmail-relay.sh`.
- **`pop-pull`** fetches remote mail into the same mbox on demand
  (`configure/configure-mail-pull.sh`).
- **Send** goes through `/usr/sbin/sendmail` → exim → smarthost.
- No credentials live anywhere in this project.

## Requirements

- Cygwin with `cmake`, `libncurses-devel`, a C++17 `g++`
  (`setup-x86_64.exe -q -P cmake libncurses-devel gcc-g++`)
- a `python3` / `python3.9` / `python3.8` on `PATH` (the backend has an
  sh/python polyglot shebang, so a broken `/usr/bin/python3` alternatives link
  is fine)
- for `--mingw` builds only: `mingw64-x86_64-gcc-g++`

## Build

Turbo Vision has no Cygwin target (its Unix backend assumes Linux/BSD:
`FIONREAD` on pipes, `SA_NOCLDWAIT`, XSI `wcwidth`, C-linkage `strupr`).
`patches/cygwin_patch.py` fixes those in the cloned tree; `build.sh` runs it
automatically and then builds a normal Cygwin **ncurses** app — the right thing
for mintty.

```bash
./build.sh                # clones + patches tvision into third_party/, builds
```

- `./build.sh --mingw` — cross-compile a static native `tvmail.exe` instead
  (only useful from a real Windows console, not mintty).
- On Linux / macOS `build.sh` just builds natively.

Install both binaries onto `PATH`:

```bash
cmake --install build --prefix ~/.local
export PATH="$HOME/.local/bin:$PATH"        # add to ~/.bashrc
```

## Run

```bash
tvmail
```

`tvmail-backend` must be reachable from the shell tvmail invokes; the install
step above puts it on `PATH`. Otherwise:
`PATH="$PWD/backend:$PATH" ./build/tvmail`.

## The three panes

| pane | what |
|---|---|
| **Folders** (left) | the five mailboxes below |
| **Messages** (top right) | the selected folder's messages |
| **Message body** (bottom right) | decoded headers + text of the highlighted message |

`Tab` / `Shift-Tab` move between panes. Arrows / `PgUp` / `PgDn` move within
one. Moving the highlight in **Folders** reloads the message list; moving it in
**Messages** loads the body. `Enter` on a message jumps focus to the body pane.

## Folders

| name | path | notes |
|---|---|---|
| `inbox` | `/var/mail/$USER` (`$MAIL`) | where exim + `pop-pull` deliver |
| `drafts` | `$folder/drafts` (default `~/Mail/drafts`) | `mail -f +drafts` opens it too |
| `saved` | `~/mbox` | where `mail(1)` files messages you've read |
| `trash` | `~/.local/share/tvmail/trash.mbox` | `Ctrl-D` moves here; deleting from trash is permanent |
| `dead.letter` | `$DEAD` (default `~/dead.letter`) | a single message `mail(1)` or tvmail left behind |

## Keys

| key | action |
|---|---|
| `F1` | help |
| `Tab` / `Shift-Tab` | move between panes |
| `Enter` | (Messages) jump to the body · (Drafts) open the draft to edit |
| `Ctrl-R` | reply to the selected message |
| `Ctrl-N` | new message |
| `F2` | send the compose window you're in |
| `Ctrl-D` | delete (→ trash) |
| `F3` | pull mail (`pop-pull`) · `F5` reload the folder |
| `F4` | address book |
| `F6` / `Shift-F6` | next / previous window · `F10` menu · `Alt-X` quit |

Editing keys (compose body, signature): `Shift-Del` cut, `Ctrl-Ins` copy,
`Shift-Ins` paste; **Edit** menu has Find / Replace / Find again.

## Composing

- **New message** starts with your `~/.signature` (after the quoted text on
  replies). **Message ▸ Insert signature / Insert dead.letter** add them by
  hand — the classic `~a` / `~d` tilde escapes.
- Closing an **unsent** message offers **Save draft / Discard / Cancel**. Save
  appends to the drafts mbox.
- In the **Drafts** folder, `Enter` reopens a draft to finish; sending it
  removes it from Drafts.

## Address book

**Message ▸ Address book** (`F4`) opens a window listing the `alias` and
`group` entries from `/etc/mailrc` and `~/.mailrc` (recursively expanded).
`Enter` drops the addresses into your compose window's `To:` field. You can
also just type an alias name in `To:` — `tvmail-backend send` expands it.

## Signature

**File ▸ Edit signature** opens `~/.signature` in a `TEditor` window.
`Ctrl-S` saves; closing prompts if modified.

## mail(1) interoperability

tvmail reads your existing `~/.mailrc` (and `/etc/mailrc`, `$MAILRC`):

| `.mailrc` | used for |
|---|---|
| `set folder=DIR` | base directory for the `drafts` folder |
| `set DEAD=PATH` | the `dead.letter` folder |
| `alias NAME addr…` / `group NAME …` | the address book, and recipient expansion on send |

`~/.signature` and `~/dead.letter` follow the usual conventions.

## `tvmail-backend` (usable on its own)

```
tvmail-backend list  [MBOX]              idx <TAB> flags <TAB> date <TAB> from <TAB> subject
tvmail-backend show  IDX [MBOX]          decoded headers + text/plain body
tvmail-backend raw   IDX [MBOX]          the raw RFC822 message
tvmail-backend parts IDX [MBOX]          list MIME parts
tvmail-backend save  IDX PARTNO DEST [MBOX]
tvmail-backend delete IDX [IDX...] [--mbox MBOX] [--trash DEST]
tvmail-backend mark  IDX read|unread [MBOX]
tvmail-backend compose-template [--to A] [--subject S] [--in-reply-to IDX] [MBOX]
tvmail-backend send  [--from A] [--to A ...] [--subject S]   < message-or-body
tvmail-backend save-draft   < rfc822-message
tvmail-backend aliases                   NAME <TAB> expanded, addresses
tvmail-backend pull
```

`MBOX` is a path or one of the shorthands `spool` `mbox` `trash` `drafts`
`dead`.

## Help

`F1` or **Help ▸ Contents** opens a scrollable reference inside the app.

## Licence

MIT — see [LICENSE](LICENSE). **No warranty**; see [WARRANTY](WARRANTY). It
sends mail and deletes messages: try it on a mailbox you can afford to lose and
keep mbox backups.

## Credits

Created by Chris Pollitt (see [AUTHORS](AUTHORS)), with pair-programming help
from Claude. Turbo Vision by [magiblot](https://github.com/magiblot/tvision);
Find/Replace dialog helpers adapted from its `tvedit` example.

## TODO

- Window List dialog (open windows, à la Turbo Pascal)
- attachment browser (`parts` / `save` are already in the backend)
- threading, search / filter, sort by column
- a `tvmailrc` for colours / default folder / poll-on-start
- mark-as-read only after the body is actually scrolled
