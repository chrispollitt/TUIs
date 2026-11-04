# History

## 0.1.0 (Nov 3, 2026) - first version

- tvcal: month, agenda and to-do list; events with repeats and reminders; to-dos with due dates and
  priorities; Find, Go to date; standard iCalendar throughout; Import / Export `.ics`.
- The calendar on the server: CalDAV (Radicale) from `tvcal-backend`, a local copy for reads while it is
  down, a local `.ics` file when there is no server. `configure/configure-radicale.sh` sets up both halves.
- With tvmail and tvchat: Message > Add to calendar / Add as to-do (tvmail), Chat > Add selection to calendar /
  as to-do (tvchat), Calendar > Incoming... asks before anything is added; reminders go to both as toasts;
  Share > Mail invitation... and Share in chat.
- Floyd: `!todo`, `!cal`, `!event` on the calendar's own list, in tvcal (F7), tvmail and tvchat (for the owner).
- tvcommon: the spool can send to several apps (`spoolFromConf(conf, self, "a,b")`); `peerRequest()` writes a
  request file for another app; `tvfloyd.py` gets `CalNotes` and `make_notes()`.
