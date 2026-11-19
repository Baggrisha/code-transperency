"""Интеграционные проверки backend-зоны: CORS, журнал, перенос данных, PDF, фильтры истории."""
from __future__ import annotations

import copy
from contextlib import closing
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pdfplumber
from fastapi.testclient import TestClient

from kontrol.api import create_app
from tests.oracle import load_cases

ROOT = Path(__file__).resolve().parent.parent
CASES = {case['id']: case for case in load_cases()}
EXAMPLES = ROOT / 'examples'


def make_client(root: Path, **env) -> TestClient:
    """Создаёт приложение с чистыми настройками: без токена и с CORS по умолчанию, если не указано иное."""
    saved = os.environ.copy()
    try:
        os.environ.pop('KONTROL_CORS_ORIGINS', None)
        os.environ['KONTROL_API_TOKEN'] = ''
        os.environ.update(env)
        app = create_app(root)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    return TestClient(app)


class BackendIntegration(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='kontrol-int-')
        self.root = Path(self.directory.name) / 'data'
        self.client = make_client(self.root)

    def tearDown(self):
        self.client.close()
        self.directory.cleanup()

    def load_fixture(self, fixture='T02'):
        source = copy.deepcopy(CASES[fixture]['request'])
        case_id = self.client.post('/api/cases', json={'title': f'Дело {fixture}'}).json()['id']
        for document in source['documents']:
            self.assertEqual(201, self.client.post(f'/api/cases/{case_id}/structured', json=document).status_code)
        options = {k: v for k, v in source.items() if k != 'documents'}
        return case_id, options

    def run_case(self, case_id, options):
        response = self.client.post(f'/api/cases/{case_id}/runs', json=options)
        self.assertEqual(201, response.status_code, response.text)
        return response.json()

    def upload(self, case_id, name):
        with (EXAMPLES / name).open('rb') as handle:
            return self.client.post(f'/api/cases/{case_id}/documents', files={'file': (name, handle)})

    # --- полный сценарий с реальными файлами -------------------------------------------------
    def test_full_file_scenario_survives_restart(self):
        case_id = self.client.post('/api/cases', json={'title': 'Поставка мебели'}).json()['id']
        for name in ('contract.txt', 'delivery_8.csv', 'delivery_2.csv', 'acceptance.txt'):
            self.assertEqual(201, self.upload(case_id, name).status_code)
        again = self.upload(case_id, 'delivery_8.csv').json()
        self.assertTrue(again['duplicate'])
        run = self.run_case(case_id, {'as_of': '2026-10-31'})
        self.assertEqual('ok', run['result']['status'])
        delivered = {p['delivered_quantity'] for p in run['result']['positions']}
        self.assertEqual({'10'}, delivered)

        self.client.close()
        self.client = make_client(self.root)  # «перезапуск» сервера на той же базе
        case = self.client.get(f'/api/cases/{case_id}').json()
        self.assertEqual(4, len(case['documents']))
        self.assertEqual(run['id'], case['runs'][0]['id'])
        self.assertEqual('ok', case['runs'][0]['status'])
        for record in case['documents']:
            response = self.client.get(f'/api/cases/{case_id}/documents/{record["id"]}/file')
            self.assertEqual(200, response.status_code)
        actions = [e['action'] for e in self.client.get(f'/api/cases/{case_id}/events').json()]
        self.assertEqual(['case_created'] + ['document_added'] * 4 + ['duplicate_upload', 'check_completed'], actions)

    def test_source_files_survive_moving_data_directory(self):
        case_id = self.client.post('/api/cases', json={'title': 'Перенос'}).json()['id']
        document_id = self.upload(case_id, 'delivery_8.csv').json()['id']
        self.client.close()
        moved = Path(self.directory.name) / 'moved-data'
        shutil.move(str(self.root), moved)
        self.client = make_client(moved)
        response = self.client.get(f'/api/cases/{case_id}/documents/{document_id}/file')
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual((EXAMPLES / 'delivery_8.csv').read_bytes(), response.content)

    def test_legacy_absolute_paths_still_resolve_after_move(self):
        case_id = self.client.post('/api/cases', json={'title': 'Старая база'}).json()['id']
        document_id = self.upload(case_id, 'delivery_8.csv').json()['id']
        db = self.root / 'kontrol.sqlite3'
        with closing(sqlite3.connect(db)) as conn, conn:  # имитируем запись старой версии с абсолютным путём
            (relative,) = conn.execute('SELECT file_path FROM documents').fetchone()
            conn.execute('UPDATE documents SET file_path=?', (str(Path('/old/location') / relative),))
        response = self.client.get(f'/api/cases/{case_id}/documents/{document_id}/file')
        self.assertEqual(200, response.status_code)

    # --- журнал проверок и сводки ------------------------------------------------------------
    def test_run_journal_and_case_summaries(self):
        t02, options02 = self.load_fixture('T02')
        t03, options03 = self.load_fixture('T03')
        first = self.run_case(t02, options02)
        self.run_case(t03, options03)
        journal = self.client.get('/api/runs').json()
        self.assertEqual(2, journal['total'])
        self.assertEqual({'needs_review', 'ok'}, {r['status'] for r in journal['items']})
        only = self.client.get('/api/runs', params={'case_id': t02}).json()
        self.assertEqual([first['id']], [r['id'] for r in only['items']])
        self.assertEqual(len(first['result']['signals']), only['items'][0]['signal_count'])
        self.assertEqual('Дело T02', only['items'][0]['case_title'])
        self.assertEqual(1, self.client.get('/api/runs', params={'status': 'ok'}).json()['total'])
        self.assertEqual(404, self.client.get('/api/runs', params={'case_id': 'missing'}).status_code)
        self.assertEqual(422, self.client.get('/api/runs', params={'status': 'bad'}).status_code)

        cases = {c['id']: c for c in self.client.get('/api/cases').json()}
        self.assertEqual('needs_review', cases[t02]['last_run_status'])
        self.assertEqual(1, cases[t02]['run_count'])
        self.assertFalse(cases[t02]['last_run_stale'])

    def test_prior_decisions_are_offered_for_identical_signals_only(self):
        case_id, options = self.load_fixture('T02')
        first = self.run_case(case_id, options)
        signal = first['result']['signals'][0]['id']
        review = {'status': 'confirmed', 'reviewer': 'Маша', 'comment': 'Проверено по оригиналу'}
        self.assertEqual(201, self.client.post(f'/api/runs/{first["id"]}/signals/{signal}/reviews', json=review).status_code)
        current = self.client.get(f'/api/runs/{first["id"]}').json()
        self.assertEqual('confirmed', current['latest_reviews'][signal]['status'])
        self.assertEqual({}, current['prior_reviews'])

        second = self.run_case(case_id, options)
        self.assertEqual('confirmed', second['prior_reviews'][signal]['status'])
        loaded = self.client.get(f'/api/runs/{second["id"]}').json()
        self.assertEqual([], loaded['reviews'])  # новый запуск не получает чужих решений автоматически
        self.assertEqual('confirmed', loaded['prior_reviews'][signal]['status'])
        self.assertEqual(first['id'], loaded['prior_reviews'][signal]['run_id'])

    # --- экспорт -------------------------------------------------------------------------------
    def test_pdf_export_contains_cyrillic_text_and_reviews(self):
        case_id, options = self.load_fixture('T02')
        run = self.run_case(case_id, options)
        signal = run['result']['signals'][0]['id']
        self.client.post(f'/api/runs/{run["id"]}/signals/{signal}/reviews',
                         json={'status': 'resolved', 'reviewer': 'Маша', 'comment': 'Довезли две единицы'})
        response = self.client.get(f'/api/runs/{run["id"]}/export', params={'format': 'pdf'})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual('application/pdf', response.headers['content-type'])
        self.assertIn(f'check-{run["id"]}.pdf', response.headers['content-disposition'])
        with pdfplumber.open(io.BytesIO(response.content)) as pdf:
            text = '\n'.join(page.extract_text() or '' for page in pdf.pages)
        for expected in ('Контроль исполнения', 'Дело T02', 'Нужна проверка', 'Устранено',
                         'Довезли две единицы', run['result']['signals'][0]['title']):
            self.assertIn(expected, text)
        exports = self.client.get(f'/api/cases/{case_id}/events', params={'action': 'report_exported'}).json()
        self.assertEqual([{'run_id': run['id'], 'format': 'pdf'}], [e['payload'] for e in exports])

    def test_all_export_formats_succeed_for_clean_run(self):
        case_id, options = self.load_fixture('T03')
        run = self.run_case(case_id, options)
        for fmt, mime in (('json', 'application/json'), ('csv', 'text/csv'), ('html', 'text/html'), ('pdf', 'application/pdf')):
            response = self.client.get(f'/api/runs/{run["id"]}/export', params={'format': fmt})
            self.assertEqual(200, response.status_code, fmt)
            self.assertTrue(response.headers['content-type'].startswith(mime), fmt)
        self.assertEqual(422, self.client.get(f'/api/runs/{run["id"]}/export', params={'format': 'xlsx'}).status_code)

    # --- история ---------------------------------------------------------------------------
    def test_event_filters(self):
        case_id, options = self.load_fixture('T02')
        run = self.run_case(case_id, options)
        all_events = self.client.get(f'/api/cases/{case_id}/events').json()
        checks = self.client.get(f'/api/cases/{case_id}/events', params={'action': 'check_completed'}).json()
        self.assertEqual([run['id']], [e['payload']['run_id'] for e in checks])
        several = self.client.get(f'/api/cases/{case_id}/events',
                                  params=[('action', 'case_created'), ('action', 'check_completed')]).json()
        self.assertEqual(2, len(several))
        tail = self.client.get(f'/api/cases/{case_id}/events',
                               params={'after_sequence': all_events[-2]['sequence']}).json()
        self.assertEqual([all_events[-1]['sequence']], [e['sequence'] for e in tail])
        self.assertEqual(2, len(self.client.get(f'/api/cases/{case_id}/events', params={'limit': 2}).json()))

    # --- ошибки ----------------------------------------------------------------------------
    def test_not_found_names_the_missing_object(self):
        response = self.client.get('/api/runs/abc123')
        self.assertEqual(404, response.status_code)
        self.assertIn('abc123', response.json()['detail'])


