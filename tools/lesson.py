#!/usr/bin/env python3
"""Lesson pipeline CLI. See AGENTS.md for the full workflow.

Mechanical steps only (download, clip, transcribe, TTS, HTML build).
The language work -- writing lesson.json -- is done by an AI agent
following docs/lesson-format.md.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LESSONS = ROOT / "lessons"
TEMPLATES = ROOT / "tools" / "templates"
ASSETS = ROOT / "assets"

USE_MLX = sys.platform == "darwin" and platform.machine() == "arm64"
WHISPER_MODEL = os.environ.get(
    "LESSON_WHISPER_MODEL",
    "mlx-community/whisper-large-v3-turbo" if USE_MLX else "large-v3-turbo",
)
TTS_VOICE = os.environ.get("LESSON_TTS_VOICE", "en-US-AvaNeural")
LONG_CLIP_WARNING = 180  # seconds
SCHEMA_VERSION = 1


# ---------- helpers ----------

def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        die(f"command failed: {' '.join(cmd)}\n{proc.stderr[-2000:]}")


def parse_time(value: str | None) -> float | None:
    """Accept 90, 90.5, 1:30, 01:02:03."""
    if value is None:
        return None
    parts = value.strip().split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        die(f"bad time '{value}', use seconds or mm:ss")
    total = 0.0
    for n in nums:
        total = total * 60 + n
    return total


def fmt_ts(sec: float) -> str:
    m, s = divmod(sec, 60)
    return f"{int(m):02d}:{s:06.3f}"


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        die(f"ffprobe cannot read {path}")
    data = json.loads(out.stdout)
    types = {s.get("codec_type") for s in data.get("streams", [])}
    return {"duration": float(data["format"]["duration"]), "has_video": "video" in types}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def lesson_dirs() -> list[Path]:
    if not LESSONS.exists():
        return []
    return sorted(p for p in LESSONS.iterdir() if p.is_dir() and re.fullmatch(r"EP\d{3,}", p.name))


def resolve_lesson(lesson_id: str) -> Path:
    lid = lesson_id.upper()
    if lid.isdigit():
        lid = f"EP{int(lid):03d}"
    elif re.fullmatch(r"EP\d+", lid):
        lid = f"EP{int(lid[2:]):03d}"
    path = LESSONS / lid
    if not path.is_dir():
        die(f"lesson {lid} not found (run `./lesson status`)")
    return path


def next_lesson_id() -> str:
    nums = [int(p.name[2:]) for p in lesson_dirs()]
    return f"EP{(max(nums) + 1 if nums else 1):03d}"


def tts_name(prefix: str, key, text: str) -> str:
    digest = hashlib.sha1(f"{TTS_VOICE}|{text}".encode()).hexdigest()[:8]
    return f"{prefix}{key}-{digest}.mp3"


def tts_jobs(data: dict) -> dict[str, str]:
    """filename -> text for every sentence and every expression's quiz answer."""
    jobs = {tts_name("s", s["id"], s["en"]): s["en"] for s in data["sentences"]}
    for e in data["expressions"]:
        ans = quiz_answer(e)
        jobs[tts_name("e-", e["id"], ans)] = ans
    return jobs


ANSWER_RE = re.compile(r"^[A-Za-z0-9'’\- ]+$")


def quiz_answer(expr: dict) -> str:
    """What the learner types in the quiz: `answer`, else `phrase` without ellipses."""
    if expr.get("answer"):
        return expr["answer"].strip()
    return re.sub(r"\s+", " ", re.sub(r"…|\.\.\.", " ", expr.get("phrase", ""))).strip()


def stage(path: Path) -> str:
    if not (path / "transcript.json").exists():
        return "1-media"           # media only, needs transcribe
    if not (path / "lesson.json").exists():
        return "2-needs-lesson-json"
    if not (path / "index.html").exists():
        return "3-needs-finish"
    if (path / "lesson.json").stat().st_mtime > (path / "index.html").stat().st_mtime:
        return "3-needs-finish"    # lesson.json edited after last build
    return "4-done"


# ---------- new / transcribe ----------

def cmd_new(args) -> None:
    start, end = parse_time(args.start), parse_time(args.end)
    if start is not None and end is not None and end <= start:
        die("--end must be after --start")

    lid = next_lesson_id()
    out = LESSONS / lid
    out.mkdir(parents=True)
    print(f"[{lid}] created {out}")

    try:
        source = fetch_media(args.source, out, start, end)
    except SystemExit:
        shutil.rmtree(out, ignore_errors=True)
        raise
    write_json(out / "source.json", source)

    if source["duration"] > LONG_CLIP_WARNING:
        print(f"WARNING: clip is {source['duration']:.0f}s long. Lessons work best at 20-90s; "
              f"consider re-running with --start/--end.")

    transcribe(out, args.model)
    print_next_steps(out)


