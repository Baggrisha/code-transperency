import { useRef, useState } from 'react';

const signalStatuses = ['Требует проверки', 'В работе', 'Подтверждено специалистом', 'Не подтверждено', 'Недостаточно данных'];
const reviewed = signal => ['Подтверждено специалистом', 'Не подтверждено'].includes(signal.status);

function IconHeading({ icon, children }) {
  return <h2 className="section-heading sectionTitle"><span className={'identity-icon identity-icon--' + icon + ' section-icon'} aria-hidden="true"/><span>{children}</span></h2>;
}

function Header({ eyebrow, title, subtitle, children }) {
  return <header className="top"><div className="header-copy"><div className="eyebrow">{eyebrow}</div><h1 className="heading">{title}</h1><p className="sub">{subtitle}</p></div>{children && <div className="header-action">{children}</div>}</header>;
}

function Metrics({ values, className = '' }) {
  return <div className={'grid' + (className ? ' ' + className : '')}>{values.map(([label, value]) => <div className="metric" key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>;
}

function Pill({ status }) {
  const color = /Подтверждено|Завершена/.test(status) ? 'green' : /Требует|Недостаточно/.test(status) ? 'orange' : /Не подтверждено/.test(status) ? 'red' : '';
  return <span className={'pill' + (color ? ' ' + color : '')}>{status}</span>;
}

export function RegistryPage({ cases, history, actions }) {
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const list = cases.filter(item => (!history || item.status === 'Завершена') && (item.number + ' ' + item.name).toLowerCase().includes(query.toLowerCase()) && (!status || item.status === status));
  return <>
    <Header eyebrow={history ? 'ИСТОРИЯ ПРОВЕРОК' : 'ВСЕ ПРОВЕРКИ'} title={history ? 'История проверок' : 'Проверки контрактов'} subtitle={history ? 'Завершённые дела и решения специалистов' : 'Реестр проверочных дел и состояние рассмотрения'}>
      <button className="btn primary" onClick={actions.createCase}>+ Новая проверка</button><button className="btn" onClick={actions.openOriginalDemo}>Открыть проверку</button>
    </Header>
    <Metrics className="registry-metrics" values={[
      ['Всего проверок', cases.length], ['В работе', cases.filter(item => item.status !== 'Завершена').length],
      ['Сигналов', cases.reduce((count, item) => count + item.signals.length, 0)],
      ['Рассмотрено', cases.reduce((count, item) => count + item.signals.filter(reviewed).length, 0)],
    ]}/>
    <section className="panel registry-panel">
      <div className="toolbar"><input id="search" aria-label="Поиск по контракту или названию" placeholder="Поиск по контракту или названию" value={query} onChange={event => setQuery(event.target.value)}/><select id="statusFilter" aria-label="Статус проверки" value={status} onChange={event => setStatus(event.target.value)}><option value="">Все статусы</option><option>Черновик</option><option>Требует рассмотрения</option><option>Завершена</option></select></div>
      <div className="scroll"><table className="data-table registry-table"><thead><tr><th className="cell-id">Контракт</th><th className="cell-description">Наименование</th><th className="cell-status">Статус</th><th className="cell-count">Документы</th><th className="cell-count">Сигналы</th><th className="cell-date">Изменено</th></tr></thead><tbody id="caseRows">
        {list.length ? list.map(item => <tr className="clickable" key={item.id} onClick={() => actions.openCase(item.id)}><td className="cell-id"><button type="button" className="case-link" aria-label={'Открыть проверку ' + (item.number || item.id)} onClick={event => { event.stopPropagation(); actions.openCase(item.id); }}>{item.number || item.id}</button></td><td className="cell-description">{item.name || 'Без названия'}</td><td className="cell-status"><Pill status={item.status}/></td><td className="cell-count">{item.docs.length}</td><td className="cell-count">{item.signals.length}</td><td className="cell-date">{item.updated}</td></tr>) : <tr><td colSpan="6" className="table-empty"><span className="empty-message">Проверок пока нет</span></td></tr>}
      </tbody></table></div>
    </section>
  </>;
}

const tabs = [['overview', 'Обзор'], ['docs', 'Документы'], ['extract', 'Распознавание'], ['match', 'Сопоставление'], ['results', 'Результаты'], ['history', 'История действий']];

export function CasePage({ item, tab, actions }) {
  return <>
    <Header eyebrow="РАБОЧАЯ ОБЛАСТЬ" title={item.name || 'Новая проверка'} subtitle={<>Контракт <b>{item.number || 'номер не указан'}</b> — {item.status.toLowerCase()}</>}><button className="btn" onClick={() => actions.go('home')}>← К списку</button></Header>
    <nav className="steps" aria-label="Разделы проверки">{tabs.map(([key, label]) => <button key={key} className={'step' + (tab === key ? ' active' : '')} aria-current={tab === key ? 'page' : undefined} onClick={() => actions.go('case', key)}>{label}</button>)}</nav>
    {tab === 'overview' ? <Overview item={item} actions={actions}/> : tab === 'docs' ? <Documents item={item} actions={actions}/> : tab === 'extract' ? <Recognition item={item} actions={actions}/> : tab === 'match' ? <Matching item={item} actions={actions}/> : tab === 'results' ? <Results item={item} actions={actions}/> : <History item={item}/>}
  </>;
}

function CaseFields({ item, actions, recognition = false }) {
  const [number, setNumber] = useState(item.number ?? '');
  const [name, setName] = useState(item.name ?? '');
  const [description, setDescription] = useState(item.description ?? '');
  return <>
    <div className="field"><label htmlFor="caseNumber">Номер</label><input id="caseNumber" value={number} onChange={event => setNumber(event.target.value)}/></div>
    <div className="field"><label htmlFor="caseName">Наименование</label><input id="caseName" value={name} onChange={event => setName(event.target.value)}/></div>
    <div className="field"><label htmlFor="caseDescription">Описание</label><textarea id="caseDescription" rows="3" value={description} onChange={event => setDescription(event.target.value)}/></div>
    <div className="actions"><button className="btn primary" onClick={() => actions.updateCase({ number, name, description })}>{recognition ? 'Сохранить исправления' : 'Сохранить реквизиты'}</button><button className="btn" onClick={() => actions.go('case', recognition ? 'match' : 'docs')}>{recognition ? 'К сопоставлению →' : 'К документам →'}</button></div>
  </>;
}

function Overview({ item, actions }) {
  return <>
    <Metrics className="overview-metrics" values={[
      ['Документов', item.docs.length], ['Сигналов', item.signals.length],
      ['Ожидают решения', item.signals.filter(signal => signal.status === 'Требует проверки').length], ['Рассмотрено', item.signals.filter(reviewed).length],
    ]}/>
    <section className="panel contract-panel"><IconHeading icon="wallet">Данные контракта</IconHeading><CaseFields key={item.id} item={item} actions={actions}/></section>
  </>;
}

function Documents({ item, actions }) {
  const input = useRef(null);
  const [dragging, setDragging] = useState(false);
  return <>
    <div className="cols docs-cols">
      <section className="panel upload-card"><IconHeading icon="folder">Загрузка документов</IconHeading>
        <div id="drop" className={'drop' + (dragging ? ' drag' : '')} onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); actions.addFiles(Array.from(event.dataTransfer.files)); }}>
          <span className="identity-icon identity-icon--folder upload-icon" aria-hidden="true"/><h3>Перетащите файлы сюда</h3><p className="upload-hint">или выберите файлы на устройстве (<b>PDF, PNG, JPEG, TXT</b>)</p>
          <div className="file-picker-row"><button type="button" className="btn" onClick={() => input.current?.click()}>Выбрать файлы</button><span>Файлы не выбраны</span></div>
          <input ref={input} className="visually-hidden" type="file" id="files" multiple accept=".pdf,.png,.jpg,.jpeg,.txt" aria-label="Выбрать файлы" onChange={event => { actions.addFiles(Array.from(event.currentTarget.files)); event.currentTarget.value = ''; }}/>
        </div>
      </section>
      <section className="panel document-card"><IconHeading icon="folder">Комплект документов: <span className="accent">{item.docs.length}</span></IconHeading>
        {item.docs.length ? item.docs.map(doc => <div className="doc" key={doc.id}><div className="document-info"><div className="docname"><b>{doc.name}</b></div><div className="sub">{doc.type} – {Math.round(doc.size / 1024)} КБ – версия {doc.version}</div></div><div className="actions"><button className="btn small" onClick={() => actions.changeDocType(doc.id)}>Тип</button><button className="btn small" onClick={() => actions.viewDoc(doc.id)}>Открыть</button><button className="btn small" aria-label={'Удалить ' + doc.name + ' из комплекта'} onClick={() => actions.removeDoc(doc.id)}>×</button></div></div>) : <div className="empty">Документы ещё не добавлены</div>}
      </section>
    </div>
    <div className="warn documents-warning">Если в загруженном комплекте отсутствует документ, это не означает, что он отсутствует у сторон контракта</div><div className="actions"><button className="btn primary" onClick={() => actions.go('case', 'extract')}>К распознаванию →</button></div>
  </>;
}

