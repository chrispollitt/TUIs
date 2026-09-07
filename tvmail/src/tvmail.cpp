// tvmail - a Turbo Vision front-end for a local mbox + the tvmail-backend helper.
//
// On Cygwin this builds as a native ncurses app (build.sh patches tvision for
// the missing FIONREAD &c).  `build.sh --mingw` instead makes a static .exe
// for a real Windows console.  Either way, every mail operation is emitted as a
// small bash script and run via the Cygwin `bash` in TVMAIL_BASH; the C++ side
// just orchestrates windows.
//
// Build: ../build.sh

#define Uses_TApplication
#define Uses_TKeys
#define Uses_TRect
#define Uses_TBackground
#define Uses_TDeskTop
#define Uses_TMenuBar
#define Uses_TMenuItem
#define Uses_TSubMenu
#define Uses_TStatusLine
#define Uses_TStatusItem
#define Uses_TStatusDef
#define Uses_TDeskTop
#define Uses_TWindow
#define Uses_TFrame
#define Uses_TScrollBar
#define Uses_TScroller
#define Uses_TListViewer
#define Uses_TDrawBuffer
#define Uses_TEvent
#define Uses_MsgBox
#define Uses_TInputLine
#define Uses_TLabel
#define Uses_TEditor
#define Uses_TIndicator
#define Uses_TDialog
#define Uses_TButton
#define Uses_TCheckBoxes
#define Uses_TSItem
#define Uses_THistory
#define Uses_TCommandSet
#include <tvision/tv.h>

#include <string>
#include <vector>
#include <sstream>
#include <algorithm>
#include <cctype>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#ifdef _WIN32
#  include <windows.h>
#  define popen  _popen
#  define pclose _pclose
#else
#  include <unistd.h>
#endif

#ifndef TVMAIL_BASH
#  define TVMAIL_BASH "bash"
#endif

// ---------------------------------------------------------------- commands ---
const ushort cmPull         = 2000;
const ushort cmReload       = 2001;
const ushort cmOpenMsg      = 2002;
const ushort cmReplyMsg     = 2003;
const ushort cmCompose      = 2004;
const ushort cmDeleteMsg    = 2005;
const ushort cmViewSrc      = 2006;
const ushort cmAboutBox     = 2007;
const ushort cmFolderSpool  = 2008;
const ushort cmFolderMbox   = 2009;
const ushort cmFolderOther  = 2010;
const ushort cmSendMsg      = 2011;   // send the focused compose window

// ------------------------------------------------------ editor dialogs -----
// Wire up the standard Find / Replace / "search failed" dialogs the TEditor
// needs (tvision ships only a cmCancel stub).  Lifted from tvision's tvedit
// example (tvedit2/3.cpp), trimmed to what a composer uses.
static ushort execDialog(TDialog *d, void *data)
{
    TView *p = TProgram::application->validView(d);
    if (!p) return cmCancel;
    if (data) p->setData(data);
    ushort result = TProgram::deskTop->execView(p);
    if (result != cmCancel && data) p->getData(data);
    TObject::destroy(p);
    return result;
}

static TDialog *createFindDialog()
{
    TDialog *d = new TDialog(TRect(0, 0, 38, 12), "Find");
    d->options |= ofCentered;
    TInputLine *c = new TInputLine(TRect(3, 3, 32, 4), 80);
    d->insert(c);
    d->insert(new TLabel(TRect(2, 2, 15, 3), "~T~ext to find", c));
    d->insert(new THistory(TRect(32, 3, 35, 4), c, 10));
    d->insert(new TCheckBoxes(TRect(3, 5, 35, 7),
        new TSItem("~C~ase sensitive",
        new TSItem("~W~hole words only", 0))));
    d->insert(new TButton(TRect(14, 9, 24, 11), "O~K~", cmOK, bfDefault));
    d->insert(new TButton(TRect(26, 9, 36, 11), "Cancel", cmCancel, bfNormal));
    d->selectNext(False);
    return d;
}

