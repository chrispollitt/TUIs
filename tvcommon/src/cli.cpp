// cli.cpp - the command line both apps take, and the ESCDELAY setting.
#include "tvcommon.h"

bool parseOptions(int argc, char **argv, CliOptions &options,
                  const char *version, const char *usage)
{
    for (int i = 1; i < argc; ++i)
    {
        std::string arg(argv[i]);
        if (arg == "--help")
        {
            std::fputs(usage, stdout);
            return false;
        }
        if (arg == "--version")
        {
            std::printf("%s %s\n", gAppName, version);
            return false;
        }
        if (arg == "--selftest")
        {
            options.selftest = true;
            continue;
        }
        std::string *path = nullptr;
        std::string name;
        if (arg == "--debug")
        {
            path = &options.debugPath;
            name = std::string("debug.") + gAppName + ".log";
        }
        else if (arg == "--trace")
        {
            path = &options.tracePath;
            name = std::string("trace.") + gAppName + ".log";
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
            std::fprintf(stderr, "%s: unknown option: %s\n", gAppName, arg.c_str());
            std::fputs(usage, stderr);
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

// tvision's ncurses input waits ESCDELAY ms (default 10) after an ESC before
// calling it the Escape key.  Over ssh from Windows Terminal, win32-input-mode
// key records arrive cut in two, the rest 16-33 ms later (termprobe
// --tvision), so the ESC was taken as Escape and the rest typed as text
// ("[68;32;100;1;0;1_").  An ESCDELAY set by the user still wins.
// (Not on the mingw build: no setenv(), and no ncurses - it reads the
// Win32 console.)
void setEscDelay()
{
#ifndef _WIN32
    setenv("ESCDELAY", "100", 0);
#endif
}
