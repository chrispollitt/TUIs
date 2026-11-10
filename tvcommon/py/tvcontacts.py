#!/usr/bin/env python3
"""tvcontacts - the address book of the TUI suite: contacts and mail groups kept in Radicale (CardDAV) on cpbee.
Stdlib only, Python >= 3.6.  tvmail, tvchat and tvcal share it (design: tvcommon/CONTACTS-DESIGN.md).

    book = Book("https://cpbee.cmnet/radicale/chris/contacts/", user="chris", ca="~/.config/tvmail/cpbee-ca.pem")
    book.sync()                          # fetches what changed (ETags), keeps a copy in the state directory
    book.complete("mich")                # [(display name, address)] for the To: field
    book.expand("family")                # a group's members' addresses
    book.offline                         # True when the server could not be reached and the copy was used

One contact is one vCard (3.0) in the collection.  A mail group is a vCard with KIND:group and MEMBER:urn:uuid:... lines
(RFC 6350's own way, which Radicale and other CardDAV programs keep as they are).  Properties this module does not
know are kept as they are when a card is written back.  Reads come from the copy when the server is unreachable;
writes are refused then, saying so (nothing is queued).

The password is not in any configuration file: $TVCONTACTS_PASS, then ~/.mu-tickets, then ~/.netrc (as tvcal does).
"""
import base64
import hashlib
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

VERSION = "0.1.0"
DAV = "{DAV:}"


class ContactsError(Exception):
    """Something to say to the person, in words."""


# ---------------------------------------------------------------- vCard ---
def _unfold(text):
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return [ln for ln in lines if ln.strip()]


def _unesc(v):
    out, i = [], 0
    while i < len(v):
        c = v[i]
        if c == "\\" and i + 1 < len(v):
            n = v[i + 1]
            out.append("\n" if n in "nN" else n)
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _esc(v):
    return v.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def _fold(line):
    out, b = [], line.encode("utf-8")
    while len(b) > 75:
        cut = 75
        while cut > 0 and (b[cut] & 0xC0) == 0x80:      # not in the middle of a UTF-8 character
            cut -= 1
        out.append(b[:cut].decode("utf-8"))
        b = b[cut:]
        b = b" " + b
    out.append(b.decode("utf-8"))
    return "\r\n".join(out)


class Card(object):
    """A parsed vCard.  props keeps every property as [name, params, value] so that a write-back loses nothing."""

    def __init__(self):
        self.props = []
        self.href = ""
        self.etag = ""

    # -- reading
    @classmethod
    def parse(cls, text):
        card = cls()
        inside = False
        for ln in _unfold(text):
            up = ln.upper()
            if up == "BEGIN:VCARD":
                inside = True
                continue
            if up == "END:VCARD":
                break
            if not inside or ":" not in ln:
                continue
            head, _, value = ln.partition(":")
            name, *params = head.split(";")
            name = name.split(".")[-1].upper()           # "item1.EMAIL" (Apple) is EMAIL
            card.props.append([name, ";".join(params), value])
        if not card.props:
            raise ContactsError("not a vCard")
        return card

    def _all(self, name):
        return [p for p in self.props if p[0] == name]

    def get(self, name, default=""):
        found = self._all(name)
        return _unesc(found[0][2]) if found else default

    @property
    def uid(self):
        return self.get("UID")

    @property
    def kind(self):
        return self.get("KIND", "individual").lower()

    @property
    def is_group(self):
        return self.kind == "group"

    @property
    def name(self):
        fn = self.get("FN")
        if fn:
            return fn
        n = self.get("N")
        return " ".join(x for x in (n.split(";") + ["", "", "", ""])[1::-1] if x).strip() or self.get("EMAIL")

    @property
    def emails(self):
        return [_unesc(p[2]).strip() for p in self._all("EMAIL") if _unesc(p[2]).strip()]

    @property
    def members(self):
        """The uids a group lists (MEMBER:urn:uuid:...)."""
        out = []
        for p in self._all("MEMBER"):
            v = _unesc(p[2]).strip()
            out.append(v[9:] if v.lower().startswith("urn:uuid:") else v)
        return out

    @property
    def nicknames(self):
        return [x.strip() for p in self._all("NICKNAME") for x in _unesc(p[2]).split(",") if x.strip()]

    # -- writing
    @classmethod
    def new(cls, name, emails=(), group_members=None, uid=None):
        c = cls()
        c.props = [["VERSION", "", "3.0"], ["UID", "", uid or str(uuid.uuid4())], ["FN", "", _esc(name)]]
        if group_members is not None:
            c.props.append(["KIND", "", "group"])
            c.props += [["MEMBER", "", "urn:uuid:" + m] for m in group_members]
        c.props += [["EMAIL", "TYPE=INTERNET", _esc(e)] for e in emails]
        return c

    def set_values(self, name, values, params=""):
        keep = [p for p in self.props if p[0] != name]
        self.props = keep + [[name, params, _esc(v)] for v in values]

    def text(self):
        body = [["VERSION", "", "3.0"]] + [p for p in self.props if p[0] != "VERSION"]
        lines = ["BEGIN:VCARD"] + [_fold("%s%s:%s" % (n, (";" + pr) if pr else "", v)) for n, pr, v in body] + ["END:VCARD"]
        return "\r\n".join(lines) + "\r\n"