static TDialog *createReplaceDialog()
{
    TDialog *d = new TDialog(TRect(0, 0, 40, 16), "Replace");
    d->options |= ofCentered;
    TInputLine *c = new TInputLine(TRect(3, 3, 34, 4), 80);
    d->insert(c);
    d->insert(new TLabel(TRect(2, 2, 15, 3), "~T~ext to find", c));
    d->insert(new THistory(TRect(34, 3, 37, 4), c, 10));
    c = new TInputLine(TRect(3, 6, 34, 7), 80);
    d->insert(c);
    d->insert(new TLabel(TRect(2, 5, 12, 6), "~N~ew text", c));
    d->insert(new THistory(TRect(34, 6, 37, 7), c, 11));
    d->insert(new TCheckBoxes(TRect(3, 8, 37, 12),
        new TSItem("~C~ase sensitive",
        new TSItem("~W~hole words only",
        new TSItem("~P~rompt on replace",
        new TSItem("~R~eplace all", 0))))));
    d->insert(new TButton(TRect(17, 13, 27, 15), "O~K~", cmOK, bfDefault));
    d->insert(new TButton(TRect(28, 13, 38, 15), "Cancel", cmCancel, bfNormal));
    d->selectNext(False);
    return d;
}

static ushort tvmailEditDialog(int dialog, ...)
{
    va_list arg;
    switch (dialog) {
        case edOutOfMemory:
            return messageBox("Not enough memory for this operation.",
                              mfError | mfOKButton);
        case edFind: {
            va_start(arg, dialog);
            void *p = va_arg(arg, void *);
            va_end(arg);
            return execDialog(createFindDialog(), p);
        }
        case edSearchFailed:
            return messageBox("Search string not found.", mfError | mfOKButton);
        case edReplace: {
            va_start(arg, dialog);
            void *p = va_arg(arg, void *);
            va_end(arg);
            return execDialog(createReplaceDialog(), p);
        }
        case edReplacePrompt:
            return messageBox("Replace this occurrence?",
                              mfYesNoCancel | mfInformation);
    }
    return cmCancel;
}

// -------------------------------------------------------- shell plumbing ----
// Everything the backend needs runs inside one bash script so we never fight
// cmd.exe quoting.  The preamble fixes PATH so tvmail-backend is found.
static const char *kPreamble =
    "export PATH=\"$HOME/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH\"\n";

static std::string tempDir()
{
#ifdef _WIN32
    char b[MAX_PATH];
    DWORD n = GetTempPathA(MAX_PATH, b);
    return std::string(b, n);
#else
    const char *t = getenv("TMPDIR");
    return (t && *t) ? std::string(t) + "/" : std::string("/tmp/");
#endif
}

static long procId()
{
#ifdef _WIN32
    return (long)GetCurrentProcessId();
#else
    return (long)getpid();
#endif
}

static std::string writeScript(const std::string &body)
{
    static int seq = 0;
    std::string path = tempDir() + "tvmail_" + std::to_string(procId())
                     + "_" + std::to_string(++seq) + ".sh";
    if (FILE *f = fopen(path.c_str(), "wb")) {
        fputs(kPreamble, f);
        fputs(body.c_str(), f);
        fputc('\n', f);
        fclose(f);
    }
    return path;
}

// a plain temp file (e.g. an RFC822 draft) - path is fine for Cygwin bash
static std::string writeTemp(const std::string &content, const char *suffix)
{
    static int seq = 0;
    std::string path = tempDir() + "tvmail_msg_" + std::to_string(procId())
                     + "_" + std::to_string(++seq) + suffix;
    if (FILE *f = fopen(path.c_str(), "wb")) {
        fwrite(content.data(), 1, content.size(), f);
        fclose(f);
    }
    return path;
}

static std::string q(const std::string &s) { return "\"" + s + "\""; }
static std::string bashInvoke(const std::string &script)
{
    return q(TVMAIL_BASH) + " " + q(script);
}

static std::string shCapture(const std::string &body)
{
    std::string sp = writeScript(body), out;
    if (FILE *p = popen(bashInvoke(sp).c_str(), "r")) {
        char buf[8192]; size_t n;
        while ((n = fread(buf, 1, sizeof buf, p)) > 0) out.append(buf, n);
        pclose(p);
    }
    remove(sp.c_str());
    return out;
}

static int shInteractive(const std::string &body)
{
    std::string sp = writeScript(body);
    TProgram::application->suspend();
    std::fputs("\n", stdout);
    int rc = std::system(bashInvoke(sp).c_str());
    std::fputs("\n[tvmail] done - press Enter to return ", stdout);
    std::fflush(stdout);
    for (int c; (c = std::getchar()) != '\n' && c != EOF; ) {}
    TProgram::application->resume();
    TProgram::application->redraw();
    remove(sp.c_str());
    return rc;
}

