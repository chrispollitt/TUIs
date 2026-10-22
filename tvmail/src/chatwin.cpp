// chatwin.cpp - the chat UI: the Chat menu and the 3-pane chat window
// (servers | conversations / the chat).  Everything here talks to tvchat-backend
// through ChatClient (chat.h) and hears about changes from the cmChatEvent /
// cmChatReset broadcasts the app sends after each poll; nothing here knows
// which protocol a conversation speaks.
#include "tvmail.h"
#include "views.h"
#include "chat.h"
#include <ctime>
#include <functional>
#include <map>
#include <set>
#include <tuple>

namespace
{
// ------------------------------------------------------------- helpers ---
ChatClient &chat() { return ChatClient::instance(); }

std::string trimmed(std::string s)
{
    size_t a = s.find_first_not_of(" \t\r\n");
    size_t b = s.find_last_not_of(" \t\r\n");
    return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
}

std::string accountIdOf(const std::string &conv)
{
    return conv.substr(0, conv.find('/'));
}

const ChatAccount *findAccount(const std::string &id)
{
    for (const ChatAccount &a : chat().accounts())
        if (a.id == id)
            return &a;
    return nullptr;
}

bool isLive(const ChatAccount &a)
{
    return a.state == "connecting" || a.state == "connected";
}

// A menu item name is not literal text: '~' marks the hotkey letter.
std::string menuSafe(std::string s)
{
    s.erase(std::remove(s.begin(), s.end(), '~'), s.end());
    return s;
}

// What a conversation is called: "#channel — Libera", a contact's name for
// a query, "Libera (server)" for the network's own window.  Empty when the
// model no longer has it (left, or the backend lost it).
std::string convName(const std::string &conv)
{
    const auto &cs = chat().convs();
    auto it = cs.find(conv);
    if (it == cs.end())
        return std::string();
    const ChatAccount *a = findAccount(accountIdOf(conv));
    std::string acct = a ? a->display : accountIdOf(conv);
    if (it->second.kind == "server")
        return acct + " (server)";
    if (it->second.kind == "direct")
        return it->second.title;
    return it->second.title + " \xE2\x80\x94 " + acct; // em dash
}

// Ask the app to poll now: what a request just set off (our own message, a
// state change) shows at once instead of at the next idle tick.
void pollNow()
{
    message(TProgram::application, evCommand, cmChatEvents, nullptr);
}

// One request; a status-1 reply (the backend's words for the user) or a
// dead backend goes to `err`.
bool ask(const std::string &req, std::string &body, std::string &err)
{
    int st = -1;
    if (!chat().request(req, body, st))
    {
        err = "tvchat-backend is not answering.";
        return false;
    }
    if (st != 0)
    {
        err = trimmed(body);
        if (err.empty())
            err = "tvchat-backend refused: " + req.substr(0, req.find(' '));
        return false;
    }
    return true;
}

// "2026-10-21T17:05:09Z" -> "10:05", the local time.  Days-from-civil by
// hand: timegm() is not on every libc tvmail builds with.
std::string localHHMM(const std::string &iso)
{
    int Y, M, D, h, m, s;
    if (std::sscanf(iso.c_str(), "%d-%d-%dT%d:%d:%d", &Y, &M, &D, &h, &m, &s) != 6)
        return "--:--";
    Y -= M <= 2;
    long era = (Y >= 0 ? Y : Y - 399) / 400;
    long yoe = Y - era * 400;
    long doy = (153L * (M + (M > 2 ? -3 : 9)) + 2) / 5 + D - 1;
    long doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    time_t t = (time_t)(era * 146097 + doe - 719468) * 86400 + h * 3600 + m * 60 + s;
    struct tm tm = {};
#ifdef _WIN32
    localtime_s(&tm, &t);
#else
    localtime_r(&t, &tm);
#endif
    char b[8];
    std::snprintf(b, sizeof b, "%02d:%02d", tm.tm_hour % 100, tm.tm_min % 100);
    return b;
}

std::string nowHHMM()
{
    time_t t = std::time(nullptr);
    struct tm tm = {};
#ifdef _WIN32
    localtime_s(&tm, &t);
#else
    localtime_r(&t, &tm);
#endif
    char b[8];
    std::snprintf(b, sizeof b, "%02d:%02d", tm.tm_hour % 100, tm.tm_min % 100);
    return b;
}

// --------------------------------------------------------------- the log ---
// One message as the window keeps it.  Lines tvmail adds itself (a state
// change, a refused send) have no id and flag n.
struct ChatLine
{
    std::string id, hhmm, nick, flags, text;
    bool has(char f) const { return flags.find(f) != std::string::npos; }
};

// A message line from `history` (from = 0) or a msg event (from = 1, after
// the type): msgid, conv, utc-iso8601, sender-address, sender-display,
// flags, text.
bool parseMsg(const std::vector<std::string> &f, size_t from, ChatLine &out)
{
    if (f.size() < from + 7)
        return false;
    out.id = f[from];
    out.hhmm = localHHMM(f[from + 2]);
    out.nick = f[from + 4].empty() ? f[from + 3] : f[from + 4];
    out.flags = f[from + 5];
    out.text = f[from + 6];
    return true;
}

enum LineKind : char
{
    lkOther,
    lkOwn,    // flag s: what you sent
    lkSystem, // flag n: notices, server text, tvmail's own notes
};

// Own lines and system lines get their own colours, both >= 3:1 on the
// cyan (e2e_regress #36 scores every cell): blue (4.6:1) and a dark grey,
// dimmer than the black of everyone else's lines (3.4:1).
inline TColorAttr cOwn() { return vga(0x31); }
inline TColorAttr cSystem() { return TColorAttr(TColorRGB(0x444444), TColorRGB(0x00AAAA)); }

// "HH:MM <nick> text", "HH:MM * nick does", "HH:MM -nick- notice"; [e] for
// an encrypted (Delta Chat) message; the lines after the first of a
// multi-line message line up under its text.
void renderLine(const ChatLine &m, std::vector<std::string> &out, std::vector<char> &kinds)
{
    std::string head = m.hhmm + " ";
    if (m.has('e'))
        head += "[e] ";
    if (m.has('a'))
        head += "* " + m.nick + " ";
    else if (m.has('n'))
        head += m.nick.empty() ? "-- " : "-" + m.nick + "- ";
    else
        head += "<" + m.nick + "> ";
    char kind = m.has('n') ? lkSystem : m.has('s') ? lkOwn : lkOther;
    std::string indent((size_t)TText::width(head), ' ');
    std::vector<std::string> ls = splitLines(m.text);
    if (ls.empty())
        ls.push_back(std::string());
    for (size_t i = 0; i < ls.size(); ++i)
    {
        std::string l = ls[i];
        if (!l.empty() && l.back() == '\r')
            l.pop_back();
        out.push_back((i ? indent : head) + l);
        kinds.push_back(kind);
    }
}

// The scrolling log: a TMailText (UTF-8 columns, wrap, select + copy, Find)
// with a colour per line.  PgUp (or Up) at the very top asks for older
// messages.
class TChatLog : public TTextView
{
    std::vector<char> kinds;
    bool wrapped = true;

public:
    std::function<void()> wantOlder;
    using TMailText::atBottom;
    using TMailText::firstShownLine;

