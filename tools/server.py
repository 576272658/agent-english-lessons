"""Web app: serves web/ (the UI), lesson media, and a small JSON API.

Lessons are read live from lessons/ on every request (cached by file mtime),
so a lesson published with `./lesson finish` appears on the next refresh.
Progress (check-ins, favorites, quiz attempts) is stored in data/progress.db.

All UI URLs are relative, so the app also works behind a reverse proxy under
a sub-path (e.g. `tailscale serve --set-path /english`). If the proxy does not
strip the prefix, set LESSON_BASE_PATH=/english.
"""

from __future__ import annotations

import mimetypes
import os
import re
import sys
from pathlib import Path

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

import dictionary
import lesson as L
import progress

WEB = L.ROOT / "web"
mimetypes.add_type("application/manifest+json", ".webmanifest")
LESSON_ID_RE = re.compile(r"^EP\d{3,}$")
MEDIA_RE = re.compile(r"^(clip\.mp4|audio\.mp3|tts/[A-Za-z0-9_-]+\.mp3)$")
EXPR_ID_RE = L.SLUG_RE
WORD_AUDIO = L.DATA_DIR / "word-audio"

_cache: dict[str, tuple[tuple, dict | None]] = {}


def _mtimes(d: Path) -> tuple:
    return tuple((d / n).stat().st_mtime_ns if (d / n).exists() else 0
                 for n in ("lesson.json", L.READY_FILE, "source.json", "tts"))


def published() -> dict[str, dict]:
    """id -> payload for every published lesson whose lesson.json is currently valid."""
    out = {}
    for d in L.lesson_dirs():
        if not L.is_published(d):
            continue
        key = _mtimes(d)
        hit = _cache.get(d.name)
        if not hit or hit[0] != key:
            errors = L.validate(d)
            if errors:
                print(f"[server] {d.name} hidden: lesson.json invalid ({errors[0]})", file=sys.stderr)
                payload = None
            else:
                payload = L.lesson_payload(d)
            _cache[d.name] = hit = (key, payload)
        if hit[1] is not None:
            out[d.name] = hit[1]
    return out


# ---------- lessons ----------

async def list_lessons(request: Request):
    return JSONResponse([{
        "id": lid,
        "title": p["title"],
        "topic": p["topic"],
        "channel": p["source"].get("channel"),
        "created": p["source"].get("created"),
        "duration": p["source"].get("duration"),
        "n_sentences": len(p["sentences"]),
        "n_expressions": len(p["expressions"]),
    } for lid, p in published().items()])


async def get_lesson(request: Request):
    lid = request.path_params["lesson_id"].upper()
    lessons = published()
    if lid not in lessons:
        raise HTTPException(404, "lesson not found")
    ids = list(lessons)
    pos = ids.index(lid)
    return JSONResponse({**lessons[lid],
                         "prev": ids[pos - 1] if pos > 0 else None,
                         "next": ids[pos + 1] if pos + 1 < len(ids) else None})


async def expressions(request: Request):
    """Merged expression library: one entry per expression id, with every source sentence."""
    library: dict[str, dict] = {}
    for lid, p in published().items():
        for e in p["expressions"]:
            entry = library.setdefault(e["id"], {**e, "occurrences": []})
            if not entry.get("tts") and e.get("tts"):
                entry["tts"] = e["tts"]
            for s in p["sentences"]:
                if any(h.get("expr") == e["id"] for h in s.get("highlights", [])):
                    entry["occurrences"].append({"lesson": lid, "title": p["title"], "en": s["en"], "zh": s["zh"]})
    return JSONResponse(sorted(library.values(), key=lambda e: e["phrase"].lower()))


async def media(request: Request):
    lid, path = request.path_params["lesson_id"], request.path_params["path"]
    if not LESSON_ID_RE.match(lid) or not MEDIA_RE.match(path):
        raise HTTPException(404)
    f = L.LESSONS / lid / path
    if not f.is_file():
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "public, max-age=86400"})


# ---------- progress ----------

async def get_progress(request: Request):
    return JSONResponse(progress.snapshot())


async def checkin(request: Request):
    lid = request.path_params["lesson_id"].upper()
    if not LESSON_ID_RE.match(lid):
        raise HTTPException(400, "bad lesson id")
    progress.set_checkin(lid, request.method == "PUT")
    return JSONResponse(progress.snapshot()["checkins"])


async def favorite(request: Request):
    eid = request.path_params["expr_id"]
    if not EXPR_ID_RE.match(eid):
        raise HTTPException(400, "bad expression id")
    progress.set_favorite(eid, request.method == "PUT")
    return JSONResponse(progress.snapshot()["favorites"])


