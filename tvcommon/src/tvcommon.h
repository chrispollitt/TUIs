// tvcommon.h - what tvmail and tvchat share: the Turbo Vision classes they
// use, the colours, the command ids the shared views know, and the free
// functions for diagnostics, the shell, text by columns and the status line.
// Classes shared between windows live in views.h; the application base
// (desktop, palette, Window list) in tvapp.h.  Each app's own header
// (tvmail.h, tvchat.h) includes this one and adds its commands.
#pragma once

#define Uses_TApplication
#define Uses_TKeys
#define Uses_TRect
#define Uses_TBackground
#define Uses_TDeskTop
#define Uses_TMenuBar
#define Uses_TMenuItem
#define Uses_TMenu
#define Uses_TSubMenu
#define Uses_TStatusLine
#define Uses_TStatusItem
#define Uses_TStatusDef
#define Uses_TScreen
#define Uses_TFileDialog
#define Uses_TDeskTop
#define Uses_TWindow
#define Uses_TFrame
#define Uses_TScrollBar
#define Uses_TScroller
#define Uses_TListViewer
#define Uses_TDrawBuffer
#define Uses_TEvent
#define Uses_MsgBox
#define Uses_TInputLine
#define Uses_TLabel
#define Uses_TEditor
#define Uses_TIndicator
#define Uses_TDialog
#define Uses_TButton
#define Uses_TCheckBoxes
#define Uses_TRadioButtons
#define Uses_TSItem
#define Uses_THistory
#define Uses_TCommandSet
#define Uses_TStaticText
#define Uses_TMemo
#define Uses_TText
#define Uses_TClipboard
#define Uses_TFindDialogRec
#include <tvision/tv.h>

#include <functional>
#include <string>
#include <vector>
#include <sstream>
#include <algorithm>
#include <cctype>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <chrono>
#include <functional>

#include <unistd.h>
#include <fcntl.h>
#include <poll.h>
#include <csignal>
#include <cerrno>
#include <sys/wait.h>

// The POSIX shell helper scripts run through (/bin/sh).  Set by tvcommon.cmake.
#ifndef TV_SH
#define TV_SH "/bin/sh"
#endif

// The app's name, for messages, temp files and the trace ("tvmail",
// "tvchat").  Each main() sets it before anything else runs.
extern const char *gAppName;

// ------------------------------------------------ commands both apps use ---
// Numbered with tvmail's (2000-2099); an app's own commands go in its header.
const ushort cmReload = 2001;       // File > Reload (F5)
const ushort cmAboutBox = 2007;
const ushort cmFocusContent = 2014; // Enter in a list pane -> on to the next pane
const ushort cmShowHelp = 2022;     // in-app help window
const ushort cmPaneFocused = 2024;  // broadcast: a 3-pane pane took focus
const ushort cmWindowList = 2034;   // Window > List... (Alt-0)
const ushort cmToggleWrap = 2043;   // View > Wrap long lines (body pane / focused text window)
const ushort cmRedrawScreen = 2044; // Window > Redraw screen (Ctrl-L)
const ushort cmNotifications = 2046; // Window > Notification history
const ushort cmEditConfig = 2045;   // File > Edit configuration... (the app opens configwin.cpp's window)

// -------------------------------------------------------- classic colors ----
// The Turbo Vision DOS look, in exact RGB.  BIOS colour numbers are not
// enough: the terminal's theme decides what "grey" or "green" look like, and
// under e.g. Windows Terminal's Dark+ (white == bright white == #E5E5E5) the
// classic white-on-grey dialog titles and light-cyan-on-green buttons
// vanish.  RGB goes out as 24-bit colour, or as xterm's fixed 6x6x6 cube on
// a 256-colour terminal - neither is touched by the theme.  (A 16-colour
// terminal gets the nearest theme colour, as before.)
inline TColorAttr vga(uchar bios, ushort style = 0)
{
    static const uint32_t rgb[16] = {
        0x000000, 0x0000AA, 0x00AA00, 0x00AAAA, 0xAA0000, 0xAA00AA, 0xAA5500, 0xAAAAAA,
        0x555555, 0x5555FF, 0x55FF55, 0x55FFFF, 0xFF5555, 0xFF55FF, 0xFFFF55, 0xFFFFFF};
    return TColorAttr(TColorRGB(rgb[bios & 15]), TColorRGB(rgb[(bios >> 4) & 15]), style);
}
// The "focus" green: darker than VGA green so black, white, yellow and light
// cyan text all read on it (contrast >= 4); an exact xterm-cube colour, so
// 256-colour terminals show the same.
constexpr uint32_t kFocusGreen = 0x008700;
inline TColorAttr onGreen(uint32_t fg, ushort style = 0)
{
    return TColorAttr(TColorRGB(fg), TColorRGB(kFocusGreen), style);
}

inline TColorAttr cNorm() { return vga(0x30); }          // black on cyan: text
inline TColorAttr cHi() { return vga(0x3F); }            // white on cyan: scroll arrows
inline TColorAttr cFocus() { return onGreen(0xFFFFFF); } // white on green: focused row, selection
inline TColorAttr cCur() { return vga(0xB0); }           // black on light cyan: current row, unfocused
inline TColorAttr cDiv() { return vga(0x33); }           // cyan on cyan
inline TColorAttr cFrame() { return vga(0x30); }         // black on cyan: frames + titles
                                                                // (white on cyan is < 3:1 anywhere)
