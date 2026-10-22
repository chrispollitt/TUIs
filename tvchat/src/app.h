// app.h - the application object.
#pragma once
#include "tvchat.h"
#include "tvapp.h"
#include "chat.h"
#include <functional>

class TVChatApp : public TTvApp
{
public:
    TVChatApp();

    static TMenuBar *initMenuBar(TRect r);
    static TStatusLine *initStatusLine(TRect r);
    void handleEvent(TEvent &e) override;
    void idle() override; // the periodic ticks below

private:
    void chatEvents(); // cmChatEvents handler
    void aboutBox();
    // Periodic work.  idle() only decides that one is due and posts its
    // command; handleEvent() does the work, outside idle().  The chat
    // event poll is one.
    struct IdleTick
    {
        ushort command;
        std::function<int()> intervalMs; // <= 0: not now
        std::chrono::steady_clock::time_point last;
    };
    std::vector<IdleTick> ticks;
    void post(ushort command);
};