// -------------------------------------------------------- classic colors ----
// Turbo Vision DOS palette — hard-coded so we never depend on terminal quirks.
static inline TColorAttr cNorm()  { return TColorAttr(TColorBIOS(0x00), TColorBIOS(0x03)); } // black on cyan
static inline TColorAttr cHi()    { return TColorAttr(TColorBIOS(0x0F), TColorBIOS(0x03)); } // white on cyan
static inline TColorAttr cSel()   { return TColorAttr(TColorBIOS(0x0F), TColorBIOS(0x0B)); } // white on light-cyan
static inline TColorAttr cDiv()   { return TColorAttr(TColorBIOS(0x03), TColorBIOS(0x03)); } // cyan on cyan
static inline TColorAttr cFrame() { return TColorAttr(TColorBIOS(0x0F), TColorBIOS(0x03)); } // white on cyan
static inline TColorAttr cDim()   { return TColorAttr(TColorBIOS(0x08), TColorBIOS(0x03)); } // grey on cyan

// ---------------------------------------------------------------- data -------
struct MsgRow { int idx = 0; char flag = '.'; std::string date, from, subj; };
static std::vector<MsgRow> gRows;
static std::string gMbox;                        // "", "spool", "mbox", or a path
static std::string gTitle = "Mailbox";           // list-window title (must outlive it)

static std::string mboxArg() { return gMbox.empty() ? std::string() : " '" + gMbox + "'"; }
static std::string mboxOpt() { return gMbox.empty() ? std::string() : " --mbox '" + gMbox + "'"; }
static const char *folderLabel()
{
    if (gMbox.empty() || gMbox == "spool") return "spool  (/var/mail)";
    if (gMbox == "mbox") return "~/mbox";
    return gMbox.c_str();
}

static std::vector<std::string> splitLines(const std::string &s)
{
    std::vector<std::string> v;
    std::istringstream is(s);
    std::string ln;
    while (std::getline(is, ln)) {
        if (!ln.empty() && ln.back() == '\r') ln.pop_back();
        v.push_back(ln);
    }
    if (v.empty()) v.push_back(std::string());
    return v;
}

static void loadList()
{
    gRows.clear();
    std::string raw = shCapture("tvmail-backend list" + mboxArg() + " 2>/dev/null");
    std::istringstream is(raw);
    std::string line;
    while (std::getline(is, line)) {
        if (line.empty()) continue;
        std::istringstream ls(line);
        std::string idx, flag, date, from, subj;
        std::getline(ls, idx,  '\t');
        std::getline(ls, flag, '\t');
        std::getline(ls, date, '\t');
        std::getline(ls, from, '\t');
        std::getline(ls, subj);
        MsgRow r;
        r.idx  = std::atoi(idx.c_str());
        r.flag = flag.empty() ? '.' : flag[0];
        r.date = date; r.from = from; r.subj = subj;
        gRows.push_back(r);
    }
}

// ------------------------------------------------------- blue desktop ------
// Some terminals map Turbo Vision's default desktop palette to a jarring
// colour (seen: solid red). Paint it the classic blue explicitly.
class TBlueBg : public TBackground {
public:
    TBlueBg(const TRect &r) : TBackground(r, char(0xB0)) {}   // 0xB0 = light shade
    void draw() override
    {
        TColorAttr c(TColorBIOS(0x07), TColorBIOS(0x01));     // grey on blue
        TDrawBuffer b;
        b.moveChar(0, pattern, c, size.x);
        for (short y = 0; y < size.y; ++y)
            writeLine(0, y, size.x, 1, b);
    }
};

class TBlueDeskTop : public TDeskTop {
public:
    TBlueDeskTop(const TRect &r)
        : TDeskInit(&TBlueDeskTop::initBackground), TDeskTop(r) {}
    static TBackground *initBackground(TRect r) { return new TBlueBg(r); }
};

// ---------------------------------------------------------- message list ----
class TMsgList : public TListViewer {
public:
    TMsgList(const TRect &b, TScrollBar *vsb) : TListViewer(b, 1, nullptr, vsb)
    {
        setRange((short)gRows.size());
    }

    // Override mapColor (virtual in TView) — NOT getColor
    TColorAttr mapColor(uchar index) override
    {
        switch (index) {
            case 1: return cNorm();  // active
            case 2: return cNorm();  // inactive
            case 3: return cHi();    // focused
            case 4: return cSel();   // selected
            case 5: return cDiv();   // divider
        }
        return TView::mapColor(index);
    }

