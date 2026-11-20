"""tvnntp - a small NNTP client and a .newsrc store, standard library only.

Written for tvmail's Usenet service (tvmail/SERVICES-DESIGN.md).  Python's own nntplib was
deprecated in 3.11 and is gone in 3.13, and the apps must keep running on 3.6 and later.

    c = NNTP("cpbee", 119)
    count, low, high = c.group("comp.os.linux")
    for row in c.over(high - 99, high): ...
    raw = c.article(high)                        # the whole article, bytes
    c.post(b"Newsgroups: lan.test\\r\\nSubject: hi\\r\\n\\r\\nbody\\r\\n")

    rc = Newsrc(path, seed=["lan.misc"])        # which groups, and which articles you have read
    rc.mark("lan.misc", 7, True); rc.save()

Only what a reader needs: MODE READER, AUTHINFO, GROUP, OVER / XOVER, ARTICLE, POST, LIST ACTIVE,
LIST NEWSGROUPS.  Text comes back as str (UTF-8, else Latin-1: the old articles are not tidy); an
article comes back as the bytes the server sent.
"""
import os
import socket
import ssl
import tempfile


class NNTPError(Exception):
    """The server refused a command (4xx / 5xx); .code and .text say how."""

    def __init__(self, code, text):
        Exception.__init__(self, "%s %s" % (code, text))
        self.code = code
        self.text = text


class NNTPAbort(NNTPError):
    """The connection is gone or the server closed it (EOF, 400, 205): reconnect and ask again."""


def _text(b):
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("latin-1")


# the standard overview fields, in the order RFC 3977 gives them after the article number
_STD_FMT = ["subject", "from", "date", "message-id", "references", ":bytes", ":lines"]


