"""Registry summaries, independent card revisions, legacy migration and UI assets."""
from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from kontrol.api import create_app
from kontrol.storage import Store
from tests.oracle import load_cases

CASES = {case["id"]: case for case in load_cases()}


class CaseMetadataTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="kontrol-card-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        with patch.dict("os.environ", {"KONTROL_API_TOKEN": "", "KONTROL_CORS_ORIGINS": ""}):
            self.app = create_app(self.root / "data")
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def fixture(self):
        request = copy.deepcopy(CASES["T02"]["request"])
        case = self.client.post("/api/cases", json={"title": "Поставка"}).json()
        for document in request.pop("documents"):
            response = self.client.post(f'/api/cases/{case["id"]}/structured', json=document)
            self.assertEqual(201, response.status_code, response.text)
        response = self.client.post(f'/api/cases/{case["id"]}/runs', json=request)
        self.assertEqual(201, response.status_code, response.text)
        return case, request, response.json()

    def summary(self, case_id):
        response = self.client.get("/api/cases")
        self.assertEqual(200, response.status_code, response.text)
        return next(case for case in response.json() if case["id"] == case_id)

    def test_optional_metadata_and_empty_registry_summary(self):
        response = self.client.post("/api/cases", json={
            "title": "  Поставка мебели  ", "description": "  Для парка  ", "contract_number": "  ГК-0142  "})
        self.assertEqual(201, response.status_code, response.text)
        created = response.json()
        self.assertEqual("Поставка мебели", created["title"])
        self.assertEqual("Для парка", created["description"])
        self.assertEqual("ГК-0142", created["contract_number"])
        self.assertEqual(0, created["metadata_revision"])
        case = self.summary(created["id"])
        self.assertEqual((0, 0, 0), tuple(case[key] for key in
                         ("signal_count", "reviewed_signal_count", "pending_signal_count")))
        self.assertIsNone(case["last_run_stale"])
        self.assertGreaterEqual(case["updated_at"], case["created_at"])
        self.assertEqual(422, self.client.post("/api/cases", json={"title": "  "}).status_code)

    def test_card_edit_is_versioned_without_changing_documents_or_run(self):
        original, _, run = self.fixture()
        endpoint = f'/api/cases/{original["id"]}'
        before = self.client.get(endpoint).json()
        payload = {"title": "Новое название", "description": "Примечание", "contract_number": "КАРТОЧКА-42",
                   "expected_metadata_revision": 0}
        response = self.client.put(endpoint, json=payload)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(1, response.json()["metadata_revision"])
        after = self.client.get(endpoint).json()
        self.assertEqual(before["revision"], after["revision"])
        self.assertEqual(before["documents"], after["documents"])
        stored = self.client.get(f'/api/runs/{run["id"]}').json()
        self.assertFalse(stored["stale"])
        self.assertEqual(run["result"], stored["result"])
        self.assertEqual(run["request"], stored["request"])
        self.assertEqual("КАРТОЧКА-42", self.summary(original["id"])["contract_number"])
        event = self.client.get(endpoint + "/events").json()[-1]
        self.assertEqual("case_updated", event["action"])
        self.assertEqual({"before": "Поставка", "after": "Новое название"}, event["payload"]["changes"]["title"])
        self.assertEqual(1, event["payload"]["metadata_revision"])
        self.assertEqual(event["created_at"], after["updated_at"])

    def test_stale_metadata_edit_is_rejected_and_noop_is_not_a_new_revision(self):
        created = self.client.post("/api/cases", json={"title": "Один"}).json()
        endpoint = f'/api/cases/{created["id"]}'
        payload = {"title": "Два", "description": "", "contract_number": "", "expected_metadata_revision": 0}
        self.assertEqual(200, self.client.put(endpoint, json=payload).status_code)
        self.assertEqual(409, self.client.put(endpoint, json={**payload, "title": "Три"}).status_code)
        self.assertEqual("Два", self.client.get(endpoint).json()["title"])
        payload["expected_metadata_revision"] = 1
        response = self.client.put(endpoint, json=payload)
        self.assertEqual(1, response.json()["metadata_revision"])
        events = self.client.get(endpoint + "/events").json()
        self.assertEqual(["case_created", "case_updated"], [event["action"] for event in events])
        self.assertEqual(404, self.client.put("/api/cases/missing", json=payload).status_code)
        self.assertEqual(422, self.client.put(endpoint, json={"title": "Два", "expected_metadata_revision": 1}).status_code)

    def test_summary_counts_latest_unique_decisions_only_for_latest_run(self):
        case, options, run = self.fixture()
        case_id = case["id"]
        signal_id = run["result"]["signals"][0]["id"]
        count = len(run["result"]["signals"])
        initial = self.summary(case_id)
        self.assertEqual((count, 0, count), tuple(initial[key] for key in
                         ("signal_count", "reviewed_signal_count", "pending_signal_count")))
        endpoint = f'/api/runs/{run["id"]}/signals/{signal_id}/reviews'
        for status in ("confirmed", "resolved", "resolved"):
            response = self.client.post(endpoint, json={"status": status, "reviewer": "Тест", "comment": "Сверено"})
            self.assertEqual(201, response.status_code, response.text)
        self.assertEqual(1, self.summary(case_id)["reviewed_signal_count"])
        self.assertEqual(count - 1, self.summary(case_id)["pending_signal_count"])
        self.client.post(endpoint, json={"status": "needs_review", "reviewer": "Тест", "comment": "Проверить вновь"})
        self.assertEqual(0, self.summary(case_id)["reviewed_signal_count"])
        self.client.post(endpoint, json={"status": "dismissed", "reviewer": "Тест", "comment": "Уточнено"})
        new_run = self.client.post(f"/api/cases/{case_id}/runs", json=options).json()
        summary = self.summary(case_id)
        self.assertEqual(new_run["id"], summary["last_run_id"])
        self.assertEqual(0, summary["reviewed_signal_count"])
        self.assertEqual(count, summary["pending_signal_count"])
        # Decisions on an older run must not leak into the current registry totals.
        self.client.post(endpoint, json={"status": "confirmed", "reviewer": "Тест", "comment": "Старый запуск"})
        self.assertEqual(0, self.summary(case_id)["reviewed_signal_count"])

    def test_contract_number_fallback_is_unique_active_contract_not_delivery_number(self):
        created = self.client.post("/api/cases", json={"title": "Без номера"}).json()
        case_id = created["id"]
        documents = CASES["T02"]["request"]["documents"]
        delivery = copy.deepcopy(next(d for d in documents if d["type"] == "delivery"))
        self.client.post(f"/api/cases/{case_id}/structured", json=delivery)
        self.assertEqual("", self.summary(case_id)["contract_number"])
        contract = copy.deepcopy(next(d for d in documents if d["type"] == "contract"))
        self.client.post(f"/api/cases/{case_id}/structured", json=contract)
        self.assertEqual(contract["number"], self.summary(case_id)["contract_number"])
        other = {**contract, "id": "contract-other", "number": "ДРУГОЙ", "status": "cancelled"}
        self.client.post(f"/api/cases/{case_id}/structured", json=other)
        self.assertEqual(contract["number"], self.summary(case_id)["contract_number"])
        self.client.post(f"/api/cases/{case_id}/structured", json={**other, "id": "contract-third", "status": "active"})
        self.assertEqual("", self.summary(case_id)["contract_number"])

    def test_legacy_database_migrates_without_losing_document_run_or_event(self):
        legacy = self.root / "legacy"
        legacy.mkdir()
        request = CASES["T02"]["request"]
        doc = request["documents"][0]
        with sqlite3.connect(legacy / "kontrol.sqlite3") as conn:
            conn.executescript("""
                CREATE TABLE cases (id TEXT PRIMARY KEY,title TEXT NOT NULL,created_at TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE documents (case_id TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,
                    data TEXT NOT NULL,file_path TEXT,file_hash TEXT,original_name TEXT,created_at TEXT NOT NULL,
                    PRIMARY KEY(case_id,id));
                CREATE TABLE runs (id TEXT PRIMARY KEY,case_id TEXT NOT NULL,case_revision INTEGER NOT NULL,
                    request TEXT NOT NULL,result TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TABLE events (sequence INTEGER PRIMARY KEY AUTOINCREMENT,case_id TEXT NOT NULL,
                    action TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
                INSERT INTO cases VALUES ('legacy','Старое дело','2026-01-01T00:00:00+00:00',1);
                INSERT INTO events(case_id,action,payload,created_at)
                    VALUES ('legacy','case_created','{}','2026-01-02T00:00:00+00:00');
            """)
            conn.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)",
                         ("legacy", doc["id"], 1, json.dumps(doc), None, None, None, "2026-01-01T00:00:00+00:00"))
            result = {"signals": [], "status": "ok", "as_of": "2026-01-02", "result_hash": "legacy-hash"}
            conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)",
                         ("legacy-run", "legacy", 1, json.dumps(request), json.dumps(result), "2026-01-02T00:00:00+00:00"))
        store = Store(legacy)
        case = store.case("legacy")
        self.assertEqual(("", "", 0, 1), (case["description"], case["contract_number"], case["metadata_revision"], case["revision"]))
        self.assertEqual("2026-01-02T00:00:00+00:00", case["updated_at"])
        self.assertEqual(doc, case["documents"][0]["document"])
        self.assertEqual(result, store.run("legacy-run")["result"])
        store.update_case("legacy", "Обновлённое", "Описание", "Номер", 0)
        reopened = Store(legacy)
        self.assertEqual(1, reopened.case("legacy")["metadata_revision"])
        self.assertFalse(reopened.run("legacy-run")["stale"])
        self.assertEqual(["case_created", "case_updated"], [event["action"] for event in reopened.events("legacy")])

    def test_static_assets_are_served_with_content_type_and_cannot_escape_directory(self):
        web_root = self.root / "project"
        assets = web_root / "web" / "dist" / "assets"
        assets.mkdir(parents=True)
        (assets / "app.css").write_text("body{color:#78FF9E}", encoding="utf-8")
        (web_root / "web" / "dist" / "private.txt").write_text("not an asset", encoding="utf-8")
        with patch("kontrol.api.ROOT", web_root), patch.dict("os.environ", {"KONTROL_API_TOKEN": ""}):
            app = create_app(self.root / "asset-data")
        with TestClient(app) as client:
            response = client.get("/assets/app.css")
            self.assertEqual(200, response.status_code, response.text)
            self.assertIn("text/css", response.headers["content-type"])
            self.assertEqual("body{color:#78FF9E}", response.text)
            self.assertEqual(404, client.get("/assets/missing.css").status_code)
            self.assertEqual(404, client.get("/assets/%2e%2e/private.txt").status_code)


if __name__ == "__main__":
    unittest.main()