# ----------------------------------------------------------------- the book ---
def find_password(url, user):
    """$TVCONTACTS_PASS, then ~/.mu-tickets (a URL with user and password in front of the host), then ~/.netrc: an entry for this host AND user."""
    pw = os.environ.get("TVCONTACTS_PASS")
    if pw:
        return pw
    u = urllib.parse.urlparse(url)
    host = (u.hostname or "").lower()

    def same(h):
        h = h.lower()
        return h == host or ("." not in h and host.partition(".")[0] == h) or ("." not in host and h.partition(".")[0] == host)
    try:
        with open(os.environ.get("TVMAIL_MU_TICKETS") or os.path.expanduser("~/.mu-tickets"), encoding="utf-8",
                  errors="replace") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln or ln.startswith("#") or "@" not in ln:
                    continue
                tail = ln.split("://", 1)[-1]
                cred, _, hostpart = tail.rpartition("@")
                usr, _, pwd = cred.partition(":")
                if pwd and urllib.parse.unquote(usr) == user and same(hostpart.split("/")[0].partition(":")[0]):
                    return urllib.parse.unquote(pwd)
    except OSError:
        pass
    try:
        with open(os.path.expanduser("~/.netrc"), encoding="utf-8", errors="replace") as fh:
            toks = fh.read().split()
    except OSError:
        return ""
    cur, found, i = None, [], 0
    while i < len(toks):
        t = toks[i]
        if t == "machine" and i + 1 < len(toks):
            cur = {"machine": toks[i + 1]}
            found.append(cur)
            i += 2
            continue
        if cur is not None and t in ("login", "password") and i + 1 < len(toks):
            cur[t] = toks[i + 1]
            i += 2
            continue
        i += 1
    for want in (("%s:%s" % (host, u.port)) if u.port else None, host):
        for e in found:
            if want and e.get("login") == user and e.get("password") and (e["machine"] == want or same(e["machine"].partition(":")[0]) and want == host):
                return e["password"]
    return ""


def state_dir():
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "tvcommon", "contacts")