class NNTP(object):
    def __init__(self, host, port=119, tls=False, timeout=20, cafile=None, user=None, password=None):  # allowlist secret: a parameter, not a value
        self.host, self.port = host, int(port)
        self.timeout = timeout
        s = socket.create_connection((host, self.port), timeout)
        if tls:
            ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
            s = ctx.wrap_socket(s, server_hostname=host)
        self.sock = s
        self.f = s.makefile("rb")
        self.fmt = None
        code, text = self._reply()
        if code not in (200, 201):
            raise NNTPError(code, text)
        self.can_post = code == 200
        try:                                    # INN answers 200 to this; a plain reader server may say 500
            code, text = self.cmd("MODE READER", ())
            if code in (200, 201):
                self.can_post = code == 200
        except NNTPError:
            pass
        if user:
            self.authenticate(user, password or "")

    # ---- the wire ----
    def _readline(self):
        line = self.f.readline(65536)
        if not line:
            raise NNTPAbort(400, "connection closed")
        return line.rstrip(b"\r\n")

    def _reply(self):
        line = _text(self._readline())
        try:
            code = int(line[:3])
        except ValueError:
            raise NNTPError(0, "bad reply: %r" % line)
        return code, line[4:]

    def cmd(self, line, ok):
        """Send one command; return (code, text).  A code not in `ok` (and not 1xx-3xx when ok is empty) is an error."""
        self.sock.sendall(line.encode("utf-8") + b"\r\n")
        code, text = self._reply()
        if code in (400, 205):
            raise NNTPAbort(code, text)
        if ok and code not in ok:
            raise NNTPError(code, text)
        if not ok and code >= 400:
            raise NNTPError(code, text)
        return code, text

    def _block(self):
        """The lines of a multi-line response, up to the lone dot, dot-unstuffed, as bytes."""
        out = []
        while True:
            line = self._readline()
            if line == b".":
                return out
            if line.startswith(b".."):
                line = line[1:]
            out.append(line)

    # ---- session ----
    def authenticate(self, user, password):
        code, text = self.cmd("AUTHINFO USER " + user, (281, 381))
        if code == 381:
            self.cmd("AUTHINFO PASS " + password, (281,))

    def quit(self):
        try:
            self.sock.sendall(b"QUIT\r\n")
        except OSError:
            pass
        self.close()

    def close(self):
        for o in (self.f, self.sock):
            try:
                o.close()
            except Exception:
                pass

    # ---- reading ----
    def group(self, name):
        """(count, low, high) of a group; NNTPError 411 when there is none.  `count` is an estimate, as ever."""
        code, text = self.cmd("GROUP " + name, (211,))
        parts = text.split()
        return int(parts[0]), int(parts[1]), int(parts[2])

    def _overview_fmt(self):
        if self.fmt is None:
            try:
                self.cmd("LIST OVERVIEW.FMT", (215,))
                names = []
                for ln in self._block():
                    n = _text(ln).strip().lower()
                    if n.endswith(":full"):
                        n = n[:-5]
                    names.append(n.rstrip(":") if not n.startswith(":") else n)
                self.fmt = names if names and names[0] == "subject" else list(_STD_FMT)
            except NNTPError:
                self.fmt = list(_STD_FMT)
        return self.fmt

    def over(self, first, last):
        """The overview of articles first..last of the selected group: a list of dicts with number, subject, from,
        date, message-id, references (the standard fields)."""
        fmt = self._overview_fmt()
        try:
            self.cmd("OVER %d-%d" % (first, last), (224,))
        except NNTPError as e:
            if e.code in (423, 420):
                return []                       # no articles in that range
            if e.code not in (500, 501, 503):
                raise
            try:
                self.cmd("XOVER %d-%d" % (first, last), (224,))
            except NNTPError as e2:
                if e2.code in (423, 420):
                    return []
                raise
        rows = []
        for ln in self._block():
            f = _text(ln).split("\t")
            if len(f) < 2 or not f[0].strip().isdigit():
                continue
            d = {"number": int(f[0])}
            for name, val in zip(fmt, f[1:]):
                d[name] = val
            for k in ("subject", "from", "date", "message-id", "references"):
                d.setdefault(k, "")
            rows.append(d)
        return rows

    def article(self, number):
        """The raw article `number` of the selected group, headers and body, with CRLF folded to LF."""
        self.cmd("ARTICLE %d" % number, (220,))
        return b"\n".join(self._block()) + b"\n"

    def list_active(self, pattern=None):
        """[(name, high, low, status)] of the groups the server carries (LIST ACTIVE [wildmat])."""
        self.cmd("LIST ACTIVE" + (" " + pattern if pattern else ""), (215,))
        out = []
        for ln in self._block():
            f = _text(ln).split()
            if len(f) >= 4 and f[1].isdigit() and f[2].isdigit():
                out.append((f[0], int(f[1]), int(f[2]), f[3]))
        return out

    def list_newsgroups(self, pattern=None):
        """{name: description} (LIST NEWSGROUPS [wildmat]); {} when the server has none."""
        try:
            self.cmd("LIST NEWSGROUPS" + (" " + pattern if pattern else ""), (215,))
        except NNTPError:
            return {}
        out = {}
        for ln in self._block():
            name, _, desc = _text(ln).partition("\t")
            if not desc:
                name, _, desc = _text(ln).partition(" ")
            out[name.strip()] = desc.strip()
        return out

    # ---- writing ----
    def post(self, data):
        """POST an article (bytes, LF or CRLF); NNTPError with the server's words when it is refused."""
        self.cmd("POST", (340,))
        lines = data.replace(b"\r\n", b"\n").split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
        out = b"".join((b"." + ln if ln.startswith(b".") else ln) + b"\r\n" for ln in lines)
        self.sock.sendall(out + b".\r\n")
        code, text = self._reply()
        if code != 240:
            raise NNTPError(code, text)
        return text


