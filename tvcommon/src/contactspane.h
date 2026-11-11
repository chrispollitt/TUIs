// contactspane.h - the list of contacts and groups on its own, for a window of any shape: the Contacts window
// (contactswin.cpp) is this list with a hint line, and tvcal's Contacts service is this list with the contact shown
// whole in the pane below.  It does no networking of its own: it asks tvcontacts.py (see contactswin.cpp).
#pragma once
#include "views.h"

struct ContactRow
{
    bool group = false;
    std::string uid, name, nicks, detail, book; // book: the label of the address book it is in (mine, family ...)
};

class TContactsPane : public TListViewer
{
public:
    std::vector<ContactRow> rows;
    std::string note;                      // e.g. "offline: this is the copy from the last time ..."
    std::function<void()> moved, gotFocus; // the highlight moved / the pane took the focus

    TContactsPane(const TRect &b, TScrollBar *vsb);

    void reload(bool force); // force: sync with the server first
    void addEntry(bool group);
    void editEntry();
    void deleteEntry();
    const ContactRow *current() const { return focused >= 0 && focused < (short)rows.size() ? &rows[focused] : nullptr; }
    std::vector<std::string> detailLines() const; // the entry shown whole, one line each

    void getText(char *dest, short item, short maxLen) override;
    void focusItem(short item) override;
    void setState(ushort aState, Boolean enable) override;
    void handleEvent(TEvent &e) override;
    TColorAttr mapColor(uchar i) override;

private:
    void save(const ContactRow &row, const std::string &uid);
};
