/* Node-only smoke check of rendered states. This is not a browser/E2E test. */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(source); // Parse the exact shipped browser script.
const fixture = JSON.parse(fs.readFileSync(path.join(root, 'fixtures/cases.json'))).find(c => c.id === 'T02');
const observed = JSON.parse(fs.readFileSync(path.join(root, 'reports/acceptance.json'))).cases.find(c => c.id === 'T02').observed;
const request = structuredClone(fixture.request);
request.manual_links = [{document_id:'delivery-1', line_id:'1', target_line_id:'1', reviewer:'Тест', comment:'Проверено по артикулу'}];
const dossier = {id:'case-1',title:'Тестовый комплект',contract_number:'ГК-2026/0142',description:'Синтетические данные',revision:3,documents:request.documents.map(document => ({id:document.id,revision:1,document})),runs:[{id:'run-1'}]};
// Validate request defaults exactly as serialized by Pydantic on real API input.
for (const d of dossier.documents) {
  d.document.extraction_warnings ||= [];
  d.document.fields_confidence ||= {};
  for (const item of d.document.items) item.confidence ||= {};
}
const run = {id:'run-1',case_id:dossier.id,request,result:observed,reviews:[],stale:false};
const elements = new Map(), listeners = {};
function element(selector) {
  if (!elements.has(selector)) elements.set(selector,{innerHTML:'',textContent:'',style:{},value:'',attributes:{},
    setAttribute(name,value){this.attributes[name]=String(value);},focus(){this.focused=true;},
    showModal(){this.open=true},close(){this.open=false}});
  return elements.get(selector);
}
const cases = [
  {id:dossier.id,title:dossier.title,contract_number:dossier.contract_number,document_count:dossier.documents.length,
    last_run_status:observed.status,signal_count:observed.signals.length,pending_signal_count:observed.signals.length},
  {id:'reviewed',title:'Рассмотренный комплект',contract_number:'ГК-0002',document_count:2,
    last_run_status:'needs_review',signal_count:2,pending_signal_count:0},
  {id:'outdated',title:'Изменённый комплект',contract_number:'ГК-0003',document_count:3,
    last_run_status:'ok',last_run_stale:true,signal_count:1,pending_signal_count:0},
];
const routes = {
  '/api/demo': [{id:'T02',title:fixture.title}],
  '/api/cases': cases,
  '/api/cases/case-1': dossier,
  '/api/runs/run-1':run,
  '/api/cases/case-1/events': [{action:'case_created',created_at:'2026-11-18T09:00:00Z',payload:{comment:'Комплект загружен'}}],
  '/api/runs?limit=25&offset=0': {items:[{id:run.id,case_id:dossier.id,case_title:dossier.title,
    created_at:'2026-11-18T10:00:00Z',status:observed.status,signal_count:observed.signals.length,stale:false}],total:1,limit:25,offset:0},
};
const location = {hash:''};
const context = vm.createContext({
  console, Date, encodeURIComponent, decodeURIComponent, structuredClone,
  setTimeout(){return 0;}, clearTimeout(){},
  location,history:{replaceState(_state,_unused,hash){location.hash=hash;}},
  sessionStorage:{getItem(){return null},setItem(){}},
  document:{querySelector:element,addEventListener(type,listener){listeners[type]=listener;}},
  fetch:async url=>{assert.ok(routes[url], 'Unexpected request: '+url);return {ok:true,json:async()=>structuredClone(routes[url])}},
});
function statValue(label) {
  const match=element('#main').innerHTML.match(new RegExp('<span>'+label+'</span><b>([^<]+)</b>'));
  assert.ok(match,'Missing summary: '+label);
  return Number(match[1]);
}
async function action(name, dataset={}) {
  const button={dataset:{action:name,...dataset},disabled:false};
  await listeners.click({target:{closest(){return button;}}});
  assert.equal(button.disabled,false,'Actions must become available again');
}
(async()=>{
  await vm.runInContext(source,context);
  assert.match(element('#main').innerHTML,/Проверки контрактов/);
  assert.match(element('#main').innerHTML,/T02/);
  assert.equal(element('#nav-checks').attributes['aria-current'],'page');
  assert.equal(location.hash,'#checks');
  assert.equal(statValue('Всего проверок'),3);
  assert.equal(statValue('В работе'),2,'An outdated run needs work even after its signals were reviewed');
  assert.equal(statValue('Рассмотрено'),1);
  assert.equal(statValue('Сигналов'),observed.signals.length+3);
  listeners.input({target:{id:'registry-query',value:'гк-2026/0142'}});
  assert.match(element('#registry-rows').innerHTML,/Тестовый комплект/);
  assert.doesNotMatch(element('#registry-rows').innerHTML,/Рассмотренный комплект|Изменённый комплект/);
  listeners.input({target:{id:'registry-query',value:''}});
  listeners.change({target:{id:'registry-status',value:'reviewed'}});
  assert.match(element('#registry-rows').innerHTML,/Рассмотренный комплект/);
  assert.doesNotMatch(element('#registry-rows').innerHTML,/Тестовый комплект|Изменённый комплект/);
  listeners.change({target:{id:'registry-status',value:'all'}});
  await action('open',{id:'case-1'});
  assert.match(element('#main').innerHTML,/Данные контракта/);
  assert.equal(element('#nav-workspace').attributes['aria-current'],'page');
  assert.equal(location.hash,'#case/case-1/overview');
  assert.equal(statValue('Документов'),dossier.documents.length);
  assert.equal(statValue('Ожидают решения'),observed.signals.length);
  assert.equal(vm.runInContext('state.options.manual_links.length',context),1,'Saved manual links must survive reopening');
  assert.equal(vm.runInContext('state.options.as_of',context),request.as_of);
  await action('tab',{tab:'documents'});
  assert.match(element('#main').innerHTML,/Загрузка документов/);
  assert.match(element('#main').innerHTML,/data-action="type"/);
  assert.match(element('#main').innerHTML,/data-action="view"/);
  await action('tab',{tab:'recognition'});
  assert.match(element('#main').innerHTML,/Распознавание реквизитов/);
  assert.match(element('#main').innerHTML,/data-action="edit"/);
  assert.doesNotMatch(element('#main').innerHTML,/OCR и извлечения данных ещё не подключён/);
  await action('tab',{tab:'matching'});
  assert.match(element('#main').innerHTML,/Сопоставление позиций/);
  assert.match(element('#main').innerHTML,/К результатам/);
  assert.match(element('#main').innerHTML,/data-tab="matching"[^>]*aria-selected="true"/);
  await action('tab',{tab:'results'});
  assert.match(element('#main').innerHTML,/Приёмка превышает документы поставки/);
  assert.match(element('#main').innerHTML,/Остаток/);
  assert.equal(vm.runInContext('state.options.manual_links.length',context),1,'Switching screens must retain saved comparison settings');
  assert.equal(vm.runInContext('state.options.as_of',context),request.as_of);
  vm.runInContext('state.run.stale=true;render()',context);
  assert.match(element('#main').innerHTML,/Документы изменены после этой проверки/);
  await action('tab',{tab:'actions'});
  assert.match(element('#events').innerHTML,/Создана проверка/);
  assert.match(element('#events').innerHTML,/Комплект загружен/);
  await action('global-history');
  assert.equal(element('#nav-history').attributes['aria-current'],'page');
  assert.match(element('#main').innerHTML,/Журнал проверок/);
  assert.match(element('#main').innerHTML,/Тестовый комплект/);
  await action('home');
  await action('workspace');
  assert.equal(vm.runInContext('state.current.id',context),dossier.id,'Workspace navigation must reopen the last dossier');
  location.hash='#case/case-1/results';
  await vm.runInContext('routeFromHash()',context);
  assert.equal(vm.runInContext('state.tab',context),'results');
  assert.match(element('#main').innerHTML,/Приёмка превышает документы поставки/);
  vm.runInContext('editDocument("delivery-1")',context);
  assert.equal(element('#editor').open,true);
  assert.match(element('#editor').innerHTML,/Сохранить исправления/);
  const escaped=vm.runInContext('esc("<img src=x onerror=alert(1)>")',context);
  assert.ok(!escaped.includes('<img'));
  console.log(JSON.stringify({status:'passed',checks:['syntax','registry rendering and counts','registry search and status filters','workspace navigation','document and recognition screens','matching and result screens','signal rendering','saved options','stale notice','case events and global history','deep links','editor rendering','escaping'],scope:'Node VM with minimal document stubs; not a real browser or visual test'},null,2));
})().catch(error=>{console.error(error);process.exitCode=1});
