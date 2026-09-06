# tvmail

A **Turbo Vision** front-end for a local mailbox — dropdown menus, dialog
boxes with shadows, the cyan desktop. Built on
[magiblot/tvision](https://github.com/magiblot/tvision) (the modern C++ port of
Borland's Turbo Vision).

It is the "face" only. All mail-format work — mbox parsing, MIME decode,
delete, send — lives in a small stdlib-only Python helper, `tvmail-backend`,
which the C++ app shells out to. This keeps the TUI tiny and the backend
independently useful.

```
  +--------------------------------------------------------------+
  |  File   Message   Help                                       |
  +--------------------------------------------------------------+
  | +- Mailbox --------------------------------------------[^]-+ |
  | | N  2026-09-05 21:14   chris@cmlaptop      test          | |
  | | .  2026-09-05 21:16   Chris Pollitt       Re: hello ... | |
  | |                                                         | |
  | +---------------------------------------------------------+ |
  |  F3 Pull  F5 Reload  Enter Open  ^R Reply  ^N New  ^D Del   |
  +--------------------------------------------------------------+
```

## Fits the rest of the cmlaptop mail setup

- Reads `/var/mail/$USER` (mbox) — where `exim`'s `local_delivery` and
  `pop-pull` put mail.
- **Send** goes through `/usr/sbin/sendmail` (→ exim → smarthost), so the
  outbound From/Reply-To rewrite still applies.
- **Pull** just runs `pop-pull`.
- No credentials anywhere in this project.

## Build

Turbo Vision has no Cygwin target (its Unix backend assumes Linux/BSD:
`FIONREAD` on pipes, `SA_NOCLDWAIT`, XSI-visible `wcwidth`, C++-linkage
`strupr`…). `patches/cygwin_patch.py` fixes those in the cloned tree and
`build.sh` runs it automatically, then builds a normal Cygwin **ncurses** app —
which is what you want in mintty.

```bash
D:/cygwin/packages/setup-x86_64.exe -q -P cmake libncurses-devel
./build.sh          # clones tvision into third_party/, patches it, builds
```

`./build.sh --mingw` instead cross-compiles a static native `tvmail.exe` (needs
`mingw64-x86_64-gcc-g++`) — only useful if you run it from a real Windows
console rather than mintty. On Linux/macOS `build.sh` just builds natively.

The backend uses an sh/python polyglot shebang, so it runs even though this
box's `/usr/bin/python3` is a broken `alternatives` link (only `python3.9`
works). Any `python3` / `python3.9` / `python3.8` on PATH is fine.

Install both onto your PATH:

```bash
cmake --install build --prefix ~/.local     # -> ~/.local/bin/{tvmail.exe,tvmail-backend}
export PATH="$HOME/.local/bin:$PATH"         # (add to ~/.bashrc)
```

## Run

```bash
tvmail                 # opens $MAIL or /var/mail/$USER
tvmail /var/mail/chris # explicit mailbox
```

`tvmail-backend` must be reachable from the Cygwin `bash` the .exe calls; the
install above puts it on PATH. Otherwise:
`PATH="$PWD/backend:$PATH" ./build/tvmail.exe`.

## Keys

| key | action |
|---|---|
| `↑`/`↓`, `PgUp`/`PgDn` | move in the message list |
| `Enter` | open the focused message in a viewer window |
| `Ctrl-R` | reply (opens `$EDITOR` with a quoted draft, then asks to send) |
| `Ctrl-N` | new message (prompts To/Subject, then `$EDITOR`) |
| `Ctrl-D` | delete the focused message (a `.bak` of the mbox is kept) |
| `F3` | pull mail (`pop-pull`) |
| `F5` | reload the list |
| `F10` | menu · `Alt-X` quit · `Tab` cycle windows |

## `tvmail-backend` (usable on its own)

```
tvmail-backend list  [MBOX]              idx <TAB> flags <TAB> date <TAB> from <TAB> subject
tvmail-backend show  IDX [MBOX]          decoded headers + text/plain body
tvmail-backend raw   IDX [MBOX]          the raw RFC822 message
tvmail-backend parts IDX [MBOX]          list MIME parts
tvmail-backend save  IDX PARTNO DEST [MBOX]
tvmail-backend delete IDX [IDX...] [--mbox MBOX]
tvmail-backend mark  IDX read|unread [MBOX]
tvmail-backend compose-template [--to A] [--subject S] [--in-reply-to IDX] [MBOX]
tvmail-backend send  [--from A] [--to A ...] [--subject S]   < message-or-body
tvmail-backend pull
```

## Status / TODO

v0 works: list, read, reply/compose via `$EDITOR`, delete, pull. Wishlist:

- native Turbo Vision compose dialog (To/Cc/Subject `TInputLine`s + a `TEditor`
  body) instead of shelling to `$EDITOR`
- multiple folders / Maildir; a folder pane on the left
- threading, search / filter, sort by column
- attachment browser (`parts` / `save` are already in the backend)
- a `~/.config/tvmailrc` for colours, default mailbox, editor, poll-on-start
- mark-as-read only after actually scrolling, not on open

The C++ side is deliberately thin — most features above are a backend
sub-command plus a window.
