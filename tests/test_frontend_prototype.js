/* Browser-level contracts for the built standalone React prototype. */
const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium, expect } = require('@playwright/test');

const STORAGE_KEY = 'control-execution-demo-v1';
const dist = path.resolve(__dirname, '../web/dist');
const source = require('./fixtures/prototype_source.json');
const original = structuredClone(source.initial[0]);
original.description = 'Поставка уличных скамеек по контракту ГК-2026/0142.';
const previews = structuredClone(source.demoContents);
previews.d1.lines = previews.d1.lines.filter(line => line !== 'Данные синтетические. Документ показан для демонстрации.');
previews.d2.lines = previews.d2.lines.map(line => line === 'Редакция: 1 (демонстрационная)' ? 'Редакция: 1' : line);

const mimeTypes = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.woff2': 'font/woff2',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
};

let server;
let browser;
let origin;

before(async () => {
  if (!fs.existsSync(path.join(dist, 'prototype.html'))) {
    throw new Error('web/dist/prototype.html is missing; run npm run build before the browser tests');
  }
  server = http.createServer((request, response) => {
    const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
    const file = path.resolve(dist, '.' + pathname);
    if (!file.startsWith(dist + path.sep)) {
      response.writeHead(403).end();
      return;
    }
    fs.readFile(file, (error, data) => {
      if (error) {
        response.writeHead(404).end();
        return;
      }
      response.writeHead(200, { 'content-type': mimeTypes[path.extname(file)] || 'application/octet-stream' });
      response.end(data);
    });
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true });
});

after(async () => {
  if (browser) await browser.close();
  if (server) await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
});

async function start(t, { stored, blockedStorage = false, blockExternal = false } = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
  t.after(() => context.close());
  const externalRequests = [];
  if (blockExternal) {
    await context.route('**/*', route => {
      const url = route.request().url();
      if (new URL(url).origin !== origin) {
        externalRequests.push(url);
        return route.abort();
      }
      return route.continue();
    });
  }
  const page = await context.newPage();
  if (blockedStorage) {
    await page.addInitScript(() => {
      Object.defineProperty(window, 'localStorage', {
        configurable: true,
        get() { throw new DOMException('Storage unavailable', 'SecurityError'); },
      });
    });
  }
  await page.goto(`${origin}/prototype.html`);
  await expect(page.getByRole('heading', { name: 'Проверки контрактов' })).toBeVisible();
  if (stored !== undefined) {
    const value = typeof stored === 'string' ? stored : JSON.stringify(stored);
    await page.evaluate(([key, data]) => localStorage.setItem(key, data), [STORAGE_KEY, value]);
    await page.reload();
  }
  return { page, externalRequests };
}

async function snapshot(page) {
  return page.evaluate(key => JSON.parse(localStorage.getItem(key)), STORAGE_KEY);
}

