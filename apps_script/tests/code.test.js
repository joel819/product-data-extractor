// Tests for Code.gs, run with: node --test apps_script/tests/code.test.js
// Google's services (SpreadsheetApp, UrlFetchApp, ...) are replaced by small in-memory mocks; Code.gs itself is
// loaded unmodified, exactly as it would be pasted into the Apps Script editor.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(path.join(__dirname, '..', 'Code.gs'), 'utf8');

function colLetter(n) { let s = ''; while (n > 0) { const r = (n - 1) % 26; s = String.fromCharCode(65 + r) + s; n = Math.floor((n - 1) / 26); } return s; }

class Sheet {
  constructor(name = 'Products') { this.name = name; this.cells = new Map(); }
  getName() { return this.name; }
  cell(row, col) { const k = `${colLetter(col)}${row}`; if (!this.cells.has(k)) this.cells.set(k, { value: '', formula: null, bg: null }); return this.cells.get(k); }
  getRange(row, col) {
    const c = this.cell(row, col);
    return {
      getValue: () => c.value, setValue: (v) => { c.value = v; c.formula = null; }, setFormula: (f) => { c.formula = f; c.value = f; },
      setBackground: (b) => { c.bg = b; },
    };
  }
  get(ref) { return this.cells.has(ref) ? this.cells.get(ref) : { value: '', formula: null, bg: null }; }
}

function editRange(sheet, row, col, numRows = 1, numCols = 1) {
  return { getSheet: () => sheet, getRow: () => row, getColumn: () => col, getNumColumns: () => numCols, getNumRows: () => numRows, getLastRow: () => row + numRows - 1 };
}

const PRODUCT = {
  url: 'https://shop.example.test/p/1', name: 'Arc Desk Lamp', brand: 'Norvane', sku: 'NV-ARC-204', price: '89.00', currency: 'EUR',
  availability: 'in_stock', description: 'A slim lamp.', image_url: 'https://shop.example.test/img/a.jpg', dimensions: 'W 28 cm', colour: null,
  finish: null, material: 'Steel',
  source_method: {}, confidence: { name: 'high', price: 'high', brand: 'medium', sku: 'high', material: 'low', description: 'low', image_url: 'high' },
  warnings: ['LLM fallback skipped (no LLM_API_KEY set); still missing: colour, finish.'],
};

function load({ fetchImpl, props = {}, triggers = [] } = {}) {
  const calls = { fetch: [], toasts: [], alerts: [], locks: { wait: 0, release: 0 }, created: [], deleted: [] };
  const sheet = new Sheet();
  const triggerList = triggers.slice();
  const ctx = vm.createContext({
    console: { log() {}, error() {} },
    SpreadsheetApp: {
      getUi: () => ({ createMenu: () => ({ addItem() { return this; }, addToUi() {} }), alert: (m) => calls.alerts.push(m) }),
      getActive: () => ({ toast: (m) => calls.toasts.push(m) }),
      getActiveRange: () => editRange(sheet, 2, 8, 2, 1),
      flush() {},
    },
    UrlFetchApp: { fetch: (url, opts) => { calls.fetch.push({ url, opts, notesAtCall: sheet.get('N2').value }); return fetchImpl(url, opts); } },
    LockService: { getDocumentLock: () => ({ waitLock: () => calls.locks.wait++, releaseLock: () => calls.locks.release++ }) },
    PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => props[k] || null }) },
    ScriptApp: {
      getProjectTriggers: () => triggerList,
      deleteTrigger: (t) => { calls.deleted.push(t); triggerList.splice(triggerList.indexOf(t), 1); },
      newTrigger: (fn) => ({ forSpreadsheet: () => ({ onEdit: () => ({ create: () => { calls.created.push(fn); triggerList.push({ getHandlerFunction: () => fn }); } }) }) }),
    },
  });
  vm.runInContext(SOURCE + '\n;globalThis.__CONFIG = CONFIG;', ctx);
  ctx.__CONFIG.API_URL = 'https://api.example.test';
  return { ctx, sheet, calls, cfg: ctx.__CONFIG };
}

