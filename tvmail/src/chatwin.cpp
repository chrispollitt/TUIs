// chatwin.cpp - the chat UI: the Chat menu, the Conversations list and one
// TChatWindow per conversation.  Everything here talks to tvchat-backend
// through ChatClient (chat.h) and hears about changes from the cmChatEvent /
// cmChatReset broadcasts the app sends after each poll; nothing here knows
// which protocol a conversation speaks.
#include "tvmail.h"
#include "views.h"
#include "chat.h"
#include <ctime>
#include <functional>
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

// ------------------------------------------------------- chat window ----
// One conversation: the log over a one-line input.  Enter sends; the
// scrolling keys work from the input line too, so typing never needs a
// Tab.  Esc / Alt-F3 close it - asking first when the input holds text,
// and Alt-X asks the same.
class TChatWindow;
std::vector<TChatWindow *> gChatWins;

class TChatWindow : public TWindow
{
    std::string conv, name; // name: the last title the model gave it
    TChatLog *log = nullptr;
    TFieldLine *input = nullptr;
    std::vector<ChatLine> msgs; // oldest first
    std::set<std::string> ids;  // what msgs holds: history and events overlap
    bool noOlder = false;       // `history --before` came back empty
    bool alive = false;         // constructed, not yet shut down

    bool front() const { return (state & sfActive) && (state & sfSelected); }

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

    void markRead()
    {
        auto it = chat().convs().find(conv);
        if (it == chat().convs().end() || it->second.unread <= 0)
            return;
        std::string body, err;
        if (!ask("mark-read " + shq(conv), body, err))
            debugLog("chat: mark-read %s: %s", conv.c_str(), err.c_str());
        // the backend answers with `unread CONV 0`, which the next poll
        // applies to the model (and the title, through the broadcast)
    }

    void sendInput()
    {
        std::string text = input->data ? input->data : "";
        if (trimmed(text).empty())
            return;
        std::string body, err;
        if (!ask("send " + shq(conv) + " " + shq(text), body, err))
        {
            note("not sent: " + err); // the text stays in the input line
            return;
        }
        input->data[0] = '\0';
        input->selectAll(False);
        input->drawView();
        pollNow(); // our line (the backend's copy or the server's echo) now
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
        else if (t == "unread" && f.size() >= 3 && f[1] == conv)
        {
            if (std::atoi(f[2].c_str()) > 0 && front())
                markRead(); // read as it arrives: you are looking at it
            updateTitle();
        }
        else if (t == "state" && f.size() >= 3 && f[1] == accountIdOf(conv))
        {
            std::string d = f.size() > 3 ? trimmed(f[3]) : std::string();
            note(f[2] + (d.empty() ? std::string() : ": " + d));
        }
        else if (t == "topic" && f.size() >= 3 && f[1] == conv)
            note("topic: " + f[2]);
        else if (t == "conv-gone" && f.size() >= 2 && f[1] == conv)
            note("left " + name);
        else if (t == "conv-new" && f.size() >= 2 && f[1] == conv)
            updateTitle();
    }

public:
    TChatWindow(const TRect &bounds, const std::string &c)
        : TWindowInit(&TChatWindow::initFrame),
          TWindow(bounds, "", wnNoNumber), conv(c)
    {
        palette = wpCyanWindow;
        options |= ofTileable;
        TScrollBar *v = standardScrollBar(sbVertical);
        TRect r = getExtent();
        r.grow(-1, -1);
        r.b.y -= 1;
        log = new TChatLog(r, v);
        log->wantOlder = [this]
        { loadOlder(); };
        insert(log);
        // "> text": the input line keeps its own first column (the "<"
        // shown when its text scrolls), so the prompt is one cell
        auto *prompt = new TStaticText(TRect(1, size.y - 2, 2, size.y - 1), ">");
        prompt->growMode = gfGrowLoY | gfGrowHiY;
        insert(prompt);
        input = new TFieldLine(TRect(2, size.y - 2, size.x - 1, size.y - 1), 2000);
        input->growMode = gfGrowLoY | gfGrowHiX | gfGrowHiY;
        insert(input);
        name = convName(conv);
        if (name.empty())
            name = conv;
        gChatWins.push_back(this);
        loadHistory();
        input->select();
        alive = true;
        updateTitle();
    }

    const std::string &convId() const { return conv; }
    bool unsent() const { return input && input->data && !trimmed(input->data).empty(); }

