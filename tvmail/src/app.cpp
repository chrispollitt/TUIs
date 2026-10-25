// app.cpp - the application: menus, status line, the commands that act on
// the current message, and the dialogs behind them (Out of office, Move,
// Purge, Empty trash, background pull).  The desktop, the palette and the
// Window list are TTvApp's (tvcommon).
#include "app.h"
#include <fstream>
#include <cstdio>
#include <unistd.h>
#include "views.h"
#include "coprocess.h"

TMenuBar *TVMailApp::initMenuBar(TRect r)
{
    r.b.y = r.a.y + 1;
    return new TMenuBar(r,
                        *new TSubMenu("~F~ile", kbAltF) +
                            *new TMenuItem(gRemote ? "File ~s~pam" : "~P~ull mail",
                                           cmPull, kbF3, hcNoContext, "F3") +
                            tvReloadItem() +
                            *new TMenuItem("Sa~v~e message as...", cmSaveMsg, kbNoKey, hcNoContext) +
                            *new TMenuItem("~O~ut of office...", cmOutOfOffice, kbNoKey, hcNoContext) +
                            *new TMenuItem("P~u~rge...", cmPurgeMsgs, kbNoKey, hcNoContext) +
                            *new TMenuItem("Empty ~t~rash...", cmEmptyTrash, kbNoKey, hcNoContext) +
                            newLine() +
                            *new TMenuItem("Edit si~g~nature", cmEditSig, kbNoKey, hcNoContext) +
                            *new TMenuItem("Resume dead.~l~etter", cmResumeDead, kbNoKey, hcNoContext) +
                            tvExitItem() +
                            *new TSubMenu("~M~essage", kbAltM) +
                            *new TMenuItem("~R~eply", cmReplyMsg, kbCtrlR, hcNoContext, "Ctrl-R") +
                            *new TMenuItem("Repl~y~ All", cmReplyAllMsg, kbNoKey, hcNoContext) +
                            *new TMenuItem("~F~orward", cmForwardMsg, kbNoKey, hcNoContext) +
                            *new TMenuItem("~N~ew message", cmCompose, kbCtrlN, hcNoContext, "Ctrl-N") +
                            *new TMenuItem("~E~dit draft", cmEditDraft, kbNoKey, hcNoContext, "Enter") +
                            *new TMenuItem("~S~end", cmSendMsg, kbF2, hcNoContext, "F2") +
                            newLine() +
                            *new TMenuItem("Insert si~g~nature", cmInsSig, kbNoKey, hcNoContext) +
                            *new TMenuItem("Insert dead.~l~etter", cmInsDead, kbNoKey, hcNoContext) +
                            newLine() +
                            *new TMenuItem("~A~ddress book", cmAddrBook, kbF4, hcNoContext, "F4") +
                            *new TMenuItem("~P~arts...", cmViewParts, kbNoKey, hcNoContext) +
                            *new TMenuItem("~V~iew source", cmViewSrc, kbNoKey, hcNoContext) +
                            newLine() +
                            *new TMenuItem("Mar~k~ read", cmMarkRead, kbNoKey, hcNoContext) +
                            *new TMenuItem("Mark ~u~nread", cmMarkUnread, kbNoKey, hcNoContext) +
                            *new TMenuItem("M~o~ve to...", cmMoveMsg, kbNoKey, hcNoContext) +
                            *new TMenuItem("~D~elete", cmDeleteMsg, kbCtrlD, hcNoContext, "Ctrl-D") +
                            newLine() +
                            *new TMenuItem("Cha~t~ with sender", cmChatWith, kbNoKey, hcNoContext) +
                            *new TMenuItem("Open chat ~i~nvite", cmChatInvite, kbNoKey, hcNoContext) +
                            *new TSubMenu("~E~dit", kbAltE) +
                            *new TMenuItem("~U~ndo", cmUndo, kbNoKey, hcNoContext) +
                            newLine() +
                            *new TMenuItem("Cu~t~", cmCut, kbShiftDel, hcNoContext, "Shift-Del") +
                            *new TMenuItem("~C~opy", cmCopy, kbCtrlIns, hcNoContext, "Ctrl-Ins") +
                            *new TMenuItem("~P~aste", cmPaste, kbShiftIns, hcNoContext, "Shift-Ins") +
                            newLine() +
                            tvFindItems(true) +
                            *new TSubMenu("~V~iew", kbAltV) +
                            *new TMenuItem("Sort by ~d~ate", cmSortDate, kbNoKey, hcNoContext) +
                            *new TMenuItem("Sort by ~f~rom", cmSortFrom, kbNoKey, hcNoContext) +
                            *new TMenuItem("Sort by ~s~ubject", cmSortSubj, kbNoKey, hcNoContext) +
                            *new TMenuItem("~U~nread first", cmSortUnread, kbNoKey, hcNoContext) +
                            *new TMenuItem("~R~everse order", cmSortReverse, kbNoKey, hcNoContext) +
                            newLine() +
                            *new TMenuItem("~W~rap long lines", cmToggleWrap, kbNoKey, hcNoContext) +
                            *new TMenuItem("~C~lear filter", cmClearFilter, kbNoKey, hcNoContext, "Esc") +
                            tvWindowMenu() +
                            tvHelpMenu());
}

