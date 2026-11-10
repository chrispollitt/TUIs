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
            if e.code in (404, 412):
                return e.code, b"", {}
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

    def refresh(self, max_age=60):
        """sync(), unless the copy was brought up to date less than max_age seconds ago.  True = the copy is current."""
        try:
            if time.time() - os.path.getmtime(self._index_path()) < max_age:
                return True
        except OSError:
            pass
        ok = self.sync()
        if ok:
            try:
                os.utime(self._index_path(), None)
            except OSError:
                pass
        return ok

    # -- writing (refused while the server cannot be reached: nothing is queued)
    def _write_call(self, method, url, body=None, headers=None):
        try:
            return self.request(method, url, body, headers)
        except ConnectionError as e:
            self.offline = True
            raise ContactsError("the address book server cannot be reached (%s): contacts are read-only until it is back" % e)

    def make(self):
        """Create the collection (an address book) when it is not there yet."""
        body = (b'<?xml version="1.0"?><mkcol xmlns="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav"><set><prop>'
                b'<resourcetype><collection/><C:addressbook/></resourcetype><displayname>Contacts</displayname>'
                b'</prop></set></mkcol>')
        st, _, _ = self._write_call("MKCOL", self.url, body, {"Content-Type": "application/xml"})
        if st not in (200, 201, 204):
            raise ContactsError("could not create the address book (the server said %d)" % st)

    def _href_for(self, card):
        return urllib.parse.urlparse(self.url).path + urllib.parse.quote(card.uid or str(uuid.uuid4()), safe="") + ".vcf"

    def save(self, card):
        """Write a card: a new one (If-None-Match) or a changed one (If-Match its ETag).  A card somebody else changed
        meanwhile is refused with a ContactsError saying so; sync() and try again."""
        if not card.uid:
            card.props.append(["UID", "", str(uuid.uuid4())])
        new = not card.href
        href = card.href or self._href_for(card)
        hdr = {"Content-Type": "text/vcard; charset=utf-8"}
        if new:
            hdr["If-None-Match"] = "*"
        elif card.etag:
            hdr["If-Match"] = card.etag
        st, _, rh = self._write_call("PUT", self._abs(urllib.parse.quote(href)), card.text().encode("utf-8"), hdr)
        if st == 412:
            raise ContactsError("%s was changed by someone else meanwhile: reload (sync) and try again" % card.name)
        if st not in (200, 201, 204):
            raise ContactsError("the address book server did not take %s (it said %d)" % (card.name, st))
        card.href = href
        card.etag = rh.get("ETag") or rh.get("Etag") or ""
        self.cards[href] = card
        self._save_cache()
        return card

    def delete(self, card):
        if not card.href:
            return
        hdr = {"If-Match": card.etag} if card.etag else {}
        st, _, _ = self._write_call("DELETE", self._abs(urllib.parse.quote(card.href)), None, hdr)
        if st == 412:
            raise ContactsError("%s was changed by someone else meanwhile: reload (sync) and try again" % card.name)
        if st not in (200, 204, 404):
            raise ContactsError("the address book server did not delete %s (it said %d)" % (card.name, st))
        self.cards.pop(card.href, None)
        self._save_cache()

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


# ------------------------------------------------------------ configuration ---
def _conf(app):
    import configparser
    c = configparser.ConfigParser(interpolation=None)
    try:
        c.read(os.path.expanduser("~/.config/%s/%s.conf" % (app, app)), encoding="utf-8")
    except configparser.Error:
        pass
    return c


def default_book(apps=("tvmail", "tvchat", "tvcal")):
    """The Book the person's configuration names, or None.  The first of tvmail.conf, tvchat.conf, tvcal.conf with a
    [contacts] url (user, ca, insecure beside it) wins; failing that, the one beside tvcal's calendar:
    [server] url of tvcal.conf with its last part (calendar) replaced by contacts.  [contacts] enabled = no switches it off.
    $TVCONTACTS_URL / $TVCONTACTS_USER name one directly."""
    if os.environ.get("TVCONTACTS_URL"):
        return Book(os.environ["TVCONTACTS_URL"], os.environ.get("TVCONTACTS_USER", ""),
                    ca=os.environ.get("TVCONTACTS_CA", ""), insecure=bool(os.environ.get("TVCONTACTS_INSECURE")))
    yes = ("yes", "true", "1", "y", "on")
    for app in apps:
        c = _conf(app)
        if c.get("contacts", "enabled", fallback="yes").strip().lower() in ("no", "false", "0", "off"):
            return None
        url = c.get("contacts", "url", fallback="").strip()
        if url:
            return Book(url, c.get("contacts", "user", fallback="").strip(), ca=c.get("contacts", "ca", fallback="").strip(),
                        insecure=c.get("contacts", "insecure", fallback="no").strip().lower() in yes)
    cal = _conf("tvcal")
    cu = cal.get("server", "url", fallback="").strip().rstrip("/")
    if cu and cal.get("server", "mode", fallback="").strip().lower() != "local":
        return Book(cu.rpartition("/")[0] + "/contacts/", cal.get("server", "user", fallback="").strip(),
                    ca=cal.get("server", "ca", fallback="").strip(),
                    insecure=cal.get("server", "insecure", fallback="no").strip().lower() in yes)
    return None


