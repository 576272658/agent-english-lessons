"""Find the clips of a long video that suit the learner: `./lesson scan <url>`.

1. Get a timed transcript of the whole video: YouTube subtitles if available
   (seconds, no download), else download the audio and run Whisper.
2. Slide a window (default 45 s) over it and score each window: share of known
   words for this learner (difficulty.py) and speaking density.
3. Print the best non-overlapping windows with ready-to-run `./lesson new` commands.

Transcripts are cached in data/scans/<video id>/ so re-scans are instant.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import difficulty
import lesson as L

SCANS = L.DATA_DIR / "scans"
MIN_WPM = 90          # windows with less speech than this are mostly silence/music


def _yt_dlp(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-warnings", *args],
                          capture_output=True, text=True)


def video_info(url: str) -> dict:
    r = _yt_dlp("-J", url)
    if r.returncode != 0:
        L.die(f"cannot read video info: {r.stderr.strip()[-500:]}")
    return json.loads(r.stdout)


def _units_from_json3(path: Path) -> list[dict]:
    """YouTube json3 captions -> [{start, end, text}] (one unit per caption event)."""
    events = json.loads(path.read_text(encoding="utf-8")).get("events", [])
    units = []
    for ev in events:
        text = "".join(seg.get("utf8", "") for seg in ev.get("segs") or []).replace("\n", " ").strip()
        if not text or text.startswith("["):  # [Music], [Applause]
            continue
        start = ev["tStartMs"] / 1000
        units.append({"start": start, "end": start + ev.get("dDurationMs", 0) / 1000, "text": text})
    # auto captions overlap (each event lasts until the next line is done); clip to the next start
    for a, b in zip(units, units[1:]):
        a["end"] = min(a["end"], b["start"]) if b["start"] > a["start"] else a["end"]
    return units


def transcript_units(url: str, info: dict, model: str) -> tuple[list[dict], str]:
    work = SCANS / info["id"]
    cached = work / "units.json"
    if cached.exists():
        data = L.read_json(cached)
        return data["units"], data["source"]
    work.mkdir(parents=True, exist_ok=True)

    has_subs = any(k.startswith("en") for k in (info.get("subtitles") or {})) or \
        any(k.startswith("en") for k in (info.get("automatic_captions") or {}))
    units, source = [], ""
    if has_subs:
        print("downloading subtitles ...")
        _yt_dlp("--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", "en,en-US,en-GB,en-orig",
                "--sub-format", "json3", "-o", str(work / "sub.%(ext)s"), url)
        files = sorted(work.glob("sub.*.json3"), key=lambda p: ("orig" in p.name, p.name))
        if files:
            units, source = _units_from_json3(files[0]), "youtube-subtitles"
    if not units:
        print("no usable subtitles; downloading audio and transcribing the whole video "
              f"({info.get('duration', 0) // 60} min) -- this can take a few minutes ...")
        r = _yt_dlp("-f", "ba/b", "-x", "--audio-format", "mp3", "-o", str(work / "audio.%(ext)s"), url)
        if r.returncode != 0 or not (work / "audio.mp3").exists():
            L.die(f"audio download failed: {r.stderr.strip()[-500:]}")
        L.transcribe(work, model)
        segs = L.read_json(work / "transcript.json")["segments"]
        units = [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in segs]
        source = "whisper"
        (work / "audio.mp3").unlink(missing_ok=True)
    L.write_json(cached, {"source": source, "units": units})
    return units, source


def windows(units: list[dict], length: float, level: str, known: set[str]) -> list[dict]:
    out = []
    for i, first in enumerate(units):
        chunk = []
        for u in units[i:]:
            if u["end"] - first["start"] > length * 1.25:
                break
            chunk.append(u)
        if not chunk or chunk[-1]["end"] - first["start"] < length * 0.6:
            continue
        start, end = first["start"], chunk[-1]["end"]
        text = " ".join(u["text"] for u in chunk)
        words = difficulty.word_count(text)
        wpm = words / (end - start) * 60 if end > start else 0
        if wpm < MIN_WPM:
            continue
        d = difficulty.analyze([u["text"] for u in chunk], level, known)
        out.append({"start": round(start, 1), "end": round(end, 1), "coverage": d["coverage"], "fit": d["fit"],
                    "fit_name": d["fit_name"], "vocab_level_name": d["vocab_level_name"], "wpm": round(wpm),
                    "unknown": [u["surface"] for u in d["unknown"]][:10], "text": text})
    return out


def pick(cands: list[dict], top: int) -> list[dict]:
    """Best coverage first; prefer real conversation speed; no overlaps."""
    cands = sorted(cands, key=lambda w: (-w["coverage"], abs(w["wpm"] - 160)))
    chosen: list[dict] = []
    for w in cands:
        if all(w["end"] <= c["start"] or w["start"] >= c["end"] for c in chosen):
            chosen.append(w)
        if len(chosen) == top:
            break
    return sorted(chosen, key=lambda w: w["start"])


def mmss(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


def cmd_scan(args) -> None:
    import dictionary

    if not dictionary.installed():
        L.die("dictionary not installed: ./lesson dict install")
    info = video_info(args.source)
    units, source = transcript_units(args.source, info, args.model)
    level, known = L.learner()
    best = pick(windows(units, args.length, level, known), args.top)
    whole = difficulty.analyze([u["text"] for u in units], level, known)

    if args.json:
        print(json.dumps({"video": {"id": info["id"], "title": info.get("title"), "channel": info.get("channel"),
                                    "duration": info.get("duration"), "transcript": source},
                          "whole_video": {k: whole[k] for k in ("coverage", "fit", "fit_name", "vocab_level_name")},
                          "level": whole["level_name"], "clips": best}, ensure_ascii=False, indent=2))
        return
    print(f"\n{info.get('title')} — {info.get('channel')} ({mmss(info.get('duration') or 0)}), transcript: {source}")
    print(f"learner level {whole['level_name']}; whole video: {whole['fit_name']} "
          f"(knows ~{whole['coverage']:.0%}), vocabulary {whole['vocab_level_name']}")
    if not best:
        print("no clip with enough continuous speech found")
        return
    print(f"\nBest ~{int(args.length)} s clips for this learner:")
    for n, w in enumerate(best, 1):
        print(f"\n{n}. {mmss(w['start'])}–{mmss(w['end'])}  {w['fit_name']} (knows {w['coverage']:.0%}), "
              f"vocab {w['vocab_level_name']}, {w['wpm']} wpm")
        print(f"   \"{w['text'][:160]}{'…' if len(w['text']) > 160 else ''}\"")
        if w["unknown"]:
            print(f"   likely unknown: {', '.join(w['unknown'])}")
        print(f"   ./lesson new \"{args.source}\" --start {mmss(w['start'])} --end {mmss(w['end'] + 0.5)}")
