# tvcommon

What [tvmail](../tvmail/README.md) and [tvchat](../tvchat/README.md) share.
It is not an app: each app's `CMakeLists.txt` includes `tvcommon.cmake` and
compiles `src/` into its own executable. Nothing here knows about mail or chat.

## What is in it

| path | what |
| --- | --- |
| `src/tvcommon.h` | the one header: includes, the colours (`cNorm`, `cFocus`, `cRule` ...), the command ids both apps use, and the declarations of everything below |
| `src/tvapp.h`, `tvapp.cpp` | `TTvApp`, the application base: the cyan desktop, the retuned palette, the editor clipboard, Window > List, the status-line flash, the full redraw (Ctrl-L), and the builders both apps' menus and key lines are made from (`tvExitItem`, `tvReloadItem`, `tvFindItems`, `tvWindowMenu`, `tvHelpMenu`, `tvStatusDef`, `tvAboutBox`) so File, Edit, View, Window and Help look and work alike |
| `src/views.h` | views several windows share: `TMailText` (the read-only text view: UTF-8 columns, wrap, select, copy, Find), `TDivider` and `TPaneTitle` (the three-pane rules and headings), `TViewerWindow`, `TBodyEditor`, `TFieldLine`, and the table pieces (`TableCol`, `drawTableRow`, `TTableHead`, `drawTableList`) behind the Folders and Servers panes |
| `src/coprocess.h`, `coprocess.cpp` | `CoProcess`: one Python helper in `serve` mode over two pipes - a banner, one request line per call, a framed reply, restart of a dead child, `EINTR`-safe |
| `src/shell.cpp`, `util.cpp` | the sh plumbing (`shCapture`, `shInteractive`), `--debug` / `--trace` and the helper's log names; quoting, UTF-8 column fitting, the clipboard |
| `src/cli.cpp` | the command line both apps take (`--help`, `--version`, `--selftest`, `--debug [FILE]`, `--trace [FILE]`) and the `ESCDELAY` setting |
| `src/tty.cpp` | `mesg n` while the app runs (`[ui] mesg` in the app's .conf): the terminal's group-write bit is cleared so `talk` and `wall` do not write over the windows, and put back on exit, around a helper, on stop and on a signal |
| `src/dialogs.cpp`, `textwin.cpp` | the Find / Replace dialogs; the read-only text window (Help) |
| `tvcommon.cmake` | locates Turbo Vision, sets the Cygwin defines and the shell the helper scripts run through |
| `third_party.sh` | fetches Turbo Vision (patched by `patches/`) and chrispollitt/POSIX's `mail-setup` + `chat-setup` once into `third_party/`, for both apps |
| `tests/tvscreen.py` | the pty + [pyte](https://pypi.org/project/pyte/) test harness: `Tv` runs an app in a pty on its own temp HOME and reads its screen into a model you can check cell by cell (columns and colours), `Case`, key names, contrast scoring, and `render_png` for the README screenshots |

## Conventions the apps rely on

- **Log names carry the app:** `--debug` writes `debug.APP.log`, the helper's
  goes beside it as `debug.APP.backend.log`; `--trace` likewise
  (`trace.APP.log`, `trace.APP.backend.log`).
- **Nothing is posted while a menu or dialog is up.** Any command ends a
  tvision menu and a dialog swallows commands it does not know, so an app's
  idle ticks (a poll, a pull) post nothing while `TopView() != this`.
- **tvision tracks only commands 0-255**; a command-set change resets every
  menu item's `disabled` flag, so an app that greys items by hand does it
  before and after each event.
- **Tests wait for a condition, not a time.** `Tv.wait(pred, timeout)`; a
  fixed settle after an action that runs a cold helper races it (tvmail BUGS
  #47). An app's harness can hold a helper command up to prove it (tvmail's
  `Tv(slow=...)`).
- **Screenshots:** `tests/screenshot.py` in each app regenerates its
  `images/*.png` from a scripted run (needs Pillow and the Consolas font).

## Building and testing

tvcommon has no build of its own: build and test through an app (`./build.sh`,
`./test.sh` in `tvmail/` or `tvchat/`). A change here should be tested in both.
