// tvmail - a Turbo Vision front-end for a local mbox + the tvmail-backend helper.
//
// Portable: builds natively on Linux (incl. Raspberry Pi / WSL), macOS and the
// BSDs.  On Cygwin, build.sh first patches tvision for the missing FIONREAD &c;
// `build.sh --mingw` makes a static native .exe for a real Windows console.
//
// Every mail operation is emitted as a tiny POSIX-sh script and run via the
// shell in TVMAIL_SH (default /bin/sh; the mingw build points it at a Cygwin
// shell).  The C++ side just orchestrates windows.
//
// Build: ../build.sh

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
#define Uses_TSItem
#define Uses_THistory
#define Uses_TCommandSet
#define Uses_TStaticText
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

#ifdef _WIN32
#  include <windows.h>
#  define popen  _popen
#  define pclose _pclose
#else
#  include <unistd.h>
#  include <fcntl.h>
#  include <poll.h>
#  include <csignal>
#  include <cerrno>
#  include <sys/wait.h>
#endif

#ifndef TVMAIL_SH
#  define TVMAIL_SH "/bin/sh"
#endif

// ---------------------------------------------------------------- commands ---
const ushort cmPull         = 2000;
const ushort cmReload       = 2001;
const ushort cmOpenMsg      = 2002;
const ushort cmReplyMsg     = 2003;
const ushort cmCompose      = 2004;
const ushort cmDeleteMsg    = 2005;
const ushort cmViewSrc      = 2006;
const ushort cmAboutBox     = 2007;
const ushort cmSendMsg      = 2011;   // send the focused compose window
const ushort cmFolderPicked = 2012;   // broadcast: folder pane focus changed
const ushort cmMsgPicked    = 2013;   // broadcast: message pane focus changed
const ushort cmFocusContent = 2014;   // Enter in message pane -> jump to body
const ushort cmEditDraft    = 2015;   // open the focused Drafts message to edit
const ushort cmAddrBook     = 2016;   // open the address-book window
const ushort cmEditSig      = 2017;   // open ~/.signature in an editor window
const ushort cmSaveSig      = 2018;   // (signature window) write the file
const ushort cmInsSig       = 2019;   // (compose) insert the signature
const ushort cmInsDead      = 2020;   // (compose) insert ~/dead.letter
const ushort cmResumeDead   = 2021;   // open ~/dead.letter as a new compose
const ushort cmShowHelp   = 2022;   // in-app help window
const ushort cmPullDone   = 2023;   // background pop-pull finished (self-posted)
const ushort cmPaneFocused = 2024;  // broadcast: a 3-pane pane took focus

// ------------------------------------------------------ editor dialogs -----
// Wire up the standard Find / Replace / "search failed" dialogs the TEditor
// needs (tvision ships only a cmCancel stub).  Lifted from tvision's tvedit
// example (tvedit2/3.cpp), trimmed to what a composer uses.
static ushort execDialog(TDialog *d, void *data)
{
    TView *p = TProgram::application->validView(d);
    if (!p) return cmCancel;
    if (data) p->setData(data);
    ushort result = TProgram::deskTop->execView(p);
    if (result != cmCancel && data) p->getData(data);
    TObject::destroy(p);
    return result;
}

static TDialog *createFindDialog()
{
    TDialog *d = new TDialog(TRect(0, 0, 38, 12), "Find");
    d->options |= ofCentered;
    TInputLine *c = new TInputLine(TRect(3, 3, 32, 4), 80);
    d->insert(c);
    d->insert(new TLabel(TRect(2, 2, 15, 3), "~T~ext to find", c));
    d->insert(new THistory(TRect(32, 3, 35, 4), c, 10));
    d->insert(new TCheckBoxes(TRect(3, 5, 35, 7),
        new TSItem("~C~ase sensitive",
        new TSItem("~W~hole words only", 0))));
    d->insert(new TButton(TRect(14, 9, 24, 11), "O~K~", cmOK, bfDefault));
    d->insert(new TButton(TRect(26, 9, 36, 11), "Cancel", cmCancel, bfNormal));
    d->selectNext(False);
    return d;
}

static TDialog *createReplaceDialog()
{
    TDialog *d = new TDialog(TRect(0, 0, 40, 16), "Replace");
    d->options |= ofCentered;
    TInputLine *c = new TInputLine(TRect(3, 3, 34, 4), 80);
    d->insert(c);
    d->insert(new TLabel(TRect(2, 2, 15, 3), "~T~ext to find", c));
    d->insert(new THistory(TRect(34, 3, 37, 4), c, 10));
    c = new TInputLine(TRect(3, 6, 34, 7), 80);
    d->insert(c);
    d->insert(new TLabel(TRect(2, 5, 12, 6), "~N~ew text", c));
    d->insert(new THistory(TRect(34, 6, 37, 7), c, 11));
    d->insert(new TCheckBoxes(TRect(3, 8, 37, 12),
        new TSItem("~C~ase sensitive",
        new TSItem("~W~hole words only",
        new TSItem("~P~rompt on replace",
        new TSItem("~R~eplace all", 0))))));
    d->insert(new TButton(TRect(17, 13, 27, 15), "O~K~", cmOK, bfDefault));
    d->insert(new TButton(TRect(28, 13, 38, 15), "Cancel", cmCancel, bfNormal));
    d->selectNext(False);
    return d;
}

static ushort tvmailEditDialog(int dialog, ...)
{
    va_list arg;
    switch (dialog) {
        case edOutOfMemory:
            return messageBox("Not enough memory for this operation.",
                              mfError | mfOKButton);
        case edFind: {
            va_start(arg, dialog);
            void *p = va_arg(arg, void *);
            va_end(arg);
            return execDialog(createFindDialog(), p);
        }
        case edSearchFailed:
            return messageBox("Search string not found.", mfError | mfOKButton);
        case edReplace: {
            va_start(arg, dialog);
            void *p = va_arg(arg, void *);
            va_end(arg);
            return execDialog(createReplaceDialog(), p);
        }
        case edReplacePrompt:
            return messageBox("Replace this occurrence?",
                              mfYesNoCancel | mfInformation);
    }
    return cmCancel;
}

// -------------------------------------------------------- shell plumbing ----
// Everything the backend needs runs inside one small POSIX-sh script (one
// script per call keeps us clear of cmd.exe quoting on the mingw build).  The
// preamble widens PATH so tvmail-backend is found wherever it was installed.
static const char *kPreamble =
    "export PATH=\"$HOME/bin:$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin"
    ":/opt/local/bin:/usr/pkg/bin:/usr/bin:/bin:$PATH\"\n";

static std::string tempDir()
{
#ifdef _WIN32
    char b[MAX_PATH];
    DWORD n = GetTempPathA(MAX_PATH, b);
    return std::string(b, n);
#else
    const char *t = getenv("TMPDIR");
    return (t && *t) ? std::string(t) + "/" : std::string("/tmp/");
#endif
}

static long procId()
{
#ifdef _WIN32
    return (long)GetCurrentProcessId();
#else
    return (long)getpid();
#endif
}

static std::string writeScript(const std::string &body)
{
    static int seq = 0;
    std::string path = tempDir() + "tvmail_" + std::to_string(procId())
                     + "_" + std::to_string(++seq) + ".sh";
    if (FILE *f = fopen(path.c_str(), "wb")) {
        fputs(kPreamble, f);
        fputs(body.c_str(), f);
        fputc('\n', f);
        fclose(f);
    }
    return path;
}

// a plain temp file (e.g. an RFC822 draft); the path is handed to the shell
static std::string writeTemp(const std::string &content, const char *suffix)
{
    static int seq = 0;
    std::string path = tempDir() + "tvmail_msg_" + std::to_string(procId())
                     + "_" + std::to_string(++seq) + suffix;
    if (FILE *f = fopen(path.c_str(), "wb")) {
        fwrite(content.data(), 1, content.size(), f);
        fclose(f);
    }
    return path;
}

static std::string q(const std::string &s) { return "\"" + s + "\""; }
static std::string shInvoke(const std::string &script)
{
    return q(TVMAIL_SH) + " " + q(script);
}

static std::string shCapture(const std::string &body)
{
    std::string sp = writeScript(body), out;
    if (FILE *p = popen(shInvoke(sp).c_str(), "r")) {
        char buf[8192]; size_t n;
        while ((n = fread(buf, 1, sizeof buf, p)) > 0) out.append(buf, n);
        pclose(p);
    }
    remove(sp.c_str());
    return out;
}

static int shInteractive(const std::string &body)
{
    std::string sp = writeScript(body);
    TProgram::application->suspend();
    std::fputs("\n", stdout);
    int rc = std::system(shInvoke(sp).c_str());
    std::fputs("\n[tvmail] done - press Enter to return ", stdout);
    std::fflush(stdout);
    for (int c; (c = std::getchar()) != '\n' && c != EOF; ) {}
    TProgram::application->resume();
    TProgram::application->redraw();
    remove(sp.c_str());
    return rc;
}

