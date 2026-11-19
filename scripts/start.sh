#!/usr/bin/env sh
# Запуск сервера: ./scripts/start.sh [порт]. Использует .venv, если он создан scripts/setup.sh.
set -eu
cd "$(dirname "$0")/.."
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
PORT="${1:-${PORT:-8000}}"
exec "$PY" -m uvicorn kontrol.api:app --host 127.0.0.1 --port "$PORT"
