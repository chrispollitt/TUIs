// chat.cpp - see chat.h.
#include "chat.h"
#include "coprocess.h"

CoProcess &chatBackend()
{
    static CoProcess b("tvchat-backend", "TVCHAT_BACKEND_LOG", "chat");
    return b;
}

std::vector<std::string> chatFields(const std::string &line)
{
    std::vector<std::string> out(1);
    for (size_t i = 0; i < line.size(); ++i)
    {
        char c = line[i];
        if (c == '\t')
            out.emplace_back();
        else if (c == '\\' && i + 1 < line.size())
        {
            char n = line[++i];
            out.back() += n == 't' ? '\t' : n == 'n' ? '\n' : n == 'r' ? '\r' : n;
        }
        else
            out.back() += c;
    }
    return out;
}

ChatClient &ChatClient::instance()
{
    static ChatClient c;
    return c;
}

bool ChatClient::configured()
{
    const char *env = getenv("TVCHAT_CONF");
    if (env && *env)
        return ::access(env, R_OK) == 0;
    return ::access(homePath(".config/tvchat/tvchat.conf").c_str(), R_OK) == 0 ||
           ::access(homePath(".config/tvmail/tvchat.conf").c_str(), R_OK) == 0;
}

bool ChatClient::request(const std::string &req, std::string &body, int &status)
{
    long long started = (gTraceLog || gDebugLog) ? traceMicros() : 0;
    bool ok = chatBackend().call(req, body, status);
    traceSpan("chat", started);
    if (!ok)
        debugLog("chat: no reply to: %s", req.c_str());
    // the 250 ms `events` poll only when it brought something
    else if (gDebugLog && (req.compare(0, 6, "events") != 0 || !body.empty()))
        debugLog("chat: %s -> %d, %lu bytes, %.1f ms%s%s", req.substr(0, 200).c_str(),
                 status, (unsigned long)body.size(), (traceMicros() - started) / 1000.0,
                 status ? ": " : "", status ? body.substr(0, 200).c_str() : "");
    return ok;
}

bool ChatClient::start()
{
    if (isStarted)
        return true;
    isStarted = sync();
    debugLog("chat: %s", isStarted ? "tvchat-backend up" : "tvchat-backend unavailable");
    return isStarted;
}

void ChatClient::stop()
{
    chatBackend().stop();
    isStarted = false;
}

// Read the accounts and conversations afresh.  The queue is drained FIRST:
// what it held is already reflected in the snapshot read after it, and an
// event that lands between the two is newer than lastSeq, so the next poll
// applies it (applying one twice is harmless - each sets, never adds).
bool ChatClient::sync()
{
    std::string body;
    int st = -1;
    lastSeq = -1;
    if (!request("events", body, st))
        return false;
    if (st == 0)
        for (const std::string &ln : splitLines(body))
            if (!ln.empty())
                lastSeq = std::max(lastSeq, std::atoll(ln.c_str()));
    if (!request("accounts", body, st))
        return false;
    accts.clear();
    convMap.clear();
    if (st == 0)
        for (const std::string &ln : splitLines(body))
        {
            std::vector<std::string> f = chatFields(ln);
            if (f.size() >= 4)
                accts.push_back({f[0], f[1], f[2], f[3], ""});
        }
    else
        debugLog("chat: accounts: %s", body.c_str());
    if (request("convs", body, st) && st == 0)
        for (const std::string &ln : splitLines(body))
        {
            std::vector<std::string> f = chatFields(ln);
            if (f.size() >= 4)
                convMap[f[0]] = {f[0], f[1], f[3], std::atoi(f[2].c_str()), ""};
        }
    // the generation is read last: a restart during the calls above shows up
    // as a mismatch on the next poll and the model is read again
    gen = chatBackend().generation();
    return true;
}

int ChatClient::pollIntervalMs() const
{
    if (!isStarted)
        return 0;
    for (const ChatAccount &a : accts)
        if (a.state == "connecting" || a.state == "connected")
            return 250;
    return 2000; // quiet: still notice an account a window connects later
}

std::vector<ChatEvent> ChatClient::poll(bool &reset)
{
    reset = false;
    std::vector<ChatEvent> out;
    if (!isStarted)
        return out;
    std::string body;
    int st = -1;
    std::string req = lastSeq < 0 ? "events" : "events --after " + std::to_string(lastSeq);
    bool ok = request(req, body, st);
    if (chatBackend().generation() != gen)
    {
        // the child died and call() started a new one: whatever this reply
        // holds is that one's, and the model may be stale - read it again
        reset = true;
        debugLog("chat: tvchat-backend restarted - reading accounts and conversations again");
        if (!sync())
            isStarted = false;
        return out;
    }
    if (!ok)
    {
        isStarted = false; // restart budget spent: chat stays off
        reset = true;
        return out;
    }
    if (st != 0)
        return out;
    for (const std::string &ln : splitLines(body))
    {
        if (ln.empty())
            continue;
        std::vector<std::string> f = chatFields(ln);
        if (f.size() < 2)
            continue;
        ChatEvent ev;
        ev.seq = std::atoll(f[0].c_str());
        if (ev.seq <= lastSeq)
            continue; // never apply one twice
        lastSeq = ev.seq;
        ev.fields.assign(f.begin() + 1, f.end());
        apply(ev);
        out.push_back(std::move(ev));
    }
    return out;
}

void ChatClient::apply(const ChatEvent &ev)
{
    const std::vector<std::string> &f = ev.fields;
    const std::string &t = f[0];
    if (gDebugLog)
    {
        std::string all;
        for (size_t i = 1; i < f.size(); ++i)
            all += (i > 1 ? " | " : "") + f[i];
        debugLog("chat event %lld: %s %s", ev.seq, t.c_str(), all.substr(0, 300).c_str());
    }
    if (t == "state" && f.size() >= 3)
    {
        for (ChatAccount &a : accts)
            if (a.id == f[1])
            {
                a.state = f[2];
                a.detail = f.size() > 3 ? f[3] : "";
            }
    }
    else if (t == "conv-new" && f.size() >= 5)
        convMap[f[1]] = {f[1], f[2], f[4], std::atoi(f[3].c_str()), ""};
    else if (t == "conv-gone" && f.size() >= 2)
        convMap.erase(f[1]);
    else if (t == "topic" && f.size() >= 3)
    {
        auto it = convMap.find(f[1]);
        if (it != convMap.end())
            it->second.topic = f[2];
    }
    else if (t == "unread" && f.size() >= 3)
    {
        auto it = convMap.find(f[1]);
        if (it != convMap.end())
            it->second.unread = std::atoi(f[2].c_str());
    }
}

int ChatClient::unreadTotal() const
{
    int n = 0;
    for (const auto &kv : convMap)
        n += kv.second.unread;
    return n;
}
