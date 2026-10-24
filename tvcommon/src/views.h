// views.h - view classes shared between windows: the read-only text view
// (body pane, Source / Help / Purge preview, the chat log), the 3-pane
// rules and headings, the viewer window, and the editor + input line the
// composer and others use.
#pragma once
#include "tvcommon.h"

// ------------------------------------------------------- mail text view ----
// Read-only text for the body pane and the Source / Help / Purge-preview
// windows.  Lines are measured in screen columns (UTF-8 safe, tabs
// expanded) and can be word-wrapped to the view width (View > Wrap long
// lines); unwrapped, a line that runs past the edge ends in a ">>" mark.
// Edit > Find / Find again search the text.  Shift+arrows / Shift+PgUp/PgDn
// or a mouse drag select whole lines; Edit > Copy (Ctrl-Ins) puts them on
// the clipboard - the whole text when nothing is selected.  Esc drops the
// selection.

class TMailText : public TScroller
{
    struct Seg // one screen row: a piece of source line `line`
    {
        int line;
        size_t off, len;
        bool cont; // continues the row above (a wrapped line)
    };
    std::vector<std::string> src; // tab-expanded source lines
    std::vector<int> srcWidth;    // their widths in columns
    std::vector<Seg> segs;
    bool wrap;
    int selA = -1, selB = -1; // selected rows: anchor, moving end
    int hlLine = -1;          // last Find match, in source coordinates
    size_t hlOff = 0, hlLen = 0;

    int dx() const { return wrap ? 0 : delta.x; }
    int topLine() const
    {
        return delta.y >= 0 && delta.y < (int)segs.size() ? segs[delta.y].line : 0;
    }

    // Rebuild the screen rows from src; keepLine = the source line to show
    // at the top afterwards (-1: leave the scroll position to the caller).
    void layout(int keepLine)
    {
        segs.clear();
        selA = selB = -1;
        int W = size.x > 0 ? size.x : 1, maxW = 1;
        for (int i = 0; i < (int)src.size(); ++i)
        {
            const std::string &s = src[i];
            maxW = std::max(maxW, srcWidth[i]);
            if (!wrap || srcWidth[i] <= W)
            {
                segs.push_back({i, 0, s.size(), false});
                continue;
            }
            size_t pos = 0;
            bool cont = false;
            while (pos < s.size())
            {
                TStringView rest(s.data() + pos, s.size() - pos);
                size_t len = TText::scroll(rest, W, False);
                if (len == 0) // one character wider than the view
                    len = std::max<size_t>(1, TText::next(rest));
                if (pos + len < s.size())
                {
                    if (s[pos + len] == ' ')
                        ++len; // break on the space: it ends this row
                    else
                    {
                        size_t sp = s.rfind(' ', pos + len - 1);
                        if (sp != std::string::npos && sp > pos)
                            len = sp + 1 - pos; // whole words only
                    }
                }
                segs.push_back({i, pos, len, cont});
                pos += len;
                cont = true;
            }
        }
        if (segs.empty())
            segs.push_back({0, 0, 0, false});
        setLimit(wrap ? W : maxW + 1, (int)segs.size());
        if (keepLine >= 0)
            for (size_t r = 0; r < segs.size(); ++r)
                if (segs[r].line >= keepLine)
                {
                    scrollTo(0, (int)r);
                    break;
                }
    }

    int rowOf(int line, size_t off) const
    {
        int row = 0;
        for (size_t r = 0; r < segs.size(); ++r)
            if (segs[r].line == line && segs[r].off <= off)
                row = (int)r;
        return row;
    }

    // Move the selection's free end to `row` and keep it on screen.
    void extendTo(int row)
    {
        row = std::max(0, std::min(row, (int)segs.size() - 1));
        if (selA < 0)
            selA = row;
        selB = row;
        if (row < delta.y)
            scrollTo(delta.x, row);
        else if (row >= delta.y + size.y)
            scrollTo(delta.x, row - size.y + 1);
        drawView();
    }

    void trackMouse(TEvent &e)
    {
        if (!(state & sfFocused))
            select();
        bool extend = (e.mouse.controlKeyState & kbShift) && selA >= 0;
        int anchor = -1;
        bool moved = false;
        do
        {
            TPoint p = makeLocal(e.mouse.where);
            if (p.y < 0)
                scrollTo(delta.x, delta.y - 1);
            else if (p.y >= size.y)
                scrollTo(delta.x, delta.y + 1);
            int row = delta.y + std::max(0, std::min<int>(p.y, size.y - 1));
            row = std::max(0, std::min(row, (int)segs.size() - 1));
            if (anchor < 0)
                anchor = extend ? selA : row;
            if (row != anchor)
                moved = true;
            if (moved || extend)
            {
                selA = anchor;
                selB = row;
                drawView();
            }
        } while (mouseEvent(e, evMouseMove | evMouseAuto));
        if (!moved && !extend && selA >= 0)
        {
            selA = selB = -1; // a plain click drops the selection
            drawView();
        }
    }

