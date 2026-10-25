#include "tvmail.h"
#include "views.h"

// ===================================================== MIME parts ==========
// Lists `tvmail-backend parts IDX MBOX`; Enter on a part prompts for a save
// path and runs `tvmail-backend save IDX PARTNO DEST [MBOX]`.
class TPartsPane : public TListViewer
{
    struct Part
    {
        std::string no, type, name, bytes;
    };
    std::vector<Part> rows;
    int msgIdx;
    std::string msgMbox;

public:
    TPartsPane(const TRect &b, TScrollBar *vsb, int idx, const std::string &mbox)
        : TListViewer(b, 1, nullptr, vsb), msgIdx(idx), msgMbox(mbox)
    {
        std::istringstream is(backendRun("parts " + std::to_string(idx) + " " + mbox));
        std::string ln;
        while (std::getline(is, ln))
        {
            if (!ln.empty() && ln.back() == '\r')
                ln.pop_back();
            std::vector<std::string> f;
            size_t p = 0;
            for (;;)
            {
                size_t t = ln.find('\t', p);
                f.push_back(ln.substr(p, t == std::string::npos ? std::string::npos : t - p));
                if (t == std::string::npos)
                    break;
                p = t + 1;
            }
            if (f.size() >= 4)
                rows.push_back({f[0], f[1], f[2], f[3]});
        }
        setRange((short)rows.size());
    }
    void getText(char *dest, short item, short maxLen) override
    {
        if (item < 0 || item >= (short)rows.size())
        {
            dest[0] = 0;
            return;
        }
        char line[64]; // number, type and size are ASCII; the name may not be
        std::snprintf(line, sizeof line, "%-4s %-28.28s %8s bytes  ",
                      rows[item].no.c_str(), rows[item].type.c_str(),
                      rows[item].bytes.c_str());
        putText(dest, line + rows[item].name, maxLen);
    }
    void selectItem(short i) override
    {
        if (i < 0 || i >= (short)rows.size())
            return;
        std::string suggested = rows[i].name.empty() ? "part" + rows[i].no : rows[i].name;
        std::string dest = askSavePath(suggested);
        if (dest.empty())
            return;
        std::string cmd = backendCommand("save " + std::to_string(msgIdx) + " " + rows[i].no + " " + shq(dest));
        if (!msgMbox.empty())
            cmd += " " + shq(msgMbox);
        std::string out = shCapture(cmd + " 2>&1");
        while (!out.empty() && (out.back() == '\n' || out.back() == ' '))
            out.pop_back();
        notify(out, out.rfind("wrote", 0) == 0 ? noteInfo : noteError);
    }
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

class TPartsWindow : public TViewerWindow
{
public:
    TPartsWindow(const TRect &b, int idx, const std::string &mbox, const char *title)
        : TWindowInit(&TPartsWindow::initFrame),
          TViewerWindow(b, title)
    {
        palette = wpCyanWindow;
        options |= ofTileable;
        TScrollBar *vsb = new TScrollBar(TRect(size.x - 1, 1, size.x, size.y - 1));
        insert(vsb);
        insert(new TPartsPane(TRect(1, 1, size.x - 1, size.y - 1), vsb, idx, mbox));
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

TWindow *newPartsWindow(const TRect &bounds, int idx, const std::string &mbox,
                        const char *title)
{
    return new TPartsWindow(bounds, idx, mbox, title);
}
