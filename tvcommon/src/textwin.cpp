// textwin.cpp - a read-only text window in the cyan style: each app's Help.
#include "tvcommon.h"
#include "views.h"

namespace
{
class TTextWindow : public TViewerWindow
{
public:
    TTextWindow(const TRect &b, const char *title, const std::string &text)
        : TWindowInit(&TTextWindow::initFrame),
          TViewerWindow(b, title)
    {
        palette = wpCyanWindow;
        options |= ofTileable;
        TScrollBar *v = standardScrollBar(sbVertical | sbHandleKeyboard);
        TScrollBar *h = standardScrollBar(sbHorizontal | sbHandleKeyboard);
        TRect r = getExtent();
        r.grow(-1, -1);
        insert(new TTextView(r, h, v, splitLines(text)));
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

TWindow *newTextWindow(const TRect &bounds, const char *title, const std::string &text)
{
    return new TTextWindow(bounds, title, text);
}