    TChatLog(const TRect &b, TScrollBar *v)
        : TTextView(b, nullptr, v, std::vector<std::string>(), /*wrap=*/true) {}

    void show(std::vector<std::string> ls, std::vector<char> ks, int top, bool keepSel)
    {
        kinds = std::move(ks);
        replaceLines(std::move(ls), top, keepSel);
    }

    // View > Wrap long lines, from the input line (the log handles it
    // itself when it has the focus, and says so through wrapChanged()).
    void toggleWrap() { setWrap(wrapped = !wrapped); }

    // A scrolling key, from the log itself or handed on by the input line.
    void scrollKey(ushort code)
    {
        if ((code == kbPgUp || code == kbUp) && delta.y == 0 && wantOlder)
        {
            wantOlder();
            return;
        }
        KeyDownEvent k = {};
        k.keyCode = code;
        keyDown(k);
    }

    void handleEvent(TEvent &e) override
    {
        if (e.what == evKeyDown && (state & sfFocused) && delta.y == 0 &&
            (e.keyDown.keyCode == kbPgUp || e.keyDown.keyCode == kbUp) && wantOlder)
        {
            wantOlder();
            clearEvent(e);
            return;
        }
        TTextView::handleEvent(e);
    }

protected:
    void wrapChanged(bool w) override { wrapped = w; }

    TColorAttr lineColor(int line, TColorAttr norm) override
    {
        if (line >= 0 && line < (int)kinds.size())
        {
            if (kinds[line] == lkOwn)
                return cOwn();
            if (kinds[line] == lkSystem)
                return cSystem();
        }
        return norm;
    }
};

// The cyan window colours every tvmail text window uses (TMailViewWindow).
TColorAttr cyanWindowColor(uchar index, TView *self)
{
    switch (index)
    {
    case 1:
    case 4:
    case 6:
    case 8:
        return cNorm();
    case 2:
    case 3:
        return cFrame();
    case 5:
    case 7:
        return cHi();
    }
    return self->TView::mapColor(index);
}

// ----------------------------------------------------- the chat window ---
// One full-size window laid out like the mail one (mailwin.cpp): the chat
// accounts (left) | the picked account's conversations (top right) / the
// picked conversation, its log over a one-line input (bottom right).
// Alt-C brings it forward and, from here, goes back to mail; so does Esc.
// Tab moves servers -> conversations -> input.  Each account's list starts
// with its server messages (MOTD, notices, SASL results).  Text typed and
// not sent stays with its conversation when you pick another; Alt-X asks
// before it is thrown away.

void connectAccount(const ChatAccount &a); // below, with the dialogs

// The chat pane's two views say when they take the focus, as the lists do,
// so the window lights the right heading and knows you are reading.
class TChatInput : public TFieldLine
{
public:
    TChatInput(const TRect &b, int lim) : TFieldLine(b, lim) {}
    void setState(ushort aState, Boolean enable) override
    {
        TFieldLine::setState(aState, enable);
        if (enable && (aState & sfFocused) && owner)
            message(owner, evBroadcast, cmPaneFocused, this);
    }
};

class TChatPaneLog : public TChatLog
{
public:
    TChatPaneLog(const TRect &b, TScrollBar *v) : TChatLog(b, v) {}
    void setState(ushort aState, Boolean enable) override
    {
        TChatLog::setState(aState, enable);
        if (enable && (aState & sfFocused) && owner)
            message(owner, evBroadcast, cmPaneFocused, this);
    }
};

// Conversations of one account in list order: its server messages, then
// channels, groups and direct talk, by name.
std::vector<std::string> convsOf(const std::string &acct)
{
    std::vector<const ChatConv *> cs;
    for (const auto &kv : chat().convs())
        if (accountIdOf(kv.first) == acct)
            cs.push_back(&kv.second);
    auto rank = [](const ChatConv &c)
    {
        int k = c.kind == "server" ? 0 : c.kind == "channel" ? 1 : c.kind == "group" ? 2 : 3;
        return std::make_tuple(k, lowered(c.title));
    };
    std::sort(cs.begin(), cs.end(), [&](const ChatConv *x, const ChatConv *y)
              { return rank(*x) < rank(*y); });
    std::vector<std::string> out;
    for (const ChatConv *c : cs)
        out.push_back(c->id);
    return out;
}

int unreadOf(const std::string &acct)
{
    int n = 0;
    for (const auto &kv : chat().convs())
        if (accountIdOf(kv.first) == acct && kv.second.kind != "server")
            n += std::max(0, kv.second.unread);
    return n;
}

// The list panes: the mail panes' colours, and "I moved" / "I have the
// focus" told to the window as the mail panes do.
class TChatList : public TListViewer
{
    ushort picked;

public:
    std::vector<std::string> ids;

