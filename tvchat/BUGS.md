# tvchat bugs

Every squashed bug gets a row here and a regression case (in `tests/`). The
next free number is the highest in both tables plus one.

## To be squashed

| # | Bug | Notes |
|---|-----|-------|

## Squashed

| # | Bug | Fixed in | Test |
|---|-----|----------|------|

## From tvmail

Found while chat was still part of tvmail, and numbered in tvmail's
[BUGS.md](../tvmail/BUGS.md). Their regression cases came over to tvchat's
tests.

| tvmail # | Bug | Fixed in | Test |
|---|-----|----------|------|
| 43 | A terminal resize (SIGWINCH) during a co-process round trip was taken for a dead backend (`EINTR`): during a start the co-process was given up for the session.  The fix is tvcommon's `CoProcess`; only tvchat starts one inside the running app, so the case is here | 62347cf | e2e (`resize_keeps_backend`) |
| 44 | With a chat account connected, any open menu closed by itself within a quarter second: idle() posted the 250 ms chat poll as a command, and any command ends a tvision menu.  Under a dialog the poll was swallowed.  Now idle() posts nothing while a menu or a dialog is up; the backend's queue holds the events | 611efde | e2e (`menus_stay_open_while_chat_polls`) |
| 46 | Chat SASL sent the wrong password: the backend borrowed the mail backend's `~/.mu-tickets` lookup, which falls back to the first entry for the host whatever its user, so a mail ticket `*://chris:...@cpbee` was sent as `chris-pollitt`'s password.  `~/.netrc` did not check the login either.  Now both count only an entry for the SASL login, and a failed login says where its password came from | b97d83c | unit (`test_sasl_password_only_for_its_login`) |