    void getText(char *dest, short item, short maxLen) override
    {
        if (item < 0 || item >= (short)gRows.size()) { dest[0] = 0; return; }
        const MsgRow &r = gRows[item];
        char line[600];
        std::snprintf(line, sizeof line, "%c  %-16.16s  %-24.24s  %s",
                      r.flag, r.date.c_str(), r.from.c_str(), r.subj.c_str());
        std::strncpy(dest, line, maxLen);
        dest[maxLen] = 0;
    }

    void selectItem(short) override
    {
        message(TProgram::application, evCommand, cmOpenMsg, nullptr);
    }

    // Enter opens the focused message - handled here (not as a global menu
    // accelerator) so Enter still means "newline" inside the compose editor.
    void handleEvent(TEvent &e) override
    {
        if (e.what == evKeyDown && e.keyDown.keyCode == kbEnter) {
            if (focused < range) selectItem(focused);
            clearEvent(e);
            return;
        }
        TListViewer::handleEvent(e);
    }

    void refresh()
    {
        setRange((short)gRows.size());
        if (focused >= (short)gRows.size())
            focusItem(gRows.empty() ? 0 : (short)gRows.size() - 1);
        drawView();
    }
};

class TMailListWindow : public TWindow {
public:
    TMsgList *list = nullptr;
    TMailListWindow(const TRect &b)
        : TWindowInit(&TMailListWindow::initFrame),
          TWindow(b, gTitle.c_str(), wnNoNumber)
    {
        palette = wpCyanWindow;          // <-- use cyan window palette
        flags &= ~(wfClose | wfZoom);
        options |= ofTileable;
        TScrollBar *vsb = new TScrollBar(TRect(size.x - 1, 1, size.x, size.y - 1));
        insert(vsb);
        list = new TMsgList(TRect(1, 1, size.x - 1, size.y - 1), vsb);
        insert(list);
    }

    TColorAttr mapColor(uchar index) override
    {
        switch (index) {
            case 1: return cDim();   // frame passive
            case 2: return cFrame(); // frame active
            case 3: return cFrame(); // frame icon
            case 4: return cNorm();  // scrollbar page
            case 5: return cHi();    // scrollbar controls
            case 6: return cNorm();  // scroller normal
            case 7: return cHi();    // scroller selected
            case 8: return cNorm();  // reserved
        }
        return TView::mapColor(index);
    }
};

// --------------------------------------------------------- message viewer ---
class TTextView : public TScroller {
    std::vector<std::string> lines;
public:
    TTextView(const TRect &b, TScrollBar *h, TScrollBar *v, std::vector<std::string> ls)
        : TScroller(b, h, v), lines(std::move(ls))
    {
        growMode = gfGrowHiX | gfGrowHiY;
        size_t w = 1;
        for (auto &l : lines) w = std::max(w, l.size());
        setLimit((int)w + 1, (int)lines.size());
    }

    TColorAttr mapColor(uchar index) override
    {
        switch (index) {
            case 1: return cNorm();  // normal text
            case 2: return cHi();    // selected text
        }
        return TView::mapColor(index);
    }

    void draw() override
    {
        TColorAttr c = getColor(1);
        for (short y = 0; y < size.y; ++y) {
            TDrawBuffer b;
            b.moveChar(0, ' ', c, size.x);
            int li = delta.y + y;
            if (li >= 0 && li < (int)lines.size()) {
                const std::string &s = lines[li];
                if (delta.x < (int)s.size())
                    b.moveStr(0, s.c_str() + delta.x, c);
            }
            writeLine(0, y, size.x, 1, b);
        }
    }
};

class TMailViewWindow : public TWindow {
public:
    TMailViewWindow(const TRect &b, const char *title, std::vector<std::string> lines)
        : TWindowInit(&TMailViewWindow::initFrame),
          TWindow(b, title, wnNoNumber)
    {
        palette = wpCyanWindow;          // <-- use cyan window palette
        options |= ofTileable;
        TScrollBar *v = standardScrollBar(sbVertical   | sbHandleKeyboard);
        TScrollBar *h = standardScrollBar(sbHorizontal | sbHandleKeyboard);
        TRect r = getExtent();
        r.grow(-1, -1);
        insert(new TTextView(r, h, v, std::move(lines)));
    }

    TColorAttr mapColor(uchar index) override
    {
        switch (index) {
            case 1: return cDim();
            case 2: return cFrame();
            case 3: return cFrame();
            case 4: return cNorm();
            case 5: return cHi();
            case 6: return cNorm();
            case 7: return cHi();
            case 8: return cNorm();
        }
        return TView::mapColor(index);
    }
};

