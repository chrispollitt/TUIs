// tvapp.cpp - see tvapp.h.
#include "tvapp.h"

// ------------------------------------------------------- blue desktop ------
// Some terminals map Turbo Vision's default desktop palette to a jarring
// colour (seen: solid red). Paint it the classic blue explicitly.
namespace
{
class TBlueBg : public TBackground
{
public:
    TBlueBg(const TRect &r) : TBackground(r, char(0xB0)) {} // 0xB0 = light shade
    void draw() override
    {
        TColorAttr c = vga(0x17); // grey on blue
        TDrawBuffer b;
        b.moveChar(0, pattern, c, size.x);
        for (short y = 0; y < size.y; ++y)
            writeLine(0, y, size.x, 1, b);
    }
};

class TBlueDeskTop : public TDeskTop
{
public:
    TBlueDeskTop(const TRect &r)
        : TDeskInit(&TBlueDeskTop::initBackground), TDeskTop(r) {}
    static TBackground *initBackground(TRect r) { return new TBlueBg(r); }
};
} // namespace

TTvApp::TTvApp()
    : TProgInit(&TTvApp::initStatusLine, &TTvApp::initMenuBar, &TTvApp::initDeskTop)
{
    // editor clipboard + Find/Replace dialogs (tvision ships neither)
    TEditor::clipboard = new TEditor(TRect(0, 0, 0, 0), 0, 0, 0, 0x10000);
    TEditor::clipboard->canUndo = False;
    TEditor::editorDialog = tvEditDialog;

    // grey the edit commands until an editor has focus
    TCommandSet es;
    es.enableCmd(cmCut);
    es.enableCmd(cmCopy);
    es.enableCmd(cmPaste);
    es.enableCmd(cmClear);
    es.enableCmd(cmUndo);
    es.enableCmd(cmFind);
    es.enableCmd(cmReplace);
    es.enableCmd(cmSearchAgain);
    disableCommands(es);
}

TDeskTop *TTvApp::initDeskTop(TRect r)
{
    r.a.y++;
    r.b.y--;
    return new TBlueDeskTop(r);
}

// tvision's own views - dialogs, message boxes, menus, the status line - end
// their palette lookup here, at the application palette (cpAppColor).
// Retune the classic entries that are hard to read even in true VGA colours
// (each scored by WCAG contrast across Dark+, Campbell, VGA and mintty), and
// hand everything back as exact RGB like the apps' own views.
TColorAttr TTvApp::mapColor(uchar index)
{
    switch (index)
    {
    case 7: // menu: hotkey of the selected item (was red on green)
        return vga(0x21);
    case 25: // grey window / dialog: frame + title, and the frame icons
    case 26: // (were white / light green on grey: invisible under Dark+)
    case 33:
    case 34:
        return vga(0x70);
    case 39: // label of the focused control (was white on grey)
        return vga(0x71);
    case 40: // label hotkey (was yellow on grey)
        return vga(0x74);
    case 41: // buttons: one green for every state, so the hotkey letter's
        return onGreen(0x000000); // cell always matches; the text says it
    case 42: // default (was light cyan on green)
        return onGreen(0x55FFFF);
    case 43: // focused (was white on green)
        return onGreen(0xFFFFFF);
    case 45: // hotkey (was yellow on green)
        return onGreen(0xFFFF55);
    case 48: // focused check box / radio button item (was white on cyan)
        return vga(0x31);
    case 49: // check box / radio button hotkey (was yellow on cyan)
        return vga(0x30, slUnderline);
    case 51: // input line selection, focused list row (were white on green)
    case 58:
        return cFocus();
    case 54: // history button sides (was green on grey)
        return vga(0x70);
    case 59: // list row in a list without focus (was yellow on cyan)
        return cCur();
    }
    TColorAttr c = TApplication::mapColor(index);
    TColor fg = c.getForeground(), bg = c.getBackground();
    if (fg.isBIOS() && bg.isBIOS())
        return vga(uchar(uchar(fg.asBIOS()) | (uchar(bg.asBIOS()) << 4)), c.getStyle());
    return c;
}

