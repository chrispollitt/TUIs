// tvmail.h - what every part of tvmail shares: the Turbo Vision classes it
// uses, the command ids, the colours, and the free functions and globals that
// cross a file boundary.  Classes shared between files live in views.h; each
// window that only the app creates stays private to its own .cpp, reached
// through the factory functions declared here.
#pragma once

#define Uses_TApplication
#define Uses_TKeys
#define Uses_TRect
#define Uses_TBackground
#define Uses_TDeskTop
#define Uses_TMenuBar
#define Uses_TMenuItem
#define Uses_TSubMenu
#define Uses_TStatusLine
#define Uses_TStatusItem
#define Uses_TStatusDef
#define Uses_TScreen
#define Uses_TFileDialog
#define Uses_TDeskTop
#define Uses_TWindow
#define Uses_TFrame
#define Uses_TScrollBar
#define Uses_TScroller
#define Uses_TListViewer
#define Uses_TDrawBuffer
#define Uses_TEvent
#define Uses_MsgBox
#define Uses_TInputLine
#define Uses_TLabel
#define Uses_TEditor
#define Uses_TIndicator
#define Uses_TDialog
#define Uses_TButton
#define Uses_TCheckBoxes
#define Uses_TRadioButtons
#define Uses_TSItem
#define Uses_THistory
#define Uses_TCommandSet
#define Uses_TStaticText
#define Uses_TMemo
#define Uses_TText
#define Uses_TClipboard
#define Uses_TFindDialogRec
#include <tvision/tv.h>

#include <string>
#include <vector>
#include <sstream>
#include <algorithm>
#include <cctype>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <chrono>

#ifdef _WIN32
#include <windows.h>
#define popen _popen
#define pclose _pclose
#else
#include <unistd.h>
#include <fcntl.h>
#include <poll.h>
#include <csignal>
#include <cerrno>
#include <sys/wait.h>
#endif

#ifndef TVMAIL_SH
#define TVMAIL_SH "/bin/sh"
#endif
#ifndef TVMAIL_VERSION
#define TVMAIL_VERSION "1.2.0"
#endif

// ---------------------------------------------------------------- commands ---
const ushort cmPull = 2000;
const ushort cmReload = 2001;
const ushort cmOpenMsg = 2002;
const ushort cmReplyMsg = 2003;
const ushort cmCompose = 2004;
const ushort cmDeleteMsg = 2005;
const ushort cmViewSrc = 2006;
const ushort cmAboutBox = 2007;
const ushort cmSendMsg = 2011;      // send the focused compose window
const ushort cmFolderPicked = 2012; // broadcast: folder pane focus changed
const ushort cmMsgPicked = 2013;    // broadcast: message pane focus changed
const ushort cmFocusContent = 2014; // Enter in message pane -> jump to body
const ushort cmEditDraft = 2015;    // open the focused Drafts message to edit
const ushort cmAddrBook = 2016;     // open the address-book window
const ushort cmEditSig = 2017;      // open ~/.signature in an editor window
const ushort cmSaveSig = 2018;      // (signature window) write the file
const ushort cmInsSig = 2019;       // (compose) insert the signature
const ushort cmInsDead = 2020;      // (compose) insert ~/dead.letter
const ushort cmResumeDead = 2021;   // open ~/dead.letter as a new compose
const ushort cmShowHelp = 2022;     // in-app help window
const ushort cmPullDone = 2023;     // background pop-pull finished (self-posted)
const ushort cmPaneFocused = 2024;  // broadcast: a 3-pane pane took focus
const ushort cmViewParts = 2025;    // open the MIME-parts window
const ushort cmForwardMsg = 2026;   // forward the focused message
const ushort cmReplyAllMsg = 2027;  // reply-all to the focused message
const ushort cmMarkUnread = 2028;   // mark the focused message unread
const ushort cmPurgeMsgs = 2029;    // open the purge dialog
const ushort cmMoveMsg = 2030;      // move the focused message to another folder
const ushort cmEmptyTrash = 2031;   // permanently delete everything in the trash folder
const ushort cmMarkRead = 2032;     // mark the focused message read
const ushort cmSaveMsg = 2033;      // save the focused message to a file (.eml)
const ushort cmWindowList = 2034;   // Window > List... (Alt-0)
const ushort cmSortDate = 2035;     // list order: mailbox order (oldest first)
const ushort cmSortFrom = 2036;     //             by sender
const ushort cmSortSubj = 2037;     //             by subject
const ushort cmSortUnread = 2038;   //             unread first
const ushort cmSortReverse = 2039;  // flip the current order
const ushort cmClearFilter = 2040;  // drop the type-to-filter text
const ushort cmFilterChanged = 2041; // broadcast: list view rebuilt (title refresh)
const ushort cmOutOfOffice = 2042;  // File > Out of office... (Sieve vacation reply)
const ushort cmToggleWrap = 2043;   // View > Wrap long lines (body pane / focused text window)

