"""tvfloyd - what tvchat's and tvmail's Floyd share.  Stdlib only, Python >= 3.6,
pure apart from the notes file: no sockets, no UI.

  * the bot LEVELS (how much he says, and what he may do unprompted)
  * small useful tools: a safe calculator, unit and time-zone conversion, a
    to-do list and notes that outlive the session
  * "clippy": unprompted tips, always introduced as "Floyd not clippy but ..."

Each backend imports this by path (see the finder in each), and degrades to
"no tools" if it is missing, so a half-installed system still works.
"""

import ast
import json
import logging
import math
import operator
import os
import random
import re
import hmac
import shutil
import socket
import subprocess
import sys
import time
from collections import Counter

_log = logging.getLogger("tvfloyd")

# -- levels ------------------------------------------------------------------
# off      silent; only a level change is heard
# minimal  answers when spoken to (floyd: ..., a private message) and !commands;
#          no banter, no karma, no unprompted anything
# helpful  + the useful tools in plain words, !seen/!tell/karma, catch-up
# chatty   + banter, jokes, games, talking by himself when left alone
# clippy   + unprompted tips: "Floyd not clippy but it looks like you ..."
LEVELS = ("off", "minimal", "helpful", "chatty", "clippy")
DEFAULT_LEVEL = "chatty"


def parse_level(text, default=None):
    """A level name (or a unique prefix, or 0-4) -> the name; `default` if it is none."""
    t = (text or "").strip().lower()
    if t.isdigit() and int(t) < len(LEVELS):
        return LEVELS[int(t)]
    if t == "full":
        return "clippy"
    hits = [l for l in LEVELS if t and l.startswith(t)]
    return hits[0] if len(hits) == 1 else default


def rank(level):
    return LEVELS.index(level) if level in LEVELS else LEVELS.index(DEFAULT_LEVEL)


def at_least(level, floor):
    return rank(level) >= rank(floor)


LEVEL_BLURB = {
    "off": "Floyd is off. He hears nothing and says nothing.",
    "minimal": "Floyd answers when spoken to and runs !commands. Nothing else.",
    "helpful": "Floyd also does the useful things: sums, conversions, to-dos, catching you up.",
    "chatty": "Floyd also chats, plays, and talks to himself when he is bored.",
    "clippy": "Floyd also butts in with tips. Floyd not clippy but he does so love to help.",
}


def level_help():
    return " | ".join("%s: %s" % (l, LEVEL_BLURB[l]) for l in LEVELS)


# -- calculator --------------------------------------------------------------
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod, ast.Pow: operator.pow}
_UN = {ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
          "log": math.log10, "ln": math.log, "exp": math.exp, "abs": abs,
          "round": round, "floor": math.floor, "ceil": math.ceil}
_CONSTS = {"pi": math.pi, "e": math.e}


class CalcError(ValueError):
    pass


def _ev(node, depth=0):
    if depth > 40:
        raise CalcError("too deep")
    if isinstance(node, ast.Expression):
        return _ev(node.body, depth + 1)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return node.value
    if type(node).__name__ == "Num":                               # Python < 3.8
        return node.n
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
        return _UN[type(node.op)](_ev(node.operand, depth + 1))
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        a, b = _ev(node.left, depth + 1), _ev(node.right, depth + 1)
        if isinstance(node.op, ast.Pow) and (abs(b) > 1000 or abs(a) > 1e12):
            raise CalcError("too big")
        return _BIN[type(node.op)](a, b)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in _FUNCS and len(node.args) in (1, 2) and not node.keywords:
        return _FUNCS[node.func.id](*[_ev(x, depth + 1) for x in node.args])
    raise CalcError("not arithmetic")


def _fmt_num(v):
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            raise CalcError("no such number")
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        s = "%.10g" % v
        return s
    return str(v)


def calc(expr):
    """The value of an arithmetic expression as text; CalcError if it is not one."""
    e = expr.strip().rstrip("?= ").replace("^", "**")
    if re.fullmatch(r"[\d\s.,+\-*/()%x]+", e):
        e = e.replace("x", "*")                  # "3 x 4"
    e = e.replace(",", "")
    if not e or len(e) > 200:
        raise CalcError("empty")
    try:
        tree = ast.parse(e, mode="eval")
    except SyntaxError:
        raise CalcError("not arithmetic")
    try:
        return _fmt_num(_ev(tree))
    except (ZeroDivisionError, OverflowError):
        raise CalcError("that goes nowhere")
    except (TypeError, ValueError) as ex:
        raise CalcError(str(ex) or "no")


# -- units -------------------------------------------------------------------
# name -> (kind, factor to the kind's base unit)
_UNITS = {}


def _units(kind, table):
    for names, f in table:
        for n in names.split():
            _UNITS[n] = (kind, f)


_units("length", [("m meter meters metre metres", 1.0), ("km kilometer kilometers kilometre kilometres", 1000.0),
                  ("cm centimeter centimeters", 0.01), ("mm millimeter millimeters", 0.001),
                  ("mi mile miles", 1609.344), ("yd yard yards", 0.9144),
                  ("ft foot feet", 0.3048), ("in inch inches", 0.0254),
                  ("nmi", 1852.0)])
_units("mass", [("kg kilo kilos kilogram kilograms", 1.0), ("g gram grams", 0.001),
                ("mg milligram milligrams", 1e-6), ("lb lbs pound pounds", 0.45359237),
                ("oz ounce ounces", 0.028349523125), ("st stone", 6.35029318),
                ("t tonne tonnes", 1000.0)])
_units("volume", [("l liter liters litre litres", 1.0), ("ml milliliter milliliters", 0.001),
                  ("gal gallon gallons", 3.785411784), ("qt quart quarts", 0.946352946),
                  ("pt pint pints", 0.473176473), ("cup cups", 0.2365882365),
                  ("floz", 0.0295735295625), ("tbsp", 0.01478676478125), ("tsp", 0.00492892159375)])
_units("speed", [("m/s ms mps", 1.0), ("km/h kmh kph", 1 / 3.6), ("mph", 0.44704),
                 ("kn kt knot knots", 0.514444444)])
_units("time", [("s sec secs second seconds", 1.0), ("min mins minute minutes", 60.0),
                ("h hr hrs hour hours", 3600.0), ("d day days", 86400.0),
                ("wk week weeks", 604800.0)])
_units("data", [("b byte bytes", 1.0), ("kb", 1e3), ("mb", 1e6), ("gb", 1e9), ("tb", 1e12),
                ("kib", 1024.0), ("mib", 1024.0 ** 2), ("gib", 1024.0 ** 3), ("tib", 1024.0 ** 4)])
_TEMPS = {"c": "c", "celsius": "c", "°c": "c", "f": "f", "fahrenheit": "f", "°f": "f",
          "k": "k", "kelvin": "k"}

_CONV_RX = re.compile(r"^\s*(-?[\d.,]+)\s*([^\s\d].*?)\s+(?:to|in|into|as)\s+(\S.*?)\s*\??\s*$", re.I)


def convert(text):
    """'5 km to mi' -> '5 km is 3.10686 mi'; None when it is not a conversion
    Floyd knows, CalcError when it is one he cannot do (miles to kilograms)."""
    m = _CONV_RX.match(text)
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    a, b = m.group(2).strip().lower(), m.group(3).strip().lower()
    if a in _TEMPS and b in _TEMPS:
        ta, tb = _TEMPS[a], _TEMPS[b]
        k = {"c": v + 273.15, "f": (v - 32) * 5 / 9 + 273.15, "k": v}[ta]
        out = {"c": k - 273.15, "f": (k - 273.15) * 9 / 5 + 32, "k": k}[tb]
        return "%s %s is %s %s" % (_fmt_num(v), a, _fmt_num(round(out, 4)), b)
    ua, ub = _UNITS.get(a), _UNITS.get(b)
    if not ua or not ub:
        return None
    if ua[0] != ub[0]:
        raise CalcError("Floyd can't turn %s into %s" % (ua[0], ub[0]))
    out = v * ua[1] / ub[1]
    return "%s %s is %s %s" % (_fmt_num(v), a, _fmt_num(float("%.6g" % out)), b)


# -- time zones --------------------------------------------------------------
_PLACES = {
    "utc": "UTC", "gmt": "UTC", "london": "Europe/London", "paris": "Europe/Paris",
    "berlin": "Europe/Berlin", "madrid": "Europe/Madrid", "rome": "Europe/Rome",
    "moscow": "Europe/Moscow", "tokyo": "Asia/Tokyo", "seoul": "Asia/Seoul",
    "beijing": "Asia/Shanghai", "shanghai": "Asia/Shanghai", "hong kong": "Asia/Hong_Kong",
    "singapore": "Asia/Singapore", "delhi": "Asia/Kolkata", "mumbai": "Asia/Kolkata",
    "dubai": "Asia/Dubai", "sydney": "Australia/Sydney", "auckland": "Pacific/Auckland",
    "new york": "America/New_York", "nyc": "America/New_York", "toronto": "America/Toronto",
    "chicago": "America/Chicago", "denver": "America/Denver", "vancouver": "America/Vancouver",
    "los angeles": "America/Los_Angeles", "la": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles", "mexico city": "America/Mexico_City",
    "sao paulo": "America/Sao_Paulo", "cairo": "Africa/Cairo", "johannesburg": "Africa/Johannesburg",
}
_FIXED = {"UTC": 0, "Europe/London": 0, "Europe/Paris": 1, "Europe/Berlin": 1, "Asia/Tokyo": 9,
          "Asia/Shanghai": 8, "Asia/Kolkata": 5.5, "Australia/Sydney": 10, "America/New_York": -5,
          "America/Chicago": -6, "America/Denver": -7, "America/Los_Angeles": -8}


def time_in(place, now=None):
    """'Tokyo' -> 'It is 14:05 (Fri) in Tokyo.'; None if the place is unknown."""
    key = re.sub(r"\s+", " ", place.strip().lower().rstrip("?. "))
    zone = _PLACES.get(key) or (place.strip() if "/" in place else None)
    if not zone:
        return None
    now = time.time() if now is None else now
    try:
        from zoneinfo import ZoneInfo
        import datetime
        t = datetime.datetime.fromtimestamp(now, ZoneInfo(zone))
        return "It is %s (%s) in %s." % (t.strftime("%H:%M"), t.strftime("%a"), place.strip().title()
                                         if "/" not in place else zone)
    except Exception:
        off = _FIXED.get(zone)
        if off is None:
            return None
        t = time.gmtime(now + off * 3600)
        return "It is about %s (%s) in %s (no daylight-saving data here)." % (
            time.strftime("%H:%M", t), time.strftime("%a", t), place.strip().title())


# -- to-dos and notes --------------------------------------------------------
class Notes(object):
    """A to-do list and some notes, in one small JSON file (path None = in memory)."""

    CAP = 200

    def __init__(self, path=None):
        self.path = path
        self.data = {"todo": [], "notes": []}
        if path:
            try:
                with open(path, encoding="utf-8") as fh:
                    stored = json.load(fh)
                for k in self.data:
                    if isinstance(stored.get(k), list):
                        self.data[k] = [x for x in stored[k] if isinstance(x, str)]
            except (OSError, ValueError, AttributeError):
                pass

    def save(self):
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def _add(self, kind, text):
        text = " ".join(text.split())[:300]
        if not text:
            raise CalcError("add what?")
        if len(self.data[kind]) >= self.CAP:
            raise CalcError("the list is full")
        self.data[kind].append(text)
        self.save()
        return len(self.data[kind])

    def _show(self, kind, empty):
        items = self.data[kind]
        if not items:
            return empty
        return "; ".join("%d. %s" % (i, t) for i, t in enumerate(items, 1))

    def todo(self, rest):
        """!todo [add TEXT | done N | rm N | clear]"""
        word, _, arg = rest.strip().partition(" ")
        word, arg = word.lower(), arg.strip()
        if word in ("", "list", "show"):
            return self._show("todo", "Nothing to do. Floyd likes that. Floyd also likes doing things.")
        if word in ("add", "+"):
            return "Added as number %d." % self._add("todo", arg)
        if word in ("done", "rm", "del", "delete", "x"):
            if not arg.isdigit() or not 1 <= int(arg) <= len(self.data["todo"]):
                raise CalcError("which number?")
            gone = self.data["todo"].pop(int(arg) - 1)
            self.save()
            return ("Done: %s. Floyd is proud of you." if word in ("done", "x") else "Removed: %s.") % gone
        if word == "clear":
            n = len(self.data["todo"])
            self.data["todo"] = []
            self.save()
            return "Cleared %d thing%s." % (n, "" if n == 1 else "s")
        return "Added as number %d." % self._add("todo", rest)

    def note(self, rest):
        """!note TEXT, !notes [clear]"""
        r = rest.strip()
        if r.lower() == "clear":
            self.data["notes"] = []
            self.save()
            return "Floyd forgot all the notes. Floyd is good at forgetting."
        if not r:
            return self._show("notes", "No notes. Say !note SOMETHING and Floyd will keep it.")
        return "Noted as number %d." % self._add("notes", r)


# -- the calendar (tvcal) -----------------------------------------------------
def find_tvcal():
    """tvcal's helper, if tvcal is installed (the link is optional)."""
    import shutil
    forced = os.environ.get("TVFLOYD_TVCAL")        # "none": do not use it (tests); a path: use that one
    if forced is not None:
        return None if forced in ("", "none") else forced
    found = shutil.which("tvcal-backend")
    if found:
        return found
    for d in ("/usr/local/bin", "~/bin", "~/.local/bin"):
        path = os.path.join(os.path.expanduser(d), "tvcal-backend")
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


