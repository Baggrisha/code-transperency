/* Behavioral tests for the shipped editor script using a minimal Node DOM; not a browser test. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const decode = value => value.replace(/&(amp|lt|gt|quot|#39);/g, (_, name) => ({amp:'&',lt:'<',gt:'>',quot:'"','#39':"'"})[name]);

function controls(markup) {
  return [...markup.matchAll(/<input\b[^>]*>|<select\b[^>]*>[\s\S]*?<\/select>|<textarea\b[^>]*>[\s\S]*?<\/textarea>/g)].map(([tag]) => {
    const open = tag.slice(0, tag.indexOf('>') + 1);
    const attrs = Object.fromEntries([...open.matchAll(/([\w-]+)(?:="([^"]*)")?/g)].slice(1).map(([, key, value]) => [key, value === undefined ? true : decode(value)]));
    let value = attrs.value === undefined ? '' : attrs.value;
    if (tag.startsWith('<textarea')) value = decode(tag.slice(open.length, tag.lastIndexOf('</textarea>')));
    if (tag.startsWith('<select')) {
      const options = [...tag.matchAll(/<option\b([^>]*)>([\s\S]*?)<\/option>/g)];
      const chosen = options.find(([, option]) => /\bselected\b/.test(option)) || options[0];
      const match = chosen && chosen[1].match(/value="([^"]*)"/);
      value = match ? decode(match[1]) : '';
    }
    const dataset = {};
    for (const [key, val] of Object.entries(attrs)) if (key.startsWith('data-')) dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = val;
    return {dataset, name:attrs.name, value, checked:!!attrs.checked, disabled:!!attrs.disabled};
  });
}
function selected(nodes, selector) {
  const match = selector.match(/^\[data-([\w-]+)(?:="([^"]*)")?\](:checked)?$/);
  if (!match) throw new Error(`Unsupported test selector: ${selector}`);
  const key = match[1].replace(/-([a-z])/g, (_, c) => c.toUpperCase());
  return nodes.filter(node => Object.hasOwn(node.dataset, key) && (match[2] === undefined || node.dataset[key] === match[2]) && (!match[3] || node.checked));
}
function editorForm(markup) {
  const rows = [];
  const form = {
    elements: {},
    nodes: controls(markup),
    querySelectorAll(selector) {
      if (selector === '[data-item-row]') return rows;
      return selected(this.nodes, selector);
    },
    querySelector(selector) {
      if (selector === '[data-items]') return container;
      if (selector === '[type="submit"]') return this.submitButton;
      if (selector === '[data-editor-action="add"]') return this.addButton;
      return this.querySelectorAll(selector)[0] || null;
    },
    submitButton: {disabled:false},
    addButton: {focus(){}},
    click(action, row) {
      const button = {dataset:{editorAction:action}, closest(selector) { return selector === '[data-editor-action]' ? this : row; }};
      return this.onclick({target:button});
    },
    input(control) { return this.oninput({target:control}); },
    change(control) { return this.onchange({target:control}); },
    async submit() { return this.onsubmit({target:this, preventDefault(){}}); },
  };
  for (const node of form.nodes) if (node.name) form.elements[node.name] = node;
  const container = {insertAdjacentHTML(_where, content) { addRows(content); }};
  function addRows(content) {
    for (const match of content.matchAll(/<fieldset\b[^>]*data-item-row="([^"]*)"[^>]*>[\s\S]*?<\/fieldset>/g)) {
      const row = {
        dataset:{itemRow:decode(match[1])},
        nodes:controls(match[0]),
        querySelectorAll(selector) { return selected(this.nodes, selector); },
        querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
        remove() { rows.splice(rows.indexOf(this), 1); },
      };
      for (const node of row.nodes) node.closest=() => row;
      rows.push(row);
    }
  }
  addRows(markup);
  return form;
}
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
  return {line_id:id,position_id:'P-'+id,sku:null,name:'Товар '+id,unit:'шт',quantity:'2',unit_price:'100',amount:'200',currency:'RUB',tax_included:true,vat_rate:'20',discount_percent:'0',conversion_factor:null,conversion_to:null,attributes:{},confidence:{position_id:0.3,amount:0.4},source:{document_id:'doc-1',filename:'original.pdf',page:1,row:2,field:null,excerpt:'исходная строка'},field_sources:{amount:{document_id:'doc-1',filename:'original.pdf',page:1,row:2,field:'amount',excerpt:'200'}} ,...changes};
}
async function setup(doc, putError, reloadError) {
  const dossier = {id:'case-1',title:'Комплект',revision:2,documents:[{id:doc.id,revision:3,document:doc}],runs:[]};
  const requests = [];
  const elements = new Map();
  let form;
  function element(selector) {
    if (selector === '#document-form') return form;
    if (!elements.has(selector)) {
      const node = {textContent:'',style:{},value:'',open:false,attributes:{},
        setAttribute(name,value){this.attributes[name]=String(value);},focus(){},
        showModal(){this.open=true;},close(){this.open=false;}};
      Object.defineProperty(node, 'innerHTML', {get(){return this._html || '';},set(value){this._html=value;if (selector === '#editor') form=editorForm(value);}});
      elements.set(selector,node);
    }
    return elements.get(selector);
  }
  const context = vm.createContext({
    console, Date, encodeURIComponent, structuredClone, setTimeout(){return 0;}, clearTimeout(){},
    FormData:class FormData {}, confirm:()=>true,
    sessionStorage:{getItem(){return null;},setItem(){}},
    document:{querySelector:element,addEventListener(){}},
    fetch:async (url, options={}) => {
      if (options.method === 'PUT') {
        requests.push(JSON.parse(options.body));
        if (putError) return {ok:false,json:async()=>({detail:putError})};
        return {ok:true,json:async()=>({})};
      }
      if (reloadError && requests.length && url === '/api/cases/case-1') return {ok:false,json:async()=>({detail:reloadError})};
      const value = url === '/api/demo' ? [] : url === '/api/cases' ? [] : url === '/api/cases/case-1' ? dossier : null;
      assert.notEqual(value, null, `Unexpected request: ${url}`);
      return {ok:true,json:async()=>structuredClone(value)};
    },
  });
  await vm.runInContext(script, context);
  await vm.runInContext('openCase("case-1")', context);
  vm.runInContext('editDocument("doc-1")', context);
  return {element, requests, form:() => form};
}
const field = (owner, name) => owner.querySelector(`[data-${owner.dataset ? 'item' : 'doc'}-field="${name}"]`);
const confirmField = (owner, name) => { owner.querySelector(`[data-${owner.dataset ? 'item' : 'doc'}-confirm="${name}"]`).checked = true; };
function reviewer(form) { form.elements.reviewer.value='Анна';form.elements.comment.value='Сверено с оригиналом'; }

test('unrecognized table: add a row and confirm only the fields explicitly reviewed', async () => {
  const doc = documentFixture();
  const app = await setup(doc);
  const form = app.form();
  form.click('add');
  const [row] = form.querySelectorAll('[data-item-row]');
  assert.ok(row, 'The add action creates an editable row');
  field(row,'position_id').value='К-12';
  field(row,'name').value='Насос';
  field(row,'currency').value='RUB';
  field(row,'amount').value='1250,00';
  confirmField(row,'position_id');
  confirmField(row,'currency');
  confirmField(form,'date');
  reviewer(form);
  await form.submit();
  assert.equal(app.requests.length,1);
  const payload = app.requests[0];
  const added = payload.document.items[0];
  assert.match(added.line_id,/^manual-\d+$/);
  assert.equal(added.position_id,'К-12');
  assert.equal(added.amount,'1250,00');
  assert.equal(added.confidence.position_id,1);
  assert.equal(added.confidence.currency,1);
  assert.equal(added.confidence.amount,0);
  assert.equal(added.confidence.name,0);
  assert.equal(added.confidence.discount_percent,0, 'Default zero discount is not a confirmed fact');
  assert.deepEqual(payload.confirmed_items[added.line_id].sort(),['currency','position_id']);
  assert.deepEqual(payload.confirmed_fields,['date']);
  assert.equal(payload.document.fields_confidence.number,0.4);
  assert.equal(payload.document.fields_confidence.date,1);
  assert.equal(payload.document.fields_confidence.items,0);
  assert.deepEqual(payload.document.missing_fields,['items','pages']);
  assert.deepEqual(payload.document.extraction_warnings,doc.extraction_warnings);
  assert.equal(payload.expected_revision,3);
  assert.equal(payload.reviewer,'Анна');
  assert.equal(payload.comment,'Сверено с оригиналом');
});

test('delete one row and edit another without losing its identity, provenance, or unreviewed confidence', async () => {
  const doc = documentFixture();
  doc.items=[item('old'),item('keep')];
  const app = await setup(doc);
  const form = app.form();
  const [old, keep] = form.querySelectorAll('[data-item-row]');
  form.click('remove',old);
  field(keep,'amount').value='250';
  field(keep,'discount_percent').value='5';
  confirmField(keep,'discount_percent');
  form.querySelector('[data-resolve-missing="0"]').checked=true;
  form.querySelector('[data-resolve-warning="0"]').checked=true;
  reviewer(form);
  await form.submit();
  const payload = app.requests[0];
  assert.deepEqual(payload.document.items.map(x=>x.line_id),['keep']);
  const saved = payload.document.items[0];
  assert.equal(saved.amount,'250');
  assert.equal(saved.confidence.amount,0);
  assert.equal(saved.confidence.position_id,0.3);
  assert.equal(saved.confidence.discount_percent,1);
  assert.deepEqual(saved.source,doc.items[1].source);
  assert.deepEqual(saved.field_sources,doc.items[1].field_sources);
  assert.deepEqual(payload.confirmed_items,{keep:['discount_percent']});
  assert.deepEqual(payload.document.missing_fields,['pages']);
  assert.deepEqual(payload.document.extraction_warnings,['PAGE_COUNT_MISMATCH']);
  assert.deepEqual(payload.document.source,doc.source);
});

test('source snippets and field values are escaped and invalid attributes do not submit', async () => {
  const doc = documentFixture();
  const badId='unsafe" autofocus onfocus="alert(1)';
  doc.items=[item(badId, {name:'<img src=x onerror=alert(1)>', attributes:{'" onfocus="alert(1)':'<script>'}})];
  doc.fields_confidence['<img src=x onerror=alert(2)>']=0.1;
  const app = await setup(doc);
  const rendered = app.element('#editor').innerHTML;
  assert.ok(!rendered.includes('<img src=x'));
  assert.ok(!rendered.includes('<script>alert(1)</script>'));
  assert.match(rendered,/&lt;img/);
  assert.match(rendered,/&lt;script&gt;/);
  assert.ok(!rendered.includes('aria-label="Удалить строку unsafe" autofocus'));
  assert.match(rendered,/unsafe&quot; autofocus onfocus=&quot;alert/);
  const form = app.form();
  const [row] = form.querySelectorAll('[data-item-row]');
  field(row,'attributes').value='{"bad": 42}';
  reviewer(form);
  await form.submit();
  assert.equal(app.requests.length,0);
  assert.equal(app.element('#editor').open,true);
  assert.match(app.element('#toast').textContent,/характеристик/i);
  field(row,'attributes').value='{"цвет": "белый"}';
  confirmField(row,'attributes');
  await form.submit();
  assert.equal(app.requests.length,1);
  assert.deepEqual(app.requests[0].document.items[0].attributes,{'цвет':'белый'});
  assert.deepEqual(app.requests[0].confirmed_items[badId],['attributes']);
});

test('only explicitly discarded unknown confidence keys are removed, never service metadata or other fields', async () => {
  const doc=documentFixture();
  doc.fields_confidence.unknown_doc=0.2;
  doc.fields_confidence.filename=0.3;
  doc.items=[item('1',{confidence:{position_id:0.3,source:0.2,unknown_item:0.4,untouched:0.1}})];
  const app=await setup(doc), form=app.form(), [row]=form.querySelectorAll('[data-item-row]');
  assert.match(app.element('#editor').innerHTML,/оценки ниже порога продолжат блокировать сверку/);
  assert.equal(form.querySelectorAll('[data-doc-discard]').length,1);
  assert.equal(row.querySelectorAll('[data-item-discard]').length,2);
  form.querySelector('[data-doc-discard="2"]').checked=true;
  row.querySelector('[data-item-discard="2"]').checked=true;
  reviewer(form);
  await form.submit();
  const payload=app.requests[0];
  assert.deepEqual(payload.discarded_confidence_fields,['unknown_doc']);
  assert.deepEqual(payload.discarded_item_confidence,{'1':['unknown_item']});
  assert.equal(Object.hasOwn(payload.document.fields_confidence,'unknown_doc'),false);
  assert.equal(Object.hasOwn(payload.document.items[0].confidence,'unknown_item'),false);
  assert.equal(payload.document.fields_confidence.filename,0.3);
  assert.equal(payload.document.items[0].confidence.source,0.2);
  assert.equal(payload.document.items[0].confidence.untouched,0.1);
  assert.equal(payload.document.items[0].confidence.position_id,0.3);
  assert.deepEqual(payload.confirmed_fields,[]);
  assert.deepEqual(payload.confirmed_items,{});
  assert.deepEqual(payload.document.source,doc.source);
  assert.deepEqual(payload.document.items[0].source,doc.items[0].source);
});

test('discarding unusual confidence keys and row identifiers is safe', async () => {
  const doc=documentFixture();
  doc.fields_confidence=JSON.parse('{\"__proto__\":0.2}');
  doc.items=[item('__proto__',{confidence:JSON.parse('{\"constructor\":0.3}')})];
  const app=await setup(doc), form=app.form(), [row]=form.querySelectorAll('[data-item-row]');
  form.querySelector('[data-doc-discard=\"0\"]').checked=true;
  row.querySelector('[data-item-discard=\"0\"]').checked=true;
  reviewer(form);
  await form.submit();
  const payload=app.requests[0];
  assert.deepEqual(payload.discarded_confidence_fields,['__proto__']);
  assert.deepEqual(payload.discarded_item_confidence['__proto__'],['constructor']);
  assert.equal(Object.hasOwn(payload.document.fields_confidence,'__proto__'),false);
  assert.equal(Object.hasOwn(payload.document.items[0].confidence,'constructor'),false);
  assert.deepEqual(payload.confirmed_fields,[]);
  assert.deepEqual(payload.confirmed_items,{});
});

test('failed save keeps the editor open and the entered values available to retry', async () => {
  const app = await setup(documentFixture(),'Документ уже изменён. Обновите данные.');
  const form = app.form();
  field(form,'number').value='N-2';
  reviewer(form);
  await form.submit();
  assert.equal(app.requests.length,1);
  assert.equal(app.requests[0].document.fields_confidence.number,0);
  assert.equal(app.element('#editor').open,true);
  assert.equal(field(form,'number').value,'N-2');
  assert.match(app.element('#toast').textContent,/уже изменён/);
  assert.equal(form.submitButton.disabled,false);
});

test('validation errors name the affected row and field and leave the form editable', async () => {
  const doc=documentFixture();
  doc.items=[item('1')];
  const app=await setup(doc,[{loc:['body','document','items',0,'amount'],msg:'Неверное число'}]);
  const form=app.form();
  reviewer(form);
  await form.submit();
  assert.match(app.element('#toast').textContent,/Строка 1: Сумма: Неверное число/);
  assert.equal(app.element('#editor').open,true);
  assert.equal(form.inert,false);
});

test('a saved document is not reported as failed when only the refresh fails', async () => {
  const app = await setup(documentFixture(),null,'Нет связи');
  reviewer(app.form());
  await app.form().submit();
  assert.equal(app.requests.length,1);
  assert.equal(app.element('#editor').open,false);
  assert.match(app.element('#toast').textContent,/сохранено.*не удалось обновить/i);
});

test('unchanged documents retain their data and all confidence levels', async () => {
  const doc = documentFixture();
  doc.items=[item('existing')];
  doc.base_document_id='older-document-not-in-the-current-list';
  doc.related_delivery_ids=['delivery,with-comma'];
  doc.revision=4;
  const app = await setup(doc);
  reviewer(app.form());
  await app.form().submit();
  assert.deepEqual(app.requests[0].document,doc);
  assert.deepEqual(app.requests[0].confirmed_fields,[]);
  assert.deepEqual(app.requests[0].confirmed_items,{});
});

test('adding several rows preserves unsaved edits to existing and newly added rows', async () => {
  const doc=documentFixture();
  doc.items=[item('existing')];
  const app=await setup(doc), form=app.form();
  const first=form.querySelectorAll('[data-item-row]')[0];
  field(first,'sku').value='EDITED';
  form.click('add');
  const added=form.querySelectorAll('[data-item-row]')[1];
  field(added,'name').value='Первая новая строка';
  form.click('add');
  assert.equal(field(first,'sku').value,'EDITED');
  assert.equal(field(added,'name').value,'Первая новая строка');
  reviewer(form);
  await form.submit();
  const items=app.requests[0].document.items;
  assert.deepEqual(items.map(x=>x.line_id),['existing','manual-1','manual-2']);
  assert.equal(items[0].sku,'EDITED');
  assert.equal(items[1].name,'Первая новая строка');
  assert.deepEqual(items[0].source,doc.items[0].source);
  assert.equal(items[0].confidence.sku,0);
  assert.equal(items[1].confidence.name,0);
});

test('row identity and complete composition can be explicitly confirmed without editing them', async () => {
  const doc = documentFixture();
  doc.items=[item('__proto__',{confidence:{line_id:0.2}})];
  doc.fields_confidence.items=0.3;
  const app = await setup(doc);
  const form=app.form(), [row]=form.querySelectorAll('[data-item-row]');
  confirmField(row,'line_id');
  confirmField(form,'items');
  reviewer(form);
  await form.submit();
  const payload=app.requests[0];
  assert.deepEqual(payload.confirmed_items['__proto__'],['line_id']);
  assert.deepEqual(payload.confirmed_fields,['items']);
  assert.equal(payload.document.items[0].line_id,'__proto__');
  assert.equal(payload.document.items[0].confidence.line_id,1);
  assert.equal(payload.document.fields_confidence.items,1);
});

test('adding a row never reuses the identity of a row removed in the same edit', async () => {
  const doc = documentFixture();
  doc.items=[item('manual-1')];
  const app = await setup(doc);
  const form=app.form();
  form.click('remove',form.querySelectorAll('[data-item-row]')[0]);
  form.click('add');
  reviewer(form);
  await form.submit();
  assert.equal(app.requests[0].document.items[0].line_id,'manual-2');
  assert.equal(app.requests[0].document.fields_confidence.items,0);
});

test('editing a previously checked value or composition requires a fresh explicit confirmation', async () => {
  const doc=documentFixture();
  doc.items=[item('1')];
  const app=await setup(doc), form=app.form();
  const [row]=form.querySelectorAll('[data-item-row]');
  confirmField(row,'amount');
  field(row,'amount').value='300';
  form.input(field(row,'amount'));
  assert.equal(row.querySelector('[data-item-confirm="amount"]').checked,false);
  confirmField(form,'number');
  field(form,'number').value='N-2';
  form.input(field(form,'number'));
  assert.equal(form.querySelector('[data-doc-confirm="number"]').checked,false);
  confirmField(form,'missing_fields');
  const missing=form.querySelector('[data-resolve-missing="0"]');
  missing.checked=true;
  form.change(missing);
  assert.equal(form.querySelector('[data-doc-confirm="missing_fields"]').checked,false);
  confirmField(form,'items');
  form.click('add');
  assert.equal(form.querySelector('[data-doc-confirm="items"]').checked,false);
  reviewer(form);
  await form.submit();
  const payload=app.requests[0];
  assert.equal(payload.document.items[0].confidence.amount,0);
  assert.equal(payload.document.fields_confidence.number,0);
  assert.equal(payload.document.fields_confidence.missing_fields,0);
  assert.equal(payload.document.fields_confidence.items,0);
  assert.deepEqual(payload.confirmed_fields,[]);
});

test('an empty discount cannot be confirmed as zero', async () => {
  const app = await setup(documentFixture());
  const form=app.form();
  form.click('add');
  const [row]=form.querySelectorAll('[data-item-row]');
  field(row,'discount_percent').value='';
  confirmField(row,'discount_percent');
  reviewer(form);
  await form.submit();
  assert.equal(app.requests.length,0);
  assert.match(app.element('#toast').textContent,/скидку/i);
  row.querySelector('[data-item-confirm="discount_percent"]').checked=false;
  await form.submit();
  assert.equal(app.requests[0].document.items[0].discount_percent,'0');
  assert.equal(app.requests[0].document.items[0].confidence.discount_percent,0);
});

test('critical financial, matching and conversion fields are reviewed independently', async () => {
  const doc=documentFixture();
  doc.items=[item('1',{confidence:{position_id:0.2,currency:0.3,amount:0.4,discount_percent:0.5}})];
  const app=await setup(doc);
  const form=app.form(), [row]=form.querySelectorAll('[data-item-row]');
  field(row,'position_id').value='К-2';
  field(row,'currency').value='EUR';
  field(row,'amount').value='190,50';
  field(row,'discount_percent').value='5';
  field(row,'conversion_factor').value='1000';
  field(row,'conversion_to').value='г';
  confirmField(row,'position_id');
  confirmField(row,'amount');
  confirmField(row,'conversion_factor');
  confirmField(row,'conversion_to');
  field(form,'deadline').value='2026-11-01';
  confirmField(form,'deadline');
  reviewer(form);
  await form.submit();
  const payload=app.requests[0], saved=payload.document.items[0];
  assert.equal(saved.position_id,'К-2');
  assert.equal(saved.amount,'190,50');
  assert.equal(saved.currency,'EUR');
  assert.equal(saved.discount_percent,'5');
  assert.equal(saved.conversion_factor,'1000');
  assert.equal(saved.conversion_to,'г');
  assert.equal(saved.confidence.currency,0);
  assert.equal(saved.confidence.discount_percent,0);
  assert.equal(saved.confidence.amount,1);
  assert.deepEqual(payload.confirmed_items['1'].sort(),['amount','conversion_factor','conversion_to','position_id']);
  assert.equal(payload.document.deadline,'2026-11-01');
  assert.equal(payload.document.fields_confidence.deadline,1);
  assert.deepEqual(payload.confirmed_fields,['deadline']);
});
