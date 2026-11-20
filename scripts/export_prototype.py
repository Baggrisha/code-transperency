"""Export the user's restyled prototype as one offline HTML file.

The React frontend and local assets are bundled into a standalone HTML file.
No browser state or server data is read by this exporter.
"""
from __future__ import annotations

import argparse
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from build_singlefile import build_singlefile


def export(output: Path) -> Path:
    html = build_singlefile("prototype")
    licenses = "\n\n".join((ROOT / "web/assets/fonts" / name).read_text(encoding="utf-8")
                             for name in ("OFL-Unbounded.txt", "OFL-Involve.txt", "OFL-Montserrat.txt"))
    html = html.replace("</body>", '<template id="font-licenses"><pre>' + escape(licenses) + '</pre></template>\n</body>')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(export(args.output.resolve()))