// ------------------------------------------------ persistent backend -------
// Keep one tvmail-backend alive in "serve" mode and talk to it over a pipe.
// Before this, every folder switch spawned three cold Python interpreters
// through the shell (~800 ms on Cygwin, which has no real fork()).  Now the
// mbox/MIME machinery is parsed once and each request is a pipe round-trip.
//
// Wire protocol - a request line, then a framed reply:
//     ->  show 3 spool\n
//     <-  <status> <nbytes>\n   followed by exactly <nbytes> bytes
// The read verbs (list/show/raw/mark/aliases/compose-template) go through
// here; the stdin-consuming / interactive ones (send/save-draft/pull) keep
// the one-shot shell path.  The --mingw Windows build has no fork() and uses
// the shell path for everything.

#ifndef _WIN32
class Backend {
public:
    static Backend &instance() { static Backend b; return b; }

    // One request.  Returns false if the co-process is unavailable - the
    // caller then falls back to a one-shot `tvmail-backend ...` via the shell.
    bool call(const std::string &req, std::string &body, int &status)
    {
        if (permFail) return false;
        if (!up && !start()) return false;
        if (!writeLine(req) || !readFrame(body, status)) {
            if (!restart() || !writeLine(req) || !readFrame(body, status))
                return false;
        }
        return true;
    }

    void stop()
    {
        if (wr) { std::fclose(wr); wr = nullptr; }
        if (rd) { std::fclose(rd); rd = nullptr; }
        if (pid > 0) { int s; while (::waitpid(pid, &s, 0) < 0 && errno == EINTR) {} }
        pid = -1;
        up  = false;
    }

    // Fire-and-forget: run `tvmail-backend <args>` detached, stdin from
    // /dev/null and stdout+stderr to logpath.  Returns the pid (the caller
    // reaps it) or -1.  Used for the background mail pull, so F3 no longer
    // suspends the whole UI.
    pid_t spawnLogged(const std::string &args, const std::string &logpath)
    {
        pid_t p = ::fork();
        if (p < 0) return -1;
        if (p == 0) {
            int n = ::open("/dev/null", O_RDONLY);
            if (n >= 0) { ::dup2(n, 0); if (n > 2) ::close(n); }
            int f = ::open(logpath.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0600);
            if (f >= 0) { ::dup2(f, 1); ::dup2(f, 2); if (f > 2) ::close(f); }
            std::string sc = std::string(kPreamble) + "exec tvmail-backend " + args + "\n";
            ::execl(TVMAIL_SH, TVMAIL_SH, "-c", sc.c_str(), (char *)nullptr);
            ::_exit(127);
        }
        return p;
    }

    ~Backend() { stop(); }

private:
    Backend() { std::signal(SIGPIPE, SIG_IGN); }
    Backend(const Backend &) = delete;
    Backend &operator=(const Backend &) = delete;

    FILE *wr = nullptr, *rd = nullptr;
    pid_t pid = -1;
    bool  up = false;
    bool  permFail = false;
    int   restarts = 0;

    bool giveUp() { permFail = true; return false; }

    bool start()
    {
        int a[2], b[2];               // a: parent->child stdin; b: child stdout->parent
        if (::pipe(a) != 0) return giveUp();
        if (::pipe(b) != 0) { ::close(a[0]); ::close(a[1]); return giveUp(); }

        pid = ::fork();
        if (pid < 0) {
            ::close(a[0]); ::close(a[1]); ::close(b[0]); ::close(b[1]);
            return giveUp();
        }
        if (pid == 0) {                                  // ---- child ----
            ::dup2(a[0], 0);
            ::dup2(b[1], 1);
            const char *log = ::getenv("TVMAIL_BACKEND_LOG");
            int e = ::open((log && *log) ? log : "/dev/null",
                           O_WRONLY | O_CREAT | O_APPEND, 0600);
            if (e >= 0) { ::dup2(e, 2); if (e > 2) ::close(e); }
            if (a[0] > 2) ::close(a[0]);
            if (a[1] > 2) ::close(a[1]);
            if (b[0] > 2) ::close(b[0]);
            if (b[1] > 2) ::close(b[1]);
            std::string sc = std::string(kPreamble) + "exec tvmail-backend serve\n";
            ::execl(TVMAIL_SH, TVMAIL_SH, "-c", sc.c_str(), (char *)nullptr);
            ::_exit(127);
        }
        ::close(a[0]); ::close(b[1]);                    // ---- parent ----
        wr = ::fdopen(a[1], "w");
        rd = ::fdopen(b[0], "r");
        if (!wr || !rd) { stop(); return giveUp(); }
        // keep the pipe out of every later fork/exec (shell calls, the
        // background pull) so closing wr on shutdown really reaches the child
        ::fcntl(::fileno(wr), F_SETFD, FD_CLOEXEC);
        ::fcntl(::fileno(rd), F_SETFD, FD_CLOEXEC);

        std::string banner; int st = -1;                // expect "0 5\nready"
        if (!readFrame(banner, st) || st != 0 || banner != "ready") {
            stop();
            return giveUp();
        }
        up = true;
        return true;
    }

    bool restart()
    {
        stop();
        if (permFail || ++restarts > 3) return giveUp();
        return start();
    }

    bool writeLine(const std::string &s)
    {
        if (!wr) return false;
        if (std::fwrite(s.data(), 1, s.size(), wr) != s.size()) return false;
        if (std::fputc('\n', wr) == EOF)                        return false;
        return std::fflush(wr) == 0;
    }

    // "<status> <nbytes>\n" then exactly <nbytes> bytes.  poll() before the
    // first byte bounds the wait so a wedged backend can't freeze the UI;
    // once bytes are flowing the reply is one write on the far side, so
    // blocking reads for the remainder can't deadlock.
    bool readFrame(std::string &body, int &status)
    {
        body.clear();
        struct pollfd pfd;
        pfd.fd = ::fileno(rd); pfd.events = POLLIN; pfd.revents = 0;
        if (::poll(&pfd, 1, 8000) <= 0) return false;

        std::string hdr; int c;
        while ((c = std::fgetc(rd)) != EOF && c != '\n') {
            hdr += char(c);
            if (hdr.size() > 64) return false;
        }
        if (c == EOF) return false;

        long n = -1; int st = 0;
        if (std::sscanf(hdr.c_str(), "%d %ld", &st, &n) != 2 || n < 0) return false;
        status = st;
        body.resize((size_t)n);
        size_t got = 0;
        while (got < (size_t)n) {
            size_t r = std::fread(&body[got], 1, (size_t)n - got, rd);
            if (r == 0) return false;
            got += r;
        }
        return true;
    }
};
#else   // _WIN32: no fork(); every call uses the one-shot shell path.
class Backend {
public:
    static Backend &instance() { static Backend b; return b; }
    bool call(const std::string &, std::string &, int &) { return false; }
    void stop() {}
};
#endif

// A read-only sub-command: try the warm co-process, else a one-shot shell call.
static std::string backendRun(const std::string &args)
{
    std::string body; int st = 0;
    if (Backend::instance().call(args, body, st)) return body;
    return shCapture("tvmail-backend " + args + " 2>&1");
}

// -------------------------------------------------------- classic colors ----
// Turbo Vision DOS palette — hard-coded so we never depend on terminal quirks.
static inline TColorAttr cNorm()  { return TColorAttr(TColorBIOS(0x00), TColorBIOS(0x03)); } // black on cyan
static inline TColorAttr cHi()    { return TColorAttr(TColorBIOS(0x0F), TColorBIOS(0x03)); } // white on cyan
static inline TColorAttr cSel()   { return TColorAttr(TColorBIOS(0x0F), TColorBIOS(0x0B)); } // white on light-cyan
static inline TColorAttr cDiv()   { return TColorAttr(TColorBIOS(0x03), TColorBIOS(0x03)); } // cyan on cyan
static inline TColorAttr cFrame() { return TColorAttr(TColorBIOS(0x0F), TColorBIOS(0x03)); } // white on cyan
static inline TColorAttr cDim()   { return TColorAttr(TColorBIOS(0x08), TColorBIOS(0x03)); } // grey on cyan

// ---------------------------------------------------------------- data -------
struct MsgRow { int idx = 0; char flag = '.'; std::string date, from, subj; };
static std::vector<MsgRow> gRows;

struct Folder { const char *name; const char *mbox; };
static const Folder gFolders[] = {
    { "inbox  (/var/mail)", "spool"  },
    { "drafts",             "drafts" },
    { "saved  (~/mbox)",    "mbox"   },
    { "trash",              "trash"  },
    { "dead.letter",        "dead"   },
};
static const int gFolderCount = int(sizeof gFolders / sizeof gFolders[0]);
static int gFolderIdx = 0;                       // current folder (drives gMbox)
static std::string gMbox = gFolders[0].mbox;

