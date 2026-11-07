// main.cpp - command line, --selftest, and the run of the app.
// tvcal - a Turbo Vision calendar and to-do manager that works with tvmail and tvchat.
//
// POSIX only (Linux incl. Raspberry Pi / WSL, macOS, the BSDs, Cygwin): the backend is
// a co-process (fork()).  On Windows use Cygwin.
//
// Build: ../build.sh

#include "app.h"
#include "coprocess.h"

const char *gAppName = "tvcal";

static const char kUsage[] =
    "Usage: tvcal [--debug [FILE]] [--trace [FILE]] [--help] [--version]\n"
    "       tvcal --selftest\n\n"
    "Options:\n"
    "  --debug [FILE]   write diagnostic logging (default: debug.tvcal.log;\n"
    "                   the backend's: debug.tvcal.backend.log)\n"
    "  --trace [FILE]   write Chrome Trace Event profiling data (default: trace.tvcal.log;\n"
    "                   the backend's cProfile data: trace.tvcal.backend.log)\n"
    "  --help           show this help\n"
    "  --version        show the tvcal version\n"
    "\n"
    "The calendar lives on the CalDAV server named in ~/.config/tvcal/tvcal.conf ([server] url);\n"
    "without one, in ~/.local/share/tvcal/calendar.ics ($TVCAL_DATA overrides it).\n"
    "$TVCAL_CONF overrides the configuration file.\n";

int main(int argc, char **argv)
{
    setEscDelay();

    CliOptions options;
    if (!parseOptions(argc, argv, options, TVCAL_VERSION, kUsage))
        return options.parseError ? 2 : 0;
    startDiagnostics(options);
    debugLog("starting tvcal %s", TVCAL_VERSION);

    // Dev aid: the co-process as the app drives it, without the TUI - start it, a ping, and the
    // to-do list and a month asked for.
    if (options.selftest)
    {
        long long selftestStarted = gTraceLog ? traceMicros() : 0;
        std::string err, todos, month;
        bool ok = calBackendUp();
        ok = ok && calAsk("todos", todos, err);
        ok = ok && calAsk("range 2000-01-01 2000-01-31", month, err);
        if (!ok && !err.empty())
            std::fprintf(stderr, "selftest: %s\n", err.c_str());
        std::fprintf(stderr, "selftest: cal served=%d todos=%zu events=%zu\n", (int)ok, splitLines(todos).size(),
                     splitLines(month).size());
        calBackendStop();
        traceSpan("selftest", selftestStarted);
        stopDiagnostics();
        return ok ? 0 : 1;
    }

    if (!helperVersionGuard("tvcal-backend", TVCAL_VERSION))
        return 1;
    mesgFromConf(calConfPath());
    beepFromConf(calConfPath());
    // reminders go to both: whichever of them you are looking at shows them
    spoolFromConf(calConfPath(), "tvcal", "tvmail,tvchat");
    TVCalApp app;
    long long tuiStarted = gTraceLog ? traceMicros() : 0;
    app.run();
    traceSpan("tui", tuiStarted);
    app.shutDown();
    calBackendStop();
    stopDiagnostics();
    return 0;
}
