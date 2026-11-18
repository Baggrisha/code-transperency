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
const dossier = {id:'case-1',title:'Тестовый комплект',revision:3,documents:request.documents.map(document => ({id:document.id,revision:1,document})),runs:[{id:'run-1'}]};
// Validate request defaults exactly as serialized by Pydantic on real API input.
for (const d of dossier.documents) {
  d.document.extraction_warnings ||= [];
  d.document.fields_confidence ||= {};
  for (const item of d.document.items) item.confidence ||= {};
}
const run = {id:'run-1',case_id:dossier.id,request,result:observed,reviews:[],stale:false};
const elements = new Map();
function element(selector) {
  if (!elements.has(selector)) elements.set(selector,{innerHTML:'',textContent:'',style:{},value:'',showModal(){this.open=true},close(){this.open=false}});
  return elements.get(selector);
}
const routes = {
  '/api/demo': [{id:'T02',title:fixture.title}],
  '/api/cases': [{id:dossier.id,title:dossier.title,document_count:dossier.documents.length}],
  '/api/cases/case-1': dossier,
  '/api/runs/run-1':run,
};
const context = vm.createContext({
  console, Date, encodeURIComponent, structuredClone,
  setTimeout, clearTimeout,
  sessionStorage:{getItem(){return null},setItem(){}},
  document:{querySelector:element,addEventListener(){}},
  fetch:async url=>{assert.ok(routes[url], 'Unexpected request: '+url);return {ok:true,json:async()=>structuredClone(routes[url])}},
});
(async()=>{
  await vm.runInContext(source,context);
  assert.match(element('#main').innerHTML,/Проверьте, сходятся ли условия и поставка/);
  assert.match(element('#main').innerHTML,/T02/);
  await vm.runInContext('openCase("case-1")',context);
  assert.match(element('#main').innerHTML,/Приёмка превышает документы поставки/);
  assert.match(element('#main').innerHTML,/Остаток/);
  assert.equal(vm.runInContext('state.options.manual_links.length',context),1,'Saved manual links must survive reopening');
  assert.equal(vm.runInContext('state.options.as_of',context),request.as_of);
  vm.runInContext('state.run.stale=true;render()',context);
  assert.match(element('#main').innerHTML,/Документы изменены после этой проверки/);
  vm.runInContext('editDocument("delivery-1")',context);
  assert.equal(element('#editor').open,true);
  assert.match(element('#editor').innerHTML,/Сохранить исправления/);
  const escaped=vm.runInContext('esc("<img src=x onerror=alert(1)>")',context);
  assert.ok(!escaped.includes('<img'));
  console.log(JSON.stringify({status:'passed',checks:['syntax','home rendering','signal rendering','saved options','stale notice','editor rendering','escaping'],scope:'Node VM with minimal document stubs; not a real browser or visual test'},null,2));
})().catch(error=>{console.error(error);process.exitCode=1});
