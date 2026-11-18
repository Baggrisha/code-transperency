"""Command line interface without a web server or external API key."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .engine import run_check
from .exports import report_html
from .extract import ExtractionError, extract_document
from .models import CheckRequest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kontrol", description="Контроль исполнения: локальная сверка")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="Сверить JSON-комплект")
    check.add_argument("request", type=Path)
    check.add_argument("-o", "--output", type=Path)
    check.add_argument("--html", type=Path)
    extract = sub.add_parser("extract", help="Извлечь реквизиты и таблицу")
    extract.add_argument("file", type=Path)
    extract.add_argument("--type", choices=["contract", "specification", "delivery", "acceptance", "amendment"])
    extract.add_argument("--contract-id")
    extract.add_argument("-o", "--output", type=Path)
    demo = sub.add_parser("demo", help="Проверить один синтетический сценарий T01–T12")
    demo.add_argument("case", nargs="?", default="T02")
    demo.add_argument("-o", "--output", type=Path)
    demo.add_argument("--html", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "extract":
            result = extract_document(args.file, document_type=args.type, contract_id=args.contract_id)
        else:
            if args.command == "demo":
                path = Path(__file__).resolve().parent.parent / "fixtures" / "cases.json"
                data = json.loads(path.read_text(encoding="utf-8"))
                cases = data if isinstance(data, list) else data["cases"]
                case = next((x for x in cases if x["id"] == args.case), None)
                if case is None:
                    raise ValueError(f"Неизвестный сценарий {args.case}")
                request = CheckRequest.model_validate(case["request"])
            else:
                request = CheckRequest.model_validate_json(args.request.read_text(encoding="utf-8"))
            result = run_check(request)
            if args.html:
                args.html.parent.mkdir(parents=True, exist_ok=True)
                args.html.write_text(report_html({"result": result.model_dump(mode="json")}), encoding="utf-8")
        output = result.model_dump_json(indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output + "\n", encoding="utf-8")
            print(str(args.output))
        else:
            print(output)
        return 0
    except (OSError, ValueError, ValidationError, ExtractionError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
