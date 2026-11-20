import React, {useEffect, useRef, useState} from 'react';
import {Action, AsyncForm, dateLabel, documentName, documentTypeLabel, number, types} from './appShared.jsx';
import {Editor} from './appEditor.jsx';

function Dialog({id, className, label, labelledBy, onClose, children}) {
  const ref = useRef(null);
  useEffect(() => { const node = ref.current; if (!node.open) node.showModal(); return () => { if (node.open) node.close(); }; }, []);
  return <dialog ref={ref} id={id} className={className} aria-label={label} aria-labelledby={labelledBy} onCancel={event => {event.preventDefault(); onClose();}} onClose={() => onClose()} onClick={event => {
    if (event.target !== event.currentTarget) return;
    const {left, right, top, bottom} = event.currentTarget.getBoundingClientRect();
    if (event.clientX < left || event.clientX > right || event.clientY < top || event.clientY > bottom) onClose();
  }}>{children}</dialog>;
}
function ModalHeader({app, eyebrow, title}) { return <div className="modal-header"><div><div className="modal-eyebrow">{eyebrow}</div><h2 id="modal-title">{title}</h2></div><button type="button" className="ghost" onClick={app.closeModal} aria-label="Закрыть диалог">Закрыть ×</button></div>; }
export function Dialogs({app}) {
  const {modal, editor} = app.state;
  return <>{modal && <Dialog key={modal.ticket} id="modal" className={modal.type === 'viewer' ? 'viewer' : modal.type === 'type' ? 'type-modal' : ''} labelledBy="modal-title" onClose={() => app.closeModal(modal.ticket)}><ModalContent app={app} modal={modal}/></Dialog>}{editor && <Dialog key={editor.ticket} id="editor" label="Редактирование документа" onClose={() => app.closeEditor(editor.ticket)}><Editor app={app} editor={editor}/></Dialog>}</>;
}
function ModalContent({app, modal}) {
  if (modal.type === 'new') return <><ModalHeader app={app} eyebrow="Новая проверка" title="Данные контракта"/><AsyncForm app={app} onSave={app.createCase} className="contract-form">{pending => <><div className="field"><label htmlFor="new-number">Номер</label><input id="new-number" name="contract_number" placeholder="ГК-2026/0142" maxLength="300"/></div><div className="field"><label htmlFor="new-title">Наименование</label><input id="new-title" name="title" placeholder="Например, поставка уличных скамеек" maxLength="300" required autoFocus/></div><div className="field"><label htmlFor="new-description">Описание</label><textarea id="new-description" name="description" maxLength="5000" placeholder="Что нужно проверить"/></div><div className="modal-actions"><button className="primary" type="submit" disabled={pending}>Создать проверку</button><button type="button" onClick={app.closeModal}>Отмена</button></div></>}</AsyncForm></>;
  if (modal.type === 'token') return <><ModalHeader app={app} eyebrow="Настройки" title="Доступ к серверу"/><AsyncForm app={app} onSave={app.saveToken}>{pending => <><p className="modal-description">Если для сервера задан токен, введите его здесь. Он будет храниться только в этой вкладке.</p><div className="field"><label htmlFor="api-token">Токен доступа</label><input id="api-token" name="token" type="password" autoComplete="off" defaultValue={app.state.token}/></div><div className="modal-actions"><button className="primary" type="submit" disabled={pending}>Сохранить</button><button type="button" onClick={app.closeModal}>Отмена</button></div></>}</AsyncForm></>;
  if (modal.type === 'type') return <TypeDialog app={app} modal={modal}/>;
  if (modal.type === 'status') return <StatusDialog app={app} modal={modal}/>;
  if (modal.type === 'viewer') return <Viewer app={app} modal={modal}/>;
  return null;
}
function TypeDialog({app, modal}) {
  const record = modal.record, [selected,setSelected] = useState(record.document.type), [expanded,setExpanded] = useState(false), [help,setHelp] = useState('');
  const trigger = useRef(null), list = useRef(null);
  const entries = [...Object.entries(types), ['corrected','Исправленная версия'], ['other','Другое']];
  useEffect(() => { if (expanded) (list.current?.querySelector('[aria-selected="true"]') || list.current?.querySelector('[role="option"]'))?.focus(); },[expanded]);
  function choose(value) {
    if (value === 'other' && !app.state.preview) { setHelp('Для сверки подходят контракт, спецификация, накладная, документ о приёмке или дополнительное соглашение. Для другого документа сначала уточните его роль.'); }
    else { setSelected(value); setHelp(value === 'corrected' ? 'Исправленная версия сохраняет категорию документа. После продолжения укажите заменяемый документ и редакцию в реквизитах.' : ''); }
    setExpanded(false); trigger.current?.focus();
  }
  return <><ModalHeader app={app} eyebrow="Классификация документа" title="Изменить тип документа"/><p className="modal-filename">{documentName(record)}</p><AsyncForm app={app} onSave={form => app.saveType(modal,selected,form)}>{pending => <><label htmlFor="type-trigger" className="type-label">Категория документа</label><input type="hidden" id="type-value" name="type" value={selected}/><div className="type-picker"><button ref={trigger} type="button" id="type-trigger" className="type-trigger" aria-haspopup="listbox" aria-expanded={expanded} aria-controls="type-options" onClick={() => setExpanded(!expanded)}><span id="type-selected">{entries.find(([value]) => value === selected)?.[1] || documentTypeLabel(selected)}</span><svg className="icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></button><div ref={list} id="type-options" className="type-list" role="listbox" aria-label="Категория документа" hidden={!expanded} onKeyDown={event => {if (!['ArrowDown','ArrowUp','Home','End','Escape'].includes(event.key)) return; event.preventDefault(); event.stopPropagation(); if (event.key === 'Escape') {setExpanded(false); trigger.current?.focus(); return;} const options = [...list.current.querySelectorAll('[role="option"]')], current = options.indexOf(document.activeElement), next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : (current + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length; options[next]?.focus();}}>{entries.map(([value,label]) => <button type="button" key={value} className="type-option" role="option" aria-selected={selected === value} aria-disabled={value === 'other' && !app.state.preview ? 'true' : undefined} title={value === 'other' && !app.state.preview ? 'Для этого документа нужно выбрать одну из поддерживаемых категорий' : undefined} onClick={() => choose(value)}>{label}</button>)}</div></div><p className="modal-description">Тип определяет роль документа в сверке: контракт задаёт условия, накладные подтверждают поставку, акт фиксирует приёмку, дополнительное соглашение меняет действующую редакцию.</p>{help && <p id="type-help" className="small muted">{help}</p>}{!app.state.preview && <div className="type-review-fields"><label>Проверяющий<input name="reviewer" required maxLength="200" placeholder="Имя или роль"/></label><label>Основание изменения<input name="comment" required maxLength="10000" placeholder="Что проверено по оригиналу"/></label></div>}<div className="modal-actions"><button className="primary" type="submit" disabled={pending}>Сохранить тип</button><button type="button" onClick={app.closeModal}>Отмена</button></div></>}</AsyncForm></>;
}
function StatusDialog({app, modal}) {
  const restoring = modal.record.document.status === 'cancelled';
  return <><ModalHeader app={app} eyebrow="Состав комплекта" title={restoring ? 'Восстановить документ' : 'Исключить из сверки'}/><p className="modal-filename">{documentName(modal.record)}</p><p className="modal-description">{restoring ? 'Документ снова будет участвовать в следующей сверке.' : 'Документ получит статус «Отменён» и не будет участвовать в следующей сверке. Оригинал и история сохранятся.'}</p><AsyncForm app={app} onSave={form => app.saveStatus(modal,restoring ? 'active' : 'cancelled',form)}>{pending => <><div className="field"><label htmlFor="status-reviewer">Проверяющий</label><input id="status-reviewer" name="reviewer" required maxLength="200"/></div><div className="field"><label htmlFor="status-comment">Причина</label><textarea id="status-comment" name="comment" required maxLength="10000"/></div><div className="modal-actions"><button className="primary" type="submit" disabled={pending}>{restoring ? 'Восстановить' : 'Исключить из сверки'}</button><button type="button" onClick={app.closeModal}>Отмена</button></div></>}</AsyncForm></>;
}
function Snapshot({doc}) {
  return <div className="viewer-data"><div className="notice">Показаны сохранённые реквизиты и фрагменты источника. Они не заменяют оригинальный файл.</div><dl className="document-facts"><div><dt>Категория</dt><dd>{documentTypeLabel(doc.type)}</dd></div><div><dt>Номер</dt><dd>{doc.number || 'Не указан'}</dd></div><div><dt>Дата</dt><dd>{dateLabel(doc.date)}</dd></div><div><dt>Контракт</dt><dd>{doc.contract_id || 'Не указан'}</dd></div></dl>{doc.source && <pre className="source">{doc.source.excerpt}</pre>}<div className="scroll"><table className="data-table document-items-table"><thead><tr><th className="cell-description">Позиция</th><th className="cell-count">Количество</th><th className="cell-unit">Ед.</th><th className="cell-amount">Цена</th></tr></thead><tbody>{doc.items.length ? doc.items.map((item,index) => <tr key={item.line_id || index}><td className="cell-description">{item.name || item.line_id}</td><td className="cell-count">{number(item.quantity)}</td><td className="cell-unit">{item.unit || '—'}</td><td className="cell-amount">{number(item.unit_price)}</td></tr>) : <tr><td colSpan="4" className="table-empty"><span className="empty-message">Товарные строки не извлечены.</span></td></tr>}</tbody></table></div></div>;
}
function Viewer({app, modal}) {
  const {doc, record, snapshot, id, context} = modal, title = doc.filename || id;
  const [content,setContent] = useState(snapshot || app.state.preview ? {kind:'snapshot'} : {kind:'loading'}), [attempt,setAttempt] = useState(0);
  useEffect(() => {
    if (snapshot || app.state.preview) return;
    let active = true, objectURL;
    (async () => {
      setContent({kind:'loading'});
      try {
        const blob = await app.fetchSource(context.caseId,id);
        if (!active) return;
        const lower = title.toLocaleLowerCase();
        if (/\.(txt|csv|tsv|json)$/.test(lower)) { const text = await blob.text(); if (active) setContent({kind:'text',text}); }
        else if (/\.(pdf|png|jpe?g|gif|webp)$/.test(lower)) { objectURL = URL.createObjectURL(blob); setContent({kind:lower.endsWith('.pdf') ? 'pdf' : 'image',url:objectURL}); }
        else setContent({kind:'download'});
      } catch (error) { if (active) setContent(error.status === 404 ? {kind:'missing'} : {kind:'error',message:error.message}); }
    })();
    return () => { active = false; if (objectURL) URL.revokeObjectURL(objectURL); };
  },[modal.ticket,attempt]);
  return <><ModalHeader app={app} eyebrow={snapshot ? 'Источники сохранённой сверки' : 'Просмотр документа'} title={title}/><div id="viewer-content">{content.kind === 'loading' && <p className="muted">Открываю документ…</p>}{content.kind === 'text' && <pre className="source file-text">{content.text}</pre>}{content.kind === 'pdf' && <iframe title={title} src={content.url}/>} {content.kind === 'image' && <img src={content.url} alt={title}/>} {content.kind === 'missing' && <p className="small muted">Оригинальный файл не сохранён в этом комплекте.</p>}{['snapshot','download','missing'].includes(content.kind) && <Snapshot doc={doc}/>} {content.kind === 'download' && <Action app={app} action={() => app.downloadSource(id,context.caseId,record)}>Скачать исходный файл</Action>} {content.kind === 'error' && <><div className="notice" role="alert">Не удалось открыть исходный файл: {content.message}</div><button type="button" onClick={() => setAttempt(value => value + 1)}>Повторить</button></>}</div><div className="modal-actions"><Action app={app} action={() => app.editFromViewer(modal)}>Реквизиты</Action><button type="button" onClick={app.closeModal}>Закрыть</button></div></>;
}