TStatusLine *TVMailApp::initStatusLine(TRect r)
{
    r.a.y = r.b.y - 1;
    return new TStatusLine(r,
                           tvStatusDef(*new TStatusDef(0, 0xFFFF) +
                                       *new TStatusItem(gRemote ? "~F3~ Spam" : "~F3~ Pull", kbF3, cmPull) +
                                       *new TStatusItem("~^R~ Reply", kbCtrlR, cmReplyMsg) +
                                       *new TStatusItem("~^N~ New", kbCtrlN, cmCompose) +
                                       *new TStatusItem("~F2~ Send", kbF2, cmSendMsg) +
                                       *new TStatusItem("~^D~ Del", kbCtrlD, cmDeleteMsg)));
}

int TVMailApp::currentRow()
{
    return mailFocusedRow();
}

void TVMailApp::viewSource(int row)
{
    if (row < 0)
        return;
    int b = gRows[row].idx;
    auto lines = splitLines(backendRun("raw " + std::to_string(b) + " " + gMbox));
    TRect r = deskTop->getExtent();
    r.grow(-3, -1);
    std::string title = "Source of msg " + std::to_string(b);
    deskTop->insert(new TMailViewWindow(r, title.c_str(), std::move(lines)));
}

void TVMailApp::viewParts(int row)
{
    if (row < 0)
        return;
    int b = gRows[row].idx;
    TRect r = deskTop->getExtent();
    r.grow(-8, -4);
    std::string title = "Parts of msg " + std::to_string(b);
    deskTop->insert(newPartsWindow(r, b, gMbox, title.c_str()));
}

void TVMailApp::replyOrCompose(int row, bool all)
{
    if (row >= 0)
    {
        std::string cmd = "compose-template --in-reply-to " + std::to_string(gRows[row].idx) + " " + gMbox;
        if (all)
            cmd += " --reply-all";
        openCompose(backendRun(cmd), "", -1, /*addSig=*/true);
    }
    else
    {
        openCompose("", "", -1, /*addSig=*/true);
    }
}

void TVMailApp::forwardMsg(int row)
{
    if (row < 0)
        return;
    std::string t = backendRun("compose-template --forward " + std::to_string(gRows[row].idx) + " " + gMbox);
    openCompose(t, "", -1, /*addSig=*/true);
}

void TVMailApp::editDraft(int row)
{
    if (row < 0 || gMbox != "drafts")
        return;
    int idx = gRows[row].idx;
    std::string raw = backendRun("raw " + std::to_string(idx) + " " + gMbox);
    openCompose(raw, "drafts", idx, /*addSig=*/false);
}

void TVMailApp::resumeDeadLetter()
{
    std::string raw = readDeadLetter();
    if (raw.empty())
    {
        messageBox("~/dead.letter is empty.", mfInformation | mfOKButton);
        return;
    }
    openCompose(raw, "", -1, /*addSig=*/false);
}

void TVMailApp::deleteMsg(int row)
{
    if (row < 0)
        return;
    int b = gRows[row].idx;
    // name the message by its subject (the backend index means nothing to
    // anyone), and say so when this delete is for good
    std::string subj = gRows[row].subj.empty() ? "(no subject)" : gRows[row].subj;
    subj = fitCols(subj, 34);
    while (!subj.empty() && subj.back() == ' ')
        subj.pop_back();
    bool forGood = gMbox == "trash";
    if (messageBox(mfConfirmation | mfYesButton | mfNoButton,
                   forGood ? "Permanently delete \"%s\"?\n\nThis cannot be undone."
                           : "Move \"%s\" to the trash?",
                   subj.c_str()) != cmYes)
        return;
    std::string cmd = backendCommand("delete " + std::to_string(b) + mboxOpt());
    if (gMbox != "trash")
        cmd += " --trash trash"; // move to trash, don't destroy
    std::string out = shCapture(cmd + " 2>&1");
    reload();
    if (!out.empty() && out.rfind("deleted", 0) != 0)
        messageBox(out.c_str(), mfInformation | mfOKButton);
}

// Mark read / unread.  The window does the work (backend call, flag, folder
// counts); a message only becomes read by itself once the body pane takes
// focus, so an unread mark made here stays put.
void TVMailApp::markUnread(int row)
{
    if (row >= 0)
        mailMarkFocused(false);
}

void TVMailApp::markRead(int row)
{
    if (row >= 0)
        mailMarkFocused(true);
}

