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

// Notes: a title and free text, newest first.  The list shows the day it was changed, the title and the
// start of the text; the whole text is in the pane below.
struct NoteItem
{
    std::string uid, date, title, cats, preview;
};

const TableCols kNoteCols = {{"Date", 5}, {"Note", 0}};

class TNotesPane : public TCalList
{
public:
    std::vector<NoteItem> items;

    TNotesPane(const TRect &b, TScrollBar *vsb) : TCalList(b, vsb) {}

    void setItems(std::vector<NoteItem> v, const std::string &keepUid)
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
    const NoteItem *current() const { return focused >= 0 && focused < (short)items.size() ? &items[focused] : nullptr; }

    void draw() override
    {
        drawTableList(*this, kNoteCols, [this](short item) -> std::vector<std::string>
                      {
            if (item >= (short)items.size())
                return {};
            const NoteItem &n = items[item];
            return {n.date.size() >= 10 ? n.date.substr(5) : "", n.title + (n.preview.empty() ? "" : "  - " + n.preview)}; });
    }
    void getText(char *dest, short item, short maxLen) override
    {
        putText(dest, item >= 0 && item < (short)items.size() ? items[item].title : std::string(), maxLen);
    }
};

// A table of text rows: the Inbox (what other apps asked to add) and the Reminders (what fires next).
// `data` holds each row as the backend gave it; `cells` what the table shows.
class TRowsPane : public TCalList
{
    TableCols cols;

public:
    std::vector<std::vector<std::string>> cells, data;

    TRowsPane(const TRect &b, TScrollBar *vsb, TableCols c) : TCalList(b, vsb), cols(std::move(c)) {}

    void setRows(std::vector<std::vector<std::string>> shown, std::vector<std::vector<std::string>> raw, const std::string &keepKey)
    {
        cells = std::move(shown);
        data = std::move(raw);
        setRange((short)cells.size());
        short at = 0;
        for (size_t i = 0; i < data.size(); ++i)
            if (!keepKey.empty() && !data[i].empty() && key(data[i]) == keepKey)
                at = (short)i;
        TListViewer::focusItem(at);
        drawView();
    }
    // what identifies a row across refreshes: the id (Inbox) or the uid and day (Reminders)
    static std::string key(const std::vector<std::string> &d) { return d.size() >= 7 ? d[2] + d[3] : d.empty() ? std::string() : d[0]; }
    const std::vector<std::string> *current() const
    {
        return focused >= 0 && focused < (short)data.size() ? &data[focused] : nullptr;
    }
    void draw() override
    {
        drawTableList(*this, cols, [this](short item) -> std::vector<std::string>
                      { return item < (short)cells.size() ? cells[item] : std::vector<std::string>(); });
    }
    void getText(char *dest, short item, short maxLen) override
    {
        putText(dest, item >= 0 && item < (short)cells.size() && !cells[item].empty() ? cells[item].back() : std::string(), maxLen);
    }
};

const TableCols kInboxCols = {{"From", 8}, {"Day", 5}, {"Request", 0}};
const TableCols kRemCols = {{"Fires", 11}, {"Day", 5}, {"Reminder", 0}};

// The services, top left: what the other panes show.  A count at the right of each name.
struct Service
{
    const char *name;
    int count = -1; // -1: none shown
};

class TServicePane : public TCalList
{
public:
    std::vector<Service> items;

    TServicePane(const TRect &b) : TCalList(b, nullptr)
    {
        items = {{"Calendar"}, {"To-do"}, {"Notes"}, {"Inbox"}, {"Reminders"}};
        setRange((short)items.size());
    }
    void getText(char *dest, short item, short maxLen) override
    {
        if (item < 0 || item >= (short)items.size())
        {
            putText(dest, std::string(), maxLen);
            return;
        }
        std::string name = items[item].name, n = items[item].count >= 0 ? std::to_string(items[item].count) : "";
        // fitCols pads on the right to the width it is given
        // (a column short of the width: the list viewer keeps maxLen - 1 characters)
        putText(dest, n.empty() ? fitCols(name, size.x - 1) : fitCols(name, std::max(1, size.x - (int)n.size() - 2)) + " " + n, maxLen);
    }
};

