// app.cpp - the application: menus, status line, the periodic poll.  The desktop, the
// palette and the Window list are TTvApp's (tvcommon); the calendar window is calwin.cpp's.
#include "app.h"
#include "coprocess.h"

namespace
{
// The status line, with a quiet "N incoming" at its right end while tvmail or tvchat have
// asked for something to be added (Calendar > Pending...).
class TVCalStatusLine : public TStatusLine
{
public:
    TVCalStatusLine(const TRect &r, TStatusDef &d) : TStatusLine(r, d) {}
    void draw() override
    {
        TStatusLine::draw();
        int n = calIncomingCount();
        if (n <= 0)
            return;
        std::string s = " " + std::to_string(n) + " incoming ";
        int w = (int)s.size();
        if (w >= size.x)
            return;
        TDrawBuffer b;
        b.moveStr(0, s, getColor(1));
        writeLine(size.x - w, 0, w, 1, b);
    }
};
const ushort cmCalHint = 2249; // status-line hints: does nothing
} // namespace

TVCalApp::TVCalApp()
    : TProgInit(&TVCalApp::initStatusLine, &TVCalApp::initMenuBar, &TTvApp::initDeskTop)
{
    lastPoll = std::chrono::steady_clock::now();
    calOpenWindow();
}

TMenuBar *TVCalApp::initMenuBar(TRect r)
{
    r.b.y = r.a.y + 1;
    return new TMenuBar(
        r, *new TSubMenu("~F~ile", kbAltF) +
               *new TMenuItem("~F~loyd, the assistant...", cmFloyd, kbF7, hcNoContext, "F7") +
               tvContactsItem() +
               tvConfigItem() +
               tvReloadItem() +
               *new TMenuItem("~I~mport .ics file...", cmCalImport, kbNoKey, hcNoContext) +
               *new TMenuItem("Ex~p~ort .ics file...", cmCalExport, kbNoKey, hcNoContext) +
               tvExitItem() +
               *new TSubMenu("~C~alendar", kbAltC) +
               *new TMenuItem("~N~ew event...", cmCalNewEvent, kbCtrlN, hcNoContext, "Ctrl-N") +
               *new TMenuItem("~E~dit event...", cmCalEditEvent, kbNoKey, hcNoContext, "Enter") +
               *new TMenuItem("~D~elete event", cmCalDelEvent, kbNoKey, hcNoContext, "Del") +
               newLine() +
               *new TMenuItem("Go to ~t~oday", cmCalToday, kbNoKey, hcNoContext, "Home") +
               *new TMenuItem("Go to ~d~ate...", cmCalGoto, kbCtrlG, hcNoContext, "Ctrl-G") +
               *new TMenuItem("~F~ind...", cmCalFind, kbCtrlF, hcNoContext, "Ctrl-F") +
               newLine() +
               *new TMenuItem("~S~end to", kbNoKey,
                              new TMenu(*new TMenuItem("~M~ail (an invitation)...", cmCalMail, kbNoKey, hcNoContext) +
                                        *new TMenuItem("~C~hat (share)", cmCalShareChat, kbNoKey, hcNoContext))) +
               *new TMenuItem("~P~ending (from tvmail / tvchat)...", cmCalIncoming, kbNoKey, hcNoContext) +
               *new TSubMenu("~T~o-do", kbAltT) +
               *new TMenuItem("~N~ew to-do...", cmCalNewTodo, kbCtrlT, hcNoContext, "Ctrl-T") +
               *new TMenuItem("~E~dit to-do...", cmCalEditTodo, kbNoKey, hcNoContext, "Enter") +
               *new TMenuItem("~M~ark done / not done", cmCalToggleTodo, kbNoKey, hcNoContext, "Space") +
               *new TMenuItem("~D~elete to-do", cmCalDelTodo, kbNoKey, hcNoContext, "Del") +
               newLine() +
               *new TMenuItem("~S~how completed", cmCalShowDone, kbNoKey, hcNoContext) +
               *new TSubMenu("~E~dit", kbAltE) +
               *new TMenuItem("~C~opy", cmCopy, kbCtrlIns, hcNoContext, "Ctrl-Ins") +
               *new TMenuItem("~P~aste", cmPaste, kbShiftIns, hcNoContext, "Shift-Ins") +
               tvWindowMenu() +
               tvHelpMenu());
}