// -------------------------------------------------------- compose window ----
// A real in-app composer (cf. third_party/tvision/examples/tvedit): To/Cc/
// Subject input lines over a TEditor body with a scrollbar + L:C indicator.
// F2 sends via `tvmail-backend send`; closing a modified draft asks first.

class TBodyEditor : public TEditor {
public:
    TBodyEditor(const TRect &b, TScrollBar *h, TScrollBar *v, TIndicator *i, uint sz)
        : TEditor(b, h, v, i, sz) {}
    TColorAttr mapColor(uchar index) override
    {
        switch (index) {
            case 1: return cNorm();   // normal text
            case 2: return cSel();    // selected text
        }
        return TView::mapColor(index);
    }
};

class TFieldLine : public TInputLine {
public:
    TFieldLine(const TRect &b, int lim) : TInputLine(b, lim) {}
    TColorAttr mapColor(uchar index) override
    {
        switch (index) {
            case 1: return cNorm();   // passive
            case 2: return cSel();    // active
            case 3: return cSel();    // selected block
            case 4: return cHi();     // scroll arrows
        }
        return TView::mapColor(index);
    }
};

class TComposeWindow : public TWindow {
    TFieldLine  *toLine = nullptr, *ccLine = nullptr, *subjLine = nullptr;
    TBodyEditor *editor = nullptr;
    Boolean sent = False;

    static std::string strip(std::string s)
    {
        size_t a = s.find_first_not_of(" \t\r\n");
        size_t b = s.find_last_not_of(" \t\r\n");
        return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
    }

public:
    TComposeWindow(const TRect &bounds,
                   const std::string &to, const std::string &cc,
                   const std::string &subj, const std::string &body)
        : TWindowInit(&TComposeWindow::initFrame),
          TWindow(bounds, "Compose", wnNoNumber)
    {
        palette = wpCyanWindow;
        options |= ofTileable;
        const int W = size.x;

        auto addField = [&](int y, const char *label, const std::string &val) {
            insert(new TLabel(TRect(2, y, 11, y + 1), label, nullptr));
            auto *il = new TFieldLine(TRect(11, y, W - 2, y + 1), 900);
            il->growMode = gfGrowHiX;
            if (!val.empty()) {
                std::strncpy(il->data, val.c_str(), il->maxLen);
                il->data[il->maxLen] = '\0';
            }
            insert(il);
            return il;
        };
        toLine   = addField(1, "~T~o",      to);
        ccLine   = addField(2, "~C~c",      cc);
        subjLine = addField(3, "~S~ubject", subj);

        TScrollBar *vsb = new TScrollBar(TRect(W - 2, 5, W - 1, size.y - 1));
        insert(vsb);
        TIndicator *ind = new TIndicator(TRect(2, size.y - 1, 16, size.y));
        insert(ind);
        editor = new TBodyEditor(TRect(1, 5, W - 2, size.y - 1), nullptr, vsb, ind, 64000);
        editor->growMode = gfGrowHiX | gfGrowHiY;
        insert(editor);

        if (!body.empty()) {
            editor->insertText(body.data(), (uint)body.size(), False);
            editor->setSelect(0, 0, False);
            editor->trackCursor(False);
            editor->modified = False;
        }
        // blank compose -> cursor in To:, reply -> cursor in the body
        if (to.empty()) toLine->select(); else editor->select();
    }

    TColorAttr mapColor(uchar index) override
    {
        switch (index) {
            case 1: return cDim();    case 2: return cFrame();
            case 3: return cFrame();  case 4: return cNorm();
            case 5: return cHi();     case 6: return cNorm();
            case 7: return cHi();     case 8: return cNorm();
        }
        return TView::mapColor(index);
    }

    std::string bodyText()
    {
        uint n = editor->bufLen;
        std::string s(n, '\0');
        if (n) editor->getText(0, TSpan<char>(&s[0], (size_t)n));
        return s;
    }

