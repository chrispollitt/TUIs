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