// The item shown whole, bottom right: read-only text, the notes in full.  Enter on a list edits.
class TCalDetail : public TMailText
{
public:
    TCalDetail(const TRect &b, TScrollBar *v) : TMailText(b, nullptr, v, {std::string()}, true) {}
    void setState(ushort aState, Boolean enable) override
    {
        TMailText::setState(aState, enable);
        if (enable && (aState & sfFocused) && owner)
            message(owner, evBroadcast, cmCalPane, this);
    }
    TColorAttr mapColor(uchar i) override
    {
        return i == 1 ? cNorm() : (i == 2 ? cHi() : TView::mapColor(i));
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

// A multi-line text box with its own scroll bar, in a dialog.  Enter makes a new line; Tab leaves it.
TMemo *addMemo(TDialog *d, const TRect &r, const std::string &init, int maxLen = 6000)
{
    TScrollBar *vsb = new TScrollBar(TRect(r.b.x, r.a.y, r.b.x + 1, r.b.y));
    d->insert(vsb);
    TMemo *m = new TMemo(r, nullptr, vsb, nullptr, (ushort)maxLen);
    d->insert(m);
    std::vector<char> data(m->dataSize(), 0);
    std::string t = init.substr(0, (size_t)std::min<size_t>(init.size(), (size_t)maxLen - 1));
    ((TMemoData *)data.data())->length = (ushort)t.size();
    memcpy(((TMemoData *)data.data())->buffer, t.data(), t.size());
    m->setData(data.data());
    return m;
}

// What was typed in a memo: lines end in \n, whatever the editor used, and blank ends are cut.
std::string memoText(TMemo *m)
{
    std::vector<char> data(m->dataSize(), 0);
    m->getData(data.data());
    TMemoData *md = (TMemoData *)data.data();
    std::string t(md->buffer, md->length), out;
    for (size_t i = 0; i < t.size(); ++i)
    {
        if (t[i] == '\r')
        {
            out += '\n';
            if (i + 1 < t.size() && t[i + 1] == '\n')
                ++i;
        }
        else
            out += t[i];
    }
    return trimmed(out);
}

// A request is one line: a new line in the text is sent as \n and a backslash as \\ (--notesx).
std::string escNl(const std::string &s)
{
    std::string o;
    for (char c : s)
        o += c == '\\' ? "\\\\" : c == '\n' ? "\\n" : std::string(1, c);
    return o;
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
    TDialog *d = new TDialog(TRect(0, 0, 74, 27), caption);
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
    d->insert(new TStaticText(TRect(36, 12, 71, 16), "Dates: 2026-11-05, 11-05, tomorrow,\n+3, fri.  Times: 14:30.  Empty start\n= all day.  A repeat changes the series."));
    TMemo *notes = addMemo(d, TRect(3, 18, 70, 24), f.notes);
    d->insert(new TLabel(TRect(3, 17, 40, 18), "No~t~es (Enter: new line, Tab: leave)", notes));
    int y = 24;
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
    f.notes = memoText(notes);
    TObject::destroy(d);
    return r;
}

struct TodoForm
{
    std::string uid, title, due, prio = "0", cats, notes;
};

ushort todoDialog(const char *caption, TodoForm &f, int mode)
{
    TDialog *d = new TDialog(TRect(0, 0, 74, 23), caption);
    d->options |= ofCentered;
    TInputLine *title = addField(d, 3, 2, 66, "~T~o do", 300, f.title);
    TInputLine *due = addField(d, 3, 5, 12, "~D~ue", 20, f.due);
    TInputLine *prio = addField(d, 18, 5, 4, "~P~riority", 2, f.prio == "0" ? "" : f.prio);
    TInputLine *cats = addField(d, 30, 5, 38, "~C~ategories", 100, f.cats);
    TMemo *notes = addMemo(d, TRect(3, 9, 70, 17), f.notes);
    d->insert(new TLabel(TRect(3, 8, 40, 9), "~N~otes (Enter: new line, Tab: leave)", notes));
    d->insert(new TStaticText(TRect(3, 17, 70, 18), "Priority 1 (highest) to 9, empty for none.  Due: 2026-11-05, 11-05, tomorrow, +3, fri."));
    int y = 19;
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
    f.notes = memoText(notes);
    TObject::destroy(d);
    return r;
}

struct NoteForm
{
    std::string uid, title, cats, notes;
};

// mode 0: a new note; 1: editing (+ Delete: cmNo).  The text box is the point of a note.
ushort noteDialog(const char *caption, NoteForm &f, int mode)
{
    TDialog *d = new TDialog(TRect(0, 0, 76, 24), caption);
    d->options |= ofCentered;
    TInputLine *title = addField(d, 3, 2, 68, "~T~itle (empty: the first line of the text)", 200, f.title);
    TInputLine *cats = addField(d, 3, 5, 68, "~C~ategories (separated by commas)", 100, f.cats);
    TMemo *memo = addMemo(d, TRect(3, 9, 71, 19), f.notes);
    d->insert(new TLabel(TRect(3, 8, 20, 9), "~N~otes", memo));
    d->insert(new TStaticText(TRect(3, 19, 72, 20), "Enter starts a new line.  Tab leaves the text.  Alt-O saves, Esc cancels."));
    d->insert(new TButton(TRect(10, 21, 22, 23), "~O~K", cmOK, bfDefault));
    d->insert(new TButton(TRect(26, 21, 38, 23), "Cancel", cmCancel, bfNormal));
    if (mode == 1)
        d->insert(new TButton(TRect(42, 21, 54, 23), "~D~elete", cmNo, bfNormal));
    (f.notes.empty() ? title : (TView *)memo)->select();
    ushort r = TProgram::deskTop->execView(d);
    f.title = text(title);
    f.cats = text(cats);
    f.notes = memoText(memo);
    TObject::destroy(d);
    return r;
}

std::string noteArgs(const NoteForm &f, bool creating)
{
    std::string a = opt("cats", f.cats) + opt("notesx", escNl(f.notes));
    if (creating)
        return "add-note" + a + (f.title.empty() ? "" : " -- " + shq(f.title));
    return a + opt("summary", f.title);
}

std::string eventArgs(const EvForm &f)
{
    return opt("date", f.date) + opt("start", f.start) + opt("end", f.end) + opt("days", f.days) +
           opt("location", f.location) + opt("notesx", escNl(f.notes)) + opt("repeat", kRepeat[f.repeat]) +
           opt("interval", f.interval) + opt("until", f.until) + opt("remind", f.remind) + " -- " + shq(f.title);
}

std::string todoArgs(const TodoForm &f)
{
    return opt("due", f.due) + opt("prio", f.prio) + opt("cats", f.cats) + opt("notesx", escNl(f.notes));
}

int repeatIndex(const std::string &r)
{
    for (int i = 0; i < 5; ++i)
        if (r == kRepeat[i])
            return i;
    return 0;
}

// ---------------------------------------- an item as text (the details pane) ---
// A rule, then the free text as it was written.
void addNotes(std::vector<std::string> &out, const std::string &notes)
{
    if (notes.empty())
        return;
    out.push_back(std::string());
    out.push_back(std::string(30, '-'));
    for (const std::string &l : splitLines(notes))
        out.push_back(l);
}

// An event from a `show` row (13 fields), on the day `date` (an occurrence), from `start` to `end`.
void eventLines(std::vector<std::string> &out, const std::vector<std::string> &f, const std::string &date, const std::string &start,
                const std::string &end)
{
    Ymd d;
    out.push_back(f[2]);
    out.push_back(std::string());
    std::string when = (ymdParse(date, d) ? niceDate(d) : date) + "  " +
                       (start.empty() ? std::string("all day") : start + (end.empty() ? "" : "-" + end));
    if (atoi(f[7].c_str()) > 1)
        when += "  (" + f[7] + " days)";
    out.push_back("When:      " + when);
    if (!f[3].empty())
        out.push_back("Where:     " + f[3]);
    if (f[8] != "none" && !f[8].empty())
        out.push_back("Repeats:   " + f[8] + (f[9] != "1" && !f[9].empty() ? ", every " + f[9] : "") +
                      (f[10].empty() ? "" : ", until " + f[10]));
    if (!f[11].empty())
        out.push_back("Reminder:  " + f[11] + " min before");
    addNotes(out, f[12]);
}

// A to-do from a `show` row (8 fields).
void todoLines(std::vector<std::string> &out, const std::vector<std::string> &f, bool done)
{
    out.push_back(f[2]);
    out.push_back(std::string());
    out.push_back(std::string("Status:    ") + (done ? "done" : "not done"));
    if (!f[3].empty())
        out.push_back("Due:       " + f[3]);
    if (f[4] != "0" && !f[4].empty())
        out.push_back("Priority:  " + f[4]);
    if (!f[6].empty())
        out.push_back("Categories: " + f[6]);
    addNotes(out, f[7]);
}

// ------------------------------------------------------ a request from another app ---
// Its dialog, filled in: Add (cmOK) keeps it, Discard (cmNo) drops it, Skip (cmCancel) leaves it for later.
// 0 when the request is gone or not understood.  `p` is a `pending` row: id, kind, source, summary, day.
ushort incomingHandle(const std::vector<std::string> &p)
{
    if (p.size() < 5)
        return 0;
    std::string id = p[0], kind = p[1], source = p[2], got, err, out;
    if (!calAsk("pending-get " + shq(id), got, err))
        return 0;
    auto rows = rowsOf(got);
    if (rows.empty())
        return 0;
    const auto &r = rows[0];
    std::string cap = "From " + source;
    ushort res;
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
        return 0;
    if (res == cmOK || res == cmNo)
        calAsk("pending-drop " + shq(id), out, err);
    return res;
}

// ------------------------------------------------------------ the window ---
class TCalWindow;
TCalWindow *gWin = nullptr;
int gIncoming = 0;

class TCalWindow : public TWindow
{
    TServicePane *svc = nullptr;
    TMonthPane *month = nullptr;
    TAgendaPane *agenda = nullptr;
    TTodoPane *todo = nullptr;
    TNotesPane *notes = nullptr;
    TRowsPane *inbox = nullptr, *rem = nullptr;
    TCalDetail *detail = nullptr;
    TPaneTitle *svcTitle = nullptr, *monthTitle = nullptr, *listTitle = nullptr, *detailTitle = nullptr;
    TTableHead *todoHead = nullptr, *noteHead = nullptr, *inboxHead = nullptr, *remHead = nullptr;
    TDivider *vdiv = nullptr;
    TScrollBar *agendaVsb = nullptr, *todoVsb = nullptr, *noteVsb = nullptr, *inboxVsb = nullptr, *remVsb = nullptr,
                *detailVsb = nullptr;
    int service = 0; // 0 Calendar, 1 To-do, 2 Notes, 3 Inbox, 4 Reminders

    Ymd sel, today;
    std::vector<Occ> agendaOcc; // occurrences from agLo to agHi
    Ymd agLo, agHi;
    bool haveAgenda = false;
    bool showDone = false;
    bool settling = false; // a pane was moved by code: it does not move the others again

    int leftWidth() const { return std::max(26, std::min(30, size.x / 4)); }

    // Left: Services, and under them the month (Calendar only).  Right: the items of the service
    // (the agenda or the to-do list, one on top of the other) over the item shown whole.
    struct Rects
    {
        TRect svcTitle, svc, monthTitle, month, vbar, listTitle, agenda, agendaVsb, todoHead, todo, todoVsb, detailTitle, detail,
            detailVsb;
    };
    Rects paneRects() const
    {
        int W = size.x, H = size.y, L = leftWidth();
        int n = svc ? (int)svc->items.size() : 2;
        int sy = 1 + (H - 2) * 2 / 5;
        if (sy < 6)
            sy = 6; // title + a few rows + the detail title
        if (sy > H - 5)
            sy = H - 5;
        Rects r;
        r.svcTitle = TRect(1, 1, 1 + L, 2);
        r.svc = TRect(1, 2, 1 + L, 2 + n);
        r.monthTitle = TRect(1, 3 + n, 1 + L, 4 + n);
        r.month = TRect(1, 4 + n, 1 + L, 11 + n);
        r.vbar = TRect(1 + L, 1, 2 + L, H - 1);
        r.listTitle = TRect(2 + L, 1, W - 1, 2);
        r.agenda = TRect(2 + L, 2, W - 2, sy);
        r.agendaVsb = TRect(W - 2, 2, W - 1, sy);
        r.todoHead = TRect(2 + L, 2, W - 2, 3);
        r.todo = TRect(2 + L, 3, W - 2, std::max(4, sy));
        r.todoVsb = TRect(W - 2, 3, W - 1, std::max(4, sy));
        r.detailTitle = TRect(2 + L, sy, W - 1, sy + 1);
        r.detail = TRect(2 + L, sy + 1, W - 2, H - 1);
        r.detailVsb = TRect(W - 2, sy + 1, W - 1, H - 1);
        return r;
    }

public:
    TCalWindow(const TRect &bounds)
        : TWindowInit(&TCalWindow::initFrame), TWindow(bounds, "tvcal", wnNoNumber)
    {
        palette = wpCyanWindow;
        flags &= ~(wfClose | wfZoom | wfMove);
        growMode = gfGrowHiX | gfGrowHiY;
        today = sel = ymdToday();

        svc = new TServicePane(TRect(0, 0, 1, 1));
        Rects R = paneRects();
        svcTitle = new TPaneTitle(R.svcTitle, "Services");
        svc->changeBounds(R.svc);
        monthTitle = new TPaneTitle(R.monthTitle, "Month");
        month = new TMonthPane(R.month);
        todoHead = new TTableHead(R.todoHead, kTodoCols);
        todoVsb = new TScrollBar(R.todoVsb);
        todo = new TTodoPane(R.todo, todoVsb);
        noteHead = new TTableHead(R.todoHead, kNoteCols);
        noteVsb = new TScrollBar(R.todoVsb);
        notes = new TNotesPane(R.todo, noteVsb);
        inboxHead = new TTableHead(R.todoHead, kInboxCols);
        inboxVsb = new TScrollBar(R.todoVsb);
        inbox = new TRowsPane(R.todo, inboxVsb, kInboxCols);
        remHead = new TTableHead(R.todoHead, kRemCols);
        remVsb = new TScrollBar(R.todoVsb);
        rem = new TRowsPane(R.todo, remVsb, kRemCols);
        vdiv = new TDivider(R.vbar, true);
        listTitle = new TPaneTitle(R.listTitle, "Agenda");
        agendaVsb = new TScrollBar(R.agendaVsb);
        agenda = new TAgendaPane(R.agenda, agendaVsb);
        detailTitle = new TPaneTitle(R.detailTitle, "Event");
        detailVsb = new TScrollBar(R.detailVsb);
        detail = new TCalDetail(R.detail, detailVsb);
        for (TView *v : {(TView *)svc, (TView *)month, (TView *)todoHead, (TView *)todo, (TView *)agenda, (TView *)svcTitle,
                         (TView *)monthTitle, (TView *)listTitle, (TView *)detailTitle, (TView *)vdiv, (TView *)todoVsb,
                         (TView *)agendaVsb, (TView *)detail, (TView *)detailVsb, (TView *)noteHead, (TView *)notes,
                         (TView *)noteVsb, (TView *)inboxHead, (TView *)inbox,
                         (TView *)inboxVsb, (TView *)remHead, (TView *)rem, (TView *)remVsb})
            v->growMode = 0; // placed by changeBounds()

        svc->moved = [this] { useService(svc->focused); };
        svc->enter = [this] { focusPane(listPane()); };
        svc->pick = [this] { focusPane(listPane()); };
        svc->newItem = [this]
        {
            if (service <= 2)
                service == 0 ? newEvent() : service == 1 ? newTodo() : newNote();
        };

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
            showDetail();
        };
        agenda->enter = [this] { editEvent(); };
        agenda->pick = [this] { editEvent(); };
        agenda->newItem = [this] { newEvent(); };
        agenda->del = [this] { deleteEvent(); };

        todo->moved = [this] { showDetail(); };
        todo->enter = [this] { editTodo(); };
        todo->pick = [this] { toggleTodo(); };
        todo->newItem = [this] { newTodo(); };
        todo->del = [this] { deleteTodo(); };

        notes->moved = [this] { showDetail(); };
        notes->enter = [this] { editNote(); };
        notes->pick = [this] { editNote(); };
        notes->newItem = [this] { newNote(); };
        notes->del = [this] { deleteNote(); };

        inbox->moved = [this] { showDetail(); };
        inbox->enter = [this] { inboxAct(); };
        inbox->pick = [this] { inboxAct(); };
        inbox->del = [this] { inboxDiscard(); };

        rem->moved = [this] { showDetail(); };
        rem->enter = [this] { remGo(); };
        rem->pick = [this] { remGo(); };

        insert(vdiv);
        insert(svcTitle);
        insert(monthTitle);
        insert(listTitle);
        insert(detailTitle);
        insert(todoHead);
        insert(todoVsb);
        insert(noteHead);
        insert(noteVsb);
        insert(inboxHead);
        insert(inboxVsb);
        insert(remHead);
        insert(remVsb);
        insert(agendaVsb);
        insert(detailVsb);
        // insert order sets the Tab cycle: services -> month -> the items -> the item shown
        insert(detail);
        insert(rem);
        insert(inbox);
        insert(notes);
        insert(todo);
        insert(agenda);
        insert(month);
        insert(svc);
        gWin = this;
        refreshAll(true);
        useService(0);
        svc->select();
        updateTitles();
    }
    ~TCalWindow() { gWin = nullptr; }

    std::string dateShown() const { return ymdIso(sel); }
    bool showsDone() const { return showDone; }
    int serviceShown() const { return service; }
    void setShowDone(bool on)
    {
        showDone = on;
        refreshTodos();
    }

    // The list of the service shown, and which panes the window cycles through with Tab.
    TView *listPane() const
    {
        return service == 0 ? (TView *)agenda
               : service == 1 ? (TView *)todo
               : service == 2 ? (TView *)notes
               : service == 3 ? (TView *)inbox
                              : (TView *)rem;
    }
    std::vector<TView *> cycle() const
    {
        std::vector<TView *> v{svc};
        if (service == 0)
            v.push_back(month);
        v.push_back(listPane());
        v.push_back(detail);
        return v;
    }

    // Calendar or To-do: show that service's panes, hide the others', and set the headings.
    void useService(int s)
    {
        if (s < 0 || s >= (int)svc->items.size())
            return;
        service = s;
        // each service's own panes: shown for it, hidden for the others
        const std::vector<std::vector<TView *>> groups = {{month, monthTitle, agenda, agendaVsb},
                                                          {todo, todoHead, todoVsb},
                                                          {notes, noteHead, noteVsb},
                                                          {inbox, inboxHead, inboxVsb},
                                                          {rem, remHead, remVsb}};
        for (int g = 0; g < (int)groups.size(); ++g)
            for (TView *v : groups[g])
                g == s ? v->show() : v->hide();
        showDetail();
        updateTitles();
        if (svc->focused != s)
            svc->focusItem((short)s);
    }

    // ---- what the panes show
    void updateTitles()
    {
        svc->items[0].count = (int)monthCountOn(sel);
        svc->items[1].count = (int)todo->items.size();
        svc->items[2].count = (int)notes->items.size();
        svc->items[3].count = (int)inbox->cells.size();
        svc->items[4].count = (int)rem->cells.size();
        svc->drawView();
        monthTitle->setLabel(std::string(kMonthNames[sel.m - 1]) + " " + std::to_string(sel.y));
        int n = (int)todo->items.size();
        listTitle->setLabel(service == 0 ? "Agenda from " + niceDate(sel)
                            : service == 1 ? std::string("To-do (") + std::to_string(n) + (showDone ? ", with done" : "") + ")"
                            : service == 2 ? "Notes (" + std::to_string(notes->items.size()) + ")"
                            : service == 3 ? "Inbox (" + std::to_string(inbox->cells.size()) + ") - asked for by tvmail and tvchat"
                                           : "Reminders (" + std::to_string(rem->cells.size()) + ") - the next two weeks");
        detailTitle->setLabel(service == 0   ? (detailIsDay ? "Day" : "Event")
                              : service == 1 ? "To-do"
                              : service == 2 ? "Note"
                              : service == 3 ? "Request"
                                             : "Reminder");
        TView *c = current;
        svcTitle->setActive(c == svc ? True : False);
        monthTitle->setActive(c == month ? True : False);
        listTitle->setActive(c == agenda || c == todo || c == notes || c == inbox || c == rem ? True : False);
        detailTitle->setActive(c == detail ? True : False);
    }

    // Events on the day: for the count beside Calendar.
    size_t monthCountOn(const Ymd &d) const
    {
        auto it = month->counts.find(ymdIso(d));
        return it == month->counts.end() ? 0 : (size_t)it->second;
    }

    // The item under the highlight, whole: its fields, then the notes in full.
    void showDetail()
    {
        std::vector<std::string> out;
        detailIsDay = false;
        std::string body, err;
        if (service == 0)
        {
            const AgRow *r = agenda->current();
            if (!r)
                out.push_back("(nothing planned)");
            else if (r->kind != AgRow::event)
            {
                detailIsDay = true;
                out.push_back(niceDate(r->date));
                out.push_back(std::string());
                int n = 0;
                for (const Occ &o : agendaOcc)
                    if (o.date == ymdIso(r->date))
                    {
                        out.push_back("  " + (o.start.empty() ? std::string("all day") : o.start + (o.end.empty() ? "" : "-" + o.end)) +
                                      "  " + o.summary);
                        ++n;
                    }
                if (!n)
                    out.push_back("  (nothing planned)");
                out.push_back(std::string());
                out.push_back("Ins or n adds an event on this day.");
            }
            else if (calAsk("show " + shq(r->occ.uid), body, err))
            {
                auto rows = rowsOf(body);
                if (!rows.empty() && rows[0].size() >= 13 && rows[0][0] == "event")
                {
                    eventLines(out, rows[0], ymdIso(r->date), r->occ.start, r->occ.end);
                }
            }
            else
                out.push_back(err);
        }
        else if (service == 3)
        {
            const std::vector<std::string> *p = inbox->current();
            if (!p || p->size() < 5)
                out.push_back("(nothing is waiting: tvmail's Message > Send to > Calendar and To-do, and tvchat's Chat > Send selection to, leave requests here)");
            else if (calAsk("pending-get " + shq((*p)[0]), body, err))
            {
                auto rows = rowsOf(body);
                out.push_back("From " + (*p)[2] + " - not in your calendar until you say yes");
                out.push_back(std::string());
                if (!rows.empty() && rows[0][0] == "event" && rows[0].size() >= 13)
                    eventLines(out, rows[0], rows[0][4], rows[0][5], rows[0][6]);
                else if (!rows.empty() && rows[0].size() >= 8)
                    todoLines(out, rows[0], false);
                out.push_back(std::string());
                out.push_back("Enter: look at it and add it.  Del: discard it.");
            }
            else
                out.push_back(err);
        }
        else if (service == 4)
        {
            const std::vector<std::string> *p = rem->current();
            if (!p || p->size() < 7)
                out.push_back("(no reminders in the next two weeks)");
            else if (calAsk("show " + shq((*p)[2]), body, err))
            {
                auto rows = rowsOf(body);
                out.push_back((*p)[1] == "todo" ? std::string((*p)[6] == "overdue" ? "Overdue since " : "Due ") + (*p)[3]
                                              : "Reminds you " + (*p)[0] + ", " + (*p)[6] + " min before");
                out.push_back(std::string());
                if (!rows.empty() && (*p)[1] == "event" && rows[0].size() >= 13)
                    eventLines(out, rows[0], (*p)[3], (*p)[4], rows[0][6]);
                else if (!rows.empty() && rows[0].size() >= 8)
                    todoLines(out, rows[0], false);
                out.push_back(std::string());
                out.push_back("Enter: go to it.");
            }
            else
                out.push_back(err);
        }
        else if (service == 2)
        {
            const NoteItem *n = notes->current();
            if (!n)
                out.push_back("(no notes: Ins or n writes one)");
            else if (calAsk("show " + shq(n->uid), body, err))
            {
                auto rows = rowsOf(body);
                if (!rows.empty() && rows[0].size() >= 6 && rows[0][0] == "note")
                {
                    const auto &f = rows[0];
                    out.push_back(f[2]);
                    out.push_back(std::string());
                    if (!f[3].empty())
                        out.push_back("Changed:   " + f[3]);
                    if (!f[4].empty())
                        out.push_back("Categories: " + f[4]);
                    addNotes(out, f[5]);
                }
            }
            else
                out.push_back(err);
        }
        else if (const TodoItem *t = todo->current())
        {
            if (calAsk("show " + shq(t->uid), body, err))
            {
                auto rows = rowsOf(body);
                if (!rows.empty() && rows[0].size() >= 8)
                {
                    todoLines(out, rows[0], t->done);
                }
            }
            else
                out.push_back(err);
        }
        else
            out.push_back("(no to-dos: Ins or n adds one)");
        detail->setLines(std::move(out));
        updateTitles();
    }

    bool detailIsDay = false;

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
        if (service == 1)
            showDetail();
        else
            updateTitles();
    }