const ok = (body) => () => ({ getResponseCode: () => 200, getContentText: () => JSON.stringify(body) });
const reply = (status, body) => () => ({ getResponseCode: () => status, getContentText: () => (typeof body === 'string' ? body : JSON.stringify(body)) });
function paste(env, url, row = 2, col = 8) { env.sheet.getRange(row, col).setValue(url); env.ctx.handleEdit({ range: editRange(env.sheet, row, col) }); }

test('column letters convert to indexes and bad letters are refused', () => {
  const { ctx } = load({ fetchImpl: ok(PRODUCT) });
  assert.equal(ctx.columnIndex_('A'), 1); assert.equal(ctx.columnIndex_('h'), 8); assert.equal(ctx.columnIndex_('Z'), 26);
  assert.equal(ctx.columnIndex_('AA'), 27); assert.equal(ctx.columnIndex_('AZ'), 52);
  for (const bad of ['', '1', 'A1', 'ABCD', null]) assert.throws(() => ctx.columnIndex_(bad), /Not a column letter/);
});

test('pasting a URL calls the API and fills the mapped columns of the same row', () => {
  const env = load({ fetchImpl: ok(PRODUCT) });
  paste(env, 'https://shop.example.test/p/1');
  const { fetch } = env.calls;
  assert.equal(fetch.length, 1);
  assert.equal(fetch[0].url, 'https://api.example.test/extract');
  assert.equal(fetch[0].opts.method, 'post'); assert.equal(fetch[0].opts.contentType, 'application/json');
  assert.deepEqual(JSON.parse(fetch[0].opts.payload), { url: 'https://shop.example.test/p/1' });
  assert.equal(fetch[0].opts.muteHttpExceptions, true);
  const s = env.sheet;
  assert.equal(s.get('A2').value, 'Arc Desk Lamp'); assert.equal(s.get('B2').value, 'Norvane'); assert.equal(s.get('C2').value, 'NV-ARC-204');
  assert.strictEqual(s.get('D2').value, 89);                       // a real number, not the string "89.00"
  assert.equal(s.get('E2').value, 'EUR'); assert.equal(s.get('F2').value, 'in_stock');
  assert.equal(s.get('I2').value, 'A slim lamp.'); assert.equal(s.get('J2').value, 'W 28 cm'); assert.equal(s.get('M2').value, 'Steel');
  assert.equal(s.get('K2').value, ''); assert.equal(s.get('L2').value, '');   // not found: left empty, nothing invented
  assert.equal(s.get('H2').value, 'https://shop.example.test/p/1');           // the URL cell is untouched
});

test('the image is inserted with =IMAGE()', () => {
  const env = load({ fetchImpl: ok(PRODUCT) });
  paste(env, 'https://shop.example.test/p/1');
  assert.equal(env.sheet.get('G2').formula, '=IMAGE("https://shop.example.test/img/a.jpg")');
});

test('a quote in an image URL cannot break out of the formula', () => {
  const env = load({ fetchImpl: ok({ ...PRODUCT, image_url: 'https://x.test/a.jpg"),HYPERLINK("http://evil' }) });
  paste(env, 'https://shop.example.test/p/1');
  assert.equal(env.sheet.get('G2').formula, null);
  assert.ok(!String(env.sheet.get('G2').value).startsWith('='));
});

test('only low-confidence cells are highlighted yellow; the others are cleared', () => {
  const env = load({ fetchImpl: ok(PRODUCT) });
  env.sheet.getRange(2, 1).setBackground('#ff0000'); // stale colour from a previous row content
  paste(env, 'https://shop.example.test/p/1');
  const s = env.sheet;
  assert.equal(s.get('M2').bg, '#ffff00'); assert.equal(s.get('I2').bg, '#ffff00');   // material, description: low
  assert.equal(s.get('A2').bg, null); assert.equal(s.get('B2').bg, null); assert.equal(s.get('G2').bg, null);
  assert.equal(s.get('K2').bg, null);                                                 // an empty cell is never highlighted
});

test('the highlight rule and colour come from CONFIG', () => {
  const env = load({ fetchImpl: ok(PRODUCT) });
  env.cfg.HIGHLIGHT_CONFIDENCE = ['low', 'medium']; env.cfg.HIGHLIGHT_COLOR = '#fff59d';
  paste(env, 'https://shop.example.test/p/1');
  assert.equal(env.sheet.get('B2').bg, '#fff59d'); assert.equal(env.sheet.get('A2').bg, null);
});

