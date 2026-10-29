const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('os');
const path = require('path');
const vm = require('node:vm');
const { spawn, execFileSync } = require('node:child_process');

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

function pythonBin() {
  for (const cmd of ['python', 'python3']) {
    try {
      execFileSync(cmd, ['-c', 'import PIL']);
      return cmd;
    } catch (_) { /* try next */ }
  }
  throw new Error('python interpreter not found');
}

function startServer(python, dataDir) {
  return new Promise((resolve, reject) => {
    const child = spawn(python, ['server.py', '--no-browser', '--data-dir', dataDir, '--port', '0'], {
      cwd: __dirname,
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
    });
    let buf = '';
    let settled = false;
    const fail = (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      child.kill();
      reject(err);
    };
    const succeed = (base) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      child.stdout.off('data', onData);
      child.off('exit', onExit);
      child.off('error', fail);
      resolve({ child, base });
    };
    const onData = (chunk) => {
      buf += chunk.toString();
      const match = buf.match(/Cropper is running at (http:\/\/127\.0\.0\.1:\d+)/);
      if (match) succeed(match[1]);
    };
    const onExit = (code) => fail(new Error(`server exited ${code}: ${buf}`));
    const timer = setTimeout(() => fail(new Error(`server start timeout: ${buf}`)), 30000);
    child.stdout.on('data', onData);
    child.stderr.on('data', (chunk) => { buf += chunk.toString(); });
    child.on('error', fail);
    child.on('exit', onExit);
  });
}

function loadCropClient({ base, token, els, skipChecked }) {
  const start = app.indexOf('const batchPromptState');
  const end = app.indexOf('\nfunction saveSettings()');
  const exported = {};
  const dialogOpens = { overwrite: 0 };
  const context = {
    Promise,
    fetch: (resource, opts) => globalThis.fetch(base + resource, opts),
    state: { token, stageRoutingAvailable: true },
    exported,
    $(id) {
      return els[id] ||= {
        textContent: '',
        checked: false,
        returnValue: 'cancel',
        showModal() {
          if (id === 'overwrite-dialog') {
            dialogOpens.overwrite += 1;
            this.returnValue = 'overwrite';
            if (skipChecked && els['overwrite-skip-batch']) els['overwrite-skip-batch'].checked = true;
          }
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
exported.api = api;
exported.ensureOverwriteConfirmed = ensureOverwriteConfirmed;
exported.batchPromptState = batchPromptState;`,
    context,
  );
  exported.dialogOpens = dialogOpens;
  exported.el = context.$;
  return exported;
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
  assert.match(app, /confirmation_token/);
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

test('overwrite confirm records skip only with a real token', async () => {
  const { helpers } = loadPromptHelpers();
  helpers.el('overwrite-dialog').returnValue = 'overwrite';
  const first = await helpers.askOverwrite(1, ['photo_c_ai.png']);
  assert.equal(first.ok, true);
  const skipped = helpers.overwriteAckForBatch('issued-token', true);
  assert.equal(skipped.confirm_overwrite, true);
  assert.equal(skipped.skip_overwrite_prompt, true);
  assert.equal(skipped.confirmation_token, 'issued-token');
  assert.equal(helpers.hasOverwriteSkip('issued-token'), true);
  assert.equal(helpers.hasOverwriteSkip('other-token'), false);
  const missing = helpers.overwriteAckForBatch('', true);
  assert.equal(missing.confirm_overwrite, true);
  assert.equal(missing.skip_overwrite_prompt, undefined);
  assert.equal(missing.confirmation_token, undefined);
});

test('replace original is remembered once per issued token', async () => {
  const { helpers, els } = loadPromptHelpers();
  helpers.el('replace-dialog').returnValue = 'cancel';
  const cancelled = await helpers.askReplace(2, ['keep_me.png', 'keep_me_too.png']);
  assert.equal(cancelled.ok, false);
  assert.equal(els['replace-dialog-count'].textContent, '2');
  helpers.el('replace-dialog').returnValue = 'replace';
  const confirmed = await helpers.askReplace(1, ['keep_me.png']);
  assert.equal(confirmed.ok, true);
  const ack = helpers.replaceAckForBatch('issued-token');
  assert.equal(ack.confirm_replace, true);
  assert.equal(ack.confirmation_token, 'issued-token');
  assert.equal(helpers.hasReplaceAck('issued-token'), true);
  assert.equal(helpers.hasReplaceAck('other-token'), false);
});

test('crop through the overwrite dialog overwrites the existing file', async () => {
  const python = pythonBin();
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'cropper-ow-'));
  const batch = path.join(dataDir, 'batch');
  execFileSync(python, ['-c', `
from pathlib import Path
from PIL import Image
root = Path(${JSON.stringify(batch)})
root.mkdir(parents=True, exist_ok=True)
Image.new('RGB', (16, 16), 'red').save(root / 'photo.png')
out = root / '1.5. TBP Cropped'
out.mkdir(parents=True, exist_ok=True)
Image.new('RGB', (16, 16), 'gray').save(out / 'photo_c.png')
`]);
  const before = fs.readFileSync(path.join(batch, '1.5. TBP Cropped', 'photo_c.png'));
  const { child, base } = await startServer(python, dataDir);
  try {
    const session = await (await fetch(`${base}/api/session`)).json();
    const els = {};
    const client = loadCropClient({ base, token: session.token, els, skipChecked: true });
    await client.api('/api/settings', {
      conflict_policy: 'overwrite',
      replace_original: false,
      filename_template: '{name}{suffix}{ext}',
    });
    const result = await client.api('/api/crop', {
      path: path.join(batch, 'photo.png'),
      points: [[0, 0], [15, 0], [15, 15], [0, 15]],
      rotation: 0,
    });
    assert.equal(result.name, 'photo_c.png');
    assert.equal(client.dialogOpens.overwrite, 1);
    assert.equal(els['overwrite-dialog-count'].textContent, '1');
    const after = fs.readFileSync(path.join(batch, '1.5. TBP Cropped', 'photo_c.png'));
    assert.notEqual(Buffer.compare(before, after), 0);
  } finally {
    child.kill();
  }
});