    // "#chan — Libera", and the unread count while it is not the window
    // you are in.  Window > List shows the same.
    void updateTitle()
    {
        std::string n = convName(conv);
        if (!n.empty())
            name = n;
        auto it = chat().convs().find(conv);
        int unread = it != chat().convs().end() ? it->second.unread : 0;
        std::string t = name;
        if (unread > 0 && !front())
            t += " (" + std::to_string(unread) + ")";
        if (title && t == title)
            return;
        delete[] (char *)title;
        title = newStr(t);
        if (frame)
            frame->drawView();
    }

    void reload()
    {
        loadHistory();
        updateTitle();
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
        TWindow::handleEvent(e);
        if (e.what == evKeyDown && e.keyDown.keyCode == kbEsc)
        {
            clearEvent(e);
            close(); // may delete this
            return;
        }
        if (e.what == evBroadcast && e.message.command == cmChatEvent && e.message.infoPtr)
            onEvent(*static_cast<const ChatEvent *>(e.message.infoPtr));
        else if (e.what == evBroadcast && e.message.command == cmChatReset)
            reload(); // a new backend: its history is all there is now
    }

    void setState(ushort aState, Boolean enable) override
    {
        TWindow::setState(aState, enable);
        if (alive && (aState & (sfActive | sfSelected)))
        {
            if (front())
                markRead();
            updateTitle();
        }
    }

    Boolean valid(ushort command) override
    {
        if (!TWindow::valid(command))
            return False;
        // cmQuit too: Alt-X asks every window, as for an unsent compose
        if ((command == cmClose || command == cmQuit) && unsent())
        {
            if (command == cmQuit)
                select(); // show which window the question is about
            if (messageBox(mfConfirmation | mfYesButton | mfNoButton,
                           "Discard the message you have not sent\nto %s?",
                           name.c_str()) != cmYes)
                return False;
            input->data[0] = '\0'; // a quit called off later must not ask twice
            input->selectAll(False);
            input->drawView();
        }
        return True;
    }

    void shutDown() override
    {
        alive = false;
        gChatWins.erase(std::remove(gChatWins.begin(), gChatWins.end(), this), gChatWins.end());
        log = nullptr;
        input = nullptr;
        TWindow::shutDown();
    }

    TColorAttr mapColor(uchar index) override { return cyanWindowColor(index, this); }
};

TChatWindow *chatWin(const std::string &conv)
{
    for (TChatWindow *w : gChatWins)
        if (w->convId() == conv)
            return w;
    return nullptr;
}

// Open the conversation's window, or bring it to the front.
void openChat(const std::string &conv)
{
    if (TChatWindow *w = chatWin(conv))
    {
        w->select();
        return;
    }
    TRect r = TProgram::deskTop->getExtent();
    r.grow(-4, -2);
    int n = (int)gChatWins.size() % 5; // cascade a little
    r.a.x += 2 * n;
    r.a.y += n;
    if (r.b.x - r.a.x < 30 || r.b.y - r.a.y < 6)
        r = TProgram::deskTop->getExtent();
    TProgram::deskTop->insert(new TChatWindow(r, conv));
}

// -------------------------------------------------- conversations list ---
// Every conversation of every account: protocol tag, name, unread count.
// Enter (or a double click) opens or focuses its window.
struct ConvRow
{
    std::string id, text;
};

class TConvList : public TListViewer
{
public:
    std::vector<ConvRow> rows;

    TConvList(const TRect &b, TScrollBar *vsb) : TListViewer(b, 1, nullptr, vsb)
    {
        growMode = gfGrowHiX | gfGrowHiY;
        refresh();
    }

