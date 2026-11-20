"""Local, conservative document extraction.

This is a template/table parser, not a universal contract understanding model.
Unknown cells stay None. OCR is optional, local, and always requires review.
Confidence values are routing heuristics, not calibrated probabilities.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import shutil
import statistics
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import Document, ExtractionResult, Item, SourceRef
from .normalize import date_value, decimal_value, normalize_identifier, normalize_name

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_PAGES = 100
MAX_OCR_PAGES = 20
MAX_ROWS = 10000
# Below the minimum supported comparison threshold (0.50); OCR needs review even when it is lowered.
_OCR_CONFIDENCE_CAP = 0.49
SUPPORTED_EXTENSIONS = {".json", ".txt", ".csv", ".tsv", ".docx", ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
DOCUMENT_TYPES = {"contract", "specification", "delivery", "acceptance", "amendment"}


class ExtractionError(ValueError):
    """Expected extraction failure suitable for a readable API response."""


@dataclass
class Table:
    rows: list[list[str]]
    page: int | None = None
    row_numbers: list[int] = field(default_factory=list)
    ocr_confidence: float | None = None
    confidence_cap: float = 1.0


@dataclass
class Parsed:
    text: str = ""
    native_text: str = ""
    tables: list[Table] = field(default_factory=list)
    pages: int | None = None
    warnings: list[str] = field(default_factory=list)
    incomplete: bool = False
    ocr: bool = False
    ocr_confidence: float = 1.0
    method: str = "rules-text-v1"


HEADER_NAMES = {
    "line_id": ["line_id", "строка", "идентификатор строки", "номер строки", "№ п/п", "п/п", "no"],
    "position_id": ["position_id", "позиция", "номер позиции", "позиция контракта", "идентификатор позиции", "код позиции"],
    "sku": ["sku", "артикул", "код товара", "код", "номенклатурный номер"],
    "name": ["name", "наименование", "товар", "наименование товара", "наименование товара работ услуг", "описание", "номенклатура"],
    "unit": ["unit", "ед", "ед изм", "единица", "единица измерения", "единицы измерения", "океи"],
    "quantity": ["quantity", "qty", "количество", "кол во", "кол", "поставлено", "принято"],
    "unit_price": ["unit_price", "price", "цена", "цена за единицу", "цена ед", "цена руб", "цена без ндс", "цена с ндс"],
    "amount": ["amount", "total", "сумма", "стоимость", "стоимость всего", "сумма руб", "всего", "стоимость с ндс", "сумма с ндс"],
    "currency": ["currency", "валюта"],
    "vat_rate": ["vat_rate", "ндс", "ндс %", "ставка ндс", "ставка ндс %"],
    "tax_included": ["tax_included", "ндс включён", "ндс включен", "цена включает ндс"],
    "discount_percent": ["discount_percent", "скидка", "скидка %", "скидка процентов"],
    "conversion_factor": ["conversion_factor", "коэффициент перевода", "коэффициент"],
    "conversion_to": ["conversion_to", "единица перевода", "перевести в"],
}


def _key(value: str) -> str:
    return normalize_name(value.replace("_", " ").replace("%", " ").replace("№", "номер"))


HEADERS = {_key(alias): name for name, aliases in HEADER_NAMES.items() for alias in aliases}
NUMERIC_FIELDS = {"quantity", "unit_price", "amount", "vat_rate", "discount_percent", "conversion_factor"}
TYPE_PATTERNS = [
    ("amendment", r"(?:дополнительное\s+соглашение|допсоглашение|amendment)"),
    ("specification", r"(?:спецификация|specification)"),
    ("acceptance", r"(?:акт(?:\s+(?:при[её]мки|о\s+при[её]мке|сдачи|выполненных))?|acceptance)"),
    ("delivery", r"(?:товарная\s+накладная|накладная|универсальный\s+передаточный\s+документ|упд|delivery(?:\s+note)?)"),
    ("contract", r"(?:(?:государственный\s+)?контракт|договор|contract)"),
]


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return data.decode("cp1251")
        except UnicodeDecodeError as exc:
            raise ExtractionError("Не удалось прочитать текст. Сохраните файл в UTF-8.") from exc


def _cells(line: str, delimiter: str) -> list[str]:
    if delimiter == "spaces":
        return [x.strip() for x in re.split(r"\s{2,}", line.strip())]
    try:
        values = next(csv.reader([line], delimiter=delimiter))
    except csv.Error:
        return []
    if delimiter == "|":
        if values and not values[0].strip():
            values.pop(0)
        if values and not values[-1].strip():
            values.pop()
    return [x.strip() for x in values]


def _header_map(cells: list[str]) -> dict[int, str]:
    return {i: HEADERS[_key(v)] for i, v in enumerate(cells) if _key(v) in HEADERS}


def _is_header(cells: list[str]) -> bool:
    names = set(_header_map(cells).values())
    return len(names) >= 3 and "quantity" in names and bool(names & {"name", "sku", "position_id"})


def _text_tables(text: str, page: int | None = None, *, ocr_confidence: float | None = None) -> list[Table]:
    tables: list[Table] = []
    lines = text.splitlines()
    current: Table | None = None
    delimiter: str | None = None
    for row_number, line in enumerate(lines, 1):
        if not line.strip():
            # Keep a header through a blank line, but do not swallow prose later.
            continue
        possible = [";", "\t", "|", ",", "spaces"]
        found = next(((sep, _cells(line, sep)) for sep in possible if _is_header(_cells(line, sep))), None)
        if found:
            delimiter, cells = found
            current = Table(rows=[cells], page=page, row_numbers=[row_number], ocr_confidence=ocr_confidence)
            tables.append(current)
            continue
        if current is None or delimiter is None:
            continue
        cells = _cells(line, delimiter)
        if all(re.fullmatch(r"[-: ]*", cell) for cell in cells):
            continue  # Markdown separator, never a line item.
        if len(cells) < 2:
            current = None
            continue
        current.rows.append(cells)
        current.row_numbers.append(row_number)
    return tables


def _extract_docx(path: Path) -> Parsed:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 3000 or sum(x.file_size for x in entries) > 50 * 1024 * 1024:
                raise ExtractionError("DOCX превышает допустимый распакованный размер.")
        from docx import Document as WordDocument
        doc = WordDocument(str(path))
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError("Не удалось открыть DOCX. Проверьте целостность файла.") from exc
    paragraphs = [p.text for p in doc.paragraphs]
    tables = [Table([[c.text.strip() for c in row.cells] for row in t.rows]) for t in doc.tables]
    text = "\n".join(paragraphs + ["\n".join(" | ".join(row) for row in t.rows) for t in tables])
    return Parsed(text=text, tables=tables or _text_tables(text), method="rules-docx-v1")


def _ocr(path: Path, language: str) -> tuple[str, float, list[str]]:
    if not re.fullmatch(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*", language):
        raise ExtractionError("Недопустимое имя языка OCR.")
    if not shutil.which("tesseract"):
        return "", 0.0, ["OCR_UNAVAILABLE: Tesseract не установлен. Распознавание скана требует проверки."]
    try:
        langs = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=10, check=True)
        available = set((langs.stdout + "\n" + langs.stderr).splitlines())
        missing = [x for x in language.split("+") if x not in available]
        if missing:
            return "", 0.0, [f"OCR_LANGUAGE_MISSING: не установлены языки {', '.join(missing)}. Установите соответствующие traineddata; русский текст не распознан."]
        result = subprocess.run(
            ["tesseract", str(path), "stdout", "-l", language, "--psm", "6", "tsv"],
            capture_output=True, text=True, timeout=45, check=True,
        )
    except subprocess.TimeoutExpired:
        return "", 0.0, ["OCR_TIMEOUT: превышено время распознавания страницы; требуется ручной ввод."]
    except (OSError, subprocess.CalledProcessError):
        return "", 0.0, ["OCR_FAILED: распознать скан не удалось; требуется ручной ввод."]
    reader = csv.DictReader(io.StringIO(result.stdout), delimiter="\t")
    tsv_columns = {"level", "page_num", "block_num", "par_num", "line_num", "word_num",
                   "left", "top", "width", "height", "conf", "text"}
    if not tsv_columns.issubset(reader.fieldnames or []):
        return "", 0.0, ["OCR_FAILED: Tesseract вернул некорректный TSV. Проверьте его конфигурацию, в том числе configs/tsv в каталоге tessdata (TESSDATA_PREFIX); требуется ручной ввод."]
    groups: dict[tuple[str, ...], list[dict[str, str]]] = {}
    confidences = []
    for word in reader:
        if not (word.get("text") or "").strip():
            continue
        try:
            confidence = float(word.get("conf", "-1")) / 100.0
            if confidence >= 0:
                confidences.append(confidence)
            key = tuple(word.get(k, "") for k in ("page_num", "block_num", "par_num", "line_num"))
            groups.setdefault(key, []).append(word)
        except (ValueError, TypeError):
            continue
    lines = []
    for words in groups.values():
        words.sort(key=lambda w: int(w.get("left", "0")))
        widths = [int(w.get("width", "0")) / max(1, len(w["text"])) for w in words]
        typical = statistics.median(widths) if widths else 8
        line = ""
        right = 0
        for i, w in enumerate(words):
            left = int(w.get("left", "0"))
            gap = left - right
            line += ("\t" if i and gap > max(12, typical * 2.5) else " " if i else "") + w["text"]
            right = left + int(w.get("width", "0"))
        lines.append(line)
    # Mean OCR word confidence is only an extraction heuristic; routing is capped below.
    mean_conf = sum(confidences) / len(confidences) if confidences else 0.0
    return "\n".join(lines), mean_conf, ["OCR_REVIEW_REQUIRED: значения из скана нужно сверить с оригиналом; оценка OCR не является вероятностью правильности."]


def _extract_image(path: Path, language: str) -> Parsed:
    try:
        from PIL import Image
        with Image.open(path) as im:
            if im.width * im.height > 40_000_000:
                raise ExtractionError("Изображение превышает 40 млн пикселей.")
            if getattr(im, "n_frames", 1) > 1:
                raise ExtractionError("Многостраничный TIFF нужно преобразовать в PDF или отдельные изображения.")
            im.verify()
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError("Изображение повреждено или имеет неподдерживаемый формат.") from exc
    text, confidence, warnings = _ocr(path, language)
    return Parsed(text=text, tables=_text_tables(text, 1, ocr_confidence=confidence), pages=1, warnings=warnings,
                  ocr=True, ocr_confidence=confidence, method="tesseract-local-v1")


def _pdf_columns(row: Any) -> tuple[tuple[float, float], ...] | None:
    if not row.cells or any(cell is None for cell in row.cells):
        return None
    return tuple((cell[0], cell[2]) for cell in row.cells)


def _same_pdf_columns(first: tuple[tuple[float, float], ...] | None,
                      second: tuple[tuple[float, float], ...] | None) -> bool:
    if not first or not second or len(first) != len(second):
        return False
    # Allow a shifted page margin, not a different distribution of column widths.
    offset = second[0][0] - first[0][0]
    return all(
        abs(x0 + offset - y0) <= 2 and abs(x1 + offset - y1) <= 2
        for (x0, x1), (y0, y1) in zip(first, second)
    )


def _extract_pdf(path: Path, language: str) -> Parsed:
    try:
        import pdfplumber
        pdf = pdfplumber.open(path)
    except Exception as exc:
        raise ExtractionError("Не удалось открыть PDF: файл повреждён, зашифрован или не является PDF.") from exc
    parsed = Parsed(method="rules-pdfplumber-v1")
    texts = []
    native_texts = []
    ocr_confidences = []
    ocr_pages = 0
    previous_table = None
    unverified_pages = []
    try:
        with pdf:
            parsed.pages = len(pdf.pages)
            if parsed.pages > MAX_PAGES:
                raise ExtractionError(f"PDF содержит больше {MAX_PAGES} страниц. Разделите комплект.")
            with tempfile.TemporaryDirectory(prefix="kontrol-ocr-") as tmp:
                for n, page in enumerate(pdf.pages, 1):
                    text = page.extract_text() or ""
                    native_texts.append(text)
                    usable_tables = []
                    unrecognized_table = False
                    for table_index, pdf_table in enumerate(page.find_tables()):
                        rows = [[str(c or "").strip() for c in row] for row in pdf_table.extract()]
                        if not any(any(row) for row in rows):
                            continue
                        physical_rows = pdf_table.rows
                        header_indexes = [i for i, row in enumerate(rows) if _is_header(row)]
                        if header_indexes:
                            usable_tables.append(Table(rows, page=n))
                            header_index = header_indexes[-1]
                            header = rows[header_index]
                            mapping = _header_map(header)
                            columns = _pdf_columns(physical_rows[header_index])
                            previous_table = (
                                (n, header, columns)
                                if len(mapping) == len(set(mapping.values())) and columns is not None
                                else None
                            )
                            if any(any(row) for row in rows[:header_indexes[0]]):
                                unrecognized_table = True
                            continue
                        columns_match = (
                            previous_table is not None
                            and previous_table[0] == n - 1
                            and table_index == 0
                            and bool(physical_rows)
                            and all(_same_pdf_columns(previous_table[2], _pdf_columns(row)) for row in physical_rows)
                        )
                        if columns_match:
                            header = previous_table[1]
                            usable_tables.append(Table(
                                rows=[header, *rows], page=n,
                                row_numbers=[1, *range(1, len(rows) + 1)], confidence_cap=0.85,
                            ))
                            previous_table = (n, header, previous_table[2])
                            parsed.incomplete = True
                            parsed.warnings.append(
                                f"PDF_TABLE_CONTINUATION_REVIEW: страница {n}, шапка таблицы восстановлена "
                                "по совпадению столбцов с предыдущей страницей; подтвердите строки по оригиналу."
                            )
                        else:
                            unrecognized_table = True
                    if unrecognized_table:
                        parsed.incomplete = True
                        parsed.warnings.append(
                            f"PDF_TABLE_UNRECOGNIZED: страница {n} содержит таблицу или строки без надёжной шапки; "
                            "проверьте все позиции по оригиналу."
                        )
                    usable_tables = usable_tables or _text_tables(text, n)
                    if page.images:
                        parsed.ocr = True
                        ocr_pages += 1
                        parsed.warnings.append(
                            f"PDF_IMAGE_CONTENT_UNVERIFIED: страница {n} содержит изображение; "
                            "проверьте, что все таблицы и строки извлечены."
                        )
                        for table in usable_tables:
                            table.confidence_cap = 0.85
                        if ocr_pages > MAX_OCR_PAGES:
                            parsed.warnings.append(f"OCR_PAGE_LIMIT: страница {n} не обработана; лимит {MAX_OCR_PAGES} страниц OCR.")
                        elif not shutil.which("pdftoppm"):
                            parsed.warnings.append(f"OCR_UNAVAILABLE: для страницы {n} нужен pdftoppm из Poppler.")
                        else:
                            prefix = Path(tmp) / f"page-{n}"
                            try:
                                subprocess.run(["pdftoppm", "-f", str(n), "-l", str(n), "-r", "160", "-scale-to", "2500", "-singlefile", "-png", str(path), str(prefix)],
                                               capture_output=True, timeout=30, check=True)
                                ocr_text, confidence, warnings = _ocr(prefix.with_suffix(".png"), language)
                                ocr_confidences.append(confidence)
                                parsed.warnings.extend(f"Страница {n}: {w}" for w in warnings)
                                if ocr_text.strip():
                                    text = "\n".join(part for part in (text, ocr_text) if part)
                                    ocr_tables = _text_tables(ocr_text, n, ocr_confidence=confidence)
                                    # Full-page OCR can repeat native rows; never merge the two blindly.
                                    if usable_tables and ocr_tables:
                                        parsed.warnings.append(
                                            f"PDF_OCR_TABLES_UNMERGED: страница {n} содержит текстовые и OCR-таблицы; "
                                            "проверьте все строки вручную, чтобы исключить пропуски и повторы."
                                        )
                                    elif not usable_tables:
                                        usable_tables = ocr_tables
                            except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
                                parsed.warnings.append(f"OCR_FAILED: страницу {n} не удалось преобразовать в изображение.")
                    elif text.strip() and not usable_tables and not unrecognized_table:
                        unverified_pages.append(n)
                    if not text.strip():
                        parsed.warnings.append(f"EMPTY_PAGE: страница {n} не содержит извлечённого текста; проверьте полноту.")
                    texts.append(text)
                    parsed.tables.extend(usable_tables)
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError("Ошибка чтения страниц PDF; проверьте исходный файл.") from exc
    if parsed.tables and parsed.pages and parsed.pages > 1:
        for n in unverified_pages:
            parsed.incomplete = True
            parsed.warnings.append(
                f"PDF_PAGE_TABLE_UNVERIFIED: страница {n} не содержит распознанной таблицы; "
                "проверьте, нет ли на ней продолжения позиций."
            )
    parsed.text = "\n\f\n".join(texts)
    parsed.native_text = "\n\f\n".join(native_texts)
    if parsed.ocr:
        parsed.method = "rules-pdfplumber+tesseract-v1"
        parsed.ocr_confidence = min(ocr_confidences, default=0.0)
    return parsed


def _detect_type(text: str) -> str | None:
    # First document title wins; a later "Контракт №" is only a reference.
    for line in text.splitlines()[:30]:
        for name, pattern in TYPE_PATTERNS:
            if re.match(r"\s*" + pattern + r"(?:\s|№|:|$)", line, re.I):
                return name
    return None


def _parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    v = normalize_name(str(value))
    if v in {"да", "yes", "true", "1", "включен", "включены", "с ндс"}:
        return True
    if v in {"нет", "no", "false", "0", "не включен", "без ндс"}:
        return False
    return None


def _metadata_from_text(text: str, dtype: str) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    result: dict[str, Any] = {}
    defaults: dict[str, Any] = {"currency": None}
    warnings: list[str] = []
    lines = text.splitlines()
    # Explicit labelled values avoid taking a bank-account date or party ID as a contract ID.
    labels = {
        "номер документа": "number", "document number": "number",
        "контракт": "contract_id", "номер контракта": "contract_id", "contract id": "contract_id", "contract_id": "contract_id",
        "дата": "date", "дата документа": "date", "date": "date",
        "срок поставки": "deadline", "deadline": "deadline",
        "действует с": "effective_from", "дата вступления в силу": "effective_from", "effective_from": "effective_from",
        "версия": "revision", "revision": "revision", "статус": "status", "status": "status",
        "заменяет": "supersedes", "supersedes": "supersedes", "logical_id": "logical_id",
        "спецификация id": "specification_id", "specification_id": "specification_id",
        "новая спецификация id": "replacement_spec_id", "replacement_spec_id": "replacement_spec_id",
        "основание id": "base_document_id", "base_document_id": "base_document_id",
        "подтверждено": "approved", "approved": "approved",
        "ожидается страниц": "expected_pages", "expected_pages": "expected_pages",
    }
    for line in lines:
        m = re.match(r"^\s*([^:;|\t]{2,60}):\s*(.+?)\s*$", line)
        if not m:
            continue
        label, value = m.groups()
        key = labels.get(label.strip().lower())
        if key:
            result[key] = value
        label = normalize_name(label)
        if label in {"цены включают ндс", "цена включает ндс", "ндс включен", "tax included"}:
            defaults["tax_included"] = _parse_bool(value)
            if defaults["tax_included"] is None:
                warnings.append("Неоднозначно указано, включён ли НДС в цену.")
        elif label in {"ставка ндс", "vat rate"}:
            defaults["vat_rate"] = value.rstrip("% ")
        elif label in {"валюта", "currency"}:
            defaults["currency"] = value
        elif label in {"скидка", "discount percent"}:
            defaults["discount_percent"] = value.rstrip("% ")
    # Parse the title number/date only from the document's own title.
    pattern = dict(TYPE_PATTERNS)[dtype]
    for line in lines:
        if re.match(r"\s*" + pattern + r"(?:\s|№|:|$)", line, re.I):
            number_match = re.search(r"(?:№|\bno\.?\s|\bnumber\s)\s*([^\s;,]+)", line, re.I)
            if number_match:
                result.setdefault("number", number_match.group(1).rstrip("."))
            date_match = re.search(r"(?:\bот\s+|\bdated\s+)(\d{2}[./]\d{2}[./]\d{4}|\d{4}-\d{2}-\d{2})", line, re.I)
            if date_match:
                result.setdefault("date", date_match.group(1))
            break
    ref = re.search(r"(?:^|\n)\s*(?:Контракт|Договор|Contract)\s*(?:№|No\.?|:|ID:)\s*([^\s;,]+)", text, re.I)
    if ref:
        result.setdefault("contract_id", ref.group(1))
    if dtype == "contract" and result.get("number"):
        result.setdefault("contract_id", result["number"])
    if result.get("contract_id"):
        result["contract_id"] = normalize_identifier(result["contract_id"])
    if result.get("number"):
        result["number"] = normalize_identifier(result["number"])
    status_names = {"действующий": "active", "действующая": "active", "активный": "active", "отменён": "cancelled", "отменен": "cancelled", "аннулирован": "cancelled", "заменён": "superseded", "заменен": "superseded", "черновик": "draft"}
    if "status" in result:
        result["status"] = status_names.get(str(result["status"]).lower(), result["status"])
    for key in ("date", "deadline", "effective_from"):
        if key in result:
            try:
                result[key] = date_value(result[key])
            except ValueError:
                warnings.append(f"Не удалось однозначно распознать {key}: {result[key]}")
                result.pop(key)
    for key in ("revision", "expected_pages"):
        if key in result:
            try:
                value = int(str(result[key]))
                if value < 1:
                    raise ValueError()
                result[key] = value
            except ValueError:
                warnings.append(f"Некорректное значение {key}; требуется подтверждение.")
                result.pop(key)
    if "approved" in result:
        result["approved"] = _parse_bool(result["approved"]) is True
    for key in ("vat_rate", "discount_percent"):
        if key in defaults:
            try:
                number = decimal_value(defaults[key])
                if number is None or not 0 <= number <= 100:
                    raise ValueError()
                defaults[key] = number
            except ValueError:
                warnings.append(f"Неоднозначное общее значение {key}; проверьте исходник.")
                defaults[key] = None
    return result, defaults, warnings


def _items(parsed: Parsed, doc_id: str, filename: str, defaults: dict[str, Any]) -> tuple[list[Item], list[str]]:
    items: list[Item] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    for table_index, table in enumerate(parsed.tables, 1):
        mapping: dict[int, str] = {}
        ambiguous_columns: set[str] = set()
        price_included_from_header: bool | None = None
        price_header_source: SourceRef | None = None
        for row_index, cells in enumerate(table.rows, 1):
            if _is_header(cells):
                mapping = _header_map(cells)
                names = list(mapping.values())
                ambiguous_columns = {name for name in names if names.count(name) > 1}
                if ambiguous_columns:
                    warnings.append("Неоднозначные столбцы таблицы: " + ", ".join(sorted(ambiguous_columns)) + "; значения требуют выбора пользователем.")
                for i, fieldname in mapping.items():
                    if fieldname == "unit_price":
                        label = normalize_name(cells[i])
                        if "без ндс" in label:
                            price_included_from_header = False
                        elif "с ндс" in label:
                            price_included_from_header = True
                        if table.ocr_confidence is not None and ("без ндс" in label or "с ндс" in label):
                            header_row = table.row_numbers[row_index - 1] if table.row_numbers else row_index
                            price_header_source = SourceRef(
                                document_id=doc_id, filename=filename, page=table.page, row=header_row,
                                field="tax_included", excerpt=" | ".join(cells)[:1800],
                            )
                continue
            if not mapping or not any(c.strip() for c in cells):
                continue
            if normalize_name(cells[0]).startswith(("итого", "всего к оплате", "total")):
                continue
            actual_row = table.row_numbers[row_index - 1] if table.row_numbers else row_index
            source = SourceRef(document_id=doc_id, filename=filename, page=table.page,
                               row=actual_row, excerpt=" | ".join(cells)[:1800])
            values: dict[str, Any] = dict(defaults)
            if price_included_from_header is not None:
                values["tax_included"] = price_included_from_header
            confidence: dict[str, float] = {}
            fields_sources: dict[str, SourceRef] = {}
            if parsed.ocr:
                # Document-wide defaults may come from OCR, including on another PDF page.
                for key, value in defaults.items():
                    if value is not None:
                        confidence[key] = min(parsed.ocr_confidence, _OCR_CONFIDENCE_CAP)
                        fields_sources[key] = SourceRef(document_id=doc_id, filename=filename, field=key)
            if table.ocr_confidence is not None:
                # An unrecognized identifier column is not proof that the identifier is absent.
                for key in ("position_id", "sku"):
                    confidence[key] = 0.0
                    fields_sources[key] = source.model_copy(update={"field": key})
                if price_included_from_header is not None:
                    confidence["tax_included"] = min(table.ocr_confidence, _OCR_CONFIDENCE_CAP)
                    if price_header_source is not None:
                        fields_sources["tax_included"] = price_header_source
            for col, key in mapping.items():
                value = cells[col].strip() if col < len(cells) else ""
                fields_sources[key] = source.model_copy(update={"field": key})
                base_conf = min(table.confidence_cap, min(_OCR_CONFIDENCE_CAP, table.ocr_confidence) if table.ocr_confidence is not None else 0.99)
                confidence[key] = base_conf
                if not value or value in {"—", "–", "-", "?", "не указано"}:
                    values[key] = None
                    confidence[key] = 0.0
                    continue
                if key in NUMERIC_FIELDS:
                    value = value.rstrip("% ")
                    try:
                        number = decimal_value(value)
                        if number is not None and (number < 0 or (key in {"vat_rate", "discount_percent"} and number > 100) or (key == "conversion_factor" and number <= 0)):
                            raise ValueError()
                        if table.ocr_confidence is not None and table.ocr_confidence < 0.60:
                            raise ValueError()
                        values[key] = number
                    except ValueError:
                        values[key] = None
                        confidence[key] = 0.0
                        warnings.append(f"Строка {actual_row}, {key}: неоднозначное значение «{value}»; нужен ручной ввод.")
                elif key == "tax_included":
                    values[key] = _parse_bool(value)
                    if values[key] is None:
                        confidence[key] = 0.0
                        warnings.append(f"Строка {actual_row}: не установлен налоговый базис цены.")
                else:
                    values[key] = value
            for key in ambiguous_columns:
                values[key] = None
                confidence[key] = 0.0
            if not any(values.get(k) for k in ("name", "sku", "position_id")):
                warnings.append(f"Строка {actual_row}: не удалось установить товар, строка не включена в сверку.")
                continue
            line_id = str(values.get("line_id") or f"t{table_index}-r{row_index}")
            if line_id in seen_ids:
                warnings.append(f"Повтор идентификатора строки {line_id}; строки сохранены раздельно для проверки.")
                line_id = f"{line_id}__t{table_index}-r{row_index}"
            seen_ids.add(line_id)
            values["line_id"] = line_id
            if "discount_percent" in values and values["discount_percent"] is None:
                # Invalid discount is not silently turned into zero: block price routing.
                values["discount_percent"] = 0
                confidence["unit_price"] = 0.0
                warnings.append(f"Строка {actual_row}: скидка неизвестна, сравнение цены заблокировано.")
            if bool(values.get("conversion_to")) != (values.get("conversion_factor") is not None):
                values["conversion_to"] = None
                values["conversion_factor"] = None
                warnings.append(f"Строка {actual_row}: перевод единиц не подтверждён, коэффициент не применён.")
            values.update(source=source, confidence=confidence, field_sources=fields_sources)
            try:
                items.append(Item.model_validate(values))
            except ValidationError as exc:
                warnings.append(f"Строка {actual_row}: данные не прошли проверку структуры ({exc.errors()[0]['loc']}); требуется ручной ввод.")
            if len(items) > MAX_ROWS:
                raise ExtractionError(f"Документ содержит больше {MAX_ROWS} позиций.")
    return items, warnings


def _decorate_json(document: Document, path: Path, digest: str) -> Document:
    # A local JSON is structured input, never proof of the original PDF page location.
    values = document.model_dump()
    values.update(filename=path.name, content_hash=digest, extraction_method="structured-json-v1")
    if document.source is None:
        values["source"] = SourceRef(document_id=document.id, filename=path.name, field="document", excerpt=f"Структурированный документ {document.number or document.id}")
    decorated = []
    for n, item in enumerate(document.items, 1):
        if item.source is None:
            item = item.model_copy(update={"source": SourceRef(document_id=document.id, filename=path.name, row=n,
                                                               field=f"items[{n-1}]", excerpt=json.dumps(item.model_dump(mode="json", exclude={"source", "field_sources"}), ensure_ascii=False)[:1800])})
        decorated.append(item)
    values["items"] = decorated
    return Document.model_validate(values)


def extract_document(path: Path, *, document_type: str | None = None,
                     contract_id: str | None = None,
                     metadata: dict | None = None) -> ExtractionResult:
    """Read one local file and return a reviewable normalized document.

    ``metadata`` may specify document fields (for example ``number``, ``date``,
    ``expected_pages``) and the optional OCR setting ``ocr_language``. Explicit
    metadata is treated as a human-provided correction, not extracted evidence.
    ``items``, ``id``, ``content_hash`` and source fields cannot be overwritten.
    """
    path = Path(path)
    if not path.is_file():
        raise ExtractionError("Файл не найден.")
    size = path.stat().st_size
    if size == 0:
        raise ExtractionError("Файл пуст.")
    if size > MAX_FILE_BYTES:
        raise ExtractionError("Размер файла превышает 25 МиБ.")
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ExtractionError("Неподдерживаемый формат. Используйте JSON, TXT, CSV, TSV, DOCX, PDF, PNG, JPEG или TIFF.")
    if document_type is not None and document_type not in DOCUMENT_TYPES:
        raise ExtractionError("Неизвестный тип документа.")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    explicit = dict(metadata or {})
    language = str(explicit.pop("ocr_language", "rus+eng"))
    forbidden = {"items", "id", "filename", "content_hash", "source", "extraction_method", "fields_confidence", "extraction_warnings", "actual_pages"}
    unknown = set(explicit) - set(Document.model_fields)
    if unknown or set(explicit) & forbidden:
        raise ExtractionError("Недопустимые поля metadata: " + ", ".join(sorted(unknown | (set(explicit) & forbidden))))
    if document_type is not None:
        explicit["type"] = document_type
    if contract_id is not None:
        explicit["contract_id"] = normalize_identifier(contract_id)
    if ext == ".json":
        try:
            raw = json.loads(_read_text(path))
            if isinstance(raw, dict) and "document" in raw:
                raw = raw["document"]
            if not isinstance(raw, dict):
                raise ExtractionError("JSON должен содержать один объект Document.")
            raw = {**raw, **explicit}
            document = _decorate_json(Document.model_validate(raw), path, digest)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ExtractionError(f"JSON не соответствует схеме Document: {str(exc)[:700]}") from exc
        warnings = list(document.extraction_warnings)
        return ExtractionResult(document=document, text=_read_text(path), warnings=warnings,
                                needs_review=document.is_complete is False or bool(warnings) or any(v < 0.90 for item in document.items for v in item.confidence.values()))
    if ext in {".txt", ".csv", ".tsv"}:
        text = _read_text(path)
        parsed = Parsed(text=text, tables=_text_tables(text), method="rules-delimited-v1")
    elif ext == ".docx":
        parsed = _extract_docx(path)
    elif ext == ".pdf":
        parsed = _extract_pdf(path, language)
    else:
        parsed = _extract_image(path, language)
    dtype = explicit.get("type") or _detect_type(parsed.native_text or parsed.text) or _detect_type(parsed.text)
    if dtype not in DOCUMENT_TYPES:
        detail = " ".join(parsed.warnings)
        raise ExtractionError("Не удалось определить тип документа. Укажите document_type. " + detail)
    doc_id = "doc-" + digest[:20]
    extracted, defaults, warnings = _metadata_from_text(parsed.text, dtype)
    if parsed.ocr and parsed.native_text.strip():
        native_values, native_defaults, native_warnings = _metadata_from_text(parsed.native_text, dtype)
        extracted.update(native_values)
        defaults.update({key: value for key, value in native_defaults.items() if key != "currency" or value is not None})
        warnings.extend(native_warnings)
    warnings = parsed.warnings + warnings
    items, item_warnings = _items(parsed, doc_id, path.name, defaults)
    warnings.extend(item_warnings)
    values = {**extracted, **explicit}
    missing = list(values.pop("missing_fields", []))
    for key in ("number", "contract_id", "date"):
        if not values.get(key):
            missing.append(key)
    if not items and dtype != "amendment":
        missing.append("items")
        warnings.append("TABLE_NOT_RECOGNIZED: таблица позиций не распознана. Добавьте структурированные строки после проверки оригинала.")
    for item in items:
        for key in ("quantity", "unit"):
            if getattr(item, key) is None:
                missing.append(f"items.{item.line_id}.{key}")
        if dtype in {"contract", "specification", "delivery"} and item.unit_price is None:
            missing.append(f"items.{item.line_id}.unit_price")
    expected = values.get("expected_pages")
    if expected is not None:
        try:
            expected = int(expected)
        except (ValueError, TypeError) as exc:
            raise ExtractionError("expected_pages должен быть положительным целым числом.") from exc
        if expected < 1:
            raise ExtractionError("expected_pages должен быть положительным целым числом.")
        if parsed.pages is None:
            missing.append("pages")
            warnings.append("PAGE_COUNT_UNVERIFIED: формат не содержит достоверной нумерации страниц; подтвердите полноту вручную.")
        elif expected != parsed.pages:
            missing.append("pages")
            warnings.append(f"PAGE_COUNT_MISMATCH: ожидается {expected} страниц, получено {parsed.pages}.")
    if parsed.ocr:
        warnings.append(
            "OCR_REVIEW_REQUIRED: откройте оригинал и на каждой странице посимвольно сверьте номера документа и "
            "контракта, даты, номера позиций и артикулы (в том числе нераспознанные), наименования, единицы, "
            "количества, цены, суммы, валюту и НДС. Распознанные строки и фрагменты — не изображение оригинала."
        )
    if explicit:
        warnings.append("METADATA_OVERRIDE: часть реквизитов задана пользователем; эти значения не являются результатом распознавания.")
    source = SourceRef(document_id=doc_id, filename=path.name, page=1 if parsed.pages else None,
                       excerpt=parsed.text[:1800])
    complete = not missing and not parsed.ocr and not parsed.incomplete and not any("EMPTY_PAGE" in w or "не включена" in w or "не прошли" in w for w in warnings)
    if explicit.get("is_complete") is False:
        complete = False
    confidence = {key: min(parsed.ocr_confidence, _OCR_CONFIDENCE_CAP) if parsed.ocr else 0.99
                  for key in ("number", "contract_id", "date") if values.get(key)}
    if parsed.ocr:
        confidence.update({key: min(parsed.ocr_confidence, _OCR_CONFIDENCE_CAP)
                           for key in extracted if key not in explicit})
        if "type" not in explicit:
            confidence["type"] = min(parsed.ocr_confidence, _OCR_CONFIDENCE_CAP)
    values.update(id=doc_id, type=dtype, items=items, filename=path.name, content_hash=digest,
                  source=source, actual_pages=parsed.pages, extraction_method=parsed.method,
                  extraction_warnings=list(dict.fromkeys(warnings)), missing_fields=list(dict.fromkeys(missing)),
                  is_complete=complete, fields_confidence=confidence)
    try:
        document = Document.model_validate(values)
    except ValidationError as exc:
        raise ExtractionError(f"Реквизиты документа требуют исправления: {str(exc)[:700]}") from exc
    needs_review = not complete or bool(warnings) or any(v < 0.90 for item in items for v in item.confidence.values())
    return ExtractionResult(document=document, text=parsed.text, warnings=list(dict.fromkeys(warnings)), needs_review=needs_review)
