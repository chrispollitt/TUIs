# tvcommon

What [tvmail](../tvmail/README.md) and [tvchat](../tvchat/README.md) share.
It is not an app: each app's `CMakeLists.txt` includes `tvcommon.cmake` and
compiles `src/` into its own executable. Nothing here knows about mail or chat.

## Toasts instead of pop-ups

A message that needs no answer is `notify(text, level)` (notify.cpp), not an OK box: a small
note at the bottom right of the desktop that takes no focus and no key and goes away by
itself (info 4 s, warnings 7 s, errors 10 s; a click removes it; at most four show, newest
at the bottom). Every one - and every status-line flash - is kept in the history, **Window >
Notification history** (newest first, 500 kept, in memory). Boxes stay for questions (Yes /
No, "save changes?"). The toasts are views of the application group, so they sit over windows
and dialogs, and expire from `idle()` (`TTvApp::idle` calls `notifyTick`), not from a timer,
so one raised while a dialog is open still goes away on time. An app's own `idle()` must call
`TTvApp::idle()`.

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
| `src/notify.cpp` | `notify()`: toasts and their history (below) |
| `src/configwin.cpp` | the config editor window both apps open from File > Edit configuration...: F9 verify, F8 save + apply, driven by a `ConfigSpec` (the file, the checker, the apply step) |
| `src/dialogs.cpp`, `textwin.cpp` | the Find / Replace dialogs; the read-only text window (Help) |
| `tvcommon.cmake` | locates Turbo Vision, sets the Cygwin defines and the shell the helper scripts run through |
| `third_party.sh` | fetches Turbo Vision (patched by `patches/`) and chrispollitt/POSIX's `mail-setup` + `chat-setup` once into `third_party/`, for both apps |
| `TERMINAL-HISTORY.md` | an essay on how Unix and Windows terminals diverged and met, ending with the ssh-from-Windows-Terminal case study (section 7) |
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

## Over ssh from Windows Terminal

Running tvmail or tvchat over ssh from inside Windows Terminal used to garble
the screen: stray text such as `[68;32;100;1;0;1_` appeared in what you were
typing, and keystrokes or mouse clicks went missing. The app asks Windows
Terminal for win32-input-mode, so every key press arrives as a record `ESC [
Vk;Sc;Uc;Kd;Cs;Rc _`. On the way through ssh, the Cygwin pty and ConPTY, some
of those records arrive cut in two, the second half 16-33 ms after the first.
ncurses (tvision's input layer) waits only 10 ms after an ESC before deciding it
was the Escape key, so the ESC became a keypress and the rest was typed as text.
(The same can happen to the terminal's replies to the app's capability
questions.)

**Fixed in the apps:** `ESCDELAY` defaults to 100 ms (`src/cli.cpp`) and
`build.sh` builds tvision with a 50 ms read timeout instead of 10 ms. You need
a build from this tree, on the host where the app runs (the Pi, not the
Windows side). Confirmed: Windows Terminal -> ssh -> Cygwin sshd. Not
re-tested: the older "garbage replies" and "dropped keys with
`CYGWIN=disable_pcon`" symptoms, other ssh clients, other remote hosts. A bare
Escape key press now takes up to 100 ms to register (`ESCDELAY=10` restores the
old feel on a fast link).

To check a path without the app, run `termprobe` (CWPutils/toolbox):

    termprobe --tvision      # replays tvision's startup, tests typing and
                             # clicking, reports records cut in two and how
                             # late the rest came

If it still reports split records with gaps longer than your `ESCDELAY`, raise
`ESCDELAY`, or work around it:

1. Run ssh under **wincygterm** (CWPutils/toolbox, next to termprobe). It runs
   a Cygwin program on a real pty inside Windows Terminal instead of Cygwin's
   own console layer, which is what swallows the terminal's colour reply and
   hands ncurses its key input in fragments. Make a Windows Terminal profile
   from the output of `python wincygterm --profile` (a native Windows Python,
   not Cygwin's), and end its command line with `-- ssh cpbee` to land
   straight in the session, or leave that off for a Cygwin shell and run ssh
   from there. See `man wincygterm`. Not needed in mintty.
2. Run ssh from mintty instead of Windows Terminal.
3. Run ssh from inside WSL (the old workaround), e.g. a Windows Terminal
   profile whose command line is `wsl.exe -d Ubuntu-20.04 ssh cpbee`.

The measurements behind the diagnosis are in
[TERMINAL-HISTORY.md](TERMINAL-HISTORY.md), section 7. Which layer cuts the
records in two, and why in multiples of about 16 ms, is still an open question;
so is why Cygwin's own console swallows OSC colour queries.
