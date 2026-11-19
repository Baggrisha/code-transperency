#!/usr/bin/env sh
# Первичная установка на macOS (Apple Silicon/Intel) и Linux: ./scripts/setup.sh
set -eu
cd "$(dirname "$0")/.."

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.14 python3; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PYTHON="$candidate"; break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "Нужен Python 3.11 или новее. На macOS: brew install python@3.13" >&2
  exit 1
fi
echo "Использую $($PYTHON --version) ($(command -v "$PYTHON"))"

[ -d .venv ] || "$PYTHON" -m venv .venv
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -r requirements-lock.txt

if ! command -v tesseract >/dev/null 2>&1; then
  echo "Предупреждение: tesseract не найден — сканы (PNG/JPG/TIFF, PDF без текста) обрабатываться не будут."
  echo "  macOS: brew install tesseract tesseract-lang poppler"
fi
echo "Готово. Запуск: ./scripts/start.sh  →  http://127.0.0.1:8000"
