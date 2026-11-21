// mailwin.cpp - the mail data (folders, the message list, its filter and
// sort) and the 3-pane mail window that shows it.
#include "tvmail.h"
#include "views.h"
#include <chrono>
#include <map>

std::vector<MsgRow> gAll;  // everything the backend listed
std::vector<MsgRow> gRows; // what the pane shows: gAll, filtered and sorted
                                  // (MsgRow::idx is still the backend index)
SortKey gSortKey = sortDate;
bool gSortRev = false;
std::string gFilter; // type-to-filter text (From / Subject, any case)
std::string gSearch;       // "Search messages" words; empty = no search is showing
std::set<int> gSearchHits; // MsgRow::idx of the messages it found
std::vector<Service> gServices; // what the backend's `services` verb lists (loadServices)
int gServiceIdx = 0;            // the selected service
std::vector<Folder> gFolders;   // the selected service's folders (loadFolders)
int gFolderIdx = 0; // current folder (drives gMbox)
std::string gMbox = "spool";
bool gRemote = false; // backend "mode" == remote (IMAP)
std::string gHost;    // backend "host": the box we're talking to

// What the backend would say for Mail, for a backend that does not know the `services` / `folders` verbs yet
// (an old install beside a new tvmail): the folders without counts.  dead.letter is always a local file.
static std::vector<Folder> mailFolderDefaults()
{
    return {{"inbox", "spool"}, {"drafts", "drafts"}, {"sent", "sent"}, {"saved", "mbox"},
            {"spam", "spam"}, {"trash", "trash"}, {"dead.letter", "dead"}};
}

std::string serviceFlag()
{
    if (gServiceIdx < 0 || gServiceIdx >= (int)gServices.size() || gServices[gServiceIdx].name == "mail")
        return std::string(); // mail is the default: the commands are what they always were
    return "--service " + shq(gServices[gServiceIdx].name) + " ";
}

bool gThreads = true;
int gNewsLimit = 0;

std::string serviceKind()
{
    return gServiceIdx >= 0 && gServiceIdx < (int)gServices.size() ? gServices[gServiceIdx].kind : std::string("mail");
}

bool notForUsenet(const char *what)
{
    if (serviceKind() != "usenet")
        return false;
    notify(std::string(what) + " is for mail, not for Usenet.", noteInfo);
    return true;
}

std::string mboxArg() { return gMbox.empty() ? std::string() : " '" + gMbox + "'"; }
std::string mboxOpt() { return gMbox.empty() ? std::string() : " --mbox '" + gMbox + "'"; }

static std::string mailWindowTitle()
{
    if (gHost.empty())
        return "tvmail";
    return "tvmail - " + gHost + (gRemote ? " (remote)" : " (local)");
}

// The sort key of a list date: the text when it is "YYYY-MM-DD HH:MM", else "" (older than any date).
static std::string dateKey(const std::string &d)
{
    if (d.size() != 16 || d[4] != '-' || d[7] != '-' || d[10] != ' ' || d[13] != ':')
        return std::string();
    return d;
}

// Usenet: put the rows in thread order - each article under the one it answers (the last Message-ID of its
// References that is in the list), threads and replies in the order they arrived (article number, not date:
// the dates of old articles are not to be trusted) - and set each row's depth.
static void threadOrder(std::vector<MsgRow> &rows)
{
    const int n = (int)rows.size();
    std::map<std::string, int> byId;
    for (int i = 0; i < n; ++i)
        if (!rows[i].id.empty())
            byId[rows[i].id] = i;
    std::vector<int> parent(n, -1);
    for (int i = 0; i < n; ++i)
    {
        std::vector<std::string> refs;
        std::istringstream is(rows[i].refs);
        for (std::string t; is >> t;)
            refs.push_back(t);
        for (int k = (int)refs.size() - 1; k >= 0; --k)
        {
            auto it = byId.find(refs[k]);
            if (it != byId.end() && it->second != i)
            {
                parent[i] = it->second;
                break;
            }
        }
    }
    for (int i = 0; i < n; ++i) // a loop of References has no root: cut it here
    {
        int p = parent[i], steps = 0;
        while (p >= 0 && p != i && steps++ < n)
            p = parent[p];
        if (p == i)
            parent[i] = -1;
    }
    std::vector<int> byIdx(n);
    for (int i = 0; i < n; ++i)
        byIdx[i] = i;
    std::stable_sort(byIdx.begin(), byIdx.end(), [&](int a, int b)
                     { return rows[a].idx < rows[b].idx; });
    std::vector<std::vector<int>> kids(n);
    std::vector<int> roots;
    for (int i : byIdx)
        (parent[i] < 0 ? roots : kids[parent[i]]).push_back(i);
    std::vector<MsgRow> out;
    out.reserve(n);
    std::vector<std::pair<int, int>> stack; // (row, depth), the next to show on top
    for (auto r = roots.rbegin(); r != roots.rend(); ++r)
        stack.push_back({*r, 0});
    while (!stack.empty())
    {
        std::pair<int, int> cur = stack.back();
        stack.pop_back();
        out.push_back(rows[cur.first]);
        out.back().depth = cur.second;
        for (auto k = kids[cur.first].rbegin(); k != kids[cur.first].rend(); ++k)
            stack.push_back({*k, cur.second + 1});
    }
    rows.swap(out);
}

// Rebuild gRows from gAll: apply the type-to-filter text, then the sort.
static void applyView()
{
    gRows.clear();
    std::string f = lowered(gFilter);
    for (const MsgRow &r : gAll)
    {
        if (!gSearch.empty() && !gSearchHits.count(r.idx))
            continue; // a search is showing: only what it found
        if (f.empty() || lowered(r.from).find(f) != std::string::npos ||
            lowered(r.subj).find(f) != std::string::npos)
            gRows.push_back(r);
    }
    for (MsgRow &r : gRows)
        r.depth = 0;
    if (serviceKind() == "usenet" && gThreads && gSortKey == sortDate && !gSortRev)
    {
        threadOrder(gRows); // the thread view is the date order of Usenet: the other sorts are flat
        return;
    }
    switch (gSortKey)
    {
    case sortDate: // by the date shown (the backend prints it as local "YYYY-MM-DD HH:MM", so the text sorts as time does);
                   // mail with no readable date goes first; equal dates keep mailbox order (BUGS.md #13 / issue #2)
        std::stable_sort(gRows.begin(), gRows.end(), [](const MsgRow &a, const MsgRow &b)
                         { return dateKey(a.date) < dateKey(b.date); });
        break;
    case sortFrom:
        std::stable_sort(gRows.begin(), gRows.end(), [](const MsgRow &a, const MsgRow &b)
                         { return lowered(a.from) < lowered(b.from); });
        break;
    case sortSubj:
        std::stable_sort(gRows.begin(), gRows.end(), [](const MsgRow &a, const MsgRow &b)
                         { return lowered(a.subj) < lowered(b.subj); });
        break;
    case sortUnread:
        std::stable_sort(gRows.begin(), gRows.end(), [](const MsgRow &a, const MsgRow &b)
                         { return (a.flag == 'N') > (b.flag == 'N'); });
        break;
    }
    if (gSortRev)
        std::reverse(gRows.begin(), gRows.end());
}

