"""Package the exact supplied icon windows as self-contained SVG viewports.

No raster pixels are redrawn or altered. The provided PNG remains the source,
including its navy background and glows.
"""
from __future__ import annotations

import argparse
import base64
from html import escape
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "web/assets/icons"


def catalogue(manifest: dict, image_source: str) -> str:
    css = (ROOT / "web/assets/icons.css").read_text(encoding="utf-8")
    css = css.replace('icons/reference-sheet.png', image_source)
    cards = "".join(
        '<article><span class="identity-icon identity-icon--' + item["name"]
        + '" role="img" aria-label="' + escape(item["label"], quote=True)
        + '"></span><h2>' + escape(item["label"]) + '</h2><code>' + item["name"]
        + '</code><a href="' + item["name"] + '.svg" download data-x="'
        + str(item["x"]) + '" data-y="' + str(item["y"])
        + '" data-label="' + escape(item["label"], quote=True)
        + '">Скачать SVG</a></article>'
        for item in manifest["icons"]
    )
    return '''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Иконки из айдентики — uzniki’s project</title><style>'''+css+'''
*{box-sizing:border-box}body{margin:0;padding:40px;background:#f3f4f8;color:#26306e;font:16px/1.5 "Segoe UI",Arial,sans-serif}main{max-width:1120px;margin:auto}h1{margin:0 0 12px;font-size:32px;line-height:1.2}p{max-width:850px;margin:0 0 28px}.grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:16px}article{display:flex;flex-direction:column;align-items:flex-start;padding:18px;border:1px solid #d0d7df;border-radius:16px;background:white}.identity-icon{width:112px;height:112px;border-radius:16px}h2{font-size:16px;line-height:1.25;margin:16px 0 6px}code{font-size:12px;color:#5964cb}a{font-size:13px;color:#26306e;margin-top:16px;text-underline-offset:4px}.download-note{margin-top:28px;font-size:13px}@media(max-width:900px){.grid{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:560px){body{padding:24px 16px}.grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}article{padding:12px}.identity-icon{width:96px;height:96px}h1{font-size:26px}}
</style></head><body><main><h1>Иконки из твоей айдентики</h1><p>Все 20 иконок взяты непосредственно из присланного листа. Сохранены исходные цвета, синий фон и свечения. SVG-файлы содержат растровое изображение внутри; фон не прозрачный.</p><div class="grid">'''+cards+'''</div><p class="download-note">Каждую иконку можно скачать отдельно. В архиве uzniki-icons.zip также лежат исходный лист, координаты и каталог.</p></main><script>
document.addEventListener('click', function (event) {
  const link = event.target.closest('a[data-x]');
  if (!link) return;
  const background = getComputedStyle(document.querySelector('.identity-icon')).backgroundImage;
  const imageSource = background.slice(4, -1).replace(/^['"]|['"]$/g, '');
  if (!imageSource.startsWith('data:image/png;base64,')) return;
  event.preventDefault();
  const namespace = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(namespace, 'svg');
  svg.setAttribute('width', '112');
  svg.setAttribute('height', '112');
  svg.setAttribute('overflow', 'hidden');
  svg.setAttribute('viewBox', link.dataset.x + ' ' + link.dataset.y + ' 112 112');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', link.dataset.label);
  const title = document.createElementNS(namespace, 'title');
  title.textContent = link.dataset.label;
  const raster = document.createElementNS(namespace, 'image');
  raster.setAttribute('width', '1280');
  raster.setAttribute('height', '720');
  raster.setAttribute('href', imageSource);
  svg.append(title, raster);
  const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svg)], {type: 'image/svg+xml'}));
  const download = document.createElement('a');
  download.href = url;
  download.download = link.getAttribute('href');
  document.body.append(download);
  download.click();
  download.remove();
  setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
});
</script></body></html>'''


def export(output_dir: Path, zip_path: Path | None = None, catalog_path: Path | None = None) -> None:
    manifest = json.loads((ASSETS / "manifest.json").read_text(encoding="utf-8"))
    raster = (ASSETS / manifest["source"]).read_bytes()
    data_uri = "data:image/png;base64," + base64.b64encode(raster).decode("ascii")
    output_dir.mkdir(parents=True, exist_ok=True)
    for item in manifest["icons"]:
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="112" height="112" overflow="hidden" '
               f'viewBox="{item["x"]} {item["y"]} 112 112" role="img" '
               f'aria-label="{escape(item["label"], quote=True)}">'
               f'<title>{escape(item["label"])}</title>'
               f'<image width="1280" height="720" href="{data_uri}"/></svg>\n')
        (output_dir / (item["name"] + ".svg")).write_text(svg, encoding="utf-8")
    (output_dir / manifest["source"]).write_bytes(raster)
    (output_dir / "manifest.json").write_bytes((ASSETS / "manifest.json").read_bytes())
    (output_dir / "README.md").write_bytes((ASSETS / "README.md").read_bytes())
    (output_dir / "index.html").write_text(catalogue(manifest, manifest["source"]), encoding="utf-8")
    if catalog_path:
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(catalogue(manifest, data_uri), encoding="utf-8")
    if zip_path:
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
            for item in sorted(output_dir.iterdir()):
                if item.is_file():
                    archive.write(item, item.name)
    print(f"Exported {len(manifest['icons'])} original icon viewports to {output_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--zip", dest="zip_path", type=Path)
    parser.add_argument("--catalog", dest="catalog_path", type=Path)
    args = parser.parse_args()
    export(args.output_dir.resolve(), args.zip_path.resolve() if args.zip_path else None,
           args.catalog_path.resolve() if args.catalog_path else None)