void TTvApp::handleEvent(TEvent &e)
{
    TApplication::handleEvent(e);
    if (e.what == evBroadcast && e.message.command == cmTimerExpired &&
        gFlashTimer && e.message.infoPtr == gFlashTimer)
    {
        gFlashTimer = nullptr; // flashStatus() note is up: back to the F-key hints
        if (statusLine)
            statusLine->drawView();
        clearEvent(e);
        return;
    }
    if (e.what == evCommand && e.message.command == cmWindowList)
    {
        windowList();
        clearEvent(e);
    }
    if (e.what == evCommand && e.message.command == cmRedrawScreen)
    {
        redrawScreen();
        clearEvent(e);
    }
}

void TTvApp::getEvent(TEvent &e)
{
    TApplication::getEvent(e);
    if (e.what == evKeyDown && e.keyDown.keyCode == kbCtrlL)
    {
        redrawScreen();
        e.what = evNothing;
    }
}

// clearScreen() also forgets what tvision thinks is on the terminal, so the
// redraw sends every cell again instead of only those it thinks changed.
void TTvApp::redrawScreen()
{
    TScreen::clearScreen();
    redraw();
    TScreen::flushScreen();
}

// ------------------------------------------------------------ Window list ---
namespace
{
struct WinEntry
{
    TView *view;
    std::string title;
};
void collectWindow(TView *v, void *arg)
{
    auto *list = static_cast<std::vector<WinEntry> *>(arg);
    if (!(v->options & ofSelectable) || !(v->state & sfVisible))
        return;
    if (TWindow *w = dynamic_cast<TWindow *>(v))
        list->push_back({v, (w->title && *w->title) ? w->title : "(untitled)"});
}

// One row per window, in the dialog's list.
class TWinListPane : public TListViewer
{
    std::vector<WinEntry> *items;

public:
    TWinListPane(const TRect &b, TScrollBar *vsb, std::vector<WinEntry> *its)
        : TListViewer(b, 1, nullptr, vsb), items(its)
    {
        setRange((short)items->size());
    }
    void getText(char *dest, short item, short maxLen) override
    {
        if (item < 0 || item >= (short)items->size())
        {
            dest[0] = 0;
            return;
        }
        putText(dest, (*items)[item].title, maxLen);
    }
    void selectItem(short) override { message(owner, evCommand, cmOK, this); }
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
    short chosen() const { return focused; }
};
} // namespace

// Window > List... (Alt-0): every window on the desktop, pick one to bring to
// the front.  Compose windows, source, parts, help and the main window stack
// up fast and F6 only cycles.
void TTvApp::windowList()
{
    std::vector<WinEntry> wins;
    deskTop->forEach(collectWindow, &wins);
    if (wins.empty())
        return;
    std::reverse(wins.begin(), wins.end()); // forEach walks back to front: top window first

    int n = std::min<int>((int)wins.size(), 12);
    TDialog *d = new TDialog(TRect(0, 0, 52, 7 + n), "Window list");
    d->options |= ofCentered;
    TScrollBar *vsb = new TScrollBar(TRect(49, 2, 50, 2 + n));
    d->insert(vsb);
    TWinListPane *lp = new TWinListPane(TRect(2, 2, 49, 2 + n), vsb, &wins);
    d->insert(lp);
    d->insert(new TButton(TRect(14, 3 + n, 26, 5 + n), "~S~elect", cmOK, bfDefault));
    d->insert(new TButton(TRect(28, 3 + n, 40, 5 + n), "Cancel", cmCancel, bfNormal));
    d->selectNext(False);

    ushort r = deskTop->execView(d);
    short sel = lp->chosen();
    TObject::destroy(d);
    if (r != cmCancel && sel >= 0 && sel < (short)wins.size())
        wins[sel].view->select();
}

// ----------------------------------------------- shared menus and status ---
TMenuItem &tvExitItem()
{
    return newLine() +
           *new TMenuItem("E~x~it", cmQuit, kbAltX, hcNoContext, "Alt-X");
}

