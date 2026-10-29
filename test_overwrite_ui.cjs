const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');

function loadPromptHelpers() {
  const start = app.indexOf('const batchPromptState');
  const end = app.indexOf('async function ensureOverwriteConfirmed');
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  const els = {};
  const exported = {};
  const context = {
    Set,
    Promise,
    exported,
    $(id) {
      return els[id] ||= {
        textContent: '',
        checked: false,
        returnValue: 'cancel',
        showModal() {
          this._open = true;
          queueMicrotask(() => { if (this._close) this._close(); });
        },
        setAttribute() {},
        addEventListener(ev, fn) { if (ev === 'close') this._close = fn; },
        removeEventListener() {},
      };
    },
  };
  vm.runInNewContext(
    `${app.slice(start, end)}
exported.batchPromptState = batchPromptState;
exported.hasOverwriteSkip = hasOverwriteSkip;
exported.hasReplaceAck = hasReplaceAck;
exported.overwriteAckForBatch = overwriteAckForBatch;
exported.replaceAckForBatch = replaceAckForBatch;
exported.askOverwrite = askOverwrite;
exported.askReplace = askReplace;`,
    context,
  );
  exported.el = context.$;
  return { helpers: exported, els };
}

test('overwrite and replace confirmation copy is per batch', () => {
  assert.match(html, /id="overwrite-dialog"/);
  assert.match(html, /Don't ask again this batch/);
  assert.match(html, /data-testid="overwrite-confirm"/);
  assert.match(html, /data-testid="overwrite-cancel"/);
  assert.match(html, /id="replace-dialog"/);
  assert.match(html, /data-testid="replace-confirm"/);
  assert.match(html, /0\. Backups until the new file is verified/);
  assert.doesNotMatch(html, /Don't ask again this session/);
  assert.match(app, /ensureOverwriteConfirmed/);
  assert.match(app, /skip_overwrite_prompt/);
  assert.match(app, /overwrite_confirmation_required/);
  assert.match(app, /replace_confirmation_required/);
  assert.match(app, /confirm_replace/);
  assert.doesNotMatch(app, /overwriteSession/);
  assert.doesNotMatch(app, /skip_overwrite_prompt: true \}/);
});

test('GPS keep is an opt-in setting stripped by default', () => {
  assert.match(html, /id="settings-keep-gps"/);
  assert.match(html, /Keep GPS location in saved photos/);
  assert.match(app, /keep_gps/);
  assert.doesNotMatch(html, /id="settings-keep-gps" checked/);
});

test('overwrite dialog cancel keeps the count and does not confirm', async () => {
  const { helpers, els } = loadPromptHelpers();
  helpers.el('overwrite-dialog').returnValue = 'cancel';
  const decision = await helpers.askOverwrite(3, ['scan_001.png', 'scan_002.png']);
  assert.equal(decision.ok, false);
  assert.equal(decision.skip, false);
  assert.equal(els['overwrite-dialog-count'].textContent, '3');
  assert.equal(els['overwrite-dialog-names'].textContent, 'scan_001.png, scan_002.png');
});

test('overwrite confirm records skip only for that batch', async () => {
  const { helpers, els } = loadPromptHelpers();
  helpers.el('overwrite-dialog').returnValue = 'overwrite';
  const first = await helpers.askOverwrite(1, ['photo_c_ai.png']);
  assert.equal(first.ok, true);
  const skipped = helpers.overwriteAckForBatch('batch-one', true);
  assert.equal(skipped.confirm_overwrite, true);
  assert.equal(skipped.skip_overwrite_prompt, true);
  assert.equal(skipped.batch_id, 'batch-one');
  assert.equal(helpers.hasOverwriteSkip('batch-one'), true);
  assert.equal(helpers.hasOverwriteSkip('batch-two'), false);
  assert.equal(helpers.hasOverwriteSkip('forged-batch-id'), false);
  const missing = helpers.overwriteAckForBatch('', true);
  assert.equal(missing.confirm_overwrite, true);
  assert.equal(missing.skip_overwrite_prompt, undefined);
  assert.equal(missing.batch_id, undefined);
  assert.equal(helpers.hasOverwriteSkip(''), false);
  const secondBatch = helpers.overwriteAckForBatch('batch-two', false);
  assert.equal(secondBatch.skip_overwrite_prompt, false);
  assert.equal(secondBatch.batch_id, 'batch-two');
  assert.equal(helpers.hasOverwriteSkip('batch-two'), false);
});

test('replace original is remembered once per batch and not for another batch', async () => {
  const { helpers, els } = loadPromptHelpers();
  helpers.el('replace-dialog').returnValue = 'cancel';
  const cancelled = await helpers.askReplace(2, ['keep_me.png', 'keep_me_too.png']);
  assert.equal(cancelled.ok, false);
  assert.equal(els['replace-dialog-count'].textContent, '2');
  helpers.el('replace-dialog').returnValue = 'replace';
  const confirmed = await helpers.askReplace(1, ['keep_me.png']);
  assert.equal(confirmed.ok, true);
  const ack = helpers.replaceAckForBatch('batch-one');
  assert.equal(ack.confirm_replace, true);
  assert.equal(ack.batch_id, 'batch-one');
  assert.equal(helpers.hasReplaceAck('batch-one'), true);
  assert.equal(helpers.hasReplaceAck('batch-two'), false);
  const missingReplace = helpers.replaceAckForBatch('');
  assert.equal(missingReplace.confirm_replace, true);
  assert.equal(missingReplace.batch_id, undefined);
});
