// notify.cpp - toasts and their history, shared by tvmail and tvchat.
//
// A message that needs no answer is not a box to dismiss: notify() puts a small
// note at the bottom right of the desktop - it takes no focus and no key, goes
// away by itself (errors stay longer; a click removes it) - and keeps a line in
// the history, which Window > Notification history shows, newest first.  The
// status line's quick flash (flashStatus) is recorded there too.  Boxes that ask
// something (Yes / No, "save changes?") stay boxes.
//
// Toasts are drawn by a view of the application group, so they sit over windows
// and dialogs alike.  They expire from idle() (TTvApp calls notifyTick), not a
// timer, so one raised while a dialog is open still goes away on time.
#include "views.h"
#include <chrono>
#include <ctime>
#include <deque>

namespace
{
using Clock = std::chrono::steady_clock;

struct Note
{
    time_t when;
    NoteLevel level;
    std::string text;
};
std::deque<Note> gHistory;
const size_t kHistoryMax = 500;
const size_t kMaxToasts = 4;
const int kMaxLines = 4;

class TToast : public TView
{
public:
    std::vector<std::string> lines;
    NoteLevel level;
    Clock::time_point until;

    TToast(const TRect &r, const std::vector<std::string> &ls, NoteLevel lv, Clock::time_point u)
        : TView(r), lines(ls), level(lv), until(u)
    {
        options &= ~ofSelectable; // never takes the focus
        growMode = 0;
        eventMask |= evMouseDown;
    }

    TColorAttr attr() const
    {
        switch (level)
        {
        case noteError:
            return vga(0x4F); // white on red
        case noteWarn:
            return vga(0x6E); // yellow on brown
        default:
            return vga(0x70); // black on grey
        }
    }

    void draw() override
    {
        TColorAttr c = attr();
        int w = size.x;
        for (int y = 0; y < size.y; ++y)
        {
            TDrawBuffer b;
            b.moveChar(0, ' ', c, w);
            std::string row;
            if (y == 0 || y == size.y - 1)
            {
                row = y == 0 ? "\xE2\x94\x8C" : "\xE2\x94\x94"; // corners
                for (int i = 2; i < w; ++i)
                    row += "\xE2\x94\x80"; // horizontal rule
                row += y == 0 ? "\xE2\x94\x90" : "\xE2\x94\x98";
            }
            else
                row = "\xE2\x94\x82 " + fitCols(lines[y - 1], w - 4) + " \xE2\x94\x82";
            b.moveStr(0, row, c);
            writeLine(0, y, w, 1, b);
        }
    }

    void handleEvent(TEvent &e) override
    {
        TView::handleEvent(e);
        if (e.what == evMouseDown)
        {
            until = Clock::now(); // a click puts it away (the next idle round)
            clearEvent(e);
        }
    }
};

std::vector<TToast *> gToasts;

// Newest at the bottom right, older ones stacked above it.
void layout()
{
    if (!TProgram::deskTop)
        return;
    TRect area = TProgram::deskTop->getBounds();
    int y = area.b.y - 1; // inside the window frame at the desktop's edge
    for (size_t i = gToasts.size(); i-- > 0;)
    {
        TToast *t = gToasts[i];
        int h = t->size.y, w = t->size.x;
        TRect r(area.b.x - w - 2, y - h, area.b.x - 2, y);
        if (r.a.y < area.a.y)
            r.a.y = area.a.y;
        t->locate(r);
        y -= h;
    }
}

void drop(size_t i)
{
    TToast *t = gToasts[i];
    gToasts.erase(gToasts.begin() + i);
    TProgram::application->remove(t); // the desktop under it is redrawn
    TObject::destroy(t);
}

// The text in lines of at most `w` columns; at most kMaxLines, the last ending in ... if cut.
std::vector<std::string> wrap(const std::string &text, int w)
{
    std::vector<std::string> out;
    std::string cur, word;
    auto flush = [&]()
    {
        if (!word.empty())
        {
            if (!cur.empty() && (int)(cur.size() + 1 + word.size()) > w)
            {
                out.push_back(cur);
                cur.clear();
            }
            while ((int)word.size() > w) // one long word (a link): cut it
            {
                if (!cur.empty())
                {
                    out.push_back(cur);
                    cur.clear();
                }
                out.push_back(word.substr(0, w));
                word.erase(0, w);
            }
            cur += (cur.empty() ? "" : " ") + word;
            word.clear();
        }
    };
    for (char ch : text)
    {
        if (ch == ' ' || ch == '\t' || ch == '\n' || ch == '\r')
            flush();
        else
            word += ch;
    }
    flush();
    if (!cur.empty())
        out.push_back(cur);
    if (out.empty())
        out.push_back("");
    if ((int)out.size() > kMaxLines)
    {
        out.resize(kMaxLines);
        std::string &last = out.back();
        if ((int)last.size() > w - 3)
            last.resize(w - 3);
        last += "...";
    }
    return out;
}

void record(const std::string &text, NoteLevel level)
{
    gHistory.push_back({time(nullptr), level, text});
    while (gHistory.size() > kHistoryMax)
        gHistory.pop_front();
}
} // namespace

