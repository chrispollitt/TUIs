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
#include "chat.h"

const char *gAppName = "tvmail";

static const char kUsage[] =
    "Usage: tvmail [--debug [FILE]] [--trace [FILE]] [--help] [--version]\n"
    "       tvmail --selftest\n\n"
    "Options:\n"
    "  --debug [FILE]   write diagnostic logging (default: debug.log;\n"
    "                   mail backend: *.backend.log, chat backend: *.chat.log)\n"
    "  --trace [FILE]   write Chrome Trace Event profiling data (default: trace.log;\n"
    "                   the backends' cProfile data: *.backend.log, *.chat.log)\n"
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
    //                          then checks tvchat-backend the same way
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

        // the chat co-process, as the app drives it: start + read the
        // accounts, then one event poll
        bool chatOk = ChatClient::instance().start();
        bool reset = false;
        size_t nev = ChatClient::instance().poll(reset).size();
        int cst = -1;
        chatOk = chatOk && !reset && ChatClient::instance().request("ping", body, cst) &&
                 body == "pong\n";
        std::fprintf(stderr, "\nselftest: chat served=%d status=%d accounts=%zu events=%zu\n",
                     (int)chatOk, cst, ChatClient::instance().accounts().size(), nev);
        ChatClient::instance().stop();
        traceSpan("selftest", selftestStarted);
        stopDiagnostics();
        return ok && chatOk ? 0 : 1;
    }

    gRemote = backendRun("mode").rfind("remote", 0) == 0;
    gHost = splitLines(backendRun("host")).front();

    TVMailApp app;
    long long tuiStarted = gTraceLog ? traceMicros() : 0;
    app.run();
    traceSpan("tui", tuiStarted);
    app.shutDown();
    ChatClient::instance().stop();
    mailBackend().stop();
    stopDiagnostics();
    return 0;
}