TStatusLine *TVCalApp::initStatusLine(TRect r)
{
    r.a.y = r.b.y - 1;
    return new TVCalStatusLine(r, tvStatusDef(*new TStatusDef(0, 0xFFFF) +
                                              *new TStatusItem("~Tab~ Pane", kbNoKey, cmCalHint) +
                                              *new TStatusItem("~Enter~ Edit", kbNoKey, cmCalHint) +
                                              *new TStatusItem("~Ins~ New", kbNoKey, cmCalHint)));
}

void TVCalApp::post(ushort command)
{
    TEvent ev;
    ev.what = evCommand;
    ev.message.command = command;
    ev.message.infoPtr = nullptr;
    putEvent(ev);
}

void TVCalApp::idle()
{
    TTvApp::idle();
    // Nothing is posted while a menu or a dialog is up (tvmail bug #44): any command ends a tvision
    // menu, and a dialog swallows what it does not know.
    if (TopView() != this)
        return;
    auto now = std::chrono::steady_clock::now();
    if (now - lastPoll >= std::chrono::seconds(1))
    {
        lastPoll = now;
        post(cmCalPoll);
    }
}

// File > Edit configuration...: tvcal.conf in an editor window.  A local calendar needs none, so
// the window starts from the example.  F9 checks the text with `tvcal-backend check-config`;
// F8 checks, saves and applies it.
void TVCalApp::editConfig()
{
    ConfigSpec spec;
    spec.title = "tvcal.conf";
    spec.path = calConfPath();
    for (const char *p : {"/usr/local/share/doc/tvcal/tvcal.conf.example", "/usr/share/doc/tvcal/tvcal.conf.example"})
    {
        spec.sample = slurp(p);
        if (!spec.sample.empty())
            break;
    }
    if (spec.sample.empty())
        spec.sample = "# tvcal configuration (read by tvcal-backend). NO PASSWORDS HERE:\n"
                      "# $TVCAL_PASS or ~/.mu-tickets.\n\n[server]\n#url = https://cpbee.cmnet/radicale/you/calendar/\n"
                      "#user = you\n\n[cal]\n#remind = 15\n#week_start = mon\n";
    spec.verify = [](const std::string &file, std::string &report)
    {
        std::string out = shCapture("tvcal-backend check-config " + shq(file) + " 2>&1; echo \"rc=$?\"");
        size_t at = out.rfind("rc=");
        int rc = at == std::string::npos ? 1 : atoi(out.c_str() + at + 3);
        report = at == std::string::npos ? out : out.substr(0, at);
        return rc == 0;
    };
    spec.apply = [](std::string &msg)
    {
        calSettingsChanged();
        msg = "Applied: the server, week start and reminder default were read again";
        return true;
    };
    openConfigWindow(deskTop, spec);
}

void TVCalApp::aboutBox()
{
    tvAboutBox(deskTop, "tvcal", TVCAL_VERSION,
               "A Turbo Vision calendar and to-do manager\nthat works with tvmail and tvchat.",
               "Plumbing: tvcal-backend, a CalDAV server, iCalendar.");
}

void TVCalApp::handleEvent(TEvent &e)
{
    TTvApp::handleEvent(e); // the flash timer, Window > List
    if (e.what != evCommand)
        return;
    ushort c = e.message.command;
    if (c == cmCalPoll)
        calPoll();
    else if (!calCommand(c))
    {
        switch (c)
        {
        case cmReload: // File > Reload: read everything again
            calWindowOnReload();
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
        case cmEditConfig:
            editConfig();
            break;
        case cmCalHint:
            break;
        default:
            return;
        }
    }
    clearEvent(e);
}
