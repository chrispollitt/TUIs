#include "tvmail.h"
#include "views.h"

// ===================================================== signature editor =====
class TSigWindow : public TWindow
{
    TBodyEditor *editor = nullptr;
    std::string path;

    std::string text()
    {
        uint n = editor->bufLen;
        std::string s(n, '\0');
        if (n)
            editor->getText(0, TSpan<char>(&s[0], (size_t)n));
        return s;
    }
    bool save()
    {
        std::string s = text();
        if (FILE *f = fopen(path.c_str(), "wb"))
        {
            bool ok = fwrite(s.data(), 1, s.size(), f) == s.size();
            ok = (fclose(f) == 0) && ok;
            if (ok)
            {
                editor->modified = False;
                flashStatus("Signature saved"); // a note, not one more box to dismiss
                return true;
            }
        }
        notify("Cannot write the signature file.", noteError);
        return false;
    }

public:
    TSigWindow(const TRect &b)
        : TWindowInit(&TSigWindow::initFrame),
          TWindow(b, "Signature - ~/.signature   (Ctrl-S saves)", wnNoNumber)
    {
        palette = wpCyanWindow;
        options |= ofTileable;
        path = homePath(".signature");
        TScrollBar *vsb = standardScrollBar(sbVertical | sbHandleKeyboard);
        TRect r = getExtent();
        r.grow(-1, -1);
        editor = new TBodyEditor(r, nullptr, vsb, nullptr, 32000);
        editor->growMode = gfGrowHiX | gfGrowHiY;
        insert(editor);
        std::string s = slurp(path);
        if (!s.empty())
        {
            editor->insertText(s.data(), (uint)s.size(), False);
            editor->setSelect(0, 0, False);
            editor->trackCursor(False);
            editor->modified = False;
        }
        editor->select();
    }
    void handleEvent(TEvent &e) override
    {
        if (e.what == evKeyDown && e.keyDown.keyCode == kbCtrlS)
        {
            save();
            clearEvent(e);
            return;
        }
        TWindow::handleEvent(e);
        if (e.what == evCommand && e.message.command == cmSaveSig)
        {
            save();
            clearEvent(e);
        }
    }
    Boolean valid(ushort cmd) override
    {
        if (!TWindow::valid(cmd))
            return False;
        if ((cmd == cmClose || cmd == cmQuit) && editor->modified)
        {
            if (cmd == cmQuit)
                select();
            ushort r = messageBox("Save changes to ~/.signature?",
                                  mfInformation | mfYesNoCancel);
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

TWindow *newSigWindow(const TRect &bounds)
{
    return new TSigWindow(bounds);
}