// ---- the optional link to tvchat -------------------------------------------
// tvmail and tvchat are separate programs and this is all they share: a one-line
// request file in a directory that tvchat's backend looks at every couple of
// seconds, whether or not tvchat was running when it was written:
//   chat ADDRESS     open the chat with ADDRESS (or invite them by mail)
//   invite LINK      offer a Delta Chat invitation found in a mail
// Nothing here needs tvchat: without it the two menu items say so.
namespace
{
std::string chatRequestDir()
{
    if (const char *d = getenv("TVCHAT_REQUESTS"))
        if (*d)
            return d;
    std::string base;
    if (const char *x = getenv("XDG_STATE_HOME"))
        base = x;
    if (base.empty())
        if (const char *h = getenv("HOME"))
            base = std::string(h) + "/.local/state";
    return base + "/tvchat/requests";
}

std::string stripped(std::string s)
{
    while (!s.empty() && isspace((unsigned char)s.back()))
        s.pop_back();
    size_t i = 0;
    while (i < s.size() && isspace((unsigned char)s[i]))
        ++i;
    return s.substr(i);
}

bool tvchatInstalled()
{
    return !stripped(shCapture("command -v tvchat-backend || ls /usr/local/bin/tvchat-backend "
                               "\"$HOME/bin/tvchat-backend\" \"$HOME/.local/bin/tvchat-backend\" 2>/dev/null | head -1"))
                .empty();
}

// One request, written whole (a temp name, then renamed) so tvchat never reads half of it.
bool writeChatRequest(const std::string &line, std::string &err)
{
    std::string dir = chatRequestDir();
    std::string out = stripped(shCapture("mkdir -p " + shq(dir) + " 2>&1"));
    if (!out.empty())
    {
        err = out;
        return false;
    }
    static int n = 0;
    std::string base = dir + "/" + std::to_string((long)time(nullptr)) + "-" + std::to_string((long)getpid()) +
                       "-" + std::to_string(++n);
    {
        std::ofstream f(base + ".tmp");
        f << line << "\n";
        if (!f)
        {
            err = "cannot write " + base + ".tmp";
            return false;
        }
    }
    if (rename((base + ".tmp").c_str(), (base + ".req").c_str()) != 0)
    {
        err = "cannot rename the request file in " + dir;
        return false;
    }
    return true;
}

// "Name <a@b.c>", "a@b.c" -> a@b.c
std::string addressOf(const std::string &from)
{
    size_t lt = from.find('<'), gt = from.find('>', lt == std::string::npos ? 0 : lt);
    std::string a = (lt != std::string::npos && gt != std::string::npos && gt > lt) ? from.substr(lt + 1, gt - lt - 1)
                                                                                    : from;
    a = stripped(a);
    return a.find('@') == std::string::npos || a.find(' ') != std::string::npos ? std::string() : a;
}

// the address in the first From: header of a raw message
std::string senderOf(const std::string &raw)
{
    size_t end = raw.find("\n\n");
    std::string head = raw.substr(0, end == std::string::npos ? raw.size() : end);
    size_t at = head.rfind("\nFrom:") == std::string::npos ? std::string::npos : head.find("\nFrom:");
    if (at == std::string::npos && head.rfind("From:", 0) == 0)
        at = 0;
    else if (at != std::string::npos)
        at += 1;
    if (at == std::string::npos)
        return std::string();
    size_t eol = head.find('\n', at);
    std::string line = head.substr(at + 5, eol == std::string::npos ? std::string::npos : eol - at - 5);
    // a folded header carries on in the next lines
    while (eol != std::string::npos && eol + 1 < head.size() && (head[eol + 1] == ' ' || head[eol + 1] == '\t'))
    {
        size_t e2 = head.find('\n', eol + 1);
        line += head.substr(eol + 1, e2 == std::string::npos ? std::string::npos : e2 - eol - 1);
        eol = e2;
    }
    return addressOf(line);
}

// the first Delta Chat invite link in a raw message (quoted-printable soft breaks and =3D undone first)
std::string inviteLinkIn(std::string raw)
{
    std::string low = raw;
    for (char &ch : low)
        ch = (char)tolower((unsigned char)ch);
    if (low.find("content-transfer-encoding: quoted-printable") != std::string::npos)
    {
        // soft line breaks and =3D, but only here: in plain text a line may end in "="
        for (size_t p; (p = raw.find("=\r\n")) != std::string::npos;)
            raw.erase(p, 3);
        for (size_t p; (p = raw.find("=\n")) != std::string::npos;)
            raw.erase(p, 2);
        for (size_t p = 0; (p = raw.find("=3D", p)) != std::string::npos;)
            raw.replace(p, 3, "=");
    }
    size_t at = std::string::npos;
    for (const char *key : {"https://i.delta.chat/#", "OPENPGP4FPR:"})
    {
        size_t k = raw.find(key);
        if (k != std::string::npos && (at == std::string::npos || k < at))
            at = k;
    }
    if (at == std::string::npos)
        return std::string();
    size_t e = at;
    while (e < raw.size() && !isspace((unsigned char)raw[e]) && raw[e] != '<' && raw[e] != '>' && raw[e] != '"')
        ++e;
    return raw.substr(at, e - at);
}

bool needTvchat()
{
    if (tvchatInstalled())
        return true;
    messageBox("tvchat is not installed here.\n\nThis only hands the request to it; install tvchat\n"
               "(and a Delta Chat account) to use it.",
               mfInformation | mfOKButton);
    return false;
}
} // namespace