    void copy()
    {
        int a = selA, b = selB;
        if (a < 0)
        {
            a = 0;
            b = (int)segs.size() - 1;
        }
        if (a > b)
            std::swap(a, b);
        std::string out;
        for (int r = a; r <= b; ++r)
        {
            const Seg &g = segs[r];
            if (r > a && !g.cont)
                out += '\n';
            out.append(src[g.line], g.off, g.len);
        }
        out += '\n';
        // the app's own clipboard: what Shift-Ins pastes into an editor
        if (TEditor::clipboard && out.size() < TEditor::clipboard->bufSize)
            TEditor::clipboard->insertText(out.data(), (uint)out.size(), True);
        systemClipboard(out); // and the desktop's, for other programs
        int n = 0;
        for (int r = a; r <= b; ++r)
            n += !segs[r].cont;
        flashStatus(selA < 0 ? "Copied the whole text (" + std::to_string(n) + " lines)"
                             : "Copied " + std::to_string(n) + (n == 1 ? " line" : " lines"));
    }

    static bool isWordChar(char c)
    {
        return std::isalnum((unsigned char)c) || c == '_' || (c & 0x80);
    }

    // Edit > Find (ask) / Find again (reuse TEditor's last search, which
    // the compose editor shares).  Searches on from the last match, or from
    // the top of the view, and wraps around to the start once.
    void find(bool ask)
    {
        if (ask || !*TEditor::findStr)
        {
            TFindDialogRec rec(TEditor::findStr, TEditor::editorFlags);
            if (TEditor::editorDialog(edFind, &rec) == cmCancel)
                return;
            std::strcpy(TEditor::findStr, rec.find);
            TEditor::editorFlags = rec.options & ~efDoReplace;
        }
        std::string pat = TEditor::findStr;
        if (pat.empty() || src.empty())
            return;
        bool cs = TEditor::editorFlags & efCaseSensitive;
        bool ww = TEditor::editorFlags & efWholeWordsOnly;
        if (!cs)
            pat = lowered(pat);

        int n = (int)src.size(), start = hlLine >= 0 ? hlLine : topLine();
        size_t from = hlLine >= 0 ? hlOff + 1 : 0;
        for (int k = 0; k <= n; ++k)
        {
            int L = (start + k) % n;
            std::string s = cs ? src[L] : lowered(src[L]);
            size_t p = (k == 0) ? from : 0;
            while ((p = s.find(pat, p)) != std::string::npos)
            {
                if (k == n && p >= from)
                    break; // back where we began
                bool okWord = !ww || ((p == 0 || !isWordChar(s[p - 1])) &&
                                      (p + pat.size() >= s.size() || !isWordChar(s[p + pat.size()])));
                if (okWord)
                {
                    hlLine = L;
                    hlOff = p;
                    hlLen = pat.size();
                    int row = rowOf(L, p), x = delta.x;
                    if (!wrap)
                    {
                        int col = (int)TText::width(TStringView(src[L].data(), p));
                        if (col < delta.x || col + (int)hlLen > delta.x + size.x)
                            x = std::max(0, col - size.x / 3);
                    }
                    int y = delta.y;
                    if (row < delta.y || row >= delta.y + size.y)
                        y = std::max(0, row - size.y / 3);
                    scrollTo(x, y);
                    drawView();
                    return;
                }
                ++p;
            }
        }
        TEditor::editorDialog(edSearchFailed);
    }

protected:
    // The scrolling keys (also what a chat window's input line hands on).
    bool keyDown(const KeyDownEvent &k)
    {
        int page = size.y > 1 ? size.y - 1 : 1;
        ushort code = k.keyCode;
        if ((k.controlKeyState & kbShift) &&
            (code == kbUp || code == kbDown || code == kbPgUp || code == kbPgDn))
        {
            bool up = code == kbUp || code == kbPgUp;
            if (selA < 0) // start at the edge of the view you move away from
                extendTo(up ? delta.y + size.y - 1 : delta.y);
            else
                extendTo(selB + (code == kbUp ? -1 : code == kbDown ? 1 : up ? -page : page));
            return true;
        }
        if (code == kbEsc && selA >= 0)
        {
            selA = selB = -1;
            drawView();
            return true;
        }
        int nx = delta.x, ny = delta.y;
        switch (code)
        {
        case kbUp:
            ny -= 1;
            break;
        case kbDown:
            ny += 1;
            break;
        case kbLeft:
            nx -= 1;
            break;
        case kbRight:
            nx += 1;
            break;
        case kbPgUp:
            ny -= page;
            break;
        case kbPgDn:
            ny += page;
            break;
        case kbHome:
            nx = 0;
            break;
        case kbEnd:
            ny = limit.y;
            break; // scrollTo() clamps
        case kbCtrlPgUp:
            ny = 0;
            break;
        case kbCtrlPgDn:
            ny = limit.y;
            break;
        case kbEnter:
            ny += 1;
            break; // like a pager
        default:
            if (k.charScan.charCode == ' ')
                ny += page;
            else
                return false;
        }
        scrollTo(nx, ny);
        return true;
    }

