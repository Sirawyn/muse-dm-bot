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
    # signs for the table (birthday/bottle signs, sparklers): the team sets these up
    r"\bsigns?\b(?! up)", r"sparkler", r"\bbanner\b", r"\bneon\b",
]
# "Not sure" as the whole answer to a question (no number in it).
UNSURE_ANSWER = re.compile(
    r"^\W*(i'?m |im |we'?re |were )?(not sure|unsure|idk|i don'?t know|don'?t know|dont know|no idea|tbd|"
    r"not certain|maybe|possibly|might|we'?ll see|probably not|prob not|hmm+|unclear|depends)"
    r"( yet| tbh| lol| atm| rn| right now| honestly| actually)*\W*$")
UNSURE_GUYS = [r"(not sure|unsure|don'?t know|dont know|idk|no idea|tbd).{0,25}(guys?|boys?|men|dudes?)\b",
               r"(guys?|boys?)\b.{0,25}(not sure|unsure|tbd|idk|don'?t know|dont know|no idea)"]
# She doesn't know her group size yet: the team follows up instead of the bot pushing for a number.
UNSURE_COUNT = [
    r"(not sure|unsure|don'?t know|dont know|idk|no idea|not certain|unclear|tbd).{0,25}how many",
    r"how many.{0,30}(not sure|unsure|don'?t know|idk|tbd|yet to (see|find out))",
    r"(not sure|unsure|don'?t know|idk) (yet )?(about|on) (the )?(number|numbers|count|headcount|group size)",
]
# Common questions with a fixed answer (answers live in config.yaml under faq:).
FAQ = {
    "bot": [r"\b(are|r) (you|u) (a )?(bot|robot|ai|real|human|a real person|a person)\b", r"\b(is|am i talking to) (this|a|an)? ?(bot|robot|ai|real person|human)\b",
            r"\bis this (a )?(real|automated|auto)", r"\b(bot|robot|automated|ai) (reply|replying|response|message)s?\b"],
    "guys_cost": [r"(cover|ticket|pay|cost|price|free|how much).{0,40}\b(guys?|boys?|men|bf|boyfriend)\b",
                  r"can (my |a |the )?(guy ?friends?|boyfriend|bf|guys?|boys?|brother|husband)( friend)? (come|join)",
                  r"(guys|boys|men) allowed", r"bring (a |my |some )?(guy|guys|boys?|boyfriend|bf)\b",
                  r"\b(guys?|boys?|men|bf|boyfriend)\b.{0,40}(cover|ticket|pay|cost|price|free|how much)",
                  r"how much (would|are|is|do) (the )?tickets?", r"tickets? cost"],
    "free": [r"(have|need) to pay", r"\bfree\b.{0,15}\?|\bis it free\b|\bfree entry\b|\bfor free\b",
             r"how much", r"\bcost\b", r"\bprice\b", r"\bcover\b"],
    "table": [r"\btables?\b", r"\bdrinks?\b", r"\bbottles?\b"],
    "timing": [r"what time", r"(how )?late(st)? (can|should|do)", r"\bearliest\b", r"until when",
               r"when should (we|i)", r"time should (we|i)", r"good time", r"(would|is) (\d{1,2}|midnight)\S* (be )?(ok|okay|good|fine)",
               r"arrive (between|by|at|around)", r"when (do|does) (it|the list|guestlist|entry) close", r"list clos"],
    "schedule": [r"where (will|are|r) (you|u|y'?all)", r"which (club|venue|place|spot)", r"what (club|venue|clubs|venues)",
                 r"where (is|are) (it|the event|the party|you guys)", r"anything (for )?(today|tonight|tn|this week)",
                 r"what('?s| is) (on|the plan|happening)", r"where('?s| is) (it|the party)"],
    "bring_friends": [r"can (i|we) (bring|invite|add) (a |my |some )?(friend|girl|bestie|sister|roommate|plus|\+)",
                      r"can my (friend|friends|girl ?friends?|bestie|sister|roommate)s? (come|join)",
                      r"bring (more|other|extra) (girls|friends)"],
    "dress_code": [r"dress ?code", r"what (should|do) (we|i) wear", r"(can|could) (i|we) wear", r"\bsneakers?\b\?",
                   r"what to wear", r"\boutfits?\b.*\?"],
    "age": [r"\b(is it|is this|is the event|is the club)\s*(1[89]|21)\s*(\+|plus|and over)?", r"\bage (limit|requirement)",
            r"how old"],
    "how_it_works": [r"how (does|do) (this|it) work", r"what do (i|we) (do|say) at the door", r"show (them|this|the bouncer)",
                     r"do (i|we) (need|have) to (sign up|register|rsvp|fill)", r"how do (i|we) get (in|on)"],
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
            r"\b(1[5-9]|20)\s*(y/?o|yrs?|years?)\s*(olds?)?\b", r"(no|forgot (my|her|their)|lost (my|her)|don'?t have( a)?( physical)?) ids?\b",
            r"photo (of|id)|picture of (my|her) id|passport photo", r"\bfake( id)?\b"]