// Flag change after mark read/unread: gAll is the master, gRows a view of it.
static void setRowFlag(int idx, char flag)
{
    for (MsgRow &r : gAll)
        if (r.idx == idx)
            r.flag = flag;
    for (MsgRow &r : gRows)
        if (r.idx == idx)
            r.flag = flag;
}

static bool allDigits(const std::string &s)
{
    return !s.empty() && s.find_first_not_of("0123456789") == std::string::npos;
}

// The services, from the backend (name, kind, label); Mail alone when it does not answer.
static void loadServices()
{
    gServices.clear();
    std::istringstream is(backendRun("services"));
    std::string line;
    while (std::getline(is, line))
    {
        while (!line.empty() && line.back() == '\r')
            line.pop_back();
        std::istringstream ls(line);
        Service s;
        std::getline(ls, s.name, '\t');
        std::getline(ls, s.kind, '\t');
        std::getline(ls, s.label);
        if (!s.name.empty() && !s.kind.empty() && !s.label.empty()) // an error line has no tabs
            gServices.push_back(s);
    }
    if (gServices.empty())
        gServices.push_back({"mail", "mail", "Mail", -1});
    if (gServiceIdx >= (int)gServices.size())
        gServiceIdx = 0;
}

// The badge of the service shown: what is new in it - Mail: the inbox; Usenet: all the groups together.
static void setBadge()
{
    if (gServiceIdx < 0 || gServiceIdx >= (int)gServices.size() || gFolders.empty())
        return;
    int n = 0;
    if (gServices[gServiceIdx].kind == "usenet" || gServices[gServiceIdx].kind == "lists")
        for (const Folder &f : gFolders)
            n += std::max(0, f.unread);
    else
        n = gFolders[0].unread;
    gServices[gServiceIdx].unread = n;
}

// The selected service's folders and their message / unread counts (-1 = not known), from the backend.
// The highlight stays on the same folder when it is still there.
static void loadFolders()
{
    std::string was = gFolderIdx >= 0 && gFolderIdx < (int)gFolders.size() ? gFolders[gFolderIdx].mbox : std::string();
    std::vector<Folder> now;
    std::string raw = backendRun("folders");
    std::istringstream is(raw);
    std::string line;
    while (std::getline(is, line))
    {
        while (!line.empty() && line.back() == '\r')
            line.pop_back();
        std::istringstream ls(line);
        Folder f;
        std::string tot, uns;
        std::getline(ls, f.name, '\t');
        std::getline(ls, f.mbox, '\t');
        std::getline(ls, tot, '\t');
        std::getline(ls, uns);
        if (f.name.empty() || f.mbox.empty() || !allDigits(tot) || !allDigits(uns))
            continue; // an error line, not a folder
        f.total = std::atoi(tot.c_str());
        f.unread = std::atoi(uns.c_str());
        now.push_back(f);
    }
    if (now.empty())
    {
        reportListError("Cannot list the folders", raw);
        if (gServiceIdx >= 0 && gServiceIdx < (int)gServices.size() && gServices[gServiceIdx].kind == "mail")
            now = mailFolderDefaults();
    }
    gFolders = now;
    gFolderIdx = 0;
    for (int i = 0; i < (int)gFolders.size(); ++i)
        if (gFolders[i].mbox == was)
            gFolderIdx = i;
    if (!gFolders.empty())
        gMbox = gFolders[gFolderIdx].mbox;
    setBadge();
}

static void loadList()
{
    gAll.clear();
    bool usenet = serviceKind() == "usenet";
    std::string raw = backendRun("list " + gMbox + (usenet && gNewsLimit > 0 ? " --limit " + std::to_string(gNewsLimit) : std::string()));
    reportListError("Cannot list " + (gMbox.empty() ? std::string("the folder") : gMbox), raw);
    std::istringstream is(raw);
    std::string line;
    while (std::getline(is, line))
    {
        if (line.empty() || line.find('\t') == std::string::npos)
            continue;
        std::istringstream ls(line);
        std::string idx, flag, date, from, subj, id, refs;
        std::getline(ls, idx, '\t');
        std::getline(ls, flag, '\t');
        std::getline(ls, date, '\t');
        std::getline(ls, from, '\t');
        if (usenet) // the subject, then the Message-ID and References the threads are built from
        {
            std::getline(ls, subj, '\t');
            std::getline(ls, id, '\t');
            std::getline(ls, refs);
        }
        else
            std::getline(ls, subj);
        MsgRow r;
        r.idx = std::atoi(idx.c_str());
        r.flag = flag.empty() ? '.' : flag[0];
        r.date = date;
        r.from = from;
        r.subj = subj;
        r.id = id;
        r.refs = refs;
        gAll.push_back(r);
    }
    applyView();
}

// ============================================== 4-pane mail window =======
// One full-screen window: services over folders (left) | message list over
// message content (right).  Panes talk via evBroadcast: service focus loads
// its folders, folder focus reloads the list, message focus loads the content.

// Folder | Total | New (the table pieces are in views.h)
const TableCols kFolderCols = {{"Folder", 0}, {"Total", 5, true}, {"New", 4, true}};
// Service | New: no heading row, the pane's title says what it is
const TableCols kServiceCols = {{"Service", 0}, {"New", 4, true}};