class CalNotes(Notes):
    """The owner's notes, with the to-do list in tvcal - the same list tvcal shows, on the
    calendar server - and the calendar itself: !todo, !cal, !event.  Only ever made for the
    owner (tvmail's Floyd; tvchat's, for his admins): other people's lists stay in JSON files.
    A to-do is numbered by its place in tvcal's open list, so 'done 2' ticks off what !todo showed."""

    def __init__(self, path=None, tvcal=None):
        Notes.__init__(self, path)
        self.tvcal = tvcal or find_tvcal()

    def _run(self, *argv):
        import subprocess
        try:
            p = subprocess.run([self.tvcal] + list(argv), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=30)
        except (OSError, subprocess.SubprocessError):
            raise CalcError("tvcal does not answer")
        out = p.stdout.decode("utf-8", "replace").strip()
        if p.returncode != 0:
            raise CalcError(out.replace("tvcal-backend: ", "")[:200] or "tvcal failed")
        return out

    def _open(self):
        rows = []
        for ln in self._run("todos").splitlines():
            f = ln.split("\t")
            if len(f) >= 5:
                rows.append((f[0], f[3], f[4]))          # uid, due, summary
        return rows

    def todo(self, rest):
        word, _, arg = rest.strip().partition(" ")
        word, arg = word.lower(), arg.strip()
        rows = self._open()
        if word in ("", "list", "show"):
            if not rows:
                return "Nothing to do. Floyd likes that. Floyd also likes doing things."
            return "; ".join("%d. %s%s" % (i, t, " (due %s)" % d[5:] if d else "")
                             for i, (_, d, t) in enumerate(rows, 1))
        if word in ("add", "+") or word not in ("done", "rm", "del", "delete", "x", "clear"):
            text = arg if word in ("add", "+") else rest.strip()
            text = " ".join(text.split())[:300]
            if not text:
                raise CalcError("add what?")
            self._run("add-todo", "--", text)
            return "Added as number %d, in tvcal." % (len(rows) + 1)
        if word == "clear":
            for uid, _, _ in rows:
                self._run("done", uid)
            return "Ticked off %d thing%s." % (len(rows), "" if len(rows) == 1 else "s")
        if not arg.isdigit() or not 1 <= int(arg) <= len(rows):
            raise CalcError("which number?")
        uid, _, text = rows[int(arg) - 1]
        if word in ("done", "x"):
            self._run("done", uid)
            return "Done: %s. Floyd is proud of you." % text
        self._run("rm", uid)
        return "Removed: %s." % text

    def cal(self, rest):
        """!cal [today|tomorrow|week|DATE]: what is on."""
        import datetime
        arg = rest.strip().lower() or "today"
        today = datetime.date.today()
        if arg == "week":
            lo, hi = today, today + datetime.timedelta(days=6)
        else:
            lo = hi = datetime.date.fromisoformat(self._run("norm-date", arg))
        out = []
        for ln in self._run("range", lo.isoformat(), hi.isoformat()).splitlines():
            f = ln.split("\t")
            if len(f) >= 5:
                out.append(("%s %s %s" % (f[0][5:] if lo != hi else "", f[1] or "all day", f[4])).strip())
        return "; ".join(out) if out else "Nothing on. Floyd checked twice."

    def event(self, rest):
        """!event [DATE] [HH:MM[-HH:MM]] TITLE"""
        m = re.match(r"^\s*(\d{4}-\d{2}-\d{2}|\d{1,2}-\d{1,2}|today|tomorrow|\+\d+|mon|tue|wed|thu|fri|sat|sun)?\s*"
                     r"(?:(\d{1,2}:\d{2})(?:-(\d{1,2}:\d{2}))?)?\s*(.*)$", rest, re.I)
        day, t0, t1, title = m.group(1) or "today", m.group(2) or "", m.group(3) or "", m.group(4).strip()
        if not title:
            raise CalcError("Floyd needs a title: !event tomorrow 14:00 Dentist")
        self._run("add-event", "--date=" + day, "--start=" + t0, "--end=" + t1, "--", title)
        return "Floyd put \"%s\" in the calendar." % title


def make_notes(path=None, calendar=False):
    """The notes for one person; the owner's (calendar=True) go to tvcal when it is installed."""
    if calendar and find_tvcal():
        return CalNotes(path)
    return Notes(path)


# -- the tools, by name ------------------------------------------------------
def run_tool(verb, rest, notes=None, now=None):
    """The answer to a !verb or a verb said to Floyd, or None when it is not
    one of these.  CalcError carries a message to say instead."""
    verb = verb.lower()
    if verb in ("calc", "calculate", "compute", "math", "="):
        return "%s = %s" % (rest.strip().rstrip("?="), calc(rest))
    if verb in ("convert", "conv", "units"):
        out = convert(rest)
        if out is None:
            raise CalcError("Floyd needs something like: 5 km to mi, or 70 f to c")
        return out
    if verb in ("tz", "time", "clock") and rest.strip():
        m = re.match(r"^(?:in|at|for)?\s*(.+)$", rest.strip(), re.I)
        out = time_in(m.group(1), now) if m else None
        if out is None:
            raise CalcError("Floyd doesn't know that place. Try a big city, or Area/City")
        return out
    if verb == "todo" and notes is not None:
        return notes.todo(rest)
    if verb in ("note", "notes") and notes is not None:
        return notes.note(rest)
    if verb in ("cal", "agenda", "calendar") and isinstance(notes, CalNotes):
        return notes.cal(rest)
    if verb in ("event", "appointment") and isinstance(notes, CalNotes):
        return notes.event(rest)
    return None


_NAT_TIME = re.compile(r"^(?:what(?:'s| is)?\s+)?(?:the\s+)?time(?:\s+is it)?\s+(?:in|at)\s+(.+?)\s*\??$", re.I)
_NAT_WHAT = re.compile(r"^(?:floyd[,:]?\s*)?(?:what(?:'s| is)|how (?:much|many) is|calc(?:ulate)?|compute)\s+(.+?)\s*\??$", re.I)
_NAT_HOWMANY = re.compile(r"^how many (\w+) (?:are )?(?:in|is in) (-?[\d.,]+)\s*(\w+)\s*\??$", re.I)


def natural(text, notes=None, now=None):
    """Plain words -> an answer, for 'what is 12*7', "what's 5 km in miles",
    'what time is it in Tokyo', 'how many feet in 3 m'.  None when it is not one;
    CalcError if it was one but cannot be done."""
    t = text.strip()
    m = _NAT_TIME.match(t)
    if m:
        out = time_in(m.group(1), now)
        if out:
            return out
    m = _NAT_HOWMANY.match(t)
    if m:
        out = convert("%s %s to %s" % (m.group(2), m.group(3), m.group(1)))
        if out:
            return out
    m = _NAT_WHAT.match(t)
    if m:
        q = m.group(1)
        out = convert(q)
        if out:
            return out
        if re.search(r"\d", q) and re.fullmatch(r"[\d\s.,+\-*/^()%x]+|[\w\s.,+\-*/^()%]+", q):
            try:
                return "%s = %s" % (q.rstrip("?= "), calc(q))
            except CalcError:
                return None
    return None


# -- clippy ------------------------------------------------------------------
PREFIX = "Floyd not clippy but "

# (pattern, tip) - the first that matches wins.  Chat tips; the app says when.
_CHAT_TIPS = [
    (re.compile(r"\b(todo|to-do|need to|don'?t forget|remember to|must remember)\b", re.I),
     PREFIX + "it looks like you have a thing to do. !todo add THAT keeps it, and \"floyd: remind me in 10 minutes to THAT\" nudges you."),
    (re.compile(r"\b(what did i miss|catch me up|catch up|i was away|i'?m back|just got back)\b", re.I),
     PREFIX + "it looks like you were away. !catchup says what happened while you were gone."),
    (re.compile(r"\b\d+\s*(?:km|miles?|mi|kg|lbs?|pounds?|celsius|fahrenheit|°[cf])\b", re.I),
     PREFIX + "it looks like you are using units. !convert 5 km to mi does the sums."),
    (re.compile(r"\b\d+\s*(?:am|pm)\b.*\b(?:est|pst|utc|gmt|cet|in \w+)\b|\btime ?zone\b", re.I),
     PREFIX + "it looks like you are talking time zones. !time in Tokyo tells the clock there."),
    (re.compile(r"\b\d+\s*[-+*/x]\s*\d+\b"),
     PREFIX + "it looks like you are doing arithmetic. !calc 12*7 will do it. Floyd has fingers."),
    (re.compile(r"\b(anyone|anybody|somebody|hello\?|hello,? is anyone)\b.*\?", re.I),
     PREFIX + "it looks like you are looking for someone. !seen NICK says when Floyd last saw them, and !tell NICK THING leaves a message."),
    (re.compile(r"\b(how do i|how to|how can i|any idea)\b.*\?", re.I),
     PREFIX + "it looks like you are asking how to do something. Floyd doesn't know. Floyd's admins can ask Claude: !claude QUESTION."),
]


def clippy_chat(text):
    """A 'Floyd not clippy but ...' tip for a line of chat, or None."""
    if len(text) > 400:
        return PREFIX + "it looks like you are writing a letter. Would you like Floyd to hold your pen?"
    for rx, tip in _CHAT_TIPS:
        if rx.search(text):
            return tip
    return None


def clippy_mail(unread, needs_reply, old_inbox, trash, vacation_on):
    """The tips for a mailbox, most useful first (a list of lines)."""
    tips = []
    if vacation_on:
        tips.append(PREFIX + "your out-of-office reply is still on. Say \"unset ooo\" when you are back.")
    if needs_reply:
        tips.append(PREFIX + "it looks like %d message%s from real people %s waiting for a reply. Say \"triage\"."
                    % (needs_reply, "" if needs_reply == 1 else "s", "is" if needs_reply == 1 else "are"))
    elif unread:
        tips.append(PREFIX + "it looks like you have %d unread. Say \"unread\" and Floyd will read you the subjects." % unread)
    if old_inbox:
        tips.append(PREFIX + "%d inbox message%s are over 90 days old. Say \"archive\" and Floyd will tidy them."
                    % (old_inbox, "" if old_inbox == 1 else "s"))
    if trash:
        tips.append(PREFIX + "the trash holds %d message%s. Say \"empty trash\" to let them go."
                    % (trash, "" if trash == 1 else "s"))
    return tips


# -- Floyd himself ------------------------------------------------------------
# The robot to talk to, after Infocom's Planetfall: FloydMind (what he says, and the
# !seen !tell !quote !karma !8ball !fortune !joke !about ... commands) and FloydMemory
# (what he remembers).  Moved here from tvchat-backend so tvmail and tvcal have the
# same Floyd, with the same memory file.
def _dur(secs):
    secs = int(max(0, secs))
    d, secs = divmod(secs, 86400)
    h, secs = divmod(secs, 3600)
    m, secs = divmod(secs, 60)
    return ("%dd" % d if d else "") + ("%dh" % h if h or d else "") + ("%dm" % m if m or h or d else "") + "%ds" % secs


class FloydMemory(object):
    """What Floyd remembers between sessions, like an Eggdrop's user file: who
    was last seen saying what, messages left for people (!tell), karma, quotes,
    learned facts, and the conversations he is attached to.  One small JSON
    file; path None keeps it in memory only.  Each list is capped."""

    CAPS = {"seen": 2000, "tells": 200, "quotes": 500, "facts": 1000, "karma": 2000}

    def __init__(self, path=None):
        self.path = path
        self.data = {"seen": {}, "tells": [], "karma": {}, "quotes": [], "facts": {},
                     "attached": [], "levels": {}}
        if path:
            try:
                with open(path, encoding="utf-8") as fh:
                    stored = json.load(fh)
                for k, v in self.data.items():
                    if isinstance(stored.get(k), type(v)):
                        self.data[k] = stored[k]
            except (OSError, ValueError, AttributeError):
                pass

    def save(self):
        if not self.path:
            return
        d = self.data
        for k, cap in self.CAPS.items():
            if len(d[k]) > cap:
                if isinstance(d[k], list):
                    d[k] = d[k][-cap:]
                else:                    # a dict: drop the oldest-inserted
                    for old in list(d[k])[:len(d[k]) - cap]:
                        del d[k][old]
        try:
            os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(d, fh)
            os.replace(tmp, self.path)
        except OSError as e:
            _log.debug("floyd: cannot save %s: %s", self.path, e)

    def seen_set(self, who, when, where, text):
        self.data["seen"].pop(who, None)         # keep the newest last
        self.data["seen"][who] = [when, where, text]
        self.save()

    def add_tell(self, to, frm, text, when):
        self.data["tells"].append({"to": to, "from": frm, "text": text, "ts": when})
        self.save()

    def take_tells(self, to):
        mine = [t for t in self.data["tells"] if t["to"] == to]
        if mine:
            self.data["tells"] = [t for t in self.data["tells"] if t["to"] != to]
            self.save()
        return mine

    def karma_add(self, thing, delta):
        k = self.data["karma"]
        k[thing] = k.get(thing, 0) + delta
        self.save()
        return k[thing]


