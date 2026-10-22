"""Learner progress in SQLite (data/progress.db): check-ins, favorites, quiz attempts.

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
CREATE TABLE IF NOT EXISTS quiz_attempts (
    id          INTEGER PRIMARY KEY,
    at          TEXT NOT NULL,
    day         TEXT NOT NULL,           -- YYYY-MM-DD, server local date
    lesson_id   TEXT,                    -- lesson the expression came from
    expr_id     TEXT NOT NULL,
    result      TEXT NOT NULL CHECK (result IN ('known', 'unknown')),
    mode        TEXT NOT NULL,           -- typing | flash
    wrong_tries INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS quiz_attempts_expr ON quiz_attempts (expr_id);
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


def add_attempt(expr_id: str, result: str, mode: str, lesson_id: str | None, wrong_tries: int) -> None:
    t = now()
    with db() as c:
        c.execute("INSERT INTO quiz_attempts (at, day, lesson_id, expr_id, result, mode, wrong_tries) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (t.isoformat(timespec="seconds"), t.date().isoformat(), lesson_id, expr_id, result, mode,
                   wrong_tries))


# ---------- reads ----------

def snapshot() -> dict:
    """What every page needs on load: check-ins and favorites."""
    with db() as c:
        return {
            "checkins": {r["lesson_id"]: r["at"] for r in c.execute("SELECT * FROM checkins")},
            "favorites": [r["expr_id"] for r in c.execute("SELECT expr_id FROM favorites ORDER BY at")],
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
            FROM quiz_attempts q GROUP BY expr_id""").fetchall()
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

    attempts = sum(r["attempts"] for r in per_expr)
    missed_total = sum(r["missed"] for r in per_expr)
    mastered = sum(1 for r in per_expr if r["last_result"] == "known" and r["expr_id"] in exprs)

    weak = []
    for r in per_expr:
        if r["missed"] and r["expr_id"] in exprs:
            weak.append({**exprs[r["expr_id"]], "attempts": r["attempts"], "missed": r["missed"],
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
            "quiz_attempts": attempts,
            "accuracy": round(100 * (attempts - missed_total) / attempts) if attempts else None,
        },
        "calendar": calendar,
        "weak": weak[:20],
        "recent": [{"lesson": r["lesson_id"], "title": lessons.get(r["lesson_id"], {}).get("title"), "at": r["at"]}
                   for r in recent],
    }
