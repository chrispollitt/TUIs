# tvmail services: mail, listserv, usenet (design, 2026-11-19)

tvmail gets a fourth pane and learns two more kinds of service besides mail: a **listserv**
(mailing lists, LISTSERV-style) and **Usenet** (a LAN-only news server with an archive of
the old days).  The servers are set up by two new directories beside `mail-setup` and
`chat-setup` in chrispollitt/POSIX.

Status: **steps 1-4 built** (the Services pane; the Usenet service against a fake NNTP server; the news server and importer in
POSIX `news-setup`, live on cpbee; the Lists service in tvmail against a fake IMAP server); the list server (step 5,
`lists-setup`) is design.  Where step 2 differs from the text below: the
Usenet commands are a **Message > Usenet** submenu (enabled only while Usenet is selected), not a top-level menu; the
NNTP client lives in `tvcommon/py/tvnntp.py`; the group's unread count is an upper bound where its article numbers
have gaps (exact otherwise); Followup to an *archive* group (the `lan.*` redirect) waits for the server step, since
only the server knows which groups are read-only.  Decisions marked (Chris) were made on 2026-11-19;
the rest are proposals to confirm or overturn at review.

## The window

```
+- Services ---------+- Messages ------------------------------------------+
| > Mail         3   | N  Tue 10 Nov  alice        Re: invoice             |
|   Lists        12  |    Mon  9 Nov  bob          lunch?                  |
|   Usenet     1204  |                                                     |
+- Folders -----------+- Message ------------------------------------------+
| Folder  Total  New | From: alice ...                                     |
| inbox      41    3 |                                                     |
| drafts      0    0 | (decoded text; the same pane for all three kinds)   |
| ...                |                                                     |
+--------------------+-----------------------------------------------------+
```

- Left column: **Services** (top, a few rows) over **Folders**; right column: **Messages**
  over **Message detail**.  Tab order: Services, Folders, Messages, Detail.  Same title style
  and `[ bracketed ]` active pane as now; the Services rows carry an unread badge like tvcal's.
- What "Folders" holds depends on the service: mailboxes for Mail, the lists for Lists,
  the subscribed newsgroups for Usenet.  Messages and Detail are the pane classes that exist
  today (`TMsgPane`, `TContentPane`), fed by whichever service is selected.
- With only one service configured the Services pane is hidden (View > Services shows it);
  an install that never heard of lists or news looks like today's tvmail.  tvcal already has
  a Services list, so the three apps then share a vocabulary.
- Menus follow the service as tvcal's do: Message > Reply is a reply in Mail and Lists and a
  Followup in Usenet; a Lists menu (Subscribe, Unsubscribe, Digest, Available lists...) and a
  Usenet menu (Subscribe to group..., All groups..., Catch up, Cancel my article) appear only
  while that service is selected.

## What each service is

| | Mail (today) | Lists | Usenet |
|---|---|---|---|
| Folders | inbox, drafts, sent, ... | the lists you subscribe to | the groups you subscribe to |
| Backend talks | mbox, or IMAP + SMTP | **IMAP** (same login as Mail) + SMTP | **NNTP** |
| Read state | in the mailbox (`N` flag) | in the mailbox (IMAP flags) | `.newsrc`-style ranges, per user, client side |
| Compose | To / Cc / Bcc | To is the list address | Newsgroups / Followup-To |
| Message list | flat, sortable | flat, sortable | **threaded by References** (View > Threads), flat as an option |
| Delete | to trash | to trash | none (Catch up marks read; Cancel only your own article) |

### Lists

How a LISTSERV-style list reaches a reader: you mail `SUBSCRIBE name First Last` to
`listserv@host`, list mail then arrives in your mailbox, and you leave with `SIGNOFF name`.  So the
reading side is mail, not a new protocol:

- The server files list mail into each subscriber's own IMAP folder **`Lists/<name>`** with one
  global Dovecot `sieve_before` rule keyed on the list's `List-Id`.  Subscribers get real read
  flags, the Mail service's IMAP code reads them, and no per-user Sieve is needed.  Prerequisite:
  mail-setup's `configure-sieve.sh` (Pigeonhole plus Postfix delivering through `dovecot-lda`).
- tvmail's Lists service = the folders under `Lists/`, plus **Available lists...**, which mails
  `LIST` to `listserv@` and shows the reply in a dialog; Subscribe / Unsubscribe / Digest are mails to
  the same address (a `listserv@` reply is a toast, never a blocking box).
- **The contract between tvmail and `lists-setup/bin/listserv`** (step 4 is built to it; step 5 writes the other side).
  A command is a mail to `listserv@`: Subject and first line `LIST`, `SUBSCRIBE name First Last`, `SIGNOFF name`,
  `SET name DIGEST` or `SET name NODIGEST`.  It is answered by **a mail to the sender** with `In-Reply-To` the
  command's `Message-ID` (tvmail finds it in the INBOX by that header, marks it read and leaves it); the first line of the
  text is the verdict, `OK ...` or `ERROR ...`, and for `LIST` every line after it is `name<2+ spaces>description`.
  On `OK` after SUBSCRIBE tvmail creates `Lists/<name>` itself, so the list shows before its first mail; the server's
  Sieve rule files list mail there (`fileinto :create`).  A reply or new message in Lists goes to the list's `List-Post`
  address, else `<name>@<domain>`.
