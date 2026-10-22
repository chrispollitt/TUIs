// backend.cpp - tvmail's side of tvmail-backend: the warm co-process, the
// one-shot command line for what can't go through it, and the files mail(1)
// keeps in $HOME (~/.signature, ~/dead.letter).
#include "tvmail.h"
#include "coprocess.h"

CoProcess &mailBackend()
{
    static CoProcess b("tvmail-backend", "TVMAIL_BACKEND_LOG", "backend");
    return b;
}

CoProcess &chatBackend()
{
    static CoProcess b("tvchat-backend", "TVCHAT_BACKEND_LOG", "chat");
    return b;
}

std::string backendCommand(const std::string &args)
{
    return "tvmail-backend" + backendFlags() + " " + args;
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

std::string readSigFile()
{
    std::string s = slurp(homePath(".signature"));
    while (!s.empty() && (s.back() == '\n' || s.back() == '\r'))
        s.pop_back();
    return s;
}

std::string readDeadLetter()
{
    const char *d = getenv("DEAD");
    return slurp(d && *d ? std::string(d) : homePath("dead.letter"));
}
