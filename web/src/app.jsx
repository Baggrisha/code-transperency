import React, {useEffect, useRef, useState} from 'react';
import {Action, Icon, enc, tabs, types} from './appShared.jsx';
import {History, Registry, Workspace} from './appViews.jsx';
import {Dialogs} from './appDialogs.jsx';

function initialState() {
  const preview = Boolean(globalThis.KONTROL_PREVIEW);
  return {cases:[], demos:[], current:null, run:null, options:null, token:preview ? '' : sessionStorage.getItem('kontrol_token') || '', busy:0,
    page:'registry', tab:'overview', lastCaseId:null, query:'', filter:'all', files:[], events:null, eventsError:'', history:null, historyError:'', historyOffset:0,
    preview, selectedRunId:null, modal:null, editor:null, toast:null, loading:true, loadError:''};
}
export default function App() {
  const [state,setState] = useState(initialState), current = useRef(state), tickets = useRef({case:0,run:0,modal:0,editor:0}), toastTimer = useRef(null);
  function update(patch) { const next = {...current.current,...patch}; current.current = next; setState(next); }
  function toast(message,error = false) {
    clearTimeout(toastTimer.current); update({toast:{message,error}});
    toastTimer.current = setTimeout(() => update({toast:null}),error ? 10000 : 5000);
  }
  async function api(url,options = {}) {
    update({busy:current.current.busy + 1});
    try {
      if (current.current.preview) return await globalThis.KONTROL_PREVIEW.api(url,options);
      const headers = {...(current.current.token ? {Authorization:'Bearer ' + current.current.token} : {}), ...(options.body && !(options.body instanceof FormData) ? {'Content-Type':'application/json'} : {}), ...options.headers};
      const response = await fetch(url,{...options,headers});
      if (!response.ok) {
        let payload; try { payload = await response.json(); } catch { payload = {detail:response.statusText}; }
        const error = new Error(typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload.detail)); error.status = response.status; throw error;
      }
      return options.blob ? await response.blob() : await response.json();
    } finally { update({busy:Math.max(0,current.current.busy - 1)}); }
  }
  function setRoute(hash) { if (globalThis.history?.replaceState && globalThis.location) globalThis.history.replaceState(null,'',hash); }
  function workspaceRoute(id,tab,runId) { setRoute('#case/' + enc(id) + '/' + tab + (runId ? '/run/' + enc(runId) : '')); }
  function workspaceContext() {
    const s = current.current;
    return {caseId:s.current?.id,runId:s.run?.id,ticket:tickets.current.case,options:structuredClone(s.options || {}),selectedRunId:s.selectedRunId};
  }
  function isCurrentWorkspace(context,sameRun = false) {
    const s = current.current;
    return context.ticket === tickets.current.case && s.page === 'workspace' && s.current?.id === context.caseId && (!sameRun || s.run?.id === context.runId);
  }
  async function afterSaved(message,refresh) {
    try { await refresh(); toast(message); } catch (error) { toast(message + ' Не удалось обновить экран. Откройте проверку заново: ' + error.message,true); }
  }
  async function refreshList() { const cases = await api('/api/cases'); update({cases,loadError:'',loading:false}); }
  async function home(refresh = true) {
    tickets.current.case++; update({page:'registry',current:null,run:null,selectedRunId:null,files:[]}); setRoute('#checks');
    if (refresh) await refreshList();
  }
  async function openCase(id,options,tab,runId) {
    const ticket = ++tickets.current.case, previousId = current.current.current?.id;
    const selectedRunId = runId === undefined ? (previousId === id ? current.current.selectedRunId : null) : runId;
    const dossier = await api('/api/cases/' + enc(id));
    if (ticket !== tickets.current.case) return false;
    const targetRunId = selectedRunId || dossier.runs[0]?.id;
    const savedRun = targetRunId ? await api('/api/runs/' + enc(targetRunId)) : null;
    if (ticket !== tickets.current.case) return false;
    if (savedRun?.case_id && savedRun.case_id !== id) throw new Error('Этот результат относится к другой проверке');
    const previous = savedRun?.request ? structuredClone(savedRun.request) : null; if (previous) delete previous.documents;
    const nextTab = tab || (previousId === id ? current.current.tab : 'overview');
    update({current:dossier,run:savedRun,selectedRunId:selectedRunId || null,lastCaseId:id,page:'workspace',tab:nextTab,files:[],events:null,eventsError:'',options:options || previous || {as_of:new Date().toLocaleDateString('sv-SE'),delivery_complete:false},loading:false});
    workspaceRoute(id,nextTab,selectedRunId);
    await refreshList();
    if (ticket !== tickets.current.case || current.current.current?.id !== id || current.current.page !== 'workspace') return false;
    if (nextTab === 'actions') await loadEvents();
    return ticket === tickets.current.case && current.current.page === 'workspace' && current.current.current?.id === id;
  }
  async function setTab(tab) {
    if (!tabs.some(([key]) => key === tab) || !current.current.current) return;
    const s = current.current; update({tab,files:[],...(tab === 'actions' ? {events:null,eventsError:''} : {})}); workspaceRoute(s.current.id,tab,s.selectedRunId);
    if (tab === 'actions') await loadEvents();
  }
  function updateOptions(patch) { update({options:{...current.current.options,...patch}}); }
  async function loadEvents() {
    const context = workspaceContext(); if (!context.caseId) return;
    update({events:null,eventsError:''});
    try {
      const events = await api(`/api/cases/${enc(context.caseId)}/events`);
      if (isCurrentWorkspace(context) && current.current.tab === 'actions') update({events});
    } catch (error) { if (isCurrentWorkspace(context) && current.current.tab === 'actions') update({eventsError:error.message}); throw error; }
  }
  async function showHistory(offset = 0) {
    const ticket = ++tickets.current.case; update({page:'history',historyOffset:offset,history:null,historyError:'',current:null,run:null,selectedRunId:null}); setRoute('#history');
    try {
      const history = await api('/api/runs?limit=25&offset=' + offset);
      if (ticket === tickets.current.case && current.current.page === 'history' && current.current.historyOffset === offset) update({history});
    } catch (error) { if (ticket === tickets.current.case && current.current.page === 'history' && current.current.historyOffset === offset) update({historyError:error.message}); throw error; }
  }
  async function routeFromHash() {
    const hash = (globalThis.location?.hash || '#checks').slice(1);
    const match = hash.match(/^case\/([^/]+)\/(overview|documents|recognition|matching|results|actions)(?:\/run\/([^/]+))?$/);
    if (match) return openCase(decodeURIComponent(match[1]),null,match[2],match[3] ? decodeURIComponent(match[3]) : null);
    if (hash === 'history') return showHistory();
    return home(false);
  }
  useEffect(() => {
    let active = true;
    const onHashChange = () => routeFromHash().catch(error => toast(error.message,true));
    globalThis.addEventListener('hashchange',onHashChange);
    (async () => {
      try {
        const initialHash = globalThis.location?.hash, demos = await api('/api/demo'), cases = await api('/api/cases');
        if (!active) return; update({demos,cases,loading:false,loadError:''});
        if (initialHash) await routeFromHash();
        else if (current.current.preview) await openCase(globalThis.KONTROL_PREVIEW_SEED?.dossier?.id || 'demo-001',null,'matching',null);
        else await home(false);
      } catch (error) {
        if (!active) return;
        await home(false); update({loading:false,loadError:error.status === 401 ? 'Введите токен сервера через кнопку «Доступ» в боковом меню.' : 'Не удалось загрузить данные: ' + error.message});
        toast(error.status === 401 ? 'Введите токен сервера через кнопку «Доступ» в боковом меню.' : 'Не удалось загрузить данные: ' + error.message,true);
      }
    })();
    return () => { active = false; globalThis.removeEventListener('hashchange',onHashChange); clearTimeout(toastTimer.current); };
  },[]);
  function openModal(details) { const ticket = ++tickets.current.modal; update({modal:{...details,ticket}}); }
  function closeModal(ticket) { if (typeof ticket === 'number' && current.current.modal?.ticket !== ticket) return; tickets.current.modal++; update({modal:null}); }
  function closeEditor(ticket) { if (typeof ticket === 'number' && current.current.editor?.ticket !== ticket) return; tickets.current.editor++; update({editor:null}); }
  function findRecord(id) { const record = current.current.current?.documents.find(item => item.id === id); if (!record) throw new Error('Документ не найден в текущем комплекте'); return record; }
  function newCase() { openModal({type:'new'}); }
  function openType(id) { openModal({type:'type',record:findRecord(id),context:workspaceContext()}); }
  function openStatus(id) { openModal({type:'status',record:findRecord(id),context:workspaceContext()}); }
  function openEditor(id) {
    const record = findRecord(id), s = current.current;
    update({editor:{ticket:++tickets.current.editor,record,caseId:s.current.id,documents:s.current.documents,context:workspaceContext()}});
  }
  function openViewer(id,snapshot = false) {
    const s = current.current, record = s.current?.documents.find(item => item.id === id), snapshots = snapshot ? s.run?.request?.documents : null;
    const doc = snapshots ? snapshots.find(item => item.id === id) : record?.document;
    if (!doc) throw new Error(snapshots ? 'Документ отсутствует в сохранённом снимке сверки' : 'Документ не найден');
    openModal({type:'viewer',id,record,doc,snapshot,context:workspaceContext()});
  }
  function editFromViewer(modal) {
    if (!isCurrentWorkspace(modal.context)) throw new Error('Откройте комплект заново');
    closeModal(modal.ticket);
    openEditor(modal.id);
  }
  async function saveMetadata(form) {
    const context = workspaceContext(), payload = Object.fromEntries(new FormData(form)); payload.title = payload.title.trim();
    if (!payload.title) throw new Error('Укажите наименование проверки');
    payload.expected_metadata_revision = current.current.current.metadata_revision || 0;
    const metadata = await api('/api/cases/' + enc(context.caseId),{method:'PUT',body:JSON.stringify(payload)});
    if (isCurrentWorkspace(context)) update({current:{...current.current.current,...Object.fromEntries(['title','description','contract_number','metadata_revision','updated_at'].map(key => [key,metadata[key]]))}});
    await afterSaved('Реквизиты карточки сохранены.',refreshList);
  }
  async function createCase(form) {
    const ticket = tickets.current.case, modalTicket = current.current.modal?.ticket, payload = Object.fromEntries(new FormData(form)); payload.title = payload.title.trim();
    if (!payload.title) throw new Error('Укажите наименование проверки');
    const dossier = await api('/api/cases',{method:'POST',body:JSON.stringify(payload)});
    closeModal(modalTicket);
    await afterSaved('Проверка создана.',async () => {if (ticket === tickets.current.case) await openCase(dossier.id,null,'documents',null); else await refreshList();});
  }
  async function saveToken(form) {
    const token = new FormData(form).get('token').trim(); sessionStorage.setItem('kontrol_token',token); update({token}); closeModal();
    const demos = await api('/api/demo'); update({demos}); await refreshList(); toast('Настройка доступа сохранена');
  }
  async function saveType(modal,selected,form) {
    const {record,context,ticket} = modal;
    if (selected === 'corrected') { closeModal(ticket); if (!isCurrentWorkspace(context)) throw new Error('Откройте комплект заново'); openEditor(record.id); toast('Укажите заменяемый документ и редакцию. Категория документа сохраняется.'); return; }
    if (!Object.hasOwn(types,selected) && !current.current.preview) throw new Error('Выберите поддерживаемую категорию документа');
    if (selected === record.document.type) { closeModal(ticket); return; }
    const values = Object.fromEntries(new FormData(form)), updated = structuredClone(record.document); updated.type = selected; updated.fields_confidence = {...updated.fields_confidence,type:0};
    await api(`/api/cases/${enc(context.caseId)}/documents/${enc(record.id)}`,{method:'PUT',body:JSON.stringify({document:updated,expected_revision:record.revision,reviewer:values.reviewer?.trim() || (current.current.preview ? 'Пользователь' : ''),comment:values.comment?.trim() || (current.current.preview ? 'Изменён тип документа' : ''),confirmed_fields:[],confirmed_items:{}})});
    closeModal(ticket);
    await afterSaved('Тип сохранён. Подтвердите его по оригиналу в реквизитах и повторите сверку.',async () => {if (isCurrentWorkspace(context)) await openCase(context.caseId,context.options,current.current.tab,context.selectedRunId); else await refreshList();});
  }
  async function saveStatus(modal,status,form) {
    const {record,context,ticket} = modal, values = Object.fromEntries(new FormData(form)), reviewer = values.reviewer.trim(), comment = values.comment.trim();
    if (!reviewer || !comment) throw new Error('Укажите проверяющего и причину');
    const updated = structuredClone(record.document); updated.status = status; updated.fields_confidence = {...updated.fields_confidence,status:1};
    await api(`/api/cases/${enc(context.caseId)}/documents/${enc(record.id)}`,{method:'PUT',body:JSON.stringify({document:updated,expected_revision:record.revision,reviewer,comment,confirmed_fields:['status'],confirmed_items:{}})});
    closeModal(ticket);
    await afterSaved('Статус сохранён. Повторите сверку для обновления результата.',async () => {if (isCurrentWorkspace(context)) await openCase(context.caseId,context.options,current.current.tab,context.selectedRunId); else await refreshList();});
  }
  async function saveEditor(editor,payload) {
    const context = workspaceContext();
    await api(`/api/cases/${enc(editor.caseId)}/documents/${enc(editor.record.id)}`,{method:'PUT',body:JSON.stringify(payload)});
    closeEditor(editor.ticket);
    await afterSaved('Исправление сохранено. Выполните сверку ещё раз.',async () => {if (context.caseId === editor.caseId && isCurrentWorkspace(context)) await openCase(editor.caseId,context.options,current.current.tab,context.selectedRunId); else await refreshList();});
  }
  async function editorHistory(editor) { return api(`/api/cases/${enc(editor.caseId)}/documents/${enc(editor.record.id)}/history`); }
  function fetchSource(caseId,id) { return api(`/api/cases/${enc(caseId)}/documents/${enc(id)}/file`,{blob:true}); }
  async function download(url,name) {
    const blob = await api(url,{blob:true}), objectURL = URL.createObjectURL(blob), link = document.createElement('a'); link.href = objectURL; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(objectURL),1000);
  }
  function downloadSource(id,caseId,record) { return download(`/api/cases/${enc(caseId)}/documents/${enc(id)}/file`,record?.document.filename || 'document'); }
  function exportRun(format) { return download(`/api/runs/${enc(current.current.run.id)}/export?format=${format}`,'proverka.' + format); }
  function selectFiles(files) { update({files:Array.from(files)}); }
  async function uploadFiles(form) {
    const s = current.current; if (!s.files.length) throw new Error('Выберите файлы');
    const context = workspaceContext(), files = s.files.slice(), values = Object.fromEntries(new FormData(form)), errors = [], successful = [];
    for (const file of files) {
      try {
        if (file.size > 20 * 1024 * 1024) throw new Error('Файл больше 20 МБ');
        if (!/\.(pdf|docx|txt|csv|tsv|json|png|jpe?g|tiff?)$/i.test(file.name)) throw new Error('Неподдерживаемый формат');
        const data = new FormData(); data.append('file',file); if (values.document_type) data.append('document_type',values.document_type); if (values.contract_id.trim()) data.append('contract_id',values.contract_id.trim());
        const response = await api(`/api/cases/${enc(context.caseId)}/documents`,{method:'POST',body:data}); successful.push(response.duplicate ? `${file.name}: повторный файл исключён` : file.name);
      } catch (error) { errors.push({file,message:error.message}); }
    }
    let refreshed = false;
    try { if (isCurrentWorkspace(context)) refreshed = await openCase(context.caseId,context.options,current.current.tab,context.selectedRunId); else await refreshList(); }
    catch (error) { toast(`${successful.length ? 'Обработано файлов: ' + successful.length + '. ' : ''}Не удалось обновить комплект: ${error.message}${errors.length ? '. Ошибки загрузки: ' + errors.map(item => item.file.name + ': ' + item.message).join('; ') : ''}`,true); return; }
    if (errors.length) { if (refreshed && current.current.current?.id === context.caseId && current.current.tab === 'documents') selectFiles(errors.map(item => item.file)); toast(`${successful.length ? 'Обработано: ' + successful.length + '. ' : ''}${errors.map(item => item.file.name + ': ' + item.message).join('; ')}`,true); }
    else toast('Документы обработаны: ' + successful.join(', '));
  }
  async function run() {
    const context = workspaceContext(); if (!context.caseId) throw new Error('Откройте проверку');
    const options = structuredClone(current.current.options || {}); if (!options.as_of) throw new Error('Укажите дату среза');
    const ticket = ++tickets.current.run, response = await api(`/api/cases/${enc(context.caseId)}/runs`,{method:'POST',body:JSON.stringify(options)});
    await afterSaved('Сверка выполнена.',async () => {
      if (isCurrentWorkspace(context) && tickets.current.run === ticket) {
        const savedRun = await api('/api/runs/' + enc(response.id)), dossier = await api('/api/cases/' + enc(context.caseId));
        if (isCurrentWorkspace(context) && tickets.current.run === ticket) { update({run:savedRun,current:dossier,selectedRunId:null}); workspaceRoute(context.caseId,current.current.tab,null); }
      }
      await refreshList();
    }); return response;
  }
  async function saveLinks(form) {
    const values = Object.fromEntries(new FormData(form)), reviewer = values.reviewer.trim(), comment = values.comment.trim(); if (!reviewer || !comment) throw new Error('Укажите проверяющего и основание');
    const links = [...form.querySelectorAll('[data-link-document]')].filter(node => node.value).map(node => ({document_id:node.dataset.linkDocument,line_id:node.dataset.linkLine,target_line_id:node.value,reviewer,comment}));
    const options = current.current.options || {}; updateOptions({manual_links:[...(options.manual_links || []).filter(old => !links.some(link => link.document_id === old.document_id && link.line_id === old.line_id)),...links]}); await run();
  }
  async function saveReview(signalId,form) {
    const context = workspaceContext(), data = Object.fromEntries(new FormData(form)); data.reviewer = data.reviewer.trim(); data.comment = data.comment.trim(); if (!data.reviewer || !data.comment) throw new Error('Укажите проверяющего и комментарий');
    await api(`/api/runs/${enc(context.runId)}/signals/${enc(signalId)}/reviews`,{method:'POST',body:JSON.stringify(data)});
    form.reset();
    form.elements.status.value = data.status;
    await afterSaved('Решение сохранено в истории.',async () => {if (isCurrentWorkspace(context,true)) {const savedRun = await api('/api/runs/' + enc(context.runId)); if (isCurrentWorkspace(context,true)) update({run:savedRun});} await refreshList();});
  }
  async function demo(id) {
    const ticket = tickets.current.case, response = await api('/api/demo/' + enc(id),{method:'POST'});
    try { if (ticket !== tickets.current.case) {await refreshList(); toast('Проверка создана в реестре');} else if (await openCase(response.case.id,response.options,'overview',null)) {if (!current.current.preview) await run();} }
    catch (error) {toast('Проверка создана, но открыть или выполнить её не удалось: ' + error.message,true);}
  }
  const app = {state,update,toast,home,openCase,setTab,updateOptions,loadEvents,showHistory,newCase,openType,openStatus,openViewer,openEditor,editFromViewer,closeModal,closeEditor,saveMetadata,createCase,saveToken,saveType,saveStatus,saveEditor,editorHistory,fetchSource,downloadSource,exportRun,selectFiles,uploadFiles,run,saveLinks,saveReview,demo,openToken:() => openModal({type:'token'}),retry:async () => {const demos = await api('/api/demo'); update({demos}); await refreshList();}};
  return <><a href="#main" className="skip-link" onClick={event => {event.preventDefault(); document.getElementById('main')?.focus();}}>Перейти к содержимому</a><div className="app-shell"><aside className="sidebar" aria-label="Основная навигация"><a className="brand wordmark" href="#checks" aria-label="uzniki’s project, все проверки"><span>uzniki’s</span><span className="brand-accent">project</span></a><nav className="side-nav"><Action app={app} className={`nav-item${state.page === 'registry' ? ' active' : ''}`} id="nav-checks" aria-current={state.page === 'registry' ? 'page' : 'false'} action={home}><Icon name="layers"/><span>Все проверки</span></Action><Action app={app} className={`nav-item${state.page === 'workspace' ? ' active' : ''}`} id="nav-workspace" aria-current={state.page === 'workspace' ? 'page' : 'false'} action={() => {const id = current.current.current?.id || current.current.lastCaseId || current.current.cases[0]?.id; return id ? openCase(id) : newCase();}}><Icon name="home"/><span>Рабочая область</span></Action><Action app={app} className={`nav-item${state.page === 'history' ? ' active' : ''}`} id="nav-history" aria-current={state.page === 'history' ? 'page' : 'false'} action={() => showHistory()}><Icon name="alarm"/><span>История проверок</span></Action></nav><div className="sidebar-bottom"><div className="sidebar-tools"><Action app={app} className="ghost" action={app.openToken}>Доступ</Action>{!state.preview && <a id="api-link" href="/docs" target="_blank" rel="noopener">API ↗</a>}<span id="busy" role="status" aria-live="polite" style={{display:state.busy ? 'inline' : 'none'}}>Обработка…</span></div></div></aside><main id="main" className={`main page-${state.page}`} tabIndex="-1">{state.loading ? <p className="muted">Загрузка проверок…</p> : state.page === 'workspace' && state.current ? <Workspace app={app}/> : state.page === 'history' ? <History app={app}/> : <Registry app={app}/>}</main></div><Dialogs app={app}/><div id="toast" role="status" aria-live="polite" aria-atomic="true" className={state.toast?.error ? 'error' : ''} style={{display:state.toast ? 'block' : 'none'}}>{state.toast?.message}</div></>;
}