static std::string mboxArg() { return gMbox.empty() ? std::string() : " '" + gMbox + "'"; }
static std::string mboxOpt() { return gMbox.empty() ? std::string() : " --mbox '" + gMbox + "'"; }

static std::string slurp(const std::string &path)
{
    std::string s;
    if (FILE *f = fopen(path.c_str(), "rb")) {
        char buf[8192]; size_t n;
        while ((n = fread(buf, 1, sizeof buf, f)) > 0) s.append(buf, n);
        fclose(f);
    }
    return s;
}
static std::string homePath(const char *rel)
{
    const char *h = getenv("HOME");
    return std::string(h ? h : ".") + "/" + rel;
}
static std::string readSigFile()
{
    std::string s = slurp(homePath(".signature"));
    while (!s.empty() && (s.back() == '\n' || s.back() == '\r')) s.pop_back();
    return s;
}
static std::string readDeadLetter()
{
    const char *d = getenv("DEAD");
    return slurp(d && *d ? std::string(d) : homePath("dead.letter"));
}

static std::vector<std::string> splitLines(const std::string &s)
{
    std::vector<std::string> v;
    std::istringstream is(s);
    std::string ln;
    while (std::getline(is, ln)) {
        if (!ln.empty() && ln.back() == '\r') ln.pop_back();
        v.push_back(ln);
    }
    if (v.empty()) v.push_back(std::string());
    return v;
}

static void loadList()
{
    gRows.clear();
    std::string raw = backendRun("list " + gMbox);
    std::istringstream is(raw);
    std::string line;
    while (std::getline(is, line)) {
        if (line.empty() || line.find('\t') == std::string::npos) continue;
        std::istringstream ls(line);
        std::string idx, flag, date, from, subj;
        std::getline(ls, idx,  '\t');
        std::getline(ls, flag, '\t');
        std::getline(ls, date, '\t');
        std::getline(ls, from, '\t');
        std::getline(ls, subj);
        MsgRow r;
        r.idx  = std::atoi(idx.c_str());
        r.flag = flag.empty() ? '.' : flag[0];
        r.date = date; r.from = from; r.subj = subj;
        gRows.push_back(r);
    }
}

// ------------------------------------------------------- blue desktop ------
// Some terminals map Turbo Vision's default desktop palette to a jarring
// colour (seen: solid red). Paint it the classic blue explicitly.
class TBlueBg : public TBackground {
public:
    TBlueBg(const TRect &r) : TBackground(r, char(0xB0)) {}   // 0xB0 = light shade
    void draw() override
    {
        TColorAttr c(TColorBIOS(0x07), TColorBIOS(0x01));     // grey on blue
        TDrawBuffer b;
        b.moveChar(0, pattern, c, size.x);
        for (short y = 0; y < size.y; ++y)
            writeLine(0, y, size.x, 1, b);
    }
};

class TBlueDeskTop : public TDeskTop {
public:
    TBlueDeskTop(const TRect &r)
        : TDeskInit(&TBlueDeskTop::initBackground), TDeskTop(r) {}
    static TBackground *initBackground(TRect r) { return new TBlueBg(r); }
};

// ================================================= 3-pane mail window =======
// One full-screen window: folders (left) | message list (top-right) /
// message content (bottom-right).  Panes talk via evBroadcast: folder focus
// reloads the list, message focus loads the content.

class TDivider : public TView {
    bool vert;
public:
    TDivider(const TRect &b, bool v) : TView(b), vert(v) {}
    void draw() override
    {
        TColorAttr c = cFrame();
        TDrawBuffer b;
        if (vert) {
            b.moveChar(0, char(0xB3), c, 1);           // vertical bar
            for (short y = 0; y < size.y; ++y) writeLine(0, y, 1, 1, b);
        } else {
            b.moveChar(0, char(0xC4), c, size.x);       // horizontal bar
            writeLine(0, 0, size.x, 1, b);
        }
    }
};

class TFolderPane : public TListViewer {
public:
    TFolderPane(const TRect &b) : TListViewer(b, 1, nullptr, nullptr)
    {
        setRange(gFolderCount);
        TListViewer::focusItem(gFolderIdx);
    }
    void getText(char *dest, short item, short maxLen) override
    {
        const char *s = (item >= 0 && item < gFolderCount) ? gFolders[item].name : "";
        std::strncpy(dest, s, maxLen); dest[maxLen] = 0;
    }
    void focusItem(short item) override
    {
        TListViewer::focusItem(item);
        gFolderIdx = item;
        if (owner) message(owner, evBroadcast, cmFolderPicked, this);
    }
    void selectItem(short item) override { focusItem(item); }
    void setState(ushort aState, Boolean enable) override
    {
        TListViewer::setState(aState, enable);
        if (enable && (aState & sfFocused) && owner)
            message(owner, evBroadcast, cmPaneFocused, this);
    }
    TColorAttr mapColor(uchar i) override
    {
        switch (i) {
            case 1: case 2: return cNorm();
            case 3:         return cHi();
            case 4:         return cSel();
            case 5:         return cDiv();
        }
        return TView::mapColor(i);
    }
};

class TMsgPane : public TListViewer {
public:
    TMsgPane(const TRect &b, TScrollBar *vsb) : TListViewer(b, 1, nullptr, vsb)
    {
        setRange((short)gRows.size());
    }
    void getText(char *dest, short item, short maxLen) override
    {
        if (item < 0 || item >= (short)gRows.size()) { dest[0] = 0; return; }
        const MsgRow &r = gRows[item];
        char line[600];
        std::snprintf(line, sizeof line, "%c %-16.16s %-20.20s %s",
                      r.flag, r.date.c_str(), r.from.c_str(), r.subj.c_str());
        std::strncpy(dest, line, maxLen); dest[maxLen] = 0;
    }
    void focusItem(short item) override
    {
        TListViewer::focusItem(item);
        if (owner) message(owner, evBroadcast, cmMsgPicked, this);
    }
    void selectItem(short) override
    {
        if (!owner) return;
        // Enter on a draft opens it for editing; otherwise jump to the body
        message(owner, evBroadcast,
                gMbox == "drafts" ? cmEditDraft : cmFocusContent, this);
    }
    void handleEvent(TEvent &e) override
    {
        if (e.what == evKeyDown && e.keyDown.keyCode == kbEnter) {
            selectItem(focused); clearEvent(e); return;
        }
        TListViewer::handleEvent(e);
    }
    void setState(ushort aState, Boolean enable) override
    {
        TListViewer::setState(aState, enable);
        if (enable && (aState & sfFocused) && owner)
            message(owner, evBroadcast, cmPaneFocused, this);
    }
    TColorAttr mapColor(uchar i) override
    {
        switch (i) {
            case 1: case 2: return cNorm();
            case 3:         return cHi();
            case 4:         return cSel();
            case 5:         return cDiv();
        }
        return TView::mapColor(i);
    }
    void reload()
    {
        setRange((short)gRows.size());
        if (focused >= (short)gRows.size())
            focused = gRows.empty() ? 0 : (short)gRows.size() - 1;
        drawView();
    }
};

class TContentPane : public TScroller {
    std::vector<std::string> lines;
public:
    TContentPane(const TRect &b, TScrollBar *h, TScrollBar *v) : TScroller(b, h, v)
    {
        lines.push_back(std::string());
        setLimit(1, 1);
    }
    void setLines(std::vector<std::string> ls)
    {
        lines = std::move(ls);
        if (lines.empty()) lines.push_back(std::string());
        size_t w = 1;
        for (auto &l : lines) w = std::max(w, l.size());
        setLimit((int)w + 1, (int)lines.size());
        scrollTo(0, 0);
        drawView();
    }
    void draw() override
    {
        TColorAttr c = cNorm();
        for (short y = 0; y < size.y; ++y) {
            TDrawBuffer b;
            b.moveChar(0, ' ', c, size.x);
            int li = delta.y + y;
            if (li >= 0 && li < (int)lines.size()) {
                const std::string &s = lines[li];
                if (delta.x < (int)s.size())
                    b.moveStr(0, s.c_str() + delta.x, c);
            }
            writeLine(0, y, size.x, 1, b);
        }
    }
    // A plain TScroller ignores the keyboard (scrolling normally comes from a
    // focused scrollbar, which we don't have).  Drive it here so arrows /
    // PgUp / PgDn / Home / End / Space / Enter scroll the body when this pane
    // has focus.
    void handleEvent(TEvent &e) override
    {
        if (e.what == evKeyDown && (state & sfFocused)) {
            int page = size.y > 1 ? size.y - 1 : 1;
            int nx = delta.x, ny = delta.y;
            bool mine = true;
            switch (e.keyDown.keyCode) {
                case kbUp:       ny -= 1;      break;
                case kbDown:     ny += 1;      break;
                case kbLeft:     nx -= 1;      break;
                case kbRight:    nx += 1;      break;
                case kbPgUp:     ny -= page;   break;
                case kbPgDn:     ny += page;   break;
                case kbHome:     nx  = 0;      break;
                case kbEnd:      ny  = limit.y; break;   // scrollTo() clamps
                case kbCtrlPgUp: ny  = 0;      break;
                case kbCtrlPgDn: ny  = limit.y; break;
                case kbEnter:    ny += 1;      break;    // like a pager
                default:
                    if (e.keyDown.charScan.charCode == ' ') ny += page;
                    else mine = false;
            }
            if (mine) { scrollTo(nx, ny); clearEvent(e); return; }
        }
        TScroller::handleEvent(e);
    }
    void setState(ushort aState, Boolean enable) override
    {
        TScroller::setState(aState, enable);
        if (enable && (aState & sfFocused) && owner)
            message(owner, evBroadcast, cmPaneFocused, this);
    }
    TColorAttr mapColor(uchar i) override
    { return i == 1 ? cNorm() : (i == 2 ? cHi() : TView::mapColor(i)); }
};