    TChatList(const TRect &b, TScrollBar *vsb, ushort cmd)
        : TListViewer(b, 1, nullptr, vsb), picked(cmd) {}

    std::string current() const
    {
        return focused >= 0 && focused < (short)ids.size() ? ids[focused] : std::string();
    }
    // New rows, the highlight on `keep` if it is still there (else the
    // first row), without announcing a move.
    void setIds(std::vector<std::string> v, const std::string &keep)
    {
        ids = std::move(v);
        setRange((short)ids.size());
        short at = 0;
        for (size_t i = 0; i < ids.size(); ++i)
            if (ids[i] == keep)
                at = (short)i;
        TListViewer::focusItem(at);
        drawView();
    }
    void focusItem(short item) override
    {
        bool moved = item != focused; // a click on the current row is no move
        TListViewer::focusItem(item);
        if (moved && owner)
            message(owner, evBroadcast, picked, this);
    }
    void selectItem(short) override
    {
        if (owner)
            message(owner, evBroadcast, cmFocusContent, this);
    }
    void handleEvent(TEvent &e) override
    {
        if (e.what == evKeyDown && e.keyDown.keyCode == kbEnter)
        {
            selectItem(focused);
            clearEvent(e);
            return;
        }
        TListViewer::handleEvent(e);
    }
    void setState(ushort aState, Boolean enable) override
    {
        TListViewer::setState(aState, enable);
        if (enable && (aState & sfFocused) && owner)
            message(owner, evBroadcast, cmPaneFocused, this);
    }
    TColorAttr mapColor(uchar i) override
    {
        switch (i)
        {
        case 1:
        case 2:
            return cNorm();
        case 3:
            return cFocus();
        case 4:
            return cCur();
        case 5:
            return cDiv();
        }
        return TView::mapColor(i);
    }
};

// "Libera (3)      connected": the account, its unread count, its state.
class TServerPane : public TChatList
{
public:
    TServerPane(const TRect &b) : TChatList(b, nullptr, cmChatAcctPicked) {}
    void getText(char *dest, short item, short maxLen) override
    {
        const ChatAccount *a = item >= 0 && item < (short)ids.size() ? findAccount(ids[item]) : nullptr;
        if (!a)
        {
            dest[0] = 0;
            return;
        }
        std::string name = a->display;
        int n = unreadOf(a->id);
        if (n > 0)
            name += " (" + std::to_string(n) + ")";
        int w = size.x - 1 - (int)a->state.size() - 1;
        putText(dest, w > 3 ? fitCols(name, w) + " " + a->state : name, maxLen);
    }
};

// "* server messages", "#test (2)", "tester2".
class TConvPane : public TChatList
{
public:
    TConvPane(const TRect &b, TScrollBar *vsb) : TChatList(b, vsb, cmChatConvPicked) {}
    void getText(char *dest, short item, short maxLen) override
    {
        auto it = item >= 0 && item < (short)ids.size() ? chat().convs().find(ids[item])
                                                         : chat().convs().end();
        if (it == chat().convs().end())
        {
            dest[0] = 0;
            return;
        }
        const ChatConv &c = it->second;
        std::string t = c.kind == "server" ? std::string("* server messages") : c.title;
        if (c.kind != "server" && c.unread > 0)
            t += " (" + std::to_string(c.unread) + ")";
        putText(dest, t, maxLen);
    }
};

class TChatMainWindow;
TChatMainWindow *gChatWin = nullptr; // the one chat window, once made

class TChatMainWindow : public TWindow
{
    TPaneTitle *srvTitle = nullptr, *convTitle = nullptr, *logTitle = nullptr;
    TDivider *vdiv = nullptr;
    TScrollBar *convVsb = nullptr, *logVsb = nullptr;
    TStaticText *prompt = nullptr;
    TServerPane *servers = nullptr;
    TConvPane *convs = nullptr;
    TChatPaneLog *log = nullptr;
    TChatInput *input = nullptr;

    std::string acct, conv, name;                  // shown: account, conversation, its name
    std::map<std::string, std::string> lastConv;   // per account: the conversation last shown
    std::map<std::string, std::string> drafts;     // per conversation: typed, not sent
    std::vector<ChatLine> msgs; // the shown conversation's, oldest first
    std::set<std::string> ids;  // what msgs holds: history and events overlap
    bool noOlder = false;       // `history --before` came back empty
    bool alive = false;         // constructed, not yet shut down