    virtual void wrapChanged(bool) {}
    // The colour of source line `line` when it is not selected: the chat log
    // tells system and own lines apart this way.
    virtual TColorAttr lineColor(int, TColorAttr norm) { return norm; }
    int firstShownLine() const { return topLine(); }
    bool atBottom() const { return delta.y + size.y >= limit.y; }

    // Swap in new text, unlike setLines() even when it is the same, with
    // source line `top` at the top of the view (-1: the end of the text in
    // view).  keepSel: the old lines are still the first ones (text was only
    // added at the end), so a selection over them stays.
    void replaceLines(std::vector<std::string> ls, int top, bool keepSel = false)
    {
        int a = selA, b = selB;
        src.clear();
        srcWidth.clear();
        for (auto &l : ls)
        {
            src.push_back(expandTabs(l));
            srcWidth.push_back((int)TText::width(src.back()));
        }
        if (src.empty())
        {
            src.push_back(std::string());
            srcWidth.push_back(0);
        }
        hlLine = -1;
        layout(top);
        if (top < 0)
            scrollTo(0, limit.y); // clamped to the last page
        if (keepSel && a >= 0 && std::max(a, b) < (int)segs.size())
            selA = a, selB = b;
        drawView();
    }

public:
    TMailText(const TRect &b, TScrollBar *h, TScrollBar *v,
              std::vector<std::string> ls, bool wrapLines)
        : TScroller(b, h, v), wrap(wrapLines)
    {
        options |= ofFirstClick; // the first click can start a selection
        setLines(std::move(ls));
    }

    void setLines(std::vector<std::string> ls)
    {
        std::vector<std::string> fresh;
        for (auto &l : ls)
            fresh.push_back(expandTabs(l));
        if (fresh.empty())
            fresh.push_back(std::string());
        // The same text again (a reload after a pull, a draft save, a mark):
        // leave it be - scroll position, selection and Find match included -
        // instead of jumping back to line 1 of what you were reading.
        if (fresh == src)
            return;
        src = std::move(fresh);
        srcWidth.clear();
        for (auto &l : src)
            srcWidth.push_back((int)TText::width(l));
        hlLine = -1;
        layout(-1);
        scrollTo(0, 0);
        drawView();
    }

    void setWrap(bool w)
    {
        if (w == wrap)
            return;
        int top = topLine();
        wrap = w;
        scrollTo(0, delta.y);
        layout(top);
        drawView();
    }

    void changeBounds(const TRect &bounds) override
    {
        int top = topLine();
        TScroller::changeBounds(bounds);
        if (wrap)
        {
            layout(top);
            drawView();
        }
    }

    void draw() override
    {
        TColorAttr norm = getColor(1);
        TColorAttr selc = cFocus();
        TColorAttr match = vga(0xE0); // black on yellow
        int x0 = dx();
        int s0 = std::min(selA, selB), s1 = std::max(selA, selB);
        for (short y = 0; y < size.y; ++y)
        {
            int row = delta.y + y;
            bool inSel = selA >= 0 && row >= s0 && row <= s1;
            TColorAttr c = inSel                                 ? selc
                           : row >= 0 && row < (int)segs.size() ? lineColor(segs[row].line, norm)
                                                                 : norm;
            TDrawBuffer b;
            b.moveChar(0, ' ', c, size.x);
            if (row >= 0 && row < (int)segs.size())
            {
                const Seg &g = segs[row];
                const std::string &s = src[g.line];
                b.moveStr(0, TStringView(s.data() + g.off, g.len), c, size.x, x0);
                if (g.line == hlLine)
                {
                    size_t a = std::max(hlOff, g.off), e = std::min(hlOff + hlLen, g.off + g.len);
                    if (a < e)
                    {
                        int c0 = (int)TText::width(TStringView(s.data() + g.off, a - g.off)) - x0;
                        int cw = (int)TText::width(TStringView(s.data() + a, e - a));
                        for (int x = std::max(0, c0); x < c0 + cw && x < size.x; ++x)
                            b.putAttribute(x, match);
                    }
                }
                if (!wrap && size.x > 2)
                {
                    TColorAttr m = inSel ? cFocus() : cHi();
                    if (srcWidth[g.line] > x0 + size.x)
                        b.moveStr(size.x - 1, "\xC2\xBB", m); // >> more to the right
                    if (x0 > 0 && srcWidth[g.line] > 0)
                        b.moveStr(0, "\xC2\xAB", m); // << more to the left
                }
            }
            writeLine(0, y, size.x, 1, b);
        }
    }

