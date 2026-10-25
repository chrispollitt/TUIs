// app.cpp - the application: menus, status line, the chat poll and its
// broadcasts.  The desktop, the palette and the Window list are TTvApp's
// (tvcommon); the chat window and the Chat menu are chatwin.cpp's.
#include "app.h"

namespace
{
// The menu bar, with the Chat menu's items greyed by hand: tvision only
// tracks commands 0-255 (cmChat* are 2100+) and resets every item's
// disabled flag whenever the command set changes.  Greying before and after
// each event the bar sees keeps the flags right whenever a menu opens.
class TVChatMenuBar : public TMenuBar
{
public:
    TVChatMenuBar(const TRect &r, TSubMenu &m) : TMenuBar(r, m) {}
    void handleEvent(TEvent &e) override
    {
        chatMenuGrey();
        TMenuBar::handleEvent(e);
        chatMenuGrey();
    }
};

// The status line, with a quiet "N new" at its right end while
// conversations hold unread messages (never a box).
class TVChatStatusLine : public TStatusLine
{
public:
    TVChatStatusLine(const TRect &r, TStatusDef &d) : TStatusLine(r, d) {}
    void draw() override
    {
        TStatusLine::draw();
        int n = chatOn() ? ChatClient::instance().unreadTotal() : 0;
        if (n <= 0)
            return;
        std::string s = " " + std::to_string(n) + " new ";
        int w = (int)s.size();
        if (w >= size.x)
            return;
        TDrawBuffer b;
        b.moveStr(0, s, getColor(1));
        writeLine(size.x - w, 0, w, 1, b);
    }
};
} // namespace

TVChatApp::TVChatApp()
    : TProgInit(&TVChatApp::initStatusLine,
                &TVChatApp::initMenuBar,
                &TTvApp::initDeskTop)
{
    // (the clipboard, Find dialogs and greyed edit commands: TTvApp)
    // no tvchat.conf: no co-process; the window says why
    if (ChatClient::configured() && ChatClient::instance().start())
        ticks.push_back({cmChatEvents, []
                         { return ChatClient::instance().pollIntervalMs(); },
                         std::chrono::steady_clock::now()});
    chatMenuRebuild(); // the accounts, or why there is no chat
    openChatWindow();
}

TMenuBar *TVChatApp::initMenuBar(TRect r)
{
    r.b.y = r.a.y + 1;
    return new TVChatMenuBar(r,
                        *new TSubMenu("~F~ile", kbAltF) +
                            tvReloadItem() +
                            tvExitItem() +
                            chatSubMenu() +
                            *new TSubMenu("~E~dit", kbAltE) +
                            *new TMenuItem("~C~opy", cmCopy, kbCtrlIns, hcNoContext, "Ctrl-Ins") +
                            *new TMenuItem("~P~aste", cmPaste, kbShiftIns, hcNoContext, "Shift-Ins") +
                            newLine() +
                            tvFindItems(false) +
                            *new TSubMenu("~V~iew", kbAltV) +
                            *new TMenuItem("~T~alk view: classic / IRC", cmChatTalkView, kbCtrlT, hcNoContext, "Ctrl-T") +
                            newLine() +
                            *new TMenuItem("~W~rap long lines", cmToggleWrap, kbNoKey, hcNoContext) +
                            tvWindowMenu() +
                            tvHelpMenu());
}

TStatusLine *TVChatApp::initStatusLine(TRect r)
{
    r.a.y = r.b.y - 1;
    return new TVChatStatusLine(r,
                           // hints only: cmChatOff does nothing
                           tvStatusDef(*new TStatusDef(0, 0xFFFF) +
                                       *new TStatusItem("~Tab~ Pane", kbNoKey, cmChatOff) +
                                       *new TStatusItem("~Enter~ Send", kbNoKey, cmChatOff) +
                                       *new TStatusItem("~PgUp~ Older", kbNoKey, cmChatOff)));
}

void TVChatApp::post(ushort command)
{
    TEvent ev;
    ev.what = evCommand;
    ev.message.command = command;
    ev.message.infoPtr = nullptr;
    putEvent(ev);
}

void TVChatApp::idle()
{
    TApplication::idle();
    // Nothing is posted while a menu or a dialog is up (tvmail bug #44):
    // any command ends a tvision menu, so the 250 ms poll shut every menu at
    // once, and a dialog swallows what it does not know.  Events wait in
    // the backend's queue until the next `events --after`.
    if (TopView() != this)
        return;
    auto now = std::chrono::steady_clock::now();
    for (IdleTick &t : ticks)
    {
        int ms = t.intervalMs();
        if (ms > 0 && now - t.last >= std::chrono::milliseconds(ms))
        {
            t.last = now;
            post(t.command);
            return;
        }
    }
}

// Poll tvchat-backend and hand each event to every window as a broadcast;
// a restarted backend is announced first, so the windows re-read.
void TVChatApp::chatEvents()
{
    ChatClient &cc = ChatClient::instance();
    int unread = cc.unreadTotal();
    bool reset = false;
    std::vector<ChatEvent> evs = cc.poll(reset);
    bool accounts = reset; // the Chat menu lists the accounts and their states
    if (reset)
        message(deskTop, evBroadcast, cmChatReset, nullptr);
    for (const ChatEvent &ev : evs)
    {
        accounts = accounts || ev.fields[0] == "state";
        message(deskTop, evBroadcast, cmChatEvent, (void *)&ev);
    }
    if (accounts)
        chatMenuRebuild();
    if (cc.unreadTotal() != unread && statusLine && !gFlashTimer)
        statusLine->drawView(); // "N new"
}

void TVChatApp::aboutBox()
{
    tvAboutBox(deskTop, "tvchat", TVCHAT_VERSION,
               "A Turbo Vision chat client:\nIRC (with IRCv3), talk(1) and Delta Chat.",
               "Plumbing: tvchat-backend.");
}

void TVChatApp::handleEvent(TEvent &e)
{
    TTvApp::handleEvent(e); // the flash timer, Window > List
    if (e.what != evCommand)
        return;
    if (chatCommand(e.message.command))
    {
        clearEvent(e);
        return;
    }
    switch (e.message.command)
    {
    case cmChatEvents:
        chatEvents();
        break;
    case cmReload: // File > Reload: the windows re-read the backend's state
        if (chatOn())
        {
            message(deskTop, evBroadcast, cmChatReset, nullptr);
            chatMenuRebuild();
        }
        break;
    case cmShowHelp:
    {
        TRect r = deskTop->getExtent();
        r.grow(-4, -2);
        deskTop->insert(newHelpWindow(r));
        break;
    }
    case cmAboutBox:
        aboutBox();
        break;
    default:
        return;
    }
    clearEvent(e);
}