// A one-row heading above a pane.  Reverse-video when its pane holds focus,
// dim otherwise - the "which pane am I in?" cue.  The one over the body also
// serves as the rule between the message list and the message text.
class TPaneTitle : public TView {
    std::string label;
    Boolean active = False;
public:
    TPaneTitle(const TRect &b, const char *l) : TView(b), label(l) {}
    void setActive(Boolean a) { if (a != active) { active = a; drawView(); } }
    void draw() override
    {
        TColorAttr rule = cDim();
        TColorAttr lab  = active ? cSel() : cDim();
        TDrawBuffer b;
        b.moveChar(0, char(0xC4), rule, size.x);          // horizontal rule
        std::string s = active ? "[ " + label + " ]" : "  " + label + "  ";
        b.moveStr(2, s.c_str(), lab);
        writeLine(0, 0, size.x, 1, b);
    }
    TColorAttr mapColor(uchar) override { return active ? cSel() : cDim(); }
};

class TMailWindow : public TWindow {
    Boolean ready = False;
    TScrollBar *msgVsb = nullptr, *contentVsb = nullptr;
    TDivider *vdiv = nullptr;
    TPaneTitle *folderTitle = nullptr, *msgTitle = nullptr, *contentTitle = nullptr;
    int activePane = 0;

    struct Rects {
        TRect folderTitle, folder, vbar, msgTitle, msg, msgVsb,
              contentTitle, content, contentVsb;
    };
    Rects paneRects() const
    {
        int W = size.x, H = size.y;
        const int FW = 22;
        int sy = 1 + (H - 2) * 2 / 5;
        if (sy < 6)      sy = 6;                 // title + a few rows + body title
        if (sy > H - 5)  sy = H - 5;
        Rects r;
        r.folderTitle  = TRect(1,      1,     1 + FW, 2);
        r.folder       = TRect(1,      2,     1 + FW, H - 1);
        r.vbar         = TRect(1 + FW, 1,     2 + FW, H - 1);
        r.msgTitle     = TRect(2 + FW, 1,     W - 1,  2);
        r.msg          = TRect(2 + FW, 2,     W - 2,  sy);
        r.msgVsb       = TRect(W - 2,  2,     W - 1,  sy);
        r.contentTitle = TRect(2 + FW, sy,    W - 1,  sy + 1);
        r.content      = TRect(2 + FW, sy + 1, W - 2, H - 1);
        r.contentVsb   = TRect(W - 2,  sy + 1, W - 1, H - 1);
        return r;
    }

public:
    TFolderPane  *folderPane  = nullptr;
    TMsgPane     *msgPane      = nullptr;
    TContentPane *contentPane  = nullptr;

    TMailWindow(const TRect &bounds)
        : TWindowInit(&TMailWindow::initFrame),
          TWindow(bounds, "tvmail", wnNoNumber)
    {
        palette = wpCyanWindow;
        flags   &= ~(wfClose | wfZoom | wfMove);
        growMode = gfGrowHiX | gfGrowHiY;

        Rects R = paneRects();
        folderTitle  = new TPaneTitle(R.folderTitle,  "Folders");
        msgTitle     = new TPaneTitle(R.msgTitle,     "Messages");
        contentTitle = new TPaneTitle(R.contentTitle, "Message");
        folderPane  = new TFolderPane(R.folder);
        vdiv        = new TDivider(R.vbar, true);
        msgVsb      = new TScrollBar(R.msgVsb);
        msgPane     = new TMsgPane(R.msg, msgVsb);
        contentVsb  = new TScrollBar(R.contentVsb);
        contentPane = new TContentPane(R.content, nullptr, contentVsb);

        insert(vdiv);
        insert(folderTitle); insert(msgTitle); insert(contentTitle);
        insert(msgVsb);      insert(contentVsb);
        insert(contentPane); insert(msgPane); insert(folderPane);

        loadFolder();
        ready = True;
        folderPane->select();
        setActivePane(0);
    }

    void setActivePane(int p)
    {
        activePane = p;
        if (folderTitle)  folderTitle->setActive(Boolean(p == 0));
        if (msgTitle)     msgTitle->setActive(Boolean(p == 1));
        if (contentTitle) contentTitle->setActive(Boolean(p == 2));
    }

    void loadFolder()
    {
        gMbox = gFolders[gFolderIdx].mbox;
        loadList();
        if (msgPane) { msgPane->focused = 0; msgPane->reload(); }
        loadContent();
    }

    void loadContent()
    {
        if (!contentPane) return;
        if (gRows.empty() || !msgPane ||
            msgPane->focused < 0 || msgPane->focused >= (int)gRows.size()) {
            contentPane->setLines({ std::string("(no message)") });
            return;
        }
        int b = gRows[msgPane->focused].idx;
        auto ls = splitLines(backendRun("show " + std::to_string(b) + " " + gMbox));
        contentPane->setLines(std::move(ls));
        {
            std::string o; int s = 0;
            if (!Backend::instance().call("mark " + std::to_string(b) + " read " + gMbox, o, s))
                shCapture("tvmail-backend mark " + std::to_string(b) + " read"
                          + mboxArg() + " >/dev/null 2>&1");
        }
        gRows[msgPane->focused].flag = '.';
        msgPane->drawView();
    }

    void reloadFolder() { loadFolder(); }

    void handleEvent(TEvent &e) override
    {
        TWindow::handleEvent(e);
        if (ready && e.what == evBroadcast) {
            switch (e.message.command) {
                case cmFolderPicked: loadFolder();  clearEvent(e); break;
                case cmMsgPicked:    loadContent(); clearEvent(e); break;
                case cmPaneFocused: {
                    void *v = e.message.infoPtr;
                    setActivePane(v == folderPane ? 0 : v == msgPane ? 1 : 2);
                    clearEvent(e);
                    break;
                }
                case cmFocusContent:
                    if (contentPane) contentPane->select();
                    clearEvent(e);
                    break;
                case cmEditDraft:               // hand up to the app
                    message(TProgram::application, evCommand, cmEditDraft, this);
                    clearEvent(e);
                    break;
            }
        }
    }

    void changeBounds(const TRect &bounds) override
    {
        TWindow::changeBounds(bounds);
        Rects R = paneRects();
        folderTitle->changeBounds(R.folderTitle);
        folderPane->changeBounds(R.folder);
        vdiv->changeBounds(R.vbar);
        msgTitle->changeBounds(R.msgTitle);
        msgPane->changeBounds(R.msg);
        msgVsb->changeBounds(R.msgVsb);
        contentTitle->changeBounds(R.contentTitle);
        contentPane->changeBounds(R.content);
        contentVsb->changeBounds(R.contentVsb);
    }

    TColorAttr mapColor(uchar i) override
    {
        switch (i) {
            case 1: return cDim();
            case 2: case 3: return cFrame();
            case 4: case 6: case 8: return cNorm();
            case 5: case 7: return cHi();
        }
        return TView::mapColor(i);
    }
};

// --------------------------------------------------------- message viewer ---
class TTextView : public TScroller {
    std::vector<std::string> lines;
public:
    TTextView(const TRect &b, TScrollBar *h, TScrollBar *v, std::vector<std::string> ls)
        : TScroller(b, h, v), lines(std::move(ls))
    {
        growMode = gfGrowHiX | gfGrowHiY;
        size_t w = 1;
        for (auto &l : lines) w = std::max(w, l.size());
        setLimit((int)w + 1, (int)lines.size());
    }

    TColorAttr mapColor(uchar index) override
    {
        switch (index) {
            case 1: return cNorm();  // normal text
            case 2: return cHi();    // selected text
        }
        return TView::mapColor(index);
    }

