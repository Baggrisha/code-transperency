"""Hold-out behavioral checks for failure modes beyond the 12 demo cases."""
from __future__ import annotations
import copy
import hashlib
import unittest
from decimal import Decimal

from kontrol.engine import run_check
from kontrol.models import CheckRequest
from tests.oracle import load_cases

CASES = {case['id']: case for case in load_cases()}


def request(case='T01'):
    return copy.deepcopy(CASES[case]['request'])


def evaluate(value):
    return run_check(CheckRequest.model_validate(value))


def codes(result):
    return {signal.code for signal in result.signals}


def renamed(document, new_id, number):
    cloned = copy.deepcopy(document)
    cloned['id'] = new_id
    cloned['number'] = number
    cloned['filename'] = new_id + '.json'
    cloned.pop('content_hash', None)
    for item in cloned['items']:
        for source in [item.get('source'), *item.get('field_sources', {}).values()]:
            if source:
                source['document_id'] = new_id
                source['filename'] = cloned['filename']
    if cloned.get('source'):
        cloned['source']['document_id'] = new_id
        cloned['source']['filename'] = cloned['filename']
        cloned['source']['excerpt'] = f'Документ {number}; синтетический регрессионный пример'
    return cloned


class IndependentRegressions(unittest.TestCase):
    def test_repeated_run_is_identical(self):
        source = request('T02')
        first = evaluate(source)
        second = evaluate(source)
        self.assertEqual(first.model_dump(mode='json'), second.model_dump(mode='json'))
        self.assertTrue(first.input_hash)
        self.assertTrue(first.result_hash)

    def test_document_order_does_not_change_result(self):
        source = request('T03')
        expected = evaluate(source)
        source['documents'].reverse()
        reversed_result = evaluate(source)
        self.assertEqual(expected.model_dump(mode='json'), reversed_result.model_dump(mode='json'))

    def test_identical_goods_in_different_invoices_are_not_duplicate(self):
        source = request()
        first = source['documents'][1]
        first['items'][0]['quantity'] = '5'
        second = renamed(first, 'delivery-2', 'Н-002')
        source['documents'].append(second)
        source['documents'][2]['related_delivery_ids'] = ['delivery-1', 'delivery-2']
        result = evaluate(source)
        self.assertEqual(Decimal('10'), result.positions[0].delivered_quantity)
        self.assertEqual('ok', result.status)
        self.assertNotIn('delivery-2', result.ignored_document_ids)

    def test_duplicate_acceptance_is_not_counted_twice(self):
        source = request()
        original = source['documents'][2]
        duplicate_hash = hashlib.sha256(b'synthetic repeated acceptance bytes').hexdigest()
        original['content_hash'] = duplicate_hash
        duplicate = renamed(original, 'acceptance-1-copy', original['number'])
        duplicate['content_hash'] = duplicate_hash
        source['documents'].append(duplicate)
        result = evaluate(source)
        self.assertEqual(Decimal('10'), result.positions[0].accepted_quantity)
        self.assertEqual('ok', result.status)
        self.assertIn('DUPLICATE_IGNORED', {entry.code for entry in result.audit})

    def test_same_id_with_changed_values_is_a_conflict(self):
        source = request()
        modified = copy.deepcopy(source['documents'][1])
        modified['items'][0]['quantity'] = '8'
        source['documents'].append(modified)
        result = evaluate(source)
        self.assertIn('DUPLICATE_ID_CONFLICT', codes(result))
        self.assertNotEqual('ok', result.status)

    def test_unknown_quantity_is_not_zero(self):
        source = request()
        source['documents'][1]['items'][0]['quantity'] = None
        result = evaluate(source)
        self.assertEqual('incomplete', result.status)
        self.assertIsNone(result.positions[0].delivered_quantity)
        self.assertNotIn('QUANTITY_MISMATCH', codes(result))
        self.assertTrue(any(signal.blocks_comparison for signal in result.signals))

    def test_explicit_package_conversion_adjusts_quantity_and_price(self):
        source = request('T07')
        item = source['documents'][1]['items'][0]
        item.update(conversion_factor='5', conversion_to='шт', unit_price='6000')
        result = evaluate(source)
        self.assertEqual('ok', result.status)
        self.assertEqual(Decimal('10'), result.positions[0].delivered_quantity)
        self.assertNotIn('UNIT_CONVERSION_REQUIRED', codes(result))
        self.assertNotIn('PRICE_MISMATCH', codes(result))

    def test_decimal_quantities_sum_exactly(self):
        source = request()
        source['documents'][0]['items'][0]['quantity'] = '0.3'
        source['documents'][1]['items'][0]['quantity'] = '0.1'
        source['documents'][2]['items'][0]['quantity'] = '0.3'
        second = renamed(source['documents'][1], 'delivery-2', 'Н-002')
        second['items'][0]['quantity'] = '0.2'
        source['documents'].append(second)
        source['documents'][2]['related_delivery_ids'] = ['delivery-1', 'delivery-2']
        result = evaluate(source)
        self.assertEqual(Decimal('0.3'), result.positions[0].delivered_quantity)
        self.assertEqual('ok', result.status)

    def test_future_amendment_does_not_remove_current_specification(self):
        source = request('T04')
        for document in source['documents']:
            if document['id'] in ('spec-v2', 'amendment-1'):
                document['date'] = '2026-10-25'
                document['effective_from'] = '2026-11-01'
            if document['type'] in ('delivery', 'acceptance'):
                document['items'][0]['quantity'] = '10'
        result = evaluate(source)
        self.assertEqual('ok', result.status)
        self.assertEqual(Decimal('10'), result.positions[0].expected_quantity)
        self.assertNotIn('amendment-1', result.applied_amendment_ids)

    def test_earlier_delivery_uses_price_effective_on_delivery_date(self):
        source = request('T04')
        for document in source['documents']:
            if document['id'] in ('spec-v2', 'amendment-1'):
                document['date'] = '2026-10-20'
                document['effective_from'] = '2026-10-20'
            if document['items']:
                document['items'][0]['quantity'] = '10'
            if document['id'] == 'spec-v2':
                document['items'][0]['unit_price'] = '1500'
        result = evaluate(source)
        self.assertNotIn('PRICE_MISMATCH', codes(result))
        self.assertEqual('ok', result.status)
        self.assertIn('amendment-1', result.applied_amendment_ids)

    def test_unapproved_amendment_does_not_change_terms(self):
        source = request('T11')
        source['documents'][-1]['approved'] = False
        result = evaluate(source)
        self.assertIn('LATE_DELIVERY', codes(result))
        self.assertNotIn('amendment-1', result.applied_amendment_ids)

    def test_late_delivery_without_amendment_is_explained(self):
        source = request()
        source['documents'][1]['date'] = '2026-10-25'
        result = evaluate(source)
        self.assertIn('LATE_DELIVERY', codes(result))
        signal = next(signal for signal in result.signals if signal.code == 'LATE_DELIVERY')
        self.assertTrue(signal.rule)
        self.assertGreaterEqual(len(signal.sources), 2)

    def test_currency_difference_is_not_silently_compared(self):
        source = request()
        source['documents'][1]['items'][0]['currency'] = 'USD'
        result = evaluate(source)
        self.assertNotEqual('ok', result.status)
        self.assertNotIn('PRICE_MISMATCH', codes(result))
        self.assertTrue(any(signal.blocks_comparison and ('валют' in signal.explanation.lower() or 'CURRENCY' in signal.code) for signal in result.signals))

    def test_unknown_tax_basis_is_not_assumed(self):
        source = request()
        source['documents'][1]['items'][0]['tax_included'] = None
        source['documents'][1]['items'][0]['vat_rate'] = None
        result = evaluate(source)
        self.assertIn('PRICE_BASIS_UNKNOWN', codes(result))
        self.assertNotIn('PRICE_MISMATCH', codes(result))

    def test_ambiguous_name_match_does_not_create_false_quantity_signal(self):
        source = request()
        first = source['documents'][0]['items'][0]
        second = copy.deepcopy(first)
        second.update(line_id='2', position_id='P-02', sku='BENCH-02')
        source['documents'][0]['items'].append(second)
        item = source['documents'][1]['items'][0]
        item['sku'] = None
        item['position_id'] = None
        result = evaluate(source)
        self.assertIn('AMBIGUOUS_MATCH', codes(result))
        self.assertNotIn('QUANTITY_MISMATCH', codes(result))
        self.assertTrue(any(match.state == 'needs_review' for match in result.matches))

    def test_other_contract_is_excluded_from_totals(self):
        source = request()
        source['documents'][1]['contract_id'] = 'К-ДРУГОЙ'
        result = evaluate(source)
        self.assertIn('CONTRACT_MISMATCH', codes(result))
        self.assertNotEqual('ok', result.status)
        self.assertNotEqual(Decimal('10'), result.positions[0].delivered_quantity)

    def test_partial_delivery_without_closure_is_not_shortage(self):
        source = request()
        source['documents'][1]['items'][0]['quantity'] = '8'
        source['documents'][2]['items'][0]['quantity'] = '8'
        result = evaluate(source)
        self.assertEqual('ok', result.status)
        self.assertEqual(Decimal('2'), result.positions[0].remaining_quantity)
        self.assertNotIn('INCOMPLETE_DELIVERY', codes(result))

    def test_explicit_completed_set_reports_remaining_quantity(self):
        source = request()
        source['delivery_complete'] = True
        source['documents'][1]['items'][0]['quantity'] = '8'
        source['documents'][2]['items'][0]['quantity'] = '8'
        result = evaluate(source)
        self.assertIn('INCOMPLETE_DELIVERY', codes(result))
        self.assertEqual(Decimal('2'), result.positions[0].remaining_quantity)

    def test_cancelled_correction_does_not_resurrect_superseded_original(self):
        source = request('T08')
        source['documents'][1]['status'] = 'superseded'
        source['documents'][2]['status'] = 'cancelled'
        result = evaluate(source)
        self.assertNotEqual('ok', result.status)
        self.assertIsNone(result.positions[0].delivered_quantity)
        self.assertNotIn('delivery-1', result.active_document_ids)
        self.assertNotIn('delivery-1-v2', result.active_document_ids)

    def test_competing_corrections_require_review(self):
        source = request('T08')
        competing = renamed(source['documents'][2], 'delivery-1-v2-other', 'Н-001-ИСП')
        competing['items'][0]['quantity'] = '12'
        source['documents'].append(competing)
        result = evaluate(source)
        self.assertIn('DOCUMENT_VERSION_CONFLICT', codes(result))
        self.assertIsNone(result.positions[0].delivered_quantity)

    def test_superseded_contract_without_replacement_is_unusable(self):
        source = request()
        source['documents'][0]['status'] = 'superseded'
        result = evaluate(source)
        self.assertEqual('incomplete', result.status)
        self.assertIn('DOCUMENT_VERSION_CONFLICT', codes(result))
        self.assertNotIn('contract', result.active_document_ids)

    def test_empty_contract_cannot_report_a_successful_check(self):
        source = request()
        source['documents'][0]['items'] = []
        result = evaluate(source)
        self.assertEqual('incomplete', result.status)
        self.assertTrue(any(signal.blocks_comparison for signal in result.signals))
        self.assertEqual([], result.positions)

    def test_missing_document_date_does_not_produce_numeric_conclusion(self):
        source = request()
        source['documents'][1]['date'] = None
        result = evaluate(source)
        self.assertEqual('incomplete', result.status)
        self.assertIsNone(result.positions[0].delivered_quantity)
        self.assertNotIn('QUANTITY_MISMATCH', codes(result))

    def test_extra_unmatched_goods_are_reported(self):
        source = request()
        extra = copy.deepcopy(source['documents'][1]['items'][0])
        extra.update(line_id='2', position_id=None, sku='UNREQUESTED-ITEM', name='Дополнительный неизвестный товар', quantity='3')
        source['documents'][1]['items'].append(extra)
        result = evaluate(source)
        self.assertIn('UNMATCHED_ITEM', codes(result))
        self.assertEqual('incomplete', result.status)

    def test_unknown_manual_link_does_not_silently_disappear(self):
        source = request()
        source['manual_links'] = [{'document_id': 'missing-document', 'line_id': '1', 'target_line_id': '1',
                                   'reviewer': 'Эксперт', 'comment': 'Ссылка на неизвестный документ.'}]
        result = evaluate(source)
        self.assertEqual('incomplete', result.status)
        self.assertTrue(any(signal.blocks_comparison for signal in result.signals))

    def test_conflicting_extraction_under_same_hash_is_not_deduplicated(self):
        source = request('T05')
        source['documents'][-1]['items'][0]['quantity'] = '8'
        result = evaluate(source)
        self.assertIn('CONTENT_HASH_CONFLICT', codes(result))
        self.assertIsNone(result.positions[0].delivered_quantity)

    def test_linked_acceptance_cannot_borrow_another_deliveries_quantity(self):
        source = request('T03')
        original_act = source['documents'][-1]
        original_act['items'][0]['quantity'] = '2'
        original_act['related_delivery_ids'] = ['delivery-1']
        other_act = renamed(original_act, 'acceptance-2', 'А-002')
        other_act['items'][0]['quantity'] = '8'
        other_act['related_delivery_ids'] = ['delivery-2']
        source['documents'].append(other_act)
        result = evaluate(source)
        self.assertEqual(Decimal('10'), result.positions[0].delivered_quantity)
        self.assertEqual(Decimal('10'), result.positions[0].accepted_quantity)
        self.assertIn('QUANTITY_MISMATCH', codes(result))
        mismatch = [signal for signal in result.signals if signal.code == 'QUANTITY_MISMATCH']
        self.assertTrue(any({'delivery-2', 'acceptance-2'} <= {s.document_id for s in signal.sources} for signal in mismatch))

    def test_acceptance_after_price_change_uses_linked_delivery_conditions(self):
        source = request('T04')
        for document in source['documents']:
            if document['id'] in ('spec-v2', 'amendment-1'):
                document['date'] = '2026-10-20'
                document['effective_from'] = '2026-10-20'
            if document['items']:
                document['items'][0]['quantity'] = '10'
            if document['id'] == 'spec-v2':
                document['items'][0]['unit_price'] = '1500'
            if document['type'] == 'acceptance':
                document['date'] = '2026-10-25'
        result = evaluate(source)
        self.assertEqual('ok', result.status)
        self.assertNotIn('PRICE_MISMATCH', codes(result))

    def test_cumulative_acceptance_is_blocked_instead_of_double_counted(self):
        source = request()
        source['documents'][2]['acceptance_mode'] = 'cumulative'
        result = evaluate(source)
        self.assertEqual('incomplete', result.status)
        self.assertIn('CUMULATIVE_ACCEPTANCE_UNSUPPORTED', codes(result))
        self.assertIsNone(result.positions[0].accepted_quantity)

    def test_evidence_and_link_aliases_resolve_to_real_documents(self):
        source = request('T05')
        source['documents'][2]['related_delivery_ids'] = ['delivery-1-copy']
        result = evaluate(source)
        self.assertEqual('ok', result.status)
        actual_ids = {document['id'] for document in source['documents']}
        all_sources = [ref for position in result.positions for ref in position.sources]
        all_sources += [ref for signal in result.signals for ref in signal.sources]
        self.assertTrue(all(ref.document_id in actual_ids for ref in all_sources))
        self.assertTrue(all(match.document_id in actual_ids and match.target_document_id in actual_ids for match in result.matches))
        self.assertTrue(all(doc_id in actual_ids for event in result.audit for doc_id in event.document_ids))

    def test_uncertain_date_or_deadline_does_not_claim_lateness(self):
        for document_index, field in ((1, 'date'), (0, 'deadline')):
            with self.subTest(field=field):
                source = request()
                source['documents'][1]['date'] = '2026-10-25'
                source['documents'][document_index]['fields_confidence'] = {field: 0.5}
                result = evaluate(source)
                self.assertEqual('incomplete', result.status)
                self.assertIn('LOW_CONFIDENCE', codes(result))
                self.assertNotIn('LATE_DELIVERY', codes(result))


if __name__ == '__main__':
    unittest.main(verbosity=2)
