# Installing tvcal

tvcal needs a POSIX system (Linux, macOS, the BSDs, Cygwin, WSL): its backend is a co-process.

## The program

```sh
cd tvcal
./build.sh        # fetches and patches Turbo Vision into ../tvcommon/third_party/ (shared with tvmail
                  # and tvchat), then builds; needs cmake, a C++17 compiler, ncurses headers, git, python3
./test.sh         # unit tests (backend, CalDAV against a fake server) and the --selftest
./install.sh      # /usr/local (sudo if needed): tvcal, tvcal-backend, Floyd's words, docs
```

`./uninstall.sh` reverses it; `--purge` also offers `~/.config/tvcal/` (never the calendar itself).

## The calendar server (once, on the mail / chat host)

```sh
sudo tvcal/configure/configure-radicale.sh server --user chris
```

Installs Radicale and `apache2-utils` (apt), serves TLS on port 5232 (the host's certificate with
`--cert/--key`, else a self-signed one made for 10 years), stores `chris`'s password with bcrypt, makes the
collection `/chris/calendar/`, enables the service and opens the port with `lan-port` if it is there (on
cpbee: add the port to `/etc/router-setup/lan0-tcp-ports` and run `setup-router.sh fw`). The calendars are
plain files in `/var/lib/radicale/collections`: back them up with the mail.

Not run on cpbee yet: the first run there is the test of the script.

## Each machine

```sh
tvcal/configure/configure-radicale.sh client --user chris --host cpbee
```

Writes `[server]` into `~/.config/tvcal/tvcal.conf`, fetches the server's certificate (scp), puts the
password in `~/.netrc` (mode 600) and checks that `tvcal-backend` can read the calendar. Or write the file by
hand (File > Edit configuration... in tvcal; F9 verifies).

## Floyd

tvmail's Floyd (F7) and tvchat's use the calendar for their owner's `!todo`, `!cal` and `!event` when
`tvcal-backend` is installed on the same machine. The central Floyd on cpbee needs the client half run as his
service user, with `--host localhost` (start the server half with `--bind 127.0.0.1` if nothing else on the
LAN should see the calendar).
