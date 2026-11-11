# tvcal

A [Turbo Vision](https://github.com/magiblot/tvision) calendar and to-do manager that works with
[tvmail](../tvmail/README.md) and [tvchat](../tvchat/README.md), built on what they share
([tvcommon](../tvcommon/README.md)). The calendar lives **on the server, like the mail and the chat**:
Radicale (a small CalDAV server) on the mail / chat host, so every machine sees the same events and
to-dos. Without a server it keeps one local `.ics` file.

```
 File  Calendar  To-do   Edit  Window  Help
╔═════════════════════════════════ tvcal ═══════════════════════════════════╗
║──[ Services ]──────────────────│──  Agenda from Tue 2026-11-03  ──────────║
║ Calendar                      1│Tue 2026-11-03  today                   ▲║
║ To-do                         2│  12:30        Lunch with Ann           ■║
║──  November 2026  ─────────────│Thu 2026-11-05                           ▒║
║  Mo Tu We Th Fr Sa Su          │  14:00-15:00  Dentist @ Clinic          ▒║
║ 26 27 28 29 30 31  1           │──  Event  ──────────────────────────────║
║  2  3* 4  5  6  7  8           │Lunch with Ann                           ║
║  9 10 11 12 13 14 15           │                                         ║
║                                │When:      Tue 2026-11-03  12:30         ║
║                                │Reminder:  15 min before                 ║
```

## What it does

- **Three panes**, as in tvmail and tvchat: services (Calendar, To-do) on the left with the month under them, the
  items of the service top right, the item under the bar shown whole (notes in full) bottom right. `Tab` moves between the panes; the arrows, `PgUp` /
  `PgDn`, `Home` (today), `Enter`, `Ins`, `Del` and `Space` do what you would guess (F1 has the list).
- **Events**: title, date, start / end (empty start = all day), several days, place, notes (a tall text box), a repeat
  (daily, weekly, monthly, yearly, every N, until a date) and a reminder in minutes before.
- **Inbox and Reminders**: the Inbox lists what tvmail and tvchat asked to add; Reminders lists what will remind
  you in the next two weeks, soonest first (Enter goes to the event or to-do).
- **Notes**: a title, categories and free text of any length (a tall text box), newest first; stored as
  standard iCalendar journal entries on the server, so every machine has them. Floyd's `!note` writes here.
- **To-dos**: text, due date, priority 1-9, categories, notes; `Space` ticks one off; the Due column
  shows `!` when overdue. View > Show completed brings the done ones back.
- **Reminders** fire once, show here as a toast, and are passed on to tvmail and tvchat as toasts too
  (whichever you are looking at). To-dos due today or overdue are announced the same way.
- **Standard iCalendar** (RFC 5545) throughout: VEVENT, VTODO, VALARM, RRULE (including `BYDAY`),
  `EXDATE`. Properties tvcal does not know are kept when it saves an item. File > Import / Export moves
  `.ics` files to and from any other calendar program.
- **Find** (Ctrl-F), **Go to date** (Ctrl-G: `2026-11-05`, `11-05`, `tomorrow`, `+30`, `fri`).

## With tvmail, tvchat and Floyd

| from | what | how |
| --- | --- | --- |
| tvmail | **Message > Add to calendar** | an invitation (an `.ics` part) arrives with its time and place filled in; any other mail becomes an event named for its subject |
| tvmail | **Message > Add as to-do** | "Subject (from Name)" |
| tvchat | **Chat > Add selection to calendar... / as to-do...** | the lines selected in the log |
| tvcal | **Calendar > Send to > Mail...** | an `.ics` mailed through tvmail's own account |
| tvcal | **Calendar > Send to > Chat** | tvchat says title, day, time and place in its channel, once you agree there |
| tvcal | reminders, due to-dos | toasts in tvmail and tvchat |
| Floyd | `!todo`, `!todo add TEXT`, `!todo done N`, `!cal week`, `!event fri 14:00 Dentist` | the **same** list and calendar, from tvmail's Floyd (F7), from tvchat's Floyd for his admins and the owner's own session, and from tvcal's own **F7** |

Nothing another program asks for is added without your yes: tvmail and tvchat leave a one-line request
file for tvcal (`~/.local/state/tvcal/requests`, even when tvcal is not running), the status line shows
"N incoming", and the **Inbox** service lists them, each shown whole; Enter opens one with its dialog filled in: Add, Skip or Discard.
Other people's Floyd lists stay in Floyd's own files; only the owner's is the calendar's.

## Where the calendar lives

`~/.config/tvcal/tvcal.conf` (all optional; see [configure/tvcal.conf.example](configure/tvcal.conf.example)):

```ini
[server]
url  = https://cpbee.cmnet:5232/chris/calendar/
user = chris
ca   = ~/.config/tvcal/cpbee-ca.pem      ; a self-signed server's certificate
```

The password is `$TVCAL_PASS`, `~/.mu-tickets` (the file tvmail and tvchat share; `POSIX/net-utils/netrc-to-tickets` moves old `~/.netrc` logins there) or `~/.netrc` — never the configuration file. `configure/configure-radicale.sh`
does both halves: `server` on cpbee (installs and configures Radicale, TLS, the user, the calendar), `client`
on each machine (the `[server]` section, the CA, the password in `~/.mu-tickets`, a login check).

With a `[server]`, every verb reads the collection (one GET) and sends back only the items that changed
(PUT, DELETE). A copy in `~/.local/state/tvcal/cache.ics` serves reads for 15 s and while the server cannot
be reached; changes made then are refused with a message, never lost quietly. Two machines editing the
*same item* at once: the last save wins.

## Plumbing

`tvcal` is the Turbo Vision front end; `tvcal-backend` (Python, stdlib only) does everything else and runs
as a co-process like tvmail's and tvchat's: iCalendar, repeats, reminders, CalDAV, the request inbox,
Floyd. Every verb also works from the shell (`tvcal-backend todos`, `range 2026-11-01 2026-11-30`,
`add-todo --due fri -- Pay the bill`, `ics`, `import FILE`; `tvcal-backend --help`).

Times are the machine's local time. An imported time with a `Z` is converted; a `TZID` is taken as local
(see TO-DO). Editing a repeating event changes the whole series.

## Building and testing

[INSTALL.md](INSTALL.md). `./build.sh`, `./test.sh` (backend unit tests and the `--selftest`);
`python3 tests/e2e_cal.py --bin build/tvcal --backend-dir backend` drives the real program in a pty.

Files: [HISTORY.md](HISTORY.md), [BUGS.md](BUGS.md), [TO-DO.txt](TO-DO.txt).
