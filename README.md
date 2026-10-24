My handcrafted Text User Interface apps

| folder | what |
| --- | --- |
| [tvmail](tvmail/) | a Turbo Vision mail client: a local mbox on the mail host, IMAP / SMTP anywhere else |
| [tvchat](tvchat/) | a Turbo Vision chat client: IRC (with IRCv3) and talk(1) |
| [tvcommon](tvcommon/) | what both share: the C++ views and plumbing, `tvcommon.cmake`, the pty test harness, and `third_party.sh` (Turbo Vision, patched, and chrispollitt/POSIX's `mail-setup` + `chat-setup`, fetched once into `tvcommon/third_party/`) |

## Project files

Each app keeps the same set; tvcommon has the ones that apply to shared code.

| file | what |
| --- | --- |
| `README.md` | what it is and how to use it |
| `INSTALL.md` | building and installing (tvmail, tvchat) |
| `HISTORY.md` | what changed, release by release (tvchat); why tvmail exists (tvmail) |
| `BUGS.md` | defects: open ones, and squashed ones with the commit and the test that guards them |
| `TO-DO.txt` | features and wishes not done yet |
| `DESIGN.md` | how it is built (tvchat) |
| [`HANDOFF.md`](HANDOFF.md) | where the work stands, for the next session: what is next, what was decided, the live setup |