test('the notes column shows progress, the field count and the API warnings', () => {
  const env = load({ fetchImpl: ok(PRODUCT) });
  paste(env, 'https://shop.example.test/p/1');
  assert.equal(env.calls.fetch[0].notesAtCall, 'Fetching…');
  assert.match(env.sheet.get('N2').value, /^10 fields found \| LLM fallback skipped/);
});

test('re-pasting a different URL in the same row clears values that the new page does not have', () => {
  const env = load({ fetchImpl: ok({ ...PRODUCT, name: 'Other', brand: null, material: null, image_url: null, confidence: {}, warnings: [] }) });
  for (const ref of ['B2', 'M2']) env.sheet.cells.set(ref, { value: 'STALE', formula: null, bg: '#ffff00' });
  env.sheet.cells.set('G2', { value: '=IMAGE("old")', formula: '=IMAGE("old")', bg: null });
  paste(env, 'https://shop.example.test/p/2');
  assert.equal(env.sheet.get('A2').value, 'Other'); assert.equal(env.sheet.get('B2').value, ''); assert.equal(env.sheet.get('M2').value, '');
  assert.equal(env.sheet.get('G2').formula, null); assert.equal(env.sheet.get('G2').value, ''); assert.equal(env.sheet.get('M2').bg, null);
});

test('API errors are written to the notes column using the API error code and message, and no data is written', () => {
  const env = load({ fetchImpl: reply(400, { error: 'blocked_address', message: "Host 'localhost' resolves to a non-public address" }) });
  paste(env, 'http://localhost/admin');
  assert.equal(env.sheet.get('N2').value, "Error: blocked_address: Host 'localhost' resolves to a non-public address");
  assert.equal(env.sheet.get('A2').value, '');
});

test('non-JSON and 5xx answers become a short HTTP error', () => {
  for (const [status, body] of [[500, 'Internal Server Error'], [502, '<html>Bad gateway</html>'], [200, 'not json']]) {
    const env = load({ fetchImpl: reply(status, body) });
    paste(env, 'https://shop.example.test/p/1');
    assert.equal(env.sheet.get('N2').value, `Error: HTTP ${status}`);
  }
});

test('a network exception is reported and the document lock is always released', () => {
  const env = load({ fetchImpl: () => { throw new Error('DNS error: api.example.test'); } });
  paste(env, 'https://shop.example.test/p/1');
  assert.equal(env.sheet.get('N2').value, 'Error: DNS error: api.example.test');
  assert.equal(env.calls.locks.wait, 1); assert.equal(env.calls.locks.release, 1);
});

test('the placeholder API_URL is rejected with an instruction instead of a request', () => {
  const env = load({ fetchImpl: ok(PRODUCT) });
  env.cfg.API_URL = 'https://your-api.example.com';
  paste(env, 'https://shop.example.test/p/1');
  assert.match(env.sheet.get('N2').value, /Set CONFIG\.API_URL/); assert.equal(env.calls.fetch.length, 0);
});

test('the API key is sent from CONFIG or from script properties, and omitted when neither is set', () => {
  let env = load({ fetchImpl: ok(PRODUCT) });
  paste(env, 'https://shop.example.test/p/1');
  assert.equal(Object.keys(env.calls.fetch[0].opts.headers).length, 0);   // no key configured: no header
  env = load({ fetchImpl: ok(PRODUCT) }); env.cfg.API_KEY = 'from-config';
  paste(env, 'https://shop.example.test/p/1');
  assert.equal(env.calls.fetch[0].opts.headers['X-API-Key'], 'from-config');
  env = load({ fetchImpl: ok(PRODUCT), props: { API_KEY: 'from-properties' } });
  paste(env, 'https://shop.example.test/p/1');
  assert.equal(env.calls.fetch[0].opts.headers['X-API-Key'], 'from-properties');
});

test('edits that are not a URL in the URL column are ignored', () => {
  const env = load({ fetchImpl: ok(PRODUCT) });
  paste(env, 'https://shop.example.test/p/1', 2, 3);            // wrong column (C)
  paste(env, 'https://shop.example.test/p/1', 1, 8);            // header row
  paste(env, 'just some text', 2, 8);                           // not a link
  paste(env, 'ftp://files.example.test/x', 3, 8);               // not http(s)
  paste(env, '   ', 4, 8);                                      // blank
  paste(env, 'javascript:alert(1)', 5, 8);
  env.ctx.handleEdit({ range: editRange(env.sheet, 2, 8, 1, 2) }); // a multi-column paste
  env.ctx.handleEdit({});                                       // malformed event
  env.ctx.handleEdit(undefined);
  assert.equal(env.calls.fetch.length, 0);
});

