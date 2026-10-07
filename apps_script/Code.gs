/**
 * product-data-extractor for Google Sheets
 *
 * Paste a product page URL into the URL column and this script calls your product-data-extractor API,
 * then fills the same row with the fields it found, inserts the product image with =IMAGE(), and
 * highlights low-confidence cells yellow so a human can check them. Fields the API did not find are left
 * empty. Nothing is guessed.
 *
 * Setup: see README.md in this folder (about five minutes). Everything runs in YOUR Google account and
 * talks only to YOUR API; no keys or accounts belong to anyone else.
 */

/* ============================== CONFIG: edit only this block ============================== */
const CONFIG = {
  // Where your product-data-extractor API is running, reachable from the internet (Google's servers call it).
  // No trailing slash. "http://localhost:8000" will NOT work: Apps Script runs on Google's servers.
  API_URL: 'https://your-api.example.com',

  // Optional. Only needed if you set API_KEY on the server. For a sheet other people can edit, leave this
  // empty and put the key in Project Settings > Script properties (name: API_KEY) instead.
  API_KEY: '',

  // Only react on this sheet (tab). Leave '' to react on every sheet in the spreadsheet.
  SHEET_NAME: '',

  // The column where you paste product URLs, and the first row that holds data (row 1 = headers).
  URL_COLUMN: 'H',
  FIRST_DATA_ROW: 2,

  // Which column each field is written to. Delete a line (or set it to '') to skip that field.
  // 'image_url' is written as an =IMAGE() formula. 'notes' receives status text and warnings.
  COLUMNS: {
    name: 'A',
    brand: 'B',
    sku: 'C',
    price: 'D',
    currency: 'E',
    availability: 'F',
    image_url: 'G',
    description: 'I',
    dimensions: 'J',
    colour: 'K',
    finish: 'L',
    material: 'M',
    notes: 'N',
  },

  // Cells whose confidence is in this list get the highlight colour (the rest are cleared).
  HIGHLIGHT_CONFIDENCE: ['low'],
  HIGHLIGHT_COLOR: '#ffff00',

  // Pasting many URLs at once is allowed up to this many rows per edit (Apps Script has run-time and quota limits).
  MAX_ROWS_PER_EDIT: 10,
};
/* ========================================================================================== */

const STATUS_FETCHING = 'Fetching…';

/** Adds a menu: Product extractor > Install automatic trigger / Extract selected rows / Remove trigger. */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Product extractor')
    .addItem('Install automatic trigger', 'installTrigger')
    .addItem('Extract selected rows', 'extractSelectedRows')
    .addItem('Remove automatic trigger', 'removeTrigger')
    .addToUi();
}

/**
 * Creates the installable onEdit trigger. Run this once. A plain onEdit(e) function cannot call external
 * services; an installable trigger can, which is why this step exists.
 */
function installTrigger() {
  removeTrigger_();
  ScriptApp.newTrigger('handleEdit').forSpreadsheet(SpreadsheetApp.getActive()).onEdit().create();
  alert_('Done. Paste a product URL into column ' + CONFIG.URL_COLUMN + ' to try it.');
}

function removeTrigger() {
  const n = removeTrigger_();
  alert_(n ? 'Automatic trigger removed.' : 'There was no automatic trigger to remove.');
}

/** The installable onEdit handler: runs when a cell in the URL column is edited. */
function handleEdit(e) {
  if (!e || !e.range) return;
  const range = e.range;
  const sheet = range.getSheet();
  if (CONFIG.SHEET_NAME && sheet.getName() !== CONFIG.SHEET_NAME) return;
  const urlCol = columnIndex_(CONFIG.URL_COLUMN);
  // only a paste/typing confined to the URL column; edits elsewhere in the row are ignored
  if (range.getColumn() !== urlCol || range.getNumColumns() !== 1) return;

  const first = Math.max(range.getRow(), CONFIG.FIRST_DATA_ROW);
  const last = Math.min(range.getLastRow(), first + CONFIG.MAX_ROWS_PER_EDIT - 1);
  for (let row = first; row <= last; row++) processRow_(sheet, row);
  if (range.getLastRow() > last) {
    toast_('Only the first ' + CONFIG.MAX_ROWS_PER_EDIT + ' pasted URLs were processed. Select the rest and use Product extractor > Extract selected rows.');
  }
}

/** Menu action: process every row in the current selection that has a URL. */
function extractSelectedRows() {
  const range = SpreadsheetApp.getActiveRange();
  const sheet = range.getSheet();
  const first = Math.max(range.getRow(), CONFIG.FIRST_DATA_ROW);
  const last = Math.min(range.getLastRow(), first + CONFIG.MAX_ROWS_PER_EDIT - 1);
  for (let row = first; row <= last; row++) processRow_(sheet, row);
}

