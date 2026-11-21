// tvmail.h - what every part of tvmail shares: its command ids and the free
// functions and globals that cross a file boundary.  What tvmail shares with
// tvchat (the Turbo Vision classes, the colours, diagnostics, the shell, text
// by columns) is ../tvcommon/src/tvcommon.h; shared views are in views.h.
// Each window that only the app creates stays private to its own .cpp,
// reached through the factory functions declared here.
#pragma once
#include "tvcommon.h"
#include <set>

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
const ushort cmPullAuto = 2052;     // the periodic tick: pull quietly, a toast for new mail (self-posted)
const ushort cmViewParts = 2025;    // open the MIME-parts window
const ushort cmForwardMsg = 2026;   // forward the focused message
const ushort cmReplyAllMsg = 2027;  // reply-all to the focused message
const ushort cmMarkUnread = 2028;   // mark the focused message unread
const ushort cmPurgeMsgs = 2029;    // open the purge dialog
const ushort cmMoveMsg = 2030;      // move the focused message to another folder
const ushort cmEmptyTrash = 2031;   // permanently delete everything in the trash folder
const ushort cmMarkToggle = 2057;   // Message > Mark read/unread: whichever applies to the focused message
const ushort cmSaveSender = 2058;   // Message > Send to > Contacts: add the sender to the address book on the server
const ushort cmMarkRead = 2032;     // mark the focused message read
const ushort cmSaveMsg = 2033;      // save the focused message to a file (.eml)
const ushort cmSortDate = 2035;     // list order: mailbox order (oldest first)
const ushort cmSortFrom = 2036;     //             by sender
const ushort cmSortSubj = 2037;     //             by subject
const ushort cmSortUnread = 2038;   //             unread first
const ushort cmSortReverse = 2039;  // flip the current order
const ushort cmShowChat = 2090;     // View > Delta Chat mail (shown in / hidden from the inbox)
const ushort cmClearFilter = 2040;  // drop the type-to-filter text
const ushort cmFilterChanged = 2041; // broadcast: list view rebuilt (title refresh)
const ushort cmChatWith = 2050;     // Message > Chat with sender (asks tvchat, if installed)
const ushort cmChatInvite = 2051;   // Message > Open chat invite (hands a Delta Chat link to tvchat)
const ushort cmToCal = 2055;       // Message > Add to calendar (asks tvcal; its dialog is filled in from the invitation or the subject)
const ushort cmToTodo = 2056;      // Message > Add as to-do
const ushort cmShareChat = 2054;    // Message > Share in chat (asks tvchat to say sender and subject in its channel, on your yes there)
const ushort cmOutOfOffice = 2042;  // File > Out of office... (Sieve vacation reply)
const ushort cmFloyd = 2053;        // File > Floyd... (the assistant)
const ushort cmServicePicked = 2059;   // broadcast: the Services pane's highlight moved
const ushort cmToggleServices = 2060;  // View > Services pane: show or hide it
const ushort cmNewsSubscribe = 2061;   // Message > Usenet > Subscribe to group...
const ushort cmNewsUnsub = 2062;       //                    Unsubscribe from this group
const ushort cmNewsCatchUp = 2063;     //                    Catch up (mark the group read)
const ushort cmNewsGroups = 2064;      //                    All groups...
const ushort cmNewsOlder = 2065;       //                    Load older articles
const ushort cmToggleThreads = 2066;   // View > Threads
const ushort cmListsSubscribe = 2068;  // Message > Lists > Subscribe to a list...
const ushort cmListsSignoff = 2069;    //                    Sign off from this list
const ushort cmListsDigest = 2070;     //                    Digest or every message...
const ushort cmListsAvailable = 2071;  //                    Available lists...
const ushort cmSearchMsgs = 2067;      // Edit > Search messages... (Ctrl-F): the folder's From / To / Subject / text, on the server

// ------------------------------------------------------ tvmail-backend ---
// backend.cpp
std::string backendCommand(const std::string &args); // for shCapture / shInteractive
std::string backendRun(const std::string &args);     // warm co-process, else the shell
void reportListError(const std::string &what, const std::string &raw); // the non-row lines of a listing, as an error toast
std::string readSigFile();
std::string readDeadLetter();

