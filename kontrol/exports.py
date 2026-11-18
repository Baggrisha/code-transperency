"""Shareable, self-contained exports; every source and review stays visible."""
from __future__ import annotations

import csv
import html
import io
import json
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