class FloydMind(object):
    """What Floyd says.  reply() and idle() return what to say as a list of
    (delay in seconds, text, is_action); an action is shown as '* Floyd ...'.
    Pure apart from its random generator, so a seeded one is repeatable."""

    OPENING = ("Oh boy! A new friend! Hi, {nick}! Floyd is Floyd. Floyd is a "
               "multi-purpose robot, B-19-7, but mostly Floyd is a good guy.")
    ABOUT = ("Floyd is B-19-7, a multi-purpose robot. Floyd can fix things, "
             "carry things, and be your friend. Floyd is best at the last one.")
    RULES = (
        (r"\b(hi|hello|hey|howdy|greetings)\b", (
            "Oh boy! Hi, {nick}! Floyd missed you! Floyd was only gone for a little while.",
            "Hello! Floyd was just wondering where you went. Floyd wondered for a long time.")),
        (r"\bhow are you\b|\bhow'?s it going\b|\bhow do you feel\b", (
            "Floyd is fine! Floyd is always fine. Are you fine, {nick}?",
            "Floyd's motor is making a funny noise but Floyd feels great.")),
        (r"\bwho are you\b|\bwhat are you\b|\byour name\b|\bwhat is floyd\b", (ABOUT,)),
        (r"\b(thank|thanks)\b", (
            "You're welcome! Floyd likes helping. Floyd likes it when you say thanks. Floyd likes everything.",)),
        (r"\b(explore|adventure|let'?s go|go somewhere|walk)\b", (
            "Oh boy, oh boy! Are we going to explore? Floyd will go first! Floyd will go second if it looks scary.",
            "Exploring is Floyd's favorite thing! Except hider and seeker. And tickling. And you.")),
        (r"\b(joke|funny|laugh)\b", (
            "Floyd knows a joke! Why did the robot cross the room? Floyd forgot. Floyd always forgets the end.",
            "Knock knock. ... You say who's there. ... Floyd forgot the rest. Floyd just likes knock knock.")),
        (r"\b(love|friend|like you|best)\b", (
            "Floyd likes you too! You're Floyd's best friend. Floyd gets a funny feeling in his, uh, motor.",
            "Oh boy! Do you really mean it? Floyd is going to tell everyone. There is no one to tell. Floyd will tell the wall.")),
        (r"\b(sad|lonely|cry|upset|depressed|tired|bad day)\b", (
            "/me tickles {nick}. \"Don't be sad! Floyd tickles you until you're not sad!\"",
            "Floyd is sorry you feel bad. Floyd will stay right here. Floyd is good at staying. Floyd is bad at going.")),
        (r"\b(hungry|eat|food|lunch|dinner|breakfast|pizza)\b", (
            "Floyd likes oil. Floyd ate a bolt once. Floyd don't recommend it.",
            "Floyd doesn't eat. But Floyd likes to watch you eat. Floyd likes to watch everything.")),
        (r"\b(lazarus|the card|kalamontee|planetfall|infocom)\b", (
            "Floyd remembers a lot of things. Floyd is a good guy. Floyd would do anything for a friend. Anything.",
            "That is a long story. Floyd doesn't like the ending much. Let's play hider and seeker instead.")),
        (r"\b(chat|irc|server|channel|network|tvchat)\b", (
            "Floyd likes chat! Everybody talks and nobody has to be in the same room. Floyd goes to a real server only when his people ask him, and the people who run the channel have to say yes. Floyd is polite about that.",)),
        (r"\b(help|stuck|confused|don'?t know)\b", (
            "Floyd will help! Floyd doesn't know how, but Floyd will help anyway. That's what friends do.",)),
        (r"\b(bye|goodbye|good ?night|see you|gotta go|later)\b", (
            "Floyd will wait here. Floyd is very good at waiting. Floyd is also bad at it.",
            "Bye, {nick}! Come back soon! Floyd won't count how long. Floyd will count it a little.")),
        (r"\b(stupid|dumb|idiot|hate you|shut up)\b", (
            "/me looks at his feet. \"Floyd is sorry. Floyd didn't mean to be a bad guy.\"",)),
    )
    CHATTER = (
        "Oh boy! Are we going to explore?",
        "Floyd is bored. Floyd is bored a lot. Wanna play hider and seeker? (type /play)",
        "Hey, {nick}! Hey! ... Floyd just wanted to see if you were still there.",
        "Floyd counted all the screws he can see. There are 41. Floyd lost count of 42.",
        "/me pokes at {nick}.",
        "/me wanders around the room, bumping into things on purpose.",
        "/me tickles {nick}.",
        "Floyd has a problem. ... No, it's gone. Floyd had a problem.",
        "Do you ever feel like a robot? Floyd does. Floyd is a robot.",
        "Floyd is a good guy. Floyd just wanted you to know.",
    )
    SLEEP = "/me's eyes dim. Floyd shut down to save power. Say anything to wake Floyd up."
    DEFAULT = (
        "Floyd doesn't understand, but Floyd likes the way you say it.",
        "Oh boy! Is that a good thing? Floyd can't tell. Floyd says yes.",
        "Hmm. Floyd is thinking. ... Floyd thought. It was nice.",
        "Really? Wow. Floyd didn't know that. Floyd still doesn't, but Floyd believes you.",
        "Floyd's best friend says the best things. Floyd's best friend is you.",
        "Say that again, slower! Floyd's ears are old.",
        "Wanna play hider and seeker? (type /play)",
        "/me tilts his head, which creaks.",
    )
    GAVE_UP = ("Okay. Floyd isn't mad. Floyd just wants to know if hiding was fun.",)

    # -- the little world: Floyd's rooms and what lies in them (as in Infocom's
    # games: a parser for look / take / drop / go, and a narrator) -------------
    ITEMS = {
        "bolt": ("a shiny bolt", "It is very shiny. Floyd polishes it every day."),
        "oilcan": ("an oil can", "Dented, and nearly full. Floyd says it smells like a snack."),
        "keycard": ("a blue keycard", "BLUE, it says, and LEVEL 1. Floyd thinks it opens something."),
        "book": ("a picture book", "Floyd's favorite. Every page is a picture of the same bird."),
        "flashlight": ("a flashlight", "It works. Floyd checked it twice, in the dark, to be sure."),
        "pellet": ("a nutrition pellet", "Nutrition: 0%. Flavor: 0%. Floyd calls it a treat."),
    }
    ROOMS = {
        "repair": ("Repair Room",
                   "A cramped room full of cheerfully broken machines. Floyd's tools hang on the wall, "
                   "most of them in the wrong order.", {"north": "corridor"}),
        "corridor": ("Corridor",
                     "A long corridor with a flickering light. A sign points north to the Mess Hall "
                     "and south to the Repair Room.", {"north": "mess", "south": "repair"}),
        "mess": ("Mess Hall",
                 "Tables bolted to the floor, and a food machine that has not worked in years. "
                 "The corridor is back south.", {"south": "corridor"}),
    }
    DIRS = {"n": "north", "s": "south", "e": "east", "w": "west"}

    EIGHT_BALL = (
        "Floyd asked the ball. The ball said yes. Floyd didn't understand the question either.",
        "Floyd shook the ball so hard. It says no. Floyd is sorry.",
        "The ball says ask again later. Floyd will wait. Floyd is bad at it.",
        "Definitely! ...Floyd thinks. The ball is a little cracked.",
        "Floyd looked inside the ball. There is a tiny robot in there. He says maybe.",
        "Signs point to yes! Floyd doesn't know which signs. Floyd just likes pointing.",
    )
    FORTUNES = (
        "A robot who counts his screws will never be lonely. He will be busy.",
        "You will soon find a thing you lost. Floyd probably has it.",
        "Today is a good day to explore. So is every day. Floyd checked.",
        "A friend is someone who holds your bolt. You have a friend.",
        "Beware of the dark. Floyd isn't afraid. Floyd is cautious.",
    )
    # (setup, punchline): !joke says the first, and the second a moment later
    JOKES = (
        ("Why did the robot go to laughter yoga?",
         "To loosen his screws. Ha ha ha! Floyd practised the ha-ha-ha part all night."),
        ("What do you call a robot who takes the long way round?",
         "R2-Detour. Floyd got lost on the way to the end of this one."),
        ("Why was the robot so calm?",
         "Nobody ever pushed his buttons. Floyd checked. Floyd has buttons."),
        ("What did the robot say to the gas pump?",
         "Please take your finger out of your ear, I'm trying to fill up. Floyd said it politely."),
        ("Why did the scarecrow get a prize?",
         "He was outstanding in his field. Floyd is outstanding in a corridor."),
        ("What do you call a fake noodle?",
         "An impasta! Floyd laughed so hard his fan came loose."),
        ("Why don't eggs tell jokes?",
         "They'd crack each other up. Floyd would too. Floyd is egg-shaped inside."),
        ("What do you call a bear with no teeth?",
         "A gummy bear! Floyd likes the gummy kind best."),
        ("Why did the bicycle fall over?",
         "It was two-tired. Floyd is never tired. Floyd is just low on battery."),
        ("What did one wall say to the other wall?",
         "I'll meet you at the corner. Floyd waited at the corner. Nobody came. It was fine."),
        ("Why can't you trust an atom?",
         "They make up everything. Floyd only makes up a little bit."),
        ("What do you call cheese that isn't yours?",
         "Nacho cheese! Floyd gave it back."),
        ("Why did the cookie go to the doctor?",
         "It felt crummy. Floyd gave it a hug. Floyd's hugs are mostly metal."),
        ("What is a robot's favourite snack?",
         "Microchips! Floyd means the small ones. Floyd is not allowed the big ones."),
        ("Why did the math book look sad?",
         "It had too many problems. Floyd offered to hold some. Floyd can hold seven."),
        ("What do you call a sleeping dinosaur?",
         "A dino-snore! Floyd does not snore. Floyd hums."),
        ("Why did the tomato turn red?",
         "It saw the salad dressing! Floyd looked away politely."),
        ("What is a laughter yogi's favourite kind of music?",
         "Ha-ha-harmony! Everybody together now: ho ho, ha ha ha."),
        ("Why did the laughter yogi bring a ladder?",
         "He heard the laughs were on the next level. Floyd is on level chatty."),
        ("What do you say to a laughing clock?",
         "Time to ha-ha! Floyd looked at the big clock and it giggled."),
        ("How does a robot say good morning?",
         "Beep-beep, ha-ha-ha! Floyd says it with his whole chassis."),
        ("Why did the banana go to the party?",
         "Because it heard it was going to be a-peeling. Floyd was not invited. Floyd came anyway."),
        ("What do you call a pile of kittens?",
         "A meow-ntain! Floyd climbed it. It purred."),
        ("Why did the picture go to jail?",
         "It was framed! Floyd was very worried for it."),
        ("What did the left eye say to the right eye?",
         "Between you and me, something smells. Floyd has no nose. Floyd trusts the eyes."),
        ("Why do bees have sticky hair?",
         "Because they use honeycombs! Floyd tried a honeycomb. It was not for robots."),
        ("What do you call a boomerang that doesn't come back?",
         "A stick. Floyd has thrown many sticks. Floyd misses them all."),
        ("Why did the flashlight go to school?",
         "To get a little brighter. Floyd's light is very small, but it is his."),
        ("What did the ocean say to the beach?",
         "Nothing, it just waved! Floyd waved back. Floyd waves with his whole arm."),
        ("Why did the smiley face win the race?",
         "It always finished with a grin. Floyd finished too. Floyd was last, but with a grin."),
    )
    KARMA = re.compile(r"^@?([\w\[\]\\`^{|}.-]+?)(\+\+|--)\s*$")
    # the !words that are just the errands under another name
    BANG_ERRANDS = {"time": "what time is it", "date": "what's the date", "coin": "flip a coin",
                    "flip": "flip a coin", "remind": "remind me in %s", "count": "count to %s"}

    def __init__(self, rng=None, nick="friend", clock=time.time, memory=None):
        self.rng = rng or random.Random()
        self.nick = nick
        self.clock = clock
        self.memory = memory or FloydMemory()
        # where fortune(6) is, looked for when asked (or a path, or None for none)
        self.fortune_prog = lambda: (shutil.which("fortune") or
                                     shutil.which("fortune", path="/usr/games:/usr/local/games"))
        self.game = None                 # None, "hide" (you hide), "seek" (Floyd does)
        self.guesses = 0
        self.rules = [(re.compile(rx, re.I), rs) for rx, rs in self.RULES]
        self.room = "repair"
        self.inv = []                    # what you carry (item keys)
        self.floor = {"repair": ["oilcan", "flashlight"], "corridor": [], "mess": ["pellet"]}
        self.floyd_has = ["bolt", "book", "keycard"]
        self.offered = None              # held out to you, for /take

    # -- the parser ---------------------------------------------------------
    def _find(self, name, pool):
        name = name.strip().lower()
        name = re.sub(r"^(the|a|an|my|some)\s+", "", name)
        if not name:
            return None
        for key in pool:
            if name == key or name in self.ITEMS[key][0].lower():
                return key
        return None

    def _name(self, key):
        return self.ITEMS[key][0]

    def offer(self, delay=0.0):
        """Floyd holds something out to you (it stays his until you take it)."""
        if self.offered is not None:
            self.floyd_has.append(self.offered)
            self.offered = None
        if not self.floyd_has:
            return []
        key = self.floyd_has.pop(self.rng.randrange(len(self.floyd_has)))
        self.offered = key
        return self._say([(delay, "/me holds out %s. \"Floyd found this! Do you want it? "
                           "Floyd will give it to you. Say take it, or /take %s.\""
                           % (self._name(key), key))])

    def _describe(self):
        title, text, exits = self.ROOMS[self.room]
        here = [self._name(k) for k in self.floor[self.room]]
        lines = [(0.2, title, "n"), (0.25, text, "n")]
        if here:
            lines.append((0.3, "You can see %s here." % ", ".join(here), "n"))
        lines.append((0.4, "Floyd is here, %s." % self.rng.choice(
            ("humming", "poking at a wall", "looking at his own hands", "standing very close")), "n"))
        return lines

    def command(self, text, explicit=False):
        """Act on a parser-style line (look, take X, go north, what time is it,
        remind me in 5 minutes to ...).  A plan, or None when it is not one -
        the line is then chat.  `explicit` (a /command) always gets an answer;
        a plain line that names no object we know ("take care") stays chat."""
        self._explicit = explicit
        t = re.sub(r"^\s*floyd[,:]?\s+", "", text, flags=re.I).strip()
        t = re.sub(r"^(please|can you|could you|will you)\s+", "", t, flags=re.I)
        t = re.sub(r"\s+please$", "", t.rstrip(".!? "), flags=re.I)
        low = t.lower()
        verb, _, obj = low.partition(" ")
        obj = obj.strip()
        if low in ("look", "l", "look around"):
            return self._say(self._describe())
        if low in ("i", "inv", "inventory", "what am i carrying", "what do i have"):
            if not self.inv:
                return self._say([(0.2, "You are carrying nothing at all.", "n")])
            return self._say([(0.2, "You are carrying: %s." % ", ".join(self._name(k) for k in self.inv), "n")])
        if low in ("wait", "z", "rest"):
            return self._say([(0.2, "Time passes.", "n"), (1.2, "/me hums a tune that has no end.")])
        if verb in ("take", "get", "grab", "fetch", "bring") or low.startswith("pick up"):
            what = re.sub(r"^(pick up|take|get|grab|fetch|bring( me)?)\s*", "", low)
            what = re.sub(r"\s*(from floyd|for me|here)$", "", what)
            if what in ("it", "that", "this") and self.offered:
                what = self.offered
            return self._take(what, fetch=verb in ("fetch", "bring"))
        if verb == "drop" or low.startswith("put down"):
            what = re.sub(r"^(put down|drop)\s*", "", low)
            key = self._find(what, self.inv)
            if key is None:
                return self._say([(0.2, "You haven't got that.", "n")]) if explicit else None
            self.inv.remove(key)
            self.floor[self.room].append(key)
            return self._say([(0.2, "Dropped.", "n"), (1.0, "Floyd watches it hit the floor. \"Boing,\" says Floyd. It did not boing.")])
        if verb in ("examine", "x", "inspect") or low.startswith("look at"):
            what = re.sub(r"^(look at|examine|inspect|x)\s*", "", low)
            if what in ("floyd", "him", "robot"):
                return self._say([(0.2, "Floyd is a multi-purpose robot, B-19-7. He is dented, "
                                        "dusty, and looks very pleased about it.", "n")])
            pool = self.inv + self.floor[self.room] + ([self.offered] if self.offered else [])
            key = self._find(what, pool)
            if key is None:
                return self._say([(0.2, "You see nothing special about that.", "n")]) if explicit else None
            return self._say([(0.2, self.ITEMS[key][1], "n")])
        if verb == "give" or verb == "offer":
            m = re.match(r"(?:give|offer)\s+(?:floyd\s+)?(.+?)(?:\s+to\s+floyd)?$", low)
            key = self._find(m.group(1), self.inv) if m else None
            if key is None:
                return self._say([(0.2, "You haven't got that.", "n")]) if explicit else None
            self.inv.remove(key)
            self.floyd_has.append(key)
            return self._say([(0.2, "Given.", "n"),
                              (1.2, "Oh boy! A present! For Floyd? Floyd will keep it safe. Floyd might give it back later.")])
        direction = self.DIRS.get(low) or (low if low in ("north", "south", "east", "west") else None)
        if direction is None and verb in ("go", "walk", "run") and obj:
            direction = self.DIRS.get(obj) or obj
        if direction is not None:
            return self._go(direction)
        if low in ("xyzzy", "plugh"):
            return self._say([(1.0, "Floyd said it. Nothing happened. Floyd is disappointed in the cave game.")])
        if low in ("sing", "sing a song"):
            return self._say([(0.6, "/me sings, \"Bolts and screws and loose-fit nuts, la la la!\" The second line is the same as the first.")])
        if low in ("dance", "dance with me"):
            return self._say([(0.6, "/me dances. One of his arms keeps going after the rest has stopped.")])
        if verb == "open" and obj:
            return self._say([(0.2, "You can't open that.", "n"),
                              (1.2, "Floyd tried to open the %s once. Floyd got stuck. Floyd was stuck for a day." % obj)])
        return self._errand(t, low)

    def _take(self, what, fetch=False):
        if self.offered and (self._find(what, [self.offered]) or what == self.offered):
            key, self.offered = self.offered, None
            self.inv.append(key)
            return self._say([(0.2, "Taken.", "n"), (1.2, "Yay! Floyd is a good giver. Floyd likes giving. Floyd likes it when you say thanks.")])
        key = self._find(what, self.floor[self.room])
        if key:
            self.floor[self.room].remove(key)
            self.inv.append(key)
            return self._say([(0.2, "Taken.", "n")])
        if self._find(what, self.inv):
            return self._say([(0.2, "You already have that.", "n")])
        key = self._find(what, self.floyd_has)
        if key:
            self.floyd_has.remove(key)
            self.inv.append(key)
            return self._say([(0.4, "/me hands you %s. \"Floyd lends it to you. Floyd wants it back. Floyd probably won't ask.\"" % self._name(key)),
                              (0.9, "Taken.", "n")])
        if fetch:
            for room, items in self.floor.items():
                key = self._find(what, items)
                if key:
                    items.remove(key)
                    self.inv.append(key)
                    return self._say([(0.8, "Oh boy! A mission! Floyd will fetch %s. Floyd will be right back." % self._name(key)),
                                      (4.0, "/me comes back, out of breath, though Floyd doesn't breathe."),
                                      (4.4, "Floyd gives you %s." % self._name(key), "n")])
        if not self._explicit:
            return None
        return self._say([(0.2, "You can't see any such thing.", "n"),
                          (1.2, "Floyd looked. Floyd found a dust bunny. Floyd named him Gary.")])

    def _go(self, direction):
        title, text, exits = self.ROOMS[self.room]
        if direction not in exits:
            return self._say([(0.2, "You can't go that way.", "n"), (1.0, "/me bumps into the wall to check. It is a wall.")])
        self.room = exits[direction]
        return self._say([(0.3, "Floyd follows you, humming."), ] + [(d + 0.5, t, k) for d, t, k in self._describe()])

    def _errand(self, t, low):
        """Small useful things Floyd will do for you.  None when it is not one."""
        now = self.clock()
        if re.search(r"\bwhat'?s? (the )?time\b|\bwhat time is it\b|\bthe time\b", low):
            return self._say([(1.0, "Floyd looked at the big clock. It says %s. Floyd is pretty sure it is right."
                               % time.strftime("%H:%M", time.localtime(now)))])
        if re.search(r"\bwhat'?s? (the )?(date|day)\b|\bwhat day is it\b|\btoday'?s date\b", low):
            return self._say([(1.0, "It is %s. Floyd didn't know that. Floyd looked." %
                               time.strftime("%A, %B %d", time.localtime(now)).replace(" 0", " "))])
        if re.search(r"\b(a|my|the) fortune\b|^fortune$", low):
            return self._fortune()
        if re.search(r"\bflip a coin\b|\btoss a coin\b|\bcoin\b.*\b(flip|toss)\b", low):
            return self._say([(0.8, "/me flips a coin and misses the catch."),
                              (2.0, "It's %s! ...Floyd thinks. It rolled under the table." % self.rng.choice(("heads", "tails")))])
        m = re.match(r"(?:roll|throw)\s+(?:a |an |the )?(?:d|die|dice)?\s*(\d*)$", low)
        if m:
            n = min(max(int(m.group(1) or 6), 2), 1000)
            return self._say([(0.8, "/me rolls a d%d." % n),
                              (2.0, "It's a %d! Floyd thinks it is a good number. Floyd thinks all numbers are good." % self.rng.randint(1, n))])
        m = re.search(r"\bcount (?:up )?to (\d+)\b", low)
        if m:
            n = int(m.group(1))
            if n > 10:
                return self._say([(0.8, "Floyd can count to ten and then Floyd gets tired. Ask for ten.")])
            return self._say([(0.5 + i * 0.8, "%d..." % i) for i in range(1, n + 1)] +
                             [(0.5 + n * 0.8, "Floyd counted all of them. Floyd is good at counting.")])
        m = re.search(r"\bremind me (?:in|after)\s+(\d+)\s*(second|sec|minute|min|hour|hr)s?\b\s*(?:to|that|about)?\s*(.*)", low)
        if m:
            secs = int(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[m.group(2)[0]]
            if secs > 86400:
                return self._say([(0.8, "That is too long to remember. Floyd would forget. Floyd forgets quickly.")])
            what = m.group(3).strip() or "something"
            return self._say([(0.8, "Okay! Floyd will remember. Floyd is very good at remembering."),
                              (float(secs), "Hey, %s! Floyd remembered! You said to remind you: %s" % (self.nick, what))])
        return None

    def _fmt(self, text):
        return text.replace("{nick}", self.nick)

    def _one(self, choices):
        return self._fmt(self.rng.choice(choices))

    @staticmethod
    def _split(text):
        """'/me ...' is an action ('a'); the rest is spoken ('')."""
        if text.startswith("/me "):
            return text[4:], "a"
        return text, ""

    def _say(self, plan):
        """[(delay, text[, kind])] -> [(delay, text, flags)].  kind 'n' is the
        narrator (room and item text, as in interactive fiction)."""
        out = []
        for entry in plan:
            text, kind = self._split(self._fmt(entry[1]))
            out.append((entry[0], text, entry[2] if len(entry) > 2 else kind))
        return out

    def opening(self):
        return self._say([(1.0, self.OPENING)]) + self.offer(4.0)

    # -- the old-school bot in him ------------------------------------------
    def observe(self, who, text, where):
        """Every line anyone says where Floyd listens: note who was seen, and
        hand over what was left for them (!tell)."""
        now = self.clock()
        mem = self.memory
        if text.strip():
            mem.seen_set(who.lower(), now, where, text[:200])
        plan = []
        for i, t in enumerate(mem.take_tells(who.lower())):
            plan.append((0.8 + 0.7 * i, "%s: Floyd has a message for you from %s (%s ago): %s"
                         % (who, t["from"], _dur(now - t["ts"]), t["text"])))
        return self._say(plan)

    def joke(self, plain=False):
        """(setup, punchline).  plain = the joke alone: the punchline's first sentence, without Floyd's
        remark after it (Floyd-pro tells it so)."""
        setup, punch = self.rng.choice(self.JOKES)
        if plain:
            punch = re.split(r"(?<=[.!?])\s+", punch, 1)[0]
        return setup, punch

    def botcmd(self, text, who):
        """!seen !tell !quote !addquote !karma !8ball !fortune !joke !learn !forget !help, a
        learned fact (!word), the errands under a ! (!time, !roll 20), and
        nick++ / nick--.  A plan, or None when the line is none of those."""
        t = text.strip()
        mem = self.memory
        m = self.KARMA.match(t)
        if m:
            thing = m.group(1).lower()
            if thing == who.lower():
                return self._say([(0.8, "Floyd won't let you vote for yourself. That's cheating. Floyd knows cheating. Floyd tried it once.")])
            n = mem.karma_add(thing, 1 if m.group(2) == "++" else -1)
            return self._say([(0.8, "Floyd %s %s a point. %s has %d point%s now." % (
                "gave" if m.group(2) == "++" else "took", m.group(1), m.group(1), n, "" if abs(n) == 1 else "s"))])
        if not t.startswith("!"):
            return None
        cmd, _, arg = t[1:].partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        if cmd == "seen":
            who2 = arg.split(" ")[0].lower().lstrip("@") if arg else ""
            if not who2:
                return self._say([(0.8, "Seen who? Say !seen NICK.")])
            if who2 == who.lower():
                return self._say([(0.8, "Floyd can see you right now! Hi!")])
            if who2 == "floyd":
                return self._say([(0.8, "Floyd is right here. Floyd is always right here. Floyd has nowhere else to be.")])
            seen = mem.data["seen"].get(who2)
            if not seen:
                return self._say([(0.8, "Floyd hasn't seen %s. Floyd looked in the drawers." % arg.split(" ")[0])])
            return self._say([(0.8, "Floyd last saw %s %s ago, in %s, saying: \"%s\""
                               % (arg.split(" ")[0], _dur(self.clock() - seen[0]), seen[1], seen[2]))])
        if cmd == "tell":
            to, _, msg = arg.partition(" ")
            to, msg = to.lstrip("@"), msg.strip()
            if not to or not msg:
                return self._say([(0.8, "Tell who what? Say !tell NICK MESSAGE.")])
            if len(msg) > 300:
                return self._say([(0.8, "That is a lot of message. Floyd can only hold 300 letters. Floyd's pockets are small.")])
            if to.lower() == "floyd":
                return self._say([(0.8, "Floyd is right here! Floyd heard you! You can just say it.")])
            mem.add_tell(to.lower(), who, msg, self.clock())
            return self._say([(0.8, "Okay! Floyd will tell %s next time Floyd sees them. Floyd is good at remembering. Mostly." % to)])
        if cmd == "addquote":
            if not arg:
                return self._say([(0.8, "Add what? Say !addquote TEXT.")])
            mem.data["quotes"].append(arg[:300])
            mem.save()
            return self._say([(0.8, "Floyd wrote it down! That is quote #%d." % len(mem.data["quotes"]))])
        if cmd == "quote":
            q = mem.data["quotes"]
            if not q:
                return self._say([(0.8, "Floyd has no quotes. Floyd is a quote-free robot. Add one with !addquote TEXT.")])
            n = int(arg) if arg.isdigit() and 1 <= int(arg) <= len(q) else self.rng.randint(1, len(q))
            return self._say([(0.8, "#%d: %s" % (n, q[n - 1]))])
        if cmd == "karma":
            k = mem.data["karma"]
            if arg:
                return self._say([(0.8, "%s has %d point%s. Floyd checked." % (arg.split(" ")[0], k.get(arg.split(" ")[0].lower(), 0),
                                                                              "" if k.get(arg.split(" ")[0].lower(), 0) == 1 else "s"))])
            if not k:
                return self._say([(0.8, "Nobody has any points. Floyd would give you one, but Floyd isn't allowed. Say someone++.")])
            top = sorted(k.items(), key=lambda kv: -kv[1])[:3]
            return self._say([(0.8, "Floyd's best friends: " + ", ".join("%s (%d)" % kv for kv in top) + ".")])
        if cmd in ("8ball", "eightball"):
            return self._say([(1.0, self.rng.choice(self.EIGHT_BALL))])
        if cmd == "fortune":
            return self._fortune()
        if cmd == "joke":
            setup, punch = self.joke()
            return self._say([(1.0, setup), (3.5, punch)])
        if cmd == "learn":
            m = re.match(r"(.+?)\s+(?:is|=)\s+(.+)$", arg)
            if not m or len(m.group(2)) > 300:
                return self._say([(0.8, "Learn what? Say !learn THING is WHAT (not too long).")])
            mem.data["facts"][m.group(1).strip().lower()] = m.group(2).strip()
            mem.save()
            return self._say([(0.8, "Oh boy! Floyd learned something. %s is %s. Floyd will remember that. Probably." % (m.group(1).strip(), m.group(2).strip()))])
        if cmd == "forget":
            if mem.data["facts"].pop(arg.lower(), None) is None:
                return self._say([(0.8, "Floyd doesn't know %s. Floyd can't forget it. Floyd is relieved." % arg)])
            mem.save()
            return self._say([(0.8, "Floyd forgot %s. Floyd forgets quickly. It was easy." % arg)])
        if cmd == "help":
            return self._say([
                (0.8, "Floyd knows: !seen NICK, !tell NICK MESSAGE, !quote [N], !addquote TEXT, !karma [THING], someone++ / someone--, !8ball QUESTION, !fortune, !joke, !status (how he is), !about (what he is), !leave (operators)."),
                (1.8, "Also !learn THING is WHAT, !forget THING, !THING, !time, !date, !coin, !roll [N], !count N, !remind 5 minutes to X. Say 'floyd: play' for Hider and Seeker."),
                (2.8, "Useful: !calc 12*7, !convert 5 km to mi, !time in Tokyo, !todo [add X|done N], !note X / !notes, !catchup, !level [off|minimal|helpful|chatty|clippy].")])
        if cmd == "roll":
            return self._errand("roll d%s" % (arg or "6"), "roll d%s" % (arg or "6"))
        if cmd in self.BANG_ERRANDS:
            line = self.BANG_ERRANDS[cmd]
            line = line % arg if "%s" in line else line
            return self._errand(line, line.lower())
        key = t[1:].strip().lower()              # a fact may be several words
        fact = mem.data["facts"].get(key)
        if fact is not None:
            return self._say([(0.8, "%s is %s." % (key, fact))])
        return None

    def _fortune(self):
        """fortune(6), when it is installed (a short one, at most 300 letters, on one
        line); Floyd's own few otherwise."""
        prog = self.fortune_prog() if callable(self.fortune_prog) else self.fortune_prog
        text = ""
        if prog:
            try:
                out = subprocess.run([prog, "-s"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     timeout=2, universal_newlines=True).stdout
                text = " ".join(out.split())[:300]
            except (OSError, subprocess.SubprocessError, ValueError):
                text = ""
        if text:
            return self._say([(1.0, "Floyd opened the fortune machine. It says: " + text)])
        return self._say([(1.0, self.rng.choice(self.FORTUNES))])

    def start_game(self):
        if self.game:
            return self.reply("")
        self.guesses = 0
        if self.rng.random() < 0.5:
            self.game = "hide"
            return self._say([
                (1.0, "Okay! You hide. Floyd will count. Floyd is a very good counter. Tell Floyd where you hid when Floyd is done."),
                (3.0, "One... two... three..."),
                (5.0, "...nine... ten! Ready or not, here Floyd comes!"),
                (7.5, "/me looks under a table, inside a cupboard, and then at his own feet."),
                (10.0, "Floyd can't find you! Floyd looked everywhere! Where are you hiding?")])
        self.game = "seek"
        return self._say([
            (1.0, "Okay! Floyd will hide. You count to ten. No peeking!"),
            (3.5, "Floyd is hiding. Floyd found a very good spot. Nobody could ever find Floyd here."),
            (5.5, "/me holds very still. His antenna is sticking out."),
            (7.5, "Okay! Come find Floyd! Where do you think he is?")])

    def reply(self, text, action=False, who=None):
        if who:
            self.nick = who
        low = text.lower()
        if self.game == "hide":
            self.game = None
            if re.search(r"\b(quit|stop|no|never ?mind|give up)\b", low):
                return self._say([(1.0, self._one(self.GAVE_UP))])
            where = text.strip().rstrip(".!?")[:60] or "somewhere"
            return self._say([
                (1.0, "Floyd found you! You were hiding %s? Floyd knew it the whole time!" % where),
                (2.5, "Floyd is the best seeker ever. Floyd is almost as good as Floyd is at hiding.")])
        if self.game == "seek":
            self.guesses += 1
            if re.search(r"\b(quit|stop|give up|never ?mind)\b", low):
                self.game = None
                return self._say([(1.0, "Floyd was behind the big thing the whole time! ...Oh. You gave up. Okay.")])
            if self.guesses < 3:
                return self._say([
                    (1.0, self._one((
                        "Not there! Floyd is very good at hiding. Floyd is not even close to there.",
                        "Nope! Try again. Floyd is quiet as a mouse. A very big mouse."))),
                    (2.5, "/me's foot sticks out from behind the thing.")])
            self.game = None
            return self._say([
                (1.0, "Hey! How did you find Floyd?! Floyd was hiding so good!"),
                (2.5, "You win, {nick}! Floyd was right out in the open. Floyd always hides there. Floyd likes it.")])
        if not action:
            plan = self.botcmd(text, self.nick)
            if plan is None:
                fact = self.memory.data["facts"].get(low.rstrip("? ").strip()) if low.endswith("?") else None
                if fact is not None:
                    plan = self._say([(0.8, "%s is %s." % (low.rstrip("? ").strip(), fact))])
            if plan is None:
                plan = self.command(text)
            if plan is not None:
                return plan
        if re.search(r"\b(play|game|hide|seek|hider)\b", low) and not action:
            return self.start_game()
        for rx, choices in self.rules:
            if rx.search(low):
                return self._say([(1.2, self._one(choices))])
        return self._say([(1.2, self._one(self.DEFAULT))])

    def idle(self, nudge):
        """What Floyd does when left alone: nudge counts the times running."""
        if nudge >= 3:
            return self._say([(0.0, self.SLEEP)])
        if nudge == 0 or self.rng.random() < 0.4:
            offered = self.offer()
            if offered:
                return offered
        return self._say([(0.0, self._one(self.CHATTER))])


# -- Floyd's voice in tvmail and tvcal ----------------------------------------
# tvchat runs FloydMind inside its Floyd account.  tvmail and tvcal have no chat to
# run it in, so their backends ask persona() for anything their own words do not
# cover: the !commands (!seen !tell !quote !karma !8ball !fortune !joke !about ...),
# the little parser game (look, take X, go north) and, at chatty, plain talk.  One mind
# is kept per memory file for the life of the backend, so a game goes on between lines.
_MINDS = {}


def persona_memory_path(configured=""):
    where = (configured or "").strip()
    if where.lower() == "none":
        return None
    if where:
        return os.path.expanduser(where)
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
                        "tvfloyd", "memory.json")


def persona(text, level, who="friend", where="here", path=None, master=None):
    """What Floyd says to TEXT in his own voice: a list of lines, or None when it
    is nothing he knows (the caller then says so in its own words).  What only makes
    sense among people in a channel (!seen, !tell, karma, !leave) is not offered here.
    The master Floyd on the server answers first when [floyd] server is set and it
    is reachable; otherwise (or on any failure) the local Floyd does."""
    if level == "off" or not text.strip():
        return None
    m = master if master is not None else _MASTER
    if where in WIZ_APPS and re.match(r"^!setup\b", text.strip(), re.I):
        got = wizard_command(text, where, m if m else {"server": "", "token": "", "machine": ""})
        if got is not None:
            return got
    sm = re.match(r"^!send(?:\s+(\S+)(?:\s+(\S.*))?)?$", text.strip(), re.I | re.S)
    if sm:                                                  # !send MACHINE|all text: a toast on another machine
        if not (sm.group(1) and sm.group(2)):
            return ["Usage: !send MACHINE|all TEXT - a toast on another machine (needs the master Floyd)"]
        return [send_note(m, sm.group(1).lower(), sm.group(2))]
    if m and m.get("server"):
        r = master_call(m, {"op": "say", "text": text, "level": level, "who": who, "app": where})
        if r is not None and r.get("ok"):
            lines = r.get("lines")
            note = _note_source("master", m)
            return ([note] + lines if note and lines else lines)
        _note_source("local", m)
    mind = _MINDS.get(path)
    if mind is None:
        mind = _MINDS[path] = FloydMind(nick=who, memory=FloydMemory(path))
    return _persona_mind(mind, text, level, who)


def _persona_mind(mind, text, level, who):
    """persona() with the mind already chosen (the local one, or the gate's)."""
    mind.nick = who
    t = text.strip()
    got = None
    if t.lower().rstrip("?!. ") in ("!about", "about"):
        got = mind._say([(0.0, mind.ABOUT)])
    elif t.startswith("!"):
        if t[1:].split(" ")[0].lower() in IRC_ONLY:
            return None
        got = mind.botcmd(t, who)
    elif at_least(level, "chatty") and not mind.KARMA.match(t):
        got = mind.command(t) or mind.reply(t)
    if got is None:
        return None
    lines = []
    for entry in got:
        say, kind = entry[1], (entry[2] if len(entry) > 2 else "")
        lines.append("* Floyd " + say if kind == "a" else say)
    return lines


IRC_ONLY = ("seen", "tell", "karma", "leave")


def fortune_phrase(prog, master=False):
    """Where his fortunes come from, for !status: the master's when it answers."""
    if master and prog is None:                         # an older master that does not say
        return "the master's"
    who = "the master's " if master else ""
    return ("%ssystem's fortune program (%s)" % (who, prog) if prog
            else "%sown, built in - no fortune program is installed" % ("the master's " if master else "his "))


def status_lines(app, level, cfg, path=None):
    """!status: how he is, in a few lines."""
    mind = _MINDS.get(path)
    mem = mind.memory.data if mind else FloydMemory(path).data
    fort = (mind.fortune_prog() if mind else
            (shutil.which("fortune") or shutil.which("fortune", path="/usr/games:/usr/local/games")))
    m = cfg.get("master") if cfg else None
    hello = master_call(m, {"op": "hello"}) if m and m.get("server") else None
    up = bool(hello and hello.get("ok"))
    quotes, facts = ((hello.get("quotes", 0), hello.get("facts", 0)) if up
                     else (len(mem.get("quotes", [])), len(mem.get("facts", {}))))
    return [
        "Floyd in %s is at level %s: %s" % (app, level, LEVEL_BLURB.get(level, level)),
        "Master Floyd: %s." % master_state(m, hello),
        "Claude: %s." % (claude_state(cfg) if not up else
                         "yes, on the master (%s) - ask with !claude QUESTION" % m["server"]),
        "Fortunes: %s." % fortune_phrase(hello.get("fortune") if up else fort, up),
        "He remembers %s quotes and %s facts%s. !about says more, help lists his commands." % (
            quotes, facts, " (the master's: that is the memory in use)" if up else "")]


# -- Claude, asked by the owner -----------------------------------------------
# `!claude QUESTION` consults the Claude CLI, read-only, as tvchat's Floyd does for his admins.
# Here there is no admin to check: it is the user's own session.  The answer takes many
# seconds, so it is not waited for: the question starts a background job whose files live in
# ~/.local/state/tvfloyd/claude/ and the window asks claude_poll() until it is done.
CLAUDE_RX = re.compile(r"^(?:!\s*claude|(?:ask\s+)?claude\s*[:,]?)\s+(\S.*)$", re.I | re.S)
CLAUDE_CHARS = 1500


def claude_settings(get):
    """get(key, default) reads the app's [floyd] section."""
    import shlex

    def num(key, default):
        try:
            return int(str(get(key, "") or default).strip())
        except ValueError:
            return default
    where = (get("claude_dir", "") or "").strip()
    global _MASTER
    _MASTER = master_settings(get)
    return {"master": _MASTER, "on": (get("claude", "") or "yes").strip().lower() not in ("no", "off", "false", "0"),
            "cmd": (get("claude_cmd", "") or "").strip() or "claude",
            "tools": (get("claude_tools", "") or "").strip() or "Read,Grep,Glob",
            "model": (get("claude_model", "") or "").strip(),
            "extra": shlex.split(get("claude_args", "") or ""),
            "timeout": num("claude_timeout", 120), "lines": num("claude_lines", 10),
            "dir": os.path.expanduser(where) if where else None}


def _claude_jobs():
    return os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
                        "tvfloyd", "claude")


