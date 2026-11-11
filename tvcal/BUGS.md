# Bugs

## Open

None known. tvcal has been run against the real Radicale on cpbee (issue #26, closed 2026-11-11), as well as the fake CalDAV server in the tests.
Open bugs are GitHub Issues: https://github.com/chrispollitt/TUIs/issues?q=is%3Aissue+label%3Abug+label%3Atvcal

## Squashed

1. **A time typed into the dialog lost its last character.** tvision's `TInputLine(bounds, n)` keeps `n - 1`
   characters, so the 5-character time field held "12:3". `addField` asks for one more than the limit.
   Guarded by `e2e_cal.py` new_event_shows_in_the_agenda.
2. **"N incoming" did not appear until something else redrew the status line.** `incomingRefresh()` redraws it.
   Guarded by `e2e_cal.py` incoming_request_is_added_on_a_yes.
