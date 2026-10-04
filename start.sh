#!/bin/bash
# Turtle Nodes - Linux / macOS launcher
set -e
cd "$(dirname "$0")"

# Prefer the cross-platform Python launcher, fall back to the inline path.
if command -v python3 >/dev/null 2>&1 && [ -f start.py ]; then
    exec python3 start.py
fi

mkdir -p data backups cache

if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        echo "[!] Created .env from .env.example - Edit it with your token!"
        exit 1
    fi
fi

echo "[*] Starting Turtle Nodes Bot..."
exec python3 -u bot.py