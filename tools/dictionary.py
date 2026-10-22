"""English-Chinese dictionary for click-to-look-up, built from ECDICT (MIT, github.com/skywind3000/ECDICT).

`./lesson dict install` downloads ecdict.csv once and builds data/dict.db (SQLite).
Lookups resolve inflected forms to their base word via ECDICT's `exchange` field
(dried -> dry, getting -> get) and strip English contractions (I'm, don't, she's).
"""

from __future__ import annotations

import csv
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "dict.db"
CSV_URL = "https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv"

WORD_RE = re.compile(r"^[a-z][a-z'\-]{0,39}$")
KEEP_RE = re.compile(r"^[A-Za-z][A-Za-z'\-]*$")   # single words only; phrases come from lessons
CONTRACTIONS = ("n't", "'s", "'re", "'ve", "'ll", "'d", "'m")
TAG_NAMES = {"zk": "中考", "gk": "高考", "cet4": "四级", "cet6": "六级", "ky": "考研",
             "toefl": "托福", "ielts": "雅思", "gre": "GRE"}
EXCHANGE_NAMES = {"p": "过去式", "d": "过去分词", "i": "现在分词", "3": "第三人称单数",
                  "r": "比较级", "t": "最高级", "s": "复数"}


def installed() -> bool:
    return DB_PATH.exists()


def install(force: bool = False) -> None:
    if installed() and not force:
        print(f"dictionary already installed: {DB_PATH}")
        return
    DATA_DIR.mkdir(exist_ok=True)
    src = DATA_DIR / "dict-src" / "ecdict.csv"
    if not src.exists():
        src.parent.mkdir(exist_ok=True)
        print(f"downloading ECDICT (~63 MB) from {CSV_URL}")
        tmp = src.with_suffix(".part")
        urllib.request.urlretrieve(CSV_URL, tmp)
        tmp.rename(src)

    print("building data/dict.db ...")
    tmp_db = DB_PATH.with_suffix(".building")
    tmp_db.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp_db)
    conn.executescript("""
        CREATE TABLE words (
            word TEXT PRIMARY KEY,      -- lowercase headword
            display TEXT NOT NULL,      -- original casing (e.g. "SPF", "California")
            phonetic TEXT, translation TEXT, definition TEXT,
            tags TEXT, frq INTEGER, exchange TEXT
        ) WITHOUT ROWID;
        CREATE TABLE forms (           -- inflected form -> base word
            form TEXT PRIMARY KEY, base TEXT NOT NULL
        ) WITHOUT ROWID;
    """)
    words, forms, frqs = 0, {}, {}
    csv.field_size_limit(sys.maxsize)
    with open(src, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            w = row["word"].strip()
            if not KEEP_RE.match(w) or not row["translation"].strip():
                continue
            key = w.lower()
            frq = int(row["frq"] or 0) or None
            conn.execute(
                "INSERT INTO words VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(word) DO UPDATE SET "
                # prefer the lowercase entry over a capitalised duplicate (e.g. "may" vs "May")
                "display=CASE WHEN excluded.display=excluded.word THEN excluded.display ELSE display END",
                (key, w, row["phonetic"], row["translation"], row["definition"], row["tag"], frq,
                 row["exchange"]))
            words += 1
            frqs[key] = frq or 10**9
            for part in (row["exchange"] or "").split("/"):
                kind, _, value = part.partition(":")
                if kind in EXCHANGE_NAMES and value and KEEP_RE.match(value) and value.lower() != key:
                    forms.setdefault(value.lower(), set()).add(key)
    # a form can belong to several bases ("dried" <- dry, dry-cure...): keep the most frequent base
    conn.executemany("INSERT OR IGNORE INTO forms VALUES (?,?)",
                     ((form, min(bases, key=lambda b: frqs.get(b, 10**9))) for form, bases in forms.items()))
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    tmp_db.replace(DB_PATH)
    print(f"dictionary ready: {words} words, {len(forms)} inflected forms -> {DB_PATH} "
          f"({DB_PATH.stat().st_size // 1048576} MB)")


def _row(conn, word: str):
    return conn.execute("SELECT * FROM words WHERE word = ?", (word,)).fetchone()


# ECDICT uses an older notation: ә for ə, ^ for ɡ, ' and . for stress, : for length
IPA_FIX = str.maketrans({"ә": "ə", "^": "ɡ", "'": "ˈ", ".": "ˌ", ":": "ː", ",": "ˌ"})


def _entry(row, query: str) -> dict:
    lines = lambda s: [x.strip() for x in (s or "").replace("\\n", "\n").split("\n") if x.strip()]
    forms = []
    for part in (row["exchange"] or "").split("/"):
        kind, _, value = part.partition(":")
        if kind in EXCHANGE_NAMES and value:
            forms.append({"kind": EXCHANGE_NAMES[kind], "form": value})
    return {
        "word": row["display"],
        "key": row["word"],
        "query": query,
        "phonetic": f"/{row['phonetic'].translate(IPA_FIX)}/" if row["phonetic"] else "",
        "translation": lines(row["translation"]),
        "definition": lines(row["definition"])[:3],
        "tags": [TAG_NAMES[t] for t in (row["tags"] or "").split() if t in TAG_NAMES],
        "frq": row["frq"],
        "forms": forms,
    }


def lookup(raw: str) -> dict | None:
    """Look up a word as it appears in text. Returns None if unknown or the dictionary is missing."""
    if not installed():
        return None
    q = raw.strip().lower().replace("’", "'")
    dropped_g = q.endswith("in'")      # talkin' -> talking
    q = q.strip("'-")
    if not WORD_RE.match(q):
        return None
    candidates = [q + "g"] if dropped_g else []
    candidates.append(q)
    for suffix in CONTRACTIONS:
        if q.endswith(suffix) and len(q) > len(suffix):
            candidates.append(q[: -len(suffix)])

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        for c in candidates:
            row = _row(conn, c)
            base = conn.execute("SELECT base FROM forms WHERE form = ?", (c,)).fetchone()
            base = base["base"] if base and base["base"] != c else None
            base_row = _row(conn, base) if base else None
            own = None
            # a thin entry for an inflected form ("getting": 采煤) -> show the base word instead,
            # but keep the form's own meaning ("excited": a. 兴奋的) as a note
            if row is not None and base_row is not None and not row["tags"] and (base_row["tags"] or base_row["frq"]):
                own = _entry(row, raw)
                row = None
            if row is not None:
                # "left" is a word AND a form of "leave": show the word, mention the base
                entry = _entry(row, raw)
                if base_row is not None:
                    entry["also_form_of"] = base_row["display"]
                return entry
            if base_row is not None:
                entry = _entry(base_row, raw)   # "dries" -> entry for "dry"
                entry["inflection_of"] = entry["word"]
                entry["surface"] = c
                if own:
                    entry["surface_translation"] = own["translation"][:2]
                    entry["surface_phonetic"] = own["phonetic"]
                return entry
        return None
    finally:
        conn.close()