inline TColorAttr cDim() { return vga(0x38); }           // grey on cyan: pane rules
inline TColorAttr cSystem() { return TColorAttr(TColorRGB(0x444444), TColorRGB(0x00AAAA)); } // dark grey on cyan: system lines
inline TColorAttr cRule() { return TColorAttr(TColorRGB(0x227777), TColorRGB(0x00AAAA)); }   // fainter still: the table column rules

// ---------------------------------------------------------- command line ---
// cli.cpp: --debug [FILE] / --trace [FILE] / --selftest / --help / --version.
struct CliOptions
{
    bool selftest = false;
    bool parseError = false;
    std::string debugPath;
    std::string tracePath;
};
// usage: the app's own --help text.  false = stop now (help, version or a
// bad option: exit with parseError ? 2 : 0).
bool parseOptions(int argc, char **argv, CliOptions &options,
                  const char *version, const char *usage);
void setEscDelay(); // before the app starts: see cli.cpp

// --------------------------------------------------- shell + diagnostics ---
// shell.cpp: the POSIX-sh plumbing helper calls go through, and the
// --debug / --trace logs.
extern const char *const kPreamble; // PATH preamble for every helper script
extern FILE *gDebugLog;
extern FILE *gTraceLog;
void debugLog(const char *fmt, ...);
long long traceMicros();
void traceSpan(const char *name, long long start);
void startDiagnostics(const CliOptions &options);
void stopDiagnostics();
std::string backendFlags(const char *tag = "backend"); // --debug/--trace for a helper
std::string tempDir();
long procId();
std::string writeTemp(const std::string &content, const char *suffix);
std::string shCapture(const std::string &body);
int shInteractive(const std::string &body);

// ------------------------------------------------------------- the tty ---
// tty.cpp: "mesg n" while the app runs - talk rings and wall from ordinary
// users stay off the screen (root's still get through: Ctrl-L clears them).
// mesgFromConf reads [ui] mesg = n | y (default n) from the app's .conf and
// calls mesgHold; it goes in main(), before the app is constructed.
void mesgHold(bool on); // clear the tty's group-write bit until exit
void mesgRelease();     // put it back while a helper has the terminal ...
void mesgRetake();      // ... and clear it again after
void mesgFromConf(const std::string &confPath);
std::string confValue(const std::string &path, const char *section, const char *key);

// ------------------------------------------------------------- dialogs ---
// dialogs.cpp: the Find / Replace dialogs TEditor and TMailText ask for
ushort tvEditDialog(int dialog, ...);

// ---------------------------------------------------- text + system util ---
// util.cpp
std::string shq(const std::string &s);
std::string slurp(const std::string &path);
std::string homePath(const char *rel);
std::vector<std::string> splitLines(const std::string &s);
std::string lowered(std::string s);
std::string fitCols(const std::string &s, int w);
void putText(char *dest, const std::string &s, short maxLen);
std::string expandTabs(const std::string &s);
extern TTimerId gFlashTimer;
void flashStatus(const std::string &msg);

// ---------------------------------------------------- toasts + history ---
// notify.cpp: a message that needs no answer.  A small note at the bottom right
// of the desktop (no focus, no key; gone in a few seconds, errors later; a click
// removes it) and a line in the history, Window > Notification history.  Use it
// instead of an OK box; boxes are for questions.
enum NoteLevel
{
    noteInfo,
    noteWarn,
    noteError
};
void notify(const std::string &text, NoteLevel level = noteInfo);
void notifyTick();                          // from idle(): expire the toasts
void notifyHistoryWindow(TDeskTop *desk);
void notifyRecordFlash(const std::string &msg); // flashStatus() keeps its line in the history too
// A new message, mail or chat: the toast, and a BEL if [ui] beep = y (a terminal
// beeps or flashes its window; at most one in two seconds).
void beepFromConf(const std::string &confPath); // goes in main(), like mesgFromConf
void notifyBeep();
void notifyAlert(const std::string &text, NoteLevel level = noteInfo);
// The last key or mouse click, from TTvApp::getEvent: "idle" for the chat toast.
void inputSeen();
int idleSeconds();
void systemClipboard(const std::string &text);

// ---------------------------------------------------- the config editor ---
// configwin.cpp: an editor window for the app's configuration file, with Verify
// (F9) and Save + apply (F8).  The app says which file it is, what to check it with
// and how to make itself use it.
struct ConfigSpec
{
    std::string title;  // "tvchat.conf"
    std::string path;   // the file; if it does not exist the window opens with `sample`
    std::string sample; // text for a new file (the shipped example), may be empty
    // Check the TEXT in `file` (a copy of the window's buffer, not the real file);
    // false = it has errors.  `report` is what to tell the user (warnings too).
    std::function<bool(const std::string &file, std::string &report)> verify;
    // After the file is saved: make the running app use it.  false = it could not,
    // and `msg` says why; on success `msg` may be a note for the status line.
    std::function<bool(std::string &msg)> apply;
};
void openConfigWindow(TDeskTop *desk, const ConfigSpec &spec);

// ------------------------------------------------------------- windows ---
// textwin.cpp: a read-only text window in the cyan style (an app's Help)
TWindow *newTextWindow(const TRect &bounds, const char *title, const std::string &text);
