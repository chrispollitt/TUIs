// main.cpp - command line, --selftest, and the run of the app.
// tvchat - a Turbo Vision chat client: IRC (with IRCv3) through the
// tvchat-backend helper, Delta Chat.
//
// POSIX only (Linux incl. Raspberry Pi / WSL, macOS, the BSDs, Cygwin): the
// backend is a co-process, and a native Windows build has no fork().
//
// Build: ../build.sh

#include "app.h"
#include "coprocess.h"

const char *gAppName = "tvchat";

static const char kUsage[] =
    "Usage: tvchat [--debug [FILE]] [--trace [FILE]] [--help] [--version]\n"
    "       tvchat --selftest\n\n"
    "Options:\n"
    "  --debug [FILE]   write diagnostic logging (default: debug.tvchat.log;\n"
    "                   the backend's: debug.tvchat.backend.log)\n"
    "  --trace [FILE]   write Chrome Trace Event profiling data (default: trace.tvchat.log;\n"
    "                   the backend's cProfile data: trace.tvchat.backend.log)\n"
    "  --help           show this help\n"
    "  --version        show the tvchat version\n"
    "\n"
    "Accounts: ~/.config/tvchat/tvchat.conf ($TVCHAT_CONF overrides it).\n";

int main(int argc, char **argv)
{
    setEscDelay();

    CliOptions options;
    if (!parseOptions(argc, argv, options, TVCHAT_VERSION, kUsage))
        return options.parseError ? 2 : 0;
    startDiagnostics(options);
    debugLog("starting tvchat %s", TVCHAT_VERSION);

    // Dev aid: the chat co-process as the app drives it, without the TUI -
    // start + read the accounts, one event poll, a ping.
    if (options.selftest)
    {
        long long selftestStarted = gTraceLog ? traceMicros() : 0;
        bool ok = ChatClient::instance().start();
        bool reset = false;
        size_t nev = ChatClient::instance().poll(reset).size();
        std::string body;
        int st = -1;
        ok = ok && !reset && ChatClient::instance().request("ping", body, st) &&
             body == "pong\n";
        std::fprintf(stderr, "selftest: chat served=%d status=%d accounts=%zu events=%zu\n",
                     (int)ok, st, ChatClient::instance().accounts().size(), nev);
        ChatClient::instance().stop();
        traceSpan("selftest", selftestStarted);
        stopDiagnostics();
        return ok ? 0 : 1;
    }

    mesgFromConf(ChatClient::confPath());
    TVChatApp app;
    long long tuiStarted = gTraceLog ? traceMicros() : 0;
    app.run();
    traceSpan("tui", tuiStarted);
    app.shutDown();
    ChatClient::instance().stop();
    stopDiagnostics();
    return 0;
}
