"""Build one self-contained frontend page without touching the server build."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def build_singlefile(entry: str) -> str:
    if entry not in {"index", "prototype"}:
        raise ValueError("Unknown frontend entry")
    with TemporaryDirectory(prefix=".export-", dir=ROOT / "web") as directory:
        env = {**os.environ, "KONTROL_EXPORT_ENTRY": entry, "KONTROL_EXPORT_DIR": directory}
        subprocess.run(["npm", "run", "build"], cwd=ROOT, env=env, check=True)
        return (Path(directory) / f"{entry}.html").read_text(encoding="utf-8")
