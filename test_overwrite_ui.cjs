const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');

test('overwrite confirmation is once per batch with a session skip', () => {
  assert.match(html, /id="overwrite-dialog"/);
  assert.match(html, /Don't ask again this session/);
  assert.match(html, /data-testid="overwrite-confirm"/);
  assert.match(app, /ensureOverwriteConfirmed/);
  assert.match(app, /skip_overwrite_prompt/);
  assert.match(app, /overwrite_confirmation_required/);
  assert.match(app, /Don't ask again this session|overwrite-skip-session/);
});

test('GPS keep is an opt-in setting stripped by default', () => {
  assert.match(html, /id="settings-keep-gps"/);
  assert.match(html, /Keep GPS location in saved photos/);
  assert.match(app, /keep_gps/);
  assert.doesNotMatch(html, /id="settings-keep-gps" checked/);
});
