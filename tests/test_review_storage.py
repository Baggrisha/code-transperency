"""Review updates preserve the distinction between edits and explicit verification."""
from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pdfplumber
from fastapi.testclient import TestClient

from kontrol.api import create_app
from kontrol.models import Document
from kontrol.storage import ConflictError, Store
from tests.oracle import load_cases


def example_document() -> Document:
    return Document.model_validate({
        'id': 'doc-1', 'type': 'acceptance', 'number': 'ACT-1',
        'contract_id': 'contract-1', 'date': '2026-01-01',
        'base_document_id': 'spec-1', 'acceptance_mode': 'incremental',
        'related_delivery_ids': ['delivery-1'], 'expected_pages': 1, 'actual_pages': 1,
        'filename': 'original.pdf', 'content_hash': 'original-hash',
        'extraction_method': 'pdf-text', 'extraction_warnings': ['original warning'],
        'source': {'document_id': 'doc-1', 'filename': 'original.pdf', 'page': 1,
                   'excerpt': 'Original document evidence'},
        'fields_confidence': {'date': 0.4, 'base_document_id': 1.0},
        'items': [{
            'line_id': 'line-1', 'name': 'Widget', 'unit': 'шт', 'quantity': '8',
            'unit_price': '100', 'currency': 'RUB', 'tax_included': True,
            'confidence': {'quantity': 1.0, 'unit_price': 0.3, 'line_id': 0.4},
            'source': {'document_id': 'doc-1', 'filename': 'original.pdf', 'page': 1,
                       'row': 4, 'excerpt': 'Original row evidence'},
            'field_sources': {'quantity': {'document_id': 'doc-1', 'filename': 'original.pdf',
                                           'page': 1, 'row': 4, 'field': 'quantity',
                                           'excerpt': 'Original quantity is 8'}},
        }],
    })


class StoreReviewTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='kontrol-review-store-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = Store(self.root)
        self.case_id = self.store.create_case('Review')['id']
        self.original = example_document()
        self.store.add_document(self.case_id, self.original)

    def update(self, document, revision=1, **kwargs):
        return self.store.update_document(self.case_id, 'doc-1', document, revision,
                                          'Reviewer', 'Checked against source', **kwargs)

    def test_all_reconciliation_fields_are_audited_without_implicit_confirmation(self):
        edited = self.original.model_copy(deep=True)
        changes = {
            'type': 'delivery', 'number': 'ACT-2', 'contract_id': 'contract-2',
            'date': '2026-02-01', 'effective_from': '2026-02-01', 'deadline': '2026-03-01',
            'revision': 2, 'logical_id': 'logical-1', 'status': 'draft', 'supersedes': 'doc-0',
            'specification_id': 'spec-2', 'base_document_id': 'spec-3',
            'replacement_spec_id': 'spec-4', 'approved': True,
            'related_delivery_ids': ['delivery-2'], 'acceptance_mode': 'cumulative',
            'is_complete': True, 'missing_fields': ['date'], 'expected_pages': 2, 'actual_pages': 2,
        }
        for field, value in changes.items():
            setattr(edited, field, value)
            edited.fields_confidence[field] = 1.0  # A stale full-document payload is not a confirmation.
        edited.items[0].quantity = '10'
        edited.items[0].confidence['quantity'] = 1.0
        edited.source.excerpt = 'Forged document source'
        edited.items[0].source.excerpt = 'Forged row source'
        edited.items[0].field_sources['quantity'].excerpt = 'Forged field source'
        edited.filename = 'other.pdf'
        edited.content_hash = 'other-hash'

        result = self.update(edited)
        doc = result['document']
        self.assertEqual(2, result['revision'])
        self.assertTrue(all(doc['fields_confidence'][field] == 0 for field in changes))
        self.assertTrue(all(field in doc['source']['excerpt'] for field in changes))
        self.assertIn('Original document evidence', doc['source']['excerpt'])
        self.assertNotIn('Forged', doc['source']['excerpt'])
        self.assertEqual('original.pdf', doc['filename'])
        self.assertEqual('original-hash', doc['content_hash'])
        self.assertEqual('pdf-text', doc['extraction_method'])
        item = doc['items'][0]
        self.assertEqual(0, item['confidence']['quantity'])
        self.assertEqual(0.3, item['confidence']['unit_price'])
        self.assertEqual('Original row evidence', item['source']['excerpt'])
        self.assertIn('Original quantity is 8', item['field_sources']['quantity']['excerpt'])
        self.assertNotIn('Forged', item['field_sources']['quantity']['excerpt'])
        history = self.store.document_history(self.case_id, 'doc-1')
        self.assertEqual([1, 2], [entry['revision'] for entry in history])
        self.assertEqual(self.original.model_dump(mode='json'), history[0]['document'])
        self.assertEqual('Reviewer', history[1]['reviewer'])
        self.assertEqual('Checked against source', history[1]['comment'])
        self.assertEqual(doc, history[1]['document'])

    def test_explicit_confirmation_of_changed_and_unchanged_fields_is_audited(self):
        edited = self.original.model_copy(deep=True)
        edited.items[0].quantity = '10'
        result = self.update(edited, confirmed_fields=['date'],
                             confirmed_items={'line-1': ['quantity', 'unit_price', 'line_id']})
        doc = result['document']
        self.assertEqual(1.0, doc['fields_confidence']['date'])
        self.assertIn('date', doc['source']['excerpt'])
        for field in ('quantity', 'unit_price', 'line_id'):
            with self.subTest(field=field):
                self.assertEqual(1.0, doc['items'][0]['confidence'][field])
                ref = doc['items'][0]['field_sources'][field]
                self.assertEqual(field, ref['field'])
                self.assertIn('Reviewer', ref['excerpt'])
                self.assertIn('Checked against source', ref['excerpt'])
        self.assertIn('Original quantity is 8', doc['items'][0]['field_sources']['quantity']['excerpt'])
        self.assertIn('Ручное изменение (Reviewer): quantity: 8 → 10 (подтверждено)',
                      doc['items'][0]['field_sources']['quantity']['excerpt'])
        self.assertIn('Подтверждение (Reviewer): unit_price:', doc['items'][0]['field_sources']['unit_price']['excerpt'])
        self.assertNotIn('Ручное изменение', doc['items'][0]['field_sources']['unit_price']['excerpt'])
        self.assertIn('Подтверждение (Reviewer): date:', doc['source']['excerpt'])
        self.assertNotIn('Ручное изменение', doc['source']['excerpt'])
        self.assertEqual('Original row evidence', doc['items'][0]['source']['excerpt'])

    def test_unchanged_confirmation_preserves_sources_and_version_history(self):
        result = self.update(self.original, confirmed_fields=['date'],
                             confirmed_items={'line-1': ['quantity']})
        doc = result['document']
        field_source = doc['items'][0]['field_sources']['quantity']
        self.assertEqual(1.0, doc['items'][0]['confidence']['quantity'])
        self.assertEqual((1, 4, 'quantity'), (field_source['page'], field_source['row'], field_source['field']))
        self.assertIn('Original quantity is 8', field_source['excerpt'])
        self.assertIn('Подтверждение (Reviewer): quantity: 8', field_source['excerpt'])
        self.assertIn('Checked against source', field_source['excerpt'])
        self.assertNotIn('Ручное изменение', field_source['excerpt'])
        self.assertIn('Original document evidence', doc['source']['excerpt'])
        self.assertIn('Подтверждение (Reviewer): date: 2026-01-01', doc['source']['excerpt'])
        self.assertNotIn('Ручное изменение', doc['source']['excerpt'])
        history = self.store.document_history(self.case_id, 'doc-1')
        self.assertEqual([1, 2], [version['revision'] for version in history])
        self.assertEqual(self.original.model_dump(mode='json'), history[0]['document'])
        self.assertEqual(doc, history[1]['document'])
        self.assertEqual(('Reviewer', 'Checked against source'), (history[1]['reviewer'], history[1]['comment']))

    def test_changed_and_confirmed_document_fields_are_distinct_in_same_audit(self):
        edited = self.original.model_copy(deep=True)
        edited.number = 'ACT-2'
        doc = self.update(edited, confirmed_fields=['number', 'date'])['document']
        excerpt = doc['source']['excerpt']
        change = next(line for line in excerpt.splitlines() if line.startswith('Ручное изменение'))
        confirmation = next(line for line in excerpt.splitlines() if line.startswith('Подтверждение'))
        self.assertIn('number: ACT-1 → ACT-2 (подтверждено)', change)
        self.assertNotIn('date:', change)
        self.assertIn('date: 2026-01-01', confirmation)
        self.assertNotIn('number:', confirmation)
        self.assertIn('Checked against source', change)
        self.assertIn('Checked against source', confirmation)

    def test_confidence_cannot_be_raised_or_deleted_without_confirmation(self):
        edited = self.original.model_copy(deep=True)
        edited.fields_confidence = {'date': 1.0}
        edited.items[0].confidence = {'unit_price': 1.0, 'quantity': 0.2, 'line_id': 1.0}
        doc = self.update(edited)['document']
        self.assertEqual(0.4, doc['fields_confidence']['date'])
        self.assertEqual(1.0, doc['fields_confidence']['base_document_id'])
        self.assertEqual(0.3, doc['items'][0]['confidence']['unit_price'])
        self.assertEqual(0.2, doc['items'][0]['confidence']['quantity'])
        self.assertEqual(0.4, doc['items'][0]['confidence']['line_id'])
        self.assertIn('quantity', doc['items'][0]['field_sources'])
        excerpt = doc['items'][0]['field_sources']['quantity']['excerpt']
        self.assertIn('Уверенность снижена (Reviewer): quantity: 1.0 → 0.2', excerpt)
        self.assertNotIn('Ручное изменение', excerpt)

    def test_removing_unknown_confidence_keys_is_explicit_audited_and_versioned(self):
        original = self.original.model_copy(deep=True)
        original.fields_confidence['not_a_document_field'] = 0.1
        original.items[0].confidence['not_an_item_field'] = 0.2
        case_id = self.store.create_case('Unknown confidence')['id']
        self.store.add_document(case_id, original)
        edited = original.model_copy(deep=True)
        del edited.fields_confidence['not_a_document_field']
        del edited.items[0].confidence['not_an_item_field']
        first = self.store.update_document(case_id, 'doc-1', edited, 1, 'Reviewer', 'No removal')['document']
        self.assertEqual(0.1, first['fields_confidence']['not_a_document_field'])
        self.assertEqual(0.2, first['items'][0]['confidence']['not_an_item_field'])
        second = self.store.update_document(
            case_id, 'doc-1', Document.model_validate(first), 2, 'Reviewer', 'Remove invalid metadata',
            discarded_confidence_fields=['not_a_document_field'],
            discarded_item_confidence={'line-1': ['not_an_item_field']})['document']
        self.assertNotIn('not_a_document_field', second['fields_confidence'])
        self.assertNotIn('not_an_item_field', second['items'][0]['confidence'])
        self.assertEqual(0.4, second['fields_confidence']['date'])
        self.assertEqual(0.3, second['items'][0]['confidence']['unit_price'])
        self.assertEqual(first['items'][0]['source'], second['items'][0]['source'])
        self.assertEqual(first['items'][0]['field_sources'], second['items'][0]['field_sources'])
        for text in ('not_a_document_field', 'not_an_item_field', '0.1', '0.2', 'Reviewer', 'Remove invalid metadata'):
            self.assertIn(text, second['source']['excerpt'])
        history = self.store.document_history(case_id, 'doc-1')
        self.assertEqual([1, 2, 3], [version['revision'] for version in history])
        self.assertEqual(0.1, history[0]['document']['fields_confidence']['not_a_document_field'])
        self.assertEqual(0.2, history[1]['document']['items'][0]['confidence']['not_an_item_field'])
        self.assertEqual('Reviewer', history[2]['reviewer'])
        self.assertEqual('Remove invalid metadata', history[2]['comment'])

    def test_discard_rejects_real_fields_and_missing_original_keys_atomically(self):
        original = self.original.model_copy(deep=True)
        original.fields_confidence['not_a_document_field'] = 0.1
        original.items[0].confidence['not_an_item_field'] = 0.2
        case_id = self.store.create_case('Invalid discard')['id']
        self.store.add_document(case_id, original)
        for kwargs in ({'discarded_confidence_fields': ['date']},
                       {'discarded_confidence_fields': ['filename']},
                       {'discarded_item_confidence': {'line-1': ['quantity']}},
                       {'discarded_item_confidence': {'line-1': ['source']}},
                       {'discarded_confidence_fields': ['not_a_document_field', 'not_stored']},
                       {'discarded_item_confidence': {'line-1': ['not_stored']}},
                       {'discarded_item_confidence': {'missing-line': ['not_an_item_field']}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.store.update_document(case_id, 'doc-1', original, 1, 'Reviewer', 'Check', **kwargs)
        history = self.store.document_history(case_id, 'doc-1')
        self.assertEqual(1, len(history))
        self.assertEqual(0.1, history[0]['document']['fields_confidence']['not_a_document_field'])
        self.assertEqual(0.2, history[0]['document']['items'][0]['confidence']['not_an_item_field'])

    def test_new_row_defaults_are_unconfirmed_and_old_row_survives_in_history(self):
        edited = self.original.model_copy(deep=True)
        edited.items = [
            {'line_id': 'manual-1', 'name': 'New', 'quantity': '3',
             'confidence': {'name': 1.0, 'quantity': 1.0, 'discount_percent': 1.0},
             'source': {'document_id': 'other', 'excerpt': 'Untrusted source'}},
        ]
        doc = self.update(edited)['document']
        item = doc['items'][0]
        self.assertEqual('manual-1', item['line_id'])
        for field in ('name', 'quantity', 'discount_percent'):
            self.assertEqual(0, item['confidence'][field])
            self.assertIn('Reviewer', item['field_sources'][field]['excerpt'])
        self.assertEqual(0, doc['fields_confidence']['items'])
        self.assertNotIn('line_id', item['confidence'])
        self.assertNotIn('attributes', item['confidence'])
        self.assertNotIn('amount', item['confidence'])
        self.assertIsNone(item['source'])
        self.assertIn('line-1', doc['source']['excerpt'])
        self.assertIn('manual-1', doc['source']['excerpt'])
        history = self.store.document_history(self.case_id, 'doc-1')
        self.assertEqual('line-1', history[0]['document']['items'][0]['line_id'])
        self.assertEqual('manual-1', history[1]['document']['items'][0]['line_id'])
        restored = Store(self.root).document_history(self.case_id, 'doc-1')
        self.assertEqual(history, restored)

    def test_structure_and_new_values_require_their_own_confirmations(self):
        edited = self.original.model_copy(deep=True)
        edited.items = [*edited.items, {'line_id': 'manual-1', 'name': 'New', 'quantity': '3'}]
        doc = self.update(edited, confirmed_fields=['items'],
                          confirmed_items={'manual-1': ['name', 'quantity']})['document']
        self.assertEqual(1, doc['fields_confidence']['items'])
        self.assertEqual(1, doc['items'][1]['confidence']['name'])
        self.assertEqual(1, doc['items'][1]['confidence']['quantity'])
        self.assertEqual(0, doc['items'][1]['confidence']['discount_percent'])
        self.assertIn('подтверждено', doc['items'][1]['field_sources']['quantity']['excerpt'])
        self.assertIn('items', doc['source']['excerpt'])

    def test_reconfirmation_and_later_edit_have_separate_history_and_evidence(self):
        edited = self.original.model_copy(deep=True)
        edited.base_document_id = 'spec-2'
        edited.acceptance_mode = 'cumulative'
        edited.related_delivery_ids = ['delivery-2']
        edited.expected_pages = 2
        edited.actual_pages = 2
        fields = ['base_document_id', 'acceptance_mode', 'related_delivery_ids', 'expected_pages', 'actual_pages']
        first = self.update(edited)['document']
        self.assertTrue(all(first['fields_confidence'][field] == 0 for field in fields))
        second = self.update(Document.model_validate(first), revision=2, confirmed_fields=fields)['document']
        self.assertTrue(all(second['fields_confidence'][field] == 1 for field in fields))
        third_input = Document.model_validate(second)
        third_input.base_document_id = 'spec-3'
        third = self.update(third_input, revision=3)['document']
        self.assertEqual(0, third['fields_confidence']['base_document_id'])
        self.assertIn('spec-1 → spec-2', third['source']['excerpt'])
        self.assertIn('spec-2 → spec-3', third['source']['excerpt'])
        history = self.store.document_history(self.case_id, 'doc-1')
        self.assertEqual([1, 2, 3, 4], [version['revision'] for version in history])
        self.assertEqual('spec-1', history[0]['document']['base_document_id'])
        self.assertEqual(0, history[1]['document']['fields_confidence']['base_document_id'])
        self.assertEqual(1, history[2]['document']['fields_confidence']['base_document_id'])
        self.assertEqual(0, history[3]['document']['fields_confidence']['base_document_id'])

    def test_invalid_confirmations_and_stale_revision_cannot_change_history(self):
        for kwargs in ({'confirmed_fields': ['not_a_field']},
                       {'confirmed_items': {'missing-line': ['quantity']}},
                       {'confirmed_items': {'line-1': ['source']}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.update(self.original, **kwargs)
        self.assertEqual(1, len(self.store.document_history(self.case_id, 'doc-1')))
        self.update(self.original, confirmed_fields=['date'])
        with self.assertRaises(ConflictError):
            self.update(self.original, revision=1)
        self.assertEqual(2, len(self.store.document_history(self.case_id, 'doc-1')))


class ReviewAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='kontrol-review-api-')
        self.addCleanup(self.directory.cleanup)
        with patch.dict('os.environ', {'KONTROL_API_TOKEN': '', 'KONTROL_CORS_ORIGINS': ''}):
            self.app = create_app(Path(self.directory.name))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.case_id = self.client.post('/api/cases', json={'title': 'Review API'}).json()['id']

    def test_unknown_confirmation_fields_and_line_ids_are_422_without_a_version(self):
        original = example_document().model_dump(mode='json')
        create = self.client.post(f'/api/cases/{self.case_id}/structured', json=original)
        self.assertEqual(201, create.status_code, create.text)
        path = f'/api/cases/{self.case_id}/documents/doc-1'
        body = {'document': original, 'expected_revision': 1, 'reviewer': 'Reviewer', 'comment': 'Check'}
        for payload in ({'confirmed_fields': ['base_document_typo']},
                        {'confirmed_items': {'missing-line': ['quantity']}},
                        {'confirmed_items': {'line-1': ['confidence']}}):
            with self.subTest(payload=payload):
                response = self.client.put(path, json={**body, **payload})
                self.assertEqual(422, response.status_code, response.text)
                self.assertEqual(1, len(self.client.get(path + '/history').json()))

    def test_discard_validates_keys_against_model_and_stored_revision(self):
        original = example_document().model_dump(mode='json')
        original['fields_confidence']['bad_document_key'] = 0.1
        original['items'][0]['confidence']['bad_item_key'] = 0.2
        created = self.client.post(f'/api/cases/{self.case_id}/structured', json=original)
        self.assertEqual(201, created.status_code, created.text)
        path = f'/api/cases/{self.case_id}/documents/doc-1'
        body = {'document': copy.deepcopy(original), 'expected_revision': 1,
                'reviewer': 'Reviewer', 'comment': 'Remove invalid metadata'}
        del body['document']['fields_confidence']['bad_document_key']
        del body['document']['items'][0]['confidence']['bad_item_key']
        for selection in (
            {'discarded_confidence_fields': ['date']},
            {'discarded_confidence_fields': ['content_hash']},
            {'discarded_item_confidence': {'line-1': ['source']}},
            {'discarded_confidence_fields': ['bad_document_key', 'never_stored']},
            {'discarded_item_confidence': {'line-1': ['never_stored']}},
            {'discarded_item_confidence': {'missing-line': ['bad_item_key']}},
        ):
            with self.subTest(selection=selection):
                response = self.client.put(path, json={**body, **selection})
                self.assertEqual(422, response.status_code, response.text)
                self.assertEqual(1, len(self.client.get(path + '/history').json()))
        selection = {'discarded_confidence_fields': ['bad_document_key'],
                     'discarded_item_confidence': {'line-1': ['bad_item_key']}}
        response = self.client.put(path, json={**body, **selection})
        self.assertEqual(200, response.status_code, response.text)
        doc = response.json()['document']
        self.assertNotIn('bad_document_key', doc['fields_confidence'])
        self.assertNotIn('bad_item_key', doc['items'][0]['confidence'])
        self.assertEqual(0.4, doc['fields_confidence']['date'])
        self.assertEqual(0.3, doc['items'][0]['confidence']['unit_price'])
        self.assertIn('bad_document_key', doc['source']['excerpt'])
        self.assertIn('bad_item_key', doc['source']['excerpt'])
        history = self.client.get(path + '/history').json()
        self.assertEqual(0.1, history[0]['document']['fields_confidence']['bad_document_key'])
        self.assertEqual(0.2, history[0]['document']['items'][0]['confidence']['bad_item_key'])
        self.assertEqual(409, self.client.put(path, json={**body, **selection}).status_code)

    def test_discard_removes_only_spurious_blocks_not_real_uncertainty(self):
        case = next(c for c in load_cases() if c['id'] == 'T01')
        request = copy.deepcopy(case['request'])
        document = request['documents'][1]
        document.setdefault('fields_confidence', {})['bad_document_key'] = 0.1
        item = document['items'][0]
        item.setdefault('confidence', {}).update({'bad_item_key': 0.2, 'quantity': 0.4})
        for entry in request['documents']:
            response = self.client.post(f'/api/cases/{self.case_id}/structured', json=entry)
            self.assertEqual(201, response.status_code, response.text)
        options = {key: value for key, value in request.items() if key != 'documents'}
        before = self.client.post(f'/api/cases/{self.case_id}/runs', json=options)
        self.assertEqual(201, before.status_code, before.text)
        self.assertIn('LOW_CONFIDENCE', {signal['code'] for signal in before.json()['result']['signals']})
        line_id = item['line_id']
        path = f'/api/cases/{self.case_id}/documents/{document["id"]}'
        discarded = self.client.put(path, json={
            'document': document, 'expected_revision': 1, 'reviewer': 'Reviewer', 'comment': 'Remove invalid keys',
            'discarded_confidence_fields': ['bad_document_key'],
            'discarded_item_confidence': {line_id: ['bad_item_key']},
        })
        self.assertEqual(200, discarded.status_code, discarded.text)
        self.assertEqual(0.4, discarded.json()['document']['items'][0]['confidence']['quantity'])
        still_blocked = self.client.post(f'/api/cases/{self.case_id}/runs', json=options)
        self.assertEqual(201, still_blocked.status_code, still_blocked.text)
        self.assertNotEqual('ok', still_blocked.json()['result']['status'])
        self.assertIn('LOW_CONFIDENCE', {signal['code'] for signal in still_blocked.json()['result']['signals']})
        confirmed = self.client.put(path, json={
            'document': discarded.json()['document'], 'expected_revision': 2,
            'reviewer': 'Reviewer', 'comment': 'Confirm actual quantity',
            'confirmed_items': {line_id: ['quantity']},
        })
        self.assertEqual(200, confirmed.status_code, confirmed.text)
        final = self.client.post(f'/api/cases/{self.case_id}/runs', json=options)
        self.assertEqual(201, final.status_code, final.text)
        self.assertEqual('ok', final.json()['result']['status'])

    def test_edit_preserves_uploaded_original_file_and_its_sources(self):
        original = example_document().model_dump(mode='json')
        payload = json.dumps(original, ensure_ascii=False).encode('utf-8')
        uploaded = self.client.post(f'/api/cases/{self.case_id}/documents',
                                    files={'file': ('evidence.json', payload, 'application/json')})
        self.assertEqual(201, uploaded.status_code, uploaded.text)
        record = uploaded.json()
        path = f'/api/cases/{self.case_id}/documents/{record["id"]}'
        edited = copy.deepcopy(record['document'])
        edited['items'][0]['quantity'] = '10'
        updated = self.client.put(path, json={
            'document': edited, 'expected_revision': 1, 'reviewer': 'Reviewer', 'comment': 'Checked source',
            'confirmed_items': {'line-1': ['quantity']},
        })
        self.assertEqual(200, updated.status_code, updated.text)
        self.assertEqual(payload, self.client.get(path + '/file').content)
        current = updated.json()['document']
        self.assertEqual(record['document']['content_hash'], current['content_hash'])
        self.assertEqual('evidence.json', current['filename'])
        self.assertEqual(record['document']['source'], current['source'])
        self.assertIn('Original quantity is 8', current['items'][0]['field_sources']['quantity']['excerpt'])
        history = self.client.get(path + '/history').json()
        self.assertEqual(record['document'], history[0]['document'])

    def test_confirming_unchanged_quantity_is_visible_in_report_without_false_edit(self):
        request = copy.deepcopy(next(c for c in load_cases() if c['id'] == 'T02')['request'])
        for document in request['documents']:
            created = self.client.post(f'/api/cases/{self.case_id}/structured', json=document)
            self.assertEqual(201, created.status_code, created.text)
        document = request['documents'][1]
        item = document['items'][0]
        path = f'/api/cases/{self.case_id}/documents/{document["id"]}'
        updated = self.client.put(path, json={
            'document': document, 'expected_revision': 1, 'reviewer': 'Эксперт',
            'comment': 'Сверено с оригиналом', 'confirmed_items': {item['line_id']: ['quantity']},
        })
        self.assertEqual(200, updated.status_code, updated.text)
        stored_item = updated.json()['document']['items'][0]
        excerpt = stored_item['field_sources']['quantity']['excerpt']
        self.assertEqual('8', stored_item['quantity'])
        self.assertEqual(1, stored_item['confidence']['quantity'])
        self.assertIn(item['field_sources']['quantity']['excerpt'], excerpt)
        self.assertIn('Подтверждение (Эксперт): quantity: 8', excerpt)
        self.assertIn('Сверено с оригиналом', excerpt)
        self.assertNotIn('Ручное изменение', excerpt)
        history = self.client.get(path + '/history').json()
        self.assertEqual([1, 2], [version['revision'] for version in history])
        self.assertEqual('8', history[0]['document']['items'][0]['quantity'])
        self.assertEqual(('Эксперт', 'Сверено с оригиналом'), (history[1]['reviewer'], history[1]['comment']))

        options = {key: value for key, value in request.items() if key != 'documents'}
        checked = self.client.post(f'/api/cases/{self.case_id}/runs', json=options)
        self.assertEqual(201, checked.status_code, checked.text)
        run = checked.json()
        signal = next(signal for signal in run['result']['signals'] if signal['code'] == 'QUANTITY_MISMATCH')
        self.assertTrue(any(source['excerpt'] == excerpt for source in signal['sources']))
        report_path = f'/api/runs/{run["id"]}/export'
        html_report = self.client.get(report_path, params={'format': 'html'})
        self.assertEqual(200, html_report.status_code, html_report.text)
        pdf_report = self.client.get(report_path, params={'format': 'pdf'})
        self.assertEqual(200, pdf_report.status_code, pdf_report.text)
        with pdfplumber.open(io.BytesIO(pdf_report.content)) as pdf:
            pdf_text = '\n'.join(page.extract_text() or '' for page in pdf.pages)
        for report in (html_report.text, pdf_text):
            self.assertIn('Подтверждение (Эксперт): quantity: 8', report)
            self.assertIn('Сверено с оригиналом', report)
            self.assertNotIn('Ручное изменение', report)

    def test_unconfirmed_edit_blocks_check_until_explicit_confirmation(self):
        case = next(c for c in load_cases() if c['id'] == 'T02')
        request = copy.deepcopy(case['request'])
        for document in request['documents']:
            response = self.client.post(f'/api/cases/{self.case_id}/structured', json=document)
            self.assertEqual(201, response.status_code, response.text)
        document = request['documents'][1]
        line_id = document['items'][0]['line_id']
        document['items'][0]['quantity'] = '10'
        path = f'/api/cases/{self.case_id}/documents/{document["id"]}'
        body = {'document': document, 'expected_revision': 1,
                'reviewer': 'Reviewer', 'comment': 'Correct quantity'}
        first = self.client.put(path, json=body)
        self.assertEqual(200, first.status_code, first.text)
        self.assertEqual(0, first.json()['document']['items'][0]['confidence']['quantity'])
        options = {key: value for key, value in request.items() if key != 'documents'}
        blocked = self.client.post(f'/api/cases/{self.case_id}/runs', json=options)
        self.assertEqual(201, blocked.status_code, blocked.text)
        self.assertNotEqual('ok', blocked.json()['result']['status'])
        self.assertIn('LOW_CONFIDENCE', {s['code'] for s in blocked.json()['result']['signals']})
        confirmed = self.client.put(path, json={
            'document': first.json()['document'], 'expected_revision': 2,
            'reviewer': 'Reviewer', 'comment': 'Confirmed against the original',
            'confirmed_items': {line_id: ['quantity']},
        })
        self.assertEqual(200, confirmed.status_code, confirmed.text)
        self.assertEqual(1, confirmed.json()['document']['items'][0]['confidence']['quantity'])
        excerpt = confirmed.json()['document']['items'][0]['field_sources']['quantity']['excerpt']
        self.assertIn('Ручное изменение (Reviewer): quantity: 8 → 10 (не подтверждено)', excerpt)
        self.assertIn('Подтверждение (Reviewer): quantity: 10', excerpt)
        fresh = self.client.post(f'/api/cases/{self.case_id}/runs', json=options)
        self.assertEqual(201, fresh.status_code, fresh.text)
        self.assertEqual('ok', fresh.json()['result']['status'])
        self.assertTrue(self.client.get(f'/api/runs/{blocked.json()["id"]}').json()['stale'])
        history = self.client.get(path + '/history').json()
        self.assertEqual([1, 2, 3], [entry['revision'] for entry in history])
        self.assertEqual('8', history[0]['document']['items'][0]['quantity'])
        self.assertEqual(0, history[1]['document']['items'][0]['confidence']['quantity'])
        self.assertEqual(1, history[2]['document']['items'][0]['confidence']['quantity'])


if __name__ == '__main__':
    unittest.main()
