import React, {useRef, useState} from 'react';
import {Action, AsyncForm, Heading, dateLabel, documentTypeLabel, types} from './appShared.jsx';

const documentFields = [
  {key:'id',label:'ID документа',kind:'readonly'}, {key:'type',label:'Тип документа',kind:'select',options:Object.entries(types)},
  {key:'number',label:'Номер'}, {key:'date',label:'Дата',kind:'date'}, {key:'contract_id',label:'Номер контракта'},
  {key:'status',label:'Статус',kind:'select',options:[['active','Действует'],['cancelled','Отменён'],['superseded','Заменён'],['draft','Черновик']]},
  {key:'is_complete',label:'Полнота документа',kind:'nullableBool',options:[['','Не подтверждена'],['true','Подтверждена'],['false','Неполный']]},
  {key:'deadline',label:'Срок поставки',kind:'date'}, {key:'effective_from',label:'Действует с',kind:'date'}, {key:'revision',label:'Редакция документа',kind:'integer',min:1},
  {key:'logical_id',label:'Общий ID документа'}, {key:'supersedes',label:'ID заменяемого документа'}, {key:'specification_id',label:'ID базовой спецификации'},
  {key:'base_document_id',label:'ID документа-основания'}, {key:'replacement_spec_id',label:'ID новой спецификации'},
  {key:'approved',label:'Допсоглашение подтверждено',kind:'bool',options:[['false','Нет'],['true','Да']]},
  {key:'acceptance_mode',label:'Режим приёмки',kind:'select',options:[['incremental','Отдельная'],['cumulative','Накопительная']]},
  {key:'related_delivery_ids',label:'ID связанных поставок, по одному в строке',kind:'list'},
  {key:'expected_pages',label:'Ожидается страниц',kind:'optionalInteger',min:1}, {key:'actual_pages',label:'Получено страниц',kind:'optionalInteger',min:0}
];
const itemFields = [
  {key:'line_id',label:'ID строки',kind:'readonly'}, {key:'position_id',label:'Номер позиции'}, {key:'sku',label:'Артикул'},
  {key:'name',label:'Наименование'}, {key:'unit',label:'Единица измерения'}, {key:'quantity',label:'Количество',kind:'decimal'},
  {key:'unit_price',label:'Цена за единицу',kind:'decimal'}, {key:'amount',label:'Сумма',kind:'decimal'}, {key:'currency',label:'Валюта'},
  {key:'tax_included',label:'Цена включает НДС',kind:'nullableBool',options:[['','Неясно'],['true','Да'],['false','Нет']]},
  {key:'vat_rate',label:'НДС, %',kind:'decimal'}, {key:'discount_percent',label:'Скидка, %',kind:'decimal'},
  {key:'conversion_factor',label:'Коэффициент перевода',kind:'decimal'}, {key:'conversion_to',label:'Единица перевода'},
  {key:'attributes',label:'Характеристики (JSON: название и значение)',kind:'json'}
];
function valueOf(def,value) {
  if (def.kind === 'json') return JSON.stringify(value ?? {},null,2);
  if (def.kind === 'list') return (value || []).join('\n');
  return value == null ? '' : String(value);
}
function fieldValues(fields,subject) { return Object.fromEntries(fields.map(def => [def.key,valueOf(def,subject[def.key])])); }
function parseValue(def,value) {
  if (def.kind === 'list') return value.split('\n').map(x => x.trim()).filter(Boolean);
  if (def.kind === 'json') {
    let parsed; try { parsed = JSON.parse(value); } catch { throw new Error('Характеристики должны быть объектом JSON с текстовыми значениями'); }
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object' || Object.values(parsed).some(x => typeof x !== 'string')) throw new Error('Характеристики должны быть объектом JSON с текстовыми значениями');
    return parsed;
  }
  if (def.kind === 'nullableBool') return value === '' ? null : value === 'true';
  if (def.kind === 'bool') return value === 'true';
  if (def.kind === 'integer' || def.kind === 'optionalInteger') {
    if (value === '' && def.kind === 'optionalInteger') return null;
    const parsed = Number(value);
    if (!/^\d+$/.test(value) || !Number.isSafeInteger(parsed) || parsed < def.min) throw new Error(`Поле «${def.label}» должно быть целым числом не меньше ${def.min}`);
    return parsed;
  }
  if (def.key === 'discount_percent' && value.trim() === '') return '0';
  return value || null;
}
function editorError(error) {
  let details; try { details = JSON.parse(error.message); } catch { return error.message; }
  if (!Array.isArray(details)) return error.message;
  return details.map(detail => {
    const loc = Array.isArray(detail.loc) ? detail.loc : [], itemIndex = loc.indexOf('items');
    const def = (itemIndex >= 0 ? itemFields : documentFields).find(field => field.key === loc[loc.length - 1]);
    const row = itemIndex >= 0 && Number.isInteger(loc[itemIndex + 1]) ? `Строка ${loc[itemIndex + 1] + 1}: ` : '';
    return `${row}${def ? def.label + ': ' : ''}${detail.msg || 'Некорректные данные'}`;
  }).join('; ');
}
function Source({source, fieldSources = {}}) {
  const refs = [['Исходный фрагмент',source], ...Object.entries(fieldSources || {})].filter(([,ref]) => ref);
  return refs.length ? <details><summary>Фрагменты источника</summary>{refs.map(([label,ref],index) => <div className="source" key={index}><b>{label}</b> · {ref.filename || ref.document_id}{ref.page ? ' · стр. ' + ref.page : ''}{ref.row ? ' · строка ' + ref.row : ''}{'\n'}{ref.excerpt}</div>)}</details> : null;
}
function UnknownConfidence({subject, confidence, fields, discarded, onChange}) {
  const unknown = Object.keys(confidence).filter(key => !fields.includes(key)), invalid = unknown.filter(key => !Object.hasOwn(subject,key)), unsupported = unknown.filter(key => Object.hasOwn(subject,key));
  if (!unknown.length) return null;
  return <div className="notice small">{!!invalid.length && <>Обнаружены оценки уверенности для несуществующих полей. Отметьте только ошибочные записи для удаления; это не подтверждает значения документа.<div className="editor-list">{invalid.map(key => <label className="check" key={key}><input type="checkbox" checked={Object.hasOwn(discarded,key) && !!discarded[key]} onChange={event => onChange(key,event.target.checked)}/><span>Удалить ошибочную оценку «{key}» ({Math.round(confidence[key] * 100)}%)</span></label>)}</div></>}{!!unsupported.length && <>Оценки для служебных полей нельзя подтвердить или удалить в редакторе: {unsupported.map(key => `${key} (${Math.round(confidence[key] * 100)}%)`).join(', ')}. Они сохранены без изменений; оценки ниже порога продолжат блокировать сверку.</>}</div>;
}
function Field({def, scope, identity, value, confidence, confirmed, onValue, onConfirm}) {
  const id = `editor-${scope}-${encodeURIComponent(String(identity))}-${def.key}`;
  const common = {id, value, onChange:event => onValue(event.target.value)};
  return <div className="editor-field"><label htmlFor={id}>{def.label}{Object.hasOwn(confidence,def.key) && <span className="editor-confidence"> Уверенность: {Math.round(confidence[def.key] * 100)}%</span>}</label>{def.options ? <select {...common}>{value === 'other' && def.key === 'type' && <option value="other">Другое</option>}{def.options.map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select> : def.kind === 'list' || def.kind === 'json' ? <textarea {...common} rows="3"/> : <input {...common} type={def.kind === 'date' ? 'date' : 'text'} readOnly={def.kind === 'readonly'} inputMode={def.kind === 'decimal' ? 'decimal' : def.kind === 'integer' || def.kind === 'optionalInteger' ? 'numeric' : undefined}/>}<label className="check"><input type="checkbox" checked={!!confirmed} onChange={event => onConfirm(event.target.checked)} aria-label={`Подтвердить по оригиналу: ${def.label}${scope === 'item' ? ', строка ' + identity : ', документ'}`}/>Проверено по оригиналу</label></div>;
}
function newRow(original) { return {original, values:fieldValues(itemFields,original), confirmed:{}, discarded:{}}; }

export function Editor({app, editor}) {
  const {record, documents} = editor, doc = record.document, confidence = doc.fields_confidence || {}, missing = doc.missing_fields || [], warnings = doc.extraction_warnings || [];
  const [values,setValues] = useState(() => fieldValues(documentFields,doc)), [confirmed,setConfirmed] = useState({}), [discarded,setDiscarded] = useState({});
  const [rows,setRows] = useState(() => doc.items.map(newRow)), [resolvedMissing,setResolvedMissing] = useState({}), [resolvedWarnings,setResolvedWarnings] = useState({}), [history,setHistory] = useState(null);
  const usedIds = useRef(new Set(doc.items.map(item => item.line_id))), nextManual = useRef(1), addButton = useRef(null);
  function updateRow(index,patch) { setRows(old => old.map((row,i) => i === index ? {...row,...patch(row)} : row)); }
  function addRow() {
    while (usedIds.current.has(`manual-${nextManual.current}`)) nextManual.current++;
    const line_id = `manual-${nextManual.current++}`; usedIds.current.add(line_id);
    setRows(old => [...old,newRow({line_id,position_id:null,sku:null,name:null,unit:null,quantity:null,unit_price:null,amount:null,currency:null,tax_included:null,vat_rate:null,discount_percent:'0',conversion_factor:null,conversion_to:null,attributes:{},confidence:{discount_percent:0},source:null,field_sources:{}})]);
    setConfirmed(old => ({...old,items:false}));
  }
  async function save(form) {
    try {
      const data = new FormData(form), reviewer = data.get('reviewer').trim(), comment = data.get('comment').trim();
      if (!reviewer || !comment) throw new Error('Укажите проверяющего и комментарий');
      const updated = structuredClone(doc), confirmed_fields = [], confirmed_items = Object.create(null), discarded_item_confidence = Object.create(null);
      updated.fields_confidence = updated.fields_confidence || {};
      const discarded_confidence_fields = Object.keys(discarded).filter(key => discarded[key]);
      discarded_confidence_fields.forEach(key => delete updated.fields_confidence[key]);
      documentFields.forEach(def => {
        if (def.kind !== 'readonly' && values[def.key] !== valueOf(def,doc[def.key])) { updated[def.key] = parseValue(def,values[def.key]); updated.fields_confidence[def.key] = 0; }
        if (confirmed[def.key]) { updated.fields_confidence[def.key] = 1; confirmed_fields.push(def.key); }
      });
      updated.missing_fields = missing.filter((_,index) => !resolvedMissing[index]);
      if (Object.values(resolvedMissing).some(Boolean)) updated.fields_confidence.missing_fields = 0;
      if (confirmed.missing_fields) { updated.fields_confidence.missing_fields = 1; confirmed_fields.push('missing_fields'); }
      updated.extraction_warnings = warnings.filter((_,index) => !resolvedWarnings[index]);
      updated.items = rows.map(row => {
        const item = structuredClone(row.original), checked = []; item.confidence = item.confidence || {};
        itemFields.forEach(def => {
          const value = row.values[def.key];
          if (def.key === 'discount_percent' && row.confirmed[def.key] && !value.trim()) throw new Error('Укажите скидку в процентах, прежде чем её подтвердить');
          if (def.kind !== 'readonly' && value !== valueOf(def,row.original[def.key])) { item[def.key] = parseValue(def,value); item.confidence[def.key] = 0; }
          if (row.confirmed[def.key]) { item.confidence[def.key] = 1; checked.push(def.key); }
        });
        const removed = Object.keys(row.discarded).filter(key => row.discarded[key]);
        if (removed.length) { discarded_item_confidence[item.line_id] = removed; removed.forEach(key => delete item.confidence[key]); }
        if (checked.length) confirmed_items[item.line_id] = checked;
        return item;
      });
      if (JSON.stringify(updated.items.map(item => item.line_id)) !== JSON.stringify(doc.items.map(item => item.line_id))) updated.fields_confidence.items = 0;
      if (confirmed.items) { updated.fields_confidence.items = 1; confirmed_fields.push('items'); }
      await app.saveEditor(editor,{document:updated,expected_revision:record.revision,reviewer,comment,confirmed_fields,confirmed_items,discarded_confidence_fields,discarded_item_confidence});
    } catch (error) { throw new Error(editorError(error)); }
  }
  return <AsyncForm app={app} id="document-form" onSave={save}>{pending => <div ref={node => {if (node) node.inert = pending;}}>
    <div className="row between"><Heading icon="wallet">Реквизиты документа</Heading><button type="button" onClick={app.closeEditor}>Закрыть</button></div><p className="small muted">Изменённые, но не отмеченные поля сохраняются неподтверждёнными. Отмечайте только то, что проверили по оригиналу. Исходный файл и история исправлений сохраняются.</p>
    <div className="formgrid">{documentFields.map(def => <Field key={def.key} def={def} scope="doc" identity="0" value={values[def.key]} confidence={confidence} confirmed={confirmed[def.key]} onValue={value => {setValues(old => ({...old,[def.key]:value})); setConfirmed(old => ({...old,[def.key]:false}));}} onConfirm={value => setConfirmed(old => ({...old,[def.key]:value}))}/>)}</div>
    <Source source={doc.source}/><UnknownConfidence subject={doc} confidence={confidence} fields={[...documentFields.map(def => def.key),'missing_fields','items']} discarded={discarded} onChange={(key,value) => setDiscarded(old => ({...old,[key]:value}))}/>
    <details><summary>ID документов комплекта</summary><ul className="small">{documents.map(item => <li key={item.id}>{documentTypeLabel(item.document.type)} · {item.document.number || 'без номера'} · <span className="editor-id">{item.id}</span></li>)}</ul></details>
    <div className="row between" style={{marginTop:20}}><h3>Товарные строки</h3><button ref={addButton} type="button" onClick={addRow}>Добавить строку</button></div><p className="small muted">Для новой строки нулевая скидка остаётся неподтверждённой до проверки. Удаление строки может потребовать повторного подтверждения ручных связей.</p><label className="check"><input type="checkbox" checked={!!confirmed.items} onChange={event => {const checked = event.target.checked; setConfirmed(old => ({...old,items:checked}));}}/>Весь состав товарных строк проверен по оригиналу{Object.hasOwn(confidence,'items') ? ` · уверенность ${Math.round(confidence.items * 100)}%` : ''}</label>
    <div data-items>{rows.map((row,index) => <fieldset className="editor-item" key={row.original.line_id}><legend className="editor-id">Строка {row.original.line_id}</legend><div className="row between"><span className="small muted">ID строки используется в ручных связях и не изменяется.</span><button type="button" aria-label={`Удалить строку ${row.original.line_id}`} onClick={() => {if (confirm('Удалить эту строку? Изменение вступит в силу после сохранения документа.')) {setRows(old => old.filter((_,i) => i !== index)); setConfirmed(old => ({...old,items:false})); addButton.current?.focus();}}}>Удалить строку</button></div><div className="formgrid">{itemFields.map(def => <Field key={def.key} def={def} scope="item" identity={row.original.line_id} value={row.values[def.key]} confidence={row.original.confidence || {}} confirmed={row.confirmed[def.key]} onValue={value => updateRow(index,old => ({values:{...old.values,[def.key]:value},confirmed:{...old.confirmed,[def.key]:false}}))} onConfirm={value => updateRow(index,old => ({confirmed:{...old.confirmed,[def.key]:value}}))}/>)}</div><Source source={row.original.source} fieldSources={row.original.field_sources}/><UnknownConfidence subject={row.original} confidence={row.original.confidence || {}} fields={itemFields.map(def => def.key)} discarded={row.discarded} onChange={(key,value) => updateRow(index,old => ({discarded:{...old.discarded,[key]:value}}))}/></fieldset>)}</div>
    <h3>Пропущенные данные и замечания извлечения</h3><p className="small muted">Отметьте только устранённые пропуски и разобранные замечания. Их удаление не подтверждает другие поля.</p>{missing.length ? <div className="editor-list">{missing.map((value,index) => <label className="check" key={index}><input type="checkbox" checked={!!resolvedMissing[index]} onChange={event => {const checked = event.target.checked; setResolvedMissing(old => ({...old,[index]:checked})); setConfirmed(old => ({...old,missing_fields:false}));}}/><span>Пропуск устранён: {value}</span></label>)}</div> : <p className="small muted">Пропуски не указаны.</p>}<label className="check"><input type="checkbox" checked={!!confirmed.missing_fields} onChange={event => {const checked = event.target.checked; setConfirmed(old => ({...old,missing_fields:checked}));}}/>Проверен список оставшихся пропусков{Object.hasOwn(confidence,'missing_fields') ? ` · уверенность ${Math.round(confidence.missing_fields * 100)}%` : ''}</label>{warnings.length ? <div className="editor-list">{warnings.map((value,index) => <label className="check" key={index}><input type="checkbox" checked={!!resolvedWarnings[index]} onChange={event => {const checked = event.target.checked; setResolvedWarnings(old => ({...old,[index]:checked}));}}/><span>Замечание разобрано: {value}</span></label>)}</div> : <p className="small muted">Замечаний извлечения нет.</p>}
    <div className="formgrid" style={{marginTop:20}}><label>Проверяющий<input name="reviewer" required maxLength="200"/></label><label>Комментарий к исправлению<input name="comment" required maxLength="10000"/></label></div><div className="row between" style={{marginTop:20}}><div className="row"><Action app={app} action={() => app.downloadSource(record.id,editor.caseId,record)}>Исходный файл</Action><Action app={app} action={async () => setHistory(await app.editorHistory(editor))}>История</Action></div><button className="primary" type="submit" disabled={pending}>Сохранить исправления</button></div><div id="history">{history && <><h3>История исправлений</h3>{history.map((item,index) => <p className="small" key={index}>Редакция {item.revision} · {item.reviewer} · {dateLabel(item.created_at,true)}<br/>{item.comment}</p>)}</>}</div>
  </div>}</AsyncForm>;
}