    void draw() override
    {
        TColorAttr c = getColor(1);
        for (short y = 0; y < size.y; ++y) {
            TDrawBuffer b;
            b.moveChar(0, ' ', c, size.x);
            int li = delta.y + y;
            if (li >= 0 && li < (int)lines.size()) {
                const std::string &s = lines[li];
                if (delta.x < (int)s.size())
                    b.moveStr(0, s.c_str() + delta.x, c);
            }
            writeLine(0, y, size.x, 1, b);
        }
    }
};

class TMailViewWindow : public TWindow {
public:
    TMailViewWindow(const TRect &b, const char *title, std::vector<std::string> lines)
        : TWindowInit(&TMailViewWindow::initFrame),
          TWindow(b, title, wnNoNumber)
    {
        palette = wpCyanWindow;          // <-- use cyan window palette
        options |= ofTileable;
        TScrollBar *v = standardScrollBar(sbVertical   | sbHandleKeyboard);
        TScrollBar *h = standardScrollBar(sbHorizontal | sbHandleKeyboard);
        TRect r = getExtent();
        r.grow(-1, -1);
        insert(new TTextView(r, h, v, std::move(lines)));
    }

    TColorAttr mapColor(uchar index) override
    {
        switch (index) {
            case 1: return cDim();
            case 2: return cFrame();
            case 3: return cFrame();
            case 4: return cNorm();
            case 5: return cHi();
            case 6: return cNorm();
            case 7: return cHi();
            case 8: return cNorm();
        }
        return TView::mapColor(index);
    }
};

// -------------------------------------------------------- compose window ----
// A real in-app composer (cf. third_party/tvision/examples/tvedit): To/Cc/
// Subject input lines over a TEditor body with a scrollbar + L:C indicator.
// F2 sends via `tvmail-backend send`; closing a modified draft asks first.

class TBodyEditor : public TEditor {
public:
    TBodyEditor(const TRect &b, TScrollBar *h, TScrollBar *v, TIndicator *i, uint sz)
        : TEditor(b, h, v, i, sz) {}
    TColorAttr mapColor(uchar index) override
    {
        switch (index) {
            case 1: return cNorm();   // normal text
            case 2: return cSel();    // selected text
        }
        return TView::mapColor(index);
    }
};

class TFieldLine : public TInputLine {
public:
    TFieldLine(const TRect &b, int lim) : TInputLine(b, lim) {}
    TColorAttr mapColor(uchar index) override
    {
        switch (index) {
            case 1: return cNorm();   // passive
            case 2: return cSel();    // active
            case 3: return cSel();    // selected block
            case 4: return cHi();     // scroll arrows
        }
        return TView::mapColor(index);
    }
};

class TComposeWindow;
static TComposeWindow *gLastCompose = nullptr;   // address book / menu target

static ushort askSaveDraft()
{
    TDialog *d = new TDialog(TRect(0, 0, 46, 9), "Unsent message");
    d->options |= ofCentered;
    d->insert(new TStaticText(TRect(3, 2, 43, 4), "This message hasn't been sent."));
    d->insert(new TButton(TRect(3, 5, 15, 7),  "~S~ave draft", cmYes,    bfDefault));
    d->insert(new TButton(TRect(17, 5, 30, 7), "~D~iscard",    cmNo,     bfNormal));
    d->insert(new TButton(TRect(32, 5, 43, 7), "Cancel",       cmCancel, bfNormal));
    ushort r = TProgram::deskTop->execView(d);
    TObject::destroy(d);
    return r;
}

class TComposeWindow : public TWindow {
    TFieldLine  *toLine = nullptr, *ccLine = nullptr, *subjLine = nullptr;
    TBodyEditor *editor = nullptr;
    Boolean sent = False;
    std::string draftMbox;              // set when editing an existing draft
    int         draftIdx = -1;

    static std::string strip(std::string s)
    {
        size_t a = s.find_first_not_of(" \t\r\n");
        size_t b = s.find_last_not_of(" \t\r\n");
        return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
    }

public:
    void linkDraft(const std::string &m, int i) { draftMbox = m; draftIdx = i; }

    void addRecipient(const std::string &a)
    {
        std::string cur = toLine->data ? toLine->data : "";
        std::string nw  = strip(cur).empty() ? a : cur + ", " + a;
        std::strncpy(toLine->data, nw.c_str(), toLine->maxLen);
        toLine->data[toLine->maxLen] = '\0';
        toLine->drawView();
        toLine->select();
    }

    std::string buildMessage()
    {
        std::string to   = toLine->data   ? toLine->data   : "";
        std::string cc   = ccLine->data   ? ccLine->data   : "";
        std::string subj = subjLine->data ? subjLine->data : "";
        std::string msg = "To: " + to + "\n";
        if (!strip(cc).empty()) msg += "Cc: " + cc + "\n";
        msg += "Subject: " + subj + "\n\n" + bodyText();
        if (msg.empty() || msg.back() != '\n') msg += '\n';
        return msg;
    }

    void dropSourceDraft()
    {
        if (draftMbox.empty() || draftIdx < 0) return;
        shCapture("tvmail-backend delete " + std::to_string(draftIdx)
                  + " --mbox '" + draftMbox + "' 2>&1");
        message(TProgram::application, evCommand, cmReload, nullptr);
        draftIdx = -1;
    }

    void shutDown() override
    {
        if (gLastCompose == this) gLastCompose = nullptr;
        TWindow::shutDown();
    }

    TComposeWindow(const TRect &bounds,
                   const std::string &to, const std::string &cc,
                   const std::string &subj, const std::string &body)
        : TWindowInit(&TComposeWindow::initFrame),
          TWindow(bounds, "Compose", wnNoNumber)
    {
        palette = wpCyanWindow;
        options |= ofTileable;
        gLastCompose = this;
        const int W = size.x;

        auto addField = [&](int y, const char *label, const std::string &val) {
            insert(new TLabel(TRect(2, y, 11, y + 1), label, nullptr));
            auto *il = new TFieldLine(TRect(11, y, W - 2, y + 1), 900);
            il->growMode = gfGrowHiX;
            if (!val.empty()) {
                std::strncpy(il->data, val.c_str(), il->maxLen);
                il->data[il->maxLen] = '\0';
            }
            insert(il);
            return il;
        };
        toLine   = addField(1, "~T~o",      to);
        ccLine   = addField(2, "~C~c",      cc);
        subjLine = addField(3, "~S~ubject", subj);

        TScrollBar *vsb = new TScrollBar(TRect(W - 2, 5, W - 1, size.y - 1));
        insert(vsb);
        TIndicator *ind = new TIndicator(TRect(2, size.y - 1, 16, size.y));
        insert(ind);
        editor = new TBodyEditor(TRect(1, 5, W - 2, size.y - 1), nullptr, vsb, ind, 64000);
        editor->growMode = gfGrowHiX | gfGrowHiY;
        insert(editor);

        if (!body.empty()) {
            editor->insertText(body.data(), (uint)body.size(), False);
            editor->setSelect(0, 0, False);
            editor->trackCursor(False);
            editor->modified = False;
        }
        // blank compose -> cursor in To:, reply -> cursor in the body
        if (to.empty()) toLine->select(); else editor->select();
    }

    TColorAttr mapColor(uchar index) override
    {
        switch (index) {
            case 1: return cDim();    case 2: return cFrame();
            case 3: return cFrame();  case 4: return cNorm();
            case 5: return cHi();     case 6: return cNorm();
            case 7: return cHi();     case 8: return cNorm();
        }
        return TView::mapColor(index);
    }

    std::string bodyText()
    {
        uint n = editor->bufLen;
        std::string s(n, '\0');
        if (n) editor->getText(0, TSpan<char>(&s[0], (size_t)n));
        return s;
    }

    void doSend()
    {
        if (strip(toLine->data ? toLine->data : "").empty()) {
            messageBox("Enter at least one To: address.", mfError | mfOKButton);
            return;
        }
        std::string path = writeTemp(buildMessage(), ".eml");
        std::string out = shCapture("tvmail-backend send < '" + path + "' 2>&1; rm -f '" + path + "'");
        while (!out.empty() && (out.back() == '\n' || out.back() == ' ')) out.pop_back();

        if (out == "sent" || out.empty()) {
            sent = True;
            dropSourceDraft();                  // if this was a saved draft
            messageBox("Message sent.", mfInformation | mfOKButton);
            TEvent ev;                          // close after this event unwinds
            ev.what = evCommand;
            ev.message.command = cmClose;
            ev.message.infoPtr = this;
            putEvent(ev);
        } else {
            messageBox(("Send failed:\n" + out).c_str(), mfError | mfOKButton);
        }
    }

