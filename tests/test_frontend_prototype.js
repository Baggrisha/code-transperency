/* Standalone prototype behavior: local state, navigation, files and decisions. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const prototypePath = process.env.KONTROL_PROTOTYPE_FILE || path.join(__dirname,'../web/prototype.html');
const html = fs.readFileSync(prototypePath,'utf8');
const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(match=>match[1]).join('\n');
const KEY = 'control-execution-demo-v1';
const suppliedSource = JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/prototype_source.json'),'utf8'));
const presentedSource = structuredClone(suppliedSource);
presentedSource.initial[0].description = 'Поставка уличных скамеек по контракту ГК-2026/0142.';
presentedSource.demoContents.d1.lines = presentedSource.demoContents.d1.lines.filter(line=>line!=='Данные синтетические. Документ показан для демонстрации.');
presentedSource.demoContents.d2.lines = presentedSource.demoContents.d2.lines.map(line=>line==='Редакция: 1 (демонстрационная)'?'Редакция: 1':line);
const visibleText = markup => markup.replace(/<[^>]+>/g,' ');

function setup(saved = new Map(), storageAccess = {}) {
  const storage = new Map(saved), elements = new Map(), listeners = {}, blobs = new Map();
  const alerts = [], opened = [], downloads = [], timers = [], prompts = [];
  let confirmation = true, objectSequence = 0;
  const makeNode = (tag='div') => {
    const classes = new Set(), descendants = new Map();
    const node = {tagName:tag.toUpperCase(),id:'',innerHTML:'',textContent:'',value:'',style:{},hidden:false,
      attributes:{},dataset:{},children:[],classList:{add(...values){values.forEach(value=>classes.add(value));},remove(...values){values.forEach(value=>classes.delete(value));},contains(value){return classes.has(value);}},
      setAttribute(key,value){this.attributes[key]=String(value);},getAttribute(key){return this.attributes[key];},
      addEventListener(){},removeEventListener(){},focus(){node.focused=true;context.document.activeElement=node;},contains(other){return other===node||[...descendants.values()].includes(other);},
      querySelector(selector){
        if(selector.startsWith('#'))return element(selector.slice(1));
        if(selector!=='.modalWindow')return null;
        if(!descendants.has(selector))descendants.set(selector,makeNode('section'));
        return descendants.get(selector);
      },querySelectorAll(){return [];},
      appendChild(child){this.children.push(child);if(child.id)elements.set(child.id,child);return child;},
      remove(){if(this.id)elements.delete(this.id);},
      click(){if(tag==='a')downloads.push({href:this.href,download:this.download});},
    };
    return node;
  };
  function element(id) {
    if(id==='docModal')return elements.get(id) || null;
    if(!elements.has(id)){const node=makeNode();node.id=id;elements.set(id,node);}
    return elements.get(id);
  }
  const body=makeNode('body');
  const context=vm.createContext({
    console,Date,Math,Blob,encodeURIComponent,decodeURIComponent,structuredClone,
    localStorage:{
      getItem(key){if(storageAccess.readError)throw new Error('Storage unavailable');return storage.get(key)??null;},
      setItem(key,value){if(storageAccess.writeError)throw new Error('Storage unavailable');storage.set(key,String(value));},
    },
    document:{getElementById:element,createElement:makeNode,body,activeElement:null,
      querySelector:selector=>selector.startsWith('#')?element(selector.slice(1)):null,querySelectorAll:()=>[],
      addEventListener:(name,fn)=>listeners[name]=fn},
    window:{open:url=>opened.push(url),addEventListener:(name,fn)=>listeners[name]=fn},
    URL:{createObjectURL(blob){const id='blob:prototype-'+(++objectSequence);blobs.set(id,blob);return id;},revokeObjectURL(){}},
    setTimeout(fn){timers.push(fn);return timers.length;},clearTimeout(){},
    prompt:()=>prompts.shift()??'',confirm:()=>confirmation,alert:message=>alerts.push(message),
  });
  const evaluate=code=>vm.runInContext(code,context);
  evaluate(scripts);
  return {context,evaluate,element,storage,alerts,opened,downloads,blobs,listeners,
    setConfirm(value){confirmation=value;},setPrompts(...answers){prompts.push(...answers);},
    create(number='К-002',name='Поставка мебели'){prompts.push(number,name);evaluate('createCase()');},
    add(name='накладная.txt',content='Документ поставки'){
      context.addedFile=Object.assign(new Blob([content],{type:'text/plain'}),{name});
      evaluate('addFiles([addedFile])');
      return evaluate('c().docs.at(-1)?.id');
    }};
}

test('standalone startup renders the supplied case without presentation labels',()=>{
  const app=setup();
  assert.equal(app.evaluate('state.cases.length'),1);
  assert.equal(app.evaluate('state.cases[0].docs.length'),4);
  assert.equal(app.evaluate('state.cases[0].signals.length'),1);
  assert.match(app.element('root').innerHTML,/Проверки контрактов/);
  assert.match(app.element('root').innerHTML,/ГК-2026\/0142/);
  assert.match(app.element('root').innerHTML,/Открыть проверку/);
  assert.doesNotMatch(visibleText(app.element('root').innerHTML),/прототип|локальный режим|демо-кейс|демонстрационн|синтетическ|учебный кейс/i);
  assert.doesNotMatch(html.match(/<title>([\s\S]*?)<\/title>/)?.[1]||'',/прототип|демо/i);
  assert.doesNotMatch(html.match(/<noscript>([\s\S]*?)<\/noscript>/)?.[1]||'',/прототип|демо/i);
});

test('the initial case and four previews retain source data with the approved neutral descriptions',()=>{
  const app=setup();
  assert.deepEqual(JSON.parse(app.evaluate('JSON.stringify(initial)')),presentedSource.initial);
  assert.deepEqual(JSON.parse(app.evaluate('JSON.stringify(state.cases)')),presentedSource.initial);
  assert.deepEqual(JSON.parse(app.evaluate('JSON.stringify(demoContents)')),presentedSource.demoContents);
  assert.equal(app.evaluate('c().history.length'),0);
  assert.equal(app.evaluate('c().signals[0].history.length'),0);
  assert.equal(app.evaluate('c().signals[0].comments.length'),0);
});

test('overview and recognition retain form values without presentation identifiers or general disclaimers',()=>{
  const app=setup(),original=presentedSource.initial[0];
  for(const tab of ['overview','extract']){
    app.evaluate(`openCase("DEMO-001");go("case","${tab}")`);
    const markup=app.element('root').innerHTML;
    for(const [id,value] of [['caseNumber',original.number],['caseName',original.name]]){
      const input=[...markup.matchAll(/<input\b[^>]*>/g)].map(match=>match[0]).find(tag=>tag.includes(`id="${id}"`));
      assert.ok(input,`${tab}: ${id} is rendered`);
      assert.equal(input.match(/\bvalue="([^"]*)"/)?.[1],value,`${tab}: ${id} value`);
    }
    const textarea=[...markup.matchAll(/<textarea\b([^>]*)>([\s\S]*?)<\/textarea>/g)].find(match=>match[1].includes('id="caseDescription"'));
    assert.equal(textarea?.[2],original.description,`${tab}: complete description`);
    assert.equal(markup.match(/<div class="eyebrow">([\s\S]*?)<\/div>/)?.[1],'РАБОЧАЯ ОБЛАСТЬ');
    const subtitle=markup.match(/<p class="sub">([\s\S]*?)<\/p>/)?.[1];
    assert.ok(subtitle);
    assert.doesNotMatch(subtitle,/ДЕМОНСТРАЦИОННЫЕ ДАННЫЕ| · /);
    assert.equal(app.evaluate('c().demo'),true);
    assert.doesNotMatch(visibleText(markup),/DEMO-001|прототип|демонстрационн|синтетическ|учебный кейс/i);
    assert.doesNotMatch(markup,/Система предоставляет информационные сигналы|Не принимает юридически значимых решений/);
  }
});

test('opening the original demo preserves edited legacy data, uploaded files and other cases',()=>{
  const app=setup();
  app.evaluate('openCase("DEMO-001")');
  app.element('caseNumber').value='';app.element('caseName').value='';app.element('caseDescription').value='';
  app.evaluate('updateCase();openSignal("SIG-001")');
  app.element('signalStatus').value='В работе';app.element('comment').value='Решение ещё уточняется';
  app.evaluate('saveSignal();removeDoc("d2")');
  app.create('USER-42','Пользовательское дело');const fileId=app.add('пользовательский.txt','Сохранённый исходник');
  const userId=app.evaluate('c().id'),before=JSON.parse(app.evaluate('JSON.stringify(state.cases)'));
  const stored=setup(app.storage);
  assert.equal(stored.evaluate('state.cases.find(x=>x.id==="DEMO-001").number'),'');
  assert.equal(stored.evaluate('state.cases.length'),2);
  assert.equal(app.evaluate('initial[0].description'),presentedSource.initial[0].description);
  app.evaluate('openOriginalDemo()');
  assert.equal(app.evaluate('state.cases.length'),3);
  assert.notEqual(app.evaluate('c().id'),'DEMO-001');
  const fresh=JSON.parse(app.evaluate('JSON.stringify(c())'));
  assert.deepEqual({...fresh,id:'DEMO-001'},presentedSource.initial[0]);
  for(const item of before)
    assert.deepEqual(JSON.parse(app.evaluate(`JSON.stringify(state.cases.find(x=>x.id===${JSON.stringify(item.id)}))`)),item);
  assert.equal(app.evaluate(`ephemeralFiles[${JSON.stringify(fileId)}].name`),'пользовательский.txt');
  const freshId=app.evaluate('c().id');
  app.evaluate('openOriginalDemo()');
  assert.equal(app.evaluate('c().id'),freshId);
  assert.equal(app.evaluate('state.cases.length'),3);
  const restored=setup(app.storage);
  assert.equal(restored.evaluate('state.selected'),freshId);
  assert.equal(restored.evaluate(`state.cases.find(x=>x.id===${JSON.stringify(userId)}).docs.length`),1);
  assert.equal(restored.evaluate('state.cases.find(x=>x.id==="DEMO-001").signals[0].comments[0].text'),'Решение ещё уточняется');
});

test('opening the original demo restores a missing example and reuses an untouched example',()=>{
  const app=setup();app.create('USER-ONLY','Оставшееся дело');
  app.evaluate('state.cases=state.cases.filter(x=>!x.demo);save()');
  const originalUser=app.evaluate('JSON.stringify(c())');
  const restored=setup(app.storage);restored.evaluate('openOriginalDemo()');
  assert.equal(restored.evaluate('state.cases.length'),2);
  assert.equal(restored.evaluate('c().id'),'DEMO-001');
  assert.equal(restored.evaluate('JSON.stringify(state.cases.find(x=>!x.demo))'),originalUser);
  restored.evaluate('openOriginalDemo()');
  assert.equal(restored.evaluate('state.cases.length'),2);
});

test('all workspace tabs and the separate signal page remain reachable',()=>{
  const app=setup();
  app.evaluate('openCase("DEMO-001")');
  for(const [tab,label] of [['overview',/Данные контракта/],['docs',/Загрузка документов/],
    ['extract',/Распознавание реквизитов/],['match',/Сопоставление позиций/],
    ['results',/Реестр расхождений/],['history',/Журнал действий|История действий/]]){
    app.evaluate(`go('case','${tab}')`);
    assert.equal(app.evaluate('state.tab'),tab);
    assert.match(app.element('root').innerHTML,label);
    assert.doesNotMatch(visibleText(app.element('root').innerHTML),/прототип|локальный режим|демо-кейс|демонстрационн|синтетическ|учебный кейс/i);
  }
  app.evaluate('openSignal("SIG-001")');
  assert.equal(app.evaluate('state.page'),'signal');
  for(const label of ['Основание сигнала','Документы-источники','Рассмотрение специалистом','История рассмотрения'])
    assert.ok(app.element('root').innerHTML.includes(label),label);
  assert.doesNotMatch(visibleText(app.element('root').innerHTML),/прототип|локальный режим|демо-кейс|демонстрационн|синтетическ|учебный кейс/i);
  app.evaluate('go("case","results")');
  assert.match(app.element('root').innerHTML,/Реестр расхождений/);
});

test('factory descriptions migrate narrowly while metadata and selected screen survive reopening',()=>{
  const original=suppliedSource.initial[0];
  const legacy={cases:[
    structuredClone(original),
    {...structuredClone(original),id:'DEMO-SHORT',description:'Синтетические данные.'},
    {...structuredClone(original),id:'DEMO-CUSTOM',description:'Моя проверенная правка'},
    {...structuredClone(original),id:'DEMO-EMPTY',description:''},
    {...structuredClone(original),id:'USER-OLD-LABEL',demo:false},
  ],selected:'DEMO-CUSTOM',tab:'extract',page:'case',signal:null};
  const expected=structuredClone(legacy);
  expected.cases[0].description=presentedSource.initial[0].description;
  expected.cases[1].description=presentedSource.initial[0].description;
  const app=setup(new Map([[KEY,JSON.stringify(legacy)]]));
  assert.deepEqual(JSON.parse(app.storage.get(KEY)),expected);
  assert.deepEqual(JSON.parse(app.evaluate('JSON.stringify(state)')),expected);
  app.create();
  app.evaluate('go("case","overview")');
  app.element('caseNumber').value='ГК-42';
  app.element('caseName').value='Новые скамейки';
  app.element('caseDescription').value='Ручное уточнение';
  app.evaluate('updateCase();go("case","extract")');
  const snapshot=JSON.parse(app.storage.get(KEY)),id=app.evaluate('state.selected');
  assert.equal(snapshot.cases.find(item=>item.id===id).description,'Ручное уточнение');
  const restored=setup(app.storage);
  assert.equal(restored.evaluate('state.selected'),id);
  assert.equal(restored.evaluate('state.tab'),'extract');
  assert.equal(restored.evaluate('c().number'),'ГК-42');
  assert.equal(restored.evaluate('c().name'),'Новые скамейки');
  assert.match(restored.element('root').innerHTML,/Распознавание реквизитов/);
});

test('newly uploaded files do not acquire the demonstration comparison or signals',()=>{
  const app=setup();app.create();app.add();
  assert.equal(app.evaluate('c().demo'),false);
  assert.equal(app.evaluate('c().signals.length'),0);
  app.evaluate('go("case","match")');
  assert.match(app.element('root').innerHTML,/Автоматическое сопоставление не выполняется/);
  assert.doesNotMatch(app.element('root').innerHTML,/<td>10 шт\.<\/td>/);
  app.evaluate('go("case","results")');
  assert.match(app.element('root').innerHTML,/Отсутствие сигналов.*не означает/);
});

test('file selection retains metadata and ephemeral bytes while duplicate or unsupported files are rejected',()=>{
  const app=setup();app.create();
  const id=app.add('накладная.txt','12345');
  assert.equal(app.evaluate('c().docs.length'),1);
  assert.equal(app.evaluate('c().docs[0].type'),'Накладная');
  assert.equal(app.evaluate(`ephemeralFiles[${JSON.stringify(id)}].name`),'накладная.txt');
  app.add('накладная.txt','12345');
  app.add('wrong.exe','12345');
  assert.equal(app.evaluate('c().docs.length'),1);
  assert.ok(app.alerts.some(message=>/дубликат/i.test(message)));
  assert.ok(app.alerts.some(message=>/Неподдерживаемый формат/.test(message)));
});

test('all seven prototype categories remain available and changing a type is persisted and logged',()=>{
  const app=setup();app.create();const id=app.add();
  assert.equal(app.evaluate('types.length'),7);
  assert.ok(app.evaluate('types.includes("Исправленная версия") && types.includes("Другое")'));
  app.evaluate(`changeDocType(${JSON.stringify(id)})`);
  assert.match(app.element('docModal').innerHTML,/Изменить тип документа/);
  app.element('docCategory').value='Исправленная версия';
  app.evaluate(`saveDocCategory(${JSON.stringify(id)})`);
  assert.equal(app.evaluate('c().docs[0].type'),'Исправленная версия');
  assert.ok(app.evaluate('c().history.some(item=>item.text.includes("Тип документа"))'));
  assert.equal(setup(app.storage).evaluate('c().docs[0].type'),'Исправленная версия');
});

test('the custom category picker updates its hidden value before saving and supports cancellation',()=>{
  const app=setup();app.evaluate('openCase("DEMO-001");changeDocType("d1")');
  app.element('prototype-type-options').hidden=true;
  app.evaluate('togglePrototypeType()');
  assert.equal(app.element('prototype-type-options').hidden,false);
  assert.equal(app.element('prototype-type-trigger').getAttribute('aria-expanded'),'true');
  app.evaluate('choosePrototypeType(types.indexOf("Другое"))');
  assert.equal(app.element('docCategory').value,'Другое');
  assert.equal(app.element('prototype-type-selected').textContent,'Другое');
  assert.equal(app.element('prototype-type-options').hidden,true);
  app.evaluate('closeDocModal()');
  assert.equal(app.evaluate('c().docs.find(d=>d.id==="d1").type'),'Контракт');
  assert.equal(app.element('docModal'),null);
  app.evaluate('changeDocType("d1");choosePrototypeType(types.indexOf("Исправленная версия"));saveDocCategory("d1")');
  assert.equal(app.evaluate('c().docs.find(d=>d.id==="d1").type'),'Исправленная версия');
});

test('dropping files preserves the same upload flow and clears the drag state',()=>{
  const app=setup();app.create();
  const drop=app.element('drop');drop.classList.add('drag');
  const file=Object.assign(new Blob(['Поставка']),{name:'акт.txt'});
  let prevented=false;
  app.context.dropEvent={preventDefault(){prevented=true;},currentTarget:drop,dataTransfer:{files:[file]}};
  app.evaluate('dropFiles(dropEvent)');
  assert.equal(prevented,true);
  assert.equal(drop.classList.contains('drag'),false);
  assert.equal(app.evaluate('c().docs[0].type'),'Документ о приёмке');
  assert.equal(app.evaluate('ephemeralFiles[c().docs[0].id].name'),'акт.txt');
});

test('removing a file respects cancellation and removes its ephemeral bytes after confirmation',()=>{
  const app=setup();app.create();const id=app.add();
  app.setConfirm(false);app.evaluate(`removeDoc(${JSON.stringify(id)})`);
  assert.equal(app.evaluate('c().docs.length'),1);
  app.setConfirm(true);app.evaluate(`removeDoc(${JSON.stringify(id)})`);
  assert.equal(app.evaluate('c().docs.length'),0);
  assert.equal(app.evaluate(`ephemeralFiles[${JSON.stringify(id)}]`),undefined);
  assert.ok(app.evaluate('c().history.some(item=>item.text.includes("Удалён документ"))'));
});

test('uploaded originals can be viewed in-session but reopening does not fabricate file bytes',()=>{
  const app=setup();app.create();const id=app.add('original.txt','Original evidence');
  app.evaluate(`viewDoc(${JSON.stringify(id)})`);
  assert.equal(app.opened.length,1);
  assert.equal(app.blobs.get(app.opened[0]).name,'original.txt');
  const restored=setup(app.storage);
  assert.equal(restored.evaluate('c().docs.length'),1);
  assert.equal(restored.evaluate(`ephemeralFiles[${JSON.stringify(id)}]`),undefined);
  restored.evaluate(`viewDoc(${JSON.stringify(id)})`);
  assert.match(restored.element('docModal').innerHTML,/Оригинальный файл недоступен/);
  assert.equal(restored.opened.length,0);
});

test('document previews keep their evidence values without claiming OCR or original file availability',()=>{
  const app=setup();app.evaluate('openCase("DEMO-001");viewDoc("d3")');
  const markup=app.element('docModal').innerHTML;
  assert.match(markup,/Предпросмотр документа/);
  assert.doesNotMatch(visibleText(markup),/прототип|демонстрационн|синтетическ|учебный кейс|оригинальный PDF|результат OCR/i);
  assert.match(markup,/8 шт\./);
  assert.equal(app.opened.length,0);
});

test('signal status and comments persist with both histories without changing source values',()=>{
  const app=setup();app.evaluate('openSignal("SIG-001")');
  const before=app.evaluate('JSON.stringify([c().signals[0].expected,c().signals[0].actual,c().signals[0].rule])');
  app.element('signalStatus').value='Подтверждено специалистом';
  app.element('comment').value='  Сверено с актом <img src=x onerror=alert(1)>  ';
  app.evaluate('saveSignal()');
  assert.equal(app.evaluate('c().signals[0].status'),'Подтверждено специалистом');
  assert.equal(app.evaluate('c().signals[0].comments.length'),1);
  assert.equal(app.evaluate('c().signals[0].history.length'),2);
  assert.equal(app.evaluate('c().history.length'),2);
  assert.equal(app.evaluate('JSON.stringify([c().signals[0].expected,c().signals[0].actual,c().signals[0].rule])'),before);
  assert.match(app.element('root').innerHTML,/&lt;img/);
  assert.doesNotMatch(app.element('root').innerHTML,/<img src=x/);
  const restored=setup(app.storage);
  assert.equal(restored.evaluate('c().signals[0].status'),'Подтверждено специалистом');
  assert.equal(restored.evaluate('c().signals[0].comments.length'),1);
});

test('registry and signal search filters continue to use their original controls',()=>{
  const app=setup();
  app.element('search').value='0142';app.element('statusFilter').value='Требует рассмотрения';app.evaluate('filterCases()');
  assert.match(app.element('caseRows').innerHTML,/ГК-2026\/0142/);
  app.element('search').value='несуществующий';app.evaluate('filterCases()');
  assert.doesNotMatch(app.element('caseRows').innerHTML,/onclick="openCase/);
  app.evaluate('openCase("DEMO-001");go("case","results")');
  app.element('signalSearch').value='скамейка';app.element('signalFilter').value='Требует проверки';app.evaluate('filterSignals()');
  assert.match(app.element('signalRows').innerHTML,/SIG-001/);
  app.element('signalFilter').value='Не подтверждено';app.evaluate('filterSignals()');
  assert.doesNotMatch(app.element('signalRows').innerHTML,/SIG-001/);
});

test('completion respects the unresolved-signal confirmation and the archive contains completed cases',()=>{
  const app=setup();app.evaluate('openCase("DEMO-001")');
  app.setConfirm(false);app.evaluate('finishCase()');
  assert.equal(app.evaluate('c().status'),'Требует рассмотрения');
  app.setConfirm(true);app.evaluate('finishCase()');
  assert.equal(app.evaluate('c().status'),'Завершена');
  app.create('UNFINISHED','Незавершённое дело');
  app.evaluate('go("history")');
  const markup=app.element('root').innerHTML;
  assert.match(markup,/ГК-2026\/0142/);
  assert.doesNotMatch(markup,/Незавершённое дело/);
  app.element('search').value='UNFINISHED';app.element('statusFilter').value='';app.evaluate('filterCases()');
  assert.doesNotMatch(app.element('caseRows').innerHTML,/UNFINISHED|Незавершённое дело/);
  app.element('search').value='';app.evaluate('filterCases()');
  assert.match(app.element('caseRows').innerHTML,/ГК-2026\/0142/);
  assert.doesNotMatch(app.element('caseRows').innerHTML,/Незавершённое дело/);
  assert.equal(setup(app.storage).evaluate('state.cases.find(item=>item.id==="DEMO-001").status'),'Завершена');
});

test('question export preserves the selected case and signal explanation without presentation labels',async()=>{
  const app=setup();app.evaluate('openCase("DEMO-001");exportQuestions()');
  assert.equal(app.downloads.length,1);
  assert.equal(app.downloads[0].download,'questions-DEMO-001.txt');
  const blob=app.blobs.get(app.downloads[0].href),content=await blob.text();
  assert.match(blob.type,/text\/plain/);
  for(const text of ['ГК-2026/0142','SIG-001','Несоответствие количества','Требует проверки','Это сигнал для проверки'])
    assert.ok(content.includes(text),text);
  assert.doesNotMatch(content,/Демонстрационные данные/);
  assert.equal(app.evaluate('c().demo'),true);
});

test('invalid stored JSON recovers the demonstration without network calls',()=>{
  const app=setup(new Map([[KEY,'not valid JSON']]));
  assert.equal(app.evaluate('state.cases[0].id'),'DEMO-001');
  assert.match(app.element('root').innerHTML,/Проверки контрактов/);
});

test('blocked browser storage keeps the open session usable and explains the persistence limit',()=>{
  const app=setup(new Map(),{readError:true,writeError:true});
  assert.equal(app.evaluate('state.cases[0].id'),'DEMO-001');
  app.create('LOCAL-ONLY','Дело в открытой вкладке');
  assert.equal(app.evaluate('c().number'),'LOCAL-ONLY');
  assert.equal(app.storage.has(KEY),false);
  assert.match(app.element('root').innerHTML,/Дело в открытой вкладке/);
  assert.match(app.element('prototype-toast').textContent,/Хранилище браузера недоступно/);
  assert.match(app.element('prototype-toast').textContent,/только в открытой вкладке/);
});
