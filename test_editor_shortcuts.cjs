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

function makeControl(tag, extra = {}) {
  const el = {
    tagName: tag,
    type: extra.type || '',
    value: extra.value || '',
    checked: extra.checked || false,
    isContentEditable: false,
    disabled: false,
    textContent: '',
    style: {},
    children: [{}, {}, {}, {}],
    _hidden: extra.hidden || false,
    _listeners: {},
    classList: {
      contains: (name) => name === 'hidden' && el._hidden,
      add: (name) => { if (name === 'hidden') el._hidden = true; },
      remove: (name) => { if (name === 'hidden') el._hidden = false; },
      toggle() {},
    },
    querySelector: () => ({textContent: ''}),
    addEventListener(type, fn) { (el._listeners[type] ||= []).push(fn); },
    dispatch(type) {
      for (const fn of el._listeners[type] || []) fn({currentTarget: el, target: el});
    },
  };
  return el;
}

function shortcutHarness(api) {
  const state = {
    index: 0, width: 600, height: 400, busy: false, requestId: 1, folderLoadId: 0, points: [],
    images: [{path: 'photo.jpg', name: 'photo.jpg', bytes: 10, modified: 1}], view: 'crop',
  };
  const messages = [];
  const body = {tagName: 'BODY', isContentEditable: false};
  let active = body;
  let folderModalHidden = true;
  let openDialog = null;
  const listeners = [];
  const cropAspect = makeControl('SELECT', {value: 'auto'});
  const autoNext = makeControl('INPUT', {type: 'checkbox', checked: true});
  const cropAspectCustom = makeControl('INPUT', {type: 'text', value: '2:3'});
  const imageLayer = makeControl('DIV');
  const els = {
    'crop-aspect': cropAspect,
    'auto-next': autoNext,
    'crop-aspect-custom': cropAspectCustom,
    'crop-aspect-custom-field': makeControl('LABEL', {hidden: true}),
    'save-error': makeControl('DIV', {hidden: true}),
    'image-layer': imageLayer,
    'empty-state': makeControl('DIV', {hidden: true}),
    'image-title': makeControl('DIV'),
    'image-subtitle': makeControl('DIV'),
    position: makeControl('DIV'),
    photo: makeControl('IMG'),
    'predict-corners': makeControl('BUTTON'),
    'select-whole': makeControl('BUTTON'),
  };
  cropAspect.blur = () => { if (active === cropAspect) active = body; };
  autoNext.blur = () => { if (active === autoNext) active = body; };
  const context = {
    state,
    settings: {autoNext: true, aspect: 'auto', customAspect: '2:3'},
    localStorage: {setItem() {}, getItem() { return null; }},
    api,
    formatBytes: (n) => `${n}`,
    clearLiveCropPreview() {},
    assignImage: async () => {},
    imagePath: () => '',
    fitImage() {},
    handleOutsideAllowedFolder: () => false,
    document: {
      get activeElement() { return active; },
      addEventListener(type, fn) { if (type === 'keydown') listeners.push(fn); },
      querySelector(sel) { return sel === 'dialog[open]' ? openDialog : null; },
      querySelectorAll(sel) {
        if (sel === '#auto-next, #crop-aspect, #crop-aspect-custom') return [autoNext, cropAspect, cropAspectCustom];
        if (sel === '.image-tile') return [];
        return [];
      },
    },
    $(id) {
      if (id === 'folder-modal') return {classList: {contains: (cls) => cls === 'hidden' ? folderModalHidden : false}};
      if (els[id]) return els[id];
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
  if (code.includes('function releaseCropShortcutFocus')) {
    vm.runInContext(extractBlock(code, 'function releaseCropShortcutFocus') + ';', context);
  }
  vm.runInContext(extractBlock(code, 'function saveSettings') + ';', context);
  vm.runInContext(extractBlock(code, 'async function openImage') + ';', context);
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
  function dispatch(partial) {
    const event = {
      key: 'a',
      ctrlKey: false,
      metaKey: false,
      altKey: false,
      shiftKey: false,
      repeat: false,
      isComposing: false,
      keyCode: 0,
      defaultPrevented: false,
      preventDefault() { event.defaultPrevented = true; },
      ...partial,
    };
    listeners[0](event);
    return event;
  }
  async function flush() {
    const batch = pending.splice(0);
    await Promise.all(batch);
  }
  async function press(partial) {
    const event = dispatch(partial);
    await flush();
    return event;
  }
  return {
    state, messages, press, dispatch, flush,
    els, cropAspect, autoNext, body,
    setActive(el) { active = el; },
    setFolderModalHidden(hidden) { folderModalHidden = hidden; },
    setOpenDialog(el) { openDialog = el; },
    bindSettings() { vm.runInContext(extractBlock(code, 'function loadSettings') + '; loadSettings();', context); },
    context,
  };
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

test('a third Ctrl+A wraps to the first candidate', async () => {
  let calls = 0;
  const h = shortcutHarness(async () => { calls++; return twoCandidates; });
  await h.press({key: 'a', ctrlKey: true});
  await h.press({key: 'a', ctrlKey: true});
  await h.press({key: 'a', ctrlKey: true});
  assert.equal(h.context.predictCalls, 3);
  assert.equal(calls, 1);
  assert.equal(h.state.points[0].x, 10);
});

test('Cmd+A predicts and Cmd+Shift+A selects the whole image', async () => {
  let calls = 0;
  const h = shortcutHarness(async () => { calls++; return twoCandidates; });
  const predictEvent = await h.press({key: 'a', metaKey: true});
  assert.equal(predictEvent.defaultPrevented, true);
  assert.equal(h.context.predictCalls, 1);
  assert.equal(calls, 1);
  assert.equal(h.state.points[0].x, 10);
  const selectEvent = await h.press({key: 'A', metaKey: true, shiftKey: true});
  assert.equal(selectEvent.defaultPrevented, true);
  assert.equal(h.context.selectCalls, 1);
  assert.equal(h.context.predictCalls, 1);
  assert.equal(h.state.points[0].x, 0);
  assert.equal(h.state.points[1].x, 599);
});

test('Ctrl+A while a predict is in flight does not start a second request', async () => {
  let release;
  let calls = 0;
  const h = shortcutHarness(() => { calls++; return new Promise((resolve) => { release = resolve; }); });
  h.dispatch({key: 'a', ctrlKey: true});
  assert.equal(calls, 1);
  const second = h.dispatch({key: 'a', ctrlKey: true});
  assert.equal(second.defaultPrevented, true);
  assert.equal(calls, 1);
  release(twoCandidates);
  await h.flush();
  assert.equal(h.state.points[0].x, 10);
});

test('Ctrl+Shift+A while a predict is in flight still selects the whole image', async () => {
  let release;
  const h = shortcutHarness(() => new Promise((resolve) => { release = resolve; }));
  h.dispatch({key: 'a', ctrlKey: true});
  const event = h.dispatch({key: 'A', ctrlKey: true, shiftKey: true});
  assert.equal(event.defaultPrevented, true);
  assert.equal(h.context.selectCalls, 1);
  assert.equal(h.state.points[0].x, 0);
  assert.equal(h.state.points[1].x, 599);
  release(twoCandidates);
  await h.flush();
  assert.equal(h.state.points[0].x, 0);
  assert.equal(h.state.points[1].x, 599);
});

test('a late predict after Whole image does not overwrite the corners', async () => {
  let releaseShortcut;
  const viaShortcut = shortcutHarness(() => new Promise((resolve) => { releaseShortcut = resolve; }));
  viaShortcut.dispatch({key: 'a', ctrlKey: true});
  viaShortcut.dispatch({key: 'A', ctrlKey: true, shiftKey: true});
  assert.equal(viaShortcut.state.points[0].x, 0);
  releaseShortcut(twoCandidates);
  await viaShortcut.flush();
  assert.equal(viaShortcut.state.points[0].x, 0);
  assert.equal(viaShortcut.state.points[1].x, 599);

  let releaseButton;
  const viaButton = shortcutHarness(() => new Promise((resolve) => { releaseButton = resolve; }));
  viaButton.dispatch({key: 'a', ctrlKey: true});
  viaButton.context.selectWholeImage();
  assert.equal(viaButton.state.points[0].x, 0);
  releaseButton(twoCandidates);
  await viaButton.flush();
  assert.equal(viaButton.state.points[0].x, 0);
  assert.equal(viaButton.state.points[1].x, 599);
});

test('shortcut strip and tooltips mention P and W, not Ctrl+A', () => {
  assert.match(html, /<kbd>P<\/kbd> predict corners/);
  assert.match(html, /<kbd>W<\/kbd> whole image/);
  assert.doesNotMatch(html, /<kbd>Ctrl<\/kbd><kbd>A<\/kbd> predict corners/);
  assert.doesNotMatch(html, /<kbd>Ctrl<\/kbd><kbd>Shift<\/kbd><kbd>A<\/kbd> whole image/);
  assert.match(html, /id="predict-corners" title="Predict the photo corners locally \(P\)"/);
  assert.match(html, /id="select-whole" title="Select the full image boundary \(W\)"/);
});

test('P calls predict', async () => {
  let calls = 0;
  const h = shortcutHarness(async () => { calls++; return twoCandidates; });
  const event = await h.press({key: 'p'});
  assert.equal(event.defaultPrevented, true);
  assert.equal(h.context.predictCalls, 1);
  assert.equal(calls, 1);
  assert.equal(h.state.points[0].x, 10);
  const upper = await h.press({key: 'P'});
  assert.equal(upper.defaultPrevented, true);
  assert.equal(h.context.predictCalls, 2);
  assert.equal(calls, 1);
  assert.equal(h.state.points[0].x, 320);
});

test('P cycles cached candidates and wraps to the first', async () => {
  let calls = 0;
  const h = shortcutHarness(async () => { calls++; return twoCandidates; });
  await h.press({key: 'p'});
  await h.press({key: 'p'});
  assert.equal(h.state.points[0].x, 320);
  await h.press({key: 'p'});
  assert.equal(h.context.predictCalls, 3);
  assert.equal(calls, 1);
  assert.equal(h.state.points[0].x, 10);
});

test('W selects the whole image', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  const event = await h.press({key: 'w'});
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

test('W after an in-flight predict keeps the whole-image corners', async () => {
  let release;
  const h = shortcutHarness(() => new Promise((resolve) => { release = resolve; }));
  h.dispatch({key: 'p'});
  const event = h.dispatch({key: 'w'});
  assert.equal(event.defaultPrevented, true);
  assert.equal(h.context.selectCalls, 1);
  assert.equal(h.state.points[0].x, 0);
  assert.equal(h.state.points[1].x, 599);
  release(twoCandidates);
  await h.flush();
  assert.equal(h.state.points[0].x, 0);
  assert.equal(h.state.points[1].x, 599);
});

test('Shift+P and Ctrl+P do not predict', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  const shiftP = await h.press({key: 'P', shiftKey: true});
  const ctrlP = await h.press({key: 'p', ctrlKey: true});
  const altP = await h.press({key: 'p', altKey: true});
  const metaP = await h.press({key: 'p', metaKey: true});
  assert.equal(shiftP.defaultPrevented, false);
  assert.equal(ctrlP.defaultPrevented, false);
  assert.equal(altP.defaultPrevented, false);
  assert.equal(metaP.defaultPrevented, false);
  assert.equal(h.context.predictCalls, 0);
  assert.equal(h.context.selectCalls, 0);
  assert.equal(h.state.points.length, 0);
});

test('P and W with focus in a text input are ignored', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  h.setActive({tagName: 'INPUT', isContentEditable: false});
  const predictEvent = await h.press({key: 'p'});
  const selectEvent = await h.press({key: 'w'});
  assert.equal(predictEvent.defaultPrevented, false);
  assert.equal(selectEvent.defaultPrevented, false);
  assert.equal(h.context.predictCalls, 0);
  assert.equal(h.context.selectCalls, 0);
  assert.equal(h.state.points.length, 0);
});

test('P and W are ignored while a dialog is open', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  h.setOpenDialog({tagName: 'DIALOG'});
  const withDialogP = await h.press({key: 'p'});
  const withDialogW = await h.press({key: 'w'});
  assert.equal(withDialogP.defaultPrevented, false);
  assert.equal(withDialogW.defaultPrevented, false);
  assert.equal(h.context.predictCalls, 0);
  h.setOpenDialog(null);
  h.setFolderModalHidden(false);
  const withModalP = await h.press({key: 'p'});
  const withModalW = await h.press({key: 'w'});
  assert.equal(withModalP.defaultPrevented, false);
  assert.equal(withModalW.defaultPrevented, false);
  assert.equal(h.context.predictCalls, 0);
  assert.equal(h.context.selectCalls, 0);
});

test('P and W are no-ops while busy', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  h.state.busy = true;
  const predictEvent = await h.press({key: 'p'});
  const selectEvent = await h.press({key: 'w'});
  assert.equal(predictEvent.defaultPrevented, false);
  assert.equal(selectEvent.defaultPrevented, false);
  assert.equal(h.context.predictCalls, 0);
  assert.equal(h.context.selectCalls, 0);
  assert.equal(h.state.points.length, 0);
});