    void refreshNotes()
    {
        std::string keep = notes->current() ? notes->current()->uid : std::string();
        std::vector<NoteItem> v;
        std::string body, err;
        if (calAsk("notes", body, err))
            for (auto &f : rowsOf(body))
                if (f.size() >= 5)
                    v.push_back({f[0], f[1], f[2], f[3], f[4]});
        notes->setItems(std::move(v), keep);
        if (service == 2)
            showDetail();
        else
            updateTitles();
    }

    void refreshInbox()
    {
        std::string keep = inbox->current() ? TRowsPane::key(*inbox->current()) : std::string();
        std::vector<std::vector<std::string>> shown, raw;
        std::string body, err;
        if (calAsk("pending", body, err))
            for (auto &f : rowsOf(body))
                if (f.size() >= 5)
                {
                    shown.push_back({f[2], f[4].size() >= 10 ? f[4].substr(5) : "", std::string(f[1] == "todo" ? "to do: " : "") + f[3]});
                    raw.push_back(f);
                }
        gIncoming = (int)raw.size();
        inbox->setRows(std::move(shown), std::move(raw), keep);
        if (service == 3)
            showDetail();
        else
            updateTitles();
    }

    void refreshReminders()
    {
        std::string keep = rem->current() ? TRowsPane::key(*rem->current()) : std::string();
        std::vector<std::vector<std::string>> shown, raw;
        std::string body, err;
        if (calAsk("upcoming", body, err))
            for (auto &f : rowsOf(body))
                if (f.size() >= 7)
                {
                    shown.push_back({f[1] == "todo" ? std::string(f[6] == "overdue" ? "overdue" : "due") : f[0].substr(5),
                                     f[3].size() >= 10 ? f[3].substr(5) : "", f[5]});
                    raw.push_back(f);
                }
        rem->setRows(std::move(shown), std::move(raw), keep);
        if (service == 4)
            showDetail();
        else
            updateTitles();
    }

