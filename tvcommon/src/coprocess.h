// coprocess.h - a helper program kept alive in "serve" mode, spoken to over
// a pair of pipes: tvmail-backend for tvmail (its mailBackend()),
// tvchat-backend for tvchat (its chatBackend()).
//
// Wire protocol - a request line, then a framed reply:
//     ->  show 3 spool\n
//     <-  <status> <nbytes>\n   followed by exactly <nbytes> bytes
// The child greets with a "0 5\nready" frame.  For mail, the read verbs
// (list/show/raw/mark/aliases/compose-template) go through here; the
// stdin-consuming / interactive ones (send/save-draft/pull) keep the
// one-shot shell path.  The --mingw Windows build has no fork(): call()
// always returns false there, mail uses the shell path for everything and
// chat is off.
#pragma once
#include "tvcommon.h"

class CoProcess
{
public:
    // program: run as `<program>[ diagnostics flags] serve` through the
    // shell preamble's PATH.  logEnv: an environment variable that, if set,
    // names a file for the child's stderr (else /dev/null).  diagTag: pass
    // the app's --debug / --trace on, as FILE.<diagTag>.ext (nullptr: don't).
    CoProcess(const char *program, const char *logEnv, const char *diagTag);
    ~CoProcess() { stop(); }
    CoProcess(const CoProcess &) = delete;
    CoProcess &operator=(const CoProcess &) = delete;

    // One request.  Returns false if the co-process is unavailable (a dead
    // child is restarted, at most three times in a row, first).
    bool call(const std::string &req, std::string &body, int &status);
    void stop();
    bool running() const { return up; }
    // bumped by every successful start, so a caller holding state from the
    // child (chat's event sequence numbers) can tell it was restarted
    unsigned generation() const { return starts; }

#ifndef _WIN32
    // Fire-and-forget: run `<program> <args>` detached, stdin from /dev/null
    // and stdout+stderr to logpath.  Returns the pid (the caller reaps it) or
    // -1.  Used for the background mail pull, so F3 doesn't suspend the UI.
    pid_t spawnLogged(const std::string &args, const std::string &logpath);
#endif

private:
    std::string program;
    const char *logEnv;
    const char *diagTag;
    FILE *wr = nullptr, *rd = nullptr;
#ifndef _WIN32
    pid_t pid = -1;
#endif
    bool up = false;
    bool permFail = false;
    int restarts = 0;
    unsigned starts = 0;

    std::string command(const std::string &args) const;
    bool giveUp();
    bool start();
    bool restart();
    bool writeLine(const std::string &s);
    bool readFrame(std::string &body, int &status);
};

