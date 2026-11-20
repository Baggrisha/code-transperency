import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { KEY, initial } from './prototype-data.js';
import { CasePage, RegistryPage, SignalPage } from './prototype-pages.jsx';
import { DocumentModal } from './prototype-modal.jsx';

const clone = value => JSON.parse(JSON.stringify(value));
const today = () => new Date().toLocaleDateString('ru-RU');

function loadState() {
  let state;
  try {
    state = JSON.parse(localStorage.getItem(KEY)) || {
      cases: clone(initial), selected: null, tab: 'overview', page: 'home', signal: null,
    };
  } catch {
    state = { cases: clone(initial), selected: null, tab: 'overview', page: 'home', signal: null };
  }

  const previousDescriptions = new Set(['Учебный кейс T02. Синтетические данные.', 'Синтетические данные.']);
  let migrated = false;
  for (const item of Array.isArray(state.cases) ? state.cases : []) {
    if (item?.demo === true && previousDescriptions.has(item.description)) {
      item.description = initial[0].description;
      migrated = true;
    }
  }
  if (!state.selected && state.cases?.length) state.selected = state.cases[0].id;
  return { state, migrated };
}

function log(item, text) {
  item.history ??= [];
  item.history.unshift({ date: new Date().toLocaleString('ru-RU'), text });
}