# ---------------------------------------------------------------- .newsrc ---
class Newsrc(object):
    """Which groups you read and which articles of each you have read, in the classic file::

        comp.os.linux: 1-4000,4010
        alt.other! 1-20

    ``:`` is subscribed, ``!`` is not.  Groups keep the order they have in the file.  A file that does
    not exist yet starts from `seed` (the groups named in the config)."""

    def __init__(self, path, seed=()):
        self.path = path
        self.groups = []                        # names, in order
        self.sub = {}                           # name -> subscribed?
        self.read = {}                          # name -> [[a, b], ...] sorted, disjoint, merged
        self.exists = os.path.exists(path)
        if self.exists:
            self._load()
        else:
            for g in seed:
                self.subscribe(g)

    def _load(self):
        with open(self.path, "rb") as f:
            for raw in f.read().splitlines():
                line = _text(raw).strip()
                if not line:
                    continue
                for i, ch in enumerate(line):
                    if ch in ":!":
                        break
                else:
                    continue
                name, sub, rest = line[:i].strip(), ch == ":", line[i + 1:]
                if not name:
                    continue
                if name not in self.sub:
                    self.groups.append(name)
                self.sub[name] = sub
                self.read[name] = self._parse(rest)

    @staticmethod
    def _parse(text):
        out = []
        for part in text.replace(" ", "").split(","):
            if not part:
                continue
            a, _, b = part.partition("-")
            try:
                lo = int(a)
                hi = int(b) if b else lo
            except ValueError:
                continue
            if hi >= lo:
                out.append([lo, hi])
        return Newsrc._merge(out)

    @staticmethod
    def _merge(rs):
        rs = sorted([list(r) for r in rs])
        out = []
        for lo, hi in rs:
            if out and lo <= out[-1][1] + 1:
                out[-1][1] = max(out[-1][1], hi)
            else:
                out.append([lo, hi])
        return out

    def subscribed(self):
        return [g for g in self.groups if self.sub.get(g)]

    def subscribe(self, name):
        if name not in self.sub:
            self.groups.append(name)
            self.read[name] = []
        self.sub[name] = True

    def unsubscribe(self, name):
        if name in self.sub:
            self.sub[name] = False

    def is_read(self, name, n):
        for lo, hi in self.read.get(name, ()):
            if lo <= n <= hi:
                return True
            if lo > n:
                break
        return False

    def mark(self, name, n, read=True):
        self.mark_range(name, n, n, read)

    def mark_range(self, name, lo, hi, read=True):
        """Mark articles lo..hi read (or unread)."""
        if name not in self.sub:
            self.subscribe(name)
        rs = self.read.setdefault(name, [])
        if read:
            self.read[name] = self._merge(rs + [[lo, hi]])
            return
        out = []
        for a, b in rs:
            if b < lo or a > hi:
                out.append([a, b])
                continue
            if a < lo:
                out.append([a, lo - 1])
            if b > hi:
                out.append([hi + 1, b])
        self.read[name] = out

    def catch_up(self, name, high):
        self.mark_range(name, 1, high, True)

    def unread_estimate(self, name, count, low, high):
        """How many articles of low..high are unread.  The server's `count` is an estimate and the read ranges
        may cover numbers that no longer exist, so this is the smaller of the two figures."""
        if high < low:
            return 0
        seen = 0
        for lo, hi in self.read.get(name, ()):
            a, b = max(lo, low), min(hi, high)
            if b >= a:
                seen += b - a + 1
        return max(0, min(count, (high - low + 1) - seen))

    def text(self):
        lines = []
        for g in self.groups:
            rs = ",".join(str(a) if a == b else "%d-%d" % (a, b) for a, b in self.read.get(g, ()))
            lines.append(("%s%s %s" % (g, ":" if self.sub.get(g) else "!", rs)).rstrip())
        return "\n".join(lines) + ("\n" if lines else "")

    def save(self):
        d = os.path.dirname(self.path) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".newsrc-", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(self.text())
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self.exists = True