class TFolderPane : public TListViewer
{
public:
    TFolderPane(const TRect &b) : TListViewer(b, 1, nullptr, nullptr)
    {
        reloadRows();
    }
    // The folders changed (another service, or F5): new range, highlight on gFolderIdx, nothing announced.
    void reloadRows()
    {
        setRange((short)gFolders.size());
        TListViewer::focusItem(gFolderIdx);
        drawView();
    }
    void draw() override
    {
        drawTableList(*this, kFolderCols, [](short item) -> std::vector<std::string>
                      {
            if (item < 0 || item >= (short)gFolders.size())
                return {};
            // how many there are, and how many of them are unread
            const Folder &f = gFolders[item];
            return {f.name,
                    f.total >= 0 ? std::to_string(f.total) : std::string(),
                    f.unread >= 0 ? std::to_string(f.unread) : std::string()}; });
    }
    void getText(char *dest, short item, short maxLen) override
    {
        putText(dest, item >= 0 && item < (short)gFolders.size() ? gFolders[item].name : std::string(), maxLen);
    }
    // TListViewer calls this for every click and arrow key, even when the
    // highlight lands where it already was (clicking the pane to focus it,
    // Up on the first row) - only a real move reloads the folder (and
    // drops the filter).  F5 is the way to re-read the same folder.
    void focusItem(short item) override
    {
        bool moved = item != focused;
        TListViewer::focusItem(item);
        gFolderIdx = item;
        if (moved && owner)
            message(owner, evBroadcast, cmFolderPicked, this);
    }
    void selectItem(short item) override { focusItem(item); }
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

// The services, top left: Mail, and later the listserv and usenet.  A count of what is new at the right.
class TServicePane : public TListViewer
{
public:
    TServicePane(const TRect &b) : TListViewer(b, 1, nullptr, nullptr)
    {
        setRange((short)gServices.size());
        TListViewer::focusItem(gServiceIdx);
    }
    void draw() override
    {
        drawTableList(*this, kServiceCols, [](short item) -> std::vector<std::string>
                      {
            if (item < 0 || item >= (short)gServices.size())
                return {};
            const Service &s = gServices[item];
            return {s.label, s.unread > 0 ? std::to_string(s.unread) : std::string()}; });
    }
    void getText(char *dest, short item, short maxLen) override
    {
        putText(dest, item >= 0 && item < (short)gServices.size() ? gServices[item].label : std::string(), maxLen);
    }
    // As in TFolderPane: only a real move announces itself.
    void focusItem(short item) override
    {
        bool moved = item != focused;
        TListViewer::focusItem(item);
        gServiceIdx = item;
        if (moved && owner)
            message(owner, evBroadcast, cmServicePicked, this);
    }
    void selectItem(short item) override { focusItem(item); }
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

class TMsgPane : public TListViewer
{
public:
    TMsgPane(const TRect &b, TScrollBar *vsb) : TListViewer(b, 1, nullptr, vsb)
    {
        setRange((short)gRows.size());
    }
    void getText(char *dest, short item, short maxLen) override
    {
        if (item < 0 || item >= (short)gRows.size())
        {
            dest[0] = 0;
            return;
        }
        const MsgRow &r = gRows[item];
        putText(dest, std::string(1, r.flag) + " " + fitCols(r.date, 16) + " " +
                          fitCols(r.from, 20) + " " + std::string(2 * std::min(r.depth, 8), ' ') + r.subj, // a reply sits under its thread
                maxLen);
    }
    void focusItem(short item) override
    {
        bool moved = item != focused; // as in TFolderPane: a click on the
        TListViewer::focusItem(item); // current row doesn't re-fetch it
        if (moved && owner)
            message(owner, evBroadcast, cmMsgPicked, this);
    }
    void selectItem(short) override
    {
        if (!owner)
            return;
        // Enter on a draft opens it for editing; otherwise jump to the body
        message(owner, evBroadcast,
                gMbox == "drafts" && serviceKind() == "mail" ? cmEditDraft : cmFocusContent, this);
    }
    void handleEvent(TEvent &e) override
    {
        if (e.what == evKeyDown)
        {
            if (e.keyDown.keyCode == kbEnter)
            {
                selectItem(focused);
                clearEvent(e);
                return;
            }
            if (filterKey(e.keyDown))
            {
                clearEvent(e);
                return;
            }
        }
        TListViewer::handleEvent(e);
    }
    // Type-to-filter: plain characters build a From/Subject filter, Backspace
    // trims it, Esc drops it.  Nothing else in this pane uses plain letters.
    bool filterKey(const KeyDownEvent &k)
    {
        if (k.controlKeyState & (kbCtrlShift | kbAltShift))
            return false;
        std::string f = gFilter;
        if (k.keyCode == kbEsc)
        {
            if (f.empty() && gSearch.empty())
                return false;
            f.clear();
            gSearch.clear(); // Esc also ends a search
            gSearchHits.clear();
        }
        else if (k.keyCode == kbBack)
        {
            if (f.empty())
                return false;
            while (f.size() > 1 && ((uchar)f.back() & 0xC0) == 0x80)
                f.pop_back(); // whole UTF-8 character
            f.pop_back();
        }
        else if (k.textLength > 0 && (uchar)k.text[0] >= 0x20 && (uchar)k.text[0] != 0x7F)
        {
            if (k.text[0] == ' ' && f.empty())
                return false; // Space on a message stays "open it"
            f.append(k.text, k.textLength);
        }
        else
            return false;
        gFilter = f;
        applyView();
        if (owner)
            message(owner, evBroadcast, cmFilterChanged, this);
        return true;
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
    // Re-read gRows and put the highlight on row `sel` (clamped).  Goes
    // through the base focusItem(), which scrolls to it without announcing
    // cmMsgPicked - the caller decides whether the body needs reloading.
    void reload(int sel = 0)
    {
        setRange((short)gRows.size());
        if (sel >= (int)gRows.size())
            sel = (int)gRows.size() - 1;
        if (sel < 0)
            sel = 0;
        TListViewer::focusItem((short)sel);
        drawView();
    }
};

bool gWrap = true; // the body pane's wrap setting

class TContentPane : public TMailText
{
protected:
    void wrapChanged(bool w) override { gWrap = w; }

public:
    TContentPane(const TRect &b, TScrollBar *h, TScrollBar *v)
        : TMailText(b, h, v, {std::string()}, gWrap) {}
    void setState(ushort aState, Boolean enable) override
    {
        TMailText::setState(aState, enable);
        if (enable && (aState & sfFocused) && owner)
            message(owner, evBroadcast, cmPaneFocused, this);
    }
    TColorAttr mapColor(uchar i) override
    {
        return i == 1 ? cNorm() : (i == 2 ? cHi() : TView::mapColor(i));
    }
};

class TMailWindow;
static TMailWindow *gMailWin = nullptr; // the one 3-pane window, once made

class TMailWindow : public TWindow
{
    Boolean ready = False;
    // Debounce (BUGS.md #17 / issue #4): the highlight moves at once, but what it costs - the folder's list, the
    // message's text, a round trip over IMAP - is asked for only when the keys pause (TV_DEBOUNCE_MS, 150).  A
    // command, a pane change or any key that is not a way of moving or typing flushes it first.
    bool pendFolder = false, pendContent = false;
    std::chrono::steady_clock::time_point pendAt;
    static int debounceMs()
    {
        static int ms = -1;
        if (ms < 0)
        {
            const char *e = getenv("TV_DEBOUNCE_MS");
            ms = e && *e ? std::max(0, atoi(e)) : 150;
        }
        return ms;
    }
    void deferLoad(bool folder)
    {
        if (debounceMs() == 0)
        {
            folder ? loadFolder() : loadContent();
            return;
        }
        (folder ? pendFolder : pendContent) = true;
        pendAt = std::chrono::steady_clock::now() + std::chrono::milliseconds(debounceMs());
    }

public:
    // Do the load that is waiting, if the keys have paused for the debounce time (or `force`).
    void flushPending(bool force)
    {
        if (!pendFolder && !pendContent)
            return;
        if (!force && std::chrono::steady_clock::now() < pendAt)
            return;
        bool f = pendFolder;
        pendFolder = pendContent = false;
        if (f)
            loadFolder(); // loads the first message too
        else
            loadContent();
    }

private:
    TScrollBar *msgVsb = nullptr, *contentVsb = nullptr;
    TDivider *vdiv = nullptr;
    TTableHead *folderHead = nullptr;
    TPaneTitle *folderTitle = nullptr, *msgTitle = nullptr, *contentTitle = nullptr, *svcTitle = nullptr;
    int activePane = 0; // 0 folders, 1 messages, 2 body, 3 services
    int leftW = 22;     // the width of the left column: wider for Usenet's group names
    // The Services pane takes the top of the left column.  With one service there is nothing to choose, so
    // the window is then the three panes it always was; View > Services pane shows it anyway.
    bool showServices = false;

