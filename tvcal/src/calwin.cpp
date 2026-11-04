// calwin.cpp - the calendar window (a month, the agenda, the to-do list), the
// dialogs that edit an event or a to-do, and everything the menus do.  The data
// is the backend's: every view here is built from a few `range` / `todos` calls.
#include "tvcal.h"
#include "views.h"
#include <map>

namespace
{
// ---------------------------------------------------------------- helpers ---
std::string trimmed(const std::string &s)
{
    size_t a = 0, b = s.size();
    while (a < b && isspace((unsigned char)s[a]))
        ++a;
    while (b > a && isspace((unsigned char)s[b - 1]))
        --b;
    return s.substr(a, b - a);
}

// --opt=VALUE as one shell word: a value that starts with "-" is not taken for an option
std::string opt(const char *name, const std::string &value)
{
    return " " + shq(std::string("--") + name + "=" + value);
}

const char *kRepeat[] = {"none", "daily", "weekly", "monthly", "yearly"};
const char *kDayNames[] = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"};
const char *kMonthNames[] = {"January", "February", "March",     "April",   "May",      "June",
                             "July",    "August",   "September", "October", "November", "December"};

struct Settings
{
    bool mondayFirst = true;
    std::string remind = "15";
} gSet;

void loadSettings()
{
    std::string body, err;
    if (!calAsk("settings", body, err))
        return;
    for (const std::string &ln : splitLines(body))
    {
        std::vector<std::string> f = calFields(ln);
        if (f.size() < 2)
            continue;
        if (f[0] == "week_start")
            gSet.mondayFirst = f[1] != "sun";
        else if (f[0] == "remind")
            gSet.remind = f[1];
    }
}

std::string niceDate(const Ymd &d)
{
    return std::string(kDayNames[ymdWeekday(d)]) + " " + ymdIso(d);
}

// A reply's lines as rows of fields.
std::vector<std::vector<std::string>> rowsOf(const std::string &body)
{
    std::vector<std::vector<std::string>> out;
    for (const std::string &ln : splitLines(body))
        if (!ln.empty())
            out.push_back(calFields(ln));
    return out;
}

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

// ------------------------------------------------------------ the panes ---
// The month: seven columns of three cells, the week starting Monday or Sunday ([cal] week_start).
// A day with something on it is bold, today is white on blue, the day picked is the focus colour.
class TMonthPane : public TView
{
public:
    Ymd sel, today;
    std::map<std::string, int> counts; // ISO date -> events that day
    std::function<void(int)> moveDays, moveMonths;
    std::function<void()> goToday, enter, newEvent;
    std::function<void(const Ymd &)> clicked;

    TMonthPane(const TRect &b) : TView(b)
    {
        options |= ofSelectable | ofFirstClick;
        growMode = 0;
        sel = today = ymdToday();
    }

    Ymd gridStart() const
    {
        Ymd first = sel;
        first.d = 1;
        int wd = ymdWeekday(first);
        return ymdAdd(first, -(gSet.mondayFirst ? wd : (wd + 1) % 7));
    }

    void draw() override
    {
        TDrawBuffer b;
        b.moveChar(0, ' ', cNorm(), size.x);
        const char *mon[] = {"Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"};
        for (int c = 0; c < 7; ++c)
            b.moveStr(2 + c * 3, mon[(c + (gSet.mondayFirst ? 0 : 6)) % 7], cHi());
        writeLine(0, 0, size.x, 1, b);
        Ymd d = gridStart();
        bool focus = (state & sfFocused) != 0;
        for (int w = 0; w < 6; ++w)
        {
            b.moveChar(0, ' ', cNorm(), size.x);
            for (int c = 0; c < 7; ++c, d = ymdAdd(d, 1))
            {
                std::string iso = ymdIso(d);
                auto it = counts.find(iso);
                bool busy = it != counts.end() && it->second > 0;
                bool inMonth = d.m == sel.m;
                TColorAttr a = inMonth ? (busy ? vga(0x30, slBold) : cNorm()) : cDim();
                if (d == today)
                    a = vga(0x1F);
                if (d == sel)
                    a = focus ? cFocus() : cCur();
                char cell[8];
                snprintf(cell, sizeof cell, "%2d%c", d.d, busy ? '*' : ' ');
                b.moveStr(2 + c * 3 - 1 + 0, cell, a);
            }
            writeLine(0, w + 1, size.x, 1, b);
        }
    }

    void handleEvent(TEvent &e) override
    {
        TView::handleEvent(e);
        if (e.what == evMouseDown)
        {
            TPoint p = makeLocal(e.mouse.where);
            int row = p.y - 1, col = (p.x - 1) / 3;
            if (row >= 0 && row < 6 && col >= 0 && col < 7)
            {
                Ymd d = ymdAdd(gridStart(), row * 7 + col);
                select();
                if (clicked)
                    clicked(d);
                if ((e.mouse.eventFlags & meDoubleClick) && enter)
                    enter();
            }
            clearEvent(e);
        }
        else if (e.what == evKeyDown)
        {
            ushort k = e.keyDown.keyCode;
            char ch = e.keyDown.charScan.charCode;
            bool did = true;
            if (k == kbLeft)
                moveDays(-1);
            else if (k == kbRight)
                moveDays(1);
            else if (k == kbUp)
                moveDays(-7);
            else if (k == kbDown)
                moveDays(7);
            else if (k == kbPgUp)
                moveMonths(-1);
            else if (k == kbPgDn)
                moveMonths(1);
            else if (k == kbHome)
                goToday();
            else if (k == kbEnter)
                enter();
            else if (k == kbIns || ch == 'n')
                newEvent();
            else
                did = false;
            if (did)
                clearEvent(e);
        }
    }

    void setState(ushort aState, Boolean enable) override
    {
        TView::setState(aState, enable);
        if (aState & sfFocused)
        {
            drawView();
            if (enable && owner)
                message(owner, evBroadcast, cmCalPane, this);
        }
    }
};

// The list panes: the mail panes' colours, and "I moved" / "I have the focus" told to the window.
class TCalList : public TListViewer
{
public:
    std::function<void()> moved, enter, newItem, del, pick; // pick: space or a double click

