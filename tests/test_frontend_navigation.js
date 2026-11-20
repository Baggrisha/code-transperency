/* Navigation races and deep links exercised through the real browser UI. */
const nodeTest = require('node:test');
const test = (name, fn) => nodeTest(name, {timeout:60000}, fn);
test.before = nodeTest.before;
test.after = nodeTest.after;
const assert = require('node:assert/strict');
const {deferred, expect, http, scenario, startBrowser} = require('../scripts/check_web');

let runtime;
test.before(async () => { runtime = await startBrowser(); });
test.after(async () => { if (runtime) await runtime.close(); });

function documentFixture(id) {
  return {id,type:'contract',filename:id+'.txt',number:id,contract_id:id,status:'active',items:[],fields_confidence:{}};
}
function run(id, caseId, documents, asOf = '2026-10-31') {
  return {id,case_id:caseId,request:{documents,as_of:asOf,delivery_complete:false,manual_links:[]},
    result:{status:'ok',signals:[],positions:[],matches:[],audit:[],as_of:asOf,engine_version:'test',input_hash:'input',result_hash:'result'},
    reviews:[],latest_reviews:{},stale:false};
}
function signal() {
  return {id:'signal-A',code:'TEST',title:'Сигнал A',explanation:'Проверить источник',expected:'A',actual:'B',rule:'Тестовое правило',blocks_comparison:false,
    sources:[{document_id:'doc-B',filename:'doc-B.txt',excerpt:'Источник B'}]};
}
async function setup(t, initial = '/', initScript) {
  const docs = [documentFixture('doc-A'),documentFixture('doc-B')];
  const cases = {
    A:{id:'A',title:'Проверка A',contract_number:'A',documents:docs.map(document => ({id:document.id,revision:1,document})),runs:[{id:'run-A'}],metadata_revision:0},
    B:{id:'B',title:'Проверка B',contract_number:'B',documents:[{id:docs[1].id,revision:1,document:docs[1]}],runs:[{id:'run-B'}],metadata_revision:0},
  };
  const runs = {'run-A':run('run-A','A',docs),'run-B':run('run-B','B',[docs[1]],'2026-11-15'),
    'run-old':run('run-old','A',[docs[0]],'2026-01-01'),'run-new':run('run-new','A',docs)};
  runs['run-A'].result.signals = [signal()];
  runs['run-old'].result.signals = [signal()];
  const app = await scenario(runtime, {
    'GET /api/demo': [],
    'GET /api/cases': Object.values(cases).map(value => ({id:value.id,title:value.title,contract_number:value.contract_number,document_count:value.documents.length,last_run_status:'ok',signal_count:0,pending_signal_count:0})),
    'GET /api/cases/A': cases.A,
    'GET /api/cases/B': cases.B,
    ...Object.fromEntries(Object.entries(runs).map(([id,value]) => ['GET /api/runs/'+id,value])),
    'GET /api/runs?limit=25&offset=0': {items:[],total:0,limit:25,offset:0},
    'GET /api/cases/A/documents/doc-B/file': http(200,'Document B'),
    'GET /api/cases/B/documents/doc-B/file': http(200,'Document B'),
  });
  const gates = [];
  t.after(() => { for (const gate of gates) gate.release.resolve(); });
  t.after(() => app.close());
  app.delay = (key, result) => {
    const gate = {started:deferred(),release:deferred()};
    gates.push(gate);
    app.routes.set(key, async () => { gate.started.resolve(); await gate.release.promise; return result; });
    return gate;
  };
  app.cases = cases;
  app.runs = runs;
  if (initScript) await app.page.addInitScript(initScript);
  await app.page.goto(initial);
  if (initial === '/') await expect(app.page.getByRole('heading',{name:'Проверки контрактов'})).toBeVisible();
  else await expect(app.page.getByRole('heading',{name:'Проверка A'})).toBeVisible();
  await expect(app.page.locator('#busy')).toBeHidden();
  return app;
}
async function openB(page) {
  const home = page.getByRole('button',{name:'Все проверки'});
  await home.click();
  await expect(home).toBeEnabled();
  const refreshed = page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === '/api/cases');
  await page.getByRole('button',{name:'B',exact:true}).click();
  await refreshed;
  await expect(page.getByRole('heading',{name:'Проверка B'})).toBeVisible();
}
async function assertBOptions(page) {
  await expect(page.getByRole('heading',{name:'Проверка B'})).toBeVisible();
  await page.getByRole('tab',{name:'Сопоставление'}).click();
  await expect(page.getByLabel('Дата среза')).toHaveValue('2026-11-15');
  await expect(page).toHaveURL(/#case\/B\/matching$/);
}

test('a completed run cannot replace another case or its options after navigation', async t => {
  const app = await setup(t,'/#case/A/matching'), gate = app.delay('POST /api/cases/A/runs',{id:'run-new'});
  await app.page.getByRole('button',{name:'Повторить сверку'}).click();
  await gate.started.promise;
  await openB(app.page);
  gate.release.resolve();
  await expect(app.page.locator('#toast')).toContainText('Сверка выполнена');
  await assertBOptions(app.page);
  await expect(app.page.locator('main')).not.toContainText('Проверка A');
});

test('run completion does not force the workspace back over the run history', async t => {
  const app = await setup(t,'/#case/A/matching'), gate = app.delay('POST /api/cases/A/runs',{id:'run-new'});
  await app.page.getByRole('button',{name:'Повторить сверку'}).click();
  await gate.started.promise;
  await app.page.getByRole('button',{name:'История проверок'}).click();
  await expect(app.page.getByRole('heading',{name:'Журнал проверок'})).toBeVisible();
  gate.release.resolve();
  await expect(app.page.locator('#toast')).toContainText('Сверка выполнена');
  await expect(app.page.getByRole('heading',{name:'Журнал проверок'})).toBeVisible();
  await expect(app.page).toHaveURL(/#history$/);
});

test('returning home invalidates an in-flight case opening', async t => {
  const app = await setup(t), gate = app.delay('GET /api/cases/A',app.cases.A);
  await app.page.getByRole('button',{name:'A',exact:true}).click();
  await gate.started.promise;
  await app.page.getByRole('button',{name:'Все проверки'}).click();
  gate.release.resolve();
  await expect(app.page.locator('#busy')).toBeHidden();
  await expect(app.page.getByRole('heading',{name:'Проверки контрактов'})).toBeVisible();
  await expect(app.page).toHaveURL(/#checks$/);
  assert.equal(app.requests.filter(request => request.key === 'GET /api/runs/run-A').length,0);
});

test('historical run identity and options survive tab navigation and a deep-link reload', async t => {
  const app = await setup(t,'/#case/A/results/run/run-old'), {page} = app;
  await expect(page).toHaveURL(/#case\/A\/results\/run\/run-old$/);
  await page.getByRole('tab',{name:'Сопоставление'}).click();
  await expect(page).toHaveURL(/#case\/A\/matching\/run\/run-old$/);
  await expect(page.getByLabel('Дата среза')).toHaveValue('2026-01-01');
  await page.reload();
  await expect(page.getByRole('tab',{name:'Сопоставление'})).toHaveAttribute('aria-selected','true');
  await expect(page.getByLabel('Дата среза')).toHaveValue('2026-01-01');
  await expect(page).toHaveURL(/#case\/A\/matching\/run\/run-old$/);
  assert.ok(app.requests.filter(request => request.key === 'GET /api/runs/run-old').length >= 2);
});

test('an upload continues for its original case without reopening it over a newer case', async t => {
  const app = await setup(t,'/#case/A/documents'), gate = app.delay('POST /api/cases/A/documents',{id:'uploaded'});
  await app.page.getByLabel('Выбрать документы').setInputFiles({name:'original.txt',mimeType:'text/plain',buffer:Buffer.from('hello')});
  await app.page.getByRole('button',{name:'Загрузить документы'}).click();
  await gate.started.promise;
  await openB(app.page);
  gate.release.resolve();
  await expect(app.page.locator('#toast')).toContainText('Документы обработаны');
  await assertBOptions(app.page);
  const uploads = app.requests.filter(request => request.key.startsWith('POST ') && request.key.endsWith('/documents'));
  assert.deepEqual(uploads.map(request => request.key),['POST /api/cases/A/documents']);
  assert.match(uploads[0].body,/original\.txt/);
});

test('saving a signal decision cannot read or overwrite a newly selected run', async t => {
  const app = await setup(t,'/#case/A/results');
  const gate = app.delay('POST /api/runs/run-A/signals/signal-A/reviews',{sequence:1});
  const article = app.page.locator('article').filter({hasText:'Сигнал A'});
  await article.getByLabel('Решение').selectOption('resolved');
  await article.getByLabel('Проверяющий').fill('Тест');
  await article.getByLabel('Комментарий').fill('Сверено');
  await article.getByRole('button',{name:'Сохранить'}).click();
  await gate.started.promise;
  await openB(app.page);
  const count = app.requests.length;
  gate.release.resolve();
  await expect(app.page.locator('#toast')).toContainText('Решение сохранено');
  await assertBOptions(app.page);
  assert.deepEqual(app.requests.slice(count).map(request => request.key),['GET /api/cases']);
});

test('a slow document text read cannot overwrite a later viewer in the same case', async t => {
  const app = await setup(t,'/#case/A/documents',() => {
    const text = Blob.prototype.text;
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    window.__textRead = {started:false,completed:false,release};
    let first = true;
    Blob.prototype.text = function(...args) {
      if (!first) return text.apply(this,args);
      first = false;
      window.__textRead.started = true;
      return gate.then(() => text.apply(this,args)).finally(() => { window.__textRead.completed = true; });
    };
  });
  app.routes.set('GET /api/cases/A/documents/doc-A/file',http(200,'Document A'));
  const {page} = app;
  await page.getByRole('button',{name:'Открыть doc-A.txt'}).click();
  await page.waitForFunction(() => window.__textRead.started);
  await page.getByRole('button',{name:'Закрыть диалог'}).click();
  await page.getByRole('button',{name:'Открыть doc-B.txt'}).click();
  await expect(page.locator('#viewer-content')).toContainText('Document B');
  await page.evaluate(() => window.__textRead.release());
  await page.waitForFunction(() => window.__textRead.completed);
  await expect(page.getByRole('dialog')).toContainText('doc-B.txt');
  await expect(page.locator('#viewer-content')).toContainText('Document B');
  await expect(page.locator('#viewer-content')).not.toContainText('Document A');
});

test('a late viewer error cannot replace a newer document with an old failure', async t => {
  const app = await setup(t,'/#case/A/documents'), gate = app.delay('GET /api/cases/A/documents/doc-A/file',http(503,{detail:'Old failure'}));
  const {page} = app;
  await page.getByRole('button',{name:'Открыть doc-A.txt'}).click();
  await gate.started.promise;
  await page.getByRole('button',{name:'Закрыть диалог'}).click();
  await page.getByRole('button',{name:'Открыть doc-B.txt'}).click();
  await expect(page.locator('#viewer-content')).toContainText('Document B');
  gate.release.resolve();
  await expect(page.locator('#busy')).toBeHidden();
  await expect(page.locator('#viewer-content')).toContainText('Document B');
  await expect(page.locator('#viewer-content')).not.toContainText('Old failure');
});

test('historical viewing does not substitute a newer document absent from its snapshot', async t => {
  const app = await setup(t,'/#case/A/results/run/run-old');
  const article = app.page.locator('article').filter({hasText:'Сигнал A'});
  await article.getByText('Исходные данные · 1 фрагментов').click();
  await article.getByRole('button',{name:'Просмотреть'}).click();
  await expect(app.page.locator('#toast')).toContainText('отсутствует в сохранённом снимке');
  assert.equal(app.requests.filter(request => request.key.endsWith('/file')).length,0);
});

test('metadata save reports successful persistence when registry refresh fails', async t => {
  const app = await setup(t,'/#case/A/overview');
  app.routes.set('PUT /api/cases/A',{id:'A',title:'Updated',description:'',contract_number:'',metadata_revision:1,revision:0,updated_at:'2026-11-20'});
  app.routes.set('GET /api/cases',http(503,{detail:'Network unavailable'}));
  await app.page.getByLabel('Наименование',{exact:true}).fill('Updated');
  await app.page.getByRole('button',{name:'Сохранить реквизиты'}).click();
  await expect(app.page.getByRole('heading',{name:'Updated'})).toBeVisible();
  await expect(app.page.locator('#toast')).toContainText('сохранены');
  await expect(app.page.locator('#toast')).toContainText('Не удалось обновить');
  assert.equal(app.requests.filter(request => request.key === 'PUT /api/cases/A').length,1);
});

test('a completed type edit does not close a newer dialog or return to the old case', async t => {
  const app = await setup(t,'/#case/A/documents'), gate = app.delay('PUT /api/cases/A/documents/doc-A',{revision:2});
  const {page} = app;
  await page.getByRole('button',{name:'Изменить тип doc-A.txt'}).click();
  const dialog = page.getByRole('dialog');
  await dialog.locator('#type-trigger').click();
  await dialog.getByRole('option',{name:'Накладная'}).click();
  await dialog.getByLabel('Проверяющий').fill('Тест');
  await dialog.getByLabel('Основание изменения').fill('Сверено');
  await dialog.getByRole('button',{name:'Сохранить тип'}).click();
  await gate.started.promise;
  await dialog.getByRole('button',{name:'Закрыть диалог'}).click();
  await openB(page);
  await page.getByRole('tab',{name:'Документы'}).click();
  await page.getByRole('button',{name:'Открыть doc-B.txt'}).click();
  await expect(page.locator('#viewer-content')).toContainText('Document B');
  gate.release.resolve();
  await expect(page.locator('#toast')).toContainText('Тип сохранён');
  await expect(page.getByRole('dialog')).toContainText('doc-B.txt');
  await expect(page.locator('#viewer-content')).toContainText('Document B');
  await page.getByRole('dialog').getByRole('button',{name:'Закрыть диалог'}).click();
  await expect(page.getByRole('heading',{name:'Проверка B'})).toBeVisible();
});

test('the skip link moves focus without changing the workspace route', async t => {
  const app = await setup(t,'/#case/A/matching'), {page} = app;
  const url = page.url();
  await page.getByRole('link',{name:'Перейти к содержимому'}).focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('main')).toBeFocused();
  assert.equal(page.url(),url);
  await expect(page.getByRole('tab',{name:'Сопоставление'})).toHaveAttribute('aria-selected','true');
});

test('a click outside a dialog closes it without closing on clicks inside', async t => {
  const {page} = await setup(t);
  await page.getByRole('button',{name:'+ Новая проверка'}).click();
  const dialog = page.getByRole('dialog',{name:'Данные контракта'});
  await expect(dialog).toBeVisible();
  await dialog.getByLabel('Наименование').fill('Тестовая проверка');
  const box = await dialog.boundingBox();
  assert.ok(box && box.x > 0 && box.y > 0, 'Dialog must leave a clickable backdrop');
  await page.mouse.click(box.x + 3, box.y + 3);
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel('Наименование')).toHaveValue('Тестовая проверка');
  await page.mouse.click(box.x - 2, box.y - 2);
  await expect(dialog).toBeHidden();
  await expect(page.getByRole('heading',{name:'Проверки контрактов'})).toBeVisible();
});