    // rows the Services pane takes: its title and its services (at most 5)
    int svcRows() const { return showServices ? 1 + std::min<int>(5, (int)gServices.size()) : 0; }

    struct Rects
    {
        TRect svcTitle, svc, folderTitle, folderHead, folder, vbar, msgTitle, msg, msgVsb,
            contentTitle, content, contentVsb;
    };
    Rects paneRects() const
    {
        int W = size.x, H = size.y;
        const int FW = leftW;
        int sy = 1 + (H - 2) * 2 / 5;
        if (sy < 6)
            sy = 6; // title + a few rows + body title
        if (sy > H - 5)
            sy = H - 5;
        int t = 1 + svcRows(); // where the Folders title goes: below the Services pane, when there is one
        if (t > H - 5)
            t = std::max(1, H - 5); // a tiny terminal: the folders matter more
        Rects r;
        r.svcTitle = TRect(1, 1, 1 + FW, 2);
        r.svc = TRect(1, 2, 1 + FW, t);
        r.folderTitle = TRect(1, t, 1 + FW, t + 1);
        r.folderHead = TRect(1, t + 1, 1 + FW, t + 2);
        r.folder = TRect(1, t + 2, 1 + FW, H - 1);
        r.vbar = TRect(1 + FW, 1, 2 + FW, H - 1);
        r.msgTitle = TRect(2 + FW, 1, W - 1, 2);
        r.msg = TRect(2 + FW, 2, W - 2, sy);
        r.msgVsb = TRect(W - 2, 2, W - 1, sy);
        r.contentTitle = TRect(2 + FW, sy, W - 1, sy + 1);
        r.content = TRect(2 + FW, sy + 1, W - 2, H - 1);
        r.contentVsb = TRect(W - 2, sy + 1, W - 1, H - 1);
        return r;
    }

public:
    TServicePane *svcPane = nullptr;
    TFolderPane *folderPane = nullptr;
    TMsgPane *msgPane = nullptr;
    TContentPane *contentPane = nullptr;

    TMailWindow(const TRect &bounds)
        : TWindowInit(&TMailWindow::initFrame),
          TWindow(bounds, mailWindowTitle().c_str(), wnNoNumber)
    {
        palette = wpCyanWindow;
        flags &= ~(wfClose | wfZoom | wfMove);
        growMode = gfGrowHiX | gfGrowHiY;

        loadServices();
        showServices = gServices.size() > 1;
        loadFolders();
        Rects R = paneRects();
        svcTitle = new TPaneTitle(R.svcTitle, "Services");
        svcPane = new TServicePane(R.svc);
        folderTitle = new TPaneTitle(R.folderTitle, "Folders");
        msgTitle = new TPaneTitle(R.msgTitle, "Messages");
        contentTitle = new TPaneTitle(R.contentTitle, "Message");
        folderHead = new TTableHead(R.folderHead, kFolderCols);
        folderPane = new TFolderPane(R.folder);
        vdiv = new TDivider(R.vbar, true);
        msgVsb = new TScrollBar(R.msgVsb);
        msgPane = new TMsgPane(R.msg, msgVsb);
        contentVsb = new TScrollBar(R.contentVsb);
        contentPane = new TContentPane(R.content, nullptr, contentVsb);

        insert(vdiv);
        insert(svcTitle);
        insert(folderTitle);
        insert(folderHead);
        insert(msgTitle);
        insert(contentTitle);
        insert(msgVsb);
        insert(contentVsb);
        // insert order sets the Tab cycle: the last view inserted, then the first, second...
        // so services -> folders -> messages -> body (a hidden pane is skipped)
        insert(folderPane);
        insert(msgPane);
        insert(contentPane);
        insert(svcPane);
        if (!showServices)
        {
            svcTitle->hide();
            svcPane->hide();
        }

        gMailWin = this;
        loadFolder();
        ready = True;
        folderPane->select();
        setActivePane(0);
        serviceCommandsChanged(); // the Usenet commands stay off until Usenet is picked
    }

