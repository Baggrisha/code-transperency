"""Shareable, self-contained exports; every source and review stays visible."""
from __future__ import annotations

import csv
import html
import io
import json
from pathlib import Path
from typing import Any


def safe_cell(value: Any) -> str:
    s = str(value if value is not None else "")
    return "'" + s if s.lstrip().startswith(("=", "+", "-", "@")) else s


def signals_csv(run: dict) -> str:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["ID", "Код", "Статус рассмотрения", "Заголовок", "Объяснение", "Правило",
                     "Ожидается", "В комплекте", "Источники", "Проверяющий", "Комментарий", "Актуальность"])
    reviews = {r["signal_id"]: r for r in run.get("reviews", [])}
    for s in run["result"]["signals"]:
        r = reviews.get(s["id"], {})
        writer.writerow([safe_cell(v) for v in [s["id"], s["code"], r.get("status", s["status"]), s["title"],
                          s["explanation"], s["rule"], json.dumps(s["expected"], ensure_ascii=False),
                          json.dumps(s["actual"], ensure_ascii=False), json.dumps(s["sources"], ensure_ascii=False),
                          r.get("reviewer", ""), r.get("comment", ""),
                          "Документы изменены после этой проверки" if run.get("stale") else "Снимок на момент проверки"]])
    return "\ufeff" + out.getvalue()


def report_html(run: dict) -> str:
    esc = lambda v: html.escape(str(v if v is not None else "—"))
    result = run["result"]
    reviews = {r["signal_id"]: r for r in run.get("reviews", [])}
    status_names = {"ok": "Расхождения не обнаружены в пределах выполненных проверок",
                    "needs_review": "Нужна проверка", "incomplete": "Недостаточно данных"}
    stale_notice = '<p><strong>Документы изменены после этой проверки.</strong> Этот отчёт содержит прежний снимок данных. Выполните новую сверку для актуального комплекта.</p>' if run.get("stale") else ""
    cards = []
    for signal in result["signals"]:
        s, r = signal, reviews.get(signal["id"], {})
        sources = "".join(f'<li><strong>{esc(x["document_id"])}</strong> · страница {esc(x.get("page"))} · строка {esc(x.get("row"))} · {esc(x.get("field"))}<blockquote>{esc(x.get("excerpt"))}</blockquote></li>' for x in s["sources"])
        cards.append(f'<article><small>{esc(s["code"])}</small><h2>{esc(s["title"])}</h2><p>{esc(s["explanation"])}</p>'
                     f'<p><b>Правило:</b> {esc(s["rule"])}</p><p><b>Ожидается:</b> {esc(json.dumps(s["expected"],ensure_ascii=False))}<br>'
                     f'<b>В комплекте:</b> {esc(json.dumps(s["actual"],ensure_ascii=False))}</p><ul>{sources}</ul>'
                     f'<p><b>Рассмотрение:</b> {esc(r.get("status",s["status"]))} {esc(r.get("reviewer",""))} {esc(r.get("comment",""))}</p></article>')
    rows = "".join(f'<tr><td>{esc(p["name"])}</td><td>{esc(p["unit"])}</td><td>{esc(p["expected_quantity"])}</td><td>{esc(p["delivered_quantity"])}</td><td>{esc(p["accepted_quantity"])}</td><td>{"Да" if p["complete"] else "Нет"}</td></tr>' for p in result["positions"])
    audit = "".join(f'<li>{esc(a["code"])}: {esc(a["explanation"])} ({esc(", ".join(a["document_ids"]))})</li>' for a in result["audit"])
    return f'''<!doctype html><html lang="ru"><meta charset="utf-8"><title>Отчёт сверки</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;color:#142c43;max-width:1050px;margin:40px auto;padding:0 24px}}h1{{font-size:32px}}small{{color:#54728d}}p{{text-align:justify}}article{{border:1px solid #d7e3ec;padding:20px;margin:16px 0;border-radius:12px;break-inside:avoid}}table{{border-collapse:collapse;width:100%}}td,th{{padding:10px;border-bottom:1px solid #d7e3ec;text-align:left}}blockquote{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f7fa;padding:12px;margin:8px 0}}code{{overflow-wrap:anywhere}}@media print{{body{{margin:0;font-size:11pt}}article{{border-radius:0}}}}</style>
<h1>Контроль исполнения</h1><h2>{esc(status_names[result["status"]])}</h2>{stale_notice}<p>Срез: {esc(result["as_of"])}. Версия правил: {esc(result["engine_version"])}. Запуск: {esc(run.get("id","CLI"))}.</p>
<p>Информационная сверка загруженного комплекта. Отсутствие сигнала не подтверждает полноту документов. Решение по приёмке принимает специалист.</p>
<table><thead><tr><th>Позиция</th><th>Ед.</th><th>По условиям</th><th>Поставлено</th><th>Принято</th><th>Данных достаточно</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Сигналы ({len(result["signals"])})</h2>{''.join(cards) or '<p>Сигналов нет.</p>'}<h2>История учёта документов</h2><ul>{audit}</ul>
<p><b>Хеш входа:</b> <code>{esc(result["input_hash"])}</code><br><b>Хеш результата:</b> <code>{esc(result["result_hash"])}</code></p></html>'''


