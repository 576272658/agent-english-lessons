# DEPLOY.md — deploy the web app on a home server (for AI agents)

Goal: the web app runs permanently on this machine and the user opens it from
anywhere on their **Tailscale** network over **HTTPS**. Lessons the agent
creates later show up on the next page refresh — no rebuilds, no restarts.

Written for an agent with shell access on the server. Target: macOS (Apple
Silicon recommended). Linux notes at the end. Do the steps in order and report
the final URL to the user.

## 0. Before you start

- Look at how this machine already runs services (where they live, how ports
  are chosen, how they are exposed via `tailscale serve`). **Follow the
  existing conventions** where they differ from the defaults below.
- Never run `tailscale serve reset`, and never remove existing serve entries —
  other services depend on them. Never use `tailscale funnel` (that exposes the
  app to the public internet; it has no login).

Requirements: git, Homebrew (macOS), Tailscale installed and logged in
(`tailscale status` works), ~3 GB free disk for the speech model and tools.

## 1. Install

```sh
git clone https://github.com/576272658/agent-english-lessons.git
cd agent-english-lessons
./setup.sh                    # installs uv, ffmpeg, Python deps, and the dictionary (~63 MB download)
```

Pick a stable location (e.g. next to the machine's other services). The
directory holds everything: code, `lessons/` (content) and `data/` (progress
database). Remember the absolute path — you need it for every lesson later.

## 2. Choose a port and start the service

Pick a free local port per this machine's conventions, then:

```sh
lsof -nP -iTCP:<PORT> -sTCP:LISTEN     # must print nothing
./lesson service install --port <PORT>  # launchd: starts at login, restarts on crash
./lesson service status                 # state: running
curl -s http://127.0.0.1:<PORT>/api/health   # {"ok":true,"lessons":0,"dictionary":true}
```

The server binds to `127.0.0.1` only; Tailscale exposes it in the next step.
Logs: `data/server.log`.

## 3. Expose it over Tailscale HTTPS

HTTPS is required, not optional: browsers only allow the microphone (the
shadowing/recording feature) and offline mode (service worker) on HTTPS pages.

Check what is already served: `tailscale serve status`. Then use **one** of:

**A. Its own HTTPS port** (simplest; pick a port not used by other serve entries)

```sh
tailscale serve --bg --https=<HTTPS_PORT> http://127.0.0.1:<PORT>
# URL: https://<machine>.<tailnet>.ts.net:<HTTPS_PORT>/
```

**B. A sub-path on the machine's existing HTTPS host**

```sh
tailscale serve --bg --set-path /english http://127.0.0.1:<PORT>
# URL: https://<machine>.<tailnet>.ts.net/english/   ← trailing slash matters
```

All app URLs are relative, so a sub-path works. If pages under the sub-path
load but API calls return 404 (the proxy did not strip `/english`), reinstall
with `./lesson service install --port <PORT> --base-path /english`.

Get the exact hostname from `tailscale status --json` (`Self.DNSName`) or the
output of `tailscale serve status`.

## 4. Verify

```sh
curl -s https://<url>/api/health        # from this machine or any tailnet device
```

Then, ideally, create a first lesson end-to-end (see `AGENTS.md`) and confirm
it appears at the URL. The first `./lesson new` downloads the speech model
(~1.5 GB), so it is slower than later runs.

## 5. Report to the user

Tell the user: the URL (they can add it to the phone home screen as an app: Safari → Share → Add to Home Screen; Chrome → Install app), that new lessons
appear after a refresh, and that progress (check-ins, favorites, quiz results)
is saved on the server and shared across devices.

## Operations

| Task | Command |
|---|---|
| Update to latest code | `git pull && uv sync && ./lesson dict install && ./lesson service restart` (`dict install` is a no-op once installed) |
| Status / logs | `./lesson service status`, `tail -n 50 data/server.log` |
| Restart | `./lesson service restart` |
| Change port | `./lesson service install --port <NEW>` then update `tailscale serve` |
| Stop and remove | `./lesson service uninstall` (and remove its `tailscale serve` entry) |
| Backup | copy `lessons/` and `data/progress.db` (the rest of `data/` can be regenerated) |

Updating never touches `lessons/` or `data/`; both are git-ignored.

**A lesson is missing from the site:** `./lesson status` — it must be at
`4-done` (run `./lesson finish EPxxx`). A lesson whose `lesson.json` became
invalid is hidden until fixed; `./lesson validate EPxxx` shows why, and the
log has a `[server] EPxxx hidden` line.

## Linux

Everything except `./lesson service` works the same (speech recognition uses
faster-whisper instead of MLX). Run the server with systemd:

```ini
# /etc/systemd/system/english-lessons.service
[Service]
WorkingDirectory=/path/to/agent-english-lessons
ExecStart=/path/to/agent-english-lessons/lesson serve --port <PORT>
Environment=PATH=/home/<user>/.local/bin:/usr/local/bin:/usr/bin:/bin
Restart=always
User=<user>

[Install]
WantedBy=multi-user.target
```

`sudo systemctl enable --now english-lessons`, then step 3 as above.
