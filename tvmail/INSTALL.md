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
- **LAN clients:** "Accept mail from LAN clients?" Say **yes** if other
  machines will send through this one (tvmail clients, the sendmail shim).
  Postfix then listens on all interfaces and trusts your LAN (worked out
  for you, e.g. `192.168.1.0/24`) on port 25 with no password. `--yes`
  never turns this on by itself; for unattended runs pass `--lan auto` (or
  `--lan 192.168.1.0/24`). Re-running without the flag keeps whatever is
  set; `--lan off` goes back to loopback-only.
- **Dovecot:** IMAP on 993 using the distro's self-signed certificate.
  INBOX is `/var/mail/$USER`, other folders live in `~/mail`, and
  Drafts/Sent/Trash/Junk/Archive are created for you. Say **yes** to
  "password login over plain IMAP (143)" only if `mail(1)` on your clients
  will use `imap://…:143`.
- **Puller:** `pop-pull` (tiny, nothing to install) or `getmail` (POP3S or
  IMAPS). Either way you get a `mail-pull` command in `~/bin` or
  `~/.local/bin`. It then asks **how often to pull** (default every 5
  minutes, `0` = only when you run it) and schedules it: a systemd `--user`
  timer (with lingering, so it keeps running after you log out), or a
  crontab line where there's no systemd (WSL, containers). Unattended:
  `--pull-every N`.
- **GNU Mailutils:** `mail(1)`, plus `~/.mail` and `~/.mu-tickets` pointed
  at this box.
- **Test messages:** through `mail(1)` and through sendmail.

**2. `./build.sh`.** Clones tvision and mail-setup into `third_party/`,
then builds `build/tvmail`.

**3. `./test.sh`.** Runs the ctest layers; see `tests/README.md`.
`./test.sh -LE mail_setup` skips mail-setup's suites.

**4. `./setup.sh`.** Asks where to install: `~/.local` (just you) or
`/usr/local` (everyone). Then it writes `~/.config/tvmail/tvmail.conf`.
On a box with Dovecot it knows it's the master and offers **`master`** (the
default). That writes a config that reads through this box's own Dovecot and
sends through its own Postfix:

```ini
[service]
mode = remote

[imap]
host   = localhost      # or the host your ~/.mail names, so ~/.mu-tickets matches
port   = 993
ssl    = true
user   = you
verify = false          # Dovecot's self-signed cert

[smtp]
host     = localhost
port     = 25
starttls = false
auth     = false        # Postfix trusts loopback
```

It then offers to put your login password in `~/.netrc` (unless
`~/.mu-tickets` already has it). It also warns if nothing is pulling your
mail on a schedule, because in this mode F3 only files spam; the puller
timer from step 1 does the pulling.

Why not local mode on the master? Local mode makes tvmail edit
`/var/mail/$USER` directly while Dovecot is doing the same, and the two
don't lock the file the same way (`!WARNINGS.txt`, Warning 3). If you answer
`local` anyway, `setup.sh` warns and asks again.

**Changing your mind later** (no need to re-run the whole wizard):

```bash
M=third_party/POSIX/mail-setup/scripts
$M/configure-mail-pull.sh --timer 10         # pull every 10 min (pop-pull)
$M/configure-getmail.sh   --timer 10         # ...or switch to getmail
sudo $M/configure-sendmail-relay.sh --lan auto   # let LAN clients in
sudo $M/configure-sendmail-relay.sh --lan off    # ...or shut them out again
```

Only ever expose ports 25 and 993/143 to your LAN, never to the Internet.

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
  accept the LAN (the "Accept mail from LAN clients?" question, or
  `--lan auto`).
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
| F3 on the master only files spam | you're in remote mode (as recommended); pulling is the timer's job: `crontab -l` or `systemctl --user list-timers mail-pull.timer` |
| the timer never runs (WSL) | cron isn't started there: `sudo service cron start` |
| a client can't send | the master's Postfix is loopback-only: on the master, `sudo third_party/POSIX/mail-setup/scripts/configure-sendmail-relay.sh --lan auto` |
| mail reaches the master's Postfix but not `/var/mail` | `sudo postqueue -p`; "alias database unavailable" → re-run `configure-sendmail-relay.sh` (it pins `alias_maps`) |
| `build.sh`: couldn't fetch mail-setup | only a warning; the build continues. Check network/GitHub, or set `MAIL_SETUP_DIR` |
| check a master end to end | `sudo MAIL_SETUP_E2E_USER=$USER bash third_party/POSIX/mail-setup/tests/e2e/e2e_master.sh` |
| slow folder switching | tvmail is talking to a WAN IMAP host; see `!WARNINGS.txt`, Warning 4 (use a LAN master) |

More: `README.md` (usage), `man tvmail`, `man tvmail-backend`,
`!WARNINGS.txt`, `tests/README.md`.
