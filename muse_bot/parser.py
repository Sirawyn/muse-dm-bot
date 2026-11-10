"""Pull what we need (night, venue, party size, guys, age, intent) out of a free-text DM.

Tuned on 2,850 real guest messages from the Muse Instagram history (see tests/test_real_messages.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAY_ALIASES = {
    "mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3,
    "fri": 4, "sat": 5, "sun": 6,
}
GIRLS = r"(?:girls|girlies|ladies|gals|women|girl ?friends|baddies)"
GUYS = r"(?:guys?|boys?|men|males?|dudes?|gentlemen|guy ?friends?)"
YES = re.compile(r"^\W*(yes+|yeah|yea|yep|ya+|yup|yess+|ye+s|of course|ofc|yesss)\b")
NO = re.compile(r"^\W*(no+|nope|nah|n)\b")

# Things the bot must NOT answer itself.
HANDOFF = [
    r"birthday", r"\bb-?day", r"bachelorette", r"bachelor\b", r"discount", r"\bdeal\b", r"special(s)?\b",
    r"\brude\b", r"attitude", r"complain", r"refund", r"manager", r"real person", r"\bhuman\b",
    r"hiring", r"\bjob\b", r"work (for|with) (you|muse)", r"be a promoter", r"promoter position",
    r"buy (a |the )?(table|bottle)", r"bottle service", r"(get|book|reserve) a table for (my|our|a) ",
    r"\bminimum\b", r"\bvip section\b", r"collab", r"partnership", r"sponsor",
]
# Common questions with a fixed answer (answers live in config.yaml under faq:).
FAQ = {
    "guys_cost": [r"(cover|ticket|pay|cost|price|free).{0,40}\b(guys?|boys?|men|bf|boyfriend)\b",
                  r"\b(guys?|boys?|men|bf|boyfriend)\b.{0,40}(cover|ticket|pay|cost|price|free)",
                  r"how much (would|are|is|do) (the )?tickets?", r"tickets? cost"],
    "free": [r"(have|need) to pay", r"\bfree\b.{0,15}\?|\bis it free\b|\bfree entry\b|\bfor free\b",
             r"how much", r"\bcost\b", r"\bprice\b", r"\bcover\b"],
    "table": [r"\btables?\b", r"\bdrinks?\b", r"\bbottles?\b"],
    "timing": [r"what time", r"(how )?late(st)? (can|should|do)", r"\bearliest\b", r"until when",
               r"when should (we|i)", r"time should (we|i)"],
}
DECLINE = [
    r"not this (weekend|wknd|week|time|saturday|friday|thursday|sat|fri)", r"can'?t (make it|this|tonight|come|go)",
    r"won'?t be able", r"maybe next (time|week|weekend)", r"next time\b",
    r"out of town", r"\bi'?m (busy|sick|away|out)\b", r"(have|got) plans", r"plans changed",
    r"\bpass\b", r"no thanks?\b|no thank you", r"rain ?check", r"not (tonight|going out)",
    r"maybe not", r"another (week|time|weekend)", r"in the future", r"not feeling (well|good)",
]
ARRIVAL = [
    r"\b(we|i)\s*('re|'m|m| are| am| r)\s+(here+|outside|in (the )?line|at the (door|front|back of the line)|"
    r"on (the|our) way|otw|close|around the corner|pulling up|parking|inside)\b",
    r"\b(im|we)\s+here+\b", r"^\W*here+\W*$", r"\boutside\b", r"\botw\b", r"\bon (the|our) way\b",
    r"\b\d+\s*min(ute)?s?\s+away\b", r"\bthere in \d+\s*min", r"\bin (the )?line\b", r"\bback of the line\b",
    r"finding parking", r"around the block", r"\b(came|got|are) inside\b", r"\bjust got here\b",
    r"\bat the door\b", r"\bpulling up\b", r"\barrived\b",
]
ON_THE_WAY = [r"\botw\b", r"\bon (the|our) way\b", r"\bmin(ute)?s?\s+away\b", r"\bthere in \d+",
              r"finding parking", r"around the block", r"pulling up"]
UNDERAGE = [r"\b(is|am|im|i'm|she'?s|he'?s|turning)\s+(1[5-9]|20)\b", r"under\s*21", r"not 21",
            r"\b(1[5-9]|20)\s*(y/?o|years? old)\b", r"(no|forgot (my|her|their)|lost (my|her)|don'?t have( a)?( physical)?) ids?\b",
            r"photo (of|id)|picture of (my|her) id|passport photo", r"\bfake( id)?\b"]
OVER21 = [r"\b(all|everyone( is)?|we'?re all|we are all|both|all of us|are)\s*(over\s*)?21\b", r"21\s*\+",
          r"over 21", r"21 and (over|up)", r"of age", r"\ball legal\b"]


def night_for(now: datetime) -> date:
    """Before 5am still counts as the previous night."""
    return (now - timedelta(hours=5)).date()


def normalize(text: str) -> str:
    t = text.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = t.replace("+", " plus ").replace("&", " and ")
    t = re.sub(r"\b(w/|w)\s", "with ", t)
    t = re.sub(r"\s(n)\s", " and ", t)
    t = re.sub(r"(\d)(girls?|guys?|ppl|people|min)", r"\1 \2", t)
    for w, n in NUMBER_WORDS.items():
        t = re.sub(rf"\b{w}\b", str(n), t)
    t = re.sub(r"\b(\d+)\s*[-–]\s*(\d+)\b(?=\s*(girls|guys|people|ppl|of us|friends))", r"\2", t)  # "3-4 girls"
    return re.sub(r"\s+", " ", t)


def _any(patterns, t) -> bool:
    return any(re.search(p, t) for p in patterns)


@dataclass
class Parsed:
    night: date | None = None
    venue: str | None = None
    party_size: int | None = None
    guys: int | None = None
    guys_yes_no_count: bool = False   # said "yes" there are guys but no number
    over21: bool | None = None        # True = confirmed, False = someone under 21 / no physical ID
    wants_human: bool = False
    faq: list[str] = field(default_factory=list)
    declined: bool = False
    arrived: bool = False             # here, in line, or on the way
    on_the_way: bool = False
    is_question: bool = False
    pleasantry: bool = False          # "ty!!", "sounds good", "ok" with nothing else
    text: str = ""


def parse_night(t: str, now: datetime) -> date | None:
    base = night_for(now)
    if re.search(r"\b(tonight|tn|tonite|today|rn|right now|tnite)\b", t):
        return base
    if re.search(r"\b(tomorrow|tmrw|tmr|tomorow|tmw)\b", t):
        return base + timedelta(days=1)
    for word in re.findall(r"[a-z]+", t):
        idx = None
        for i, name in enumerate(WEEKDAYS):
            if word in (name, name + "s"):
                idx = i
        if idx is None and word in WEEKDAY_ALIASES:
            idx = WEEKDAY_ALIASES[word]
        if idx is not None:
            return base + timedelta(days=(idx - base.weekday()) % 7)
    return None


def parse_venue(t: str, venues: dict) -> str | None:
    t = " " + re.sub(r"[^a-z0-9 ]", " ", t) + " "
    best, best_len = None, 0
    for key, v in venues.items():
        for alias in v.get("aliases", []) + [v["name"].lower()]:
            if f" {alias} " in t and len(alias) > best_len:
                best, best_len = key, len(alias)
    return best


def parse_counts(text: str, expecting: str | None = None) -> tuple[int | None, int | None, bool]:
    """Return (party_size incl. the guest, guys, said_yes_without_count). None = not stated."""
    t = normalize(text)
    # Ages are not headcounts: "we're all 21", "21+", "my girl is 20", "not 21 yet".
    t = re.sub(r"\b(all|are|is|im|i'm|i am|over|under|not|turning|turned)\s+\d{2}\b( plus)?", " ", t)
    t = re.sub(r"\b\d{2}\s*plus\b|\b\d{2}\s+(yet|and over|and up|y/?o|years? old)\b", " ", t)
    t = re.sub(r"\b\d{1,2}:\d{2}\b|\b\d+\s*(min|mins|minutes|am|pm|th|st|nd|rd)\b|\$\s*\d+", " ", t)

    mentions_girls = bool(re.search(rf"\b{GIRLS}\b|\ball girls\b|\bgirls only\b|\bonly girls\b", t))
    guys = None
    guys_in_others = False
    others = total = girls = None
    just_me = bool(re.search(r"\b(just|only) (me|myself)\b|\bsolo\b", t))

    def take(pattern, group=1):
        nonlocal t
        m = re.search(pattern, t)
        if not m:
            return None
        t = t[:m.start()] + " _ " + t[m.end():]
        return int(m.group(group))

    # "me and my guy friend" / "me and my boyfriend": one extra person, who is a guy
    if re.search(r"\b(me|myself|i) and my (guy ?friend|boyfriend|bf|husband|brother|man)\b", t):
        t = re.sub(r"\b(me|myself|i) and my (guy ?friend|boyfriend|bf|husband|brother|man)\b", " _ ", t)
        others, guys, guys_in_others = 1, 1, True
    # explicit guy counts
    g = take(rf"\b(\d+)\s+(?:(?:other|more|of my)\s+)?{GUYS}\b")
    while g is not None:
        guys = (guys or 0) + g
        g = take(rf"\b(\d+)\s+(?:(?:other|more|of my)\s+)?{GUYS}\b")
    if guys is None and re.search(r"\bmy (guy ?friend|boyfriend|bf|husband|brother)\b|\ba (guy|boy|man|dude)\b", t):
        guys = 1
    # people besides the guest: "me and 3", "bring 2", "with 2 friends", "2 other girls", "4 friends"
    for pat in (r"\b(?:me|myself|i) (?:and|plus) (\d+)\b", r"\bbring (\d+)\b", r"\bwith (\d+)\b",
                r"\b(\d+) (?:other|more|extra)\b", r"\b(\d+) (?:\w+ )?friends\b"):
        n = take(pat)
        if n is not None and others is None:
            others = n
    if others is None and re.search(r"\b(me|myself|i) (and|plus) (my|a|1) (friend|bestie|girl ?friend|girl|sister|"
                                    r"plus 1|plus one|roommate|cousin|bff)\b|\bmy (friend|bestie|sister) and (i|me)\b"
                                    r"|\bmy plus (1|one)\b", t):
        others = 1
    # explicit totals
    for pat in (r"\b(\d+) of us\b", r"\b(\d+) (?:\w+ )?total\b", r"\b(?:party|group|table) (?:of|for) (\d+)\b",
                r"\b(\d+) (?:people|ppl)\b"):
        n = take(pat)
        if n is not None and total is None:
            total = n
    n = take(rf"\b(\d+) (?:\w+ )?{GIRLS}\b")
    if n is not None:
        girls = n

    if total is not None:
        party = total
    elif girls is not None:
        party = girls + (guys or 0)
    elif others is not None:
        party = 1 + others + (0 if guys_in_others else (guys or 0))
    elif just_me:
        party = 1 + (guys or 0)
    else:
        party = None

    yes_no_count = False
    if guys is None and (mentions_girls or re.search(r"\b(no|zero|0) (guys|boys|men|dudes|males)\b|\bjust (us )?girls\b", t)):
        guys = 0
    stripped = re.sub(r"[^\w ]", "", t).strip()
    if expecting == "party_size" and party is None and re.fullmatch(r"\d{1,2}", stripped):
        party = int(stripped)
    if expecting in ("guys", "guys_count") and guys is None:
        if re.fullmatch(r"\d{1,2}", stripped):
            guys = int(stripped)
        elif expecting == "guys" and NO.match(stripped):
            guys = 0
        elif expecting == "guys" and YES.match(stripped):
            yes_no_count = True
    if party is not None and party > 60:
        party = None
    return party, guys, yes_no_count


REFERRAL_CUE = (r"(sent|referred|invited|told|recommended|added|dm'?d|texted) me|told us|sent us|referred us|"
                r"\bfrom\b|\bvia\b|friend of|through|\bref\b|referral|code")


def find_scout(text: str, scouts: list[dict]) -> str | None:
    """Return the scout's Team name if the DM credits them: their @handle anywhere, their full name anywhere,
    or their first name next to a referral cue ("Mia sent me", "from Mia", "Mia told me about this")."""
    t = normalize(text)
    for s in scouts:
        handle = (s.get("handle") or "").lower().lstrip("@")
        full = s["name"].lower()
        first = full.split()[0]
        if handle and re.search(rf"@?\b{re.escape(handle)}\b", t):
            return s["name"]
        if " " in full and re.search(rf"\b{re.escape(full)}\b", t):
            return s["name"]
        if len(first) >= 3 and re.search(rf"\b{re.escape(first)}\b", t):
            window = re.search(rf"(.{{0,30}})\b{re.escape(first)}\b(.{{0,30}})", t)
            if window and re.search(REFERRAL_CUE, window.group(1) + " | " + window.group(2)):
                return s["name"]
    return None


def parse_age(t: str, expecting: str | None = None) -> bool | None:
    if _any(UNDERAGE, t):
        return False
    if _any(OVER21, t):
        return True
    if expecting == "age":
        if YES.match(t):
            return True
        if NO.match(t):
            return False
    return None


def parse(text: str, now: datetime, venues: dict, expecting: str | None = None) -> Parsed:
    t = normalize(text).strip()
    p = Parsed()
    p.night = parse_night(t, now)
    p.venue = parse_venue(t, venues)
    p.party_size, p.guys, p.guys_yes_no_count = parse_counts(text, expecting)
    p.over21 = parse_age(t, expecting)
    p.wants_human = _any(HANDOFF, t)
    p.faq = [k for k, pats in FAQ.items() if _any(pats, t)]
    if "guys_cost" in p.faq and "free" in p.faq:
        p.faq.remove("free")
    p.declined = _any(DECLINE, t) and not p.wants_human
    p.arrived = _any(ARRIVAL, t)
    p.on_the_way = p.arrived and _any(ON_THE_WAY, t)
    p.text = t
    p.pleasantry = (len(t) < 70 and "?" not in t and not (p.night or p.venue or p.party_size) and bool(re.match(
        r"^\W*(ok+|okay|oki+|kk|ty+|thank|tysm|thx|sounds good|sg+|bet|perfect|yay+|amazing|love (it|that|you)|"
        r"will do|no worries|np|got it|gotcha|noted|cute|omg|haha|lol|\W*$)", t)))
    p.is_question = "?" in t or bool(re.match(r"^(is|are|do|does|can|will|what|where|when|how|who)\b", t))
    return p
