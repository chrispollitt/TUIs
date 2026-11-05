# Design: central contacts and mail groups for the TUI suite via Radicale CardDAV

Status: FUTURE TO-DO. Start only after the centralized Delta Chat work (dcgw on cpbee, thin tvchat clients, multi-user)
reaches a satisfactory working state. Not started; nothing in the repo has been changed for it.

## Goal
One address book per person plus shared groups, kept on cpbee in Radicale (which already serves tvcal's calendars and also
speaks CardDAV). tvmail, tvchat and tvcal read it through one shared library. Decided against LDAP: too heavy for a
household and Radicale already runs.

## State of the repo when this was written (verify before starting)
- Radicale is used only by tvcal (calendars); no CardDAV/vCard code exists anywhere in the repo.
- tvchat's only "contacts" are Delta Chat key-contacts (`get_contacts`, tvchat/DESIGN.md "Groups").
- How tvmail stores/looks up addresses was NOT checked: read its current address code first.

## Layout
```
tvmail  --\                        Radicale (cpbee)
tvchat  ---+--> tvcontacts lib -->   /chris/contacts/      personal
tvcal   --/    (CardDAV client)      /michelle/contacts/
                                     /shared/family/       shared groups
```
Radicale rights file: each person owns their personal book; /shared/ is read-write for both.
Uses the Radicale login tvcal already has; no new credential.

## Pieces
1. `tvcontacts` library in tvcommon (next to tvfloyd): PROPFIND/REPORT to list, GET to read, PUT/DELETE to write; local
   cache keyed by ETag; offline reads from the cache, writes queued until cpbee is back.
2. Mail groups = vCards with `KIND:group` and `MEMBER:` lines (urn:uuid of members). A group expands to its members'
   addresses when sending in tvmail.
3. tvmail: To-field completion from contacts and groups; group expansion on send; "save sender" adds a contact.
4. tvchat: contact email -> Delta key-contact lookup ("chat with Michelle" from the book); a group vCard may SEED a Delta
   group via new-group, after which the Delta group stands alone (no ongoing sync). With the central design, lookup
   goes through dcgw's account for that user.
5. tvcal: attendee completion from the same books.
6. Floyd: sentinel checks the contacts collection; optional `!whois NAME`.

## Rules
- Conflicts: ETag + If-Match on PUT; on failure reload and retry or ask.
- Only our own tools write the books, so vCard-dialect differences between CardDAV clients stay manageable; keep group
  encoding simple and documented. Test against Radicale's actual behaviour.
- Bootstrap: one-off import script from wherever the current addresses live (to be found).

## Order of work
1. tvcontacts read path, cache, tests.   2. tvmail completion.   3. Groups expansion + write support.
4. tvchat link, then tvcal attendees.   5. Sentinel check, README/INSTALL notes, deploy via configure-radicale.sh
(extend it: per-user books, rights file).

## Open questions
- Where do the current addresses live (tvmail store, mail client, vCard files)?
- Michelle's shared/personal split; who may edit /shared/.
- Whether to keep a local copy as a fallback when Radicale is unreachable beyond the cache.
