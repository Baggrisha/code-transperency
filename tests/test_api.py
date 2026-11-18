"""Independent end-to-end HTTP checks with a disposable SQLite database."""
from __future__ import annotations
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from kontrol.api import create_app
from kontrol.storage import Store
from tests.oracle import load_cases

CASES = {case['id']: case for case in load_cases()}


class APIScenarios(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='kontrol-api-test-')
        self.root = Path(self.directory.name)
        with patch.dict('os.environ', {'KONTROL_API_TOKEN': '', 'KONTROL_CORS_ORIGINS': ''}):
            self.app = create_app(self.root)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.directory.cleanup()

    def create_case(self):
        response = self.client.post('/api/cases', json={'title': 'Синтетический проверочный комплект'})
        self.assertEqual(201, response.status_code, response.text)
        return response.json()['id']

    def load_case(self, fixture='T02'):
        source = copy.deepcopy(CASES[fixture]['request'])
        case_id = self.create_case()
        for document in source['documents']:
            response = self.client.post(f'/api/cases/{case_id}/structured', json=document)
            self.assertEqual(201, response.status_code, response.text)
        options = {k: v for k, v in source.items() if k != 'documents'}
        return case_id, source, options

    def run_case(self, case_id, options):
        response = self.client.post(f'/api/cases/{case_id}/runs', json=options)
        self.assertEqual(201, response.status_code, response.text)
        return response.json()

    def test_upload_duplicate_preserves_count_and_original_bytes(self):
        case_id = self.create_case()
        document = CASES['T01']['request']['documents'][1]
        payload = json.dumps(document, ensure_ascii=False).encode('utf-8')
        endpoint = f'/api/cases/{case_id}/documents'
        first = self.client.post(endpoint, files={'file': ('delivery.json', payload, 'application/json')})
        self.assertEqual(201, first.status_code, first.text)
        self.assertFalse(first.json()['duplicate'])
        second = self.client.post(endpoint, files={'file': ('copy.json', payload, 'application/json')})
        self.assertEqual(201, second.status_code, second.text)
        self.assertTrue(second.json()['duplicate'])
        case = self.client.get(f'/api/cases/{case_id}').json()
        self.assertEqual(1, len(case['documents']))
        self.assertEqual(1, case['revision'])
        self.assertEqual(1, len(list((self.root / 'files').iterdir())))
        original = self.client.get(f'/api/cases/{case_id}/documents/{first.json()["id"]}/file')
        self.assertEqual(payload, original.content)
        events = self.client.get(f'/api/cases/{case_id}/events').json()
        self.assertIn('duplicate_upload', {entry['action'] for entry in events})

    def test_correction_is_versioned_and_old_run_becomes_stale(self):
        case_id, source, options = self.load_case()
        original_run = self.run_case(case_id, options)
        self.assertEqual('needs_review', original_run['result']['status'])
        original_snapshot = copy.deepcopy(original_run['result'])
        corrected = copy.deepcopy(source['documents'][1])
        corrected['items'][0]['quantity'] = '10'
        endpoint = f'/api/cases/{case_id}/documents/{corrected["id"]}'
        body = {'document': corrected, 'expected_revision': 1, 'reviewer': 'Тестовый специалист', 'comment': 'Проверено исходное количество: 10.'}
        update = self.client.put(endpoint, json=body)
        self.assertEqual(200, update.status_code, update.text)
        self.assertEqual(2, update.json()['revision'])
        conflict = self.client.put(endpoint, json=body)
        self.assertEqual(409, conflict.status_code)
        history = self.client.get(endpoint + '/history').json()
        self.assertEqual([1, 2], [version['revision'] for version in history])
        self.assertEqual('8', history[0]['document']['items'][0]['quantity'])
        self.assertEqual('10', history[1]['document']['items'][0]['quantity'])
        stored = self.client.get(f'/api/runs/{original_run["id"]}').json()
        self.assertTrue(stored['stale'])
        self.assertEqual(original_snapshot, stored['result'])
        fresh = self.run_case(case_id, options)
        self.assertEqual('ok', fresh['result']['status'])
        self.assertNotEqual(original_snapshot['input_hash'], fresh['result']['input_hash'])
        self.assertFalse(self.client.get(f'/api/runs/{fresh["id"]}').json()['stale'])

    def test_review_and_exports_preserve_immutable_machine_result(self):
        case_id, source, options = self.load_case()
        run = self.run_case(case_id, options)
        signal = next(s for s in run['result']['signals'] if s['code'] == 'QUANTITY_MISMATCH')
        path = f'/api/runs/{run["id"]}'
        comment = '<script>alert("test")</script> Запрошена дополнительная накладная.'
        response = self.client.post(path + f'/signals/{signal["id"]}/reviews', json={
            'status': 'resolved', 'reviewer': 'Эксперт', 'comment': comment})
        self.assertEqual(201, response.status_code, response.text)
        stored = self.client.get(path).json()
        self.assertEqual(run['result'], stored['result'])
        self.assertEqual('needs_review', stored['result']['signals'][0]['status'])
        self.assertEqual('resolved', stored['reviews'][-1]['status'])
        exported = self.client.get(path + '/export?format=json')
        self.assertEqual(200, exported.status_code)
        self.assertEqual(run['result'], exported.json()['result'])
        self.assertEqual(comment, exported.json()['reviews'][-1]['comment'])
        csv = self.client.get(path + '/export?format=csv')
        self.assertEqual(200, csv.status_code)
        self.assertIn('QUANTITY_MISMATCH', csv.text)
        self.assertIn('resolved', csv.text)
        html = self.client.get(path + '/export?format=html')
        self.assertEqual(200, html.status_code)
        self.assertNotIn('<script>', html.text)
        self.assertIn('&lt;script&gt;', html.text)
        self.assertIn('delivery-1', html.text)
        self.assertIn(run['result']['result_hash'], html.text)

    def test_csv_prevents_formula_interpretation_of_review_comment(self):
        case_id, source, options = self.load_case()
        run = self.run_case(case_id, options)
        signal_id = run['result']['signals'][0]['id']
        endpoint = f'/api/runs/{run["id"]}'
        review = self.client.post(endpoint + f'/signals/{signal_id}/reviews', json={
            'status': 'confirmed', 'reviewer': 'Эксперт', 'comment': '=1+1'})
        self.assertEqual(201, review.status_code)
        export = self.client.get(endpoint + '/export?format=csv')
        self.assertIn("'=1+1", export.text)

    def test_invalid_upload_does_not_leave_documents_or_files(self):
        case_id = self.create_case()
        endpoint = f'/api/cases/{case_id}/documents'
        for filename, data, status in [('empty.json', b'', 422), ('broken.json', b'{unclosed', 422), ('payload.exe', b'anything', 415)]:
            with self.subTest(filename=filename):
                response = self.client.post(endpoint, files={'file': (filename, data)})
                self.assertEqual(status, response.status_code, response.text)
        self.assertEqual([], self.client.get(f'/api/cases/{case_id}').json()['documents'])
        self.assertEqual([], list((self.root / 'files').iterdir()))

    def test_metadata_validation_is_controlled(self):
        case_id = self.create_case()
        payload = json.dumps(CASES['T01']['request']['documents'][0]).encode()
        response = self.client.post(f'/api/cases/{case_id}/documents',
                                    files={'file': ('contract.json', payload)}, data={'metadata': '[1, 2]'})
        self.assertEqual(422, response.status_code)
        self.assertEqual([], list((self.root / 'files').iterdir()))

    def test_missing_objects_and_invalid_input_are_controlled(self):
        self.assertEqual(404, self.client.get('/api/cases/does-not-exist').status_code)
        self.assertEqual(404, self.client.get('/api/runs/does-not-exist').status_code)
        self.assertEqual(422, self.client.post('/api/check', json={'documents': []}).status_code)
        case_id = self.create_case()
        self.assertEqual(422, self.client.post(f'/api/cases/{case_id}/runs', json={'as_of': '2026-10-31'}).status_code)

    def test_store_survives_reinitialization(self):
        case_id, source, options = self.load_case()
        run = self.run_case(case_id, options)
        recovered = Store(self.root)
        self.assertEqual(3, len(recovered.case(case_id)['documents']))
        self.assertEqual(run['result'], recovered.run(run['id'])['result'])
        self.assertGreaterEqual(len(recovered.events(case_id)), 5)

    def test_demo_run_agrees_with_stateless_check(self):
        response = self.client.post('/api/demo/T03')
        self.assertEqual(201, response.status_code, response.text)
        imported = response.json()
        run = self.run_case(imported['case']['id'], imported['options'])
        pure = self.client.post('/api/check', json=CASES['T03']['request'])
        self.assertEqual(200, pure.status_code)
        self.assertEqual(pure.json(), run['result'])
        self.assertEqual(12, len(self.client.get('/api/demo').json()))

    def test_stale_html_export_is_visibly_marked(self):
        case_id, source, options = self.load_case()
        run = self.run_case(case_id, options)
        corrected = copy.deepcopy(source['documents'][1])
        corrected['items'][0]['quantity'] = '10'
        self.client.put(f'/api/cases/{case_id}/documents/{corrected["id"]}', json={
            'document': corrected, 'expected_revision': 1, 'reviewer': 'Эксперт', 'comment': 'Проверено количество.'})
        html = self.client.get(f'/api/runs/{run["id"]}/export?format=html')
        self.assertEqual(200, html.status_code)
        self.assertTrue('устар' in html.text.lower() or 'документы изменены после' in html.text.lower(), 'Old report must visibly identify an outdated snapshot.')

    def test_optional_token_protects_api(self):
        with patch.dict('os.environ', {'KONTROL_API_TOKEN': 'synthetic-test-token'}):
            app = create_app(self.root / 'protected')
        with TestClient(app) as client:
            self.assertEqual(200, client.get('/health').status_code)
            self.assertEqual(401, client.get('/api/cases').status_code)
            self.assertEqual(401, client.get('/api/cases', headers={'Authorization': 'Bearer wrong'}).status_code)
            self.assertEqual(200, client.get('/api/cases', headers={'Authorization': 'Bearer synthetic-test-token'}).status_code)


if __name__ == '__main__':
    unittest.main(verbosity=2)
