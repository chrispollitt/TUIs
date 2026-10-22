// dialogs.cpp - the Find / Replace dialogs an editor or a text view asks for
// (TEditor::editorDialog; each app sets it to tvEditDialog).
#include "tvcommon.h"

// ------------------------------------------------------ editor dialogs -----
// Wire up the standard Find / Replace / "search failed" dialogs the TEditor
// needs (tvision ships only a cmCancel stub).  Lifted from tvision's tvedit
// example (tvedit2/3.cpp), trimmed to what tvmail and tvchat use.
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

ushort tvEditDialog(int dialog, ...)
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
