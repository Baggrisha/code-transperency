"""Independent regressions for uncertain deadlines and historical price bases."""
from __future__ import annotations

import copy
import unittest
from decimal import Decimal

from kontrol.engine import run_check
from kontrol.models import CheckRequest


def item(position, price, *, quantity='1', unit='шт', **extra):
    return {
        'line_id': position,
        'position_id': position,
        'sku': 'SKU-' + position,
        'name': 'Товар ' + position,
        'unit': unit,
        'quantity': quantity,
        'unit_price': price,
        'currency': 'RUB',
        'tax_included': True,
        'vat_rate': '20',
        **extra,
    }


def document(id, type, date, items=None, **extra):
    return {
        'id': id,
        'type': type,
        'number': id,
        'contract_id': 'К-1',
        'date': date,
        'is_complete': True,
        'items': items or [],
        **extra,
    }


def check(documents):
    return run_check(CheckRequest.model_validate({
        'as_of': '2026-01-31',
        'selected_contract_id': 'contract',
        'documents': documents,
    }))


def signals(result, code, *, document_id=None):
    return [signal for signal in result.signals
            if signal.code == code and (document_id is None or
               any(ref.document_id == document_id for ref in signal.sources))]


def deadline_documents():
    return [
        document('contract', 'contract', '2026-01-01', [item('A', '100')],
                 deadline='2026-01-10'),
        document('delivery', 'delivery', '2026-01-15', [item('A', '100')]),
        document('acceptance', 'acceptance', '2026-01-16', [item('A', '100')],
                 related_delivery_ids=['delivery']),
        document('amendment', 'amendment', '2026-01-04',
                 effective_from='2026-01-05', base_document_id='contract',
                 approved=True, deadline='2026-01-25'),
    ]


def changed_price_documents():
    """One act covers A from the old price and B from the new price."""
    return [
        document('contract', 'contract', '2026-01-01',
                 [item('A', '100'), item('B', '200')], deadline='2026-01-30'),
        document('spec-v2', 'specification', '2026-01-10',
                 [item('A', '150'), item('B', '250')]),
        document('amendment', 'amendment', '2026-01-10',
                 effective_from='2026-01-11', base_document_id='contract',
                 replacement_spec_id='spec-v2', approved=True),
        document('delivery-a', 'delivery', '2026-01-08', [item('A', '100')]),
        document('delivery-b', 'delivery', '2026-01-12', [item('B', '250')]),
        document('acceptance', 'acceptance', '2026-01-15',
                 [item('A', '100'), item('B', '250')],
                 related_delivery_ids=['delivery-a', 'delivery-b']),
    ]


def partial_price_documents(old_price, new_price):
    return [
        document('contract', 'contract', '2026-01-01',
                 [item('A', old_price, quantity='2')], deadline='2026-01-30'),
        document('spec-v2', 'specification', '2026-01-10',
                 [item('A', new_price, quantity='2')]),
        document('amendment', 'amendment', '2026-01-10',
                 effective_from='2026-01-11', base_document_id='contract',
                 replacement_spec_id='spec-v2', approved=True),
        document('delivery-old', 'delivery', '2026-01-08', [item('A', old_price)]),
        document('delivery-new', 'delivery', '2026-01-12', [item('A', new_price)]),
        document('acceptance', 'acceptance', '2026-01-15',
                 [item('A', new_price, quantity='2')],
                 related_delivery_ids=['delivery-old', 'delivery-new']),
    ]


