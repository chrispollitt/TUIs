// spool.cpp - toasts shared between tvmail and tvchat.
//
// Both are full-screen apps, so one is usually out of sight while the other runs.
// A note worth a glance in the other app (an error, a new mail, someone inviting
// you to a channel) is a small file in the other app's inbox:
//
//     $TV_SPOOL_DIR, else $XDG_STATE_HOME/tvcommon/spool, else ~/.local/state/tvcommon/spool
//         tvchat/<expires>-<pid>-<n>.note        for tvchat to show
//         tvmail/...                             for tvmail
//
// One line each, TAB-separated: 1 <from> <level> <text>.  <expires> (epoch
// seconds) is in the file name, so old ones can be swept without reading them.
// Written under a temp name and renamed, so a reader never sees half a note.
// The reader (spoolTick, from idle) shows each as a toast "from: text" and
// deletes it; one that waited past its time is dropped unseen.  Nothing needs
// the other app to be running: a note waits for it, until it expires.
//
// What goes out: toasts at or above [ui] share = error (default) | warn | info,
// and what the app marks as worth sharing whatever its level; share = off sends
// nothing.  What was read is never sent on, and the same text is sent once per
// 30 s.
#include "views.h"
#include <dirent.h>
#include <sys/stat.h>
#include <algorithm>
#include <ctime>
#include <map>

namespace
{
std::string gSelf;
std::vector<std::string> gPeers; // who gets what this app sends: one app, or several (tvcal)
bool gOff = false;
NoteLevel gMin = noteError;
bool gReading = false; // showing a note that came in: it is not sent on
std::map<std::string, time_t> gRecent;
std::chrono::steady_clock::time_point gLastTick;
unsigned gCounter = 0;
const size_t kInboxMax = 50;

std::string root()
{
    const char *e = ::getenv("TV_SPOOL_DIR");
    if (e && *e)
        return e;
    const char *x = ::getenv("XDG_STATE_HOME");
    return (x && *x ? std::string(x) : homePath(".local/state")) + "/tvcommon/spool";
}

void mkdirs(const std::string &path)
{
    for (size_t at = 1; at <= path.size(); ++at)
        if (at == path.size() || path[at] == '/')
            ::mkdir(path.substr(0, at).c_str(), 0700);
}

std::vector<std::string> notes(const std::string &dir)
{
    std::vector<std::string> out;
    if (DIR *d = ::opendir(dir.c_str()))
    {
        while (dirent *e = ::readdir(d))
        {
            std::string n = e->d_name;
            if (n.size() > 5 && n.compare(n.size() - 5, 5, ".note") == 0)
                out.push_back(n);
        }
        ::closedir(d);
    }
    std::sort(out.begin(), out.end());
    return out;
}

const char *levelName(NoteLevel l)
{
    return l == noteError ? "error" : l == noteWarn ? "warn" : "info";
}

NoteLevel levelOf(const std::string &s)
{
    return s == "error" ? noteError : s == "warn" ? noteWarn : noteInfo;
}

time_t expiryOf(const std::string &name)
{
    return (time_t)std::atoll(name.c_str());
}
} // namespace

// [ui] share = error | warn | info | off in the app's .conf (default error):
// the lowest level of toast sent to the other app on its own.  `self` is this
// app's name and `peer` the other's.
void spoolFromConf(const std::string &confPath, const char *self, const char *peer)
{
    gSelf = self;
    gPeers.clear();
    for (const char *p = peer; p && *p;) // `peer` may name several apps, comma-separated
    {
        const char *e = strchr(p, ',');
        std::string one = e ? std::string(p, e - p) : std::string(p);
        if (!one.empty())
            gPeers.push_back(one);
        p = e ? e + 1 : nullptr;
    }
    std::string v = lowered(confValue(confPath, "ui", "share"));
    gOff = v == "off" || v == "no" || v == "n" || v == "none" || v == "0";
    gMin = v == "info" || v == "all" ? noteInfo : v == "warn" ? noteWarn : noteError;
}

