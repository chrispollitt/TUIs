// compose.cpp - the compose window, and opening one from a template.
#include "tvmail.h"
#include "views.h"

// -------------------------------------------------------- compose window ----
// A real in-app composer (cf. third_party/tvision/examples/tvedit): To/Cc/
// Subject input lines over a TEditor body with a scrollbar + L:C indicator.
// F2 sends via `tvmail-backend send`; closing a modified draft asks first.

class TComposeWindow;
static TComposeWindow *gLastCompose = nullptr; // address book / menu target

class TComposeWindow : public TWindow
{
    TFieldLine *toLine = nullptr, *ccLine = nullptr, *bccLine = nullptr, *subjLine = nullptr;
    TBodyEditor *editor = nullptr;
    Boolean sent = False;
    std::string draftMbox; // set when editing an existing draft
    int draftIdx = -1;
    std::string draftRaw; // the draft as opened: finds it again if indices shift
    std::string initTo, initCc, initBcc, initSubj; // header fields as opened

    static std::string strip(std::string s)
    {
        size_t a = s.find_first_not_of(" \t\r\n");
        size_t b = s.find_last_not_of(" \t\r\n");
        return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
    }

public:
    void linkDraft(const std::string &m, int i, const std::string &raw)
    {
        draftMbox = m;
        draftIdx = i;
        draftRaw = raw;
    }

    // Unsent changes anywhere: the body, or any header field.
    bool dirty()
    {
        auto val = [](TFieldLine *l)
        { return std::string(l->data ? l->data : ""); };
        return (editor && editor->modified) || val(toLine) != initTo ||
               val(ccLine) != initCc || val(bccLine) != initBcc ||
               val(subjLine) != initSubj;
    }

    void addRecipient(const std::string &a)
    {
        std::string cur = toLine->data ? toLine->data : "";
        std::string nw = strip(cur).empty() ? a : cur + ", " + a;
        std::strncpy(toLine->data, nw.c_str(), toLine->maxLen);
        toLine->data[toLine->maxLen] = '\0';
        toLine->drawView();
        toLine->select();
    }

    std::string buildMessage()
    {
        std::string to = toLine->data ? toLine->data : "";
        std::string cc = ccLine->data ? ccLine->data : "";
        std::string bcc = bccLine->data ? bccLine->data : "";
        std::string subj = subjLine->data ? subjLine->data : "";
        // loud and proud: name the tool + version.  tvmail-backend adds the
        // origin user/host (X-Origin-User/-Host) and never overwrites this.
        std::string msg = "X-Mailer: tvmail " TVMAIL_VERSION "\n";
        msg += "To: " + to + "\n";
        if (!strip(cc).empty())
            msg += "Cc: " + cc + "\n";
        if (!strip(bcc).empty())
            msg += "Bcc: " + bcc + "\n";
        msg += "Subject: " + subj + "\n\n" + bodyText();
        if (msg.empty() || msg.back() != '\n')
            msg += '\n';
        return msg;
    }

    // Delete the draft this window was opened from.  Its index may have moved
    // since (another draft deleted or filed meanwhile), so only delete a
    // message whose raw text is still the one we opened; if none matches,
    // leave Drafts alone - a stale extra draft beats deleting the wrong one.
    void dropSourceDraft()
    {
        if (draftMbox.empty() || draftIdx < 0)
            return;
        auto rawAt = [&](int i)
        { return backendRun("raw " + std::to_string(i) + " " + draftMbox); };
        int victim = -1;
        if (!draftRaw.empty() && rawAt(draftIdx) == draftRaw)
            victim = draftIdx;
        else if (!draftRaw.empty())
        {
            std::istringstream is(backendRun("list " + draftMbox));
            std::string line;
            while (victim < 0 && std::getline(is, line))
            {
                if (line.find('\t') == std::string::npos)
                    continue;
                int i = std::atoi(line.c_str());
                if (rawAt(i) == draftRaw)
                    victim = i;
            }
        }
        draftIdx = -1;
        if (victim < 0)
            return;
        shCapture(backendCommand("delete " + std::to_string(victim) + " --mbox '" + draftMbox + "'") + " 2>&1");
        message(TProgram::application, evCommand, cmReload, nullptr);
    }