# ------------------------------------------------------------- command line ---
def _clean(v):
    return " ".join(str(v).replace("\t", " ").split())


def _row(card, book):
    kind = "g" if card.is_group else "c"
    names = []
    if card.is_group:
        for uid in card.members:
            m = book.by_uid(uid)
            names.append(m.name if m is not None else uid)
    return "\t".join([kind, card.uid, _clean(card.name), _clean(",".join(card.nicknames)),
                      _clean(",".join(names if card.is_group else card.emails)), _clean(card.get("NOTE")[:60])])


def _split(text):
    return [x.strip() for x in re.split(r"[,;\n]", text or "") if x.strip()]


def _member_uids(book, names):
    """The uids for member names or addresses; a ContactsError naming those not in the book."""
    uids, missing = [], []
    for n in names:
        found = None
        for c in book.cards.values():
            if c.name.lower() == n.lower() or n.lower() in [e.lower() for e in c.emails] or n.lower() in [x.lower() for x in c.nicknames]:
                found = c
                break
        if found is None:
            missing.append(n)
        else:
            uids.append(found.uid)
    if missing:
        raise ContactsError("not in the address book: %s" % ", ".join(missing))
    return uids


def import_mailrc(book, paths=None, dry=False):
    """One-off: each ~/.mailrc / /etc/mailrc alias with ONE address becomes a contact, each alias or group with several (or
    naming other aliases) a group.  Entries the book already has by name are left alone.  Returns the lines to report."""
    import shlex
    paths = paths or ["/etc/mailrc", os.path.expanduser("~/.mailrc")]
    raw = {}
    for f in paths:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for ln in fh:
                    head = ln.strip().split(None, 1)
                    if len(head) == 2 and head[0] in ("alias", "group"):
                        try:
                            toks = shlex.split(head[1])
                        except ValueError:
                            continue
                        if len(toks) >= 2:
                            raw[toks[0]] = toks[1:]
        except OSError:
            continue
    have = {c.name.lower() for c in book.cards.values()}
    out = []
    singles = {k: v[0] for k, v in raw.items() if len(v) == 1 and "@" in v[0] and v[0] not in raw}
    for name, addr in sorted(singles.items()):
        if name.lower() in have:
            out.append("kept  %s (already there)" % name)
            continue
        out.append("added %s <%s>" % (name, addr))
        if not dry:
            book.save(Card.new(name, [addr]))
            have.add(name.lower())
    for name, members in sorted(raw.items()):
        if name in singles:
            continue
        if name.lower() in have:
            out.append("kept  group %s (already there)" % name)
            continue
        uids = []
        for m in members:
            target = m if m in raw else None
            if target is not None and target in singles:
                m = singles[target]
            card = None
            for c in book.cards.values():
                if m in c.emails or c.name == m:
                    card = c
                    break
            if card is None and "@" in m and m not in raw:
                card = Card.new(m.split("@")[0], [m])
                out.append("added %s <%s>" % (card.name, m))
                if not dry:
                    book.save(card)
            if card is not None and card.uid:
                uids.append(card.uid)
        out.append("added group %s (%d member%s)" % (name, len(uids), "" if len(uids) == 1 else "s"))
        if not dry:
            book.save(Card.new(name, group_members=uids))
            have.add(name.lower())
    return out


