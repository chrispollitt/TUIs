My handcrafted Text User Interface apps

| folder | what |
| --- | --- |
| [tvmail](tvmail/) | a Turbo Vision mail client: a local mbox on the mail host, IMAP / SMTP anywhere else |
| [tvchat](tvchat/) | a Turbo Vision chat client: IRC (with IRCv3), talk(1) and Delta Chat |
| [tvcal](tvcal/) | a Turbo Vision calendar and to-do manager: the calendar lives on the server (CalDAV, Radicale on the mail / chat host), and it works with tvmail, tvchat and Floyd |
| [tvcommon](tvcommon/) | what all three share: the C++ views and plumbing, `tvcommon.cmake`, the pty test harness, and `third_party.sh` (Turbo Vision, patched, and chrispollitt/POSIX's `mail-setup` + `chat-setup`, fetched once into `tvcommon/third_party/`) |

## Versions

tvmail and tvchat share one version, in [VERSION](VERSION) (tvcal, new, has its own: 0.1.0, in `tvcal/CMakeLists.txt`); `./version.sh` shows it and every place
that records it, `./version.sh X.Y.Z` sets them all. A release is a git tag, nothing built: `vX.Y.Z` for the
whole repo (plus `tvmail-vX.Y.Z` and `tvchat-vX.Y.Z`), fetched with
`git clone --branch vX.Y.Z --depth 1 <repo>`.

## Project files

Each app keeps the same set (tvcal has a part of it so far: README, INSTALL, HISTORY, BUGS, TO-DO); tvcommon has the ones that apply to shared code.

| file | what |
| --- | --- |
| `README.md` | what it is and how to use it |
| `INSTALL.md` | building and installing (tvmail, tvchat) |
| `HISTORY.md` | what changed, release by release |
| `WHY.md` | why tvmail exists (tvmail) |
| `WARNINGS.txt` | caveats about mail setups worth knowing before you build one (tvmail) |
| `BUGS.md` | defects: open ones, and squashed ones with the commit and the test that guards them |
| `TO-DO.txt` | features and wishes not done yet |
| `DESIGN.md` | how it is built (tvchat) |
| [`HANDOFF.md`](HANDOFF.md) | where the work stands, for the next session: what is next, what was decided, the live setup |