function Recognition({ item, actions }) {
  return <section className="panel recognition-panel"><IconHeading icon="chip">Распознавание реквизитов</IconHeading><div className="warn">Реквизиты вводятся и уточняются вручную. Данные из файлов автоматически не извлекаются.</div>
    <div className="cols recognition-cols"><section className="recognition-sources"><IconHeading icon="folder">Исходные документы</IconHeading>{item.docs.length ? item.docs.map(doc => <div className="doc" key={doc.id}><span className="docname">{doc.name}</span><button className="btn small" onClick={() => actions.viewDoc(doc.id)}>Просмотреть</button></div>) : <p className="muted">Документы ещё не добавлены</p>}</section>
      <section className="recognition-fields"><CaseFields key={item.id} item={item} actions={actions} recognition/></section></div>
  </section>;
}

function Matching({ item, actions }) {
  return <><div className="panel"><IconHeading icon="layers">Сопоставление позиций</IconHeading><div className="warn">Автоматическое сопоставление не выполняется. Проверьте позиции и единицы измерения по документам.</div>
    {item.demo ? <div className="scroll"><table className="data-table matching-table"><thead><tr><th className="cell-description">Позиция</th><th className="cell-count">Контракт</th><th className="cell-count">Накладная</th><th className="cell-count">Приёмка</th><th className="cell-status">Состояние</th></tr></thead><tbody><tr><td className="cell-description">Скамейка парковая</td><td className="cell-count">10 шт.</td><td className="cell-count">8 шт.</td><td className="cell-count">10 шт.</td><td className="cell-status"><Pill status="Требует проверки"/></td></tr></tbody></table></div> : <div className="empty">Сопоставленные позиции пока отсутствуют.</div>}
  </div><div className="actions"><button className="btn primary" onClick={() => actions.go('case', 'results')}>К результатам →</button></div></>;
}

