// app.h - the application object.
#pragma once
#include "tvcal.h"
#include "tvapp.h"
#include <functional>

class TVCalApp : public TTvApp
{
public:
    TVCalApp();

    static TMenuBar *initMenuBar(TRect r);
    static TStatusLine *initStatusLine(TRect r);
    void handleEvent(TEvent &e) override;
    void idle() override; // the periodic poll

private:
    void aboutBox();
    void editConfig(); // File > Edit configuration...
    std::chrono::steady_clock::time_point lastPoll;
    void post(ushort command);
};
