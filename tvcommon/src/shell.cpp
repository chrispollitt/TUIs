// shell.cpp - diagnostics (--debug / --trace) and the POSIX-sh plumbing that
// runs an app's helper (tvmail-backend, tvchat-backend): one small script per
// call.
#include "tvcommon.h"

// -------------------------------------------------------- shell plumbing ----
// Everything a helper needs runs inside one small POSIX-sh script (one
// script per call keeps us clear of cmd.exe quoting on tvmail's mingw build).
// The preamble widens PATH so the helper is found wherever it was installed.
const char *const kPreamble =
    "export PATH=\"$HOME/bin:$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin"
    ":/opt/local/bin:/usr/pkg/bin:/usr/bin:/bin:$PATH\"\n";

FILE *gDebugLog = nullptr;
FILE *gTraceLog = nullptr;
static bool gTraceFirst = true;
static std::chrono::steady_clock::time_point gTraceStart;
static std::string gDebugPath; // the app's own --debug / --trace files: each
static std::string gTracePath; // helper's go beside them (backendFlags)

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
                 "  {\"name\":\"%s\",\"cat\":\"%s\",\"ph\":\"X\","
                 "\"ts\":%lld,\"dur\":%lld,\"pid\":1,\"tid\":1}",
                 name, gAppName, start, end - start);
    std::fflush(gTraceLog);
}

void startDiagnostics(const CliOptions &options)
{
    if (!options.debugPath.empty())
    {
        gDebugPath = options.debugPath;
        gDebugLog = std::fopen(options.debugPath.c_str(), "ab");
        if (!gDebugLog)
            std::fprintf(stderr, "%s: cannot open debug log %s\n", gAppName,
                         options.debugPath.c_str());
    }
    if (!options.tracePath.empty())
    {
        gTracePath = options.tracePath;
        gTraceLog = std::fopen(options.tracePath.c_str(), "wb");
        if (!gTraceLog)
        {
            std::fprintf(stderr, "%s: cannot open trace log %s\n", gAppName,
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

// A helper's log beside the app's: insert .TAG before the extension
// (debug.log -> debug.backend.log, debug.chat.log), or append it if there's
// none.  trace.sh knows the same rule.
static std::string helperPath(const std::string &path, const char *tag)
{
    size_t slash = path.find_last_of("/\\");
    size_t dot = path.find_last_of('.');
    std::string t = std::string(".") + tag;
    if (dot == std::string::npos || (slash != std::string::npos && dot < slash + 1))
        return path + t;
    return path.substr(0, dot) + t + path.substr(dot);
}

std::string backendFlags(const char *tag)
{
    std::string flags;
    if (!gDebugPath.empty())
        flags += " --debug " + shq(helperPath(gDebugPath, tag));
    if (!gTracePath.empty())
        flags += " --trace " + shq(helperPath(gTracePath, tag));
    return flags;
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
    std::string path = tempDir() + gAppName + "_" + std::to_string(procId()) + "_" + std::to_string(++seq) + ".sh";
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
    std::string path = tempDir() + gAppName + "_msg_" + std::to_string(procId()) + "_" + std::to_string(++seq) + suffix;
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
    return q(TV_SH) + " " + q(script);
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
    std::fprintf(stdout, "\n[%s] done - press Enter to return ", gAppName);
    std::fflush(stdout);
    for (int c; (c = std::getchar()) != '\n' && c != EOF;)
    {
    }
    TProgram::application->resume();
    TProgram::application->redraw();
    remove(sp.c_str());
    return rc;
}
