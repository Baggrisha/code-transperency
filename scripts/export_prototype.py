"""Export the user's restyled prototype as one offline HTML file.

This embeds the local stylesheets, latest brand theme and their licensed fonts. The script and
initial synthetic data come from web/prototype.html; no browser state or server
data is read by this exporter.
"""
from __future__ import annotations

import argparse
import base64
from html import escape
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def export(output: Path) -> Path:
    html = (ROOT / "web/prototype.html").read_text(encoding="utf-8")
    for stylesheet in ("styles.css", "prototype.css", "icons.css", "brand.css"):
        css = (ROOT / "web/assets" / stylesheet).read_text(encoding="utf-8")
        if stylesheet == "icons.css":
            source = base64.b64encode((ROOT / "web/assets/icons/reference-sheet.png").read_bytes()).decode("ascii")
            css = css.replace("icons/reference-sheet.png", "data:image/png;base64," + source)
        for font in ("Unbounded-Variable.woff2", "Involve-Variable.woff2", "Montserrat-Variable.woff2"):
            encoded = base64.b64encode((ROOT / "web/assets/fonts" / font).read_bytes()).decode("ascii")
            css = css.replace(f'fonts/{font}', "data:font/woff2;base64," + encoded)
        marker = f'<link rel="stylesheet" href="assets/{stylesheet}">'
        if html.count(marker) != 1:
            raise RuntimeError(f"Expected one stylesheet marker for {stylesheet}")
        html = html.replace(marker, "<style>\n" + css + "\n</style>")
    html = re.sub(r'  <link rel="preload"[^>]+>\n', '', html)
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
