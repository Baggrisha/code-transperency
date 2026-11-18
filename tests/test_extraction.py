"""Independent extraction checks on actual file bytes, including PDF/DOCX/OCR."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from kontrol.extract import ExtractionError, extract_document

ROOT = Path(__file__).resolve().parents[1]


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        p = self.root / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_real_text_contract_and_csv_decimal(self):
        result = extract_document(ROOT / "examples" / "contract.txt")
        self.assertEqual(result.document.type, "contract")
        self.assertEqual(result.document.contract_id, "GK-2026-001")
        self.assertEqual(str(result.document.deadline), "2026-11-05")
        item = result.document.items[0]
        self.assertEqual(item.quantity, Decimal("10"))
        self.assertEqual(item.unit_price, Decimal("1200.00"))
        self.assertEqual(item.amount, Decimal("12000.00"))
        self.assertEqual(item.unit, "шт")
        self.assertTrue(item.tax_included)
        self.assertTrue(result.document.is_complete)
        self.assertFalse(result.needs_review)
        self.assertIsNotNone(item.source.row)
        self.assertIsNone(item.source.page)  # A text file has lines, no original pages.
        self.assertIn("1 200,00", item.field_sources["unit_price"].excerpt)
        delivery = extract_document(ROOT / "examples" / "delivery_8.csv")
        self.assertEqual(delivery.document.type, "delivery")
        self.assertEqual(delivery.document.items[0].quantity, Decimal("8"))

    def test_supported_document_types(self):
        for filename, dtype in (("acceptance.txt", "acceptance"), ("specification.txt", "specification"), ("amendment.txt", "amendment")):
            with self.subTest(filename=filename):
                result = extract_document(ROOT / "examples" / filename)
                self.assertEqual(result.document.type, dtype)
                self.assertEqual(result.document.contract_id, "GK-2026-001")
        amendment = extract_document(ROOT / "examples" / "amendment.txt").document
        self.assertTrue(amendment.approved)
        self.assertEqual(str(amendment.effective_from), "2026-10-15")

    def test_ambiguous_quantity_is_not_zero(self):
        source = (ROOT / "examples" / "contract.txt").read_text(encoding="utf-8").replace(";10;", ";1O;")
        result = extract_document(self.write("ambiguous.txt", source))
        self.assertIsNone(result.document.items[0].quantity)
        self.assertEqual(result.document.items[0].confidence["quantity"], 0)
        self.assertFalse(result.document.is_complete)
        self.assertTrue(result.needs_review)
        self.assertIn("1O", result.document.items[0].source.excerpt)

    def test_missing_quantity_and_tax_not_invented(self):
        p = self.write("partial.csv", "Накладная № N-1\nКонтракт № GK-1\nДата: 20.10.2026\nАртикул;Наименование;Количество;Ед. изм.;Цена\nA1;Стул;;шт;1 000,00\n")
        result = extract_document(p)
        item = result.document.items[0]
        self.assertIsNone(item.quantity)
        self.assertIsNone(item.tax_included)
        self.assertIsNone(item.currency)
        self.assertFalse(result.document.is_complete)

    def test_unknown_discount_blocks_price(self):
        p = self.write("discount.txt", "Контракт № K-1\nДата: 01.10.2026\nСкидка: неизвестна\nАртикул;Наименование;Количество;Ед. изм.;Цена\nA1;Стул;1;шт;100\n")
        result = extract_document(p)
        self.assertEqual(result.document.items[0].confidence["unit_price"], 0)
        self.assertTrue(result.needs_review)

    def test_conflicting_price_columns_are_not_silently_chosen(self):
        p = self.write("columns.csv", "Контракт № K-1\nДата: 01.10.2026\nАртикул;Наименование;Количество;Ед. изм.;Цена;Цена с НДС\nA1;Стул;1;шт;100;120\n")
        result = extract_document(p)
        self.assertIsNone(result.document.items[0].unit_price)
        self.assertFalse(result.document.is_complete)
        self.assertTrue(result.needs_review)

    def test_unknown_format_and_bad_json_controlled(self):
        for name, text in (("bad.exe", "hello"), ("bad.json", "{broken"), ("empty.txt", "")):
            with self.subTest(name=name), self.assertRaises(ExtractionError):
                extract_document(self.write(name, text))

    def test_metadata_cannot_replace_source_or_rows(self):
        with self.assertRaises(ExtractionError):
            extract_document(ROOT / "examples" / "contract.txt", metadata={"items": []})

    def test_json_and_content_hash_stable(self):
        data = {"id": "J-1", "type": "delivery", "number": "N-1", "contract_id": "K-1", "date": "2026-10-01",
                "items": [{"line_id": "1", "name": "Стул", "quantity": "2", "unit": "шт", "unit_price": "100"}]}
        p = self.write("doc.json", json.dumps(data, ensure_ascii=False))
        a, b = extract_document(p), extract_document(p)
        self.assertEqual(a.document.content_hash, b.document.content_hash)
        self.assertEqual(a.model_dump(mode="json"), b.model_dump(mode="json"))
        self.assertEqual(a.document.items[0].source.document_id, "J-1")
        self.assertIsNone(a.document.items[0].source.page)

    def test_docx_table_reads_real_office_bytes(self):
        from docx import Document
        doc = Document()
        doc.add_paragraph("Накладная № N-2 от 20.10.2026")
        doc.add_paragraph("Контракт № GK-2026-001")
        doc.add_paragraph("Валюта: RUB")
        doc.add_paragraph("Цены включают НДС: да")
        rows = [["Артикул", "Наименование", "Количество", "Ед. изм.", "Цена"], ["BENCH-01", "Скамья", "8", "шт.", "1 200,00"]]
        table = doc.add_table(rows=0, cols=5)
        for row in rows:
            for cell, value in zip(table.add_row().cells, row):
                cell.text = value
        path = self.root / "invoice.docx"
        doc.save(path)
        result = extract_document(path)
        self.assertEqual(result.document.items[0].quantity, Decimal("8"))
        self.assertEqual(result.document.items[0].unit_price, Decimal("1200"))
        self.assertEqual(result.document.extraction_method, "rules-docx-v1")
        self.assertIsNone(result.document.items[0].source.page)
        self.assertFalse(result.needs_review)

    def test_pdf_table_extracts_real_pdf_page(self):
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        path = self.root / "invoice.pdf"
        styles = getSampleStyleSheet()
        table = Table([["sku", "name", "quantity", "unit", "price"], ["BENCH-01", "Bench", "8", "piece", "1200.00"]])
        table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
        flow = [Paragraph("Delivery No N-2 dated 2026-10-20", styles["Normal"]),
                Paragraph("Contract: GK-2026-001", styles["Normal"]),
                Paragraph("Currency: RUB", styles["Normal"]),
                Paragraph("Tax included: yes", styles["Normal"]), Spacer(1, 12), table]
        SimpleDocTemplate(str(path)).build(flow)
        result = extract_document(path)
        self.assertEqual(result.document.items[0].quantity, Decimal("8"))
        self.assertEqual(result.document.items[0].source.page, 1)
        self.assertEqual(result.document.actual_pages, 1)
        self.assertTrue(result.document.is_complete)
        self.assertFalse(result.needs_review)

    def test_pdf_missing_expected_page_is_incomplete(self):
        from reportlab.pdfgen import canvas
        path = self.root / "page.pdf"
        c = canvas.Canvas(str(path))
        c.drawString(30, 700, "Contract No K1 dated 2026-10-01")
        c.save()
        result = extract_document(path, metadata={"expected_pages": 2})
        self.assertFalse(result.document.is_complete)
        self.assertIn("pages", result.document.missing_fields)

    def test_non_tabular_document_does_not_pretend_success(self):
        p = self.write("prose.txt", "Контракт № K-1 от 01.10.2026\nПоставщик поставляет продукцию по отдельной спецификации.\n")
        result = extract_document(p)
        self.assertFalse(result.document.is_complete)
        self.assertEqual(result.document.items, [])
        self.assertTrue(any("TABLE_NOT_RECOGNIZED" in w for w in result.warnings))

    def test_ocr_missing_language_is_explicit(self):
        from PIL import Image
        path = self.root / "scan.png"
        Image.new("RGB", (320, 120), "white").save(path)
        with patch("kontrol.extract.shutil.which", return_value="/usr/bin/tesseract"), patch("kontrol.extract.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "List of available languages (1):\neng\n", "")
            result = extract_document(path, document_type="delivery")
        self.assertFalse(result.document.is_complete)
        self.assertTrue(any("OCR_LANGUAGE_MISSING" in w for w in result.warnings))
        self.assertEqual(result.document.items, [])

    @unittest.skipUnless(shutil.which("tesseract"), "Tesseract is optional")
    def test_real_english_ocr_reads_digits_but_requires_review(self):
        from PIL import Image, ImageDraw, ImageFont
        candidates = [Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"), Path("/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf")]
        font_path = next((p for p in candidates if p.exists()), None)
        if font_path is None:
            self.skipTest("Monospace test font unavailable")
        langs = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True)
        if "eng" not in langs.stdout.splitlines():
            self.skipTest("English Tesseract model unavailable")
        im = Image.new("RGB", (1900, 520), "white")
        draw = ImageDraw.Draw(im)
        font = ImageFont.truetype(str(font_path), 38)
        lines = ["Delivery No N1 dated 2026-10-20", "Contract: K1", "Currency: RUB", "Tax included: yes",
                 "sku      name       quantity    unit      price", "A1       Bench      8           piece     1200.00"]
        for n, line in enumerate(lines):
            draw.text((35, 25 + n * 70), line, fill="black", font=font)
        path = self.root / "english_scan.png"
        im.save(path)
        result = extract_document(path, document_type="delivery", metadata={"ocr_language": "eng"})
        self.assertIn("1200", result.text)
        self.assertIn("8", result.text)
        self.assertTrue(result.needs_review)
        self.assertFalse(result.document.is_complete)
        if result.document.items:
            self.assertLess(result.document.items[0].confidence["quantity"], 0.90)


if __name__ == "__main__":
    unittest.main()
