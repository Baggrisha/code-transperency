/* Matching and manual-link behavior in the built React app. */
const nodeTest = require('node:test');
const test = (name, fn) => nodeTest(name, {timeout:60000}, fn);
test.before = nodeTest.before;
test.after = nodeTest.after;
const assert = require('node:assert/strict');
const {expect, scenario, startBrowser} = require('../scripts/check_web');

let runtime;
test.before(async () => { runtime = await startBrowser(); });
test.after(async () => { if (runtime) await runtime.close(); });

function documentFixture(id, type, items) {
  return {id,type,items,filename:id+'.pdf',number:null,date:null,status:'active',is_complete:true};
}
function fixtures() {
  const documents = [
    documentFixture('delivery','delivery',[{line_id:'source-1',sku:'ОРИГ<&"',name:'Исходный товар'}]),
    documentFixture('other-spec','specification',[{line_id:'1',sku:'ЧУЖОЙ-1',name:'Чужая позиция'},{line_id:'2',sku:'ЧУЖОЙ-2',name:'Другая чужая позиция'}]),
    documentFixture('target-spec','specification',[{line_id:'1',sku:'ЦЕЛЬ<&"-1',name:'Первая позиция'},{line_id:'2',sku:'ЦЕЛЬ-2',name:'Вторая позиция'},{line_id:'3',sku:'ДРУГОЙ-3',name:'Ручной вариант'}]),
  ];
  const result = {status:'incomplete',signals:[],matches:[],positions:[],audit:[],engine_version:'test',as_of:'2026-10-31',input_hash:'test',result_hash:'test'};
  const match = {document_id:'delivery',line_id:'source-1',target_document_id:'target-spec',target_line_id:null,state:'needs_review',method:'similar_sku',score:0.9,candidates:['1','2'],explanation:'Сверьте с оригиналом <документа>'};
  return {documents,result,match};
}
async function setup(t, {documents,savedDocuments=documents,result,includeRequestDocuments=true,stale=false}) {
  const request = {as_of:'2026-10-31',delivery_complete:false,manual_links:[]};
  if (includeRequestDocuments) request.documents = documents;
  const dossier = {id:'case-1',title:'Синтетический комплект',revision:2,documents:savedDocuments.map(document => ({id:document.id,revision:1,document})),runs:[{id:'run-1'}]};
  const run = {id:'run-1',case_id:'case-1',request,result,reviews:[],stale};
  const app = await scenario(runtime, {
    'GET /api/demo': [],
    'GET /api/cases': [],
    'GET /api/cases/case-1': dossier,
    'GET /api/runs/run-1': run,
    'GET /api/runs/run-2': {...run,id:'run-2'},
    'POST /api/cases/case-1/runs': {id:'run-2'},
  });
  t.after(() => app.close());
  await app.page.goto('/#case/case-1/matching');
  await expect(app.page.getByRole('heading',{name:'Сопоставление позиций'})).toBeVisible();
  app.posted = () => app.requests.filter(item => item.key === 'POST /api/cases/case-1/runs').map(item => JSON.parse(item.body));
  return app;
}
const selector = (page, doc = 'delivery', line = 'source-1') =>
  page.getByRole('combobox', {name:`Позиция для ${doc}, строка ${line}`, exact:true});
const options = locator => locator.locator('option').evaluateAll(nodes => nodes.map(node => ({value:node.value,text:node.textContent})));