    void doSend()
    {
        std::string to   = toLine->data   ? toLine->data   : "";
        std::string cc   = ccLine->data   ? ccLine->data   : "";
        std::string subj = subjLine->data ? subjLine->data : "";
        if (strip(to).empty()) {
            messageBox("Enter at least one To: address.", mfError | mfOKButton);
            return;
        }
        std::string msg = "To: " + to + "\n";
        if (!strip(cc).empty()) msg += "Cc: " + cc + "\n";
        msg += "Subject: " + subj + "\n\n" + bodyText();
        if (msg.empty() || msg.back() != '\n') msg += '\n';

        std::string path = writeTemp(msg, ".eml");
        std::string out = shCapture("tvmail-backend send < '" + path + "' 2>&1; rm -f '" + path + "'");
        while (!out.empty() && (out.back() == '\n' || out.back() == ' ')) out.pop_back();

        if (out == "sent" || out.empty()) {
            sent = True;
            messageBox("Message sent.", mfInformation | mfOKButton);
            // close after this event unwinds (don't free 'this' mid-handleEvent)
            TEvent ev;
            ev.what = evCommand;
            ev.message.command = cmClose;
            ev.message.infoPtr = this;
            putEvent(ev);
        } else {
            messageBox(("Send failed:\n" + out).c_str(), mfError | mfOKButton);
        }
    }

    void handleEvent(TEvent &e) override
    {
        TWindow::handleEvent(e);
        if (e.what == evCommand && e.message.command == cmSendMsg) {
            doSend();
            clearEvent(e);
        }
    }

    Boolean valid(ushort command) override
    {
        if (!TWindow::valid(command)) return False;
        if (command == cmClose && editor && editor->modified && !sent)
            return Boolean(messageBox("Discard this draft?",
                                      mfWarning | mfYesButton | mfNoButton) == cmYes);
        return True;
    }
};

