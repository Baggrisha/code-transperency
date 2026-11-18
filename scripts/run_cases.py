"""Run the fixed T01–T12 protocol and write an honest machine-readable report."""
from __future__ import annotations
import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kontrol.engine import run_check
from kontrol.models import CheckRequest, ENGINE_VERSION, SCHEMA_VERSION
from tests.oracle import compare_result, load_cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'reports' / 'acceptance.json')
    args = parser.parse_args()
    results = []
    for case in load_cases():
        try:
            result = run_check(CheckRequest.model_validate(case['request']))
            errors = compare_result(case, result)
            results.append({'id': case['id'], 'title': case['title'], 'passed': not errors,
                            'errors': errors, 'expected': case['expected'], 'observed': result.model_dump(mode='json')})
        except Exception as exc:
            results.append({'id': case['id'], 'title': case['title'], 'passed': False,
                            'errors': [f'{type(exc).__name__}: {exc}'], 'expected': case['expected']})
    passed = sum(result['passed'] for result in results)
    report = {'protocol': 'T01–T12', 'schema_version': SCHEMA_VERSION, 'engine_version': ENGINE_VERSION,
              'oracle_version': '1.0', 'python_version': platform.python_version(),
              'dataset_sha256': hashlib.sha256((ROOT / 'fixtures' / 'cases.json').read_bytes()).hexdigest(),
              'dataset': 'synthetic', 'as_of': '2026-10-31', 'passed': passed, 'total': len(results),
              'interpretation': 'Выполнение фиксированных сценариев на синтетических данных; не измерение точности OCR, качества на реальных закупках или эффекта для пользователей.',
              'cases': results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for result in results:
        print(f"{result['id']} {'PASS' if result['passed'] else 'FAIL'} {result['title']}")
        for error in result['errors']:
            print(f'  {error}')
    print(f'{passed}/{len(results)} protocol cases passed. Report: {args.output}')
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