async def attempt(request: Request):
    try:
        body = await request.json()
        eid, result, mode = body["expr"], body["result"], body.get("mode", "typing")
        lid = body.get("lesson")
        kind = body.get("kind", "expr")
        wrong = int(body.get("wrong_tries", 0))
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "expected {expr, kind, result, mode, lesson, wrong_tries}")
    id_ok = EXPR_ID_RE.match(str(eid)) if kind == "expr" else dictionary.WORD_RE.match(str(eid))
    if kind not in ("expr", "word") or not id_ok or result not in ("known", "unknown") \
            or mode not in ("typing", "flash") or (lid is not None and not LESSON_ID_RE.match(str(lid))):
        raise HTTPException(400, "invalid attempt")
    progress.add_attempt(eid, result, mode, lid, max(0, min(wrong, 10)), kind)
    return JSONResponse({"ok": True})


# ---------- dictionary + word book ----------

async def word_lookup(request: Request):
    raw = request.path_params["word"]
    if not dictionary.installed():
        return JSONResponse({"found": False, "error": "dictionary not installed (run ./lesson dict install)"},
                            status_code=503)
    entry = dictionary.lookup(raw)
    if entry is None:
        return JSONResponse({"found": False, "query": raw}, status_code=404)
    # book_key: what "add to word book" saves -- the clicked form when it has its own meaning ("excited")
    book_key = entry["surface"] if entry.get("surface_translation") else entry["key"]
    return JSONResponse({"found": True, **entry, "book_key": book_key,
                         "audio": f"api/word-audio/{entry.get('surface') or entry['key']}"})


async def word_audio(request: Request):
    """TTS for a single word, generated on first request and cached in data/word-audio/."""
    word = request.path_params["word"].lower()
    if not dictionary.WORD_RE.match(word):
        raise HTTPException(404)
    f = WORD_AUDIO / f"{word}.mp3"
    if not f.exists():
        import edge_tts

        WORD_AUDIO.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".part")
        try:
            await edge_tts.Communicate(word, L.TTS_VOICE).save(str(tmp))
            tmp.rename(f)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise HTTPException(503, "TTS unavailable")
    return FileResponse(f, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=604800"})


async def list_words(request: Request):
    return JSONResponse([{**w, "audio": f"api/word-audio/{w['word']}"} for w in progress.words()])


async def word_book(request: Request):
    raw = request.path_params["word"]
    if request.method == "DELETE":
        if not dictionary.WORD_RE.match(raw.lower()):
            raise HTTPException(400, "bad word")
        progress.remove_word(raw.lower())
        return JSONResponse(progress.snapshot()["words"])
    entry = dictionary.lookup(raw)
    if entry is None:
        raise HTTPException(404, "word not in dictionary")
    if entry.get("surface_translation") and raw.lower() == entry["surface"]:
        # save "excited" (兴奋的) itself rather than its base "excite" (刺激)
        entry = {**entry, "key": entry["surface"], "word": entry["surface"],
                 "phonetic": entry.get("surface_phonetic") or "", "translation": entry["surface_translation"]}
    try:
        body = await request.json()
    except ValueError:
        body = {}
    lid = body.get("lesson")
    if lid is not None and not LESSON_ID_RE.match(str(lid)):
        lid = None
    clip = lambda v: str(v)[:400] if v else None
    progress.add_word(entry, lid, clip(body.get("context_en")), clip(body.get("context_zh")))
    return JSONResponse(progress.snapshot()["words"])


async def stats(request: Request):
    days = min(max(int(request.query_params.get("days", 112)), 7), 366)
    return JSONResponse(progress.stats(published(), days=days))


async def health(request: Request):
    return JSONResponse({"ok": True, "lessons": len(published()), "dictionary": dictionary.installed()})


# ---------- app ----------

class NoCacheUI:
    """UI files change on `git pull`; make browsers revalidate them every load."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
                headers.append((b"cache-control", b"no-cache"))
                message = {**message, "headers": headers}
            await send(message)
        await self.app(scope, receive, send_wrapper)


class StripBasePath:
    def __init__(self, app, base: str):
        self.app, self.base = app, base.rstrip("/")

    async def __call__(self, scope, receive, send):
        if self.base and scope["type"] == "http" and scope["path"].startswith(self.base):
            scope = {**scope, "path": scope["path"][len(self.base):] or "/"}
        await self.app(scope, receive, send)


routes = [
    Route("/api/health", health),
    Route("/api/lessons", list_lessons),
    Route("/api/lessons/{lesson_id}", get_lesson),
    Route("/api/expressions", expressions),
    Route("/api/progress", get_progress),
    Route("/api/checkins/{lesson_id}", checkin, methods=["PUT", "DELETE"]),
    Route("/api/favorites/{expr_id}", favorite, methods=["PUT", "DELETE"]),
    Route("/api/attempts", attempt, methods=["POST"]),
    Route("/api/word/{word}", word_lookup),
    Route("/api/word-audio/{word}", word_audio),
    Route("/api/words", list_words),
    Route("/api/words/{word}", word_book, methods=["PUT", "DELETE"]),
    Route("/api/stats", stats),
    Route("/media/{lesson_id}/{path:path}", media),
    Mount("/", app=NoCacheUI(StaticFiles(directory=WEB, html=True))),
]

app = StripBasePath(Starlette(routes=routes), os.environ.get("LESSON_BASE_PATH", ""))
