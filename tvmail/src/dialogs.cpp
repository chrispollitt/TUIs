// dialogs.cpp - tvmail's small modal dialogs: the unsent message prompt and
// the save-as file browser.  (The editor's Find / Replace are tvcommon's.)
#include "tvmail.h"

ushort askSaveDraft()
{
    TDialog *d = new TDialog(TRect(0, 0, 46, 9), "Unsent message");
    d->options |= ofCentered;
    d->insert(new TStaticText(TRect(3, 2, 43, 4), "This message hasn't been sent."));
    d->insert(new TButton(TRect(3, 5, 15, 7), "~S~ave draft", cmYes, bfDefault));
    d->insert(new TButton(TRect(17, 5, 30, 7), "~D~iscard", cmNo, bfNormal));
    d->insert(new TButton(TRect(32, 5, 43, 7), "Cancel", cmCancel, bfNormal));
    ushort r = TProgram::deskTop->execView(d);
    TObject::destroy(d);
    return r;
}

// Prompt for a filesystem path (used by the attachment save dialog), via
// tvision's own file browser - lets you navigate directories instead of
// typing a full path blind. Pre-filled with the attachment's own name (not
// a wildcard) so the directory listing still shows every file and Enter
// alone saves into the current directory under that name. Empty string
// means cancelled.
std::string askSavePath(const std::string &suggested, const char *title)
{
    TFileDialog *d = new TFileDialog("*.*", title, "~N~ame",
                                     fdOKButton, 100);
    char path[MAXPATH];
    std::strncpy(path, suggested.c_str(), sizeof path - 1);
    path[sizeof path - 1] = '\0';
    if (path[0])
        d->setData(path);
    ushort r = TProgram::deskTop->execView(d);
    std::string dest;
    if (r != cmCancel)
    {
        d->getData(path);
        dest = path;
    }
    TObject::destroy(d);
    return dest;
}

// One line of text in a small dialog; "" when cancelled.
std::string askLine(const char *title, const char *label, const std::string &suggested)
{
    TDialog *d = new TDialog(TRect(0, 0, 54, 9), title);
    d->options |= ofCentered;
    TInputLine *il = new TInputLine(TRect(3, 3, 51, 4), 200);
    d->insert(il);
    d->insert(new TLabel(TRect(3, 2, 51, 3), label, il));
    std::strncpy(il->data, suggested.c_str(), il->maxLen);
    il->data[il->maxLen] = '\0';
    d->insert(new TButton(TRect(14, 5, 26, 7), "O~K~", cmOK, bfDefault));
    d->insert(new TButton(TRect(28, 5, 40, 7), "Cancel", cmCancel, bfNormal));
    il->select();
    ushort r = TProgram::deskTop->execView(d);
    std::string s;
    if (r == cmOK)
        s = il->data;
    TObject::destroy(d);
    size_t a = s.find_first_not_of(" \t");
    size_t b = s.find_last_not_of(" \t");
    return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
}

// The rows of a pick list; Enter chooses (the dialog's OK), Esc cancels.
class TPickList : public TListViewer
{
    const std::vector<std::string> &rows;

public:
    TPickList(const TRect &b, TScrollBar *sb, const std::vector<std::string> &r)
        : TListViewer(b, 1, nullptr, sb), rows(r)
    {
        setRange((short)rows.size());
    }
    void getText(char *dest, short item, short maxLen) override
    {
        std::string s = item >= 0 && item < (short)rows.size() ? rows[item] : std::string();
        std::strncpy(dest, s.c_str(), maxLen);
        dest[maxLen] = '\0';
    }
    void selectItem(short) override { message(owner, evCommand, cmOK, this); }
};

int pickFromList(const char *title, const std::vector<std::string> &rows, int start)
{
    if (rows.empty())
        return -1;
    TDialog *d = new TDialog(TRect(0, 0, 76, 22), title);
    d->options |= ofCentered;
    TScrollBar *sb = new TScrollBar(TRect(73, 2, 74, 18));
    d->insert(sb);
    TPickList *pl = new TPickList(TRect(2, 2, 73, 18), sb, rows);
    d->insert(pl);
    d->insert(new TButton(TRect(22, 19, 34, 21), "O~K~", cmOK, bfDefault));
    d->insert(new TButton(TRect(40, 19, 52, 21), "Cancel", cmCancel, bfNormal));
    pl->focusItem((short)std::max(0, std::min<int>(start, (int)rows.size() - 1)));
    pl->select();
    ushort r = TProgram::deskTop->execView(d);
    int picked = r == cmOK ? pl->focused : -1;
    TObject::destroy(d);
    return picked;
}
