"""
Turns a spoken (or typed) sentence into structured actions for the attendance screen.

Pure functions, no Flask and no database, so they are easy to test.
It never changes data. It only returns what it understood, and the UI applies it
and asks the faculty to confirm before anything is saved.

Examples it understands:
    "mark Priya absent"
    "Rahul Sharma and Arjun late"
    "everyone present except Priya and Karthik"
    "roll number 4 and 9 absent"
    "period 3, all present"
    "yesterday second period Meera absent"
    "save" / "undo" / "show defaulters"
"""
import re
from datetime import date, timedelta
from difflib import SequenceMatcher

MAX_PERIOD = 8

STATUS_WORDS = {
    "absent": "absent", "absents": "absent", "absence": "absent", "missing": "absent",
    "late": "late", "latecomer": "late", "latecomers": "late",
    "present": "present",
}
ALL_WORDS = {"everyone", "everybody", "all", "rest", "others", "remaining"}
EXCEPT_RE = re.compile(r"\b(?:except|apart from|excluding|other than|but not)\b")
FILLER = {
    "mark", "make", "put", "set", "as", "is", "are", "was", "were", "the", "student", "students",
    "roll", "number", "numbers", "no", "nos", "please", "today", "also", "that", "them", "him",
    "her", "he", "she", "they", "to", "be", "ok", "okay", "can", "you", "kindly", "of", "a",
    "marked", "attendance", "for", "in", "on", "mam", "sir", "madam",
}
NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
ORDINALS = {"first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5",
            "sixth": "6", "seventh": "7", "eighth": "8"}

PERIOD_A = re.compile(r"\b(?:period|hour)\s+(\d+)\b")
PERIOD_B = re.compile(r"\b(\d+)(?:st|nd|rd|th)?\s+(?:period|hour)\b")
SAVE_WORDS = {"save", "submit", "confirm", "finalize", "finalise"}
UNDO_WORDS = {"undo", "revert", "cancel"}
REPORT_RE = re.compile(r"\b(defaulters?|shortage|report|summary|percentage|below\s+\d+)\b")

THRESHOLD = 0.8


# ---------- text cleanup ----------

def _clean(text):
    t = (text or "").lower().replace("’", "'")
    t = t.replace("'", "")
    t = re.sub(r"[^a-z0-9,;.& ]+", " ", t)
    t = t.replace(",", " , ")             # so "absent," is the word absent
    t = re.sub(r"\b(whole|entire|full) class\b", "everyone", t)
    t = re.sub(r"\bnot present\b", "absent", t)
    t = re.sub(r"\bday before yesterday\b", "daybefore", t)
    t = re.sub(r"\b(" + "|".join(ORDINALS) + r")\s+(period|hour)\b",
               lambda m: f"{ORDINALS[m.group(1)]} {m.group(2)}", t)
    # number words become digits, so "number four" and "number 4" parse the same
    t = re.sub(r"\b(" + "|".join(NUMBER_WORDS) + r")\b", lambda m: NUMBER_WORDS[m.group(1)], t)
    return re.sub(r"\s+", " ", t).strip()


# ---------- name matching ----------

def _key(word):
    """Rough sound-alike key, so Indian-English spellings (Vikram/Wikram, Karthik/Kartik) still match."""
    w = word.lower()
    for a, b in (("ph", "f"), ("w", "v"), ("ee", "i"), ("oo", "u"), ("th", "t"),
                 ("dh", "d"), ("kh", "k"), ("sh", "s"), ("ck", "k")):
        w = w.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", w)


def _token_score(heard, name):
    if heard == name:
        return 1.0
    if len(heard) == 1:
        return 0.9 if name.startswith(heard) else 0.0
    ka, kb = _key(heard), _key(name)
    if ka == kb and len(ka) >= 3:
        return 0.95
    return max(SequenceMatcher(None, heard, name).ratio(), SequenceMatcher(None, ka, kb).ratio())


def _brief(s):
    return {"id": s["id"], "roll_no": s["roll_no"], "name": s["name"]}


def _roll_tail(roll_no):
    m = re.search(r"(\d+)$", roll_no or "")
    return int(m.group(1)) if m else None


def _resolve(entity, students):
    """Return ("ok", [student]) / ("ambiguous", [students]) / ("none", [])."""
    ent = entity.strip()
    if not ent:
        return "none", []

    # Roll numbers: "5", "005" or the full "21cse005"
    compact = ent.replace(" ", "")
    if re.fullmatch(r"\d+", compact):
        n = int(compact)
        hits = [s for s in students if _roll_tail(s["roll_no"]) == n]
        return ("ok", hits) if len(hits) == 1 else (("ambiguous", hits) if hits else ("none", []))
    exact = [s for s in students if s["roll_no"].lower() == compact]
    if exact:
        return "ok", exact

    # Names: every spoken word must match a different word of the student's name
    heard = ent.split()
    scored = []
    for s in students:
        parts = re.sub(r"[^a-z ]", "", s["name"].lower()).split()
        total, ok = 0.0, True
        for w in heard:
            best = max((_token_score(w, p) for p in parts), default=0.0)
            if best < THRESHOLD:
                ok = False
                break
            total += best
        if ok:
            scored.append((total / len(heard), s))
    if not scored:
        return "none", []
    scored.sort(key=lambda x: -x[0])
    top = scored[0][0]
    group = [s for sc, s in scored if sc >= top - 0.03]
    return ("ok", group) if len(group) == 1 else ("ambiguous", group[:6])