    void shutDown() override
    {
        if (gLastCompose == this)
            gLastCompose = nullptr;
        TWindow::shutDown();
    }

    TComposeWindow(const TRect &bounds,
                   const std::string &to, const std::string &cc,
                   const std::string &subj, const std::string &body,
                   const std::string &bcc = std::string())
        : TWindowInit(&TComposeWindow::initFrame),
          TWindow(bounds, "Compose", wnNoNumber)
    {
        palette = wpCyanWindow;
        options |= ofTileable;
        gLastCompose = this;
        const int W = size.x;

        auto addField = [&](int y, const char *label, const std::string &val)
        {
            auto *il = new TFieldLine(TRect(11, y, W - 2, y + 1), 900);
            // linked, so the label's Alt-letter (and a click on it) jumps here
            insert(new TLabel(TRect(2, y, 11, y + 1), label, il));
            il->growMode = gfGrowHiX;
            if (!val.empty())
            {
                std::strncpy(il->data, val.c_str(), il->maxLen);
                il->data[il->maxLen] = '\0';
            }
            insert(il);
            return il;
        };
        toLine = addField(1, "~T~o", to);
        ccLine = addField(2, "~C~c", cc);
        bccLine = addField(3, "~B~cc", bcc);
        subjLine = addField(4, "~S~ubject", subj);
        initTo = toLine->data;
        initCc = ccLine->data;
        initBcc = bccLine->data;
        initSubj = subjLine->data;

        TScrollBar *vsb = new TScrollBar(TRect(W - 2, 6, W - 1, size.y - 1));
        insert(vsb);
        TIndicator *ind = new TIndicator(TRect(2, size.y - 1, 16, size.y));
        insert(ind);
        editor = new TBodyEditor(TRect(1, 6, W - 2, size.y - 1), nullptr, vsb, ind, 64000);
        editor->growMode = gfGrowHiX | gfGrowHiY;
        insert(editor);

        if (!body.empty())
        {
            editor->insertText(body.data(), (uint)body.size(), False);
            editor->setSelect(0, 0, False);
            editor->trackCursor(False);
            editor->modified = False;
        }
        // blank compose -> cursor in To:, reply -> cursor in the body
        if (to.empty())
            toLine->select();
        else
            editor->select();
    }

    TColorAttr mapColor(uchar index) override
    {
        switch (index)
        {
        case 1:
            return cNorm();
        case 2:
            return cFrame();
        case 3:
            return cFrame();
        case 4:
            return cNorm();
        case 5:
            return cHi();
        case 6:
            return cNorm();
        case 7:
            return cHi();
        case 8:
            return cNorm();
        }
        return TView::mapColor(index);
    }

    std::string bodyText()
    {
        uint n = editor->bufLen;
        std::string s(n, '\0');
        if (n)
            editor->getText(0, TSpan<char>(&s[0], (size_t)n));
        return s;
    }

    void doSend()
    {
        if (strip(toLine->data ? toLine->data : "").empty())
        {
            messageBox("Enter at least one To: address.", mfError | mfOKButton);
            return;
        }
        std::string path = writeTemp(buildMessage(), ".eml");
        std::string out = shCapture(backendCommand("send") + " < '" + path + "' 2>&1; rm -f '" + path + "'");
        while (!out.empty() && (out.back() == '\n' || out.back() == ' '))
            out.pop_back();

        if (out == "sent" || out.empty())
        {
            sent = True;
            dropSourceDraft(); // if this was a saved draft
            messageBox("Message sent.", mfInformation | mfOKButton);
            TEvent ev; // close after this event unwinds
            ev.what = evCommand;
            ev.message.command = cmClose;
            ev.message.infoPtr = this;
            putEvent(ev);
        }
        else
        {
            messageBox(("Send failed:\n" + out).c_str(), mfError | mfOKButton);
        }
    }