// -------------------------------------------------------- classic colors ----
// The Turbo Vision DOS look, in exact RGB.  BIOS colour numbers are not
// enough: the terminal's theme decides what "grey" or "green" look like, and
// under e.g. Windows Terminal's Dark+ (white == bright white == #E5E5E5) the
// classic white-on-grey dialog titles and light-cyan-on-green buttons
// vanish.  RGB goes out as 24-bit colour, or as xterm's fixed 6x6x6 cube on
// a 256-colour terminal - neither is touched by the theme.  (A 16-colour
// terminal gets the nearest theme colour, as before.)
inline TColorAttr vga(uchar bios, ushort style = 0)
{
    static const uint32_t rgb[16] = {
        0x000000, 0x0000AA, 0x00AA00, 0x00AAAA, 0xAA0000, 0xAA00AA, 0xAA5500, 0xAAAAAA,
        0x555555, 0x5555FF, 0x55FF55, 0x55FFFF, 0xFF5555, 0xFF55FF, 0xFFFF55, 0xFFFFFF};
    return TColorAttr(TColorRGB(rgb[bios & 15]), TColorRGB(rgb[(bios >> 4) & 15]), style);
}
// The "focus" green: darker than VGA green so black, white, yellow and light
// cyan text all read on it (contrast >= 4); an exact xterm-cube colour, so
// 256-colour terminals show the same.
constexpr uint32_t kFocusGreen = 0x008700;
inline TColorAttr onGreen(uint32_t fg, ushort style = 0)
{
    return TColorAttr(TColorRGB(fg), TColorRGB(kFocusGreen), style);
}

inline TColorAttr cNorm() { return vga(0x30); }          // black on cyan: text
inline TColorAttr cHi() { return vga(0x3F); }            // white on cyan: scroll arrows
inline TColorAttr cFocus() { return onGreen(0xFFFFFF); } // white on green: focused row, selection
inline TColorAttr cCur() { return vga(0xB0); }           // black on light cyan: current row, unfocused
inline TColorAttr cDiv() { return vga(0x33); }           // cyan on cyan
inline TColorAttr cFrame() { return vga(0x30); }         // black on cyan: frames + titles
                                                                // (white on cyan is < 3:1 anywhere)
inline TColorAttr cDim() { return vga(0x38); }           // grey on cyan: pane rules

// ---------------------------------------------------------- command line ---
struct CliOptions
{
    bool selftest = false;
    bool parseError = false;
    std::string debugPath;
    std::string tracePath;
};

// --------------------------------------------------- shell + diagnostics ---
// shell.cpp: the POSIX-sh plumbing every backend call goes through, and the
// --debug / --trace logs.
extern const char *const kPreamble; // PATH preamble for every helper script
extern FILE *gDebugLog;
extern FILE *gTraceLog;
void debugLog(const char *fmt, ...);
long long traceMicros();
void traceSpan(const char *name, long long start);
void startDiagnostics(const CliOptions &options);
void stopDiagnostics();
std::string backendFlags();
std::string backendCommand(const std::string &args);
std::string tempDir();
long procId();
std::string writeTemp(const std::string &content, const char *suffix);
std::string shCapture(const std::string &body);
int shInteractive(const std::string &body);
std::string backendRun(const std::string &args); // warm co-process, else the shell

// ------------------------------------------------------------- dialogs ---
// dialogs.cpp
ushort tvmailEditDialog(int dialog, ...);
ushort askSaveDraft();
std::string askSavePath(const std::string &suggested,
                        const char *title = "Save attachment as");

// ---------------------------------------------------- text + system util ---
// util.cpp
std::string shq(const std::string &s);
std::string slurp(const std::string &path);
std::string homePath(const char *rel);
std::string readSigFile();
std::string readDeadLetter();
std::vector<std::string> splitLines(const std::string &s);
std::string lowered(std::string s);
std::string fitCols(const std::string &s, int w);
void putText(char *dest, const std::string &s, short maxLen);
std::string expandTabs(const std::string &s);
extern TTimerId gFlashTimer;
void flashStatus(const std::string &msg);
void systemClipboard(const std::string &text);

// ------------------------------------------------------------ mail data ---
// mailwin.cpp: the folder list and the current folder's messages
struct MsgRow
{
    int idx = 0;
    char flag = '.';
    std::string date, from, subj;
};
enum SortKey
{
    sortDate, // mailbox order, oldest first
    sortFrom,
    sortSubj,
    sortUnread // unread before read
};
struct Folder
{
    const char *name;
    const char *mbox;
};
extern std::vector<MsgRow> gAll;  // everything the backend listed
extern std::vector<MsgRow> gRows; // what the pane shows: gAll, filtered and sorted
extern SortKey gSortKey;
extern bool gSortRev;
extern std::string gFilter; // type-to-filter text (From / Subject, any case)
extern const Folder gFolders[];
extern const int gFolderCount;
extern int gFolderIdx;      // current folder (drives gMbox)
extern std::string gMbox;
extern bool gRemote;        // backend "mode" == remote (IMAP)
extern std::string gHost;   // backend "host": the box we're talking to
extern bool gWrap;          // the body pane's wrap setting
std::string mboxArg();
std::string mboxOpt();

// The one 3-pane mail window.  The app drives it through these rather than
// reaching into its panes.
TWindow *newMailWindow(const TRect &bounds);
int mailFocusedRow();             // the highlighted row of gRows, or -1
void mailMarkFocused(bool read);
void mailReload();                // F5: counts + the folder, same message
void mailViewChanged(bool resetSel);
void mailSetWrap(bool wrap);

// -------------------------------------------------------------- windows ---
void openCompose(const std::string &raw, const std::string &linkMbox = "",
                 int linkIdx = -1, bool addSig = false);     // compose.cpp
bool composeAddRecipient(const std::string &addr);          // compose.cpp
TWindow *newAddrBookWindow(const TRect &bounds);            // addrbook.cpp
TWindow *newPartsWindow(const TRect &bounds, int idx,       // parts.cpp
                        const std::string &mbox, const char *title);
TWindow *newSigWindow(const TRect &bounds);                 // signature.cpp
TWindow *newHelpWindow(const TRect &bounds);                // help.cpp
