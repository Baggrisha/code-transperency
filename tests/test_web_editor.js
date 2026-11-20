/* User-facing editor regressions against the built React app in Chromium. */
const nodeTest = require('node:test');
const test = (name, fn) => nodeTest(name, {timeout:60000}, fn);
test.before = nodeTest.before;
test.after = nodeTest.after;
const assert = require('node:assert/strict');
const {expect, http, scenario, startBrowser} = require('../scripts/check_web');

let runtime;
test.before(async () => { runtime = await startBrowser(); });
test.after(async () => { if (runtime) await runtime.close(); });

function documentFixture() {
  return {
    id:'doc-1', type:'delivery', number:'N-1', contract_id:'C-1', date:'2026-10-20',
    effective_from:null, deadline:null, revision:1, logical_id:null, status:'active',
    supersedes:null, specification_id:null, base_document_id:null, replacement_spec_id:null,
    approved:false, related_delivery_ids:[], acceptance_mode:'incremental', items:[],
    is_complete:false, missing_fields:['items','pages'], expected_pages:2, actual_pages:1,
    content_hash:'hash', filename:'original.pdf', extraction_method:'ocr',
    source:{document_id:'doc-1',filename:'original.pdf',page:1,row:null,field:null,excerpt:'Исходник <script>alert(1)</script>'},
    extraction_warnings:['TABLE_NOT_RECOGNIZED','PAGE_COUNT_MISMATCH'],
    fields_confidence:{number:0.4,date:0.6},
  };
}
function item(id, changes = {}) {
  return {line_id:id,position_id:'P-'+id,sku:null,name:'Товар '+id,unit:'шт',quantity:'2',unit_price:'100',amount:'200',currency:'RUB',tax_included:true,vat_rate:'20',discount_percent:'0',conversion_factor:null,conversion_to:null,attributes:{},confidence:{position_id:0.3,amount:0.4},source:{document_id:'doc-1',filename:'original.pdf',page:1,row:2,field:null,excerpt:'исходная строка'},field_sources:{amount:{document_id:'doc-1',filename:'original.pdf',page:1,row:2,field:'amount',excerpt:'200'}},...changes};
}
const labels = {
  number:'Номер', date:'Дата', deadline:'Срок поставки', line_id:'ID строки',
  position_id:'Номер позиции', sku:'Артикул', name:'Наименование', currency:'Валюта',
  amount:'Сумма', discount_percent:'Скидка, %', conversion_factor:'Коэффициент перевода',
  conversion_to:'Единица перевода', attributes:'Характеристики (JSON: название и значение)',
};
const escapeRegExp = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const field = (owner, key) => owner.getByLabel(new RegExp(`^${escapeRegExp(labels[key])}(?: Уверенность:.*)?$`));
const row = (editor, id) => editor.getByRole('group', {name:`Строка ${id}`, exact:true});
const confirmField = (owner, key, identity = 'документ') =>
  owner.getByRole('checkbox', {name:`Подтвердить по оригиналу: ${labels[key]}, ${identity}`, exact:true});

async function setup(t, doc, {putError, reloadError} = {}) {
  const dossier = {id:'case-1',title:'Комплект',revision:2,documents:[{id:doc.id,revision:3,document:doc}],runs:[]};
  let saves = 0;
  const app = await scenario(runtime, {
    'GET /api/demo': [],
    'GET /api/cases': [],
    'GET /api/cases/case-1': () => reloadError && saves ? http(503, {detail:reloadError}) : dossier,
    'PUT /api/cases/case-1/documents/doc-1': () => {
      saves++;
      return putError ? http(422, {detail:putError}) : {};
    },
  });
  t.after(() => app.close());
  app.page.on('dialog', dialog => dialog.type() === 'confirm' ? dialog.accept() : dialog.dismiss());
  await app.page.goto('/#case/case-1/recognition');
  await app.page.getByRole('button', {name:'Реквизиты', exact:true}).click();
  app.editor = app.page.getByRole('dialog', {name:'Редактирование документа'});
  await expect(app.editor).toBeVisible();
  app.saves = () => app.requests.filter(request => request.key === 'PUT /api/cases/case-1/documents/doc-1').map(request => JSON.parse(request.body));
  return app;
}
async function reviewer(editor) {
  await editor.getByLabel('Проверяющий', {exact:true}).fill('Анна');
  await editor.getByLabel('Комментарий к исправлению').fill('Сверено с оригиналом');
}
async function submit(app, count = 1) {
  await app.editor.getByRole('button', {name:'Сохранить исправления'}).click();
  await expect.poll(() => app.saves().length).toBe(count);
  return app.saves()[count - 1];
}