TMenuItem &tvReloadItem()
{
    return *new TMenuItem("~R~eload", cmReload, kbF5, hcNoContext, "F5");
}

TMenuItem &tvFindItems(bool withReplace)
{
    TMenuItem &m = *new TMenuItem("~F~ind...", cmFind, kbNoKey, hcNoContext);
    if (withReplace)
        return m + *new TMenuItem("~R~eplace...", cmReplace, kbNoKey, hcNoContext) +
               *new TMenuItem("Find a~g~ain", cmSearchAgain, kbNoKey, hcNoContext);
    return m + *new TMenuItem("Find a~g~ain", cmSearchAgain, kbNoKey, hcNoContext);
}

TSubMenu &tvWindowMenu()
{
    return *new TSubMenu("~W~indow", kbAltW) +
           *new TMenuItem("~N~ext", cmNext, kbF6, hcNoContext, "F6") +
           *new TMenuItem("~P~revious", cmPrev, kbShiftF6, hcNoContext, "Shift-F6") +
           *new TMenuItem("~Z~oom", cmZoom, kbNoKey, hcNoContext) +
           *new TMenuItem("~T~ile", cmTile, kbNoKey, hcNoContext) +
           *new TMenuItem("C~a~scade", cmCascade, kbNoKey, hcNoContext) +
           *new TMenuItem("~L~ist...", cmWindowList, kbAlt0, hcNoContext, "Alt-0") +
           *new TMenuItem("~R~edraw screen", cmRedrawScreen, kbCtrlL, hcNoContext, "Ctrl-L") +
           newLine() +
           *new TMenuItem("~C~lose", cmClose, kbAltF3, hcNoContext, "Alt-F3");
}

TSubMenu &tvHelpMenu()
{
    return *new TSubMenu("~H~elp", kbAltH) +
           *new TMenuItem("~C~ontents", cmShowHelp, kbF1, hcNoContext, "F1") +
           newLine() +
           *new TMenuItem("~A~bout", cmAboutBox, kbNoKey, hcNoContext);
}

TStatusDef &tvStatusDef(TStatusDef &appItems)
{
    return appItems +
           *new TStatusItem("~F5~ Reload", kbF5, cmReload) +
           *new TStatusItem("~F6~ Next", kbF6, cmNext) +
           *new TStatusItem("~Alt-X~ Exit", kbAltX, cmQuit) +
           *new TStatusItem("~F1~ Help", kbF1, cmShowHelp) +
           *new TStatusItem(nullptr, kbF10, cmMenu) +
           *new TStatusItem(nullptr, kbShiftF6, cmPrev) +
           *new TStatusItem(nullptr, kbAltF3, cmClose);
}

void tvAboutBox(TDeskTop *desk, const char *app, const char *version,
                const char *tagline, const char *plumbing)
{
    // its own dialog, sized to the text: messageBox() holds about five lines
    // (\003 centres a line, each one)
    std::string text = std::string("\003") + app + " " + version + "\n\n";
    std::string line;
    for (const char *p = tagline;; ++p)
    {
        if (*p == '\n' || !*p)
        {
            text += "\003" + line + "\n";
            line.clear();
            if (!*p)
                break;
        }
        else
            line += *p;
    }
    text += "\n"
            "\003Chris Pollitt  -  MIT licence, no warranty.\n"
            "\003https://github.com/chrispollitt/TUIs\n"
            "\n"
            "\003Turbo Vision by magiblot.\n";
    text += std::string("\003") + plumbing + "\n"
            "\003See HISTORY.md.";
    std::string title = std::string("About ") + app;
    TDialog *d = new TDialog(TRect(0, 0, 54, 18), title.c_str());
    d->options |= ofCentered;
    d->insert(new TStaticText(TRect(2, 2, 52, 13), text.c_str()));
    d->insert(new TButton(TRect(21, 14, 33, 16), "O~K~", cmOK, bfDefault));
    desk->execView(d);
    TObject::destroy(d);
}