function App() {
  const [loaded] = useState(loadState);
  const [state, setState] = useState(loaded.state);
  const stateRef = useRef(state);
  const files = useRef(new Map());
  const returnFocus = useRef(null);
  const noticeTimer = useRef(null);
  const [notice, setNotice] = useState('');
  const [modal, setModal] = useState(null);

  function notify(message) {
    setNotice(message);
    clearTimeout(noticeTimer.current);
    noticeTimer.current = setTimeout(() => setNotice(''), 6500);
  }

  function save(next) {
    try {
      localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      notify('Хранилище браузера недоступно. Изменения сохраняются только в открытой вкладке.');
    }
  }

  useEffect(() => {
    if (loaded.migrated) save(stateRef.current);
    return () => clearTimeout(noticeTimer.current);
  }, []);

  function change(update) {
    const next = clone(stateRef.current);
    update(next);
    stateRef.current = next;
    setState(next);
    save(next);
  }

  function go(page, tab) {
    change(next => {
      next.page = page;
      if (tab) next.tab = tab;
    });
  }

  function createCase() {
    const number = prompt('Номер контракта (можно оставить пустым):', '');
    if (number === null) return;
    const name = prompt('Наименование контракта (можно оставить пустым):', '');
    if (name === null) return;
    const id = 'CASE-' + Date.now();
    change(next => {
      next.cases.unshift({ id, number, name, description: '', created: today(), updated: today(), status: 'Черновик', docs: [], signals: [], history: [], demo: false });
      next.selected = id;
      next.tab = 'docs';
      next.page = 'case';
    });
  }

  function openCase(id) {
    change(next => {
      next.selected = id;
      next.page = 'case';
      next.tab = 'overview';
    });
  }

  function openOriginalDemo() {
    change(next => {
      const original = clone(initial[0]);
      let demo = next.cases.find(item => item.demo && JSON.stringify({ ...item, id: original.id }) === JSON.stringify(original));
      if (!demo) {
        if (next.cases.some(item => item.id === original.id)) {
          const prefix = 'DEMO-T02-' + Date.now();
          original.id = prefix;
          let suffix = 1;
          while (next.cases.some(item => item.id === original.id)) original.id = prefix + '-' + suffix++;
        }
        next.cases.unshift(original);
        demo = original;
      }
      next.signal = null;
      next.selected = demo.id;
      next.page = 'case';
      next.tab = 'overview';
    });
    notify('Проверка открыта.');
  }

  function updateCase(fields) {
    change(next => {
      const item = next.cases.find(entry => entry.id === next.selected);
      Object.assign(item, fields);
      log(item, 'Изменены реквизиты проверки');
    });
  }

  function addFiles(incoming) {
    change(next => {
      const item = next.cases.find(entry => entry.id === next.selected);
      for (const file of incoming) {
        if (!/\.(pdf|png|jpe?g|txt)$/i.test(file.name)) {
          alert('Неподдерживаемый формат: ' + file.name);
          continue;
        }
        if (item.docs.some(doc => doc.name === file.name && doc.size === file.size)) {
          alert('Возможный дубликат: ' + file.name);
          continue;
        }
        const id = 'DOC-' + Date.now() + '-' + Math.random().toString(36).slice(2, 7);
        const type = /контракт/i.test(file.name) ? 'Контракт' : /спец/i.test(file.name) ? 'Спецификация' : /наклад/i.test(file.name) ? 'Накладная' : /акт|прием|приём/i.test(file.name) ? 'Документ о приёмке' : 'Другое';
        item.docs.push({ id, name: file.name, size: file.size, type, version: 1 });
        files.current.set(id, file);
        log(item, 'Добавлен документ ' + file.name);
      }
      item.updated = today();
    });
  }

  function removeDoc(id) {
    if (!confirm('Удалить документ из комплекта?')) return;
    change(next => {
      const item = next.cases.find(entry => entry.id === next.selected);
      const doc = item.docs.find(entry => entry.id === id);
      item.docs = item.docs.filter(entry => entry.id !== id);
      files.current.delete(id);
      log(item, 'Удалён документ ' + doc.name);
    });
  }

  function openModal(kind, id) {
    if (!modal) returnFocus.current = document.activeElement;
    setModal({ kind, id });
  }

  function viewDoc(id) {
    const file = files.current.get(id);
    if (file) {
      window.open(URL.createObjectURL(file), '_blank');
      return;
    }
    openModal('viewer', id);
  }

  function saveDocType(id, type) {
    const item = stateRef.current.cases.find(entry => entry.id === stateRef.current.selected);
    const doc = item.docs.find(entry => entry.id === id);
    if (doc && type !== doc.type) {
      change(next => {
        const current = next.cases.find(entry => entry.id === next.selected);
        const selectedDoc = current.docs.find(entry => entry.id === id);
        log(current, 'Тип документа ' + selectedDoc.name + ': ' + selectedDoc.type + ' → ' + type);
        selectedDoc.type = type;
      });
    }
    setModal(null);
  }

  function openSignal(id) {
    change(next => {
      next.signal = id;
      next.page = 'signal';
    });
  }

  function saveSignal(status, comment) {
    change(next => {
      const item = next.cases.find(entry => entry.id === next.selected);
      const signal = item.signals.find(entry => entry.id === next.signal);
      if (status !== signal.status) {
        log(signal, 'Статус: ' + signal.status + ' → ' + status);
        log(item, 'Сигнал ' + signal.id + ': ' + status);
        signal.status = status;
      }
      const text = comment.trim();
      if (text) {
        signal.comments.push({ date: new Date().toLocaleString('ru-RU'), text });
        log(signal, 'Комментарий: ' + text);
        log(item, 'Добавлен комментарий к ' + signal.id);
      }
      item.updated = today();
    });
  }

  function finishCase() {
    const item = stateRef.current.cases.find(entry => entry.id === stateRef.current.selected);
    if (item.signals.some(signal => signal.status === 'Требует проверки' || signal.status === 'В работе') && !confirm('Остались сигналы без итогового решения. Завершить рассмотрение?')) return;
    change(next => {
      const current = next.cases.find(entry => entry.id === next.selected);
      current.status = 'Завершена';
      log(current, 'Рассмотрение завершено');
    });
  }

  function exportQuestions() {
    const item = stateRef.current.cases.find(entry => entry.id === stateRef.current.selected);
    const lines = ['Проверка: ' + (item.number || item.id), ''];
    item.signals.forEach(signal => lines.push(signal.id + ' — ' + signal.type + ' — ' + signal.item + ' — ' + signal.status + '\n' + signal.explanation + '\n'));
    const url = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'questions-' + item.id + '.txt';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  const selected = state.cases.find(item => item.id === state.selected);
  const signal = selected?.signals.find(item => item.id === state.signal);
  const page = (state.page === 'case' || state.page === 'signal') && !selected ? 'home' : state.page === 'signal' && !signal ? 'case' : state.page;
  const tab = state.page === 'signal' && !signal ? 'results' : state.tab;
  const actions = { go, createCase, openCase, openOriginalDemo, updateCase, addFiles, removeDoc, viewDoc, changeDocType: id => openModal('type', id), openSignal, saveSignal, finishCase, exportQuestions };
  const doc = modal && selected?.docs.find(item => item.id === modal.id);
  const active = page === 'case' || page === 'signal';

  return <>
    <div className="app" data-page={page} data-tab={tab}>
      <aside className="sidebar" aria-label="Основная навигация">
        <a className="brand" href="#" onClick={event => { event.preventDefault(); go('home'); }} aria-label="uzniki’s project, все проверки"><span>uzniki’s</span><span className="brand-accent">project</span></a>
        <nav className="prototype-nav">
          <button className={'nav' + (page === 'home' ? ' active' : '')} aria-current={page === 'home' ? 'page' : undefined} onClick={() => go('home')}><span className="identity-icon identity-icon--layers" aria-hidden="true"/><span>Все проверки</span></button>
          <button className={'nav' + (active ? ' active' : '')} aria-current={active ? 'page' : undefined} onClick={() => selected ? go('case', 'overview') : go('home')}><span className="identity-icon identity-icon--home" aria-hidden="true"/><span>Рабочая область</span></button>
          <button className={'nav' + (page === 'history' ? ' active' : '')} aria-current={page === 'history' ? 'page' : undefined} onClick={() => go('history')}><span className="identity-icon identity-icon--alarm" aria-hidden="true"/><span>История проверок</span></button>
        </nav>
      </aside>
      <main className="main" id="prototype-main">
        {page === 'case' ? <CasePage item={selected} tab={tab} actions={actions}/> : page === 'signal' ? <SignalPage key={signal.id} item={selected} signal={signal} actions={actions}/> : <RegistryPage key={page} cases={state.cases} history={page === 'history'} actions={actions}/>}
      </main>
    </div>
    {modal && doc && <DocumentModal key={modal.id} kind={modal.kind} doc={doc} demo={selected.demo} returnFocus={returnFocus} onClose={() => setModal(null)} onChangeType={() => openModal('type', doc.id)} onSaveType={type => saveDocType(doc.id, type)}/>}
    {notice && <div id="prototype-toast" role="status">{notice}</div>}
  </>;
}

createRoot(document.getElementById('root')).render(<App/>);
