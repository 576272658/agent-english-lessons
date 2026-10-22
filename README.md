# 地道口语六步精学 · Agent English Lessons

把喜欢的油管视频变成英语口语课。把视频链接发给你的 AI Agent，它会生成一节课并发布到你自己的学习网站上；你在手机或电脑上打开网页就能学，学习进度长期保存。

[English summary](#english)

## 每节课是什么样的

每节课用一段 20–90 秒的母语者视频生成。如果片段对你有些难，开头会自动多出一个**课前预习**，列出可能不认识的词。然后分六步学：

1. **对照中文，开口说英文**：看中文说英文，要学的地道表达有高亮提示
2. **对照英文，排查卡壳**：对照原文看视频，字幕可切换英文 / 中英 / 关闭
3. **精读表达**：每个表达都有音标、释义、用法、例句、同义和相关表达
4. **单句跟读**：逐句播放原声、标准美音朗读、单句循环、录音对比、变速
5. **口头填空**：关键表达挖空，口头说出后点击核对
6. **小测验**：听发音拼写表达（Typing）或用闪卡自测（Flashcard），做完自动打卡

在第 2、3、4 步，**点击任意英文单词**都能查看音标、中文释义和考试标签，并听发音（例如点 dried 会显示原形 dry）。查到的词可以一键**加入生词本**。

网站还有三个页面：
- **课程**：按主题筛选、搜索，可筛选未打卡或「适合我」，新课排在最前。每节课都标有难度：**对你**轻松 / 合适 / 有挑战 / 太难（按你的水平和生词本计算），以及需要的词汇等级和语速
- **表达库**：汇总所有课的表达，可收藏、搜索、测验，并显示每个表达在哪些课出现过；另有**生词本**标签页，保存你点过的单词和原句，可以测验
- **学习统计**：设置你的水平（初中 / 高中 / 四级 / 六级 / 雅思），以及学习天数、连续学习天数、掌握的表达和生词数、正确率、最近 16 周的学习日历、最常出错的表达和生词（可以一键测验）

## 工作原理

```
你（聊天软件）："把这个视频做成一课 <链接> 1:20-2:00"
        │
        ▼
   你的 AI Agent（运行在服务器上）
     ./lesson new <链接>     下载 → 截取 → Whisper 转写（带逐词时间）
     写 lesson.json           分句、翻译、音标、挑选地道表达
     ./lesson finish EPxxx   校验 → 生成朗读 → 发布
        │
        ▼
   学习网站（常驻服务）── 刷新页面，新课就出现了
        ▲
   手机 / 电脑浏览器（例如通过 Tailscale 访问家里的服务器）
```

- 脚本只负责机械步骤，语言部分由你的 AI Agent 按 [`docs/lesson-format.md`](docs/lesson-format.md) 完成，所以**不需要 API Key**。Claude Code、Codex 或其他能运行命令、读写文件的 Agent 都可以。
- **课程内容**保存为文件（`lessons/EPxxx/`），**学习记录**（打卡、收藏、每次测验结果）保存在 SQLite（`data/progress.db`）。两者都只留在你自己的服务器上。
- Agent 可以用 `./lesson progress` 查到你的错题，再根据错题帮你复习或挑选新视频。

## 部署

让服务器上的 Agent 读取本仓库并按 [`DEPLOY.md`](DEPLOY.md) 部署即可。比如对它说：

> 读一下 https://github.com/576272658/agent-english-lessons ，按 DEPLOY.md 把它部署到这台机器上，通过 Tailscale 发布 HTTPS 访问地址。

部署步骤概要（macOS，推荐 Apple 芯片）：

```sh
git clone https://github.com/576272658/agent-english-lessons.git
cd agent-english-lessons
./setup.sh                                  # 安装 uv、ffmpeg 和 Python 依赖
./lesson service install --port 8765        # 开机自启，崩溃自动重启
tailscale serve --bg --https=8443 http://127.0.0.1:8765
```

必须通过 **HTTPS** 访问，因为浏览器只在 HTTPS 下允许使用麦克风（跟读录音需要）。`tailscale serve` 会自动配置证书。Linux 部署见 DEPLOY.md。

### 离线学习

网站会自动把**还没打卡的最新 5 课**（含视频，每课约 6 MB）下载到手机上。没网的时候（地铁、飞机上，或者连不上家里的服务器），照样打开桌面上的 App 学习：
- 视频、跟读、录音、测验都能用
- 课内单词也能点击查词
- 打卡、测验结果、加生词这些记录会先存在手机上，联网后自动同步，日期按实际学习的那天算

在课程首页可以关掉自动下载；每节课的页面上也可以手动「下载到本机」或删除。打卡后的课会在下次联网时自动从手机上删除，腾出空间给新课。

### 添加到手机桌面（PWA）

部署好后可以像 App 一样添加到手机桌面，打开时全屏显示，没有浏览器地址栏：

- **iPhone / iPad**：用 Safari 打开网址 → 点「分享」→「添加到主屏幕」
- **Android**：用 Chrome 打开网址 → 点菜单 →「添加到主屏幕」或「安装应用」

学习进度保存在服务器上，所以桌面图标、浏览器和电脑上看到的进度都是一样的。

语音识别在 Apple 芯片上用 [mlx-whisper](https://github.com/ml-explore/mlx-examples)，其他平台用 [faster-whisper](https://github.com/SYSTRAN/faster-whisper)。模型约 1.5 GB，首次使用时自动下载。

## 日常使用

在聊天软件里对 Agent 说：

> 把这个视频做成一课：https://www.youtube.com/watch?v=xxxx 从 1:20 到 2:00

不知道选哪一段的话，只发链接就行。Agent 会用 `./lesson scan` 扫描整个视频，挑出最适合你水平的几段让你选。

它会按 [`AGENTS.md`](AGENTS.md) 完成全部步骤，然后告诉你课程已经上线。想修改时直接说，比如"第 3 课的翻译不太对""再加两个表达""我最近哪些表达老是错"。

### 命令一览

| 命令 | 作用 |
|---|---|
| `./lesson scan <链接>` | 找出视频里最适合你水平的片段 |
| `./lesson new <链接或文件> [--start 1:20] [--end 2:00]` | 新建一课：下载、截取、转写，并报告难度 |
| `./lesson level [zk/gk/cet4/cet6/ielts]` | 查看或设置你的水平 |
| `./lesson finish EP001` | 校验 → 生成朗读 → 发布到网站 |
| `./lesson status` | 各课进度 |
| `./lesson expressions` | 已有表达列表 |
| `./lesson progress` | 学习统计和错题 |
| `./lesson serve --port 8765` | 前台运行网站（开发用） |
| `./lesson service install/restart/status/uninstall` | 后台常驻服务（macOS） |

### 配置

| 环境变量 | 默认值 | 作用 |
|---|---|---|
| `LESSON_TTS_VOICE` | `en-US-AvaNeural` | 标准朗读的声音（`uv run edge-tts --list-voices` 查看全部） |
| `LESSON_WHISPER_MODEL` | `large-v3-turbo` | 语音识别模型；机器较慢可以换成 `small.en` |

### 更新与备份

- 更新：`git pull && uv sync && ./lesson dict install && ./lesson service restart`。课程和学习记录不受影响。
- 备份：复制 `lessons/` 文件夹和 `data/progress.db`。

查词使用开源英汉词典 [ECDICT](https://github.com/skywind3000/ECDICT)（MIT 许可），安装时自动下载，查词在本地完成。

## 版权说明

本项目只是一个学习工具，**不包含任何视频内容**。你下载的视频、生成的课程和学习记录都保存在你自己的服务器上，并已通过 `.gitignore` 排除在仓库之外。请仅将下载的内容用于个人学习，并遵守视频平台的服务条款和原作者的版权。请不要把网站暴露到公网（例如 `tailscale funnel`），网站没有登录功能。

## 许可证

[MIT](LICENSE)

---

## English

**Agent English Lessons** turns short native-speaker videos into six-step
spoken-English lessons for Chinese learners, served as a self-hosted web app:
speak from the Chinese, compare with the original video, study key expressions,
shadow sentence by sentence (original audio, TTS, loop, record yourself), an
oral cloze test, and a listen-and-type / flashcard quiz. Plus an expression
library and study stats (streak, activity calendar, most-missed expressions).

Scripts handle the mechanical work (yt-dlp, ffmpeg, Whisper with word
timestamps, edge-tts) and a small Starlette server; your own AI agent writes
each lesson's `lesson.json` following [`AGENTS.md`](AGENTS.md) and
[`docs/lesson-format.md`](docs/lesson-format.md), checked by a validator. No API
key needed. Lessons are files; progress is SQLite. New lessons appear on the
next page refresh. Deployment for agents: [`DEPLOY.md`](DEPLOY.md).
