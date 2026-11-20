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

    def test_labeled_and_explicit_identifiers_drop_numero_before_normalization(self):
        path = self.write(
            "numero.csv",
            "Накладная\nНомер документа: № К-1\nКонтракт: № К-1\nДата: 20.10.2026\n"
            "Артикул;Наименование;Количество;Ед. изм.;Цена\nA1;Стул;1;шт;100\n",
        )
        extracted = extract_document(path)
        self.assertEqual(extracted.document.number, "К-1")
        self.assertEqual(extracted.document.contract_id, "К-1")
        overridden = extract_document(path, contract_id="№ К-1")
        self.assertEqual(overridden.document.contract_id, "К-1")

    def _draw_pdf_table(self, pdf, rows, widths=None, x=40):
        from reportlab.lib import colors
        from reportlab.platypus import Table, TableStyle

        table = Table(rows, colWidths=widths or [80, 120, 75, 65, 80], rowHeights=22)
        table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
        table.wrapOn(pdf, 500, 500)
        table.drawOn(pdf, x, 600)

    def _pdf_with_first_table(self, filename):
        from reportlab.pdfgen import canvas

        path = self.root / filename
        pdf = canvas.Canvas(str(path))
        pdf.drawString(40, 780, "Delivery No N-2 dated 2026-10-20")
        pdf.drawString(40, 760, "Contract: GK-2026-001")
        pdf.drawString(40, 740, "Currency: RUB")
        pdf.drawString(40, 720, "Tax included: yes")
        self._draw_pdf_table(pdf, [
            ["sku", "name", "quantity", "unit", "price"],
            ["A1", "Bench", "1", "piece", "100"],
        ])
        pdf.showPage()
        return path, pdf

    def _draw_scanned_table(self, pdf):
        from PIL import Image, ImageDraw
        from reportlab.lib.utils import ImageReader

        image = Image.new("RGB", (800, 160), "white")
        draw = ImageDraw.Draw(image)
        draw.text((15, 20), "sku     name     quantity     unit     price", fill="black")
        draw.text((15, 60), "B2      Chair    2            piece    200", fill="black")
        pdf.drawImage(ImageReader(image), 40, 450, width=420, height=84)

    def test_pdf_headerless_table_continuation_preserves_second_page(self):
        path, pdf = self._pdf_with_first_table("continuation.pdf")
        self._draw_pdf_table(pdf, [["B2", "Chair", "2", "piece", "200"]])
        pdf.save()

        result = extract_document(path)
        self.assertEqual([item.sku for item in result.document.items], ["A1", "B2"])
        second = result.document.items[1]
        self.assertEqual(second.quantity, Decimal("2"))
        self.assertEqual(second.unit_price, Decimal("200"))
        self.assertEqual((second.source.page, second.source.row), (2, 1))
        self.assertEqual(second.field_sources["quantity"].page, 2)
        self.assertLess(second.confidence["quantity"], 0.90)
        self.assertFalse(result.document.is_complete)
        self.assertTrue(result.needs_review)
        self.assertTrue(any("PDF_TABLE_CONTINUATION_REVIEW" in w for w in result.warnings))

    def test_pdf_continuation_can_have_shifted_left_margin(self):
        path, pdf = self._pdf_with_first_table("shifted-continuation.pdf")
        self._draw_pdf_table(pdf, [["B2", "Chair", "2", "piece", "200"]], x=70)
        pdf.save()

        result = extract_document(path)
        self.assertEqual([item.sku for item in result.document.items], ["A1", "B2"])
        self.assertEqual(result.document.items[1].source.page, 2)
        self.assertFalse(result.document.is_complete)
        self.assertTrue(any("PDF_TABLE_CONTINUATION_REVIEW" in w for w in result.warnings))

    def test_pdf_repeated_header_keeps_both_pages_complete(self):
        path, pdf = self._pdf_with_first_table("repeated-header.pdf")
        self._draw_pdf_table(pdf, [
            ["sku", "name", "quantity", "unit", "price"],
            ["B2", "Chair", "2", "piece", "200"],
        ])
        pdf.save()

        result = extract_document(path)
        self.assertEqual([item.sku for item in result.document.items], ["A1", "B2"])
        self.assertEqual((result.document.items[1].source.page, result.document.items[1].source.row), (2, 2))
        self.assertTrue(result.document.is_complete)
        self.assertFalse(result.needs_review)

    def test_pdf_headerless_table_with_changed_columns_is_not_guessed(self):
        path, pdf = self._pdf_with_first_table("changed-columns.pdf")
        self._draw_pdf_table(pdf, [["B2", "Chair", "2", "piece", "200"]], [95, 105, 75, 65, 80])
        pdf.save()

        result = extract_document(path)
        self.assertEqual([item.sku for item in result.document.items], ["A1"])
        self.assertFalse(result.document.is_complete)
        self.assertTrue(result.needs_review)
        self.assertTrue(any("PDF_TABLE_UNRECOGNIZED" in w for w in result.warnings))

    def test_pdf_header_is_not_carried_across_an_unrelated_page(self):
        path, pdf = self._pdf_with_first_table("nonadjacent-table.pdf")
        pdf.drawString(40, 700, "Separate page between tables")
        pdf.showPage()
        self._draw_pdf_table(pdf, [["B2", "Chair", "2", "piece", "200"]])
        pdf.save()

        result = extract_document(path)
        self.assertEqual([item.sku for item in result.document.items], ["A1"])
        self.assertFalse(result.document.is_complete)
        self.assertTrue(any("PDF_TABLE_UNRECOGNIZED" in w and "3" in w for w in result.warnings))

    def test_pdf_page_without_recognized_table_is_not_silently_complete(self):
        path, pdf = self._pdf_with_first_table("unruled-continuation.pdf")
        pdf.drawString(40, 700, "B2     Chair     2     piece     200")
        pdf.save()

        result = extract_document(path)
        self.assertEqual([item.sku for item in result.document.items], ["A1"])
        self.assertFalse(result.document.is_complete)
        self.assertTrue(any("PDF_PAGE_TABLE_UNVERIFIED" in w for w in result.warnings))

    def test_unverified_page_before_a_recognized_table_is_incomplete(self):
        from reportlab.pdfgen import canvas

        path = self.root / "unverified-first-page.pdf"
        pdf = canvas.Canvas(str(path))
        pdf.drawString(40, 780, "Delivery No N-2 dated 2026-10-20")
        pdf.drawString(40, 760, "Contract: GK-2026-001")
        pdf.drawString(40, 700, "B2     Chair     2     piece     200")
        pdf.showPage()
        self._draw_pdf_table(pdf, [
            ["sku", "name", "quantity", "unit", "price"],
            ["A1", "Bench", "1", "piece", "100"],
        ])
        pdf.save()

        result = extract_document(path)
        self.assertEqual([item.sku for item in result.document.items], ["A1"])
        self.assertFalse(result.document.is_complete)
        self.assertTrue(any("PDF_PAGE_TABLE_UNVERIFIED" in w and "1" in w for w in result.warnings))

    def test_pdf_mixed_text_and_image_without_poppler_is_incomplete(self):
        path, pdf = self._pdf_with_first_table("mixed-no-poppler.pdf")
        pdf.drawString(40, 730, "Footer page two: document and signature information")
        self._draw_scanned_table(pdf)
        pdf.save()

        with patch("kontrol.extract.shutil.which", return_value=None):
            result = extract_document(path, metadata={"ocr_language": "eng"})
        self.assertEqual([item.sku for item in result.document.items], ["A1"])
        self.assertEqual(result.document.items[0].quantity, Decimal("1"))
        self.assertEqual(result.document.items[0].confidence["quantity"], 0.99)
        self.assertEqual(result.document.actual_pages, 2)
        self.assertFalse(result.document.is_complete)
        self.assertTrue(result.needs_review)
        self.assertTrue(any("PDF_IMAGE_CONTENT_UNVERIFIED" in w and "2" in w for w in result.warnings))
        self.assertTrue(any("OCR_UNAVAILABLE" in w and "2" in w for w in result.warnings))
        self.assertIn("Footer page two", result.text)

    def test_pdf_failed_ocr_keeps_native_footer_text(self):
        path, pdf = self._pdf_with_first_table("mixed-ocr-failure.pdf")
        pdf.drawString(40, 730, "Footer page two: document and signature information")
        self._draw_scanned_table(pdf)
        pdf.save()

        with patch("kontrol.extract.shutil.which", return_value="/usr/bin/tool"), \
                patch("kontrol.extract.subprocess.run"), \
                patch("kontrol.extract._ocr", return_value=("", 0.0, ["OCR_LANGUAGE_MISSING"])):
            result = extract_document(path, metadata={"ocr_language": "eng"})
        self.assertIn("Footer page two", result.text)
        self.assertEqual([item.sku for item in result.document.items], ["A1"])
        self.assertEqual(result.document.items[0].quantity, Decimal("1"))
        self.assertFalse(result.document.is_complete)
        self.assertTrue(any("OCR_LANGUAGE_MISSING" in w for w in result.warnings))

    def test_pdf_mixed_text_and_image_runs_ocr_when_available(self):
        path, pdf = self._pdf_with_first_table("mixed-with-ocr.pdf")
        pdf.drawString(40, 730, "Footer page two: document and signature information")
        self._draw_scanned_table(pdf)
        pdf.save()

        columns = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
        tsv = [columns]
        for line_number, words in enumerate([
            ["Contract:", "BAD"],
            ["sku", "name", "quantity", "unit", "price"],
            ["B2", "Chair", "2", "piece", "200"],
        ], 1):
            for index, word in enumerate(words, 1):
                tsv.append(f"5\t1\t1\t1\t{line_number}\t{index}\t{index * 140}\t0\t{len(word) * 7}\t20\t96\t{word}")

        def run(command, **kwargs):
            if command[0] == "pdftoppm":
                return subprocess.CompletedProcess(command, 0, b"", b"")
            if command[1] == "--list-langs":
                return subprocess.CompletedProcess(command, 0, "List of available languages (1):\neng\n", "")
            return subprocess.CompletedProcess(command, 0, "\n".join(tsv), "")

        with patch("kontrol.extract.shutil.which", return_value="/usr/bin/tool"), \
                patch("kontrol.extract.subprocess.run", side_effect=run) as commands:
            result = extract_document(path, metadata={"ocr_language": "eng"})
        self.assertEqual([call.args[0][0] for call in commands.call_args_list], ["pdftoppm", "tesseract", "tesseract"])
        self.assertEqual([item.sku for item in result.document.items], ["A1", "B2"])
        self.assertEqual(result.document.contract_id, "GK-2026-001")
        self.assertEqual(result.document.items[1].source.page, 2)
        self.assertEqual(result.document.items[1].quantity, Decimal("2"))
        self.assertEqual(result.document.items[0].confidence["quantity"], 0.99)
        self.assertLess(result.document.items[1].confidence["quantity"], 0.90)
        self.assertFalse(result.document.is_complete)
        self.assertTrue(result.needs_review)
        self.assertIn("Footer page two", result.text)

    def test_pdf_ocr_does_not_duplicate_a_native_table(self):
        path, pdf = self._pdf_with_first_table("mixed-same-page.pdf")
        self._draw_pdf_table(pdf, [
            ["sku", "name", "quantity", "unit", "price"],
            ["B2", "Chair", "2", "piece", "200"],
        ])
        self._draw_scanned_table(pdf)
        pdf.save()

        ocr_text = "sku\tname\tquantity\tunit\tprice\nB2\tChair\t2\tpiece\t200"
        with patch("kontrol.extract.shutil.which", return_value="/usr/bin/tool"), \
                patch("kontrol.extract.subprocess.run"), \
                patch("kontrol.extract._ocr", return_value=(ocr_text, 0.96, ["OCR_REVIEW_REQUIRED"])):
            result = extract_document(path, metadata={"ocr_language": "eng"})
        self.assertEqual([item.sku for item in result.document.items], ["A1", "B2"])
        self.assertFalse(result.document.is_complete)
        self.assertTrue(any("PDF_OCR_TABLES_UNMERGED" in w and "2" in w for w in result.warnings))

    def test_failed_pdf_ocr_does_not_discard_native_table(self):
        path, pdf = self._pdf_with_first_table("short-text-with-image.pdf")
        self._draw_pdf_table(pdf, [["sku", "name", "qty"], ["B2", "Chair", "2"]], [80, 120, 75])
        self._draw_scanned_table(pdf)
        pdf.save()

        with patch("kontrol.extract.shutil.which", return_value=None):
            result = extract_document(path, metadata={"ocr_language": "eng"})
        self.assertEqual([item.sku for item in result.document.items], ["A1", "B2"])
        self.assertEqual(result.document.items[1].source.page, 2)
        self.assertEqual(result.document.items[1].quantity, Decimal("2"))
        self.assertLess(result.document.items[1].confidence["quantity"], 0.90)
        self.assertFalse(result.document.is_complete)
        self.assertTrue(any("OCR_UNAVAILABLE" in w for w in result.warnings))

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

    def test_ocr_missing_tsv_config_is_reported_as_failure(self):
        from PIL import Image

        path = self.root / "scan.png"
        Image.new("RGB", (320, 120), "white").save(path)

        def run(command, **kwargs):
            if command[1] == "--list-langs":
                return subprocess.CompletedProcess(command, 0, "List of available languages (1):\neng\n", "")
            return subprocess.CompletedProcess(
                command, 0, "Delivery No N1\nContract: K1\n", "read_params_file: Can't open tsv\n",
            )

        with patch("kontrol.extract.shutil.which", return_value="/usr/bin/tesseract"), \
                patch("kontrol.extract.subprocess.run", side_effect=run):
            result = extract_document(path, document_type="delivery", metadata={"ocr_language": "eng"})

        self.assertEqual(result.text, "")
        self.assertFalse(result.document.is_complete)
        self.assertTrue(result.needs_review)
        self.assertEqual(result.document.items, [])
        self.assertTrue(any("OCR_FAILED" in warning and "configs/tsv" in warning for warning in result.warnings))

    def test_valid_empty_ocr_tsv_is_not_a_configuration_failure(self):
        from PIL import Image

        path = self.root / "blank.png"
        Image.new("RGB", (320, 120), "white").save(path)
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"

        def run(command, **kwargs):
            if command[1] == "--list-langs":
                return subprocess.CompletedProcess(command, 0, "List of available languages (1):\neng\n", "")
            return subprocess.CompletedProcess(command, 0, header, "")

        with patch("kontrol.extract.shutil.which", return_value="/usr/bin/tesseract"), \
                patch("kontrol.extract.subprocess.run", side_effect=run):
            result = extract_document(path, document_type="delivery", metadata={"ocr_language": "eng"})

        self.assertEqual(result.text, "")
        self.assertTrue(result.needs_review)
        self.assertFalse(result.document.is_complete)
        self.assertTrue(any("OCR_REVIEW_REQUIRED" in warning for warning in result.warnings))
        self.assertFalse(any("OCR_FAILED" in warning for warning in result.warnings))

    def test_ocr_tax_basis_from_header_points_to_header_and_needs_review(self):
        from PIL import Image

        path = self.root / "scan.png"
        Image.new("RGB", (320, 120), "white").save(path)
        text = (
            "Накладная № N-1 от 20.10.2026\nКонтракт: K1\n"
            "Артикул;Наименование;Количество;Ед. изм.;Цена с НДС\nA1;Стул;1;шт;100"
        )
        with patch("kontrol.extract._ocr", return_value=(text, 0.96, ["OCR_REVIEW_REQUIRED"])):
            result = extract_document(path, document_type="delivery")

        item = result.document.items[0]
        self.assertTrue(item.tax_included)
        self.assertLess(item.confidence["tax_included"], 0.50)
        self.assertEqual(item.field_sources["tax_included"].page, 1)
        self.assertEqual(item.field_sources["tax_included"].row, 3)
        self.assertIn("Цена с НДС", item.field_sources["tax_included"].excerpt)

    def test_ocr_identifiers_can_be_corrected_and_explicitly_confirmed(self):
        from PIL import Image, ImageDraw
        from kontrol.storage import Store

        path = self.root / "scan.png"
        image = Image.new("RGB", (320, 120), "white")
        ImageDraw.Draw(image).text((10, 10), "A1", fill="black")
        image.save(path)
        text = (
            "Накладная № N-1 от 20.10.2026\nКонтракт: K1\n"
            "Артикул;Наименование;Количество;Ед. изм.;Цена\nAl;Стул;1;шт;100"
        )
        with patch("kontrol.extract._ocr", return_value=(text, 0.96, ["OCR_REVIEW_REQUIRED"])):
            document = extract_document(path, document_type="delivery").document

        store = Store(self.root / "review-data")
        case_id = store.create_case("OCR review")["id"]
        store.add_document(case_id, document)
        corrected = document.model_copy(deep=True)
        item = corrected.items[0]
        item.sku = "A1"
        updated = store.update_document(
            case_id, document.id, corrected, 1, "reviewer", "Сверено с исходным изображением",
            confirmed_items={item.line_id: ["sku", "position_id"]},
        )["document"]["items"][0]
        self.assertEqual(updated["sku"], "A1")
        self.assertEqual(updated["confidence"]["sku"], 1.0)
        self.assertEqual(updated["confidence"]["position_id"], 1.0)
        self.assertLess(updated["confidence"]["quantity"], 0.50)
        self.assertEqual(updated["field_sources"]["sku"]["page"], 1)

    def test_ocr_identifiers_and_critical_fields_require_source_review_at_minimum_threshold(self):
        from PIL import Image, ImageDraw
        from kontrol.engine import run_check
        from kontrol.models import CheckRequest

        contract = extract_document(self.write(
            "contract.csv",
            "Contract No K1 dated 2026-10-01\nContract: K1\nCurrency: RUB\nTax included: yes\n"
            "sku;name;quantity;unit;price\nAl;Bench;10;piece;1200\n",
        )).document
        metadata_lines = [
            ["Delivery", "No", "N1", "dated", "2026-10-20"],
            ["Contract:", "K1"],
            ["Currency:", "RUB"],
            ["Tax", "included:", "yes"],
            ["VAT", "rate:", "20"],
            ["Discount", "percent:", "5"],
            ["Deadline:", "2026-11-05"],
        ]
        for has_sku in (True, False):
            with self.subTest(has_sku=has_sku):
                path = self.root / f"scan-{has_sku}.png"
                image = Image.new("RGB", (900, 300), "white")
                draw = ImageDraw.Draw(image)
                for index, line in enumerate(metadata_lines):
                    draw.text((5, 5 + index * 20), " ".join(line), fill="black")
                draw.text((5, 165), "sku   name   quantity   unit   price", fill="black")
                draw.text((5, 185), "A1    Bench  8          piece  1200", fill="black")
                image.save(path)

                headers = ["name", "quantity", "unit", "price"]
                values = ["Bench", "8", "piece", "1200"]
                if has_sku:
                    headers.insert(0, "sku")
                    values.insert(0, "Al")  # Simulate OCR confusing digit 1 with lowercase l.
                tsv = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"]
                for line_number, words in enumerate([*metadata_lines, headers, values], 1):
                    left = 0
                    for word_number, word in enumerate(words, 1):
                        width = len(word) * 7
                        tsv.append(f"5\t1\t1\t1\t{line_number}\t{word_number}\t{left}\t0\t{width}\t20\t96\t{word}")
                        left += 140 if line_number > len(metadata_lines) else width + 7

                def run(command, **kwargs):
                    if command[1] == "--list-langs":
                        return subprocess.CompletedProcess(command, 0, "List of available languages (1):\neng\n", "")
                    return subprocess.CompletedProcess(command, 0, "\n".join(tsv), "")

                with patch("kontrol.extract.shutil.which", return_value="/usr/bin/tesseract"), \
                        patch("kontrol.extract.subprocess.run", side_effect=run):
                    result = extract_document(path, metadata={"ocr_language": "eng"})

                item = result.document.items[0]
                self.assertEqual(item.sku, "Al" if has_sku else None)
                self.assertTrue(result.needs_review)
                self.assertFalse(result.document.is_complete)
                for key in ("type", "number", "contract_id", "date", "deadline"):
                    self.assertLess(result.document.fields_confidence[key], 0.50)
                for key in ("position_id", "sku", "name", "quantity", "unit_price",
                            "currency", "tax_included", "vat_rate", "discount_percent"):
                    self.assertLess(item.confidence[key], 0.50)
                self.assertEqual(item.field_sources["sku"].filename, path.name)
                self.assertEqual(item.field_sources["sku"].page, 1)
                self.assertIn("Al" if has_sku else "Bench", item.field_sources["sku"].excerpt)
                self.assertTrue(any("OCR_REVIEW_REQUIRED" in warning and "артикул" in warning
                                    and "оригинал" in warning and "страниц" in warning
                                    for warning in result.warnings))

                # Confirm document metadata and position absence, but not the OCR SKU.
                reviewed = result.document.model_copy(deep=True, update={"fields_confidence": {}, "is_complete": True})
                reviewed.items[0].confidence["position_id"] = 1.0
                reviewed.items[0].confidence["name"] = 1.0
                checked = run_check(CheckRequest(
                    documents=[contract, reviewed], as_of="2026-10-22",
                    required_types=["contract", "delivery"], confidence_threshold=0.50,
                ))
                match = next(match for match in checked.matches if match.document_id == reviewed.id)
                self.assertEqual(match.method, "sku" if has_sku else "exact_name")
                self.assertEqual(match.state, "needs_review")
                self.assertIsNone(match.target_line_id)

    @unittest.skipUnless(shutil.which("tesseract"), "Tesseract is optional")
    def test_real_english_ocr_reads_digits_but_requires_review(self):
        from PIL import Image, ImageDraw, ImageFont
        candidates = [Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"), Path("/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf"), Path("/System/Library/Fonts/Menlo.ttc")]
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

    @unittest.skipUnless(shutil.which("tesseract"), "Tesseract is optional")
    def test_real_russian_ocr_requires_review(self):
        from PIL import Image, ImageDraw, ImageFont
        candidates = [Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                      Path("/System/Library/Fonts/Supplemental/Arial.ttf")]
        font_path = next((p for p in candidates if p.exists()), None)
        langs = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True)
        if font_path is None or not {"rus", "eng"}.issubset(langs.stdout.splitlines()):
            self.skipTest("Cyrillic font or Russian/English Tesseract model unavailable")
        im = Image.new("RGB", (2200, 650), "white")
        draw = ImageDraw.Draw(im)
        font = ImageFont.truetype(str(font_path), 45)
        lines = ["Накладная № Н-001 от 20.10.2026", "Контракт: К-2026-001", "Валюта: RUB",
                 "Цены включают НДС: да", "Артикул     Наименование     Количество     Ед. изм.     Цена",
                 "A01         Скамья            8              шт           1200"]
        for n, line in enumerate(lines):
            draw.text((30, 20 + n * 90), line, fill="black", font=font)
        path = self.root / "russian_scan.png"
        im.save(path)
        result = extract_document(path, document_type="delivery")
        self.assertIn("Скамья", result.text)
        self.assertIn("1200", result.text)
        self.assertTrue(result.needs_review)
        self.assertFalse(result.document.is_complete)
        self.assertEqual(result.document.items[0].quantity, Decimal("8"))
        self.assertLess(result.document.items[0].confidence["quantity"], 0.90)


if __name__ == "__main__":
    unittest.main()