    // View > Services pane
    void toggleServices()
    {
        showServices = !showServices;
        int want = activePane; // showing or hiding a pane makes Turbo Vision pick the topmost one to focus
        if (!showServices && want == 3)
            want = 0;
        // locked, so the panes are placed first and the window repaints once
        lock();
        layout();
        if (showServices)
        {
            svcTitle->show();
            svcPane->show();
        }
        else
        {
            svcTitle->hide();
            svcPane->hide();
        }
        unlock();
        // A window keeps its picture in a buffer and draws only what a view tells it to, and a view moved
        // while another still covered its new place is "not exposed" and draws nothing (found by the e2e
        // case: a stale row of the old list stayed): so draw every pane again, now that all are in place.
        redraw();
        (want == 0 ? (TView *)folderPane : want == 1 ? (TView *)msgPane : want == 2 ? (TView *)contentPane : (TView *)svcPane)->select();
    }

    // Put every pane where paneRects() says (a resize, or the Services pane shown or hidden).
    void layout()
    {
        Rects R = paneRects();
        svcTitle->changeBounds(R.svcTitle);
        svcPane->changeBounds(R.svc);
        folderTitle->changeBounds(R.folderTitle);
        folderHead->changeBounds(R.folderHead);
        folderPane->changeBounds(R.folder);
        vdiv->changeBounds(R.vbar);
        msgTitle->changeBounds(R.msgTitle);
        msgPane->changeBounds(R.msg);
        msgVsb->changeBounds(R.msgVsb);
        contentTitle->changeBounds(R.contentTitle);
        contentPane->changeBounds(R.content);
        contentVsb->changeBounds(R.contentVsb);
    }

    void setActivePane(int p)
    {
        activePane = p;
        if (svcTitle)
            svcTitle->setActive(Boolean(p == 3));
        if (folderTitle)
            folderTitle->setActive(Boolean(p == 0));
        if (msgTitle)
            msgTitle->setActive(Boolean(p == 1));
        if (contentTitle)
            contentTitle->setActive(Boolean(p == 2));
    }

    // Where to put the highlight when there is no message to stay on: in
    // date order the newest message by date (the last row, or the first
    // when reversed; of equal dates the later in the mailbox) so a folder
    // opens on the latest mail, not on the oldest at the top; in the other
    // orders, the first row.
    static int startRow()
    {
        if (!gRows.empty() && serviceKind() == "usenet") // the newest article (dates in old articles are not to be trusted)
        {
            int newest = 0;
            for (int i = 1; i < (int)gRows.size(); ++i)
                if (gRows[i].idx > gRows[newest].idx)
                    newest = i;
            return newest;
        }
        if (gSortKey != sortDate || gRows.empty())
            return 0;
        int best = 0;
        for (int i = 1; i < (int)gRows.size(); ++i)
        {
            std::string a = dateKey(gRows[i].date), b = dateKey(gRows[best].date);
            if (a > b || (a == b && gRows[i].idx > gRows[best].idx))
                best = i;
        }
        return best;
    }

    // keep: stay on the same message across the reload (F5, mark, delete,
    // pull...) instead of jumping back to the top of the list.
    void loadFolder(bool keep = false)
    {
        int keepIdx = -1, keepPos = 0;
        if (keep && msgPane && msgPane->focused >= 0 && msgPane->focused < (int)gRows.size())
        {
            keepPos = msgPane->focused;
            keepIdx = gRows[keepPos].idx;
        }
        if (gFolderIdx < 0 || gFolderIdx >= (int)gFolders.size())
        {
            gAll.clear(); // a service with no folders
            applyView();
        }
        else
        {
            gMbox = gFolders[gFolderIdx].mbox;
            loadList();
            // the list we just read is the freshest word on this folder's counts (a Usenet list is only
            // the newest articles of the group: its counts are the server's)
            if (gMbox != "dead" && serviceKind() != "usenet")
            {
                gFolders[gFolderIdx].total = (int)gAll.size();
                gFolders[gFolderIdx].unread = (int)std::count_if(gAll.begin(), gAll.end(),
                                                                 [](const MsgRow &r)
                                                                 { return r.flag == 'N'; });
                syncBadge();
            }
        }
        if (msgPane)
        {
            // message gone (deleted/moved): the nearest row; nothing to
            // stay on (a folder just opened): the newest message
            int sel = keepIdx >= 0 ? keepPos : startRow();
            if (keepIdx >= 0)
                for (size_t i = 0; i < gRows.size(); ++i)
                    if (gRows[i].idx == keepIdx)
                    {
                        sel = (int)i;
                        break;
                    }
            msgPane->reload(sel);
        }
        refreshTitles();
        if (folderPane)
            folderPane->drawView();
        loadContent();
    }

    // The badge of the service shown follows its first folder (Mail: the inbox).
    void syncBadge()
    {
        setBadge();
        if (svcPane)
            svcPane->drawView();
    }

    // Another service was picked: its folders, then its first folder.
    void servicePicked()
    {
        gFilter.clear();
        gSearch.clear();
        gSearchHits.clear();
        gFolders.clear();
        gFolderIdx = 0;
        gNewsLimit = 0;
        retitle();
        lock();
        layout(); // Usenet's group names are long: a wider left column
        unlock();
        loadFolders();
        folderPane->reloadRows();
        loadFolder();
        serviceCommandsChanged();
        redraw(); // the left column changed width: every pane again (see toggleServices)
    }

    // The service decides what the panes are called and how wide the left column is.
    void retitle()
    {
        bool usenet = serviceKind() == "usenet";
        leftW = usenet ? 32 : 22;
        folderTitle->setLabel(usenet ? "Groups" : serviceKind() == "lists" ? "Lists" : "Folders");
        contentTitle->setLabel(usenet ? "Article" : "Message");
    }

    // ---- the Usenet commands (Message > Usenet) ----
    // After a change on the server side of things: the folders again, the highlight on `group` if it is there.
    void newsRefresh(const std::string &group = std::string())
    {
        std::string want = group.empty() && gFolderIdx >= 0 && gFolderIdx < (int)gFolders.size() ? gFolders[gFolderIdx].mbox : group;
        gFolderIdx = 0;
        loadFolders();
        for (int i = 0; i < (int)gFolders.size(); ++i)
            if (gFolders[i].mbox == want)
                gFolderIdx = i;
        if (gFolders.empty() || gFolders[gFolderIdx].mbox != want)
            for (int i = 0; i < (int)gFolders.size(); ++i) // Lists: the name you subscribed with is the label
                if (gFolders[i].name == want)
                    gFolderIdx = i;
        gNewsLimit = 0;
        folderPane->reloadRows();
        loadFolder();
    }

