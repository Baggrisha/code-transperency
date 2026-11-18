"""Small, deterministic normalizers. Unknown values stay unknown."""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


def decimal_value(value: Any) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError("Логическое значение не является числом")
    text = str(value).strip().replace("\u00a0", "").replace("\u202f", "")
    text = re.sub(r"\s+", "", text)
    if "," in text and "." in text:
        # Only unambiguous grouped decimal forms are accepted.
        if re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+\.\d+", text):
            text = text.replace(",", "")
        elif re.fullmatch(r"[+-]?\d{1,3}(?:\.\d{3})+,\d+", text):
            text = text.replace(".", "").replace(",", ".")
        else:
            raise ValueError("Неоднозначный формат числа")
    else:
        text = text.replace(",", ".")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Не удалось распознать число: {value}") from exc
    if not result.is_finite():
        raise ValueError("Число должно быть конечным")
    if abs(result) > Decimal("1e15") or result.as_tuple().exponent < -12:
        raise ValueError("Число выходит за допустимый диапазон")
    return result


def date_value(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError("Дата должна иметь формат YYYY-MM-DD или ДД.ММ.ГГГГ")


def normalize_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold().replace("ё", "е")
    return " ".join(re.findall(r"[\w]+", text, flags=re.UNICODE))


def normalize_identifier(value: str | None) -> str:
    # Preserve leading zeroes, Cyrillic/Latin distinction and separators.
    text = unicodedata.normalize("NFKC", value or "").strip().upper()
    text = text.replace("№", "").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", "", text)


UNIT_ALIASES = {
    "шт": "шт", "штука": "шт", "штуки": "шт", "штук": "шт", "796": "шт",
    "кг": "кг", "килограмм": "кг", "килограммы": "кг", "166": "кг",
    "г": "г", "грамм": "г", "163": "г", "т": "т", "тонна": "т", "168": "т",
    "л": "л", "литр": "л", "литры": "л", "112": "л", "мл": "мл",
    "м": "м", "метр": "м", "006": "м", "см": "см", "мм": "мм",
    "м2": "м2", "м²": "м2", "кв м": "м2", "м3": "м3", "м³": "м3",
    "уп": "упак", "упак": "упак", "упаковка": "упак", "упаковки": "упак",
    "компл": "компл", "комплект": "компл", "набор": "набор",
}


def normalize_unit(value: str | None) -> str | None:
    if not value:
        return None
    v = unicodedata.normalize("NFKC", value).lower().strip().rstrip(".")
    v = re.sub(r"\s+", " ", v)
    return UNIT_ALIASES.get(v, v)


def normalize_currency(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().upper().rstrip(".")
    return {"РУБ": "RUB", "РУБЛЬ": "RUB", "РУБЛИ": "RUB", "₽": "RUB", "RUR": "RUB", "643": "RUB", "$": "USD", "€": "EUR"}.get(v, v)
