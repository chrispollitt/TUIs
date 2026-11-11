# History

## Unreleased

- The three-pane window (tvcal/UI-REDESIGN.md, step 1): Services on the left (Calendar, To-do) with the month
  under them, the agenda or the to-do list top right, the item under the bar shown whole bottom right.  The
  Calendar and To-do menus switch to their service.  Inbox, Reminders and Contacts follow.
- Step 4: the Inbox service (what other apps asked to add; Calendar > Pending... became Calendar > Inbox, which goes
  there) and the Reminders service (`tvcal-backend upcoming [--days N]`, soonest first, nothing marked as reported).
- Step 3: events and to-dos have the same tall, multi-line Notes text box as notes (TO-DO 12).
- Notes (step 2): a third service.  A note is a VJOURNAL on the calendar server (title, categories, free
  text), edited in a multi-line text box.  tvcal-backend: `notes`, `add-note`, `set-note`; `--notesx` takes text
  with `\n` for a new line (a request is one line); show, find, rm, import and export know notes.  Floyd's
  `!note` / `!notes` are tvcal's notes now; his old notes.json notes move over the first time.

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
