"""Learner progress in SQLite (data/progress.db): check-ins, favorites, word book, quiz attempts.

Lesson content is NOT stored here -- it lives in lessons/EPxxx/ as files.
Tables are keyed by lesson id ("EP001") and expression id ("get-ready-to").
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "progress.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS checkins (
    lesson_id TEXT PRIMARY KEY,
    at        TEXT NOT NULL              -- ISO 8601, server local time
);
CREATE TABLE IF NOT EXISTS favorites (
    expr_id TEXT PRIMARY KEY,
    at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wordbook (
    word       TEXT PRIMARY KEY,         -- dictionary key, lowercase base form ("dry")
    display    TEXT NOT NULL,            -- headword as shown ("dry", "California")
    phonetic   TEXT,
    meaning    TEXT NOT NULL,            -- short Chinese meaning, snapshot from the dictionary
    lesson_id  TEXT,                     -- where it was added
    context_en TEXT,                     -- the sentence it was clicked in
    context_zh TEXT,
    at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quiz_attempts (
    id          INTEGER PRIMARY KEY,
    at          TEXT NOT NULL,
    day         TEXT NOT NULL,           -- YYYY-MM-DD, server local date
    lesson_id   TEXT,                    -- lesson the item came from
    expr_id     TEXT NOT NULL,           -- expression id, or the word for kind='word'
    kind        TEXT NOT NULL DEFAULT 'expr' CHECK (kind IN ('expr', 'word')),
    result      TEXT NOT NULL CHECK (result IN ('known', 'unknown')),
    mode        TEXT NOT NULL,           -- typing | flash
    wrong_tries INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS quiz_attempts_expr ON quiz_attempts (kind, expr_id);
CREATE INDEX IF NOT EXISTS quiz_attempts_day ON quiz_attempts (day);
"""


def now() -> dt.datetime:
    return dt.datetime.now().astimezone()


@contextmanager
def db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        # databases created before the word book: add quiz_attempts.kind first
        cols = {r[1] for r in conn.execute("PRAGMA table_info(quiz_attempts)")}
        if cols and "kind" not in cols:
            conn.execute("ALTER TABLE quiz_attempts ADD COLUMN kind TEXT NOT NULL DEFAULT 'expr'")
            conn.execute("DROP INDEX IF EXISTS quiz_attempts_expr")
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------- writes ----------

def set_checkin(lesson_id: str, done: bool) -> None:
    with db() as c:
        if done:
            c.execute("INSERT OR IGNORE INTO checkins (lesson_id, at) VALUES (?, ?)",
                      (lesson_id, now().isoformat(timespec="seconds")))
        else:
            c.execute("DELETE FROM checkins WHERE lesson_id = ?", (lesson_id,))


def set_favorite(expr_id: str, fav: bool) -> None:
    with db() as c:
        if fav:
            c.execute("INSERT OR IGNORE INTO favorites (expr_id, at) VALUES (?, ?)",
                      (expr_id, now().isoformat(timespec="seconds")))
        else:
            c.execute("DELETE FROM favorites WHERE expr_id = ?", (expr_id,))


def add_attempt(item_id: str, result: str, mode: str, lesson_id: str | None, wrong_tries: int,
                kind: str = "expr") -> None:
    t = now()
    with db() as c:
        c.execute("INSERT INTO quiz_attempts (at, day, lesson_id, expr_id, kind, result, mode, wrong_tries) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (t.isoformat(timespec="seconds"), t.date().isoformat(), lesson_id, item_id, kind, result, mode,
                   wrong_tries))


def add_word(entry: dict, lesson_id: str | None, context_en: str | None, context_zh: str | None) -> None:
    """entry: a dictionary lookup result (tools/dictionary.py)."""
    meaning = "；".join(entry["translation"][:2])[:120]
    with db() as c:
        c.execute("INSERT INTO wordbook (word, display, phonetic, meaning, lesson_id, context_en, context_zh, at) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(word) DO NOTHING",
                  (entry["key"], entry["word"], entry["phonetic"], meaning, lesson_id, context_en, context_zh,
                   now().isoformat(timespec="seconds")))


def remove_word(word: str) -> None:
    with db() as c:
        c.execute("DELETE FROM wordbook WHERE word = ?", (word,))


def words() -> list[dict]:
    """Word book, newest first, with each word's quiz record."""
    with db() as c:
        rows = c.execute("""
            SELECT w.*,
                   (SELECT COUNT(*) FROM quiz_attempts q WHERE q.kind = 'word' AND q.expr_id = w.word) AS attempts,
                   (SELECT result FROM quiz_attempts q WHERE q.kind = 'word' AND q.expr_id = w.word
                     ORDER BY id DESC LIMIT 1) AS last_result
            FROM wordbook w ORDER BY at DESC""").fetchall()
    return [dict(r) for r in rows]


# ---------- reads ----------