test('P and W are ignored outside the crop view', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  h.state.view = 'chatgpt';
  const predictEvent = await h.press({key: 'p'});
  const selectEvent = await h.press({key: 'w'});
  assert.equal(predictEvent.defaultPrevented, false);
  assert.equal(selectEvent.defaultPrevented, false);
  assert.equal(h.context.predictCalls, 0);
  assert.equal(h.context.selectCalls, 0);
  assert.equal(h.state.points.length, 0);
});

test('a held P (event.repeat) does not spam predict', async () => {
  let calls = 0;
  const h = shortcutHarness(async () => { calls++; return twoCandidates; });
  await h.press({key: 'p', repeat: true});
  assert.equal(h.context.predictCalls, 0);
  assert.equal(calls, 0);
  await h.press({key: 'p'});
  await h.press({key: 'p', repeat: true});
  assert.equal(h.context.predictCalls, 1);
  assert.equal(calls, 1);
  assert.equal(h.state.points[0].x, 10);
});

test('P, W, Ctrl+A and the buttons do nothing while an image is loading', async () => {
  let releaseInfo;
  const posted = [];
  const h = shortcutHarness((url, body) => {
    posted.push([url, body]);
    if (String(url).startsWith('/api/info')) return new Promise((resolve) => { releaseInfo = resolve; });
    return twoCandidates;
  });
  h.state.images = [
    {path: 'a.jpg', name: 'a.jpg', bytes: 10, modified: 1},
    {path: 'b.jpg', name: 'b.jpg', bytes: 20, modified: 2},
  ];
  const opening = h.context.openImage(1);
  await Promise.resolve();
  assert.equal(h.state.width, 0);
  assert.equal(h.els['image-layer'].classList.contains('hidden'), true);
  await h.press({key: 'p'});
  await h.press({key: 'w'});
  await h.press({key: 'a', ctrlKey: true});
  await h.press({key: 'A', ctrlKey: true, shiftKey: true});
  h.context.predictCropCorners();
  h.context.selectWholeImage();
  await h.flush();
  assert.equal(posted.some((entry) => String(entry[0]).includes('crop-corners')), false);
  assert.equal(h.state.points.length, 0);
  assert.equal(h.state.width, 0);
  releaseInfo({width: 800, height: 500});
  await opening;
});