class DeadlineReadinessRegressions(unittest.TestCase):
    def test_unreliable_or_incomplete_deadline_change_does_not_assert_lateness(self):
        variants = (
            {'fields_confidence': {'deadline': 0.5}},
            {'fields_confidence': {'effective_from': 0.5}},
            {'is_complete': False, 'expected_pages': 2, 'actual_pages': 1},
            {'deadline': None, 'missing_fields': ['deadline']},
            {'base_document_id': None},
        )
        for changes in variants:
            with self.subTest(changes=changes):
                documents = deadline_documents()
                documents[-1].update(changes)
                result = check(documents)
                self.assertEqual('incomplete', result.status)
                self.assertFalse(signals(result, 'LATE_DELIVERY', document_id='delivery'))
                unresolved = signals(result, 'DEADLINE_BASIS_UNKNOWN', document_id='amendment')
                self.assertTrue(unresolved)
                self.assertTrue(all(signal.blocks_comparison for signal in unresolved))
                self.assertTrue(any(ref.document_id == 'contract' and ref.field == 'deadline'
                                    for signal in unresolved for ref in signal.sources))

    def test_confident_later_change_does_not_hide_an_earlier_late_delivery(self):
        documents = deadline_documents()
        documents[-1]['effective_from'] = '2026-01-20'
        result = check(documents)
        late = signals(result, 'LATE_DELIVERY', document_id='delivery')
        self.assertEqual(1, len(late))
        self.assertTrue(any(ref.document_id == 'contract' and ref.field == 'deadline'
                            for ref in late[0].sources))
        self.assertFalse(signals(result, 'DEADLINE_BASIS_UNKNOWN'))

    def test_future_effective_date_with_low_confidence_cannot_exclude_change(self):
        documents = deadline_documents()
        documents[-1].update(effective_from='2026-02-10',
                             fields_confidence={'effective_from': 0.5})
        result = check(documents)
        self.assertFalse(signals(result, 'LATE_DELIVERY', document_id='delivery'))
        self.assertTrue(signals(result, 'DEADLINE_BASIS_UNKNOWN', document_id='amendment'))
        self.assertEqual('incomplete', result.status)

    def test_confirmed_future_effective_date_does_not_block_earlier_quantities(self):
        documents = deadline_documents()
        documents[0]['deadline'] = '2026-01-30'
        documents[-1].update(effective_from='2026-02-10',
                             fields_confidence={'status': 0.0})
        result = check(documents)
        self.assertTrue(signals(result, 'LOW_CONFIDENCE', document_id='amendment'))
        self.assertFalse(signals(result, 'DEADLINE_BASIS_UNKNOWN'))
        self.assertEqual(Decimal('1'), result.positions[0].expected_quantity)
        self.assertEqual(Decimal('1'), result.positions[0].delivered_quantity)
        self.assertEqual(Decimal('1'), result.positions[0].accepted_quantity)

    def test_confirmed_change_is_used_and_retains_its_deadline_source(self):
        documents = deadline_documents()
        documents[-1]['deadline'] = '2026-01-12'
        result = check(documents)
        late = signals(result, 'LATE_DELIVERY', document_id='delivery')
        self.assertEqual(1, len(late))
        self.assertEqual('2026-01-12', late[0].expected)
        self.assertTrue(any(ref.document_id == 'amendment' and ref.field == 'deadline'
                            for ref in late[0].sources))
        self.assertFalse(signals(result, 'DEADLINE_BASIS_UNKNOWN'))

    def test_incomplete_document_supplying_deadline_cannot_prove_lateness(self):
        documents = deadline_documents()[:3]
        documents[0].update(is_complete=False, expected_pages=2, actual_pages=1)
        result = check(documents)
        self.assertEqual('incomplete', result.status)
        self.assertTrue(signals(result, 'INCOMPLETE_DOCUMENT', document_id='contract'))
        self.assertFalse(signals(result, 'LATE_DELIVERY', document_id='delivery'))


