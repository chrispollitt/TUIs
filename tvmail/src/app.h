// app.h - the application object.
#pragma once
#include "tvmail.h"
#include "tvapp.h"
#include <functional>

// ---------------------------------------------------------------- app -------
class TVMailApp : public TTvApp
{
public:
    TWindow *mainWin = nullptr; // the 3-pane mail window (see mailwin.cpp)

    TVMailApp()
        : TProgInit(&TVMailApp::initStatusLine,
                    &TVMailApp::initMenuBar,
                    &TTvApp::initDeskTop)
    {
        // (the clipboard, Find dialogs and greyed edit commands: TTvApp)
        mainWin = newMailWindow(deskTop->getExtent());
        deskTop->insert(mainWin);
        // [ui] check = SECONDS between quiet pulls (default 300, 0 = never); a pull
        // files spam, and a toast says how many new messages it left in the inbox
        const char *conf = getenv("TVMAIL_CONF");
        std::string v = confValue(conf && *conf ? conf : homePath(".config/tvmail/tvmail.conf"), "ui", "check");
        int secs = v.empty() ? 300 : std::atoi(v.c_str());
        ticks.push_back({cmPullAuto, [secs]() { return secs * 1000; }, std::chrono::steady_clock::now()});
    }

    static TMenuBar *initMenuBar(TRect r);
    static TStatusLine *initStatusLine(TRect r);
    void handleEvent(TEvent &e) override;
    void idle() override; // the background pull, and the periodic ticks below

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
    void editConfig(); // File > Edit configuration...
    void chatWithSender(int row);
    void openChatInvite(int row);
    void outOfOffice();
    void moveMsg(int row);
    void viewParts(int row);
    void purgeDialog();
    void emptyTrash();
    void pullMail();
    void pullFinished(); // cmPullDone handler
    void reload();
    // Periodic work.  idle() only decides that one is due and posts its
    // command; handleEvent() does the work, outside idle().  TO-DO 14's
    // periodic mail pull can be one.
    struct IdleTick
    {
        ushort command;
        std::function<int()> intervalMs; // <= 0: not now
        std::chrono::steady_clock::time_point last;
    };
    std::vector<IdleTick> ticks;
    void post(ushort command);
    pid_t pullPid = -1; // >0 while pop-pull runs detached
    int pullExit = 0;
    std::string pullLogPath;
    bool pullQuiet = false;  // the pull is the periodic one: only errors and new mail speak
    int unreadBefore = -1;   // the inbox when the pull began
    int lastUnread = -1;     // the inbox after the last pull
};