// Message > Chat with sender: tvchat opens the chat with whoever wrote this
// (a Delta Chat account is used; with no key for them yet it mails them an invitation).
void TVMailApp::chatWithSender(int row)
{
    if (row < 0 || !needTvchat())
        return;
    std::string addr = senderOf(shCapture(backendCommand("raw " + std::to_string(gRows[row].idx) + " " + gMbox)));
    if (addr.empty())
    {
        messageBox("This message has no sender address to chat with.", mfInformation | mfOKButton);
        return;
    }
    std::string err;
    if (!writeChatRequest("chat " + addr, err))
        messageBox(err.c_str(), mfError | mfOKButton);
    else
        messageBox(("Asked tvchat to chat with " + addr + ".\n\nIt picks that up within a couple of seconds\n"
                    "if it is running, or when you next start it.")
                       .c_str(),
                   mfInformation | mfOKButton);
}

// Message > Open chat invite: this message holds a Delta Chat invite link;
// tvchat offers it (Accept? Yes / No) - tvmail does not have to be told what to do next.
void TVMailApp::openChatInvite(int row)
{
    if (row < 0 || !needTvchat())
        return;
    std::string link = inviteLinkIn(shCapture(backendCommand("raw " + std::to_string(gRows[row].idx) + " " + gMbox)));
    if (link.empty())
    {
        messageBox("This message holds no Delta Chat invite link.", mfInformation | mfOKButton);
        return;
    }
    std::string err;
    if (!writeChatRequest("invite " + link, err))
        messageBox(err.c_str(), mfError | mfOKButton);
    else
        messageBox("Handed the invitation to tvchat.\n\nIt asks whether to accept - in tvchat, if it is running,\n"
                   "or when you next start it.",
                   mfInformation | mfOKButton);
}

// File > Save message as...: the raw RFC822 message (headers, attachments and
// all) to a file - an .eml that any mail program can open.
void TVMailApp::saveMsg(int row)
{
    if (row < 0)
        return;
    int b = gRows[row].idx;
    std::string dest = askSavePath("message-" + std::to_string(b) + ".eml", "Save message as");
    if (dest.empty())
        return;
    std::string out = shCapture(backendCommand("raw " + std::to_string(b) + " " + gMbox) +
                                " > " + shq(dest) + " 2>&1");
    while (!out.empty() && (out.back() == '\n' || out.back() == ' '))
        out.pop_back();
    if (out.empty())
        messageBox(("Saved to " + dest).c_str(), mfInformation | mfOKButton);
    else
        messageBox(out.c_str(), mfError | mfOKButton);
}