    static std::string firstLine(std::string s)
    {
        s = s.substr(0, s.find('\n'));
        while (!s.empty() && (s.back() == '\r' || s.back() == ' '))
            s.pop_back();
        return s;
    }

    void newsSubscribe(const std::string &name)
    {
        std::string g = name;
        if (g.empty())
            g = askLine("Subscribe to newsgroup", "~G~roup name (for example comp.os.linux)");
        if (g.empty())
            return;
        std::string out = firstLine(backendRun("news-subscribe " + shq(g)));
        bool ok = out.rfind("subscribed", 0) == 0;
        notify(out.empty() ? "No answer from tvmail-backend." : out, ok ? noteInfo : noteError);
        if (ok)
            newsRefresh(g);
    }

    void newsUnsubscribe()
    {
        if (gFolderIdx < 0 || gFolderIdx >= (int)gFolders.size())
            return;
        std::string g = gFolders[gFolderIdx].mbox;
        if (messageBox(mfConfirmation | mfYesButton | mfNoButton, "Unsubscribe from %s?", g.c_str()) != cmYes)
            return;
        notify(firstLine(backendRun("news-unsubscribe " + shq(g))), noteInfo);
        gFolderIdx = 0;
        newsRefresh();
    }

    void newsCatchUp()
    {
        if (gFolderIdx < 0 || gFolderIdx >= (int)gFolders.size())
            return;
        std::string g = gFolders[gFolderIdx].mbox;
        if (messageBox(mfConfirmation | mfYesButton | mfNoButton, "Mark every article in %s as read?", g.c_str()) != cmYes)
            return;
        std::string out = firstLine(backendRun("news-catchup " + shq(g)));
        notify(out, out.rfind("caught up", 0) == 0 ? noteInfo : noteError);
        newsRefresh(g);
    }

    // Every group on the server, to subscribe to (or just go to) one.
    void newsAllGroups()
    {
        std::string raw = backendRun("news-groups");
        std::vector<std::string> names, rows;
        std::istringstream is(raw);
        std::string line;
        while (std::getline(is, line))
        {
            std::vector<std::string> f;
            std::istringstream ls(line);
            for (std::string c; std::getline(ls, c, '\t');)
                f.push_back(c);
            if (f.size() < 5)
                continue; // an error line, not a group
            names.push_back(f[0]);
            int n = std::max(0, std::atoi(f[2].c_str()) - std::atoi(f[1].c_str()) + 1);
            std::string desc = f.size() > 5 ? f[5] : std::string();
            rows.push_back(std::string(f[4] == "*" ? "* " : "  ") + fitCols(f[0], 38) + " " +
                           fitCols(std::to_string(n), 7) + " " + desc);
        }
        if (names.empty())
        {
            reportListError("Cannot list the groups", raw);
            if (raw.find('\t') == std::string::npos && raw.empty())
                notify("The news server has no groups.", noteInfo);
            return;
        }
        int at = pickFromList("All groups  (* = subscribed; Enter subscribes)", rows);
        if (at < 0)
            return;
        if (rows[at][0] == '*')
            newsRefresh(names[at]); // already yours: go there
        else
            newsSubscribe(names[at]);
    }

    // The list shows the newest articles; this asks for more of them.
    void newsLoadOlder()
    {
        if (gFolderIdx < 0 || gFolderIdx >= (int)gFolders.size())
            return;
        size_t before = gAll.size();
        int base = std::max<int>(gNewsLimit, (int)before);
        gNewsLimit = base + 500;
        loadFolder(true); // stays on the article you were reading
        if (gAll.size() == before)
            notify("There are no older articles.", noteInfo);
    }

    void newsCommand(ushort command)
    {
        switch (command)
        {
        case cmNewsSubscribe:
            newsSubscribe(std::string());
            break;
        case cmNewsUnsub:
            newsUnsubscribe();
            break;
        case cmNewsCatchUp:
            newsCatchUp();
            break;
        case cmNewsGroups:
            newsAllGroups();
            break;
        case cmNewsOlder:
            newsLoadOlder();
            break;
        case cmToggleThreads:
            gThreads = !gThreads;
            viewChanged(false);
            break;
        }
    }

    // ---- the Lists commands (Message > Lists) ----
    // A command is a mail to listserv@; the backend waits for the answer mail and hands back its first line (OK ... or
    // ERROR ...), which is shown as a toast, never a box you have to click away.
    void listsSay(const std::string &out)
    {
        std::string l = firstLine(out);
        bool bad = l.empty() || l.rfind("ERROR", 0) == 0 || l.rfind("tvmail-backend", 0) == 0;
        notify(l.empty() ? std::string("No answer from tvmail-backend.") : l, bad ? noteError : noteInfo);
    }

    void listsSubscribe(const std::string &name)
    {
        std::string n = name;
        if (n.empty())
            n = askLine("Subscribe to a list", "~L~ist name (for example lan-talk)");
        if (n.empty())
            return;
        listsSay(backendRun("lists-subscribe " + shq(n)));
        newsRefresh(n); // the folders again: a list the server accepted is there at once, empty
    }

    void listsSignoff()
    {
        if (gFolderIdx < 0 || gFolderIdx >= (int)gFolders.size())
            return;
        std::string n = gFolders[gFolderIdx].name;
        if (messageBox(mfConfirmation | mfYesButton | mfNoButton, "Sign off from %s?\n(The folder and its mail stay.)", n.c_str()) != cmYes)
            return;
        listsSay(backendRun("lists-signoff " + shq(n)));
    }

    void listsDigest()
    {
        if (gFolderIdx < 0 || gFolderIdx >= (int)gFolders.size())
            return;
        std::string n = gFolders[gFolderIdx].name;
        int at = pickFromList(("Mail from " + n).c_str(), {"Every message as it comes", "One digest mail"});
        if (at < 0)
            return;
        listsSay(backendRun("lists-digest " + shq(n) + (at == 1 ? " on" : " off")));
    }

