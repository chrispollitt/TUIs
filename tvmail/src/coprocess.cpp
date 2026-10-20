// coprocess.cpp - see coprocess.h.  Keep one helper alive in "serve" mode.
// Before this, every folder switch spawned three cold Python interpreters
// through the shell (~800 ms on Cygwin, which has no real fork()).  Now the
// mbox/MIME machinery is parsed once and each request is a pipe round-trip.
#include "coprocess.h"

CoProcess &mailBackend()
{
    static CoProcess b("tvmail-backend", "TVMAIL_BACKEND_LOG", true);
    return b;
}

CoProcess &chatBackend()
{
    static CoProcess b("tvchat-backend", "TVCHAT_BACKEND_LOG", false);
    return b;
}

std::string CoProcess::command(const std::string &args) const
{
    return program + (diagFlags ? backendFlags() : std::string()) + " " + args;
}

#ifndef _WIN32
CoProcess::CoProcess(const char *prog, const char *env, bool diag)
    : program(prog), logEnv(env), diagFlags(diag)
{
    std::signal(SIGPIPE, SIG_IGN);
}

// One request.  Returns false if the co-process is unavailable - the
// caller then falls back to a one-shot `tvmail-backend ...` via the shell.
bool CoProcess::call(const std::string &req, std::string &body, int &status)
{
    if (permFail)
        return false;
    if (!up && !start())
        return false;
    if (!writeLine(req) || !readFrame(body, status))
    {
        if (!restart() || !writeLine(req) || !readFrame(body, status))
            return false;
    }
    // a good round trip: the restart budget is for a backend that keeps
    // dying, not for three hiccups spread over a long session
    restarts = 0;
    return true;
}

void CoProcess::stop()
{
    if (wr)
    {
        std::fclose(wr);
        wr = nullptr;
    }
    if (rd)
    {
        std::fclose(rd);
        rd = nullptr;
    }
    if (pid > 0)
    {
        int s;
        while (::waitpid(pid, &s, 0) < 0 && errno == EINTR)
        {
        }
    }
    pid = -1;
    up = false;
}

// Fire-and-forget: run `tvmail-backend <args>` detached, stdin from
// /dev/null and stdout+stderr to logpath.  Returns the pid (the caller
// reaps it) or -1.  Used for the background mail pull, so F3 no longer
// suspends the whole UI.
pid_t CoProcess::spawnLogged(const std::string &args, const std::string &logpath)
{
    pid_t p = ::fork();
    if (p < 0)
        return -1;
    if (p == 0)
    {
        int n = ::open("/dev/null", O_RDONLY);
        if (n >= 0)
        {
            ::dup2(n, 0);
            if (n > 2)
                ::close(n);
        }
        int f = ::open(logpath.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0600);
        if (f >= 0)
        {
            ::dup2(f, 1);
            ::dup2(f, 2);
            if (f > 2)
                ::close(f);
        }
        std::string sc = std::string(kPreamble) + "exec " + command(args) + "\n";
        ::execl(TVMAIL_SH, TVMAIL_SH, "-c", sc.c_str(), (char *)nullptr);
        ::_exit(127);
    }
    return p;
}

bool CoProcess::giveUp()
{
    permFail = true;
    return false;
}

bool CoProcess::start()
{
    int a[2], b[2]; // a: parent->child stdin; b: child stdout->parent
    if (::pipe(a) != 0)
        return giveUp();
    if (::pipe(b) != 0)
    {
        ::close(a[0]);
        ::close(a[1]);
        return giveUp();
    }

    pid = ::fork();
    if (pid < 0)
    {
        ::close(a[0]);
        ::close(a[1]);
        ::close(b[0]);
        ::close(b[1]);
        return giveUp();
    }
    if (pid == 0)
    { // ---- child ----
        ::dup2(a[0], 0);
        ::dup2(b[1], 1);
        const char *log = ::getenv(logEnv);
        int e = ::open((log && *log) ? log : "/dev/null",
                       O_WRONLY | O_CREAT | O_APPEND, 0600);
        if (e >= 0)
        {
            ::dup2(e, 2);
            if (e > 2)
                ::close(e);
        }
        if (a[0] > 2)
            ::close(a[0]);
        if (a[1] > 2)
            ::close(a[1]);
        if (b[0] > 2)
            ::close(b[0]);
        if (b[1] > 2)
            ::close(b[1]);
        std::string sc = std::string(kPreamble) + "exec " + command("serve") + "\n";
        ::execl(TVMAIL_SH, TVMAIL_SH, "-c", sc.c_str(), (char *)nullptr);
        ::_exit(127);
    }
    ::close(a[0]);
    ::close(b[1]); // ---- parent ----
    wr = ::fdopen(a[1], "w");
    rd = ::fdopen(b[0], "r");
    if (!wr || !rd)
    {
        stop();
        return giveUp();
    }
    // keep the pipe out of every later fork/exec (shell calls, the
    // background pull) so closing wr on shutdown really reaches the child
    ::fcntl(::fileno(wr), F_SETFD, FD_CLOEXEC);
    ::fcntl(::fileno(rd), F_SETFD, FD_CLOEXEC);

    std::string banner;
    int st = -1; // expect "0 5\nready"
    if (!readFrame(banner, st) || st != 0 || banner != "ready")
    {
        stop();
        return giveUp();
    }
    up = true;
    ++starts;
    return true;
}