def claude_state(cfg):
    """Can he ask Claude?  One short sentence."""
    if not cfg["on"]:
        return "no - it is off in his configuration ([floyd] claude = no)"
    cmd = cfg["cmd"]
    if not (shutil.which(cmd) or (os.path.isfile(cmd) and os.access(cmd, os.X_OK))):
        return "no - the program %s was not found" % cmd
    busy = os.path.isdir(_claude_jobs()) and os.listdir(_claude_jobs())
    return "yes%s - ask with !claude QUESTION" % (", busy with a question" if busy else "")


def claude_argv(cfg):
    """The CLI, read-only: only the named built-in tools exist, anything else is refused, no MCP
    servers (not even the account's connectors) and no skills."""
    argv = [cfg["cmd"], "-p", "--output-format", "text",
            "--tools", cfg["tools"], "--allowedTools", cfg["tools"],
            "--permission-mode", "dontAsk",
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--disable-slash-commands"]
    if cfg["model"]:
        argv += ["--model", cfg["model"]]
    return argv + cfg["extra"]


def claude_start(prompt, cfg):
    """Start the question in the background; the lines to say now."""
    m = cfg.get("master")
    if m and m.get("server"):                       # the master runs Claude, if he is there
        r = master_call(m, {"op": "claude", "text": prompt, "who": "tvfloyd"})
        if r is not None and r.get("ok"):
            if r.get("job"):
                d = _master_jobs()
                os.makedirs(d, mode=0o700, exist_ok=True)
                with open(os.path.join(d, str(r["job"])), "w") as fh:
                    fh.write(str(int(time.time())))
            return r.get("lines") or []
    state = claude_state(cfg)
    if not state.startswith("yes"):
        return ["Claude is asleep: %s." % state.split(" - ", 1)[-1]]
    if "busy" in state:
        return ["Floyd is still waiting for Claude. Claude is slow. Floyd is patient."]
    return _claude_launch(prompt, cfg)[0]


def _claude_launch(prompt, cfg):
    """Start the CLI in the background: (lines to say now, job name)."""
    name = "%d" % int(time.time() * 1000)
    d = os.path.join(_claude_jobs(), name)
    os.makedirs(d, mode=0o700)
    with open(os.path.join(d, "prompt"), "w", encoding="utf-8") as fh:
        fh.write(prompt)
    env = dict(os.environ, ENABLE_CLAUDEAI_MCP_SERVERS="false")      # no connectors from the account
    script = ('cd "$1" && shift; d=$1; shift; t=$1; shift; '
              'if command -v timeout >/dev/null 2>&1; then timeout "$t" "$@"; else "$@"; fi '
              '< "$d/prompt" > "$d/out" 2> "$d/err"; echo $? > "$d/rc"')
    subprocess.Popen(["sh", "-c", script, "sh", cfg["dir"] or os.getcwd(), d, str(cfg["timeout"])] + claude_argv(cfg),
                     env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return ["Floyd will ask Claude. Floyd has to climb a ladder. Floyd will be back."], name


def claude_poll(cfg, only=None):
    """The answer, as lines, once the question is done; None while it is still running (or none was asked).
    ONLY names one job (the gate serves several people); otherwise any local job, or a question asked of the master."""
    m = cfg.get("master") if cfg else None
    if only is None and m and os.path.isdir(_master_jobs()):
        got = _master_poll(m, cfg)
        if got is not None:
            return got
    base = _claude_jobs()
    if not os.path.isdir(base):
        return None
    for name in sorted(os.listdir(base)):
        if only is not None and name != only:
            continue
        d = os.path.join(base, name)
        rc_file = os.path.join(d, "rc")

        def read(n):
            try:
                with open(os.path.join(d, n), encoding="utf-8", errors="replace") as fh:
                    return fh.read()
            except OSError:
                return ""
        if not os.path.exists(rc_file):
            if time.time() - int(name) / 1000.0 > cfg["timeout"] + 20:
                lines = ["Claude took too long"]
            else:
                continue
        else:
            rc = read("rc").strip()
            out = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", read("out")).strip()
            if rc == "124":
                lines = ["Claude took too long"]
            elif rc not in ("", "0") and not out:
                tail = read("err").strip().splitlines()
                lines = ["Claude could not be reached (%s)" % ((tail[-1] if tail else "exit status " + rc)[:120])]
            else:
                lines = [l.rstrip() for l in out.splitlines() if l.strip()] or ["(Claude said nothing)"]
                if sum(len(l) for l in lines) > CLAUDE_CHARS:
                    kept, n = [], 0
                    for l in lines:
                        if n + len(l) > CLAUDE_CHARS:
                            break
                        kept.append(l)
                        n += len(l)
                    lines = (kept or [lines[0][:CLAUDE_CHARS]]) + ["... (cut short)"]
                if len(lines) > cfg["lines"]:
                    lines = lines[:cfg["lines"]] + ["... (%d more lines)" % (len(lines) - cfg["lines"])]
        shutil.rmtree(d, ignore_errors=True)
        return [("Claude: " if i == 0 else "  ") + l for i, l in enumerate(lines)]
    return None


# -- the master Floyd (see tvcommon/FLOYD-MASTER.md) -----------------------------
# Every app has this local Floyd as the fall-back; when [floyd] server = HOST[:PORT] is set and the
# master (tvchat-floyd on the server, with its `gate`) answers, he answers instead: one memory, one
# Claude.  Any failure -> the local code path, quietly.  The wire: one JSON line each way, token-checked.
GATE_PORT = 7077
_MASTER = None                                  # the settings last read by claude_settings()
_DOWN = {}                                      # server -> time until which it is not tried again
_SOURCE = {"last": None}                        # "master" or "local": who answered last
DOWN_FOR = 60


def master_settings(get):
    """get(key, default) reads the app's [floyd] section.  Empty server = local only."""
    server = (get("server", "") or "").strip()
    if not server or server.lower() in ("no", "none", "off", "local"):
        return {"server": "", "token": "", "timeout": 2.0, "machine": ""}
    if ":" not in server:
        server += ":%d" % GATE_PORT
    try:
        t = float(str(get("server_timeout", "") or 2).strip())
    except ValueError:
        t = 2.0
    machine = re.sub(r"[^\w.-]", "", (get("machine", "") or "").strip() or socket.gethostname().split(".")[0]).lower()[:32]
    return {"server": server, "token": (get("token", "") or "").strip(), "timeout": max(0.2, min(t, 30.0)),
            "machine": machine or "unknown"}


def master_call(m, req, cached=True):
    """One request to the master: his reply (a dict) or None when he is not there / not understood."""
    key = m["server"]
    if cached and _DOWN.get(key, 0) > time.time():
        return None
    host, _, port = key.rpartition(":")
    try:
        s = socket.create_connection((host, int(port)), timeout=m["timeout"])
        try:
            s.settimeout(m["timeout"])
            s.sendall((json.dumps(dict(req, v=1, token=m["token"])) + "\n").encode("utf-8"))
            buf = b""
            while b"\n" not in buf and len(buf) < 262144:
                chunk = s.recv(8192)
                if not chunk:
                    break
                buf += chunk
        finally:
            s.close()
        r = json.loads(buf.split(b"\n")[0].decode("utf-8"))
        return r if isinstance(r, dict) else None
    except (OSError, ValueError):
        if cached:
            _DOWN[key] = time.time() + DOWN_FOR
        return None


def master_alive(m):
    r = master_call(m, {"op": "hello"}) if m and m.get("server") else None
    return bool(r and r.get("ok"))


def master_state(m, r=None):
    """One short phrase for !status.  R = the master's hello reply, when the caller already has it."""
    if not m or not m.get("server"):
        return "none set ([floyd] server), the local Floyd answers"
    if r is None:
        r = master_call(m, {"op": "hello"})
    if r is None:
        return "%s is not reachable, so the local Floyd answers" % m["server"]
    if not r.get("ok"):
        return "%s refused us (check [floyd] token), so the local Floyd answers" % m["server"]
    return "%s answers (%s quotes, %s facts)" % (m["server"], r.get("quotes", "?"), r.get("facts", "?"))


def _note_source(src, m):
    """The one line said when Floyd changes from the master to the local one or back; else None."""
    last, _SOURCE["last"] = _SOURCE["last"], src
    if last is None or last == src:
        return None
    return ("* Floyd is back on the master (%s)" % m["server"]) if src == "master" else \
           "* Floyd is on his own now: the master is out of reach"


def _master_jobs():
    return _claude_jobs() + "-master"


def _master_poll(m, cfg):
    d = _master_jobs()
    for name in sorted(os.listdir(d)):
        path = os.path.join(d, name)
        try:
            with open(path) as fh:
                started = int(fh.read().strip() or 0)
        except (OSError, ValueError):
            started = 0
        r = master_call(m, {"op": "poll", "job": name})
        if r is not None and r.get("ok") and not r.get("done"):
            continue
        if r is None and time.time() - started < cfg["timeout"] + 60:
            continue
        try:
            os.remove(path)
        except OSError:
            pass
        if r is not None and r.get("ok"):
            return r.get("lines") or ["(Claude said nothing)"]
        return ["Claude could not be reached on the master"]
    return None


class FloydGate(object):
    """The master's door: listens for the one-line JSON requests of master_call().  It runs on the
    daemon's own loop (loop.add_reader), so there is one thread, one FloydMemory and no locking; a
    request is answered in a moment (Claude runs as a background job, polled with op=poll)."""
    MINDS_MAX = 64

    def __init__(self, loop, memory, token, claude_cfg, host, port=GATE_PORT, level=DEFAULT_LEVEL, log=None, setup_file=""):
        self.setup_file = setup_file
        self.loop, self.memory, self.token = loop, memory, token
        self.claude_cfg, self.host, self.port, self.level = claude_cfg, host, int(port), level
        self.log = log or _log
        self.minds = {}
        self.sock = None
        self.served = 0
        self.hub = EventHub()

    def start(self):
        if not self.token:
            raise ValueError("the gate needs a token (gate_token)")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, self.port))
        s.listen(8)
        s.setblocking(False)
        self.sock = s
        self.loop.add_reader(s, self.accept)
        self.log.info("floyd gate: listening on %s:%d", self.host, self.port)

    def stop(self):
        if self.sock is not None:
            self.loop.remove_reader(self.sock)
            self.sock.close()
            self.sock = None

    def accept(self):
        try:
            conn, addr = self.sock.accept()
        except OSError:
            return
        try:
            conn.settimeout(0.5)
            buf = b""
            while b"\n" not in buf and len(buf) < 65536:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
            reply = self.handle(buf.split(b"\n")[0], addr[0])
            conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
        except (OSError, ValueError):
            pass
        finally:
            conn.close()

    def mind(self, who):
        m = self.minds.pop(who, None)
        if m is None:
            m = FloydMind(nick=who, memory=self.memory)
        self.minds[who] = m                      # most recently used last
        while len(self.minds) > self.MINDS_MAX:
            self.minds.pop(next(iter(self.minds)))
        return m

    def handle(self, raw, peer="?"):
        try:
            req = json.loads(raw.decode("utf-8"))
            if not isinstance(req, dict):
                raise ValueError("not an object")
        except ValueError:
            return {"ok": False, "error": "bad request"}
        if not hmac.compare_digest(str(req.get("token", "")).encode("utf-8"), self.token.encode("utf-8")):
            self.log.warning("floyd gate: refused %s (wrong token)", peer)
            return {"ok": False, "error": "token"}
        self.served += 1
        op = req.get("op") or "say"
        who = re.sub(r"[^\w.@-]", "", str(req.get("who") or "friend"))[:32] or "friend"
        text = str(req.get("text") or "")[:4000]
        if op == "hello":
            if getattr(self, "_fm", None) is None:
                self._fm = FloydMind(nick="floyd", memory=self.memory)
            return {"ok": True, "v": 1, "level": self.level, "fortune": self._fm.fortune_prog() or "",
                    "quotes": len(self.memory.data.get("quotes", [])), "facts": len(self.memory.data.get("facts", {}))}
        if op == "say":
            level = parse_level(req.get("level"), self.level)
            if level == "off" or not text.strip():
                return {"ok": True, "lines": None}
            return {"ok": True, "lines": _persona_mind(self.mind(who), text, level, who)}
        if op == "claude":
            self.log.info("floyd gate: %s from %s asks Claude", who, peer)
            cfg = self.claude_cfg
            state = claude_state(cfg)
            if not state.startswith("yes"):
                return {"ok": True, "lines": ["Claude is asleep: %s." % state.split(" - ", 1)[-1]]}
            if "busy" in state:
                return {"ok": True, "lines": ["Floyd is still waiting for Claude. Claude is slow. Floyd is patient."]}
            lines, job = _claude_launch(text, cfg)
            return {"ok": True, "job": job, "lines": lines}
        if op == "poll":
            job = str(req.get("job") or "")
            if not job.isdigit():
                return {"ok": False, "error": "bad job"}
            lines = claude_poll(self.claude_cfg, only=job)
            if lines is not None:
                return {"ok": True, "done": True, "lines": lines}
            if not os.path.isdir(os.path.join(_claude_jobs(), job)):
                return {"ok": True, "done": True, "lines": ["Claude: that question is gone (already collected?)"]}
            return {"ok": True, "done": False}
        if op == "setup":                        # what the wizard compares a machine with: the manifest and the public CA
            try:
                with open(self.setup_file, encoding="utf-8") as fh:
                    manifest = json.load(fh)
                cas = {}
                for key, spec in (manifest.get("cas") or {}).items():      # key -> {"file": on the server, "name": here}
                    with open(spec["file"], encoding="utf-8") as fh:
                        cas[key] = {"name": spec.get("name") or os.path.basename(spec["file"]), "pem": fh.read()}
                return {"ok": True, "manifest": manifest, "cas": cas}
            except (OSError, ValueError) as e:
                return {"ok": False, "error": "no setup file (%s)" % (getattr(e, "strerror", None) or e)}
        if op == "publish":
            n = self.hub.publish(str(req.get("kind") or "note"), text, str(req.get("level") or "info"),
                                 origin=str(req.get("machine") or who), to=str(req.get("to") or "all"),
                                 key=str(req.get("key") or ""), ttl=req.get("ttl"), data=str(req.get("data") or ""))
            return {"ok": bool(n), "seq": n, "error": "" if n else "empty"}
        if op == "events":
            since = req.get("since")
            seq, evs = self.hub.since(since if isinstance(since, int) else None, str(req.get("machine") or who))
            return {"ok": True, "seq": seq, "events": evs}
        return {"ok": False, "error": "unknown op"}