    void handleEvent(TEvent &e) override
    {
        if (e.what == evMouseDown && (e.mouse.buttons & mbLeftButton))
        {
            trackMouse(e);
            clearEvent(e);
            return;
        }
        if (e.what == evKeyDown && (state & sfFocused) && keyDown(e.keyDown))
        {
            clearEvent(e);
            return;
        }
        if (e.what == evCommand && (state & sfFocused))
        {
            switch (e.message.command)
            {
            case cmCopy:
                copy();
                clearEvent(e);
                return;
            case cmFind:
                find(true);
                clearEvent(e);
                return;
            case cmSearchAgain:
                find(false);
                clearEvent(e);
                return;
            case cmToggleWrap:
                setWrap(!wrap);
                wrapChanged(wrap);
                clearEvent(e);
                return;
            }
        }
        TScroller::handleEvent(e);
    }

    // Copy / Find / Find again are live while this is the current view of
    // the active window.  Keyed on sfActive + sfSelected like TEditor, not
    // sfFocused: opening a menu takes the focus, and the items would grey
    // out just as the menu draws them.
    void setState(ushort aState, Boolean enable) override
    {
        TScroller::setState(aState, enable);
        if (aState & (sfActive | sfSelected))
        {
            TCommandSet cs;
            cs.enableCmd(cmCopy);
            cs.enableCmd(cmFind);
            cs.enableCmd(cmSearchAgain);
            if ((state & sfActive) && (state & sfSelected))
                enableCommands(cs);
            else
                disableCommands(cs);
        }
    }
};

// ------------------------------------------------------ 3-pane pieces ---
// The rule between panes and the heading over each one, for the mail and
// chat windows alike.
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

// --------------------------------------------------------- message viewer ---
// Source / Help / Purge preview: a TMailText that grows with its window.
class TTextView : public TMailText
{
public:
    TTextView(const TRect &b, TScrollBar *h, TScrollBar *v,
              std::vector<std::string> ls, bool wrapLines = false)
        : TMailText(b, h, v, std::move(ls), wrapLines)
    {
        growMode = gfGrowHiX | gfGrowHiY;
    }

    TColorAttr mapColor(uchar index) override
    {
        switch (index)
        {
        case 1:
            return cNorm(); // normal text
        case 2:
            return cHi(); // selected text
        }
        return TView::mapColor(index);
    }
};

// A read-only window (Source, Help, Parts, Address book, Purge preview):
// Esc closes it, once nothing inside wanted the key (a text selection takes
// the first Esc).  Help has always said "Esc / Alt-F3 closes".
class TViewerWindow : public TWindow
{
public:
    TViewerWindow(const TRect &b, const char *title)
        : TWindowInit(&TViewerWindow::initFrame), TWindow(b, title, wnNoNumber) {}
    void handleEvent(TEvent &e) override
    {
        TWindow::handleEvent(e);
        if (e.what == evKeyDown && e.keyDown.keyCode == kbEsc)
        {
            clearEvent(e);
            close();
        }
    }
};

class TMailViewWindow : public TViewerWindow
{
public:
    TMailViewWindow(const TRect &b, const char *title, std::vector<std::string> lines,
                    bool wrapLines = false)
        : TWindowInit(&TMailViewWindow::initFrame),
          TViewerWindow(b, title)
    {
        palette = wpCyanWindow; // <-- use cyan window palette
        options |= ofTileable;
        TScrollBar *v = standardScrollBar(sbVertical | sbHandleKeyboard);
        TScrollBar *h = standardScrollBar(sbHorizontal | sbHandleKeyboard);
        TRect r = getExtent();
        r.grow(-1, -1);
        insert(new TTextView(r, h, v, std::move(lines), wrapLines));
    }

