# tvchat primer: the chat protocols and how to get each one running

tvchat speaks three chat protocols. Each needs a **server** somewhere and a
**client** setup on the machine you run tvchat on. This file says what each one
is, and the few commands that bring it up. How tvchat is built and installed is
[INSTALL.md](INSTALL.md); what the settings mean is in `man tvchat-backend` and
[configure/tvchat.conf.example](configure/tvchat.conf.example).

| protocol | what it is | server | needs on the client |
|---|---|---|---|
| **IRC** | public or private channels, nicks; many people at once | an IRC server: a public one (Libera.Chat), or your own Ergo | nothing extra |
| **talk** | 1-to-1 text, the Unix `talk(1)` you know; LAN only, no login | one `talkd` on the LAN master | nothing extra |
| **Delta Chat** | private, end-to-end encrypted chat that travels as email | a mailbox per person, with IMAP and authenticated SMTP | the Delta Chat core, `deltachat-rpc-server` |

The set-up scripts live in the sibling repository
[chrispollitt/POSIX](https://github.com/chrispollitt/POSIX): `chat-setup/`
(IRC, talk, the client scripts) and `mail-setup/` (the mailboxes). Every script
is safe to run again, takes `-h`, and prints what it did. Nothing is installed
in `~/.local` unless you ask (`--prefix ~/.local`); the default is `/usr/local`.

---

## Part 1 - the server (once, on the LAN master)

Do this on the one box that stays on - cpbee in the author's LAN. Skip a
protocol you do not want. Public IRC needs no server of your own.

### IRC - your own Ergo

```sh
sudo ./chat-setup/scripts/configure-ergo.sh --open-firewall --register yournick
```

Installs [Ergo](https://ergo.chat), LAN-only, with TLS and NickServ accounts
(`--register` makes one for `yournick`; you are asked for its password). It
writes a CA certificate to `/opt/ergo/ca.pem` that clients fetch. Or skip all
of this and use a public network such as irc.libera.chat.

### talk - the LAN's talkd

```sh
sudo ./chat-setup/scripts/configure-talkd.sh --open-firewall
```

Installs `talkd` so that a plain `talk you@cpbee` from any shell on the LAN
rings you in tvchat, and tvchat can call out. LAN only; talk has no passwords.

### Delta Chat - mailboxes with a login

Delta Chat uses ordinary email, but its core logs in to IMAP to read **and to
SMTP, with a password, to send**. A mail server that only relays for the LAN on
port 25 has no such login, and the core also wants a mail domain with a dot.

```sh
sudo ./mail-setup/scripts/configure-delta.sh --domain cpbee.cmnet --users "delta delta2"
```

Creates a dedicated system user per name (a random password each, printed once
and saved in `/root/mail-setup-delta-passwords`), makes the domain local in
Postfix, and adds a Postfix **submission** service on port 587 (STARTTLS, login
required, relay only for logged-in users) with Dovecot checking the logins. It
logs in to both as each new user to prove it works. It is also a step of the
`mail-setup.sh` wizard. The server's certificate is self-signed unless you
install a real one.

Use a **dedicated** mailbox for each Delta Chat account. The core moves chat
mail into its own folder and may delete it from the server; it must never be
your everyday inbox. One mailbox is one identity: two machines that should talk
to each other need two mailboxes (`delta` and `delta2`), not one used twice.

---

## Part 2 - the client (on each machine that runs tvchat)

First build and install tvchat ([INSTALL.md](INSTALL.md): `./build.sh &&
./install.sh`). Each protocol is one account section in
`~/.config/tvchat/tvchat.conf`; passwords are never in that file but in
`$TVCHAT_PASS_<ACCOUNT>`, `~/.mu-tickets` or `~/.netrc` (mode 600). The scripts
below write the section for you and test the login.

### IRC

```sh
./chat-setup/scripts/configure-tvchat.sh --nick yournick --autoconnect
```

Fetches your server's CA, writes `[account:cpbee]`, asks for the NickServ
password once (into `~/.netrc`) and logs in to prove it. For a public network
write the section by hand: the example config has one for Libera.Chat.

### talk

Add a section naming the master (no password):

```ini
[account:talk]
protocol = talk
host     = cpbee
```

Chat > Join... a user (or `user@host`) calls them; Enter answers a call.

### Delta Chat

```sh
./chat-setup/scripts/configure-tvchat-delta.sh --addr delta@cpbee.cmnet --autoconnect
```

It installs the core into `/usr/local/bin` (the Windows build on Cygwin; on
Linux and macOS from PyPI), works out the mail server (the address's domain, or
its first label if that is not a host name) and whether its certificate is
self-signed, writes `[account:delta]`, finds the password in `~/.netrc` (or asks
for it) and connects once for real. `--host`, `--self-signed` and `--prefix`
override its guesses.

**Starting a conversation.** The core refuses to write to an address it has no
key for, so the two people swap keys once:

1. One person chooses **Chat > Join...** and types the other's address. tvchat
   mails them an invitation, and says so in the server messages.
2. The other person's tvchat notices it (within half a minute) and asks
   "delta@... invites you to chat - Accept?". **Yes** completes the key
   exchange; the chat opens on both sides, marked end-to-end encrypted.
3. From then on either side just types. **Chat > Accept invite...** answers an
   invitation you put off; **Chat > Invite...** shows a link to hand over by
   other means (any Delta Chat app can use it).

**With tvmail (optional).** If tvmail is installed too, an invitation can travel
through ordinary mail: in tvmail, **Message > Chat with sender** asks tvchat to
chat with whoever wrote the message you are reading (it opens the chat, or
mails them an invitation), and when a message holds a Delta Chat invite link,
**Message > Open chat invite** hands it to tvchat, which asks "Accept?". The two
programs only leave one-line files in `~/.local/state/tvchat/requests`; neither
needs the other. The other way round, **Chat > Invite...** has a field "Also mail it
to (through tvmail)": the invitation goes out from your ordinary mail account, to
the person's ordinary address, which is how to reach someone whose Delta Chat
address you do not know.

---

## When something does not work

| symptom | usual cause |
|---|---|
| Delta Chat account stays `connecting` or `error` | the account's state line in the Servers pane says why; `tvchat --debug` writes `debug.tvchat.backend.log` |
| "Exec format error" from the core | a pip-installed `deltachat-rpc-server` on Cygwin; remove it and rerun `configure-tvchat-delta.sh`, which installs the Windows build |
| "Could not find CMAKE_ROOT" while building (Cygwin) | a PATH with `/bin` but not `/usr/bin`; the scripts put `/usr/bin` first; `/etc/profile.d/path.sh` should too |
| IRC login refused | the password is not found for that login: `chat-setup/scripts/configure-tvchat.sh` says where it looked |
| sending refused, "exchange an invite first" | no key for that person yet: Join their address, or Invite |
| invitation never arrives | mail to the other mailbox is not delivered: check the master's Postfix log, and that the domain is local there |
