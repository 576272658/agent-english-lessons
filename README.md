# 地道口语五步精学 · Agent English Lessons

把你喜欢的油管视频，变成一节**可离线学习**的英语口语课。
发给你的 AI Agent 一个视频链接，它就会帮你生成学习页面。

[English summary](#english)

## 每节课是什么样的

从一段 20–90 秒的母语者视频生成，分五步学：

1. **对照中文，开口说英文**：看着中文说英文，高亮处标出要学的地道表达
2. **对照英文，排查卡壳**：看原文和视频（字幕可切换为英文、中英或关闭）
3. **精读表达**：每个表达都有音标、释义、用法、例句、同义和相关表达
4. **单句跟读**：原声逐句播放、标准美音朗读、单句循环、录音对比、0.75x/1.25x 变速
5. **口头填空**：关键表达挖空，口头说出后点击核对

另外还有课程目录（支持打卡）和一个跨课程的**表达库**：自动汇总所有学过的表达，并能看到每个表达在哪几课出现过。

生成的都是普通 HTML 文件和媒体文件，不需要网络、不需要服务器，双击就能打开，也可以拷到别的电脑上用。

## 工作原理

```
视频链接 ──► ./lesson new ──► 下载、截取、Whisper 转写（带逐词时间轴）
                                   │
                    AI Agent 读取 transcript + docs/lesson-format.md
                                   │
                                   ▼
                          写出 lesson.json（分句、翻译、音标、挑选表达）
                                   │
             ./lesson finish ──► 校验 → TTS 朗读 → 生成学习页面
```

脚本只负责机械步骤，语言部分交给你自己的 AI Agent 完成。所以**不需要任何 API Key**，用你已经在用的 Agent 就行：Claude Code、Codex、Cursor，或者能读写文件的聊天机器人。规范写在 [`AGENTS.md`](AGENTS.md) 和 [`docs/lesson-format.md`](docs/lesson-format.md) 里，任何 Agent 读完都能接手。

## 安装

需要 macOS 或 Linux（Windows 请用 WSL）。

```sh
git clone https://github.com/<you>/agent-english-lessons.git
cd agent-english-lessons
./setup.sh
```

`setup.sh` 会安装 [uv](https://github.com/astral-sh/uv)、ffmpeg 和 Python 依赖。语音识别在 Apple 芯片的 Mac 上用 [mlx-whisper](https://github.com/ml-explore/mlx-examples)，在其他平台上用 [faster-whisper](https://github.com/SYSTRAN/faster-whisper)。模型约 1.5 GB，首次使用时自动下载。

也可以直接让 Agent 来装：

> 克隆 https://github.com/<you>/agent-english-lessons 并按 README 安装好

## 使用

在项目目录里打开你的 Agent，对它说：

> 把这个视频做成一课：https://www.youtube.com/watch?v=xxxx 从 1:20 到 2:00

Agent 会按 `AGENTS.md` 依次执行：`./lesson new` → 写 `lesson.json` → `./lesson finish`，完成后告诉你页面在哪里。

想改内容时，直接跟它说"第 3 课的翻译不太对""再加两个表达"就行。

打开 `index.html` 可以查看全部课程。想在手机上看，运行 `./lesson serve --lan`，然后用同一 Wi-Fi 下的手机打开它显示的地址。

### 手动命令

| 命令 | 作用 |
|---|---|
| `./lesson new <链接或文件> [--start 1:20] [--end 2:00]` | 新建一课：下载、截取、转写 |
| `./lesson finish EP001` | 校验 lesson.json → 生成朗读 → 生成页面 |
| `./lesson status` | 查看各课进度 |
| `./lesson build` | 重新生成所有页面（修改模板后用） |
| `./lesson serve [--lan]` | 本地网页服务（可选） |

### 配置

| 环境变量 | 默认值 | 作用 |
|---|---|---|
| `LESSON_TTS_VOICE` | `en-US-AvaNeural` | 标准朗读的声音，可用 `uv run edge-tts --list-voices` 查看全部 |
| `LESSON_WHISPER_MODEL` | `large-v3-turbo` | 语音识别模型，机器较慢可以换成 `small.en` |

## 版权说明

本项目只是一个学习工具，**不包含任何视频内容**。你下载的视频和生成的课程都保存在本地的 `lessons/` 目录，这个目录已在 `.gitignore` 中排除。请仅将下载的内容用于个人学习，并遵守视频平台的服务条款和原作者的版权。

## 许可证

[MIT](LICENSE)

---

## English

**Agent English Lessons** turns a short native-speaker video into an offline,
five-step spoken-English lesson page for Chinese learners: speak from the
Chinese, compare with the original, study key expressions, shadow sentence by
sentence (original audio, TTS, loop, record yourself), and an oral cloze test.

Scripts handle the mechanical work (yt-dlp download, ffmpeg clip, Whisper
transcription with word timestamps, edge-tts, static HTML). Your own AI agent
writes the lesson content (`lesson.json`) by following
[`AGENTS.md`](AGENTS.md) and [`docs/lesson-format.md`](docs/lesson-format.md),
and a validator checks it. No API key needed.

```sh
./setup.sh
./lesson new "https://www.youtube.com/watch?v=..." --start 1:20 --end 2:00
# agent writes lessons/EP001/lesson.json
./lesson finish EP001
open index.html
```
