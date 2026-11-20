/* Browser smoke test and shared harness for the Node behavioral tests.
 * The API is intercepted at the HTTP boundary; the frontend is the built app
 * served by an isolated uvicorn process, not a substitute DOM or React mock.
 */
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {chromium, expect} = require('@playwright/test');

const root = path.resolve(__dirname, '..');
const http = (status, body) => ({httpResponse: true, status, body});
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}

async function startBrowser() {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'kontrol-browser-'));
  const venvPython = path.join(root, '.venv', 'bin', 'python');
  const python = process.env.PYTHON || (fs.existsSync(venvPython) ? venvPython : 'python3');
  const server = spawn(python, ['-m', 'uvicorn', 'kontrol.api:app', '--host', '127.0.0.1', '--port', '0'], {
    cwd: root, env: {...process.env, KONTROL_DATA_DIR: dataDir}, stdio: ['ignore', 'ignore', 'pipe'],
  });
  let log = '';
  let browser;
  async function close() {
    try {
      if (browser) await browser.close();
    } finally {
      if (server.pid && server.exitCode === null && server.signalCode === null) {
        const exited = once(server, 'exit');
        server.kill('SIGTERM');
        await exited;
      }
      fs.rmSync(dataDir, {recursive: true, force: true});
    }
  }
  try {
    const baseURL = await new Promise((resolve, reject) => {
      let timer;
      const finish = (error, url) => {
        clearTimeout(timer);
        server.stderr.off('data', onData);
        server.off('error', onError);
        server.off('exit', onExit);
        error ? reject(error) : resolve(url);
      };
      timer = setTimeout(() => finish(new Error('uvicorn did not start: ' + log)), 30000);
      const onData = chunk => {
        log += chunk.toString();
        const match = log.match(/Uvicorn running on (http:\/\/127\.0\.0\.1:\d+)/);
        if (match) finish(null, match[1]);
      };
      const onError = error => finish(error);
      const onExit = () => finish(new Error('uvicorn exited before starting: ' + log));
      server.stderr.on('data', onData);
      server.once('error', onError);
      server.once('exit', onExit);
    });
    server.stderr.resume();
    browser = await chromium.launch(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? {executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH} : {});
    return {browser, baseURL, close};
  } catch (error) {
    await close();
    throw error;
  }
}

async function scenario(runtime, initialRoutes = {}) {
  const context = await runtime.browser.newContext({baseURL: runtime.baseURL});
  const page = await context.newPage();
  const routes = new Map(Object.entries(initialRoutes));
  const requests = [], unexpected = [], pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url());
    const key = `${request.method()} ${url.pathname}${url.search}`;
    requests.push({key, body: request.postData(), request});
    if (!routes.has(key)) {
      unexpected.push(key);
      await route.fulfill({status: 500, json: {detail: 'Unexpected request: ' + key}});
      return;
    }
    const handler = routes.get(key);
    const result = typeof handler === 'function' ? await handler(request) : handler;
    if (result?.httpResponse) {
      await route.fulfill({status: result.status, ...(typeof result.body === 'string'
        ? {body: result.body, contentType: 'text/plain; charset=utf-8'} : {json: result.body})});
    } else {
      await route.fulfill({json: result});
    }
  });
  return {
    page, routes, requests,
    async close() {
      await context.close();
      assert.deepEqual(unexpected, [], 'Unexpected API requests');
      assert.deepEqual(pageErrors, [], 'Uncaught browser errors');
    },
  };
}