test('unrecognized table: add a row and confirm only explicitly reviewed fields', async t => {
  const doc = documentFixture(), app = await setup(t, doc), {editor} = app;
  await editor.getByRole('button', {name:'Добавить строку'}).click();
  const added = row(editor, 'manual-1');
  await field(added, 'position_id').fill('К-12');
  await field(added, 'name').fill('Насос');
  await field(added, 'currency').fill('RUB');
  await field(added, 'amount').fill('1250,00');
  await confirmField(added, 'position_id', 'строка manual-1').check();
  await confirmField(added, 'currency', 'строка manual-1').check();
  await confirmField(editor, 'date').check();
  await reviewer(editor);
  const payload = await submit(app), saved = payload.document.items[0];
  assert.equal(saved.line_id, 'manual-1');
  assert.equal(saved.position_id, 'К-12');
  assert.equal(saved.amount, '1250,00');
  assert.equal(saved.confidence.position_id, 1);
  assert.equal(saved.confidence.currency, 1);
  assert.equal(saved.confidence.amount, 0);
  assert.equal(saved.confidence.name, 0);
  assert.equal(saved.confidence.discount_percent, 0);
  assert.deepEqual(payload.confirmed_items[saved.line_id].sort(), ['currency','position_id']);
  assert.deepEqual(payload.confirmed_fields, ['date']);
  assert.equal(payload.document.fields_confidence.number, 0.4);
  assert.equal(payload.document.fields_confidence.date, 1);
  assert.equal(payload.document.fields_confidence.items, 0);
  assert.deepEqual(payload.document.missing_fields, ['items','pages']);
  assert.deepEqual(payload.document.extraction_warnings, doc.extraction_warnings);
  assert.equal(payload.expected_revision, 3);
  assert.equal(payload.reviewer, 'Анна');
  assert.equal(payload.comment, 'Сверено с оригиналом');
});

test('deleting a row and editing another preserves identity, provenance and unreviewed confidence', async t => {
  const doc = documentFixture(); doc.items = [item('old'),item('keep')];
  const app = await setup(t, doc), {editor} = app;
  await row(editor,'old').getByRole('button', {name:'Удалить строку old'}).click();
  const keep = row(editor,'keep');
  await field(keep,'amount').fill('250');
  await field(keep,'discount_percent').fill('5');
  await confirmField(keep,'discount_percent','строка keep').check();
  await editor.getByLabel('Пропуск устранён: items').check();
  await editor.getByLabel('Замечание разобрано: TABLE_NOT_RECOGNIZED').check();
  await reviewer(editor);
  const payload = await submit(app), saved = payload.document.items[0];
  assert.deepEqual(payload.document.items.map(value => value.line_id), ['keep']);
  assert.equal(saved.amount, '250');
  assert.equal(saved.confidence.amount, 0);
  assert.equal(saved.confidence.position_id, 0.3);
  assert.equal(saved.confidence.discount_percent, 1);
  assert.deepEqual(saved.source, doc.items[1].source);
  assert.deepEqual(saved.field_sources, doc.items[1].field_sources);
  assert.deepEqual(payload.confirmed_items, {keep:['discount_percent']});
  assert.deepEqual(payload.document.missing_fields, ['pages']);
  assert.deepEqual(payload.document.extraction_warnings, ['PAGE_COUNT_MISMATCH']);
  assert.deepEqual(payload.document.source, doc.source);
});

