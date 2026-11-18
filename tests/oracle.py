"""Independent assertions derived from the written acceptance specification."""
from __future__ import annotations
import json
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_cases():
    return json.loads((ROOT / 'fixtures' / 'cases.json').read_text(encoding='utf-8'))


def compare_result(case, result):
    """Return human-readable deviations. No production rules are called here."""
    actual = result.model_dump(mode='json') if hasattr(result, 'model_dump') else result
    expected = case['expected']
    errors = []
    codes = {signal['code'] for signal in actual['signals']}
    if actual['status'] != expected['status']:
        errors.append(f"status: expected {expected['status']}, got {actual['status']}")
    for code in expected.get('required_codes', []):
        if code not in codes:
            errors.append(f'missing signal {code}')
    for code in expected.get('forbidden_codes', []):
        if code in codes:
            errors.append(f'unexpected forbidden signal {code}')
    if 'allowed_codes' in expected:
        extras = codes - set(expected['allowed_codes'])
        if extras:
            errors.append(f'unexpected signals {sorted(extras)}')
    positions = actual['positions']
    for field in ('expected_quantity', 'delivered_quantity', 'accepted_quantity'):
        if field not in expected:
            continue
        if len(positions) != 1:
            errors.append(f'{field}: expected one position, got {len(positions)}')
            continue
        exp, got = expected[field], positions[0][field]
        if exp is None:
            if got is not None:
                errors.append(f'{field}: expected unknown, got {got}')
        elif got is None or Decimal(str(got)) != Decimal(str(exp)):
            errors.append(f'{field}: expected {exp}, got {got}')
    for field, result_field in (('ignored_ids', 'ignored_document_ids'), ('active_ids', 'active_document_ids')):
        missing = set(expected.get(field, [])) - set(actual[result_field])
        if missing:
            errors.append(f'{result_field}: missing {sorted(missing)}')
    if 'applied_amendments' in expected and set(actual['applied_amendment_ids']) != set(expected['applied_amendments']):
        errors.append(f"applied amendments: expected {expected['applied_amendments']}, got {actual['applied_amendment_ids']}")
    audit_codes = {entry['code'] for entry in actual['audit']}
    for code in expected.get('audit_codes', []):
        if code not in audit_codes:
            errors.append(f'missing audit event {code}')
    for code in expected.get('blocked_codes', []):
        matching = [s for s in actual['signals'] if s['code'] == code]
        if not matching or not all(s['blocks_comparison'] for s in matching):
            errors.append(f'{code} must block numeric comparison')
    required_match = expected.get('required_match')
    if required_match and not any(all(match.get(key) == value for key, value in required_match.items()) for match in actual['matches']):
        errors.append(f'missing expected match {required_match}')
    doc_ids = {d['id'] for d in case['request']['documents']}
    for signal in actual['signals']:
        for field in ('id', 'code', 'title', 'explanation', 'rule'):
            if not signal.get(field):
                errors.append(f"signal {signal.get('code')}: no {field}")
        if signal.get('status') != 'needs_review':
            errors.append(f"signal {signal['code']}: automatic final review status")
        if not signal['sources']:
            errors.append(f"signal {signal['code']}: no source")
        for source in signal['sources']:
            if source['document_id'] not in doc_ids:
                errors.append(f"signal {signal['code']}: invented document {source['document_id']}")
            if not source.get('excerpt'):
                errors.append(f"signal {signal['code']}: no excerpt")
        minimum = expected.get('minimum_signal_sources', 1)
        if signal['code'] in expected.get('required_codes', []) and len(signal['sources']) < minimum:
            errors.append(f"signal {signal['code']}: expected at least {minimum} source fragments")
    return errors
