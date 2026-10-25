// util.cpp - text and system helpers: shell quoting, files, UTF-8 column
// arithmetic, the status-line flash and the desktop clipboard.
#include "tvcommon.h"

// Single-quote a value for /bin/sh.  Every other string interpolated into a
// shCapture() command is program-controlled (folder shorthands, indices);
// this is for the first free-form, user-typed values that reach one (purge
// filters, an attachment's save-as path) - naive quoting breaks on a plain
// apostrophe in the text.
std::string shq(const std::string &s)
{
    std::string r = "'";
    for (char c : s)
        r += (c == '\'') ? "'\\''" : std::string(1, c);
    return r + "'";
}

std::string slurp(const std::string &path)
{
    std::string s;
    if (FILE *f = fopen(path.c_str(), "rb"))
    {
        char buf[8192];
        size_t n;
        while ((n = fread(buf, 1, sizeof buf, f)) > 0)
            s.append(buf, n);
        fclose(f);
    }
    return s;
}
std::string homePath(const char *rel)
{
    const char *h = getenv("HOME");
    return std::string(h ? h : ".") + "/" + rel;
}

std::vector<std::string> splitLines(const std::string &s)
{
    std::vector<std::string> v;
    std::istringstream is(s);
    std::string ln;
    while (std::getline(is, ln))
    {
        if (!ln.empty() && ln.back() == '\r')
            ln.pop_back();
        v.push_back(ln);
    }
    if (v.empty())
        v.push_back(std::string());
    return v;
}


std::string lowered(std::string s)
{
    for (char &c : s)
        c = (char)std::tolower((unsigned char)c);
    return s;
}

// ------------------------------------------------------ text by columns ----
// Mail text is UTF-8, so a byte count is not a screen width: these measure
// and cut by display columns (TText), keeping accented / wide characters
// whole and the list columns lined up.

// `s` cut or space-padded to exactly `w` columns
std::string fitCols(const std::string &s, int w)
{
    size_t len = 0, width = 0;
    TText::scroll(s, w, False, len, width);
    std::string r = s.substr(0, len);
    if ((int)width < w)
        r.append(w - width, ' ');
    return r;
}

// Copy into a getText() buffer without splitting a UTF-8 character.
void putText(char *dest, const std::string &s, short maxLen)
{
    size_t n = std::min(s.size(), (size_t)std::max<short>(maxLen, 0));
    if (n < s.size())
        while (n > 0 && ((uchar)s[n] & 0xC0) == 0x80)
            --n;
    std::memcpy(dest, s.data(), n);
    dest[n] = 0;
}

// Tabs to spaces, stops every 8 columns (counted in columns, not bytes).
std::string expandTabs(const std::string &s)
{
    if (s.find('\t') == std::string::npos)
        return s;
    std::string r;
    size_t col = 0, i = 0;
    while (i < s.size())
    {
        if (s[i] == '\t')
        {
            size_t n = 8 - col % 8;
            r.append(n, ' ');
            col += n;
            ++i;
            continue;
        }
        size_t j = i;
        if (!TText::next(s, j, col))
            break;
        r.append(s, i, j - i);
        i = j;
    }
    return r;
}

// A short note on the status line ("Copied 12 lines"), wiped by a timer.
TTimerId gFlashTimer = nullptr;
void flashStatus(const std::string &msg)
{
    if (!TProgram::statusLine || !TProgram::application)
        return;
    notifyRecordFlash(msg);
    TColorAttr c = vga(0x1F);
    TDrawBuffer b;
    short w = TProgram::statusLine->size.x; // whole line, as in showBusy()
    b.moveChar(0, ' ', c, w);
    b.moveStr(0, " " + msg + " ", c);
    TProgram::statusLine->writeLine(0, 0, w, 1, b);
    TScreen::flushScreen();
    if (gFlashTimer)
        TProgram::application->killTimer(gFlashTimer);
    gFlashTimer = TProgram::application->setTimer(2000);
}

