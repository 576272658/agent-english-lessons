#!/bin/sh
# One-time setup: installs uv (Python manager), ffmpeg, and Python dependencies.
set -e
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "==> installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "==> installing ffmpeg"
  if command -v brew >/dev/null 2>&1; then brew install ffmpeg
  elif command -v apt-get >/dev/null 2>&1; then sudo apt-get update && sudo apt-get install -y ffmpeg
  elif command -v dnf >/dev/null 2>&1; then sudo dnf install -y ffmpeg
  else echo "Please install ffmpeg manually: https://ffmpeg.org/download.html" >&2; exit 1
  fi
fi

echo "==> installing Python dependencies"
uv sync

echo "==> installing the click-to-look-up dictionary (ECDICT, ~63 MB download, once)"
uv run --quiet python tools/lesson.py dict install

chmod +x lesson
echo
echo "Ready. Try:  ./lesson new \"https://www.youtube.com/watch?v=...\" --start 0:30 --end 1:15"
echo "(The speech-recognition model, ~1.5 GB, downloads on first use.)"