class HistoricalPriceReadinessRegressions(unittest.TestCase):
    def test_missing_historical_conversion_blocks_price_but_not_row_arithmetic(self):
        documents = [
            document('contract', 'contract', '2026-01-01',
                     [item('A', '100', quantity='10')], deadline='2026-01-30'),
            document('spec-v2', 'specification', '2026-01-10',
                     [item('A', '1000', quantity='10', unit='упак')]),
            document('amendment', 'amendment', '2026-01-10',
                     effective_from='2026-01-11', base_document_id='contract',
                     replacement_spec_id='spec-v2', approved=True),
            document('delivery', 'delivery', '2026-01-08',
                     [item('A', '1500', quantity='2', unit='упак', amount='2900')]),
            document('acceptance', 'acceptance', '2026-01-09',
                     [item('A', '1500', quantity='2', unit='упак', amount='3000')],
                     related_delivery_ids=['delivery']),
        ]
        result = check(documents)
        self.assertEqual('incomplete', result.status)
        conversion = signals(result, 'UNIT_CONVERSION_REQUIRED', document_id='delivery')
        self.assertTrue(conversion)
        self.assertTrue(all(signal.blocks_comparison for signal in conversion))
        self.assertTrue(any(ref.document_id == 'contract' and ref.field == 'unit'
                            for signal in conversion for ref in signal.sources))
        amount = signals(result, 'AMOUNT_MISMATCH', document_id='delivery')
        self.assertEqual(1, len(amount))
        self.assertEqual('3000.00', amount[0].expected)
        self.assertEqual('2900', amount[0].actual)
        self.assertFalse(signals(result, 'PRICE_MISMATCH', document_id='delivery'))
        self.assertEqual(Decimal('2'), result.positions[0].delivered_quantity)

        for entry in documents[-2:]:
            entry['items'][0].update(conversion_factor='10', conversion_to='шт')
        resolved = check(documents)
        self.assertFalse(signals(resolved, 'UNIT_CONVERSION_REQUIRED', document_id='delivery'))
        price = [signal for signal in signals(resolved, 'PRICE_MISMATCH')
                 if any(ref.document_id == 'delivery' and ref.field == 'unit_price'
                        for ref in signal.sources)]
        self.assertEqual(1, len(price))
        self.assertEqual(Decimal('100'), Decimal(price[0].expected))
        self.assertEqual(Decimal('150'), Decimal(price[0].actual))
        acceptance_price = [signal for signal in signals(resolved, 'PRICE_MISMATCH')
                            if any(ref.document_id == 'acceptance' and ref.field == 'unit_price'
                                   for ref in signal.sources)]
        self.assertEqual(1, len(acceptance_price))
        self.assertNotEqual(price[0].id, acceptance_price[0].id)
        self.assertEqual(Decimal('100'), Decimal(acceptance_price[0].expected))
        self.assertEqual(Decimal('150'), Decimal(acceptance_price[0].actual))
        self.assertTrue(any(ref.document_id == 'delivery' and ref.field == 'date'
                            for ref in acceptance_price[0].sources))
        self.assertEqual(1, len(signals(resolved, 'AMOUNT_MISMATCH', document_id='delivery')))

    def test_act_uses_only_supplies_containing_each_position(self):
        result = check(changed_price_documents())
        self.assertEqual('ok', result.status)
        self.assertFalse(signals(result, 'PRICE_BASIS_UNKNOWN'))
        self.assertFalse(signals(result, 'PRICE_MISMATCH'))
        positions = {position.line_id: position for position in result.positions}
        for position in positions.values():
            self.assertEqual(Decimal('1'), position.delivered_quantity)
            self.assertEqual(Decimal('1'), position.accepted_quantity)

    def test_wrong_act_prices_are_compared_to_each_positions_actual_history(self):
        documents = changed_price_documents()
        documents[-1]['items'][0]['unit_price'] = '150'
        documents[-1]['items'][1]['unit_price'] = '200'
        result = check(documents)
        prices = {signal.position_id: signal for signal in
                  signals(result, 'PRICE_MISMATCH', document_id='acceptance')}
        self.assertEqual({'A', 'B'}, set(prices))
        self.assertEqual((Decimal('100'), Decimal('150')),
                         (Decimal(prices['A'].expected), Decimal(prices['A'].actual)))
        self.assertEqual((Decimal('250'), Decimal('200')),
                         (Decimal(prices['B'].expected), Decimal(prices['B'].actual)))
        self.assertTrue(any(ref.document_id == 'contract' for ref in prices['A'].sources))
        self.assertTrue(any(ref.document_id == 'spec-v2' for ref in prices['B'].sources))
        self.assertTrue(any(ref.document_id == 'delivery-a' and ref.field == 'date'
                            for ref in prices['A'].sources))
        self.assertTrue(any(ref.document_id == 'delivery-b' and ref.field == 'date'
                            for ref in prices['B'].sources))
        self.assertFalse(any(ref.document_id == 'delivery-b' for ref in prices['A'].sources))
        self.assertFalse(any(ref.document_id == 'delivery-a' for ref in prices['B'].sources))
        self.assertFalse(signals(result, 'PRICE_BASIS_UNKNOWN'))

    def test_historical_discrepancy_uses_the_current_position_identifier(self):
        documents = changed_price_documents()
        documents[0]['items'][0]['line_id'] = 'old-A'
        documents[1]['items'][0]['line_id'] = 'new-A'
        documents[-1]['items'][0]['unit_price'] = '150'
        result = check(documents)
        prices = signals(result, 'PRICE_MISMATCH', document_id='acceptance')
        self.assertEqual(1, len(prices))
        self.assertEqual('new-A', prices[0].position_id)
        self.assertTrue(any(ref.document_id == 'contract' for ref in prices[0].sources))
        self.assertTrue(any(ref.document_id == 'delivery-a' and ref.field == 'date'
                            for ref in prices[0].sources))

    def test_partial_supplies_of_same_position_at_different_prices_remain_ambiguous(self):
        result = check(partial_price_documents('100', '150'))
        unknown = signals(result, 'PRICE_BASIS_UNKNOWN', document_id='acceptance')
        self.assertTrue(unknown)
        self.assertTrue(all(signal.blocks_comparison for signal in unknown))
        self.assertFalse(signals(result, 'PRICE_MISMATCH', document_id='acceptance'))
        cited = {ref.document_id for signal in unknown for ref in signal.sources}
        self.assertTrue({'delivery-old', 'delivery-new'} <= cited)
        self.assertEqual(Decimal('2'), result.positions[0].delivered_quantity)
        self.assertEqual(Decimal('2'), result.positions[0].accepted_quantity)

    def test_equal_decimal_prices_with_different_scales_are_the_same_basis(self):
        result = check(partial_price_documents('100.0', '100.00'))
        self.assertEqual('ok', result.status)
        self.assertFalse(signals(result, 'PRICE_BASIS_UNKNOWN'))
        self.assertFalse(signals(result, 'PRICE_MISMATCH'))
        self.assertEqual(Decimal('2'), result.positions[0].accepted_quantity)

    def test_unlinked_act_does_not_use_a_later_partial_delivery(self):
        documents = partial_price_documents('100', '150')
        documents[-1].update(date='2026-01-09', related_delivery_ids=[])
        documents[-1]['items'][0].update(quantity='1', unit_price='100')
        result = check(documents)
        self.assertFalse(signals(result, 'PRICE_BASIS_UNKNOWN', document_id='acceptance'))
        self.assertFalse(signals(result, 'PRICE_MISMATCH', document_id='acceptance'))
        self.assertEqual('ok', result.status)

    def test_untrusted_later_delivery_date_cannot_be_excluded_from_an_unlinked_act(self):
        documents = partial_price_documents('100', '150')
        documents[-2]['fields_confidence'] = {'date': 0.0}
        documents[-1].update(date='2026-01-09', related_delivery_ids=[])
        documents[-1]['items'][0].update(quantity='1', unit_price='100')
        result = check(documents)
        self.assertTrue(signals(result, 'PRICE_BASIS_UNKNOWN', document_id='acceptance'))
        self.assertFalse(signals(result, 'PRICE_MISMATCH', document_id='acceptance'))
        self.assertIsNone(result.positions[0].delivered_quantity)

    def test_missing_or_untrusted_row_price_does_not_produce_an_amount_conclusion(self):
        for changes in ({'unit_price': None}, {'confidence': {'unit_price': 0.5}}):
            with self.subTest(changes=changes):
                documents = changed_price_documents()
                documents[-3]['items'][0].update(amount='1', **changes)
                result = check(documents)
                self.assertFalse(signals(result, 'AMOUNT_MISMATCH', document_id='delivery-a'))
                self.assertEqual('incomplete', result.status)

    def test_no_linked_supply_for_the_position_is_not_invented(self):
        documents = changed_price_documents()
        documents[-1]['related_delivery_ids'] = ['delivery-b']
        result = check(documents)
        unknown = [signal for signal in signals(result, 'PRICE_BASIS_UNKNOWN', document_id='acceptance')
                   if signal.position_id == 'A']
        self.assertTrue(unknown)
        self.assertTrue(all(signal.blocks_comparison for signal in unknown))
        self.assertFalse([signal for signal in signals(result, 'PRICE_MISMATCH', document_id='acceptance')
                          if signal.position_id == 'A'])

    def test_unmatched_linked_delivery_row_does_not_supply_a_certain_price(self):
        documents = changed_price_documents()
        documents[-2]['items'].append(item('unknown', '999', position_id=None, sku=None, name='?'))
        result = check(documents)
        self.assertTrue(signals(result, 'PRICE_BASIS_UNKNOWN', document_id='acceptance'))
        self.assertFalse(signals(result, 'PRICE_MISMATCH', document_id='acceptance'))

    def test_price_history_is_independent_of_document_order(self):
        documents = changed_price_documents()
        forward = check(documents)
        reverse = check(list(reversed(copy.deepcopy(documents))))
        self.assertEqual(forward.model_dump(mode='json'), reverse.model_dump(mode='json'))


