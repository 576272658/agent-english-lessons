# lesson.json — format and authoring guide

This is the one step a script cannot do: turning a raw transcript into a lesson.
You (the AI agent) read `transcript.txt` / `transcript.json` and write
`lessons/EPxxx/lesson.json`. Then run `./lesson finish EPxxx`, which validates
the file, generates TTS and publishes the lesson to the web app. If validation fails it prints
every problem; fix them and run `finish` again.

The learner is a **Chinese native speaker** learning **spoken American English**
from real native-speaker videos. Everything explanatory is written in
Simplified Chinese; everything being learned is English.

## Inputs you have

| File | What it is |
|---|---|
| `source.json` | URL, video title, channel, clip start/end, `duration` (seconds, of the clip) |
| `transcript.txt` | Whisper segments with timestamps, easy to read |
| `transcript.json` | Same segments plus **per-word timestamps** (`words[].start/end`) — use these for sentence timing |

All timestamps are seconds **relative to the clip** (0 = start of `clip.mp4`).

## Output: `lesson.json`

```jsonc
{
  "schema_version": 1,
  "title": "去上健身课、戴耳机怎么说？",       // zh, "<2–3 key ideas>怎么说？" style, ≤ 20 chars
  "topic": "运动健身",                        // zh, 2–4 chars, a reusable category
  "notes": "",                               // optional zh note shown under the title
  "sentences": [
    {
      "id": 1,                                // 1, 2, 3 … in order
      "en": "So this morning I am getting ready to go to a workout class.",
      "zh": "今天早上我正准备去上一节健身课。",
      "ipa": "/soʊ ðɪs ˈmɔrnɪŋ aɪ æm ˈɡɛtɪŋ ˈrɛdi tə ɡoʊ tə ə ˈwɝkaʊt klæs/",
      "start": 16.30,                         // seconds, from word timestamps
      "end": 20.36,
      "highlights": [
        { "en": "getting ready to", "zh": "正准备", "expr": "get-ready-to" },
        { "en": "workout class",    "zh": "健身课",  "expr": "workout-class" }
      ]
    }
  ],
  "expressions": [
    {
      "id": "get-ready-to",                   // lowercase-kebab-case, stable across lessons
      "phrase": "get ready to…",              // dictionary/base form
      "answer": "get ready to",               // optional: what the learner types in the quiz (see below)
      "phonetic": "/ɡɛt ˈrɛdi tuː/",
      "pos": "phrase",                        // optional: phrase | phrasal verb | idiom | adj | noun | verb | adv | slang
      "meaning_zh": "准备做某事",
      "note": "口语里常用进行时 I'm getting ready to… 表示「正在收拾、准备出门去…」。", // optional zh usage note
      "examples": [                           // 1–2, NEW sentences (not from the video)
        { "en": "I'm getting ready to leave for work.", "zh": "我正准备出门上班。" }
      ],
      "synonyms": [ { "phrase": "be about to…", "zh": "即将做…" } ],          // optional
      "related":  [ { "phrase": "get ready for…", "zh": "为…做准备" } ],       // optional
      "tags": ["daily routine"]               // optional, English
    }
  ]
}
```

## Rules (the validator enforces the ones marked ✔)

### Sentences
- ✔ `id` is 1, 2, 3 … in order; `en`, `zh`, `ipa` non-empty; `start` < `end`;
  sentences don't overlap; `end` ≤ clip duration.
- **Fix the transcript.** Whisper output may be lowercase, unpunctuated or
  wrong. Restore capitalization and punctuation, fix misheard words, but keep
  what the speaker actually said — keep fillers like *like*, *you know*,
  *gonna* when they are part of natural speech; drop pure stutters.
- **One sentence = one natural spoken unit**, roughly 4–25 words, that a
  learner can repeat in one breath or two. Split long run-ons at natural
  pauses; merge tiny fragments.