    struct Rects
    {
        TRect srvTitle, servers, vbar, convTitle, convs, convVsb,
            logTitle, log, logVsb, prompt, input;
    };
    Rects paneRects() const
    {
        int W = size.x, H = size.y;
        const int FW = 22;
        int sy = 1 + (H - 2) * 2 / 5;
        if (sy < 6)
            sy = 6;
        if (sy > H - 5)
            sy = H - 5;
        Rects r;
        r.srvTitle = TRect(1, 1, 1 + FW, 2);
        r.servers = TRect(1, 2, 1 + FW, H - 1);
        r.vbar = TRect(1 + FW, 1, 2 + FW, H - 1);
        r.convTitle = TRect(2 + FW, 1, W - 1, 2);
        r.convs = TRect(2 + FW, 2, W - 2, sy);
        r.convVsb = TRect(W - 2, 2, W - 1, sy);
        r.logTitle = TRect(2 + FW, sy, W - 1, sy + 1);
        r.log = TRect(2 + FW, sy + 1, W - 2, H - 2);
        r.logVsb = TRect(W - 2, sy + 1, W - 1, H - 2);
        // "> text": the input line keeps its own first column (the "<"
        // shown when its text scrolls), so the prompt is one cell
        r.prompt = TRect(2 + FW, H - 2, 3 + FW, H - 1);
        r.input = TRect(3 + FW, H - 2, W - 1, H - 1);
        return r;
    }

    bool front() const { return (state & sfActive) && (state & sfSelected); }
    bool inChatPane() const { return current == input || current == log; }
    bool isServer() const
    {
        auto it = chat().convs().find(conv);
        return it != chat().convs().end() && it->second.kind == "server";
    }

    bool add(const ChatLine &m)
    {
        if (!m.id.empty() && !ids.insert(m.id).second)
            return false; // in the history we read and in an event too
        msgs.push_back(m);
        return true;
    }

    // tvmail's own note in the log: a state change, a refused send.
    void note(const std::string &text)
    {
        bool bottom = log->atBottom();
        msgs.push_back({std::string(), nowHHMM(), std::string(), "n", text});
        render(bottom ? -1 : log->firstShownLine(), true);
    }

    // top: the source line to show at the top, -1 = follow the end
    void render(int top, bool keepSel)
    {
        std::vector<std::string> ls;
        std::vector<char> ks;
        for (const ChatLine &m : msgs)
            renderLine(m, ls, ks);
        log->show(std::move(ls), std::move(ks), top, keepSel);
    }

    void loadHistory()
    {
        msgs.clear();
        ids.clear();
        noOlder = false;
        if (conv.empty())
        {
            msgs.push_back({std::string(), nowHHMM(), std::string(), "n",
                            chat().accounts().empty() ? "no chat accounts" : "no conversation"});
            render(-1, false);
            return;
        }
        std::string body, err;
        if (ask("history " + shq(conv), body, err))
        {
            for (const std::string &ln : splitLines(body))
            {
                ChatLine m;
                if (!ln.empty() && parseMsg(chatFields(ln), 0, m))
                    add(m);
            }
        }
        else
            msgs.push_back({std::string(), nowHHMM(), std::string(), "n", err});
        auto it = chat().convs().find(conv);
        if (it != chat().convs().end() && !it->second.topic.empty())
            msgs.push_back({std::string(), nowHHMM(), std::string(), "n",
                            "topic: " + it->second.topic});
        render(-1, false);
    }

    // PgUp past the top: the 50 before the oldest we have, kept in view
    // where they were.
    void loadOlder()
    {
        std::string oldest;
        for (const ChatLine &m : msgs)
            if (!m.id.empty())
            {
                oldest = m.id;
                break;
            }
        if (noOlder || oldest.empty())
        {
            flashStatus("No older messages in " + name);
            return;
        }
        std::string body, err;
        if (!ask("history " + shq(conv) + " --before " + shq(oldest), body, err))
        {
            flashStatus(err);
            return;
        }
        std::vector<ChatLine> older;
        for (const std::string &ln : splitLines(body))
        {
            ChatLine m;
            if (!ln.empty() && parseMsg(chatFields(ln), 0, m) && ids.insert(m.id).second)
                older.push_back(m);
        }
        if (older.empty())
        {
            noOlder = true;
            flashStatus("No older messages in " + name);
            return;
        }
        std::vector<std::string> ls;
        std::vector<char> ks;
        for (const ChatLine &m : older)
            renderLine(m, ls, ks);
        int top = log->firstShownLine() + (int)ls.size();
        msgs.insert(msgs.begin(), older.begin(), older.end());
        render(top, false);
        // one page up into what just arrived, as PgUp would have gone
        log->scrollKey(kbPgUp);
    }

    // Read once you actually look, as in mail: the chat pane has the focus
    // and the window is in front.  looking: the chat pane is just taking
    // the focus (tvision sets `current` only after telling the view).
    void markRead(bool looking = false)
    {
        if (!front() || !(looking || inChatPane()))
            return;
        auto it = chat().convs().find(conv);
        if (it == chat().convs().end() || it->second.unread <= 0)
            return;
        std::string body, err;
        if (!ask("mark-read " + shq(conv), body, err))
            debugLog("chat: mark-read %s: %s", conv.c_str(), err.c_str());
        // the backend answers with `unread CONV 0`, which the next poll
        // applies to the model (and the lists, through the broadcast)
    }

    std::string inputText() const { return input && input->data ? input->data : ""; }
    void setInput(const std::string &s)
    {
        size_t n = std::min(s.size(), (size_t)input->maxLen);
        std::memcpy(input->data, s.data(), n);
        input->data[n] = '\0';
        input->selectAll(False);
        input->curPos = (int)n;
        input->drawView();
    }