function Results({ item, actions }) {
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const signals = item.signals.filter(signal => (signal.type + ' ' + signal.item).toLowerCase().includes(query.toLowerCase()) && (!status || signal.status === status));
  return <>
    <Metrics values={[
      ['Обработанные документы', item.docs.length], ['Сопоставленные позиции', item.demo ? '1' : '—'],
      ['Сигналы', item.signals.length], ['Рассмотренные сигналы', item.signals.filter(reviewed).length],
    ]}/>
    {!item.demo && <div className="warn">Автоматическая сверка не выполняется. Отсутствие сигналов не означает, что расхождений нет.</div>}
    <div className="panel"><IconHeading icon="bell">Реестр расхождений</IconHeading>
      <div className="toolbar"><input id="signalSearch" aria-label="Поиск по позиции или типу" placeholder="Поиск по позиции или типу" value={query} onChange={event => setQuery(event.target.value)}/><select id="signalFilter" aria-label="Статус сигнала" value={status} onChange={event => setStatus(event.target.value)}><option value="">Все статусы</option>{signalStatuses.map(value => <option key={value}>{value}</option>)}</select></div>
      <div className="scroll"><table className="data-table signals-table"><thead><tr><th className="cell-id">ID</th><th className="cell-description">Тип</th><th className="cell-description">Позиция</th><th className="cell-status">Статус</th><th className="cell-action">Действие</th></tr></thead><tbody id="signalRows">
        {signals.length ? signals.map(signal => <tr key={signal.id}><td className="cell-id">{signal.id}</td><td className="cell-description">{signal.type}</td><td className="cell-description">{signal.item}</td><td className="cell-status"><Pill status={signal.status}/></td><td className="cell-action"><button className="btn small" onClick={() => actions.openSignal(signal.id)}>Рассмотреть <span className="action-arrow" aria-hidden="true">→</span></button></td></tr>) : <tr><td colSpan="5" className="table-empty"><span className="empty-message">Нет сигналов для отображения</span></td></tr>}
      </tbody></table></div>
    </div><div className="actions"><button className="btn" onClick={actions.exportQuestions}>Экспорт перечня вопросов</button><button className="btn" onClick={actions.finishCase}>Завершить рассмотрение</button></div>
  </>;
}