    // What the server offers: its answer to LIST is a verdict line and then "name  description" lines.
    void listsAvailable()
    {
        std::string raw = backendRun("lists-available");
        std::istringstream is(raw);
        std::string line, verdict;
        std::vector<std::string> names, rows;
        while (std::getline(is, line))
        {
            while (!line.empty() && (line.back() == '\r' || line.back() == ' '))
                line.pop_back();
            size_t b = line.find_first_not_of(" \t");
            if (b == std::string::npos)
                continue;
            line = line.substr(b);
            if (verdict.empty())
            {
                verdict = line;
                continue;
            }
            size_t sp = line.find_first_of(" \t");
            std::string nm = line.substr(0, sp);
            std::string desc = sp == std::string::npos ? std::string() : line.substr(line.find_first_not_of(" \t", sp));
            bool mine = std::any_of(gFolders.begin(), gFolders.end(), [&](const Folder &f)
                                    { return f.name == nm; });
            names.push_back(nm);
            rows.push_back(std::string(mine ? "* " : "  ") + fitCols(nm, 24) + " " + desc);
        }
        if (verdict.rfind("OK", 0) != 0)
        {
            listsSay(verdict.empty() ? raw : verdict);
            return;
        }
        if (names.empty())
        {
            notify("The list server has no lists.", noteInfo);
            return;
        }
        int at = pickFromList("Available lists  (* = yours; Enter subscribes)", rows);
        if (at < 0)
            return;
        if (rows[at][0] == '*')
            newsRefresh(names[at]); // already yours: go there
        else
            listsSubscribe(names[at]);
    }

    void listsCommand(ushort command)
    {
        switch (command)
        {
        case cmListsSubscribe:
            listsSubscribe(std::string());
            break;
        case cmListsSignoff:
            listsSignoff();
            break;
        case cmListsDigest:
            listsDigest();
            break;
        case cmListsAvailable:
            listsAvailable();
            break;
        }
    }

    // Folder-pane numbers and the Messages heading (filter, sort order).
    void refreshTitles()
    {
        if (!msgTitle)
            return;
        bool usenet = serviceKind() == "usenet";
        std::string t = usenet ? "Articles" : "Messages";
        if (usenet && gFolderIdx >= 0 && gFolderIdx < (int)gFolders.size() && gFolders[gFolderIdx].total > (int)gAll.size())
            t += "  (newest " + std::to_string(gAll.size()) + " of " + std::to_string(gFolders[gFolderIdx].total) +
                 " - Message > Usenet > Load older)";
        if (!gSearch.empty())
            t += "  search: \"" + gSearch + "\"" + (gFilter.empty() ? "" : "  filter: " + gFilter + "_") + "  (" +
                 std::to_string(gRows.size()) + " of " + std::to_string(gAll.size()) + ", Esc clears)";
        else if (!gFilter.empty())
            t += "  filter: " + gFilter + "_  (" + std::to_string(gRows.size()) +
                 " of " + std::to_string(gAll.size()) + ")";
        const char *how = nullptr;
        switch (gSortKey)
        {
        case sortDate:
            how = gSortRev ? "newest first" : nullptr;
            break;
        case sortFrom:
            how = gSortRev ? "from Z-A" : "from A-Z";
            break;
        case sortSubj:
            how = gSortRev ? "subject Z-A" : "subject A-Z";
            break;
        case sortUnread:
            how = gSortRev ? "read first" : "unread first";
            break;
        }
        if (how)
            t += std::string("  sort: ") + how;
        msgTitle->setLabel(t);
    }

    // The view (filter / sort) changed but the folder didn't: rebuild the
    // list without asking the backend again, and stay on the same message
    // if it is still shown.
    void viewChanged(bool resetSel, bool debounce = false)
    {
        int keepIdx = -1;
        if (!resetSel && msgPane && msgPane->focused >= 0 && msgPane->focused < (int)gRows.size())
            keepIdx = gRows[msgPane->focused].idx;
        applyView();
        int sel = startRow();
        for (size_t i = 0; keepIdx >= 0 && i < gRows.size(); ++i)
            if (gRows[i].idx == keepIdx)
                sel = (int)i;
        if (msgPane)
            msgPane->reload(sel);
        refreshTitles();
        if (debounce && !pendFolder)
            deferLoad(false); // typing a filter: the text of the row it lands on is asked for when you pause
        else
            loadContent();
    }

    // Showing a message does not read it: the body is only fetched.  It is
    // marked read once you actually look - the body pane takes focus (Enter,
    // Tab, click), or Message > Mark read - so arrowing through the inbox
    // leaves it unread, and "Mark unread" stays marked.
    void loadContent()
    {
        if (!contentPane)
            return;
        if (gRows.empty() || !msgPane ||
            msgPane->focused < 0 || msgPane->focused >= (int)gRows.size())
        {
            contentPane->setLines({std::string("(no message)")});
            return;
        }
        int b = gRows[msgPane->focused].idx;
        contentPane->setLines(splitLines(backendRun("show " + std::to_string(b) + " " + gMbox)));
    }

    // Flag the highlighted message read (or unread), best effort: a failed
    // mark must not disturb what is on screen.
    void markFocused(bool read)
    {
        if (!msgPane || msgPane->focused < 0 || msgPane->focused >= (int)gRows.size())
            return;
        const MsgRow &r = gRows[msgPane->focused];
        if (gMbox == "dead" || (r.flag == 'N') == !read)
            return; // nothing to change
        backendRun("mark " + std::to_string(r.idx) + (read ? " read " : " unread ") + gMbox);
        setRowFlag(r.idx, read ? '.' : 'N');
        if (gFolderIdx >= 0 && gFolderIdx < (int)gFolders.size() && gFolders[gFolderIdx].unread >= 0)
            gFolders[gFolderIdx].unread = std::max(0, gFolders[gFolderIdx].unread + (read ? -1 : 1));
        syncBadge();
        msgPane->drawView();
        folderPane->drawView();
        if (gSortKey == sortUnread) // the order depends on the flag
            viewChanged(false);
    }

    void reloadFolder()
    {
        loadFolders(); // counts, and a folder list that may have changed
        folderPane->reloadRows();
        loadFolder(true);
    }

    void shutDown() override
    {
        if (gMailWin == this)
            gMailWin = nullptr;
        TWindow::shutDown();
    }

