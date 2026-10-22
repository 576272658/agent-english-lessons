"""How hard is a piece of English for THIS learner?

Three numbers, computed from the dictionary (data/dict.db):
- coverage: share of word tokens the learner probably knows, given their level
  (exam word lists in ECDICT + frequency rank) plus words they have mastered in
  the word book. Mapped to a fit label: easy / good / hard / too_hard.
- vocab level: the lowest level at which coverage reaches 95%.
- speed: words per minute of actual speech.

Proper nouns and words the dictionary does not know are left out of the count.
"""

from __future__ import annotations

import re

import dictionary

# Learner levels, low to high. A word is "known" at a level if it is on that level's exam list
# (or a lower one) or its frequency rank is within the level's threshold.
LEVELS = [
    {"id": "zk", "name": "初中", "tags": {"zk"}, "frq": 1500},
    {"id": "gk", "name": "高中", "tags": {"zk", "gk"}, "frq": 3000},
    {"id": "cet4", "name": "四级", "tags": {"zk", "gk", "cet4"}, "frq": 5000},
    {"id": "cet6", "name": "六级", "tags": {"zk", "gk", "cet4", "cet6", "ky"}, "frq": 8000},
    {"id": "ielts", "name": "雅思/托福", "tags": {"zk", "gk", "cet4", "cet6", "ky", "ielts", "toefl"}, "frq": 12000},
]
LEVEL_IDS = [lv["id"] for lv in LEVELS]
DEFAULT_LEVEL = "zk"

# coverage thresholds (research: ~95-98% known words for comfortable comprehension)
FIT = [(0.98, "easy", "轻松"), (0.95, "good", "合适"), (0.90, "hard", "有挑战"), (0.0, "too_hard", "太难")]
# words per minute measured over sentence spans (pauses between sentences excluded), so higher than
# the usual "overall" speaking rate: ~160-210 is ordinary conversational speed
SPEED = [(160, "slow", "慢"), (210, "normal", "正常"), (10**9, "fast", "快")]

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")
TAG_IDS = {"中考": "zk", "高考": "gk", "四级": "cet4", "六级": "cet6", "考研": "ky", "托福": "toefl",
           "雅思": "ielts", "GRE": "gre"}

# irregular auxiliaries ECDICT does not link to their base word
AUX = {f: "be" for f in ("am", "are", "is", "was", "were", "been", "being")}
AUX.update({f: "have" for f in ("has", "had", "having")})
AUX.update({f: "do" for f in ("does", "did", "done", "doing")})
AUX.update({"can't": "can", "won't": "will", "shan't": "shall", "ain't": "be", "gonna": "go", "wanna": "want",
            "gotta": "get"})

_cache: dict[str, dict | None] = {}


def _entry(token: str) -> dict | None:
    key = token.lower().replace("’", "'")
    if key not in _cache:
        _cache[key] = dictionary.lookup(key)
    return _cache[key]


def level_index(level: str) -> int:
    return LEVEL_IDS.index(level) if level in LEVEL_IDS else LEVEL_IDS.index(DEFAULT_LEVEL)


def _listed_at(entry: dict, idx: int) -> bool:
    lv = LEVELS[idx]
    tags = {TAG_IDS.get(t) for t in entry["tags"]}
    return bool(tags & lv["tags"]) or bool(entry["frq"] and entry["frq"] <= lv["frq"])


