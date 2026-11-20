"""Public, versioned data contract used by extraction, engine and HTTP API."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from .normalize import date_value, decimal_value, normalize_currency, normalize_unit

SCHEMA_VERSION = "1.0"
ENGINE_VERSION = "rules-1.2.0"
Number = Annotated[Decimal, BeforeValidator(decimal_value)]
Day = Annotated[date, BeforeValidator(date_value)]
DocType = Literal["contract", "specification", "delivery", "acceptance", "amendment"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class SourceRef(Model):
    document_id: str
    filename: str | None = None
    page: int | None = Field(default=None, ge=1)
    row: int | None = Field(default=None, ge=1)
    field: str | None = None
    excerpt: str = ""


class Item(Model):
    line_id: str = Field(min_length=1, max_length=200)
    position_id: str | None = None
    sku: str | None = None
    name: str | None = None
    unit: str | None = None
    quantity: Number | None = Field(default=None, ge=0)
    unit_price: Number | None = Field(default=None, ge=0)
    amount: Number | None = Field(default=None, ge=0)
    currency: str | None = None
    tax_included: bool | None = None
    vat_rate: Number | None = Field(default=None, ge=0, le=100)
    discount_percent: Number = Field(default=Decimal("0"), ge=0, le=100)
    conversion_factor: Number | None = Field(default=None, gt=0)
    conversion_to: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    confidence: dict[str, float] = Field(default_factory=dict)
    source: SourceRef | None = None
    field_sources: dict[str, SourceRef] = Field(default_factory=dict)

    @field_validator("unit", "conversion_to", mode="before")
    @classmethod
    def units(cls, value: Any) -> str | None:
        return normalize_unit(value)

    @field_validator("currency", mode="before")
    @classmethod
    def currencies(cls, value: Any) -> str | None:
        return normalize_currency(value)

    @field_validator("confidence")
    @classmethod
    def confidences(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not 0 <= v <= 1 for v in value.values()):
            raise ValueError("Уверенность должна быть от 0 до 1")
        return value

    @model_validator(mode="after")
    def conversion_pair(self) -> "Item":
        if (self.conversion_factor is None) != (self.conversion_to is None):
            raise ValueError("Для перевода единиц нужны conversion_factor и conversion_to")
        return self


class Document(Model):
    id: str = Field(min_length=1, max_length=200)
    type: DocType
    number: str | None = None
    contract_id: str | None = None
    date: Day | None = None
    effective_from: Day | None = None
    deadline: Day | None = None
    revision: int = Field(default=1, ge=1)
    logical_id: str | None = None
    status: Literal["active", "cancelled", "superseded", "draft"] = "active"
    supersedes: str | None = None
    specification_id: str | None = None
    base_document_id: str | None = None
    replacement_spec_id: str | None = None
    approved: bool = False
    related_delivery_ids: list[str] = Field(default_factory=list)
    acceptance_mode: Literal["incremental", "cumulative"] = "incremental"
    items: list[Item] = Field(default_factory=list)
    is_complete: bool | None = None
    missing_fields: list[str] = Field(default_factory=list)
    expected_pages: int | None = Field(default=None, ge=1)
    actual_pages: int | None = Field(default=None, ge=0)
    content_hash: str | None = None
    filename: str | None = None
    source: SourceRef | None = None
    extraction_method: str = "structured"
    extraction_warnings: list[str] = Field(default_factory=list)
    fields_confidence: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_lines(self) -> "Document":
        ids = [x.line_id for x in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("line_id должен быть уникальным в документе")
        if any(not 0 <= v <= 1 for v in self.fields_confidence.values()):
            raise ValueError("Уверенность должна быть от 0 до 1")
        return self


class ManualLink(Model):
    document_id: str
    line_id: str
    target_line_id: str
    comment: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)


class CheckRequest(Model):
    documents: list[Document] = Field(min_length=1, max_length=300)
    as_of: Day
    delivery_complete: bool = False
    selected_contract_id: str | None = None
    manual_links: list[ManualLink] = Field(default_factory=list)
    required_types: list[DocType] = Field(default_factory=lambda: ["contract", "delivery", "acceptance"])
    confidence_threshold: float = Field(default=0.90, ge=0.5, le=1)
    money_tolerance: Number = Field(default=Decimal("0.01"), ge=0, le=1)


class Signal(Model):
    id: str
    code: str
    severity: Literal["info", "warning", "error"] = "warning"
    title: str
    explanation: str
    rule: str
    expected: Any = None
    actual: Any = None
    sources: list[SourceRef] = Field(default_factory=list)
    position_id: str | None = None
    status: Literal["needs_review", "confirmed", "resolved", "dismissed"] = "needs_review"
    blocks_comparison: bool = False


class Match(Model):
    document_id: str
    line_id: str
    target_document_id: str | None = None
    target_line_id: str | None = None
    state: Literal["matched", "needs_review", "unmatched"]
    method: str
    score: float
    candidates: list[str] = Field(default_factory=list)
    explanation: str


class PositionSummary(Model):
    line_id: str
    name: str | None = None
    unit: str | None = None
    expected_quantity: Decimal | None = None
    delivered_quantity: Decimal | None = None
    accepted_quantity: Decimal | None = None
    remaining_quantity: Decimal | None = None
    complete: bool = True
    sources: list[SourceRef] = Field(default_factory=list)


class AuditEvent(Model):
    code: str
    document_ids: list[str] = Field(default_factory=list)
    explanation: str


class CheckResult(Model):
    schema_version: str = SCHEMA_VERSION
    engine_version: str = ENGINE_VERSION
    input_hash: str
    result_hash: str = ""
    as_of: Day
    status: Literal["ok", "needs_review", "incomplete"]
    signals: list[Signal] = Field(default_factory=list)
    matches: list[Match] = Field(default_factory=list)
    positions: list[PositionSummary] = Field(default_factory=list)
    audit: list[AuditEvent] = Field(default_factory=list)
    active_document_ids: list[str] = Field(default_factory=list)
    ignored_document_ids: list[str] = Field(default_factory=list)
    applied_amendment_ids: list[str] = Field(default_factory=list)


class ExtractionResult(Model):
    document: Document
    text: str = ""
    warnings: list[str] = Field(default_factory=list)
    needs_review: bool = False


class ReviewUpdate(Model):
    status: Literal["needs_review", "confirmed", "resolved", "dismissed"]
    comment: str = Field(min_length=1, max_length=10000)
    reviewer: str = Field(min_length=1, max_length=200)