class CorsBehaviour(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='kontrol-cors-')
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def preflight(self, client, origin):
        return client.options('/api/cases', headers={'Origin': origin, 'Access-Control-Request-Method': 'POST',
                                                     'Access-Control-Request-Headers': 'content-type,authorization'})

    def test_local_frontends_allowed_by_default(self):
        with make_client(self.root) as client:
            for origin in ('http://localhost:5173', 'http://127.0.0.1:3000', 'http://localhost:8080'):
                response = self.preflight(client, origin)
                self.assertEqual(200, response.status_code, origin)
                self.assertEqual(origin, response.headers['access-control-allow-origin'])
            remote = self.preflight(client, 'https://evil.example')
            self.assertNotIn('access-control-allow-origin', remote.headers)
            download = client.get('/api/cases', headers={'Origin': 'http://localhost:5173'})
            self.assertIn('Content-Disposition', download.headers['access-control-expose-headers'])

    def test_explicit_origin_list_replaces_default(self):
        with make_client(self.root, KONTROL_CORS_ORIGINS='https://demo.team.local') as client:
            self.assertEqual('https://demo.team.local',
                             self.preflight(client, 'https://demo.team.local').headers['access-control-allow-origin'])
            self.assertNotIn('access-control-allow-origin', self.preflight(client, 'http://localhost:5173').headers)

    def test_preflight_and_401_work_with_token(self):
        with make_client(self.root, KONTROL_API_TOKEN='secret') as client:
            self.assertEqual(200, self.preflight(client, 'http://localhost:5173').status_code)
            denied = client.get('/api/cases', headers={'Origin': 'http://localhost:5173'})
            self.assertEqual(401, denied.status_code)
            self.assertEqual('http://localhost:5173', denied.headers['access-control-allow-origin'])
            allowed = client.get('/api/cases', headers={'Origin': 'http://localhost:5173',
                                                        'Authorization': 'Bearer secret'})
            self.assertEqual(200, allowed.status_code)


class OpenApiContract(unittest.TestCase):
    def test_published_schema_matches_application(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Path(directory))
            published = json.loads((ROOT / 'docs' / 'openapi.json').read_text(encoding='utf-8'))
            self.assertEqual(app.openapi(), published,
                             'Обновите docs/openapi.json: python scripts/export_openapi.py')


if __name__ == '__main__':
    unittest.main()
