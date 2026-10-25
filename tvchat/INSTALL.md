# Installing tvchat

tvchat runs on Linux (including the Raspberry Pi and WSL), macOS, the BSDs and
Cygwin. It has no native Windows build: `tvchat-backend` is a co-process, which
needs `fork()`. Use Cygwin or WSL there.

## What it needs

- **To build:** `cmake` 3.13 or later, a C++17 compiler, the ncurses(w)
  headers, `git` (to fetch Turbo Vision), and `python3` 3.6 or later (on
  Cygwin, to patch Turbo Vision).
- **To run:** `python3` 3.6 or later, standard library only. `openssl` is
  needed only for the TLS tests.

## Build and install

```sh
./build.sh          # fetches magiblot/tvision into third_party/, patches it, builds
./test.sh           # every test layer (no network, no real account)
./install.sh        # /usr/local: tvchat, tvchat-backend, man pages, docs
```

`./build.sh` needs `../tvcommon` beside it (a source tarball from `./dist.sh`
carries it as `./tvcommon`). For another prefix,
`cmake --install build --prefix DIR` (`~/.local` if you want it: it installs
there only when you say so). `./uninstall.sh` removes what was
installed; `--purge` also offers to remove `~/.config/tvchat/`.

## Configure

`~/.config/tvchat/tvchat.conf` (`$TVCHAT_CONF` names another file for one
run). With no file at all, tvchat starts with no accounts and says why. A file
from when chat was part of tvmail, `~/.config/tvmail/tvchat.conf`, is still
read if the new one is missing.

Start from `configure/tvchat.conf.example`. Write comments on lines of their
own: a `# ...` after a value becomes part of the value.

```ini
[chat]
autoconnect = libera

[account:libera]
protocol = irc
display  = Libera
host     = irc.libera.chat
port     = 6697
tls      = true
nick     = yournick
sasl     = yournick
channels = #yourchannel
```

The SASL password, looked up in this order; in each place only an entry for
the SASL login counts:

1. `$TVCHAT_PASS_<ACCOUNT>`, e.g. `$TVCHAT_PASS_LIBERA`;
2. `~/.mu-tickets`: `irc://yournick:PASSWORD@irc.libera.chat`;
3. `~/.netrc` (mode 600): `machine irc.libera.chat login yournick password PASSWORD`.

## Delta Chat

A Delta Chat account needs the official core, `deltachat-rpc-server`, a
separate program (tvchat does not reimplement Delta Chat). Without it only the
Delta Chat account reports an error; IRC and talk work as before. Either:

- let `configure-tvchat-delta.sh` do it (below): it takes the program out of the PyPI
  wheel (Linux, macOS, and the Windows build on Cygwin) into `/usr/local/bin`, using
  sudo if that needs it; `--prefix DIR` picks another place (`--prefix ~/.local` if
  you want it there), or
- download a release binary from <https://github.com/chatmail/core/releases>
  and put it on your `PATH`, or name it with `rpc_server =` in the account.

Tested with 2.65.0. On Cygwin the Windows build works too (tvchat hands it a
Windows path for its data directory).

**The short way:** on the mail master, `mail-setup`'s `configure-delta.sh` makes
the mailbox users and the authenticated submission port Delta Chat needs (it is
a step of the `mail-setup.sh` wizard); then on each client, once tvchat is
installed, `chrispollitt/POSIX`'s `chat-setup/scripts/configure-tvchat-delta.sh
--addr delta@your.domain --autoconnect` installs
the core, works out the mail server (the address's domain, or its first label
if that is not a host name) and whether its certificate is self-signed, writes
the account into `tvchat.conf`, finds the password in `~/.netrc` (or asks for it)
and connects once to prove it. `--host` and `--self-signed` override its guesses. What follows is what those do.

Use a **dedicated** mailbox: the core moves chat mail into its own folder and
may delete it from the server. The first connect sets the account up (the
address and its password, from the same places as IRC's: `$TVCHAT_PASS_<ACCOUNT>`,
`~/.mu-tickets`, or `~/.netrc` with `machine <mail host> login <address>`); after
that the core remembers it in `~/.local/share/tvchat/deltachat/<ACCOUNT>/`
(`$XDG_DATA_HOME` is respected), which belongs to the core: do not edit it.
The ~/.netrc entry may name the mail host or the address's domain, and the bare
login or the full address.

Two people (or two accounts) must exchange keys before the core will send:
one runs `tvchat-backend invite ACCOUNT` and gives the link to the other, who
joins it (`join`). Against a plain mail server (Dovecot + Postfix) the account
usually needs `imap_user` / `smtp_user` (the login name, often not the full
address) and `smtp_port = 587`; the server needs an authenticated submission
port.

```ini
[account:delta]
protocol = deltachat
display  = Delta Chat
addr     = chat@example.org
# only when autoconfiguration cannot work it out:
#imap_server = mail.example.org
#smtp_server = mail.example.org
# a server with a self-signed certificate:  verify = false
```

## Your own IRC server

`chrispollitt/POSIX`'s `chat-setup` sets up an Ergo server on a LAN box and
points tvchat at it:

```sh
# on the server (root):
sudo chat-setup/scripts/configure-ergo.sh --open-firewall --register yournick
# on the machine where tvchat runs (no root):
chat-setup/scripts/configure-tvchat.sh --host SERVER --nick yournick
```

The second script copies the server's CA, checks that TLS verifies, writes the
account into `tvchat.conf`, stores the password in `~/.netrc`, and logs in once
to prove it.

## Over ssh from Windows Terminal

If keys go missing or stray text such as `[68;32;100;1;0;1_` appears when you
run tvchat over ssh from Windows Terminal, see
[`../tvcommon/README.md`](../tvcommon/README.md#over-ssh-from-windows-terminal):
the apps already allow for it (`ESCDELAY`), and there are workarounds
(`wincygterm`, mintty, WSL).
