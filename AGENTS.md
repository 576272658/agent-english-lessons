# AGENTS.md — instructions for AI agents

This repo turns a short native-speaker video into an offline, five-step
English lesson page for Chinese learners. **You are the AI part of the
pipeline**: scripts do download/transcribe/TTS/HTML; you write the lesson
content. Any agent that can run shell commands and read/write files can do
this (Claude Code, Codex, Cursor, a chat-app bot with file access, …).

Reply to the user in the language they use (usually Chinese).

## When the user sends a video

The user typically sends a YouTube link (any site yt-dlp supports works) or a
video/audio file, optionally with a time range ("从 1:20 到 2:00").

1. **Pick a clip.** Lessons work best at **20–90 seconds**. If the user gave a
   range, use it. If not and the video is long, ask which part, or pick a
   dense, talky 30–60 s section yourself and say which one you picked.
2. **Create the lesson** (downloads, clips, transcribes; ~1–3 min):
   ```sh
   ./lesson new "<url-or-file-path>" --start 1:20 --end 2:00
   ```
   It prints the new lesson id, e.g. `EP007`, and the files to read next.
   A file the user sent in chat: save it to disk first, pass its path.
3. **Write the lesson content.** Read `docs/lesson-format.md` (the spec; read
   it every time, don't work from memory), then
   `lessons/EP007/transcript.txt`, `transcript.json`, `source.json`, and
   `expressions.json` in the repo root if it exists (reuse ids). Write
   `lessons/EP007/lesson.json`.
4. **Build:**
   ```sh
   ./lesson finish EP007
   ```
   If it lists validation problems, fix `lesson.json` and run it again until
   it says `done`.
5. **Report back:** title, number of sentences/expressions, the list of
   expressions, and the page path `lessons/EP007/index.html`. Tell the user
   they can open `index.html` in the repo root to see all lessons.

## Commands

| Command | What it does |
|---|---|
| `./lesson new <url\|file> [--start T] [--end T]` | New lesson folder: media + `transcript.*`. T = seconds or mm:ss |
| `./lesson validate EPxxx` | Check `lesson.json` only |
| `./lesson finish EPxxx` | Validate → TTS → build lesson page, catalog, expression library |
| `./lesson build` | Rebuild every page (after template changes) |
| `./lesson status` | List lessons and their stage |
| `./lesson transcribe EPxxx` | Re-run speech recognition |
| `./lesson serve [--lan]` | Optional HTTP server, e.g. to open lessons on a phone |

`EPxxx` also accepts `7`, `EP7`, `ep007`.

## Stages (`./lesson status`)

| Stage | Meaning | Next action |
|---|---|---|
| `1-media` | Media downloaded, no transcript | `./lesson transcribe EPxxx` |
| `2-needs-lesson-json` | Transcript ready | Write `lesson.json` (step 3 above) |
| `3-needs-finish` | `lesson.json` new or edited | `./lesson finish EPxxx` |
| `4-done` | Page built | — |

## Other requests

- **"改一下第 3 课 / 这个翻译不对 / 多加几个表达"**: edit
  `lessons/EPxxx/lesson.json`, run `./lesson finish EPxxx`.
- **Delete a lesson**: confirm with the user first, then remove its folder and
  run `./lesson build`. Ids are not reused-renumbered.
- **Change how pages look**: edit `tools/templates/*` (not generated
  `index.html` files), then `./lesson build`.

## Layout

```
lesson                      entry point (wraps uv run tools/lesson.py)
tools/lesson.py             pipeline CLI
tools/templates/            lesson.html, index.html, expressions.html, style.css
docs/lesson-format.md       lesson.json spec + authoring rules  ← read before writing
docs/example-lesson.json    a complete valid example
lessons/EPxxx/              per lesson (git-ignored, user content):
  source.json               url, title, channel, clip range, duration   (script)
  clip.mp4 | audio.mp3      media                                         (script)
  transcript.txt/.json      Whisper output with word timestamps          (script)
  lesson.json               lesson content                               (YOU)
  tts/*.mp3                 per-sentence TTS                              (script)
  index.html                lesson page                                   (script)
index.html, expressions.html, expressions.json, assets/   generated site (git-ignored)
```

## Rules

- Never hand-edit generated HTML; it is overwritten on every build.
- Only `lesson.json` is yours to write. Don't edit `transcript.*` or
  `source.json`; re-run the commands instead.
- Downloaded videos are for the user's personal study. Don't publish
  `lessons/` anywhere or commit it (it is git-ignored).
- If `./lesson` fails because tools are missing, run `./setup.sh`.