OVER21 = [r"\b(all|everyone( is)?|we'?re all|we are all|both|all of us|are)\s*(over\s*)?21\b", r"21\s*\+",
          r"over 21", r"21 and (over|up)", r"of age", r"\ball legal\b"]


def night_for(now: datetime) -> date:
    """Before 5am still counts as the previous night."""
    return (now - timedelta(hours=5)).date()


# Texting shorthand guests actually use, mined from the Muse DM history (3,463 guest messages, Oct 2026).
# Expanded before anything else is read, so "abt 5 of us tmrw" reads like "about 5 of us tomorrow".
# Words the rules already know in short form (ty, tysm, thx, kk, omw, otw, tn, rn, idk) are left alone.
SLANG = {
    "abt": "about", "bout": "about", "lyk": "like", "lk": "like", "u": "you", "r": "are", "ur": "your",
    "ppl": "people", "bc": "because", "bcs": "because", "cuz": "because", "cus": "because", "coz": "because",
    "pls": "please", "plz": "please", "lmk": "let me know", "js": "just", "jus": "just", "alr": "alright",
    "idt": "i don't think", "yk": "you know", "imma": "i'm going to", "lemme": "let me", "gna": "going to",
    "gonna": "going to", "wanna": "want to", "tryna": "trying to", "tix": "tickets", "tkts": "tickets",
    "wknd": "weekend", "wkend": "weekend", "shld": "should", "wld": "would", "cld": "could", "rly": "really",
    "rlly": "really", "hv": "have", "nw": "no worries", "tgt": "together", "smth": "something",
    "smt": "something", "gc": "group chat", "yall": "you all", "y'all": "you all", "nvm": "never mind",
    "prob": "probably", "prolly": "probably", "def": "definitely", "deff": "definitely", "sm": "so much",
    "tnit": "tonight", "2nite": "tonight", "2night": "tonight", "tmrow": "tomorrow", "tomo": "tomorrow",
    "2morrow": "tomorrow", "b4": "before", "l8r": "later", "l8": "late", "thru": "through", "tho": "though",
    "w/o": "without", "bday": "birthday", "bdays": "birthdays", "gf": "girlfriend", "gfs": "girlfriends",
    "ig": "instagram", "msg": "message", "abt.": "about", "wya": "where are you",
    "hbu": "how about you", "wbu": "what about you", "ofc": "of course", "obv": "obviously", "ngl": "not gonna lie",
    "fs": "for sure", "asap": "as soon as possible", "aight": "alright", "ight": "alright",
}
_SLANG_RE = re.compile(r"(?<![\w'/])(" + "|".join(re.escape(k) for k in sorted(SLANG, key=len, reverse=True))
                       + r")(?![\w'/])")