def main(argv=None):
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="tvcontacts", description="the TUI suite's address book (Radicale, CardDAV)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync", help="bring the local copy up to date")
    sub.add_parser("list", help="TSV: kind(c/g) uid name nicknames emails-or-members note")
    sub.add_parser("init", help="create the address book on the server if it is not there")
    p = sub.add_parser("show"); p.add_argument("uid")
    p = sub.add_parser("delete"); p.add_argument("uid")
    p = sub.add_parser("save", help="add or change a contact (or a group with --group); prints its uid")
    p.add_argument("--uid", default="")
    p.add_argument("--name", required=True)
    p.add_argument("--nicks", default="")
    p.add_argument("--emails", default="")
    p.add_argument("--group", action="store_true")
    p.add_argument("--members", default="", help="a group's members: names, nicknames or addresses, comma separated")
    p = sub.add_parser("add-address", help="add an address (a sender) unless the book already has it")
    p.add_argument("--email", required=True)
    p.add_argument("--name", default="")
    p = sub.add_parser("complete"); p.add_argument("text")
    p = sub.add_parser("import-mailrc"); p.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    book = default_book()
    if book is None:
        sys.stderr.write("no address book is configured: [contacts] url in tvmail.conf, or tvcal's [server] url\n")
        return 2
    try:
        if a.cmd == "init":
            try:
                if not book.sync():
                    raise ContactsError("the address book server cannot be reached")
                print("the address book is there")
            except ContactsError as e:
                if "does not exist" not in str(e):
                    raise
                book.make()
                book.sync()
                print("the address book was made")
            return 0
        ok = book.refresh(0 if a.cmd in ("sync", "save", "delete", "import-mailrc", "add-address") else 30)
        if not ok and a.cmd in ("save", "delete", "import-mailrc", "add-address"):
            raise ContactsError("the address book server cannot be reached: contacts are read-only until it is back")
        if a.cmd == "sync":
            print("offline: using the copy" if book.offline else "up to date")
        elif a.cmd == "list":
            for c in sorted(book.cards.values(), key=lambda c: (c.is_group, c.name.lower())):
                print(_row(c, book))
            if book.offline:
                sys.stderr.write("offline: this is the copy from the last time the server answered\n")
        elif a.cmd == "show":
            c = book.by_uid(a.uid)
            if c is None:
                raise ContactsError("no such entry")
            print(_row(c, book))
        elif a.cmd == "add-address":
            addr = a.email.strip()
            if "@" not in addr:
                raise ContactsError("not an email address: %s" % addr)
            for c in book.cards.values():
                if addr.lower() in [e.lower() for e in c.emails]:
                    print("%s is already in the address book (%s)" % (addr, c.name))
                    return 0
            name = _clean(a.name).strip(" \"'") or addr.split("@")[0]
            book.save(Card.new(name, [addr]))
            print("added %s <%s> to the address book" % (name, addr))
        elif a.cmd == "complete":
            for name, addr in book.complete(a.text):
                print("%s\t%s" % (_clean(name), _clean(addr)))
        elif a.cmd == "delete":
            c = book.by_uid(a.uid)
            if c is None:
                raise ContactsError("no such entry")
            book.delete(c)
            print("deleted %s" % c.name)
        elif a.cmd == "import-mailrc":
            for ln in import_mailrc(book, dry=a.dry_run):
                print(ln)
        elif a.cmd == "save":
            card = book.by_uid(a.uid) if a.uid else None
            if a.uid and card is None:
                raise ContactsError("no such entry")
            if card is None:
                card = Card.new(a.name, group_members=[] if a.group else None)
            card.set_values("FN", [a.name])
            card.props = [pr for pr in card.props if pr[0] != "N" or not a.name]
            if a.group:
                if not card.is_group:
                    card.props.append(["KIND", "", "group"])
                card.props = [pr for pr in card.props if pr[0] != "MEMBER"] + \
                    [["MEMBER", "", "urn:uuid:" + u] for u in _member_uids(book, _split(a.members))]
            else:
                bad = [e for e in _split(a.emails) if "@" not in e]
                if bad:
                    raise ContactsError("not an email address: %s" % ", ".join(bad))
                card.props = [pr for pr in card.props if pr[0] != "EMAIL"] + \
                    [["EMAIL", "TYPE=INTERNET", _esc(e)] for e in _split(a.emails)]
            card.props = [pr for pr in card.props if pr[0] != "NICKNAME"]
            if _split(a.nicks):
                card.props.append(["NICKNAME", "", ",".join(_esc(n) for n in _split(a.nicks))])
            book.save(card)
            print(card.uid)
    except ContactsError as e:
        sys.stderr.write("%s\n" % e)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
