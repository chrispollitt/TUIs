// app.h - the application object.
#pragma once
#include "tvmail.h"

// ---------------------------------------------------------------- app -------
class TVMailApp : public TApplication
{
public:
    TWindow *mainWin = nullptr; // the 3-pane mail window (see mailwin.cpp)

    TVMailApp()
        : TProgInit(&TVMailApp::initStatusLine,
                    &TVMailApp::initMenuBar,
                    &TVMailApp::initDeskTop)
    {
        // editor clipboard + Find/Replace dialogs (tvision ships neither)
        TEditor::clipboard = new TEditor(TRect(0, 0, 0, 0), 0, 0, 0, 0x10000);
        TEditor::clipboard->canUndo = False;
        TEditor::editorDialog = tvmailEditDialog;

        // grey the edit commands until an editor has focus
        TCommandSet es;
        es.enableCmd(cmCut);
        es.enableCmd(cmCopy);
        es.enableCmd(cmPaste);
        es.enableCmd(cmClear);
        es.enableCmd(cmUndo);
        es.enableCmd(cmFind);
        es.enableCmd(cmReplace);
        es.enableCmd(cmSearchAgain);
        disableCommands(es);

        mainWin = newMailWindow(deskTop->getExtent());
        deskTop->insert(mainWin);
    }

    static TMenuBar *initMenuBar(TRect r);
    static TStatusLine *initStatusLine(TRect r);
    static TDeskTop *initDeskTop(TRect r);
    void handleEvent(TEvent &e) override;
    TColorAttr mapColor(uchar index) override;
#ifndef _WIN32
    void idle() override; // poll the background pull
#endif

private:
    int currentRow();
    void viewSource(int row);
    void replyOrCompose(int row, bool all = false); // row < 0 => new message
    void forwardMsg(int row);
    void editDraft(int row);
    void resumeDeadLetter();
    void deleteMsg(int row);
    void markUnread(int row);
    void markRead(int row);
    void saveMsg(int row);
    void windowList();
    void outOfOffice();
    void moveMsg(int row);
    void viewParts(int row);
    void purgeDialog();
    void emptyTrash();
    void pullMail();
    void pullFinished(); // cmPullDone handler
    void reload();
#ifndef _WIN32
    pid_t pullPid = -1; // >0 while pop-pull runs detached
    int pullExit = 0;
    std::string pullLogPath;
#endif
};