test('source and field text cannot inject markup; invalid attributes remain editable', async t => {
  const doc = documentFixture(), badId = 'unsafe" autofocus onfocus="alert(1)';
  doc.items = [item(badId, {name:'<img src=x onerror=alert(1)>',attributes:{'" onfocus="alert(1)':'<script>'}})];
  doc.fields_confidence['<img src=x onerror=alert(2)>'] = 0.1;
  const app = await setup(t, doc), {editor} = app, entry = row(editor,badId);
  await editor.getByText('Фрагменты источника', {exact:true}).first().click();
  await expect(editor.getByText('Исходник <script>alert(1)</script>')).toBeVisible();
  await expect(field(entry,'name')).toHaveValue('<img src=x onerror=alert(1)>');
  await expect(editor.locator('img, script, [autofocus]')).toHaveCount(0);
  await field(entry,'attributes').fill('{"bad": 42}');
  await reviewer(editor);
  await editor.getByRole('button', {name:'Сохранить исправления'}).click();
  await expect(app.page.locator('#toast')).toContainText(/характеристик/i);
  assert.equal(app.saves().length, 0);
  await expect(editor).toBeVisible();
  await field(entry,'attributes').fill('{"цвет": "белый"}');
  await confirmField(entry,'attributes',`строка ${badId}`).check();
  const payload = await submit(app);
  assert.deepEqual(payload.document.items[0].attributes, {'цвет':'белый'});
  assert.deepEqual(payload.confirmed_items[badId], ['attributes']);
});

test('only explicitly discarded unknown confidence keys are removed', async t => {
  const doc = documentFixture();
  doc.fields_confidence.unknown_doc = 0.2;
  doc.fields_confidence.filename = 0.3;
  doc.items = [item('1',{confidence:{position_id:0.3,source:0.2,unknown_item:0.4,untouched:0.1}})];
  const app = await setup(t, doc), {editor} = app, entry = row(editor,'1');
  await expect(editor).toContainText('оценки ниже порога продолжат блокировать сверку');
  await editor.getByLabel('Удалить ошибочную оценку «unknown_doc»').check();
  await entry.getByLabel('Удалить ошибочную оценку «unknown_item»').check();
  await reviewer(editor);
  const payload = await submit(app);
  assert.deepEqual(payload.discarded_confidence_fields, ['unknown_doc']);
  assert.deepEqual(payload.discarded_item_confidence, {'1':['unknown_item']});
  assert.equal(Object.hasOwn(payload.document.fields_confidence,'unknown_doc'), false);
  assert.equal(Object.hasOwn(payload.document.items[0].confidence,'unknown_item'), false);
  assert.equal(payload.document.fields_confidence.filename, 0.3);
  assert.equal(payload.document.items[0].confidence.source, 0.2);
  assert.equal(payload.document.items[0].confidence.untouched, 0.1);
  assert.equal(payload.document.items[0].confidence.position_id, 0.3);
  assert.deepEqual(payload.confirmed_fields, []);
  assert.deepEqual(payload.confirmed_items, {});
  assert.deepEqual(payload.document.source, doc.source);
  assert.deepEqual(payload.document.items[0].source, doc.items[0].source);
});

test('unusual confidence keys and row identifiers can be discarded safely', async t => {
  const doc = documentFixture();
  doc.fields_confidence = JSON.parse('{"__proto__":0.2}');
  doc.items = [item('__proto__',{confidence:JSON.parse('{"constructor":0.3}')})];
  const app = await setup(t, doc), {editor} = app, entry = row(editor,'__proto__');
  const docDiscard = editor.getByLabel('Удалить ошибочную оценку «__proto__»');
  const itemDiscard = entry.getByLabel('Удалить ошибочную оценку «constructor»');
  await expect(docDiscard).not.toBeChecked();
  await expect(itemDiscard).not.toBeChecked();
  await docDiscard.check();
  await itemDiscard.check();
  await reviewer(editor);
  const payload = await submit(app);
  assert.deepEqual(payload.discarded_confidence_fields, ['__proto__']);
  assert.deepEqual(payload.discarded_item_confidence['__proto__'], ['constructor']);
  assert.equal(Object.hasOwn(payload.document.fields_confidence,'__proto__'), false);
  assert.equal(Object.hasOwn(payload.document.items[0].confidence,'constructor'), false);
  assert.deepEqual(payload.confirmed_fields, []);
  assert.deepEqual(payload.confirmed_items, {});
});

