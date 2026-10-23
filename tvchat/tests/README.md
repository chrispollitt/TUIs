# tvchat tests

Five layers, no dependencies beyond a Python 3, bash and — for `e2e_chat` and
`e2e_regress` — a pty (`e2e_regress` also wants [pyte](https://pypi.org/project/pyte/),
and skips without it). None needs network access or a real account: the IRC
server is always a scripted fake on 127.0.0.1.

| test | kind | what it covers |
|---|---|---|
| **`chat_unit`** (`test_chat_backend.py`) | stdlib `unittest` | `tvchat-backend`: `tvchat.conf` loading and its errors (the shipped example too, and the old `~/.config/tvmail/tvchat.conf` fallback), TAB/newline wire escaping, the event queue's sequence numbers and `--after`, the `selectors` loop and its timers on a fake clock, the **`serve` frame protocol** (handshake even with a broken config, `accounts`/`events`, an unknown verb, several requests in one write, clean `quit` and EOF), `--debug` (wire, state and end logged, SASL redacted, a handler bug logged with the connection alive), the IRC line parser (tags, prefix, trailing, CAP LS), mIRC colour stripping, UTF-8-safe splitting, the password lookup (bug #46), and the **IRC adapter against a fake server**: CAP LS 302 + SASL PLAIN (and its failure), nick-in-use, JOIN/NAMES/TOPIC, server-time + msgid, `/me`, CTCP VERSION, echo-message, PING, queries, part, a server `ERROR` + hang-up and the reconnect that follows (backoff doubling to 300 s, channels rejoined, Disconnect stops it; a first connection is not retried), the keepalive PING and its timeout, flood control (PONG jumps the queue), typed `/commands` (and their refusals), CHATHISTORY on join and before the oldest (BATCH merged in time order, FAIL), talk(1) against a fake talkd (calling out with both ways to talk, the other side's erase and unfinished line, answering, declining, refusals, no talkd) and, where chat-setup's `talkd.py` is at hand, two accounts calling each other through the real one, TLS with a `cafile` and a refused untrusted cert (openssl makes the cert; skipped without it), and autoconnect through a real `serve` process |
| **`backend_pipe`** | `ctest` → `tvchat --selftest` | the C++ side: tvcommon's `CoProcess` starts `tvchat-backend serve`, reads `accounts` from `tests/tvchat-selftest.conf`, polls events and stops it |
| **`e2e_chat`** (`e2e_chat.py`) | `pty` + a fake IRC server | tvchat with a `tvchat.conf` that autoconnects to 127.0.0.1: the co-process starts with the app, the idle loop keeps polling it (a message sent 1.5 s after the join arrives - checked in tvchat's `--debug` log), a line typed in the chat pane reaches the server and the server's answer comes back, a killed `tvchat-backend` is restarted and re-read (the log too), and Alt-X makes it QUIT |
| **`e2e_regress`** (`e2e_regress.py`) | `pty` + pyte screen model, `fake_irc.py` | the cells actually on screen, their columns and colours (harness: `../tvcommon/tests/tvscreen.py`): `chat_window_three_panes` (unread counts, history and topic without reading, read on focus, live messages and `/me`, the Tab cycle), `chat_history_pgup`, `chat_join_and_leave`, `chat_typed_commands` (`/me`, a refused `/bogus` kept in the input, `/join` from the server messages), `talk_classic_call_in` and `talk_irc_mode_call_out_and_toggle` (against `fake_talkd.py`: a ring and Enter to answer, their keystrokes live under the rule with their erase applied, ours sent as typed; a call out, lines only on Enter, "is typing", Ctrl-T to classic and back), `chat_log_utf8_columns` (CJK and two-byte nicks, wrap), `chat_unsent_input_kept` (drafts; Alt-X asks), `chat_colours_readable` (every cell >= 3:1), `chat_menu_greyed_without_config`, and one case per squashed bug in [BUGS.md](../BUGS.md): `menus_stay_open_while_chat_polls` (tvmail #44) and `resize_keeps_backend` (tvmail #43) |
| **`install_roundtrip`** (`install_roundtrip.sh`) | bash | `cmake --install` into a temp prefix (tvchat, `tvchat-backend`, man pages - and nothing of tvmail's), then `./uninstall.sh` removes every installed file; the real `build/install_manifest.txt` is restored |

## Running

```sh
./test.sh                    # build + ctest, every layer
./test.sh -R chat_unit       # one by name
cd build && ctest -V         # verbose

python3 -m unittest discover -s tests -p test_chat_backend.py   # just the backend, no build
python3 tests/e2e_chat.py --bin build/tvchat --backend-dir backend
python3 tests/e2e_regress.py --bin build/tvchat --backend-dir backend -k 44 -v       # one bug case
python3 tests/e2e_regress.py --bin build/tvchat --backend-dir backend -k chat_log_utf8_columns
```

`e2e_chat` and `e2e_regress` exit **77** (ctest: *skipped*) when they cannot
get a usable terminal, and `e2e_regress` also without pyte.

## Notes

* tvchat's shell preamble puts `~/bin`, `~/.local/bin` and `/usr/local/bin`
  before `$PATH`; the pty tests pass `--backend-dir backend` so they run this
  checkout's `tvchat-backend`, not an installed one.
