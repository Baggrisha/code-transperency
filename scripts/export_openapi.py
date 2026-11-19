"""Перегенерирует docs/openapi.json из текущего приложения: python scripts/export_openapi.py"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kontrol.api import create_app  # noqa: E402

with tempfile.TemporaryDirectory() as directory:
    schema = create_app(Path(directory)).openapi()
target = ROOT / "docs" / "openapi.json"
target.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Записано: {target}")