def _related(tok: str, e: dict) -> list[dict]:
    """The entry plus entries a learner who knows the base would also understand:
    are -> be, don't -> do, honestly -> honest, happily -> happy."""
    out = [e]
    low = tok.lower().replace("’", "'")
    names = [e.get("also_form_of"), e.get("inflection_of"), AUX.get(low)]
    for suffix in dictionary.CONTRACTIONS:
        if low.endswith(suffix) and len(low) > len(suffix):
            names.append(low[: -len(suffix)])
    if low.endswith("ically"):
        names.append(low[:-4])                  # basically -> basic
    elif low.endswith("ily"):
        names.append(low[:-3] + "y")            # happily -> happy
    elif low.endswith("ly"):
        names.append(low[:-2])                  # honestly -> honest
    # American spellings whose exam tags sit on the British entry
    if "or" in low:
        names.append(low.replace("or", "our", 1))   # favorite -> favourite, color -> colour
    if low.endswith(("ize", "ized", "izing")):
        names.append(low.replace("iz", "is"))
    for n in names:
        if n and (r := _entry(n)) is not None:
            out.append(r)
    return out


def _known_at(tok: str, e: dict, idx: int) -> bool:
    return any(_listed_at(r, idx) for r in _related(tok, e))


def tokens(text: str) -> list[tuple[str, bool]]:
    """(token, sentence_initial) pairs."""
    out, initial = [], True
    for m in re.finditer(r"[A-Za-z][A-Za-z'’-]*|[.!?]", text):
        if m.group() in ".!?":
            initial = True
            continue
        out.append((m.group(), initial))
        initial = False
    return out


def analyze(sentences: list[str], level: str, known_words: set[str] = frozenset(),
            skip_words: set[str] = frozenset()) -> dict:
    """sentences: English text. known_words: lowercase words/bases the learner has mastered.
    skip_words: lowercase tokens to leave out of the unknown list (e.g. ones taught as lesson expressions)."""
    idx = level_index(level)
    counted = 0
    known_by_level = [0] * len(LEVELS)
    unknown: dict[str, dict] = {}
    for text in sentences:
        for tok, initial in tokens(text):
            e = _entry(tok)
            if e is None:
                continue
            if tok[0].isupper() and not initial and not e["tags"] and tok.lower() != "i":
                continue  # proper noun (Minnesota, California)
            counted += 1
            personal = e["key"] in known_words or tok.lower() in known_words
            for i in range(len(LEVELS)):
                if personal or _known_at(tok, e, i):
                    known_by_level[i] += 1
            if not personal and not _known_at(tok, e, idx) and tok.lower() not in skip_words:
                u = unknown.setdefault(e["key"], {
                    "word": e["word"], "key": e["key"], "surface": tok, "phonetic": e["phonetic"],
                    # what "add to word book" saves (the form itself when it has its own meaning: excited)
                    "book_key": e["surface"] if e.get("surface_translation") else e["key"],
                    "meaning": "；".join((e.get("surface_translation") or e["translation"])[:2])[:80],
                    "tags": e["tags"], "count": 0})
                u["count"] += 1
    coverage_by_level = [(k / counted) if counted else 1.0 for k in known_by_level]
    coverage = coverage_by_level[idx]
    vocab = next((i for i, c in enumerate(coverage_by_level) if c >= 0.95), len(LEVELS) - 1)
    fit = next(f for f in FIT if coverage >= f[0])
    return {
        "level": LEVEL_IDS[idx],
        "level_name": LEVELS[idx]["name"],
        "tokens": counted,
        "coverage": round(coverage, 3),
        "fit": fit[1],
        "fit_name": fit[2],
        "vocab_level": LEVEL_IDS[vocab],
        "vocab_level_name": LEVELS[vocab]["name"],
        "coverage_by_level": {LEVEL_IDS[i]: round(c, 3) for i, c in enumerate(coverage_by_level)},
        "unknown": sorted(unknown.values(), key=lambda u: (-u["count"], u["word"])),
    }


def speed(word_count: int, seconds: float) -> dict | None:
    if seconds <= 0 or word_count == 0:
        return None
    wpm = round(word_count / seconds * 60)
    s = next(x for x in SPEED if wpm < x[0])
    return {"wpm": wpm, "speed": s[1], "speed_name": s[2]}


def word_count(text: str) -> int:
    return len(TOKEN_RE.findall(text))
