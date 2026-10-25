My handcrafted Text User Interface apps

| folder | what |
| --- | --- |
| [tvmail](tvmail/) | a Turbo Vision mail client: a local mbox on the mail host, IMAP / SMTP anywhere else |
| [tvchat](tvchat/) | a Turbo Vision chat client: IRC (with IRCv3), talk(1) and Delta Chat |
| [tvcommon](tvcommon/) | what both share: the C++ views and plumbing, `tvcommon.cmake`, the pty test harness, and `third_party.sh` (Turbo Vision, patched, and chrispollitt/POSIX's `mail-setup` + `chat-setup`, fetched once into `tvcommon/third_party/`) |

## Versions

tvmail and tvchat share one version, in [VERSION](VERSION); `./version.sh` shows it and every place
that records it, `./version.sh X.Y.Z` sets them all. A release is a git tag, nothing built: `vX.Y.Z` for the
whole repo (plus `tvmail-vX.Y.Z` and `tvchat-vX.Y.Z`), fetched with
`git clone --branch vX.Y.Z --depth 1 <repo>`.

## Project files

Each app keeps the same set; tvcommon has the ones that apply to shared code.

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