    TColorAttr mapColor(uchar index) override
    {
        switch (index)
        {
        case 1:
            return cNorm();
        case 2:
            return cFrame();
        case 3:
            return cFrame();
        case 4:
            return cNorm();
        case 5:
            return cHi();
        case 6:
            return cNorm();
        case 7:
            return cHi();
        case 8:
            return cNorm();
        }
        return TView::mapColor(index);
    }
};

// -------------------------------------------------------- compose window ----
// A real in-app composer (cf. third_party/tvision/examples/tvedit): To/Cc/
// Subject input lines over a TEditor body with a scrollbar + L:C indicator.
// (tvmail's compose window: F2 sends via `tvmail-backend send`; closing a
// modified draft asks first.)

class TBodyEditor : public TEditor
{
public:
    TBodyEditor(const TRect &b, TScrollBar *h, TScrollBar *v, TIndicator *i, uint sz)
        : TEditor(b, h, v, i, sz) {}
    TColorAttr mapColor(uchar index) override
    {
        switch (index)
        {
        case 1:
            return cNorm(); // normal text
        case 2:
            return cFocus(); // selected text
        }
        return TView::mapColor(index);
    }
};

class TFieldLine : public TInputLine
{
public:
    TFieldLine(const TRect &b, int lim) : TInputLine(b, lim) {}
    TColorAttr mapColor(uchar index) override
    {
        switch (index)
        {
        case 1:
            return cNorm(); // passive
        case 2:
            return cCur(); // active
        case 3:
            return cFocus(); // selected block
        case 4:
            return cHi(); // scroll arrows
        }
        return TView::mapColor(index);
    }
};

// ------------------------------------------------------------- tables ------
// The left pane of the mail and chat windows (folders, servers) is a small
// table: a heading row, then one row per item, the columns split by faint
// rules.  `w` is the column's width; 0 = the one column that takes what the
// others leave.  A row is one cell per column; no cells = an empty row (the
// rules run on below the last item).
struct TableCol
{
    std::string head;
    int w;
    bool right = false; // numbers: flush right
};
using TableCols = std::vector<TableCol>;

inline void drawTableRow(TDrawBuffer &b, int width, const TableCols &cols,
                         const std::vector<std::string> &cells, TColorAttr text, TColorAttr rule)
{
    int fixed = 0;
    for (const TableCol &c : cols)
        fixed += c.w;
    int rest = std::max(width - fixed - (int)cols.size() + 1, 1);
    b.moveChar(0, ' ', text, width);
    int x = 0;
    for (size_t i = 0; i < cols.size(); ++i)
    {
        int w = cols[i].w ? cols[i].w : rest;
        if (i)
            b.moveChar(x++, '\xB3', rule, 1); // the vertical rule
        std::string s = i < cells.size() ? cells[i] : std::string();
        s = fitCols(s, w);
        if (cols[i].right) // fitCols pads on the right: move the padding over
        {
            size_t e = s.find_last_not_of(' ');
            std::string t = e == std::string::npos ? std::string() : s.substr(0, e + 1);
            s = std::string(s.size() - t.size(), ' ') + t;
        }
        b.moveStr(x, s, text);
        x += w;
    }
}

// The heading row over the table.
class TTableHead : public TView
{
    TableCols cols;

public:
    TTableHead(const TRect &b, TableCols c) : TView(b), cols(std::move(c)) { growMode = 0; }
    void draw() override
    {
        std::vector<std::string> cells;
        for (const TableCol &c : cols)
            cells.push_back(c.head);
        TDrawBuffer b;
        drawTableRow(b, size.x, cols, cells, cNorm(), cSystem());
        writeLine(0, 0, size.x, 1, b);
    }
};

// Draw a list as the table's rows: `cells(item)` gives an item's cells.  The
// colours are TListViewer's (1 normal, 2 inactive, 3 focused, 4 current but
// unfocused), the rules in the row's own colour on the highlighted row.
inline void drawTableList(TListViewer &v, const TableCols &cols,
                          const std::function<std::vector<std::string>(short)> &cells)
{
    bool front = (v.state & sfActive) && (v.state & sfSelected);
    TDrawBuffer b;
    for (short y = 0; y < v.size.y; ++y)
    {
        short item = y + v.topItem;
        bool real = item < v.range;
        TColorAttr text = front ? v.getColor(1) : v.getColor(2), rule = cSystem();
        if (real && item == v.focused)
            rule = text = (v.state & sfFocused) ? v.getColor(3) : v.getColor(4);
        drawTableRow(b, v.size.x, cols, real ? cells(item) : std::vector<std::string>(), text, rule);
        v.writeLine(0, y, v.size.x, 1, b);
    }
}