    void sendInput()
    {
        std::string text = inputText();
        if (trimmed(text).empty())
            return;
        if (isServer())
        {
            flashStatus("Pick a channel or a person to talk to - this is " + name);
            return;
        }
        std::string body, err;
        if (!ask("send " + shq(conv) + " " + shq(text), body, err))
        {
            note("not sent: " + err); // the text stays in the input line
            return;
        }
        setInput(std::string());
        drafts.erase(conv);
        pollNow(); // our line (the backend's copy or the server's echo) now
    }

    void updateTitles()
    {
        std::string n = convName(conv);
        if (!n.empty())
            name = n;
        else if (conv.empty())
            name = "Chat";
        logTitle->setLabel(isServer() ? name.substr(0, name.rfind(" (server)")) + " - server messages" : name);
        const ChatAccount *a = findAccount(acct);
        convTitle->setLabel(a ? "Conversations - " + menuSafe(a->display) : std::string("Conversations"));
        // "tvmail chat (3)" while it is not the window you are in:
        // Window > List shows the same
        int unread = chatOn() ? ChatClient::instance().unreadTotal() : 0;
        std::string t = "tvmail chat";
        if (unread > 0 && !front())
            t += " (" + std::to_string(unread) + ")";
        if (!title || t != title)
        {
            delete[] (char *)title;
            title = newStr(t);
            if (frame)
                frame->drawView();
        }
    }

    // Both lists from the model, staying on the account and conversation
    // shown.  follow: if that conversation is gone (left, a new backend),
    // show the one the list now highlights - otherwise it stays, as a
    // conversation just joined does until its conv-new arrives.
    void refreshLists(bool follow = false)
    {
        std::vector<std::string> as;
        for (const ChatAccount &a : chat().accounts())
            as.push_back(a.id);
        if (std::find(as.begin(), as.end(), acct) == as.end())
            acct = as.empty() ? std::string() : as[0];
        servers->setIds(as, acct);
        std::vector<std::string> cs = convsOf(acct);
        convs->setIds(cs, conv);
        if (follow && std::find(cs.begin(), cs.end(), conv) == cs.end() &&
            convs->current() != conv)
            showConv(convs->current());
        updateTitles();
    }

    void showConv(const std::string &c)
    {
        if (c == conv && !msgs.empty())
            return;
        if (!conv.empty())
        {
            std::string d = inputText();
            if (trimmed(d).empty())
                drafts.erase(conv);
            else
                drafts[conv] = d;
        }
        conv = c;
        if (!acct.empty())
            lastConv[acct] = conv;
        auto d = drafts.find(conv);
        setInput(d != drafts.end() ? d->second : std::string());
        name = convName(conv);
        loadHistory();
        updateTitles();
        markRead();
    }

    void showAccount(const std::string &a)
    {
        acct = a;
        std::vector<std::string> cs = convsOf(acct);
        auto last = lastConv.find(acct);
        convs->setIds(cs, last != lastConv.end() ? last->second : std::string());
        showConv(convs->current());
    }

    void setActivePane(TView *v)
    {
        srvTitle->setActive(Boolean(v == servers));
        convTitle->setActive(Boolean(v == convs));
        logTitle->setActive(Boolean(v == log || v == input));
    }

    void onEvent(const ChatEvent &ev)
    {
        const std::vector<std::string> &f = ev.fields;
        const std::string &t = f[0];
        if (t == "msg" && f.size() >= 8 && f[2] == conv)
        {
            ChatLine m;
            bool bottom = log->atBottom();
            if (parseMsg(f, 1, m) && add(m))
                render(bottom ? -1 : log->firstShownLine(), true);
        }
        else if (t == "unread" && f.size() >= 3)
        {
            if (f[1] == conv && std::atoi(f[2].c_str()) > 0)
                markRead(); // read as it arrives: you are looking at it
            servers->drawView();
            convs->drawView();
            updateTitles();
        }
        else if (t == "state" && f.size() >= 3)
        {
            servers->drawView();
            if (f[1] == accountIdOf(conv))
            {
                std::string d = f.size() > 3 ? trimmed(f[3]) : std::string();
                note(f[2] + (d.empty() ? std::string() : ": " + d));
            }
        }
        else if (t == "topic" && f.size() >= 3 && f[1] == conv)
            note("topic: " + f[2]);
        else if ((t == "conv-new" || t == "conv-gone") && f.size() >= 2)
        {
            bool gone = t == "conv-gone" && f[1] == conv;
            if (gone)
                note("left " + name);
            refreshLists(gone);
        }
    }

public:
    TChatMainWindow(const TRect &bounds)
        : TWindowInit(&TChatMainWindow::initFrame),
          TWindow(bounds, "tvmail chat", wnNoNumber)
    {
        palette = wpCyanWindow;
        flags &= ~(wfClose | wfZoom | wfMove);
        growMode = gfGrowHiX | gfGrowHiY;
        helpCtx = hcChatWin; // its own status line: no mail keys here

        Rects R = paneRects();
        srvTitle = new TPaneTitle(R.srvTitle, "Servers");
        convTitle = new TPaneTitle(R.convTitle, "Conversations");
        logTitle = new TPaneTitle(R.logTitle, "Chat");
        vdiv = new TDivider(R.vbar, true);
        servers = new TServerPane(R.servers);
        convVsb = new TScrollBar(R.convVsb);
        convs = new TConvPane(R.convs, convVsb);
        logVsb = new TScrollBar(R.logVsb);
        log = new TChatPaneLog(R.log, logVsb);
        log->growMode = 0; // placed by changeBounds(), as the mail panes are
        log->wantOlder = [this]
        { loadOlder(); };
        prompt = new TStaticText(R.prompt, ">");
        input = new TChatInput(R.input, 2000);

        insert(vdiv);
        insert(srvTitle);
        insert(convTitle);
        insert(logTitle);
        insert(convVsb);
        insert(logVsb);
        insert(prompt);
        insert(log);
        insert(convs);
        insert(input);
        insert(servers); // first to have the focus; Tab: see handleEvent

        gChatWin = this;
        refreshLists(true);
        showAccount(acct);
        alive = true;
        servers->select();
        setActivePane(servers);
        updateTitles();
    }

