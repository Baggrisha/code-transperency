/* Async navigation regressions against the shipped script, with a minimal DOM. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const response = value => ({ok:true,json:async()=>structuredClone(value)});
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes,no) => { resolve=yes; reject=no; });
  return {promise,resolve,reject};
}
function document(id, name = id + '.txt') {
  return {id,type:'contract',filename:name,number:id,contract_id:id,status:'active',items:[],fields_confidence:{}};
}
function run(id, caseId, documents, asOf = '2026-10-31') {
  return {id,case_id:caseId,request:{documents,as_of:asOf,delivery_complete:false,manual_links:[]},
    result:{status:'ok',signals:[],positions:[],matches:[],audit:[],as_of:asOf,engine_version:'test',input_hash:'input',result_hash:'result'},
    reviews:[],latest_reviews:{},stale:false};
}
async function setup() {
  const docs=[document('doc-A'),document('doc-B')];
  const cases={A:{id:'A',title:'Проверка A',documents:docs.map(doc=>({id:doc.id,revision:1,document:doc})),runs:[{id:'run-A'}],metadata_revision:0},
    B:{id:'B',title:'Проверка B',documents:[],runs:[{id:'run-B'}],metadata_revision:0}};
  const runs={'run-A':run('run-A','A',docs),'run-B':run('run-B','B',[],'2026-11-15'),
    'run-old':run('run-old','A',[docs[0]],'2026-01-01'),'run-new':run('run-new','A',docs)};
  const routes=new Map([
    ['/api/demo',[]],['/api/cases',[]],['/api/cases/A',cases.A],['/api/cases/B',cases.B],
    ...Object.entries(runs).map(([id,item])=>['/api/runs/'+id,item]),
    ['/api/runs?limit=25&offset=0',{items:[],total:0,limit:25,offset:0}],
  ]);
  const elements=new Map(), listeners={}, requests=[], overrides=new Map();
  function element(selector) {
    if (['#asof','#complete'].includes(selector) && !(elements.get('#main')?.innerHTML || '').includes(`id="${selector.slice(1)}"`)) return null;
    if (!elements.has(selector)) {
      const handlers={};
      const node={innerHTML:'',textContent:'',style:{},value:'',checked:false,hidden:false,open:false,
        attributes:{},setAttribute(name,value){this.attributes[name]=String(value);},
        getAttribute(name){return this.attributes[name];},focus(){this.focused=true;},
        showModal(){this.open=true;},close(){this.open=false;handlers.close?.({target:this});},
        addEventListener(type,listener){handlers[type]=listener;}};
      elements.set(selector,node);
    }
    return elements.get(selector);
  }
  class FormDataStub {
    constructor(form){this.values=new Map(Object.entries(form?.values || {}));}
    append(key,value){this.values.set(key,value);}
    [Symbol.iterator](){return this.values[Symbol.iterator]();}
  }
  const context=vm.createContext({
    console,Date,encodeURIComponent,decodeURIComponent,structuredClone,URL,Blob,
    setTimeout(){return 0;},clearTimeout(){},FormData:FormDataStub,
    sessionStorage:{getItem(){return null;},setItem(){}},
    location:{hash:''},history:{replaceState(_state,_title,hash){context.location.hash=hash;}},
    document:{querySelector:element,querySelectorAll(){return [];},addEventListener(type,listener){listeners[type]=listener;}},
    fetch:async(url,options={})=>{
      requests.push({url,options});
      const handler=overrides.get((options.method || 'GET')+' '+url);
      if(handler)return handler(options);
      assert.ok(routes.has(url),'Unexpected request: '+url);
      return response(routes.get(url));
    },
  });
  const evaluate=code=>vm.runInContext(code,context);
  await evaluate(script);
  return {context,evaluate,element,listeners,requests,overrides,cases,runs,routes};
}

test('a completed run cannot replace another case or its options after navigation',async()=>{
  const app=await setup(),gate=deferred(),started=deferred();
  await app.evaluate('openCase("A")');
  app.overrides.set('POST /api/cases/A/runs',async()=>{started.resolve();await gate.promise;return response({id:'run-new'});});
  const pending=app.evaluate('run()');
  await started.promise;
  await app.evaluate('openCase("B")');
  gate.resolve();await pending;
  assert.equal(app.evaluate('state.current.id'),'B');
  assert.equal(app.evaluate('state.run.id'),'run-B');
  assert.equal(app.evaluate('state.options.as_of'),'2026-11-15');
  assert.doesNotMatch(app.element('#main').innerHTML,/Проверка A/);
});

test('completion does not force the workspace back over the run history',async()=>{
  const app=await setup(),gate=deferred(),started=deferred();
  await app.evaluate('openCase("A")');
  app.overrides.set('POST /api/cases/A/runs',async()=>{started.resolve();await gate.promise;return response({id:'run-new'});});
  const pending=app.evaluate('run()');await started.promise;
  await app.evaluate('showHistory()');gate.resolve();await pending;
  assert.equal(app.evaluate('state.page'),'history');
  assert.match(app.element('#main').innerHTML,/Журнал проверок/);
  assert.equal(app.context.location.hash,'#history');
});

test('returning home invalidates an in-flight case opening',async()=>{
  const app=await setup(),gate=deferred(),started=deferred();
  app.overrides.set('GET /api/cases/A',async()=>{started.resolve();await gate.promise;return response(app.cases.A);});
  const pending=app.evaluate('openCase("A")');await started.promise;
  app.evaluate('home()');gate.resolve();assert.equal(await pending,false);
  assert.equal(app.evaluate('state.page'),'registry');
  assert.equal(app.evaluate('state.current'),null);
});

test('historical run identity and options survive tab navigation and a deep-link reload',async()=>{
  const app=await setup();
  await app.evaluate('openCase("A",null,"results","run-old")');
  assert.equal(app.evaluate('state.run.id'),'run-old');
  assert.equal(app.evaluate('state.options.as_of'),'2026-01-01');
  assert.equal(app.context.location.hash,'#case/A/results/run/run-old');
  app.evaluate('state.tab="matching";render()');
  assert.equal(app.context.location.hash,'#case/A/matching/run/run-old');
  await app.evaluate('routeFromHash()');
  assert.equal(app.evaluate('state.run.id'),'run-old');
  assert.equal(app.evaluate('state.tab'),'matching');
  assert.equal(app.evaluate('state.options.as_of'),'2026-01-01');
});

test('an upload continues for its original case without reopening it over a newer case',async()=>{
  const app=await setup(),gate=deferred(),started=deferred();
  await app.evaluate('openCase("A",null,"documents")');
  app.evaluate('state.files=[{name:"original.txt",size:5}]');
  app.overrides.set('POST /api/cases/A/documents',async()=>{started.resolve();await gate.promise;return response({id:'uploaded'});});
  const pending=app.evaluate('uploadFiles()');await started.promise;
  await app.evaluate('openCase("B")');gate.resolve();await pending;
  assert.equal(app.evaluate('state.current.id'),'B');
  assert.equal(app.evaluate('state.run.id'),'run-B');
  assert.equal(app.evaluate('state.options.as_of'),'2026-11-15');
  assert.equal(app.requests.filter(item=>item.options.method==='POST').length,1);
});

test('saving a signal decision does not read or overwrite a newly selected run',async()=>{
  const app=await setup(),gate=deferred(),started=deferred();
  await app.evaluate('openCase("A",null,"results")');
  app.context.reviewForm={dataset:{signal:'signal-A'},values:{status:'resolved',reviewer:'Тест',comment:'Сверено'}};
  app.overrides.set('POST /api/runs/run-A/signals/signal-A/reviews',async()=>{started.resolve();await gate.promise;return response({sequence:1});});
  const pending=app.evaluate('saveSignalReview(reviewForm)');await started.promise;
  await app.evaluate('openCase("B")');const requestCount=app.requests.length;
  gate.resolve();await pending;
  assert.equal(app.evaluate('state.run.id'),'run-B');
  assert.deepEqual(app.requests.slice(requestCount).map(item=>item.url),['/api/cases']);
});

test('a slow document text read cannot overwrite a later viewer in the same case',async()=>{
  const app=await setup(),gate=deferred(),started=deferred();
  await app.evaluate('openCase("A",null,"documents")');
  app.overrides.set('GET /api/cases/A/documents/doc-A/file',async()=>({ok:true,blob:async()=>({text:async()=>{started.resolve();return gate.promise;}})}));
  app.overrides.set('GET /api/cases/A/documents/doc-B/file',async()=>({ok:true,blob:async()=>({text:async()=>'Document B'})}));
  const pending=app.evaluate('viewDocument("doc-A")');await started.promise;
  await app.evaluate('viewDocument("doc-B")');gate.resolve('Document A');await pending;
  assert.match(app.element('#modal').innerHTML,/doc-B.txt/);
  assert.match(app.element('#viewer-content').innerHTML,/Document B/);
  assert.doesNotMatch(app.element('#viewer-content').innerHTML,/Document A/);
});

test('a late viewer error cannot replace a newer document with an old failure',async()=>{
  const app=await setup(),gate=deferred(),started=deferred();
  await app.evaluate('openCase("A",null,"documents")');
  app.overrides.set('GET /api/cases/A/documents/doc-A/file',async()=>{started.resolve();await gate.promise;throw new Error('Old failure');});
  app.overrides.set('GET /api/cases/A/documents/doc-B/file',async()=>({ok:true,blob:async()=>({text:async()=>'Document B'})}));
  const pending=app.evaluate('viewDocument("doc-A")');await started.promise;
  await app.evaluate('viewDocument("doc-B")');gate.resolve();await pending;
  assert.match(app.element('#viewer-content').innerHTML,/Document B/);
  assert.doesNotMatch(app.element('#viewer-content').innerHTML,/Old failure/);
});

test('historical viewing does not substitute a newer document absent from its snapshot',async()=>{
  const app=await setup();
  await app.evaluate('openCase("A",null,"results","run-old")');
  await assert.rejects(app.evaluate('viewDocument("doc-B",true)'),/отсутствует в сохранённом снимке/);
  assert.equal(app.requests.filter(item=>item.url.endsWith('/file')).length,0);
});

test('metadata save reports successful persistence even if the registry refresh fails',async()=>{
  const app=await setup();
  await app.evaluate('openCase("A")');
  app.context.metadataForm={values:{title:'Updated',description:'',contract_number:''}};
  app.overrides.set('PUT /api/cases/A',async()=>response({id:'A',title:'Updated',description:'',contract_number:'',metadata_revision:1,revision:0,updated_at:'2026-11-20'}));
  app.overrides.set('GET /api/cases',async()=>{throw new Error('Network unavailable');});
  await app.evaluate('saveCaseMetadata(metadataForm)');
  assert.equal(app.evaluate('state.current.title'),'Updated');
  assert.match(app.element('#toast').textContent,/сохранены/);
  assert.match(app.element('#toast').textContent,/Не удалось обновить/);
  assert.equal(app.requests.filter(item=>item.options.method==='PUT').length,1);
});

test('a completed type edit does not close a newer dialog or return to the old case',async()=>{
  const app=await setup(),gate=deferred(),started=deferred();
  await app.evaluate('openCase("A",null,"documents")');
  app.evaluate('openModal("Original type dialog")');
  app.element('#type-value').value='delivery';
  app.context.typeForm={dataset:{id:'doc-A'},values:{type:'delivery',reviewer:'Тест',comment:'Сверено'}};
  app.overrides.set('PUT /api/cases/A/documents/doc-A',async()=>{started.resolve();await gate.promise;return response({revision:2});});
  const pending=app.evaluate('saveDocumentType(typeForm)');await started.promise;
  app.evaluate('closeModal()');await app.evaluate('openCase("B")');app.evaluate('newCaseModal()');
  gate.resolve();await pending;
  assert.equal(app.evaluate('state.current.id'),'B');
  assert.equal(app.element('#modal').open,true);
  assert.match(app.element('#modal').innerHTML,/Создать проверку/);
});

test('skip link moves focus without changing the workspace route',async()=>{
  const app=await setup();
  await app.evaluate('openCase("A",null,"matching")');
  const hash=app.context.location.hash;
  let prevented=false;
  await app.listeners.click({target:{closest(selector){return selector==='a.skip-link'?{getAttribute:()=> '#main'}:null;}},preventDefault(){prevented=true;}});
  assert.equal(prevented,true);
  assert.equal(app.element('#main').focused,true);
  assert.equal(app.evaluate('state.page'),'workspace');
  assert.equal(app.context.location.hash,hash);
});
