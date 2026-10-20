// shell.cpp - diagnostics (--debug / --trace) and the POSIX-sh plumbing that
// runs tvmail-backend: one small script per call, and backendRun() which tries
// the warm co-process first.
#include "tvmail.h"
#include "coprocess.h"

// -------------------------------------------------------- shell plumbing ----
// Everything the backend needs runs inside one small POSIX-sh script (one
// script per call keeps us clear of cmd.exe quoting on the mingw build).  The
// preamble widens PATH so tvmail-backend is found wherever it was installed.
const char *const kPreamble =
    "export PATH=\"$HOME/bin:$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin"
    ":/opt/local/bin:/usr/pkg/bin:/usr/bin:/bin:$PATH\"\n";

FILE *gDebugLog = nullptr;
FILE *gTraceLog = nullptr;
static bool gTraceFirst = true;
static std::chrono::steady_clock::time_point gTraceStart;
static std::string gBackendDebugPath;
static std::string gBackendTracePath;

void debugLog(const char *fmt, ...)
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

long long traceMicros()
{
    return std::chrono::duration_cast<std::chrono::microseconds>(
               std::chrono::steady_clock::now() - gTraceStart)
        .count();
}

void traceSpan(const char *name, long long start)
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

void startDiagnostics(const CliOptions &options)
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

void stopDiagnostics()
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

std::string backendFlags()
{
    std::string flags;
    if (!gBackendDebugPath.empty())
        flags += " --debug " + shq(gBackendDebugPath);
    if (!gBackendTracePath.empty())
        flags += " --trace " + shq(gBackendTracePath);
    return flags;
}

std::string backendCommand(const std::string &args)
{
    return "tvmail-backend" + backendFlags() + " " + args;
}

std::string tempDir()
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

long procId()
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
std::string writeTemp(const std::string &content, const char *suffix)
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

std::string shCapture(const std::string &body)
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

int shInteractive(const std::string &body)
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
    TColorAttr c = vga(0x1F); // white on blue - the desktop's own accent
    TDrawBuffer b;
    short w = TProgram::statusLine->size.x; // the whole line, so no half-
    b.moveChar(0, ' ', c, w);               // covered F-key hints show past it
    b.moveStr(0, " Working... ", c);
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
std::string backendRun(const std::string &args)
{
    BusyGuard busy;
    std::string body;
    int st = 0;
    if (mailBackend().call(args, body, st))
        return body;
    return shCapture("tvmail-backend" + backendFlags() + " " + args + " 2>&1");
}