def normalize(text: str) -> str:
    t = text.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = _SLANG_RE.sub(lambda m: SLANG[m.group(1)], t)
    t = t.replace("+", " plus ").replace("&", " and ")
    t = re.sub(r"\b(w/|w)\s", "with ", t)
    t = re.sub(r"\s(n)\s", " and ", t)
    t = re.sub(r"(\d)(girls?|guys?|ppl|people|min)", r"\1 \2", t)
    for w, n in NUMBER_WORDS.items():
        t = re.sub(rf"\b{w}\b", str(n), t)
    t = re.sub(r"\b(\d+)\s*[-–]\s*(\d+)\b(?=\s*(girls|guys|people|ppl|of us|friends))", r"\2", t)  # "3-4 girls"
    # a bare headcount range ("5-6", "5 to 6", "5 or 6") means the bigger number, never "56"
    t = re.sub(r"\b(\d{1,2})\s*(?:[-–~]|to|or)\s*(\d{1,2})\b",
               lambda m: m.group(2) if 0 < int(m.group(2)) - int(m.group(1)) <= 5 else m.group(0), t)
    return re.sub(r"\s+", " ", t)


def _any(patterns, t) -> bool:
    return any(re.search(p, t) for p in patterns)


@dataclass
class Parsed:
    night: date | None = None
    venue: str | None = None
    venues: list[str] = field(default_factory=list)   # all venues she named ("nebula or marquee")
    party_size: int | None = None
    guys: int | None = None
    guys_yes_no_count: bool = False   # said "yes" there are guys but no number
    over21: bool | None = None        # True = confirmed, False = someone under 21 / no physical ID
    wants_human: bool = False
    unsure_count: bool = False
    thanks: bool = False              # "thank you", "ty", "tysm": the bot likes her message
    unsure_guys: bool = False         # "not sure" about guys: don't ask again, confirm the girls
    faq: list[str] = field(default_factory=list)
    declined: bool = False
    arrived: bool = False             # here, in line, or on the way
    on_the_way: bool = False
    is_question: bool = False
    pleasantry: bool = False          # "ty!!", "sounds good", "ok" with nothing else
    text: str = ""


