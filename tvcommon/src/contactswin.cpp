// contactswin.cpp - the Contacts window, the same in tvmail, tvchat and tvcal (File > Contacts...): the address book
// on the server (Radicale, CardDAV) with add, edit and delete for contacts and for groups.  It does no networking of its
// own: it asks tvcommon's tvcontacts.py (list, save, delete, sync), which keeps the copy, checks ETags and says in
// words what went wrong.  With the server unreachable the list is the copy and every change is refused with that message.
#include "views.h"
#include <cstdio>

namespace
{
// Where tvcontacts.py is: $TVCONTACTS_PY, else beside the app's own helpers (each app installs it in its share directory).
std::string cliPath()
{
    std::vector<std::string> cand;
    if (const char *e = getenv("TVCONTACTS_PY"))
        cand.push_back(e);
    for (const char *app : {"tvmail", "tvchat", "tvcal"})
    {
        cand.push_back(std::string("/usr/local/share/") + app + "/tvcontacts.py");
        cand.push_back(std::string("/usr/share/") + app + "/tvcontacts.py");
    }
    cand.push_back("../tvcommon/py/tvcontacts.py");
    for (const std::string &p : cand)
        if (FILE *f = fopen(p.c_str(), "r"))
        {
            fclose(f);
            return p;
        }
    return std::string();
}

struct Result
{
    bool ok = false;
    std::string out; // stdout and stderr; on failure, the words to say
};

Result runCli(const std::string &args)
{
    Result r;
    std::string path = cliPath();
    if (path.empty())
    {
        r.out = "tvcontacts.py is not installed (it comes with tvmail, tvchat and tvcal).";
        return r;
    }
    std::string out = shCapture("python3 " + shq(path) + " " + args + " 2>&1; echo \"rc=$?\"");
    size_t at = out.rfind("rc=");
    int rc = at == std::string::npos ? 1 : atoi(out.c_str() + at + 3);
    r.out = at == std::string::npos ? out : out.substr(0, at);
    while (!r.out.empty() && (r.out.back() == '\n' || r.out.back() == '\r'))
        r.out.pop_back();
    r.ok = rc == 0;
    return r;
}

std::string trim(const std::string &s)
{
    size_t a = s.find_first_not_of(" \t\r\n"), b = s.find_last_not_of(" \t\r\n");
    return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
}

struct Row
{
    bool group = false;
    std::string uid, name, nicks, detail;
};

std::vector<std::string> tabs(const std::string &ln)
{
    std::vector<std::string> f;
    size_t at = 0;
    for (;;)
    {
        size_t t = ln.find('\t', at);
        f.push_back(ln.substr(at, t == std::string::npos ? std::string::npos : t - at));
        if (t == std::string::npos)
            break;
        at = t + 1;
    }
    return f;
}

// One entry's dialog: a contact (name, nicknames, addresses) or a group (name, members).  False = cancelled.
bool editDialog(Row &row, bool isNew)
{
    const bool g = row.group;
    TDialog *d = new TDialog(TRect(0, 0, 66, g ? 12 : 14), g ? (isNew ? "New group" : "Edit group") : (isNew ? "New contact" : "Edit contact"));
    d->options |= ofCentered;
    TInputLine *name = new TInputLine(TRect(3, 3, 63, 4), 120);
    d->insert(name);
    d->insert(new TLabel(TRect(2, 2, 63, 3), "~N~ame", name));
    TInputLine *nick = nullptr, *what = nullptr;
    int y = 5;
    if (!g)
    {
        nick = new TInputLine(TRect(3, y + 1, 63, y + 2), 200);
        d->insert(nick);
        d->insert(new TLabel(TRect(2, y, 63, y + 1), "N~i~cknames (comma separated)", nick));
        y += 3;
    }
    what = new TInputLine(TRect(3, y + 1, 63, y + 2), 800);
    d->insert(what);
    d->insert(new TLabel(TRect(2, y, 63, y + 1), g ? "~M~embers (names, nicknames or addresses, comma separated)"
                                                    : "~E~mail addresses (comma separated)", what));
    y += 3;
    d->insert(new TButton(TRect(16, y, 28, y + 2), "O~K~", cmOK, bfDefault));
    d->insert(new TButton(TRect(32, y, 44, y + 2), "Cancel", cmCancel, bfNormal));
    char b[801];
    putText(b, row.name, 120);
    name->setData(b);
    if (nick)
    {
        putText(b, row.nicks, 200);
        nick->setData(b);
    }
    putText(b, row.detail, 800);
    what->setData(b);
    d->selectNext(False);
    name->select();
    ushort r = TProgram::deskTop->execView(d);
    if (r != cmCancel)
    {
        row.name = trim(name->data ? name->data : "");
        row.nicks = nick ? trim(nick->data ? nick->data : "") : std::string();
        row.detail = trim(what->data ? what->data : "");
    }
    TObject::destroy(d);
    return r != cmCancel;
}

class TContactsPane : public TListViewer
{
public:
    std::vector<Row> rows;
    std::string note;

    TContactsPane(const TRect &b, TScrollBar *vsb) : TListViewer(b, 1, nullptr, vsb) { reload(false); }

