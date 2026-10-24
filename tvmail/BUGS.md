# tvmail bugs

Defects only - features and wishes live in [TO-DO.txt](TO-DO.txt).
Numbers (`#n`) are from the October 2026 code review, so a commit message or
note that says "#14" means the same thing everywhere; bugs found later get
the next free number.  The **Test** column says what guards a fix against
coming back: `unit` (tests/test_backend.py), `e2e` (tests/e2e_regress.py,
one case per bug; tests/e2e_tui.py is the older smoke test), `build`
(tests/mingw_compile.sh, the mingw cross-compile), or `manual` (checked on
screen, no automated test yet - the gaps a TUI regression suite should
close).

## To be squashed

| # | Bug | Notes |
|---|-----|-------|
| 47 | **Top priority - intermittent test failures.** `e2e_regress` #4 `source_draft_found_by_content` fails now and then (seen Oct 22 and Oct 23: 1 run in 5 once an extra backend round trip was added to the folder counts; the screen showed "Working..."); #28 and #29 also failed once in a full run and passed on rerun.  Every case is timing-based (fixed `settle` waits), and the app blocks on each backend call, so a slow spawn under load tips them.  A flaky suite hides real regressions | Make the cases wait on a condition, not a delay; find out whether the app itself (not just the test) mishandles a slow backend (see #33) |
| 6 | Ctrl-D / Ctrl-R / Ctrl-N are status-line keys, caught before the focused view, so in a compose or signature editor they open "Delete message?", a reply or a new message instead of acting as editor keys | Fix: a compose-window status line (helpCtx) without them |
| 10 | Body pane: Home goes to column 0 but End goes to the last line; no page-up key to pair with Space | |
| 13 | View > Sort by date is mailbox order, not date order - moved or pulled mail lands out of date order | |
| 15 | The pull result pops up a modal box whenever the background pull ends - while you are typing in a compose window, keystrokes go to it | Fix: a status-line note |
| 16 | With "Unread first", reading a message re-sorts the list and moves it out from under the highlight | |
| 17 | Every filter keystroke and every arrow key in the folder pane waits on a backend round trip - felt over IMAP | Needs debouncing |
| 18 | Send, delete, move, purge, empty trash, save part / message show no "Working...", and run a cold one-shot backend | |
| 20 | Long text (send errors, backend output, the 8-line pull summary) is clipped in the small message box | Needs a sized dialog |
| 21 | Purge leaves the "Purge preview" window open after confirm / cancel / "nothing matches" | |
| 23 | The address book adds to the most recently opened compose window, not the focused one, and says "open a compose window first" when that one closed but others are open | |
| 24 | Every compose window is titled "Compose" - Window > List cannot tell them apart | Fix: title by subject |
| 25 | A backend error from `list` is dropped (non-tab lines skipped), so a failure looks like an empty folder; Address book / Parts likewise show an empty window | The raw-UTF-8 From crash (#35) hid behind this |
| 32 | Commands that cannot apply (Edit draft outside Drafts; Reply / Parts / Move on an empty list) are never greyed and silently do nothing | |
| 33 | A backend reply slower than 8 s freezes the UI for the rest of it (stop() waits for the old backend), then the request is retried | |
| 39 | The dialog drop shadow is still drawn in a terminal-theme colour (tvision applies shadowAttr directly, not through mapColor) | Cosmetic |
| 42 | Compose window header labels are under 3:1 contrast under Windows Terminal Dark+: the To / Cc / Bcc / Subject hotkey letters are themed white on light red (2.8:1), the label text white on cyan #00AFAF (2.7:1) | Found by e2e_regress #36; not scored there until fixed |

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