test('SHEET_NAME limits which tab reacts', () => {
  const env = load({ fetchImpl: ok(PRODUCT) });
  env.cfg.SHEET_NAME = 'Catalogue';
  paste(env, 'https://shop.example.test/p/1');                  // the mock sheet is called "Products"
  assert.equal(env.calls.fetch.length, 0);
  env.cfg.SHEET_NAME = 'Products';
  paste(env, 'https://shop.example.test/p/1');
  assert.equal(env.calls.fetch.length, 1);
});

test('the URL column and first data row are configurable', () => {
  const env = load({ fetchImpl: ok(PRODUCT) });
  env.cfg.URL_COLUMN = 'P'; env.cfg.FIRST_DATA_ROW = 5;
  paste(env, 'https://shop.example.test/p/1', 3, 16);           // row 3 is above FIRST_DATA_ROW
  assert.equal(env.calls.fetch.length, 0);
  paste(env, 'https://shop.example.test/p/1', 5, 16);
  assert.equal(env.calls.fetch.length, 1); assert.equal(env.sheet.get('A5').value, 'Arc Desk Lamp');
});

test('pasting several URLs processes up to MAX_ROWS_PER_EDIT and says what was left', () => {
  const env = load({ fetchImpl: ok(PRODUCT) });
  env.cfg.MAX_ROWS_PER_EDIT = 3;
  for (let r = 2; r <= 6; r++) env.sheet.getRange(r, 8).setValue(`https://shop.example.test/p/${r}`);
  env.ctx.handleEdit({ range: editRange(env.sheet, 2, 8, 5, 1) });
  assert.equal(env.calls.fetch.length, 3);
  assert.equal(env.calls.toasts.length, 1); assert.match(env.calls.toasts[0], /first 3/);
  assert.equal(env.sheet.get('A4').value, 'Arc Desk Lamp'); assert.equal(env.sheet.get('A5').value, '');
});

test('mapped columns are optional: removing one skips that field', () => {
  const env = load({ fetchImpl: ok(PRODUCT) });
  delete env.cfg.COLUMNS.sku; env.cfg.COLUMNS.brand = ''; delete env.cfg.COLUMNS.notes;
  paste(env, 'https://shop.example.test/p/1');
  assert.equal(env.sheet.get('C2').value, ''); assert.equal(env.sheet.get('B2').value, ''); assert.equal(env.sheet.get('A2').value, 'Arc Desk Lamp');
  assert.equal(env.sheet.get('N2').value, '');
});

test('a non-numeric price is kept as text rather than lost', () => {
  const env = load({ fetchImpl: ok({ ...PRODUCT, price: 'on request' }) });
  paste(env, 'https://shop.example.test/p/1');
  assert.equal(env.sheet.get('D2').value, 'on request');
});

test('installTrigger replaces any existing trigger and removeTrigger deletes it', () => {
  const old = { getHandlerFunction: () => 'handleEdit' }, other = { getHandlerFunction: () => 'somethingElse' };
  const env = load({ fetchImpl: ok(PRODUCT), triggers: [old, other] });
  env.ctx.installTrigger();
  assert.deepEqual(env.calls.created, ['handleEdit']); assert.deepEqual(env.calls.deleted, [old]);
  env.ctx.removeTrigger();
  assert.equal(env.calls.deleted.length, 2); assert.match(env.calls.alerts.at(-1), /removed/);
  env.ctx.removeTrigger();
  assert.match(env.calls.alerts.at(-1), /no automatic trigger/);
});

test('extractSelectedRows processes every selected row that holds a URL', () => {
  const env = load({ fetchImpl: ok(PRODUCT) });
  env.sheet.getRange(2, 8).setValue('https://shop.example.test/p/2'); env.sheet.getRange(3, 8).setValue('not a url');
  env.ctx.extractSelectedRows();            // the mock selection is H2:H3
  assert.equal(env.calls.fetch.length, 1);
});