    void handleEvent(TEvent &e) override
    {
        TWindow::handleEvent(e);
        if (e.what != evCommand) return;
        switch (e.message.command) {
            case cmSendMsg: doSend(); clearEvent(e); break;
            case cmInsSig: {
                std::string s = "\n-- \n" + readSigFile() + "\n";
                editor->insertText(s.data(), (uint)s.size(), False);
                clearEvent(e);
                break;
            }
            case cmInsDead: {
                std::string s = readDeadLetter();
                editor->insertText(s.data(), (uint)s.size(), False);
                clearEvent(e);
                break;
            }
        }
    }

    Boolean valid(ushort command) override
    {
        if (!TWindow::valid(command)) return False;
        if (command == cmClose && editor && editor->modified && !sent) {
            ushort r = askSaveDraft();
            if (r == cmCancel) return False;
            if (r == cmYes) {
                std::string path = writeTemp(buildMessage(), ".eml");
                shCapture("tvmail-backend save-draft < '" + path + "' 2>&1; rm -f '" + path + "'");
                dropSourceDraft();
                message(TProgram::application, evCommand, cmReload, nullptr);
            }
        }
        return True;
    }
};

static void parseTemplate(const std::string &t, std::string &to, std::string &cc,
                          std::string &subj, std::string &body)
{
    std::istringstream is(t);
    std::string line, b;
    bool inBody = false;
    auto ieq = [](const std::string &a, const char *k) {
        if (a.size() != std::strlen(k)) return false;
        for (size_t i = 0; i < a.size(); ++i)
            if (std::tolower((unsigned char)a[i]) != std::tolower((unsigned char)k[i]))
                return false;
        return true;
    };
    while (std::getline(is, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (!inBody) {
            if (line.empty()) { inBody = true; continue; }
            size_t p = line.find(':');
            if (p != std::string::npos) {
                std::string k = line.substr(0, p), v = line.substr(p + 1);
                while (!v.empty() && (v[0] == ' ' || v[0] == '\t')) v.erase(0, 1);
                if      (ieq(k, "To"))      to = v;
                else if (ieq(k, "Cc"))      cc = v;
                else if (ieq(k, "Subject")) subj = v;
            }
        } else {
            b += line;
            b += '\n';
        }
    }
    body = b;
}

// ===================================================== address book =========
// A ~/.mailrc alias/group list.  Enter drops the expansion into the most
// recently opened compose window's To: field.
class TAddrPane : public TListViewer {
    std::vector<std::pair<std::string, std::string>> rows;   // name, expansion
public:
    TAddrPane(const TRect &b, TScrollBar *vsb) : TListViewer(b, 1, nullptr, vsb)
    {
        std::istringstream is(backendRun("aliases"));
        std::string ln;
        while (std::getline(is, ln)) {
            if (!ln.empty() && ln.back() == '\r') ln.pop_back();
            size_t t = ln.find('\t');
            if (t != std::string::npos)
                rows.emplace_back(ln.substr(0, t), ln.substr(t + 1));
        }
        setRange((short)rows.size());
    }
    void getText(char *dest, short item, short maxLen) override
    {
        if (item < 0 || item >= (short)rows.size()) { dest[0] = 0; return; }
        char line[600];
        std::snprintf(line, sizeof line, "%-14.14s  %s",
                      rows[item].first.c_str(), rows[item].second.c_str());
        std::strncpy(dest, line, maxLen); dest[maxLen] = 0;
    }
    void selectItem(short i) override
    {
        if (i < 0 || i >= (short)rows.size()) return;
        if (!gLastCompose) {
            messageBox("Open a compose window first.", mfInformation | mfOKButton);
            return;
        }
        gLastCompose->addRecipient(rows[i].second);
    }
    void handleEvent(TEvent &e) override
    {
        if (e.what == evKeyDown && e.keyDown.keyCode == kbEnter) {
            selectItem(focused); clearEvent(e); return;
        }
        TListViewer::handleEvent(e);
    }
    TColorAttr mapColor(uchar i) override
    {
        switch (i) { case 1: case 2: return cNorm(); case 3: return cHi();
                     case 4: return cSel(); case 5: return cDiv(); }
        return TView::mapColor(i);
    }
};

class TAddrBookWindow : public TWindow {
public:
    TAddrBookWindow(const TRect &b)
        : TWindowInit(&TAddrBookWindow::initFrame),
          TWindow(b, "Address book (~/.mailrc)", wnNoNumber)
    {
        palette = wpCyanWindow;
        options |= ofTileable;
        TScrollBar *vsb = new TScrollBar(TRect(size.x - 1, 1, size.x, size.y - 1));
        insert(vsb);
        insert(new TAddrPane(TRect(1, 1, size.x - 1, size.y - 1), vsb));
    }
    TColorAttr mapColor(uchar i) override
    {
        switch (i) {
            case 1: return cDim();  case 2: case 3: return cFrame();
            case 4: case 6: case 8: return cNorm();  case 5: case 7: return cHi();
        }
        return TView::mapColor(i);
    }
};

// ===================================================== signature editor =====
class TSigWindow : public TWindow {
    TBodyEditor *editor = nullptr;
    std::string  path;

    std::string text()
    {
        uint n = editor->bufLen;
        std::string s(n, '\0');
        if (n) editor->getText(0, TSpan<char>(&s[0], (size_t)n));
        return s;
    }
    void save()
    {
        std::string s = text();
        if (FILE *f = fopen(path.c_str(), "wb")) {
            fwrite(s.data(), 1, s.size(), f);
            fclose(f);
            editor->modified = False;
            messageBox("Signature saved.", mfInformation | mfOKButton);
        } else {
            messageBox("Cannot write the signature file.", mfError | mfOKButton);
        }
    }

public:
    TSigWindow(const TRect &b)
        : TWindowInit(&TSigWindow::initFrame),
          TWindow(b, "Signature - ~/.signature   (Ctrl-S saves)", wnNoNumber)
    {
        palette = wpCyanWindow;
        options |= ofTileable;
        path = homePath(".signature");
        TScrollBar *vsb = standardScrollBar(sbVertical | sbHandleKeyboard);
        TRect r = getExtent(); r.grow(-1, -1);
        editor = new TBodyEditor(r, nullptr, vsb, nullptr, 32000);
        editor->growMode = gfGrowHiX | gfGrowHiY;
        insert(editor);
        std::string s = slurp(path);
        if (!s.empty()) {
            editor->insertText(s.data(), (uint)s.size(), False);
            editor->setSelect(0, 0, False);
            editor->trackCursor(False);
            editor->modified = False;
        }
        editor->select();
    }
    void handleEvent(TEvent &e) override
    {
        if (e.what == evKeyDown && e.keyDown.keyCode == kbCtrlS) {
            save(); clearEvent(e); return;
        }
        TWindow::handleEvent(e);
        if (e.what == evCommand && e.message.command == cmSaveSig) {
            save(); clearEvent(e);
        }
    }
    Boolean valid(ushort cmd) override
    {
        if (!TWindow::valid(cmd)) return False;
        if (cmd == cmClose && editor->modified) {
            ushort r = messageBox("Save changes to ~/.signature?",
                                  mfInformation | mfYesNoCancel);
            if (r == cmCancel) return False;
            if (r == cmYes) save();
        }
        return True;
    }
    TColorAttr mapColor(uchar i) override
    {
        switch (i) {
            case 1: return cDim();  case 2: case 3: return cFrame();
            case 4: case 6: case 8: return cNorm();  case 5: case 7: return cHi();
        }
        return TView::mapColor(i);
    }
};

// ========================================================== in-app help ====
static const char *kHelpText =
"tvmail  -  a Turbo Vision mail client                (Esc / Alt-F3 closes)\n"
"\n"
"THE THREE PANES\n"
"  Folders (left)  .  Messages (top right)  .  Message body (bottom right).\n"
"  Tab / Shift-Tab move between panes.  Arrows, PgUp, PgDn move within one.\n"
"  Moving the highlight in Folders reloads the message list; moving it in\n"
"  Messages loads the body below.  Enter on a message jumps to the body.\n"
"\n"
"FOLDERS\n"
"  inbox        /var/mail/$USER    - where exim and pop-pull deliver\n"
"  drafts       ~/Mail/drafts      - messages you chose to keep unsent\n"
"  saved        ~/mbox             - where mail(1) files read messages\n"
"  trash        ~/.local/share/tvmail/trash.mbox\n"
"  dead.letter  ~/dead.letter      - a message mail(1) or tvmail left behind\n"
"\n"
"READING\n"
"  Enter    jump to the body pane and scroll it\n"
"  Ctrl-D   delete  (moves to trash; from trash it deletes for good)\n"
"  F5       reload the current folder      F3   pull new mail (pop-pull)\n"
"  Message > View source shows the raw RFC822 message.\n"
"\n"
"COMPOSING\n"
"  Ctrl-N   new message            Ctrl-R  reply to the selected message\n"
"  F2       send the compose window you are in\n"
"  A new message starts with your ~/.signature (after the quote on replies).\n"
"  Message > Insert signature / Insert dead.letter add them by hand\n"
"  (the classic ~a / ~d escapes).\n"
"  Closing an unsent message offers  Save draft / Discard / Cancel.\n"
"  In the Drafts folder, Enter opens the draft to finish it; sending it\n"
"  removes it from Drafts.\n"
"\n"
"ADDRESS BOOK   (Message > Address book, F4)\n"
"  Lists the alias / group entries from /etc/mailrc and ~/.mailrc.\n"
"  Enter drops the addresses into the To: field of your compose window.\n"
"  You can also just type an alias name in To: - it is expanded on send.\n"
"\n"
"SIGNATURE   (File > Edit signature)\n"
"  Opens ~/.signature in the editor.  Ctrl-S saves.\n"
"\n"
"EDITING   (compose body, signature)\n"
"  Shift-Del cut   Ctrl-Ins copy   Shift-Ins paste\n"
"  Edit > Find / Replace / Find again\n"
"\n"
"mail(1) COMPATIBILITY\n"
"  tvmail reads your ~/.mailrc:  set folder, set DEAD, and the alias / group\n"
"  address book.  Drafts in ~/Mail/drafts open with  mail -f +drafts .\n"
"  It is a face on the same mailbox, not a silo.\n"
"\n"
"KEYS AT A GLANCE\n"
"  F1 help    F3 pull    F5 reload    F6 next window    F10 menu\n"
"  Ctrl-R reply   Ctrl-N new   F2 send   Ctrl-D delete   F4 address book\n"
"  Tab pane   Alt-X quit\n";

class THelpWindow : public TWindow {
public:
    THelpWindow(const TRect &b)
        : TWindowInit(&THelpWindow::initFrame),
          TWindow(b, "Help", wnNoNumber)
    {
        palette = wpCyanWindow;
        options |= ofTileable;
        TScrollBar *v = standardScrollBar(sbVertical   | sbHandleKeyboard);
        TScrollBar *h = standardScrollBar(sbHorizontal | sbHandleKeyboard);
        TRect r = getExtent(); r.grow(-1, -1);
        insert(new TTextView(r, h, v, splitLines(kHelpText)));
    }
    TColorAttr mapColor(uchar i) override
    {
        switch (i) {
            case 1: return cDim();  case 2: case 3: return cFrame();
            case 4: case 6: case 8: return cNorm();  case 5: case 7: return cHi();
        }
        return TView::mapColor(i);
    }
};

// ---------------------------------------------------------------- app -------
class TVMailApp : public TApplication {
public:
    TMailWindow *mainWin = nullptr;

    TVMailApp()
        : TProgInit(&TVMailApp::initStatusLine,
                    &TVMailApp::initMenuBar,
                    &TVMailApp::initDeskTop)
    {
        // editor clipboard + Find/Replace dialogs (tvision ships neither)
        TEditor::clipboard = new TEditor(TRect(0, 0, 0, 0), 0, 0, 0, 0x10000);
        TEditor::clipboard->canUndo = False;
        TEditor::editorDialog = tvmailEditDialog;

        // grey the edit commands until an editor has focus
        TCommandSet es;
        es.enableCmd(cmCut);    es.enableCmd(cmCopy);   es.enableCmd(cmPaste);
        es.enableCmd(cmClear);  es.enableCmd(cmUndo);
        es.enableCmd(cmFind);   es.enableCmd(cmReplace);
        es.enableCmd(cmSearchAgain);
        disableCommands(es);

        mainWin = new TMailWindow(deskTop->getExtent());
        deskTop->insert(mainWin);
    }

    static TMenuBar *initMenuBar(TRect r);
    static TStatusLine *initStatusLine(TRect r);
    static TDeskTop *initDeskTop(TRect r)
    {
        r.a.y++; r.b.y--;
        return new TBlueDeskTop(r);
    }
    void handleEvent(TEvent &e) override;
#ifndef _WIN32
    void idle() override;                   // poll the background pull
#endif

private:
    int  currentRow();
    void viewSource(int row);
    void replyOrCompose(int row);           // row < 0 => new message
    void editDraft(int row);
    void resumeDeadLetter();
    void deleteMsg(int row);
    void pullMail();
    void pullFinished();                    // cmPullDone handler
    void reload();
#ifndef _WIN32
    pid_t       pullPid  = -1;              // >0 while pop-pull runs detached
    int         pullExit = 0;
    std::string pullLogPath;
#endif
    void openComposeWith(const std::string &raw,
                         const std::string &linkMbox = "", int linkIdx = -1,
                         bool addSig = false);
};

TMenuBar *TVMailApp::initMenuBar(TRect r)
{
    r.b.y = r.a.y + 1;
    return new TMenuBar(r,
        *new TSubMenu("~F~ile", kbAltF) +
            *new TMenuItem("~P~ull mail", cmPull,   kbF3, hcNoContext, "F3") +
            *new TMenuItem("~R~eload",    cmReload, kbF5, hcNoContext, "F5") +
            newLine() +
            *new TMenuItem("Edit si~g~nature",   cmEditSig,    kbNoKey, hcNoContext) +
            *new TMenuItem("Resume dead.~l~etter", cmResumeDead, kbNoKey, hcNoContext) +
            newLine() +
            *new TMenuItem("E~x~it", cmQuit, kbAltX, hcNoContext, "Alt-X") +
        *new TSubMenu("~M~essage", kbAltM) +
            *new TMenuItem("~R~eply",         cmReplyMsg,  kbCtrlR,  hcNoContext, "Ctrl-R") +
            *new TMenuItem("~N~ew message",   cmCompose,   kbCtrlN,  hcNoContext, "Ctrl-N") +
            *new TMenuItem("~E~dit draft",    cmEditDraft, kbNoKey,  hcNoContext, "Enter") +
            *new TMenuItem("~S~end draft",    cmSendMsg,   kbF2,     hcNoContext, "F2") +
            newLine() +
            *new TMenuItem("Insert si~g~nature",  cmInsSig,  kbNoKey, hcNoContext) +
            *new TMenuItem("Insert dead.~l~etter", cmInsDead, kbNoKey, hcNoContext) +
            newLine() +
            *new TMenuItem("~A~ddress book",  cmAddrBook,  kbF4,     hcNoContext, "F4") +
            *new TMenuItem("~V~iew source",   cmViewSrc,   kbNoKey,  hcNoContext) +
            *new TMenuItem("~D~elete",        cmDeleteMsg, kbCtrlD,  hcNoContext, "Ctrl-D") +
        *new TSubMenu("~E~dit", kbAltE) +
            *new TMenuItem("~U~ndo",  cmUndo,  kbNoKey, hcNoContext) +
            newLine() +
            *new TMenuItem("Cu~t~",   cmCut,   kbShiftDel, hcNoContext, "Shift-Del") +
            *new TMenuItem("~C~opy",  cmCopy,  kbCtrlIns,  hcNoContext, "Ctrl-Ins") +
            *new TMenuItem("~P~aste", cmPaste, kbShiftIns, hcNoContext, "Shift-Ins") +
            newLine() +
            *new TMenuItem("~F~ind...",    cmFind,        kbNoKey, hcNoContext) +
            *new TMenuItem("~R~eplace...", cmReplace,     kbNoKey, hcNoContext) +
            *new TMenuItem("Find a~g~ain", cmSearchAgain, kbNoKey, hcNoContext) +
        *new TSubMenu("~W~indow", kbAltW) +
            *new TMenuItem("~N~ext",     cmNext,    kbF6,      hcNoContext, "F6") +
            *new TMenuItem("~P~revious", cmPrev,    kbShiftF6, hcNoContext, "Shift-F6") +
            *new TMenuItem("~Z~oom",     cmZoom,    kbNoKey,   hcNoContext) +
            *new TMenuItem("~T~ile",     cmTile,    kbNoKey,   hcNoContext) +
            *new TMenuItem("C~a~scade",  cmCascade, kbNoKey,   hcNoContext) +
            newLine() +
            *new TMenuItem("~C~lose",    cmClose,   kbAltF3,   hcNoContext, "Alt-F3") +
        *new TSubMenu("~H~elp", kbAltH) +
            *new TMenuItem("~C~ontents", cmShowHelp,     kbF1,   hcNoContext, "F1") +
            newLine() +
            *new TMenuItem("~A~bout",    cmAboutBox, kbNoKey, hcNoContext)
        );
}

TStatusLine *TVMailApp::initStatusLine(TRect r)
{
    r.a.y = r.b.y - 1;
    return new TStatusLine(r,
        *new TStatusDef(0, 0xFFFF) +
            *new TStatusItem("~F3~ Pull",    kbF3,    cmPull) +
            *new TStatusItem("~F5~ Reload",  kbF5,    cmReload) +
            *new TStatusItem("~^R~ Reply",   kbCtrlR, cmReplyMsg) +
            *new TStatusItem("~^N~ New",     kbCtrlN, cmCompose) +
            *new TStatusItem("~F2~ Send",    kbF2,    cmSendMsg) +
            *new TStatusItem("~^D~ Del",     kbCtrlD, cmDeleteMsg) +
            *new TStatusItem("~F6~ Next",    kbF6,    cmNext) +
            *new TStatusItem("~Alt-X~ Exit", kbAltX,  cmQuit) +
            *new TStatusItem("~F1~ Help",    kbF1,    cmShowHelp) +
            *new TStatusItem(nullptr,        kbF10,   cmMenu) +
            *new TStatusItem(nullptr,        kbShiftF6, cmPrev) +
            *new TStatusItem(nullptr,        kbAltF3, cmClose)
        );
}

int TVMailApp::currentRow()
{
    if (!mainWin || !mainWin->msgPane) return -1;
    short f = mainWin->msgPane->focused;
    return (f >= 0 && f < (short)gRows.size()) ? (int)f : -1;
}

void TVMailApp::viewSource(int row)
{
    if (row < 0) return;
    int b = gRows[row].idx;
    auto lines = splitLines(backendRun("raw " + std::to_string(b) + " " + gMbox));
    TRect r = deskTop->getExtent();
    r.grow(-3, -1);
    std::string title = "Source of msg " + std::to_string(b);
    deskTop->insert(new TMailViewWindow(r, title.c_str(), std::move(lines)));
}

void TVMailApp::openComposeWith(const std::string &raw, const std::string &linkMbox,
                                int linkIdx, bool addSig)
{
    std::string to, cc, subj, body;
    if (!raw.empty()) parseTemplate(raw, to, cc, subj, body);
    if (addSig) {
        std::string sig = readSigFile();
        if (!sig.empty()) body += "\n-- \n" + sig + "\n";
    }
    TRect r = deskTop->getExtent();
    r.grow(-5, -2);
    auto *w = new TComposeWindow(r, to, cc, subj, body);
    if (!linkMbox.empty()) w->linkDraft(linkMbox, linkIdx);
    deskTop->insert(w);
}

void TVMailApp::replyOrCompose(int row)
{
    if (row >= 0) {
        std::string t = backendRun("compose-template --in-reply-to "
                                   + std::to_string(gRows[row].idx) + " " + gMbox);
        openComposeWith(t, "", -1, /*addSig=*/true);
    } else {
        openComposeWith("", "", -1, /*addSig=*/true);
    }
}

void TVMailApp::editDraft(int row)
{
    if (row < 0 || gMbox != "drafts") return;
    int idx = gRows[row].idx;
    std::string raw = backendRun("raw " + std::to_string(idx) + " " + gMbox);
    openComposeWith(raw, "drafts", idx, /*addSig=*/false);
}

void TVMailApp::resumeDeadLetter()
{
    std::string raw = readDeadLetter();
    if (raw.empty()) {
        messageBox("~/dead.letter is empty.", mfInformation | mfOKButton);
        return;
    }
    openComposeWith(raw, "", -1, /*addSig=*/false);
}

void TVMailApp::deleteMsg(int row)
{
    if (row < 0) return;
    int b = gRows[row].idx;
    if (messageBox(mfConfirmation | mfYesNoCancel, "Delete message %d?", b) != cmYes) return;
    std::string cmd = "tvmail-backend delete " + std::to_string(b) + mboxOpt();
    if (gMbox != "trash") cmd += " --trash trash";   // move to trash, don't destroy
    std::string out = shCapture(cmd + " 2>&1");
    reload();
    if (!out.empty() && out.rfind("deleted", 0) != 0)
        messageBox(out.c_str(), mfInformation | mfOKButton);
}

void TVMailApp::pullMail()
{
#ifndef _WIN32
    if (pullPid > 0) return;                        // one at a time
    std::string log = tempDir() + "tvmail_pull_" + std::to_string(procId()) + ".log";
    pid_t p = Backend::instance().spawnLogged("pull", log);
    if (p > 0) {
        pullPid = p;
        pullLogPath = log;
        TCommandSet cs; cs.enableCmd(cmPull);
        disableCommands(cs);                        // grey "F3 Pull" until it's done
        return;
    }
#endif
    shInteractive("tvmail-backend pull");           // fork failed / Windows: old way
    reload();
}

#ifndef _WIN32
void TVMailApp::idle()
{
    TApplication::idle();
    if (pullPid <= 0) return;
    int st = 0;
    pid_t r = ::waitpid(pullPid, &st, WNOHANG);
    if (r == 0) return;                             // still pulling
    pullExit = (r == pullPid && WIFEXITED(st)) ? WEXITSTATUS(st) : -1;
    pullPid  = -1;
    TEvent ev;                                      // finish outside idle()
    ev.what = evCommand;
    ev.message.command = cmPullDone;
    ev.message.infoPtr = nullptr;
    putEvent(ev);
}
#endif

void TVMailApp::pullFinished()
{
#ifndef _WIN32
    TCommandSet cs; cs.enableCmd(cmPull);
    enableCommands(cs);

    std::string out = slurp(pullLogPath);
    if (!pullLogPath.empty()) ::remove(pullLogPath.c_str());
    pullLogPath.clear();
    reload();

    // pop-pull's last line is the summary; show the tail so verbose
    // "delivered msg ..." lines are visible too.
    std::vector<std::string> ls = splitLines(out);
    std::string tail;
    for (int i = (int)ls.size() - 1, shown = 0; i >= 0 && shown < 8; --i) {
        if (ls[i].empty()) continue;
        tail = ls[i] + (tail.empty() ? std::string() : "\n" + tail);
        ++shown;
    }
    if (tail.empty()) tail = "Pull finished (no output).";
    messageBox(tail.c_str(),
               (pullExit >= 2 ? mfError : mfInformation) | mfOKButton);
#endif
}

void TVMailApp::reload()
{
    if (mainWin) mainWin->reloadFolder();
}

void TVMailApp::handleEvent(TEvent &e)
{
    TApplication::handleEvent(e);
    if (e.what != evCommand) return;
    bool handled = true;
    switch (e.message.command) {
        case cmPull:      pullMail();                   break;
        case cmPullDone:  pullFinished();               break;
        case cmReload:    reload();                     break;
        case cmReplyMsg:  replyOrCompose(currentRow()); break;
        case cmCompose:   replyOrCompose(-1);           break;
        case cmDeleteMsg: deleteMsg(currentRow());      break;
        case cmViewSrc:   viewSource(currentRow());     break;
        case cmEditDraft: editDraft(currentRow());      break;
        case cmResumeDead: resumeDeadLetter();          break;
        case cmAddrBook: {
            TRect r = deskTop->getExtent(); r.grow(-8, -4);
            deskTop->insert(new TAddrBookWindow(r));
            break;
        }
        case cmEditSig: {
            TRect r = deskTop->getExtent(); r.grow(-6, -3);
            deskTop->insert(new TSigWindow(r));
            break;
        }
        case cmShowHelp: {
            TRect r = deskTop->getExtent(); r.grow(-4, -2);
            deskTop->insert(new THelpWindow(r));
            break;
        }
        case cmAboutBox:
            messageBox("tvmail 1.0\n\n"
                       "A Turbo Vision mail client for a local mailbox.\n"
                       "Chris Pollitt  -  MIT licence, no warranty.\n\n"
                       "Turbo Vision by magiblot.  Plumbing: exim +\n"
                       "tvmail-backend + pop-pull.  See HISTORY.md.",
                       mfInformation | mfOKButton);
            break;
        default: handled = false;
    }
    if (handled) clearEvent(e);
}

int main(int argc, char **argv)
{
    // Dev aid: exercise the persistent-backend pipe without the full TUI.
    //   tvmail --selftest   -> prints the raw `list spool` reply on stdout
    if (argc > 1 && std::string(argv[1]) == "--selftest") {
        std::string body; int st = -1;
        bool ok = Backend::instance().call("list spool", body, st);
        std::fprintf(stderr, "selftest: served=%d status=%d bytes=%zu\n",
                     (int)ok, st, body.size());
        std::fwrite(body.data(), 1, body.size(), stdout);
#ifndef _WIN32
        std::string log = tempDir() + "tvmail_selftest.log";
        pid_t p = Backend::instance().spawnLogged("ping", log);
        int wst = 0;
        if (p > 0) while (::waitpid(p, &wst, 0) < 0 && errno == EINTR) {}
        std::fprintf(stderr, "selftest: spawnLogged pid=%ld exit=%d log=%s",
                     (long)p, (p > 0 && WIFEXITED(wst)) ? WEXITSTATUS(wst) : -1,
                     slurp(log).c_str());
        ::remove(log.c_str());
#endif
        Backend::instance().stop();
        return ok ? 0 : 1;
    }

    TVMailApp app;
    app.run();
    app.shutDown();
    Backend::instance().stop();
    return 0;
}