def _entities(segment):
    words = [w for w in re.split(r"\s+", segment) if w and w not in FILLER]
    text = " ".join(words)
    parts = [p.strip() for p in re.split(r"\s*(?:,|&|\band\b|\balso\b|\bplus\b)\s*", text) if p.strip()]
    out = []
    for p in parts:
        toks = p.split()
        if len(toks) > 1 and all(t.isdigit() for t in toks):   # "4 9" -> two roll numbers
            out.extend(toks)
        else:
            out.append(p)
    return out


# ---------- the parser ----------

def parse(transcript, students, today=None):
    today = today or date.today()
    out = {"transcript": transcript, "actions": [], "ambiguous": [], "unresolved": []}
    for clause in re.split(r"\s*(?:[.;]|\bthen\b)\s*", _clean(transcript)):
        if clause.strip():
            _clause(clause.strip(), students, today, out)

    order = {"set_date": 0, "set_period": 1, "undo": 2, "set_all": 3, "set_status": 4, "show_report": 5, "save": 6}
    out["actions"].sort(key=lambda a: order[a["type"]])
    return out


def _add_students(out, status, entities, students):
    found = {}
    for ent in entities:
        kind, hits = _resolve(ent, students)
        if kind == "ok":
            found[hits[0]["id"]] = hits[0]
        elif kind == "ambiguous":
            out["ambiguous"].append({"heard": ent, "status": status, "options": [_brief(s) for s in hits]})
        else:
            out["unresolved"].append(ent)
    if found:
        out["actions"].append({"type": "set_status", "status": status,
                               "students": [_brief(s) for s in found.values()]})


def _clause(c, students, today, out):
    # 1. period and date can sit anywhere in the sentence
    for rx in (PERIOD_A, PERIOD_B):
        m = rx.search(c)
        if m:
            p = int(m.group(1))
            if 1 <= p <= MAX_PERIOD:
                out["actions"].append({"type": "set_period", "period": p})
            else:
                out["unresolved"].append(f"period {p}")
            c = (c[:m.start()] + " " + c[m.end():]).strip()
            break
    for word, days in (("daybefore", 2), ("yesterday", 1)):
        if re.search(rf"\b{word}\b", c):
            out["actions"].append({"type": "set_date", "date": (today - timedelta(days=days)).isoformat()})
            c = re.sub(rf"\b{word}\b", " ", c).strip()
    c = re.sub(r"\s+", " ", c)
    words = set(c.split())
    if not c:
        return

    # 2. save / undo / report
    if words & UNDO_WORDS:
        out["actions"].append({"type": "undo"})
        return
    if words & SAVE_WORDS and not (words & set(STATUS_WORDS)):
        out["actions"].append({"type": "save"})
        return
    if REPORT_RE.search(c) and not (words & {"absent", "late", "present", "missing"}):
        out["actions"].append({"type": "show_report", "defaulters_only": bool(re.search(r"defaulter|shortage|below", c))})
        return
    if words & SAVE_WORDS:
        out["actions"].append({"type": "save"})
        c = " ".join(w for w in c.split() if w not in SAVE_WORDS)

    # 3. "everyone present except X and Y"
    m = EXCEPT_RE.search(c)
    if m and (set(c[:m.start()].split()) & ALL_WORDS):
        before, after = c[:m.start()], c[m.end():]
        status = next((STATUS_WORDS[w] for w in before.split() if w in STATUS_WORDS), "present")
        out["actions"].append({"type": "set_all", "status": status})
        _add_students(out, "absent" if status == "present" else "present", _entities(after), students)
        return

    # 4. names and statuses, in either order:
    #    "Priya and Sneha absent, Arjun late"   or   "mark absent Priya and Sneha"
    tokens = c.split()
    segs, stats, cur = [], [], []
    for t in tokens:
        if t in STATUS_WORDS:
            segs.append(cur)
            stats.append(STATUS_WORDS[t])
            cur = []
        else:
            cur.append(t)
    segs.append(cur)
    if not stats:
        leftover = " ".join(w for w in tokens if w not in FILLER)
        if leftover:
            out["unresolved"].append(leftover)
        return

    names_first = bool(_entities(" ".join(segs[0])))
    for i, status in enumerate(stats):
        seg = segs[i] if names_first else segs[i + 1]
        ents = _entities(" ".join(seg))
        if any(e in ALL_WORDS for e in ents):
            out["actions"].append({"type": "set_all", "status": status})
            ents = [e for e in ents if e not in ALL_WORDS]
        _add_students(out, status, ents, students)