bool CoProcess::restart()
{
    stop();
    if (permFail || ++restarts > 3)
        return giveUp();
    return start();
}

// A signal (SIGWINCH from a terminal resize, SIGCHLD from the background
// pull...) can land in the middle of any of these calls.  Unless its handler
// was installed with SA_RESTART, the call then fails with EINTR - which is not
// the co-process dying, so every step below retries it.  (Treating it as a
// death threw a healthy backend away, and during start-up - a resize while
// tvmail opens - gave the co-process up for the whole session.)
static bool interrupted(FILE *f)
{
    if (errno != EINTR || !std::ferror(f))
        return false;
    std::clearerr(f);
    return true;
}

bool CoProcess::writeLine(const std::string &s0)
{
    if (!wr)
        return false;
    std::string s = s0 + "\n";
    size_t done = 0;
    while (done < s.size())
    {
        errno = 0;
        size_t w = std::fwrite(s.data() + done, 1, s.size() - done, wr);
        done += w;
        if (done < s.size() && !interrupted(wr))
            return false;
    }
    for (;;)
    {
        errno = 0;
        if (std::fflush(wr) == 0)
            return true;
        if (!interrupted(wr))
            return false;
    }
}

// "<status> <nbytes>\n" then exactly <nbytes> bytes.  poll() before the
// first byte bounds the wait so a wedged backend can't freeze the UI;
// once bytes are flowing the reply is one write on the far side, so
// blocking reads for the remainder can't deadlock.
bool CoProcess::readFrame(std::string &body, int &status)
{
    body.clear();
    struct pollfd pfd;
    pfd.fd = ::fileno(rd);
    pfd.events = POLLIN;
    auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(8000);
    for (;;)
    {
        long left = (long)std::chrono::duration_cast<std::chrono::milliseconds>(
                        deadline - std::chrono::steady_clock::now())
                        .count();
        if (left <= 0)
            return false;
        pfd.revents = 0;
        int r = ::poll(&pfd, 1, (int)left);
        if (r > 0)
            break;
        if (r == 0 || errno != EINTR)
            return false;
    }

    std::string hdr;
    int c;
    for (;;)
    {
        errno = 0;
        c = std::fgetc(rd);
        if (c == EOF && interrupted(rd))
            continue;
        if (c == EOF || c == '\n')
            break;
        hdr += char(c);
        if (hdr.size() > 64)
            return false;
    }
    if (c == EOF)
        return false;

    long n = -1;
    int st = 0;
    if (std::sscanf(hdr.c_str(), "%d %ld", &st, &n) != 2 || n < 0)
        return false;
    status = st;
    body.resize((size_t)n);
    size_t got = 0;
    while (got < (size_t)n)
    {
        errno = 0;
        size_t r = std::fread(&body[got], 1, (size_t)n - got, rd);
        got += r;
        if (r == 0 && !interrupted(rd))
            return false;
    }
    return true;
}

#else // _WIN32: no fork(); every call uses the one-shot shell path.
CoProcess::CoProcess(const char *prog, const char *env, bool diag)
    : program(prog), logEnv(env), diagFlags(diag) {}
bool CoProcess::call(const std::string &, std::string &, int &) { return false; }
void CoProcess::stop() {}
#endif