    void handleEvent(TEvent &e) override
    {
        TWindow::handleEvent(e);
        if (e.what != evCommand)
            return;
        switch (e.message.command)
        {
        case cmSendMsg:
            doSend();
            clearEvent(e);
            break;
        case cmInsSig:
        {
            std::string s = "\n-- \n" + readSigFile() + "\n";
            editor->insertText(s.data(), (uint)s.size(), False);
            clearEvent(e);
            break;
        }
        case cmInsDead:
        {
            std::string s = readDeadLetter();
            editor->insertText(s.data(), (uint)s.size(), False);
            clearEvent(e);
            break;
        }
        }
    }

    Boolean valid(ushort command) override
    {
        if (!TWindow::valid(command))
            return False;
        // cmQuit too: Alt-X asks every window, and must not drop unsent mail
        if ((command == cmClose || command == cmQuit) && !sent && dirty())
        {
            if (command == cmQuit)
                select(); // show which message the question is about
            ushort r = askSaveDraft();
            if (r == cmCancel)
                return False;
            if (r == cmYes)
            {
                std::string path = writeTemp(buildMessage(), ".eml");
                std::string out = shCapture(backendCommand("save-draft") + " < '" + path + "' 2>&1; rm -f '" + path + "'");
                while (!out.empty() && (out.back() == '\n' || out.back() == ' '))
                    out.pop_back();
                if (out.size() < 11 || out.compare(out.size() - 11, 11, "draft saved") != 0)
                {
                    // keep the window (and the text) - the draft is not saved
                    messageBox(("Draft not saved:\n" + (out.empty() ? std::string("no reply from tvmail-backend") : out)).c_str(),
                               mfError | mfOKButton);
                    return False;
                }
                dropSourceDraft();
                message(TProgram::application, evCommand, cmReload, nullptr);
                // saved: if a quit is called off by another window, this
                // one stays open and must not save the same draft again
                editor->modified = False;
                initTo = toLine->data;
                initCc = ccLine->data;
                initBcc = bccLine->data;
                initSubj = subjLine->data;
            }
        }
        return True;
    }
};

static void parseTemplate(const std::string &t, std::string &to, std::string &cc,
                          std::string &subj, std::string &body, std::string &bcc)
{
    std::istringstream is(t);
    std::string line, b;
    bool inBody = false;
    auto ieq = [](const std::string &a, const char *k)
    {
        if (a.size() != std::strlen(k))
            return false;
        for (size_t i = 0; i < a.size(); ++i)
            if (std::tolower((unsigned char)a[i]) != std::tolower((unsigned char)k[i]))
                return false;
        return true;
    };
    while (std::getline(is, line))
    {
        if (!line.empty() && line.back() == '\r')
            line.pop_back();
        if (!inBody)
        {
            if (line.empty())
            {
                inBody = true;
                continue;
            }
            size_t p = line.find(':');
            if (p != std::string::npos)
            {
                std::string k = line.substr(0, p), v = line.substr(p + 1);
                while (!v.empty() && (v[0] == ' ' || v[0] == '\t'))
                    v.erase(0, 1);
                if (ieq(k, "To"))
                    to = v;
                else if (ieq(k, "Cc"))
                    cc = v;
                else if (ieq(k, "Bcc"))
                    bcc = v;
                else if (ieq(k, "Subject"))
                    subj = v;
            }
        }
        else
        {
            b += line;
            b += '\n';
        }
    }
    body = b;
}

void openCompose(const std::string &raw, const std::string &linkMbox,
                 int linkIdx, bool addSig)
{
    std::string to, cc, subj, body, bcc;
    if (!raw.empty())
        parseTemplate(raw, to, cc, subj, body, bcc);
    if (addSig)
    {
        std::string sig = readSigFile();
        if (!sig.empty())
            body += "\n-- \n" + sig + "\n";
    }
    TRect r = TProgram::deskTop->getExtent();
    r.grow(-5, -2);
    auto *w = new TComposeWindow(r, to, cc, subj, body, bcc);
    if (!linkMbox.empty())
        w->linkDraft(linkMbox, linkIdx, raw);
    TProgram::deskTop->insert(w);
}

// The address book's Enter: into the most recently opened compose window.
bool composeAddRecipient(const std::string &addr)
{
    if (!gLastCompose)
        return false;
    gLastCompose->addRecipient(addr);
    return true;
}
