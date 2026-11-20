"""SQLite persistence: immutable run snapshots and append-only change history."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .models import CheckRequest, CheckResult, Document, Item, ReviewUpdate, SourceRef


DOCUMENT_REVIEW_FIELDS = (
    "id", "type", "number", "contract_id", "date", "effective_from", "deadline",
    "revision", "logical_id", "status", "supersedes", "specification_id",
    "base_document_id", "replacement_spec_id", "approved", "related_delivery_ids",
    "acceptance_mode", "is_complete", "missing_fields", "expected_pages", "actual_pages", "items",
)
ITEM_REVIEW_FIELDS = (
    "line_id", "position_id", "sku", "name", "unit", "quantity", "unit_price",
    "amount", "currency", "tax_included", "vat_rate", "discount_percent",
    "conversion_factor", "conversion_to", "attributes",
)


def validate_review_confirmations(document: Document, confirmed_fields: list[str],
                                  confirmed_items: dict[str, list[str]]) -> None:
    unknown = set(confirmed_fields) - set(DOCUMENT_REVIEW_FIELDS)
    if unknown:
        raise ValueError(f"Неизвестные поля подтверждения документа: {', '.join(sorted(unknown))}")
    line_ids = {item.line_id for item in document.items}
    unknown_lines = set(confirmed_items) - line_ids
    if unknown_lines:
        raise ValueError(f"Строки подтверждения отсутствуют в документе: {', '.join(sorted(unknown_lines))}")
    for line_id, fields in confirmed_items.items():
        unknown = set(fields) - set(ITEM_REVIEW_FIELDS)
        if unknown:
            raise ValueError(f"Неизвестные поля подтверждения строки {line_id}: {', '.join(sorted(unknown))}")


def validate_review_discards(document: Document, discarded_confidence_fields: list[str],
                             discarded_item_confidence: dict[str, list[str]]) -> None:
    real_fields = set(discarded_confidence_fields) & set(Document.model_fields)
    if real_fields:
        raise ValueError(f"Нельзя удалить уверенность реальных полей документа: {', '.join(sorted(real_fields))}")
    missing_lines = set(discarded_item_confidence) - {item.line_id for item in document.items}
    if missing_lines:
        raise ValueError(f"Строки удаления уверенности отсутствуют в документе: {', '.join(sorted(missing_lines))}")
    for line_id, fields in discarded_item_confidence.items():
        real_fields = set(fields) & set(Item.model_fields)
        if real_fields:
            raise ValueError(f"Нельзя удалить уверенность реальных полей строки {line_id}: {', '.join(sorted(real_fields))}")


def _retained_confidence(original: dict[str, float], supplied: dict[str, float]) -> dict[str, float]:
    """A full-document update may lower confidence but cannot silently raise it."""
    result = original.copy()
    for field, value in supplied.items():
        if value < original.get(field, 1.0):
            result[field] = value
    return result


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class ConflictError(ValueError):
    pass


class NotFoundError(KeyError):
    pass


class InvalidReviewError(ValueError):
    pass


RUN_SUMMARY_SQL = """r.id, r.case_id, r.case_revision, r.created_at,
    json_extract(r.result,'$.status') AS status,
    json_extract(r.result,'$.as_of') AS as_of,
    json_array_length(json_extract(r.result,'$.signals')) AS signal_count,
    json_extract(r.result,'$.result_hash') AS result_hash,
    (r.case_revision != c.revision) AS stale"""


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
                (SELECT id FROM runs r WHERE r.case_id=c.id ORDER BY created_at DESC LIMIT 1) AS last_run_id,
                (SELECT json_extract(r.result,'$.status') FROM runs r WHERE r.case_id=c.id
                    ORDER BY created_at DESC LIMIT 1) AS last_run_status,
                (SELECT r.created_at FROM runs r WHERE r.case_id=c.id ORDER BY created_at DESC LIMIT 1) AS last_run_at,
                (SELECT COUNT(*) FROM runs r WHERE r.case_id=c.id) AS run_count,
                (SELECT r.case_revision != c.revision FROM runs r WHERE r.case_id=c.id
                    ORDER BY created_at DESC LIMIT 1) AS last_run_stale
                FROM cases c ORDER BY created_at DESC, id""").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            if item["last_run_stale"] is not None:
                item["last_run_stale"] = bool(item["last_run_stale"])
            result.append(item)
        return result

    def case(self, case_id: str) -> dict:
        with self.connect() as conn:
            # A read transaction makes revision + document snapshot consistent.
            conn.execute("BEGIN")
            row = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
            if row is None:
                raise NotFoundError(case_id)
            docs = conn.execute("SELECT * FROM documents WHERE case_id=? ORDER BY id", (case_id,)).fetchall()
            runs = conn.execute(f"SELECT {RUN_SUMMARY_SQL} FROM runs r JOIN cases c ON c.id=r.case_id "
                                "WHERE r.case_id=? ORDER BY r.created_at DESC", (case_id,)).fetchall()
        return {**dict(row), "documents": [self._doc_record(r) for r in docs],
                "runs": [self._run_summary(r) for r in runs]}

    @staticmethod
    def _run_summary(row: sqlite3.Row) -> dict:
        result = dict(row)
        result["stale"] = bool(result["stale"])
        return result

    def list_runs(self, case_id: str | None = None, status: str | None = None,
                  limit: int = 100, offset: int = 0) -> dict:
        """Журнал проверок: краткие сводки запусков, новые сверху."""
        where, params = [], []
        if case_id:
            self.case(case_id)
            where.append("r.case_id=?")
            params.append(case_id)
        if status:
            where.append("json_extract(r.result,'$.status')=?")
            params.append(status)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        with self.connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM runs r {clause}", params).fetchone()[0]
            rows = conn.execute(f"SELECT {RUN_SUMMARY_SQL}, c.title AS case_title, "
                                "(SELECT COUNT(*) FROM reviews v WHERE v.run_id=r.id) AS review_count "
                                f"FROM runs r JOIN cases c ON c.id=r.case_id {clause} "
                                "ORDER BY r.created_at DESC, r.id LIMIT ? OFFSET ?",
                                [*params, limit, offset]).fetchall()
        return {"total": total, "limit": limit, "offset": offset, "items": [self._run_summary(r) for r in rows]}

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
            stored_path = self._relative_file(file_path) if file_path else None
            conn.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)",
                         (case_id, document.id, 1, data, stored_path, file_hash, original_name, timestamp))
            conn.execute("INSERT INTO document_versions VALUES (?,?,?,?,?,?,?)",
                         (case_id, document.id, 1, data, "import", "Исходное извлечение", timestamp))
            conn.execute("UPDATE cases SET revision=revision+1 WHERE id=?", (case_id,))
            self._event(conn, case_id, "document_added", {"document_id": document.id, "file_hash": file_hash})
        return {"id": document.id, "case_id": case_id, "revision": 1,
                "document": document.model_dump(mode="json"), "duplicate": False}

    def _relative_file(self, file_path: str) -> str:
        """Пути хранятся относительно каталога данных, чтобы его можно было переносить."""
        path = Path(file_path).resolve()
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def record_duplicate(self, case_id: str, document_id: str, file_hash: str) -> None:
        with self.connect() as conn:
            self._event(conn, case_id, "duplicate_upload", {"document_id": document_id, "file_hash": file_hash})

    def update_document(self, case_id: str, document_id: str, document: Document,
                        expected_revision: int, reviewer: str, comment: str,
                        confirmed_fields: list[str] | None = None,
                        confirmed_items: dict[str, list[str]] | None = None,
                        discarded_confidence_fields: list[str] | None = None,
                        discarded_item_confidence: dict[str, list[str]] | None = None) -> dict:
        confirmed_fields = confirmed_fields or []
        confirmed_items = confirmed_items or {}
        discarded_confidence_fields = discarded_confidence_fields or []
        discarded_item_confidence = discarded_item_confidence or {}
        validate_review_confirmations(document, confirmed_fields, confirmed_items)
        validate_review_discards(document, discarded_confidence_fields, discarded_item_confidence)
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
            missing_keys = set(discarded_confidence_fields) - set(original.fields_confidence)
            if missing_keys:
                raise InvalidReviewError(f"Ключи уверенности отсутствуют в сохранённом документе: {', '.join(sorted(missing_keys))}")
            old_lines = {item.line_id: item for item in original.items}
            for line_id, fields in discarded_item_confidence.items():
                if line_id not in old_lines:
                    raise InvalidReviewError(f"Строка отсутствует в сохранённом документе: {line_id}")
                missing_keys = set(fields) - set(old_lines[line_id].confidence)
                if missing_keys:
                    raise InvalidReviewError(f"Ключи уверенности отсутствуют в сохранённой строке {line_id}: {', '.join(sorted(missing_keys))}")
            document = document.model_copy(deep=True)
            document.content_hash = original.content_hash
            document.filename = original.filename
            document.extraction_method = original.extraction_method
            document.source = original.source.model_copy(deep=True) if original.source else None
            supplied_confidence = document.fields_confidence
            document.fields_confidence = _retained_confidence(original.fields_confidence, supplied_confidence)
            changes, confirmations = [], []
            for field in sorted(set(discarded_confidence_fields)):
                value = document.fields_confidence.pop(field)
                changes.append(f"удалён ошибочный ключ уверенности документа {field!r}: {value}")
            for field in DOCUMENT_REVIEW_FIELDS:
                if field == "items":
                    before = [item.line_id for item in original.items]
                    after = [item.line_id for item in document.items]
                else:
                    before, after = getattr(original, field), getattr(document, field)
                confirmed = field in confirmed_fields
                changed = before != after
                if confirmed:
                    document.fields_confidence[field] = 1.0
                elif changed:
                    document.fields_confidence[field] = 0.0
                lowered = document.fields_confidence.get(field, 1.0) < original.fields_confidence.get(field, 1.0)
                if changed:
                    state = "подтверждено" if confirmed else "не подтверждено"
                    changes.append(f"{field}: {before} → {after} ({state})")
                elif confirmed:
                    confirmations.append(f"{field}: {after}")
                elif lowered:
                    changes.append(f"{field}: уверенность {original.fields_confidence.get(field, 1.0)} → "
                                   f"{document.fields_confidence[field]}")
            for field in sorted(set(document.fields_confidence) - set(DOCUMENT_REVIEW_FIELDS)):
                before = original.fields_confidence.get(field, 1.0)
                after = document.fields_confidence[field]
                if after < before:
                    changes.append(f"{field}: уверенность {before} → {after}")
            if original.extraction_warnings != document.extraction_warnings:
                changes.append(f"extraction_warnings: {original.extraction_warnings} → {document.extraction_warnings}")

            for line_id, fields in discarded_item_confidence.items():
                for field in sorted(set(fields)):
                    value = old_lines[line_id].confidence[field]
                    changes.append(f"удалён ошибочный ключ уверенности строки {line_id!r} {field!r}: {value}")
            new_lines = {item.line_id for item in document.items}
            for line_id in sorted(old_lines.keys() - new_lines):
                changes.append(f"строка удалена: {line_id}")
            for item in document.items:
                previous = old_lines.get(item.line_id)
                supplied_confidence = item.confidence
                item.confidence = _retained_confidence(previous.confidence if previous else {}, supplied_confidence)
                for field in discarded_item_confidence.get(item.line_id, []):
                    item.confidence.pop(field, None)
                item.source = previous.source.model_copy(deep=True) if previous and previous.source else None
                item.field_sources = ({field: ref.model_copy(deep=True) for field, ref in previous.field_sources.items()}
                                      if previous else {})
                if previous is None:
                    changes.append(f"строка добавлена: {item.line_id}")
                for field in ITEM_REVIEW_FIELDS:
                    before = getattr(previous, field) if previous else None
                    after = getattr(item, field)
                    confirmed = field in confirmed_items.get(item.line_id, [])
                    changed = previous is not None and before != after
                    created = (previous is None and field != "line_id" and
                               (after is not None and after != {} or field in supplied_confidence))
                    if confirmed:
                        item.confidence[field] = 1.0
                    elif changed or created or (previous is None and field == "line_id" and field in supplied_confidence):
                        item.confidence[field] = 0.0
                    old_confidence = previous.confidence.get(field, 1.0) if previous else 1.0
                    lowered = item.confidence.get(field, 1.0) < old_confidence
                    if changed or created or confirmed or lowered:
                        old_ref = (previous.field_sources.get(field) or previous.source) if previous else None
                        excerpt = old_ref.excerpt + "\n" if old_ref and old_ref.excerpt else ""
                        if changed or created or previous is None:
                            state = "подтверждено" if confirmed else "не подтверждено"
                            excerpt += (f"Ручное изменение ({reviewer}): {field}: {before} → {after} "
                                        f"({state}). {comment}")
                        elif confirmed:
                            excerpt += f"Подтверждение ({reviewer}): {field}: {after}. {comment}"
                        else:
                            excerpt += (f"Уверенность снижена ({reviewer}): {field}: {old_confidence} → "
                                        f"{item.confidence[field]}. {comment}")
                        item.field_sources[field] = SourceRef(document_id=document_id, filename=document.filename,
                            page=old_ref.page if old_ref else None, row=old_ref.row if old_ref else None,
                            field=field, excerpt=excerpt)
                for field in sorted(set(item.confidence) - set(ITEM_REVIEW_FIELDS)):
                    before = previous.confidence.get(field, 1.0) if previous else 1.0
                    if item.confidence[field] < before:
                        old_ref = (previous.field_sources.get(field) or previous.source) if previous else None
                        excerpt = old_ref.excerpt + "\n" if old_ref and old_ref.excerpt else ""
                        excerpt += (f"Уверенность снижена ({reviewer}): {field}: {before} → "
                                    f"{item.confidence[field]}. {comment}")
                        item.field_sources[field] = SourceRef(document_id=document_id, filename=document.filename,
                            page=old_ref.page if old_ref else None, row=old_ref.row if old_ref else None,
                            field=field, excerpt=excerpt)
            if changes or confirmations:
                ref = document.source or SourceRef(document_id=document_id, filename=document.filename)
                if changes:
                    ref.excerpt += f"\nРучное изменение ({reviewer}): {'; '.join(changes)}. {comment}"
                if confirmations:
                    ref.excerpt += f"\nПодтверждение ({reviewer}): {'; '.join(confirmations)}. {comment}"
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
        stored = Path(row["file_path"])
        if stored.is_absolute():
            # Совместимость с базами, созданными до перехода на относительные пути:
            # если каталог данных перенесли, ищем файл по имени в текущем files/.
            path = stored.resolve()
            if not path.is_file() or not path.is_relative_to(self.root / "files"):
                path = (self.root / "files" / stored.name).resolve()
        else:
            path = (self.root / stored).resolve()
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
            prior = conn.execute("""SELECT v.* FROM reviews v JOIN runs r ON r.id=v.run_id
                WHERE r.case_id=? AND v.run_id<>? AND r.created_at<=? ORDER BY r.created_at, v.sequence""",
                (row["case_id"], run_id, row["created_at"])).fetchall()
        result = json.loads(row["result"])
        signal_ids = {s["id"] for s in result["signals"]}
        review_list = [dict(r) for r in reviews]
        latest = {r["signal_id"]: r for r in review_list}
        # Идентификатор сигнала — хеш его содержания, поэтому совпадение id означает
        # тот же самый вывод алгоритма. Прежнее решение показывается как подсказка,
        # но не копируется в reviews этого запуска.
        prior_latest = {r["signal_id"]: dict(r) for r in prior if r["signal_id"] in signal_ids}
        return {**{k: row[k] for k in row.keys() if k not in ("request", "result")},
                "request": json.loads(row["request"]), "result": result,
                "reviews": review_list, "stale": row["case_revision"] != case_revision,
                "latest_reviews": latest,
                "prior_reviews": {k: v for k, v in prior_latest.items() if k not in latest}}

    def record_export(self, run: dict, fmt: str) -> None:
        with self.connect() as conn:
            self._event(conn, run["case_id"], "report_exported", {"run_id": run["id"], "format": fmt})

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

    def events(self, case_id: str, action: list[str] | None = None, since: str | None = None,
               after_sequence: int | None = None, limit: int | None = None) -> list[dict]:
        self.case(case_id)
        where, params = ["case_id=?"], [case_id]
        if action:
            where.append(f"action IN ({','.join('?' * len(action))})")
            params.extend(action)
        if since:
            where.append("created_at>=?")
            params.append(since)
        if after_sequence is not None:
            where.append("sequence>?")
            params.append(after_sequence)
        sql = f"SELECT * FROM events WHERE {' AND '.join(where)} ORDER BY sequence"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]