// File > Out of office...: an auto-reply has to run where the mail arrives, so
// this edits a Sieve vacation script on the master over ManageSieve (via
// tvmail-backend vacation).  Nothing here keeps running after tvmail exits.
void TVMailApp::outOfOffice()
{
    // --- what is there now -----------------------------------------------
    std::string st = backendRun("vacation status");
    if (st.rfind("state: ", 0) != 0)
    {
        while (!st.empty() && (st.back() == '\n' || st.back() == ' '))
            st.pop_back();
        messageBox(st.empty() ? "Could not read the out-of-office settings\n(is tvmail-backend up to date?)."
                              : st.c_str(),
                   mfError | mfOKButton);
        return;
    }
    std::string state, otherActive, subject = "Out of office", days = "7", start, end, addresses;
    std::string message = "Thank you for your message.\n"
                          "I am out of the office and will reply when I am back.\n";
    {
        std::vector<std::string> ls = splitLines(st);
        size_t i = 0;
        bool haveScript = false;
        for (; i < ls.size() && !ls[i].empty(); ++i)
        {
            size_t c = ls[i].find(": ");
            if (c == std::string::npos)
                continue;
            std::string k = ls[i].substr(0, c), v = ls[i].substr(c + 2);
            if (k == "state")
                state = v;
            else if (k == "other_active")
                otherActive = v;
            else if (k == "subject" && !v.empty())
                subject = v, haveScript = true;
            else if (k == "days" && !v.empty())
                days = v;
            else if (k == "start")
                start = v;
            else if (k == "end")
                end = v;
            else if (k == "addresses")
                addresses = v;
        }
        if (haveScript)
        {
            std::string m;
            for (++i; i < ls.size(); ++i)
                m += ls[i] + "\n";
            while (m.size() > 1 && m[m.size() - 1] == '\n' && m[m.size() - 2] == '\n')
                m.pop_back();
            if (!m.empty() && m != "\n")
                message = m;
        }
    }

    // --- the dialog ------------------------------------------------------
    // Loops until saved or cancelled: a rejected change (or a No to switching
    // off another Sieve script) brings the dialog back with what was typed.
    auto trim = [](std::string s)
    {
        size_t a = s.find_first_not_of(" \t");
        size_t b = s.find_last_not_of(" \t");
        return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
    };
    ushort on = (state == "on") ? 1 : 0;
    bool force = false; // the user already agreed to replace otherActive
    for (;;)
    {
        const ushort kMemoMax = 4096;
        TDialog *d = new TDialog(TRect(0, 0, 70, 24), "Out of office");
        d->options |= ofCentered;

        TCheckBoxes *onCb = new TCheckBoxes(TRect(3, 2, 60, 3), new TSItem("Automatic reply is ~O~N", 0));
        d->insert(onCb);

        TInputLine *subjIl = new TInputLine(TRect(14, 4, 67, 5), 120);
        d->insert(subjIl);
        d->insert(new TLabel(TRect(2, 4, 14, 5), "Su~b~ject", subjIl));

        TInputLine *fromIl = new TInputLine(TRect(14, 6, 26, 7), 11);
        d->insert(fromIl);
        d->insert(new TLabel(TRect(2, 6, 14, 7), "~F~rom date", fromIl));
        d->insert(new TStaticText(TRect(28, 6, 68, 7), "YYYY-MM-DD; blank = from now on"));

        TInputLine *toIl = new TInputLine(TRect(14, 7, 26, 8), 11);
        d->insert(toIl);
        d->insert(new TLabel(TRect(2, 7, 14, 8), "~T~o date", toIl));
        d->insert(new TStaticText(TRect(28, 7, 68, 8), "last day; blank = until switched off"));

        // The server only answers mail addressed to one of these.  Mail pulled in
        // from a POP account is addressed to *that* account, not to this one.
        TInputLine *addrIl = new TInputLine(TRect(14, 9, 67, 10), 160);
        d->insert(addrIl);
        d->insert(new TLabel(TRect(2, 9, 14, 10), "~A~ddresses", addrIl));
        d->insert(new TStaticText(TRect(14, 10, 68, 11), "public + local (comma separated); the server answers mail sent to any"));

        TInputLine *daysIl = new TInputLine(TRect(43, 12, 49, 13), 4);
        d->insert(daysIl);
        d->insert(new TLabel(TRect(2, 12, 43, 13), "~R~eply to each sender at most every", daysIl));
        d->insert(new TStaticText(TRect(50, 12, 56, 13), "days"));

        TScrollBar *vsb = new TScrollBar(TRect(65, 15, 66, 20));
        d->insert(vsb);
        TMemo *memo = new TMemo(TRect(3, 15, 65, 20), nullptr, vsb, nullptr, kMemoMax);
        d->insert(memo);
        d->insert(new TLabel(TRect(2, 14, 20, 15), "~M~essage", memo));

        d->insert(new TButton(TRect(20, 21, 32, 23), "~S~ave", cmOK, bfDefault));
        d->insert(new TButton(TRect(36, 21, 48, 23), "Cancel", cmCancel, bfNormal));

        onCb->setData(&on);
        // TInputLine::setData copies its full buffer size, so hand it one
        auto fill = [](TInputLine *il, const std::string &s, size_t maxLen)
        {
            std::vector<char> b(maxLen + 1, 0);
            std::memcpy(b.data(), s.data(), std::min(s.size(), maxLen));
            il->setData(b.data());
        };
        fill(subjIl, subject, 120);
        fill(fromIl, start, 11);
        fill(toIl, end, 11);
        fill(addrIl, addresses, 160);
        fill(daysIl, days, 4);
        {
            std::vector<char> md(sizeof(ushort) + kMemoMax);
            ushort len = (ushort)std::min<size_t>(message.size(), kMemoMax - 1);
            std::memcpy(md.data(), &len, sizeof len);
            std::memcpy(md.data() + sizeof len, message.data(), len);
            memo->setData(md.data());
        }
        d->selectNext(False);

        ushort r = TProgram::deskTop->execView(d);
        if (r == cmCancel)
        {
            TObject::destroy(d);
            return;
        }
        onCb->getData(&on);
        char sbuf[121], fbuf[12], tbuf[12], dbuf[5], abuf[161];
        addrIl->getData(abuf);
        subjIl->getData(sbuf);
        fromIl->getData(fbuf);
        toIl->getData(tbuf);
        daysIl->getData(dbuf);
        std::vector<char> md(sizeof(ushort) + kMemoMax);
        memo->getData(md.data());
        ushort mlen = 0;
        std::memcpy(&mlen, md.data(), sizeof mlen);
        std::string text(md.data() + sizeof mlen, std::min<size_t>(mlen, kMemoMax));
        TObject::destroy(d);

        // keep what was typed, for a second round if this one fails
        subject = trim(sbuf);
        start = trim(fbuf);
        end = trim(tbuf);
        addresses = trim(abuf);
        days = trim(dbuf);
        // the editor may use CR or CRLF line ends; the backend wants plain LF
        message.clear();
        for (size_t i = 0; i < text.size(); ++i)
        {
            if (text[i] == '\r')
            {
                message += '\n';
                if (i + 1 < text.size() && text[i + 1] == '\n')
                    ++i;
            }
            else
                message += text[i];
        }

        std::string out;
        bool ok;
        if (!(on & 1))
        {
            out = backendRun("vacation off");
            ok = out.rfind("off", 0) == 0;
        }
        else
        {
            if (!otherActive.empty() && !force)
            {
                std::string q = "The Sieve script '" + otherActive + "' is active on the server.\n"
                                "Only one script can be, so switching on the out-of-office\n"
                                "reply will switch that one off.\n\nContinue?";
                if (messageBox(mfConfirmation | mfYesNoCancel, "%s", q.c_str()) != cmYes)
                    continue; // back to the dialog, entries intact
                force = true;
            }
            std::string f = writeTemp(message, ".txt");
            std::string args = "vacation on --message-file " + shq(f) + " --subject " + shq(subject) +
                               " --days " + shq(days.empty() ? "7" : days) +
                               " --addresses " + shq(addresses);
            if (!start.empty())
                args += " --start " + shq(start);
            if (!end.empty())
                args += " --end " + shq(end);
            if (force)
                args += " --force";
            out = backendRun(args);
            ::remove(f.c_str());
            ok = out.rfind("on", 0) == 0;
        }
        while (!out.empty() && (out.back() == '\n' || out.back() == ' '))
            out.pop_back();
        if (ok)
        {
            messageBox((on & 1) ? "Out-of-office reply is ON.\n\nThe server answers for you from now on, even with tvmail closed."
                                : "Out-of-office reply is OFF.",
                       mfInformation | mfOKButton);
            return;
        }
        messageBox(out.empty() ? "The server did not accept the change." : out.c_str(),
                   mfError | mfOKButton);
        // and round again, with the entries as typed
    }
}