    // Show this account (its server messages, or the conversation last
    // shown there) or this conversation, and put the focus where it fits.
    void goTo(const std::string &account, const std::string &c = std::string())
    {
        refreshLists();
        std::string a = c.empty() ? account : accountIdOf(c);
        if (!a.empty() && a != acct)
        {
            servers->setIds(servers->ids, a);
            showAccount(a);
        }
        if (c.empty())
        {
            convs->select();
            return;
        }
        convs->setIds(convs->ids, c); // a channel just joined is not listed yet
        showConv(c);
        input->select();
    }

    const std::string &convId() const { return conv; }
    bool showingServer() const { return isServer(); }
    bool unsent() const
    {
        if (!trimmed(inputText()).empty())
            return true;
        for (const auto &kv : drafts)
            if (kv.first != conv && !trimmed(kv.second).empty())
                return true;
        return false;
    }

    void handleEvent(TEvent &e) override
    {
        // typing with the log focused (after a click in it) goes to the input
        if (e.what == evKeyDown && current == log && e.keyDown.textLength > 0 &&
            !(e.keyDown.controlKeyState & (kbCtrlShift | kbAltShift)))
            input->select();
        if (e.what == evCommand && e.message.command == cmToggleWrap && current != log)
        {
            log->toggleWrap();
            clearEvent(e);
            return;
        }
        if (e.what == evKeyDown && current == input)
        {
            ushort k = e.keyDown.keyCode;
            if (k == kbEnter)
            {
                sendInput();
                clearEvent(e);
                return;
            }
            if (k == kbPgUp || k == kbPgDn || k == kbUp || k == kbDown ||
                k == kbCtrlPgUp || k == kbCtrlPgDn)
            {
                log->scrollKey(k);
                clearEvent(e);
                return;
            }
        }
        // Tab: servers -> conversations -> input, and round; the log is
        // not a stop (the scrolling keys work from the input), but Tab from
        // it goes on as from the input
        if (e.what == evKeyDown && (e.keyDown.keyCode == kbTab || e.keyDown.keyCode == kbShiftTab))
        {
            TView *order[] = {servers, convs, input};
            int at = current == servers ? 0 : current == convs ? 1 : 2;
            at = (at + (e.keyDown.keyCode == kbTab ? 1 : 2)) % 3;
            order[at]->select();
            clearEvent(e);
            return;
        }
        TWindow::handleEvent(e);
        if (e.what == evKeyDown && e.keyDown.keyCode == kbEsc)
        {
            clearEvent(e);
            showMailWindow(); // the text typed here stays
            return;
        }
        if (!alive || e.what != evBroadcast)
            return;
        switch (e.message.command)
        {
        case cmChatEvent:
            if (e.message.infoPtr)
                onEvent(*static_cast<const ChatEvent *>(e.message.infoPtr));
            break;
        case cmChatReset: // a new backend: its model and history are all there is now
            // stay on the conversation shown: the new backend's autoconnect
            // joins it again (and lists it) a moment later
            refreshLists();
            msgs.clear();
            showConv(conv);
            break;
        case cmChatAcctPicked:
            if (e.message.infoPtr == servers)
            {
                showAccount(servers->current());
                clearEvent(e);
            }
            break;
        case cmChatConvPicked:
            if (e.message.infoPtr == convs)
            {
                showConv(convs->current());
                clearEvent(e);
            }
            break;
        case cmFocusContent: // Enter in a list
            if (e.message.infoPtr == servers)
            {
                const ChatAccount *a = findAccount(acct);
                if (a && !isLive(*a))
                    connectAccount(*a);
                convs->select();
                clearEvent(e);
            }
            else if (e.message.infoPtr == convs)
            {
                input->select();
                clearEvent(e);
            }
            break;
        case cmPaneFocused:
        {
            TView *v = (TView *)e.message.infoPtr;
            setActivePane(v);
            if (v == input || v == log)
                markRead(true); // you are reading it now
            clearEvent(e);
            break;
        }
        }
    }

    void setState(ushort aState, Boolean enable) override
    {
        TWindow::setState(aState, enable);
        if (alive && (aState & (sfActive | sfSelected)))
        {
            markRead();
            updateTitles();
        }
    }

    Boolean valid(ushort command) override
    {
        if (!TWindow::valid(command))
            return False;
        // Alt-X asks every window, as for an unsent compose
        if (command == cmQuit && unsent())
        {
            select(); // show which window the question is about
            if (messageBox(mfConfirmation | mfYesButton | mfNoButton,
                           "Discard the chat message%s you have not sent?",
                           drafts.size() > 1 ? "s" : "") != cmYes)
                return False;
            drafts.clear(); // a quit called off later must not ask twice
            setInput(std::string());
        }
        return True;
    }