class ControlFieldConfidenceRegressions(unittest.TestCase):
    def test_unverified_delivery_control_fields_cannot_hide_or_count_the_document(self):
        fields = ('id', 'type', 'number', 'contract_id', 'date', 'revision', 'logical_id',
                  'status', 'supersedes', 'is_complete', 'missing_fields',
                  'expected_pages', 'actual_pages', 'items')
        for field in fields:
            with self.subTest(field=field):
                documents = deadline_documents()[:3]
                documents[0]['deadline'] = '2026-01-30'
                documents[1]['fields_confidence'] = {field: 0.0}
                if field == 'status':
                    documents[1]['status'] = 'cancelled'
                result = check(documents)
                low = [signal for signal in signals(result, 'LOW_CONFIDENCE', document_id='delivery')
                       if any(ref.document_id == 'delivery' and ref.field == field for ref in signal.sources)]
                self.assertTrue(low)
                self.assertTrue(all(signal.blocks_comparison for signal in low))
                self.assertEqual('incomplete', result.status)
                if result.positions:
                    self.assertIsNone(result.positions[0].delivered_quantity)
                self.assertFalse(signals(result, 'LATE_DELIVERY', document_id='delivery'))

    def test_unverified_act_routing_does_not_create_acceptance_or_group_conclusions(self):
        for field in ('related_delivery_ids', 'acceptance_mode', 'items'):
            with self.subTest(field=field):
                documents = deadline_documents()[:3]
                documents[0]['deadline'] = '2026-01-30'
                documents[2]['fields_confidence'] = {field: 0.0}
                if field == 'related_delivery_ids':
                    documents[2]['related_delivery_ids'] = ['missing-delivery']
                if field == 'acceptance_mode':
                    documents[2]['acceptance_mode'] = 'cumulative'
                result = check(documents)
                self.assertTrue(signals(result, 'LOW_CONFIDENCE', document_id='acceptance'))
                self.assertEqual(Decimal('1'), result.positions[0].delivered_quantity)
                self.assertIsNone(result.positions[0].accepted_quantity)
                self.assertFalse(signals(result, 'QUANTITY_MISMATCH'))
                self.assertFalse(signals(result, 'MISSING_DOCUMENT', document_id='acceptance'))

    def test_unverified_amendment_control_cannot_prove_old_deadline(self):
        for field, value in (('approved', False), ('base_document_id', None),
                             ('status', 'cancelled'), ('type', 'delivery')):
            with self.subTest(field=field):
                documents = deadline_documents()
                documents[-1][field] = value
                documents[-1]['fields_confidence'] = {field: 0.0}
                result = check(documents)
                self.assertTrue(signals(result, 'LOW_CONFIDENCE', document_id='amendment'))
                self.assertFalse(signals(result, 'LATE_DELIVERY', document_id='delivery'))
                self.assertTrue(signals(result, 'DEADLINE_BASIS_UNKNOWN', document_id='amendment'))
                self.assertNotIn('amendment', result.applied_amendment_ids)

    def test_unverified_replacement_cannot_leave_its_possible_predecessor_proven(self):
        documents = deadline_documents()[:3]
        replacement = document('replacement', 'delivery', '2026-01-16', [item('A', '100')],
                               supersedes='delivery', fields_confidence={'supersedes': 0.0})
        documents.append(replacement)
        result = check(documents)
        self.assertTrue(signals(result, 'LOW_CONFIDENCE', document_id='replacement'))
        self.assertFalse(signals(result, 'LATE_DELIVERY', document_id='delivery'))
        self.assertIsNone(result.positions[0].delivered_quantity)

    def test_unverified_duplicate_id_does_not_select_the_other_version(self):
        documents = deadline_documents()[:3]
        other_version = copy.deepcopy(documents[1])
        other_version.update(status='cancelled', fields_confidence={'status': 0.0})
        documents.append(other_version)
        result = check(documents)
        self.assertTrue(signals(result, 'LOW_CONFIDENCE', document_id='delivery'))
        self.assertTrue(signals(result, 'DUPLICATE_ID_CONFLICT', document_id='delivery'))
        self.assertNotIn('delivery', result.active_document_ids)
        self.assertIsNone(result.positions[0].delivered_quantity)
        self.assertFalse(signals(result, 'LATE_DELIVERY', document_id='delivery'))

    def test_quarantined_document_is_not_reported_as_missing_from_manual_link(self):
        documents = deadline_documents()[:3]
        documents[0]['deadline'] = '2026-01-30'
        documents[1]['fields_confidence'] = {'status': 0.0}
        result = run_check(CheckRequest.model_validate({
            'as_of': '2026-01-31', 'selected_contract_id': 'contract', 'documents': documents,
            'manual_links': [{'document_id': 'delivery', 'line_id': 'A', 'target_line_id': 'A',
                              'reviewer': 'Проверяющий', 'comment': 'Связь строк'}],
        }))
        self.assertTrue(signals(result, 'LOW_CONFIDENCE', document_id='delivery'))
        self.assertFalse(signals(result, 'INVALID_MANUAL_LINK'))
        self.assertIsNone(result.positions[0].delivered_quantity)

    def test_unverified_contract_selection_does_not_pick_a_specification(self):
        documents = changed_price_documents()
        documents[0]['fields_confidence'] = {'specification_id': 0.0}
        result = check(documents)
        self.assertTrue(signals(result, 'LOW_CONFIDENCE', document_id='contract'))
        self.assertEqual('incomplete', result.status)
        self.assertEqual([], result.positions)
        self.assertFalse(signals(result, 'PRICE_MISMATCH'))

    def test_unverified_separate_delivery_does_not_discard_trusted_delivery(self):
        documents = deadline_documents()[:3]
        documents[0]['deadline'] = '2026-01-30'
        documents.append(document('other', 'delivery', '2026-01-20', [item('A', '100')],
                                  status='cancelled', fields_confidence={'status': 0.0}))
        result = check(documents)
        self.assertIn('delivery', result.active_document_ids)
        self.assertTrue(any(match.document_id == 'delivery' and match.state == 'matched'
                            for match in result.matches))
        self.assertIsNone(result.positions[0].delivered_quantity)

    def test_unverified_item_identifiers_do_not_create_automatic_matches(self):
        for document_index in (0, 1):
            for field in ('line_id', 'position_id', 'sku'):
                with self.subTest(document_index=document_index, field=field):
                    documents = deadline_documents()[:3]
                    documents[0]['deadline'] = '2026-01-30'
                    row = documents[document_index]['items'][0]
                    row['confidence'] = {field: 0.0}
                    if field == 'sku':
                        row['sku'] = None
                    result = check(documents)
                    matches = [match for match in result.matches if match.document_id == 'delivery']
                    self.assertEqual(1, len(matches))
                    self.assertNotEqual('matched', matches[0].state)
                    self.assertIsNone(result.positions[0].delivered_quantity)
                    self.assertTrue(signals(result, 'LOW_CONFIDENCE'))

    def test_unverified_other_candidate_cannot_establish_match_uniqueness(self):
        documents = changed_price_documents()
        documents[1]['items'][1]['confidence'] = {'position_id': 0.0}
        result = check(documents)
        matches = [match for match in result.matches if match.document_id == 'delivery-a']
        self.assertEqual(1, len(matches))
        self.assertNotEqual('matched', matches[0].state)
        self.assertTrue(signals(result, 'LOW_CONFIDENCE', document_id='spec-v2'))

    def test_unverified_unit_does_not_create_a_conversion_conclusion(self):
        for document_index in (0, 1):
            with self.subTest(document_index=document_index):
                documents = deadline_documents()[:3]
                documents[0]['deadline'] = '2026-01-30'
                row = documents[document_index]['items'][0]
                row['unit'] = 'упак'
                row['confidence'] = {'unit': 0.0}
                result = check(documents)
                self.assertTrue(signals(result, 'LOW_CONFIDENCE'))
                self.assertFalse(signals(result, 'UNIT_CONVERSION_REQUIRED', document_id='delivery'))
                self.assertIsNone(result.positions[0].delivered_quantity)
                if document_index == 0:
                    self.assertIsNone(result.positions[0].unit)

    def test_unverified_attributes_do_not_create_attribute_discrepancy(self):
        for document_index in (0, 1):
            with self.subTest(document_index=document_index):
                documents = deadline_documents()[:3]
                documents[0]['deadline'] = '2026-01-30'
                documents[0]['items'][0]['attributes'] = {'цвет': 'красный'}
                documents[1]['items'][0]['attributes'] = {'цвет': 'синий'}
                documents[document_index]['items'][0]['confidence'] = {'attributes': 0.0}
                result = check(documents)
                self.assertTrue(signals(result, 'LOW_CONFIDENCE'))
                self.assertFalse(signals(result, 'ATTRIBUTE_MISMATCH', document_id='delivery'))




if __name__ == '__main__':
    unittest.main()
