// mailwin.cpp - the mail data (folders, the message list, its filter and
// sort) and the 3-pane mail window that shows it.
#include "tvmail.h"
#include "views.h"

std::vector<MsgRow> gAll;  // everything the backend listed
std::vector<MsgRow> gRows; // what the pane shows: gAll, filtered and sorted
                                  // (MsgRow::idx is still the backend index)
SortKey gSortKey = sortDate;
bool gSortRev = false;
std::string gFilter; // type-to-filter text (From / Subject, any case)
const Folder gFolders[] = {
    {"inbox", "spool"},
    {"drafts", "drafts"},
    {"sent", "sent"},
    {"saved", "mbox"},
    {"spam", "spam"},
    {"trash", "trash"},
    {"dead.letter", "dead"}, // always a local file, whatever the mode
};
const int gFolderCount = int(sizeof gFolders / sizeof gFolders[0]);
int gFolderIdx = 0; // current folder (drives gMbox)
std::string gMbox = gFolders[0].mbox;
bool gRemote = false; // backend "mode" == remote (IMAP)
std::string gHost;    // backend "host": the box we're talking to

std::string mboxArg() { return gMbox.empty() ? std::string() : " '" + gMbox + "'"; }
std::string mboxOpt() { return gMbox.empty() ? std::string() : " --mbox '" + gMbox + "'"; }

static std::string mailWindowTitle()
{
    if (gHost.empty())
        return "tvmail";
    return "tvmail - " + gHost + (gRemote ? " (remote)" : " (local)");
}