test('uncertain SKU proposals use the correct target snapshot and remain unselected', async t => {
  const {documents,result,match} = fixtures(); result.matches = [match];
  const savedDocuments = structuredClone(documents);
  savedDocuments[0].items[0].sku = 'НОВЫЙ-ИСТОЧНИК';
  savedDocuments[2].items[0].sku = 'НОВАЯ-ЦЕЛЬ';
  const {page} = await setup(t,{documents,savedDocuments,result,stale:true});
  await expect(page.getByText('Исходный артикул:',{exact:false})).toContainText('ОРИГ<&"');
  await expect(page.locator('main')).not.toContainText('НОВЫЙ-ИСТОЧНИК');
  await expect(page.locator('main')).toContainText('Сверьте с оригиналом <документа>');
  const select = selector(page), choices = await options(select);
  assert.deepEqual(choices.map(option => option.value), ['','1','2','3']);
  await expect(select).toHaveValue('');
  assert.match(choices[0].text,/Оставить без подтверждения/);
  assert.match(choices[1].text,/Предложение.*ЦЕЛЬ<&"-1/);
  assert.match(choices[2].text,/Предложение.*ЦЕЛЬ-2/);
  assert.doesNotMatch(choices[3].text,/Предложение/);
  assert.match(choices[3].text,/ДРУГОЙ-3/);
  assert.doesNotMatch(choices.map(option => option.text).join(' '),/ЧУЖОЙ|НОВАЯ-ЦЕЛЬ/);
});

test('a proposed link is sent only after a person selects it', async t => {
  const {documents,result,match} = fixtures(); result.matches = [match];
  const app = await setup(t,{documents,result}), {page} = app;
  await page.getByLabel('Проверяющий связи').fill('Проверяющий');
  await page.getByLabel('Основание сопоставления').fill('Сверено с оригиналами');
  await page.getByRole('button',{name:'Применить и пересчитать'}).click();
  await expect.poll(() => app.posted().length).toBe(1);
  assert.deepEqual(app.posted()[0].manual_links, []);
  await expect(page.getByRole('button',{name:'Применить и пересчитать'})).toBeEnabled();
  await selector(page).selectOption('2');
  await page.getByLabel('Проверяющий связи').fill('Проверяющий');
  await page.getByLabel('Основание сопоставления').fill('Сверено с оригиналами');
  await page.getByRole('button',{name:'Применить и пересчитать'}).click();
  await expect.poll(() => app.posted().length).toBe(2);
  assert.deepEqual(app.posted()[1].manual_links,[{document_id:'delivery',line_id:'source-1',target_line_id:'2',reviewer:'Проверяющий',comment:'Сверено с оригиналами'}]);
});

test('automatic similar SKU matches display both original values without manual controls', async t => {
  const {documents,result,match} = fixtures();
  result.matches = [{...match,state:'matched',method:'similar_sku_auto',target_line_id:'1'}];
  const {page} = await setup(t,{documents,result});
  await expect(page.getByRole('heading',{name:'Автоматически сопоставленные похожие артикулы'})).toBeVisible();
  await expect(page.locator('main')).toContainText('Связано автоматически');
  await expect(page.locator('main')).toContainText('Остальные реквизиты сверяются отдельно');
  await expect(page.locator('main')).toContainText('ОРИГ<&"');
  await expect(page.locator('main')).toContainText('ЦЕЛЬ<&"-1');
  await expect(page.locator('main')).toContainText('target-spec');
  await expect(page.locator('main')).not.toContainText('ЧУЖОЙ-1');
  await expect(page.getByRole('button',{name:'Применить и пересчитать'})).toHaveCount(0);
  await expect(page.getByRole('combobox',{name:/^Позиция для /})).toHaveCount(0);
});

test('saved case documents are used when the run has no document snapshot', async t => {
  const {documents,result,match} = fixtures(); result.matches = [match];
  const {page} = await setup(t,{documents,result,includeRequestDocuments:false});
  await expect(page.locator('main')).toContainText('ОРИГ<&"');
  assert.match((await options(selector(page)))[1].text,/ЦЕЛЬ<&"-1/);
});

test('missing documents in an existing snapshot are not replaced with newer case values', async t => {
  const {documents,result,match} = fixtures(); result.matches = [match];
  const {page} = await setup(t,{documents:documents.slice(0,2),savedDocuments:documents,result,stale:true});
  await expect(page.locator('main')).toContainText('Данные целевого документа недоступны');
  assert.deepEqual((await options(selector(page))).map(option => option.value), ['']);
  await expect(page.locator('main')).not.toContainText('ЦЕЛЬ<&"-1');
});

test('each match resolves repeated line IDs in its own target document', async t => {
  const {documents,result,match} = fixtures();
  documents[0].items.push({line_id:'source-2',sku:'ОРИГ-2',name:'Второй товар'});
  result.matches = [match,{...match,line_id:'source-2',target_document_id:'other-spec',candidates:['1']}];
  const {page} = await setup(t,{documents,result});
  const first = selector(page), second = selector(page,'delivery','source-2');
  const firstOptions = await options(first), secondOptions = await options(second);
  assert.match(firstOptions[1].text,/Предложение.*ЦЕЛЬ<&"-1/);
  assert.match(secondOptions[1].text,/Предложение.*ЧУЖОЙ-1/);
  assert.doesNotMatch(secondOptions[2].text,/Предложение/);
  assert.match(secondOptions[2].text,/ЧУЖОЙ-2/);
  await expect(first).toHaveValue('');
  await expect(second).toHaveValue('');
});

test('document IDs, line IDs and option names cannot inject markup', async t => {
  const {documents,result,match} = fixtures(), injected = '<img src=x onerror=alert(1)>';
  documents[0].id = 'delivery"'+injected;
  documents[0].items[0].line_id = 'source"'+injected;
  documents[2].id = 'target"'+injected;
  documents[2].items[0].line_id = '1"'+injected;
  documents[2].items[0].name = injected;
  result.matches = [{...match,document_id:documents[0].id,line_id:documents[0].items[0].line_id,target_document_id:documents[2].id,candidates:[documents[2].items[0].line_id]}];
  const {page} = await setup(t,{documents,result});
  const select = selector(page,documents[0].id,documents[0].items[0].line_id);
  await expect(page.locator('main img')).toHaveCount(0);
  await expect(page.locator('main')).toContainText(injected);
  const choices = await options(select);
  assert.equal(choices[1].value, documents[2].items[0].line_id);
  assert.match(choices[1].text,/Предложение.*<img src=x onerror=alert\(1\)>/);
  await select.selectOption(documents[2].items[0].line_id);
  await page.getByLabel('Проверяющий связи').fill('Проверяющий');
  await page.getByLabel('Основание сопоставления').fill('Сверено');
  const posted = page.waitForRequest(request => request.method() === 'POST' && request.url().endsWith('/api/cases/case-1/runs'));
  await page.getByRole('button',{name:'Применить и пересчитать'}).click();
  assert.deepEqual((await posted).postDataJSON().manual_links,[{document_id:documents[0].id,line_id:documents[0].items[0].line_id,target_line_id:documents[2].items[0].line_id,reviewer:'Проверяющий',comment:'Сверено'}]);
});

test('other unresolved matches retain their ordinary manual choices', async t => {
  const {documents,result,match} = fixtures();
  result.matches = [{...match,method:'name'}];
  result.positions = [{line_id:'p1',name:'Обычная позиция',unit:'шт',complete:true}];
  const {page} = await setup(t,{documents,result}), select = selector(page), choices = await options(select);
  assert.deepEqual(choices.map(option => option.value), ['','p1']);
  await expect(select).toHaveValue('');
  assert.match(choices[1].text,/Обычная позиция/);
  assert.doesNotMatch(choices[1].text,/Предложение/);
});

test('automatic contract-number matching is prominent even without SKU matches', async t => {
  const {documents,result} = fixtures();
  result.audit = [{code:'SIMILAR_CONTRACT_ID',document_ids:['contract','delivery'],explanation:'Номер договора a1lgc<&" сопоставлен с аIlgс: похожие символы и совпадающие подтверждённые реквизиты.'}];
  const {page} = await setup(t,{documents,result});
  await expect(page.getByRole('heading',{name:'Автоматически сопоставленные номера договоров'})).toBeVisible();
  await expect(page.locator('main')).toContainText('Похожие номера договоров сопоставлены автоматически');
  await expect(page.locator('main')).toContainText('a1lgc<&"');
  await expect(page.locator('main')).toContainText('аIlgс');
  await expect(page.locator('main')).toContainText('contract, delivery');
  await expect(page.getByRole('button',{name:'Применить и пересчитать'})).toHaveCount(0);
});
