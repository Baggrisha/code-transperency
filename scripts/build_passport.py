"""Create the editable project-passport section from the reviewed Markdown."""
from __future__ import annotations

import json
import re
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent


def text_run(paragraph, text: str):
    # No italic or decorative formatting; code tokens remain editable text.
    paragraph.add_run(text.replace("`", ""))


def main():
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    section.top_margin = section.bottom_margin = Inches(.72)
    section.left_margin = section.right_margin = Inches(.82)
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    normal.font.color.rgb = RGBColor(0, 0, 0)
    normal.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    normal.paragraph_format.line_spacing = 1
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.first_line_indent = Inches(.25)
    normal.paragraph_format.widow_control = True
    for key, size in (("Title", 19), ("Heading 1", 13), ("Heading 2", 11.5)):
        st = doc.styles[key]
        st.font.name = "Calibri"
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = RGBColor(0, 0, 0)
        st.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        st.paragraph_format.first_line_indent = Inches(0)
        st.paragraph_format.line_spacing = 1
        st.paragraph_format.space_before = Pt(8 if key != "Title" else 0)
        st.paragraph_format.space_after = Pt(4)
        st.paragraph_format.keep_with_next = True
    # The bundled Word template carries a blue Title border. Remove it.
    for border in list(doc.styles.element.xpath(".//w:pBdr")):
        border.getparent().remove(border)
    for border in list(doc.element.xpath(".//w:pBdr")):
        border.getparent().remove(border)
    doc.core_properties.title = "Техническое описание системы Контроль исполнения"
    doc.core_properties.subject = "Раздел для паспорта проекта и результаты технической проверки"
    doc.core_properties.author = "Команда проекта"
    doc.core_properties.keywords = "Контроль исполнения, техническое описание, сверка документов"

    source = (ROOT / "docs" / "passport.md").read_text(encoding="utf-8")
    blocks = re.split(r"\n\s*\n", source.strip())
    for block in blocks:
        if block.startswith("# "):
            p = doc.add_paragraph("Техническое описание системы Контроль исполнения", "Title")
            meta = doc.add_paragraph("Версия 0.1.0 от 18 ноября 2026 года")
            meta.paragraph_format.first_line_indent = Inches(0)
            meta.paragraph_format.space_after = Pt(4)
            meta.runs[0].font.size = Pt(10)
        elif block.startswith("## "):
            doc.add_paragraph(block[3:], "Heading 1")
        else:
            text_run(doc.add_paragraph(), " ".join(block.splitlines()))

    doc.add_page_break()
    doc.add_paragraph("Протокол проверочных сценариев", "Heading 1")
    p = doc.add_paragraph()
    text_run(p, "Двенадцать сценариев воспроизводят ожидаемое поведение из раздела 9 исходного исследования. Все входы синтетические. T12 проверяет реакцию на вручную заданную неоднозначность OCR; он не измеряет качество распознавания изображения.")
    report = json.loads((ROOT / "reports" / "acceptance.json").read_text(encoding="utf-8"))
    table = doc.add_table(rows=1, cols=3)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    widths = [Inches(.6), Inches(4.9), Inches(1.1)]
    for column, width in zip(table.columns, widths):
        column.width = width
    for cell, text, width in zip(table.rows[0].cells, ("Кейс", "Проверка", "Результат"), widths):
        cell.width = width
        cell.text = text
    for case in report["cases"]:
        cells = table.add_row().cells
        for cell, value, width in zip(cells, (case["id"], case["title"], "Пройден" if case["passed"] else "Ошибка"), widths):
            cell.width = width
            cell.text = value
    for i, row in enumerate(table.rows):
        row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        if i == 0:
            row._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
        for cell in row.cells:
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            props = cell._tc.get_or_add_tcPr()
            borders = OxmlElement("w:tcBorders")
            for side in ("top", "left", "bottom", "right"):
                el = OxmlElement("w:" + side)
                el.set(qn("w:val"), "single")
                el.set(qn("w:sz"), "4")
                el.set(qn("w:color"), "D9D9D9")
                borders.append(el)
            props.append(borders)
            margins = OxmlElement("w:tcMar")
            for side in ("top", "bottom", "left", "right"):
                el = OxmlElement("w:" + side)
                el.set(qn("w:w"), "90")
                el.set(qn("w:type"), "dxa")
                margins.append(el)
            props.append(margins)
            if i == 0:
                shade = OxmlElement("w:shd")
                shade.set(qn("w:fill"), "E5EBF2")
                props.append(shade)
            for paragraph in cell.paragraphs:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                paragraph.paragraph_format.first_line_indent = Inches(0)
                paragraph.paragraph_format.line_spacing = 1
                for run in paragraph.runs:
                    run.font.size = Pt(10)
                    run.bold = i == 0
    doc.add_paragraph("Материалы для воспроизведения", "Heading 1")
    p = doc.add_paragraph()
    text_run(p, "В архиве находятся исходный код, инструкции запуска, схема API, примеры документов, зафиксированный эталон T01–T12 и полный журнал автоматических проверок. Отчёт reports/acceptance.json содержит входной хеш набора, версии правил и полученные результаты. Файл reports/build_manifest.json позволяет проверить состав переданной версии.")
    p = doc.add_paragraph()
    text_run(p, "Исходные требования: предоставленное исследование «финал финал(1).pdf», разделы 7–10, страницы 8–12, и перечень задач разработки. Копия исследования включена в архив как docs/requirements_source.pdf.")
    destination = ROOT / "docs" / "technical_description.docx"
    doc.save(destination)
    print(destination)


if __name__ == "__main__":
    main()