// Rebuild gRows from gAll: apply the type-to-filter text, then the sort.
static void applyView()
{
    gRows.clear();
    std::string f = lowered(gFilter);
    for (const MsgRow &r : gAll)
        if (f.empty() || lowered(r.from).find(f) != std::string::npos ||
            lowered(r.subj).find(f) != std::string::npos)
            gRows.push_back(r);
    switch (gSortKey)
    {
    case sortDate:
        break; // mailbox order
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

// Per-folder message and unread counts, for "inbox (3)" in the folder pane.
// -1 = not known (the backend had no line for it).
static int gTotal[gFolderCount];
static int gUnread[gFolderCount];

static void loadCounts()
{
    for (int i = 0; i < gFolderCount; ++i)
        gTotal[i] = gUnread[i] = -1;
    std::istringstream is(backendRun("counts"));
    std::string line;
    while (std::getline(is, line))
    {
        std::istringstream ls(line);
        std::string name, tot, uns;
        std::getline(ls, name, '\t');
        std::getline(ls, tot, '\t');
        std::getline(ls, uns);
        if (tot.empty() || uns.empty() ||
            tot.find_first_not_of("0123456789") != std::string::npos ||
            uns.find_first_not_of("0123456789\r") != std::string::npos)
            continue; // an error line, not a count
        for (int i = 0; i < gFolderCount; ++i)
            if (name == gFolders[i].mbox)
            {
                gTotal[i] = std::atoi(tot.c_str());
                gUnread[i] = std::atoi(uns.c_str());
            }
    }
}

static void loadList()
{
    gAll.clear();
    std::string raw = backendRun("list " + gMbox);
    std::istringstream is(raw);
    std::string line;
    while (std::getline(is, line))
    {
        if (line.empty() || line.find('\t') == std::string::npos)
            continue;
        std::istringstream ls(line);
        std::string idx, flag, date, from, subj;
        std::getline(ls, idx, '\t');
        std::getline(ls, flag, '\t');
        std::getline(ls, date, '\t');
        std::getline(ls, from, '\t');
        std::getline(ls, subj);
        MsgRow r;
        r.idx = std::atoi(idx.c_str());
        r.flag = flag.empty() ? '.' : flag[0];
        r.date = date;
        r.from = from;
        r.subj = subj;
        gAll.push_back(r);
    }
    applyView();
}

// ================================================= 3-pane mail window =======
// One full-screen window: folders (left) | message list (top-right) /
// message content (bottom-right).  Panes talk via evBroadcast: folder focus
// reloads the list, message focus loads the content.

class TDivider : public TView
{
    bool vert;

public:
    TDivider(const TRect &b, bool v) : TView(b), vert(v) {}
    void draw() override
    {
        TColorAttr c = cFrame();
        TDrawBuffer b;
        if (vert)
        {
            b.moveChar(0, char(0xB3), c, 1); // vertical bar
            for (short y = 0; y < size.y; ++y)
                writeLine(0, y, 1, 1, b);
        }
        else
        {
            b.moveChar(0, char(0xC4), c, size.x); // horizontal bar
            writeLine(0, 0, size.x, 1, b);
        }
    }
};

class TFolderPane : public TListViewer
{
public:
    TFolderPane(const TRect &b) : TListViewer(b, 1, nullptr, nullptr)
    {
        setRange(gFolderCount);
        TListViewer::focusItem(gFolderIdx);
    }
    void getText(char *dest, short item, short maxLen) override
    {
        if (item < 0 || item >= gFolderCount)
        {
            dest[0] = 0;
            return;
        }
        // "inbox (3)": unread for the mail folders, how many are waiting
        // for drafts; nothing for sent / trash / dead.letter.
        std::string label = gFolders[item].name;
        const std::string m = gFolders[item].mbox;
        int n = -1;
        if (m == "spool" || m == "mbox" || m == "spam")
            n = gUnread[item];
        else if (m == "drafts")
            n = gTotal[item];
        if (n > 0)
            label += " (" + std::to_string(n) + ")";
        putText(dest, label, maxLen);
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
                          fitCols(r.from, 20) + " " + r.subj,
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
                gMbox == "drafts" ? cmEditDraft : cmFocusContent, this);
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
            if (f.empty())
                return false;
            f.clear();
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

// A one-row heading above a pane.  Reverse-video when its pane holds focus,
// dim otherwise - the "which pane am I in?" cue.  The one over the body also
// serves as the rule between the message list and the message text.
class TPaneTitle : public TView
{
    std::string label;
    Boolean active = False;

public:
    TPaneTitle(const TRect &b, const char *l) : TView(b), label(l) {}
    void setActive(Boolean a)
    {
        if (a != active)
        {
            active = a;
            drawView();
        }
    }
    void setLabel(const std::string &l)
    {
        if (l != label)
        {
            label = l;
            drawView();
        }
    }
    void draw() override
    {
        TColorAttr rule = cDim();
        TColorAttr lab = active ? cFocus() : cNorm();
        TDrawBuffer b;
        b.moveChar(0, char(0xC4), rule, size.x); // horizontal rule
        std::string s = active ? "[ " + label + " ]" : "  " + label + "  ";
        b.moveStr(2, s.c_str(), lab);
        writeLine(0, 0, size.x, 1, b);
    }
    TColorAttr mapColor(uchar) override { return active ? cFocus() : cNorm(); }
};

class TMailWindow;
static TMailWindow *gMailWin = nullptr; // the one 3-pane window, once made

class TMailWindow : public TWindow
{
    Boolean ready = False;
    TScrollBar *msgVsb = nullptr, *contentVsb = nullptr;
    TDivider *vdiv = nullptr;
    TPaneTitle *folderTitle = nullptr, *msgTitle = nullptr, *contentTitle = nullptr;
    int activePane = 0;

    struct Rects
    {
        TRect folderTitle, folder, vbar, msgTitle, msg, msgVsb,
            contentTitle, content, contentVsb;
    };
    Rects paneRects() const
    {
        int W = size.x, H = size.y;
        const int FW = 22;
        int sy = 1 + (H - 2) * 2 / 5;
        if (sy < 6)
            sy = 6; // title + a few rows + body title
        if (sy > H - 5)
            sy = H - 5;
        Rects r;
        r.folderTitle = TRect(1, 1, 1 + FW, 2);
        r.folder = TRect(1, 2, 1 + FW, H - 1);
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

        Rects R = paneRects();
        folderTitle = new TPaneTitle(R.folderTitle, "Folders");
        msgTitle = new TPaneTitle(R.msgTitle, "Messages");
        contentTitle = new TPaneTitle(R.contentTitle, "Message");
        folderPane = new TFolderPane(R.folder);
        vdiv = new TDivider(R.vbar, true);
        msgVsb = new TScrollBar(R.msgVsb);
        msgPane = new TMsgPane(R.msg, msgVsb);
        contentVsb = new TScrollBar(R.contentVsb);
        contentPane = new TContentPane(R.content, nullptr, contentVsb);

        insert(vdiv);
        insert(folderTitle);
        insert(msgTitle);
        insert(contentTitle);
        insert(msgVsb);
        insert(contentVsb);
        // insert order sets the Tab cycle: folders -> messages -> body
        insert(msgPane);
        insert(contentPane);
        insert(folderPane);

        gMailWin = this;
        loadCounts();
        loadFolder();
        ready = True;
        folderPane->select();
        setActivePane(0);
    }

    void setActivePane(int p)
    {
        activePane = p;
        if (folderTitle)
            folderTitle->setActive(Boolean(p == 0));
        if (msgTitle)
            msgTitle->setActive(Boolean(p == 1));
        if (contentTitle)
            contentTitle->setActive(Boolean(p == 2));
    }

    // Where to put the highlight when there is no message to stay on: in
    // date (mailbox) order the newest message - the last row, or the first
    // when reversed - so a folder opens on what just arrived, not on the
    // oldest mail at the top; in the other orders, the first row.
    static int startRow()
    {
        if (gSortKey != sortDate || gRows.empty())
            return 0;
        int best = 0;
        for (int i = 1; i < (int)gRows.size(); ++i)
            if (gRows[i].idx > gRows[best].idx)
                best = i;
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
        gMbox = gFolders[gFolderIdx].mbox;
        loadList();
        // the list we just read is the freshest word on this folder's counts
        if (gMbox != "dead")
        {
            gTotal[gFolderIdx] = (int)gAll.size();
            gUnread[gFolderIdx] = (int)std::count_if(gAll.begin(), gAll.end(),
                                                     [](const MsgRow &r)
                                                     { return r.flag == 'N'; });
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

    // Folder-pane numbers and the Messages heading (filter, sort order).
    void refreshTitles()
    {
        if (!msgTitle)
            return;
        std::string t = "Messages";
        if (!gFilter.empty())
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
    void viewChanged(bool resetSel)
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
        if (gUnread[gFolderIdx] >= 0)
            gUnread[gFolderIdx] = std::max(0, gUnread[gFolderIdx] + (read ? -1 : 1));
        msgPane->drawView();
        folderPane->drawView();
        if (gSortKey == sortUnread) // the order depends on the flag
            viewChanged(false);
    }

    void reloadFolder()
    {
        loadCounts();
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
        TWindow::handleEvent(e);
        if (ready && e.what == evBroadcast)
        {
            switch (e.message.command)
            {
            case cmFolderPicked:
                gFilter.clear(); // a filter belongs to the folder it was typed in
                loadFolder();
                clearEvent(e);
                break;
            case cmMsgPicked:
                loadContent();
                clearEvent(e);
                break;
            case cmFilterChanged:
                viewChanged(true);
                clearEvent(e);
                break;
            case cmPaneFocused:
            {
                void *v = e.message.infoPtr;
                setActivePane(v == folderPane ? 0 : v == msgPane ? 1
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
        Rects R = paneRects();
        folderTitle->changeBounds(R.folderTitle);
        folderPane->changeBounds(R.folder);
        vdiv->changeBounds(R.vbar);
        msgTitle->changeBounds(R.msgTitle);
        msgPane->changeBounds(R.msg);
        msgVsb->changeBounds(R.msgVsb);
        contentTitle->changeBounds(R.contentTitle);
        contentPane->changeBounds(R.content);
        contentVsb->changeBounds(R.contentVsb);
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

void mailViewChanged(bool resetSel)
{
    if (gMailWin)
        gMailWin->viewChanged(resetSel);
}

void mailSetWrap(bool wrap)
{
    if (gMailWin && gMailWin->contentPane)
        gMailWin->contentPane->setWrap(wrap);
}