- Later: a read-only public archive namespace (a Dovecot `#public/` tree) so a list can be browsed
  without subscribing.  Per-user read state in a shared namespace needs Dovecot ACLs with
  `write-seen`; that is why it is not phase one.

### Usenet

- `tvmail-backend` gets its own small **NNTP client** (stdlib `socket`/`ssl`, about 200 lines):
  `nntplib` was deprecated in 3.11 and removed in 3.13, and the backend must keep working on
  Python 3.6+.  Verbs used: MODE READER, LIST ACTIVE/NEWSGROUPS/OVERVIEW.FMT, GROUP, OVER (XOVER
  as fallback), ARTICLE/HEAD/BODY, POST, DATE.
- **A group can hold 100 000 articles.**  The backend does not list a whole group the way `list`
  lists a mailbox: it loads the newest N (default 500, `[news] window`) and View > Load older
  fetches the next window.  Counts per group come from `GROUP` (low, high) minus the user's
  read ranges, so the Folders table stays cheap.
- Read state: `~/.local/share/tvmail/newsrc-<server>` in the classic `group: 1-4000,4010` form
  (a `.newsrc` anyone can read).  Catch up writes `1-high`.
- Old articles are ugly and the viewer must cope: bang-path `From:` (`utzoo!henry`), dates with
  two-digit years or none (the existing "no readable date sorts first" rule holds), no
  `Message-ID`, `Path:` noise, 7-bit charset claims that are really Latin-1.  The overview line
  is shown as is; the Detail pane picks a decoding by trying UTF-8, then Latin-1.
- Posting needs a compose mode with `Newsgroups:` and `Followup-To:` instead of To/Cc/Bcc.
  Archive groups are read-only on the server (INN active flag `n`), so Followup on an archive
  article offers a **new thread in `lan.*`** with the quote, not a post into 1987.  Reply by mail
  is greyed out in archive groups (the addresses are dead).

## The backend

`tvmail-backend` is one 2 946-line Python file whose verbs take an `MBOX` shorthand.  The C++ side
has the folders hard-coded (`gFolders[]` in `mailwin.cpp`) and four globals, `gMbox`, `gRemote`,
`gHost`, `gFolderIdx`, as the state of "where am I".  The change:

1. **A service object on each side.**  C++: `struct Service { kind, name, label }` and a
   `gServices` vector; `gFolders` becomes the selected service's folder list, fetched from the
   backend, not constant.  Python: `MailService` (the existing mbox/IMAP code, moved behind the
   interface, not rewritten), `ListService` (IMAP under `Lists/`), `NewsService` (NNTP).
2. **A global `--service NAME` option** on every verb (default `mail`), so the existing verbs and
   `serve` framing do not change shape; plus a new `services` verb (names, kinds, badges) and
   `folders` (what `counts` hard-codes today: name, total, unseen, flags).
3. **The verbs stay the same**: `list`, `show`, `raw`, `mark`, `counts`, `send`.  New ones are
   service-specific: `lists-available`, `lists-subscribe`, `lists-signoff`, `lists-digest`;
   `news-groups [--all]`, `news-subscribe`, `news-catchup`, `post`, `cancel`.
4. The NNTP client is a sibling module of the backend loaded the way `tvfloyd.py` is (copy in
   `share/tvmail/`, found beside the backend or in `../share/tvmail`), so the single-file install
   keeps working.

Configuration, in the existing `tvmail.conf`:

```
[services]
order  = mail, lists, news      ; absent = just mail
[lists]
address = listserv@cpbee.cmnet  ; required; IMAP and SMTP are those of [imap] / [smtp] (no [lists] host)
prefix  = Lists                 ; the IMAP folder the lists are under
domain  =                       ; list addresses are NAME@domain (default: the domain of address)
from    =                       ; who the commands come from (default [smtp] from)
realname = Chris P              ; the First Last in SUBSCRIBE
wait    = 20                    ; seconds to wait for the answer mail
[news]
host    = cpbee
port    = 119
window  = 500
sort    = alpha                       # the group list: alpha (default) or newsrc (subscription order)
subscribed = lan.misc, comp.os.linux
```

Credentials stay out of the file (rule unchanged).  A LAN news server needs none for reading;
posting is allowed by address.  Identity on posts is `[smtp] from`.

## Servers (chrispollitt/POSIX)

Two directories shaped like `chat-setup`: a README, `scripts/`, `tests/` (shell tests in a sandbox
`$HOME`, `--print` modes), master-only and Linux-only, and each script re-runnable.

