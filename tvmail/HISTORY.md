# Why tvmail exists

Short version: I have wanted a terminal mail client I actually *liked* for about
twenty years, and I finally decided to build one.

## The long-running itch

I run my own mail. Not a lot of it, but it's mine — an authenticated smarthost,
a POP mailbox, the usual. What I never had was a **client** on my
Windows/Cygwin box that felt right. Every few years I'd take another run at it:

- **pine → alpine** — the client I have the most muscle memory for. Alpine
  freed it from the old licensing weirdness but never really moved it forward.
- **elm**, **mailx**, **mutt** — mutt is where most people land, but you don't
  so much *use* mutt as maintain a `.muttrc` for six months.
- My own half-projects: a fork of **ssmtp** with TLS bolted back on
  (`ssmtp-reloaded`), **femtomail** (a tiny sendmail→mailbox shim), a Python
  **`mailx.py`** wrapper around `/usr/sbin/sendmail`. Each solved one slice —
  relay, or local delivery, or the client front end — and none was the whole
  thing.

None stuck. I'd get mail flowing for a weekend and drift back to webmail.

## The unlock

The part that had always felt hard — send *and* receive, authenticated TLS,
local `/var/mail` delivery, a one-way rewrite so replies come back somewhere
real — turned out to be a solved problem the moment I stopped hand-rolling it
and installed a real MTA. **exim** had been sitting in the Cygwin package repo
the entire time. One `setup -P exim`, a config file, and mail just worked:
`sendmail` for outbound, `local_delivery` into `/var/mail`, a small Python
`pop-pull` for inbound.

Once mail was flowing locally, the old want came right back: now I wanted a
*nice* way to read it. And the look I kept picturing was **Turbo Vision** —
Borland's DOS UI framework. Dropdown menus, dialog boxes with drop shadows, the
cyan desktop, `F10` for the menu bar.
[magiblot/tvision](https://github.com/magiblot/tvision) keeps that API alive and
portable, so it was suddenly feasible.

## Building it

tvmail came together in layers, an evening or two each:

1. a stdlib-only Python backend (`tvmail-backend`) that does all the
   mbox / MIME / send grunt work, so the C++ can stay small;
2. a Turbo Vision shell around it — menu bar, a message list, a scrolling
   viewer;
3. a real in-app composer built on `TEditor` (no more shelling out to `vi`);
4. the standard three-pane layout — folders, message list, message body;
5. drafts, an address book, a signature editor, `~/dead.letter` handling.

Getting tvision to build on Cygwin took a few small patches (`FIONREAD`,
`strupr` linkage, `_GNU_SOURCE`) — it targets Linux / macOS / MinGW, not
Cygwin — but nothing structural.

## The one rule

Stay out of the way of `mail(1)`. tvmail reads the same `~/.mailrc` GNU
Mailutils reads: `set folder`, `set DEAD`, the `alias` / `group` address book.
Drafts live in `~/Mail/drafts` so `mail -f +drafts` still opens them.
`~/dead.letter` is honoured. It's a friendlier face on the same mailbox, not a
walled garden.

## Is this over-engineered?

Completely. It's a Turbo Vision app, in 2026, to read a mailbox that usually has
three messages in it. That's the point. It's a labour of love for a workflow
I've wanted since roughly the Clinton administration, and now it exists.

— Chris
