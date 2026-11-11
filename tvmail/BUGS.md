# tvmail bugs

Defects only - features and wishes live in [TO-DO.txt](TO-DO.txt).
Numbers (`#n`) are from the October 2026 code review, so a commit message or
note that says "#14" means the same thing everywhere; bugs found later get
the next free number.  The **Test** column says what guards a fix against
coming back: `unit` (tests/test_backend.py), `e2e` (tests/e2e_regress.py,
one case per bug; tests/e2e_tui.py is the older smoke test), `build`
(the mingw cross-compile test, removed with the mingw build on Oct 27), or `manual` (checked on
screen, no automated test yet - the gaps a TUI regression suite should
close).

## To be squashed

Open bugs are GitHub Issues now (moved 2026-11-11): https://github.com/chrispollitt/TUIs/issues?q=is%3Aissue+label%3Abug+label%3Atvmail

## Squashed

| # | Bug | Fixed in | Test |
|---|-----|----------|------|
| 1 | Alt-X quit dropped unsent compose windows and an unsaved signature without asking | 7eba038 | e2e |
| 2 | Edits to To / Cc / Bcc / Subject alone did not count as changes - closing lost them | 7eba038 | e2e |
| 3 | A failed "Save draft" still closed the window | 7eba038 | e2e |
| 4 | The source draft was deleted by index - a shifted index could delete the wrong draft | 7eba038 | e2e |
| 5 | Out of office: a rejected change (or No to replacing another Sieve script) threw away what was typed | 7eba038 | e2e |
| 27 | List columns cut by bytes: accented / wide names misaligned columns and could split a UTF-8 character | 44e98b6 | e2e |
| 28 | Body: no word wrap, no sign a line was cut off, tabs not expanded | 44e98b6 | e2e |
| 29 | No Find and no copy in the body, View source or Help | 44e98b6 | e2e |
| 35 | A raw (8-bit) UTF-8 From header crashed `list` - the folder looked empty; RFC 2047 sender names were shown encoded | 44e98b6 | unit, e2e |
| 36 | Unreadable colours under themed terminals (Windows Terminal Dark+): dialog titles white on grey, default button light cyan on green, the list highlight white on light cyan (1.2:1) | ccb1006 | e2e |
| 38 | IMAP: after a delete, a blank "N (no subject)" ghost row took the message's place (the serve connection had not seen the EXPUNGE) | d8d605f | unit |
| 37 | CJK text drawn with a space after each character ("日 本 語") - tvision's validateCell wiped wide-char trail cells | 7c7017a | e2e |
| 7 | Esc did not close Help / Source / Parts / Address book, though Help said it did | 00c1ae9 | e2e |
| 8 | Clicking the current folder, or Up on the first one, reloaded it and dropped the filter | 00c1ae9 | e2e |
| 9 | Clicking the current message, or Up on the first, re-fetched it and scrolled the body to the top | 00c1ae9 | e2e |
| 11 | Compose labels showed Alt-T / C / B / S but were not linked to their fields | 00c1ae9 | e2e |
| 12 | A folder opened on its oldest message, newest mail off-screen at the bottom | 00c1ae9 | e2e |
| 14 | Any reload (pull, draft save, send, mark) scrolled the body you were reading back to line 1 | 00c1ae9 | e2e |
| 19 | "Working..." covered only part of the status line, leaving F-key hints showing past it | 00c1ae9 | manual |
| 22 | The delete prompt named the message by backend index, offered Cancel on a yes/no question, and did not say that deleting from Trash is permanent | 00c1ae9 | e2e |
| 26 | Saving the signature on close showed an extra "Signature saved." box | 00c1ae9 | e2e |
| 30 | Help described an "L / R column" the folder pane does not have | 00c1ae9 | e2e |
| 31 | The F2 menu item was labelled "Send draft" though it sends the compose window | 00c1ae9 | e2e |
| 34 | The backend restart budget (3) never reset - three hiccups over a long session left every call on the slow shell path | 00c1ae9 | manual |
| 40 | Sent folder: blank date and sender columns - send left Date / Message-ID to the server, which adds them only to the delivered copy, so the Sent copy had none (and no From); the list also showed your own name, not the recipient | 394d25f | unit |
| 41 | Help > About cut its credits off (messageBox holds about five lines) and called tvmail a client "for a local mailbox"; no link to the repo | e915646 | e2e |
| 43 | A terminal resize (SIGWINCH) during a backend round trip was taken for a dead co-process: the call fell back to the slow shell path, and during start-up tvmail gave up the warm backend for the whole session | 62347cf | e2e (case now in tvchat: the fix is tvcommon's CoProcess) |
| 44 | With a chat account connected, any open menu closed by itself within a quarter second: idle() posted the 250 ms chat poll as a command, and any command ends a tvision menu.  The background pull's cmPullDone could do the same, and under a dialog it was swallowed, so that pull's summary and reload never came (found by reading) | 611efde | e2e (moved to tvchat with chat: see tvchat/BUGS.md) |
| 45 | The mingw (native Windows) build did not compile: `main()` calls `setenv()` for ESCDELAY (since 668ea6b), which mingw does not have - unnoticed because nobody had the cross compiler at hand | 1e43880 | build |
| 46 | Chat SASL sent the wrong password: tvchat-backend borrowed the mail backend's `~/.mu-tickets` lookup, which falls back to the first entry for the host whatever its user, so the mail ticket `*://chris:...@cpbee` was sent as `chris-pollitt`'s password ("Invalid account credentials", then the reserved nick became `chris-pollitt_`), and the right one in `~/.netrc` was never read.  `~/.netrc` did not check the login either.  Now both count only an entry for the SASL login, and a failed login says where its password came from.  Found in the first live check on cpbee's Ergo | b97d83c | unit (moved to tvchat with chat: see tvchat/BUGS.md) |
| 47 | Intermittent failure of `e2e_regress` #4 `source_draft_found_by_content`: the case read the drafts folder a fixed 1.5 s after Delete and after Save draft, but those run a cold one-shot backend (a Python start under Cygwin is slow and uneven), so a slow start looked like "DRAFT-A was not deleted" / "saving deleted the wrong source draft".  The app was right; the test raced it.  Reproduced on demand by holding the backend up 2 s.  (The two #28 / #29 failures seen on Oct 23 were a real regression of the folder table, fixed the same day) | flaky-e2e | e2e (#4 now runs with `delete` and `save-draft` held up 2 s via `Tv(slow=...)` and waits for the result; #26 waits for its file; the whole suite also passes with every one-shot backend call held up 1.5 s) |
| 6 | Ctrl-D / Ctrl-R / Ctrl-N are status-line and menu keys, caught before the focused view, so in a compose or signature editor they opened "Delete message?", a reply or a new message instead of acting as editor keys (cursor right, page up).  tvision cannot grey a command above 255, so while an editor window is active the app's menu bar and status line (`TMailMenuBar`, `TMailStatusLine`) let those three keys through | 7440b06 | e2e |
| 15 | The pull result popped up a modal box whenever the background pull ended - while typing in a compose window, keystrokes went to it.  Already fixed by the toasts (c41a923, e6aa74f: a toast takes no focus and no key, and the periodic pull is quiet when it finds nothing); found stale in this list on Nov 11 | c41a923 | e2e (`toast_appears_goes_away_and_is_kept`, e2e_tui f3-background-pull) |
| 33 | A backend reply slower than 8 s froze the UI for the rest of it: `stop()` waited for the old backend to finish, and then the request was asked again, so a slow backend cost its whole time twice.  Now a call that gets no answer in time lets the backend go (pipes closed, reaped later, never waited for), is not asked again, and returns "no answer"; the next call starts a fresh backend.  The wait is 8 s, or `TV_CALL_TIMEOUT_MS` (the test's knob).  Shared with tvchat (`tvcommon/src/coprocess.cpp`) | 7440b06 | `backend_timeout` (tests/backend_timeout.sh) |
