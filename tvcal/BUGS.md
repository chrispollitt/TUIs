# Bugs

## Open

None known. Not yet exercised against a real Radicale (only a fake CalDAV server in the tests): see TO-DO 1.

## Squashed

1. **A time typed into the dialog lost its last character.** tvision's `TInputLine(bounds, n)` keeps `n - 1`
   characters, so the 5-character time field held "12:3". `addField` asks for one more than the limit.
   Guarded by `e2e_cal.py` new_event_shows_in_the_agenda.
2. **"N incoming" did not appear until something else redrew the status line.** `incomingRefresh()` redraws it.
   Guarded by `e2e_cal.py` incoming_request_is_added_on_a_yes.