    // Read the model again, keeping the highlight on the same conversation.
    void refresh()
    {
        std::string keep = focused >= 0 && focused < (short)rows.size() ? rows[focused].id : "";
        const auto &accts = chat().accounts();
        auto rank = [&](const ChatConv &c)
        {
            size_t a = 0;
            while (a < accts.size() && accts[a].id != accountIdOf(c.id))
                ++a;
            int k = c.kind == "server" ? 0 : c.kind == "channel" ? 1 : c.kind == "group" ? 2 : 3;
            return std::make_tuple(a, k, lowered(c.title));
        };
        std::vector<const ChatConv *> cs;
        for (const auto &kv : chat().convs())
            cs.push_back(&kv.second);
        std::sort(cs.begin(), cs.end(), [&](const ChatConv *x, const ChatConv *y)
                  { return rank(*x) < rank(*y); });
        rows.clear();
        for (const ChatConv *c : cs)
        {
            const ChatAccount *a = findAccount(accountIdOf(c->id));
            std::string tag = "[" + (a ? a->protocol : std::string("?")) + "]";
            std::string t = fitCols(tag, 6) + " " + convName(c->id);
            if (c->unread > 0)
                t += "  (" + std::to_string(c->unread) + ")";
            rows.push_back({c->id, t});
        }
        setRange((short)rows.size());
        for (size_t i = 0; i < rows.size(); ++i)
            if (rows[i].id == keep)
                focusItem((short)i);
        drawView();
    }

    void getText(char *dest, short item, short maxLen) override
    {
        if (item < 0 || item >= (short)rows.size())
        {
            dest[0] = 0;
            return;
        }
        putText(dest, rows[item].text, maxLen);
    }

    void selectItem(short item) override
    {
        if (item >= 0 && item < (short)rows.size())
            openChat(rows[item].id);
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

class TConversationsWindow;
TConversationsWindow *gConvWin = nullptr;

class TConversationsWindow : public TViewerWindow
{
    TConvList *list;

public:
    TConversationsWindow(const TRect &b)
        : TWindowInit(&TConversationsWindow::initFrame),
          TViewerWindow(b, "Conversations")
    {
        palette = wpCyanWindow;
        options |= ofTileable;
        TScrollBar *v = standardScrollBar(sbVertical | sbHandleKeyboard);
        TRect r = getExtent();
        r.grow(-1, -1);
        list = new TConvList(r, v);
        insert(list);
        gConvWin = this;
    }

    void handleEvent(TEvent &e) override
    {
        TViewerWindow::handleEvent(e); // Esc closes (and may delete this)
        if (e.what == evBroadcast &&
            (e.message.command == cmChatEvent || e.message.command == cmChatReset))
            list->refresh();
    }

    void shutDown() override
    {
        if (gConvWin == this)
            gConvWin = nullptr;
        TViewerWindow::shutDown();
    }

    TColorAttr mapColor(uchar index) override { return cyanWindowColor(index, this); }
};

void openConversations()
{
    if (gConvWin)
    {
        gConvWin->select();
        return;
    }
    TRect r = TProgram::deskTop->getExtent();
    r.a.x = std::max<int>(r.a.x, r.b.x - 52);
    r.grow(0, -1);
    TProgram::deskTop->insert(new TConversationsWindow(r));
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
    pollNow(); // conv-new: the window opens with its name
    std::string conv = trimmed(body);
    if (!conv.empty())
        openChat(conv);
}

void leaveFocused()
{
    TChatWindow *w = dynamic_cast<TChatWindow *>(TProgram::deskTop->current);
    if (!w)
    {
        flashStatus("Leave: focus the chat window first");
        return;
    }
    std::string body, err;
    if (!ask("part " + shq(w->convId()), body, err))
    {
        messageBox(mfError | mfOKButton, "%s", err.c_str());
        return;
    }
    pollNow();
    w->close(); // asks first if its input holds an unsent message
}

// Chat > <account>: its server window (MOTD, notices, errors) - connecting
// it first if it is not.
void accountItem(int i)
{
    const auto &as = chat().accounts();
    if (i < 0 || i >= (int)as.size())
        return;
    ChatAccount a = as[i]; // connectAccount() polls, which may reshape as
    if (!isLive(a))
        connectAccount(a);
    std::string conv = a.id + "/*";
    if (chat().convs().count(conv))
        openChat(conv);
    else
        flashStatus(menuSafe(a.display) + ": " + chat().accounts()[i].state);
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
    add(new TMenuItem("C~o~nversations...", cmChatConvs, kbNoKey, hcNoContext, "Alt-C"));
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
    bool inChat = TProgram::deskTop &&
                  dynamic_cast<TChatWindow *>(TProgram::deskTop->current) != nullptr;
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
    case cmChatConvs:
        openConversations();
        break;
    case cmChatJoin:
        joinDialog();
        break;
    case cmChatLeave:
        leaveFocused();
        break;
    default:
        if (command >= cmChatAccount)
            accountItem(command - cmChatAccount);
    }
    return true;
}