// Tell the other app: level and text, kept `ttl` seconds for it.  `always`: whatever
// the level, the app means this one to be seen (still not with share = off).
void spoolSend(NoteLevel level, const std::string &text, int ttl, bool always)
{
    if (gPeers.empty() || gOff || gReading || text.empty())
        return;
    if (!always && level < gMin)
        return;
    time_t now = ::time(nullptr);
    auto it = gRecent.find(text);
    if (it != gRecent.end() && now - it->second < 30)
        return;
    if (gRecent.size() > 200)
        gRecent.clear();
    gRecent[text] = now;

    std::string flat = text;
    for (char &c : flat)
        if (c == '\t' || c == '\n' || c == '\r')
            c = ' ';
    for (const std::string &peer : gPeers)
    {
        std::string dir = root() + "/" + peer;
        mkdirs(dir);
        std::vector<std::string> have = notes(dir);
        size_t live = 0;
        for (const std::string &n : have)
            if (expiryOf(n) <= now)
                ::remove((dir + "/" + n).c_str()); // sweep what nobody came for
            else
                ++live;
        if (live >= kInboxMax)
            continue;
        std::string name = std::to_string((long long)now + std::max(ttl, 1)) + "-" + std::to_string((long)::getpid()) +
                           "-" + std::to_string(++gCounter);
        std::string tmp = dir + "/.tmp-" + name, fin = dir + "/" + name + ".note";
        if (FILE *f = ::fopen(tmp.c_str(), "w"))
        {
            ::fprintf(f, "1\t%s\t%s\t%s\n", gSelf.c_str(), levelName(level), flat.c_str());
            ::fclose(f);
            if (::rename(tmp.c_str(), fin.c_str()) != 0)
                ::remove(tmp.c_str());
        }
    }
}

// A one-line request for another app (tvchat, tvcal): written whole under a temp name,
// then renamed, so the other never reads half of it.
//   $TVCHAT_REQUESTS / $TVCAL_REQUESTS, else $XDG_STATE_HOME/<app>/requests,
//   else ~/.local/state/<app>/requests
bool peerRequest(const char *peer, const std::string &line, std::string &err)
{
    std::string up = peer;
    for (char &c : up)
        c = (char)toupper((unsigned char)c);
    std::string dir;
    if (const char *e = ::getenv((up + "_REQUESTS").c_str()))
        dir = e;
    if (dir.empty())
    {
        const char *x = ::getenv("XDG_STATE_HOME");
        dir = (x && *x ? std::string(x) : homePath(".local/state")) + "/" + peer + "/requests";
    }
    mkdirs(dir);
    static unsigned n = 0;
    std::string base = dir + "/" + std::to_string((long)::time(nullptr)) + "-" + std::to_string((long)::getpid()) + "-" +
                       std::to_string(++n);
    bool ok = false;
    if (FILE *f = ::fopen((base + ".tmp").c_str(), "w"))
    {
        ok = ::fprintf(f, "%s\n", line.c_str()) > 0;
        ok = ::fclose(f) == 0 && ok;
    }
    if (!ok)
    {
        err = "cannot write " + base + ".tmp";
        return false;
    }
    if (::rename((base + ".tmp").c_str(), (base + ".req").c_str()) != 0)
    {
        err = "cannot rename the request file in " + dir;
        return false;
    }
    return true;
}

// Called from idle: at most every 2 seconds, show what was left for this app
// (five at a time).
void spoolTick()
{
    if (gSelf.empty())
        return;
    auto now = std::chrono::steady_clock::now();
    if (now - gLastTick < std::chrono::seconds(2))
        return;
    gLastTick = now;
    std::string dir = root() + "/" + gSelf;
    std::vector<std::string> have = notes(dir);
    time_t t = ::time(nullptr);
    int shown = 0;
    for (const std::string &n : have)
    {
        if (shown >= 5)
            break;
        std::string path = dir + "/" + n, body = slurp(path);
        ::remove(path.c_str());
        if (expiryOf(n) <= t)
            continue;
        while (!body.empty() && (body.back() == '\n' || body.back() == '\r'))
            body.pop_back();
        std::vector<std::string> f;
        for (size_t at = 0, tab; at <= body.size(); at = tab + 1)
        {
            tab = f.size() < 3 ? body.find('\t', at) : std::string::npos;
            f.push_back(body.substr(at, tab == std::string::npos ? std::string::npos : tab - at));
            if (tab == std::string::npos)
                break;
        }
        if (f.size() < 4 || f[0] != "1" || f[3].empty())
            continue;
        gReading = true;
        notifyAlert(f[1] + ": " + f[3], levelOf(f[2]));
        gReading = false;
        ++shown;
    }
}
