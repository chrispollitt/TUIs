// tvmail.h - what every part of tvmail shares: its command ids and the free
// functions and globals that cross a file boundary.  What tvmail shares with
// tvchat (the Turbo Vision classes, the colours, diagnostics, the shell, text
// by columns) is ../tvcommon/src/tvcommon.h; shared views are in views.h.
// Each window that only the app creates stays private to its own .cpp,
// reached through the factory functions declared here.
#pragma once
#include "tvcommon.h"

class CoProcess;
CoProcess &mailBackend(); // tvmail-backend serve (backend.cpp)

#ifndef TVMAIL_VERSION
#define TVMAIL_VERSION "1.3.0"
#endif

// ---------------------------------------------------------------- commands ---
// (2007 About, 2014 focus content, 2022 help, 2024 pane focused, 2034 Window
// list, 2043 wrap and 2001 reload are tvcommon.h's: the shared views use them.)
const ushort cmPull = 2000;
const ushort cmOpenMsg = 2002;
const ushort cmReplyMsg = 2003;
const ushort cmCompose = 2004;
const ushort cmDeleteMsg = 2005;
const ushort cmViewSrc = 2006;
const ushort cmSendMsg = 2011;      // send the focused compose window
const ushort cmFolderPicked = 2012; // broadcast: folder pane focus changed
const ushort cmMsgPicked = 2013;    // broadcast: message pane focus changed
const ushort cmEditDraft = 2015;    // open the focused Drafts message to edit
const ushort cmAddrBook = 2016;     // open the address-book window
const ushort cmEditSig = 2017;      // open ~/.signature in an editor window
const ushort cmSaveSig = 2018;      // (signature window) write the file
const ushort cmInsSig = 2019;       // (compose) insert the signature
const ushort cmInsDead = 2020;      // (compose) insert ~/dead.letter
const ushort cmResumeDead = 2021;   // open ~/dead.letter as a new compose
const ushort cmPullDone = 2023;     // background pop-pull finished (self-posted)
const ushort cmViewParts = 2025;    // open the MIME-parts window
const ushort cmForwardMsg = 2026;   // forward the focused message
const ushort cmReplyAllMsg = 2027;  // reply-all to the focused message
const ushort cmMarkUnread = 2028;   // mark the focused message unread
const ushort cmPurgeMsgs = 2029;    // open the purge dialog
const ushort cmMoveMsg = 2030;      // move the focused message to another folder
const ushort cmEmptyTrash = 2031;   // permanently delete everything in the trash folder
const ushort cmMarkRead = 2032;     // mark the focused message read
const ushort cmSaveMsg = 2033;      // save the focused message to a file (.eml)
const ushort cmSortDate = 2035;     // list order: mailbox order (oldest first)
const ushort cmSortFrom = 2036;     //             by sender
const ushort cmSortSubj = 2037;     //             by subject
const ushort cmSortUnread = 2038;   //             unread first
const ushort cmSortReverse = 2039;  // flip the current order
const ushort cmClearFilter = 2040;  // drop the type-to-filter text
const ushort cmFilterChanged = 2041; // broadcast: list view rebuilt (title refresh)
const ushort cmChatWith = 2050;     // Message > Chat with sender (asks tvchat, if installed)
const ushort cmChatInvite = 2051;   // Message > Open chat invite (hands a Delta Chat link to tvchat)
const ushort cmOutOfOffice = 2042;  // File > Out of office... (Sieve vacation reply)

// ------------------------------------------------------ tvmail-backend ---
// backend.cpp
std::string backendCommand(const std::string &args); // for shCapture / shInteractive
std::string backendRun(const std::string &args);     // warm co-process, else the shell
std::string readSigFile();
std::string readDeadLetter();

// ------------------------------------------------------------- dialogs ---
// dialogs.cpp
ushort askSaveDraft();
std::string askSavePath(const std::string &suggested,
                        const char *title = "Save attachment as");

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
void showMailWindow(); // bring the 3-pane mail window to the front
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
