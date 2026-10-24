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

// ---- the pieces of the menu bar, status line and About box both apps share,
// so the same action sits in the same place under the same key.
// File: a rule, then "Exit" (Alt-X), after the app's own items.
TMenuItem &tvExitItem();
// File's "Reload" (F5)
TMenuItem &tvReloadItem();
// Edit: Find... / Replace... (if withReplace) / Find again; the apps add
// Undo, Cut, Copy and Paste before them.
TMenuItem &tvFindItems(bool withReplace);
// Window: Next, Previous, Zoom, Tile, Cascade, List..., Redraw screen, Close
TSubMenu &tvWindowMenu();
// Help: Contents (F1), About
TSubMenu &tvHelpMenu();
// The status line: `appItems` (a TStatusDef(0, 0xFFFF) with the app's own items), then the shared closing slots
// F5 Reload, F6 Next, Alt-X Exit, F1 Help (and the hidden F10, Shift-F6, Alt-F3).
TStatusDef &tvStatusDef(TStatusDef &appItems);
// Help > About: `tagline` is the centred description (lines split by \n),
// `plumbing` the one line about what the app runs on.
void tvAboutBox(TDeskTop *desk, const char *app, const char *version,
                const char *tagline, const char *plumbing);