function History({ item }) {
  return <div className="panel"><IconHeading icon="alarm">Журнал действий</IconHeading>{item.history.length ? item.history.map((entry, index) => <div className="history" key={index}><b>{entry.text}</b><div className="sub">{entry.date}</div></div>) : <div className="empty">Действий пока нет</div>}</div>;
}

export function SignalPage({ item, signal, actions }) {
  const [status, setStatus] = useState(signal.status);
  const [comment, setComment] = useState('');
  return <>
    <Header eyebrow={'Результаты / ' + signal.id} title={signal.type} subtitle={signal.item}><button className="btn" onClick={() => actions.go('case', 'results')}>← К результатам</button></Header>
    <div className="cols"><div>
      <div className="panel"><IconHeading icon="bell">Основание сигнала</IconHeading><div className="detail"><div className="value"><span className="sub">Ожидаемое значение</span><b>{signal.expected}</b></div><div className="value"><span className="sub">Значение в комплекте</span><b>{signal.actual}</b></div></div>
        <div className="field"><label>Расчётное отклонение</label><strong>{signal.difference}</strong></div><div className="field"><label>Правило сопоставления</label><div className="note">{signal.rule}</div></div><div className="field"><label>Пояснение</label><p className="sub">{signal.explanation}</p></div><div className="field"><label>Определённость</label><p className="note">{signal.confidence}</p></div></div>
      <div className="panel"><IconHeading icon="folder">Документы-источники</IconHeading>{signal.sources.map(id => { const doc = item.docs.find(entry => entry.id === id); return doc ? <div className="doc" key={id}><span>{doc.name}</span><button className="btn small" onClick={() => actions.viewDoc(doc.id)}>Открыть</button></div> : null; })}<p className="note">Фрагменты не выделяются в документе автоматически.</p></div>
    </div><div>
      <div className="panel"><IconHeading icon="users">Рассмотрение специалистом</IconHeading><div className="field"><label htmlFor="signalStatus">Статус</label><select id="signalStatus" value={status} onChange={event => setStatus(event.target.value)}>{signalStatuses.map(value => <option key={value}>{value}</option>)}</select></div><div className="field"><label htmlFor="comment">Комментарий специалиста</label><textarea id="comment" rows="5" placeholder="Укажите основания решения или недостающие документы" value={comment} onChange={event => setComment(event.target.value)}/></div><button className="btn primary" onClick={() => { actions.saveSignal(status, comment); setComment(''); }}>Сохранить решение</button><p className="note">Подтверждение означает подтверждение документального расхождения, а не установление правонарушения.</p></div>
      <div className="panel"><IconHeading icon="alarm">История рассмотрения</IconHeading>{signal.history.length ? signal.history.map((entry, index) => <div className="history" key={index}><b>{entry.text}</b><div className="sub">{entry.date}</div></div>) : <div className="empty">Изменений пока нет</div>}</div>
    </div></div>
  </>;
}