/* ---------------------------------------- internals ---------------------------------------- */

function processRow_(sheet, row) {
  const url = String(sheet.getRange(row, columnIndex_(CONFIG.URL_COLUMN)).getValue() || '').trim();
  if (!isHttpUrl_(url)) return; // empty cell, header text or not a link: do nothing
  const lock = LockService.getDocumentLock();
  lock.waitLock(30000);
  try {
    setNotes_(sheet, row, STATUS_FETCHING);
    SpreadsheetApp.flush();
    const data = extract_(url);
    writeRow_(sheet, row, data);
  } catch (err) {
    setNotes_(sheet, row, 'Error: ' + (err && err.message ? err.message : err));
  } finally {
    lock.releaseLock();
  }
}

/** Calls POST {API_URL}/extract and returns the parsed ProductData, or throws with the API's own message. */
function extract_(url) {
  validateConfig_();
  const headers = {};
  const key = apiKey_();
  if (key) headers['X-API-Key'] = key;
  const response = UrlFetchApp.fetch(CONFIG.API_URL.replace(/\/+$/, '') + '/extract', {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify({ url: url }),
    headers: headers,
    muteHttpExceptions: true,
  });
  const status = response.getResponseCode();
  let body = null;
  try {
    body = JSON.parse(response.getContentText());
  } catch (ignored) {
    /* handled below */
  }
  if (status !== 200 || !body) {
    const detail = body && body.error ? body.error + ': ' + body.message : 'HTTP ' + status;
    throw new Error(detail);
  }
  return body;
}

function writeRow_(sheet, row, data) {
  const cols = CONFIG.COLUMNS;
  let found = 0;
  Object.keys(cols).forEach(function (field) {
    if (!cols[field] || field === 'notes') return;
    const cell = sheet.getRange(row, columnIndex_(cols[field]));
    const value = data[field];
    const has = value !== null && value !== undefined && value !== '';
    if (has) found++;
    if (field === 'image_url') {
      if (has && isHttpUrl_(value) && String(value).indexOf('"') === -1) cell.setFormula('=IMAGE("' + value + '")');
      else cell.setValue(has ? value : '');
    } else if (field === 'price') {
      const n = has ? Number(value) : NaN;
      cell.setValue(isNaN(n) ? (has ? value : '') : n); // a real number, so the sheet can sum and sort it
    } else {
      cell.setValue(has ? value : '');
    }
    const confidence = data.confidence ? data.confidence[field] : null;
    if (has && CONFIG.HIGHLIGHT_CONFIDENCE.indexOf(confidence) !== -1) cell.setBackground(CONFIG.HIGHLIGHT_COLOR);
    else cell.setBackground(null);
  });
  const warnings = (data.warnings || []).join(' | ');
  setNotes_(sheet, row, found + ' fields found' + (warnings ? ' | ' + warnings : ''));
}

function setNotes_(sheet, row, text) {
  if (CONFIG.COLUMNS.notes) sheet.getRange(row, columnIndex_(CONFIG.COLUMNS.notes)).setValue(text);
}

function validateConfig_() {
  if (!CONFIG.API_URL || /your-api\.example\.com/.test(CONFIG.API_URL)) {
    throw new Error('Set CONFIG.API_URL at the top of the script to your API address.');
  }
  if (!/^https?:\/\//.test(CONFIG.API_URL)) throw new Error('CONFIG.API_URL must start with https:// (or http://).');
}

function apiKey_() {
  return CONFIG.API_KEY || PropertiesService.getScriptProperties().getProperty('API_KEY') || '';
}

function isHttpUrl_(s) {
  return /^https?:\/\/[^\s]+$/i.test(String(s || ''));
}

/** 'A' -> 1, 'H' -> 8, 'AA' -> 27 */
function columnIndex_(letters) {
  const s = String(letters || '').toUpperCase();
  if (!/^[A-Z]{1,3}$/.test(s)) throw new Error('Not a column letter: "' + letters + '" (use A, B, ... AA).');
  let n = 0;
  for (let i = 0; i < s.length; i++) n = n * 26 + (s.charCodeAt(i) - 64);
  return n;
}

function removeTrigger_() {
  let removed = 0;
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'handleEdit') {
      ScriptApp.deleteTrigger(t);
      removed++;
    }
  });
  return removed;
}

function alert_(message) {
  try {
    SpreadsheetApp.getUi().alert(message);
  } catch (ignored) {
    console.log(message); // no UI available (e.g. run from a trigger)
  }
}

function toast_(message) {
  try {
    SpreadsheetApp.getActive().toast(message);
  } catch (ignored) {
    console.log(message);
  }
}