test('failed save keeps the editor and entered values available to retry', async t => {
  const app = await setup(t, documentFixture(), {putError:'Документ уже изменён. Обновите данные.'});
  await field(app.editor,'number').fill('N-2');
  await reviewer(app.editor);
  const payload = await submit(app);
  assert.equal(payload.document.fields_confidence.number, 0);
  await expect(app.editor).toBeVisible();
  await expect(field(app.editor,'number')).toHaveValue('N-2');
  await expect(app.page.locator('#toast')).toContainText('уже изменён');
  await expect(app.editor.getByRole('button', {name:'Сохранить исправления'})).toBeEnabled();
});

test('validation errors identify the affected row and field and leave the form editable', async t => {
  const doc = documentFixture(); doc.items = [item('1')];
  const app = await setup(t, doc, {putError:[{loc:['body','document','items',0,'amount'],msg:'Неверное число'}]});
  await reviewer(app.editor);
  await submit(app);
  await expect(app.page.locator('#toast')).toContainText('Строка 1: Сумма: Неверное число');
  await expect(app.editor).toBeVisible();
  await expect(field(row(app.editor,'1'),'amount')).toBeEditable();
});

test('a saved document is not reported as failed when only the refresh fails', async t => {
  const app = await setup(t, documentFixture(), {reloadError:'Нет связи'});
  await reviewer(app.editor);
  await submit(app);
  await expect(app.editor).toBeHidden();
  await expect(app.page.locator('#toast')).toContainText(/сохранено.*не удалось обновить/i);
});

test('unchanged documents retain their data and confidence levels', async t => {
  const doc = documentFixture(); doc.items = [item('existing')];
  doc.base_document_id = 'older-document-not-in-the-current-list';
  doc.related_delivery_ids = ['delivery,with-comma']; doc.revision = 4;
  const app = await setup(t, doc);
  await reviewer(app.editor);
  const payload = await submit(app);
  assert.deepEqual(payload.document, doc);
  assert.deepEqual(payload.confirmed_fields, []);
  assert.deepEqual(payload.confirmed_items, {});
});

test('adding several rows preserves unsaved edits to existing and new rows', async t => {
  const doc = documentFixture(); doc.items = [item('existing')];
  const app = await setup(t, doc), {editor} = app;
  await field(row(editor,'existing'),'sku').fill('EDITED');
  await editor.getByRole('button', {name:'Добавить строку'}).click();
  await field(row(editor,'manual-1'),'name').fill('Первая новая строка');
  await editor.getByRole('button', {name:'Добавить строку'}).click();
  await expect(field(row(editor,'existing'),'sku')).toHaveValue('EDITED');
  await expect(field(row(editor,'manual-1'),'name')).toHaveValue('Первая новая строка');
  await reviewer(editor);
  const items = (await submit(app)).document.items;
  assert.deepEqual(items.map(value => value.line_id), ['existing','manual-1','manual-2']);
  assert.equal(items[0].sku, 'EDITED');
  assert.equal(items[1].name, 'Первая новая строка');
  assert.deepEqual(items[0].source, doc.items[0].source);
  assert.equal(items[0].confidence.sku, 0);
  assert.equal(items[1].confidence.name, 0);
});

test('row identity and complete composition can be explicitly confirmed unchanged', async t => {
  const doc = documentFixture(); doc.items = [item('__proto__',{confidence:{line_id:0.2}})];
  doc.fields_confidence.items = 0.3;
  const app = await setup(t, doc), {editor} = app;
  await confirmField(row(editor,'__proto__'),'line_id','строка __proto__').check();
  await editor.getByLabel(/Весь состав товарных строк проверен по оригиналу/).check();
  await reviewer(editor);
  const payload = await submit(app);
  assert.deepEqual(payload.confirmed_items['__proto__'], ['line_id']);
  assert.deepEqual(payload.confirmed_fields, ['items']);
  assert.equal(payload.document.items[0].line_id, '__proto__');
  assert.equal(payload.document.items[0].confidence.line_id, 1);
  assert.equal(payload.document.fields_confidence.items, 1);
});