    TCalList(const TRect &b, TScrollBar *vsb) : TListViewer(b, 1, nullptr, vsb) {}

    void focusItem(short item) override
    {
        bool changed = item != focused;
        TListViewer::focusItem(item);
        if (changed && moved)
            moved();
    }
    void selectItem(short) override
    {
        if (pick)
            pick();
    }
    void handleEvent(TEvent &e) override
    {
        if (e.what == evKeyDown)
        {
            ushort k = e.keyDown.keyCode;
            char ch = e.keyDown.charScan.charCode;
            std::function<void()> *act = k == kbEnter ? &enter : (k == kbIns || ch == 'n') ? &newItem : k == kbDel ? &del : nullptr;
            if (act && *act)
            {
                (*act)();
                clearEvent(e);
                return;
            }
        }
        TListViewer::handleEvent(e);
    }
    void setState(ushort aState, Boolean enable) override
    {
        TListViewer::setState(aState, enable);
        if (enable && (aState & sfFocused) && owner)
            message(owner, evBroadcast, cmCalPane, this);
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

// The agenda: from a day on, a heading per day that has something (and for the day picked), the
// events under it.
struct AgRow
{
    enum Kind { head, event, none } kind = head;
    Ymd date;
    Occ occ;
};

class TAgendaPane : public TCalList
{
public:
    std::vector<AgRow> rows;
    Ymd today;

    TAgendaPane(const TRect &b, TScrollBar *vsb) : TCalList(b, vsb) {}

    void setRows(std::vector<AgRow> r, int focus)
    {
        rows = std::move(r);
        setRange((short)rows.size());
        TListViewer::focusItem((short)std::max(0, std::min(focus, (int)rows.size() - 1)));
        drawView();
    }
    const AgRow *current() const { return focused >= 0 && focused < (short)rows.size() ? &rows[focused] : nullptr; }

    static std::string line(const AgRow &r, const Ymd &today)
    {
        if (r.kind == AgRow::head)
        {
            int dd = ymdDiff(r.date, today);
            std::string note = dd == 0 ? "  today" : dd == 1 ? "  tomorrow" : dd == -1 ? "  yesterday" : "";
            return niceDate(r.date) + note;
        }
        if (r.kind == AgRow::none)
            return "   (nothing planned)";
        const Occ &o = r.occ;
        std::string when = o.start.empty() ? "all day    " : o.end.empty() ? o.start + "      " : o.start + "-" + o.end;
        std::string s = "  " + when + "  " + o.summary;
        if (!o.location.empty())
            s += " @ " + o.location;
        if (o.repeat != "none" && !o.repeat.empty())
            s += " (" + o.repeat + ")";
        return s;
    }

    void draw() override
    {
        bool front = (state & sfActive) && (state & sfSelected);
        TDrawBuffer b;
        for (short y = 0; y < size.y; ++y)
        {
            short item = y + topItem;
            bool real = item < range;
            TColorAttr text = front ? getColor(1) : getColor(2);
            if (real && rows[item].kind == AgRow::head)
                text = vga(0x30, slBold);
            if (real && item == focused)
                text = (state & sfFocused) ? getColor(3) : getColor(4);
            b.moveChar(0, ' ', text, size.x);
            if (real)
                b.moveStr(0, fitCols(line(rows[item], today), size.x), text);
            writeLine(0, y, size.x, 1, b);
        }
    }
    void getText(char *dest, short item, short maxLen) override
    {
        putText(dest, item >= 0 && item < (short)rows.size() ? line(rows[item], today) : std::string(), maxLen);
    }
};

const TableCols kTodoCols = {{"Due", 5}, {"P", 1}, {"To do", 0}};

class TTodoPane : public TCalList
{
public:
    std::vector<TodoItem> items;
    Ymd today;

    TTodoPane(const TRect &b, TScrollBar *vsb) : TCalList(b, vsb) {}

    void setItems(std::vector<TodoItem> v, const std::string &keepUid)
    {
        items = std::move(v);
        setRange((short)items.size());
        short at = 0;
        for (size_t i = 0; i < items.size(); ++i)
            if (items[i].uid == keepUid)
                at = (short)i;
        TListViewer::focusItem(at);
        drawView();
    }
    const TodoItem *current() const { return focused >= 0 && focused < (short)items.size() ? &items[focused] : nullptr; }

    void draw() override
    {
        drawTableList(*this, kTodoCols, [this](short item) -> std::vector<std::string>
                      {
            if (item >= (short)items.size())
                return {};
            const TodoItem &t = items[item];
            bool overdue = !t.done && !t.due.empty() && t.due < ymdIso(today);
            std::string p = overdue ? "!" : (t.prio == "0" ? "" : t.prio);
            return {t.due.size() >= 10 ? t.due.substr(5) : "", p, std::string(t.done ? "[x] " : "[ ] ") + t.summary}; });
    }
    void getText(char *dest, short item, short maxLen) override
    {
        putText(dest, item >= 0 && item < (short)items.size() ? items[item].summary : std::string(), maxLen);
    }
};

// ----------------------------------------------------------- the dialogs ---
TInputLine *addField(TDialog *d, int x, int y, int w, const char *label, int maxLen, const std::string &init)
{
    TInputLine *in = new TFieldLine(TRect(x, y + 1, x + w, y + 2), maxLen + 1); // (tvision keeps maxLen - 1 characters)
    strncpy(in->data, init.c_str(), maxLen);
    in->data[maxLen] = 0;
    in->selectAll(True);
    d->insert(in);
    d->insert(new TLabel(TRect(x, y, x + (int)strlen(label) + 2, y + 1), label, in));
    return in;
}

std::string text(TInputLine *in)
{
    return trimmed(in->data ? in->data : "");
}

struct EvForm
{
    std::string uid, title, date, start, end, location, remind, interval = "1", until, notes, days = "1";
    int repeat = 0;
};

// mode 0: a new event (OK, Cancel); 1: editing (+ Delete: cmNo); 2: asked for by another app
// (Add, Skip, Discard: cmNo).
ushort eventDialog(const char *caption, EvForm &f, int mode)
{
    TDialog *d = new TDialog(TRect(0, 0, 74, 20), caption);
    d->options |= ofCentered;
    TInputLine *title = addField(d, 3, 2, 66, "~T~itle", 200, f.title);
    TInputLine *date = addField(d, 3, 5, 12, "D~a~te", 20, f.date);
    TInputLine *start = addField(d, 18, 5, 7, "~S~tart", 5, f.start);
    TInputLine *end = addField(d, 28, 5, 7, "~E~nd", 5, f.end);
    TInputLine *days = addField(d, 38, 5, 4, "Da~y~s", 3, f.days);
    TInputLine *remind = addField(d, 46, 5, 8, "Re~m~ind", 6, f.remind);
    d->insert(new TStaticText(TRect(56, 6, 72, 7), "min before / off"));
    TInputLine *loc = addField(d, 3, 8, 66, "~L~ocation", 200, f.location);
    TRadioButtons *rep = new TRadioButtons(
        TRect(3, 12, 17, 17),
        new TSItem("N~o~ne", new TSItem("Dai~l~y", new TSItem("~W~eekly", new TSItem("Mo~n~thly", new TSItem("Y~e~arly", nullptr))))));
    ushort rv = (ushort)f.repeat;
    rep->setData(&rv);
    d->insert(rep);
    d->insert(new TLabel(TRect(3, 11, 17, 12), "~R~epeat", rep));
    TInputLine *every = addField(d, 20, 11, 5, "E~v~ery", 3, f.interval);
    d->insert(new TStaticText(TRect(26, 12, 38, 13), "(n-th)"));
    TInputLine *until = addField(d, 20, 14, 12, "U~n~til", 20, f.until);
    TInputLine *notes = addField(d, 36, 11, 33, "No~t~es", 400, f.notes);
    d->insert(new TStaticText(TRect(36, 14, 70, 17), "Dates: 2026-11-05, 11-05, tomorrow,\n+3, fri.  Times: 14:30.  Empty start\n= all day.  A repeat changes the series."));
    int y = 17;
    if (mode == 1)
    {
        d->insert(new TButton(TRect(10, y, 22, y + 2), "~O~K", cmOK, bfDefault));
        d->insert(new TButton(TRect(26, y, 38, y + 2), "Cancel", cmCancel, bfNormal));
        d->insert(new TButton(TRect(42, y, 54, y + 2), "~D~elete", cmNo, bfNormal));
    }
    else if (mode == 2)
    {
        d->insert(new TButton(TRect(10, y, 22, y + 2), "~A~dd", cmOK, bfDefault));
        d->insert(new TButton(TRect(26, y, 38, y + 2), "Ski~p~", cmCancel, bfNormal));
        d->insert(new TButton(TRect(42, y, 54, y + 2), "Disca~r~d", cmNo, bfNormal));
    }
    else
    {
        d->insert(new TButton(TRect(22, y, 34, y + 2), "~O~K", cmOK, bfDefault));
        d->insert(new TButton(TRect(38, y, 50, y + 2), "Cancel", cmCancel, bfNormal));
    }
    title->select();
    ushort r = TProgram::deskTop->execView(d);
    f.title = text(title);
    f.date = text(date);
    f.start = text(start);
    f.end = text(end);
    f.days = text(days);
    f.remind = text(remind);
    f.location = text(loc);
    rep->getData(&rv);
    f.repeat = rv;
    f.interval = text(every);
    f.until = text(until);
    f.notes = text(notes);
    TObject::destroy(d);
    return r;
}

struct TodoForm
{
    std::string uid, title, due, prio = "0", cats, notes;
};

ushort todoDialog(const char *caption, TodoForm &f, int mode)
{
    TDialog *d = new TDialog(TRect(0, 0, 74, 15), caption);
    d->options |= ofCentered;
    TInputLine *title = addField(d, 3, 2, 66, "~T~o do", 300, f.title);
    TInputLine *due = addField(d, 3, 5, 12, "~D~ue", 20, f.due);
    TInputLine *prio = addField(d, 18, 5, 4, "~P~riority", 2, f.prio == "0" ? "" : f.prio);
    TInputLine *cats = addField(d, 30, 5, 38, "~C~ategories", 100, f.cats);
    TInputLine *notes = addField(d, 3, 8, 66, "~N~otes", 400, f.notes);
    d->insert(new TStaticText(TRect(3, 10, 70, 11), "Priority 1 (highest) to 9, empty for none.  Due: 2026-11-05, 11-05, tomorrow, +3, fri."));
    int y = 12;
    if (mode == 1)
    {
        d->insert(new TButton(TRect(10, y, 22, y + 2), "~O~K", cmOK, bfDefault));
        d->insert(new TButton(TRect(26, y, 38, y + 2), "Cancel", cmCancel, bfNormal));
        d->insert(new TButton(TRect(42, y, 54, y + 2), "Dele~t~e", cmNo, bfNormal));
    }
    else if (mode == 2)
    {
        d->insert(new TButton(TRect(10, y, 22, y + 2), "~A~dd", cmOK, bfDefault));
        d->insert(new TButton(TRect(26, y, 38, y + 2), "Ski~p~", cmCancel, bfNormal));
        d->insert(new TButton(TRect(42, y, 54, y + 2), "Disca~r~d", cmNo, bfNormal));
    }
    else
    {
        d->insert(new TButton(TRect(22, y, 34, y + 2), "~O~K", cmOK, bfDefault));
        d->insert(new TButton(TRect(38, y, 50, y + 2), "Cancel", cmCancel, bfNormal));
    }
    title->select();
    ushort r = TProgram::deskTop->execView(d);
    f.title = text(title);
    f.due = text(due);
    f.prio = text(prio);
    f.cats = text(cats);
    f.notes = text(notes);
    TObject::destroy(d);
    return r;
}

std::string eventArgs(const EvForm &f)
{
    return opt("date", f.date) + opt("start", f.start) + opt("end", f.end) + opt("days", f.days) +
           opt("location", f.location) + opt("notes", f.notes) + opt("repeat", kRepeat[f.repeat]) +
           opt("interval", f.interval) + opt("until", f.until) + opt("remind", f.remind) + " -- " + shq(f.title);
}

std::string todoArgs(const TodoForm &f)
{
    return opt("due", f.due) + opt("prio", f.prio) + opt("cats", f.cats) + opt("notes", f.notes);
}

int repeatIndex(const std::string &r)
{
    for (int i = 0; i < 5; ++i)
        if (r == kRepeat[i])
            return i;
    return 0;
}

// ------------------------------------------------------------ the window ---
class TCalWindow;
TCalWindow *gWin = nullptr;
int gIncoming = 0;

class TCalWindow : public TWindow
{
    TMonthPane *month = nullptr;
    TAgendaPane *agenda = nullptr;
    TTodoPane *todo = nullptr;
    TPaneTitle *monthTitle = nullptr, *agendaTitle = nullptr, *todoTitle = nullptr;
    TTableHead *todoHead = nullptr;
    TDivider *vdiv = nullptr;
    TScrollBar *agendaVsb = nullptr, *todoVsb = nullptr;

    Ymd sel, today;
    std::vector<Occ> agendaOcc; // occurrences from agLo to agHi
    Ymd agLo, agHi;
    bool haveAgenda = false;
    bool showDone = false;
    bool settling = false; // a pane was moved by code: it does not move the others again

    int leftWidth() const { return std::max(26, std::min(40, size.x / 3)); }

    struct Rects
    {
        TRect monthTitle, month, todoTitle, todoHead, todo, todoVsb, vbar, agendaTitle, agenda, agendaVsb;
    };
    Rects paneRects() const
    {
        int W = size.x, H = size.y, L = leftWidth();
        Rects r;
        r.monthTitle = TRect(1, 1, 1 + L, 2);
        r.month = TRect(1, 2, 1 + L, 9);
        r.todoTitle = TRect(1, 9, 1 + L, 10);
        r.todoHead = TRect(1, 10, 1 + L - 1, 11);
        r.todo = TRect(1, 11, 1 + L - 1, std::max(12, H - 1));
        r.todoVsb = TRect(L, 11, 1 + L, std::max(12, H - 1));
        r.vbar = TRect(1 + L, 1, 2 + L, H - 1);
        r.agendaTitle = TRect(2 + L, 1, W - 1, 2);
        r.agenda = TRect(2 + L, 2, W - 2, H - 1);
        r.agendaVsb = TRect(W - 2, 2, W - 1, H - 1);
        return r;
    }

    TView *panes[3];

public:
    TCalWindow(const TRect &bounds)
        : TWindowInit(&TCalWindow::initFrame), TWindow(bounds, "tvcal", wnNoNumber)
    {
        palette = wpCyanWindow;
        flags &= ~(wfClose | wfZoom | wfMove);
        growMode = gfGrowHiX | gfGrowHiY;
        today = sel = ymdToday();

        Rects R = paneRects();
        monthTitle = new TPaneTitle(R.monthTitle, "Month");
        month = new TMonthPane(R.month);
        todoTitle = new TPaneTitle(R.todoTitle, "To-do");
        todoHead = new TTableHead(R.todoHead, kTodoCols);
        todoVsb = new TScrollBar(R.todoVsb);
        todo = new TTodoPane(R.todo, todoVsb);
        vdiv = new TDivider(R.vbar, true);
        agendaTitle = new TPaneTitle(R.agendaTitle, "Agenda");
        agendaVsb = new TScrollBar(R.agendaVsb);
        agenda = new TAgendaPane(R.agenda, agendaVsb);
        for (TView *v : {(TView *)month, (TView *)todoHead, (TView *)todo, (TView *)agenda, (TView *)monthTitle, (TView *)todoTitle,
                         (TView *)agendaTitle, (TView *)vdiv, (TView *)todoVsb, (TView *)agendaVsb})
            v->growMode = 0; // placed by changeBounds()
        panes[0] = month;
        panes[1] = agenda;
        panes[2] = todo;

        month->moveDays = [this](int n) { setSel(ymdAdd(sel, n), false); };
        month->moveMonths = [this](int n) { setSel(ymdAddMonths(sel, n), false); };
        month->goToday = [this] { setSel(ymdToday(), false); };
        month->enter = [this] { focusPane(agenda); };
        month->newEvent = [this] { newEvent(); };
        month->clicked = [this](const Ymd &d) { setSel(d, false); };

        agenda->moved = [this]
        {
            const AgRow *r = agenda->current();
            if (r && !settling)
                setSel(r->date, true);
        };
        agenda->enter = [this] { editEvent(); };
        agenda->pick = [this] { editEvent(); };
        agenda->newItem = [this] { newEvent(); };
        agenda->del = [this] { deleteEvent(); };

        todo->enter = [this] { editTodo(); };
        todo->pick = [this] { toggleTodo(); };
        todo->newItem = [this] { newTodo(); };
        todo->del = [this] { deleteTodo(); };

        insert(vdiv);
        insert(monthTitle);
        insert(todoTitle);
        insert(todoHead);
        insert(agendaTitle);
        insert(todoVsb);
        insert(agendaVsb);
        insert(todo);
        insert(agenda);
        insert(month); // first to have the focus
        gWin = this;
        refreshAll(true);
        month->select();
        updateTitles();
    }
    ~TCalWindow() { gWin = nullptr; }

    std::string dateShown() const { return ymdIso(sel); }
    bool showsDone() const { return showDone; }
    void setShowDone(bool on)
    {
        showDone = on;
        refreshTodos();
    }

    // ---- what the panes show
    void updateTitles()
    {
        monthTitle->setLabel(std::string(kMonthNames[sel.m - 1]) + " " + std::to_string(sel.y));
        int n = (int)todo->items.size();
        todoTitle->setLabel(std::string("To-do (") + std::to_string(n) + (showDone ? ", with done" : "") + ")");
        agendaTitle->setLabel("Agenda from " + niceDate(sel));
        TView *c = current;
        monthTitle->setActive(c == month ? True : False);
        agendaTitle->setActive(c == agenda ? True : False);
        todoTitle->setActive(c == todo ? True : False);
    }

    void refreshGrid()
    {
        month->sel = sel;
        month->today = today;
        month->counts.clear();
        Ymd g = month->gridStart();
        std::string body, err;
        if (calAsk("range " + ymdIso(g) + " " + ymdIso(ymdAdd(g, 41)), body, err))
            for (auto &f : rowsOf(body))
                if (!f.empty())
                    ++month->counts[f[0]];
        else
            notify(err, noteError);
        month->drawView();
    }

    void fetchAgenda(const Ymd &from)
    {
        agendaOcc.clear();
        agLo = from;
        agHi = ymdAdd(from, 120);
        haveAgenda = true;
        std::string body, err;
        if (calAsk("range " + ymdIso(agLo) + " " + ymdIso(agHi), body, err))
            for (auto &f : rowsOf(body))
                if (f.size() >= 8)
                    agendaOcc.push_back({f[0], f[1], f[2], f[3], f[4], f[5], f[6], f[7]});
        else
            notify(err, noteError);
    }

    // Rows from `from` on; the focus on the heading of `focusDay` (or on the event `keepUid` on it).
    void buildAgenda(const Ymd &from, const Ymd &focusDay, const std::string &keepUid)
    {
        std::vector<AgRow> rows;
        int focus = 0;
        size_t i = 0;
        Ymd last = std::min(agHi, ymdAdd(from, 90));
        for (Ymd d = from; d <= last; d = ymdAdd(d, 1))
        {
            std::string iso = ymdIso(d);
            while (i < agendaOcc.size() && agendaOcc[i].date < iso)
                ++i;
            size_t j = i;
            while (j < agendaOcc.size() && agendaOcc[j].date == iso)
                ++j;
            bool special = d == focusDay || d == today;
            if (j == i && !special)
                continue;
            AgRow h;
            h.kind = AgRow::head;
            h.date = d;
            if (d == focusDay)
                focus = (int)rows.size();
            rows.push_back(h);
            if (j == i)
            {
                AgRow n;
                n.kind = AgRow::none;
                n.date = d;
                rows.push_back(n);
            }
            for (size_t k = i; k < j; ++k)
            {
                AgRow e;
                e.kind = AgRow::event;
                e.date = d;
                e.occ = agendaOcc[k];
                if (d == focusDay && !keepUid.empty() && e.occ.uid == keepUid)
                    focus = (int)rows.size();
                rows.push_back(e);
            }
        }
        agenda->today = today;
        settling = true;
        agenda->setRows(std::move(rows), focus);
        settling = false;
    }

    void refreshTodos()
    {
        std::string keep = todo->current() ? todo->current()->uid : std::string();
        std::vector<TodoItem> v;
        std::string body, err;
        if (calAsk(showDone ? "todos --all" : "todos", body, err))
            for (auto &f : rowsOf(body))
                if (f.size() >= 6)
                {
                    TodoItem t;
                    t.uid = f[0];
                    t.done = f[1] == "done";
                    t.prio = f[2];
                    t.due = f[3];
                    t.summary = f[4];
                    t.cats = f[5];
                    v.push_back(t);
                }
        todo->today = today;
        todo->setItems(std::move(v), keep);
        updateTitles();
    }

    // Everything again from the backend; `top`: the agenda starts at the date picked.
    void refreshAll(bool top)
    {
        today = ymdToday();
        std::string keep = agenda->current() && agenda->current()->kind == AgRow::event ? agenda->current()->occ.uid : "";
        Ymd from = top || !haveAgenda ? sel : agLo;
        fetchAgenda(from);
        buildAgenda(from, sel, keep);
        refreshGrid();
        refreshTodos();
        updateTitles();
    }

    // The date picked changed.  fromAgenda: the user moved the agenda, which stays as it is.
    void setSel(const Ymd &d, bool fromAgenda)
    {
        Ymd old = sel;
        sel = d;
        today = ymdToday();
        if (!fromAgenda)
        {
            if (!haveAgenda || sel < agLo || agHi < ymdAdd(sel, 30))
                fetchAgenda(sel);
            buildAgenda(sel, sel, "");
        }
        if (old.m != sel.m || old.y != sel.y || !month->counts.size())
            refreshGrid();
        else
        {
            month->sel = sel;
            month->today = today;
            month->drawView();
        }
        updateTitles();
    }

    void focusPane(TView *p)
    {
        p->select();
        updateTitles();
    }

    // ---- events
    const Occ *eventShown() const
    {
        const AgRow *r = agenda->current();
        return r && r->kind == AgRow::event ? &r->occ : nullptr;
    }

    void newEvent()
    {
        EvForm f;
        f.date = ymdIso(sel);
        f.remind = gSet.remind;
        if (eventDialog("New event", f, 0) != cmOK)
            return;
        std::string body, err;
        if (!calAsk("add-event" + eventArgs(f), body, err))
        {
            notify(err, noteError);
            return;
        }
        notify("Added: " + f.title);
        Ymd d;
        if (ymdParse(trimmed(f.date), d))
            sel = d;
        else if (calAsk("norm-date " + shq(f.date), body, err))
            ymdParse(trimmed(body), sel);
        refreshAll(true);
    }

    bool formFor(const std::string &uid, EvForm &f)
    {
        std::string body, err;
        if (!calAsk("show " + shq(uid), body, err))
        {
            notify(err, noteError);
            return false;
        }
        auto rows = rowsOf(body);
        if (rows.empty() || rows[0].size() < 13 || rows[0][0] != "event")
            return false;
        const auto &r = rows[0];
        f.uid = r[1];
        f.title = r[2];
        f.location = r[3];
        f.date = r[4];
        f.start = r[5];
        f.end = r[6];
        f.days = r[7];
        f.repeat = repeatIndex(r[8]);
        f.interval = r[9];
        f.until = r[10];
        f.remind = r[11].empty() ? "off" : r[11];
        f.notes = r[12];
        return true;
    }

    void editEvent()
    {
        const Occ *o = eventShown();
        if (!o)
        {
            flashStatus("Pick an event in the agenda first (Tab, then the arrows).");
            return;
        }
        EvForm f;
        if (!formFor(o->uid, f))
            return;
        std::string uid = f.uid;
        ushort r = eventDialog("Edit event", f, 1);
        std::string body, err;
        if (r == cmNo)
        {
            deleteEvent();
            return;
        }
        if (r != cmOK)
            return;
        if (!calAsk("set-event " + shq(uid) + eventArgs(f), body, err))
        {
            notify(err, noteError);
            return;
        }
        refreshAll(false);
    }

    void deleteEvent()
    {
        const Occ *o = eventShown();
        if (!o)
            return;
        std::string summary = o->summary, uid = o->uid;
        bool repeats = o->repeat != "none" && !o->repeat.empty();
        std::string q = "Delete \"" + summary + "\"" + (repeats ? " and all its repeats?" : "?");
        if (messageBox(q, mfConfirmation | mfYesButton | mfNoButton) != cmYes)
            return;
        std::string body, err;
        if (!calAsk("rm " + shq(uid), body, err))
            notify(err, noteError);
        else
            notify("Deleted: " + summary);
        refreshAll(false);
    }

    // ---- to-dos
    void newTodo()
    {
        TodoForm f;
        if (todoDialog("New to-do", f, 0) != cmOK)
            return;
        std::string body, err;
        if (!calAsk("add-todo" + todoArgs(f) + " -- " + shq(f.title), body, err))
        {
            notify(err, noteError);
            return;
        }
        refreshTodos();
        refreshGrid();
    }

    void editTodo()
    {
        const TodoItem *t = todo->current();
        if (!t)
        {
            newTodo();
            return;
        }
        std::string body, err, uid = t->uid;
        if (!calAsk("show " + shq(uid), body, err))
        {
            notify(err, noteError);
            return;
        }
        auto rows = rowsOf(body);
        if (rows.empty() || rows[0].size() < 8)
            return;
        TodoForm f;
        f.uid = uid;
        f.title = rows[0][2];
        f.due = rows[0][3];
        f.prio = rows[0][4];
        f.cats = rows[0][6];
        f.notes = rows[0][7];
        ushort r = todoDialog("Edit to-do", f, 1);
        if (r == cmNo)
        {
            deleteTodo();
            return;
        }
        if (r != cmOK)
            return;
        if (!calAsk("set-todo " + shq(uid) + todoArgs(f) + opt("summary", f.title), body, err))
        {
            notify(err, noteError);
            return;
        }
        refreshTodos();
    }

    void toggleTodo()
    {
        const TodoItem *t = todo->current();
        if (!t)
            return;
        std::string body, err;
        if (!calAsk("done " + shq(t->uid) + (t->done ? " --undo" : ""), body, err))
            notify(err, noteError);
        refreshTodos();
    }

    void deleteTodo()
    {
        const TodoItem *t = todo->current();
        if (!t)
            return;
        if (messageBox("Delete \"" + t->summary + "\"?", mfConfirmation | mfYesButton | mfNoButton) != cmYes)
            return;
        std::string body, err;
        if (!calAsk("rm " + shq(t->uid), body, err))
            notify(err, noteError);
        refreshTodos();
    }

    // The item the Share menu works on: the to-do when that pane has the focus, else the event shown.
    bool shareTarget(std::string &uid, std::string &date, std::string &what) const
    {
        if (current == todo && todo->current())
        {
            uid = todo->current()->uid;
            what = todo->current()->summary;
            date.clear();
            return true;
        }
        if (const Occ *o = eventShown())
        {
            uid = o->uid;
            date = o->date;
            what = o->summary;
            return true;
        }
        return false;
    }

    TView *focusedPane() const { return current; }

    void gotoToday() { setSel(ymdToday(), false); }

    void handleEvent(TEvent &e) override
    {
        if (e.what == evKeyDown && (e.keyDown.keyCode == kbTab || e.keyDown.keyCode == kbShiftTab))
        {
            int at = 0;
            for (int i = 0; i < 3; ++i)
                if (panes[i] == current)
                    at = i;
            at = (at + (e.keyDown.keyCode == kbTab ? 1 : 2)) % 3;
            focusPane(panes[at]);
            clearEvent(e);
            return;
        }
        TWindow::handleEvent(e);
        if (e.what == evBroadcast && e.message.command == cmCalPane)
        {
            updateTitles();
            clearEvent(e);
        }
    }

    void changeBounds(const TRect &bounds) override
    {
        TWindow::changeBounds(bounds);
        Rects R = paneRects();
        monthTitle->changeBounds(R.monthTitle);
        month->changeBounds(R.month);
        todoTitle->changeBounds(R.todoTitle);
        todoHead->changeBounds(R.todoHead);
        todo->changeBounds(R.todo);
        todoVsb->changeBounds(R.todoVsb);
        vdiv->changeBounds(R.vbar);
        agendaTitle->changeBounds(R.agendaTitle);
        agenda->changeBounds(R.agenda);
        agendaVsb->changeBounds(R.agendaVsb);
    }

    TColorAttr mapColor(uchar index) override { return cyanWindowColor(index, this); }
};

TCalWindow *window()
{
    if (!gWin)
        TProgram::deskTop->insert(new TCalWindow(TProgram::deskTop->getExtent()));
    return gWin;
}

// ---------------------------------------------------- incoming from others ---
void incomingRefresh()
{
    std::string body, err;
    gIncoming = 0;
    if (calAsk("pending", body, err))
        gIncoming = (int)rowsOf(body).size();
    if (TProgram::statusLine)
        TProgram::statusLine->drawView(); // the "N incoming" at its end
}

// Calendar > Incoming...: what tvmail or tvchat asked to add, one at a time, each with its
// dialog filled in; Add keeps it, Discard drops it, Skip leaves it for later.
void incoming()
{
    std::string body, err;
    if (!calAsk("pending", body, err))
    {
        notify(err, noteError);
        return;
    }
    auto list = rowsOf(body);
    if (list.empty())
    {
        notify("Nothing is waiting: tvmail's Message > Add to calendar and tvchat's Chat > Add to calendar leave requests here.");
        return;
    }
    int added = 0;
    for (auto &p : list)
    {
        if (p.size() < 5)
            continue;
        std::string id = p[0], kind = p[1], source = p[2];
        std::string got;
        if (!calAsk("pending-get " + shq(id), got, err))
            continue;
        auto rows = rowsOf(got);
        if (rows.empty())
            continue;
        const auto &r = rows[0];
        std::string cap = "From " + source;
        ushort res;
        std::string out;
        if (kind == "event" && r.size() >= 13)
        {
            EvForm f;
            f.title = r[2];
            f.location = r[3];
            f.date = r[4];
            f.start = r[5];
            f.end = r[6];
            f.remind = gSet.remind;
            f.notes = r[12];
            res = eventDialog(cap.c_str(), f, 2);
            if (res == cmOK && !calAsk("add-event" + eventArgs(f), out, err))
            {
                notify(err, noteError);
                res = cmCancel;
            }
        }
        else if (kind == "todo" && r.size() >= 8)
        {
            TodoForm f;
            f.title = r[2];
            f.due = r[3];
            f.prio = r[4];
            f.cats = r[6];
            f.notes = r[7];
            res = todoDialog(cap.c_str(), f, 2);
            if (res == cmOK && !calAsk("add-todo" + todoArgs(f) + " -- " + shq(f.title), out, err))
            {
                notify(err, noteError);
                res = cmCancel;
            }
        }
        else
            continue;
        if (res == cmOK)
            ++added;
        if (res == cmOK || res == cmNo)
            calAsk("pending-drop " + shq(id), out, err);
        if (res == cmCancel && p != list.back() &&
            messageBox("Look at the next one?", mfConfirmation | mfYesButton | mfNoButton) != cmYes)
            break;
    }
    incomingRefresh();
    if (added)
        notify(std::to_string(added) + " added from other apps.");
    if (gWin)
        gWin->refreshAll(false);
}

// ------------------------------------------------------------ menu actions ---
void gotoDate()
{
    char buf[40] = "";
    if (inputBox("Go to date", "~D~ate (2026-11-05, 11-05, tomorrow, +30, fri)", buf, sizeof(buf) - 1) != cmOK ||
        trimmed(buf).empty())
        return;
    std::string body, err;
    Ymd d;
    if (!calAsk("norm-date " + shq(trimmed(buf)), body, err))
        notify(err, noteError);
    else if (ymdParse(trimmed(body), d))
        window()->setSel(d, false);
}

void findDialog()
{
    char buf[100] = "";
    if (inputBox("Find", "~T~ext (in titles, places and notes; a year either side)", buf, sizeof(buf) - 1) != cmOK ||
        trimmed(buf).empty())
        return;
    std::string body, err;
    if (!calAsk("find " + shq(trimmed(buf)), body, err))
    {
        notify(err, noteError);
        return;
    }
    std::string out;
    for (auto &f : rowsOf(body))
    {
        if (f[0] == "event" && f.size() >= 9)
        {
            Ymd d;
            std::string day = ymdParse(f[1], d) ? niceDate(d) : f[1];
            out += day + "  " + (f[2].empty() ? "all day" : f[2] + (f[3].empty() ? "" : "-" + f[3])) + "  " + f[5] +
                   (f[6].empty() ? "" : " @ " + f[6]) + "\n";
        }
        else if (f[0] == "todo" && f.size() >= 7)
            out += std::string(f[2] == "done" ? "[x] " : "[ ] ") + f[5] + (f[4].empty() ? "" : "   (due " + f[4] + ")") + "\n";
    }
    if (out.empty())
    {
        notify("Nothing found for \"" + trimmed(buf) + "\".");
        return;
    }
    TRect r = TProgram::deskTop->getExtent();
    r.grow(-6, -3);
    TProgram::deskTop->insert(newTextWindow(r, "Found (Esc closes)", out));
}

void mailInvite()
{
    std::string uid, date, what;
    TCalWindow *w = window();
    if (!w->shareTarget(uid, date, what))
    {
        flashStatus("Pick an event or a to-do first.");
        return;
    }
    std::string body, err;
    if (!calAsk("has-tvmail", body, err) || body != "yes\n")
    {
        notify("tvmail is not installed here: tvcal hands the invitation to tvmail to send. "
               "Share > Export .ics file... writes it to a file instead.",
               noteWarn);
        return;
    }
    char buf[200] = "";
    if (inputBox("Mail invitation", "~T~o (an address)", buf, sizeof(buf) - 1) != cmOK || trimmed(buf).empty())
        return;
    flashStatus("Sending through tvmail...");
    // one-shot, not the serve co-process: sending takes longer than its read timeout
    std::string out = shCapture("tvcal-backend mail-event " + shq(uid) + " " + shq(trimmed(buf)) + " 2>&1");
    while (!out.empty() && isspace((unsigned char)out.back()))
        out.pop_back();
    notify(out.empty() ? "tvmail did not answer." : out, out.compare(0, 4, "sent") == 0 ? noteInfo : noteError);
}

void shareInChat()
{
    std::string uid, date, what;
    if (!window()->shareTarget(uid, date, what))
    {
        flashStatus("Pick an event or a to-do first.");
        return;
    }
    std::string body, err;
    if (!calAsk("describe " + shq(uid) + (date.empty() ? "" : " " + shq(date)), body, err))
    {
        notify(err, noteError);
        return;
    }
    while (!body.empty() && isspace((unsigned char)body.back()))
        body.pop_back();
    if (!peerRequest("tvchat", "share " + body, err))
        notify(err, noteError);
    else
        notify("Asked tvchat to share this in chat. You say yes there (Chat > Share from tvmail...), "
               "within 10 minutes; only the title, day, time and place go.");
}

void importIcs()
{
    char buf[250] = "";
    if (inputBox("Import .ics file", "~F~ile", buf, sizeof(buf) - 1) != cmOK || trimmed(buf).empty())
        return;
    std::string body, err;
    if (!calAsk("import " + shq(trimmed(buf)), body, err))
        notify(err, noteError);
    else
        notify(trimmed(body));
    window()->refreshAll(false);
}

void exportIcs()
{
    char buf[250] = "tvcal.ics";
    if (inputBox("Export .ics file", "~F~ile to write (the whole calendar)", buf, sizeof(buf) - 1) != cmOK ||
        trimmed(buf).empty())
        return;
    std::string out = shCapture("tvcal-backend ics > " + shq(trimmed(buf)) + " 2>&1");
    while (!out.empty() && isspace((unsigned char)out.back()))
        out.pop_back();
    notify(out.empty() ? "Wrote " + trimmed(buf) : out, out.empty() ? noteInfo : noteError);
}

// Floyd, the assistant: a question or a !command; his answer in a box.  The same Floyd
// answers in tvmail and tvchat, and his !todo is this program's to-do list.
void floydAsk()
{
    TRect r = TProgram::deskTop->getExtent();
    r.grow(-6, -3);
    TProgram::deskTop->insert(newFloydWindow(r));
}

// ------------------------------------------------------------- the polls ---
std::chrono::steady_clock::time_point gLastRequests, gLastDue, gLastRefresh;
std::string gDay;

void reminders()
{
    std::string body, err;
    if (!calAsk("due", body, err))
        return;
    for (auto &f : rowsOf(body))
    {
        std::string msg;
        if (f[0] == "event" && f.size() >= 6)
        {
            int mins = atoi(f[5].c_str());
            msg = (f[3].empty() ? "today: " : f[3] + " ") + f[4] +
                  (f[3].empty() ? "" : mins > 0 ? " - in " + std::to_string(mins) + " min" : " - now");
        }
        else if (f[0] == "todo" && f.size() >= 5)
            msg = std::string("to do ") + (f[4] == "overdue" ? "(overdue since " + f[2] + "): " : "today: ") + f[3];
        else
            continue;
        notifyAlert(msg, noteWarn);
        spoolSend(noteInfo, msg, 900, true); // the other apps show it too, whichever you are looking at
    }
}
} // namespace

// ------------------------------------------------------------- the public ---
void calOpenWindow()
{
    loadSettings();
    window();
    incomingRefresh();
    gLastRequests = gLastDue = gLastRefresh = std::chrono::steady_clock::now();
    gDay = ymdIso(ymdToday());
    reminders();
}

int calIncomingCount()
{
    return gIncoming;
}

std::string calCurrentDate()
{
    return gWin ? gWin->dateShown() : ymdIso(ymdToday());
}

void calWindowOnReload()
{
    if (gWin)
        gWin->refreshAll(false);
}

void calSettingsChanged()
{
    loadSettings();
    if (gWin)
        gWin->refreshAll(false);
}

// Every second or so, from the app's idle (never while a menu or dialog is up): requests every
// 3 s, reminders every 30 s, everything re-read every 60 s and at midnight.
void calPoll()
{
    auto now = std::chrono::steady_clock::now();
    if (now - gLastRequests >= std::chrono::seconds(3))
    {
        gLastRequests = now;
        std::string body, err;
        if (calAsk("requests", body, err) && !body.empty())
        {
            for (auto &f : rowsOf(body))
                if (f.size() >= 2 && f[0] == "notice")
                    notifyAlert(f[1]);
            incomingRefresh();
        }
    }
    if (now - gLastDue >= std::chrono::seconds(30))
    {
        gLastDue = now;
        reminders();
    }
    std::string day = ymdIso(ymdToday());
    if (now - gLastRefresh >= std::chrono::seconds(60) || day != gDay)
    {
        gLastRefresh = now;
        gDay = day;
        if (gWin)
            gWin->refreshAll(false);
    }
}

void calWindowRefresh()
{
    if (gWin)
        gWin->refreshAll(false);
}

bool calCommand(ushort cmd)
{
    // cmCalPane is a broadcast, not a command; cmFloyd (2233) lies above it
    if (cmd <= cmCalPoll || cmd == cmCalPane || cmd > cmFloyd)
        return false;
    TCalWindow *w = window();
    switch (cmd)
    {
    case cmCalNewEvent:
        w->newEvent();
        break;
    case cmCalEditEvent:
        w->editEvent();
        break;
    case cmCalDelEvent:
        w->deleteEvent();
        break;
    case cmCalToday:
        w->gotoToday();
        break;
    case cmCalGoto:
        gotoDate();
        break;
    case cmCalFind:
        findDialog();
        break;
    case cmCalIncoming:
        incoming();
        break;
    case cmCalNewTodo:
        w->newTodo();
        break;
    case cmCalEditTodo:
        w->editTodo();
        break;
    case cmCalToggleTodo:
        w->toggleTodo();
        break;
    case cmCalDelTodo:
        w->deleteTodo();
        break;
    case cmCalShowDone:
        w->setShowDone(!w->showsDone());
        break;
    case cmCalMail:
        mailInvite();
        break;
    case cmCalShareChat:
        shareInChat();
        break;
    case cmFloyd:
        floydAsk();
        break;
    case cmCalImport:
        importIcs();
        break;
    case cmCalExport:
        exportIcs();
        break;
    default:
        return false;
    }
    return true;
}
