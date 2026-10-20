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

// ------------------------------------------------------ editor dialogs -----
// Wire up the standard Find / Replace / "search failed" dialogs the TEditor
// needs (tvision ships only a cmCancel stub).  Lifted from tvision's tvedit
// example (tvedit2/3.cpp), trimmed to what a composer uses.
static ushort execDialog(TDialog *d, void *data)
{
    TView *p = TProgram::application->validView(d);
    if (!p)
        return cmCancel;
    if (data)
        p->setData(data);
    ushort result = TProgram::deskTop->execView(p);
    if (result != cmCancel && data)
        p->getData(data);
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
    switch (dialog)
    {
    case edOutOfMemory:
        return messageBox("Not enough memory for this operation.",
                          mfError | mfOKButton);
    case edFind:
    {
        va_start(arg, dialog);
        void *p = va_arg(arg, void *);
        va_end(arg);
        return execDialog(createFindDialog(), p);
    }
    case edSearchFailed:
        return messageBox("Search string not found.", mfError | mfOKButton);
    case edReplace:
    {
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

static FILE *gDebugLog = nullptr;
static FILE *gTraceLog = nullptr;
static bool gTraceFirst = true;
static std::chrono::steady_clock::time_point gTraceStart;
static std::string gBackendDebugPath;
static std::string gBackendTracePath;

static void debugLog(const char *fmt, ...)
{
    if (!gDebugLog)
        return;
    va_list ap;
    va_start(ap, fmt);
    std::vfprintf(gDebugLog, fmt, ap);
    va_end(ap);
    std::fputc('\n', gDebugLog);
    std::fflush(gDebugLog);
}

static long long traceMicros()
{
    return std::chrono::duration_cast<std::chrono::microseconds>(
               std::chrono::steady_clock::now() - gTraceStart)
        .count();
}

static void traceSpan(const char *name, long long start)
{
    if (!gTraceLog)
        return;
    long long end = traceMicros();
    if (!gTraceFirst)
        std::fputs(",\n", gTraceLog);
    gTraceFirst = false;
    std::fprintf(gTraceLog,
                 "  {\"name\":\"%s\",\"cat\":\"tvmail\",\"ph\":\"X\","
                 "\"ts\":%lld,\"dur\":%lld,\"pid\":1,\"tid\":1}",
                 name, start, end - start);
    std::fflush(gTraceLog);
}

struct CliOptions
{
    bool selftest = false;
    bool parseError = false;
    std::string debugPath;
    std::string tracePath;
};

static void printUsage(FILE *out)
{
    std::fprintf(out,
                 "Usage: tvmail [--debug [FILE]] [--trace [FILE]] [--help] [--version]\n"
                 "       tvmail --selftest\n\n"
                 "Options:\n"
                 "  --debug [FILE]   write diagnostic logging (default: debug.log; backend: *.backend.log)\n"
                 "  --trace [FILE]   write Chrome Trace Event profiling data (default: trace.log; backend: *.backend.log)\n"
                 "  --help           show this help\n"
                 "  --version        show the tvmail version\n");
}

static bool parseOptions(int argc, char **argv, CliOptions &options)
{
    for (int i = 1; i < argc; ++i)
    {
        std::string arg(argv[i]);
        if (arg == "--help")
        {
            printUsage(stdout);
            return false;
        }
        if (arg == "--version")
        {
            std::printf("tvmail %s\n", TVMAIL_VERSION);
            return false;
        }
        if (arg == "--selftest")
        {
            options.selftest = true;
            continue;
        }
        std::string *path = nullptr;
        const char *name = nullptr;
        if (arg == "--debug")
        {
            path = &options.debugPath;
            name = "debug.log";
        }
        else if (arg == "--trace")
        {
            path = &options.tracePath;
            name = "trace.log";
        }
        else if (arg.rfind("--debug=", 0) == 0)
        {
            options.debugPath = arg.substr(8);
            continue;
        }
        else if (arg.rfind("--trace=", 0) == 0)
        {
            options.tracePath = arg.substr(8);
            continue;
        }
        else
        {
            std::fprintf(stderr, "tvmail: unknown option: %s\n", arg.c_str());
            printUsage(stderr);
            options.parseError = true;
            return false;
        }
        if (i + 1 < argc && argv[i + 1][0] != '-')
            *path = argv[++i];
        else
            *path = name;
    }
    return true;
}

static void startDiagnostics(const CliOptions &options)
{
    auto backendPath = [](const std::string &path)
    {
        size_t slash = path.find_last_of("/\\");
        size_t dot = path.find_last_of('.');
        if (dot == std::string::npos || dot < slash + 1)
            return path + ".backend";
        return path.substr(0, dot) + ".backend" + path.substr(dot);
    };
    if (!options.debugPath.empty())
    {
        gBackendDebugPath = backendPath(options.debugPath);
        gDebugLog = std::fopen(options.debugPath.c_str(), "ab");
        if (!gDebugLog)
            std::fprintf(stderr, "tvmail: cannot open debug log %s\n",
                         options.debugPath.c_str());
    }
    if (!options.tracePath.empty())
    {
        gBackendTracePath = backendPath(options.tracePath);
        gTraceLog = std::fopen(options.tracePath.c_str(), "wb");
        if (!gTraceLog)
        {
            std::fprintf(stderr, "tvmail: cannot open trace log %s\n",
                         options.tracePath.c_str());
        }
        else
        {
            gTraceStart = std::chrono::steady_clock::now();
            std::fputs("{\"traceEvents\":[\n", gTraceLog);
        }
    }
}

static void stopDiagnostics()
{
    if (gTraceLog)
    {
        std::fputs("\n]}\n", gTraceLog);
        std::fclose(gTraceLog);
        gTraceLog = nullptr;
    }
    if (gDebugLog)
    {
        std::fclose(gDebugLog);
        gDebugLog = nullptr;
    }
}

static std::string shq(const std::string &s);

static std::string backendFlags()
{
    std::string flags;
    if (!gBackendDebugPath.empty())
        flags += " --debug " + shq(gBackendDebugPath);
    if (!gBackendTracePath.empty())
        flags += " --trace " + shq(gBackendTracePath);
    return flags;
}

static std::string backendCommand(const std::string &args)
{
    return "tvmail-backend" + backendFlags() + " " + args;
}

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
    std::string path = tempDir() + "tvmail_" + std::to_string(procId()) + "_" + std::to_string(++seq) + ".sh";
    if (FILE *f = fopen(path.c_str(), "wb"))
    {
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
    std::string path = tempDir() + "tvmail_msg_" + std::to_string(procId()) + "_" + std::to_string(++seq) + suffix;
    if (FILE *f = fopen(path.c_str(), "wb"))
    {
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
    long long started = gTraceLog ? traceMicros() : 0;
    debugLog("shell: %s", body.c_str());
    std::string sp = writeScript(body), out;
    if (FILE *p = popen(shInvoke(sp).c_str(), "r"))
    {
        char buf[8192];
        size_t n;
        while ((n = fread(buf, 1, sizeof buf, p)) > 0)
            out.append(buf, n);
        pclose(p);
    }
    remove(sp.c_str());
    traceSpan("shell", started);
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
    for (int c; (c = std::getchar()) != '\n' && c != EOF;)
    {
    }
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
class Backend
{
public:
    static Backend &instance()
    {
        static Backend b;
        return b;
    }

    // One request.  Returns false if the co-process is unavailable - the
    // caller then falls back to a one-shot `tvmail-backend ...` via the shell.
    bool call(const std::string &req, std::string &body, int &status)
    {
        if (permFail)
            return false;
        if (!up && !start())
            return false;
        if (!writeLine(req) || !readFrame(body, status))
        {
            if (!restart() || !writeLine(req) || !readFrame(body, status))
                return false;
        }
        return true;
    }

    void stop()
    {
        if (wr)
        {
            std::fclose(wr);
            wr = nullptr;
        }
        if (rd)
        {
            std::fclose(rd);
            rd = nullptr;
        }
        if (pid > 0)
        {
            int s;
            while (::waitpid(pid, &s, 0) < 0 && errno == EINTR)
            {
            }
        }
        pid = -1;
        up = false;
    }

    // Fire-and-forget: run `tvmail-backend <args>` detached, stdin from
    // /dev/null and stdout+stderr to logpath.  Returns the pid (the caller
    // reaps it) or -1.  Used for the background mail pull, so F3 no longer
    // suspends the whole UI.
    pid_t spawnLogged(const std::string &args, const std::string &logpath)
    {
        pid_t p = ::fork();
        if (p < 0)
            return -1;
        if (p == 0)
        {
            int n = ::open("/dev/null", O_RDONLY);
            if (n >= 0)
            {
                ::dup2(n, 0);
                if (n > 2)
                    ::close(n);
            }
            int f = ::open(logpath.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0600);
            if (f >= 0)
            {
                ::dup2(f, 1);
                ::dup2(f, 2);
                if (f > 2)
                    ::close(f);
            }
            std::string sc = std::string(kPreamble) + "exec tvmail-backend" + backendFlags() + " " + args + "\n";
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
    bool up = false;
    bool permFail = false;
    int restarts = 0;

    bool giveUp()
    {
        permFail = true;
        return false;
    }

    bool start()
    {
        int a[2], b[2]; // a: parent->child stdin; b: child stdout->parent
        if (::pipe(a) != 0)
            return giveUp();
        if (::pipe(b) != 0)
        {
            ::close(a[0]);
            ::close(a[1]);
            return giveUp();
        }

        pid = ::fork();
        if (pid < 0)
        {
            ::close(a[0]);
            ::close(a[1]);
            ::close(b[0]);
            ::close(b[1]);
            return giveUp();
        }
        if (pid == 0)
        { // ---- child ----
            ::dup2(a[0], 0);
            ::dup2(b[1], 1);
            const char *log = ::getenv("TVMAIL_BACKEND_LOG");
            int e = ::open((log && *log) ? log : "/dev/null",
                           O_WRONLY | O_CREAT | O_APPEND, 0600);
            if (e >= 0)
            {
                ::dup2(e, 2);
                if (e > 2)
                    ::close(e);
            }
            if (a[0] > 2)
                ::close(a[0]);
            if (a[1] > 2)
                ::close(a[1]);
            if (b[0] > 2)
                ::close(b[0]);
            if (b[1] > 2)
                ::close(b[1]);
            std::string sc = std::string(kPreamble) + "exec tvmail-backend" + backendFlags() + " serve\n";
            ::execl(TVMAIL_SH, TVMAIL_SH, "-c", sc.c_str(), (char *)nullptr);
            ::_exit(127);
        }
        ::close(a[0]);
        ::close(b[1]); // ---- parent ----
        wr = ::fdopen(a[1], "w");
        rd = ::fdopen(b[0], "r");
        if (!wr || !rd)
        {
            stop();
            return giveUp();
        }
        // keep the pipe out of every later fork/exec (shell calls, the
        // background pull) so closing wr on shutdown really reaches the child
        ::fcntl(::fileno(wr), F_SETFD, FD_CLOEXEC);
        ::fcntl(::fileno(rd), F_SETFD, FD_CLOEXEC);

        std::string banner;
        int st = -1; // expect "0 5\nready"
        if (!readFrame(banner, st) || st != 0 || banner != "ready")
        {
            stop();
            return giveUp();
        }
        up = true;
        return true;
    }

    bool restart()
    {
        stop();
        if (permFail || ++restarts > 3)
            return giveUp();
        return start();
    }

    bool writeLine(const std::string &s)
    {
        if (!wr)
            return false;
        if (std::fwrite(s.data(), 1, s.size(), wr) != s.size())
            return false;
        if (std::fputc('\n', wr) == EOF)
            return false;
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
        pfd.fd = ::fileno(rd);
        pfd.events = POLLIN;
        pfd.revents = 0;
        if (::poll(&pfd, 1, 8000) <= 0)
            return false;

        std::string hdr;
        int c;
        while ((c = std::fgetc(rd)) != EOF && c != '\n')
        {
            hdr += char(c);
            if (hdr.size() > 64)
                return false;
        }
        if (c == EOF)
            return false;

        long n = -1;
        int st = 0;
        if (std::sscanf(hdr.c_str(), "%d %ld", &st, &n) != 2 || n < 0)
            return false;
        status = st;
        body.resize((size_t)n);
        size_t got = 0;
        while (got < (size_t)n)
        {
            size_t r = std::fread(&body[got], 1, (size_t)n - got, rd);
            if (r == 0)
                return false;
            got += r;
        }
        return true;
    }
};
#else // _WIN32: no fork(); every call uses the one-shot shell path.
class Backend
{
public:
    static Backend &instance()
    {
        static Backend b;
        return b;
    }
    bool call(const std::string &, std::string &, int &) { return false; }
    void stop() {}
};
#endif

// Every backendRun() call blocks the single TV event loop for the round
// trip - fast on a local mailstore, but a real, felt pause over IMAP on a
// slow link (see trace.sh). Flash a status-line message for the duration so
// a slow request doesn't look like a hang. TProgram::statusLine is null
// before the app object exists (the --selftest / early gRemote/gHost probes
// in main() run before TVMailApp is constructed) - both ends just no-op then.
static void showBusy()
{
    if (!TProgram::statusLine)
        return;
    TColorAttr c(TColorBIOS(0x0F), TColorBIOS(0x01)); // white on blue - the
    TDrawBuffer b;                                    // desktop's own accent
    ushort w = b.moveStr(0, " Working... ", c);
    TProgram::statusLine->writeLine(0, 0, w, 1, b);
    TScreen::flushScreen();
}

static void hideBusy()
{
    if (!TProgram::statusLine)
        return;
    TProgram::statusLine->drawView();   // repaint the normal F-key hints
    TScreen::flushScreen();
}

struct BusyGuard
{
    BusyGuard() { showBusy(); }
    ~BusyGuard() { hideBusy(); }
};

// A read-only sub-command: try the warm co-process, else a one-shot shell call.
static std::string backendRun(const std::string &args)
{
    BusyGuard busy;
    std::string body;
    int st = 0;
    if (Backend::instance().call(args, body, st))
        return body;
    return shCapture("tvmail-backend" + backendFlags() + " " + args + " 2>&1");
}

// -------------------------------------------------------- classic colors ----
// Turbo Vision DOS palette — hard-coded so we never depend on terminal quirks.
static inline TColorAttr cNorm() { return TColorAttr(TColorBIOS(0x00), TColorBIOS(0x03)); }  // black on cyan
static inline TColorAttr cHi() { return TColorAttr(TColorBIOS(0x0F), TColorBIOS(0x03)); }    // white on cyan
static inline TColorAttr cSel() { return TColorAttr(TColorBIOS(0x0F), TColorBIOS(0x0B)); }   // white on light-cyan
static inline TColorAttr cDiv() { return TColorAttr(TColorBIOS(0x03), TColorBIOS(0x03)); }   // cyan on cyan
static inline TColorAttr cFrame() { return TColorAttr(TColorBIOS(0x0F), TColorBIOS(0x03)); } // white on cyan
static inline TColorAttr cDim() { return TColorAttr(TColorBIOS(0x08), TColorBIOS(0x03)); }   // grey on cyan

// ---------------------------------------------------------------- data -------
struct MsgRow
{
    int idx = 0;
    char flag = '.';
    std::string date, from, subj;
};
static std::vector<MsgRow> gAll;  // everything the backend listed
static std::vector<MsgRow> gRows; // what the pane shows: gAll, filtered and sorted
                                  // (MsgRow::idx is still the backend index)

enum SortKey
{
    sortDate, // mailbox order, oldest first
    sortFrom,
    sortSubj,
    sortUnread // unread before read
};
static SortKey gSortKey = sortDate;
static bool gSortRev = false;
static std::string gFilter; // type-to-filter text (From / Subject, any case)

struct Folder
{
    const char *name;
    const char *mbox;
};
static const Folder gFolders[] = {
    {"inbox", "spool"},
    {"drafts", "drafts"},
    {"sent", "sent"},
    {"saved", "mbox"},
    {"spam", "spam"},
    {"trash", "trash"},
    {"dead.letter", "dead"}, // always a local file, whatever the mode
};
static const int gFolderCount = int(sizeof gFolders / sizeof gFolders[0]);
static int gFolderIdx = 0; // current folder (drives gMbox)
static std::string gMbox = gFolders[0].mbox;
static bool gRemote = false; // backend "mode" == remote (IMAP)
static std::string gHost;    // backend "host": the box we're talking to

static std::string mboxArg() { return gMbox.empty() ? std::string() : " '" + gMbox + "'"; }
static std::string mboxOpt() { return gMbox.empty() ? std::string() : " --mbox '" + gMbox + "'"; }

// Single-quote a value for /bin/sh.  Every other string interpolated into a
// shCapture() command is program-controlled (folder shorthands, indices);
// this is for the first free-form, user-typed values that reach one (purge
// filters, an attachment's save-as path) - naive quoting breaks on a plain
// apostrophe in the text.
static std::string shq(const std::string &s)
{
    std::string r = "'";
    for (char c : s)
        r += (c == '\'') ? "'\\''" : std::string(1, c);
    return r + "'";
}

static std::string slurp(const std::string &path)
{
    std::string s;
    if (FILE *f = fopen(path.c_str(), "rb"))
    {
        char buf[8192];
        size_t n;
        while ((n = fread(buf, 1, sizeof buf, f)) > 0)
            s.append(buf, n);
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
    while (!s.empty() && (s.back() == '\n' || s.back() == '\r'))
        s.pop_back();
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
    while (std::getline(is, ln))
    {
        if (!ln.empty() && ln.back() == '\r')
            ln.pop_back();
        v.push_back(ln);
    }
    if (v.empty())
        v.push_back(std::string());
    return v;
}

static std::string mailWindowTitle()
{
    if (gHost.empty())
        return "tvmail";
    return "tvmail - " + gHost + (gRemote ? " (remote)" : " (local)");
}

static std::string lowered(std::string s)
{
    for (char &c : s)
        c = (char)std::tolower((unsigned char)c);
    return s;
}

// ------------------------------------------------------ text by columns ----
// Mail text is UTF-8, so a byte count is not a screen width: these measure
// and cut by display columns (TText), keeping accented / wide characters
// whole and the list columns lined up.

// `s` cut or space-padded to exactly `w` columns
static std::string fitCols(const std::string &s, int w)
{
    size_t len = 0, width = 0;
    TText::scroll(s, w, False, len, width);
    std::string r = s.substr(0, len);
    if ((int)width < w)
        r.append(w - width, ' ');
    return r;
}

// Copy into a getText() buffer without splitting a UTF-8 character.
static void putText(char *dest, const std::string &s, short maxLen)
{
    size_t n = std::min(s.size(), (size_t)std::max<short>(maxLen, 0));
    if (n < s.size())
        while (n > 0 && ((uchar)s[n] & 0xC0) == 0x80)
            --n;
    std::memcpy(dest, s.data(), n);
    dest[n] = 0;
}

// Tabs to spaces, stops every 8 columns (counted in columns, not bytes).
static std::string expandTabs(const std::string &s)
{
    if (s.find('\t') == std::string::npos)
        return s;
    std::string r;
    size_t col = 0, i = 0;
    while (i < s.size())
    {
        if (s[i] == '\t')
        {
            size_t n = 8 - col % 8;
            r.append(n, ' ');
            col += n;
            ++i;
            continue;
        }
        size_t j = i;
        if (!TText::next(s, j, col))
            break;
        r.append(s, i, j - i);
        i = j;
    }
    return r;
}

// A short note on the status line ("Copied 12 lines"), wiped by a timer.
static TTimerId gFlashTimer = nullptr;
static void flashStatus(const std::string &msg)
{
    if (!TProgram::statusLine || !TProgram::application)
        return;
    TColorAttr c(TColorBIOS(0x0F), TColorBIOS(0x01));
    TDrawBuffer b;
    ushort w = b.moveStr(0, " " + msg + " ", c);
    TProgram::statusLine->writeLine(0, 0, w, 1, b);
    TScreen::flushScreen();
    if (gFlashTimer)
        TProgram::application->killTimer(gFlashTimer);
    gFlashTimer = TProgram::application->setTimer(2000);
}

// Put copied text on the desktop clipboard as well, for other programs.
// Native Windows: tvision's Win32 call, which is instant.  Elsewhere
// TClipboard::setText runs its helper in the foreground and freezes the UI
// until it answers (under Cygwin's pty: the full 1.5 s timeout, and the text
// never arrived), so do it without waiting:
//  - an OSC 52 escape to the terminal - what reaches the local clipboard
//    over ssh (Windows Terminal, kitty, iTerm2, xterm with it enabled...);
//    terminals without it ignore it, as they did when tvision sent it;
//  - a detached shell for the machine's own clipboard: Cygwin's
//    /dev/clipboard, Wayland, X11, macOS, WSL.  Best effort and silent.
// tvmail's own clipboard (Shift-Ins in a compose window) has it either way.
static void systemClipboard(const std::string &text)
{
#ifdef _WIN32
    TClipboard::setText(text);
#else
    static const char b64[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string osc = "\x1b]52;c;";
    for (size_t i = 0; i < text.size(); i += 3)
    {
        unsigned v = (unsigned char)text[i] << 16;
        if (i + 1 < text.size())
            v |= (unsigned char)text[i + 1] << 8;
        if (i + 2 < text.size())
            v |= (unsigned char)text[i + 2];
        osc += b64[(v >> 18) & 63];
        osc += b64[(v >> 12) & 63];
        osc += i + 1 < text.size() ? b64[(v >> 6) & 63] : '=';
        osc += i + 2 < text.size() ? b64[v & 63] : '=';
    }
    osc += '\x07';
    int tty = ::isatty(1) ? 1 : ::open("/dev/tty", O_WRONLY);
    if (tty >= 0)
    {
        for (size_t done = 0; done < osc.size();)
        {
            ssize_t w = ::write(tty, osc.data() + done, osc.size() - done);
            if (w < 0 && errno == EINTR)
                continue;
            if (w <= 0)
                break;
            done += (size_t)w;
        }
        if (tty != 1)
            ::close(tty);
    }

    std::string f = writeTemp(text, ".clip");
    std::string sc = std::string(kPreamble) +
                     "f=" + shq(f) + "\n"
                     // Cygwin / WSL: clip.exe takes BOM-less UTF-16 (a BOM would
                     // land in the clipboard); Windows programs want CRLF
                     "if command -v clip.exe >/dev/null && command -v iconv >/dev/null; then\n"
                     "  sed 's/$/\\r/' \"$f\" | iconv -f UTF-8 -t UTF-16LE | clip.exe\n"
                     "elif [ -w /dev/clipboard ]; then cat \"$f\" > /dev/clipboard\n"
                     "elif [ -n \"$WAYLAND_DISPLAY\" ] && command -v wl-copy >/dev/null; then wl-copy < \"$f\"\n"
                     "elif [ -n \"$DISPLAY\" ] && command -v xclip >/dev/null; then xclip -in -selection clipboard < \"$f\"\n"
                     "elif [ -n \"$DISPLAY\" ] && command -v xsel >/dev/null; then xsel --input --clipboard < \"$f\"\n"
                     "elif command -v pbcopy >/dev/null; then pbcopy < \"$f\"\n"
                     "fi\n"
                     "rm -f \"$f\"\n";
    pid_t p = ::fork();
    if (p == 0)
    {
        if (::fork() == 0) // the grandchild does it, so nobody need reap it
        {
            ::setsid();
            int n = ::open("/dev/null", O_RDWR);
            if (n >= 0)
            {
                ::dup2(n, 0);
                ::dup2(n, 1);
                ::dup2(n, 2);
                if (n > 2)
                    ::close(n);
            }
            ::execl(TVMAIL_SH, TVMAIL_SH, "-c", sc.c_str(), (char *)nullptr);
        }
        ::_exit(0);
    }
    if (p > 0)
        while (::waitpid(p, nullptr, 0) < 0 && errno == EINTR)
        {
        }
    else
        ::remove(f.c_str());
#endif
}

// Rebuild gRows from gAll: apply the type-to-filter text, then the sort.
static void applyView()
{
    gRows.clear();
    std::string f = lowered(gFilter);
    for (const MsgRow &r : gAll)
        if (f.empty() || lowered(r.from).find(f) != std::string::npos ||
            lowered(r.subj).find(f) != std::string::npos)
            gRows.push_back(r);
    switch (gSortKey)
    {
    case sortDate:
        break; // mailbox order
    case sortFrom:
        std::stable_sort(gRows.begin(), gRows.end(), [](const MsgRow &a, const MsgRow &b)
                         { return lowered(a.from) < lowered(b.from); });
        break;
    case sortSubj:
        std::stable_sort(gRows.begin(), gRows.end(), [](const MsgRow &a, const MsgRow &b)
                         { return lowered(a.subj) < lowered(b.subj); });
        break;
    case sortUnread:
        std::stable_sort(gRows.begin(), gRows.end(), [](const MsgRow &a, const MsgRow &b)
                         { return (a.flag == 'N') > (b.flag == 'N'); });
        break;
    }
    if (gSortRev)
        std::reverse(gRows.begin(), gRows.end());
}

// Flag change after mark read/unread: gAll is the master, gRows a view of it.
static void setRowFlag(int idx, char flag)
{
    for (MsgRow &r : gAll)
        if (r.idx == idx)
            r.flag = flag;
    for (MsgRow &r : gRows)
        if (r.idx == idx)
            r.flag = flag;
}

// Per-folder message and unread counts, for "inbox (3)" in the folder pane.
// -1 = not known (the backend had no line for it).
static int gTotal[gFolderCount];
static int gUnread[gFolderCount];

static void loadCounts()
{
    for (int i = 0; i < gFolderCount; ++i)
        gTotal[i] = gUnread[i] = -1;
    std::istringstream is(backendRun("counts"));
    std::string line;
    while (std::getline(is, line))
    {
        std::istringstream ls(line);
        std::string name, tot, uns;
        std::getline(ls, name, '\t');
        std::getline(ls, tot, '\t');
        std::getline(ls, uns);
        if (tot.empty() || uns.empty() ||
            tot.find_first_not_of("0123456789") != std::string::npos ||
            uns.find_first_not_of("0123456789\r") != std::string::npos)
            continue; // an error line, not a count
        for (int i = 0; i < gFolderCount; ++i)
            if (name == gFolders[i].mbox)
            {
                gTotal[i] = std::atoi(tot.c_str());
                gUnread[i] = std::atoi(uns.c_str());
            }
    }
}

static void loadList()
{
    gAll.clear();
    std::string raw = backendRun("list " + gMbox);
    std::istringstream is(raw);
    std::string line;
    while (std::getline(is, line))
    {
        if (line.empty() || line.find('\t') == std::string::npos)
            continue;
        std::istringstream ls(line);
        std::string idx, flag, date, from, subj;
        std::getline(ls, idx, '\t');
        std::getline(ls, flag, '\t');
        std::getline(ls, date, '\t');
        std::getline(ls, from, '\t');
        std::getline(ls, subj);
        MsgRow r;
        r.idx = std::atoi(idx.c_str());
        r.flag = flag.empty() ? '.' : flag[0];
        r.date = date;
        r.from = from;
        r.subj = subj;
        gAll.push_back(r);
    }
    applyView();
}

// ------------------------------------------------------- blue desktop ------
// Some terminals map Turbo Vision's default desktop palette to a jarring
// colour (seen: solid red). Paint it the classic blue explicitly.
class TBlueBg : public TBackground
{
public:
    TBlueBg(const TRect &r) : TBackground(r, char(0xB0)) {} // 0xB0 = light shade
    void draw() override
    {
        TColorAttr c(TColorBIOS(0x07), TColorBIOS(0x01)); // grey on blue
        TDrawBuffer b;
        b.moveChar(0, pattern, c, size.x);
        for (short y = 0; y < size.y; ++y)
            writeLine(0, y, size.x, 1, b);
    }
};

class TBlueDeskTop : public TDeskTop
{
public:
    TBlueDeskTop(const TRect &r)
        : TDeskInit(&TBlueDeskTop::initBackground), TDeskTop(r) {}
    static TBackground *initBackground(TRect r) { return new TBlueBg(r); }
};

// ================================================= 3-pane mail window =======
// One full-screen window: folders (left) | message list (top-right) /
// message content (bottom-right).  Panes talk via evBroadcast: folder focus
// reloads the list, message focus loads the content.

class TDivider : public TView
{
    bool vert;

public:
    TDivider(const TRect &b, bool v) : TView(b), vert(v) {}
    void draw() override
    {
        TColorAttr c = cFrame();
        TDrawBuffer b;
        if (vert)
        {
            b.moveChar(0, char(0xB3), c, 1); // vertical bar
            for (short y = 0; y < size.y; ++y)
                writeLine(0, y, 1, 1, b);
        }
        else
        {
            b.moveChar(0, char(0xC4), c, size.x); // horizontal bar
            writeLine(0, 0, size.x, 1, b);
        }
    }
};

class TFolderPane : public TListViewer
{
public:
    TFolderPane(const TRect &b) : TListViewer(b, 1, nullptr, nullptr)
    {
        setRange(gFolderCount);
        TListViewer::focusItem(gFolderIdx);
    }
    void getText(char *dest, short item, short maxLen) override
    {
        if (item < 0 || item >= gFolderCount)
        {
            dest[0] = 0;
            return;
        }
        // "inbox (3)": unread for the mail folders, how many are waiting
        // for drafts; nothing for sent / trash / dead.letter.
        std::string label = gFolders[item].name;
        const std::string m = gFolders[item].mbox;
        int n = -1;
        if (m == "spool" || m == "mbox" || m == "spam")
            n = gUnread[item];
        else if (m == "drafts")
            n = gTotal[item];
        if (n > 0)
            label += " (" + std::to_string(n) + ")";
        putText(dest, label, maxLen);
    }
    void focusItem(short item) override
    {
        TListViewer::focusItem(item);
        gFolderIdx = item;
        if (owner)
            message(owner, evBroadcast, cmFolderPicked, this);
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
        switch (i)
        {
        case 1:
        case 2:
            return cNorm();
        case 3:
            return cHi();
        case 4:
            return cSel();
        case 5:
            return cDiv();
        }
        return TView::mapColor(i);
    }
};

class TMsgPane : public TListViewer
{
public:
    TMsgPane(const TRect &b, TScrollBar *vsb) : TListViewer(b, 1, nullptr, vsb)
    {
        setRange((short)gRows.size());
    }
    void getText(char *dest, short item, short maxLen) override
    {
        if (item < 0 || item >= (short)gRows.size())
        {
            dest[0] = 0;
            return;
        }
        const MsgRow &r = gRows[item];
        putText(dest, std::string(1, r.flag) + " " + fitCols(r.date, 16) + " " +
                          fitCols(r.from, 20) + " " + r.subj,
                maxLen);
    }
    void focusItem(short item) override
    {
        TListViewer::focusItem(item);
        if (owner)
            message(owner, evBroadcast, cmMsgPicked, this);
    }
    void selectItem(short) override
    {
        if (!owner)
            return;
        // Enter on a draft opens it for editing; otherwise jump to the body
        message(owner, evBroadcast,
                gMbox == "drafts" ? cmEditDraft : cmFocusContent, this);
    }
    void handleEvent(TEvent &e) override
    {
        if (e.what == evKeyDown)
        {
            if (e.keyDown.keyCode == kbEnter)
            {
                selectItem(focused);
                clearEvent(e);
                return;
            }
            if (filterKey(e.keyDown))
            {
                clearEvent(e);
                return;
            }
        }
        TListViewer::handleEvent(e);
    }
    // Type-to-filter: plain characters build a From/Subject filter, Backspace
    // trims it, Esc drops it.  Nothing else in this pane uses plain letters.
    bool filterKey(const KeyDownEvent &k)
    {
        if (k.controlKeyState & (kbCtrlShift | kbAltShift))
            return false;
        std::string f = gFilter;
        if (k.keyCode == kbEsc)
        {
            if (f.empty())
                return false;
            f.clear();
        }
        else if (k.keyCode == kbBack)
        {
            if (f.empty())
                return false;
            while (f.size() > 1 && ((uchar)f.back() & 0xC0) == 0x80)
                f.pop_back(); // whole UTF-8 character
            f.pop_back();
        }
        else if (k.textLength > 0 && (uchar)k.text[0] >= 0x20 && (uchar)k.text[0] != 0x7F)
        {
            if (k.text[0] == ' ' && f.empty())
                return false; // Space on a message stays "open it"
            f.append(k.text, k.textLength);
        }
        else
            return false;
        gFilter = f;
        applyView();
        if (owner)
            message(owner, evBroadcast, cmFilterChanged, this);
        return true;
    }
    void setState(ushort aState, Boolean enable) override
    {
        TListViewer::setState(aState, enable);
        if (enable && (aState & sfFocused) && owner)
            message(owner, evBroadcast, cmPaneFocused, this);
    }
    TColorAttr mapColor(uchar i) override
    {
        switch (i)
        {
        case 1:
        case 2:
            return cNorm();
        case 3:
            return cHi();
        case 4:
            return cSel();
        case 5:
            return cDiv();
        }
        return TView::mapColor(i);
    }
    // Re-read gRows and put the highlight on row `sel` (clamped).  Goes
    // through the base focusItem(), which scrolls to it without announcing
    // cmMsgPicked - the caller decides whether the body needs reloading.
    void reload(int sel = 0)
    {
        setRange((short)gRows.size());
        if (sel >= (int)gRows.size())
            sel = (int)gRows.size() - 1;
        if (sel < 0)
            sel = 0;
        TListViewer::focusItem((short)sel);
        drawView();
    }
};

// ------------------------------------------------------- mail text view ----
// Read-only text for the body pane and the Source / Help / Purge-preview
// windows.  Lines are measured in screen columns (UTF-8 safe, tabs
// expanded) and can be word-wrapped to the view width (View > Wrap long
// lines); unwrapped, a line that runs past the edge ends in a ">>" mark.
// Edit > Find / Find again search the text.  Shift+arrows / Shift+PgUp/PgDn
// or a mouse drag select whole lines; Edit > Copy (Ctrl-Ins) puts them on
// the clipboard - the whole text when nothing is selected.  Esc drops the
// selection.
static bool gWrap = true; // the body pane's wrap setting

class TMailText : public TScroller
{
    struct Seg // one screen row: a piece of source line `line`
    {
        int line;
        size_t off, len;
        bool cont; // continues the row above (a wrapped line)
    };
    std::vector<std::string> src; // tab-expanded source lines
    std::vector<int> srcWidth;    // their widths in columns
    std::vector<Seg> segs;
    bool wrap;
    int selA = -1, selB = -1; // selected rows: anchor, moving end
    int hlLine = -1;          // last Find match, in source coordinates
    size_t hlOff = 0, hlLen = 0;

    int dx() const { return wrap ? 0 : delta.x; }
    int topLine() const
    {
        return delta.y >= 0 && delta.y < (int)segs.size() ? segs[delta.y].line : 0;
    }

    // Rebuild the screen rows from src; keepLine = the source line to show
    // at the top afterwards (-1: leave the scroll position to the caller).
    void layout(int keepLine)
    {
        segs.clear();
        selA = selB = -1;
        int W = size.x > 0 ? size.x : 1, maxW = 1;
        for (int i = 0; i < (int)src.size(); ++i)
        {
            const std::string &s = src[i];
            maxW = std::max(maxW, srcWidth[i]);
            if (!wrap || srcWidth[i] <= W)
            {
                segs.push_back({i, 0, s.size(), false});
                continue;
            }
            size_t pos = 0;
            bool cont = false;
            while (pos < s.size())
            {
                TStringView rest(s.data() + pos, s.size() - pos);
                size_t len = TText::scroll(rest, W, False);
                if (len == 0) // one character wider than the view
                    len = std::max<size_t>(1, TText::next(rest));
                if (pos + len < s.size())
                {
                    if (s[pos + len] == ' ')
                        ++len; // break on the space: it ends this row
                    else
                    {
                        size_t sp = s.rfind(' ', pos + len - 1);
                        if (sp != std::string::npos && sp > pos)
                            len = sp + 1 - pos; // whole words only
                    }
                }
                segs.push_back({i, pos, len, cont});
                pos += len;
                cont = true;
            }
        }
        if (segs.empty())
            segs.push_back({0, 0, 0, false});
        setLimit(wrap ? W : maxW + 1, (int)segs.size());
        if (keepLine >= 0)
            for (size_t r = 0; r < segs.size(); ++r)
                if (segs[r].line >= keepLine)
                {
                    scrollTo(0, (int)r);
                    break;
                }
    }

    int rowOf(int line, size_t off) const
    {
        int row = 0;
        for (size_t r = 0; r < segs.size(); ++r)
            if (segs[r].line == line && segs[r].off <= off)
                row = (int)r;
        return row;
    }

    // Move the selection's free end to `row` and keep it on screen.
    void extendTo(int row)
    {
        row = std::max(0, std::min(row, (int)segs.size() - 1));
        if (selA < 0)
            selA = row;
        selB = row;
        if (row < delta.y)
            scrollTo(delta.x, row);
        else if (row >= delta.y + size.y)
            scrollTo(delta.x, row - size.y + 1);
        drawView();
    }

    void trackMouse(TEvent &e)
    {
        if (!(state & sfFocused))
            select();
        bool extend = (e.mouse.controlKeyState & kbShift) && selA >= 0;
        int anchor = -1;
        bool moved = false;
        do
        {
            TPoint p = makeLocal(e.mouse.where);
            if (p.y < 0)
                scrollTo(delta.x, delta.y - 1);
            else if (p.y >= size.y)
                scrollTo(delta.x, delta.y + 1);
            int row = delta.y + std::max(0, std::min<int>(p.y, size.y - 1));
            row = std::max(0, std::min(row, (int)segs.size() - 1));
            if (anchor < 0)
                anchor = extend ? selA : row;
            if (row != anchor)
                moved = true;
            if (moved || extend)
            {
                selA = anchor;
                selB = row;
                drawView();
            }
        } while (mouseEvent(e, evMouseMove | evMouseAuto));
        if (!moved && !extend && selA >= 0)
        {
            selA = selB = -1; // a plain click drops the selection
            drawView();
        }
    }

    void copy()
    {
        int a = selA, b = selB;
        if (a < 0)
        {
            a = 0;
            b = (int)segs.size() - 1;
        }
        if (a > b)
            std::swap(a, b);
        std::string out;
        for (int r = a; r <= b; ++r)
        {
            const Seg &g = segs[r];
            if (r > a && !g.cont)
                out += '\n';
            out.append(src[g.line], g.off, g.len);
        }
        out += '\n';
        // tvmail's own clipboard: what Shift-Ins pastes into a compose window
        if (TEditor::clipboard && out.size() < TEditor::clipboard->bufSize)
            TEditor::clipboard->insertText(out.data(), (uint)out.size(), True);
        systemClipboard(out); // and the desktop's, for other programs
        int n = 0;
        for (int r = a; r <= b; ++r)
            n += !segs[r].cont;
        flashStatus(selA < 0 ? "Copied the whole text (" + std::to_string(n) + " lines)"
                             : "Copied " + std::to_string(n) + (n == 1 ? " line" : " lines"));
    }

    static bool isWordChar(char c)
    {
        return std::isalnum((unsigned char)c) || c == '_' || (c & 0x80);
    }

    // Edit > Find (ask) / Find again (reuse TEditor's last search, which
    // the compose editor shares).  Searches on from the last match, or from
    // the top of the view, and wraps around to the start once.
    void find(bool ask)
    {
        if (ask || !*TEditor::findStr)
        {
            TFindDialogRec rec(TEditor::findStr, TEditor::editorFlags);
            if (TEditor::editorDialog(edFind, &rec) == cmCancel)
                return;
            std::strcpy(TEditor::findStr, rec.find);
            TEditor::editorFlags = rec.options & ~efDoReplace;
        }
        std::string pat = TEditor::findStr;
        if (pat.empty() || src.empty())
            return;
        bool cs = TEditor::editorFlags & efCaseSensitive;
        bool ww = TEditor::editorFlags & efWholeWordsOnly;
        if (!cs)
            pat = lowered(pat);

        int n = (int)src.size(), start = hlLine >= 0 ? hlLine : topLine();
        size_t from = hlLine >= 0 ? hlOff + 1 : 0;
        for (int k = 0; k <= n; ++k)
        {
            int L = (start + k) % n;
            std::string s = cs ? src[L] : lowered(src[L]);
            size_t p = (k == 0) ? from : 0;
            while ((p = s.find(pat, p)) != std::string::npos)
            {
                if (k == n && p >= from)
                    break; // back where we began
                bool okWord = !ww || ((p == 0 || !isWordChar(s[p - 1])) &&
                                      (p + pat.size() >= s.size() || !isWordChar(s[p + pat.size()])));
                if (okWord)
                {
                    hlLine = L;
                    hlOff = p;
                    hlLen = pat.size();
                    int row = rowOf(L, p), x = delta.x;
                    if (!wrap)
                    {
                        int col = (int)TText::width(TStringView(src[L].data(), p));
                        if (col < delta.x || col + (int)hlLen > delta.x + size.x)
                            x = std::max(0, col - size.x / 3);
                    }
                    int y = delta.y;
                    if (row < delta.y || row >= delta.y + size.y)
                        y = std::max(0, row - size.y / 3);
                    scrollTo(x, y);
                    drawView();
                    return;
                }
                ++p;
            }
        }
        TEditor::editorDialog(edSearchFailed);
    }

    bool keyDown(const KeyDownEvent &k)
    {
        int page = size.y > 1 ? size.y - 1 : 1;
        ushort code = k.keyCode;
        if ((k.controlKeyState & kbShift) &&
            (code == kbUp || code == kbDown || code == kbPgUp || code == kbPgDn))
        {
            bool up = code == kbUp || code == kbPgUp;
            if (selA < 0) // start at the edge of the view you move away from
                extendTo(up ? delta.y + size.y - 1 : delta.y);
            else
                extendTo(selB + (code == kbUp ? -1 : code == kbDown ? 1 : up ? -page : page));
            return true;
        }
        if (code == kbEsc && selA >= 0)
        {
            selA = selB = -1;
            drawView();
            return true;
        }
        int nx = delta.x, ny = delta.y;
        switch (code)
        {
        case kbUp:
            ny -= 1;
            break;
        case kbDown:
            ny += 1;
            break;
        case kbLeft:
            nx -= 1;
            break;
        case kbRight:
            nx += 1;
            break;
        case kbPgUp:
            ny -= page;
            break;
        case kbPgDn:
            ny += page;
            break;
        case kbHome:
            nx = 0;
            break;
        case kbEnd:
            ny = limit.y;
            break; // scrollTo() clamps
        case kbCtrlPgUp:
            ny = 0;
            break;
        case kbCtrlPgDn:
            ny = limit.y;
            break;
        case kbEnter:
            ny += 1;
            break; // like a pager
        default:
            if (k.charScan.charCode == ' ')
                ny += page;
            else
                return false;
        }
        scrollTo(nx, ny);
        return true;
    }

protected:
    virtual void wrapChanged(bool) {}

public:
    TMailText(const TRect &b, TScrollBar *h, TScrollBar *v,
              std::vector<std::string> ls, bool wrapLines)
        : TScroller(b, h, v), wrap(wrapLines)
    {
        options |= ofFirstClick; // the first click can start a selection
        setLines(std::move(ls));
    }

    void setLines(std::vector<std::string> ls)
    {
        src.clear();
        srcWidth.clear();
        for (auto &l : ls)
        {
            src.push_back(expandTabs(l));
            srcWidth.push_back((int)TText::width(src.back()));
        }
        if (src.empty())
        {
            src.push_back(std::string());
            srcWidth.push_back(0);
        }
        hlLine = -1;
        layout(-1);
        scrollTo(0, 0);
        drawView();
    }

    void setWrap(bool w)
    {
        if (w == wrap)
            return;
        int top = topLine();
        wrap = w;
        scrollTo(0, delta.y);
        layout(top);
        drawView();
    }

    void changeBounds(const TRect &bounds) override
    {
        int top = topLine();
        TScroller::changeBounds(bounds);
        if (wrap)
        {
            layout(top);
            drawView();
        }
    }

    void draw() override
    {
        TColorAttr norm = getColor(1);
        TColorAttr selc = cSel();
        TColorAttr match(TColorBIOS(0x0F), TColorBIOS(0x01)); // white on blue
        int x0 = dx();
        int s0 = std::min(selA, selB), s1 = std::max(selA, selB);
        for (short y = 0; y < size.y; ++y)
        {
            int row = delta.y + y;
            bool inSel = selA >= 0 && row >= s0 && row <= s1;
            TColorAttr c = inSel ? selc : norm;
            TDrawBuffer b;
            b.moveChar(0, ' ', c, size.x);
            if (row >= 0 && row < (int)segs.size())
            {
                const Seg &g = segs[row];
                const std::string &s = src[g.line];
                b.moveStr(0, TStringView(s.data() + g.off, g.len), c, size.x, x0);
                if (g.line == hlLine)
                {
                    size_t a = std::max(hlOff, g.off), e = std::min(hlOff + hlLen, g.off + g.len);
                    if (a < e)
                    {
                        int c0 = (int)TText::width(TStringView(s.data() + g.off, a - g.off)) - x0;
                        int cw = (int)TText::width(TStringView(s.data() + a, e - a));
                        for (int x = std::max(0, c0); x < c0 + cw && x < size.x; ++x)
                            b.putAttribute(x, match);
                    }
                }
                if (!wrap && size.x > 2)
                {
                    TColorAttr m(TColorBIOS(0x0F), inSel ? TColorBIOS(0x0B) : TColorBIOS(0x03));
                    if (srcWidth[g.line] > x0 + size.x)
                        b.moveStr(size.x - 1, "\xC2\xBB", m); // >> more to the right
                    if (x0 > 0 && srcWidth[g.line] > 0)
                        b.moveStr(0, "\xC2\xAB", m); // << more to the left
                }
            }
            writeLine(0, y, size.x, 1, b);
        }
    }

    void handleEvent(TEvent &e) override
    {
        if (e.what == evMouseDown && (e.mouse.buttons & mbLeftButton))
        {
            trackMouse(e);
            clearEvent(e);
            return;
        }
        if (e.what == evKeyDown && (state & sfFocused) && keyDown(e.keyDown))
        {
            clearEvent(e);
            return;
        }
        if (e.what == evCommand && (state & sfFocused))
        {
            switch (e.message.command)
            {
            case cmCopy:
                copy();
                clearEvent(e);
                return;
            case cmFind:
                find(true);
                clearEvent(e);
                return;
            case cmSearchAgain:
                find(false);
                clearEvent(e);
                return;
            case cmToggleWrap:
                setWrap(!wrap);
                wrapChanged(wrap);
                clearEvent(e);
                return;
            }
        }
        TScroller::handleEvent(e);
    }

    // Copy / Find / Find again are live while this is the current view of
    // the active window.  Keyed on sfActive + sfSelected like TEditor, not
    // sfFocused: opening a menu takes the focus, and the items would grey
    // out just as the menu draws them.
    void setState(ushort aState, Boolean enable) override
    {
        TScroller::setState(aState, enable);
        if (aState & (sfActive | sfSelected))
        {
            TCommandSet cs;
            cs.enableCmd(cmCopy);
            cs.enableCmd(cmFind);
            cs.enableCmd(cmSearchAgain);
            if ((state & sfActive) && (state & sfSelected))
                enableCommands(cs);
            else
                disableCommands(cs);
        }
    }
};

class TContentPane : public TMailText
{
protected:
    void wrapChanged(bool w) override { gWrap = w; }

public:
    TContentPane(const TRect &b, TScrollBar *h, TScrollBar *v)
        : TMailText(b, h, v, {std::string()}, gWrap) {}
    void setState(ushort aState, Boolean enable) override
    {
        TMailText::setState(aState, enable);
        if (enable && (aState & sfFocused) && owner)
            message(owner, evBroadcast, cmPaneFocused, this);
    }
    TColorAttr mapColor(uchar i) override
    {
        return i == 1 ? cNorm() : (i == 2 ? cHi() : TView::mapColor(i));
    }
};

// A one-row heading above a pane.  Reverse-video when its pane holds focus,
// dim otherwise - the "which pane am I in?" cue.  The one over the body also
// serves as the rule between the message list and the message text.
class TPaneTitle : public TView
{
    std::string label;
    Boolean active = False;

public:
    TPaneTitle(const TRect &b, const char *l) : TView(b), label(l) {}
    void setActive(Boolean a)
    {
        if (a != active)
        {
            active = a;
            drawView();
        }
    }
    void setLabel(const std::string &l)
    {
        if (l != label)
        {
            label = l;
            drawView();
        }
    }
    void draw() override
    {
        TColorAttr rule = cDim();
        TColorAttr lab = active ? cSel() : cDim();
        TDrawBuffer b;
        b.moveChar(0, char(0xC4), rule, size.x); // horizontal rule
        std::string s = active ? "[ " + label + " ]" : "  " + label + "  ";
        b.moveStr(2, s.c_str(), lab);
        writeLine(0, 0, size.x, 1, b);
    }
    TColorAttr mapColor(uchar) override { return active ? cSel() : cDim(); }
};

class TMailWindow : public TWindow
{
    Boolean ready = False;
    TScrollBar *msgVsb = nullptr, *contentVsb = nullptr;
    TDivider *vdiv = nullptr;
    TPaneTitle *folderTitle = nullptr, *msgTitle = nullptr, *contentTitle = nullptr;
    int activePane = 0;

    struct Rects
    {
        TRect folderTitle, folder, vbar, msgTitle, msg, msgVsb,
            contentTitle, content, contentVsb;
    };
    Rects paneRects() const
    {
        int W = size.x, H = size.y;
        const int FW = 22;
        int sy = 1 + (H - 2) * 2 / 5;
        if (sy < 6)
            sy = 6; // title + a few rows + body title
        if (sy > H - 5)
            sy = H - 5;
        Rects r;
        r.folderTitle = TRect(1, 1, 1 + FW, 2);
        r.folder = TRect(1, 2, 1 + FW, H - 1);
        r.vbar = TRect(1 + FW, 1, 2 + FW, H - 1);
        r.msgTitle = TRect(2 + FW, 1, W - 1, 2);
        r.msg = TRect(2 + FW, 2, W - 2, sy);
        r.msgVsb = TRect(W - 2, 2, W - 1, sy);
        r.contentTitle = TRect(2 + FW, sy, W - 1, sy + 1);
        r.content = TRect(2 + FW, sy + 1, W - 2, H - 1);
        r.contentVsb = TRect(W - 2, sy + 1, W - 1, H - 1);
        return r;
    }

public:
    TFolderPane *folderPane = nullptr;
    TMsgPane *msgPane = nullptr;
    TContentPane *contentPane = nullptr;

    TMailWindow(const TRect &bounds)
        : TWindowInit(&TMailWindow::initFrame),
          TWindow(bounds, mailWindowTitle().c_str(), wnNoNumber)
    {
        palette = wpCyanWindow;
        flags &= ~(wfClose | wfZoom | wfMove);
        growMode = gfGrowHiX | gfGrowHiY;

        Rects R = paneRects();
        folderTitle = new TPaneTitle(R.folderTitle, "Folders");
        msgTitle = new TPaneTitle(R.msgTitle, "Messages");
        contentTitle = new TPaneTitle(R.contentTitle, "Message");
        folderPane = new TFolderPane(R.folder);
        vdiv = new TDivider(R.vbar, true);
        msgVsb = new TScrollBar(R.msgVsb);
        msgPane = new TMsgPane(R.msg, msgVsb);
        contentVsb = new TScrollBar(R.contentVsb);
        contentPane = new TContentPane(R.content, nullptr, contentVsb);

        insert(vdiv);
        insert(folderTitle);
        insert(msgTitle);
        insert(contentTitle);
        insert(msgVsb);
        insert(contentVsb);
        // insert order sets the Tab cycle: folders -> messages -> body
        insert(msgPane);
        insert(contentPane);
        insert(folderPane);

        loadCounts();
        loadFolder();
        ready = True;
        folderPane->select();
        setActivePane(0);
    }

    void setActivePane(int p)
    {
        activePane = p;
        if (folderTitle)
            folderTitle->setActive(Boolean(p == 0));
        if (msgTitle)
            msgTitle->setActive(Boolean(p == 1));
        if (contentTitle)
            contentTitle->setActive(Boolean(p == 2));
    }

    // keep: stay on the same message across the reload (F5, mark, delete,
    // pull...) instead of jumping back to the top of the list.
    void loadFolder(bool keep = false)
    {
        int keepIdx = -1, keepPos = 0;
        if (keep && msgPane && msgPane->focused >= 0 && msgPane->focused < (int)gRows.size())
        {
            keepPos = msgPane->focused;
            keepIdx = gRows[keepPos].idx;
        }
        gMbox = gFolders[gFolderIdx].mbox;
        loadList();
        // the list we just read is the freshest word on this folder's counts
        if (gMbox != "dead")
        {
            gTotal[gFolderIdx] = (int)gAll.size();
            gUnread[gFolderIdx] = (int)std::count_if(gAll.begin(), gAll.end(),
                                                     [](const MsgRow &r)
                                                     { return r.flag == 'N'; });
        }
        if (msgPane)
        {
            int sel = keepPos; // message gone (deleted/moved): nearest row
            if (keepIdx >= 0)
                for (size_t i = 0; i < gRows.size(); ++i)
                    if (gRows[i].idx == keepIdx)
                    {
                        sel = (int)i;
                        break;
                    }
            msgPane->reload(sel);
        }
        refreshTitles();
        if (folderPane)
            folderPane->drawView();
        loadContent();
    }

    // Folder-pane numbers and the Messages heading (filter, sort order).
    void refreshTitles()
    {
        if (!msgTitle)
            return;
        std::string t = "Messages";
        if (!gFilter.empty())
            t += "  filter: " + gFilter + "_  (" + std::to_string(gRows.size()) +
                 " of " + std::to_string(gAll.size()) + ")";
        const char *how = nullptr;
        switch (gSortKey)
        {
        case sortDate:
            how = gSortRev ? "newest first" : nullptr;
            break;
        case sortFrom:
            how = gSortRev ? "from Z-A" : "from A-Z";
            break;
        case sortSubj:
            how = gSortRev ? "subject Z-A" : "subject A-Z";
            break;
        case sortUnread:
            how = gSortRev ? "read first" : "unread first";
            break;
        }
        if (how)
            t += std::string("  sort: ") + how;
        msgTitle->setLabel(t);
    }

    // The view (filter / sort) changed but the folder didn't: rebuild the
    // list without asking the backend again, and stay on the same message
    // if it is still shown.
    void viewChanged(bool resetSel)
    {
        int keepIdx = -1;
        if (!resetSel && msgPane && msgPane->focused >= 0 && msgPane->focused < (int)gRows.size())
            keepIdx = gRows[msgPane->focused].idx;
        applyView();
        int sel = 0;
        for (size_t i = 0; keepIdx >= 0 && i < gRows.size(); ++i)
            if (gRows[i].idx == keepIdx)
                sel = (int)i;
        if (msgPane)
            msgPane->reload(sel);
        refreshTitles();
        loadContent();
    }

    // Showing a message does not read it: the body is only fetched.  It is
    // marked read once you actually look - the body pane takes focus (Enter,
    // Tab, click), or Message > Mark read - so arrowing through the inbox
    // leaves it unread, and "Mark unread" stays marked.
    void loadContent()
    {
        if (!contentPane)
            return;
        if (gRows.empty() || !msgPane ||
            msgPane->focused < 0 || msgPane->focused >= (int)gRows.size())
        {
            contentPane->setLines({std::string("(no message)")});
            return;
        }
        int b = gRows[msgPane->focused].idx;
        contentPane->setLines(splitLines(backendRun("show " + std::to_string(b) + " " + gMbox)));
    }

    // Flag the highlighted message read (or unread), best effort: a failed
    // mark must not disturb what is on screen.
    void markFocused(bool read)
    {
        if (!msgPane || msgPane->focused < 0 || msgPane->focused >= (int)gRows.size())
            return;
        const MsgRow &r = gRows[msgPane->focused];
        if (gMbox == "dead" || (r.flag == 'N') == !read)
            return; // nothing to change
        backendRun("mark " + std::to_string(r.idx) + (read ? " read " : " unread ") + gMbox);
        setRowFlag(r.idx, read ? '.' : 'N');
        if (gUnread[gFolderIdx] >= 0)
            gUnread[gFolderIdx] = std::max(0, gUnread[gFolderIdx] + (read ? -1 : 1));
        msgPane->drawView();
        folderPane->drawView();
        if (gSortKey == sortUnread) // the order depends on the flag
            viewChanged(false);
    }

    void reloadFolder()
    {
        loadCounts();
        loadFolder(true);
    }

    void handleEvent(TEvent &e) override
    {
        TWindow::handleEvent(e);
        if (ready && e.what == evBroadcast)
        {
            switch (e.message.command)
            {
            case cmFolderPicked:
                gFilter.clear(); // a filter belongs to the folder it was typed in
                loadFolder();
                clearEvent(e);
                break;
            case cmMsgPicked:
                loadContent();
                clearEvent(e);
                break;
            case cmFilterChanged:
                viewChanged(true);
                clearEvent(e);
                break;
            case cmPaneFocused:
            {
                void *v = e.message.infoPtr;
                setActivePane(v == folderPane ? 0 : v == msgPane ? 1
                                                                 : 2);
                if (v == contentPane)
                    markFocused(true); // you are reading it now
                clearEvent(e);
                break;
            }
            case cmFocusContent:
                if (contentPane)
                    contentPane->select();
                clearEvent(e);
                break;
            case cmEditDraft: // hand up to the app
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
        switch (i)
        {
        case 1:
            return cDim();
        case 2:
        case 3:
            return cFrame();
        case 4:
        case 6:
        case 8:
            return cNorm();
        case 5:
        case 7:
            return cHi();
        }
        return TView::mapColor(i);
    }
};

// --------------------------------------------------------- message viewer ---
// Source / Help / Purge preview: a TMailText that grows with its window.
class TTextView : public TMailText
{
public:
    TTextView(const TRect &b, TScrollBar *h, TScrollBar *v,
              std::vector<std::string> ls, bool wrapLines = false)
        : TMailText(b, h, v, std::move(ls), wrapLines)
    {
        growMode = gfGrowHiX | gfGrowHiY;
    }

    TColorAttr mapColor(uchar index) override
    {
        switch (index)
        {
        case 1:
            return cNorm(); // normal text
        case 2:
            return cHi(); // selected text
        }
        return TView::mapColor(index);
    }
};

class TMailViewWindow : public TWindow
{
public:
    TMailViewWindow(const TRect &b, const char *title, std::vector<std::string> lines,
                    bool wrapLines = false)
        : TWindowInit(&TMailViewWindow::initFrame),
          TWindow(b, title, wnNoNumber)
    {
        palette = wpCyanWindow; // <-- use cyan window palette
        options |= ofTileable;
        TScrollBar *v = standardScrollBar(sbVertical | sbHandleKeyboard);
        TScrollBar *h = standardScrollBar(sbHorizontal | sbHandleKeyboard);
        TRect r = getExtent();
        r.grow(-1, -1);
        insert(new TTextView(r, h, v, std::move(lines), wrapLines));
    }

    TColorAttr mapColor(uchar index) override
    {
        switch (index)
        {
        case 1:
            return cDim();
        case 2:
            return cFrame();
        case 3:
            return cFrame();
        case 4:
            return cNorm();
        case 5:
            return cHi();
        case 6:
            return cNorm();
        case 7:
            return cHi();
        case 8:
            return cNorm();
        }
        return TView::mapColor(index);
    }
};

// -------------------------------------------------------- compose window ----
// A real in-app composer (cf. third_party/tvision/examples/tvedit): To/Cc/
// Subject input lines over a TEditor body with a scrollbar + L:C indicator.
// F2 sends via `tvmail-backend send`; closing a modified draft asks first.

class TBodyEditor : public TEditor
{
public:
    TBodyEditor(const TRect &b, TScrollBar *h, TScrollBar *v, TIndicator *i, uint sz)
        : TEditor(b, h, v, i, sz) {}
    TColorAttr mapColor(uchar index) override
    {
        switch (index)
        {
        case 1:
            return cNorm(); // normal text
        case 2:
            return cSel(); // selected text
        }
        return TView::mapColor(index);
    }
};

class TFieldLine : public TInputLine
{
public:
    TFieldLine(const TRect &b, int lim) : TInputLine(b, lim) {}
    TColorAttr mapColor(uchar index) override
    {
        switch (index)
        {
        case 1:
            return cNorm(); // passive
        case 2:
            return cSel(); // active
        case 3:
            return cSel(); // selected block
        case 4:
            return cHi(); // scroll arrows
        }
        return TView::mapColor(index);
    }
};

class TComposeWindow;
static TComposeWindow *gLastCompose = nullptr; // address book / menu target

static ushort askSaveDraft()
{
    TDialog *d = new TDialog(TRect(0, 0, 46, 9), "Unsent message");
    d->options |= ofCentered;
    d->insert(new TStaticText(TRect(3, 2, 43, 4), "This message hasn't been sent."));
    d->insert(new TButton(TRect(3, 5, 15, 7), "~S~ave draft", cmYes, bfDefault));
    d->insert(new TButton(TRect(17, 5, 30, 7), "~D~iscard", cmNo, bfNormal));
    d->insert(new TButton(TRect(32, 5, 43, 7), "Cancel", cmCancel, bfNormal));
    ushort r = TProgram::deskTop->execView(d);
    TObject::destroy(d);
    return r;
}

// Prompt for a filesystem path (used by the attachment save dialog), via
// tvision's own file browser - lets you navigate directories instead of
// typing a full path blind. Pre-filled with the attachment's own name (not
// a wildcard) so the directory listing still shows every file and Enter
// alone saves into the current directory under that name. Empty string
// means cancelled.
static std::string askSavePath(const std::string &suggested,
                               const char *title = "Save attachment as")
{
    TFileDialog *d = new TFileDialog("*.*", title, "~N~ame",
                                     fdOKButton, 100);
    char path[MAXPATH];
    std::strncpy(path, suggested.c_str(), sizeof path - 1);
    path[sizeof path - 1] = '\0';
    if (path[0])
        d->setData(path);
    ushort r = TProgram::deskTop->execView(d);
    std::string dest;
    if (r != cmCancel)
    {
        d->getData(path);
        dest = path;
    }
    TObject::destroy(d);
    return dest;
}

class TComposeWindow : public TWindow
{
    TFieldLine *toLine = nullptr, *ccLine = nullptr, *bccLine = nullptr, *subjLine = nullptr;
    TBodyEditor *editor = nullptr;
    Boolean sent = False;
    std::string draftMbox; // set when editing an existing draft
    int draftIdx = -1;
    std::string draftRaw; // the draft as opened: finds it again if indices shift
    std::string initTo, initCc, initBcc, initSubj; // header fields as opened

    static std::string strip(std::string s)
    {
        size_t a = s.find_first_not_of(" \t\r\n");
        size_t b = s.find_last_not_of(" \t\r\n");
        return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
    }

public:
    void linkDraft(const std::string &m, int i, const std::string &raw)
    {
        draftMbox = m;
        draftIdx = i;
        draftRaw = raw;
    }

    // Unsent changes anywhere: the body, or any header field.
    bool dirty()
    {
        auto val = [](TFieldLine *l)
        { return std::string(l->data ? l->data : ""); };
        return (editor && editor->modified) || val(toLine) != initTo ||
               val(ccLine) != initCc || val(bccLine) != initBcc ||
               val(subjLine) != initSubj;
    }

    void addRecipient(const std::string &a)
    {
        std::string cur = toLine->data ? toLine->data : "";
        std::string nw = strip(cur).empty() ? a : cur + ", " + a;
        std::strncpy(toLine->data, nw.c_str(), toLine->maxLen);
        toLine->data[toLine->maxLen] = '\0';
        toLine->drawView();
        toLine->select();
    }

    std::string buildMessage()
    {
        std::string to = toLine->data ? toLine->data : "";
        std::string cc = ccLine->data ? ccLine->data : "";
        std::string bcc = bccLine->data ? bccLine->data : "";
        std::string subj = subjLine->data ? subjLine->data : "";
        // loud and proud: name the tool + version.  tvmail-backend adds the
        // origin user/host (X-Origin-User/-Host) and never overwrites this.
        std::string msg = "X-Mailer: tvmail " TVMAIL_VERSION "\n";
        msg += "To: " + to + "\n";
        if (!strip(cc).empty())
            msg += "Cc: " + cc + "\n";
        if (!strip(bcc).empty())
            msg += "Bcc: " + bcc + "\n";
        msg += "Subject: " + subj + "\n\n" + bodyText();
        if (msg.empty() || msg.back() != '\n')
            msg += '\n';
        return msg;
    }

    // Delete the draft this window was opened from.  Its index may have moved
    // since (another draft deleted or filed meanwhile), so only delete a
    // message whose raw text is still the one we opened; if none matches,
    // leave Drafts alone - a stale extra draft beats deleting the wrong one.
    void dropSourceDraft()
    {
        if (draftMbox.empty() || draftIdx < 0)
            return;
        auto rawAt = [&](int i)
        { return backendRun("raw " + std::to_string(i) + " " + draftMbox); };
        int victim = -1;
        if (!draftRaw.empty() && rawAt(draftIdx) == draftRaw)
            victim = draftIdx;
        else if (!draftRaw.empty())
        {
            std::istringstream is(backendRun("list " + draftMbox));
            std::string line;
            while (victim < 0 && std::getline(is, line))
            {
                if (line.find('\t') == std::string::npos)
                    continue;
                int i = std::atoi(line.c_str());
                if (rawAt(i) == draftRaw)
                    victim = i;
            }
        }
        draftIdx = -1;
        if (victim < 0)
            return;
        shCapture(backendCommand("delete " + std::to_string(victim) + " --mbox '" + draftMbox + "'") + " 2>&1");
        message(TProgram::application, evCommand, cmReload, nullptr);
    }

    void shutDown() override
    {
        if (gLastCompose == this)
            gLastCompose = nullptr;
        TWindow::shutDown();
    }

    TComposeWindow(const TRect &bounds,
                   const std::string &to, const std::string &cc,
                   const std::string &subj, const std::string &body,
                   const std::string &bcc = std::string())
        : TWindowInit(&TComposeWindow::initFrame),
          TWindow(bounds, "Compose", wnNoNumber)
    {
        palette = wpCyanWindow;
        options |= ofTileable;
        gLastCompose = this;
        const int W = size.x;

        auto addField = [&](int y, const char *label, const std::string &val)
        {
            insert(new TLabel(TRect(2, y, 11, y + 1), label, nullptr));
            auto *il = new TFieldLine(TRect(11, y, W - 2, y + 1), 900);
            il->growMode = gfGrowHiX;
            if (!val.empty())
            {
                std::strncpy(il->data, val.c_str(), il->maxLen);
                il->data[il->maxLen] = '\0';
            }
            insert(il);
            return il;
        };
        toLine = addField(1, "~T~o", to);
        ccLine = addField(2, "~C~c", cc);
        bccLine = addField(3, "~B~cc", bcc);
        subjLine = addField(4, "~S~ubject", subj);
        initTo = toLine->data;
        initCc = ccLine->data;
        initBcc = bccLine->data;
        initSubj = subjLine->data;

        TScrollBar *vsb = new TScrollBar(TRect(W - 2, 6, W - 1, size.y - 1));
        insert(vsb);
        TIndicator *ind = new TIndicator(TRect(2, size.y - 1, 16, size.y));
        insert(ind);
        editor = new TBodyEditor(TRect(1, 6, W - 2, size.y - 1), nullptr, vsb, ind, 64000);
        editor->growMode = gfGrowHiX | gfGrowHiY;
        insert(editor);

        if (!body.empty())
        {
            editor->insertText(body.data(), (uint)body.size(), False);
            editor->setSelect(0, 0, False);
            editor->trackCursor(False);
            editor->modified = False;
        }
        // blank compose -> cursor in To:, reply -> cursor in the body
        if (to.empty())
            toLine->select();
        else
            editor->select();
    }

    TColorAttr mapColor(uchar index) override
    {
        switch (index)
        {
        case 1:
            return cDim();
        case 2:
            return cFrame();
        case 3:
            return cFrame();
        case 4:
            return cNorm();
        case 5:
            return cHi();
        case 6:
            return cNorm();
        case 7:
            return cHi();
        case 8:
            return cNorm();
        }
        return TView::mapColor(index);
    }

    std::string bodyText()
    {
        uint n = editor->bufLen;
        std::string s(n, '\0');
        if (n)
            editor->getText(0, TSpan<char>(&s[0], (size_t)n));
        return s;
    }

    void doSend()
    {
        if (strip(toLine->data ? toLine->data : "").empty())
        {
            messageBox("Enter at least one To: address.", mfError | mfOKButton);
            return;
        }
        std::string path = writeTemp(buildMessage(), ".eml");
        std::string out = shCapture(backendCommand("send") + " < '" + path + "' 2>&1; rm -f '" + path + "'");
        while (!out.empty() && (out.back() == '\n' || out.back() == ' '))
            out.pop_back();

        if (out == "sent" || out.empty())
        {
            sent = True;
            dropSourceDraft(); // if this was a saved draft
            messageBox("Message sent.", mfInformation | mfOKButton);
            TEvent ev; // close after this event unwinds
            ev.what = evCommand;
            ev.message.command = cmClose;
            ev.message.infoPtr = this;
            putEvent(ev);
        }
        else
        {
            messageBox(("Send failed:\n" + out).c_str(), mfError | mfOKButton);
        }
    }

    void handleEvent(TEvent &e) override
    {
        TWindow::handleEvent(e);
        if (e.what != evCommand)
            return;
        switch (e.message.command)
        {
        case cmSendMsg:
            doSend();
            clearEvent(e);
            break;
        case cmInsSig:
        {
            std::string s = "\n-- \n" + readSigFile() + "\n";
            editor->insertText(s.data(), (uint)s.size(), False);
            clearEvent(e);
            break;
        }
        case cmInsDead:
        {
            std::string s = readDeadLetter();
            editor->insertText(s.data(), (uint)s.size(), False);
            clearEvent(e);
            break;
        }
        }
    }

    Boolean valid(ushort command) override
    {
        if (!TWindow::valid(command))
            return False;
        // cmQuit too: Alt-X asks every window, and must not drop unsent mail
        if ((command == cmClose || command == cmQuit) && !sent && dirty())
        {
            if (command == cmQuit)
                select(); // show which message the question is about
            ushort r = askSaveDraft();
            if (r == cmCancel)
                return False;
            if (r == cmYes)
            {
                std::string path = writeTemp(buildMessage(), ".eml");
                std::string out = shCapture(backendCommand("save-draft") + " < '" + path + "' 2>&1; rm -f '" + path + "'");
                while (!out.empty() && (out.back() == '\n' || out.back() == ' '))
                    out.pop_back();
                if (out.size() < 11 || out.compare(out.size() - 11, 11, "draft saved") != 0)
                {
                    // keep the window (and the text) - the draft is not saved
                    messageBox(("Draft not saved:\n" + (out.empty() ? std::string("no reply from tvmail-backend") : out)).c_str(),
                               mfError | mfOKButton);
                    return False;
                }
                dropSourceDraft();
                message(TProgram::application, evCommand, cmReload, nullptr);
                // saved: if a quit is called off by another window, this
                // one stays open and must not save the same draft again
                editor->modified = False;
                initTo = toLine->data;
                initCc = ccLine->data;
                initBcc = bccLine->data;
                initSubj = subjLine->data;
            }
        }
        return True;
    }
};

static void parseTemplate(const std::string &t, std::string &to, std::string &cc,
                          std::string &subj, std::string &body, std::string &bcc)
{
    std::istringstream is(t);
    std::string line, b;
    bool inBody = false;
    auto ieq = [](const std::string &a, const char *k)
    {
        if (a.size() != std::strlen(k))
            return false;
        for (size_t i = 0; i < a.size(); ++i)
            if (std::tolower((unsigned char)a[i]) != std::tolower((unsigned char)k[i]))
                return false;
        return true;
    };
    while (std::getline(is, line))
    {
        if (!line.empty() && line.back() == '\r')
            line.pop_back();
        if (!inBody)
        {
            if (line.empty())
            {
                inBody = true;
                continue;
            }
            size_t p = line.find(':');
            if (p != std::string::npos)
            {
                std::string k = line.substr(0, p), v = line.substr(p + 1);
                while (!v.empty() && (v[0] == ' ' || v[0] == '\t'))
                    v.erase(0, 1);
                if (ieq(k, "To"))
                    to = v;
                else if (ieq(k, "Cc"))
                    cc = v;
                else if (ieq(k, "Bcc"))
                    bcc = v;
                else if (ieq(k, "Subject"))
                    subj = v;
            }
        }
        else
        {
            b += line;
            b += '\n';
        }
    }
    body = b;
}

// ===================================================== address book =========
// A ~/.mailrc alias/group list.  Enter drops the expansion into the most
// recently opened compose window's To: field.
class TAddrPane : public TListViewer
{
    std::vector<std::pair<std::string, std::string>> rows; // name, expansion
public:
    TAddrPane(const TRect &b, TScrollBar *vsb) : TListViewer(b, 1, nullptr, vsb)
    {
        std::istringstream is(backendRun("aliases"));
        std::string ln;
        while (std::getline(is, ln))
        {
            if (!ln.empty() && ln.back() == '\r')
                ln.pop_back();
            size_t t = ln.find('\t');
            if (t != std::string::npos)
                rows.emplace_back(ln.substr(0, t), ln.substr(t + 1));
        }
        setRange((short)rows.size());
    }
    void getText(char *dest, short item, short maxLen) override
    {
        if (item < 0 || item >= (short)rows.size())
        {
            dest[0] = 0;
            return;
        }
        putText(dest, fitCols(rows[item].first, 14) + "  " + rows[item].second, maxLen);
    }
    void selectItem(short i) override
    {
        if (i < 0 || i >= (short)rows.size())
            return;
        if (!gLastCompose)
        {
            messageBox("Open a compose window first.", mfInformation | mfOKButton);
            return;
        }
        gLastCompose->addRecipient(rows[i].second);
    }
    void handleEvent(TEvent &e) override
    {
        if (e.what == evKeyDown && e.keyDown.keyCode == kbEnter)
        {
            selectItem(focused);
            clearEvent(e);
            return;
        }
        TListViewer::handleEvent(e);
    }
    TColorAttr mapColor(uchar i) override
    {
        switch (i)
        {
        case 1:
        case 2:
            return cNorm();
        case 3:
            return cHi();
        case 4:
            return cSel();
        case 5:
            return cDiv();
        }
        return TView::mapColor(i);
    }
};

class TAddrBookWindow : public TWindow
{
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
        switch (i)
        {
        case 1:
            return cDim();
        case 2:
        case 3:
            return cFrame();
        case 4:
        case 6:
        case 8:
            return cNorm();
        case 5:
        case 7:
            return cHi();
        }
        return TView::mapColor(i);
    }
};

// ===================================================== MIME parts ==========
// Lists `tvmail-backend parts IDX MBOX`; Enter on a part prompts for a save
// path and runs `tvmail-backend save IDX PARTNO DEST [MBOX]`.
class TPartsPane : public TListViewer
{
    struct Part
    {
        std::string no, type, name, bytes;
    };
    std::vector<Part> rows;
    int msgIdx;
    std::string msgMbox;

public:
    TPartsPane(const TRect &b, TScrollBar *vsb, int idx, const std::string &mbox)
        : TListViewer(b, 1, nullptr, vsb), msgIdx(idx), msgMbox(mbox)
    {
        std::istringstream is(backendRun("parts " + std::to_string(idx) + " " + mbox));
        std::string ln;
        while (std::getline(is, ln))
        {
            if (!ln.empty() && ln.back() == '\r')
                ln.pop_back();
            std::vector<std::string> f;
            size_t p = 0;
            for (;;)
            {
                size_t t = ln.find('\t', p);
                f.push_back(ln.substr(p, t == std::string::npos ? std::string::npos : t - p));
                if (t == std::string::npos)
                    break;
                p = t + 1;
            }
            if (f.size() >= 4)
                rows.push_back({f[0], f[1], f[2], f[3]});
        }
        setRange((short)rows.size());
    }
    void getText(char *dest, short item, short maxLen) override
    {
        if (item < 0 || item >= (short)rows.size())
        {
            dest[0] = 0;
            return;
        }
        char line[64]; // number, type and size are ASCII; the name may not be
        std::snprintf(line, sizeof line, "%-4s %-28.28s %8s bytes  ",
                      rows[item].no.c_str(), rows[item].type.c_str(),
                      rows[item].bytes.c_str());
        putText(dest, line + rows[item].name, maxLen);
    }
    void selectItem(short i) override
    {
        if (i < 0 || i >= (short)rows.size())
            return;
        std::string suggested = rows[i].name.empty() ? "part" + rows[i].no : rows[i].name;
        std::string dest = askSavePath(suggested);
        if (dest.empty())
            return;
        std::string cmd = backendCommand("save " + std::to_string(msgIdx) + " " + rows[i].no + " " + shq(dest));
        if (!msgMbox.empty())
            cmd += " " + shq(msgMbox);
        std::string out = shCapture(cmd + " 2>&1");
        while (!out.empty() && (out.back() == '\n' || out.back() == ' '))
            out.pop_back();
        messageBox(out.c_str(),
                   (out.rfind("wrote", 0) == 0 ? mfInformation : mfError) | mfOKButton);
    }
    void handleEvent(TEvent &e) override
    {
        if (e.what == evKeyDown && e.keyDown.keyCode == kbEnter)
        {
            selectItem(focused);
            clearEvent(e);
            return;
        }
        TListViewer::handleEvent(e);
    }
    TColorAttr mapColor(uchar i) override
    {
        switch (i)
        {
        case 1:
        case 2:
            return cNorm();
        case 3:
            return cHi();
        case 4:
            return cSel();
        case 5:
            return cDiv();
        }
        return TView::mapColor(i);
    }
};

class TPartsWindow : public TWindow
{
public:
    TPartsWindow(const TRect &b, int idx, const std::string &mbox, const char *title)
        : TWindowInit(&TPartsWindow::initFrame),
          TWindow(b, title, wnNoNumber)
    {
        palette = wpCyanWindow;
        options |= ofTileable;
        TScrollBar *vsb = new TScrollBar(TRect(size.x - 1, 1, size.x, size.y - 1));
        insert(vsb);
        insert(new TPartsPane(TRect(1, 1, size.x - 1, size.y - 1), vsb, idx, mbox));
    }
    TColorAttr mapColor(uchar i) override
    {
        switch (i)
        {
        case 1:
            return cDim();
        case 2:
        case 3:
            return cFrame();
        case 4:
        case 6:
        case 8:
            return cNorm();
        case 5:
        case 7:
            return cHi();
        }
        return TView::mapColor(i);
    }
};

// ===================================================== signature editor =====
class TSigWindow : public TWindow
{
    TBodyEditor *editor = nullptr;
    std::string path;

    std::string text()
    {
        uint n = editor->bufLen;
        std::string s(n, '\0');
        if (n)
            editor->getText(0, TSpan<char>(&s[0], (size_t)n));
        return s;
    }
    bool save()
    {
        std::string s = text();
        if (FILE *f = fopen(path.c_str(), "wb"))
        {
            bool ok = fwrite(s.data(), 1, s.size(), f) == s.size();
            ok = (fclose(f) == 0) && ok;
            if (ok)
            {
                editor->modified = False;
                messageBox("Signature saved.", mfInformation | mfOKButton);
                return true;
            }
        }
        messageBox("Cannot write the signature file.", mfError | mfOKButton);
        return false;
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
        TRect r = getExtent();
        r.grow(-1, -1);
        editor = new TBodyEditor(r, nullptr, vsb, nullptr, 32000);
        editor->growMode = gfGrowHiX | gfGrowHiY;
        insert(editor);
        std::string s = slurp(path);
        if (!s.empty())
        {
            editor->insertText(s.data(), (uint)s.size(), False);
            editor->setSelect(0, 0, False);
            editor->trackCursor(False);
            editor->modified = False;
        }
        editor->select();
    }
    void handleEvent(TEvent &e) override
    {
        if (e.what == evKeyDown && e.keyDown.keyCode == kbCtrlS)
        {
            save();
            clearEvent(e);
            return;
        }
        TWindow::handleEvent(e);
        if (e.what == evCommand && e.message.command == cmSaveSig)
        {
            save();
            clearEvent(e);
        }
    }
    Boolean valid(ushort cmd) override
    {
        if (!TWindow::valid(cmd))
            return False;
        if ((cmd == cmClose || cmd == cmQuit) && editor->modified)
        {
            if (cmd == cmQuit)
                select();
            ushort r = messageBox("Save changes to ~/.signature?",
                                  mfInformation | mfYesNoCancel);
            if (r == cmCancel)
                return False;
            if (r == cmYes && !save())
                return False; // keep the window and the text
        }
        return True;
    }
    TColorAttr mapColor(uchar i) override
    {
        switch (i)
        {
        case 1:
            return cDim();
        case 2:
        case 3:
            return cFrame();
        case 4:
        case 6:
        case 8:
            return cNorm();
        case 5:
        case 7:
            return cHi();
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
    "LOCAL / REMOTE\n"
    "  On the mailstore host tvmail reads local mbox files and sends via\n"
    "  sendmail.  On any other machine it is an IMAP/SMTP client of that host -\n"
    "  same panes and keys, folders are IMAP folders, F3 files tagged spam.\n"
    "  Set it up in ~/.config/tvmail/tvmail.conf  (see man tvmail-backend).\n"
    "\n"
    "FOLDERS   (the L / R column = Local mbox or Remote IMAP storage)\n"
    "  inbox        where mail is delivered      (local: /var/mail ; remote: INBOX)\n"
    "  drafts       messages kept unsent\n"
    "  sent         a copy of everything you send\n"
    "  saved        where read mail is filed     (local: ~/mbox ; remote: Archive)\n"
    "  spam         F3 moves messages tagged ***SPAM*** by the server here\n"
    "  trash        Ctrl-D moves here; from trash, delete is permanent\n"
    "               (File > Empty trash... deletes it all, after a count + confirm)\n"
    "  dead.letter  a message mail(1) or tvmail left behind - always a local file\n"
    "\n"
    "READING\n"
    "  Enter    jump to the body pane and scroll it\n"
    "  Ctrl-D   delete  (moves to trash; from trash it deletes for good)\n"
    "  F5       reload the current folder\n"
    "  F3       local: pull new mail (mail-pull)  .  remote: file tagged spam\n"
    "  Message > View source shows the raw RFC822 message.\n"
    "  File > Save message as... writes it to a .eml file.\n"
    "  Long lines wrap at the pane edge; View > Wrap long lines turns that off\n"
    "  (then a line cut off at the edge ends in >>, and Left / Right scroll).\n"
    "\n"
    "FIND AND COPY   (message body, View source, Help)\n"
    "  Edit > Find... / Find again search the text you are reading.\n"
    "  Shift+Up / Down / PgUp / PgDn, or a mouse drag, select whole lines;\n"
    "  Edit > Copy (Ctrl-Ins) copies them - the whole text if none are selected.\n"
    "  Shift-Ins pastes them into a compose window.  Esc drops the selection.\n"
    "\n"
    "READ AND UNREAD\n"
    "  Arrowing through the list only previews a message: it stays unread (N).\n"
    "  It is marked read once you look - Enter or Tab to the body pane - or by\n"
    "  Message > Mark read.  Message > Mark unread puts the N back, and it stays.\n"
    "  Folders show what is waiting:  inbox (3) = unread, drafts (2) = how many.\n"
    "\n"
    "FINDING AND ORDERING   (message list)\n"
    "  Just type: the list narrows to messages whose From or Subject contains the\n"
    "  text (any case).  Backspace trims it, Esc clears it (or View > Clear filter).\n"
    "  View > Sort by date / from / subject / Unread first; choosing the same sort\n"
    "  again, or View > Reverse order, flips it.  Date is mailbox order, oldest\n"
    "  first - Reverse gives newest first.  The Messages heading shows both.\n"
    "\n"
    "OUT OF OFFICE   (File > Out of office...)\n"
    "  Switches an automatic reply on or off.  It runs on the mail server (a Sieve\n"
    "  vacation script, set over ManageSieve), so it keeps answering with tvmail\n"
    "  closed.  Optional first / last day, subject, message, and how often the\n"
    "  same sender gets a reply.  Needs managesieve enabled on the server - see\n"
    "  INSTALL.md - and a [sieve] section in tvmail.conf only if its host differs.\n"
    "\n"
    "WINDOWS\n"
    "  F6 / Shift-F6 cycle windows; Window > List (Alt-0) lists them all.\n"
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

class THelpWindow : public TWindow
{
public:
    THelpWindow(const TRect &b)
        : TWindowInit(&THelpWindow::initFrame),
          TWindow(b, "Help", wnNoNumber)
    {
        palette = wpCyanWindow;
        options |= ofTileable;
        TScrollBar *v = standardScrollBar(sbVertical | sbHandleKeyboard);
        TScrollBar *h = standardScrollBar(sbHorizontal | sbHandleKeyboard);
        TRect r = getExtent();
        r.grow(-1, -1);
        insert(new TTextView(r, h, v, splitLines(kHelpText)));
    }
    TColorAttr mapColor(uchar i) override
    {
        switch (i)
        {
        case 1:
            return cDim();
        case 2:
        case 3:
            return cFrame();
        case 4:
        case 6:
        case 8:
            return cNorm();
        case 5:
        case 7:
            return cHi();
        }
        return TView::mapColor(i);
    }
};

// ---------------------------------------------------------------- app -------
class TVMailApp : public TApplication
{
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
        es.enableCmd(cmCut);
        es.enableCmd(cmCopy);
        es.enableCmd(cmPaste);
        es.enableCmd(cmClear);
        es.enableCmd(cmUndo);
        es.enableCmd(cmFind);
        es.enableCmd(cmReplace);
        es.enableCmd(cmSearchAgain);
        disableCommands(es);

        mainWin = new TMailWindow(deskTop->getExtent());
        deskTop->insert(mainWin);
    }

    static TMenuBar *initMenuBar(TRect r);
    static TStatusLine *initStatusLine(TRect r);
    static TDeskTop *initDeskTop(TRect r)
    {
        r.a.y++;
        r.b.y--;
        return new TBlueDeskTop(r);
    }
    void handleEvent(TEvent &e) override;
#ifndef _WIN32
    void idle() override; // poll the background pull
#endif

private:
    int currentRow();
    void viewSource(int row);
    void replyOrCompose(int row, bool all = false); // row < 0 => new message
    void forwardMsg(int row);
    void editDraft(int row);
    void resumeDeadLetter();
    void deleteMsg(int row);
    void markUnread(int row);
    void markRead(int row);
    void saveMsg(int row);
    void windowList();
    void outOfOffice();
    void moveMsg(int row);
    void viewParts(int row);
    void purgeDialog();
    void emptyTrash();
    void pullMail();
    void pullFinished(); // cmPullDone handler
    void reload();
#ifndef _WIN32
    pid_t pullPid = -1; // >0 while pop-pull runs detached
    int pullExit = 0;
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
                            *new TMenuItem(gRemote ? "File ~s~pam" : "~P~ull mail",
                                           cmPull, kbF3, hcNoContext, "F3") +
                            *new TMenuItem("~R~eload", cmReload, kbF5, hcNoContext, "F5") +
                            *new TMenuItem("Sa~v~e message as...", cmSaveMsg, kbNoKey, hcNoContext) +
                            *new TMenuItem("~O~ut of office...", cmOutOfOffice, kbNoKey, hcNoContext) +
                            *new TMenuItem("P~u~rge...", cmPurgeMsgs, kbNoKey, hcNoContext) +
                            *new TMenuItem("Empty ~t~rash...", cmEmptyTrash, kbNoKey, hcNoContext) +
                            newLine() +
                            *new TMenuItem("Edit si~g~nature", cmEditSig, kbNoKey, hcNoContext) +
                            *new TMenuItem("Resume dead.~l~etter", cmResumeDead, kbNoKey, hcNoContext) +
                            newLine() +
                            *new TMenuItem("E~x~it", cmQuit, kbAltX, hcNoContext, "Alt-X") +
                            *new TSubMenu("~M~essage", kbAltM) +
                            *new TMenuItem("~R~eply", cmReplyMsg, kbCtrlR, hcNoContext, "Ctrl-R") +
                            *new TMenuItem("Repl~y~ All", cmReplyAllMsg, kbNoKey, hcNoContext) +
                            *new TMenuItem("~F~orward", cmForwardMsg, kbNoKey, hcNoContext) +
                            *new TMenuItem("~N~ew message", cmCompose, kbCtrlN, hcNoContext, "Ctrl-N") +
                            *new TMenuItem("~E~dit draft", cmEditDraft, kbNoKey, hcNoContext, "Enter") +
                            *new TMenuItem("~S~end draft", cmSendMsg, kbF2, hcNoContext, "F2") +
                            newLine() +
                            *new TMenuItem("Insert si~g~nature", cmInsSig, kbNoKey, hcNoContext) +
                            *new TMenuItem("Insert dead.~l~etter", cmInsDead, kbNoKey, hcNoContext) +
                            newLine() +
                            *new TMenuItem("~A~ddress book", cmAddrBook, kbF4, hcNoContext, "F4") +
                            *new TMenuItem("~P~arts...", cmViewParts, kbNoKey, hcNoContext) +
                            *new TMenuItem("~V~iew source", cmViewSrc, kbNoKey, hcNoContext) +
                            newLine() +
                            *new TMenuItem("Mar~k~ read", cmMarkRead, kbNoKey, hcNoContext) +
                            *new TMenuItem("Mark ~u~nread", cmMarkUnread, kbNoKey, hcNoContext) +
                            *new TMenuItem("M~o~ve to...", cmMoveMsg, kbNoKey, hcNoContext) +
                            *new TMenuItem("~D~elete", cmDeleteMsg, kbCtrlD, hcNoContext, "Ctrl-D") +
                            *new TSubMenu("~E~dit", kbAltE) +
                            *new TMenuItem("~U~ndo", cmUndo, kbNoKey, hcNoContext) +
                            newLine() +
                            *new TMenuItem("Cu~t~", cmCut, kbShiftDel, hcNoContext, "Shift-Del") +
                            *new TMenuItem("~C~opy", cmCopy, kbCtrlIns, hcNoContext, "Ctrl-Ins") +
                            *new TMenuItem("~P~aste", cmPaste, kbShiftIns, hcNoContext, "Shift-Ins") +
                            newLine() +
                            *new TMenuItem("~F~ind...", cmFind, kbNoKey, hcNoContext) +
                            *new TMenuItem("~R~eplace...", cmReplace, kbNoKey, hcNoContext) +
                            *new TMenuItem("Find a~g~ain", cmSearchAgain, kbNoKey, hcNoContext) +
                            *new TSubMenu("~V~iew", kbAltV) +
                            *new TMenuItem("Sort by ~d~ate", cmSortDate, kbNoKey, hcNoContext) +
                            *new TMenuItem("Sort by ~f~rom", cmSortFrom, kbNoKey, hcNoContext) +
                            *new TMenuItem("Sort by ~s~ubject", cmSortSubj, kbNoKey, hcNoContext) +
                            *new TMenuItem("~U~nread first", cmSortUnread, kbNoKey, hcNoContext) +
                            *new TMenuItem("~R~everse order", cmSortReverse, kbNoKey, hcNoContext) +
                            newLine() +
                            *new TMenuItem("~W~rap long lines", cmToggleWrap, kbNoKey, hcNoContext) +
                            *new TMenuItem("~C~lear filter", cmClearFilter, kbNoKey, hcNoContext, "Esc") +
                            *new TSubMenu("~W~indow", kbAltW) +
                            *new TMenuItem("~N~ext", cmNext, kbF6, hcNoContext, "F6") +
                            *new TMenuItem("~P~revious", cmPrev, kbShiftF6, hcNoContext, "Shift-F6") +
                            *new TMenuItem("~Z~oom", cmZoom, kbNoKey, hcNoContext) +
                            *new TMenuItem("~T~ile", cmTile, kbNoKey, hcNoContext) +
                            *new TMenuItem("C~a~scade", cmCascade, kbNoKey, hcNoContext) +
                            *new TMenuItem("~L~ist...", cmWindowList, kbAlt0, hcNoContext, "Alt-0") +
                            newLine() +
                            *new TMenuItem("~C~lose", cmClose, kbAltF3, hcNoContext, "Alt-F3") +
                            *new TSubMenu("~H~elp", kbAltH) +
                            *new TMenuItem("~C~ontents", cmShowHelp, kbF1, hcNoContext, "F1") +
                            newLine() +
                            *new TMenuItem("~A~bout", cmAboutBox, kbNoKey, hcNoContext));
}

TStatusLine *TVMailApp::initStatusLine(TRect r)
{
    r.a.y = r.b.y - 1;
    return new TStatusLine(r,
                           *new TStatusDef(0, 0xFFFF) +
                               *new TStatusItem(gRemote ? "~F3~ Spam" : "~F3~ Pull", kbF3, cmPull) +
                               *new TStatusItem("~F5~ Reload", kbF5, cmReload) +
                               *new TStatusItem("~^R~ Reply", kbCtrlR, cmReplyMsg) +
                               *new TStatusItem("~^N~ New", kbCtrlN, cmCompose) +
                               *new TStatusItem("~F2~ Send", kbF2, cmSendMsg) +
                               *new TStatusItem("~^D~ Del", kbCtrlD, cmDeleteMsg) +
                               *new TStatusItem("~F6~ Next", kbF6, cmNext) +
                               *new TStatusItem("~Alt-X~ Exit", kbAltX, cmQuit) +
                               *new TStatusItem("~F1~ Help", kbF1, cmShowHelp) +
                               *new TStatusItem(nullptr, kbF10, cmMenu) +
                               *new TStatusItem(nullptr, kbShiftF6, cmPrev) +
                               *new TStatusItem(nullptr, kbAltF3, cmClose));
}

int TVMailApp::currentRow()
{
    if (!mainWin || !mainWin->msgPane)
        return -1;
    short f = mainWin->msgPane->focused;
    return (f >= 0 && f < (short)gRows.size()) ? (int)f : -1;
}

void TVMailApp::viewSource(int row)
{
    if (row < 0)
        return;
    int b = gRows[row].idx;
    auto lines = splitLines(backendRun("raw " + std::to_string(b) + " " + gMbox));
    TRect r = deskTop->getExtent();
    r.grow(-3, -1);
    std::string title = "Source of msg " + std::to_string(b);
    deskTop->insert(new TMailViewWindow(r, title.c_str(), std::move(lines)));
}

void TVMailApp::viewParts(int row)
{
    if (row < 0)
        return;
    int b = gRows[row].idx;
    TRect r = deskTop->getExtent();
    r.grow(-8, -4);
    std::string title = "Parts of msg " + std::to_string(b);
    deskTop->insert(new TPartsWindow(r, b, gMbox, title.c_str()));
}

void TVMailApp::openComposeWith(const std::string &raw, const std::string &linkMbox,
                                int linkIdx, bool addSig)
{
    std::string to, cc, subj, body, bcc;
    if (!raw.empty())
        parseTemplate(raw, to, cc, subj, body, bcc);
    if (addSig)
    {
        std::string sig = readSigFile();
        if (!sig.empty())
            body += "\n-- \n" + sig + "\n";
    }
    TRect r = deskTop->getExtent();
    r.grow(-5, -2);
    auto *w = new TComposeWindow(r, to, cc, subj, body, bcc);
    if (!linkMbox.empty())
        w->linkDraft(linkMbox, linkIdx, raw);
    deskTop->insert(w);
}

void TVMailApp::replyOrCompose(int row, bool all)
{
    if (row >= 0)
    {
        std::string cmd = "compose-template --in-reply-to " + std::to_string(gRows[row].idx) + " " + gMbox;
        if (all)
            cmd += " --reply-all";
        openComposeWith(backendRun(cmd), "", -1, /*addSig=*/true);
    }
    else
    {
        openComposeWith("", "", -1, /*addSig=*/true);
    }
}

void TVMailApp::forwardMsg(int row)
{
    if (row < 0)
        return;
    std::string t = backendRun("compose-template --forward " + std::to_string(gRows[row].idx) + " " + gMbox);
    openComposeWith(t, "", -1, /*addSig=*/true);
}

void TVMailApp::editDraft(int row)
{
    if (row < 0 || gMbox != "drafts")
        return;
    int idx = gRows[row].idx;
    std::string raw = backendRun("raw " + std::to_string(idx) + " " + gMbox);
    openComposeWith(raw, "drafts", idx, /*addSig=*/false);
}

void TVMailApp::resumeDeadLetter()
{
    std::string raw = readDeadLetter();
    if (raw.empty())
    {
        messageBox("~/dead.letter is empty.", mfInformation | mfOKButton);
        return;
    }
    openComposeWith(raw, "", -1, /*addSig=*/false);
}

void TVMailApp::deleteMsg(int row)
{
    if (row < 0)
        return;
    int b = gRows[row].idx;
    if (messageBox(mfConfirmation | mfYesNoCancel, "Delete message %d?", b) != cmYes)
        return;
    std::string cmd = backendCommand("delete " + std::to_string(b) + mboxOpt());
    if (gMbox != "trash")
        cmd += " --trash trash"; // move to trash, don't destroy
    std::string out = shCapture(cmd + " 2>&1");
    reload();
    if (!out.empty() && out.rfind("deleted", 0) != 0)
        messageBox(out.c_str(), mfInformation | mfOKButton);
}

// Mark read / unread.  The window does the work (backend call, flag, folder
// counts); a message only becomes read by itself once the body pane takes
// focus, so an unread mark made here stays put.
void TVMailApp::markUnread(int row)
{
    if (row >= 0 && mainWin)
        mainWin->markFocused(false);
}

void TVMailApp::markRead(int row)
{
    if (row >= 0 && mainWin)
        mainWin->markFocused(true);
}

// File > Save message as...: the raw RFC822 message (headers, attachments and
// all) to a file - an .eml that any mail program can open.
void TVMailApp::saveMsg(int row)
{
    if (row < 0)
        return;
    int b = gRows[row].idx;
    std::string dest = askSavePath("message-" + std::to_string(b) + ".eml", "Save message as");
    if (dest.empty())
        return;
    std::string out = shCapture(backendCommand("raw " + std::to_string(b) + " " + gMbox) +
                                " > " + shq(dest) + " 2>&1");
    while (!out.empty() && (out.back() == '\n' || out.back() == ' '))
        out.pop_back();
    if (out.empty())
        messageBox(("Saved to " + dest).c_str(), mfInformation | mfOKButton);
    else
        messageBox(out.c_str(), mfError | mfOKButton);
}

namespace
{
struct WinEntry
{
    TView *view;
    std::string title;
};
void collectWindow(TView *v, void *arg)
{
    auto *list = static_cast<std::vector<WinEntry> *>(arg);
    if (!(v->options & ofSelectable) || !(v->state & sfVisible))
        return;
    if (TWindow *w = dynamic_cast<TWindow *>(v))
        list->push_back({v, (w->title && *w->title) ? w->title : "(untitled)"});
}

// One row per window, in the dialog's list.
class TWinListPane : public TListViewer
{
    std::vector<WinEntry> *items;

public:
    TWinListPane(const TRect &b, TScrollBar *vsb, std::vector<WinEntry> *its)
        : TListViewer(b, 1, nullptr, vsb), items(its)
    {
        setRange((short)items->size());
    }
    void getText(char *dest, short item, short maxLen) override
    {
        if (item < 0 || item >= (short)items->size())
        {
            dest[0] = 0;
            return;
        }
        putText(dest, (*items)[item].title, maxLen);
    }
    void selectItem(short) override { message(owner, evCommand, cmOK, this); }
    void handleEvent(TEvent &e) override
    {
        if (e.what == evKeyDown && e.keyDown.keyCode == kbEnter)
        {
            selectItem(focused);
            clearEvent(e);
            return;
        }
        TListViewer::handleEvent(e);
    }
    short chosen() const { return focused; }
};
} // namespace

// Window > List... (Alt-0): every window on the desktop, pick one to bring to
// the front.  Compose windows, source, parts, help and the mail window stack up
// fast and F6 only cycles.
void TVMailApp::windowList()
{
    std::vector<WinEntry> wins;
    deskTop->forEach(collectWindow, &wins);
    if (wins.empty())
        return;
    std::reverse(wins.begin(), wins.end()); // forEach walks back to front: top window first

    int n = std::min<int>((int)wins.size(), 12);
    TDialog *d = new TDialog(TRect(0, 0, 52, 7 + n), "Window list");
    d->options |= ofCentered;
    TScrollBar *vsb = new TScrollBar(TRect(49, 2, 50, 2 + n));
    d->insert(vsb);
    TWinListPane *lp = new TWinListPane(TRect(2, 2, 49, 2 + n), vsb, &wins);
    d->insert(lp);
    d->insert(new TButton(TRect(14, 3 + n, 26, 5 + n), "~S~elect", cmOK, bfDefault));
    d->insert(new TButton(TRect(28, 3 + n, 40, 5 + n), "Cancel", cmCancel, bfNormal));
    d->selectNext(False);

    ushort r = deskTop->execView(d);
    short sel = lp->chosen();
    TObject::destroy(d);
    if (r != cmCancel && sel >= 0 && sel < (short)wins.size())
        wins[sel].view->select();
}

// File > Out of office...: an auto-reply has to run where the mail arrives, so
// this edits a Sieve vacation script on the master over ManageSieve (via
// tvmail-backend vacation).  Nothing here keeps running after tvmail exits.
void TVMailApp::outOfOffice()
{
    // --- what is there now -----------------------------------------------
    std::string st = backendRun("vacation status");
    if (st.rfind("state: ", 0) != 0)
    {
        while (!st.empty() && (st.back() == '\n' || st.back() == ' '))
            st.pop_back();
        messageBox(st.empty() ? "Could not read the out-of-office settings\n(is tvmail-backend up to date?)."
                              : st.c_str(),
                   mfError | mfOKButton);
        return;
    }
    std::string state, otherActive, subject = "Out of office", days = "7", start, end, addresses;
    std::string message = "Thank you for your message.\n"
                          "I am out of the office and will reply when I am back.\n";
    {
        std::vector<std::string> ls = splitLines(st);
        size_t i = 0;
        bool haveScript = false;
        for (; i < ls.size() && !ls[i].empty(); ++i)
        {
            size_t c = ls[i].find(": ");
            if (c == std::string::npos)
                continue;
            std::string k = ls[i].substr(0, c), v = ls[i].substr(c + 2);
            if (k == "state")
                state = v;
            else if (k == "other_active")
                otherActive = v;
            else if (k == "subject" && !v.empty())
                subject = v, haveScript = true;
            else if (k == "days" && !v.empty())
                days = v;
            else if (k == "start")
                start = v;
            else if (k == "end")
                end = v;
            else if (k == "addresses")
                addresses = v;
        }
        if (haveScript)
        {
            std::string m;
            for (++i; i < ls.size(); ++i)
                m += ls[i] + "\n";
            while (m.size() > 1 && m[m.size() - 1] == '\n' && m[m.size() - 2] == '\n')
                m.pop_back();
            if (!m.empty() && m != "\n")
                message = m;
        }
    }

    // --- the dialog ------------------------------------------------------
    // Loops until saved or cancelled: a rejected change (or a No to switching
    // off another Sieve script) brings the dialog back with what was typed.
    auto trim = [](std::string s)
    {
        size_t a = s.find_first_not_of(" \t");
        size_t b = s.find_last_not_of(" \t");
        return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
    };
    ushort on = (state == "on") ? 1 : 0;
    bool force = false; // the user already agreed to replace otherActive
    for (;;)
    {
        const ushort kMemoMax = 4096;
        TDialog *d = new TDialog(TRect(0, 0, 70, 24), "Out of office");
        d->options |= ofCentered;

        TCheckBoxes *onCb = new TCheckBoxes(TRect(3, 2, 60, 3), new TSItem("Automatic reply is ~O~N", 0));
        d->insert(onCb);

        TInputLine *subjIl = new TInputLine(TRect(14, 4, 67, 5), 120);
        d->insert(subjIl);
        d->insert(new TLabel(TRect(2, 4, 14, 5), "Su~b~ject", subjIl));

        TInputLine *fromIl = new TInputLine(TRect(14, 6, 26, 7), 11);
        d->insert(fromIl);
        d->insert(new TLabel(TRect(2, 6, 14, 7), "~F~rom date", fromIl));
        d->insert(new TStaticText(TRect(28, 6, 68, 7), "YYYY-MM-DD; blank = from now on"));

        TInputLine *toIl = new TInputLine(TRect(14, 7, 26, 8), 11);
        d->insert(toIl);
        d->insert(new TLabel(TRect(2, 7, 14, 8), "~T~o date", toIl));
        d->insert(new TStaticText(TRect(28, 7, 68, 8), "last day; blank = until switched off"));

        // The server only answers mail addressed to one of these.  Mail pulled in
        // from a POP account is addressed to *that* account, not to this one.
        TInputLine *addrIl = new TInputLine(TRect(14, 9, 67, 10), 160);
        d->insert(addrIl);
        d->insert(new TLabel(TRect(2, 9, 14, 10), "~A~ddresses", addrIl));
        d->insert(new TStaticText(TRect(14, 10, 68, 11), "public + local (comma separated); the server answers mail sent to any"));

        TInputLine *daysIl = new TInputLine(TRect(43, 12, 49, 13), 4);
        d->insert(daysIl);
        d->insert(new TLabel(TRect(2, 12, 43, 13), "~R~eply to each sender at most every", daysIl));
        d->insert(new TStaticText(TRect(50, 12, 56, 13), "days"));

        TScrollBar *vsb = new TScrollBar(TRect(65, 15, 66, 20));
        d->insert(vsb);
        TMemo *memo = new TMemo(TRect(3, 15, 65, 20), nullptr, vsb, nullptr, kMemoMax);
        d->insert(memo);
        d->insert(new TLabel(TRect(2, 14, 20, 15), "~M~essage", memo));

        d->insert(new TButton(TRect(20, 21, 32, 23), "~S~ave", cmOK, bfDefault));
        d->insert(new TButton(TRect(36, 21, 48, 23), "Cancel", cmCancel, bfNormal));

        onCb->setData(&on);
        // TInputLine::setData copies its full buffer size, so hand it one
        auto fill = [](TInputLine *il, const std::string &s, size_t maxLen)
        {
            std::vector<char> b(maxLen + 1, 0);
            std::memcpy(b.data(), s.data(), std::min(s.size(), maxLen));
            il->setData(b.data());
        };
        fill(subjIl, subject, 120);
        fill(fromIl, start, 11);
        fill(toIl, end, 11);
        fill(addrIl, addresses, 160);
        fill(daysIl, days, 4);
        {
            std::vector<char> md(sizeof(ushort) + kMemoMax);
            ushort len = (ushort)std::min<size_t>(message.size(), kMemoMax - 1);
            std::memcpy(md.data(), &len, sizeof len);
            std::memcpy(md.data() + sizeof len, message.data(), len);
            memo->setData(md.data());
        }
        d->selectNext(False);

        ushort r = TProgram::deskTop->execView(d);
        if (r == cmCancel)
        {
            TObject::destroy(d);
            return;
        }
        onCb->getData(&on);
        char sbuf[121], fbuf[12], tbuf[12], dbuf[5], abuf[161];
        addrIl->getData(abuf);
        subjIl->getData(sbuf);
        fromIl->getData(fbuf);
        toIl->getData(tbuf);
        daysIl->getData(dbuf);
        std::vector<char> md(sizeof(ushort) + kMemoMax);
        memo->getData(md.data());
        ushort mlen = 0;
        std::memcpy(&mlen, md.data(), sizeof mlen);
        std::string text(md.data() + sizeof mlen, std::min<size_t>(mlen, kMemoMax));
        TObject::destroy(d);

        // keep what was typed, for a second round if this one fails
        subject = trim(sbuf);
        start = trim(fbuf);
        end = trim(tbuf);
        addresses = trim(abuf);
        days = trim(dbuf);
        // the editor may use CR or CRLF line ends; the backend wants plain LF
        message.clear();
        for (size_t i = 0; i < text.size(); ++i)
        {
            if (text[i] == '\r')
            {
                message += '\n';
                if (i + 1 < text.size() && text[i + 1] == '\n')
                    ++i;
            }
            else
                message += text[i];
        }

        std::string out;
        bool ok;
        if (!(on & 1))
        {
            out = backendRun("vacation off");
            ok = out.rfind("off", 0) == 0;
        }
        else
        {
            if (!otherActive.empty() && !force)
            {
                std::string q = "The Sieve script '" + otherActive + "' is active on the server.\n"
                                "Only one script can be, so switching on the out-of-office\n"
                                "reply will switch that one off.\n\nContinue?";
                if (messageBox(mfConfirmation | mfYesNoCancel, "%s", q.c_str()) != cmYes)
                    continue; // back to the dialog, entries intact
                force = true;
            }
            std::string f = writeTemp(message, ".txt");
            std::string args = "vacation on --message-file " + shq(f) + " --subject " + shq(subject) +
                               " --days " + shq(days.empty() ? "7" : days) +
                               " --addresses " + shq(addresses);
            if (!start.empty())
                args += " --start " + shq(start);
            if (!end.empty())
                args += " --end " + shq(end);
            if (force)
                args += " --force";
            out = backendRun(args);
            ::remove(f.c_str());
            ok = out.rfind("on", 0) == 0;
        }
        while (!out.empty() && (out.back() == '\n' || out.back() == ' '))
            out.pop_back();
        if (ok)
        {
            messageBox((on & 1) ? "Out-of-office reply is ON.\n\nThe server answers for you from now on, even with tvmail closed."
                                : "Out-of-office reply is OFF.",
                       mfInformation | mfOKButton);
            return;
        }
        messageBox(out.empty() ? "The server did not accept the change." : out.c_str(),
                   mfError | mfOKButton);
        // and round again, with the entries as typed
    }
}

void TVMailApp::moveMsg(int row)
{
    if (row < 0)
        return;
    int b = gRows[row].idx;

    std::vector<int> dest;
    for (int i = 0; i < gFolderCount; ++i)
        if (i != gFolderIdx && std::strcmp(gFolders[i].mbox, "dead") != 0)
            dest.push_back(i);
    if (dest.empty())
        return;

    TSItem *items = nullptr;
    for (auto it = dest.rbegin(); it != dest.rend(); ++it)
        items = new TSItem(gFolders[*it].name, items);

    int n = (int)dest.size();
    TDialog *d = new TDialog(TRect(0, 0, 30, 5 + n), "Move to folder");
    d->options |= ofCentered;
    TRadioButtons *rb = new TRadioButtons(TRect(3, 2, 27, 2 + n), items);
    d->insert(rb);
    d->insert(new TButton(TRect(4, 3 + n, 14, 5 + n), "O~K~", cmOK, bfDefault));
    d->insert(new TButton(TRect(16, 3 + n, 26, 5 + n), "Cancel", cmCancel, bfNormal));
    d->selectNext(False);

    ushort r = TProgram::deskTop->execView(d);
    ushort sel = 0;
    if (r != cmCancel)
        rb->getData(&sel);
    TObject::destroy(d);
    if (r == cmCancel || sel >= dest.size())
        return;

    std::string cmd = backendCommand("delete " + std::to_string(b) + mboxOpt() + " --trash " + shq(gFolders[dest[sel]].mbox));
    std::string out = shCapture(cmd + " 2>&1");
    reload();
    if (!out.empty() && out.rfind("deleted", 0) != 0)
        messageBox(out.c_str(), mfInformation | mfOKButton);
}

void TVMailApp::purgeDialog()
{
    TDialog *d = new TDialog(TRect(0, 0, 56, 21), "Purge");
    d->options |= ofCentered;

    TInputLine *fromIl = new TInputLine(TRect(20, 2, 53, 3), 200);
    d->insert(fromIl);
    d->insert(new TLabel(TRect(2, 2, 20, 3), "~F~rom contains", fromIl));

    TInputLine *subjIl = new TInputLine(TRect(20, 3, 53, 4), 200);
    d->insert(subjIl);
    d->insert(new TLabel(TRect(2, 3, 20, 4), "~S~ubject contains", subjIl));

    TInputLine *toIl = new TInputLine(TRect(20, 4, 53, 5), 200);
    d->insert(toIl);
    d->insert(new TLabel(TRect(2, 4, 20, 5), "~T~o contains", toIl));

    TInputLine *daysIl = new TInputLine(TRect(20, 5, 30, 6), 8);
    d->insert(daysIl);
    d->insert(new TLabel(TRect(2, 5, 20, 6), "~O~lder than (days)", daysIl));

    d->insert(new TStaticText(TRect(2, 7, 30, 8), "Read status"));
    TRadioButtons *seenRb = new TRadioButtons(TRect(2, 8, 26, 11),
                                              new TSItem("~E~ither",
                                                         new TSItem("Se~e~n only",
                                                                    new TSItem("~U~nseen only", 0))));
    d->insert(seenRb);

    TCheckBoxes *expungeCb = new TCheckBoxes(TRect(2, 12, 53, 13),
                                             new TSItem("E~x~punge (delete outright, don't move to trash)", 0));
    d->insert(expungeCb);

    d->insert(new TButton(TRect(16, 16, 28, 18), "Pre~v~iew", cmOK, bfDefault));
    d->insert(new TButton(TRect(30, 16, 40, 18), "Cancel", cmCancel, bfNormal));
    d->selectNext(False);

    ushort r = TProgram::deskTop->execView(d);
    if (r == cmCancel)
    {
        TObject::destroy(d);
        return;
    }

    std::string from = fromIl->data ? fromIl->data : "";
    std::string subj = subjIl->data ? subjIl->data : "";
    std::string to = toIl->data ? toIl->data : "";
    std::string days = daysIl->data ? daysIl->data : "";
    ushort seenSel = 0;
    seenRb->getData(&seenSel);
    ushort expunge = 0;
    expungeCb->getData(&expunge);
    TObject::destroy(d);

    auto trimmed = [](std::string s)
    {
        size_t a = s.find_first_not_of(" \t");
        size_t b = s.find_last_not_of(" \t");
        return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
    };
    from = trimmed(from);
    subj = trimmed(subj);
    to = trimmed(to);
    days = trimmed(days);

    if (from.empty() && subj.empty() && to.empty() && days.empty() && seenSel == 0)
    {
        messageBox("Enter at least one filter.", mfError | mfOKButton);
        return;
    }

    std::string filt;
    if (!from.empty())
        filt += " --from " + shq(from);
    if (!subj.empty())
        filt += " --subject " + shq(subj);
    if (!to.empty())
        filt += " --to " + shq(to);
    if (!days.empty())
        filt += " --older-than " + shq(days);
    if (seenSel == 1)
        filt += " --seen";
    if (seenSel == 2)
        filt += " --unseen";
    if (expunge & 1)
        filt += " --expunge";

    std::string cmd = backendCommand("purge" + mboxArg() + filt);
    std::string preview = shCapture(cmd + " -n 2>&1");
    TRect pr = deskTop->getExtent();
    pr.grow(-6, -3);
    deskTop->insert(new TMailViewWindow(pr, "Purge preview", splitLines(preview), /*wrap=*/true));

    int n = -1;
    {
        std::istringstream is(preview);
        std::string ln;
        while (std::getline(is, ln))
            if (ln.rfind("would purge ", 0) == 0)
            {
                n = std::atoi(ln.c_str() + 12);
                break;
            }
    }
    if (n <= 0)
    {
        messageBox(n == 0 ? "Nothing matches that filter." : preview.c_str(),
                   (n == 0 ? mfInformation : mfError) | mfOKButton);
        return;
    }
    if (messageBox(mfConfirmation | mfYesNoCancel, "Purge %d message(s)?", n) != cmYes)
        return;
    std::string out = shCapture(cmd + " 2>&1");
    reload();
    if (!out.empty() && out.rfind("purged", 0) != 0)
        messageBox(out.c_str(), mfInformation | mfOKButton);
}

void TVMailApp::emptyTrash()
{
    std::string cmd = backendCommand("empty-trash");
    std::string preview = shCapture(cmd + " -n 2>&1");

    int n = -1;
    {
        std::istringstream is(preview);
        std::string ln;
        while (std::getline(is, ln))
            if (ln.rfind("would purge ", 0) == 0)
            {
                n = std::atoi(ln.c_str() + 12);
                break;
            }
    }
    if (n < 0)
    {
        messageBox(preview.c_str(), mfError | mfOKButton);
        return;
    }
    if (n == 0)
    {
        messageBox("The trash is already empty.", mfInformation | mfOKButton);
        return;
    }
    if (messageBox(mfConfirmation | mfYesNoCancel,
                   "Permanently delete %d message(s) from the trash?\n\nThis cannot be undone.", n) != cmYes)
        return;
    std::string out = shCapture(cmd + " 2>&1");
    reload();
    if (!out.empty() && out.rfind("purged", 0) != 0)
        messageBox(out.c_str(), mfInformation | mfOKButton);
}

void TVMailApp::pullMail()
{
#ifndef _WIN32
    if (pullPid > 0)
        return; // one at a time
    std::string log = tempDir() + "tvmail_pull_" + std::to_string(procId()) + ".log";
    pid_t p = Backend::instance().spawnLogged("pull", log);
    if (p > 0)
    {
        pullPid = p;
        pullLogPath = log;
        TCommandSet cs;
        cs.enableCmd(cmPull);
        disableCommands(cs); // grey "F3 Pull" until it's done
        return;
    }
#endif
    shInteractive(backendCommand("pull")); // fork failed / Windows: old way
    reload();
}

#ifndef _WIN32
void TVMailApp::idle()
{
    TApplication::idle();
    if (pullPid <= 0)
        return;
    int st = 0;
    pid_t r = ::waitpid(pullPid, &st, WNOHANG);
    if (r == 0)
        return; // still pulling
    pullExit = (r == pullPid && WIFEXITED(st)) ? WEXITSTATUS(st) : -1;
    pullPid = -1;
    TEvent ev; // finish outside idle()
    ev.what = evCommand;
    ev.message.command = cmPullDone;
    ev.message.infoPtr = nullptr;
    putEvent(ev);
}
#endif

void TVMailApp::pullFinished()
{
#ifndef _WIN32
    TCommandSet cs;
    cs.enableCmd(cmPull);
    enableCommands(cs);

    std::string out = slurp(pullLogPath);
    if (!pullLogPath.empty())
        ::remove(pullLogPath.c_str());
    pullLogPath.clear();
    reload();

    // the puller's last line is the summary; show the tail so verbose
    // "delivered msg ..." lines are visible too.
    std::vector<std::string> ls = splitLines(out);
    std::string tail;
    for (int i = (int)ls.size() - 1, shown = 0; i >= 0 && shown < 8; --i)
    {
        if (ls[i].empty())
            continue;
        tail = ls[i] + (tail.empty() ? std::string() : "\n" + tail);
        ++shown;
    }
    if (tail.empty())
        tail = "Pull finished (no output).";
    messageBox(tail.c_str(),
               (pullExit >= 2 ? mfError : mfInformation) | mfOKButton);
#endif
}

void TVMailApp::reload()
{
    if (mainWin)
        mainWin->reloadFolder();
}

void TVMailApp::handleEvent(TEvent &e)
{
    TApplication::handleEvent(e);
    if (e.what == evBroadcast && e.message.command == cmTimerExpired &&
        gFlashTimer && e.message.infoPtr == gFlashTimer)
    {
        gFlashTimer = nullptr; // flashStatus() note is up: back to the F-key hints
        if (statusLine)
            statusLine->drawView();
        clearEvent(e);
        return;
    }
    if (e.what != evCommand)
        return;
    bool handled = true;
    switch (e.message.command)
    {
    case cmPull:
        pullMail();
        break;
    case cmPullDone:
        pullFinished();
        break;
    case cmReload:
        reload();
        break;
    case cmReplyMsg:
        replyOrCompose(currentRow());
        break;
    case cmReplyAllMsg:
        replyOrCompose(currentRow(), true);
        break;
    case cmForwardMsg:
        forwardMsg(currentRow());
        break;
    case cmCompose:
        replyOrCompose(-1);
        break;
    case cmDeleteMsg:
        deleteMsg(currentRow());
        break;
    case cmMarkUnread:
        markUnread(currentRow());
        break;
    case cmMarkRead:
        markRead(currentRow());
        break;
    case cmSaveMsg:
        saveMsg(currentRow());
        break;
    case cmWindowList:
        windowList();
        break;
    case cmOutOfOffice:
        outOfOffice();
        break;
    case cmSortDate:
    case cmSortFrom:
    case cmSortSubj:
    case cmSortUnread:
    {
        SortKey k = e.message.command == cmSortDate   ? sortDate
                    : e.message.command == cmSortFrom ? sortFrom
                    : e.message.command == cmSortSubj ? sortSubj
                                                      : sortUnread;
        gSortRev = (k == gSortKey) ? !gSortRev : false; // again = flip
        gSortKey = k;
        if (mainWin)
            mainWin->viewChanged(false);
        break;
    }
    case cmSortReverse:
        gSortRev = !gSortRev;
        if (mainWin)
            mainWin->viewChanged(false);
        break;
    case cmToggleWrap: // no text window has focus: it is the body pane's setting
        gWrap = !gWrap;
        if (mainWin && mainWin->contentPane)
            mainWin->contentPane->setWrap(gWrap);
        break;
    case cmClearFilter:
        gFilter.clear();
        if (mainWin)
            mainWin->viewChanged(true);
        break;
    case cmMoveMsg:
        moveMsg(currentRow());
        break;
    case cmViewParts:
        viewParts(currentRow());
        break;
    case cmViewSrc:
        viewSource(currentRow());
        break;
    case cmEditDraft:
        editDraft(currentRow());
        break;
    case cmResumeDead:
        resumeDeadLetter();
        break;
    case cmPurgeMsgs:
        purgeDialog();
        break;
    case cmEmptyTrash:
        emptyTrash();
        break;
    case cmAddrBook:
    {
        TRect r = deskTop->getExtent();
        r.grow(-8, -4);
        deskTop->insert(new TAddrBookWindow(r));
        break;
    }
    case cmEditSig:
    {
        TRect r = deskTop->getExtent();
        r.grow(-6, -3);
        deskTop->insert(new TSigWindow(r));
        break;
    }
    case cmShowHelp:
    {
        TRect r = deskTop->getExtent();
        r.grow(-4, -2);
        deskTop->insert(new THelpWindow(r));
        break;
    }
    case cmAboutBox:
        messageBox("tvmail " TVMAIL_VERSION "\n\n"
                   "A Turbo Vision mail client for a local mailbox.\n"
                   "Chris Pollitt  -  MIT licence, no warranty.\n\n"
                   "Turbo Vision by magiblot.  Plumbing: Postfix +\n"
                   "tvmail-backend + pop-pull.  See HISTORY.md.",
                   mfInformation | mfOKButton);
        break;
    default:
        handled = false;
    }
    if (handled)
        clearEvent(e);
}

int main(int argc, char **argv)
{
    // tvision's ncurses input waits ESCDELAY ms (default 10) after an ESC before
    // calling it the Escape key.  Over ssh from Windows Terminal, win32-input-mode
    // key records arrive cut in two, the rest 16-33 ms later (termprobe
    // --tvision), so the ESC was taken as Escape and the rest typed as text
    // ("[68;32;100;1;0;1_").  An ESCDELAY set by the user still wins.
    setenv("ESCDELAY", "100", 0);

    CliOptions options;
    if (!parseOptions(argc, argv, options))
        return options.parseError ? 2 : 0;
    startDiagnostics(options);
    debugLog("starting tvmail %s", TVMAIL_VERSION);

    // Dev aid: exercise the persistent-backend pipe without the full TUI.
    //   tvmail --selftest   -> prints the raw `list spool` reply on stdout
    if (options.selftest)
    {
        long long selftestStarted = gTraceLog ? traceMicros() : 0;
        std::string body;
        int st = -1;
        bool ok = Backend::instance().call("list spool", body, st);
        std::fprintf(stderr, "selftest: served=%d status=%d bytes=%zu\n",
                     (int)ok, st, body.size());
        std::fwrite(body.data(), 1, body.size(), stdout);
#ifndef _WIN32
        std::string log = tempDir() + "tvmail_selftest.log";
        pid_t p = Backend::instance().spawnLogged("ping", log);
        int wst = 0;
        if (p > 0)
            while (::waitpid(p, &wst, 0) < 0 && errno == EINTR)
            {
            }
        std::fprintf(stderr, "selftest: spawnLogged pid=%ld exit=%d log=%s",
                     (long)p, (p > 0 && WIFEXITED(wst)) ? WEXITSTATUS(wst) : -1,
                     slurp(log).c_str());
        ::remove(log.c_str());
#endif
        Backend::instance().stop();
        traceSpan("selftest", selftestStarted);
        stopDiagnostics();
        return ok ? 0 : 1;
    }

    gRemote = backendRun("mode").rfind("remote", 0) == 0;
    gHost = splitLines(backendRun("host")).front();

    TVMailApp app;
    long long tuiStarted = gTraceLog ? traceMicros() : 0;
    app.run();
    traceSpan("tui", tuiStarted);
    app.shutDown();
    Backend::instance().stop();
    stopDiagnostics();
    return 0;
}