MONTHS = {"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
          "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
          "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12}
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
_ORD = r"(?:st|nd|rd|th)"


def _weekday_index(word: str) -> int | None:
    for i, name in enumerate(WEEKDAYS):
        if word in (name, name + "s"):
            return i
    return WEEKDAY_ALIASES.get(word)


def _upcoming(base: date, month: int, day: int) -> date | None:
    """The next time that calendar date comes round (today counts)."""
    for year in (base.year, base.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            return None
        if d >= base:
            return d
    return None


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _holidays(year: int) -> dict[str, date]:
    thanksgiving = _nth_weekday(year, 11, 3, 4)                       # 4th Thursday of November
    return {
        "thanksgiving eve": thanksgiving - timedelta(days=1), "thanksgiving": thanksgiving,
        "friendsgiving": thanksgiving - timedelta(days=1), "black friday": thanksgiving + timedelta(days=1),
        "christmas eve": date(year, 12, 24), "xmas eve": date(year, 12, 24),
        "christmas": date(year, 12, 25), "xmas": date(year, 12, 25),
        "new year'?s eve": date(year, 12, 31), "new years eve": date(year, 12, 31), "nye": date(year, 12, 31),
        "new year'?s day": date(year, 1, 1), "halloween": date(year, 10, 31),
        "valentine'?s( day)?": date(year, 2, 14), "galentine'?s( day)?": date(year, 2, 13),
        "st\\.? patrick'?s( day)?|st\\.? patty'?s( day)?|saint patrick'?s( day)?": date(year, 3, 17),
        "cinco de mayo": date(year, 5, 5), "(4th|fourth) of july|july (4th|fourth)": date(year, 7, 4),
    }


def parse_holiday(t: str, base: date) -> date | None:
    """Holidays guests use instead of dates: "thanksgiving eve", "nye", "halloween"..."""
    for year in (base.year, base.year + 1):
        for pattern, d in sorted(_holidays(year).items(), key=lambda kv: -len(kv[0])):   # "thanksgiving eve" first
            if re.search(rf"\b(?:{pattern})\b", t) and d >= base:
                return d
    return None


def parse_date(t: str, base: date) -> date | None:
    """Calendar dates: "nov 20th", "november 20", "20th of november", "11/20", "the 21st", and holidays."""
    h = parse_holiday(t, base)
    if h:
        return h
    m = re.search(rf"\b({_MONTH})\.?\s*(\d{{1,2}}){_ORD}?\b", t)
    if m and not (m.group(1) == "may" and not re.search(rf"\bmay\.?\s*\d{{1,2}}{_ORD}?\b(?!\s*(of us|girls|people|ppl))", t)):
        return _upcoming(base, MONTHS[m.group(1)], int(m.group(2)))
    m = re.search(rf"\b(\d{{1,2}}){_ORD}?\s*(?:of\s+)?({_MONTH})\b", t)
    if m:
        return _upcoming(base, MONTHS[m.group(2)], int(m.group(1)))
    m = re.search(r"(?<![\d/])(1[0-2]|0?[1-9])/(3[01]|[12]\d|0?[1-9])(?:/(?:\d{2}|\d{4}))?(?![\d/])", t)
    if m:
        return _upcoming(base, int(m.group(1)), int(m.group(2)))
    m = re.search(rf"\b(?:the\s+)?(3[01]|[12]\d|0?[1-9]){_ORD}\b", t)
    if m:
        day = int(m.group(1))
        for k in range(3):   # this month, else the next month that has that day
            y, mo = base.year + (base.month - 1 + k) // 12, (base.month - 1 + k) % 12 + 1
            try:
                d = date(y, mo, day)
            except ValueError:
                continue
            if d >= base:
                return d
    return None


def parse_night(t: str, now: datetime) -> date | None:
    base = night_for(now)
    # An explicit date wins over everything else ("next friday, november 20th").
    d = parse_date(t, base)
    if d:
        return d
    if re.search(r"\b(tonight|tn|tonite|today|rn|right now|now|tnite)\b", t) and not re.search(r"\b(not|n'?t) (going out |coming )?(rn|now|right now)\b|\bfor now\b", t):
        return base
    if re.search(r"\b(tomorrow|tmrw|tmr|tomorow|tmw)\b", t):
        return base + timedelta(days=1)
    words = re.findall(r"[a-z]+", t)
    for i, word in enumerate(words):
        idx = _weekday_index(word)
        if idx is None:
            continue
        prev = words[i - 1] if i else ""
        after = " ".join(words[i + 1:i + 3])
        if prev == "next" or after == "next week" or re.search(rf"\bnext week'?s? {word}\b", t):
            # "next friday" / "friday next week": that weekday in next calendar week, never tonight
            next_monday = base + timedelta(days=7 - base.weekday())
            return next_monday + timedelta(days=idx)
        return base + timedelta(days=(idx - base.weekday()) % 7)
    return None


def parse_venues(t: str, venues: dict) -> list[str]:
    """Every venue she names, longest match first ("nebula or marquee" -> both)."""
    t = " " + re.sub(r"[^a-z0-9 ]", " ", t) + " "
    squash = lambda x: re.sub(r"(.)\1+", r"\1", x)   # "nebulaa", "nooflex" -> typos of our venues
    ts = squash(t)
    found: dict[str, int] = {}
    for key, v in venues.items():
        for alias in v.get("aliases", []) + [v["name"].lower()]:
            if f" {alias} " in t or f" {squash(alias)} " in ts:
                found[key] = max(found.get(key, 0), len(alias))
    ranked = sorted(found, key=lambda k: -found[k])
    if len(ranked) > 1:
        # "noflex instead of nebula", "not nebula", "switch from nebula to noflex": the venue she's moving
        # away from goes last, so the one she wants wins
        def dropped(key: str) -> bool:
            names = "|".join(re.escape(a) for a in venues[key].get("aliases", []) + [venues[key]["name"].lower()])
            return bool(re.search(rf"\b(instead of|rather than|not|no longer|switch(ing)? from|change from|"
                                  rf"move from|from)\s+(the\s+)?({names})\b", t))
        ranked = [k for k in ranked if not dropped(k)] + [k for k in ranked if dropped(k)]
    return ranked


def parse_venue(t: str, venues: dict) -> str | None:
    vs = parse_venues(t, venues)
    return vs[0] if vs else None


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
                r"\b(\d+) (?:people|ppl)\b", r"\b(\d+) in (?:my|our|the) (?:group|grp|party|crew|squad)\b",
                r"\b(?:group|grp) (?:of|is) (\d+)\b", r"\b(?:we|i) (?:have|hv|got|are) (\d+)\b"):
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
        m = re.fullmatch(r"(?:maybe|probably|prob|like|around|about|max|up to|possibly|prolly|i think|"
                         r"at most)?\s*(\d{1,2})(?: guys?| boys?| of them| max)?(?: maybe| probably| max| tops)?", stripped)
        if m:
            guys = int(m.group(1))
        elif expecting == "guys" and NO.match(stripped) and not UNSURE_ANSWER.match(t):
            guys = 0
        elif expecting == "guys" and YES.match(stripped):
            yes_no_count = True
    if guys is None and re.search(r"\b(some|a few|a couple|couple|few) (of )?(guys|boys|dudes|men)\b|^\W*(yes|yeah|yea|yep)\W+(guys|boys)\b", t):
        yes_no_count = True
    if party is not None and party > 300:
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
        if NO.match(t) and not re.search(rf"\b{GUYS}\b|\bjust (us )?girls\b", t):   # "no guys" isn't "no, not 21"
            return False
    return None


def parse(text: str, now: datetime, venues: dict, expecting: str | None = None) -> Parsed:
    t = normalize(text).strip()
    p = Parsed()
    p.night = parse_night(t, now)
    p.venues = parse_venues(t, venues)
    p.venue = p.venues[0] if p.venues else None
    p.party_size, p.guys, p.guys_yes_no_count = parse_counts(text, expecting)
    p.over21 = parse_age(t, expecting)
    p.unsure_count = _any(UNSURE_COUNT, t)
    if p.guys is None and not re.search(r"\d", t) and (
            (expecting in ("guys", "guys_count") and UNSURE_ANSWER.match(t)) or _any(UNSURE_GUYS, t)):
        p.unsure_guys, p.unsure_count, p.guys_yes_no_count = True, False, False
    p.wants_human = _any(HANDOFF, t) or p.unsure_count
    p.faq = [k for k, pats in FAQ.items() if _any(pats, t)]
    if "bot" in p.faq and not _any([h for h in HANDOFF if h not in (r"real person", r"\bhuman\b")], t) \
            and not p.unsure_count:
        p.wants_human = False   # "is this a real person?" is a question Thalia answers, not a request for staff
    if "guys_cost" in p.faq and "free" in p.faq:
        p.faq.remove("free")
    p.declined = _any(DECLINE, t) and not p.wants_human
    p.arrived = _any(ARRIVAL, t)
    p.on_the_way = p.arrived and _any(ON_THE_WAY, t)
    p.text = t
    p.thanks = bool(re.search(r"\b(thank(s|x|u|ss+)?|thnx|thx|ty+|tysm+|tyvm|appreciate (it|you|u|that))\b", t))
    p.pleasantry = (len(t) < 70 and "?" not in t and not (p.night or p.venue or p.party_size) and bool(re.match(
        r"^\W*(ok+|okay|oki+|kk|ty+|thank|tysm|thx|sounds good|sg+|bet|perfect|yay+|amazing|love (it|that|you)|"
        r"will do|no worries|np|got it|gotcha|noted|cute|omg|haha|lol|\W*$)", t))
        # "I see thank you!", "oh ok thanks so much": a thank-you with nothing to act on is not a new request
        or (p.thanks and len(t) < 70 and "?" not in t and not (p.night or p.venue or p.party_size)
            and p.guys is None and p.over21 is None and not p.faq and not p.wants_human and not p.declined
            and not p.arrived))
    p.is_question = "?" in t or bool(re.match(r"^(is|are|do|does|can|will|what|where|when|how|who)\b", t))
    return p