void TVMailApp::moveMsg(int row)
{
    if (row < 0)
        return;
    int b = gRows[row].idx;

    std::vector<int> dest;
    for (int i = 0; i < gFolderCount; ++i)
        if (i != gFolderIdx && std::strcmp(gFolders[i].mbox, "dead") != 0)
            dest.push_back(i);
    if (dest.empty())
        return;

    TSItem *items = nullptr;
    for (auto it = dest.rbegin(); it != dest.rend(); ++it)
        items = new TSItem(gFolders[*it].name, items);

    int n = (int)dest.size();
    TDialog *d = new TDialog(TRect(0, 0, 30, 5 + n), "Move to folder");
    d->options |= ofCentered;
    TRadioButtons *rb = new TRadioButtons(TRect(3, 2, 27, 2 + n), items);
    d->insert(rb);
    d->insert(new TButton(TRect(4, 3 + n, 14, 5 + n), "O~K~", cmOK, bfDefault));
    d->insert(new TButton(TRect(16, 3 + n, 26, 5 + n), "Cancel", cmCancel, bfNormal));
    d->selectNext(False);

    ushort r = TProgram::deskTop->execView(d);
    ushort sel = 0;
    if (r != cmCancel)
        rb->getData(&sel);
    TObject::destroy(d);
    if (r == cmCancel || sel >= dest.size())
        return;

    std::string cmd = backendCommand("delete " + std::to_string(b) + mboxOpt() + " --trash " + shq(gFolders[dest[sel]].mbox));
    std::string out = shCapture(cmd + " 2>&1");
    reload();
    if (!out.empty() && out.rfind("deleted", 0) != 0)
        messageBox(out.c_str(), mfInformation | mfOKButton);
}