    // ---- the Inbox and the Reminders
    void showInbox()
    {
        useService(3);
        focusPane(inbox);
    }

    void inboxAct()
    {
        const std::vector<std::string> *p = inbox->current();
        if (!p)
            return;
        std::vector<std::string> row = *p; // the list is rebuilt below
        ushort res = incomingHandle(row);
        if (res == cmOK)
            notify("Added: " + row[3]);
        else if (res == 0)
            notify("That request is gone.", noteWarn);
        refreshAll(false);
    }

    void inboxDiscard()
    {
        const std::vector<std::string> *p = inbox->current();
        if (!p || p->size() < 5)
            return;
        if (messageBox("Discard \"" + (*p)[3] + "\" from " + (*p)[2] + "?", mfConfirmation | mfYesButton | mfNoButton) != cmYes)
            return;
        std::string body, err;
        calAsk("pending-drop " + shq((*p)[0]), body, err);
        refreshInbox();
    }

    // Reminders: Enter goes to the event on its day, or to the to-do.
    void remGo()
    {
        const std::vector<std::string> *p = rem->current();
        if (!p || p->size() < 7)
            return;
        std::string kind = (*p)[1], uid = (*p)[2], day = (*p)[3];
        if (kind == "event")
        {
            Ymd d;
            useService(0);
            if (ymdParse(day, d))
                setSel(d, false);
            focusPane(agenda);
        }
        else
        {
            useService(1);
            for (size_t i = 0; i < todo->items.size(); ++i)
                if (todo->items[i].uid == uid)
                    todo->focusItem((short)i);
            focusPane(todo);
        }
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
        refreshNotes();
        refreshInbox();
        refreshReminders();
        if (service == 0)
            showDetail();
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
        if (!fromAgenda && service == 0)
            showDetail();
        else
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

    // ---- notes
    void newNote()
    {
        NoteForm f;
        if (noteDialog("New note", f, 0) != cmOK)
            return;
        if (f.title.empty() && f.notes.empty())
            return;
        std::string body, err;
        if (!calAsk(noteArgs(f, true), body, err))
        {
            notify(err, noteError);
            return;
        }
        refreshNotes();
    }

    void editNote()
    {
        const NoteItem *n = notes->current();
        if (!n)
        {
            newNote();
            return;
        }
        std::string body, err, uid = n->uid;
        if (!calAsk("show " + shq(uid), body, err))
        {
            notify(err, noteError);
            return;
        }
        auto rows = rowsOf(body);
        if (rows.empty() || rows[0].size() < 6)
            return;
        NoteForm f;
        f.uid = uid;
        f.title = rows[0][2];
        f.cats = rows[0][4];
        f.notes = rows[0][5];
        ushort r = noteDialog("Edit note", f, 1);
        if (r == cmNo)
        {
            deleteNote();
            return;
        }
        if (r != cmOK)
            return;
        if (!calAsk("set-note " + shq(uid) + noteArgs(f, false), body, err))
        {
            notify(err, noteError);
            return;
        }
        refreshNotes();
    }

    void deleteNote()
    {
        const NoteItem *n = notes->current();
        if (!n)
            return;
        if (messageBox("Delete the note \"" + n->title + "\"?", mfConfirmation | mfYesButton | mfNoButton) != cmYes)
            return;
        std::string body, err;
        if (!calAsk("rm " + shq(n->uid), body, err))
            notify(err, noteError);
        refreshNotes();
    }

    // The item Send to works on: the to-do when that pane has the focus, else the event shown.
    bool shareTarget(std::string &uid, std::string &date, std::string &what) const
    {
        if (service == 1 && todo->current())
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
            std::vector<TView *> p = cycle();
            int n = (int)p.size(), at = 0;
            for (int i = 0; i < n; ++i)
                if (p[i] == current)
                    at = i;
            at = (at + (e.keyDown.keyCode == kbTab ? 1 : n - 1)) % n;
            focusPane(p[at]);
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
        svcTitle->changeBounds(R.svcTitle);
        svc->changeBounds(R.svc);
        monthTitle->changeBounds(R.monthTitle);
        month->changeBounds(R.month);
        todoHead->changeBounds(R.todoHead);
        todo->changeBounds(R.todo);
        todoVsb->changeBounds(R.todoVsb);
        noteHead->changeBounds(R.todoHead);
        notes->changeBounds(R.todo);
        noteVsb->changeBounds(R.todoVsb);
        inboxHead->changeBounds(R.todoHead);
        inbox->changeBounds(R.todo);
        inboxVsb->changeBounds(R.todoVsb);
        remHead->changeBounds(R.todoHead);
        rem->changeBounds(R.todo);
        remVsb->changeBounds(R.todoVsb);
        vdiv->changeBounds(R.vbar);
        listTitle->changeBounds(R.listTitle);
        agenda->changeBounds(R.agenda);
        agendaVsb->changeBounds(R.agendaVsb);
        detailTitle->changeBounds(R.detailTitle);
        detail->changeBounds(R.detail);
        detailVsb->changeBounds(R.detailVsb);
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
    if (gWin)
        gWin->refreshInbox(); // sets gIncoming
    else
    {
        std::string body, err;
        gIncoming = 0;
        if (calAsk("pending", body, err))
            gIncoming = (int)rowsOf(body).size();
    }
    if (TProgram::statusLine)
        TProgram::statusLine->drawView(); // the "N incoming" at its end
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
        else if (f[0] == "note" && f.size() >= 6)
            out += "note: " + f[3] + (f[5].empty() ? "" : "  - " + f[5]) + "\n";
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
               "File > Export .ics file... writes it to a file instead.",
               noteWarn);
        return;
    }
    char buf[200] = "";
    if (inputBox("Mail invitation", "~T~o (an address, a name or a group from your address book)", buf, sizeof(buf) - 1) != cmOK || trimmed(buf).empty())
        return;
    std::string to;
    if (!contactsResolve(trimmed(buf), to)) // names from the address book become addresses
    {
        notify(to, noteWarn);
        return;
    }
    flashStatus("Sending through tvmail...");
    // one-shot, not the serve co-process: sending takes longer than its read timeout
    std::string out = shCapture("tvcal-backend mail-event " + shq(uid) + " " + shq(to) + " 2>&1");
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
        notify("Asked tvchat to share this in chat. You say yes there (Chat > Pending...), "
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
    // a Calendar command shows the Calendar, a To-do command the To-do list
    if (cmd == cmCalNewEvent || cmd == cmCalEditEvent || cmd == cmCalDelEvent || cmd == cmCalToday || cmd == cmCalGoto)
        w->useService(0);
    else if (cmd >= cmCalNewTodo && cmd <= cmCalShowDone)
        w->useService(1);
    else if (cmd >= cmCalNewNote && cmd <= cmCalDelNote)
        w->useService(2);
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
        w->showInbox();
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
    case cmCalNewNote:
        w->newNote();
        break;
    case cmCalEditNote:
        w->editNote();
        break;
    case cmCalDelNote:
        w->deleteNote();
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