// ------------------------------------------------------------- dialogs ---
// dialogs.cpp
ushort askSaveDraft();
// One line of text in a small dialog; "" when cancelled.
std::string askLine(const char *title, const char *label, const std::string &suggested = std::string());
// A list to choose from (the newsgroups on the server); the row picked, -1 when cancelled.
int pickFromList(const char *title, const std::vector<std::string> &rows, int start = 0);
std::string askSavePath(const std::string &suggested,
                        const char *title = "Save attachment as");

// ------------------------------------------------------------ mail data ---
// mailwin.cpp: the folder list and the current folder's messages
struct MsgRow
{
    int idx = 0;
    char flag = '.';
    std::string date, from, subj;
    std::string id, refs; // Usenet: Message-ID and References, for the threaded view
    int depth = 0;        // Usenet threads: how far down a thread the row is (set by applyView)
};
enum SortKey
{
    sortDate, // mailbox order, oldest first
    sortFrom,
    sortSubj,
    sortUnread // unread before read
};
// A folder of the selected service, as the backend's `folders` verb lists it: the name shown, the MBOX
// shorthand every other verb takes, and the counts (-1 = not known).
struct Folder
{
    std::string name;
    std::string mbox;
    int total = -1;
    int unread = -1;
};
// A service (SERVICES-DESIGN.md): a kind of place to read from, with its own folders.  Only "mail" so far.
struct Service
{
    std::string name; // what --service takes
    std::string kind; // mail (later: listserv, usenet)
    std::string label;
    int unread = -1;  // the badge
};
extern std::vector<MsgRow> gAll;  // everything the backend listed
extern std::vector<MsgRow> gRows; // what the pane shows: gAll, filtered and sorted
extern SortKey gSortKey;
extern bool gSortRev;
extern std::string gFilter; // type-to-filter text (From / Subject, any case)
extern std::string gSearch;        // "Search messages" words; empty = no search is showing
extern std::set<int> gSearchHits;  // the MsgRow::idx of the messages the search found (the pane shows only those)
extern std::vector<Service> gServices; // from the backend's `services` verb; never empty once the window is made
extern int gServiceIdx;                // the selected service
extern std::vector<Folder> gFolders;   // the selected service's folders
extern int gFolderIdx;      // current folder (drives gMbox)
extern std::string gMbox;
extern bool gRemote;        // backend "mode" == remote (IMAP)
extern std::string gHost;   // backend "host": the box we're talking to
extern bool gWrap;          // the body pane's wrap setting
std::string serviceFlag();  // "--service NAME " for a service other than mail, else ""; backendRun/backendCommand add it
                            // to the verbs that are about a service (list, show, mark, ...), not to the account's (send, pull, ...)
std::string serviceKind();  // the selected service's kind: "mail", "lists" or "usenet"
bool notForUsenet(const char *what); // in the Usenet service: says "<what> is for mail" in a toast and returns true
void serviceCommandsChanged();       // enable the Usenet commands in the Message menu only while Usenet is selected (app.cpp)
extern bool gThreads;       // View > Threads: the Usenet list in thread order
extern int gNewsLimit;      // how many articles the Usenet list asks for (0 = the backend's window)
std::string mboxArg();
std::string mboxOpt();

// The one 3-pane mail window.  The app drives it through these rather than
// reaching into its panes.
TWindow *newMailWindow(const TRect &bounds);
void showMailWindow(); // bring the 3-pane mail window to the front
// Ctrl-D / Ctrl-R / Ctrl-N are status-line and menu keys (Delete, Reply, New) but also the editor's own
// (cursor right, page up, ...), and the menu bar and status line see a key before the focused view.  While
// an editor window (compose, signature) is the active one, the menu bar and status line let the three keys
// through to the editor (BUGS.md #6).  Called from the editor windows' setState().
void editorOwnsCtrlKeys(bool owns);                         // app.cpp
int mailFocusedRow();             // the highlighted row of gRows, or -1
void mailMarkFocused(bool read);
void mailReload();                // F5: counts + the folder, same message
void mailToggleServices();        // View > Services pane: show or hide it
void mailNews(ushort command);    // the Usenet commands (cmNews*, cmToggleThreads)
void mailLists(ushort command);   // the Lists commands (cmLists*)
int mailInboxUnread();            // unread in the inbox as of the last counts, -1 if not known
void mailViewChanged(bool resetSel);
void mailSearch();                // Edit > Search messages...: ask for words, ask the backend, show only the hits
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
TWindow *newFloydWindow(const TRect &bounds);               // floydwin.cpp
void floydTips();                                           // floydwin.cpp: toasts, clippy level only
