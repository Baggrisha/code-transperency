"""Export an offline, self-contained review copy of the frontend.

Run from the repository: python scripts/export_frontend.py --output /tmp/uzniki-frontend.html
The exported data is synthetic; no files from the server's data directory are read.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from html import escape
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kontrol.engine import run_check
from kontrol.models import CheckRequest


def preview_seed() -> dict:
    fixtures = json.loads((ROOT / "fixtures/cases.json").read_text(encoding="utf-8"))
    request = deepcopy(next(item for item in fixtures if item["id"] == "T02")["request"])
    request["as_of"] = "2026-11-18"
    documents = request["documents"]
    specification = deepcopy(documents[0])
    specification.update(id="specification", type="specification", number="Спецификация № 1")
    specification.pop("deadline", None)
    documents.insert(1, specification)
    names = {"contract":"Контракт_0142.pdf", "specification":"Спецификация.pdf",
             "delivery-1":"Накладная_8.pdf", "acceptance-1":"Акт_10.pdf"}
    sizes = {"contract":150000, "specification":82000, "delivery-1":120000, "acceptance-1":95000}
    for document in documents:
        document["contract_id"] = "ГК-2026/0142"
        document["filename"] = names[document["id"]]
        document["date"] = "2026-11-01" if document["type"] in ("contract", "specification") else "2026-11-18"
        if document["type"] == "contract":
            document["number"] = "ГК-2026/0142"
            document["deadline"] = "2026-11-30"
        document["source"] = {"document_id":document["id"], "filename":document["filename"],
                              "excerpt":f'Синтетический документ. {document["number"]}; контракт ГК-2026/0142.'}
        for item in document["items"]:
            item["name"] = "Скамейка парковая"
            source = {"document_id":document["id"], "filename":document["filename"], "row":1,
                      "excerpt":f'Скамейка парковая; артикул BENCH-01; {item["quantity"]} шт.; цена 1200 руб. с НДС 20%.'}
            item["source"] = source
            item["field_sources"] = {field:{**source,"field":field} for field in ("quantity","unit_price","unit")}
    normalized = CheckRequest.model_validate(request)
    result = run_check(normalized).model_dump(mode="json")
    if result["status"] != "needs_review" or len(result["signals"]) != 1:
        raise RuntimeError("The visual demo must preserve the single T02 quantity signal")
    normalized_json = normalized.model_dump(mode="json")
    timestamp = "2026-11-18T10:00:00Z"
    dossier = {"id":"demo-001", "title":"Поставка уличных скамеек", "description":"Учебный кейс T02. Синтетические данные.",
               "contract_number":"ГК-2026/0142", "created_at":timestamp, "updated_at":timestamp,
               "revision":4, "metadata_revision":0, "is_demo":True,
               "documents":[{"id":doc["id"], "revision":1, "document":doc,
                             "size_bytes":sizes[doc["id"]]} for doc in normalized_json["documents"]],
               "runs":[{"id":"demo-run-001", "created_at":timestamp, "status":result["status"]}]}
    run = {"id":"demo-run-001", "case_id":"demo-001", "created_at":timestamp, "stale":False,
           "request":normalized_json, "result":result, "reviews":[], "latest_reviews":{}, "prior_reviews":{}}
    events = [{"action":"case_created", "created_at":timestamp, "payload":{"title":dossier["title"]}},
              {"action":"run_created", "created_at":timestamp, "payload":{"signals":1,"comment":"Демонстрационная сверка синтетического комплекта T02."}}]
    return {"dossier":dossier, "run":run, "events":events}


def export(output: Path) -> Path:
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    fonts = ("Unbounded-Variable.woff2", "Involve-Variable.woff2", "Montserrat-Variable.woff2")
    for stylesheet in ("styles.css", "icons.css", "brand.css"):
        css = (ROOT / "web/assets" / stylesheet).read_text(encoding="utf-8")
        if stylesheet == "icons.css":
            source = base64.b64encode((ROOT / "web/assets/icons/reference-sheet.png").read_bytes()).decode("ascii")
            css = css.replace("icons/reference-sheet.png", "data:image/png;base64," + source)
        for name in fonts:
            content = base64.b64encode((ROOT / "web/assets/fonts" / name).read_bytes()).decode("ascii")
            css = css.replace(f'fonts/{name}', "data:font/woff2;base64," + content)
        marker = f'<link rel="stylesheet" href="assets/{stylesheet}">'
        if html.count(marker) != 1:
            raise RuntimeError(f"Expected one stylesheet marker for {stylesheet}")
        html = html.replace(marker, '<style>\n' + css + '\n</style>')
    for name in fonts:
        html = html.replace(f'  <link rel="preload" href="assets/fonts/{name}" as="font" type="font/woff2" crossorigin>\n', "")
    data = json.dumps(preview_seed(),ensure_ascii=False).replace("<", "\\u003c")
    adapter = (ROOT / "scripts/frontend_preview.js").read_text(encoding="utf-8")
    injection = '<script>\nglobalThis.KONTROL_PREVIEW_SEED = ' + data + ';\n' + adapter + '\n</script>\n'
    html = html.replace('  <script>\n', injection + '  <script>\n',1)
    html = html.replace('<title>uzniki’s project — Контроль исполнения</title>', '<title>uzniki’s project — автономный макет</title>')
    licenses = "\n\n".join((ROOT / "web/assets/fonts" / name).read_text(encoding="utf-8")
                             for name in ("OFL-Unbounded.txt", "OFL-Involve.txt", "OFL-Montserrat.txt"))
    html = html.replace("</body>", '<template id="font-licenses"><pre>' + escape(licenses) + '</pre></template>\n</body>')
    html = html.replace('<button class="ghost" data-action="token">Доступ</button>', '', 1)
    startup = "if (initialHash) await routeFromHash(); else home();"
    if html.count(startup) != 1:
        raise RuntimeError("Expected exactly one frontend startup route for the offline export")
    html = html.replace(startup, "if (initialHash) await routeFromHash(); else await openCase('demo-001',null,'matching');", 1)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(html,encoding="utf-8")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(export(args.output.resolve()))
