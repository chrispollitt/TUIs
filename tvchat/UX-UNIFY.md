# tvchat: one set of commands for IRC, Delta Chat and talk (design, 2026-11-06; not built)

Chris (2026-11-06, with the Chat menu open): "This workflow is awkward. We need to simplify and unify. IRC and Delta Chat should
have shared commands and tvchat should do what I mean based on context. The chat menu is too crowded with similar things."
Trigger: adding Michelle to the Delta Chat group #tvchat took Invite (link) -> Join -> Add member, and the mail invitation
("Accept invite") cannot work for gateway accounts because the scan needs the mail login that only cpbee holds.

## Principle
One command per intent, the same for every protocol, acting on the current selection (account, conversation); what you type
is read to decide what you mean.

## The Chat menu (about 6 entries instead of 20)
```
Connect / Disconnect          the selected account, whichever applies
Chat with...                  one box: #channel, nick, email address, or an invite link
Invite...                     see below
This chat...                  members (add, remove), rename, leave - one dialog for the selected chat
Send selection to >           Mail / Calendar / To-do   (replaces Mail selection, Add selection to calendar, ... as to-do)
Pending invites...            only present when an invitation is waiting
-----
<the accounts, as now>
```
- **Chat with...** replaces Join: `#name` joins an IRC channel; an email address opens a Delta Chat chat (starting the key
  exchange itself when there is no key); a nick opens an IRC or talk query; a pasted `i.delta.chat` / `irc://` link joins.
- **Invite...** replaces Invite, Add member and Accept invite: in a channel or group it adds a person to that chat (handling
  the key step if needed); with no chat selected it gives your invite link.  An invitation that arrives is a prompt
  ("Chris invites you to #tvchat. Accept?"), not a menu item.
- **This chat...** replaces Add member, Remove member, Rename group and Leave.
- "Share from tvmail" becomes a toast with Yes / No.
- "Add second device" / "Use account from another device": hidden for gateway accounts (they do not apply); kept for an account
  with its own core.
- The typed commands (`/join`, `/invite`, `/leave`, `/rename`, ...) follow the same rules, so menu and keyboard agree.

## Order of work
1. **Gateway-side invitation scan** (dcgw): the gateway holds each person's mail login, so it scans the mailbox for
   invitations (the same format tvchat scans for today) and tells its clients; the client shows the Accept prompt.  Without
   this "invite someone with no key" cannot be smooth for gateway accounts.
2. The new menu and its dialogs (Chat with, Invite, This chat, Send selection to, Pending invites).
3. The typed commands, aligned across IRC, Delta Chat and talk.

## Open
- Keep "Accept invite" as an explicit entry as well as the prompt?  Keep the second-device items visible?
- tvchat's existing pieces to reuse: `joinDialog`, `inviteDialog`, `acceptInviteDialog`, `newGroupDialog`, `groupAdd/Remove/Rename`
  in `src/chatwin.cpp`; the backend's `invite`, `join`, `invites`, `accept-invite`, `new-group`, `group-*` verbs and
  `scan_invites` in `backend/tvchat-backend`.
