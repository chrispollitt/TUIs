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
`cmake --install build --prefix ~/.local`. `./uninstall.sh` removes what was
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
