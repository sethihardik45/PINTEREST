#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt

if [ ! -f ".env" ] && [ -z "$GEMINI_API_KEY" ]; then
  echo "⚠️  GEMINI_API_KEY not set. Copy .env.example → .env and add your key."
  exit 1
fi

exec python app.py
