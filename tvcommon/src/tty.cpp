// tty.cpp - "mesg n" while the app runs, and the one setting that turns it
// off ([ui] mesg in the app's .conf).
//
// talk(1) rings and wall(1) write straight onto the terminal, over the
// windows, and tvision only repaints the cells it thinks changed, so the
// text stays there (Ctrl-L clears it: TTvApp::redrawScreen).  Clearing the
// terminal's group-write bit - what `mesg n` does - keeps them out; root's
// messages still get through.  The mode goes back on exit, while a helper
// has the terminal (shInteractive), while the app is stopped (SIGTSTP) and
// when a signal ends it.
#include "tvcommon.h"

#ifndef _WIN32
#include <sys/stat.h>

namespace
{
char gTty[256];                    // the app's terminal ("" = not ours to hold)
volatile sig_atomic_t gHeld = 0;   // its group-write bit is cleared by us
volatile mode_t gTtyMode = 0;      // its mode before we cleared it

// Both run from signal handlers too: stat() and chmod() are async-signal-safe.
void take()
{
    struct stat st;
    if (!gTty[0] || gHeld || ::stat(gTty, &st) != 0)
        return;
    gTtyMode = st.st_mode & 07777;
    if ((gTtyMode & S_IWGRP) && ::chmod(gTty, gTtyMode & ~S_IWGRP) == 0)
        gHeld = 1;
}

void giveBack()
{
    if (gHeld)
    {
        ::chmod(gTty, gTtyMode);
        gHeld = 0;
    }
}

// SIGTSTP: tvision's own handler (installed after ours, and chaining to it)
// has already put the terminal back; let the shell have messages too while
// we are stopped.
void onStop(int)
{
    int e = errno;
    bool held = gHeld;
    giveBack();
    ::kill(::getpid(), SIGSTOP);
    if (held)
        take();
    errno = e;
}

// SIGTERM, SIGINT, SIGQUIT, SIGHUP: put the mode back, then die of the signal.
void onEnd(int sig)
{
    giveBack();
    ::signal(sig, SIG_DFL);
    sigset_t s;
    sigemptyset(&s);
    sigaddset(&s, sig);
    ::sigprocmask(SIG_UNBLOCK, &s, nullptr);
    ::raise(sig);
}

void onExit() { giveBack(); }
} // namespace

void mesgHold(bool on)
{
    const char *t = on ? ::ttyname(0) : nullptr;
    if (!t || std::strlen(t) >= sizeof gTty)
        return;
    std::strcpy(gTty, t);
    take();
    debugLog("mesg: %s mode %o, held=%d", gTty, (unsigned)gTtyMode, (int)gHeld);

    // Before the TApplication: tvision's signal handler then chains to these.
    struct sigaction sa = {};
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = SA_RESTART;
    sa.sa_handler = onStop;
    ::sigaction(SIGTSTP, &sa, nullptr);
    sa.sa_handler = onEnd;
    for (int sig : {SIGTERM, SIGINT, SIGQUIT, SIGHUP})
        ::sigaction(sig, &sa, nullptr);
    std::atexit(onExit);
}

void mesgRelease() { giveBack(); }

void mesgRetake() { take(); }

#else // the mingw build: a Windows console takes no talk or wall

void mesgHold(bool) {}
void mesgRelease() {}
void mesgRetake() {}

#endif

// [section] key from an INI file the way configparser reads it (key = value
// or key: value, keys case-blind, # and ; comment lines); "" if it isn't
// there.
std::string confValue(const std::string &path, const char *section, const char *key)
{
    std::string sect, want = lowered(key);
    for (const std::string &raw : splitLines(slurp(path)))
    {
        size_t a = raw.find_first_not_of(" \t\r");
        if (a == std::string::npos || raw[a] == '#' || raw[a] == ';')
            continue;
        std::string l = raw.substr(a);
        while (!l.empty() && (l.back() == ' ' || l.back() == '\t' || l.back() == '\r'))
            l.pop_back();
        if (l[0] == '[')
        {
            size_t b = l.find(']');
            sect = l.substr(1, b == std::string::npos ? std::string::npos : b - 1);
            continue;
        }
        if (sect != section)
            continue;
        size_t eq = l.find_first_of("=:");
        if (eq == std::string::npos)
            continue;
        std::string k = l.substr(0, eq);
        while (!k.empty() && (k.back() == ' ' || k.back() == '\t'))
            k.pop_back();
        if (lowered(k) != want)
            continue;
        size_t v = l.find_first_not_of(" \t", eq + 1);
        return v == std::string::npos ? std::string() : l.substr(v);
    }
    return "";
}

void mesgFromConf(const std::string &confPath)
{
    std::string m = lowered(confValue(confPath, "ui", "mesg"));
    mesgHold(!(m == "y" || m == "yes"));
}
