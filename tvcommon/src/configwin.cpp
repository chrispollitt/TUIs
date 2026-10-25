// configwin.cpp - the in-app editor for an app's configuration file, shared by
// tvmail and tvchat: a cyan editor window with
//   Ctrl-S  save (the old file is kept as FILE.bak.DATE-TIME)
//   F9      verify what is in the window - the app's own checker is run on a
//           copy of the text; nothing is changed
//   F8      verify, save and make the running app use it (the app's `apply`)
// A file that does not exist yet opens as the shipped example, so a first
// configuration is an edit, not a blank page.  The checker and the apply step
// are the app's (ConfigSpec in tvcommon.h): this file knows nothing of INI.
#include "views.h"
#include <cstdio>
#include <ctime>
#include <sys/stat.h>
#include <unistd.h>

namespace
{
class TConfigWindow;
TConfigWindow *gConfigWin = nullptr;

std::string stamp()
{
    char b[32];
    time_t t = time(nullptr);
    strftime(b, sizeof b, "%Y%m%d-%H%M%S", localtime(&t));
    return b;
}

std::string dirOf(const std::string &p)
{
    size_t s = p.rfind('/');
    return s == std::string::npos ? std::string(".") : p.substr(0, s ? s : 1);
}

bool writeWhole(const std::string &path, const std::string &s, mode_t mode)
{
    FILE *f = fopen(path.c_str(), "wb");
    if (!f)
        return false;
    chmod(path.c_str(), mode);
    bool ok = fwrite(s.data(), 1, s.size(), f) == s.size();
    return (fclose(f) == 0) && ok;
}

class TConfigWindow : public TWindow
{
    ConfigSpec spec;
    TBodyEditor *editor = nullptr;

    std::string text()
    {
        uint n = editor->bufLen;
        std::string s(n, '\0');
        if (n)
            editor->getText(0, TSpan<char>(&s[0], (size_t)n));
        return s;
    }

    // The text goes to the file, the old one kept beside it; false (with a box) if it cannot.
    bool save()
    {
        std::string s = text();
        mode_t mode = 0600; // a config may name accounts: private unless it already was not
        struct stat st;
        if (stat(spec.path.c_str(), &st) == 0)
        {
            mode = st.st_mode & 0777;
            std::string old = slurp(spec.path);
            if (!writeWhole(spec.path + ".bak." + stamp(), old, mode))
            {
                notify("Cannot keep a backup of " + spec.path + "; nothing was saved.", noteError);
                return false;
            }
        }
        else
            shCapture("mkdir -p " + shq(dirOf(spec.path)));
        std::string tmp = spec.path + ".tmp";
        if (!writeWhole(tmp, s, mode) || rename(tmp.c_str(), spec.path.c_str()) != 0)
        {
            unlink(tmp.c_str());
            notify(("Cannot write " + spec.path + "."), noteError);
            return false;
        }
        editor->modified = False;
        return true;
    }

    // What the checker says about the text in the window.  true = no errors.
    bool verify()
    {
        const char *tmpdir = getenv("TMPDIR");
        std::string tmp = std::string(tmpdir && *tmpdir ? tmpdir : "/tmp") + "/tvconf-" +
                          std::to_string((long)getpid()) + ".conf";
        if (!writeWhole(tmp, text(), 0600))
        {
            notify("Cannot write a temporary copy to check.", noteError);
            return false;
        }
        std::string report;
        bool ok = spec.verify ? spec.verify(tmp, report) : true;
        unlink(tmp.c_str());
        while (!report.empty() && isspace((unsigned char)report.back()))
            report.pop_back();
        // a checker that only says "ok: ..." is a status-line note, not a box to dismiss
        bool onlyOk = ok;
        for (const std::string &l : splitLines(report))
            if (!l.empty() && l.compare(0, 3, "ok:") != 0)
                onlyOk = false;
        if (ok && report.empty())
            notify(spec.title + " checks out");
        else if (onlyOk)
            notify(spec.title + " checks out - " + report.substr(report.find(' ') + 1));
        else
            show(ok ? "Warnings" : "Problems", report, ok);
        return ok;
    }

    void show(const char *title, const std::string &report, bool ok)
    {
        size_t lines = 1;
        for (char c : report)
            lines += c == '\n';
        if (lines <= 4 && report.size() < 400)
            notify(report, ok ? noteWarn : noteError);
        else
        {
            TRect r = TProgram::deskTop->getExtent();
            r.grow(-6, -3);
            TProgram::deskTop->insert(newTextWindow(r, title, report));
        }
    }

    void apply()
    {
        if (!verify())
        {
            notify("Not saved: fix the problems first", noteWarn);
            return;
        }
        if (!save())
            return;
        std::string msg;
        if (spec.apply && !spec.apply(msg))
        {
            notify("Saved, but not applied: " + msg, noteError);
            return;
        }
        notify(msg.empty() ? "Saved and applied: " + spec.title : msg);
    }

public:
    TConfigWindow(const TRect &b, const ConfigSpec &s)
        : TWindowInit(&TConfigWindow::initFrame),
          TWindow(b, (s.title + "   F9 verify   F8 save + apply   Ctrl-S save").c_str(), wnNoNumber),
          spec(s)
    {
        palette = wpCyanWindow;
        options |= ofTileable;
        TScrollBar *vsb = standardScrollBar(sbVertical | sbHandleKeyboard);
        TRect r = getExtent();
        r.grow(-1, -1);
        editor = new TBodyEditor(r, nullptr, vsb, nullptr, 64000);
        editor->growMode = gfGrowHiX | gfGrowHiY;
        insert(editor);
        struct stat st;
        bool exists = stat(spec.path.c_str(), &st) == 0;
        std::string s0 = exists ? slurp(spec.path) : spec.sample;
        if (!s0.empty())
        {
            editor->insertText(s0.data(), (uint)s0.size(), False);
            editor->setSelect(0, 0, False);
            editor->trackCursor(False);
            editor->modified = exists ? False : True; // a new file is unsaved until it is
        }
        editor->select();
        gConfigWin = this;
    }
    ~TConfigWindow()
    {
        if (gConfigWin == this)
            gConfigWin = nullptr;
    }
    void handleEvent(TEvent &e) override
    {
        if (e.what == evKeyDown)
        {
            switch (e.keyDown.keyCode)
            {
            case kbCtrlS:
                if (save())
                    notify("Saved " + spec.path + " (not applied: F8 does that)");
                clearEvent(e);
                return;
            case kbF9:
                verify();
                clearEvent(e);
                return;
            case kbF8:
                apply();
                clearEvent(e);
                return;
            }
        }
        TWindow::handleEvent(e);
    }
    Boolean valid(ushort cmd) override
    {
        if (!TWindow::valid(cmd))
            return False;
        if ((cmd == cmClose || cmd == cmQuit) && editor->modified)
        {
            if (cmd == cmQuit)
                select();
            ushort r = messageBox(("Save changes to " + spec.path + "?").c_str(), mfInformation | mfYesNoCancel);
            if (r == cmCancel)
                return False;
            if (r == cmYes && !save())
                return False; // keep the window and the text
        }
        return True;
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
} // namespace

// The editor window for `spec`, opened (or brought to the front if it already is).
void openConfigWindow(TDeskTop *desk, const ConfigSpec &spec)
{
    if (gConfigWin)
    {
        gConfigWin->select();
        return;
    }
    TRect r = desk->getExtent();
    r.grow(-2, -1);
    desk->insert(new TConfigWindow(r, spec));
}