```
news-setup/                           (Chris: INN2)
  scripts/configure-inn.sh            installs inn2; LAN-only: no peers, no outgoing feeds, readers.conf
                                      by LAN address (read for all, post to lan.* only), `n` on archive
                                      groups, expire.ctl never for the archive; opens TCP 119 for the LAN
                                      via ../net-utils/lan-port (the talkd / delta pattern)
  scripts/create-groups.sh            archive groups from a list, read-only (flag n)
  bin/fetch-archive                   optional download of an Internet Archive item (names, sizes, md5)
  bin/news-import                     stdlib Python: mbox / maildir / tree / tar / zip / rnews -> IHAVE
                                      to innd; groups from a list file, dedupe by Message-ID, repair
                                      Message-ID / Path / Date, strip Control:, --limit, --dry-run, resumable
  groups.d/                           curated lists of "key groups": net.*, comp.*, rec.*, alt.folklore.computers ...
  tests/
lists-setup/                          (Chris: mlmmj + a listserv@ front-end)
  scripts/configure-lists.sh          installs mlmmj, Postfix transport for the list domain, the global
                                      `sieve_before` rule, the `listserv@` alias; checks mail-setup is there
  bin/listserv                        stdlib Python command handler fed by Postfix:
                                      SUBSCRIBE / SIGNOFF / SET ... DIGEST / LIST / REVIEW / INDEX / HELP;
                                      replies by mail; drives mlmmj (mlmmj-sub, mlmmj-unsub, mlmmj-make-ml)
  bin/listctl                         create / remove / configure a list (owner, moderated, digest, archive)
  tests/
```

- INN's `innd` has to accept articles with 1980s dates (`artcutoff: 0` in `inn.conf`) or the import is
  silently dropped; the importer's `--dry-run` reports what INN would reject.
- Archive content (Chris: public historical archives): the Internet Archive's Usenet collections and any
  mbox dumps pointed at.  Checked when step 3 was built: the UTZOO archive of 1981-1991 (Henry Spencer's)
  was **taken down from the Internet Archive in 2020** after legal demands (third-party mirrors exist); an
  item `usenethistorical` holds mbox files per group as zip files.  Names, sizes and licensing were not
  verified, so `bin/fetch-archive --list` shows what an item holds today and downloading asks first.  The
  server is LAN-only, which keeps it personal use.  The archive keeps the **original group names** (the
  pre-1987 `net.*` hierarchy included), local discussion lives in `lan.*`.
- The importer feeds innd with IHAVE from 127.0.0.1 (a peer in `incoming.conf`), not POST: Path, Date and
  Message-ID stay as they were.  It strips `Control:`, `Supersedes:`, `Xref:` and `Approved:`, so an old
  `newgroup`, `rmgroup` or `cancel` cannot act on the server.
- Opening 119 and any list SMTP port to the LAN is an exposure change: per the master-session rules that
  goes to Chris before it runs (the script ships with `--print`, and the firewall step is its own flag).
- Client-side setup is a script too, not notes (the "every manual step is a script bug" rule):
  `news-setup/scripts/configure-tvmail-news.sh` and `lists-setup/scripts/configure-tvmail-lists.sh`
  write the `[news]` / `[lists]` stanzas, as `configure-tvchat.sh` writes `tvchat.conf`.

## Build order

Each step on its own branch, tested, with a stop for Chris's review.  The two servers touch only
POSIX and need nothing from the UI, so they can run in parallel with steps 1 and 2.

1. **TUIs, the window, mail only.**  Service object and Services pane; folders come from the backend;
   `--service mail`, `services`, `folders`.  No behaviour change for a one-service install; the e2e
   cases, help, README, man pages, HISTORY updated (4 panes).
2. **TUIs, Usenet.**  NNTP client with a fake NNTP server in the tests (the way `tests/lib/fakeservers.py`
   fakes POP3/SMTP/IMAP in POSIX), read, thread view, load older, newsrc, post and followup.
3. **POSIX, news-setup.**  INN2 and the importer, tested against `--print` and a fake `rnews`; live on cpbee
   after the firewall decision.
4. **TUIs, Lists.**  The Lists service over IMAP; Subscribe / Unsubscribe / Available.  *Built* (branch `tvmail-lists`),
   against a fake IMAP server and a stand-in listserv in the tests.
5. **POSIX, lists-setup.**  mlmmj, the `listserv@` handler, the Sieve rule.
6. **Docs and the release**: README rows in TUIs and POSIX, HANDOFF, a version bump (new feature: 1.4.0).

## Open points (for review)

- Lists: per-user `Lists/<name>` folders via `sieve_before` (proposed) versus a public archive namespace first.
- Hide the Services pane when only one service is configured (proposed), or always show it.
- Usenet followups to archive groups go to `lan.*` (proposed); the alternative is no followup at all.
- Cancel and moderation: only your own articles, only on `lan.*`.
- tvcal and tvchat could use the same `Service` object later; not part of this work.
