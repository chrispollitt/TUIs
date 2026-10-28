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
import math
import operator
import os
import re
import time

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