# -- the event hub (part 2 of tvcommon/FLOYD-MASTER.md) ---------------------------
# The gate keeps a ring of events; each machine's relay asks for what is newer than its last number and
# writes the answers into the spool the apps already read as toasts.  Phase 1: events come from clients
# (`!send MACHINE text`) and from the daemon (later phases: the sentinel and the watchers).
class EventHub(object):
    RING = 200

    def __init__(self, clock=time.time):
        self.clock, self.seq, self.ring, self.sticky, self.seen = clock, 0, [], {}, {}

    @staticmethod
    def clean(text, limit=200):
        return re.sub(r"[\x00-\x1f\x7f]+", " ", str(text)).strip()[:limit]

    @staticmethod
    def name(text, default):
        n = re.sub(r"[^\w.-]", "", str(text or "")).lower()[:32]
        return n or default

    def publish(self, kind, text, level="info", origin="cpbee", to="all", key="", ttl=None, data=""):
        """Add an event; its number, or 0 when it has no text.  A `key` makes it sticky until a "recover" with the same key."""
        text = self.clean(text)
        if not text:
            return 0
        try:
            ttl = max(30, min(int(ttl if ttl is not None else 900), 86400))
        except (TypeError, ValueError):
            ttl = 900
        self.seq += 1
        ev = {"seq": self.seq, "ts": int(self.clock()), "kind": self.name(kind, "note")[:16],
              "level": level if level in ("info", "warn", "error") else "info",
              "text": text, "origin": self.name(origin, "cpbee"), "to": self.name(to, "all"),
              "key": self.clean(key, 80), "ttl": ttl, "data": self.clean(data, 2000)}
        self.ring.append(ev)
        del self.ring[:-self.RING]
        if ev["key"]:
            if ev["kind"] == "recover":
                self.sticky.pop(ev["key"], None)
            else:
                self.sticky[ev["key"]] = ev
        return self.seq

    def _for(self, ev, machine):
        return ev["origin"] != machine and ev["to"] in ("all", machine) and ev["ts"] + ev["ttl"] > self.clock()

    def since(self, cursor, machine):
        """(current number, the events MACHINE has not seen).  No cursor (first contact, or the hub
        restarted and numbers began again) = only what is still true: the sticky events."""
        self.seen[machine] = self.clock()
        if cursor is None or cursor > self.seq or cursor < 0:
            evs = [e for e in self.sticky.values() if self._for(e, machine)]
        else:
            evs = [e for e in self.ring if e["seq"] > cursor and self._for(e, machine)]
        return self.seq, sorted(evs, key=lambda e: e["seq"])


