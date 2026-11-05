#!/bin/bash
# macOS: double-click to open Gothic Dub (launcher window)
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Not set up yet - double-click setup.command first."
  read -n 1 -s -r -p "Press any key to close"; exit 1
fi
exec .venv/bin/python -m gdub ui
