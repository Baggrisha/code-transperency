"""Generate the fixed synthetic acceptance fixtures; values are not real purchases."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path

CONTRACT = 'К-001/2026'


def line(document_id, quantity='10', *, name='Скамья парковая', price='1200', unit='шт', sku='BENCH-01'):
    text = f'{name}; артикул {sku}; {quantity} {unit}; цена {price} руб. с НДС 20%'
    src = {'document_id': document_id, 'filename': document_id + '.json', 'row': 1, 'excerpt': text}
    return {'line_id': '1', 'position_id': 'P-01', 'sku': sku, 'name': name,
            'unit': unit, 'quantity': quantity, 'unit_price': price,
            'currency': 'RUB', 'tax_included': True, 'vat_rate': '20',
            'source': src,
            'field_sources': {key: dict(src, field=key) for key in ('quantity', 'unit_price', 'unit')}}


def doc(id, type, quantity='10', *, number=None, date=None, **kw):
    date = date or ('2026-10-01' if type in ('contract', 'specification') else '2026-10-18' if type == 'delivery' else '2026-10-19')
    number = number or (CONTRACT if type == 'contract' else {'delivery': 'Н-001', 'acceptance': 'А-001', 'specification': 'С-001', 'amendment': 'ДС-001'}[type])
    data = {'id': id, 'type': type, 'number': number, 'contract_id': CONTRACT, 'date': date,
            'is_complete': True, 'filename': id + '.json',
            'source': {'document_id': id, 'filename': id + '.json', 'excerpt': f'{type} № {number} от {date}; контракт {CONTRACT}'},
            'items': [] if type == 'amendment' else [line(id, quantity)]}
    if type == 'contract':
        data['deadline'] = '2026-10-20'
    if type == 'acceptance':
        data['related_delivery_ids'] = ['delivery-1']
    data.update(kw)
    return data


def base():
    return {'as_of': '2026-10-31', 'selected_contract_id': 'contract', 'delivery_complete': False,
            'documents': [doc('contract', 'contract'), doc('delivery-1', 'delivery'), doc('acceptance-1', 'acceptance')]}


def expect(status='ok', required_codes=None, **kw):
    result = {'status': status, 'required_codes': required_codes or [],
              'forbidden_codes': ['OVER_DELIVERY', 'PRICE_MISMATCH', 'LATE_DELIVERY', 'INCOMPLETE_DELIVERY'],
              'allowed_codes': required_codes or [], 'delivered_quantity': '10', 'accepted_quantity': '10',
              'expected_quantity': '10'}
    result.update(kw)
    return result


def case(id, title, description, request, expected):
    return {'id': id, 'title': title, 'description': description,
            'source_requirement': 'финал финал(1).pdf, раздел 9, страница 11',
            'synthetic': True, 'request': request, 'expected': expected}


def make_cases():
    out = []
    out.append(case('T01', 'Полное совпадение', 'Контракт, накладная и акт содержат 10 штук одной позиции.', base(), expect()))

    r = base(); r['documents'][1] = doc('delivery-1', 'delivery', '8')
    out.append(case('T02', 'Акт и комплект поставки расходятся', 'В накладной 8 штук, в акте 10; дополнительных накладных нет. Требуется уточнение состава комплекта.', r,
                    expect('needs_review', ['QUANTITY_MISMATCH'], delivered_quantity='8', minimum_signal_sources=2)))

    r = base(); r['documents'][1] = doc('delivery-1', 'delivery', '8'); r['documents'].insert(2, doc('delivery-2', 'delivery', '2', number='Н-002', date='2026-10-19'))
    r['documents'][-1]['related_delivery_ids'] = ['delivery-1', 'delivery-2']
    out.append(case('T03', 'Частичные поставки складываются', 'Две отдельные накладные на 8 и 2 штуки подтверждают акт на 10.', r, expect()))

    r = base(); c = r['documents'][0]; c['items'] = []; c['specification_id'] = 'spec-v1'
    old = doc('spec-v1', 'specification', '10', effective_from='2026-10-01', logical_id='spec', revision=1)
    new = doc('spec-v2', 'specification', '12', number='С-002', date='2026-10-15', effective_from='2026-10-15', logical_id='spec', revision=2, supersedes='spec-v1')
    amendment = doc('amendment-1', 'amendment', date='2026-10-14', effective_from='2026-10-15', base_document_id='contract', replacement_spec_id='spec-v2', approved=True)
    r['documents'] = [c, old, new, amendment, doc('delivery-1', 'delivery', '12'), doc('acceptance-1', 'acceptance', '12')]
    out.append(case('T04', 'Действующая редакция спецификации', 'Допсоглашение меняет количество с 10 на 12 до поставки. Поставка и приёмка равны 12.', r,
                    expect(delivered_quantity='12', accepted_quantity='12', expected_quantity='12', applied_amendments=['amendment-1'])))

    r = base(); h = hashlib.sha256(b'synthetic acceptance T05 delivery original bytes').hexdigest(); r['documents'][1]['content_hash'] = h
    duplicate = copy.deepcopy(r['documents'][1]); duplicate['id'] = 'delivery-1-copy'; duplicate['filename'] = 'delivery-1-copy.json'
    for s in [duplicate['source'], duplicate['items'][0]['source'], *duplicate['items'][0]['field_sources'].values()]:
        s['document_id'] = 'delivery-1-copy'; s['filename'] = 'delivery-1-copy.json'
    r['documents'].append(duplicate)
    out.append(case('T05', 'Повторная загрузка', 'Тот же файл накладной загружен повторно с тем же SHA-256; количество не удваивается.', r,
                    expect(audit_codes=['DUPLICATE_IGNORED'], ignored_ids=['delivery-1-copy'])))

    r = base(); item = r['documents'][1]['items'][0]; item['name'] = 'Лавочка уличная'; item['position_id'] = None
    item['source']['excerpt'] = 'Лавочка уличная; артикул BENCH-01; 10 шт; цена 1200 руб. с НДС 20%'
    for s in item['field_sources'].values(): s['excerpt'] = item['source']['excerpt']
    out.append(case('T06', 'Разные названия при одном артикуле', 'Скамья парковая и лавочка уличная имеют один артикул BENCH-01.', r,
                    expect(required_match={'document_id': 'delivery-1', 'line_id': '1', 'state': 'matched'})))

    r = base(); r['documents'][1]['items'][0] = line('delivery-1', '2', unit='упак')
    out.append(case('T07', 'Единицы без коэффициента', 'Контракт в штуках, накладная в упаковках. Количество в упаковке не указано.', r,
                    expect('incomplete', ['UNIT_CONVERSION_REQUIRED'], delivered_quantity=None, accepted_quantity='10', blocked_codes=['UNIT_CONVERSION_REQUIRED'])))

    r = base(); old = doc('delivery-1', 'delivery', '8', status='cancelled', logical_id='delivery-logical', revision=1)
    fixed = doc('delivery-1-v2', 'delivery', '10', number='Н-001', date='2026-10-19', logical_id='delivery-logical', revision=2, supersedes='delivery-1')
    r['documents'] = [r['documents'][0], old, fixed, r['documents'][2]]; r['documents'][-1]['related_delivery_ids'] = ['delivery-1-v2']
    out.append(case('T08', 'Исправленная накладная', 'Первая версия на 8 отменена; действует исправленная версия на 10.', r,
                    expect(ignored_ids=['delivery-1'], active_ids=['delivery-1-v2'], audit_codes=['DOCUMENT_CANCELLED'])))

    r = base(); r['documents'][1].update(is_complete=False, expected_pages=2, actual_pages=1)
    out.append(case('T09', 'Неполный исходный документ', 'В загруженной накладной одна страница из заявленных двух. Таблица может быть неполной.', r,
                    expect('incomplete', ['INCOMPLETE_DOCUMENT'], delivered_quantity=None, blocked_codes=['INCOMPLETE_DOCUMENT'])))

    r = base(); item = r['documents'][1]['items'][0]; item.update(unit_price='1250', tax_included=False, discount_percent='20', vat_rate='20', amount='12000')
    item['source']['excerpt'] = '10 шт; цена без НДС 1250; скидка 20%; НДС 20%; сумма с НДС 12000'
    for s in item['field_sources'].values(): s['excerpt'] = item['source']['excerpt']
    out.append(case('T10', 'Цена с НДС и скидкой', '1250 × (1 − 20 / 100) × (1 + 20 / 100) = 1200; структура цены известна.', r, expect()))

    r = base(); r['documents'][1]['date'] = '2026-10-25'; r['documents'][2]['date'] = '2026-10-26'
    amendment = doc('amendment-1', 'amendment', date='2026-10-14', effective_from='2026-10-15', base_document_id='contract', deadline='2026-11-05', approved=True)
    r['documents'].append(amendment)
    out.append(case('T11', 'Изменённый срок поставки', 'Исходный срок 20 октября заменён на 5 ноября до поставки 25 октября.', r,
                    expect(applied_amendments=['amendment-1'])))

    r = base(); d = r['documents'][1]; d.update(extraction_method='ocr', filename='ambiguous-scan.png')
    item = d['items'][0]; item['quantity'] = '8'; item['confidence'] = {'quantity': 0.48}
    item['source'].update(filename='ambiguous-scan.png', page=1, excerpt='Количество: 8 (синтетический эталон: неоднозначная цифра 8 или 3)')
    item['field_sources']['quantity'] = dict(item['source'], field='quantity')
    out.append(case('T12', 'Неоднозначная цифра на скане', 'Синтетический эталон результата OCR задан вручную: количество 8 с уверенностью 0,48. Это проверка реакции правил, не измерение распознавания настоящего скана.', r,
                    expect('incomplete', ['LOW_CONFIDENCE'], delivered_quantity=None, blocked_codes=['LOW_CONFIDENCE'])))
    return out


if __name__ == '__main__':
    destination = Path(__file__).with_name('cases.json')
    destination.write_text(json.dumps(make_cases(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(destination)
