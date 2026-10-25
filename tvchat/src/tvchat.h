// tvchat.h - what every part of tvchat shares: its command ids and the free
// functions that cross a file boundary.  What tvchat shares with tvmail (the
// Turbo Vision classes, the colours, diagnostics, the shell, text by
// columns) is ../tvcommon/src/tvcommon.h; shared views are in views.h.
#pragma once
#include "tvcommon.h"

class CoProcess;
CoProcess &chatBackend(); // tvchat-backend serve (chat.cpp)

#ifndef TVCHAT_VERSION
#define TVCHAT_VERSION "0.1.0"
#endif

// --------------------------------------------------------------- commands ---
// 2100-2149, as when chat was part of tvmail; the ones both apps' shared
// views use (help, Window list, wrap, pane focus ...) are tvcommon.h's.
const ushort cmChatEvents = 2100; // self-posted from idle(): poll tvchat-backend
const ushort cmChatEvent = 2101;  // broadcast, one per event: infoPtr = const ChatEvent *
const ushort cmChatReset = 2102;  // broadcast: the chat backend restarted, re-read everything
const ushort cmChatConnect = 2103;    // Chat > Connect... (pick an account)
const ushort cmChatDisconnect = 2104; // Chat > Disconnect
const ushort cmChatAnswer = 2105;     // Chat > Answer: a talk call to the conversation shown
const ushort cmChatJoin = 2106;       // Chat > Join... (channel, nick or address)
const ushort cmChatLeave = 2107;      // Chat > Leave: part the conversation the chat window shows
const ushort cmChatOff = 2108;        // a note in a menu or the status line: never does anything
const ushort cmChatTalkView = 2109;   // View > Talk view: classic <-> IRC, for the talk conversation shown
const ushort cmChatAccount = 2110;    // + i: Chat > <account i> shows it in the chat window
const int kChatMaxAccounts = 30;      // so 2110-2139
const ushort cmChatInvite = 2142;     // Chat > Invite...: a Delta Chat invite link for an account
const ushort cmChatAcceptInvite = 2143; // Chat > Accept invite...: answer the Delta Chat invitations that came by mail
const ushort cmChatAcctPicked = 2140; // broadcast: the chat window's server pane moved
const ushort cmChatConvPicked = 2141; // broadcast: its conversation pane moved

// ------------------------------------------------------------- chatwin.cpp ---
// The Chat menu and the 3-pane chat window.  Commands > 255 cannot be
// disabled in tvision, so the menu greys its own items (chatMenuGrey, from
// the menu bar) and every command checks again before acting.
bool chatOn();                         // tvchat-backend is up
TSubMenu &chatSubMenu();               // for initMenuBar: the items, before chat starts
void chatMenuRebuild();                // the account list and its states, afresh
void chatMenuGrey();                   // set the items' disabled flags
bool chatCommand(ushort command);      // a cmChat* command; false if not one
void openChatWindow();                 // the 3-pane window, made the first time

// --------------------------------------------------------------- help.cpp ---
TWindow *newHelpWindow(const TRect &bounds);
