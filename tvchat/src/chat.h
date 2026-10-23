// chat.h - tvchat's side of tvchat-backend: the co-process, polled for
// events while tvchat idles.  This keeps a copy of the parts of the chat
// model that more than one window needs (accounts, conversations, unread
// counts); everything protocol-specific stays in the backend.
#pragma once
#include "tvchat.h"
#include <map>

struct ChatAccount
{
    std::string id, protocol, state, display, detail;
    std::string view; // talk accounts: how talk conversations start - classic or irc
};

struct ChatConv
{
    std::string id, kind, title; // kind: server, channel, direct, group
    int unread = 0;
    std::string topic;  // from `topic` events only: `convs` does not carry it
    std::string state;  // a talk call: calling, ringing, incoming, connected, ended
    std::string typing; // a talk call: the other side's unfinished line
};

// One event from `events`: its sequence number and its fields, unescaped.
// fields[0] is the type (state, msg, conv-new, conv-gone, members, topic,
// unread, talk, typing); the rest are as documented in tvchat-backend.
struct ChatEvent
{
    long long seq = 0;
    std::vector<std::string> fields;
};

class ChatClient
{
public:
    static ChatClient &instance();

    // A chat config exists ($TVCHAT_CONF, else ~/.config/tvchat/tvchat.conf,
    // else the ~/.config/tvmail/tvchat.conf of when chat was part of tvmail
    // - the backend's own order): only then is the co-process started.
    static bool configured();
    static std::string confPath(); // that file (the first, if none is there)

    // Start tvchat-backend and read the accounts and conversations.
    bool start();
    bool started() const { return isStarted; }
    void stop();

    // One request; false if the co-process is gone (status/body as framed).
    bool request(const std::string &req, std::string &body, int &status);

    // How long to wait between `events` polls: 250 ms while an account is
    // connecting or connected, slower while nothing is live, 0 = don't poll.
    int pollIntervalMs() const;

    // Fetch what queued up since the last call (`events --after SEQ`), apply
    // it to the accounts / conversations below and return it, oldest first,
    // for the windows.  A restarted backend is noticed here: the model is
    // re-read and `reset` comes back true.
    std::vector<ChatEvent> poll(bool &reset);

    const std::vector<ChatAccount> &accounts() const { return accts; }
    const std::map<std::string, ChatConv> &convs() const { return convMap; }
    int unreadTotal() const;

private:
    bool isStarted = false;
    unsigned gen = 0;      // chatBackend().generation() the model was read from
    long long lastSeq = -1;
    std::vector<ChatAccount> accts;
    std::map<std::string, ChatConv> convMap;

    bool sync();           // accounts + convs, from scratch
    void apply(const ChatEvent &ev);
};

// A TAB-separated reply line split into fields, each unescaped (\t \n \r \\).
std::vector<std::string> chatFields(const std::string &line);