void TVMailApp::purgeDialog()
{
    TDialog *d = new TDialog(TRect(0, 0, 56, 21), "Purge");
    d->options |= ofCentered;

    TInputLine *fromIl = new TInputLine(TRect(20, 2, 53, 3), 200);
    d->insert(fromIl);
    d->insert(new TLabel(TRect(2, 2, 20, 3), "~F~rom contains", fromIl));

    TInputLine *subjIl = new TInputLine(TRect(20, 3, 53, 4), 200);
    d->insert(subjIl);
    d->insert(new TLabel(TRect(2, 3, 20, 4), "~S~ubject contains", subjIl));

    TInputLine *toIl = new TInputLine(TRect(20, 4, 53, 5), 200);
    d->insert(toIl);
    d->insert(new TLabel(TRect(2, 4, 20, 5), "~T~o contains", toIl));

    TInputLine *daysIl = new TInputLine(TRect(20, 5, 30, 6), 8);
    d->insert(daysIl);
    d->insert(new TLabel(TRect(2, 5, 20, 6), "~O~lder than (days)", daysIl));

    d->insert(new TStaticText(TRect(2, 7, 30, 8), "Read status"));
    TRadioButtons *seenRb = new TRadioButtons(TRect(2, 8, 26, 11),
                                              new TSItem("~E~ither",
                                                         new TSItem("Se~e~n only",
                                                                    new TSItem("~U~nseen only", 0))));
    d->insert(seenRb);

    TCheckBoxes *expungeCb = new TCheckBoxes(TRect(2, 12, 53, 13),
                                             new TSItem("E~x~punge (delete outright, don't move to trash)", 0));
    d->insert(expungeCb);

    d->insert(new TButton(TRect(16, 16, 28, 18), "Pre~v~iew", cmOK, bfDefault));
    d->insert(new TButton(TRect(30, 16, 40, 18), "Cancel", cmCancel, bfNormal));
    d->selectNext(False);

    ushort r = TProgram::deskTop->execView(d);
    if (r == cmCancel)
    {
        TObject::destroy(d);
        return;
    }

    std::string from = fromIl->data ? fromIl->data : "";
    std::string subj = subjIl->data ? subjIl->data : "";
    std::string to = toIl->data ? toIl->data : "";
    std::string days = daysIl->data ? daysIl->data : "";
    ushort seenSel = 0;
    seenRb->getData(&seenSel);
    ushort expunge = 0;
    expungeCb->getData(&expunge);
    TObject::destroy(d);

    auto trimmed = [](std::string s)
    {
        size_t a = s.find_first_not_of(" \t");
        size_t b = s.find_last_not_of(" \t");
        return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
    };
    from = trimmed(from);
    subj = trimmed(subj);
    to = trimmed(to);
    days = trimmed(days);

    if (from.empty() && subj.empty() && to.empty() && days.empty() && seenSel == 0)
    {
        messageBox("Enter at least one filter.", mfError | mfOKButton);
        return;
    }

    std::string filt;
    if (!from.empty())
        filt += " --from " + shq(from);
    if (!subj.empty())
        filt += " --subject " + shq(subj);
    if (!to.empty())
        filt += " --to " + shq(to);
    if (!days.empty())
        filt += " --older-than " + shq(days);
    if (seenSel == 1)
        filt += " --seen";
    if (seenSel == 2)
        filt += " --unseen";
    if (expunge & 1)
        filt += " --expunge";

    std::string cmd = backendCommand("purge" + mboxArg() + filt);
    std::string preview = shCapture(cmd + " -n 2>&1");
    TRect pr = deskTop->getExtent();
    pr.grow(-6, -3);
    deskTop->insert(new TMailViewWindow(pr, "Purge preview", splitLines(preview), /*wrap=*/true));

    int n = -1;
    {
        std::istringstream is(preview);
        std::string ln;
        while (std::getline(is, ln))
            if (ln.rfind("would purge ", 0) == 0)
            {
                n = std::atoi(ln.c_str() + 12);
                break;
            }
    }
    if (n <= 0)
    {
        messageBox(n == 0 ? "Nothing matches that filter." : preview.c_str(),
                   (n == 0 ? mfInformation : mfError) | mfOKButton);
        return;
    }
    if (messageBox(mfConfirmation | mfYesNoCancel, "Purge %d message(s)?", n) != cmYes)
        return;
    std::string out = shCapture(cmd + " 2>&1");
    reload();
    if (!out.empty() && out.rfind("purged", 0) != 0)
        messageBox(out.c_str(), mfInformation | mfOKButton);
}

void TVMailApp::emptyTrash()
{
    std::string cmd = backendCommand("empty-trash");
    std::string preview = shCapture(cmd + " -n 2>&1");

    int n = -1;
    {
        std::istringstream is(preview);
        std::string ln;
        while (std::getline(is, ln))
            if (ln.rfind("would purge ", 0) == 0)
            {
                n = std::atoi(ln.c_str() + 12);
                break;
            }
    }
    if (n < 0)
    {
        messageBox(preview.c_str(), mfError | mfOKButton);
        return;
    }
    if (n == 0)
    {
        messageBox("The trash is already empty.", mfInformation | mfOKButton);
        return;
    }
    if (messageBox(mfConfirmation | mfYesNoCancel,
                   "Permanently delete %d message(s) from the trash?\n\nThis cannot be undone.", n) != cmYes)
        return;
    std::string out = shCapture(cmd + " 2>&1");
    reload();
    if (!out.empty() && out.rfind("purged", 0) != 0)
        messageBox(out.c_str(), mfInformation | mfOKButton);
}

void TVMailApp::pullMail()
{
#ifndef _WIN32
    if (pullPid > 0)
        return; // one at a time
    std::string log = tempDir() + "tvmail_pull_" + std::to_string(procId()) + ".log";
    pid_t p = mailBackend().spawnLogged("pull", log);
    if (p > 0)
    {
        pullPid = p;
        pullLogPath = log;
        TCommandSet cs;
        cs.enableCmd(cmPull);
        disableCommands(cs); // grey "F3 Pull" until it's done
        return;
    }
#endif
    shInteractive(backendCommand("pull")); // fork failed / Windows: old way
    reload();
}

#ifndef _WIN32
// putEvent() holds one event, so idle() posts at most one per call; anything
// else that is due goes on the next idle round, a moment later.
void TVMailApp::post(ushort command)
{
    TEvent ev;
    ev.what = evCommand;
    ev.message.command = command;
    ev.message.infoPtr = nullptr;
    putEvent(ev);
}

