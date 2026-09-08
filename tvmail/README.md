# tvmail

A **Turbo Vision** mail client for a local Unix mailbox — dropdown menus,
dialog boxes with drop shadows, the cyan desktop, `F10` for the menu bar.
Built on [magiblot/tvision](https://github.com/magiblot/tvision), the modern C++
port of Borland's Turbo Vision. It is 2026. This is fine.

See [HISTORY.md](HISTORY.md) for the twenty-year excuse.

```
 File  Message  Edit  Window  Help
+---------------------------------------------------------------------+
| inbox  (/var/mail) | N 09-06 21:14  chris@cmlaptop   test2       ^  |
| drafts             | . 09-06 00:34  Mail Delivery..  failed      #  |
| saved  (~/mbox)    | . 09-06 19:38  Chris Pollitt    Re: test2   v  |
| trash              |----------------------------------------------- |
| dead.letter        | Date:    Sat, 06 Sep 2026 19:38 -0700       ^  |
|                    | From:    Chris Pollitt <chris.pollitt@..>      |
|                    | Subject: Re: test2                          #  |
|                    |                                                |
|                    | bye                                         v  |
+---------------------------------------------------------------------+
 F1 Help  F3 Pull  F5 Reload  ^R Reply  ^N New  F2 Send  ^D Del  F4 Book
```

## What it is

The **face** only. Every mail-format operation — mbox parsing, MIME decode,
compose, send, delete, drafts, address book — lives in a small **stdlib-only
Python helper**, `tvmail-backend`, that the C++ app shells out to. The TUI
stays tiny; the backend is useful on its own (see below).

It is a friendlier face on the **same mailbox `mail(1)` uses**, not a walled
garden — see *mail(1) interoperability* below.

## Modes

`tvmail-backend` runs in one of two modes, chosen by
`~/.config/tvmail/tvmail.conf` (copy [`tvmail.conf.example`](tvmail.conf.example)):

| mode | what it talks to |
|---|---|
| **local** | on-disk mbox files + local `sendmail(8)` — the host that owns the mailstore |
| **remote** | IMAP for reading, SMTP submission for sending — every other machine |

`mode = auto` (default) → **local** if this host's name is in `master = …`, or if
there's no `[imap]` section; **remote** otherwise. `$TVMAIL_MODE` overrides one
run. Passwords are **only** in `~/.netrc` (`machine <host> login <u> password …`,
mode 0600), never in `tvmail.conf`. In remote mode the folder shorthands map to
IMAP folders (`spool→INBOX`, `mbox→Archive`, `trash→Trash`, `drafts→Drafts`,
`sent→Sent`, `spam→Junk`, tunable in `[folders]`); `dead.letter` stays a local
file. **F3** on a client doesn't pull (the master does that) — it files
`***SPAM***`-tagged messages into the spam folder. The C++ UI is identical in
both modes.

## Plumbing

A typical setup: **one master host** owns the mailstore; the laptop / WSL / VMs
are remote clients.

- **Master** — a real MTA (`configure/configure-sendmail-relay.sh` on
  **Linux**: configures Postfix in place, no apt, or Debian/Ubuntu/Pi `exim4`
  via `update-exim4.conf`) delivering local mail to `/var/mail/$USER` and
  relaying outbound through an authenticated TLS smarthost, plus **`pop-pull`**
  (`configure/configure-mail-pull.sh --timer N` — `--pwfile` if there's no
  exim `passwd.client`) to fetch replies back, and an IMAP server (Dovecot)
  over the same mbox files. Point the timer at a wrapper that runs `pop-pull`
  then `tvmail-backend purge spool --subject "***SPAM***" --to-folder spam`
  (over `localhost` IMAP, so Dovecot owns the move) and spam is filed for
  every client automatically.
- **Clients** — just `tvmail` + `tvmail-backend` in remote mode. `[smtp] from`
  fixes the sender identity so the master relays without any server-side
  rewrite; `[smtp] auth = false` if the master's Postfix trusts the LAN.
- The single-box Cygwin path still works (`configure-sendmail-relay.sh` writes a
  hand-rolled `exim.conf`, no daemon) — but Cygwin multi-user local delivery is
  a losing fight; use a Linux master and remote clients instead.
- No credentials live anywhere in this project.

## Platforms

Builds and runs natively on **Linux** (including Raspberry Pi and WSL),
**macOS** and the **BSDs**; the app is a normal ncurses program and the backend
is pure‑stdlib Python 3. **Cygwin** works too, with a small tvision patch (see
*Build*). The exim/`pop-pull` scripts under `configure/` are Cygwin‑host
specific — everything else is portable.

## Requirements

- `cmake`, a C++17 compiler, `ncurses(w)` headers, `git`
  - Debian/Ubuntu/Pi: `apt install cmake g++ libncursesw5-dev git`
  - Fedora: `dnf install cmake gcc-c++ ncurses-devel git`
  - macOS: Xcode Command Line Tools + `brew install cmake`
  - Cygwin: `setup-x86_64.exe -q -P cmake libncurses-devel gcc-g++ git`
- a working **Python 3** (≥ 3.6) somewhere on `PATH` — `python3`, or a
  versioned `python3.x`; the backend's sh/python polyglot shebang copes with a
  broken `/usr/bin/python3` symlink
- an MTA for sending: `sendmail`, or `msmtp`, or set `$SENDMAIL`
- Cygwin `--mingw` builds only: `mingw64-x86_64-gcc-g++`

## Build

On Linux/macOS/BSD `./build.sh` just builds. On **Cygwin** only, Turbo Vision
needs a small compatibility patch first (its Unix backend assumes Linux/BSD:
`FIONREAD` on pipes, `SA_NOCLDWAIT`, XSI `wcwidth`, C‑linkage `strupr`) —
`patches/cygwin_patch.py`, which `build.sh` applies automatically.

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

## Tests

```bash
./test.sh                 # build, then ctest all three layers
./test.sh -R backend_unit # just one
```

- **`backend_unit`** — stdlib `unittest` over `tvmail-backend`: mbox parsing,
  flags, `delete` → trash + backup, `~/.mailrc` aliases, lock sweeping,
  `save-draft`, and the `serve` frame protocol. No build needed
  (`python3 -m unittest discover -s tests`).
- **`backend_pipe`** — `tvmail --selftest`: the C++ side forks the `serve`
  co-process, handshakes, round-trips a request, exercises `spawnLogged`.
- **`e2e_tui`** — drives the built binary in a pty: launch, panes populate,
  `Tab` walks the active-pane marker, the body scrolls, `F3` pulls in the
  background. Reports *skipped* where there's no usable terminal.

Details in [tests/README.md](tests/README.md). No dependencies beyond a
Python 3 and (for `e2e_tui`) a pty.

## The three panes

| pane | what |
|---|---|
| **Folders** (left) | the five mailboxes below |
| **Messages** (top right) | the selected folder's messages |
| **Message body** (bottom right) | decoded headers + text of the highlighted message |

`Tab` / `Shift-Tab` move between panes, in the order Folders → Messages →
Message body; the active pane's heading is shown `[ bracketed ]` and
highlighted. Arrows / `PgUp` / `PgDn` / `Home` / `End` / `Space` move within
whichever pane is active — including scrolling the body. Moving the highlight
in **Folders** reloads the message list; moving it in **Messages** loads the
body. `Enter` on a message jumps focus to the body pane.

## Folders

| name | path | notes |
|---|---|---|
| `inbox` | `/var/mail/$USER` (`$MAIL`) | where exim + `pop-pull` deliver |
| `drafts` | `$folder/drafts` (default `~/Mail/drafts`) | `mail -f +drafts` opens it too |
| `sent` | `$folder/sent` (honours `set record`) | a copy of everything you send lands here |
| `saved` | `~/mbox` | where `mail(1)` files messages you've read |
| `spam` | `$folder/Junk` (remote: `Junk`) | `F3` moves `***SPAM***`-tagged mail here |
| `trash` | `~/.local/share/tvmail/trash.mbox` | `Ctrl-D` moves here; deleting from trash is permanent |
| `dead.letter` | `$DEAD` (default `~/dead.letter`) | a single message `mail(1)` or tvmail left behind |

Each folder shows an **`L`** or **`R`** — local mbox or remote IMAP storage. In
**remote** mode all but `dead.letter` are IMAP folders (`INBOX` / `Drafts` /
`Sent` / `Archive` / `Trash`, tunable in `tvmail.conf`); `dead.letter` is
always a local file, so it stays `L`. `[folders] sent =` (blank) turns off the
save-a-copy-on-send.

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
| `F3` | local: pull mail (`pop-pull`) · remote: file `***SPAM***` mail · `F5` reload |
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

`~/.signature` and `~/dead.letter` follow the usual conventions. This holds in
**both** modes — the address book, signature and dead.letter are always local.

**Sharing the store with `mail(1)`.** KISS: IMAP everywhere. GNU Mailutils is
built with `ENABLE_IMAP`, so point it at the same Dovecot tvmail uses —
`localhost` on the master, the master's name on a client:

```sh
# ~/.mailrc
set folder=imaps://chris@cmpi        # or @localhost on the master itself
set record=+Sent
# mail -f +INBOX   opens the IMAP inbox
```

Credentials come from the same `~/.netrc` entry tvmail uses, so `mail` and
`tvmail` see identical folders on every box. (Postfix still delivers to
`/var/mail/$USER`, which *is* Dovecot's INBOX — so incoming mail lands in the
one place everyone reads.)

## `tvmail-backend` (usable on its own)

```
tvmail-backend list  [MBOX]              idx <TAB> flags <TAB> date <TAB> from <TAB> subject
tvmail-backend show  IDX [MBOX]          decoded headers + text/plain body
tvmail-backend raw   IDX [MBOX]          the raw RFC822 message
tvmail-backend parts IDX [MBOX]          list MIME parts
tvmail-backend save  IDX PARTNO DEST [MBOX]
tvmail-backend delete IDX [IDX...] [--mbox MBOX] [--trash DEST]
tvmail-backend purge [MBOX] [--from S] [--subject S] [--to S] [--older-than DAYS]
                     [--seen|--unseen] [--to-folder DEST | --expunge] [-n]
tvmail-backend mark  IDX read|unread [MBOX]
tvmail-backend compose-template [--to A] [--subject S] [--in-reply-to IDX] [MBOX]
tvmail-backend send  [--from A] [--to A ...] [--subject S]   < message-or-body
tvmail-backend save-draft   < rfc822-message
tvmail-backend aliases                   NAME <TAB> expanded, addresses
tvmail-backend pull                      local: pop-pull + spam sweep; remote: spam sweep
tvmail-backend ping                      -> pong
tvmail-backend mode                      -> local | remote
tvmail-backend serve                     persistent framed request loop
```

`MBOX` is a path or one of the shorthands `spool` `mbox` `trash` `drafts`
`sent` `spam` `dead` (in remote mode all but `dead` are IMAP folders).

`purge` bulk-**moves** everything matching **all** the filters you give (at
least one required) to `--to-folder DEST` (default `trash`), or deletes it with
`--expunge`; `-n` dry-runs. Handy from `cron` on the master:

```bash
tvmail-backend purge --from "Cron Daemon" --older-than 14
tvmail-backend purge --subject "***SPAM***" --to-folder spam    # what F3 does
```

Works in both modes; see `man tvmail-backend` (**MODES**) for `tvmail.conf`.

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

## Man pages

`man tvmail` and `man tvmail-backend`, installed to `$prefix/share/man/man1`.
Old school. As nature intended.

## Tarballs

```bash
./dist.sh            # -> dist/tvmail-1.0.0.tar.gz              (source)
                     #    dist/tvmail-1.0.0-<system>-<arch>.tar.gz  (binary)
```

The **source** tarball builds anywhere tvision does — Linux, macOS, Cygwin
(`build.sh` skips the Cygwin patch elsewhere; the C++ is otherwise portable and
the Python backend is pure stdlib). The **binary** tarball is per-platform: a
Cygwin build is a Windows-PE-under-Cygwin program, a Linux build is an ELF, a
macOS build is a Mach-O — pick the one that matches your box, or build from
source. `cd build && cpack` also produces the binary tarball directly.

## TODO / someday / probably not

- Window List dialog (open windows, à la Turbo Pascal)
- attachment browser (`parts` / `save` are already in the backend)
- threading, search / filter, sort by column
- a `tvmailrc` for colours / default folder / poll-on-start
- mark-as-read only after the body is actually scrolled
- a mouse. it's Turbo Vision, it should have a mouse. one day.
