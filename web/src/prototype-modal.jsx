import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { demoContents, types } from './prototype-data.js';

export function DocumentModal({ kind, doc, demo, returnFocus, onClose, onChangeType, onSaveType }) {
  const [type, setType] = useState(doc.type);
  const [expanded, setExpanded] = useState(false);
  const dialog = useRef(null);
  const list = useRef(null);
  const trigger = useRef(null);
  const overlay = useRef(null);

  useEffect(() => {
    dialog.current?.focus();
  }, [kind]);

  useEffect(() => () => {
    if (returnFocus.current?.isConnected) returnFocus.current.focus();
  }, [returnFocus]);

  useEffect(() => {
    if (expanded) (list.current?.querySelector('[aria-selected="true"]') || list.current?.querySelector('button'))?.focus();
  }, [expanded]);

  useEffect(() => {
    function handleKey(event) {
      if (expanded && ['ArrowDown', 'ArrowUp', 'Home', 'End', 'Escape'].includes(event.key)) {
        event.preventDefault();
        if (event.key === 'Escape') {
          setExpanded(false);
          trigger.current?.focus();
          return;
        }
        const options = [...list.current.querySelectorAll('[role="option"]')];
        const index = options.indexOf(document.activeElement);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
        options[next]?.focus();
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
      } else if (event.key === 'Tab') {
        const focusable = [...overlay.current.querySelectorAll('button,input:not([type="hidden"]),select,textarea,a[href],[tabindex="0"]')].filter(node => !node.disabled && !node.closest('[hidden]'));
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    }
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [expanded, onClose]);

  const preview = demo ? demoContents[doc.id] : null;
  return createPortal(<div id="docModal" ref={overlay} className={'modalOverlay' + (kind === 'type' ? ' type-overlay' : '')} onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    {kind === 'type' ? <section ref={dialog} className="modalWindow type-window" role="dialog" aria-modal="true" aria-labelledby="prototype-modal-title" tabIndex={-1}>
      <div className="modalHead"><div><div className="eyebrow">Классификация документа</div><h2 id="prototype-modal-title" className="section-heading"><span className="identity-icon identity-icon--tag section-icon" aria-hidden="true"/><span>Изменить тип документа</span></h2></div><button className="btn" onClick={onClose}>Закрыть ×</button></div>
      <p className="modal-filename">{doc.name}</p><div className="field"><label className="type-label" htmlFor="prototype-type-trigger">Категория документа</label><input type="hidden" id="docCategory" value={type}/>
        <div className="type-picker"><button ref={trigger} className="type-trigger" id="prototype-type-trigger" type="button" aria-haspopup="listbox" aria-expanded={expanded} aria-controls="prototype-type-options" onClick={() => { if (expanded) { setExpanded(false); trigger.current?.focus(); } else setExpanded(true); }}><span id="prototype-type-selected">{type}</span><svg className="icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></button>
          <div ref={list} className="type-list" id="prototype-type-options" role="listbox" aria-label="Категория документа" hidden={!expanded}>{types.map(value => <button className="type-option" type="button" role="option" aria-selected={value === type} key={value} onClick={() => { setType(value); setExpanded(false); trigger.current?.focus(); }}>{value}</button>)}</div>
        </div>
      </div><p className="note">Тип определяет роль документа в сверке: контракт задаёт условия, накладные подтверждают поставку, акт фиксирует приёмку, дополнительное соглашение меняет действующую редакцию.</p>
      <div className="actions"><button className="btn primary" onClick={() => onSaveType(type)}>Сохранить тип</button><button className="btn" onClick={onClose}>Отмена</button></div>
    </section> : <div ref={dialog} className="modalWindow viewer-window" role="dialog" aria-modal="true" aria-label="Предпросмотр документа" tabIndex={-1}>
      <div className="modalHead"><div><div className="eyebrow">Предпросмотр документа</div><h2>{doc.name}</h2></div><button className="btn" onClick={onClose}>Закрыть ×</button></div><div className="sub">{doc.type} · версия {doc.version}</div>
      {preview ? <div className="paperPreview"><h3>{preview.title}</h3>{preview.lines.map((line, index) => <p key={index}>{line}</p>)}</div> : <div className="warn">Оригинальный файл недоступен. Загрузите документ заново для просмотра.</div>}
      <div className="actions"><button className="btn" onClick={onChangeType}>Изменить тип</button><button className="btn primary" onClick={onClose}>Готово</button></div>
    </div>}
  </div>, document.body);
}