FONT_DIR = Path(__file__).resolve().parent / "fonts"
STATUS_NAMES = {"ok": "Расхождения не обнаружены в пределах выполненных проверок",
                "needs_review": "Нужна проверка", "incomplete": "Недостаточно данных"}
REVIEW_NAMES = {"needs_review": "Требует рассмотрения", "confirmed": "Подтверждено",
                "resolved": "Устранено", "dismissed": "Отклонено"}


class PdfUnavailableError(RuntimeError):
    pass


def _register_fonts() -> tuple[str, str]:
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError as exc:  # pragma: no cover - зависит от окружения
        raise PdfUnavailableError("Для PDF-отчёта установите reportlab: pip install -r requirements.txt") from exc
    regular, bold = "KontrolSans", "KontrolSans-Bold"
    if regular not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(regular, str(FONT_DIR / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont(bold, str(FONT_DIR / "DejaVuSans-Bold.ttf")))
        from reportlab.lib.fonts import addMapping
        addMapping(regular, 0, 0, regular)
        addMapping(regular, 1, 0, bold)
    return regular, bold


def _compact(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _when(value: Any) -> str:
    """2026-11-19T18:40:03.789124+00:00 → 2026-11-19 18:40 UTC."""
    text = str(value or "")
    if len(text) >= 16 and text[10] == "T":
        return f"{text[:10]} {text[11:16]}" + (" UTC" if text.endswith("+00:00") else "")
    return text or "—"


def report_pdf(run: dict, case: dict | None = None) -> bytes:
    """PDF-отчёт с кириллицей (шрифт DejaVu Sans в комплекте). Содержание совпадает с HTML."""
    regular, bold = _register_fonts()
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer

    esc = lambda v: html.escape(_compact(v))
    base = ParagraphStyle("base", fontName=regular, fontSize=9.5, leading=13)
    small = ParagraphStyle("small", parent=base, fontSize=8, leading=10.5, textColor=colors.HexColor("#54728d"))
    h1 = ParagraphStyle("h1", parent=base, fontName=bold, fontSize=18, leading=23, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=base, fontName=bold, fontSize=13, leading=17, spaceBefore=10, spaceAfter=4)
    h3 = ParagraphStyle("h3", parent=base, fontName=bold, fontSize=10.5, leading=14)
    quote = ParagraphStyle("quote", parent=small, backColor=colors.HexColor("#f3f7fa"), borderPadding=4,
                           leftIndent=6, rightIndent=6, spaceBefore=3, spaceAfter=5)
    cell = ParagraphStyle("cell", parent=base, fontSize=8.5, leading=11)

    result = run["result"]
    reviews = {r["signal_id"]: r for r in run.get("reviews", [])}
    story: list = [Paragraph("Контроль исполнения — отчёт сверки", h1)]
    if case:
        story.append(Paragraph(f"Дело: <b>{esc(case.get('title'))}</b>", base))
    story += [Paragraph(f"<b>{esc(STATUS_NAMES.get(result['status'], result['status']))}</b>", h2),
              Paragraph(f"Срез: {esc(result['as_of'])} · версия правил {esc(result['engine_version'])} · "
                        f"запуск {esc(run.get('id', 'CLI'))} · создан {esc(_when(run.get('created_at')))}", small)]
    if run.get("stale"):
        story.append(Paragraph("<b>Документы изменены после этой проверки.</b> Отчёт содержит прежний снимок "
                               "данных. Выполните новую сверку для актуального комплекта.", base))
    story += [Spacer(1, 4), Paragraph("Информационная сверка загруженного комплекта. Отсутствие сигнала не "
                                      "подтверждает полноту документов. Решение по приёмке принимает специалист.", small)]

    if case and case.get("documents"):
        story.append(Paragraph("Документы комплекта", h2))
        rows = [[Paragraph(f"<b>{x}</b>", cell) for x in ("ID", "Тип", "Номер", "Дата", "Файл")]]
        for record in case["documents"]:
            d = record["document"]
            rows.append([Paragraph(esc(v), cell) for v in (d.get("id"), d.get("type"), d.get("number"),
                                                         d.get("date"), d.get("filename") or record.get("original_name"))])
        story.append(_table(rows, [50 * mm, 25 * mm, 30 * mm, 22 * mm, 47 * mm], regular))

    story.append(Paragraph("Позиции", h2))
    rows = [[Paragraph(f"<b>{x}</b>", cell) for x in ("Позиция", "Ед.", "По условиям", "Поставлено", "Принято", "Данных хватает")]]
    for p in result["positions"]:
        rows.append([Paragraph(esc(v), cell) for v in (p["name"], p["unit"], p["expected_quantity"],
                                                     p["delivered_quantity"], p["accepted_quantity"],
                                                     "Да" if p["complete"] else "Нет")])
    story.append(_table(rows, [54 * mm, 12 * mm, 24 * mm, 26 * mm, 22 * mm, 36 * mm], regular)
                 if len(rows) > 1 else Paragraph("Позиций нет.", base))

    story.append(Paragraph(f"Сигналы ({len(result['signals'])})", h2))
    if not result["signals"]:
        story.append(Paragraph("Сигналов нет.", base))
    for s in result["signals"]:
        r = reviews.get(s["id"], {})
        status = r.get("status", s["status"])
        block = [Paragraph(f"{esc(s['code'])} · {esc(s['id'])}", small), Paragraph(esc(s["title"]), h3),
                 Paragraph(esc(s["explanation"]), base),
                 Paragraph(f"<b>Правило:</b> {esc(s['rule'])}", base),
                 Paragraph(f"<b>Ожидается:</b> {esc(s['expected'])}<br/><b>В комплекте:</b> {esc(s['actual'])}", base)]
        for src in s["sources"]:
            block.append(Paragraph(f"<b>{esc(src['document_id'])}</b> · файл {esc(src.get('filename'))} · страница "
                                   f"{esc(src.get('page'))} · строка {esc(src.get('row'))} · поле {esc(src.get('field'))}", small))
            if src.get("excerpt"):
                block.append(Paragraph(esc(src["excerpt"]).replace("\n", "<br/>"), quote))
        review_text = f"<b>Рассмотрение:</b> {esc(REVIEW_NAMES.get(status, status))}"
        if r:
            review_text += f" · {esc(r.get('reviewer'))} · {esc(_when(r.get('created_at')))}<br/>{esc(r.get('comment'))}"
        block += [Paragraph(review_text, base), Spacer(1, 8)]
        story.append(KeepTogether(block[:5]))
        story += block[5:]

    history = run.get("reviews", [])
    if history:
        history_heading = Paragraph("История рассмотрения", h2)
        history_heading.keepWithNext = True
        story.append(history_heading)
        rows = [[Paragraph(f"<b>{x}</b>", cell) for x in ("Время", "Сигнал", "Статус", "Автор", "Комментарий")]]
        for r in history:
            rows.append([Paragraph(esc(v), cell) for v in (_when(r["created_at"]), r["signal_id"],
                                                         REVIEW_NAMES.get(r["status"], r["status"]), r["reviewer"], r["comment"])])
        story.append(_table(rows, [27 * mm, 50 * mm, 32 * mm, 28 * mm, 37 * mm], regular))

    story.append(Paragraph("История учёта документов", h2))
    for a in result["audit"]:
        story.append(Paragraph(f"{esc(a['code'])}: {esc(a['explanation'])} ({esc(', '.join(a['document_ids']))})", cell))
    story += [Spacer(1, 8), Paragraph(f"Хеш входа: {esc(result['input_hash'])}", small),
              Paragraph(f"Хеш результата: {esc(result['result_hash'])}", small)]

    buffer = io.BytesIO()

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(regular, 7.5)
        canvas.setFillColor(colors.HexColor("#54728d"))
        canvas.drawString(18 * mm, 10 * mm, "Контроль исполнения · информационная сверка")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"стр. {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=18 * mm, title="Отчёт сверки", author="Контроль исполнения",
                            invariant=True)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def _table(rows: list, widths: list, font: str):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle
    table = Table(rows, colWidths=widths, repeatRows=1, splitInRow=1)
    table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), font), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaf1f6")),
                               ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#d7e3ec"))]))
    return table
