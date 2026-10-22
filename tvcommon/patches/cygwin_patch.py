#!/usr/bin/env python3
"""Make magiblot/tvision build on Cygwin.

Cygwin has neither the C++-linkage clash guard for strupr/strlwr nor FIONREAD.
Run after cloning tvision:  python3 patches/cygwin_patch.py third_party/tvision
Idempotent - safe to run repeatedly.
"""
import sys, os

TV = sys.argv[1] if len(sys.argv) > 1 else "third_party/tvision"


def patch(rel, subs):
    p = os.path.join(TV, rel)
    with open(p, encoding="utf-8") as f:
        s = f.read()
    orig = s
    for old, new in subs:
        if new in s:
            continue                      # already applied
        if old not in s:
            print("  ?? pattern not found in %s (tvision changed?)" % rel)
            continue
        s = s.replace(old, new, 1)
    if s != orig:
        with open(p, "w", encoding="utf-8") as f:
            f.write(s)
        print("  patched", rel)
    else:
        print("  ok (unchanged)", rel)


# 1. Only strupr clashes: Cygwin's <string.h> declares it with C linkage.
#    itoa/ltoa/ultoa/stricmp/strnicmp are NOT in Cygwin headers - keep those.
patch("include/tvision/util.h", [
    ("char *strupr(char *s) noexcept;",
     "#ifndef __CYGWIN__ // tvmail: Cygwin's <string.h> already declares strupr\n"
     "char *strupr(char *s) noexcept;\n"
     "#endif"),
])

# 2. FIONREAD is absent on Cygwin.  fdEmpty() -> poll(); also detect POLLHUP.
patch("source/platform/events.cpp", [
    ("""static bool fdEmpty(int fd) noexcept
{
    int nbytes;
    return ioctl(fd, FIONREAD, &nbytes) == -1 || !nbytes;
}""",
     """static bool fdEmpty(int fd) noexcept
{
#if defined(__CYGWIN__) // tvmail: no FIONREAD; use poll() + POLLHUP
    struct pollfd p; p.fd = fd; p.events = POLLIN; p.revents = 0;
    if (poll(&p, 1, 0) <= 0) return true;
    if (p.revents & (POLLHUP | POLLERR | POLLNVAL)) return true;
    return !(p.revents & POLLIN);
#else
    int nbytes;
    return ioctl(fd, FIONREAD, &nbytes) == -1 || !nbytes;
#endif
}"""),
    ("#include <sys/ioctl.h>\n#include <sys/select.h>",
     "#include <sys/ioctl.h>\n#include <sys/select.h>\n#include <poll.h>"),
])

# 3. errredir: FIONREAD gave a byte count to drain captured stderr at shutdown.
#    On Cygwin, drain non-blocking instead.
patch("source/platform/errredir.cpp", [
    ("""        int size;
        if (ioctl(bufFd[0], FIONREAD, &size) != -1 && size > 0)
            dumpPipe(bufFd[0], ttyFd, (size_t) size);""",
     """#if defined(__CYGWIN__) // tvmail: no FIONREAD; drain non-blocking
        fcntl(bufFd[0], F_SETFL, fcntl(bufFd[0], F_GETFL, 0) | O_NONBLOCK);
        dumpPipe(bufFd[0], ttyFd, (size_t) 1 << 20);
#else
        int size;
        if (ioctl(bufFd[0], FIONREAD, &size) != -1 && size > 0)
            dumpPipe(bufFd[0], ttyFd, (size_t) size);
#endif"""),
])

print("cygwin patch done.")
