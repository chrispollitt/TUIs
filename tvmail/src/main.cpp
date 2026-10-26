// main.cpp - command line, --selftest, and the run of the app.
// tvmail - a Turbo Vision front-end for a local mbox + the tvmail-backend helper.
//
// Portable: builds natively on Linux (incl. Raspberry Pi / WSL), macOS and the
// BSDs.  On Cygwin, build.sh first patches tvision for the missing FIONREAD &c;
// `build.sh --mingw` makes a static native .exe for a real Windows console.
//
// Every mail operation is emitted as a tiny POSIX-sh script and run via the
// shell in TV_SH (default /bin/sh; the mingw build points it at a Cygwin
// shell).  The C++ side just orchestrates windows.
//
// Build: ../build.sh

#include "app.h"
#include "coprocess.h"

const char *gAppName = "tvmail";

static const char kUsage[] =
    "Usage: tvmail [--debug [FILE]] [--trace [FILE]] [--help] [--version]\n"
    "       tvmail --selftest\n\n"
    "Options:\n"
    "  --debug [FILE]   write diagnostic logging (default: debug.tvmail.log;\n"
    "                   the backend's: debug.tvmail.backend.log)\n"
    "  --trace [FILE]   write Chrome Trace Event profiling data (default: trace.tvmail.log;\n"
    "                   the backend's cProfile data: trace.tvmail.backend.log)\n"
    "  --help           show this help\n"
    "  --version        show the tvmail version\n";

int main(int argc, char **argv)
{
    setEscDelay();

    CliOptions options;
    if (!parseOptions(argc, argv, options, TVMAIL_VERSION, kUsage))
        return options.parseError ? 2 : 0;
    startDiagnostics(options);
    debugLog("starting tvmail %s", TVMAIL_VERSION);

    // Dev aid: exercise both persistent-backend pipes without the full TUI.
    //   tvmail --selftest   -> prints the raw `list spool` reply on stdout,
    if (options.selftest)
    {
        long long selftestStarted = gTraceLog ? traceMicros() : 0;
        std::string body;
        int st = -1;
        bool ok = mailBackend().call("list spool", body, st);
        std::fprintf(stderr, "selftest: served=%d status=%d bytes=%zu\n",
                     (int)ok, st, body.size());
        std::fwrite(body.data(), 1, body.size(), stdout);
#ifndef _WIN32
        std::string log = tempDir() + "tvmail_selftest.log";
        pid_t p = mailBackend().spawnLogged("ping", log);
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
        mailBackend().stop();

        traceSpan("selftest", selftestStarted);
        stopDiagnostics();
        return ok ? 0 : 1;
    }

    gRemote = backendRun("mode").rfind("remote", 0) == 0;
    gHost = splitLines(backendRun("host")).front();

    const char *conf = getenv("TVMAIL_CONF");
    mesgFromConf(conf && *conf ? conf : homePath(".config/tvmail/tvmail.conf"));
    beepFromConf(conf && *conf ? conf : homePath(".config/tvmail/tvmail.conf"));
    TVMailApp app;
    long long tuiStarted = gTraceLog ? traceMicros() : 0;
    app.run();
    traceSpan("tui", tuiStarted);
    app.shutDown();
    mailBackend().stop();
    stopDiagnostics();
    return 0;
}
