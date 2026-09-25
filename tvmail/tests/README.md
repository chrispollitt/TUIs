# tvmail tests

Five layers, no dependencies beyond a Python 3, bash and — for `e2e_tui` — a pty.

| test | kind | what it covers |
|---|---|---|
| **`backend_unit`** (`test_backend.py`) | stdlib `unittest` | `tvmail-backend` logic: mbox parsing + flags, RFC2047 subjects, best-body / HTML fallback, `mbox_path` shorthands, `~/.mailrc` `folder`/`DEAD`/recursive `alias`/`group`, `_sweep_locks`, `delete` → trash + backup, `save-draft`, and the **`serve` frame protocol** (handshake, `list`/`show` over the pipe, blocked verbs, clean `quit`) |
| **`backend_pipe`** | `ctest` → `tvmail --selftest` | the C++ side: forks the `serve` co-process, handshakes, sends a framed request, then `spawnLogged` (the background-pull mechanism) |
| **`e2e_tui`** (`e2e_tui.py`) | stdlib `pty` | drives the built `tvmail` in a real terminal: it launches and fills all three panes, `Tab` walks the active-pane marker `Folders → Messages → Message`, arrows/PgDn scroll the body, and `F3` pulls in the background (completion box appears, the blocking suspend path is never taken) |
| **`install_roundtrip`** (`install_roundtrip.sh`) | bash | `cmake --install` into a temp prefix, `./uninstall.sh -n` removes nothing, `./uninstall.sh -y` removes every installed file; a legacy `pop-pull` a `mail-pull` still runs is kept, an unused one removed; the real `build/install_manifest.txt` is restored |
| **`mail_setup`** (`mail_setup.sh`) | bash → mail-setup's runner | `third_party/POSIX/mail-setup`'s unit, shell and pull-e2e suites (F3 runs its `mail-pull`); temp dirs only, never its live-master check; label `mail_setup` |

## Running

```sh
./test.sh                    # build + ctest, every layer
./test.sh -R backend_unit    # one by name
./test.sh -LE mail_setup     # skip mail-setup's suites (~30-60 s)
cd build && ctest -V         # verbose

python3 -m unittest discover -s tests    # just the backend layer, no build
python3 tests/e2e_tui.py --bin build/tvmail   # just the pty layer
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