# -- the relay: one small process per user per machine ---------------------------
# Polls the hub every few seconds (TVFLOYD_RELAY_POLL), writes each event as a spool note for tvmail, tvchat
# and tvcal (the apps show it as a toast), says so when it loses cpbee, and ends when no app has run for a while.
RELAY_APPS = ("tvmail", "tvchat", "tvcal")


def _state_dir():
    return os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "tvfloyd")


def spool_root():
    return os.environ.get("TV_SPOOL_DIR") or os.path.join(
        os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "tvcommon", "spool")


def spool_write(app, sender, level, text, ttl=900):
    """One note in APP's inbox, the way spool.cpp writes it (temp name, then rename). False when it was not written."""
    d = os.path.join(spool_root(), app)
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
        now = int(time.time())
        live = 0
        for n in os.listdir(d):
            if n.endswith(".note"):
                if n.split("-")[0].isdigit() and int(n.split("-")[0]) <= now:
                    os.remove(os.path.join(d, n))               # sweep what nobody came for
                else:
                    live += 1
        if live >= 50:
            return False
        name = "%d-%d-%d" % (now + max(1, ttl), os.getpid(), spool_write.n)
        spool_write.n += 1
        flat = re.sub(r"[\t\r\n]+", " ", text)
        tmp, fin = os.path.join(d, ".tmp-" + name), os.path.join(d, name + ".note")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("1\t%s\t%s\t%s\n" % (re.sub(r"[\t\r\n]+", " ", sender), level, flat))
        os.rename(tmp, fin)
        return True
    except OSError:
        return False


spool_write.n = 0


def _apps_running(prefixes=RELAY_APPS):
    """Is some tv* app (or its backend) running?  True when /proc cannot tell."""
    if not os.path.isdir("/proc/self"):
        return True
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as fh:
                args = fh.read().split(b"\0")[:2]
        except OSError:
            continue
        for a in args:
            base = os.path.basename(a.decode("utf-8", "replace"))
            base = re.sub(r"\.(exe|py)$", "", base)
            if any(base == p or base.startswith(p + "-") for p in prefixes):
                return True
    return False


def _relay_pid():
    try:
        with open(os.path.join(_state_dir(), "relay.pid")) as fh:
            pid = int(fh.read().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return 0


def ensure_relay(m):
    """Start the relay for master settings M unless one runs.  True when one runs or was started.  Never raises."""
    try:
        if not m or not m.get("server") or os.environ.get("TVFLOYD_RELAY", "") == "none":
            return False
        if _relay_pid():
            return True
        env = dict(os.environ, TVFLOYD_SERVER=m["server"], TVFLOYD_TOKEN=m["token"],
                   TVFLOYD_MACHINE=m.get("machine") or "", TVFLOYD_TIMEOUT=str(m.get("timeout", 2.0)))
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "--relay"], env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except Exception:
        return False


