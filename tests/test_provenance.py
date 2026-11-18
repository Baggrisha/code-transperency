"""Regression checks for inherited deadlines and uncertain linked delivery dates."""
from __future__ import annotations

import copy
import unittest

from kontrol.engine import run_check
from kontrol.models import CheckRequest
from tests.oracle import load_cases

CASES = {case['id']: case for case in load_cases()}


def run(value):
    return run_check(CheckRequest.model_validate(value))


class TemporalProvenance(unittest.TestCase):
    def test_deadline_inheritance_uses_actual_source_and_confidence(self):
        for variant in ('contract', 'earlier_amendment', 'uncertain_contract'):
            with self.subTest(variant=variant):
                source = copy.deepcopy(CASES['T04']['request'])
                documents = {document['id']: document for document in source['documents']}
                documents['delivery-1']['date'] = '2026-10-25'
                documents['acceptance-1']['date'] = '2026-10-26'
                expected_owner = 'contract'
                if variant == 'earlier_amendment':
                    earlier = copy.deepcopy(documents['amendment-1'])
                    earlier.update(id='deadline-amendment', number='ДС-СРОК', date='2026-10-09',
                                   effective_from='2026-10-10', replacement_spec_id=None,
                                   deadline='2026-10-18', source=None)
                    source['documents'].append(earlier)
                    expected_owner = 'deadline-amendment'
                elif variant == 'uncertain_contract':
                    documents['contract']['fields_confidence'] = {'deadline': 0.4}
                result = run(source)
                late = [signal for signal in result.signals if signal.code == 'LATE_DELIVERY']
                if variant == 'uncertain_contract':
                    self.assertEqual([], late)
                    self.assertIn('LOW_CONFIDENCE', {signal.code for signal in result.signals})
                else:
                    self.assertEqual(1, len(late))
                    deadline_sources = [ref for ref in late[0].sources if ref.field == 'deadline']
                    self.assertEqual([expected_owner], [ref.document_id for ref in deadline_sources])
                    self.assertNotIn('amendment-1', [ref.document_id for ref in deadline_sources])

    def test_acceptance_price_blocks_when_linked_delivery_date_is_untrusted(self):
        for variant in ('low_date_confidence', 'incomplete_delivery'):
            with self.subTest(variant=variant):
                source = copy.deepcopy(CASES['T04']['request'])
                documents = {document['id']: document for document in source['documents']}
                documents['spec-v2']['items'][0]['unit_price'] = '1500'
                documents['delivery-1']['date'] = '2026-10-14'
                documents['acceptance-1']['items'][0]['unit_price'] = '1500'
                if variant == 'low_date_confidence':
                    documents['delivery-1']['fields_confidence'] = {'date': 0.4}
                else:
                    documents['delivery-1']['is_complete'] = False
                    documents['delivery-1']['expected_pages'] = 2
                    documents['delivery-1']['actual_pages'] = 1
                result = run(source)
                codes = {signal.code for signal in result.signals}
                self.assertNotIn('PRICE_MISMATCH', codes)
                self.assertIn('PRICE_BASIS_UNKNOWN', codes)
                self.assertEqual('incomplete', result.status)
                basis_signals = [signal for signal in result.signals if signal.code == 'PRICE_BASIS_UNKNOWN']
                self.assertTrue(any(ref.document_id == 'delivery-1' and ref.field == 'date'
                                    for signal in basis_signals for ref in signal.sources))


if __name__ == '__main__':
    unittest.main()