async function openOriginal(page) {
  await page.getByRole('button', { name: 'Открыть проверку', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Данные контракта' })).toBeVisible();
}

async function openTab(page, name) {
  await page.getByRole('navigation', { name: 'Разделы проверки' })
    .getByRole('button', { name, exact: true }).click();
}

async function createCase(page, number = 'К-002', name = 'Поставка мебели') {
  const firstPrompt = page.waitForEvent('dialog');
  const click = page.getByRole('button', { name: '+ Новая проверка', exact: true }).click();
  const first = await firstPrompt;
  const secondPrompt = page.waitForEvent('dialog');
  await first.accept(number);
  const second = await secondPrompt;
  await second.accept(name);
  await click;
  assert.equal(first.type(), 'prompt');
  assert.equal(second.type(), 'prompt');
  await expect(page.getByRole('heading', { name: 'Загрузка документов' })).toBeVisible();
}

async function withDialog(page, action, accept = true) {
  const nextDialog = page.waitForEvent('dialog');
  const pending = action();
  const dialog = await nextDialog;
  const result = { type: dialog.type(), message: dialog.message() };
  if (accept) await dialog.accept();
  else await dialog.dismiss();
  await pending;
  return result;
}

async function upload(page, name = 'накладная.txt', content = 'Документ поставки') {
  await page.getByLabel('Выбрать файлы', { exact: true }).setInputFiles({
    name,
    mimeType: 'text/plain',
    buffer: Buffer.from(content),
  });
}

async function openSignal(page) {
  await openTab(page, 'Результаты');
  await page.getByRole('button', { name: 'Рассмотреть', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Основание сигнала' })).toBeVisible();
}

async function openedFile(page) {
  const popup = page.waitForEvent('popup');
  await page.getByRole('button', { name: 'Открыть', exact: true }).click();
  const opened = await popup;
  await opened.waitForLoadState();
  return opened;
}

test('startup presents the supplied case and counts without presentation labels', async t => {
  const { page } = await start(t);
  await expect(page).toHaveTitle(/Проверки контрактов/);
  const row = page.getByRole('row').filter({ hasText: original.number });
  await expect(row).toContainText(original.name);
  await expect(row.getByRole('cell').nth(2)).toHaveText(original.status);
  await expect(row.getByRole('cell').nth(3)).toHaveText(String(original.docs.length));
  await expect(row.getByRole('cell').nth(4)).toHaveText(String(original.signals.length));
  await expect(page.getByRole('button', { name: 'Открыть проверку', exact: true })).toBeVisible();
  assert.doesNotMatch(await page.locator('body').innerText(), /прототип|локальный режим|демо-кейс|демонстрационн|синтетическ|учебный кейс/i);
});

test('overview and recognition show the supplied values without presentation identifiers', async t => {
  const { page } = await start(t);
  await openOriginal(page);
  for (const [tab, heading] of [['Обзор', 'Данные контракта'], ['Распознавание', 'Распознавание реквизитов']]) {
    await openTab(page, tab);
    await expect(page.getByRole('heading', { name: heading })).toBeVisible();
    await expect(page.getByLabel('Номер', { exact: true })).toHaveValue(original.number);
    await expect(page.getByLabel('Наименование', { exact: true })).toHaveValue(original.name);
    await expect(page.getByLabel('Описание', { exact: true })).toHaveValue(original.description);
    assert.doesNotMatch(await page.locator('main').innerText(), /DEMO-001|прототип|демонстрационн|синтетическ|учебный кейс/i);
  }
});

test('the four document previews preserve the supplied evidence and neutral descriptions', async t => {
  const { page } = await start(t);
  await openOriginal(page);
  await openTab(page, 'Документы');
  for (const [index, doc] of original.docs.entries()) {
    await expect(page.getByText(doc.name, { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Открыть', exact: true }).nth(index).click();
    const dialog = page.getByRole('dialog', { name: 'Предпросмотр документа' });
    await expect(dialog.getByRole('heading', { name: previews[doc.id].title, exact: true })).toBeVisible();
    for (const line of previews[doc.id].lines) {
      await expect(dialog.getByText(line, { exact: true })).toBeVisible();
    }
    assert.doesNotMatch(await dialog.innerText(), /прототип|демонстрационн|синтетическ|учебный кейс|оригинальный PDF|результат OCR/i);
    await dialog.getByRole('button', { name: 'Готово', exact: true }).click();
    await expect(dialog).toHaveCount(0);
  }
});

test('opening an original example preserves edited examples and user cases, then reuses the untouched copy', async t => {
  const { page } = await start(t);
  await openOriginal(page);
  await page.getByLabel('Номер', { exact: true }).fill('');
  await page.getByLabel('Наименование', { exact: true }).fill('');
  await page.getByLabel('Описание', { exact: true }).fill('');
  await page.getByRole('button', { name: 'Сохранить реквизиты' }).click();
  await openSignal(page);
  await page.getByLabel('Статус', { exact: true }).selectOption('В работе');
  await page.getByLabel('Комментарий специалиста').fill('Решение ещё уточняется');
  await page.getByRole('button', { name: 'Сохранить решение' }).click();
  await page.getByRole('button', { name: '← К результатам' }).click();
  await openTab(page, 'Документы');
  const confirmation = await withDialog(page, () =>
    page.getByRole('button', { name: 'Удалить Спецификация.pdf из комплекта' }).click());
  assert.equal(confirmation.type, 'confirm');
  await expect(page.getByText('Спецификация.pdf', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '← К списку' }).click();
  await createCase(page, 'USER-42', 'Пользовательское дело');
  await upload(page, 'пользовательский.txt', 'Saved evidence');
  await expect(page.getByText('пользовательский.txt', { exact: true })).toBeVisible();
  const before = await snapshot(page);
  const userId = before.selected;
  await page.getByRole('button', { name: '← К списку' }).click();
  await openOriginal(page);
  await expect(page.getByLabel('Номер', { exact: true })).toHaveValue(original.number);
  const afterOpening = await snapshot(page);
  assert.equal(afterOpening.cases.length, 3);
  assert.notEqual(afterOpening.selected, original.id);
  assert.deepEqual({ ...afterOpening.cases.find(item => item.id === afterOpening.selected), id: original.id }, original);
  for (const item of before.cases) {
    assert.deepEqual(afterOpening.cases.find(saved => saved.id === item.id), item);
  }
  assert.equal(afterOpening.cases.find(item => item.id === original.id).signals[0].comments[0].text, 'Решение ещё уточняется');
  assert.equal(afterOpening.cases.find(item => item.id === userId).docs[0].name, 'пользовательский.txt');
  await page.getByRole('button', { name: '← К списку' }).click();
  await openOriginal(page);
  const reused = await snapshot(page);
  assert.equal(reused.selected, afterOpening.selected);
  assert.equal(reused.cases.length, 3);
  await page.getByRole('button', { name: '← К списку' }).click();
  await page.getByRole('button', { name: 'Открыть проверку USER-42', exact: true }).click();
  await openTab(page, 'Документы');
  const userFile = await openedFile(page);
  await expect(userFile.locator('body')).toContainText('Saved evidence');
  await userFile.close();
  await page.getByRole('button', { name: '← К списку' }).click();
  await openOriginal(page);
  assert.equal((await snapshot(page)).selected, reused.selected);
  await page.reload();
  await expect(page.getByLabel('Номер', { exact: true })).toHaveValue(original.number);
  assert.equal((await snapshot(page)).selected, reused.selected);
});

test('opening a missing original example preserves the existing case and does not create duplicates', async t => {
  const userCase = { ...structuredClone(original), id: 'CASE-USER', number: 'USER-ONLY', name: 'Оставшееся дело', demo: false };
  const stored = { cases: [userCase], selected: userCase.id, tab: 'overview', page: 'home', signal: null };
  const { page } = await start(t, { stored });
  await openOriginal(page);
  let saved = await snapshot(page);
  assert.equal(saved.cases.length, 2);
  assert.equal(saved.selected, original.id);
  assert.deepEqual(saved.cases.find(item => item.id === userCase.id), userCase);
  await page.getByRole('button', { name: '← К списку' }).click();
  await openOriginal(page);
  saved = await snapshot(page);
  assert.equal(saved.cases.length, 2);
  assert.equal(saved.selected, original.id);
  assert.deepEqual(saved.cases.find(item => item.id === userCase.id), userCase);
});

test('all workspace tabs and the separate signal page are reachable', async t => {
  const { page } = await start(t);
  await openOriginal(page);
  for (const [tab, heading] of [
    ['Обзор', 'Данные контракта'],
    ['Документы', 'Загрузка документов'],
    ['Распознавание', 'Распознавание реквизитов'],
    ['Сопоставление', 'Сопоставление позиций'],
    ['Результаты', 'Реестр расхождений'],
    ['История действий', 'Журнал действий'],
  ]) {
    await openTab(page, tab);
    await expect(page.getByRole('heading', { name: heading })).toBeVisible();
    assert.doesNotMatch(await page.locator('main').innerText(), /прототип|локальный режим|демо-кейс|демонстрационн|синтетическ|учебный кейс/i);
  }
  await openSignal(page);
  for (const heading of ['Основание сигнала', 'Документы-источники', 'Рассмотрение специалистом', 'История рассмотрения']) {
    await expect(page.getByRole('heading', { name: heading })).toBeVisible();
  }
  await page.getByRole('button', { name: '← К результатам' }).click();
  await expect(page.getByRole('heading', { name: 'Реестр расхождений' })).toBeVisible();
});

test('legacy factory descriptions migrate narrowly, while edits and the selected screen survive reload', async t => {
  const legacyOriginal = structuredClone(source.initial[0]);
  const stored = {
    cases: [
      structuredClone(legacyOriginal),
      { ...structuredClone(legacyOriginal), id: 'DEMO-SHORT', description: 'Синтетические данные.' },
      { ...structuredClone(legacyOriginal), id: 'DEMO-CUSTOM', description: 'Моя проверенная правка' },
      { ...structuredClone(legacyOriginal), id: 'DEMO-EMPTY', description: '' },
      { ...structuredClone(legacyOriginal), id: 'USER-OLD-LABEL', demo: false },
    ],
    selected: 'DEMO-CUSTOM', tab: 'extract', page: 'case', signal: null,
  };
  const expected = structuredClone(stored);
  expected.cases[0].description = original.description;
  expected.cases[1].description = original.description;
  const { page } = await start(t, { stored });
  await expect(page.getByRole('heading', { name: 'Распознавание реквизитов' })).toBeVisible();
  await expect(page.getByLabel('Описание', { exact: true })).toHaveValue('Моя проверенная правка');
  await expect.poll(() => snapshot(page)).toEqual(expected);

  await page.getByRole('button', { name: '← К списку' }).click();
  await createCase(page);
  await openTab(page, 'Обзор');
  await page.getByLabel('Номер', { exact: true }).fill('ГК-42');
  await page.getByLabel('Наименование', { exact: true }).fill('Новые скамейки');
  await page.getByLabel('Описание', { exact: true }).fill('Ручное уточнение');
  await page.getByRole('button', { name: 'Сохранить реквизиты' }).click();
  await openTab(page, 'Распознавание');
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Распознавание реквизитов' })).toBeVisible();
  await expect(page.getByLabel('Номер', { exact: true })).toHaveValue('ГК-42');
  await expect(page.getByLabel('Наименование', { exact: true })).toHaveValue('Новые скамейки');
  await expect(page.getByLabel('Описание', { exact: true })).toHaveValue('Ручное уточнение');
});

test('uploaded documents do not acquire the example comparison or signals', async t => {
  const { page } = await start(t);
  await createCase(page);
  await upload(page);
  await openTab(page, 'Сопоставление');
  await expect(page.getByText('Автоматическое сопоставление не выполняется', { exact: false })).toBeVisible();
  await expect(page.getByText('Сопоставленные позиции пока отсутствуют.')).toBeVisible();
  await expect(page.getByText('Скамейка парковая', { exact: true })).toHaveCount(0);
  await openTab(page, 'Результаты');
  await expect(page.getByText('Отсутствие сигналов не означает, что расхождений нет.', { exact: false })).toBeVisible();
  await expect(page.getByText('Нет сигналов для отображения')).toBeVisible();
});

test('file selection persists metadata and rejects duplicate or unsupported files', async t => {
  const { page } = await start(t);
  await createCase(page);
  await upload(page, 'накладная.txt', '12345');
  await expect(page.getByText('накладная.txt', { exact: true })).toHaveCount(1);
  await expect(page.getByText('Накладная – 0 КБ – версия 1', { exact: true })).toBeVisible();
  let state = await snapshot(page);
  let docs = state.cases.find(item => item.id === state.selected).docs;
  assert.deepEqual(docs.map(doc => [doc.name, doc.size, doc.type, doc.version]), [['накладная.txt', 5, 'Накладная', 1]]);

  const duplicate = await withDialog(page, () => upload(page, 'накладная.txt', '12345'));
  assert.equal(duplicate.type, 'alert');
  assert.match(duplicate.message, /дубликат/i);
  const unsupported = await withDialog(page, () => upload(page, 'wrong.exe', '12345'));
  assert.equal(unsupported.type, 'alert');
  assert.match(unsupported.message, /Неподдерживаемый формат/);
  await expect(page.getByText('накладная.txt', { exact: true })).toHaveCount(1);
  await expect(page.getByText('wrong.exe', { exact: true })).toHaveCount(0);
  await upload(page, 'накладная.txt', '123456');
  await expect(page.getByText('накладная.txt', { exact: true })).toHaveCount(2);
  state = await snapshot(page);
  docs = state.cases.find(item => item.id === state.selected).docs;
  assert.deepEqual(docs.map(doc => doc.size), [5, 6]);
});

test('all categories are offered, cancellation preserves the type, and a saved type is logged and restored', async t => {
  const { page } = await start(t);
  await createCase(page);
  await upload(page);
  await page.getByRole('button', { name: 'Тип', exact: true }).click();
  let dialog = page.getByRole('dialog', { name: 'Изменить тип документа' });
  let trigger = dialog.getByRole('button', { name: 'Категория документа', exact: true });
  await trigger.click();
  await expect(trigger).toHaveAttribute('aria-expanded', 'true');
  await expect(dialog.getByRole('option')).toHaveText([
    'Контракт', 'Накладная', 'Спецификация', 'Документ о приёмке', 'Дополнительное соглашение',
    'Исправленная версия', 'Другое',
  ]);
  await dialog.getByRole('option', { name: 'Другое', exact: true }).click();
  await expect(trigger).toHaveText('Другое');
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await dialog.getByRole('button', { name: 'Отмена' }).click();
  await expect(page.getByText('Накладная – 0 КБ – версия 1', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Тип', exact: true }).click();
  dialog = page.getByRole('dialog', { name: 'Изменить тип документа' });
  trigger = dialog.getByRole('button', { name: 'Категория документа', exact: true });
  await trigger.click();
  await dialog.getByRole('option', { name: 'Исправленная версия', exact: true }).click();
  await dialog.getByRole('button', { name: 'Сохранить тип' }).click();
  await expect(page.getByText('Исправленная версия – 0 КБ – версия 1', { exact: true })).toBeVisible();
  await openTab(page, 'История действий');
  await expect(page.getByText('Тип документа накладная.txt: Накладная → Исправленная версия')).toBeVisible();
  await openTab(page, 'Документы');
  await page.reload();
  await expect(page.getByText('Исправленная версия – 0 КБ – версия 1', { exact: true })).toBeVisible();
});

test('dropping a file uses the same upload and category flow', async t => {
  const { page } = await start(t);
  await createCase(page);
  const transfer = await page.evaluateHandle(() => {
    const data = new DataTransfer();
    data.items.add(new File(['Поставка'], 'акт.txt', { type: 'text/plain' }));
    return data;
  });
  const target = page.getByRole('heading', { name: 'Перетащите файлы сюда' });
  await target.dispatchEvent('dragover', { dataTransfer: transfer });
  await target.dispatchEvent('drop', { dataTransfer: transfer });
  await transfer.dispose();
  await expect(page.getByText('акт.txt', { exact: true })).toBeVisible();
  await expect(page.getByText('Документ о приёмке – 0 КБ – версия 1', { exact: true })).toBeVisible();
  const state = await snapshot(page);
  assert.equal(state.cases.find(item => item.id === state.selected).docs[0].name, 'акт.txt');
});

test('file removal respects confirmation, persists, and permits a new original with the same name', async t => {
  const { page } = await start(t);
  await createCase(page);
  await upload(page, 'original.txt', 'Old evidence');
  const remove = page.getByRole('button', { name: 'Удалить original.txt из комплекта' });
  const cancelled = await withDialog(page, () => remove.click(), false);
  assert.equal(cancelled.type, 'confirm');
  await expect(page.getByText('original.txt', { exact: true })).toBeVisible();
  const confirmed = await withDialog(page, () => remove.click());
  assert.equal(confirmed.type, 'confirm');
  await expect(page.getByText('original.txt', { exact: true })).toHaveCount(0);
  await openTab(page, 'История действий');
  await expect(page.getByText('Удалён документ original.txt')).toBeVisible();
  await openTab(page, 'Документы');
  await page.reload();
  await expect(page.getByText('original.txt', { exact: true })).toHaveCount(0);
  await upload(page, 'original.txt', 'Replacement evidence');
  const popup = await openedFile(page);
  await expect(popup.locator('body')).toContainText('Replacement evidence');
  await popup.close();
});

test('an uploaded original opens in the current session but is unavailable after reload', async t => {
  const { page } = await start(t);
  await createCase(page);
  await upload(page, 'original.txt', 'Original evidence');
  const popup = await openedFile(page);
  await expect(popup.locator('body')).toContainText('Original evidence');
  await popup.close();
  await page.reload();
  await expect(page.getByText('original.txt', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Открыть', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Предпросмотр документа' });
  await expect(dialog.getByText('Оригинальный файл недоступен.', { exact: false })).toBeVisible();
});

test('signal decisions and histories persist without changing evidence or interpreting comments as HTML', async t => {
  const { page } = await start(t);
  await openOriginal(page);
  await openSignal(page);
  const signal = original.signals[0];
  for (const value of [signal.expected, signal.actual, signal.rule, signal.explanation]) {
    await expect(page.getByText(value, { exact: true })).toBeVisible();
  }
  const comment = 'Сверено с актом <img src=x onerror=alert(1)>';
  await page.getByLabel('Статус', { exact: true }).selectOption('Подтверждено специалистом');
  await page.getByLabel('Комментарий специалиста').fill(`  ${comment}  `);
  await page.getByRole('button', { name: 'Сохранить решение' }).click();
  await expect(page.getByText(`Комментарий: ${comment}`, { exact: true })).toBeVisible();
  await expect(page.locator('img[src="x"]')).toHaveCount(0);
  let state = await snapshot(page);
  let saved = state.cases.find(item => item.id === state.selected);
  assert.equal(saved.signals[0].status, 'Подтверждено специалистом');
  assert.equal(saved.signals[0].comments.length, 1);
  assert.equal(saved.signals[0].comments[0].text, comment);
  assert.equal(saved.signals[0].history.length, 2);
  assert.equal(saved.history.length, 2);
  assert.deepEqual(
    [saved.signals[0].expected, saved.signals[0].actual, saved.signals[0].rule],
    [signal.expected, signal.actual, signal.rule],
  );
  await page.reload();
  await expect(page.getByLabel('Статус', { exact: true })).toHaveValue('Подтверждено специалистом');
  await expect(page.getByText(`Комментарий: ${comment}`, { exact: true })).toBeVisible();
  for (const value of [signal.expected, signal.actual, signal.rule]) {
    await expect(page.getByText(value, { exact: true })).toBeVisible();
  }
  state = await snapshot(page);
  saved = state.cases.find(item => item.id === state.selected);
  assert.equal(saved.signals[0].comments[0].text, comment);
  await page.getByRole('button', { name: '← К результатам' }).click();
  await openTab(page, 'История действий');
  await expect(page.getByText('Сигнал SIG-001: Подтверждено специалистом')).toBeVisible();
  await expect(page.getByText('Добавлен комментарий к SIG-001')).toBeVisible();
});

test('registry and signal filters combine search and status', async t => {
  const { page } = await start(t);
  await page.getByLabel('Поиск по контракту или названию').fill('0142');
  await page.getByLabel('Статус проверки').selectOption('Требует рассмотрения');
  await expect(page.getByRole('row').filter({ hasText: original.number })).toBeVisible();
  await page.getByLabel('Поиск по контракту или названию').fill('несуществующий');
  await expect(page.getByText('Проверок пока нет')).toBeVisible();
  await page.getByLabel('Поиск по контракту или названию').fill('');
  await openOriginal(page);
  await openTab(page, 'Результаты');
  await page.getByLabel('Поиск по позиции или типу').fill('скамейка');
  await page.getByLabel('Статус сигнала').selectOption('Требует проверки');
  await expect(page.getByRole('row').filter({ hasText: 'SIG-001' })).toBeVisible();
  await page.getByLabel('Статус сигнала').selectOption('Не подтверждено');
  await expect(page.getByText('Нет сигналов для отображения')).toBeVisible();
  await expect(page.getByRole('row').filter({ hasText: 'SIG-001' })).toHaveCount(0);
});

test('completion confirms unresolved signals and the searchable archive excludes unfinished cases', async t => {
  const { page } = await start(t);
  await openOriginal(page);
  await openTab(page, 'Результаты');
  const finish = page.getByRole('button', { name: 'Завершить рассмотрение' });
  const cancelled = await withDialog(page, () => finish.click(), false);
  assert.equal(cancelled.type, 'confirm');
  assert.match(cancelled.message, /сигналы без итогового решения/i);
  let state = await snapshot(page);
  assert.equal(state.cases.find(item => item.id === state.selected).status, 'Требует рассмотрения');
  const confirmed = await withDialog(page, () => finish.click());
  assert.equal(confirmed.type, 'confirm');
  state = await snapshot(page);
  assert.equal(state.cases.find(item => item.id === state.selected).status, 'Завершена');
  await page.getByRole('button', { name: '← К списку' }).click();
  await createCase(page, 'UNFINISHED', 'Незавершённое дело');
  await page.getByRole('button', { name: 'История проверок', exact: true }).click();
  await expect(page.getByRole('row').filter({ hasText: original.number })).toBeVisible();
  await expect(page.getByText('Незавершённое дело', { exact: true })).toHaveCount(0);
  await page.getByLabel('Поиск по контракту или названию').fill('UNFINISHED');
  await expect(page.getByText('Проверок пока нет')).toBeVisible();
  await page.getByLabel('Поиск по контракту или названию').fill('');
  await page.getByLabel('Статус проверки').selectOption('Завершена');
  await expect(page.getByRole('row').filter({ hasText: original.number })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'История проверок' })).toBeVisible();
  await expect(page.getByRole('row').filter({ hasText: original.number })).toBeVisible();
  await expect(page.getByText('Незавершённое дело', { exact: true })).toHaveCount(0);
});

test('question export downloads the selected case and signal explanation', async t => {
  const { page } = await start(t);
  await openOriginal(page);
  await openTab(page, 'Результаты');
  const pending = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Экспорт перечня вопросов' }).click();
  const download = await pending;
  assert.equal(download.suggestedFilename(), 'questions-DEMO-001.txt');
  const stream = await download.createReadStream();
  assert.ok(stream);
  let content = '';
  for await (const chunk of stream) content += chunk.toString('utf8');
  for (const value of [original.number, 'SIG-001', 'Несоответствие количества', 'Требует проверки', 'Это сигнал для проверки']) {
    assert.ok(content.includes(value), value);
  }
  assert.doesNotMatch(content, /Демонстрационные данные/);
});

test('invalid stored JSON recovers the supplied case without external network requests', async t => {
  const { page, externalRequests } = await start(t, { stored: 'not valid JSON', blockExternal: true });
  await expect(page.getByRole('heading', { name: 'Проверки контрактов' })).toBeVisible();
  await expect(page.getByRole('row').filter({ hasText: original.number })).toBeVisible();
  assert.deepEqual(externalRequests, []);
});

test('blocked browser storage leaves the current session usable and explains the persistence limit', async t => {
  const { page } = await start(t, { blockedStorage: true });
  await expect(page.getByRole('row').filter({ hasText: original.number })).toBeVisible();
  await createCase(page, 'LOCAL-ONLY', 'Дело в открытой вкладке');
  await expect(page.getByRole('heading', { name: 'Дело в открытой вкладке' })).toBeVisible();
  const notice = page.getByRole('status');
  await expect(notice).toContainText('Хранилище браузера недоступно');
  await expect(notice).toContainText('только в открытой вкладке');
  await openTab(page, 'Обзор');
  await expect(page.getByLabel('Номер', { exact: true })).toHaveValue('LOCAL-ONLY');
  await expect(page.getByLabel('Наименование', { exact: true })).toHaveValue('Дело в открытой вкладке');
});