void notify(const std::string &text, NoteLevel level)
{
    std::string flat;
    for (char ch : text)
        flat += (ch == '\n' || ch == '\r' || ch == '\t') ? ' ' : ch;
    size_t b = flat.find_first_not_of(' '), e = flat.find_last_not_of(' ');
    flat = b == std::string::npos ? std::string() : flat.substr(b, e - b + 1);
    if (flat.empty())
        return;
    record(flat, level);
    if (!TProgram::application || !TProgram::deskTop)
        return;
    int maxw = std::min(50, TProgram::deskTop->size.x - 8);
    if (maxw < 16)
        maxw = 16;
    std::vector<std::string> ls = wrap(flat, maxw);
    int w = 0;
    for (const std::string &l : ls)
        w = std::max(w, (int)strwidth(l));
    w += 4; // the rules and a space each side
    int secs = level == noteError ? 10 : level == noteWarn ? 7 : 4;
    TToast *t = new TToast(TRect(0, 0, w, (int)ls.size() + 2), ls, level, Clock::now() + std::chrono::seconds(secs));
    while (gToasts.size() >= kMaxToasts)
        drop(0);
    gToasts.push_back(t);
    layout();
    TProgram::application->insert(t);
    t->drawView();
}

// Called from idle(): the toasts whose time is up go away.
void notifyTick()
{
    if (gToasts.empty())
        return;
    bool any = false;
    auto now = Clock::now();
    for (size_t i = 0; i < gToasts.size();)
        if (gToasts[i]->until <= now)
        {
            drop(i);
            any = true;
        }
        else
            ++i;
    if (any)
        layout();
}

// Window > Notification history: newest first.
void notifyHistoryWindow(TDeskTop *desk)
{
    std::string text;
    for (size_t i = gHistory.size(); i-- > 0;)
    {
        const Note &n = gHistory[i];
        char hm[16];
        strftime(hm, sizeof hm, "%H:%M:%S", localtime(&n.when));
        text += std::string(hm) + "  " + (n.level == noteError ? "ERROR " : n.level == noteWarn ? "warn  " : "      ") +
                n.text + "\n";
    }
    if (text.empty())
        text = "Nothing has been announced yet.\n";
    TRect r = desk->getExtent();
    r.grow(-6, -2);
    desk->insert(newTextWindow(r, "Notification history (newest first)", text));
}

// The status line's flash is part of the history too.
void notifyRecordFlash(const std::string &msg)
{
    record(msg, noteInfo);
}

// ---- the optional beep, and how long the user has been idle ----
namespace
{
bool gBeepOn = false;
Clock::time_point gLastBeep, gLastInput = Clock::now();
} // namespace

// [ui] beep = y | n (default n) in the app's .conf.
void beepFromConf(const std::string &confPath)
{
    std::string v = lowered(confValue(confPath, "ui", "beep"));
    gBeepOn = v == "y" || v == "yes" || v == "true" || v == "1";
}

// BEL: a terminal turns it into a beep or a window flash.  At most one every two
// seconds, so a busy channel does not buzz.
void notifyBeep()
{
    auto now = Clock::now();
    if (!gBeepOn || now - gLastBeep < std::chrono::seconds(2))
        return;
    gLastBeep = now;
    TScreen::flushScreen(); // what tvision has pending goes first
    if (write(1, "\a", 1) < 0)
        return;
}

void notifyAlert(const std::string &text, NoteLevel level)
{
    notify(text, level);
    notifyBeep();
}

void inputSeen()
{
    gLastInput = Clock::now();
}

int idleSeconds()
{
    return (int)std::chrono::duration_cast<std::chrono::seconds>(Clock::now() - gLastInput).count();
}