void TVMailApp::idle()
{
    TApplication::idle();
    // Nothing is posted while a menu or a dialog is up (bug #44, found
    // when chat was part of tvmail): any command ends a tvision menu, and a
    // dialog swallows what it does not know.  A finished pull is reaped
    // once the dialog is gone.
    if (TopView() != this)
        return;
    auto now = std::chrono::steady_clock::now();
    for (IdleTick &t : ticks)
    {
        int ms = t.intervalMs();
        if (ms > 0 && now - t.last >= std::chrono::milliseconds(ms))
        {
            t.last = now;
            post(t.command);
            return;
        }
    }
    if (pullPid <= 0)
        return;
    int st = 0;
    pid_t r = ::waitpid(pullPid, &st, WNOHANG);
    if (r == 0)
        return; // still pulling
    pullExit = (r == pullPid && WIFEXITED(st)) ? WEXITSTATUS(st) : -1;
    pullPid = -1;
    post(cmPullDone); // finish outside idle()
}
#endif

void TVMailApp::pullFinished()
{
#ifndef _WIN32
    TCommandSet cs;
    cs.enableCmd(cmPull);
    enableCommands(cs);

    std::string out = slurp(pullLogPath);
    if (!pullLogPath.empty())
        ::remove(pullLogPath.c_str());
    pullLogPath.clear();
    reload();

    // the puller's last line is the summary; show the tail so verbose
    // "delivered msg ..." lines are visible too.
    std::vector<std::string> ls = splitLines(out);
    std::string tail;
    for (int i = (int)ls.size() - 1, shown = 0; i >= 0 && shown < 8; --i)
    {
        if (ls[i].empty())
            continue;
        tail = ls[i] + (tail.empty() ? std::string() : "\n" + tail);
        ++shown;
    }
    if (tail.empty())
        tail = "Pull finished (no output).";
    messageBox(tail.c_str(),
               (pullExit >= 2 ? mfError : mfInformation) | mfOKButton);
#endif
}

void TVMailApp::reload()
{
    mailReload();
}

void TVMailApp::handleEvent(TEvent &e)
{
    TTvApp::handleEvent(e); // the flash timer, Window > List
    if (e.what == evNothing)
        return;
    if (e.what != evCommand)
        return;
    bool handled = true;
    switch (e.message.command)
    {
    case cmPull:
        pullMail();
        break;
    case cmPullDone:
        pullFinished();
        break;
    case cmReload:
        reload();
        break;
    case cmReplyMsg:
        replyOrCompose(currentRow());
        break;
    case cmReplyAllMsg:
        replyOrCompose(currentRow(), true);
        break;
    case cmForwardMsg:
        forwardMsg(currentRow());
        break;
    case cmCompose:
        replyOrCompose(-1);
        break;
    case cmDeleteMsg:
        deleteMsg(currentRow());
        break;
    case cmMarkUnread:
        markUnread(currentRow());
        break;
    case cmMarkRead:
        markRead(currentRow());
        break;
    case cmSaveMsg:
        saveMsg(currentRow());
        break;
    case cmChatWith:
        chatWithSender(currentRow());
        break;
    case cmChatInvite:
        openChatInvite(currentRow());
        break;
    case cmOutOfOffice:
        outOfOffice();
        break;
    case cmSortDate:
    case cmSortFrom:
    case cmSortSubj:
    case cmSortUnread:
    {
        SortKey k = e.message.command == cmSortDate   ? sortDate
                    : e.message.command == cmSortFrom ? sortFrom
                    : e.message.command == cmSortSubj ? sortSubj
                                                      : sortUnread;
        gSortRev = (k == gSortKey) ? !gSortRev : false; // again = flip
        gSortKey = k;
        mailViewChanged(false);
        break;
    }
    case cmSortReverse:
        gSortRev = !gSortRev;
        mailViewChanged(false);
        break;
    case cmToggleWrap: // no text window has focus: it is the body pane's setting
        gWrap = !gWrap;
        mailSetWrap(gWrap);
        break;
    case cmClearFilter:
        gFilter.clear();
        mailViewChanged(true);
        break;
    case cmMoveMsg:
        moveMsg(currentRow());
        break;
    case cmViewParts:
        viewParts(currentRow());
        break;
    case cmViewSrc:
        viewSource(currentRow());
        break;
    case cmEditDraft:
        editDraft(currentRow());
        break;
    case cmResumeDead:
        resumeDeadLetter();
        break;
    case cmPurgeMsgs:
        purgeDialog();
        break;
    case cmEmptyTrash:
        emptyTrash();
        break;
    case cmAddrBook:
    {
        TRect r = deskTop->getExtent();
        r.grow(-8, -4);
        deskTop->insert(newAddrBookWindow(r));
        break;
    }
    case cmEditSig:
    {
        TRect r = deskTop->getExtent();
        r.grow(-6, -3);
        deskTop->insert(newSigWindow(r));
        break;
    }
    case cmShowHelp:
    {
        TRect r = deskTop->getExtent();
        r.grow(-4, -2);
        deskTop->insert(newHelpWindow(r));
        break;
    }
    case cmAboutBox:
        tvAboutBox(deskTop, "tvmail", TVMAIL_VERSION,
                   "A Turbo Vision mail client - a local mbox\non the mail host, IMAP / SMTP anywhere else.",
                   "Plumbing: Postfix + tvmail-backend + pop-pull.");
        break;
    default:
        handled = false;
    }
    if (handled)
        clearEvent(e);
}