    void shutDown() override
    {
        alive = false;
        if (gChatWin == this)
            gChatWin = nullptr;
        servers = nullptr;
        convs = nullptr;
        log = nullptr;
        input = nullptr;
        TWindow::shutDown();
    }

    void changeBounds(const TRect &bounds) override
    {
        TWindow::changeBounds(bounds);
        Rects R = paneRects();
        srvTitle->changeBounds(R.srvTitle);
        servers->changeBounds(R.servers);
        vdiv->changeBounds(R.vbar);
        convTitle->changeBounds(R.convTitle);
        convs->changeBounds(R.convs);
        convVsb->changeBounds(R.convVsb);
        logTitle->changeBounds(R.logTitle);
        log->changeBounds(R.log);
        logVsb->changeBounds(R.logVsb);
        prompt->changeBounds(R.prompt);
        input->changeBounds(R.input);
    }

    TColorAttr mapColor(uchar index) override { return cyanWindowColor(index, this); }
};

// The chat window to the front (made the first time), on this account /
// conversation if given.
void openChatWindow(const std::string &account = std::string(), const std::string &c = std::string())
{
    if (!gChatWin)
        TProgram::deskTop->insert(new TChatMainWindow(TProgram::deskTop->getExtent()));
    else
        gChatWin->select();
    if (gChatWin && (!account.empty() || !c.empty()))
        gChatWin->goTo(account, c);
}

bool chatInFront()
{
    return gChatWin && TProgram::deskTop && TProgram::deskTop->current == gChatWin;
}

// ------------------------------------------------------------ dialogs ---
// Pick one of `cand` (indices into accounts()); no dialog when there is
// only one to pick.  -1 = cancelled.
int pickAccount(const char *title, const std::vector<int> &cand)
{
    const auto &as = chat().accounts();
    if (cand.size() == 1)
        return cand[0];
    TSItem *items = nullptr;
    for (auto it = cand.rbegin(); it != cand.rend(); ++it)
    {
        std::string s = menuSafe(fitCols(as[*it].display, 22)) + " " + as[*it].state;
        items = new TSItem(s, items);
    }
    int n = (int)cand.size();
    TDialog *d = new TDialog(TRect(0, 0, 44, 5 + n), title);
    d->options |= ofCentered;
    TRadioButtons *rb = new TRadioButtons(TRect(3, 2, 41, 2 + n), items);
    d->insert(rb);
    d->insert(new TButton(TRect(9, 3 + n, 19, 5 + n), "O~K~", cmOK, bfDefault));
    d->insert(new TButton(TRect(23, 3 + n, 33, 5 + n), "Cancel", cmCancel, bfNormal));
    d->selectNext(False);
    ushort r = TProgram::deskTop->execView(d);
    ushort sel = 0;
    if (r != cmCancel)
        rb->getData(&sel);
    TObject::destroy(d);
    return r == cmCancel || sel >= cand.size() ? -1 : cand[sel];
}

std::vector<int> accountsWhere(bool live)
{
    std::vector<int> out;
    const auto &as = chat().accounts();
    for (size_t i = 0; i < as.size(); ++i)
        if (isLive(as[i]) == live)
            out.push_back((int)i);
    return out;
}

void connectAccount(const ChatAccount &a)
{
    std::string body, err;
    if (!ask("connect " + shq(a.id), body, err))
        messageBox(mfError | mfOKButton, "%s", err.c_str());
    pollNow(); // `connecting` into the model: the fast poll starts
}

void connectDialog()
{
    std::vector<int> cand = accountsWhere(false);
    if (cand.empty())
    {
        messageBox("Every chat account is connected already.", mfInformation | mfOKButton);
        return;
    }
    int i = pickAccount("Connect", cand);
    if (i >= 0)
        connectAccount(chat().accounts()[i]);
}

void disconnectDialog()
{
    std::vector<int> cand = accountsWhere(true);
    if (cand.empty())
    {
        messageBox("No chat account is connected.", mfInformation | mfOKButton);
        return;
    }
    int i = pickAccount("Disconnect", cand);
    if (i < 0)
        return;
    std::string body, err;
    if (!ask("disconnect " + shq(chat().accounts()[i].id), body, err))
        messageBox(mfError | mfOKButton, "%s", err.c_str());
    pollNow();
}

// Join...: a channel, or a nick / address for a direct conversation, on
// one of the connected accounts; opens its window.
void joinDialog()
{
    std::vector<int> cand = accountsWhere(true);
    if (cand.empty())
    {
        messageBox("Connect a chat account first (Chat > Connect...).", mfInformation | mfOKButton);
        return;
    }
    const auto &as = chat().accounts();
    int n = cand.size() > 1 ? (int)cand.size() : 0;
    TDialog *d = new TDialog(TRect(0, 0, 50, 9 + n + (n ? 1 : 0)), "Join");
    d->options |= ofCentered;
    TInputLine *il = new TInputLine(TRect(3, 3, 47, 4), 200);
    d->insert(il);
    d->insert(new TLabel(TRect(2, 2, 47, 3), "~C~hannel, or a nick / address to talk to", il));
    TRadioButtons *rb = nullptr;
    if (n)
    {
        TSItem *items = nullptr;
        for (auto it = cand.rbegin(); it != cand.rend(); ++it)
            items = new TSItem(menuSafe(as[*it].display), items);
        rb = new TRadioButtons(TRect(3, 6, 47, 6 + n), items);
        d->insert(rb);
        d->insert(new TLabel(TRect(2, 5, 47, 6), "~A~ccount", rb));
    }
    int by = 5 + n + (n ? 1 : 0);
    d->insert(new TButton(TRect(12, by, 24, by + 2), "~J~oin", cmOK, bfDefault));
    d->insert(new TButton(TRect(26, by, 38, by + 2), "Cancel", cmCancel, bfNormal));
    d->selectNext(False);
    il->select();
    ushort r = TProgram::deskTop->execView(d);
    std::string target = trimmed(il->data ? il->data : "");
    ushort sel = 0;
    if (rb)
        rb->getData(&sel);
    TObject::destroy(d);
    if (r == cmCancel || target.empty() || sel >= cand.size())
        return;
    std::string body, err;
    if (!ask("join " + shq(as[cand[sel]].id) + " " + shq(target), body, err))
    {
        messageBox(mfError | mfOKButton, "%s", err.c_str());
        return;
    }
    pollNow(); // conv-new: the list shows it with its name
    std::string conv = trimmed(body);
    if (!conv.empty())
        openChatWindow(std::string(), conv);
}

// Leave: part the conversation the chat window shows; its conv-gone takes
// it out of the list.
void leaveShown()
{
    if (!chatInFront())
    {
        flashStatus("Leave: Alt-C to the chat window first");
        return;
    }
    std::string body, err;
    if (!ask("part " + shq(gChatWin->convId()), body, err))
    {
        messageBox(mfError | mfOKButton, "%s", err.c_str());
        return;
    }
    pollNow();
}

// Chat > <account>: the chat window on that account - connecting it first
// if it is not.
void accountItem(int i)
{
    const auto &as = chat().accounts();
    if (i < 0 || i >= (int)as.size())
        return;
    ChatAccount a = as[i]; // connectAccount() polls, which may reshape as
    if (!isLive(a))
        connectAccount(a);
    openChatWindow(a.id);
}

// ---------------------------------------------------------- the menu ---
TMenuItem *gChatItem = nullptr; // the menu bar's Chat entry: its subMenu is rebuilt

std::string offReason()
{
#ifdef _WIN32
    return "(chat needs a POSIX build)";
#else
    if (!ChatClient::configured())
        return "(no chat: no tvchat.conf)";
    return "(chat: tvchat-backend is down)";
#endif
}

TMenu *buildChatMenu()
{
    TMenuItem *head = nullptr, **tail = &head;
    auto add = [&](TMenuItem *it)
    {
        *tail = it;
        tail = &it->next;
    };
    add(new TMenuItem("~C~onnect...", cmChatConnect, kbNoKey, hcNoContext));
    add(new TMenuItem("~D~isconnect...", cmChatDisconnect, kbNoKey, hcNoContext));
    add(new TMenuItem("Chat ~w~indow", cmChatConvs, kbNoKey, hcNoContext, "Alt-C"));
    add(new TMenuItem("~J~oin...", cmChatJoin, kbNoKey, hcNoContext));
    add(new TMenuItem("~L~eave", cmChatLeave, kbNoKey, hcNoContext));
    add(&newLine());
    const auto &as = chat().accounts();
    if (!chatOn() || as.empty())
        add(new TMenuItem(chatOn() ? "(no chat accounts)" : offReason(), cmChatOff, kbNoKey, hcNoContext));
    else
        for (size_t i = 0; i < as.size() && i < (size_t)kChatMaxAccounts; ++i)
            add(new TMenuItem(menuSafe(as[i].display), ushort(cmChatAccount + i), kbNoKey,
                              hcNoContext, as[i].state));
    return new TMenu(*head);
}
} // namespace