def snapshot() -> dict:
    """What every page needs on load: check-ins and favorites."""
    with db() as c:
        return {
            "checkins": {r["lesson_id"]: r["at"] for r in c.execute("SELECT * FROM checkins")},
            "favorites": [r["expr_id"] for r in c.execute("SELECT expr_id FROM favorites ORDER BY at")],
            "words": [r["word"] for r in c.execute("SELECT word FROM wordbook")],
        }


def stats(lessons: dict[str, dict], days: int = 112) -> dict:
    """Study statistics. `lessons` maps lesson id -> lesson payload (published lessons only)."""
    exprs: dict[str, dict] = {}
    for lid, lesson in lessons.items():
        for e in lesson["expressions"]:
            exprs.setdefault(e["id"], {"id": e["id"], "phrase": e["phrase"], "meaning_zh": e["meaning_zh"],
                                       "lesson": lid})

    with db() as c:
        checkins = {r["lesson_id"]: r["at"] for r in c.execute("SELECT * FROM checkins")}
        per_expr = c.execute("""
            SELECT expr_id,
                   COUNT(*) AS attempts,
                   SUM(result = 'unknown') AS missed,
                   (SELECT result FROM quiz_attempts q2 WHERE q2.expr_id = q.expr_id
                     ORDER BY id DESC LIMIT 1) AS last_result,
                   MAX(at) AS last_at
            FROM quiz_attempts q WHERE kind = 'expr' GROUP BY expr_id""").fetchall()
        book = {r["word"]: dict(r) for r in c.execute("SELECT * FROM wordbook")}
        per_word = c.execute("""
            SELECT expr_id AS word, COUNT(*) AS attempts, SUM(result = 'unknown') AS missed,
                   (SELECT result FROM quiz_attempts q2 WHERE q2.kind = 'word' AND q2.expr_id = q.expr_id
                     ORDER BY id DESC LIMIT 1) AS last_result,
                   MAX(at) AS last_at
            FROM quiz_attempts q WHERE kind = 'word' GROUP BY expr_id""").fetchall()
        quiz_days = {r["day"]: r["n"] for r in
                     c.execute("SELECT day, COUNT(*) AS n FROM quiz_attempts GROUP BY day")}
        recent = c.execute("SELECT lesson_id, at FROM checkins ORDER BY at DESC LIMIT 10").fetchall()

    # activity per day = quiz answers + check-ins
    activity: dict[str, int] = dict(quiz_days)
    for at in checkins.values():
        d = at[:10]
        activity[d] = activity.get(d, 0) + 1

    today = now().date()
    streak = 0
    day = today if today.isoformat() in activity else today - dt.timedelta(days=1)
    while day.isoformat() in activity:
        streak += 1
        day -= dt.timedelta(days=1)

    attempts = sum(r["attempts"] for r in per_expr) + sum(r["attempts"] for r in per_word)
    missed_total = sum(r["missed"] for r in per_expr) + sum(r["missed"] for r in per_word)
    mastered = sum(1 for r in per_expr if r["last_result"] == "known" and r["expr_id"] in exprs)
    words_mastered = sum(1 for r in per_word if r["last_result"] == "known" and r["word"] in book)

    weak = []
    for r in per_expr:
        if r["missed"] and r["expr_id"] in exprs:
            weak.append({**exprs[r["expr_id"]], "kind": "expr", "attempts": r["attempts"], "missed": r["missed"],
                         "last_result": r["last_result"], "last_at": r["last_at"]})
    for r in per_word:
        if r["missed"] and r["word"] in book:
            w = book[r["word"]]
            weak.append({"id": w["word"], "kind": "word", "phrase": w["display"], "meaning_zh": w["meaning"],
                         "lesson": w["lesson_id"], "attempts": r["attempts"], "missed": r["missed"],
                         "last_result": r["last_result"], "last_at": r["last_at"]})
    # still-unknown first, then most missed, then most recent (stable sorts, last key first)
    weak.sort(key=lambda w: w["last_at"] or "", reverse=True)
    weak.sort(key=lambda w: (w["last_result"] == "known", -w["missed"]))

    start = today - dt.timedelta(days=days - 1)
    calendar = [{"day": (start + dt.timedelta(days=i)).isoformat(),
                 "count": activity.get((start + dt.timedelta(days=i)).isoformat(), 0)} for i in range(days)]

    return {
        "totals": {
            "study_days": len(activity),
            "streak": streak,
            "lessons_done": sum(1 for lid in checkins if lid in lessons),
            "lessons_total": len(lessons),
            "expressions_mastered": mastered,
            "expressions_total": len(exprs),
            "words_mastered": words_mastered,
            "words_total": len(book),
            "quiz_attempts": attempts,
            "accuracy": round(100 * (attempts - missed_total) / attempts) if attempts else None,
        },
        "calendar": calendar,
        "weak": weak[:20],
        "recent": [{"lesson": r["lesson_id"], "title": lessons.get(r["lesson_id"], {}).get("title"), "at": r["at"]}
                   for r in recent],
    }
