const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const code=fs.readFileSync(__dirname+'/app.js','utf8');
const html=fs.readFileSync(__dirname+'/index.html','utf8');

function extractBlock(source, needle) {
  const start = source.indexOf(needle);
  assert.ok(start >= 0, `missing ${needle}`);
  const open = source.indexOf('{', start);
  let depth = 0;
  for (let i = open; i < source.length; i++) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}') {
      depth--;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  throw new Error(`unclosed ${needle}`);
}

function shortcutHarness(api) {
  const state = {
    index: 0, width: 600, height: 400, busy: false, requestId: 1, points: [],
    images: [{path: 'photo.jpg', modified: 1}], view: 'crop',
  };
  const messages = [];
  let active = {tagName: 'BODY', isContentEditable: false};
  const listeners = [];
  const context = {
    state,
    api,
    document: {
      get activeElement() { return active; },
      addEventListener(type, fn) { if (type === 'keydown') listeners.push(fn); },
      querySelector() { return null; },
    },
    $(id) {
      if (id === 'folder-modal') return {classList: {contains: (cls) => cls === 'hidden'}};
      return {
        classList: {contains: () => false, add() {}, remove() {}, toggle() {}},
        style: {}, disabled: false, querySelector: () => ({textContent: ''}),
        children: [], textContent: '',
      };
    },
    endZoom() {},
    updateControls() {},
    renderOverlay() {},
    toast: (message) => messages.push(message),
    startZoom() {},
    openImage() {},
    cropSave() {},
    closePicker() {},
  };
  vm.createContext(context);
  vm.runInContext(
    code.slice(code.indexOf('let cropPredictionCache ='), code.indexOf('async function cropSave(')),
    context,
  );
  vm.runInContext(extractBlock(code, "document.addEventListener('keydown'") + ');', context);
  assert.equal(listeners.length, 1);
  const pending = [];
  const realPredict = context.predictCropCorners;
  context.predictCropCorners = function (...args) {
    context.predictCalls += 1;
    const result = realPredict(...args);
    pending.push(Promise.resolve(result));
    return result;
  };
  const realSelect = context.selectWholeImage;
  context.selectWholeImage = function (...args) {
    context.selectCalls += 1;
    return realSelect(...args);
  };
  context.predictCalls = 0;
  context.selectCalls = 0;
  async function press(partial) {
    const event = {
      key: 'a',
      ctrlKey: false,
      metaKey: false,
      shiftKey: false,
      repeat: false,
      defaultPrevented: false,
      preventDefault() { event.defaultPrevented = true; },
      ...partial,
    };
    listeners[0](event);
    const batch = pending.splice(0);
    await Promise.all(batch);
    return event;
  }
  return {state, messages, press, setActive(el) { active = el; }, context};
}

const twoCandidates = {
  candidates: [
    {points: [[10, 10], [300, 10], [300, 300], [10, 300]]},
    {points: [[320, 10], [550, 10], [550, 300], [320, 300]]},
  ],
};

test('Ctrl+A calls predict', async () => {
  let calls = 0;
  const h = shortcutHarness(async () => { calls++; return twoCandidates; });
  const event = await h.press({key: 'a', ctrlKey: true});
  assert.equal(event.defaultPrevented, true);
  assert.equal(h.context.predictCalls, 1);
  assert.equal(calls, 1);
  assert.equal(h.state.points[0].x, 10);
});

test('a second fresh Ctrl+A cycles cached candidates', async () => {
  let calls = 0;
  const h = shortcutHarness(async () => { calls++; return twoCandidates; });
  await h.press({key: 'a', ctrlKey: true});
  await h.press({key: 'a', ctrlKey: true});
  assert.equal(h.context.predictCalls, 2);
  assert.equal(calls, 1);
  assert.equal(h.state.points[0].x, 320);
});

test('Ctrl+Shift+A selects the whole image', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  const event = await h.press({key: 'A', ctrlKey: true, shiftKey: true});
  assert.equal(event.defaultPrevented, true);
  assert.equal(h.context.selectCalls, 1);
  assert.equal(h.context.predictCalls, 0);
  assert.equal(h.state.points.length, 4);
  assert.equal(h.state.points[0].x, 0);
  assert.equal(h.state.points[0].y, 0);
  assert.equal(h.state.points[1].x, 599);
  assert.equal(h.state.points[2].y, 399);
  assert.equal(h.state.points[3].x, 0);
});

test('Ctrl+A with focus in a text input is ignored', async () => {
  const keys = code.slice(code.indexOf("document.addEventListener('keydown'"));
  assert.ok(keys.indexOf("'INPUT','TEXTAREA','SELECT'") < keys.indexOf('predictCropCorners()'));
  const h = shortcutHarness(async () => twoCandidates);
  h.setActive({tagName: 'INPUT', isContentEditable: false});
  const event = await h.press({key: 'a', ctrlKey: true});
  assert.equal(event.defaultPrevented, false);
  assert.equal(h.context.predictCalls, 0);
  assert.equal(h.state.points.length, 0);
});

test('a held Ctrl+A (event.repeat) does not spam predict', async () => {
  let calls = 0;
  const h = shortcutHarness(async () => { calls++; return twoCandidates; });
  await h.press({key: 'a', ctrlKey: true, repeat: true});
  assert.equal(h.context.predictCalls, 0);
  assert.equal(calls, 0);
  await h.press({key: 'a', ctrlKey: true});
  await h.press({key: 'a', ctrlKey: true, repeat: true});
  assert.equal(h.context.predictCalls, 1);
  assert.equal(calls, 1);
  assert.equal(h.state.points[0].x, 10);
});

test('Ctrl+A and Ctrl+Shift+A are no-ops while busy', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  h.state.busy = true;
  const predictEvent = await h.press({key: 'a', ctrlKey: true});
  const selectEvent = await h.press({key: 'A', ctrlKey: true, shiftKey: true});
  assert.equal(predictEvent.defaultPrevented, true);
  assert.equal(selectEvent.defaultPrevented, true);
  assert.equal(h.context.predictCalls, 0);
  assert.equal(h.context.selectCalls, 0);
  assert.equal(h.state.points.length, 0);
});

test('shortcut strip and tooltips mention Ctrl+A and Ctrl+Shift+A', () => {
  assert.match(html, /<kbd>Ctrl<\/kbd><kbd>A<\/kbd> predict corners/);
  assert.match(html, /<kbd>Ctrl<\/kbd><kbd>Shift<\/kbd><kbd>A<\/kbd> whole image/);
  assert.match(html, /id="predict-corners" title="Predict the photo corners locally \(Ctrl\+A\)"/);
  assert.match(html, /id="select-whole" title="Select the full image boundary \(Ctrl\+Shift\+A\)"/);
});
