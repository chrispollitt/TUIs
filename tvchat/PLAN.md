# Plan: Floyd on chat.u-l.ca

chat.u-l.ca is shared PHP hosting (Live Helper Chat is installed there). It can run PHP and serve
static files; it cannot run a daemon or open an IRC port. Floyd stays on cpbee and reaches out.

## Goals

1. **Floyd as an operator** on Live Helper Chat (LHC): visitors chat in a widget, Floyd answers.
2. **Floyd-pro**: a locked-down persona of the same code that answers questions for
   universal-laughter.com.
3. **A public home for tvchat users**: an IRC server people point their own tvchat at, with Floyd
   there to help and orchestrate (needs a host that can run a daemon: a small VPS).
4. **An admin-only bridge** between that server and cpbee.
5. **A tvchat web app**, hosted on chat.u-l.ca.

## What was decided

- Floyd *polls* LHC's REST API over HTTPS. Outbound only: nothing on the LAN is exposed.
  (LHC's webhooks / bot-calls-a-URL would need cpbee reachable from the internet.)
- LHC is a support desk, not a chat server: tvchat clients cannot log in to it. Goal 3 therefore
  needs a real IRC server elsewhere; chat.u-l.ca can serve its web client (gamja, static JS).
- One adapter, `protocol = lhc`, built like the mail adapter (polling, one conversation per
  correspondent). Floyd attaches with `attach = lhc/*`, as he does for `mail/*`.
- Visitors are anonymous: they are **never** admins. No `trust =` for them.
- Keys never go in the config file: `$TVCHAT_PASS_LHC` in `/etc/tvchat/floyd.env`, as for Delta Chat.

## Phase 1 - proof of concept (DONE 2026-11-01: live on chat.u-l.ca; the API is at https://chat.u-l.ca/index.php)

Done when a message typed in the LHC widget in a browser gets a reply from Floyd.

- `LhcAdapter` in `backend/tvchat-backend`:
  - `[account:lhc]` options: `url=`, `user=` (the operator), `department=` (ids, comma separated;
    empty = all), `poll=` (seconds, default 5), `name=` (what visitors see), `verify=`.
  - Each poll: list the open chats (pending, active, bot) in the departments; fetch the new
    messages of each; a visitor's message becomes a line in `lhc/<chat id>`; chats that have
    closed are dropped.
  - What Floyd says goes back as an operator message (`addmsgadmin`).
  - Limits: replies per chat per hour and in all, so a visitor cannot run up a bill.
- `check-config` knows the protocol; `floyd.conf.example` shows it; docs (README, HISTORY, DESIGN).
- Tests with a fake LHC server (like `FakeMail`): a visitor line is answered, an old message is not
  answered twice, a visitor is not an admin, a closed chat goes, the limits hold, a bad key is said.
- `TO_BE_RUN/floyd-lhc-on-cpbee.sh`: installs the backend, asks for the key (hidden), writes
  `floyd.env` and the `[account:lhc]` section, `check-config`, restarts, `DRY_RUN=1`.
- **Needs from Chris (chat.u-l.ca admin):** operator `floyd`, a REST API key for him, a department
  (its id), and Floyd set online.
- **Checked live, and adjusted if wrong:** the exact JSON shapes LHC returns (the adapter is written
  tolerantly from the published Swagger), and whether the first operator message accepts a pending
  chat or needs a `setchatstatus` first.

## Phase 2 - Floyd-pro for universal-laughter.com

Start with universal-laughter.com alone. For the beta, put the widget script from chat.u-l.ca on a test page
there (no DNS needed); a branded chat.universal-laughter.com only if wanted later (the host must answer for
that name and have a certificate for it).

- **2a - FAQ matcher (built):** `faq = FILE` on a second `protocol = floyd` account (`[account:pro]`,
  `attach = lhc/*`). Answers are the FAQ's words, nothing generated. Seed: `configure/faq.example.txt`, from
  https://laughteryoga.ca/frequently-asked-questions/. Medical questions get the "when not to take part" entry and
  "ask your doctor", never an opinion (its K: words find it).
- **To add to the FAQ (Chris):** prices and booking for private/corporate sessions, contact details for the
  fallback, where and when the clubs meet, the next leader training (date, cost).
- **2b - language model on top (later):** only after reading the real visitor questions from 2a; the FAQ is its
  only material; per-visitor and daily caps; a human handoff.
- Its own department and widget embed on the company site; a privacy line in the widget's start text.
- Review the first weeks of transcripts.

## Phase 3 - a public IRC server

- A small VPS: Ergo with TLS and accounts, Floyd as its bot (his `admins =` by services account).
- chat.u-l.ca hosts gamja (web IRC client) pointed at it, so visitors without tvchat can join.
- LHC stays on the site as the "talk to a human" widget.

## Phase 4 - the admin bridge

- Floyd sits in an admin channel on the public server and on cpbee and relays between them: one
  channel, admins only, authorised by services account. The link is outbound from cpbee.

## Phase 5 - tvchat web app

- First choice: tvchat itself in a browser terminal (ttyd / xterm.js) on the VPS.
- Later: a web front end on `tvchat-backend`'s line protocol.

## Open questions

- Which VPS provider and size (Phase 3).
- How Floyd-pro gets its knowledge (a FAQ file he is given, or the site's pages) and which model.