test('a predict started on image A is dropped after image B is opened', async () => {
  let release;
  const h = shortcutHarness(() => new Promise((resolve) => { release = resolve; }));
  h.state.images = [
    {path: 'a.jpg', name: 'a.jpg', bytes: 10, modified: 1},
    {path: 'b.jpg', name: 'b.jpg', bytes: 20, modified: 2},
  ];
  h.dispatch({key: 'p'});
  h.state.index = 1;
  h.state.width = 800;
  h.state.height = 500;
  release(twoCandidates);
  await h.flush();
  assert.equal(h.state.points.length, 0);
});

test('IME composition does not predict or preventDefault', async () => {
  let calls = 0;
  const h = shortcutHarness(async () => { calls++; return twoCandidates; });
  const composing = await h.press({key: 'p', isComposing: true});
  const ime = await h.press({key: 'p', keyCode: 229});
  assert.equal(composing.defaultPrevented, false);
  assert.equal(ime.defaultPrevented, false);
  assert.equal(h.context.predictCalls, 0);
  assert.equal(calls, 0);
  assert.equal(h.state.points.length, 0);
});

test('P, W and Ctrl+A are ignored in a textarea or select', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  for (const tag of ['TEXTAREA', 'SELECT']) {
    h.setActive({tagName: tag, isContentEditable: false});
    const p = await h.press({key: 'p'});
    const w = await h.press({key: 'w'});
    const ctrlA = await h.press({key: 'a', ctrlKey: true});
    assert.equal(p.defaultPrevented, false, tag);
    assert.equal(w.defaultPrevented, false, tag);
    assert.equal(ctrlA.defaultPrevented, false, tag);
  }
  assert.equal(h.context.predictCalls, 0);
  assert.equal(h.context.selectCalls, 0);
  assert.equal(h.state.points.length, 0);
});

test('changing Output proportions then P still predicts', async () => {
  let calls = 0;
  const h = shortcutHarness(async () => { calls++; return twoCandidates; });
  h.bindSettings();
  h.setActive(h.cropAspect);
  h.cropAspect.value = '2:3';
  h.cropAspect.dispatch('input');
  const event = await h.press({key: 'p'});
  assert.equal(event.defaultPrevented, true);
  assert.equal(h.context.predictCalls, 1);
  assert.equal(calls, 1);
  assert.equal(h.state.points[0].x, 10);
});

test('ticking Auto-next then P still predicts', async () => {
  let calls = 0;
  const h = shortcutHarness(async () => { calls++; return twoCandidates; });
  h.bindSettings();
  h.setActive(h.autoNext);
  h.autoNext.checked = false;
  h.autoNext.dispatch('input');
  const event = await h.press({key: 'p'});
  assert.equal(event.defaultPrevented, true);
  assert.equal(h.context.predictCalls, 1);
  assert.equal(calls, 1);
  assert.equal(h.state.points[0].x, 10);
});