def fetch_media(src: str, out: Path, start: float | None, end: float | None) -> dict:
    raw = out / "_raw"
    raw.mkdir()
    meta = {"input": src, "url": None, "video_title": None, "channel": None,
            "clip_start": start, "clip_end": end,
            "created": dt.date.today().isoformat()}

    if re.match(r"https?://", src):
        meta["url"] = src
        print(f"[{out.name}] downloading {src}")
        cmd = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--quiet", "--no-warnings",
               "-f", "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720]/b",
               "--merge-output-format", "mp4",
               "--print-to-file", "%(.{title,channel,uploader,webpage_url})j", str(raw / "info.json"),
               "-o", str(raw / "source.%(ext)s")]
        if start is not None or end is not None:
            cmd += ["--download-sections", f"*{start or 0}-{end if end is not None else 'inf'}",
                    "--force-keyframes-at-cuts"]
        run(cmd + [src])
        info_path = raw / "info.json"
        if info_path.exists():
            info = json.loads(info_path.read_text().strip().splitlines()[-1])
            meta["video_title"] = info.get("title")
            meta["channel"] = info.get("channel") or info.get("uploader")
            meta["url"] = info.get("webpage_url") or src
        files = [p for p in raw.iterdir() if p.name.startswith("source.")]
        if not files:
            die("download produced no media file")
        media = files[0]
        trim = None  # already trimmed by yt-dlp
    else:
        media = Path(src).expanduser().resolve()
        if not media.exists():
            die(f"file not found: {media}")
        meta["input"] = str(media)
        meta["video_title"] = media.stem
        trim = (start, end)

    info = probe(media)
    ff = ["ffmpeg", "-y", "-v", "error"]
    if trim and trim[0] is not None:
        ff += ["-ss", str(trim[0])]
    if trim and trim[1] is not None:
        ff += ["-to", str(trim[1])]
    ff += ["-i", str(media)]

    if info["has_video"]:
        print(f"[{out.name}] encoding clip.mp4")
        run(ff + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-vf", "scale=-2:'min(720,ih)'",
                  "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out / "clip.mp4")])
        run(["ffmpeg", "-y", "-v", "error", "-i", str(out / "clip.mp4"), "-vn",
             "-c:a", "libmp3lame", "-q:a", "4", str(out / "audio.mp3")])
    else:
        print(f"[{out.name}] audio-only input")
        run(ff + ["-vn", "-c:a", "libmp3lame", "-q:a", "4", str(out / "audio.mp3")])

    shutil.rmtree(raw)
    final = probe(out / ("clip.mp4" if info["has_video"] else "audio.mp3"))
    meta["duration"] = round(final["duration"], 3)
    meta["has_video"] = info["has_video"]
    return meta


def transcribe(out: Path, model: str) -> None:
    print(f"[{out.name}] transcribing with {model} (first run downloads the model)")
    audio = str(out / "audio.mp3")
    segments = []
    if USE_MLX:
        import mlx_whisper  # imported lazily: slow to load

        result = mlx_whisper.transcribe(audio, path_or_hf_repo=model,
                                        language="en", word_timestamps=True)
        raw = [(s["start"], s["end"], s["text"],
                [(w["word"], w["start"], w["end"]) for w in s.get("words", [])])
               for s in result["segments"]]
    else:
        from faster_whisper import WhisperModel

        segs, _ = WhisperModel(model, compute_type="auto").transcribe(
            audio, language="en", word_timestamps=True)
        raw = [(s.start, s.end, s.text, [(w.word, w.start, w.end) for w in (s.words or [])])
               for s in segs]
    for start, end, text, words in raw:
        segments.append({"start": round(start, 2), "end": round(end, 2), "text": text.strip(),
                         "words": [{"w": w.strip(), "start": round(ws, 2), "end": round(we, 2)}
                                   for w, ws, we in words]})
    write_json(out / "transcript.json", {"language": "en", "model": model, "segments": segments})

    lines = [f"[{fmt_ts(s['start'])} - {fmt_ts(s['end'])}] {s['text']}" for s in segments]
    (out / "transcript.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[{out.name}] transcript: {len(segments)} segments -> transcript.txt / transcript.json")


def cmd_transcribe(args) -> None:
    out = resolve_lesson(args.lesson)
    transcribe(out, args.model)


def print_next_steps(out: Path) -> None:
    rel = out.relative_to(ROOT)
    print(f"""
NEXT STEP (AI agent):
  1. Read docs/lesson-format.md
  2. Read {rel}/transcript.txt and {rel}/source.json
  3. Write {rel}/lesson.json
  4. Run: ./lesson finish {out.name}""")


# ---------- validate ----------

SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def validate(out: Path) -> list[str]:
    errors: list[str] = []
    path = out / "lesson.json"
    if not path.exists():
        return [f"{path.relative_to(ROOT)} does not exist"]
    try:
        data = read_json(path)
    except json.JSONDecodeError as e:
        return [f"lesson.json is not valid JSON: {e}"]

    source = read_json(out / "source.json") if (out / "source.json").exists() else {}
    duration = source.get("duration")

    def need(obj, key, typ, where):
        if key not in obj:
            errors.append(f"{where}: missing '{key}'")
            return None
        if not isinstance(obj[key], typ) or (typ is str and not obj[key].strip()):
            errors.append(f"{where}: '{key}' must be a non-empty {typ.__name__ if not isinstance(typ, tuple) else 'number'}")
            return None
        return obj[key]

    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    need(data, "title", str, "lesson")
    need(data, "topic", str, "lesson")

    exprs = data.get("expressions")
    expr_ids = set()
    if not isinstance(exprs, list) or not exprs:
        errors.append("lesson: 'expressions' must be a non-empty list")
        exprs = []
    for i, e in enumerate(exprs):
        where = f"expressions[{i}]"
        eid = need(e, "id", str, where)
        if eid:
            if not SLUG_RE.match(eid):
                errors.append(f"{where}: id '{eid}' must be lowercase-kebab-case")
            if eid in expr_ids:
                errors.append(f"{where}: duplicate id '{eid}'")
            expr_ids.add(eid)
        for key in ("phrase", "phonetic", "meaning_zh"):
            need(e, key, str, where)
        if "answer" in e and (not isinstance(e["answer"], str) or not e["answer"].strip()):
            errors.append(f"{where}: 'answer' must be a non-empty string when present")
        elif isinstance(e.get("phrase"), str):
            ans = quiz_answer(e)
            if not ANSWER_RE.match(ans):
                errors.append(f"{where}: quiz answer '{ans}' may contain only letters, digits, spaces, "
                              f"apostrophes and hyphens -- set 'answer' to the text the learner should type "
                              f"(e.g. phrase 'wear sth over one's ears' -> answer 'wear it over my ears')")
        ex = e.get("examples")
        if not isinstance(ex, list) or not ex:
            errors.append(f"{where}: 'examples' must be a non-empty list")
        else:
            for j, x in enumerate(ex):
                need(x, "en", str, f"{where}.examples[{j}]")
                need(x, "zh", str, f"{where}.examples[{j}]")
        for key in ("synonyms", "related"):
            for j, x in enumerate(e.get(key, []) or []):
                need(x, "phrase", str, f"{where}.{key}[{j}]")
                need(x, "zh", str, f"{where}.{key}[{j}]")

    sents = data.get("sentences")
    if not isinstance(sents, list) or not sents:
        errors.append("lesson: 'sentences' must be a non-empty list")
        sents = []
    used = set()
    prev_end = -1.0
    for i, s in enumerate(sents):
        where = f"sentences[{i}]"
        if s.get("id") != i + 1:
            errors.append(f"{where}: id must be {i + 1} (sequential from 1)")
        en = need(s, "en", str, where)
        zh = need(s, "zh", str, where)
        need(s, "ipa", str, where)
        st, en_ts = s.get("start"), s.get("end")
        if not isinstance(st, (int, float)) or not isinstance(en_ts, (int, float)):
            errors.append(f"{where}: 'start' and 'end' must be numbers (seconds)")
        else:
            if en_ts <= st:
                errors.append(f"{where}: end must be greater than start")
            if st < prev_end - 0.05:
                errors.append(f"{where}: start {st} overlaps previous sentence end {prev_end}")
            if duration and en_ts > duration + 0.5:
                errors.append(f"{where}: end {en_ts} is past clip duration {duration}")
            prev_end = en_ts
        for j, h in enumerate(s.get("highlights", []) or []):
            hw = f"{where}.highlights[{j}]"
            hen, hzh, hex_ = need(h, "en", str, hw), need(h, "zh", str, hw), need(h, "expr", str, hw)
            if hen and en and hen not in en:
                errors.append(f"{hw}: en '{hen}' is not an exact substring of the sentence")
            if hzh and zh and hzh not in zh:
                errors.append(f"{hw}: zh '{hzh}' is not an exact substring of the sentence's zh")
            if hex_:
                if hex_ not in expr_ids:
                    errors.append(f"{hw}: expr '{hex_}' does not match any expressions[].id")
                used.add(hex_)
    for eid in expr_ids - used:
        errors.append(f"expressions: '{eid}' is never highlighted in any sentence")
    return errors


def cmd_validate(args) -> None:
    out = resolve_lesson(args.lesson)
    errors = validate(out)
    if errors:
        print(f"[{out.name}] lesson.json has {len(errors)} problem(s):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print(f"[{out.name}] lesson.json OK")


# ---------- tts ----------

def make_tts(out: Path, data: dict) -> None:
    import edge_tts

    tts_dir = out / "tts"
    tts_dir.mkdir(exist_ok=True)
    wanted = tts_jobs(data)
    for old in tts_dir.glob("*.mp3"):
        if old.name not in wanted:
            old.unlink()
    todo = {n: t for n, t in wanted.items() if not (tts_dir / n).exists()}
    if not todo:
        return
    print(f"[{out.name}] generating {len(todo)} TTS clip(s) with {TTS_VOICE}")

    async def go():
        for name, text in todo.items():
            await edge_tts.Communicate(text, TTS_VOICE).save(str(tts_dir / name))

    try:
        asyncio.run(go())
    except Exception as e:  # network errors etc. -- page still works without TTS
        print(f"WARNING: TTS failed ({e}); '标准美音' buttons will be hidden. Re-run finish later.")


# ---------- build ----------

def embed(template: str, data) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return template.replace("/*__DATA__*/null", payload)


def collect() -> list[dict]:
    items = []
    for d in lesson_dirs():
        if not (d / "lesson.json").exists():
            continue
        items.append({"dir": d, "lesson": read_json(d / "lesson.json"),
                      "source": read_json(d / "source.json") if (d / "source.json").exists() else {}})
    return items


def build_lesson(out: Path, items: list[dict]) -> None:
    data = read_json(out / "lesson.json")
    source = read_json(out / "source.json")
    ids = [it["dir"].name for it in items]
    pos = ids.index(out.name)
    tts_dir = out / "tts"

    def tts_path(name: str) -> str | None:
        return f"tts/{name}" if (tts_dir / name).exists() else None

    sentences = [{**s, "tts": tts_path(tts_name("s", s["id"], s["en"]))} for s in data["sentences"]]
    expressions = [{**e, "answer": quiz_answer(e), "tts": tts_path(tts_name("e-", e["id"], quiz_answer(e)))}
                   for e in data["expressions"]]
    page = {
        "id": out.name,
        **{k: data[k] for k in ("title", "topic")},
        "notes": data.get("notes", ""),
        "source": source,
        "media": "clip.mp4" if source.get("has_video") else "audio.mp3",
        "has_video": source.get("has_video", False),
        "sentences": sentences,
        "expressions": expressions,
        "prev": ids[pos - 1] if pos > 0 else None,
        "next": ids[pos + 1] if pos + 1 < len(ids) else None,
    }
    html = embed((TEMPLATES / "lesson.html").read_text(encoding="utf-8"), page)
    (out / "index.html").write_text(html, encoding="utf-8")


def build_site(items: list[dict]) -> None:
    ASSETS.mkdir(exist_ok=True)
    for name in ("style.css", "quiz.js"):
        shutil.copy(TEMPLATES / name, ASSETS / name)

    catalog = [{
        "id": it["dir"].name,
        "title": it["lesson"]["title"],
        "topic": it["lesson"]["topic"],
        "channel": it["source"].get("channel"),
        "created": it["source"].get("created"),
        "duration": it["source"].get("duration"),
        "n_sentences": len(it["lesson"]["sentences"]),
        "n_expressions": len(it["lesson"]["expressions"]),
        "built": (it["dir"] / "index.html").exists(),
    } for it in items]
    (ROOT / "index.html").write_text(
        embed((TEMPLATES / "index.html").read_text(encoding="utf-8"), catalog), encoding="utf-8")

    # expression library: merge by id across lessons, keep every source sentence
    library: dict[str, dict] = {}
    for it in items:
        lid = it["dir"].name
        sents = {s["id"]: s for s in it["lesson"]["sentences"]}
        for e in it["lesson"]["expressions"]:
            entry = library.setdefault(e["id"], {**e, "answer": quiz_answer(e), "tts": None, "occurrences": []})
            tts = it["dir"] / "tts" / tts_name("e-", e["id"], quiz_answer(e))
            if entry["tts"] is None and tts.exists():
                entry["tts"] = tts.relative_to(ROOT).as_posix()
            for s in sents.values():
                if any(h.get("expr") == e["id"] for h in s.get("highlights", [])):
                    entry["occurrences"].append({"lesson": lid, "title": it["lesson"]["title"],
                                                 "en": s["en"], "zh": s["zh"]})
    lib = sorted(library.values(), key=lambda e: e["phrase"].lower())
    (ROOT / "expressions.html").write_text(
        embed((TEMPLATES / "expressions.html").read_text(encoding="utf-8"), lib), encoding="utf-8")
    write_json(ROOT / "expressions.json", lib)


def cmd_finish(args) -> None:
    out = resolve_lesson(args.lesson)
    errors = validate(out)
    if errors:
        print(f"[{out.name}] cannot finish, lesson.json has {len(errors)} problem(s):")
        for e in errors:
            print(f"  - {e}")
        print("Fix lesson.json (see docs/lesson-format.md) and run finish again.")
        sys.exit(1)
    make_tts(out, read_json(out / "lesson.json"))
    items = collect()
    for it in items:  # rebuild all so prev/next links stay right
        if not validate(it["dir"]):
            build_lesson(it["dir"], items)
    build_site(items)
    print(f"[{out.name}] done -> {out / 'index.html'}")
    print(f"Catalog: {ROOT / 'index.html'}")


def cmd_build(args) -> None:
    items = collect()
    ok = 0
    for it in items:
        errs = validate(it["dir"])
        if errs:
            print(f"[{it['dir'].name}] skipped: {len(errs)} validation problem(s)")
            continue
        build_lesson(it["dir"], items)
        ok += 1
    build_site(items)
    print(f"built {ok} lesson(s); catalog at {ROOT / 'index.html'}")


def cmd_serve(args) -> None:
    import functools
    import http.server
    import socket

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer((host, args.port), handler)
    print(f"Serving {ROOT}")
    print(f"  this computer: http://127.0.0.1:{args.port}/")
    if args.lan:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                s.connect(("10.255.255.255", 1))
                ip = s.getsockname()[0]
            except OSError:
                ip = "<this-computer-ip>"
        print(f"  phone/tablet on the same Wi-Fi: http://{ip}:{args.port}/")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def cmd_status(args) -> None:
    dirs = lesson_dirs()
    if not dirs:
        print("no lessons yet. Start one with: ./lesson new <youtube-url|file> [--start mm:ss --end mm:ss]")
        return
    for d in dirs:
        title = ""
        if (d / "lesson.json").exists():
            try:
                title = read_json(d / "lesson.json").get("title", "")
            except json.JSONDecodeError:
                title = "(invalid lesson.json)"
        print(f"{d.name}  {stage(d):22s} {title}")


# ---------- main ----------

def main() -> None:
    p = argparse.ArgumentParser(prog="lesson", description="English lesson pipeline (see AGENTS.md)")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="download/copy media, clip, transcribe -> new lesson folder")
    n.add_argument("source", help="YouTube (or any yt-dlp) URL, or a local video/audio file")
    n.add_argument("--start", help="clip start, seconds or mm:ss")
    n.add_argument("--end", help="clip end, seconds or mm:ss")
    n.add_argument("--model", default=WHISPER_MODEL)
    n.set_defaults(func=cmd_new)

    t = sub.add_parser("transcribe", help="re-run transcription for a lesson")
    t.add_argument("lesson")
    t.add_argument("--model", default=WHISPER_MODEL)
    t.set_defaults(func=cmd_transcribe)

    v = sub.add_parser("validate", help="check lesson.json against docs/lesson-format.md")
    v.add_argument("lesson")
    v.set_defaults(func=cmd_validate)

    f = sub.add_parser("finish", help="validate, generate TTS, build pages")
    f.add_argument("lesson")
    f.set_defaults(func=cmd_finish)

    b = sub.add_parser("build", help="rebuild all HTML pages (no TTS)")
    b.set_defaults(func=cmd_build)

    s = sub.add_parser("status", help="list lessons and what step each is at")
    s.set_defaults(func=cmd_status)

    sv = sub.add_parser("serve", help="serve the lessons over HTTP (optional; pages also open as plain files)")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--lan", action="store_true", help="also reachable from phones on the same network")
    sv.set_defaults(func=cmd_serve)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
