#!/bin/sh
# Mac/Linux: sh dublar.sh [LINK]
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -q --upgrade -r requirements.txt
.venv/bin/python dublar.py "${1:-https://www.youtube.com/watch?v=z9gTnoftoRU}" --saida saida
