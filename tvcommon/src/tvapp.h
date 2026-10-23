// tvapp.h - the application base tvmail's and tvchat's apps derive from:
// the blue desktop, the retuned palette, the editor clipboard and Find
// dialogs, the status-line flash, Window > List and the full redraw.
#pragma once
#include "tvcommon.h"

class TTvApp : public TApplication
{
public:
    // The derived app's constructor names its own menu bar and status line:
    //   TProgInit(&MyApp::initStatusLine, &MyApp::initMenuBar, &TTvApp::initDeskTop)
    // (TProgInit is a virtual base: the most derived class initialises it.)
    TTvApp();
    static TDeskTop *initDeskTop(TRect r);
    TColorAttr mapColor(uchar index) override;
    // The flash note's timer, Window > List (cmWindowList) and Redraw.  The derived
    // app calls this first; a cleared event was handled here.
    void handleEvent(TEvent &e) override;
    void windowList();
    // Ctrl-L, anywhere - in a dialog or an editor too - is the full redraw.
    void getEvent(TEvent &e) override;
    // Window > Redraw screen (cmRedrawScreen, Ctrl-L): repaint every cell.
    // The cure for text other processes write onto the terminal (root's
    // wall, shutdown notices), which tvision's repaints never cover.
    void redrawScreen();
};
