// floydwin.cpp - Floyd, the assistant: File > Floyd... (F7).  A transcript over an
// input line.  Every line typed goes to `tvmail-backend floyd TEXT` and what he
// answers is appended; the work (unread, triage, find, archive, empty trash, unset
// ooo, calc, todo, ...) and the asking-before-deleting live in the backend.
#include "tvmail.h"
#include "views.h"

namespace
{
class TFloydWindow : public TViewerWindow
{
    TTextView *log = nullptr;
    TRecallLine *input = nullptr;
    std::vector<std::string> lines;

    void say(const std::string &body, const char *who = "Floyd: ")
    {
        bool first = true;
        for (const std::string &l : splitLines(body))
        {
            if (l.empty())
                continue;
            lines.push_back((first ? std::string(who) : std::string(std::strlen(who), ' ')) + l);
            first = false;
        }
        show();
    }

    void show()
    {
        log->setLines(lines);
        log->scrollTo(0, 1 << 20); // the scroll bar clamps it to the last page: the newest line shows
        log->drawView();
    }

    // !claude QUESTION takes many seconds: the backend starts it and answers at once, and this
    // window asks `floyd --poll` once a second until Claude's answer is there
    TTimerId pollTimer = nullptr;

    void stopPolling()
    {
        if (pollTimer && TProgram::application)
            TProgram::application->killTimer(pollTimer);
        pollTimer = nullptr;
    }

    void ask(const std::string &text)
    {
        lines.push_back("you:   " + text);
        std::string body = backendRun("floyd " + shq(text));
        say(body);
        if (!pollTimer && body.find("will ask Claude") != std::string::npos)
            pollTimer = TProgram::application->setTimer(1000, 1000);
    }

    void pollClaude()
    {
        std::string body = backendRun("floyd --poll");
        if (body.find_first_not_of(" \r\n\t") == std::string::npos)
            return;
        stopPolling();
        say(body, "");
    }

public:
    TFloydWindow(const TRect &b)
        : TWindowInit(&TFloydWindow::initFrame),
          TViewerWindow(b, "Floyd - ask him anything (help lists what he does)")
    {
        palette = wpCyanWindow;
        options |= ofTileable;
        TRect r = getExtent();
        r.grow(-1, -1);
        TRect lr = r;
        lr.b.y -= 2;
        TScrollBar *v = standardScrollBar(sbVertical | sbHandleKeyboard);
        log = new TTextView(lr, nullptr, v, lines, true);
        insert(log);
        TRect ir(r.a.x, r.b.y - 1, r.b.x, r.b.y);
        input = new TRecallLine(ir, 240, floydHistoryPath()); // Up / Down: what was said before
        input->growMode = gfGrowLoY | gfGrowHiY | gfGrowHiX;
        insert(input);
        input->select();
        say(backendRun("floyd"));
        std::string tips = backendRun("floyd --tips");
        if (!tips.empty())
            say(tips);
    }

    TColorAttr mapColor(uchar i) override
    {
        switch (i)
        {
        case 1:
            return cNorm();
        case 2:
        case 3:
            return cFrame();
        case 4:
        case 6:
        case 8:
            return cNorm();
        case 5:
        case 7:
            return cHi();
        }
        return TView::mapColor(i);
    }

    ~TFloydWindow() { stopPolling(); }

    void handleEvent(TEvent &e) override
    {
        if (e.what == evBroadcast && e.message.command == cmTimerExpired && pollTimer && e.message.infoPtr == pollTimer)
        {
            clearEvent(e);
            pollClaude();
            return;
        }
        TViewerWindow::handleEvent(e);
        if (e.what == evKeyDown && e.keyDown.keyCode == kbEnter && input->state & sfFocused)
        {
            std::string t = input->data;
            clearEvent(e);
            if (t.find_first_not_of(' ') == std::string::npos)
                return;
            input->remember(t);
            input->data[0] = '\0';
            input->selectAll(True);
            input->drawView();
            ask(t);
        }
    }
};
} // namespace

TWindow *newFloydWindow(const TRect &bounds)
{
    return new TFloydWindow(bounds);
}

// What he volunteers after a pull, at the clippy level: one toast per tip.
void floydTips()
{
    for (const std::string &l : splitLines(backendRun("floyd --tips")))
        if (!l.empty())
            notify(l);
}