    void reload(bool force)
    {
        std::string keep = focused >= 0 && focused < (short)rows.size() ? rows[focused].uid : std::string();
        Result r = runCli(force ? "sync" : "list");
        if (force && r.ok)
            r = runCli("list");
        rows.clear();
        note.clear();
        if (!r.ok)
        {
            note = r.out;
            notify(r.out, noteError);
        }
        size_t at = 0;
        while (r.ok && at <= r.out.size())
        {
            size_t nl = r.out.find('\n', at);
            std::string ln = r.out.substr(at, nl == std::string::npos ? std::string::npos : nl - at);
            at = nl == std::string::npos ? r.out.size() + 1 : nl + 1;
            if (!ln.empty() && ln.back() == '\r')
                ln.pop_back();
            std::vector<std::string> f = tabs(ln);
            if (f.size() >= 5 && (f[0] == "c" || f[0] == "g"))
            {
                Row row;
                row.group = f[0] == "g";
                row.uid = f[1];
                row.name = f[2];
                row.nicks = f[3];
                row.detail = f[4];
                rows.push_back(row);
            }
            else if (!ln.empty())
                note = ln; // e.g. "offline: this is the copy from the last time ..."
        }
        setRange((short)rows.size());
        short sel = 0;
        for (size_t i = 0; i < rows.size(); ++i)
            if (rows[i].uid == keep)
                sel = (short)i;
        if (!rows.empty())
            focusItem(sel);
        drawView();
        message(owner, evBroadcast, cmContactsNote, nullptr); // let the window refresh its note line
    }

    void getText(char *dest, short item, short maxLen) override
    {
        if (item < 0 || item >= (short)rows.size())
        {
            dest[0] = 0;
            return;
        }
        const Row &r = rows[item];
        std::string line = std::string(r.group ? "[G] " : "    ") + fitCols(r.name, 24) + " " +
                           (r.nicks.empty() ? std::string() : "(" + r.nicks + ") ") + r.detail;
        putText(dest, line, maxLen);
    }

    void addEntry(bool group)
    {
        Row row;
        row.group = group;
        if (!editDialog(row, true) || row.name.empty())
            return;
        save(row, std::string());
    }

    void editEntry()
    {
        if (focused < 0 || focused >= (short)rows.size())
            return;
        Row row = rows[focused];
        std::string uid = row.uid;
        if (!editDialog(row, false) || row.name.empty())
            return;
        save(row, uid);
    }

    void save(const Row &row, const std::string &uid)
    {
        std::string args = "save --name " + shq(row.name) + (uid.empty() ? "" : " --uid " + shq(uid));
        if (row.group)
            args += " --group --members " + shq(row.detail);
        else
            args += " --nicks " + shq(row.nicks) + " --emails " + shq(row.detail);
        Result r = runCli(args);
        if (!r.ok)
            notify(r.out, noteError);
        reload(false);
    }

    void deleteEntry()
    {
        if (focused < 0 || focused >= (short)rows.size())
            return;
        const Row &row = rows[focused];
        if (messageBox(mfConfirmation | mfYesButton | mfNoButton, row.group ? "Delete the group \"%s\"?\n\nIts members stay in the book."
                                                                            : "Delete the contact \"%s\"?",
                       row.name.c_str()) != cmYes)
            return;
        Result r = runCli("delete " + shq(row.uid));
        if (!r.ok)
            notify(r.out, noteError);
        reload(false);
    }

    void handleEvent(TEvent &e) override
    {
        if (e.what == evKeyDown)
        {
            ushort k = e.keyDown.keyCode;
            char c = e.keyDown.charScan.charCode;
            if (k == kbEnter || c == 'e' || c == 'E')
                editEntry();
            else if (k == kbIns || c == 'n' || c == 'N')
                addEntry(false);
            else if (c == 'g' || c == 'G')
                addEntry(true);
            else if (k == kbDel)
                deleteEntry();
            else if (k == kbF5 || c == 'r' || c == 'R')
                reload(true);
            else
            {
                TListViewer::handleEvent(e);
                return;
            }
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

// a one-line note whose text can be changed
class TNoteLine : public TStaticText
{
public:
    TNoteLine(const TRect &b) : TStaticText(b, "") {}
    void set(const std::string &t)
    {
        delete[] const_cast<char *>(text);
        text = newStr(t.c_str());
        drawView();
    }
};

class TContactsWindow : public TViewerWindow
{
    TContactsPane *pane;
    TNoteLine *hint;

public:
    TContactsWindow(const TRect &b)
        : TWindowInit(&TContactsWindow::initFrame), TViewerWindow(b, "Contacts")
    {
        palette = wpCyanWindow;
        options |= ofTileable;
        TScrollBar *vsb = new TScrollBar(TRect(size.x - 1, 1, size.x, size.y - 2));
        insert(vsb);
        pane = new TContactsPane(TRect(1, 1, size.x - 1, size.y - 2), vsb);
        insert(pane);
        hint = new TNoteLine(TRect(1, size.y - 2, size.x - 1, size.y - 1));
        insert(hint);
        showHint();
    }

    void showHint()
    {
        std::string t = pane->note.empty() ? std::string("Ins/n new  g group  Enter/e edit  Del delete  r/F5 reload  Esc close")
                                           : pane->note;
        hint->set(fitCols(t, size.x - 2));
    }

    void handleEvent(TEvent &e) override
    {
        TViewerWindow::handleEvent(e);
        if (e.what == evBroadcast && e.message.command == cmContactsNote)
            showHint();
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
        }
        return TWindow::mapColor(i);
    }
};
} // namespace

TWindow *newContactsWindow(const TRect &bounds)
{
    return new TContactsWindow(bounds);
}

// Add an address (a sender, say) to the address book unless it is there already: the words to say, and whether it worked.
bool contactsAddAddress(const std::string &name, const std::string &email, std::string &say)
{
    Result r = runCli("add-address --email " + shq(email) + " --name " + shq(name));
    say = r.out.empty() && !r.ok ? std::string("could not add the address") : r.out;
    return r.ok;
}