async function smoke() {
  const fixture = JSON.parse(fs.readFileSync(path.join(root, 'fixtures/cases.json'))).find(item => item.id === 'T02');
  const observed = JSON.parse(fs.readFileSync(path.join(root, 'reports/acceptance.json'))).cases.find(item => item.id === 'T02').observed;
  const request = structuredClone(fixture.request);
  request.manual_links = [{document_id:'delivery-1', line_id:'1', target_line_id:'1', reviewer:'Тест', comment:'Проверено по артикулу'}];
  const dossier = {id:'case-1',title:'Тестовый комплект',contract_number:'ГК-2026/0142',description:'Синтетические данные',revision:3,documents:request.documents.map(document => ({id:document.id,revision:1,document})),runs:[{id:'run-1'}]};
  for (const record of dossier.documents) {
    record.document.extraction_warnings ||= [];
    record.document.fields_confidence ||= {};
    for (const item of record.document.items) item.confidence ||= {};
  }
  dossier.documents[0].document.source = {document_id:dossier.documents[0].id,excerpt:'Источник <img src=x onerror=alert(1)>'};
  const run = {id:'run-1',case_id:dossier.id,request,result:observed,reviews:[],stale:false};
  const cases = [
    {id:dossier.id,title:dossier.title,contract_number:dossier.contract_number,document_count:dossier.documents.length,last_run_status:observed.status,signal_count:observed.signals.length,pending_signal_count:observed.signals.length},
    {id:'reviewed',title:'Рассмотренный комплект',contract_number:'ГК-0002',document_count:2,last_run_status:'needs_review',signal_count:2,pending_signal_count:0},
    {id:'outdated',title:'Изменённый комплект',contract_number:'ГК-0003',document_count:3,last_run_status:'ok',last_run_stale:true,signal_count:1,pending_signal_count:0},
  ];
  const runtime = await startBrowser();
  let app;
  try {
    app = await scenario(runtime, {
      'GET /api/demo': [{id:'T02',title:fixture.title}],
      'GET /api/cases': cases,
      'GET /api/cases/case-1': dossier,
      'GET /api/runs/run-1': run,
      'POST /api/cases/case-1/runs': {id:'run-1'},
      'GET /api/cases/case-1/events': [{action:'case_created',created_at:'2026-11-18T09:00:00Z',payload:{comment:'Комплект загружен'}}],
      'GET /api/runs?limit=25&offset=0': {items:[{id:run.id,case_id:dossier.id,case_title:dossier.title,created_at:'2026-11-18T10:00:00Z',status:observed.status,signal_count:observed.signals.length,stale:false}],total:1,limit:25,offset:0},
    });
    const {page} = app;
    await page.goto('/');
    await expect(page.getByRole('heading', {name:'Проверки контрактов'})).toBeVisible();
    await expect(page.locator('#nav-checks')).toHaveAttribute('aria-current', 'page');
    await expect(page).toHaveURL(/#checks$/);
    for (const [label, value] of [['Всего проверок',3],['В работе',2],['Рассмотрено',1],['Сигналов',observed.signals.length+3]]) {
      await expect(page.locator('.stat').filter({hasText:label}).locator('b')).toHaveText(String(value));
    }
    await page.getByText('Готовые проверки', {exact:true}).click();
    await expect(page.getByLabel('Готовая проверка')).toHaveValue('T02');
    await page.getByLabel('Поиск по контракту или названию').fill('гк-2026/0142');
    await expect(page.locator('#registry-rows')).toContainText('Тестовый комплект');
    await expect(page.locator('#registry-rows')).not.toContainText('Рассмотренный комплект');
    await page.getByLabel('Поиск по контракту или названию').fill('');
    await page.getByLabel('Статус проверки').selectOption('reviewed');
    await expect(page.locator('#registry-rows')).toContainText('Рассмотренный комплект');
    await expect(page.locator('#registry-rows')).not.toContainText('Изменённый комплект');
    await page.getByLabel('Статус проверки').selectOption('all');
    await page.getByRole('button', {name:'ГК-2026/0142'}).click();
    await expect(page.getByRole('heading', {name:'Данные контракта'})).toBeVisible();
    await expect(page.locator('#nav-workspace')).toHaveAttribute('aria-current', 'page');
    await expect(page).toHaveURL(/#case\/case-1\/overview$/);
    await expect(page.locator('.stat').filter({hasText:'Документов'}).locator('b')).toHaveText(String(dossier.documents.length));
    await expect(page.locator('.stat').filter({hasText:'Ожидают решения'}).locator('b')).toHaveText(String(observed.signals.length));
    await page.getByRole('tab', {name:'Документы'}).click();
    await expect(page.getByRole('heading', {name:'Загрузка документов'})).toBeVisible();
    await expect(page.getByRole('button', {name:/^Изменить тип /}).first()).toBeVisible();
    await expect(page.getByRole('button', {name:/^Открыть /}).first()).toBeVisible();
    await page.getByRole('tab', {name:'Распознавание'}).click();
    await expect(page.getByRole('heading', {name:'Распознавание реквизитов'})).toBeVisible();
    await page.getByRole('button', {name:'Реквизиты', exact:true}).first().click();
    const editor = page.getByRole('dialog', {name:'Редактирование документа'});
    await editor.getByText('Фрагменты источника', {exact:true}).first().click();
    await expect(editor.getByText('Источник <img src=x onerror=alert(1)>')).toBeVisible();
    await expect(editor.locator('img')).toHaveCount(0);
    await editor.getByRole('button', {name:'Закрыть'}).click();
    await page.getByRole('tab', {name:'Сопоставление'}).click();
    await expect(page.getByRole('heading', {name:'Сопоставление позиций'})).toBeVisible();
    await expect(page.getByRole('tab', {name:'Сопоставление'})).toHaveAttribute('aria-selected','true');
    await expect(page.locator('#asof')).toHaveValue(request.as_of);
    await page.getByRole('button', {name:'Повторить сверку'}).click();
    await expect.poll(() => app.requests.filter(item => item.key === 'POST /api/cases/case-1/runs').length).toBe(1);
    const posted = JSON.parse(app.requests.find(item => item.key === 'POST /api/cases/case-1/runs').body);
    assert.deepEqual(posted.manual_links, request.manual_links);
    assert.equal(posted.as_of, request.as_of);
    await page.getByRole('tab', {name:'Результаты'}).click();
    await expect(page.getByText('Приёмка превышает документы поставки')).toBeVisible();
    await expect(page.getByText('Остаток', {exact:true})).toBeVisible();
    await page.getByRole('tab', {name:'История действий'}).click();
    await expect(page.locator('#events')).toContainText('Создана проверка');
    await expect(page.locator('#events')).toContainText('Комплект загружен');
    await page.getByRole('button', {name:'История проверок'}).click();
    await expect(page.getByRole('heading', {name:'Журнал проверок'})).toBeVisible();
    await expect(page.locator('#nav-history')).toHaveAttribute('aria-current','page');
    await expect(page.getByText('Тестовый комплект')).toBeVisible();
    await page.getByRole('button', {name:'Все проверки'}).click();
    await page.getByRole('button', {name:'Рабочая область'}).click();
    await expect(page.getByRole('heading', {name:'Тестовый комплект'})).toBeVisible();
    app.routes.set('GET /api/runs/run-1', {...run,stale:true});
    await page.goto('/#case/case-1/results');
    await expect(page.getByText('Документы изменены после этой проверки.')).toBeVisible();
    await expect(page.getByText('Приёмка превышает документы поставки')).toBeVisible();
    console.log(JSON.stringify({status:'passed',scope:'built React app in Chromium; isolated uvicorn and controlled API fixtures'},null,2));
  } finally {
    try { if (app) await app.close(); } finally { await runtime.close(); }
  }
}

module.exports = {deferred, expect, http, scenario, startBrowser};
if (require.main === module) smoke().catch(error => { console.error(error); process.exitCode = 1; });
