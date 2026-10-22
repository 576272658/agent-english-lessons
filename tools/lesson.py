#!/usr/bin/env python3
"""Lesson pipeline CLI. See AGENTS.md for the full workflow.

Mechanical steps only (download, clip, transcribe, TTS, publish, serve).
The language work -- writing lesson.json -- is done by an AI agent
following docs/lesson-format.md. The web app (tools/server.py) reads
lessons/ live, so a finished lesson shows up on the next page refresh.
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
DATA_DIR = ROOT / "data"          # progress.db, server.log
READY_FILE = "ready.json"         # written by `finish`; marks a lesson as published

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
    dirs = [p for p in LESSONS.iterdir() if p.is_dir() and re.fullmatch(r"EP\d{3,}", p.name)]
    return sorted(dirs, key=lambda p: int(p.name[2:]))  # numeric: EP999 < EP1000


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
    ready = path / READY_FILE
    if not ready.exists():
        return "3-needs-finish"
    if (path / "lesson.json").stat().st_mtime > ready.stat().st_mtime:
        return "3-needs-finish"    # lesson.json edited after last finish
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


# ---------- lesson data for the web app ----------

def is_published(path: Path) -> bool:
    """Finished at least once. Later edits show up live as long as lesson.json stays valid."""
    return (path / READY_FILE).exists() and (path / "lesson.json").exists()


def lesson_payload(out: Path) -> dict:
    """Everything the lesson page needs. Media paths are relative to the site root."""
    data = read_json(out / "lesson.json")
    source = read_json(out / "source.json")
    base = f"media/{out.name}"
    tts_dir = out / "tts"

    def tts_path(name: str) -> str | None:
        return f"{base}/tts/{name}" if (tts_dir / name).exists() else None

    has_video = source.get("has_video", False)
    return {
        "id": out.name,
        "title": data["title"],
        "topic": data["topic"],
        "notes": data.get("notes", ""),
        "source": source,
        "has_video": has_video,
        "media": f"{base}/{'clip.mp4' if has_video else 'audio.mp3'}",
        "audio": f"{base}/audio.mp3",
        "sentences": [{**s, "tts": tts_path(tts_name("s", s["id"], s["en"]))} for s in data["sentences"]],
        "expressions": [{**e, "lesson": out.name, "answer": quiz_answer(e),
                         "tts": tts_path(tts_name("e-", e["id"], quiz_answer(e)))}
                        for e in data["expressions"]],
    }


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
    write_json(out / READY_FILE, {"finished_at": dt.datetime.now().astimezone().isoformat(timespec="seconds")})
    print(f"[{out.name}] done -- published. It now appears in the web app (refresh the page).")


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


def cmd_expressions(args) -> None:
    """Existing expression ids, so a new lesson reuses them for the same expression."""
    seen: dict[str, dict] = {}
    for d in lesson_dirs():
        if not (d / "lesson.json").exists():
            continue
        try:
            data = read_json(d / "lesson.json")
        except json.JSONDecodeError:
            continue
        for e in data.get("expressions", []):
            if isinstance(e, dict) and e.get("id"):
                entry = seen.setdefault(e["id"], {"id": e["id"], "phrase": e.get("phrase", ""),
                                                  "meaning_zh": e.get("meaning_zh", ""), "lessons": []})
                entry["lessons"].append(d.name)
    rows = sorted(seen.values(), key=lambda e: e["id"])
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    for e in rows:
        print(f"{e['id']:36s} {e['phrase']:32s} {e['meaning_zh']}  [{', '.join(e['lessons'])}]")
    print(f"({len(rows)} expressions)")


# ---------- server / progress / service ----------

def cmd_serve(args) -> None:
    if args.base_path:
        os.environ["LESSON_BASE_PATH"] = args.base_path  # read by server at import
    import uvicorn

    import server

    print(f"Serving on http://{args.host}:{args.port}/  (lessons: {LESSONS}, data: {DATA_DIR})")
    uvicorn.run(server.app, host=args.host, port=args.port, log_level="warning",
                proxy_headers=True, forwarded_allow_ips="*")


def cmd_progress(args) -> None:
    import progress

    lessons = {d.name: lesson_payload(d) for d in lesson_dirs() if is_published(d)}
    report = progress.stats(lessons, days=args.days)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    t = report["totals"]
    print(f"Study days: {t['study_days']}  current streak: {t['streak']}  "
          f"lessons done: {t['lessons_done']}/{t['lessons_total']}")
    print(f"Expressions mastered: {t['expressions_mastered']}/{t['expressions_total']}  "
          f"quiz accuracy: {t['accuracy'] if t['accuracy'] is not None else '-'}%")
    if report["weak"]:
        print("\nWeak expressions (most missed first):")
        for w in report["weak"]:
            print(f"  {w['phrase']:32s} {w['meaning_zh']}  missed {w['missed']}/{w['attempts']}  ({w['lesson']})")
    else:
        print("\nNo missed expressions yet.")


SERVICE_LABEL = "com.agent-english-lessons.server"


def cmd_service(args) -> None:
    if sys.platform != "darwin":
        die("`./lesson service` manages a macOS launchd job. On Linux, create a systemd unit that runs "
            f"`{ROOT / 'lesson'} serve --port <port>` in {ROOT} (see DEPLOY.md).")
    plist = Path.home() / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{SERVICE_LABEL}"

    def launchctl(*a, check=False):
        return subprocess.run(["launchctl", *a], capture_output=True, text=True, check=check)

    if args.action == "install":
        if not args.port:
            die("--port is required for install")
        DATA_DIR.mkdir(exist_ok=True)
        uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
        path_env = ":".join(dict.fromkeys([str(Path(uv).parent), "/opt/homebrew/bin", "/usr/local/bin",
                                           "/usr/bin", "/bin", "/usr/sbin", "/sbin"]))
        base_arg = (f"\n    <string>--base-path</string><string>{args.base_path}</string>"
                    if args.base_path else "")
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{SERVICE_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{uv}</string><string>run</string><string>--quiet</string>
    <string>python</string><string>tools/lesson.py</string>
    <string>serve</string><string>--host</string><string>{args.host}</string>
    <string>--port</string><string>{args.port}</string>{base_arg}
  </array>
  <key>WorkingDirectory</key><string>{ROOT}</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>{path_env}</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{DATA_DIR / 'server.log'}</string>
  <key>StandardErrorPath</key><string>{DATA_DIR / 'server.log'}</string>
</dict>
</plist>
""", encoding="utf-8")
        launchctl("bootout", target)
        r = launchctl("bootstrap", domain, str(plist))
        if r.returncode != 0:
            die(f"launchctl bootstrap failed: {r.stderr.strip()}")
        print(f"installed {plist}\nserver starts at login and restarts if it crashes: "
              f"http://{args.host}:{args.port}/\nlog: {DATA_DIR / 'server.log'}")
    elif args.action == "uninstall":
        launchctl("bootout", target)
        plist.unlink(missing_ok=True)
        print("service removed")
    elif args.action == "restart":
        r = launchctl("kickstart", "-k", target)
        if r.returncode != 0:
            die(f"restart failed (is the service installed?): {r.stderr.strip()}")
        print("service restarted")
    else:  # status
        r = launchctl("print", target)
        if not plist.exists() or r.returncode != 0:
            print("service not installed")
            return
        state = re.search(r"^\s*state = (.+)$", r.stdout, re.M)
        pid = re.search(r"^\s*pid = (\d+)$", r.stdout, re.M)
        print(f"state: {state.group(1) if state else '?'}  pid: {pid.group(1) if pid else '-'}  plist: {plist}")


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

    f = sub.add_parser("finish", help="validate, generate TTS, publish to the web app")
    f.add_argument("lesson")
    f.set_defaults(func=cmd_finish)

    s = sub.add_parser("status", help="list lessons and what step each is at")
    s.set_defaults(func=cmd_status)

    ex = sub.add_parser("expressions", help="list existing expression ids (reuse them in new lessons)")
    ex.add_argument("--json", action="store_true")
    ex.set_defaults(func=cmd_expressions)

    sv = sub.add_parser("serve", help="run the web app in the foreground")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--base-path", default=os.environ.get("LESSON_BASE_PATH", ""),
                    help="only if a reverse proxy forwards /english/... WITHOUT stripping the prefix")
    sv.set_defaults(func=cmd_serve)

    pr = sub.add_parser("progress", help="learner's study stats and most-missed expressions")
    pr.add_argument("--json", action="store_true")
    pr.add_argument("--days", type=int, default=112, help="activity window for the calendar")
    pr.set_defaults(func=cmd_progress)

    se = sub.add_parser("service", help="macOS: run the web app at login via launchd")
    se.add_argument("action", choices=["install", "uninstall", "restart", "status"])
    se.add_argument("--port", type=int)
    se.add_argument("--host", default="127.0.0.1")
    se.add_argument("--base-path", default="", help="see `serve --help`")
    se.set_defaults(func=cmd_service)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
