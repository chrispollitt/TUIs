#include "tvmail.h"
#include "views.h"

// ===================================================== address book =========
// A ~/.mailrc alias/group list.  Enter drops the expansion into the most
// recently opened compose window's To: field.
class TAddrPane : public TListViewer
{
    std::vector<std::pair<std::string, std::string>> rows; // name, expansion
public:
    TAddrPane(const TRect &b, TScrollBar *vsb) : TListViewer(b, 1, nullptr, vsb)
    {
        std::istringstream is(backendRun("aliases"));
        std::string ln;
        while (std::getline(is, ln))
        {
            if (!ln.empty() && ln.back() == '\r')
                ln.pop_back();
            size_t t = ln.find('\t');
            if (t != std::string::npos)
                rows.emplace_back(ln.substr(0, t), ln.substr(t + 1));
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
        putText(dest, fitCols(rows[item].first, 14) + "  " + rows[item].second, maxLen);
    }
    void selectItem(short i) override
    {
        if (i < 0 || i >= (short)rows.size())
            return;
        if (!composeAddRecipient(rows[i].second))
            notify("Open a compose window first.", noteInfo);
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

class TAddrBookWindow : public TViewerWindow
{
public:
    TAddrBookWindow(const TRect &b)
        : TWindowInit(&TAddrBookWindow::initFrame),
          TViewerWindow(b, "Address book (~/.mailrc)")
    {
        palette = wpCyanWindow;
        options |= ofTileable;
        TScrollBar *vsb = new TScrollBar(TRect(size.x - 1, 1, size.x, size.y - 1));
        insert(vsb);
        insert(new TAddrPane(TRect(1, 1, size.x - 1, size.y - 1), vsb));
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

TWindow *newAddrBookWindow(const TRect &bounds)
{
    return new TAddrBookWindow(bounds);
}