// Put copied text on the desktop clipboard as well, for other programs.
// Native Windows: tvision's Win32 call, which is instant.  Elsewhere
// TClipboard::setText runs its helper in the foreground and freezes the UI
// until it answers (under Cygwin's pty: the full 1.5 s timeout, and the text
// never arrived), so do it without waiting:
//  - an OSC 52 escape to the terminal - what reaches the local clipboard
//    over ssh (Windows Terminal, kitty, iTerm2, xterm with it enabled...);
//    terminals without it ignore it, as they did when tvision sent it;
//  - a detached shell for the machine's own clipboard: Cygwin's
//    /dev/clipboard, Wayland, X11, macOS, WSL.  Best effort and silent.
// The app's own clipboard (Shift-Ins in an editor) has it either way.
void systemClipboard(const std::string &text)
{
#ifdef _WIN32
    TClipboard::setText(text);
#else
    static const char b64[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string osc = "\x1b]52;c;";
    for (size_t i = 0; i < text.size(); i += 3)
    {
        unsigned v = (unsigned char)text[i] << 16;
        if (i + 1 < text.size())
            v |= (unsigned char)text[i + 1] << 8;
        if (i + 2 < text.size())
            v |= (unsigned char)text[i + 2];
        osc += b64[(v >> 18) & 63];
        osc += b64[(v >> 12) & 63];
        osc += i + 1 < text.size() ? b64[(v >> 6) & 63] : '=';
        osc += i + 2 < text.size() ? b64[v & 63] : '=';
    }
    osc += '\x07';
    int tty = ::isatty(1) ? 1 : ::open("/dev/tty", O_WRONLY);
    if (tty >= 0)
    {
        for (size_t done = 0; done < osc.size();)
        {
            ssize_t w = ::write(tty, osc.data() + done, osc.size() - done);
            if (w < 0 && errno == EINTR)
                continue;
            if (w <= 0)
                break;
            done += (size_t)w;
        }
        if (tty != 1)
            ::close(tty);
    }

    std::string f = writeTemp(text, ".clip");
    std::string sc = std::string(kPreamble) +
                     "f=" + shq(f) + "\n"
                     // Cygwin / WSL: clip.exe takes BOM-less UTF-16 (a BOM would
                     // land in the clipboard); Windows programs want CRLF
                     "if command -v clip.exe >/dev/null && command -v iconv >/dev/null; then\n"
                     "  sed 's/$/\\r/' \"$f\" | iconv -f UTF-8 -t UTF-16LE | clip.exe\n"
                     "elif [ -w /dev/clipboard ]; then cat \"$f\" > /dev/clipboard\n"
                     "elif [ -n \"$WAYLAND_DISPLAY\" ] && command -v wl-copy >/dev/null; then wl-copy < \"$f\"\n"
                     "elif [ -n \"$DISPLAY\" ] && command -v xclip >/dev/null; then xclip -in -selection clipboard < \"$f\"\n"
                     "elif [ -n \"$DISPLAY\" ] && command -v xsel >/dev/null; then xsel --input --clipboard < \"$f\"\n"
                     "elif command -v pbcopy >/dev/null; then pbcopy < \"$f\"\n"
                     "fi\n"
                     "rm -f \"$f\"\n";
    pid_t p = ::fork();
    if (p == 0)
    {
        if (::fork() == 0) // the grandchild does it, so nobody need reap it
        {
            ::setsid();
            int n = ::open("/dev/null", O_RDWR);
            if (n >= 0)
            {
                ::dup2(n, 0);
                ::dup2(n, 1);
                ::dup2(n, 2);
                if (n > 2)
                    ::close(n);
            }
            ::execl(TV_SH, TV_SH, "-c", sc.c_str(), (char *)nullptr);
        }
        ::_exit(0);
    }
    if (p > 0)
        while (::waitpid(p, nullptr, 0) < 0 && errno == EINTR)
        {
        }
    else
        ::remove(f.c_str());
#endif
}
