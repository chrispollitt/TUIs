// dialogs.cpp - small modal dialogs: the editor's Find / Replace, the unsent
// message prompt and the save-as file browser.
#include "tvmail.h"

// ------------------------------------------------------ editor dialogs -----
// Wire up the standard Find / Replace / "search failed" dialogs the TEditor
// needs (tvision ships only a cmCancel stub).  Lifted from tvision's tvedit
// example (tvedit2/3.cpp), trimmed to what a composer uses.
static ushort execDialog(TDialog *d, void *data)
{
    TView *p = TProgram::application->validView(d);
    if (!p)
        return cmCancel;
    if (data)
        p->setData(data);
    ushort result = TProgram::deskTop->execView(p);
    if (result != cmCancel && data)
        p->getData(data);
    TObject::destroy(p);
    return result;
}

static TDialog *createFindDialog()
{
    TDialog *d = new TDialog(TRect(0, 0, 38, 12), "Find");
    d->options |= ofCentered;
    TInputLine *c = new TInputLine(TRect(3, 3, 32, 4), 80);
    d->insert(c);
    d->insert(new TLabel(TRect(2, 2, 15, 3), "~T~ext to find", c));
    d->insert(new THistory(TRect(32, 3, 35, 4), c, 10));
    d->insert(new TCheckBoxes(TRect(3, 5, 35, 7),
                              new TSItem("~C~ase sensitive",
                                         new TSItem("~W~hole words only", 0))));
    d->insert(new TButton(TRect(14, 9, 24, 11), "O~K~", cmOK, bfDefault));
    d->insert(new TButton(TRect(26, 9, 36, 11), "Cancel", cmCancel, bfNormal));
    d->selectNext(False);
    return d;
}

static TDialog *createReplaceDialog()
{
    TDialog *d = new TDialog(TRect(0, 0, 40, 16), "Replace");
    d->options |= ofCentered;
    TInputLine *c = new TInputLine(TRect(3, 3, 34, 4), 80);
    d->insert(c);
    d->insert(new TLabel(TRect(2, 2, 15, 3), "~T~ext to find", c));
    d->insert(new THistory(TRect(34, 3, 37, 4), c, 10));
    c = new TInputLine(TRect(3, 6, 34, 7), 80);
    d->insert(c);
    d->insert(new TLabel(TRect(2, 5, 12, 6), "~N~ew text", c));
    d->insert(new THistory(TRect(34, 6, 37, 7), c, 11));
    d->insert(new TCheckBoxes(TRect(3, 8, 37, 12),
                              new TSItem("~C~ase sensitive",
                                         new TSItem("~W~hole words only",
                                                    new TSItem("~P~rompt on replace",
                                                               new TSItem("~R~eplace all", 0))))));
    d->insert(new TButton(TRect(17, 13, 27, 15), "O~K~", cmOK, bfDefault));
    d->insert(new TButton(TRect(28, 13, 38, 15), "Cancel", cmCancel, bfNormal));
    d->selectNext(False);
    return d;
}

ushort tvmailEditDialog(int dialog, ...)
{
    va_list arg;
    switch (dialog)
    {
    case edOutOfMemory:
        return messageBox("Not enough memory for this operation.",
                          mfError | mfOKButton);
    case edFind:
    {
        va_start(arg, dialog);
        void *p = va_arg(arg, void *);
        va_end(arg);
        return execDialog(createFindDialog(), p);
    }
    case edSearchFailed:
        return messageBox("Search string not found.", mfError | mfOKButton);
    case edReplace:
    {
        va_start(arg, dialog);
        void *p = va_arg(arg, void *);
        va_end(arg);
        return execDialog(createReplaceDialog(), p);
    }
    case edReplacePrompt:
        return messageBox("Replace this occurrence?",
                          mfYesNoCancel | mfInformation);
    }
    return cmCancel;
}

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
