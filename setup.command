#!/bin/bash
# macOS: double-click once to install everything into ./.venv
cd "$(dirname "$0")"
set -e
if command -v uv >/dev/null 2>&1; then
  uv venv --python 3.13 .venv
  uv pip install --python .venv/bin/python -r requirements.txt
else
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
if ! .venv/bin/python -c "import tkinter; assert tkinter.TkVersion >= 8.6" 2>/dev/null; then
  echo
  echo "This Python has no usable tkinter (needs Tk 8.6+)."
  echo "Fix: install uv (brew install uv) and run this again, or: brew install python-tk@3.13"
  read -n 1 -s -r -p "Press any key to close"; exit 1
fi
command -v ffmpeg >/dev/null 2>&1 || echo "Note: ffmpeg not found - needed for Export (brew install ffmpeg)."
echo; echo "Done. Double-click start.command to open Gothic Dub."
read -n 1 -s -r -p "Press any key to close"
