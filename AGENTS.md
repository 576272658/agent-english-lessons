# AGENTS.md — instructions for AI agents

This repo turns short native-speaker videos into six-step English lessons for
Chinese learners, served as a web app the user opens in a browser. **You are
the AI part of the pipeline**: scripts download, transcribe, generate speech
and serve the site; you write the lesson content. Any agent that can run shell
commands and read/write files can do this.

Reply to the user in the language they use (usually Chinese).

- Setting up the server for the first time → follow [`DEPLOY.md`](DEPLOY.md).
- Making lessons → this file.

## When the user sends a video

The user typically sends a YouTube link (any site yt-dlp supports works) or a
video/audio file, optionally with a time range ("从 1:20 到 2:00").

1. **Pick a clip.** Lessons work best at **20–90 seconds**, and the clip must
   suit the learner's level — too many unknown words and the method stops
   working. If the user gave a range, use it. If not, run
   ```sh
   ./lesson scan "<url>"            # ~5 s with YouTube subtitles; slower without
   ```
   It lists the best ~45 s clips for this learner (fit, unknown words, speed,
   a text preview, and the exact `./lesson new` command). Show the user the
   top 2–3 in a few short lines and let them choose — or, if they said
   "你挑 / 随便", take #1 and tell them which part you picked.
2. **Create the lesson** (downloads, clips, transcribes; ~1–3 min):
   ```sh
   ./lesson new "<url-or-file-path>" --start 1:20 --end 2:00
   ```
   It prints the new lesson id, e.g. `EP007`, and a **DIFFICULTY** line for
   this learner. A file the user sent in chat: save it to disk first and pass
   its path.
   - `有挑战 (hard)`: mention it; the page adds a pre-study word list.
   - `太难 (too_hard)`: **stop and tell the user before writing lesson.json**,
     with the likely-unknown words. Offer a shorter clip, an easier section
     (`./lesson scan`), or going ahead anyway. Delete the folder if they pick
     another clip.
3. **Write the lesson content.** Read `docs/lesson-format.md` (the spec; read
   it every time, don't work from memory). Then read
   `lessons/EP007/transcript.txt`, `transcript.json`, `source.json`, and run
   `./lesson expressions` to see existing expression ids (reuse them). Write
   `lessons/EP007/lesson.json`.
4. **Publish:**
   ```sh
   ./lesson finish EP007
   ```
   If it lists validation problems, fix `lesson.json` and run it again until
   it says `done`. The lesson is now live on the site — no restart needed.
5. **Report back:** title, difficulty for the user (`./lesson check EP007`),
   the list of expressions, and that it is on the site (give the site URL if
   you know it).

## The learner's level

`./lesson level` shows it (default 初中 = `zk`); the user can change it on the
site (学习统计 → 你的水平) or ask you: `./lesson level cet4`. Levels: `zk` 初中,
`gk` 高中, `cet4` 四级, `cet6` 六级, `ielts` 雅思/托福. Difficulty labels, the
pre-study list and `scan` all use it, plus the word-book words the user has
answered correctly. Labels: 轻松 (≥98% known), 合适 (95–98%, ideal),
有挑战 (90–95%), 太难 (<90%).

## Commands

| Command | What it does |
|---|---|
| `./lesson scan <url> [--length 45] [--top 3]` | Find the clips of a video that best fit the learner |
| `./lesson new <url\|file> [--start T] [--end T]` | New lesson folder: media + transcript + difficulty report. T = seconds or mm:ss |
| `./lesson check EPxxx` | Difficulty of a lesson for this learner |
| `./lesson level [zk\|gk\|cet4\|cet6\|ielts]` | Show / set the learner's level |
| `./lesson validate EPxxx` | Check `lesson.json` only |
| `./lesson finish EPxxx` | Validate → TTS → publish to the site |
| `./lesson status` | List lessons and their stage |
| `./lesson expressions [--json]` | Existing expression ids across lessons |
| `./lesson progress [--json]` | Learner's stats, most-missed expressions and word-book words |
| `./lesson dict install` / `./lesson dict lookup <word>` | Click-to-look-up dictionary (ECDICT): install once / test a lookup |
| `./lesson transcribe EPxxx` | Re-run speech recognition |
| `./lesson serve --port N` | Run the site in the foreground (dev) |
| `./lesson service install\|restart\|status\|uninstall` | Run the site permanently (macOS launchd), see DEPLOY.md |

`EPxxx` also accepts `7`, `EP7`, `ep007`.

## Stages (`./lesson status`)

| Stage | Meaning | Next action |
|---|---|---|
| `1-media` | Media downloaded, no transcript | `./lesson transcribe EPxxx` |
| `2-needs-lesson-json` | Transcript ready | Write `lesson.json` (step 3) |
| `3-needs-finish` | `lesson.json` new or edited since last finish | `./lesson finish EPxxx` |
| `4-done` | Live on the site | — |

## Other requests

- **"改一下第 3 课 / 这个翻译不对 / 多加几个表达"**: edit
  `lessons/EPxxx/lesson.json`, run `./lesson finish EPxxx`.
- **"我最近哪些表达老是错 / 帮我复习"**: run `./lesson progress`. Summarize the
  weak expressions and words; the user can quiz them on the site's 学习统计 page.
  The user's word book (words they clicked and saved) is in the same report. When
  making new lessons, prefer clips that reuse weak expressions, and highlight
  them again (same `id`) — that feeds the same quiz history.
- **Delete a lesson**: confirm with the user first, then remove its folder.
  Lesson ids are never reused or renumbered. Progress rows for it stay in the
  database harmlessly.
- **Change how the site looks or behaves**: edit `web/*` (static files, no
  build step), refresh the browser.

## Layout

```
lesson                    entry point (wraps `uv run tools/lesson.py`)
tools/lesson.py           pipeline CLI
tools/server.py           web app: serves web/, media, JSON API
tools/progress.py         progress database (SQLite)
tools/dictionary.py       click-to-look-up dictionary built from ECDICT into data/dict.db
tools/difficulty.py       known-word coverage, vocabulary level and speech rate for a learner
tools/scan.py             `./lesson scan`: rank clips of a long video by fit
web/                      the site: index, lesson, expressions, stats pages + app.js, quiz.js, style.css
                          manifest.webmanifest + icons/ (PWA; icons/*.png are rendered from icons/*.svg)
docs/lesson-format.md     lesson.json spec + authoring rules  ← read before writing
docs/example-lesson.json  a complete valid example
DEPLOY.md                 server setup for agents
lessons/EPxxx/            per lesson (git-ignored):
  source.json             url, title, channel, clip range, duration   (script)
  clip.mp4 | audio.mp3    media                                         (script)
  transcript.txt/.json    Whisper output with word timestamps          (script)
  lesson.json             lesson content                               (YOU)
  tts/*.mp3               sentence + expression speech                  (script)
  ready.json              published marker                              (script)
data/                     git-ignored: progress.db (check-ins, favorites, word book, quiz attempts, settings),
                          dict.db (dictionary), word-audio/ (cached word TTS), scans/ (scan cache), server.log
```

## Rules

- Only `lesson.json` is yours to write. Don't edit `transcript.*`,
  `source.json`, `ready.json` or `data/progress.db` by hand.
- Downloaded videos are for the user's personal study. Never commit or
  publish `lessons/` or `data/` (both are git-ignored).
- If `./lesson` fails because tools are missing, run `./setup.sh`.