bool chatOn()
{
    return ChatClient::instance().started();
}

TSubMenu &chatSubMenu()
{
    TSubMenu *s = new TSubMenu("Ch~a~t", kbAltA);
    s->subMenu = buildChatMenu();
    gChatItem = s;
    return *s;
}

// Only ever called outside a menu: from the poll handler (idle() posts
// nothing while a menu or dialog is up) and from the app constructor.
void chatMenuRebuild()
{
    if (!gChatItem)
        return;
    TMenu *old = gChatItem->subMenu;
    gChatItem->subMenu = buildChatMenu();
    delete old;
    chatMenuGrey();
}

void chatMenuGrey()
{
    if (!gChatItem || !gChatItem->subMenu)
        return;
    bool on = chatOn();
    bool inChat = chatInFront() && !gChatWin->showingServer();
    for (TMenuItem *p = gChatItem->subMenu->items; p; p = p->next)
        if (p->name && p->command)
            p->disabled = Boolean(!on || p->command == cmChatOff ||
                                  (p->command == cmChatLeave && !inChat));
}

bool chatCommand(ushort command)
{
    if (command < cmChatConnect || command >= cmChatAccount + kChatMaxAccounts)
        return false;
    if (command == cmChatOff)
        return true;
    if (!chatOn())
    {
        flashStatus(offReason()); // greyed, but tvision lets Enter pick it
        return true;
    }
    switch (command)
    {
    case cmChatConnect:
        connectDialog();
        break;
    case cmChatDisconnect:
        disconnectDialog();
        break;
    case cmChatConvs: // Alt-C: to the chat window, and from it back to mail
        if (chatInFront())
            showMailWindow();
        else
            openChatWindow();
        break;
    case cmChatJoin:
        joinDialog();
        break;
    case cmChatLeave:
        leaveShown();
        break;
    default:
        if (command >= cmChatAccount)
            accountItem(command - cmChatAccount);
    }
    return true;
}