- **Timing:** take `start` from the first word's `start` and `end` from the
  last word's `end` in `transcript.json`. You may pad by ~0.1 s.
- **Skip** intro music/teaser lines unrelated to the main talk, and an
  incomplete sentence cut off at the clip end. Keep 4–12 sentences total.
- `zh`: natural spoken Chinese, not word-for-word.
- `ipa`: General American IPA for the whole sentence between slashes,
  reflecting connected speech lightly (e.g. *gonna* → /ˈɡʌnə/, weak *to* → /tə/).

### Highlights
- ✔ `highlights[].en` must be an **exact substring** of that sentence's `en`
  (same case and punctuation) and `highlights[].zh` an exact substring of
  its `zh`. Choose the Chinese words that carry that meaning in your own
  translation — write `zh` so such a substring exists.
- ✔ `expr` must match an `expressions[].id`, and every expression must be
  highlighted at least once.
- `en` is the surface form as spoken (*got out of*); the expression's
  `phrase` is the base form (*get out of*).
- Highlights in one sentence must not overlap.

### Expressions — what to pick
- 5–12 per lesson. Pick what a Chinese intermediate learner would **not**
  naturally say but a native speaker does: collocations, phrasal verbs,
  idioms, natural fillers/discourse markers, vivid adjectives, everyday
  nouns for daily-life objects. *pretty sunny out*, *dreary*,
  *a million times*, *get back on track*.
- Don't pick words the learner surely knows (*good morning*, *very*), or
  proper nouns.
- ✔ `id` is lowercase-kebab-case and unique in the lesson. **Reuse the same
  id across lessons** for the same expression — run `./lesson expressions`
  to list existing ids before inventing a new one, so the expression library
  and the learner's quiz history link up.
- `examples`: 1–2 new, everyday sentences (not copied from the video) with
  natural Chinese.
- **Quiz answer.** Step 6 plays the expression's audio and the learner types
  it, one box per word. ✔ The answer may contain only letters, digits,
  spaces, apostrophes and hyphens. It defaults to `phrase` with `…` removed
  (`get ready to…` → `get ready to`). Set `answer` explicitly when `phrase`
  has placeholders or symbols: `phrase: "wear sth over one's ears"` →
  `answer: "wear it over my ears"`; `phrase: "SPF (sun protection factor)"` →
  `answer: "SPF"`. `meaning_zh` is the prompt, so keep it specific enough
  that only this expression fits.

### Title and topic
- `title`: Chinese, the pattern used by the course: name 2–3 of the key
  ideas, end with 怎么说？ — e.g. `阳光明媚、天气阴沉怎么说？`
- `topic`: reuse an existing topic when it fits (`./lesson status` lists
  existing lessons; their `lesson.json` has the topic). Examples: 天气季节, 日常作息, 美妆护肤,
  饮食, 运动健身, 情绪感受, 工作学习, 购物穿搭, 旅行, 人际关系, 科技数码.

## How the page uses each field

| Page step | Uses |
|---|---|
| 1 对照中文开口说 | `sentences[].zh` with `highlights[].zh` marked and `highlights[].en` shown as hints |
| 2 对照英文找卡壳 | `sentences[].en` with `highlights[].en` marked, `highlights[].zh` as gloss; video with captions timed by `start`/`end` |
| 3 精读表达 | `expressions[]` cards |
| 4 单句跟读 | each sentence: original audio `start`→`end`, TTS of `en`, `ipa`, recording |
| 5 口头填空 | `sentences[].en` with each `highlights[].en` blanked |
| 6 小测验 | per expression: `meaning_zh` + `pos` as prompt, TTS of the answer, type the answer; flashcard mode shows `phrase`, `phonetic`, first example |

## Editing later

Edit `lesson.json` and run `./lesson finish EPxxx` again. TTS is regenerated
only for text that changed. The web app picks up the change on the next page
load (an invalid `lesson.json` hides the lesson until it is fixed).