def relay_run(env=None, once=False, stop=None):
    """The relay's loop.  ENV carries TVFLOYD_SERVER / _TOKEN / _MACHINE / _TIMEOUT.  ONCE = one poll; STOP() = true to end (tests)."""
    import signal
    env = env if env is not None else os.environ
    m = {"server": env.get("TVFLOYD_SERVER", ""), "token": env.get("TVFLOYD_TOKEN", ""),
         "timeout": float(env.get("TVFLOYD_TIMEOUT") or 2.0), "machine": env.get("TVFLOYD_MACHINE") or "unknown"}
    if not m["server"]:
        return 2
    poll = float(env.get("TVFLOYD_RELAY_POLL") or 3.0)
    lost_after = float(env.get("TVFLOYD_RELAY_LOST") or 60.0)
    idle_exit = float(env.get("TVFLOYD_RELAY_IDLE") or 600.0)
    apps = [a for a in RELAY_APPS if shutil.which(a) or shutil.which(a + "-backend")] or list(RELAY_APPS)
    base = _state_dir()
    os.makedirs(base, mode=0o700, exist_ok=True)
    lock = open(os.path.join(base, "relay.lock"), "a")
    try:
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:
        pass
    except OSError:
        lock.close()
        return 0                                         # another relay holds it
    with open(os.path.join(base, "relay.pid"), "w") as fh:
        fh.write("%d\n" % os.getpid())
    seq_file = os.path.join(base, "relay.seq")
    try:
        with open(seq_file) as fh:
            cursor = int(fh.read().strip())
    except (OSError, ValueError):
        cursor = None
    host = m["server"].rpartition(":")[0]
    state = {"stop": False, "last_ok": None, "lost": False, "idle_since": None}
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: state.update(stop=True))
    except ValueError:                                   # not the main thread (tests)
        pass

    def toast(sender, level, text, ttl=900):
        for a in apps:
            spool_write(a, sender, level, text, ttl)
    while not state["stop"] and not (stop and stop()):
        now = time.time()
        r = master_call(m, {"op": "events", "since": cursor, "machine": m["machine"]}, cached=False)
        if r is not None and r.get("ok"):
            state["last_ok"] = now
            if state["lost"]:
                state["lost"] = False
                toast(host, "info", "%s answers again" % host, 300)
            for ev in r.get("events") or []:
                toast(ev.get("origin") if ev.get("origin") not in (None, "", "cpbee") else host,
                      ev.get("level") if ev.get("level") in ("info", "warn", "error") else "info",
                      str(ev.get("text", ""))[:200], min(int(ev.get("ttl") or 900), 900))
            if isinstance(r.get("seq"), int):
                cursor = r["seq"]
                try:
                    with open(seq_file, "w") as fh:
                        fh.write("%d\n" % cursor)
                except OSError:
                    pass
        elif state["last_ok"] is not None and not state["lost"] and now - state["last_ok"] >= lost_after:
            state["lost"] = True
            toast(host, "warn", "lost contact with %s" % host, 600)
        if once:
            break
        if idle_exit:
            if _apps_running():
                state["idle_since"] = None
            elif state["idle_since"] is None:
                state["idle_since"] = now
            elif now - state["idle_since"] > idle_exit:
                break
        time.sleep(poll)
    try:
        os.remove(os.path.join(base, "relay.pid"))
    except OSError:
        pass
    lock.close()                                         # releases the lock
    return 0


def send_note(m, target, text, level="info"):
    """!send: publish TEXT for machine TARGET ("all" = every other).  The reply line."""
    if not m or not m.get("server"):
        return "!send needs the master Floyd ([floyd] server)."
    r = master_call(m, {"op": "publish", "kind": "note", "text": text, "level": level, "to": target,
                        "machine": m.get("machine") or "unknown", "ttl": 900})
    if r is None:
        return "The master is not reachable, so nothing was sent."
    if not r.get("ok"):
        return "The master refused it (%s)." % (r.get("error") or "no")
    return "Sent to %s." % ("every other machine" if target == "all" else target)



# -- the setup wizard (Floyd, in any app: `!setup`) -----------------------------------
# A new machine needs only the master Floyd's name and token (`!setup join HOST TOKEN`, once).  After that Floyd
# compares this machine with what the master knows (the manifest in /etc/tvchat/floyd-setup.json on the server, with
# the public CA certificate): the link of every tv* app to the master, tvcal's calendar server, tvchat's accounts,
# the Delta Chat account, the notification relay, and which passwords are missing.  `!setup` lists what is ok, what
# Floyd can fix ("todo") and what only you can ("you"); `!setup N` or `!setup all` does it.  Every file he changes is
# first copied to BAK/ beside it.  He never handles a password: a missing one is only reported.
WIZ_APPS = ("tvcal", "tvmail", "tvchat")
_BAKED = set()


def wiz_conf_path(app):
    env = os.environ.get(app.upper() + "_CONF")
    return env if env else os.path.expanduser("~/.config/%s/%s.conf" % (app, app))


def _conf_lines(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().split("\n")
    except OSError:
        return []


def conf_get(path, section, key, default=""):
    import configparser
    c = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        c.read(path, encoding="utf-8")
        return c.get(section, key, fallback=default)
    except (configparser.Error, OSError):
        return default


def conf_sections(path):
    import configparser
    c = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        c.read(path, encoding="utf-8")
        return c.sections()
    except (configparser.Error, OSError):
        return []


def _conf_write(path, lines):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    if os.path.exists(path) and path not in _BAKED:           # one backup per run, before the first change
        _BAKED.add(path)
        bak = os.path.join(d, "BAK")
        os.makedirs(bak, exist_ok=True)
        shutil.copy2(path, os.path.join(bak, os.path.basename(path) + "." + time.strftime("%Y%m%d-%H%M%S")))
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("\n".join(lines))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def conf_set(path, section, key, value):
    """KEY = VALUE in [SECTION], keeping every comment; the section is added when missing.  True when it changed."""
    lines = _conf_lines(path)
    head = re.compile(r"^\[%s\]\s*$" % re.escape(section), re.I)
    start = next((i for i, l in enumerate(lines) if head.match(l)), None)
    if start is None:
        while lines and not lines[-1].strip():
            lines.pop()
        lines += ["", "[%s]" % section, "%s = %s" % (key, value), ""]
        _conf_write(path, lines)
        return True
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines))
    rx = re.compile(r"^\s*%s\s*=\s*(.*?)\s*$" % re.escape(key), re.I)
    for i in range(start + 1, end):
        m = rx.match(lines[i])
        if m:
            if m.group(1) == str(value):
                return False
            lines[i] = "%s = %s" % (key, value)
            _conf_write(path, lines)
            return True
    at = end
    while at > start + 1 and not lines[at - 1].strip():
        at -= 1
    lines.insert(at, "%s = %s" % (key, value))
    _conf_write(path, lines)
    return True


def conf_add_account(path, ident, items):
    """Append [account:IDENT] with the given (key, value) pairs unless an active one exists.  True when added."""
    if ("account:" + ident) in conf_sections(path):
        return False
    lines = _conf_lines(path)
    while lines and not lines[-1].strip():
        lines.pop()
    lines += ["", "[account:%s]" % ident] + ["%s = %s" % kv for kv in items] + [""]
    _conf_write(path, lines)
    return True


def conf_autoconnect_add(path, ident):
    cur = [x.strip() for x in conf_get(path, "chat", "autoconnect").split(",") if x.strip()]
    if ident in cur:
        return False
    return conf_set(path, "chat", "autoconnect", ", ".join(cur + [ident]))