class Book(object):
    """One address book: a CardDAV collection and its local copy (a directory of .vcf files and an index of ETags)."""

    def __init__(self, url, user, ca="", insecure=False, cache=None, password=None, timeout=8):
        self.url = url if url.endswith("/") else url + "/"
        self.user, self.ca, self.insecure, self.timeout = user, ca, insecure, timeout
        self._password = password  # allowlist secret: the caller passes it in
        key = hashlib.sha1((self.url + "\0" + user).encode("utf-8")).hexdigest()[:12]
        self.cache = cache or os.path.join(state_dir(), key)
        self.offline = False
        self.cards = {}                   # href -> Card
        self._load_cache()

    # -- the wire
    def request(self, method, url, body=None, headers=None):
        pw = self._password if self._password is not None else find_password(self.url, self.user)
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        req.add_header("Authorization", "Basic " + base64.b64encode(("%s:%s" % (self.user, pw)).encode("utf-8")).decode("ascii"))
        ctx = None
        if url.startswith("https"):
            ctx = ssl.create_default_context(cafile=os.path.expanduser(self.ca) if self.ca else None)
            if self.insecure:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise ContactsError("the address book server refused the login for %s (password: $TVCONTACTS_PASS, "
                                    "~/.mu-tickets or ~/.netrc)" % (self.user or "(no user)"))
            if e.code == 404:
                return 404, b"", {}
            raise ContactsError("the address book server said %d %s" % (e.code, e.reason))
        except (urllib.error.URLError, OSError) as e:
            raise ConnectionError(str(getattr(e, "reason", e)))

    def _abs(self, href):
        u = urllib.parse.urlparse(self.url)
        return "%s://%s%s" % (u.scheme, u.netloc, href) if href.startswith("/") else href

    # -- the copy
    def _index_path(self):
        return os.path.join(self.cache, "index.json")

    def _load_cache(self):
        try:
            with open(self._index_path(), encoding="utf-8") as fh:
                idx = json.load(fh)
        except (OSError, ValueError):
            return
        for href, meta in idx.items():
            try:
                with open(os.path.join(self.cache, meta["file"]), encoding="utf-8") as fh:
                    card = Card.parse(fh.read())
            except (OSError, ContactsError, KeyError):
                continue
            card.href, card.etag = href, meta.get("etag", "")
            self.cards[href] = card

    def _save_cache(self):
        os.makedirs(self.cache, mode=0o700, exist_ok=True)
        idx = {}
        for href, card in self.cards.items():
            fname = hashlib.sha1(href.encode("utf-8")).hexdigest() + ".vcf"
            self._write(os.path.join(self.cache, fname), card.text())
            idx[href] = {"file": fname, "etag": card.etag}
        for f in os.listdir(self.cache):
            if f.endswith(".vcf") and f not in {m["file"] for m in idx.values()}:
                try:
                    os.remove(os.path.join(self.cache, f))
                except OSError:
                    pass
        self._write(self._index_path(), json.dumps(idx))

    @staticmethod
    def _write(path, text):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)

    # -- sync
    def sync(self):
        """Bring the copy up to date: one PROPFIND for the ETags, a GET for each card that is new or changed.
        Returns True when the server answered; False (and offline = True) when the copy was all there is."""
        body = (b'<?xml version="1.0"?><propfind xmlns="DAV:"><prop><getetag/></prop></propfind>')
        try:
            st, data, _ = self.request("PROPFIND", self.url, body, {"Depth": "1", "Content-Type": "application/xml"})
        except ConnectionError:
            self.offline = True
            return False
        self.offline = False
        if st == 404:
            raise ContactsError("the address book %s does not exist on the server (configure-radicale.sh makes it)" % self.url)
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            raise ContactsError("the address book server sent something that is not a listing")
        remote = {}
        base = urllib.parse.urlparse(self.url).path
        for resp in root.iter(DAV + "response"):
            href = urllib.parse.unquote(resp.findtext(DAV + "href") or "")
            etag = ""
            for pst in resp.iter(DAV + "propstat"):
                e = pst.findtext(DAV + "prop/" + DAV + "getetag")
                if e:
                    etag = e
            if href and href.rstrip("/") != base.rstrip("/") and href.lower().endswith(".vcf"):
                remote[href] = etag
        changed = False
        for href in [h for h in self.cards if h not in remote]:
            del self.cards[href]
            changed = True
        for href, etag in remote.items():
            have = self.cards.get(href)
            if have is not None and have.etag == etag and etag:
                continue
            try:
                st, raw, hdr = self.request("GET", self._abs(urllib.parse.quote(href)), headers={"Accept": "text/vcard"})
                card = Card.parse(raw.decode("utf-8", "replace"))
            except (ContactsError, ConnectionError):
                continue                                 # one bad card must not hide the others
            card.href, card.etag = href, etag or hdr.get("ETag", "")
            self.cards[href] = card
            changed = True
        if changed or not os.path.exists(self._index_path()):
            self._save_cache()
        return True

    # -- asking
    def contacts(self):
        return sorted((c for c in self.cards.values() if not c.is_group), key=lambda c: c.name.lower())

    def groups(self):
        return sorted((c for c in self.cards.values() if c.is_group), key=lambda c: c.name.lower())

    def by_uid(self, uid):
        for c in self.cards.values():
            if c.uid == uid:
                return c
        return None

    def complete(self, text, limit=12):
        """[(display name, address)] for what was typed in To: - a name, a nickname or part of an address; a group
        comes as (group name, 'name, name2, ...' - its members' addresses joined) so it can be dropped in as it is."""
        q = text.strip().lower()
        out = []
        if not q:
            return out
        for c in self.contacts():
            hay = [c.name.lower()] + [n.lower() for n in c.nicknames] + [e.lower() for e in c.emails]
            if any(h.startswith(q) or (len(q) > 1 and (" " + q) in (" " + h)) or q in h for h in hay) and c.emails:
                out.append((c.name, c.emails[0]))
        for g in self.groups():
            if g.name.lower().startswith(q) or any(n.lower().startswith(q) for n in g.nicknames):
                addrs = self.expand(g.name)
                if addrs:
                    out.append((g.name + " (group)", ", ".join(addrs)))
        return out[:limit]

    def expand(self, group_name, _seen=None):
        """A group's addresses (nested groups too, each once); [] if there is no such group."""
        seen = _seen if _seen is not None else set()
        out = []
        name = group_name.strip().lower()
        for g in self.groups():
            if g.name.lower() != name and name not in [n.lower() for n in g.nicknames]:
                continue
            if g.uid in seen:
                continue
            seen.add(g.uid)
            for uid in g.members:
                m = self.by_uid(uid)
                if m is None:
                    continue
                for a in (self.expand(m.name, seen) if m.is_group else m.emails[:1]):
                    if a not in out:
                        out.append(a)
        return out
