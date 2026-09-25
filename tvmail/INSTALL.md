# Installing tvmail

tvmail is two things: the **app** (`tvmail` + `tvmail-backend`) and the
**mail system** underneath it (Postfix, Dovecot, a puller, GNU Mailutils).
The app is built here. The mail system is set up by
[mail-setup](https://github.com/chrispollitt/POSIX/tree/main/mail-setup), a
separate project that tvmail pulls into `third_party/`.

Contents:

1. Pick a role for each machine
2. Prerequisites
3. Master, step by step
4. Client, step by step
5. Cygwin / Windows notes
6. What the scripts do (reference)
7. Updating
8. Uninstalling and cleaning up
9. Troubleshooting

---

## 1. Pick a role for each machine

```
   ISP / web host  (POP3S in, SMTP submission out)
          ^  pull (pop-pull | getmail)        ^  relay (SASL + TLS)
          |                                   |
   +------+-----------------------------------+------+
   |  MASTER  - one Linux / Raspberry Pi box         |
   |  Postfix -> /var/mail/$USER <- Dovecot (IMAP)   |
   |  tvmail talks to Dovecot on localhost           |
   +-------------------------+-----------------------+
                             |  IMAP 993 (+143) / SMTP 25, 587
          +------------------+------------------+
          |  CLIENTS - laptop, WSL, Cygwin, VMs |
          |  tvmail in remote mode              |
          +-------------------------------------+
```

| role | how many | needs | OS |
|---|---|---|---|
| **master** | exactly one | Postfix, Dovecot, a puller, Mailutils, tvmail | Linux (Debian / Ubuntu / Raspberry Pi OS tested) |
| **client** | any number | tvmail; optionally Mailutils + a sendmail shim | Linux, macOS, BSD, Cygwin |

A single machine can be a master on its own; clients are optional.

## 2. Prerequisites

- `git`, `bash`, `python3` (3.6 or newer; 3.7 tested)
- To build: `cmake`, a C++17 compiler, `make`, ncurses(w) headers
- The master also needs:
  - `sudo`
  - your ISP's mail settings: the cPanel-style "Mail Client Configuration"
    text, with `Incoming Server:`, `Outgoing Server:`, `Username:`,
    `SMTP Port:` lines, saved to a file. Leave the password out; you'll be
    asked for it.

`./configure.sh` offers to install the build tools for you (apt, dnf, yum,
pacman, brew or Cygwin setup). By hand:

| system | command |
|---|---|
| Debian / Ubuntu / Pi | `sudo apt install git cmake g++ make python3 libncurses-dev` |
| Fedora | `sudo dnf install git cmake gcc-c++ make python3 ncurses-devel` |
| macOS | Xcode Command Line Tools, then `brew install cmake` |
| Cygwin | `setup-x86_64.exe -q -P git,cmake,gcc-g++,make,python3,libncursesw-devel` |

## 3. Master, step by step

```bash
git clone https://github.com/chrispollitt/TUIs
cd TUIs/tvmail

./configure.sh --role master   # 1. build tools + the mail system (mail-setup wizard)
./build.sh                     # 2. fetch third_party/, build
./test.sh                      # 3. optional: all test layers (about 1-2 minutes)
./setup.sh                     # 4. install tvmail, write tvmail.conf
```

**1. `./configure.sh --role master`.** Every step is a yes/no question, so
you can skip what you already have. It asks about:

- **Build tools:** installs any that are missing.
- **Postfix:** local delivery into `/var/mail`, plus relaying outbound mail
  through your ISP. Give it the relay-info file; it asks for the password
  once and keeps it in `/etc/postfix/sasl_passwd` (mode 600), the only
  place it's ever stored.
- **Dovecot:** IMAP on 993 using the distro's self-signed certificate.
  INBOX is `/var/mail/$USER`, other folders live in `~/mail`, and
  Drafts/Sent/Trash/Junk/Archive are created for you. Say **yes** to
  "password login over plain IMAP (143)" only if `mail(1)` on your clients
  will use `imap://…:143`.
- **Puller:** `pop-pull` (tiny, nothing to install) or `getmail` (POP3S or
  IMAPS). Either way you get a `mail-pull` command in `~/bin` or
  `~/.local/bin`.
- **GNU Mailutils:** `mail(1)`, plus `~/.mail` and `~/.mu-tickets` pointed
  at this box.
- **Test messages:** through `mail(1)` and through sendmail.

**2. `./build.sh`.** Clones tvision and mail-setup into `third_party/`,
then builds `build/tvmail`.

**3. `./test.sh`.** Runs the ctest layers; see `tests/README.md`.
`./test.sh -LE mail_setup` skips mail-setup's suites.

**4. `./setup.sh`.** Asks where to install: `~/.local` (just you) or
`/usr/local` (everyone). Then it writes `~/.config/tvmail/tvmail.conf`.
**On the master, answer `remote` and point it at `localhost`:**

```ini
[service]
mode = remote

[imap]
host   = localhost
port   = 993
ssl    = true
verify = false      # Dovecot's self-signed cert

[smtp]
host = localhost
port = 25
starttls = false
auth = false
```

Why not local mode on the master? Local mode makes tvmail edit
`/var/mail/$USER` directly while Dovecot is doing the same, and the two
don't lock the file the same way. See `!WARNINGS.txt`, Warning 3.

**5. Pull on a timer.** In remote mode, F3 only files spam; it doesn't pull.
So give the puller a timer:

```bash
third_party/POSIX/mail-setup/scripts/configure-mail-pull.sh --timer 5   # pop-pull
# or
third_party/POSIX/mail-setup/scripts/configure-getmail.sh --timer 5     # getmail
sudo loginctl enable-linger "$USER"     # keep the timer running after you log out
```

No systemd (e.g. WSL)? Use cron instead: `*/5 * * * * $HOME/bin/mail-pull`.

**6. Let the LAN clients in (only if you have clients).** The Postfix
setup listens on loopback only. To accept mail from clients:

```bash
sudo postconf -e 'inet_interfaces = all' \
                 'mynetworks = 127.0.0.0/8 [::1]/128 192.168.1.0/24'   # your LAN
sudo systemctl restart postfix        # or: sudo service postfix restart
```

Dovecot already listens on all interfaces. Only open ports 993/143 and
25/587 to the LAN, never to the Internet.

## 4. Client, step by step

```bash
git clone https://github.com/chrispollitt/TUIs
cd TUIs/tvmail

./configure.sh --role client   # build tools; optional sendmail shim + Mailutils
./build.sh
./test.sh                      # optional
./setup.sh                     # install; answer "remote" and give the master's name
```

- **The sendmail shim** (offered by `configure.sh`) replaces
  `/usr/sbin/sendmail` with a small forwarder to the master's port 25. That
  lets anything on this box that calls `sendmail(8)` (cron, scripts) send
  mail. tvmail itself doesn't need it in remote mode. The master must
  accept the LAN; see step 6 above.
- **Mailutils** (`~/.mail` + `~/.mu-tickets`): if you set it up, tvmail
  reads host, port and user from those files, so `tvmail.conf` only needs
  `mode = remote`.
- **Passwords never go in `tvmail.conf`.** Use `~/.mu-tickets`
  (`configure.sh` writes it), `~/.netrc` (`setup.sh` offers to write it),
  or `$TVMAIL_IMAP_PASS` / `$TVMAIL_SMTP_PASS`.
- **F3 on a client** files `***SPAM***`-tagged mail; pulling happens on the
  master.

Every setting, with comments: `configure/tvmail.conf.example`.

## 5. Cygwin / Windows notes

- Cygwin is **client only**: there's no Postfix or Dovecot package.
- `build.sh` patches tvision for Cygwin automatically
  (`patches/cygwin_patch.py`). `./clean.sh --all` removes the patched copy;
  the next build fetches and patches it again.
- GNU Mailutils has no Cygwin package. `configure.sh` offers a source build
  into `/usr/local` (`MAILUTILS_VERSION=` picks the release).
- `./build.sh --mingw` cross-compiles a static native `tvmail.exe` for a
  real Windows console (needs `mingw64-x86_64-gcc-g++`). A normal Cygwin
  build is for mintty and other Unix terminals.
- Run the scripts from a Cygwin shell (mintty or `bash -l`). Git Bash/MSYS
  hasn't been tested and lacks pieces like `python3`.

## 6. What the scripts do (reference)

| script | when | what |
|---|---|---|
| `configure.sh` | once per machine, before building | build tools, then mail-setup's wizard (`--role`, `--puller`, `-y`, `--assume-no`) |
| `third_party.sh` | run for you by build/configure | clones `third_party/tvision` and `third_party/POSIX` (mail-setup only); `--update` pulls both |
| `build.sh` | after every change / update | third_party, Cygwin patch, cmake build (`--mingw` on Cygwin) |
| `test.sh` | optional | build + ctest: backend_unit, backend_pipe, e2e_tui, install_roundtrip, mail_setup |
| `setup.sh` | once, then after rebuilds | install (asks the prefix, or `--prefix`), write tvmail.conf, smoke tests |
| `install.sh` | quick reinstall | `cmake --install` into `/usr/local` (sudo if needed), no questions |
| `uninstall.sh` | to remove tvmail | see section 8 |
| `clean.sh` | to start a build over | see section 8 |
| `all.sh` | development | build, test, install, run with `--trace`, report |

mail-setup's own scripts live in `third_party/POSIX/mail-setup/scripts/`.
Each one runs on its own and takes `--help`; see that project's README.

Environment overrides:

| variable | effect |
|---|---|
| `MAIL_SETUP_DIR` | use your own mail-setup checkout instead of `third_party/POSIX` |
| `MAIL_SETUP_REPO`, `TVISION_REPO` | clone from somewhere else |
| `TVMAIL_CONF` | another tvmail.conf |
| `TVMAIL_MODE` | `local` / `remote` for one run |
| `TVMAIL_PULL` | what F3 runs on a local-mode master (default `mail-pull`) |

## 7. Updating

```bash
git pull
./third_party.sh --update      # newer tvision + mail-setup
./build.sh
./setup.sh --prefix ~/.local   # or ./install.sh for /usr/local
```

Re-running `configure.sh` is safe: every step asks first. Files it rewrites
wholesale (`~/.mail`, `~/.mu-tickets`, `mailpull.conf`, `getmailrc`, the
Dovecot drop-in, `/usr/sbin/sendmail`) are backed up as `*.bak.<date>` or
`*.orig.<date>`. Postfix settings are changed in place with `postconf -e`.

## 8. Uninstalling and cleaning up

```bash
./uninstall.sh -n          # show what's installed in ~/.local and /usr/local
./uninstall.sh             # remove it (asks first; sudo if needed)
./uninstall.sh --purge     # ...and offer to remove ~/.config/tvmail/
./clean.sh                 # remove build/, dist/, logs, __pycache__/
./clean.sh --all           # ...and third_party/ (fetched again on the next build)
```

`uninstall.sh` removes only tvmail. The mail system (Postfix, Dovecot,
pullers, `~/.mail`, `~/.mu-tickets`, `~/.netrc`) stays, because `mail(1)`
uses it too. The one exception is a `pop-pull` that older tvmail versions
installed next to `tvmail`: it offers to remove that, unless a `mail-pull`
still runs it. To remove the mail system itself, use your package manager
(`apt remove postfix dovecot-imapd getmail6 mailutils`) and delete
`/etc/dovecot/conf.d/99-mail-setup.conf`.

## 9. Troubleshooting

| symptom | fix |
|---|---|
| `CERTIFICATE_VERIFY_FAILED` | self-signed LAN cert: `verify = false` under `[imap]` (and `[smtp]`), or `cafile = …` |
| F3: "no mail puller found" | on the master: run `configure.sh` (or a mail-setup puller script) so `mail-pull` exists and is on `$PATH` |
| F3 on the master only files spam | you're in remote mode (as recommended); pulling is the timer's job (step 5) |
| a client can't send | the master's Postfix is loopback-only; see step 6 |
| mail reaches the master's Postfix but not `/var/mail` | `sudo postqueue -p`; "alias database unavailable" → re-run `configure-sendmail-relay.sh` (it pins `alias_maps`) |
| `build.sh`: couldn't fetch mail-setup | only a warning; the build continues. Check network/GitHub, or set `MAIL_SETUP_DIR` |
| check a master end to end | `sudo MAIL_SETUP_E2E_USER=$USER bash third_party/POSIX/mail-setup/tests/e2e/e2e_master.sh` |
| slow folder switching | tvmail is talking to a WAN IMAP host; see `!WARNINGS.txt`, Warning 4 (use a LAN master) |

More: `README.md` (usage), `man tvmail`, `man tvmail-backend`,
`!WARNINGS.txt`, `tests/README.md`.