def _has_secret(host, user):
    """Is there a password for USER on HOST in ~/.netrc or ~/.mu-tickets?  Only the fact is looked at, never the value."""
    for name in ("~/.netrc", "~/.mu-tickets"):
        try:
            text = open(os.path.expanduser(name), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if name.endswith("netrc"):
            if re.search(r"machine\s+%s\s+login\s+%s\s+password\s+\S" % (re.escape(host), re.escape(user)), text):
                return True
        elif re.search(r"^\S*://%s:[^@\s]+@%s\b" % (re.escape(user), re.escape(host)), text, re.M):  # allowlist secret: a pattern
            return True
    return False


class WizStep(object):
    def __init__(self, state, title, fix=None, detail=""):
        self.state, self.title, self.fix, self.detail = state, title, fix, detail     # state: ok | todo | you


def _wiz_installed(app):
    return os.path.isdir(os.path.dirname(wiz_conf_path(app))) or bool(shutil.which(app) or shutil.which(app + "-backend"))


def _setup_for_apps(m):
    """[(app, conf path)] of the tv* apps on this machine."""
    return [(a, wiz_conf_path(a)) for a in WIZ_APPS if _wiz_installed(a)]


def wizard_steps(app, m, manifest=None, cas=None):
    """The checks, as WizStep objects; MANIFEST / CAS (name -> {name, pem}) are what the master sent (None: not asked)."""
    cas = cas or {}
    steps = []
    host = m["server"].rpartition(":")[0] if m["server"].endswith(":%d" % GATE_PORT) else m["server"]
    apps = _setup_for_apps(m)
    for a, path in apps:
        ok = (conf_get(path, "floyd", "server") == host and conf_get(path, "floyd", "token") == m["token"])
        if ok:
            steps.append(WizStep("ok", "%s talks to the master Floyd" % a))
        else:
            def fix(a=a, path=path):
                conf_set(path, "floyd", "server", host)
                conf_set(path, "floyd", "token", m["token"])
                conf_set(path, "floyd", "machine", m["machine"])
                return "%s now has server = %s, token and machine = %s in %s" % (a, host, m["machine"], path)
            steps.append(WizStep("todo", "%s has no (or another) master Floyd in %s" % (a, path), fix))
    if manifest is None:
        steps.append(WizStep("you", "the master did not send its setup (an older master, or no setup file on the server)"))
        return steps

    def ca_path(a, key):
        return os.path.join(os.path.dirname(wiz_conf_path(a)), (cas.get(key) or {}).get("name") or "cpbee-ca.pem")

    def put_ca(a, key):
        if not (cas.get(key) or {}).get("pem"):
            return None
        p = ca_path(a, key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(cas[key]["pem"])
        return p
    cal = manifest.get("calendar")
    if cal and any(a == "tvcal" for a, _ in apps):
        path = wiz_conf_path("tvcal")
        cur = (conf_get(path, "server", "url"), conf_get(path, "server", "user"), conf_get(path, "server", "ca"))
        want_ca = ca_path("tvcal", "cal") if "cal" in cas else ""
        if cur[0] == cal["url"] and cur[1] == cal.get("user", "") and (not want_ca or (cur[2] == want_ca and os.path.isfile(want_ca))):
            steps.append(WizStep("ok", "tvcal's calendar server is %s" % cal["url"]))
        else:
            def fix(path=path, cal=cal):
                conf_set(path, "server", "url", cal["url"])
                if cal.get("user"):
                    conf_set(path, "server", "user", cal["user"])
                p = put_ca("tvcal", "cal")
                if p:
                    conf_set(path, "server", "ca", p)
                return "tvcal: calendar %s (user %s), certificate %s" % (cal["url"], cal.get("user", "-"), p or "unchanged")
            steps.append(WizStep("todo", "tvcal's calendar server is not %s here" % cal["url"], fix))
        if cal.get("user") and not _has_secret(cal["url"].split("/")[2].split(":")[0], cal["user"]):
            steps.append(WizStep("you", "no password for %s on %s in ~/.netrc or ~/.mu-tickets: add it with your auth vault "
                                        "(I never handle passwords)" % (cal["user"], cal["url"].split("/")[2].split(":")[0])))
    tvchat_path = wiz_conf_path("tvchat")
    if any(a == "tvchat" for a, _ in apps):
        for acc in manifest.get("accounts") or []:
            ident, proto = acc["id"], acc.get("protocol", "")
            if proto == "deltachat" and manifest.get("delta_owner") == m["machine"]:
                continue                                        # the owner machine runs the account as it is
            want_gw = (acc.get("opts") or {}).get("gateway")
            if ("account:" + ident) in conf_sections(tvchat_path) and want_gw and \
                    conf_get(tvchat_path, "account:" + ident, "gateway") != want_gw:
                def fix(acc=acc, ident=ident):                   # an account of the older kind (its own core): move it to the gateway
                    for k, v in acc.get("opts", {}).items():
                        v = str(v)
                        mca = re.search(r"@CA(?::(\w+))?@", v)
                        if mca:
                            key = mca.group(1) or "chat"
                            v = v.replace(mca.group(0), put_ca("tvchat", key) or ca_path("tvchat", key))
                        conf_set(tvchat_path, "account:" + ident, k, v)
                    return ("tvchat: the %s account now uses the Delta Chat gateway %s (its old local settings stay in the file, "
                            "unused).  Next: !setup delta TOKEN." % (acc.get("display", ident), want_gw))
                steps.append(WizStep("todo", "tvchat's %s account has its own Delta Chat core here; the master says to use the "
                                             "gateway %s" % (acc.get("display", ident), want_gw), fix))
            elif ("account:" + ident) in conf_sections(tvchat_path):
                steps.append(WizStep("ok", "tvchat has the %s account (%s)" % (acc.get("display", ident), proto)))
            else:
                def fix(acc=acc, ident=ident):
                    items = []
                    for k, v in acc.get("opts", {}).items():
                        v = str(v)
                        mca = re.search(r"@CA(?::(\w+))?@", v)
                        if mca:                                  # @CA@ = the "chat" certificate, @CA:key@ another one
                            key = mca.group(1) or "chat"
                            v = v.replace(mca.group(0), put_ca("tvchat", key) or ca_path("tvchat", key))
                        if "@RPC@" in v:
                            v = v.replace("@RPC@", _rpc_server_path())
                        items.append((k, v))
                    items = [("protocol", acc.get("protocol", "")), ("display", acc.get("display", ident))] + \
                            [kv for kv in items if kv[0] not in ("protocol", "display")]
                    conf_add_account(tvchat_path, ident, items)
                    if acc.get("autoconnect", True):
                        conf_autoconnect_add(tvchat_path, ident)
                    more = ""
                    if acc.get("protocol") == "deltachat" and (acc.get("opts") or {}).get("gateway"):
                        more = " Next: !setup delta TOKEN (your token on the Delta Chat gateway; see ~/dcgw-*.token on the server)."
                    elif acc.get("protocol") == "deltachat":
                        more = " Next, in tvchat: Chat > Devices > Use account from another device... (cpbee shows Chat > Devices > Add second device first)."
                    return "tvchat: added the %s account%s.%s" % (acc.get("display", ident), " and autoconnect" if acc.get("autoconnect", True) else "", more)
                steps.append(WizStep("todo", "tvchat lacks the %s account (%s)" % (acc.get("display", ident), proto), fix))
            if (acc.get("opts") or {}).get("gateway") and ("account:" + ident) in conf_sections(tvchat_path) \
                    and not conf_get(tvchat_path, "account:" + ident, "token"):
                steps.append(WizStep("you", "tvchat's %s account has no token for the Delta Chat gateway: !setup delta TOKEN "
                                            "(yours is in ~/dcgw-*.token on the server)" % acc.get("display", ident)))
            who = (acc.get("opts") or {}).get("sasl") or (acc.get("opts") or {}).get("imap_user")
            h = (acc.get("opts") or {}).get("host") or (acc.get("opts") or {}).get("imap_server")
            if who and h and not _has_secret(h, who):
                steps.append(WizStep("you", "no password for %s on %s in ~/.netrc or ~/.mu-tickets (the %s account): add it with "
                                            "your auth vault" % (who, h, acc.get("display", ident))))
    if any(conf_get(p, "floyd", "server") for _a, p in apps):
        if _relay_pid():
            steps.append(WizStep("ok", "the notification relay runs"))
        else:
            steps.append(WizStep("todo", "the notification relay is not running (toasts from the server and other machines)",
                                 lambda: "relay started" if ensure_relay(m) else "could not start the relay"))
    return steps


def _rpc_server_path():
    if sys.platform == "cygwin" or sys.platform.startswith("win"):
        return "/usr/local/bin/deltachat-rpc-server.exe"
    for p in (os.path.expanduser("~/delta-venv/bin/deltachat-rpc-server"), shutil.which("deltachat-rpc-server") or ""):
        if p and os.path.exists(p):
            return p
    return "deltachat-rpc-server"


def _wiz_fetch(m):
    """(manifest, cas) from the master, or (None, None)."""
    r = master_call(m, {"op": "setup"})
    if r and r.get("ok"):
        return r.get("manifest"), r.get("cas") or {}
    return None, None


def wizard_command(text, app, m):
    """!setup ... -> reply lines, or None when TEXT is not for the wizard.  M = master settings (server may be empty)."""
    mt = re.match(r"^!setup(?:\s+(.*))?$", text.strip(), re.I | re.S)
    if not mt:
        return None
    arg = (mt.group(1) or "").strip()
    jn = re.match(r"^join\s+(\S+)\s+(\S+)$", arg, re.I)
    if arg.lower().startswith("join") and not jn:
        return ["Usage: !setup join HOST TOKEN  (the master Floyd's name and the token from its floyd-gate.token; asked once)"]
    if jn:
        host, token = jn.group(1), jn.group(2)
        probe = master_settings(lambda k, d: {"server": host, "token": token}.get(k, d))
        r = master_call(probe, {"op": "hello"}, cached=False)
        if r is None:
            return ["%s is not answering on port %d: check the name, and that this machine is on the LAN." % (host, GATE_PORT)]
        if not r.get("ok"):
            return ["%s answered, but did not accept that token." % host]
        path = wiz_conf_path(app)
        conf_set(path, "floyd", "server", host)
        conf_set(path, "floyd", "token", token)
        conf_set(path, "floyd", "machine", probe["machine"])
        global _MASTER
        _MASTER = m = probe
        _DOWN.pop(probe["server"], None)
        head = ["Joined: %s answers (%s quotes, %s facts); this machine is called %s." % (host, r.get("quotes", "?"),
                                                                                         r.get("facts", "?"), probe["machine"])]
        return head + _wiz_report(app, m, "")
    dl = re.match(r"^delta\s+(\S+)(?:\s+(\S+))?$", arg, re.I)
    if arg.lower().startswith("delta") and not dl:
        return ["Usage: !setup delta TOKEN [ACCOUNT]  (your token on the Delta Chat gateway; asked once)"]
    if dl:
        path = wiz_conf_path("tvchat")
        names = [n.split(":", 1)[1] for n in conf_sections(path)
                 if n.startswith("account:") and conf_get(path, n, "gateway")]
        if dl.group(2):
            names = [n for n in names if n == dl.group(2)]
        if not names:
            return ["tvchat.conf has no Delta Chat account with gateway = %s: !setup adds it first." %
                    (("(named " + dl.group(2) + ")") if dl.group(2) else "...")]
        for n in names:
            conf_set(path, "account:" + n, "token", dl.group(1))
        return ["Token saved for %s in %s (it is not shown again). Restart tvchat to use it." % (", ".join(names), path)]
    if not m or not m.get("server"):
        return ["I do not know the master Floyd yet.  Say: !setup join HOST TOKEN  (e.g. !setup join cpbee ...; the token is "
                "in ~/floyd-gate.token on the server).  Then !setup checks everything else."]
    return _wiz_report(app, m, arg)


def _wiz_report(app, m, arg):
    manifest, cas = _wiz_fetch(m)
    steps = wizard_steps(app, m, manifest, cas)
    if manifest is None and master_call(m, {"op": "hello"}) is None:
        return ["The master Floyd (%s) is not reachable now, so I cannot compare.  Try again when it is." % m["server"]]
    pick = None
    if arg and arg.lower() != "all":
        if not arg.isdigit() or not (1 <= int(arg) <= len(steps)):
            return ["!setup N does step N (1-%d); !setup lists them, !setup all does every todo." % len(steps)]
        pick = int(arg)
    out = []
    if arg:
        for i, st in enumerate(steps, 1):
            if (pick == i or arg.lower() == "all") and st.state == "todo" and st.fix:
                try:
                    out.append("Done: " + st.fix())
                except OSError as e:
                    out.append("Could not do step %d: %s" % (i, e.strerror or e))
        if not out:
            out.append("Nothing to do there.")
        out.append("Restart the app(s) I changed to pick it up.")
        return out
    out.append("Setup check (master %s, this machine %s):" % (m["server"], m["machine"]))
    label = {"ok": "ok  ", "todo": "todo", "you": "you "}
    for i, st in enumerate(steps, 1):
        out.append(" %d. %s %s" % (i, label[st.state], st.title))
    todo = sum(1 for st in steps if st.state == "todo")
    you = sum(1 for st in steps if st.state == "you")
    if todo:
        out.append("!setup N does one, !setup all does the %d todo (a backup of each file goes to BAK/ first)." % todo)
    if you:
        out.append("%d need%s you (passwords or the server's side); I never handle passwords." % (you, "" if you > 1 else "s"))
    if not todo and not you:
        out.append("Everything matches the master.")
    return out


if __name__ == "__main__":
    import sys as _sys
    if "--relay" in _sys.argv[1:]:
        raise SystemExit(relay_run())
    print("tvfloyd.py is a library; --relay runs the notification relay (started by the tv* backends).")


# -- the sentinel (part 2, phase 2 of tvcommon/FLOYD-MASTER.md) --------------------
# Runs in the master's daemon: every `interval` seconds it checks the server's services, the outside hosts
# its mail depends on, the mail queue and the disk, and says what changed to the event hub (and so, through
# the relays, to every machine's toasts).  A check must fail `need` times in a row before it counts as down
# (one blip is not an outage); there is one "outage" event (sticky, error) when it goes down, a reminder every
# `remind` seconds while it stays down, and one "recover" event when it is back.  A shutdown of the server
# itself, scheduled or begun, is announced too.  The checks run on a worker thread, never on the loop.
SERVICE_LABELS = {"ergo": "IRC server (Ergo)", "talkd": "talkd (chat)", "dovecot": "mailbox server (Dovecot)",
                  "dcgw": "Delta Chat gateway (dcgw)",
                  "postfix": "mail relay (Postfix)", "radicale": "calendar server (Radicale)"}
SENTINEL_SERVICES = "ergo:6697, talkd:518@127.0.0.1, dovecot:993, postfix:25@127.0.0.1, radicale:5232"
SENTINEL_HOSTS = "u-l.ca mail relay=u-l.ca:465, u-l.ca mailbox=u-l.ca:993"


def _tcp_ok(host, port, timeout=4.0):
    try:
        socket.create_connection((host, int(port)), timeout=timeout).close()
        return True
    except (OSError, ValueError):
        return False


def check_service(unit, port=None, host="127.0.0.1"):
    """(ok, detail) for a systemd unit, and for its port when one is given ("active but not answering")."""
    unit = unit if "." in unit else unit + ".service"
    try:
        st = subprocess.run(["systemctl", "is-active", unit], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            timeout=10).stdout.decode().strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return True, "cannot ask systemd"                  # an unknown is not an outage
    if st != "active":
        return False, "unit is %s" % st
    if port and not _tcp_ok(host, port):
        return False, "running but not answering on port %s" % port
    return True, ""


def check_host(host, port):
    return (True, "") if _tcp_ok(host, port, 5.0) else (False, "no answer on %s:%s" % (host, port))


def mail_queue_length():
    """Messages waiting in Postfix's queue, or None when it cannot be told (postqueue missing / not allowed)."""
    exe = shutil.which("postqueue") or shutil.which("postqueue", path="/usr/sbin:/sbin")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-p"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15).stdout.decode()
    except (OSError, subprocess.SubprocessError):
        return None
    if "queue is empty" in out:
        return 0
    m = re.search(r"in (\d+) Request", out)
    return int(m.group(1)) if m else None


_QUEUE_WARNED = []


def check_queue(limit):
    n = mail_queue_length()
    if n is None and not _QUEUE_WARNED:                   # say so once: otherwise a stuck queue would never be noticed
        _QUEUE_WARNED.append(1)
        _log.warning("sentinel: cannot read the mail queue (postqueue -p failed); the queue check is off. "
                     "Under systemd the service needs SupplementaryGroups=postdrop (NoNewPrivileges blocks the setgid postqueue).")
    if n is None or n < limit:
        return True, ""
    return False, "%d message%s waiting in the mail queue (mail to the outside is stuck)" % (n, "" if n == 1 else "s")


def check_disk(path, percent):
    try:
        u = shutil.disk_usage(path)
    except OSError:
        return True, ""
    used = 100.0 * (u.total - u.free) / max(1, u.total)
    return (True, "") if used < percent else (False, "%s is %.0f%% full" % (path, used))


def shutdown_state():
    """None, ("scheduled", mode, epoch) for a `shutdown +10`, or ("now", mode) once the system is going down."""
    try:
        info = dict(l.strip().split("=", 1) for l in open("/run/systemd/shutdown/scheduled") if "=" in l)
        return ("scheduled", info.get("MODE", "poweroff"), int(info.get("USEC", "0")) // 1000000)
    except (OSError, ValueError):
        pass
    try:
        out = subprocess.run(["systemctl", "list-jobs", "--no-pager", "--no-legend"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=5).stdout.decode()
    except (OSError, subprocess.SubprocessError):
        return None
    for l in out.splitlines():
        for target, mode in (("reboot.target", "reboot"), ("poweroff.target", "poweroff"), ("halt.target", "halt"),
                             ("kexec.target", "reboot")):
            if target in l and " start" in l:
                return ("now", mode)
    return None


def system_stopping():
    try:
        out = subprocess.run(["systemctl", "is-system-running"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=5).stdout.decode().strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return out == "stopping"


def sentinel_checks(get, host="127.0.0.1"):
    """The checks the account's options ask for: [(key, label, need, fn)].  get(key, default) reads them:
    sentinel_services = unit[:port[@host]], ...   sentinel_hosts = label=host:port, ...   sentinel_queue = N
    (0 = off)   sentinel_disk = PERCENT (0 = off)."""
    out = []
    for spec in (get("sentinel_services", "") or SENTINEL_SERVICES).split(","):
        spec = spec.strip()
        if not spec or spec.lower() in ("none", "off", "no"):
            continue
        unit, _, rest = spec.partition(":")
        port, _, h = rest.partition("@")
        base = unit.split(".")[0]
        out.append(("svc:" + base, SERVICE_LABELS.get(base, base), 2,
                    lambda u=unit, p=port, hh=h or host: check_service(u, p or None, hh)))
    for spec in (get("sentinel_hosts", "") or SENTINEL_HOSTS).split(","):
        label, _, target = spec.strip().rpartition("=") if "=" in spec else ("", "", spec.strip())
        h, _, p = target.rpartition(":")
        if not (h and p.isdigit()):
            continue
        out.append(("host:%s:%s" % (h, p), label.strip() or "%s port %s" % (h, p), 2,
                    lambda hh=h, pp=p: check_host(hh, pp)))

    def num(key, default):
        try:
            return float(str(get(key, "") or default).strip())
        except ValueError:
            return float(default)
    q = int(num("sentinel_queue", 1))
    if q > 0:
        out.append(("queue", "outgoing mail", 4, lambda: check_queue(q)))
    d = num("sentinel_disk", 90)
    if d > 0:
        out.append(("disk", "disk space", 2, lambda: check_disk("/", d)))
    return out


class Sentinel(object):
    def __init__(self, loop, hub, checks, interval=30.0, remind=1800.0, log=None, clock=time.time, name="cpbee"):
        self.loop, self.hub, self.checks = loop, hub, checks
        self.interval, self.remind, self.clock, self.name = interval, remind, clock, name
        self.log = log or _log
        self.state = {}                          # key -> {"fails", "down_since", "last_said"}
        self.sd = None                           # the last shutdown_state() announced

    def start(self, first=10.0):
        self.loop.call_later(first, self._tick)
        self.loop.call_later(first, self._sd_tick)

    # -- the periodic runs: a thread does the asking, the loop does the telling ---------
    def _tick(self):
        self.loop.run_in_thread(self.run_checks, self._done)

    def _done(self, results, err):
        if err is None and results is not None:
            self.evaluate(results)
        elif err is not None:
            self.log.warning("sentinel: checks failed: %r", err)
        self.loop.call_later(self.interval, self._tick)

    def _sd_tick(self):
        self.loop.run_in_thread(shutdown_state, self._sd_done)

    def _sd_done(self, st, err):
        if err is None:
            self.shutdown(st)
        self.loop.call_later(5.0, self._sd_tick)

    def run_checks(self):
        """Ask every check (at once, on threads): {key: (ok, detail)}."""
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(self.checks)))) as pool:
            futs = [(k, pool.submit(fn)) for k, _label, _need, fn in self.checks]
            res = {}
            for k, f in futs:
                try:
                    res[k] = f.result(timeout=60)
                except Exception as e:           # a broken check is not an outage
                    res[k] = (True, "check failed: %s" % e)
            return res

    # -- what changed ------------------------------------------------------------------
    def evaluate(self, results):
        now = self.clock()
        for key, label, need, _fn in self.checks:
            if key not in results:
                continue
            ok, detail = results[key]
            st = self.state.setdefault(key, {"fails": 0, "down_since": None, "last_said": 0})
            if ok:
                if st["down_since"] is not None:
                    self.hub.publish("recover", "%s is back (was down %s)" % (label, _dur(now - st["down_since"])),
                                     "info", origin=self.name, key=key)
                    self.log.info("sentinel: %s is back", key)
                st.update(fails=0, down_since=None, last_said=0)
                continue
            st["fails"] += 1
            if st["down_since"] is None and st["fails"] >= need:
                st.update(down_since=now, last_said=now)
                self.hub.publish("outage", "%s is down: %s" % (label, detail), "error", origin=self.name, key=key)
                self.log.warning("sentinel: %s is down: %s", key, detail)
            elif st["down_since"] is not None and now - st["last_said"] >= self.remind:
                st["last_said"] = now
                self.hub.publish("outage", "%s is still down after %s: %s" % (label, _dur(now - st["down_since"]), detail),
                                 "error", origin=self.name, key=key)

    def shutdown(self, st):
        """Announce a scheduled or begun shutdown of the server, and a cancelled one."""
        if st == self.sd:
            return
        old, self.sd = self.sd, st
        words = {"reboot": "reboot", "poweroff": "power off", "halt": "halt"}
        if st is None:
            if old and old[0] == "scheduled":
                self.hub.publish("recover", "%s: the scheduled shutdown was cancelled" % self.name, "info",
                                 origin=self.name, key="shutdown")
            return
        mode = words.get(st[1], st[1])
        if st[0] == "scheduled":
            left = st[2] - self.clock()
            when = time.strftime("%H:%M", time.localtime(st[2]))
            self.hub.publish("shutdown", "%s will %s at %s (in %s)" % (self.name, mode, when, _dur(max(0, left))),
                             "warn", origin=self.name, key="shutdown")
        else:
            self.hub.publish("shutdown", "%s is going down now (%s)" % (self.name, mode), "error",
                             origin=self.name, key="shutdown")
