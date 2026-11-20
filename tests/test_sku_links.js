/* Behavioral tests of SKU link rendering with a minimal Node DOM; not a browser test. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const decode = value => value.replace(/&(amp|lt|gt|quot|#39);/g, (_, name) => ({amp:'&',lt:'<',gt:'>',quot:'"','#39':"'"})[name]);

function linkSelects(markup) {
  return [...markup.matchAll(/<select\b([^>]*)>([\s\S]*?)<\/select>/g)].flatMap(([, attrs, content]) => {
    const doc = attrs.match(/\bdata-link-document="([^"]*)"/);
    const line = attrs.match(/\bdata-link-line="([^"]*)"/);
    if (!doc || !line) return [];
    const options = [...content.matchAll(/<option\b([^>]*)>([\s\S]*?)<\/option>/g)].map(([, optionAttrs, text]) => ({
      value:decode(optionAttrs.match(/\bvalue="([^"]*)"/)[1]),
      text:decode(text),
      selected:/\bselected\b/.test(optionAttrs),
    }));
    return [{dataset:{linkDocument:decode(doc[1]),linkLine:decode(line[1])}, options,
      value:(options.find(option => option.selected) || options[0]).value}];
  });
}

function documentFixture(id, type, items) {
  return {id,type,items,filename:id+'.pdf',number:null,date:null,status:'active',is_complete:true};
}
function fixtures() {
  const documents = [
    documentFixture('delivery', 'delivery', [
      {line_id:'source-1',sku:'ОРИГ<&"',name:'Исходный товар'},
    ]),
    documentFixture('other-spec', 'specification', [
      {line_id:'1',sku:'ЧУЖОЙ-1',name:'Чужая позиция'},
      {line_id:'2',sku:'ЧУЖОЙ-2',name:'Другая чужая позиция'},
    ]),
    documentFixture('target-spec', 'specification', [
      {line_id:'1',sku:'ЦЕЛЬ<&"-1',name:'Первая позиция'},
      {line_id:'2',sku:'ЦЕЛЬ-2',name:'Вторая позиция'},
      {line_id:'3',sku:'ДРУГОЙ-3',name:'Ручной вариант'},
    ]),
  ];
  const result = {status:'incomplete',signals:[],matches:[],positions:[],audit:[],
    engine_version:'test',as_of:'2026-10-31',input_hash:'test',result_hash:'test'};
  const match = {document_id:'delivery',line_id:'source-1',target_document_id:'target-spec',
    target_line_id:null,state:'needs_review',method:'similar_sku',score:0.9,
    candidates:['1','2'],explanation:'Сверьте с оригиналом <документа>'};
  return {documents,result,match};
}

async function setup({documents, savedDocuments=documents, result, includeRequestDocuments=true, stale=false}) {
  const request = {as_of:'2026-10-31',delivery_complete:false,manual_links:[]};
  if (includeRequestDocuments) request.documents=documents;
  const dossier = {id:'case-1',title:'Синтетический комплект',revision:2,
    documents:savedDocuments.map(document => ({id:document.id,revision:1,document})),runs:[{id:'run-1'}]};
  const run = {id:'run-1',request,result,reviews:[],stale};
  const elements = new Map(), listeners = {}, posted = [];
  let selects = [];
  function element(selector) {
    if (!elements.has(selector)) {
      const node = {textContent:'',style:{},value:'',checked:false,attributes:{},
        setAttribute(name,value){this.attributes[name]=String(value);},focus(){},showModal(){},close(){}};
      Object.defineProperty(node, 'innerHTML', {get(){return this._html || '';},set(value){
        this._html=value;
        if (selector === '#main') selects=linkSelects(value);
      }});
      elements.set(selector,node);
    }
    return elements.get(selector);
  }
  const context = vm.createContext({
    console,Date,encodeURIComponent,structuredClone,setTimeout(){return 0;},clearTimeout(){},
    FormData:class FormData {},sessionStorage:{getItem(){return null;},setItem(){}},
    document:{querySelector:element,querySelectorAll(selector){
      assert.equal(selector, '[data-link-document]');
      return selects;
    },addEventListener(type, listener){listeners[type]=listener;}},
    fetch:async (url, options={}) => {
      if (url === '/api/cases/case-1/runs' && options.method === 'POST') {
        posted.push(JSON.parse(options.body));
        return {ok:true,json:async()=>({id:'run-2'})};
      }
      const routes = {'/api/demo':[], '/api/cases':[], '/api/cases/case-1':dossier,
        '/api/runs/run-1':run, '/api/runs/run-2':run};
      assert.ok(Object.hasOwn(routes,url), `Unexpected request: ${url}`);
      return {ok:true,json:async()=>structuredClone(routes[url])};
    },
  });
  await vm.runInContext(script,context);
  await vm.runInContext('openCase("case-1")',context);
  vm.runInContext('state.tab="matching";render()',context);
  return {element,selects:()=>selects,posted,async applyLinks() {
    const button={dataset:{action:'links'},disabled:false};
    await listeners.click({target:{closest(){return button;}}});
  }};
}

test('uncertain SKU proposals use the correct target snapshot, are all marked, and remain unselected', async () => {
  const {documents,result,match}=fixtures();
  result.matches=[match];
  const savedDocuments=structuredClone(documents);
  savedDocuments[0].items[0].sku='НОВЫЙ-ИСТОЧНИК';
  savedDocuments[2].items[0].sku='НОВАЯ-ЦЕЛЬ';
  const app=await setup({documents,savedDocuments,result,stale:true});
  const markup=app.element('#main').innerHTML;
  const [select]=app.selects();
  assert.match(markup,/Исходный артикул/);
  assert.ok(markup.includes('ОРИГ&lt;&amp;&quot;'));
  assert.ok(!markup.includes('НОВЫЙ-ИСТОЧНИК'));
  assert.match(markup,/оригинал/i);
  assert.deepEqual(select.options.map(option=>option.value),['','1','2','3']);
  assert.equal(select.value,'');
  assert.ok(select.options.every(option=>!option.selected));
  assert.match(select.options[0].text,/Оставить без подтверждения/);
  assert.match(select.options[1].text,/Предложение.*ЦЕЛЬ<&"-1/);
  assert.match(select.options[2].text,/Предложение.*ЦЕЛЬ-2/);
  assert.doesNotMatch(select.options[3].text,/Предложение/);
  assert.match(select.options[3].text,/ДРУГОЙ-3/);
  assert.doesNotMatch(select.options.map(option=>option.text).join(' '),/ЧУЖОЙ|НОВАЯ-ЦЕЛЬ/);
  assert.ok(!markup.includes('ЦЕЛЬ<&"-1'));
  assert.ok(markup.includes('Сверьте с оригиналом &lt;документа&gt;'));
});

test('the existing manual link action sends no proposed link until a person selects it', async () => {
  const {documents,result,match}=fixtures();
  result.matches=[match];
  const app=await setup({documents,result});
  app.element('#link-reviewer').value='Проверяющий';
  app.element('#link-comment').value='Сверено с оригиналами';
  app.element('#asof').value='2026-10-31';
  await app.applyLinks();
  assert.deepEqual(app.posted[0].manual_links,[]);
  app.selects()[0].value='2';
  await app.applyLinks();
  assert.deepEqual(app.posted[1].manual_links,[{document_id:'delivery',line_id:'source-1',
    target_line_id:'2',reviewer:'Проверяющий',comment:'Сверено с оригиналами'}]);
});

test('automatic similar SKU matches are visible with both original values and no manual controls', async () => {
  const {documents,result,match}=fixtures();
  result.matches=[{...match,state:'matched',method:'similar_sku_auto',target_line_id:'1',candidates:['1','2']}];
  const app=await setup({documents,result});
  const markup=app.element('#main').innerHTML;
  assert.match(markup,/Автоматически сопоставленные похожие артикулы/);
  assert.match(markup,/Связано автоматически/);
  assert.match(markup,/Остальные реквизиты сверяются отдельно/);
  assert.ok(markup.includes('ОРИГ&lt;&amp;&quot;'));
  assert.ok(markup.includes('ЦЕЛЬ&lt;&amp;&quot;-1'));
  assert.doesNotMatch(markup,/ЧУЖОЙ-1/);
  assert.match(markup,/target-spec/);
  assert.equal(app.selects().length,0);
  assert.doesNotMatch(markup,/data-action="links"/);
});

test('saved case documents are used when the run has no document snapshot', async () => {
  const {documents,result,match}=fixtures();
  result.matches=[match];
  const app=await setup({documents,result,includeRequestDocuments:false});
  assert.ok(app.element('#main').innerHTML.includes('ОРИГ&lt;&amp;&quot;'));
  assert.match(app.selects()[0].options[1].text,/ЦЕЛЬ<&"-1/);
});

test('missing documents in an existing snapshot are not replaced with newer case values', async () => {
  const {documents,result,match}=fixtures();
  result.matches=[match];
  const app=await setup({documents:documents.slice(0,2),savedDocuments:documents,result,stale:true});
  const markup=app.element('#main').innerHTML;
  assert.match(markup,/Данные целевого документа недоступны/);
  assert.deepEqual(app.selects()[0].options.map(option=>option.value),['']);
  assert.doesNotMatch(markup,/ЦЕЛЬ&lt;&amp;&quot;-1/);
});

test('each match resolves repeated line IDs in its own target document', async () => {
  const {documents,result,match}=fixtures();
  documents[0].items.push({line_id:'source-2',sku:'ОРИГ-2',name:'Второй товар'});
  result.matches=[match,{...match,line_id:'source-2',target_document_id:'other-spec',candidates:['1']}];
  const app=await setup({documents,result});
  const [first,second]=app.selects();
  assert.match(first.options[1].text,/Предложение.*ЦЕЛЬ<&"-1/);
  assert.match(second.options[1].text,/Предложение.*ЧУЖОЙ-1/);
  assert.doesNotMatch(second.options[2].text,/Предложение/);
  assert.match(second.options[2].text,/ЧУЖОЙ-2/);
  assert.equal(first.value,'');
  assert.equal(second.value,'');
});

test('document IDs, line IDs and option names cannot inject markup', async () => {
  const {documents,result,match}=fixtures();
  const injected='<img src=x onerror=alert(1)>';
  documents[0].id='delivery"'+injected;
  documents[0].items[0].line_id='source"'+injected;
  documents[2].id='target"'+injected;
  documents[2].items[0].line_id='1"'+injected;
  documents[2].items[0].name=injected;
  result.matches=[{...match,document_id:documents[0].id,line_id:documents[0].items[0].line_id,
    target_document_id:documents[2].id,candidates:[documents[2].items[0].line_id]}];
  const app=await setup({documents,result});
  const markup=app.element('#main').innerHTML;
  assert.ok(!markup.includes(injected));
  assert.ok(markup.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.equal(app.selects()[0].dataset.linkDocument,documents[0].id);
  assert.equal(app.selects()[0].dataset.linkLine,documents[0].items[0].line_id);
  assert.equal(app.selects()[0].options[1].value,documents[2].items[0].line_id);
  assert.match(app.selects()[0].options[1].text,/Предложение.*<img src=x onerror=alert\(1\)>/);
});

test('other unresolved matches retain their ordinary manual choices', async () => {
  const {documents,result,match}=fixtures();
  result.matches=[{...match,method:'name'}];
  result.positions=[{line_id:'p1',name:'Обычная позиция',unit:'шт',complete:true}];
  const app=await setup({documents,result});
  const [select]=app.selects();
  assert.deepEqual(select.options.map(option=>option.value),['','p1']);
  assert.equal(select.value,'');
  assert.match(select.options[1].text,/Обычная позиция/);
  assert.doesNotMatch(select.options[1].text,/Предложение/);
});

test('automatic contract-number matching is prominent even without SKU matches', async () => {
  const {documents,result}=fixtures();
  result.audit=[{code:'SIMILAR_CONTRACT_ID',document_ids:['contract','delivery'],
    explanation:'Номер договора a1lgc<&" сопоставлен с аIlgс: похожие символы и совпадающие подтверждённые реквизиты.'}];
  const app=await setup({documents,result});
  const markup=app.element('#main').innerHTML;
  assert.match(markup,/Автоматически сопоставленные номера договоров/);
  assert.match(markup,/Похожие номера договоров сопоставлены автоматически/);
  assert.ok(markup.includes('a1lgc&lt;&amp;&quot;'));
  assert.ok(markup.includes('аIlgс'));
  assert.ok(!markup.includes('a1lgc<&"'));
  assert.match(markup,/contract, delivery/);
  assert.doesNotMatch(markup,/data-action="links"/);
});
