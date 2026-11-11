# tvcal: the three-pane redesign (decided 2026-11-10)

tvcal gets the window tvmail and tvchat already have: a list of services on the left, the items of the chosen
service top right, and the chosen item in full bottom right.

```
+- Services -------+- Agenda from Tue 10 Nov -----------------------------+
| > Calendar    3  | Tue 10 Nov   09:30-10:00  Standup           (weekly) |
|   To-do       7  |              14:00        Dentist          Main St   |
|   Notes      12  | Wed 11 Nov   all day      Michelle: flight           |
|   Inbox       2  |                                                      |
|   Reminders   1  +- Event ----------------------------------------------+
|   Contacts       | Dentist                                              |
|                  | Tue 10 Nov 2026  14:00-15:00     Main St             |
| November 2026    | Repeats: no      Reminder: 30 min before             |
| Mo Tu We Th Fr Sa| ---------------------------------------------------- |
|    1  2  3  4  5 | Bring the insurance card.  Ask about the crown       |
| ...              | and whether the x-ray can be sent to Dr Lee.         |
+------------------+------------------------------------------------------+
```

## Decisions (Chris, 2026-11-10)
- Bottom-right pane is read-only, as in tvmail: it shows the whole item with its notes in full.  Enter (or `e`) opens the
  edit dialog.  Editing inline in the pane may come later.
- Notes are real items: standalone notes stored as VJOURNAL entries on the Radicale calendar collection, next to events and
  to-dos, so they sync to every machine and Floyd can read them.
- Services in the left list: Calendar, To-do, Notes, Inbox (what tvmail/tvchat asked to add), Reminders (what fires next),
  Contacts.  Each count is a badge; Inbox is highlighted when something waits.
- Every item type has a big free-text box (a multi-line memo, 6+ lines), in the edit dialog and shown whole in the details
  pane.  This replaces the one-line Notes field (tvcal/TO-DO.txt item 12).

## What each service shows
| Service   | Top-right list                          | Bottom-right details                                        |
|-----------|-----------------------------------------|-------------------------------------------------------------|
| Calendar  | agenda by day from the chosen date      | title, when, where, repeat, reminder, attendees, notes      |
| To-do     | open to-dos (done optional), by due/prio| title, due, priority, categories, notes                     |
| Notes     | newest first, title + first words       | title, date, categories, the whole text                     |
| Inbox     | requests from other apps                | the proposed item; Add / Skip / Discard                     |
| Reminders | next reminders by time                  | the event or to-do behind it                                |
| Contacts  | (existing contacts window, for now)     | -                                                           |

Under the service list, while Calendar is selected, a small month grid (moves the agenda; the counts per day stay).
Keys as in tvmail: Tab moves between panes, Enter on the list opens the editor, Ins/n new, Del delete, Space toggles a
to-do, `t` today, `g` go to date, `/` find.

## Build order (one at a time)
Status 2026-11-10: steps 1, 2 and 3 are built (the window; Notes as VJOURNAL with the multi-line box and Floyd's !note).
1. The window: service pane, item list pane, detail pane (reusing TMailText from views.h); Calendar and To-do ported; the
   month grid under the services; menus follow the service chosen.  Edit dialogs unchanged.
2. Notes: tvcal-backend add-note / set-note / notes / show / rm on VJOURNAL (check the collection accepts VJOURNAL; Floyd's
   `!note` and notes.json move over); the Notes list.
3. Edit dialogs: a tall multi-line memo for event, to-do and note (TO-DO 12 done).
4. Inbox and Reminders services (Calendar > Pending... goes away).
5. Contacts as a service: contactswin.cpp split into reusable list/detail panes in tvcommon; until then the entry opens
   the existing window.
Each step: update tests/e2e_cal.py, help.cpp, README, man page, HISTORY.md.

## Rules kept
tvcal still runs alone: no tvmail/tvchat needed (Inbox is empty without them); offline = reads from the cache.
