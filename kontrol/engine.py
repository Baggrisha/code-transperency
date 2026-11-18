"""Deterministic, explainable reconciliation. No network calls or wall clock.

Rules operate on a reviewable extraction, never directly on an opaque scan.
Unknown values remain None. Reports are informational, not legal decisions.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, localcontext
from difflib import SequenceMatcher
from hashlib import sha256
import json
from typing import Any

from .models import (ENGINE_VERSION, AuditEvent, CheckRequest, CheckResult,
                     Document, Item, Match, PositionSummary, Signal, SourceRef)
from .normalize import normalize_identifier, normalize_name

ZERO = Decimal("0")
CENT = Decimal("0.01")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return sha256(_json(value).encode("utf-8")).hexdigest()


def _ordered_document(doc: Document) -> dict:
    value = doc.model_dump(mode="json")
    value["items"] = sorted(value["items"], key=lambda x: x["line_id"])
    value["related_delivery_ids"] = sorted(value["related_delivery_ids"])
    value["missing_fields"] = sorted(value["missing_fields"])
    value["extraction_warnings"] = sorted(value["extraction_warnings"])
    return value


def _business(doc: Document) -> dict:
    """Semantic re-upload fingerprint, not a proof of document authenticity."""
    value = _ordered_document(doc)
    for key in ("id", "filename", "content_hash", "source", "extraction_method", "logical_id"):
        value.pop(key, None)
    for key in ("number", "contract_id"):
        value[key] = normalize_identifier(value.get(key))
    for item in value["items"]:
        for key in ("source", "field_sources", "line_id"):
            item.pop(key, None)
        for key in ("quantity", "unit_price", "amount", "vat_rate", "discount_percent", "conversion_factor"):
            if item.get(key) is not None:
                item[key] = format(Decimal(item[key]).normalize(), "f")
        for key in ("position_id", "sku"):
            item[key] = normalize_identifier(item.get(key))
        item["name"] = normalize_name(item.get("name"))
    value["items"] = sorted(value["items"], key=_json)
    return value


def _doc_source(doc: Document, field: str | None = None) -> SourceRef:
    if doc.source:
        ref = doc.source.model_copy(deep=True)
        ref.document_id = doc.id
        if field:
            ref.field = field
        return ref
    value = getattr(doc, field, None) if field else None
    return SourceRef(document_id=doc.id, filename=doc.filename, field=field,
                     excerpt=f"{doc.type}: {doc.number or doc.id}" + (f"; {field}={value}" if field else ""))


def _item_source(doc: Document, item: Item, field: str | None = None) -> SourceRef:
    ref = item.field_sources.get(field or "") or item.source
    if ref:
        ref = ref.model_copy(deep=True)
        ref.document_id = doc.id
        if field:
            ref.field = field
        return ref
    value = getattr(item, field, None) if field else None
    return SourceRef(document_id=doc.id, filename=doc.filename,
                     row=next((i + 1 for i, x in enumerate(doc.items) if x.line_id == item.line_id), None),
                     field=field or "items",
                     excerpt=f"Строка {item.line_id}: {item.name or item.sku or 'без названия'}" +
                     (f"; {field}={value}" if field else f"; количество={item.quantity}; цена={item.unit_price}"))


class _Context:
    def __init__(self, request: CheckRequest):
        self.req = request
        self.signals: list[Signal] = []
        self.audit: list[AuditEvent] = []
        self.matches: list[Match] = []
        self.ignored: set[str] = set()
        self.active: set[str] = set()
        self.applied: list[str] = []
        self.aliases: dict[str, str] = {}
        self.blocked_types: set[str] = set()

    def signal(self, code: str, title: str, explanation: str, rule: str,
               sources: list[SourceRef], *, expected: Any = None, actual: Any = None,
               position: str | None = None, blocks: bool = False, severity: str = "warning") -> None:
        sources = sorted(sources, key=lambda s: _json(s.model_dump(mode="json")))
        payload = dict(code=code, title=title, explanation=explanation, rule=rule,
                       expected=expected, actual=actual, sources=sources,
                       position_id=position, blocks_comparison=blocks, severity=severity)
        digest_payload = {**payload, "sources": [s.model_dump(mode="json") for s in sources]}
        sid = "sig_" + _hash(digest_payload)[:20]
        if not any(s.id == sid for s in self.signals):
            self.signals.append(Signal(id=sid, **payload))

    def event(self, code: str, documents: list[str], explanation: str) -> None:
        event = AuditEvent(code=code, document_ids=sorted(set(documents)), explanation=explanation)
        if event not in self.audit:
            self.audit.append(event)

    def alias(self, value: str | None) -> str | None:
        seen: set[str] = set()
        while value in self.aliases and value not in seen:
            seen.add(value)
            value = self.aliases[value]
        return value


def _resolve_documents(ctx: _Context) -> tuple[list[Document], dict[str, Document]]:
    """Resolve re-uploads and explicit transaction revision chains conservatively."""
    groups: dict[str, list[Document]] = defaultdict(list)
    for doc in ctx.req.documents:
        groups[doc.id].append(doc)
    unique: list[Document] = []
    for did, group in sorted(groups.items()):
        signatures = {_hash(_business(d)) for d in group}
        if len(signatures) > 1:
            ctx.signal("DUPLICATE_ID_CONFLICT", "Один ID у разных документов",
                       "Под одним идентификатором переданы разные значения. Все конфликтующие записи исключены до уточнения.",
                       "ID документа должен однозначно определять его содержимое.",
                       [_doc_source(d) for d in group], blocks=True)
            ctx.ignored.add(did)
            ctx.blocked_types.update(d.type for d in group)
            continue
        doc = sorted(group, key=lambda d: _json(_ordered_document(d)))[0]
        unique.append(doc)
        if len(group) > 1:
            ctx.event("DUPLICATE_IGNORED", [did], "Повторная запись с тем же ID и содержимым учтена один раз.")

    # A claimed byte hash cannot override conflicting extracted business values.
    hash_groups: dict[str, list[Document]] = defaultdict(list)
    for doc in unique:
        if doc.content_hash:
            hash_groups[doc.content_hash].append(doc)
    bad_hash_ids: set[str] = set()
    for group in hash_groups.values():
        if len(group) > 1 and len({_hash(_business(d)) for d in group}) > 1:
            ctx.signal("CONTENT_HASH_CONFLICT", "Извлечение одного файла расходится",
                       "Одинаковому хешу файла соответствуют разные реквизиты или строки. Требуется проверить извлечение.",
                       "Одинаковые байты не должны молча давать противоречивые исходные данные.",
                       [_doc_source(d) for d in group], blocks=True)
            bad_hash_ids.update(d.id for d in group)
            ctx.blocked_types.update(d.type for d in group)
    unique = [d for d in unique if d.id not in bad_hash_ids]
    ctx.ignored.update(bad_hash_ids)

    # Only documents with an explicit number (or byte hash) can be identified
    # as semantic duplicates; identical unnamed delivery rows may be real lots.
    seen: dict[str, Document] = {}
    deduped: list[Document] = []
    for doc in sorted(unique, key=lambda d: d.id):
        signature = _hash(_business(doc)) if (doc.number or doc.content_hash) else "id:" + doc.id
        previous = seen.get(signature)
        if previous:
            ctx.aliases[doc.id] = previous.id
            ctx.ignored.add(doc.id)
            ctx.event("DUPLICATE_IGNORED", [previous.id, doc.id],
                      "Повторный документ с теми же реквизитами и значениями исключён из суммирования.")
        else:
            seen[signature] = doc
            deduped.append(doc)
    lookup = {d.id: d for d in deduped}
    active: list[Document] = []
    for doc in deduped:
        if doc.status in {"cancelled", "draft"}:
            ctx.ignored.add(doc.id)
            ctx.event("DOCUMENT_CANCELLED" if doc.status == "cancelled" else "DOCUMENT_DRAFT", [doc.id],
                      "Отменённый документ не участвует в расчётах." if doc.status == "cancelled" else "Черновик не участвует в расчётах.")
        elif doc.date and doc.date > ctx.req.as_of:
            ctx.ignored.add(doc.id)
            ctx.event("FUTURE_DOCUMENT_IGNORED", [doc.id], "Документ датирован позже даты среза.")
        elif doc.status == "superseded" and doc.type not in {"specification", "contract"}:
            ctx.ignored.add(doc.id)
            ctx.event("DOCUMENT_SUPERSEDED", [doc.id], "Документ помечен как заменённый и исключён из расчётов.")
        else:
            active.append(doc)

    transactions = {d.id: d for d in active if d.type in {"delivery", "acceptance", "contract"}}
    bad: set[str] = set()
    parents: dict[str, list[str]] = defaultdict(list)
    for doc in transactions.values():
        if not doc.supersedes:
            continue
        pid = ctx.alias(doc.supersedes)
        parent = lookup.get(pid or "")
        if not parent or parent.type != doc.type or normalize_identifier(parent.contract_id) != normalize_identifier(doc.contract_id):
            ctx.signal("DOCUMENT_VERSION_CONFLICT", "Неясная связь исправления",
                       "Исходная версия отсутствует или относится к другому типу документа/контракту. Исправление требует подтверждения.",
                       "supersedes должен указывать на исходный документ того же типа и контракта.",
                       [_doc_source(doc, "supersedes")], blocks=True)
            bad.add(doc.id)
            ctx.blocked_types.add(doc.type)
            continue
        if parent.date and doc.date and doc.date < parent.date:
            ctx.signal("DOCUMENT_VERSION_CONFLICT", "Дата исправления раньше исходной версии",
                       "Хронология версий противоречива; обе записи исключены до уточнения.",
                       "Дата исправления не должна предшествовать дате исходного документа.",
                       [_doc_source(parent, "date"), _doc_source(doc, "date")], blocks=True)
            bad.update((doc.id, parent.id))
            ctx.blocked_types.add(doc.type)
        parents[parent.id].append(doc.id)
        # Explicit cycle detection includes inactive predecessors in the lookup.
        cursor = doc
        visited: set[str] = set()
        while cursor and cursor.supersedes:
            if cursor.id in visited:
                bad.update(visited)
                ctx.blocked_types.add(doc.type)
                ctx.signal("DOCUMENT_VERSION_CONFLICT", "Цикл в истории исправлений",
                           "Версии ссылаются друг на друга по кругу. Автоматический выбор версии остановлен.",
                           "Граф supersedes должен быть ацикличным.",
                           [_doc_source(lookup[x], "supersedes") for x in sorted(visited) if x in lookup], blocks=True)
                break
            visited.add(cursor.id)
            cursor = lookup.get(ctx.alias(cursor.supersedes) or "")
    for pid, children in parents.items():
        if len(children) > 1:
            bad.update(children)
            bad.add(pid)
            ctx.blocked_types.add(lookup[pid].type)
            ctx.signal("DOCUMENT_VERSION_CONFLICT", "Несколько исправлений одной версии",
                       "Найдены конкурирующие исправления без связи между ними. Требуется выбрать действующую цепочку.",
                       "У исходной версии должна быть одна непротиворечивая цепочка действующих исправлений.",
                       [_doc_source(lookup[x]) for x in [pid] + sorted(children)], blocks=True)
    replaced = {pid for pid, children in parents.items() if any(c not in bad for c in children)}
    for pid in replaced:
        ctx.ignored.add(pid)
        ctx.event("DOCUMENT_SUPERSEDED", [pid] + parents[pid], "Исходная версия исключена; учтено явное исправление.")
    active = [d for d in active if d.id not in bad and (d.id not in replaced or d.type == "specification")]
    ctx.ignored.update(bad)
    orphan_superseded = [d for d in active if d.type == "contract" and d.status == "superseded"]
    for doc in orphan_superseded:
        ctx.signal("DOCUMENT_VERSION_CONFLICT", "Не найдена замена контракта",
                   "Контракт помечен как заменённый, но действующее исправление не найдено. Нельзя использовать эту версию как актуальную.",
                   "Статус superseded требует известной действующей замены.", [_doc_source(doc, "status")], blocks=True)
        ctx.ignored.add(doc.id)
    active = [d for d in active if d not in orphan_superseded]

    # Conflicting records bearing the same document number/date are not two lots.
    logical: dict[tuple, list[Document]] = defaultdict(list)
    for doc in active:
        if doc.type not in {"delivery", "acceptance", "contract"}:
            continue
        if doc.logical_id or doc.number:
            identity = ("logical", doc.logical_id) if doc.logical_id else ("number", normalize_identifier(doc.number), doc.date)
            logical[(doc.type, normalize_identifier(doc.contract_id), *identity)].append(doc)
    conflict: set[str] = set()
    for group in logical.values():
        if len(group) > 1:
            conflict.update(d.id for d in group)
            ctx.blocked_types.update(d.type for d in group)
            ctx.signal("DOCUMENT_VERSION_CONFLICT", "Не выбрана версия документа",
                       "Документы имеют одинаковые идентифицирующие реквизиты, но разные значения и не связаны исправлением. Суммирование остановлено.",
                       "Разные версии одного документа не являются разными поставками.",
                       [_doc_source(d) for d in group], blocks=True)
    ctx.ignored.update(conflict)
    return [d for d in active if d.id not in conflict], lookup


def _doc_integrity(ctx: _Context, doc: Document, needs_items: bool = True) -> bool:
    missing = list(doc.missing_fields)
    for field in ("number", "contract_id", "date"):
        if not getattr(doc, field):
            missing.append(field)
    if needs_items and not doc.items:
        missing.append("items")
    if doc.expected_pages is not None and (doc.actual_pages is None or doc.actual_pages < doc.expected_pages):
        missing.append("pages")
    if doc.is_complete is False:
        missing.append("document_completeness")
    if missing:
        ctx.signal("INCOMPLETE_DOCUMENT", "Не хватает данных документа",
                   "Неполные реквизиты или страницы: " + ", ".join(sorted(set(missing))) + ". Данные необходимо дополнить; итог по затронутым сравнениям не установлен.",
                   "Пустое поле и отсутствующая страница не приравниваются к нулевому значению.",
                   [_doc_source(doc)], expected="Полный документ и обязательные реквизиты", actual=sorted(set(missing)), blocks=True)
    critical_low = [f for f, c in doc.fields_confidence.items() if c < ctx.req.confidence_threshold]
    for field in sorted(critical_low):
        ctx.signal("LOW_CONFIDENCE", "Реквизит требует подтверждения",
                   f"Уверенность извлечения поля «{field}» = {doc.fields_confidence[field]:.2f}; порог = {ctx.req.confidence_threshold:.2f}. Проверьте исходный фрагмент.",
                   "Низкая уверенность извлечения блокирует связанные сравнения до ручного подтверждения.",
                   [_doc_source(doc, field)], actual=doc.fields_confidence[field], expected=ctx.req.confidence_threshold, blocks=True)
    return not missing and not critical_low


def _term_timeline(ctx: _Context, active: list[Document], lookup: dict[str, Document]) -> tuple[Document | None, list[tuple[date, Document, date | None, Document | None]], bool]:
    contracts = [d for d in active if d.type == "contract"]
    selected = ctx.alias(ctx.req.selected_contract_id)
    if selected:
        contracts = [d for d in contracts if d.id == selected or normalize_identifier(d.contract_id) == normalize_identifier(selected)]
    if len(contracts) != 1:
        ctx.signal("CONTRACT_SELECTION_REQUIRED", "Нужно выбрать один контракт",
                   "Для сверки требуется ровно один действующий контракт. Уточните комплект и выбранный идентификатор.",
                   "Одно проверочное дело сопоставляет документы одного контракта.",
                   [_doc_source(d) for d in contracts] or [_doc_source(ctx.req.documents[0])], actual=len(contracts), expected=1, blocks=True)
        return None, [], False
    contract = contracts[0]
    valid = _doc_integrity(ctx, contract, needs_items=not bool(contract.specification_id))
    terms = contract
    if contract.specification_id:
        terms = lookup.get(ctx.alias(contract.specification_id) or "")
        if not terms or terms.type != "specification" or terms.status in {"cancelled", "draft"} or (terms.date and terms.date > ctx.req.as_of):
            ctx.signal("MISSING_DOCUMENT", "Нет исходной спецификации",
                       "Ссылка контракта на спецификацию не разрешается в доступный действительный документ.",
                       "Начальная спецификация выбирается по specification_id контракта.",
                       [_doc_source(contract, "specification_id")], actual=contract.specification_id, blocks=True)
            return contract, [], False
        if normalize_identifier(terms.contract_id) != normalize_identifier(contract.contract_id):
            ctx.signal("CONTRACT_MISMATCH", "Спецификация другого контракта",
                       "Номер контракта в исходной спецификации не совпадает с выбранным контрактом.",
                       "Начальная спецификация должна относиться к выбранному контракту.",
                       [_doc_source(contract, "contract_id"), _doc_source(terms, "contract_id")], blocks=True)
            valid = False
        valid = _doc_integrity(ctx, terms) and valid
    elif not contract.items:
        ctx.signal("MISSING_DOCUMENT", "Не определена спецификация",
                   "В контракте нет позиций и явной ссылки на спецификацию. Нельзя выбирать произвольное приложение.",
                   "Для сравнения нужны позиции контракта либо specification_id.", [_doc_source(contract)], blocks=True)
        return contract, [], False
    start = contract.effective_from or contract.date or date.min
    timeline = [(start, terms, contract.deadline, None)]
    ctx.active.add(contract.id)
    ctx.active.add(terms.id)
    amendments = [d for d in active if d.type == "amendment"]
    relevant: list[Document] = []
    for amendment in sorted(amendments, key=lambda d: (d.effective_from or date.max, d.id)):
        if normalize_identifier(amendment.contract_id) != normalize_identifier(contract.contract_id):
            ctx.signal("CONTRACT_MISMATCH", "Допсоглашение другого контракта",
                       "Допсоглашение относится к другому номеру контракта и не применяется.",
                       "Изменение должно принадлежать выбранному контракту.",
                       [_doc_source(contract, "contract_id"), _doc_source(amendment, "contract_id")], blocks=True)
            ctx.ignored.add(amendment.id)
            valid = False
            continue
        if amendment.effective_from and amendment.effective_from > ctx.req.as_of:
            ctx.event("FUTURE_AMENDMENT_IGNORED", [amendment.id], "Изменение ещё не вступило в силу на дату среза.")
            ctx.ignored.add(amendment.id)
            continue
        if not amendment.approved:
            ctx.signal("AMENDMENT_UNAPPROVED", "Изменение не подтверждено",
                       "Допсоглашение загружено, но его применение не подтверждено пользователем. Условия не изменены.",
                       "approved подтверждает применение изменения в прототипе, а не его юридическую действительность.",
                       [_doc_source(amendment, "approved")], blocks=True)
            ctx.ignored.add(amendment.id)
            valid = False
            continue
        if not amendment.effective_from or not amendment.base_document_id:
            ctx.signal("AMENDMENT_INVALID", "Не хватает основания изменения",
                       "Для применения допсоглашения нужны effective_from и base_document_id. Исходные условия не заменены.",
                       "Дата действия и исходный документ определяют применимость изменения.",
                       [_doc_source(amendment)], blocks=True)
            valid = False
            ctx.ignored.add(amendment.id)
            continue
        if not _doc_integrity(ctx, amendment, needs_items=False):
            valid = False
            ctx.ignored.add(amendment.id)
            continue
        relevant.append(amendment)
    by_date: dict[date, list[Document]] = defaultdict(list)
    for amendment in relevant:
        by_date[amendment.effective_from].append(amendment)
    for effective, group in sorted(by_date.items()):
        if len(group) > 1:
            ctx.signal("AMENDMENT_CONFLICT", "Изменения вступают в силу одновременно",
                       "Порядок применения нескольких допсоглашений на одну дату не задан. Требуется согласовать одну редакцию.",
                       "Однозначная история условий необходима до количественной и ценовой сверки.",
                       [_doc_source(d) for d in group], blocks=True)
            ctx.ignored.update(d.id for d in group)
            valid = False
            continue
        amendment = group[0]
        last_start, last_spec, last_deadline, last_amend = timeline[-1]
        base = ctx.alias(amendment.base_document_id)
        allowed_bases = {contract.id, last_spec.id}
        if last_amend:
            allowed_bases.add(last_amend.id)
        if base not in allowed_bases or effective < start:
            ctx.signal("AMENDMENT_INVALID", "Изменение не связано с действующей основой",
                       "Основание допсоглашения не соответствует контракту/текущей редакции или дата действия предшествует контракту.",
                       "Изменения применяются к известной действующей основе.",
                       [_doc_source(amendment, "base_document_id")], expected=sorted(allowed_bases), actual=base, blocks=True)
            ctx.ignored.add(amendment.id)
            valid = False
            continue
        new_spec = last_spec
        if amendment.replacement_spec_id:
            new_spec = lookup.get(ctx.alias(amendment.replacement_spec_id) or "")
            if (not new_spec or new_spec.type != "specification" or new_spec.status in {"cancelled", "draft"}
                    or normalize_identifier(new_spec.contract_id) != normalize_identifier(contract.contract_id)
                    or (new_spec.date and new_spec.date > ctx.req.as_of)):
                ctx.signal("AMENDMENT_INVALID", "Нет действительной новой спецификации",
                           "Замена не найдена, отменена либо относится к другому контракту. Применение изменения остановлено.",
                           "replacement_spec_id указывает на полную новую спецификацию того же контракта.",
                           [_doc_source(amendment, "replacement_spec_id")], actual=amendment.replacement_spec_id, blocks=True)
                valid = False
                continue
            if not _doc_integrity(ctx, new_spec):
                valid = False
                continue
        if not amendment.replacement_spec_id and amendment.deadline is None:
            ctx.signal("AMENDMENT_INVALID", "Содержание изменения не задано",
                       "Нужна новая полная спецификация или новый срок. Свободный текст сам по себе не заменяет условия.",
                       "Поддерживаемые изменения задаются replacement_spec_id и/или deadline.", [_doc_source(amendment)], blocks=True)
            valid = False
            continue
        timeline.append((effective, new_spec, amendment.deadline if amendment.deadline is not None else last_deadline, amendment))
        ctx.applied.append(amendment.id)
        ctx.active.add(amendment.id)
        ctx.active.add(new_spec.id)
        ctx.event("AMENDMENT_APPLIED", [amendment.id, new_spec.id], f"Применены условия с {effective.isoformat()}; прежняя редакция сохранена для исторических проверок.")
        if new_spec.id != last_spec.id:
            ctx.event("SPECIFICATION_REPLACED", [last_spec.id, new_spec.id, amendment.id], "Количество на дату среза берётся из новой редакции; исторические цены проверяются на дату поставки.")
    if timeline[-1][1].status == "superseded":
        ctx.signal("DOCUMENT_VERSION_CONFLICT", "Не установлена текущая спецификация",
                   "Последняя применимая спецификация помечена как заменённая, но действующее изменение не привело к новой редакции.",
                   "Нельзя использовать заменённую спецификацию как текущую без известной истории изменений.",
                   [_doc_source(timeline[-1][1], "status")], blocks=True)
        valid = False
    return contract, timeline, valid


def _terms_on(timeline: list[tuple], when: date) -> tuple | None:
    eligible = [t for t in timeline if t[0] <= when]
    return eligible[-1] if eligible else None


def _deadline_source(timeline: list[tuple], when: date | None, contract: Document) -> Document:
    """Follow an inherited deadline to the document that actually supplied it."""
    owner = contract
    for effective, _specification, _deadline, amendment in timeline:
        if when is None or effective > when:
            break
        if amendment is not None and amendment.deadline is not None:
            owner = amendment
    return owner


def _match_item(ctx: _Context, doc: Document, item: Item, target: Document, *, record: bool = True, manual: bool = True) -> Item | None:
    candidates = list(sorted(target.items, key=lambda x: x.line_id))
    chosen: Item | None = None
    method = "unmatched"
    score = 0.0
    explanation = "Однозначная связь не установлена."
    candidate_ids: list[str] = []
    state = "unmatched"
    manual_links = [x for x in ctx.req.manual_links if ctx.alias(x.document_id) == doc.id and x.line_id == item.line_id] if manual else []
    if manual_links:
        targets = {x.target_line_id for x in manual_links}
        chosen = next((x for x in candidates if x.line_id in targets), None) if len(targets) == 1 else None
        if chosen:
            method, score, state = "manual", 1.0, "matched"
            explanation = "Связь подтверждена пользователем: " + sorted(manual_links, key=lambda x: (x.reviewer, x.comment))[0].comment
        else:
            method, state = "manual_conflict", "needs_review"
            explanation = "Ручная связь указывает на отсутствующую позицию или содержит разные назначения."
            candidate_ids = sorted(targets)
    else:
        pid = normalize_identifier(item.position_id)
        sku = normalize_identifier(item.sku)
        pitems = [x for x in candidates if pid and normalize_identifier(x.position_id) == pid]
        sitems = [x for x in candidates if sku and normalize_identifier(x.sku) == sku]
        if pid:
            if len(pitems) == 1 and (not sku or not pitems[0].sku or normalize_identifier(pitems[0].sku) == sku):
                chosen, method, score = pitems[0], "position_id", 1.0
            else:
                candidate_ids = sorted({x.line_id for x in pitems + sitems})
                method, state = "identifier_conflict", "needs_review"
                explanation = "Номер позиции не найден однозначно либо противоречит артикулу."
        elif sku:
            if len(sitems) == 1:
                chosen, method, score = sitems[0], "sku", 1.0
            elif len(sitems) > 1:
                method, state = "duplicate_sku", "needs_review"
                candidate_ids = [x.line_id for x in sitems]
                explanation = "Один артикул соответствует нескольким строкам спецификации."
            else:
                method, state = "unknown_sku", "unmatched"
                explanation = "Артикул отсутствует в выбранной спецификации; совпадение названия не заменяет идентификатор."
        else:
            name = normalize_name(item.name)
            exact = [x for x in candidates if name and normalize_name(x.name) == name]
            if len(exact) == 1:
                chosen, method, score = exact[0], "exact_name", 1.0
            elif len(exact) > 1:
                method, state = "ambiguous_name", "needs_review"
                candidate_ids = [x.line_id for x in exact]
                explanation = "Одинаковое название встречается в нескольких строках."
            else:
                fuzzy = sorted([(SequenceMatcher(None, name, normalize_name(x.name)).ratio(), x.line_id) for x in candidates if name and x.name], key=lambda x: (-x[0], x[1]))
                fuzzy = [x for x in fuzzy if x[0] >= .55][:3]
                if fuzzy:
                    method, state, score = "similar_name", "needs_review", round(fuzzy[0][0], 4)
                    candidate_ids = [x[1] for x in fuzzy]
                    explanation = "Похожее название даёт только предложение связи; требуется ручное подтверждение."
        if chosen:
            used_field = {"position_id": "position_id", "sku": "sku", "exact_name": "name"}[method]
            if min(item.confidence.get(used_field, 1), chosen.confidence.get(used_field, 1)) < ctx.req.confidence_threshold:
                candidate_ids = [chosen.line_id]
                chosen = None
                state = "needs_review"
                explanation = "Идентификатор/название распознаны с низкой уверенностью; подтвердите связь."
            else:
                state = "matched"
                explanation = {"position_id": "Совпадает явный номер позиции контракта.", "sku": "Совпадает уникальный артикул; различие названий не меняет связь.", "exact_name": "Точно совпадает уникальное нормализованное название."}[method]
    if record:
        ctx.matches.append(Match(document_id=doc.id, line_id=item.line_id, target_document_id=target.id,
                                 target_line_id=chosen.line_id if chosen else None, state=state,
                                 method=method, score=score, candidates=candidate_ids, explanation=explanation))
        if not chosen:
            ctx.signal("AMBIGUOUS_MATCH" if state == "needs_review" else "UNMATCHED_ITEM", "Позиция требует сопоставления",
                       explanation + " Суммы по возможным затронутым позициям не считаются полными.",
                       "Автоматическая связь требует уникального непротиворечивого идентификатора либо точного уникального названия.",
                       [_item_source(doc, item)] + [_item_source(target, x) for x in candidates if x.line_id in candidate_ids],
                       actual=item.name or item.sku, expected=candidate_ids or "Позиция спецификации", blocks=True)
    return chosen


_METRIC = {"г": ("mass", Decimal("0.001")), "кг": ("mass", Decimal("1")), "т": ("mass", Decimal("1000")),
           "мм": ("length", Decimal("0.001")), "см": ("length", Decimal("0.01")), "м": ("length", Decimal("1")),
           "мл": ("volume", Decimal("0.001")), "л": ("volume", Decimal("1"))}


def _factor(item: Item, target: Item) -> Decimal | None:
    if not item.unit or not target.unit:
        return None
    if item.unit == target.unit:
        return Decimal("1")
    if item.conversion_factor is not None and item.conversion_to == target.unit:
        return item.conversion_factor
    left, right = _METRIC.get(item.unit), _METRIC.get(target.unit)
    if left and right and left[0] == right[0]:
        return left[1] / right[1]
    return None


def _gross(item: Item) -> Decimal | None:
    if item.unit_price is None or item.tax_included is None:
        return None
    value = item.unit_price * (Decimal("1") - item.discount_percent / 100)
    if not item.tax_included:
        if item.vat_rate is None:
            return None
        value *= Decimal("1") + item.vat_rate / 100
    return value


def _low_item_fields(ctx: _Context, doc: Document, item: Item) -> set[str]:
    low = {field for field, confidence in item.confidence.items() if confidence < ctx.req.confidence_threshold}
    for field in sorted(low):
        ctx.signal("LOW_CONFIDENCE", "Значение требует подтверждения",
                   f"В строке {item.line_id} уверенность поля «{field}» = {item.confidence[field]:.2f}; порог = {ctx.req.confidence_threshold:.2f}. Проверьте исходный фрагмент.",
                   "Низкая уверенность блокирует сравнение затронутого поля, но не превращает значение в ноль.",
                   [_item_source(doc, item, field)], actual=item.confidence[field], expected=ctx.req.confidence_threshold,
                   position=item.position_id or item.line_id, blocks=True)
    return low


def _compare_price(ctx: _Context, doc: Document, item: Item, target_doc: Document, target: Item,
                   factor: Decimal | None, low: set[str], target_low: set[str]) -> None:
    fields = {"unit_price", "currency", "tax_included", "vat_rate", "discount_percent", "unit", "conversion_factor", "conversion_to"}
    if low.intersection(fields) or target_low.intersection(fields) or factor is None:
        return
    if not item.currency or not target.currency:
        ctx.signal("PRICE_BASIS_UNKNOWN", "Не определена валюта цены",
                   "Без валюты нельзя считать цены сопоставимыми.", "Валюты обеих цен должны быть известны.",
                   [_item_source(doc, item, "currency"), _item_source(target_doc, target, "currency")], blocks=True)
        return
    if item.currency != target.currency:
        ctx.signal("CURRENCY_MISMATCH", "Цены указаны в разных валютах",
                   "Валюты различаются; валютный курс и правило пересчёта в этом комплекте не заданы.",
                   "Цены в разных валютах автоматически не сопоставляются.",
                   [_item_source(doc, item, "currency"), _item_source(target_doc, target, "currency")],
                   expected=target.currency, actual=item.currency, position=target.line_id, blocks=True)
        return
    gross, expected = _gross(item), _gross(target)
    if gross is None or expected is None:
        ctx.signal("PRICE_BASIS_UNKNOWN", "Не определена сопоставимая цена",
                   "Нужны цена, признак включения НДС и ставка НДС для цены без налога. Нельзя объявлять разницу завышением без этой структуры.",
                   "Сопоставимая цена = unit_price × (1 − discount_percent/100) × (1 + vat_rate/100 при tax_included=false).",
                   [_item_source(doc, item, "unit_price"), _item_source(target_doc, target, "unit_price")],
                   expected="Явная структура обеих цен", actual={"document_tax_included": item.tax_included, "contract_tax_included": target.tax_included},
                   position=target.line_id, blocks=True)
        return
    converted = gross / factor
    if abs(converted - expected) > ctx.req.money_tolerance:
        ctx.signal("PRICE_MISMATCH", "Расходится цена за единицу",
                   "После учёта НДС, скидки и единицы измерения цены различаются. Проверьте основание цены и комплект изменений.",
                   "|цена документа после нормализации − цена действующей на дату документа спецификации| > money_tolerance.",
                   [_item_source(doc, item, "unit_price"), _item_source(target_doc, target, "unit_price")],
                   expected=str(expected), actual=str(converted), position=target.line_id)
    if item.amount is not None and item.quantity is not None and not low.intersection({"amount", "quantity"}):
        expected_amount = (gross * item.quantity).quantize(CENT, rounding=ROUND_HALF_UP)
        if abs(item.amount - expected_amount) > ctx.req.money_tolerance:
            ctx.signal("AMOUNT_MISMATCH", "Не сходится сумма строки",
                       "Итоговая сумма с НДС в строке не совпадает с количеством, ценой и скидкой.",
                       "amount = ROUND_HALF_UP(quantity × сопоставимая цена за исходную единицу, 2).",
                       [_item_source(doc, item, "amount"), _item_source(doc, item, "quantity"), _item_source(doc, item, "unit_price")],
                       expected=str(expected_amount), actual=str(item.amount), position=target.line_id)


def _run(request: CheckRequest) -> CheckResult:
    ctx = _Context(request)
    payload = request.model_dump(mode="json")
    payload["documents"] = sorted((_ordered_document(d) for d in request.documents), key=_json)
    payload["manual_links"] = sorted(payload["manual_links"], key=_json)
    payload["required_types"] = sorted(set(payload["required_types"]))
    input_hash = _hash({"engine_version": ENGINE_VERSION, "request": payload})
    active, lookup = _resolve_documents(ctx)
    for link in sorted(request.manual_links, key=lambda x: (x.document_id, x.line_id, x.target_line_id)):
        linked_doc = lookup.get(ctx.alias(link.document_id) or "")
        if not linked_doc or not any(item.line_id == link.line_id for item in linked_doc.items):
            ctx.signal("INVALID_MANUAL_LINK", "Ручная связь не найдена в комплекте",
                       "Указанный документ или строка отсутствуют. Проверьте идентификаторы ручного сопоставления.",
                       "manual_links должны ссылаться на существующие строки исходных документов.",
                       [_doc_source(linked_doc or request.documents[0])],
                       actual={"document_id": link.document_id, "line_id": link.line_id}, blocks=True)
    contract, timeline, terms_valid = _term_timeline(ctx, active, lookup)
    positions: list[PositionSummary] = []
    if contract and timeline:
        _, current_spec, _, _ = timeline[-1]
        current_low = {x.line_id: _low_item_fields(ctx, current_spec, x) for x in sorted(current_spec.items, key=lambda x: x.line_id)}
        quantities: dict[str, dict[str, Decimal]] = {x.line_id: {"delivery": ZERO, "acceptance": ZERO} for x in current_spec.items}
        known: dict[str, dict[str, bool]] = {x.line_id: {"delivery": terms_valid, "acceptance": terms_valid} for x in current_spec.items}
        refs: dict[str, list[SourceRef]] = {x.line_id: [_item_source(current_spec, x, "quantity")] for x in current_spec.items}
        present: dict[str, bool] = {"delivery": False, "acceptance": False}
        transactions = [d for d in active if d.type in {"delivery", "acceptance"}]
        transaction_lookup = {d.id: d for d in transactions}
        doc_quantities: dict[str, dict[str, Decimal]] = {}
        doc_quantity_valid: dict[str, bool] = {}
        valid_ids = {d.id for d in transactions if d.type == "delivery"}
        accepted_coverage: dict[str, list[Document]] = defaultdict(list)
        for doc in transactions:
            if doc.type == "acceptance":
                for did in doc.related_delivery_ids:
                    accepted_coverage[ctx.alias(did) or did].append(doc)
        duplicate_coverage: set[str] = set()
        for did, acts in accepted_coverage.items():
            if len(acts) > 1:
                # Two partial acceptance acts can share a delivery legitimately;
                # their rows are incremental, and quantity totals catch excess.
                ctx.event("SHARED_DELIVERY_REFERENCE", [did] + [d.id for d in acts],
                          "Несколько актов ссылаются на одну поставку; суммируются только явно incremental строки приёмки.")
        for doc in sorted(transactions, key=lambda d: (d.date or date.min, d.id)):
            if normalize_identifier(doc.contract_id) != normalize_identifier(contract.contract_id):
                ctx.signal("CONTRACT_MISMATCH", "Документ другого контракта",
                           "Реквизит контракта не совпадает. Документ исключён из сумм; подтвердите принадлежность или исправьте извлечение.",
                           "contract_id всех документов должен совпадать с выбранным контрактом после нормализации.",
                           [_doc_source(contract, "contract_id"), _doc_source(doc, "contract_id")],
                           expected=contract.contract_id, actual=doc.contract_id, blocks=True)
                ctx.ignored.add(doc.id)
                for flags in known.values():
                    flags[doc.type] = False
                continue
            present[doc.type] = True
            ctx.active.add(doc.id)
            integrity = _doc_integrity(ctx, doc)
            if doc.type == "acceptance" and doc.acceptance_mode == "cumulative":
                ctx.signal("CUMULATIVE_ACCEPTANCE_UNSUPPORTED", "Накопительная приёмка требует разбиения",
                           "Акт содержит накопительный итог. Для сверки нужны incremental строки либо явная история охваченных поставок; сумма не добавлена повторно.",
                           "Накопительные и отдельные акты нельзя складывать как независимые объёмы.", [_doc_source(doc, "acceptance_mode")], blocks=True)
                integrity = False
            if doc.type == "acceptance":
                missing_refs = [did for did in doc.related_delivery_ids if ctx.alias(did) not in valid_ids]
                if missing_refs:
                    ctx.signal("MISSING_DOCUMENT", "Нет связанной накладной",
                               "Акт ссылается на отсутствующую либо исключённую накладную. Необходимо дополнить комплект или исправить связь.",
                               "related_delivery_ids должны разрешаться в действительные документы поставки.",
                               [_doc_source(doc, "related_delivery_ids")], actual=sorted(missing_refs), blocks=True)
                    integrity = False
            term = _terms_on(timeline, doc.date) if doc.date else None
            if term is None:
                ctx.signal("DOCUMENT_DATE_OUTSIDE_TERMS", "Не определены условия на дату документа",
                           "Дата отсутствует или предшествует началу действия контракта. Историческая цена и срок не сопоставляются.",
                           "Для исторической проверки нужна действующая на дату документа редакция.", [_doc_source(doc, "date")], blocks=True)
                integrity = False
            if not integrity:
                for flags in known.values():
                    flags[doc.type] = False
            doc_quantities[doc.id] = {item.line_id: ZERO for item in current_spec.items}
            doc_quantity_valid[doc.id] = integrity
            price_terms = [term] if term else []
            price_dates_unknown = False
            price_date_sources: list[SourceRef] = []
            if doc.type == "acceptance" and doc.related_delivery_ids:
                price_terms = []
                for delivery_id in sorted(set(doc.related_delivery_ids)):
                    delivery = transaction_lookup.get(ctx.alias(delivery_id) or "")
                    delivery_term = _terms_on(timeline, delivery.date) if delivery and delivery.date else None
                    delivery_integrity = bool(delivery and _doc_integrity(ctx, delivery)
                                              and normalize_identifier(delivery.contract_id) == normalize_identifier(contract.contract_id))
                    if delivery:
                        price_date_sources.append(_doc_source(delivery, "date"))
                    if delivery_term is None or not delivery_integrity:
                        price_dates_unknown = True
                    elif all(existing[1].id != delivery_term[1].id for existing in price_terms):
                        price_terms.append(delivery_term)
            elif doc.type == "acceptance" and len({t[1].id for t in timeline}) > 1:
                # Without explicit links, distinguish a known single price basis
                # from several possible historic prices. Never invent an average.
                for delivery in transactions:
                    if delivery.type != "delivery" or normalize_identifier(delivery.contract_id) != normalize_identifier(contract.contract_id):
                        continue
                    delivery_term = _terms_on(timeline, delivery.date) if delivery.date else None
                    if not delivery_term or not _doc_integrity(ctx, delivery):
                        price_dates_unknown = True
                        price_date_sources.append(_doc_source(delivery, "date"))
                    elif all(existing[1].id != delivery_term[1].id for existing in price_terms):
                        price_terms.append(delivery_term)
            date_trusted = doc.fields_confidence.get("date", 1) >= request.confidence_threshold
            deadline_owner = _deadline_source(timeline, doc.date, contract)
            deadline_trusted = deadline_owner.fields_confidence.get("deadline", 1) >= request.confidence_threshold
            if doc.type == "delivery" and term and term[2] and date_trusted and deadline_trusted and doc.date > term[2]:
                deadline_sources = [_doc_source(doc, "date"), _doc_source(deadline_owner, "deadline")]
                ctx.signal("LATE_DELIVERY", "Дата поставки позже срока",
                           "Дата документа поставки позже срока, действовавшего на эту дату. Проверьте фактическую дату передачи и основания изменений.",
                           "Дата документа поставки > deadline действующей на дату поставки редакции.", deadline_sources,
                           expected=term[2].isoformat(), actual=doc.date.isoformat())
            for item in sorted(doc.items, key=lambda x: x.line_id):
                low = _low_item_fields(ctx, doc, item)
                current = _match_item(ctx, doc, item, current_spec)
                if current is None:
                    doc_quantity_valid[doc.id] = False
                    for flags in known.values():
                        flags[doc.type] = False
                    continue
                refs[current.line_id].append(_item_source(doc, item, "quantity"))
                factor = _factor(item, current)
                if factor is None:
                    doc_quantity_valid[doc.id] = False
                    ctx.signal("UNIT_CONVERSION_REQUIRED", "Единицы нельзя сопоставить",
                               "Для этих единиц нет подтверждённого коэффициента перевода. Упаковки и штуки автоматически не приравниваются.",
                               "quantity_target = quantity_source × conversion_factor; для упаковок коэффициент должен быть задан явно.",
                               [_item_source(doc, item, "unit"), _item_source(current_spec, current, "unit")],
                               expected=current.unit, actual=item.unit, position=current.line_id, blocks=True)
                    known[current.line_id][doc.type] = False
                if item.quantity is None:
                    doc_quantity_valid[doc.id] = False
                    ctx.signal("INCOMPLETE_DOCUMENT", "Не извлечено количество",
                               "Количество в строке отсутствует. Нельзя заменить его нулём или восстановить только из суммы.",
                               "Для количественной сверки требуется подтверждённое quantity.", [_item_source(doc, item, "quantity")],
                               position=current.line_id, blocks=True)
                    known[current.line_id][doc.type] = False
                if low.intersection({"quantity", "unit", "conversion_factor", "conversion_to"}):
                    known[current.line_id][doc.type] = False
                    doc_quantity_valid[doc.id] = False
                if factor is not None and item.quantity is not None and integrity and not low.intersection({"quantity", "unit", "conversion_factor", "conversion_to"}):
                    quantities[current.line_id][doc.type] += item.quantity * factor
                    doc_quantities[doc.id][current.line_id] += item.quantity * factor
                if term and integrity and terms_valid:
                    chosen_price_term = price_terms[0] if price_terms else term
                    historical_spec = chosen_price_term[1]
                    historical = current if historical_spec.id == current_spec.id else _match_item(ctx, doc, item, historical_spec, record=False, manual=False)
                    price_bases: set[tuple] = set()
                    price_basis_missing = price_dates_unknown
                    for possible in price_terms:
                        possible_spec = possible[1]
                        possible_item = current if possible_spec.id == current_spec.id else _match_item(ctx, doc, item, possible_spec, record=False, manual=False)
                        if possible_item is None:
                            price_basis_missing = True
                        else:
                            basis_factor = _factor(possible_item, current)
                            gross_value = _gross(possible_item)
                            if basis_factor is None or gross_value is None:
                                price_basis_missing = True
                            else:
                                price_bases.add((possible_item.currency, str(gross_value / basis_factor)))
                    ambiguous_price = doc.type == "acceptance" and (len(price_bases) > 1 or price_dates_unknown)
                    if ambiguous_price:
                        if price_dates_unknown and len({t[1].id for t in timeline}) == 1 and len(price_bases) <= 1:
                            # The underlying incompleteness/low-confidence signal
                            # already explains this block. Avoid cascading a
                            # second price warning when no competing price exists.
                            ctx.event("PRICE_CHECK_BLOCKED", [doc.id] + [ref.document_id for ref in price_date_sources],
                                      "Цена акта не проверена: данные связанной поставки не подтверждены; причина указана в сигнале неполноты или низкой уверенности.")
                        else:
                            ctx.signal("PRICE_BASIS_UNKNOWN", "Не определена цена связанной поставки",
                                       "Акт охватывает поставки с разными ценовыми условиями либо даты связанных поставок не подтверждены. Разделите строки по поставкам или уточните связи; средняя цена не подставляется.",
                                       "Цена приёмки сравнивается с однозначными условиями связанных поставок, а не с поздней датой акта.",
                                       [_item_source(doc, item, "unit_price")] + [_doc_source(t[1]) for t in price_terms] + price_date_sources,
                                       expected="Однозначная цена связанных поставок", actual=sorted(price_bases), position=current.line_id, blocks=True)
                    elif historical is None or (doc.type == "acceptance" and price_basis_missing and len(price_terms) > 1):
                        ctx.signal("HISTORICAL_POSITION_UNRESOLVED", "Нет однозначной исторической позиции",
                                   "Текущая связь установлена, но цену нельзя отнести к позиции редакции на дату документа. Подтвердите историю изменения позиции.",
                                   "Цена сравнивается с исторической спецификацией, а не автоматически с последней редакцией.",
                                   [_item_source(doc, item), _doc_source(historical_spec)], position=current.line_id, blocks=True)
                    else:
                        historical_low = current_low[current.line_id] if historical_spec.id == current_spec.id else _low_item_fields(ctx, historical_spec, historical)
                        _compare_price(ctx, doc, item, historical_spec, historical, _factor(item, historical), low, historical_low)
                        for key, value in sorted(historical.attributes.items()):
                            got = item.attributes.get(key)
                            if got is None:
                                ctx.signal("ATTRIBUTE_MISSING", "Не хватает характеристики товара",
                                           f"В документе нет характеристики «{key}», предусмотренной спецификацией. Требуется проверить исходные документы.",
                                           "Отсутствие характеристики не подтверждает совпадение и не означает физическое несоответствие товара.",
                                           [_item_source(doc, item, "attributes"), _item_source(historical_spec, historical, "attributes")],
                                           expected={key: value}, actual=None, position=current.line_id, blocks=True)
                            elif normalize_name(got) != normalize_name(value):
                                ctx.signal("ATTRIBUTE_MISMATCH", "Расходится характеристика товара",
                                           f"Значение характеристики «{key}» отличается от спецификации; проверьте допустимость замены и исходные данные.",
                                           "Нормализованные явные значения одинаковой характеристики должны совпадать.",
                                           [_item_source(doc, item, "attributes"), _item_source(historical_spec, historical, "attributes")],
                                           expected={key: value}, actual={key: got}, position=current.line_id)
        # Aggregate totals alone can conceal an act attached to the wrong lot.
        # Check connected components of explicit delivery–acceptance references.
        # Shared references join acts into one component (incremental partial acts).
        graph: dict[str, set[str]] = defaultdict(set)
        for doc in transactions:
            if doc.type != "acceptance" or not doc.related_delivery_ids or doc.id not in doc_quantities:
                continue
            for delivery_id in doc.related_delivery_ids:
                did = ctx.alias(delivery_id)
                if did in doc_quantities:
                    graph[doc.id].add(did)
                    graph[did].add(doc.id)
        visited: set[str] = set()
        all_quantity_ids = set(doc_quantities)
        for start in sorted(graph):
            if start in visited:
                continue
            component: set[str] = set()
            pending = [start]
            while pending:
                node = pending.pop()
                if node in component:
                    continue
                component.add(node)
                pending.extend(graph[node] - component)
            visited.update(component)
            if component == all_quantity_ids or not all(doc_quantity_valid.get(did, False) for did in component):
                continue
            for item in sorted(current_spec.items, key=lambda x: x.line_id):
                delivered_group = sum((doc_quantities[did][item.line_id] for did in component if transaction_lookup[did].type == "delivery"), ZERO)
                accepted_group = sum((doc_quantities[did][item.line_id] for did in component if transaction_lookup[did].type == "acceptance"), ZERO)
                if accepted_group > delivered_group:
                    ctx.signal("QUANTITY_MISMATCH", "Акт превышает связанные накладные",
                               "В явно связанной группе актов принято больше, чем указано в относящихся к ней накладных. Общий итог контракта может скрывать ошибку связи; проверьте привязку актов.",
                               "В компоненте связей related_delivery_ids: Σ принято > Σ поставлено.",
                               [ref for ref in refs[item.line_id] if ref.document_id in component],
                               expected=str(delivered_group), actual=str(accepted_group), position=item.line_id)
        for dtype in ctx.blocked_types:
            if dtype in present:
                for flags in known.values():
                    flags[dtype] = False
        for dtype in ("delivery", "acceptance"):
            if not present[dtype]:
                for flags in known.values():
                    flags[dtype] = False
        for item in sorted(current_spec.items, key=lambda x: x.line_id):
            low = current_low[item.line_id]
            expected = item.quantity if terms_valid and not low.intersection({"quantity", "unit"}) else None
            if item.quantity is None:
                ctx.signal("INCOMPLETE_DOCUMENT", "Нет количества в спецификации",
                           "В действующей спецификации не задано количество позиции. Итоговые объёмы не сопоставляются с контрактом.",
                           "Отсутствующее контрактное количество не равно нулю.", [_item_source(current_spec, item, "quantity")],
                           position=item.line_id, blocks=True)
            delivered = quantities[item.line_id]["delivery"] if known[item.line_id]["delivery"] else None
            accepted = quantities[item.line_id]["acceptance"] if known[item.line_id]["acceptance"] else None
            evidence = sorted(refs[item.line_id], key=lambda r: _json(r.model_dump(mode="json")))
            complete = expected is not None and delivered is not None and accepted is not None
            remaining = max(expected - delivered, ZERO) if expected is not None and delivered is not None else None
            if delivered is not None and accepted is not None and accepted > delivered:
                ctx.signal("QUANTITY_MISMATCH", "Приёмка превышает документы поставки",
                           "В актах указано больше единиц, чем в учтённых накладных. Проверьте дополнительные накладные, частичные поставки и исправления. По текущему комплекту причина не установлена.",
                           "Σ принято по действующим incremental актам > Σ поставлено по действующим накладным.",
                           evidence, expected=str(delivered), actual=str(accepted), position=item.line_id)
            if expected is not None and delivered is not None and delivered > expected:
                ctx.signal("OVER_DELIVERY", "Документальный объём выше спецификации",
                           "Сумма действующих накладных превышает количество в редакции на дату среза. Проверьте изменения и принадлежность поставок.",
                           "Σ поставлено > количество в действующей спецификации.", evidence,
                           expected=str(expected), actual=str(delivered), position=item.line_id)
            if expected is not None and accepted is not None and accepted > expected:
                ctx.signal("OVER_ACCEPTANCE", "Объём приёмки выше спецификации",
                           "Сумма действующих актов превышает количество в редакции на дату среза. Требуется проверка комплекта.",
                           "Σ принято > количество в действующей спецификации.", evidence,
                           expected=str(expected), actual=str(accepted), position=item.line_id)
            if request.delivery_complete and expected is not None and delivered is not None and delivered < expected:
                ctx.signal("INCOMPLETE_DELIVERY", "Неполный документальный объём завершённой поставки",
                           "Пользователь отметил комплект поставки завершённым, но накладные покрывают не всё количество. Уточните полноту комплекта; это не автоматический вывод о нарушении.",
                           "delivery_complete=true и Σ поставлено < контрактное количество.", evidence,
                           expected=str(expected), actual=str(delivered), position=item.line_id)
            positions.append(PositionSummary(line_id=item.line_id, name=item.name, unit=item.unit,
                                             expected_quantity=expected, delivered_quantity=delivered,
                                             accepted_quantity=accepted, remaining_quantity=remaining,
                                             complete=complete, sources=evidence))
    actual_types = {d.type for d in active if d.id in ctx.active}
    for dtype in sorted(set(request.required_types) - actual_types):
        ctx.signal("MISSING_DOCUMENT", "В комплекте нет обязательного документа",
                   f"Не найден пригодный для проверки документ типа «{dtype}». Отсутствие файла в комплекте не означает, что его нет у стороны.",
                   "Проверяется полнота загруженного комплекта относительно required_types.",
                   [_doc_source(contract or request.documents[0])], expected=dtype, actual=None, blocks=True)
    ctx.signals.sort(key=lambda s: (s.code, s.position_id or "", s.id))
    ctx.matches.sort(key=lambda m: (m.document_id, m.line_id, m.target_document_id or ""))
    ctx.audit.sort(key=lambda e: (e.code, e.document_ids, e.explanation))
    status = "incomplete" if any(s.blocks_comparison for s in ctx.signals) else ("needs_review" if ctx.signals else "ok")
    result = CheckResult(input_hash=input_hash, as_of=request.as_of, status=status, signals=ctx.signals,
                         matches=ctx.matches, positions=positions, audit=ctx.audit,
                         active_document_ids=sorted(ctx.active), ignored_document_ids=sorted(ctx.ignored),
                         applied_amendment_ids=ctx.applied)
    result.result_hash = _hash(result.model_dump(mode="json", exclude={"result_hash"}))
    return result


def run_check(request: CheckRequest) -> CheckResult:
    """Run with a fixed Decimal context so caller settings cannot change results."""
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_UP
        return _run(request)