test('a newly added row does not reuse an identity removed in the same edit', async t => {
  const doc = documentFixture(); doc.items = [item('manual-1')];
  const app = await setup(t, doc);
  await row(app.editor,'manual-1').getByRole('button', {name:'Удалить строку manual-1'}).click();
  await app.editor.getByRole('button', {name:'Добавить строку'}).click();
  await reviewer(app.editor);
  const payload = await submit(app);
  assert.equal(payload.document.items[0].line_id, 'manual-2');
  assert.equal(payload.document.fields_confidence.items, 0);
});

test('changing a checked value or composition requires a fresh confirmation', async t => {
  const doc = documentFixture(); doc.items = [item('1')];
  const app = await setup(t, doc), {editor} = app, entry = row(editor,'1');
  const amount = confirmField(entry,'amount','строка 1');
  await amount.check(); await field(entry,'amount').fill('300'); await expect(amount).not.toBeChecked();
  const number = confirmField(editor,'number');
  await number.check(); await field(editor,'number').fill('N-2'); await expect(number).not.toBeChecked();
  const missing = editor.getByLabel(/Проверен список оставшихся пропусков/);
  await missing.check(); await editor.getByLabel('Пропуск устранён: items').check(); await expect(missing).not.toBeChecked();
  const composition = editor.getByLabel(/Весь состав товарных строк проверен по оригиналу/);
  await composition.check(); await editor.getByRole('button', {name:'Добавить строку'}).click(); await expect(composition).not.toBeChecked();
  await reviewer(editor);
  const payload = await submit(app);
  assert.equal(payload.document.items[0].confidence.amount, 0);
  assert.equal(payload.document.fields_confidence.number, 0);
  assert.equal(payload.document.fields_confidence.missing_fields, 0);
  assert.equal(payload.document.fields_confidence.items, 0);
  assert.deepEqual(payload.confirmed_fields, []);
});

test('an empty discount cannot be confirmed as zero', async t => {
  const app = await setup(t, documentFixture()), {editor} = app;
  await editor.getByRole('button', {name:'Добавить строку'}).click();
  const entry = row(editor,'manual-1');
  await field(entry,'discount_percent').fill('');
  const discount = confirmField(entry,'discount_percent','строка manual-1');
  await discount.check(); await reviewer(editor);
  await editor.getByRole('button', {name:'Сохранить исправления'}).click();
  await expect(app.page.locator('#toast')).toContainText(/скидку/i);
  assert.equal(app.saves().length, 0);
  await discount.uncheck();
  const saved = (await submit(app)).document.items[0];
  assert.equal(saved.discount_percent, '0');
  assert.equal(saved.confidence.discount_percent, 0);
});

test('financial, matching and conversion fields are reviewed independently', async t => {
  const doc = documentFixture(); doc.items = [item('1',{confidence:{position_id:0.2,currency:0.3,amount:0.4,discount_percent:0.5}})];
  const app = await setup(t, doc), {editor} = app, entry = row(editor,'1');
  for (const [key, value] of Object.entries({position_id:'К-2',currency:'EUR',amount:'190,50',discount_percent:'5',conversion_factor:'1000',conversion_to:'г'})) await field(entry,key).fill(value);
  for (const key of ['position_id','amount','conversion_factor','conversion_to']) await confirmField(entry,key,'строка 1').check();
  await field(editor,'deadline').fill('2026-11-01');
  await confirmField(editor,'deadline').check();
  await reviewer(editor);
  const payload = await submit(app), saved = payload.document.items[0];
  assert.equal(saved.position_id, 'К-2'); assert.equal(saved.amount, '190,50');
  assert.equal(saved.currency, 'EUR'); assert.equal(saved.discount_percent, '5');
  assert.equal(saved.conversion_factor, '1000'); assert.equal(saved.conversion_to, 'г');
  assert.equal(saved.confidence.currency, 0); assert.equal(saved.confidence.discount_percent, 0);
  assert.equal(saved.confidence.amount, 1);
  assert.deepEqual(payload.confirmed_items['1'].sort(), ['amount','conversion_factor','conversion_to','position_id']);
  assert.equal(payload.document.deadline, '2026-11-01');
  assert.equal(payload.document.fields_confidence.deadline, 1);
  assert.deepEqual(payload.confirmed_fields, ['deadline']);
});
