"""Local HTTP API. Run with: python -m uvicorn kontrol.api:app --host 127.0.0.1."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.security import HTTPBearer
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import Field, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool

from . import __version__
from .engine import run_check
from .exports import report_html, signals_csv
from .extract import ExtractionError, extract_document
from .models import CheckRequest, Day, DocType, Document, ManualLink, Model, Number, ReviewUpdate
from .storage import (ConflictError, InvalidReviewError, NotFoundError, Store,
                      validate_review_confirmations, validate_review_discards)

ROOT = Path(__file__).resolve().parent.parent
MAX_FILE_BYTES = 20 * 1024 * 1024
ALLOWED_SUFFIXES = {".json", ".txt", ".csv", ".tsv", ".pdf", ".docx", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


class CaseCreate(Model):
    title: str = Field(min_length=1, max_length=300)


class RunOptions(Model):
    as_of: Day
    delivery_complete: bool = False
    selected_contract_id: str | None = None
    manual_links: list[ManualLink] = Field(default_factory=list)
    required_types: list[DocType] = Field(default_factory=lambda: ["contract", "delivery", "acceptance"])
    confidence_threshold: float = Field(default=0.90, ge=0.5, le=1)
    money_tolerance: Number = Field(default=Decimal("0.01"), ge=0, le=1)


class DocumentUpdate(Model):
    document: Document
    expected_revision: int = Field(ge=1)
    reviewer: str = Field(min_length=1, max_length=200)
    comment: str = Field(min_length=1, max_length=10000)
    confirmed_fields: list[str] = Field(default_factory=list, description="Явно подтверждённые поля документа.")
    confirmed_items: dict[str, list[str]] = Field(
        default_factory=dict, description="Явно подтверждённые поля строк по их line_id.")
    discarded_confidence_fields: list[str] = Field(
        default_factory=list, description="Ошибочные ключи уверенности, не являющиеся полями документа.")
    discarded_item_confidence: dict[str, list[str]] = Field(
        default_factory=dict, description="Ошибочные ключи уверенности строк по их line_id.")

    @model_validator(mode="after")
    def valid_confirmations(self) -> "DocumentUpdate":
        validate_review_confirmations(self.document, self.confirmed_fields, self.confirmed_items)
        validate_review_discards(self.document, self.discarded_confidence_fields, self.discarded_item_confidence)
        return self


def create_app(data_dir: Path | None = None) -> FastAPI:
    store = Store(data_dir or Path(os.environ.get("KONTROL_DATA_DIR", ROOT / "data")))
    app = FastAPI(title="Контроль исполнения", version=__version__,
                  dependencies=[Depends(HTTPBearer(auto_error=False))],
                  description="Объяснимая сверка документов одного контракта. Локальный демонстрационный модуль.")
    app.state.store = store
    token = os.environ.get("KONTROL_API_TOKEN", "")
    origins = [x.strip() for x in os.environ.get("KONTROL_CORS_ORIGINS", "").split(",") if x.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "PUT"],
                           allow_headers=["Authorization", "Content-Type"])

    @app.middleware("http")
    async def authentication(request, call_next):
        if token and request.url.path.startswith("/api/"):
            provided = request.headers.get("authorization", "")
            if not secrets.compare_digest(provided, "Bearer " + token):
                return JSONResponse({"detail": "Требуется Bearer-токен"}, status_code=401)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(NotFoundError)
    async def not_found(request, exc):
        return JSONResponse({"detail": "Объект не найден"}, status_code=404)

    @app.exception_handler(ConflictError)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(InvalidReviewError)
    async def invalid_review(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(ExtractionError)
    async def extraction_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(ValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"detail": json.loads(exc.json(include_input=False, include_context=False))}, status_code=422)

    @app.get("/health", tags=["Служебные"])
    def health():
        return {"status": "ok", "version": __version__, "mode": "local_demo"}

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index():
        path = ROOT / "web" / "index.html"
        if path.is_file():
            return HTMLResponse(path.read_text(encoding="utf-8"))
        return HTMLResponse('<html lang="ru"><meta charset="utf-8"><h1>Контроль исполнения</h1><a href="/docs">Открыть API</a></html>')

    @app.post("/api/check", tags=["Сверка"])
    def check(request: CheckRequest):
        """Stateless, repeatable check. as_of must be explicit."""
        return run_check(request)

    @app.get("/api/cases", tags=["Дела"])
    def cases():
        return store.list_cases()

    @app.post("/api/cases", status_code=201, tags=["Дела"])
    def new_case(payload: CaseCreate):
        return store.create_case(payload.title)

    @app.get("/api/cases/{case_id}", tags=["Дела"])
    def get_case(case_id: str):
        return store.case(case_id)

    async def receive_file(file: UploadFile) -> tuple[Path, str, str]:
        original_name = Path((file.filename or "document").replace("\\", "/")).name
        suffix = Path(original_name).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(415, "Поддерживаются PDF, DOCX, TXT, CSV, TSV, JSON, PNG, JPG и TIFF")
        path = store.root / "files" / (uuid4().hex + suffix)
        digest, size = hashlib.sha256(), 0
        try:
            with path.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise HTTPException(413, "Файл больше 20 МБ")
                    digest.update(chunk)
                    output.write(chunk)
            if size == 0:
                raise HTTPException(422, "Файл пуст")
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()
        return path, digest.hexdigest(), original_name

    def parse_metadata(value: str | None) -> dict:
        if not value:
            return {}
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            raise HTTPException(422, "metadata должен быть JSON-объектом")
        if not isinstance(data, dict):
            raise HTTPException(422, "metadata должен быть JSON-объектом")
        return data

    def relabel(extracted, original_name: str, file_hash: str):
        doc = extracted.document
        doc.filename, doc.content_hash = original_name, file_hash
        sources = [doc.source] + [s for item in doc.items for s in [item.source, *item.field_sources.values()]]
        for source in sources:
            if source:
                source.filename = original_name
        return extracted

    @app.post("/api/extract", tags=["Извлечение"])
    async def extract(file: UploadFile = File(...), document_type: DocType | None = Form(None),
                      contract_id: str | None = Form(None), metadata: str | None = Form(None)):
        options = parse_metadata(metadata)
        path, file_hash, name = await receive_file(file)
        try:
            result = await run_in_threadpool(extract_document, path, document_type=document_type,
                                            contract_id=contract_id, metadata=options)
            return relabel(result, name, file_hash)
        finally:
            path.unlink(missing_ok=True)

    @app.post("/api/cases/{case_id}/documents", status_code=201, tags=["Документы"])
    async def upload(case_id: str, file: UploadFile = File(...), document_type: DocType | None = Form(None),
                     contract_id: str | None = Form(None), metadata: str | None = Form(None)):
        store.case(case_id)
        options = parse_metadata(metadata)
        path, file_hash, name = await receive_file(file)
        try:
            duplicate = store.by_file_hash(case_id, file_hash)
            if duplicate:
                store.record_duplicate(case_id, duplicate["id"], file_hash)
                path.unlink(missing_ok=True)
                return {**duplicate, "duplicate": True, "warnings": ["Повторная загрузка не увеличивает количество поставки"]}
            result = await run_in_threadpool(extract_document, path, document_type=document_type,
                                            contract_id=contract_id, metadata=options)
            result = relabel(result, name, file_hash)
            record = store.add_document(case_id, result.document, file_path=str(path), file_hash=file_hash, original_name=name)
            if record["duplicate"]:
                path.unlink(missing_ok=True)
            return {**record, "warnings": result.warnings, "needs_review": result.needs_review}
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    @app.post("/api/cases/{case_id}/structured", status_code=201, tags=["Документы"])
    def add_structured(case_id: str, document: Document):
        """Import an already reviewed structured document (no original file attached)."""
        return store.add_document(case_id, document)

    @app.put("/api/cases/{case_id}/documents/{document_id}", tags=["Документы"])
    def edit_document(case_id: str, document_id: str, payload: DocumentUpdate):
        return store.update_document(case_id, document_id, payload.document, payload.expected_revision,
                                     payload.reviewer, payload.comment, payload.confirmed_fields, payload.confirmed_items,
                                     payload.discarded_confidence_fields, payload.discarded_item_confidence)

    @app.get("/api/cases/{case_id}/documents/{document_id}/history", tags=["Документы"])
    def doc_history(case_id: str, document_id: str):
        return store.document_history(case_id, document_id)

    @app.get("/api/cases/{case_id}/documents/{document_id}/file", tags=["Документы"])
    def source_file(case_id: str, document_id: str):
        path, name = store.source_path(case_id, document_id)
        return FileResponse(path, filename=name)

    @app.post("/api/cases/{case_id}/runs", status_code=201, tags=["Сверка"])
    def check_case(case_id: str, options: RunOptions):
        case = store.case(case_id)
        request = CheckRequest(documents=[Document.model_validate(d["document"]) for d in case["documents"]],
                               **options.model_dump())
        result = run_check(request)
        return store.save_run(case_id, case["revision"], request, result)

    @app.get("/api/runs/{run_id}", tags=["Сверка"])
    def get_run(run_id: str):
        return store.run(run_id)

    @app.post("/api/runs/{run_id}/signals/{signal_id}/reviews", status_code=201, tags=["Рассмотрение"])
    def review_signal(run_id: str, signal_id: str, update: ReviewUpdate):
        return store.review(run_id, signal_id, update)

    @app.get("/api/cases/{case_id}/events", tags=["Рассмотрение"])
    def events(case_id: str):
        return store.events(case_id)

    @app.get("/api/runs/{run_id}/export", tags=["Экспорт"])
    def export(run_id: str, format: Literal["json", "csv", "html"] = "json"):
        run = store.run(run_id)
        if format == "csv":
            return Response(signals_csv(run), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="check-{run_id}.csv"'})
        if format == "html":
            return HTMLResponse(report_html(run), headers={"Content-Disposition": f'attachment; filename="check-{run_id}.html"'})
        return Response(json.dumps(run, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="check-{run_id}.json"'})

    def demo_fixtures() -> list[dict]:
        path = ROOT / "fixtures" / "cases.json"
        if not path.is_file():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data["cases"]

    @app.get("/api/demo", tags=["Демонстрация"])
    def demos():
        return [{"id": c["id"], "title": c["title"], "description": c.get("description", ""),
                 "expected": c.get("expected", {})} for c in demo_fixtures()]

    @app.post("/api/demo/{fixture_id}", status_code=201, tags=["Демонстрация"])
    def import_demo(fixture_id: str):
        fixture = next((c for c in demo_fixtures() if c["id"] == fixture_id), None)
        if fixture is None:
            raise NotFoundError(fixture_id)
        request = CheckRequest.model_validate(fixture["request"])
        case = store.create_case(f'{fixture_id} · {fixture["title"]} [синтетические данные]')
        for document in request.documents:
            store.add_document(case["id"], document)
        return {"case": store.case(case["id"]),
                "options": request.model_dump(mode="json", exclude={"documents"})}

    return app


app = create_app()
