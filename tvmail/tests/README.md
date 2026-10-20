# tvmail tests

Eight layers, no dependencies beyond a Python 3, bash and — for `e2e_tui` — a pty
(`e2e_regress` also wants [pyte](https://pypi.org/project/pyte/), and skips without it).

| test | kind | what it covers |
|---|---|---|
| **`backend_unit`** (`test_backend.py`) | stdlib `unittest` | `tvmail-backend` logic: mbox parsing + flags, RFC2047 subjects, best-body / HTML fallback, `mbox_path` shorthands, `~/.mailrc` `folder`/`DEAD`/recursive `alias`/`group`, `_sweep_locks`, `delete` → trash + backup, `save-draft`, and the **`serve` frame protocol** (handshake, `list`/`show` over the pipe, blocked verbs, clean `quit`) |
| **`chat_unit`** (`test_chat_backend.py`) | stdlib `unittest` | `tvchat-backend` (the chat co-process): `tvchat.conf` loading and its errors (the shipped example too), TAB/newline wire escaping, the event queue's sequence numbers and `--after`, the `selectors` loop and its timers on a fake clock, the **`serve` frame protocol** (handshake even with a broken config, `accounts`/`events`, an unknown verb, several requests in one write, clean `quit` and EOF), the IRC line parser (tags, prefix, trailing, CAP LS), mIRC colour stripping, UTF-8-safe splitting, and the **IRC adapter against a fake server** on 127.0.0.1: CAP LS 302 + SASL PLAIN (and its failure), nick-in-use, JOIN/NAMES/TOPIC, server-time + msgid, `/me`, CTCP VERSION, echo-message, PING, queries, part, a server `ERROR` + hang-up, TLS with a `cafile` and a refused untrusted cert (openssl makes the cert; skipped without it), and autoconnect through a real `serve` process - no network, no real account |
| **`backend_pipe`** | `ctest` → `tvmail --selftest` | the C++ side: forks the `serve` co-process, handshakes, sends a framed request, then `spawnLogged` (the background-pull mechanism); then the same `CoProcess` class drives `tvchat-backend` (start, `accounts` from `tests/tvchat-selftest.conf`, an event poll, `ping`) |
| **`e2e_tui`** (`e2e_tui.py`) | stdlib `pty` | drives the built `tvmail` in a real terminal: it launches and fills all three panes, `Tab` walks the active-pane marker `Folders → Messages → Message`, arrows/PgDn scroll the body, and `F3` pulls in the background (completion box appears, the blocking suspend path is never taken) |
| **`e2e_regress`** (`e2e_regress.py`) | `pty` + pyte screen model | one case per squashed bug in [BUGS.md](../BUGS.md) (`-k 27` runs one): it reads the cells actually on screen, their columns and colours - newest message on open, Esc closes Help / Source / Parts / Address book, the delete prompt, filter and body scroll kept, compose hotkeys, UTF-8 / CJK columns with no gaps, wrap / `»` / tabs, Find and copy → paste, Help and menu text, every painted cell >= 3:1 contrast (themed colours 0-15 as Windows Terminal Dark+, 16-255 as xterm), and the unsaved-work prompts (Alt-X, header edits, a failed save, the source draft found by content, out of office against a fake ManageSieve server) |
| **`e2e_chat`** (`e2e_chat.py`) | `pty` + a fake IRC server | tvmail with a `tvchat.conf` that autoconnects to 127.0.0.1: the chat co-process starts with the app, the idle loop keeps polling it (a message sent 1.5 s after the join arrives - checked in tvmail's `--debug` log), a killed `tvchat-backend` is restarted and re-read, and Alt-X makes it QUIT |
| **`install_roundtrip`** (`install_roundtrip.sh`) | bash | `cmake --install` into a temp prefix, `./uninstall.sh -n` removes nothing, `./uninstall.sh -y` removes every installed file; a legacy `pop-pull` a `mail-pull` still runs is kept, an unused one removed; the real `build/install_manifest.txt` is restored |
| **`mail_setup`** (`mail_setup.sh`) | bash → mail-setup's runner | `third_party/POSIX/mail-setup`'s unit, shell and pull-e2e suites (F3 runs its `mail-pull`); temp dirs only, never its live-master check; label `mail_setup` |

## Running

```sh
./test.sh                    # build + ctest, every layer
./test.sh -R backend_unit    # one by name
./test.sh -R chat_unit       # just the chat backend
./test.sh -LE mail_setup     # skip mail-setup's suites (~30-60 s)
cd build && ctest -V         # verbose

python3 -m unittest discover -s tests    # just the backend layer, no build
python3 tests/e2e_tui.py --bin build/tvmail   # just the pty layer
python3 tests/e2e_regress.py --bin build/tvmail -k 36 -v   # one bug case, final screen
```

`e2e_tui` exits **77** (ctest: *skipped*) when it cannot get a usable terminal
— a bare CI shell, a stripped Python, or a non-POSIX host. `mail_setup` does
the same when mail-setup hasn't been fetched (`./third_party.sh mail-setup`).

## Notes

* The backend layer imports `tvmail-backend` directly (its `sh`/python polyglot
  first line is a harmless string literal to Python) and also shells out to it.
* `e2e_tui.py` parses damage-based terminal output, so it asserts on sentinel
  words that are guaranteed to be re-emitted when they scroll into view, not on
  a reconstructed screen grid. It is the fragile one by nature; keep its
  assertions loose.
