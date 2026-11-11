// tvcal.h - what every part of tvcal shares: its command ids and the free
// functions that cross a file boundary.  What tvcal shares with tvmail and
// tvchat (the Turbo Vision classes, the colours, diagnostics, the shell, text
// by columns, toasts, the spool to the other apps) is ../tvcommon/src/tvcommon.h;
// shared views are in views.h.
#pragma once
#include "tvcommon.h"

class CoProcess;
CoProcess &calBackend(); // tvcal-backend serve (cal.cpp)

#ifndef TVCAL_VERSION
#define TVCAL_VERSION "0.1.0"
#endif

// --------------------------------------------------------------- commands ---
// 2200-2249: tvmail has 2000-2099 and tvchat 2100-2149.
const ushort cmCalPoll = 2200;      // self-posted from idle(): requests, reminders, refresh
const ushort cmCalNewEvent = 2201;  // Calendar > New event...
const ushort cmCalEditEvent = 2202; // Calendar > Edit event...
const ushort cmCalDelEvent = 2203;  // Calendar > Delete event
const ushort cmCalToday = 2204;     // Calendar > Go to today
const ushort cmCalGoto = 2205;      // Calendar > Go to date...
const ushort cmCalFind = 2206;      // Calendar > Find...
const ushort cmCalIncoming = 2207;  // Calendar > Incoming...: what tvmail / tvchat asked to add
const ushort cmCalNewTodo = 2210;   // To-do > New to-do...
const ushort cmCalEditTodo = 2211;
const ushort cmCalToggleTodo = 2212; // done <-> not done
const ushort cmCalDelTodo = 2213;
const ushort cmCalShowDone = 2214;   // View > Show completed to-dos
const ushort cmCalNewNote = 2215;    // Notes > New note...
const ushort cmCalEditNote = 2216;
const ushort cmCalDelNote = 2217;
const ushort cmCalMail = 2220;       // Share > Mail invitation...
const ushort cmCalShareChat = 2221;  // Share > Share in chat
const ushort cmCalImport = 2222;     // Share > Import .ics file...
const ushort cmCalExport = 2223;     // Share > Export .ics file...
const ushort cmFloyd = 2233;         // File > Floyd, the assistant... (F7)
const ushort cmCalPane = 2232;       // broadcast to the window: a pane took focus (infoPtr = pane)

// ------------------------------------------------------------- calwin.cpp ---
void calOpenWindow();          // the calendar window, made the first time
bool calCommand(ushort cmd);   // a cmCal* command; false if not one
void calPoll();                // requests from other apps, reminders, a refresh when due
int calIncomingCount();        // for the status line
void calWindowOnReload();      // File > Reload
void calShowContacts();        // File > Contacts...: the Contacts service
void calWindowRefresh();       // re-read the calendar (Floyd may have changed it)
TWindow *newFloydWindow(const TRect &bounds);   // floydwin.cpp: Floyd's chat window (F7)
void calSettingsChanged();     // after the configuration was applied
std::string calCurrentDate();  // the date shown, YYYY-MM-DD

// ---------------------------------------------------------------- help.cpp ---
TWindow *newHelpWindow(const TRect &bounds);

// ------------------------------------------------------------------ cal.cpp ---
// The co-process, and the dates the views work with.
struct Ymd
{
    int y = 1970, m = 1, d = 1;
    bool operator==(const Ymd &o) const { return y == o.y && m == o.m && d == o.d; }
    bool operator!=(const Ymd &o) const { return !(*this == o); }
    bool operator<(const Ymd &o) const { return y != o.y ? y < o.y : m != o.m ? m < o.m : d < o.d; }
    bool operator<=(const Ymd &o) const { return !(o < *this); }
};
Ymd ymdToday();
Ymd ymdAdd(const Ymd &a, int days);
int ymdDiff(const Ymd &a, const Ymd &b);        // a - b in days
int ymdWeekday(const Ymd &a);                   // Monday = 0
int ymdMonthLength(int y, int m);
std::string ymdIso(const Ymd &a);               // 2026-11-05
bool ymdParse(const std::string &iso, Ymd &out);
Ymd ymdAddMonths(const Ymd &a, int months);     // the day clamped to the month's length

struct Occ // one line of `range`
{
    std::string date, start, end, uid, summary, location, repeat, remind;
};
struct TodoItem // one line of `todos`
{
    std::string uid, prio, due, summary, cats;
    bool done = false;
};

std::vector<std::string> calFields(const std::string &line); // a reply line split and unescaped
// One request; false + err when the backend says no (status 1: its message) or is gone.
bool calAsk(const std::string &req, std::string &body, std::string &err);
bool calBackendUp();
void calBackendStop();
std::string calConfPath();