    void handleEvent(TEvent &e) override
    {
        // Ctrl-F is Search messages, but only here, in the mail window, and before the panes see it (a list viewer
        // reads Ctrl-F as the WordStar cursor key).  Not a menu hot key: that would also take it from the editors,
        // which use it as "cursor right"; Alt-S and F8 belong to compose and the configuration editor.
        if (ready && e.what == evKeyDown && e.keyDown.keyCode == kbCtrlF)
        {
            clearEvent(e);
            mailSearch();
            return;
        }
        if (e.what == evKeyDown && (pendFolder || pendContent))
        {
            // Moving and typing may go on waiting; any other key acts on what is shown, so shows it first.
            const KeyDownEvent &k = e.keyDown;
            bool moves = k.keyCode == kbUp || k.keyCode == kbDown || k.keyCode == kbPgUp || k.keyCode == kbPgDn ||
                         k.keyCode == kbHome || k.keyCode == kbEnd || k.keyCode == kbBack ||
                         (k.textLength > 0 && (uchar)k.text[0] >= 0x20 && !(k.controlKeyState & (kbCtrlShift | kbAltShift)));
            if (!moves)
                flushPending(true);
        }
        TWindow::handleEvent(e);
        if (ready && e.what == evBroadcast)
        {
            switch (e.message.command)
            {
            case cmServicePicked:
                servicePicked();
                clearEvent(e);
                break;
            case cmFolderPicked:
                gNewsLimit = 0;  // a Usenet group starts at its newest articles again
                gFilter.clear(); // a filter belongs to the folder it was typed in
                gSearch.clear(); // and so does a search
                gSearchHits.clear();
                pendContent = false; // the folder's load brings its first message
                deferLoad(true);
                clearEvent(e);
                break;
            case cmMsgPicked:
                if (!pendFolder)
                    deferLoad(false);
                clearEvent(e);
                break;
            case cmFilterChanged:
                viewChanged(true, true);
                clearEvent(e);
                break;
            case cmPaneFocused:
            {
                flushPending(true); // the pane you reach shows what you moved to
                void *v = e.message.infoPtr;
                setActivePane(v == folderPane ? 0 : v == msgPane ? 1
                                                : v == svcPane   ? 3
                                                                 : 2);
                if (v == contentPane)
                    markFocused(true); // you are reading it now
                clearEvent(e);
                break;
            }
            case cmFocusContent:
                if (contentPane)
                    contentPane->select();
                clearEvent(e);
                break;
            case cmEditDraft: // hand up to the app
                message(TProgram::application, evCommand, cmEditDraft, this);
                clearEvent(e);
                break;
            }
        }
    }

    void changeBounds(const TRect &bounds) override
    {
        TWindow::changeBounds(bounds);
        layout();
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
};

TWindow *newMailWindow(const TRect &bounds)
{
    return new TMailWindow(bounds);
}

void showMailWindow()
{
    if (gMailWin)
        gMailWin->select();
}

int mailFocusedRow()
{
    if (!gMailWin || !gMailWin->msgPane)
        return -1;
    short f = gMailWin->msgPane->focused;
    return (f >= 0 && f < (short)gRows.size()) ? (int)f : -1;
}

void mailMarkFocused(bool read)
{
    if (gMailWin)
        gMailWin->markFocused(read);
}

void mailReload()
{
    if (gMailWin)
        gMailWin->reloadFolder();
}

int mailInboxUnread()
{
    for (const Folder &f : gFolders)
        if (f.mbox == "spool")
            return f.unread;
    return -1; // the selected service has no inbox
}

void mailToggleServices()
{
    if (gMailWin)
        gMailWin->toggleServices();
}

void mailNews(ushort command)
{
    if (gMailWin)
        gMailWin->newsCommand(command);
}

void mailLists(ushort command)
{
    if (gMailWin)
        gMailWin->listsCommand(command);
}

void mailViewChanged(bool resetSel)
{
    if (gMailWin)
        gMailWin->viewChanged(resetSel);
}

// Edit > Search messages...: the words go to the backend (IMAP SEARCH, the mailbox, or the articles of a newsgroup), which
// answers with the numbers of the messages that have every word in From, To, Subject or text; the pane then shows only those.
// The load the mail window held back while keys were still coming (see TMailWindow::flushPending).
void mailFlushPending(bool force)
{
    if (gMailWin)
        gMailWin->flushPending(force);
}

void mailSearch()
{
    if (!gMailWin)
        return;
    showMailWindow(); // from a compose window too: the hits are in the mail window
    std::string q = askLine("Search messages", "~W~ords (all must match; \"quotes\" for a phrase)", gSearch);
    size_t a = q.find_first_not_of(" \t");
    if (a == std::string::npos)
        return;
    q = q.substr(a, q.find_last_not_of(" \t") - a + 1);
    std::string raw = backendRun("search " + shq(gMbox) + " " + shq(q));
    std::set<int> hits;
    std::string note, err;
    std::istringstream is(raw);
    std::string ln;
    while (std::getline(is, ln))
    {
        while (!ln.empty() && (ln.back() == '\r' || ln.back() == ' '))
            ln.pop_back();
        if (ln.empty())
            continue;
        if (ln[0] == '#')
            note = ln.substr(ln.find_first_not_of("# "));
        else if (ln.find_first_not_of("0123456789") == std::string::npos)
            hits.insert(atoi(ln.c_str()));
        else
            err += (err.empty() ? "" : " ") + ln;
    }
    if (!err.empty())
    {
        if (err.size() > 300)
            err.resize(300);
        notify("Search failed: " + err, noteError);
        return;
    }
    // only what the list holds can be shown (a message that arrived after the list was loaded is not in it: F5 loads it)
    std::set<int> loaded;
    for (const MsgRow &r : gAll)
        if (hits.count(r.idx))
            loaded.insert(r.idx);
    hits.swap(loaded);
    if (hits.empty())
    {
        notify("Nothing found for \"" + q + "\"" + (note.empty() ? "" : " - " + note) + ".", noteInfo);
        return;
    }
    gSearch = q;
    gSearchHits = hits;
    gFilter.clear();
    mailViewChanged(true); // applies the view
    if (gMailWin->msgPane)
        gMailWin->msgPane->select(); // the hits are what you look at now, and Esc (end the search) is the list's key
    notify(std::to_string(hits.size()) + (hits.size() == 1 ? " message" : " messages") + " found" +
               (note.empty() ? "" : " - " + note) + ". Esc shows them all again.",
           noteInfo);
}

void mailSetWrap(bool wrap)
{
    if (gMailWin && gMailWin->contentPane)
        gMailWin->contentPane->setWrap(wrap);
}