static void parseTemplate(const std::string &t, std::string &to, std::string &cc,
                          std::string &subj, std::string &body)
{
    std::istringstream is(t);
    std::string line, b;
    bool inBody = false;
    auto ieq = [](const std::string &a, const char *k) {
        if (a.size() != std::strlen(k)) return false;
        for (size_t i = 0; i < a.size(); ++i)
            if (std::tolower((unsigned char)a[i]) != std::tolower((unsigned char)k[i]))
                return false;
        return true;
    };
    while (std::getline(is, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (!inBody) {
            if (line.empty()) { inBody = true; continue; }
            size_t p = line.find(':');
            if (p != std::string::npos) {
                std::string k = line.substr(0, p), v = line.substr(p + 1);
                while (!v.empty() && (v[0] == ' ' || v[0] == '\t')) v.erase(0, 1);
                if      (ieq(k, "To"))      to = v;
                else if (ieq(k, "Cc"))      cc = v;
                else if (ieq(k, "Subject")) subj = v;
            }
        } else {
            b += line;
            b += '\n';
        }
    }
    body = b;
}

// ---------------------------------------------------------------- app -------
class TVMailApp : public TApplication {
public:
    TMailListWindow *listWin = nullptr;

    TVMailApp()
        : TProgInit(&TVMailApp::initStatusLine,
                    &TVMailApp::initMenuBar,
                    &TVMailApp::initDeskTop)
    {
        // editor clipboard + Find/Replace dialogs (tvision ships neither)
        TEditor::clipboard = new TEditor(TRect(0, 0, 0, 0), 0, 0, 0, 0x10000);
        TEditor::clipboard->canUndo = False;
        TEditor::editorDialog = tvmailEditDialog;

        // grey the edit commands until an editor has focus
        TCommandSet es;
        es.enableCmd(cmCut);    es.enableCmd(cmCopy);   es.enableCmd(cmPaste);
        es.enableCmd(cmClear);  es.enableCmd(cmUndo);
        es.enableCmd(cmFind);   es.enableCmd(cmReplace);
        es.enableCmd(cmSearchAgain);
        disableCommands(es);

        loadList();
        listWin = new TMailListWindow(deskTop->getExtent());
        deskTop->insert(listWin);
    }

    static TMenuBar *initMenuBar(TRect r);
    static TStatusLine *initStatusLine(TRect r);
    static TDeskTop *initDeskTop(TRect r)
    {
        r.a.y++; r.b.y--;
        return new TBlueDeskTop(r);
    }
    void handleEvent(TEvent &e) override;

private:
    int  currentRow();
    void openMsg(int row);
    void viewSource(int row);
    void replyOrCompose(int row);           // row < 0 => new message
    void deleteMsg(int row);
    void pullMail();
    void reload();
    void switchFolder(const std::string &m);
};

TMenuBar *TVMailApp::initMenuBar(TRect r)
{
    r.b.y = r.a.y + 1;
    return new TMenuBar(r,
        *new TSubMenu("~F~ile", kbAltF) +
            *new TMenuItem("~P~ull mail", cmPull,   kbF3, hcNoContext, "F3") +
            *new TMenuItem("~R~eload",    cmReload, kbF5, hcNoContext, "F5") +
            newLine() +
            *new TMenuItem("E~x~it", cmQuit, kbAltX, hcNoContext, "Alt-X") +
        *new TSubMenu("Mail~b~ox", kbAltB) +
            *new TMenuItem("~S~pool  (/var/mail)",  cmFolderSpool, kbNoKey, hcNoContext) +
            *new TMenuItem("~H~ome mbox  (~/mbox)", cmFolderMbox,  kbNoKey, hcNoContext) +
            *new TMenuItem("~O~ther...",            cmFolderOther, kbNoKey, hcNoContext) +
        *new TSubMenu("~M~essage", kbAltM) +
            *new TMenuItem("~O~pen",         cmOpenMsg,   kbNoKey,  hcNoContext, "Enter") +
            *new TMenuItem("~R~eply",        cmReplyMsg,  kbCtrlR,  hcNoContext, "Ctrl-R") +
            *new TMenuItem("~N~ew message",  cmCompose,   kbCtrlN,  hcNoContext, "Ctrl-N") +
            *new TMenuItem("~S~end draft",   cmSendMsg,   kbF2,     hcNoContext, "F2") +
            newLine() +
            *new TMenuItem("~V~iew source",  cmViewSrc,   kbNoKey, hcNoContext) +
            *new TMenuItem("~D~elete",       cmDeleteMsg, kbCtrlD,  hcNoContext, "Ctrl-D") +
        *new TSubMenu("~E~dit", kbAltE) +
            *new TMenuItem("~U~ndo",  cmUndo,  kbNoKey, hcNoContext) +
            newLine() +
            *new TMenuItem("Cu~t~",   cmCut,   kbShiftDel, hcNoContext, "Shift-Del") +
            *new TMenuItem("~C~opy",  cmCopy,  kbCtrlIns,  hcNoContext, "Ctrl-Ins") +
            *new TMenuItem("~P~aste", cmPaste, kbShiftIns, hcNoContext, "Shift-Ins") +
            newLine() +
            *new TMenuItem("~F~ind...",    cmFind,        kbNoKey, hcNoContext) +
            *new TMenuItem("~R~eplace...", cmReplace,     kbNoKey, hcNoContext) +
            *new TMenuItem("Find a~g~ain", cmSearchAgain, kbNoKey, hcNoContext) +
        *new TSubMenu("~W~indow", kbAltW) +
            *new TMenuItem("~N~ext",     cmNext,    kbF6,      hcNoContext, "F6") +
            *new TMenuItem("~P~revious", cmPrev,    kbShiftF6, hcNoContext, "Shift-F6") +
            *new TMenuItem("~Z~oom",     cmZoom,    kbNoKey,   hcNoContext) +
            *new TMenuItem("~T~ile",     cmTile,    kbNoKey,   hcNoContext) +
            *new TMenuItem("C~a~scade",  cmCascade, kbNoKey,   hcNoContext) +
            newLine() +
            *new TMenuItem("~C~lose",    cmClose,   kbAltF3,   hcNoContext, "Alt-F3") +
        *new TSubMenu("~H~elp", kbAltH) +
            *new TMenuItem("~A~bout", cmAboutBox, kbNoKey, hcNoContext)
        );
}

TStatusLine *TVMailApp::initStatusLine(TRect r)
{
    r.a.y = r.b.y - 1;
    return new TStatusLine(r,
        *new TStatusDef(0, 0xFFFF) +
            *new TStatusItem("~F3~ Pull",    kbF3,    cmPull) +
            *new TStatusItem("~F5~ Reload",  kbF5,    cmReload) +
            *new TStatusItem("~^R~ Reply",   kbCtrlR, cmReplyMsg) +
            *new TStatusItem("~^N~ New",     kbCtrlN, cmCompose) +
            *new TStatusItem("~F2~ Send",    kbF2,    cmSendMsg) +
            *new TStatusItem("~^D~ Del",     kbCtrlD, cmDeleteMsg) +
            *new TStatusItem("~F6~ Next",    kbF6,    cmNext) +
            *new TStatusItem("~Alt-X~ Exit", kbAltX,  cmQuit) +
            *new TStatusItem(nullptr,        kbF10,   cmMenu) +
            *new TStatusItem(nullptr,        kbShiftF6, cmPrev) +
            *new TStatusItem(nullptr,        kbAltF3, cmClose)
        );
}

int TVMailApp::currentRow()
{
    if (!listWin || !listWin->list) return -1;
    short f = listWin->list->focused;
    return (f >= 0 && f < (short)gRows.size()) ? (int)f : -1;
}

void TVMailApp::openMsg(int row)
{
    if (row < 0) { messageBox("No message selected.", mfInformation | mfOKButton); return; }
    int b = gRows[row].idx;
    auto lines = splitLines(shCapture("tvmail-backend show " + std::to_string(b)
                                      + mboxArg() + " 2>&1"));
    TRect r = deskTop->getExtent();
    r.grow(-3, -1);
    std::string title = "Msg " + std::to_string(b) + "  " + gRows[row].subj;
    deskTop->insert(new TMailViewWindow(r, title.c_str(), std::move(lines)));

    shCapture("tvmail-backend mark " + std::to_string(b) + " read" + mboxArg() + " >/dev/null 2>&1");
    gRows[row].flag = '.';
    if (listWin && listWin->list) listWin->list->drawView();
}

void TVMailApp::viewSource(int row)
{
    if (row < 0) return;
    int b = gRows[row].idx;
    auto lines = splitLines(shCapture("tvmail-backend raw " + std::to_string(b)
                                      + mboxArg() + " 2>&1"));
    TRect r = deskTop->getExtent();
    r.grow(-3, -1);
    std::string title = "Source of msg " + std::to_string(b);
    deskTop->insert(new TMailViewWindow(r, title.c_str(), std::move(lines)));
}

void TVMailApp::replyOrCompose(int row)
{
    std::string to, cc, subj, body;
    if (row >= 0) {
        std::string t = shCapture("tvmail-backend compose-template --in-reply-to "
                                  + std::to_string(gRows[row].idx) + mboxArg() + " 2>/dev/null");
        parseTemplate(t, to, cc, subj, body);
    }
    TRect r = deskTop->getExtent();
    r.grow(-5, -2);
    deskTop->insert(new TComposeWindow(r, to, cc, subj, body));
}

void TVMailApp::deleteMsg(int row)
{
    if (row < 0) return;
    int b = gRows[row].idx;
    if (messageBox(mfConfirmation | mfYesNoCancel, "Delete message %d?", b) != cmYes) return;
    std::string out = shCapture("tvmail-backend delete " + std::to_string(b)
                                + mboxOpt() + " 2>&1");
    reload();
    messageBox(out.empty() ? "deleted" : out.c_str(), mfInformation | mfOKButton);
}

void TVMailApp::pullMail()
{
    shInteractive("tvmail-backend pull");
    reload();
}

void TVMailApp::reload()
{
    loadList();
    if (listWin && listWin->list) listWin->list->refresh();
}

void TVMailApp::switchFolder(const std::string &m)
{
    gMbox = m;
    gTitle = std::string("Mailbox - ") + folderLabel();
    if (listWin) { listWin->title = gTitle.c_str(); listWin->frame->drawView(); }
    reload();
}

void TVMailApp::handleEvent(TEvent &e)
{
    TApplication::handleEvent(e);
    if (e.what != evCommand) return;
    bool handled = true;
    switch (e.message.command) {
        case cmPull:      pullMail();               break;
        case cmReload:    reload();                 break;
        case cmOpenMsg:   openMsg(currentRow());    break;
        case cmReplyMsg:  replyOrCompose(currentRow()); break;
        case cmCompose:   replyOrCompose(-1);       break;
        case cmDeleteMsg: deleteMsg(currentRow());  break;
        case cmViewSrc:   viewSource(currentRow()); break;
        case cmFolderSpool: switchFolder("spool"); break;
        case cmFolderMbox:  switchFolder("mbox");  break;
        case cmFolderOther: {
            char p[512] = "";
            if (inputBox("Open mailbox", "Path:", p, (uchar)(sizeof(p) - 1)) == cmOK && *p)
                switchFolder(p);
            break;
        }
        case cmAboutBox:
            messageBox("tvmail v0.9\n\nA Turbo Vision front-end for a local mbox.\n"
                       "Plumbing: exim + tvmail-backend + pop-pull",
                       mfInformation | mfOKButton);
            break;
        default: handled = false;
    }
    if (handled) clearEvent(e);
}

int main(int argc, char **argv)
{
    if (argc > 1) gMbox = argv[1];
    TVMailApp app;
    app.run();
    app.shutDown();
    return 0;
}
