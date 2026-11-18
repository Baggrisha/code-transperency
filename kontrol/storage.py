"""SQLite persistence: immutable run snapshots and append-only change history."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .models import CheckRequest, CheckResult, Document, ReviewUpdate, SourceRef


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class ConflictError(ValueError):
    pass


class NotFoundError(KeyError):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "files").mkdir(exist_ok=True)
        self.db = self.root / "kontrol.sqlite3"
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    created_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS documents (
                    case_id TEXT NOT NULL REFERENCES cases(id), id TEXT NOT NULL,
                    revision INTEGER NOT NULL, data TEXT NOT NULL,
                    file_path TEXT, file_hash TEXT, original_name TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(case_id,id)
                );
                CREATE INDEX IF NOT EXISTS document_hash ON documents(case_id,file_hash);
                CREATE TABLE IF NOT EXISTS document_versions (
                    case_id TEXT NOT NULL, document_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, data TEXT NOT NULL,
                    reviewer TEXT NOT NULL, comment TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(case_id,document_id,revision)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
                    case_revision INTEGER NOT NULL, request TEXT NOT NULL, result TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS run_case ON runs(case_id,created_at);
                CREATE TABLE IF NOT EXISTS reviews (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id), signal_id TEXT NOT NULL,
                    status TEXT NOT NULL, reviewer TEXT NOT NULL,
                    comment TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL REFERENCES cases(id), action TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
            """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _event(self, conn: sqlite3.Connection, case_id: str, action: str, payload: dict) -> None:
        conn.execute("INSERT INTO events(case_id,action,payload,created_at) VALUES (?,?,?,?)",
                     (case_id, action, json.dumps(payload, ensure_ascii=False), now()))

    def create_case(self, title: str) -> dict:
        case = {"id": uuid4().hex, "title": title, "created_at": now(), "revision": 0}
        with self.connect() as conn:
            conn.execute("INSERT INTO cases(id,title,created_at) VALUES (?,?,?)",
                         (case["id"], title, case["created_at"]))
            self._event(conn, case["id"], "case_created", {"title": title})
        return case

    def list_cases(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("""SELECT c.*, (SELECT COUNT(*) FROM documents d WHERE d.case_id=c.id) AS document_count,
                (SELECT id FROM runs r WHERE r.case_id=c.id ORDER BY created_at DESC LIMIT 1) AS last_run_id
                FROM cases c ORDER BY created_at DESC, id""").fetchall()
        return [dict(r) for r in rows]

    def case(self, case_id: str) -> dict:
        with self.connect() as conn:
            # A read transaction makes revision + document snapshot consistent.
            conn.execute("BEGIN")
            row = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
            if row is None:
                raise NotFoundError(case_id)
            docs = conn.execute("SELECT * FROM documents WHERE case_id=? ORDER BY id", (case_id,)).fetchall()
            runs = conn.execute("SELECT id,case_revision,created_at FROM runs WHERE case_id=? ORDER BY created_at DESC", (case_id,)).fetchall()
        return {**dict(row), "documents": [self._doc_record(r) for r in docs], "runs": [dict(r) for r in runs]}

    @staticmethod
    def _doc_record(row: sqlite3.Row) -> dict:
        result = dict(row)
        result["document"] = json.loads(result.pop("data"))
        result.pop("file_path", None)
        return result

    def by_file_hash(self, case_id: str, file_hash: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM documents WHERE case_id=? AND file_hash=?", (case_id, file_hash)).fetchone()
        return self._doc_record(row) if row else None

    def add_document(self, case_id: str, document: Document, *, file_path: str | None = None,
                     file_hash: str | None = None, original_name: str | None = None) -> dict:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM cases WHERE id=?", (case_id,)).fetchone():
                raise NotFoundError(case_id)
            if file_hash:
                existing = conn.execute("SELECT * FROM documents WHERE case_id=? AND file_hash=?", (case_id, file_hash)).fetchone()
                if existing:
                    self._event(conn, case_id, "duplicate_upload", {"document_id": existing["id"], "file_hash": file_hash})
                    return {**self._doc_record(existing), "duplicate": True}
            existing = conn.execute("SELECT * FROM documents WHERE case_id=? AND id=?", (case_id, document.id)).fetchone()
            if existing:
                if json.loads(existing["data"]) == document.model_dump(mode="json"):
                    self._event(conn, case_id, "duplicate_document", {"document_id": document.id})
                    return {**self._doc_record(existing), "duplicate": True}
                raise ConflictError("ID документа уже занят другим содержимым. Укажите новый ID и supersedes либо отредактируйте реквизиты с сохранением истории.")
            timestamp = now()
            data = document.model_dump_json()
            conn.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)",
                         (case_id, document.id, 1, data, file_path, file_hash, original_name, timestamp))
            conn.execute("INSERT INTO document_versions VALUES (?,?,?,?,?,?,?)",
                         (case_id, document.id, 1, data, "import", "Исходное извлечение", timestamp))
            conn.execute("UPDATE cases SET revision=revision+1 WHERE id=?", (case_id,))
            self._event(conn, case_id, "document_added", {"document_id": document.id, "file_hash": file_hash})
        return {"id": document.id, "case_id": case_id, "revision": 1,
                "document": document.model_dump(mode="json"), "duplicate": False}

    def record_duplicate(self, case_id: str, document_id: str, file_hash: str) -> None:
        with self.connect() as conn:
            self._event(conn, case_id, "duplicate_upload", {"document_id": document_id, "file_hash": file_hash})

    def update_document(self, case_id: str, document_id: str, document: Document,
                        expected_revision: int, reviewer: str, comment: str) -> dict:
        if document.id != document_id:
            raise ConflictError("ID документа нельзя менять при исправлении извлечения")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute("SELECT * FROM documents WHERE case_id=? AND id=?", (case_id, document_id)).fetchone()
            if old is None:
                raise NotFoundError(document_id)
            if old["revision"] != expected_revision:
                raise ConflictError("Документ уже изменён. Обновите данные и повторите исправление.")
            original = Document.model_validate_json(old["data"])
            document = document.model_copy(deep=True)
            document.content_hash = original.content_hash
            document.filename = original.filename
            document.extraction_method = "manual_review"
            # A reviewed number must not keep an excerpt claiming the old number.
            # Keep original versions unchanged; tag the new field's human origin.
            old_lines = {item.line_id: item for item in original.items}
            for item in document.items:
                previous = old_lines.get(item.line_id)
                for field in ("name", "position_id", "sku", "unit", "quantity", "unit_price", "amount",
                              "currency", "tax_included", "vat_rate", "discount_percent",
                              "conversion_factor", "conversion_to", "attributes"):
                    before = getattr(previous, field) if previous else None
                    after = getattr(item, field)
                    confirmed = previous and item.confidence.get(field) != previous.confidence.get(field) and item.confidence.get(field) == 1
                    if before != after or confirmed:
                        old_ref = previous.field_sources.get(field) or previous.source if previous else None
                        item.field_sources[field] = SourceRef(document_id=document_id, filename=document.filename,
                            page=old_ref.page if old_ref else None, row=old_ref.row if old_ref else None,
                            field=field, excerpt=f"Ручное уточнение ({reviewer}): {field}={after}. Ранее: {before}. {comment}")
                        item.confidence[field] = 1.0
            changes = []
            for field in ("type", "number", "contract_id", "date", "effective_from", "deadline", "status",
                          "supersedes", "specification_id", "replacement_spec_id", "approved", "is_complete"):
                before, after = getattr(original, field), getattr(document, field)
                confirmed = document.fields_confidence.get(field) == 1 and original.fields_confidence.get(field, 1) < 1
                if before != after or confirmed:
                    changes.append(f"{field}: {before} → {after}")
                    document.fields_confidence[field] = 1.0
            if changes:
                ref = original.source.model_copy(deep=True) if original.source else SourceRef(document_id=document_id, filename=document.filename)
                ref.excerpt += f"\nРучное уточнение ({reviewer}): {'; '.join(changes)}. {comment}"
                document.source = ref
            revision = expected_revision + 1
            data = document.model_dump_json()
            conn.execute("UPDATE documents SET revision=?,data=? WHERE case_id=? AND id=?",
                         (revision, data, case_id, document_id))
            conn.execute("INSERT INTO document_versions VALUES (?,?,?,?,?,?,?)",
                         (case_id, document_id, revision, data, reviewer, comment, now()))
            conn.execute("UPDATE cases SET revision=revision+1 WHERE id=?", (case_id,))
            self._event(conn, case_id, "extraction_reviewed", {"document_id": document_id,
                         "revision": revision, "reviewer": reviewer, "comment": comment})
        return {"id": document_id, "revision": revision, "document": document.model_dump(mode="json")}

    def document_history(self, case_id: str, document_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM document_versions WHERE case_id=? AND document_id=? ORDER BY revision", (case_id, document_id)).fetchall()
        if not rows:
            raise NotFoundError(document_id)
        return [{**{k: row[k] for k in row.keys() if k != "data"}, "document": json.loads(row["data"])} for row in rows]

    def source_path(self, case_id: str, document_id: str) -> tuple[Path, str]:
        with self.connect() as conn:
            row = conn.execute("SELECT file_path,original_name FROM documents WHERE case_id=? AND id=?", (case_id, document_id)).fetchone()
        if not row or not row["file_path"]:
            raise NotFoundError(document_id)
        path = Path(row["file_path"]).resolve()
        if not path.is_relative_to(self.root / "files") or not path.is_file():
            raise NotFoundError(document_id)
        return path, row["original_name"] or path.name

    def save_run(self, case_id: str, case_revision: int, request: CheckRequest, result: CheckResult) -> dict:
        run_id, timestamp = uuid4().hex, now()
        with self.connect() as conn:
            conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)", (run_id, case_id, case_revision,
                         request.model_dump_json(), result.model_dump_json(), timestamp))
            self._event(conn, case_id, "check_completed", {"run_id": run_id, "input_hash": result.input_hash,
                         "result_hash": result.result_hash, "case_revision": case_revision})
        return {"id": run_id, "case_id": case_id, "case_revision": case_revision,
                "created_at": timestamp, "result": result.model_dump(mode="json")}

    def run(self, run_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise NotFoundError(run_id)
            reviews = conn.execute("SELECT * FROM reviews WHERE run_id=? ORDER BY sequence", (run_id,)).fetchall()
            case_revision = conn.execute("SELECT revision FROM cases WHERE id=?", (row["case_id"],)).fetchone()[0]
        return {**{k: row[k] for k in row.keys() if k not in ("request", "result")},
                "request": json.loads(row["request"]), "result": json.loads(row["result"]),
                "reviews": [dict(r) for r in reviews], "stale": row["case_revision"] != case_revision}

    def review(self, run_id: str, signal_id: str, update: ReviewUpdate) -> dict:
        run = self.run(run_id)
        if signal_id not in {s["id"] for s in run["result"]["signals"]}:
            raise NotFoundError(signal_id)
        with self.connect() as conn:
            timestamp = now()
            cursor = conn.execute("INSERT INTO reviews(run_id,signal_id,status,reviewer,comment,created_at) VALUES (?,?,?,?,?,?)",
                                 (run_id, signal_id, update.status, update.reviewer, update.comment, timestamp))
            self._event(conn, run["case_id"], "signal_reviewed", {"run_id": run_id, "signal_id": signal_id,
                         **update.model_dump()})
        return {"sequence": cursor.lastrowid, "run_id": run_id, "signal_id": signal_id,
                **update.model_dump(), "created_at": timestamp}

    def events(self, case_id: str) -> list[dict]:
        self.case(case_id)
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM events WHERE case_id=? ORDER BY sequence", (case_id,)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]
