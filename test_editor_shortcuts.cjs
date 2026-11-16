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
    isContentEditable: extra.isContentEditable || false,
    disabled: false,
    href: extra.href,
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
    getAttribute(name) {
      if (name === 'role') return extra.role ?? null;
      if (name === 'href') return extra.href ?? null;
      if (name === 'tabindex') return extra.tabindex != null ? String(extra.tabindex) : null;
      return extra[name] != null ? extra[name] : null;
    },
    querySelector: () => ({textContent: ''}),
    addEventListener(type, fn) { (el._listeners[type] ||= []).push(fn); },
    dispatch(type, extra = {}) {
      const event = {currentTarget: el, target: el, defaultPrevented: false, preventDefault() { event.defaultPrevented = true; }, stopPropagation() { event._stopped = true; }, ...extra};
      for (const fn of el._listeners[type] || []) fn(event);
      return event;
    },
  };
  return el;
}

function shortcutHarness(api) {
  const state = {
    index: 0, width: 600, height: 400, busy: false, requestId: 1, folderLoadId: 0, points: [],
    rotation: 0,
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
  const straightenBowed = makeControl('INPUT', {type: 'checkbox', checked: true});
  const cropAspectCustom = makeControl('INPUT', {type: 'text', value: '2:3'});
  const imageLayer = makeControl('DIV');
  const els = {
    'crop-aspect': cropAspect,
    'auto-next': autoNext,
    'straighten-bowed-edges': straightenBowed,
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
  straightenBowed.blur = () => { if (active === straightenBowed) active = body; };
  cropAspectCustom.blur = () => { cropAspectCustom.blurred = true; if (active === cropAspectCustom) active = body; };
  const context = {
    state,
    settings: {autoNext: true, aspect: 'auto', customAspect: '2:3', straightenBowedEdges: true},
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
        if (sel === '#auto-next, #straighten-bowed-edges, #crop-aspect, #crop-aspect-custom') return [autoNext, straightenBowed, cropAspect, cropAspectCustom];
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
    Map,
    AbortController,
    JSON,
    Set,
    Promise,
    startZoom() {},
    openImage() {},
    cropSave() { context.saveCalls += 1; },
    closePicker() {},
  };
  vm.createContext(context);
  if (code.includes('function releaseCropShortcutFocus')) {
    vm.runInContext(extractBlock(code, 'function releaseCropShortcutFocus') + ';', context);
  }
  if (code.includes('function cropEnterActivatesFocusedControl')) {
    vm.runInContext(extractBlock(code, 'function cropEnterActivatesFocusedControl') + ';', context);
  }
  vm.runInContext(extractBlock(code, 'function saveSettings') + ';', context);
  vm.runInContext(extractBlock(code, 'async function openImage') + ';', context);
  vm.runInContext(
    code.slice(code.indexOf('const CROP_PREDICTION_CACHE_LIMIT'), code.indexOf('async function cropSave(')),
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
  context.undoCalls = 0;
  context.rotateCalls = 0;
  context.openCalls = 0;
  context.saveCalls = 0;
  context.clearCalls = 0;
  if (typeof context.undoCropPoint === 'function') {
    const realUndo = context.undoCropPoint;
    context.undoCropPoint = function (...args) {
      context.undoCalls += 1;
      return realUndo(...args);
    };
  }
  if (typeof context.rotateCrop === 'function') {
    const realRotate = context.rotateCrop;
    context.rotateCrop = function (...args) {
      context.rotateCalls += 1;
      return realRotate(...args);
    };
  }
  if (typeof context.clearCropPoints === 'function') {
    const realClear = context.clearCropPoints;
    context.clearCropPoints = function (...args) {
      context.clearCalls += 1;
      return realClear(...args);
    };
  }
  const realOpen = context.openImage;
  context.openImage = function (...args) {
    context.openCalls += 1;
    return realOpen(...args);
  };
  function makeKeyEvent(partial) {
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
      stopPropagation() { event._stopped = true; },
      ...partial,
    };
    return event;
  }
  function dispatch(partial) {
    const event = makeKeyEvent(partial);
    const focused = active;
    if (focused && focused._listeners && focused._listeners.keydown) {
      for (const fn of focused._listeners.keydown) {
        fn(event);
        if (event._stopped) return event;
      }
    }
    listeners[0](event);
    return event;
  }
  function dispatchDocument(partial) {
    const event = makeKeyEvent(partial);
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
    state, messages, press, dispatch, dispatchDocument, flush,
    els, cropAspect, autoNext, cropAspectCustom, straightenBowed, body,
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

test('shortcut strip and tooltips mention P, W, Z, C, R, A, D and Enter', () => {
  assert.match(html, /<kbd>P<\/kbd> predict corners/);
  assert.match(html, /<kbd>W<\/kbd> whole image/);
  assert.match(html, /<kbd>Z<\/kbd> undo/);
  assert.match(html, /<kbd>C<\/kbd> clear points/);
  assert.match(html, /<kbd>R<\/kbd> rotate/);
  assert.match(html, /<kbd>A<\/kbd> previous/);
  assert.match(html, /<kbd>D<\/kbd> next/);
  assert.match(html, /<kbd>ENTER<\/kbd> save/);
  assert.doesNotMatch(html, /<kbd>S<\/kbd>/);
  assert.doesNotMatch(html, /<kbd>Ctrl<\/kbd><kbd>A<\/kbd> predict corners/);
  assert.doesNotMatch(html, /<kbd>Ctrl<\/kbd><kbd>Shift<\/kbd><kbd>A<\/kbd> whole image/);
  assert.match(html, /id="predict-corners" title="Predict the photo corners locally \(P\)"/);
  assert.match(html, /id="select-whole" title="Select the full image boundary \(W\)"/);
  assert.match(html, /id="undo" title="Undo last point \(Z\)"/);
  assert.match(html, /id="reset" title="Reset points and rotation"/);
  assert.match(html, /id="rotate-right" title="Rotate crop right \(R\)"/);
  assert.match(html, /id="prev" title="Previous image \(A\)"/);
  assert.match(html, /id="next" title="Next image \(D\)"/);
  assert.match(html, /id="save" title="Crop and save \(Enter\)"/);
  assert.match(code, /\$\('undo'\)\.addEventListener\('click',undoCropPoint\)/);
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
  h.cropAspect.dispatch('pointerdown');
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

test('straighten bowed edges defaults on and turning it off still allows P', async () => {
  let calls = 0;
  const h = shortcutHarness(async () => { calls++; return twoCandidates; });
  h.bindSettings();
  assert.equal(h.context.settings.straightenBowedEdges, true);
  h.setActive(h.straightenBowed);
  h.straightenBowed.checked = false;
  h.straightenBowed.dispatch('input');
  assert.equal(h.context.settings.straightenBowedEdges, false);
  const event = await h.press({key: 'p'});
  assert.equal(event.defaultPrevented, true);
  assert.equal(h.context.predictCalls, 1);
  assert.equal(calls, 1);
});

const letterKeys = [
  {key: 'c'},
  {key: 'z'},
  {key: 'r'},
  {key: 'a'},
  {key: 'd'},
  {key: 'Enter'},
];

function twoImages() {
  return [
    {path: 'a.jpg', name: 'a.jpg', bytes: 10, modified: 1},
    {path: 'b.jpg', name: 'b.jpg', bytes: 20, modified: 2},
  ];
}

test('C clears crop points through clearCropPoints', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  h.state.points = [{x: 1, y: 1}, {x: 2, y: 2}, {x: 3, y: 3}];
  const event = await h.press({key: 'c'});
  assert.equal(event.defaultPrevented, true);
  assert.equal(h.context.clearCalls, 1);
  assert.equal(h.state.points.length, 0);
  const upper = await h.press({key: 'C'});
  assert.equal(upper.defaultPrevented, true);
  assert.equal(h.context.clearCalls, 2);
});

test('Z, Ctrl+Z and the Undo button all go through the shared undo', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  assert.equal(typeof h.context.undoCropPoint, 'function');
  h.state.points = [{x: 1, y: 1}, {x: 2, y: 2}, {x: 3, y: 3}];
  const z = await h.press({key: 'z'});
  assert.equal(z.defaultPrevented, true);
  assert.equal(h.context.undoCalls, 1);
  assert.equal(h.state.points.length, 2);
  const ctrlZ = await h.press({key: 'z', ctrlKey: true});
  assert.equal(ctrlZ.defaultPrevented, true);
  assert.equal(h.context.undoCalls, 2);
  assert.equal(h.state.points.length, 1);
  h.context.undoCropPoint();
  assert.equal(h.context.undoCalls, 3);
  assert.equal(h.state.points.length, 0);
});

test('R rotates the crop clockwise', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  const event = await h.press({key: 'r'});
  assert.equal(event.defaultPrevented, true);
  assert.equal(h.context.rotateCalls, 1);
  assert.equal(h.state.rotation, 90);
  await h.press({key: 'R'});
  assert.equal(h.state.rotation, 180);
  await h.press({key: 'r'});
  assert.equal(h.state.rotation, -90);
  await h.press({key: 'r'});
  assert.equal(h.state.rotation, 0);
});

test('Enter saves through cropSave', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  const event = await h.press({key: 'Enter'});
  assert.equal(event.defaultPrevented, true);
  assert.equal(h.context.saveCalls, 1);
});

test('A and D move images and drop unplaced points', async () => {
  const h = shortcutHarness((url) => {
    if (String(url).startsWith('/api/info')) return {width: 600, height: 400};
    return twoCandidates;
  });
  h.state.images = twoImages();
  h.state.index = 1;
  h.state.points = [{x: 10, y: 10}, {x: 20, y: 20}];
  const prev = h.dispatch({key: 'a'});
  assert.equal(prev.defaultPrevented, true);
  assert.equal(h.context.openCalls, 1);
  assert.equal(h.state.index, 0);
  assert.equal(h.state.points.length, 0);
  h.state.width = 600;
  h.state.height = 400;
  h.els['image-layer'].classList.remove('hidden');
  h.state.points = [{x: 30, y: 30}];
  const next = h.dispatch({key: 'd'});
  assert.equal(next.defaultPrevented, true);
  assert.equal(h.context.openCalls, 2);
  assert.equal(h.state.index, 1);
  assert.equal(h.state.points.length, 0);
});

test('S is not bound as a crop shortcut', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  h.state.images = twoImages();
  h.state.points = [{x: 1, y: 1}];
  const event = await h.press({key: 's'});
  assert.equal(event.defaultPrevented, false);
  assert.equal(h.state.points.length, 1);
  assert.equal(h.state.index, 0);
  assert.equal(h.state.rotation, 0);
  assert.equal(h.context.clearCalls, 0);
  assert.equal(h.context.undoCalls, 0);
  assert.equal(h.context.rotateCalls, 0);
  assert.equal(h.context.openCalls, 0);
  assert.equal(h.context.saveCalls, 0);
});

test('shared undo cancels an in-flight predict so a late result is dropped', async () => {
  let release;
  const h = shortcutHarness(() => new Promise((resolve) => { release = resolve; }));
  h.state.points = [{x: 1, y: 1}, {x: 2, y: 2}];
  h.dispatch({key: 'p'});
  const event = h.dispatch({key: 'z'});
  assert.equal(event.defaultPrevented, true);
  assert.equal(h.context.undoCalls, 1);
  assert.equal(h.state.points.length, 1);
  release(twoCandidates);
  await h.flush();
  assert.equal(h.state.points.length, 1);
  assert.equal(h.state.points[0].x, 1);
});

test('Ctrl+Z uses the shared undo and drops a late predict', async () => {
  let release;
  const h = shortcutHarness(() => new Promise((resolve) => { release = resolve; }));
  h.state.points = [{x: 1, y: 1}, {x: 2, y: 2}];
  h.dispatch({key: 'p'});
  const event = h.dispatch({key: 'z', ctrlKey: true});
  assert.equal(event.defaultPrevented, true);
  assert.equal(h.context.undoCalls, 1);
  assert.equal(h.state.points.length, 1);
  release(twoCandidates);
  await h.flush();
  assert.equal(h.state.points.length, 1);
  assert.equal(h.state.points[0].x, 1);
});

test('Backspace clears through clearCropPoints and drops a late predict', async () => {
  let release;
  const h = shortcutHarness(() => new Promise((resolve) => { release = resolve; }));
  h.state.points = [{x: 1, y: 1}, {x: 2, y: 2}];
  h.dispatch({key: 'p'});
  const event = h.dispatch({key: 'Backspace'});
  assert.equal(event.defaultPrevented, true);
  assert.equal(h.context.clearCalls, 1);
  assert.equal(h.state.points.length, 0);
  release(twoCandidates);
  await h.flush();
  assert.equal(h.state.points.length, 0);
});

test('Ctrl+Q still clears and Ctrl+A / Ctrl+Shift+A are unchanged with letter keys bound', async () => {
  let calls = 0;
  const h = shortcutHarness(async () => { calls++; return twoCandidates; });
  h.state.images = twoImages();
  h.state.points = [{x: 8, y: 8}];
  const ctrlC = await h.press({key: 'c', ctrlKey: true});
  assert.equal(ctrlC.defaultPrevented, false);
  assert.equal(h.state.points.length, 1);
  const ctrlQ = await h.press({key: 'q', ctrlKey: true});
  assert.equal(ctrlQ.defaultPrevented, true);
  assert.equal(h.context.clearCalls, 1);
  assert.equal(h.state.points.length, 0);
  const ctrlA = await h.press({key: 'a', ctrlKey: true});
  assert.equal(ctrlA.defaultPrevented, true);
  assert.equal(h.context.predictCalls, 1);
  assert.equal(calls, 1);
  assert.equal(h.context.openCalls, 0);
  const ctrlShiftA = await h.press({key: 'A', ctrlKey: true, shiftKey: true});
  assert.equal(ctrlShiftA.defaultPrevented, true);
  assert.equal(h.context.selectCalls, 1);
  assert.equal(h.state.points[0].x, 0);
  const ctrlZ = await h.press({key: 'z', ctrlKey: true});
  assert.equal(ctrlZ.defaultPrevented, true);
  assert.equal(h.context.undoCalls, 1);
  assert.equal(h.state.points.length, 3);
});

test('letter shortcuts ignore modifiers', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  h.state.images = twoImages();
  h.state.points = [{x: 1, y: 1}];
  for (const {key} of letterKeys) {
    for (const extra of [{shiftKey: true}, {altKey: true}, {ctrlKey: true}, {metaKey: true}]) {
      if (key === 'z' && (extra.ctrlKey || extra.metaKey)) continue;
      if (key === 'a' && (extra.ctrlKey || extra.metaKey)) continue;
      const event = await h.press({key, ...extra});
      assert.equal(event.defaultPrevented, false, `${key} ${JSON.stringify(extra)}`);
    }
  }
  assert.equal(h.context.clearCalls, 0);
  assert.equal(h.context.rotateCalls, 0);
  assert.equal(h.context.openCalls, 0);
  assert.equal(h.context.saveCalls, 0);
  assert.equal(h.state.points.length, 1);
  assert.equal(h.state.rotation, 0);
  assert.equal(h.state.index, 0);
});

test('letter shortcuts ignore event.repeat', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  h.state.images = twoImages();
  h.state.points = [{x: 1, y: 1}, {x: 2, y: 2}];
  for (const {key} of letterKeys) {
    const event = await h.press({key, repeat: true});
    assert.equal(event.defaultPrevented, false, key);
  }
  assert.equal(h.context.clearCalls, 0);
  assert.equal(h.context.undoCalls, 0);
  assert.equal(h.context.rotateCalls, 0);
  assert.equal(h.context.openCalls, 0);
  assert.equal(h.context.saveCalls, 0);
  assert.equal(h.state.points.length, 2);
  assert.equal(h.state.rotation, 0);
});

test('letter shortcuts are ignored in input, textarea, select and contenteditable', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  h.state.images = twoImages();
  h.state.points = [{x: 1, y: 1}];
  for (const active of [
    {tagName: 'INPUT', isContentEditable: false},
    {tagName: 'TEXTAREA', isContentEditable: false},
    {tagName: 'SELECT', isContentEditable: false},
    {tagName: 'DIV', isContentEditable: true},
  ]) {
    h.setActive(active);
    for (const {key} of letterKeys) {
      const event = await h.press({key});
      assert.equal(event.defaultPrevented, false, `${active.tagName} ${key}`);
    }
  }
  assert.equal(h.context.clearCalls, 0);
  assert.equal(h.context.undoCalls, 0);
  assert.equal(h.context.rotateCalls, 0);
  assert.equal(h.context.openCalls, 0);
  assert.equal(h.context.saveCalls, 0);
  assert.equal(h.state.points.length, 1);
});

test('letter shortcuts are ignored while a dialog is open', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  h.state.images = twoImages();
  h.state.points = [{x: 1, y: 1}];
  h.setOpenDialog({tagName: 'DIALOG'});
  for (const {key} of letterKeys) {
    const event = await h.press({key});
    assert.equal(event.defaultPrevented, false, key);
  }
  h.setOpenDialog(null);
  h.setFolderModalHidden(false);
  for (const {key} of letterKeys) {
    const event = await h.press({key});
    assert.equal(event.defaultPrevented, false, `modal ${key}`);
  }
  assert.equal(h.context.clearCalls, 0);
  assert.equal(h.context.undoCalls, 0);
  assert.equal(h.context.rotateCalls, 0);
  assert.equal(h.context.openCalls, 0);
  assert.equal(h.context.saveCalls, 0);
});

test('letter shortcuts are no-ops while busy', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  h.state.images = twoImages();
  h.state.points = [{x: 1, y: 1}];
  h.state.busy = true;
  for (const {key} of letterKeys) {
    const event = await h.press({key});
    assert.equal(event.defaultPrevented, false, key);
  }
  assert.equal(h.context.clearCalls, 0);
  assert.equal(h.context.undoCalls, 0);
  assert.equal(h.context.rotateCalls, 0);
  assert.equal(h.context.openCalls, 0);
  assert.equal(h.context.saveCalls, 0);
  assert.equal(h.state.points.length, 1);
  assert.equal(h.state.rotation, 0);
});

test('letter shortcuts are ignored while an image is loading', async () => {
  let releaseInfo;
  const h = shortcutHarness((url) => {
    if (String(url).startsWith('/api/info')) return new Promise((resolve) => { releaseInfo = resolve; });
    return twoCandidates;
  });
  h.state.images = twoImages();
  const opening = h.context.openImage(1);
  await Promise.resolve();
  assert.equal(h.state.width, 0);
  h.state.points = [{x: 4, y: 4}];
  h.state.rotation = 0;
  for (const {key} of letterKeys) {
    const event = await h.press({key});
    assert.equal(event.defaultPrevented, false, key);
  }
  assert.equal(h.context.clearCalls, 0);
  assert.equal(h.context.undoCalls, 0);
  assert.equal(h.context.rotateCalls, 0);
  assert.equal(h.context.saveCalls, 0);
  assert.equal(h.context.openCalls, 1);
  assert.equal(h.state.index, 1);
  assert.equal(h.state.points.length, 1);
  assert.equal(h.state.rotation, 0);
  releaseInfo({width: 800, height: 500});
  await opening;
});

test('letter shortcuts are ignored during IME composition', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  h.state.images = twoImages();
  h.state.points = [{x: 1, y: 1}];
  for (const {key} of letterKeys) {
    const composing = await h.press({key, isComposing: true});
    const ime = await h.press({key, keyCode: 229});
    assert.equal(composing.defaultPrevented, false, key);
    assert.equal(ime.defaultPrevented, false, key);
  }
  assert.equal(h.context.clearCalls, 0);
  assert.equal(h.context.undoCalls, 0);
  assert.equal(h.context.rotateCalls, 0);
  assert.equal(h.context.openCalls, 0);
  assert.equal(h.context.saveCalls, 0);
  assert.equal(h.state.points.length, 1);
});

test('letter shortcuts are ignored outside the crop view', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  h.state.images = twoImages();
  h.state.points = [{x: 1, y: 1}];
  h.state.view = 'chatgpt';
  for (const {key} of letterKeys) {
    const event = await h.press({key});
    assert.equal(event.defaultPrevented, false, key);
  }
  assert.equal(h.context.clearCalls, 0);
  assert.equal(h.context.undoCalls, 0);
  assert.equal(h.context.rotateCalls, 0);
  assert.equal(h.context.openCalls, 0);
  assert.equal(h.context.saveCalls, 0);
  assert.equal(h.state.points.length, 1);
  assert.equal(h.state.index, 0);
});

test('image load failure with a matching ticket resets width and shows empty state', async () => {
  let updateCalls = 0;
  const h = shortcutHarness((url) => {
    if (String(url).startsWith('/api/info')) return {width: 800, height: 500};
    return twoCandidates;
  });
  h.context.updateControls = function () { updateCalls += 1; };
  h.context.assignImage = async () => { throw new Error('decode failed'); };
  h.state.images = twoImages();
  h.state.width = 600;
  h.state.height = 400;
  h.els['empty-state'].classList.add('hidden');
  h.els['image-layer'].classList.remove('hidden');
  await h.context.openImage(1);
  assert.equal(h.state.width, 0);
  assert.equal(h.state.height, 0);
  assert.equal(h.els['image-layer'].classList.contains('hidden'), true);
  assert.equal(h.els['empty-state'].classList.contains('hidden'), false);
  assert.ok(updateCalls >= 2);
  assert.match(h.messages[0] || '', /decode failed/);
});

test('W, Whole image, Ctrl+Shift+A, P and Ctrl+A are blocked between info and assignImage', async () => {
  let releaseAssign;
  const posted = [];
  const h = shortcutHarness((url, body) => {
    posted.push([url, body]);
    if (String(url).startsWith('/api/info')) return {width: 800, height: 500};
    return twoCandidates;
  });
  h.context.assignImage = () => new Promise((resolve) => { releaseAssign = resolve; });
  h.state.images = twoImages();
  const opening = h.context.openImage(1);
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(h.els['image-layer'].classList.contains('hidden'), true);
  assert.equal(h.state.width, 0);
  await h.press({key: 'w'});
  await h.press({key: 'A', ctrlKey: true, shiftKey: true});
  h.context.selectWholeImage();
  await h.press({key: 'p'});
  await h.press({key: 'a', ctrlKey: true});
  await h.flush();
  assert.equal(h.context.selectCalls, 3);
  assert.equal(h.state.points.length, 0);
  assert.equal(posted.some((entry) => String(entry[0]).includes('crop-corners')), false);
  releaseAssign();
  await opening;
  assert.equal(h.state.width, 800);
  assert.equal(h.els['image-layer'].classList.contains('hidden'), false);
});

test('typing in crop-aspect-custom keeps focus', () => {
  const h = shortcutHarness(async () => twoCandidates);
  const field = h.els['crop-aspect-custom'];
  h.bindSettings();
  h.setActive(field);
  field.value = '4:5';
  field.dispatch('input');
  assert.equal(field.blurred, undefined);
  assert.equal(h.context.document.activeElement, field);
  assert.equal(h.context.settings.customAspect, '4:5');
});

test('two ArrowDowns on Output proportions keep focus and step 2:3 to 3:4 then 4:5', () => {
  const h = shortcutHarness(async () => twoCandidates);
  h.bindSettings();
  h.setActive(h.cropAspect);
  h.cropAspect.value = '2:3';
  h.cropAspect.dispatch('keydown', {key: 'ArrowDown'});
  h.cropAspect.value = '3:4';
  h.cropAspect.dispatch('input');
  assert.equal(h.context.document.activeElement, h.cropAspect);
  assert.equal(h.context.settings.aspect, '3:4');
  const blocked = h.dispatch({key: 'p'});
  assert.equal(blocked.defaultPrevented, false);
  h.cropAspect.dispatch('keydown', {key: 'ArrowDown'});
  h.cropAspect.value = '4:5';
  h.cropAspect.dispatch('input');
  assert.equal(h.context.document.activeElement, h.cropAspect);
  assert.equal(h.context.settings.aspect, '4:5');
});

test('a pointer-driven Output proportions change releases focus so P and W work', async () => {
  let calls = 0;
  const h = shortcutHarness(async () => { calls++; return twoCandidates; });
  h.bindSettings();
  h.setActive(h.cropAspect);
  h.cropAspect.value = '2:3';
  h.cropAspect.dispatch('pointerdown');
  h.cropAspect.value = '3:4';
  h.cropAspect.dispatch('input');
  assert.equal(h.context.document.activeElement, h.body);
  const predictEvent = await h.press({key: 'p'});
  assert.equal(predictEvent.defaultPrevented, true);
  assert.equal(h.context.predictCalls, 1);
  assert.equal(calls, 1);
  h.setActive(h.cropAspect);
  h.cropAspect.dispatch('mousedown');
  h.cropAspect.value = '4:5';
  h.cropAspect.dispatch('input');
  assert.equal(h.context.document.activeElement, h.body);
  const selectEvent = await h.press({key: 'w'});
  assert.equal(selectEvent.defaultPrevented, true);
  assert.equal(h.context.selectCalls, 1);
});

test('document handler SELECT guard skips save and navigation for Enter and Escape', () => {
  const h = shortcutHarness(async () => twoCandidates);
  h.bindSettings();
  h.state.images = twoImages();
  h.state.index = 1;
  h.setActive(h.cropAspect);
  const enter = h.dispatchDocument({key: 'Enter', target: h.cropAspect});
  assert.equal(enter.defaultPrevented, false);
  assert.equal(h.context.saveCalls, 0);
  assert.equal(h.context.openCalls, 0);
  assert.equal(h.context.document.activeElement, h.cropAspect);
  const escape = h.dispatchDocument({key: 'Escape', target: h.cropAspect});
  assert.equal(escape.defaultPrevented, false);
  assert.equal(h.context.saveCalls, 0);
  assert.equal(h.context.openCalls, 0);
  assert.equal(h.context.document.activeElement, h.cropAspect);
});

test('Enter and Escape on Output proportions go through the document handler, release focus, and do not save', () => {
  const h = shortcutHarness(async () => twoCandidates);
  h.bindSettings();
  h.setActive(h.cropAspect);
  const enter = h.dispatch({key: 'Enter'});
  assert.equal(enter.defaultPrevented, true);
  assert.equal(h.context.document.activeElement, h.body);
  assert.equal(h.context.saveCalls, 0);
  h.setActive(h.cropAspect);
  const escape = h.dispatch({key: 'Escape'});
  assert.equal(escape.defaultPrevented, true);
  assert.equal(h.context.document.activeElement, h.body);
  assert.equal(h.context.saveCalls, 0);
});

test('pointerdown then two ArrowDowns keep Output proportions focus and step twice', () => {
  const h = shortcutHarness(async () => twoCandidates);
  h.bindSettings();
  h.setActive(h.cropAspect);
  h.cropAspect.value = '2:3';
  h.cropAspect.dispatch('pointerdown');
  h.cropAspect.dispatch('keydown', {key: 'ArrowDown'});
  h.cropAspect.value = '3:4';
  h.cropAspect.dispatch('input');
  assert.equal(h.context.document.activeElement, h.cropAspect);
  assert.equal(h.context.settings.aspect, '3:4');
  h.cropAspect.dispatch('keydown', {key: 'ArrowDown'});
  h.cropAspect.value = '4:5';
  h.cropAspect.dispatch('input');
  assert.equal(h.context.document.activeElement, h.cropAspect);
  assert.equal(h.context.settings.aspect, '4:5');
});

test('Auto-next still blurs after a change', () => {
  const h = shortcutHarness(async () => twoCandidates);
  h.bindSettings();
  h.setActive(h.autoNext);
  h.autoNext.checked = false;
  h.autoNext.dispatch('input');
  assert.equal(h.context.document.activeElement, h.body);
  assert.equal(h.context.settings.autoNext, false);
});

test('Enter on a focused crop button activates that button and does not save', () => {
  const h = shortcutHarness(async () => twoCandidates);
  const button = makeControl('BUTTON');
  let clicks = 0;
  button.addEventListener('click', () => { clicks += 1; });
  h.setActive(button);
  const event = h.dispatch({key: 'Enter'});
  assert.equal(event.defaultPrevented, false);
  assert.equal(h.context.saveCalls, 0);
  if (!event.defaultPrevented) button.dispatch('click');
  assert.equal(clicks, 1);
});

test('Backspace with a modifier, with repeat, or while loading does not clear points', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  h.state.points = [{x: 1, y: 1}];
  const alt = await h.press({key: 'Backspace', altKey: true});
  const shift = await h.press({key: 'Backspace', shiftKey: true});
  assert.equal(alt.defaultPrevented, false);
  assert.equal(shift.defaultPrevented, false);
  assert.equal(h.state.points.length, 1);
  assert.equal(h.context.clearCalls, 0);
  let releaseInfo;
  const loading = shortcutHarness((url) => {
    if (String(url).startsWith('/api/info')) return new Promise((resolve) => { releaseInfo = resolve; });
    return twoCandidates;
  });
  loading.state.images = twoImages();
  const opening = loading.context.openImage(1);
  await Promise.resolve();
  loading.state.points = [{x: 4, y: 4}];
  const duringLoad = await loading.press({key: 'Backspace'});
  assert.equal(loading.state.points.length, 1);
  assert.equal(loading.context.clearCalls, 0);
  assert.equal(duringLoad.defaultPrevented, true);
  releaseInfo({width: 800, height: 500});
  await opening;
});

test('repeat Backspace preventDefaults and does not clear points', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  h.state.points = [{x: 1, y: 1}];
  const event = await h.press({key: 'Backspace', repeat: true});
  assert.equal(event.defaultPrevented, true);
  assert.equal(h.context.clearCalls, 0);
  assert.equal(h.state.points.length, 1);
});

test('Enter with the crop canvas or a tabindex wrapper focused still saves', () => {
  const h = shortcutHarness(async () => twoCandidates);
  const canvas = makeControl('DIV', {tabindex: '0'});
  h.setActive(canvas);
  const canvasEvent = h.dispatch({key: 'Enter'});
  assert.equal(canvasEvent.defaultPrevented, true);
  assert.equal(h.context.saveCalls, 1);
  const wrapper = makeControl('DIV', {tabindex: '0'});
  h.setActive(wrapper);
  const wrapperEvent = h.dispatch({key: 'Enter'});
  assert.equal(wrapperEvent.defaultPrevented, true);
  assert.equal(h.context.saveCalls, 2);
});

test('Backspace in crop view still preventDefaults', async () => {
  const h = shortcutHarness(async () => twoCandidates);
  h.state.points = [{x: 1, y: 1}];
  const event = await h.press({key: 'Backspace'});
  assert.equal(event.defaultPrevented, true);
  assert.equal(h.context.clearCalls, 1);
  assert.equal(h.state.points.length, 0);
});

test('a stale load failure shows no toast and does not reset the current image', async () => {
  let fail;
  const h = shortcutHarness((url) => {
    if (String(url).startsWith('/api/info')) return {width: 800, height: 500};
    return twoCandidates;
  });
  h.context.assignImage = () => new Promise((_, reject) => { fail = reject; });
  h.state.images = twoImages();
  const opening = h.context.openImage(0);
  await Promise.resolve();
  await Promise.resolve();
  h.state.requestId += 1;
  h.state.width = 900;
  h.state.height = 700;
  h.els['image-layer'].classList.remove('hidden');
  h.els['empty-state'].classList.add('hidden');
  fail(new Error('decode failed'));
  await opening;
  assert.equal(h.state.width, 900);
  assert.equal(h.state.height, 700);
  assert.equal(h.els['image-layer'].classList.contains('hidden'), false);
  assert.equal(h.els['empty-state'].classList.contains('hidden'), true);
  assert.equal(h.messages.length, 0);
});

test('switching photo aborts in-flight predict and does not leave Finding corners stuck', async () => {
  let abortSeen = false;
  const h = shortcutHarness((url, body, options) => {
    if (String(url).includes('/api/crop-corners')) {
      return new Promise((_, reject) => {
        const signal = options && options.signal;
        const fail = () => {
          abortSeen = true;
          const error = new Error('The operation was aborted.');
          error.name = 'AbortError';
          reject(error);
        };
        if (!signal) return;
        if (signal.aborted) { fail(); return; }
        signal.addEventListener('abort', fail);
      });
    }
    if (String(url).startsWith('/api/info')) return {width: 800, height: 500};
    if (String(url).includes('crop-prefetch')) return {queued: 0};
    return {};
  });
  const predictSpan = {textContent: 'Predict corners'};
  h.els['predict-corners'].querySelector = () => predictSpan;
  h.context.updateControls = function () {
    predictSpan.textContent = vm.runInContext('cropPredictionBusy', h.context) ? 'Finding corners…' : 'Predict corners';
  };
  h.context.assignImage = async () => {};
  h.state.images = twoImages();
  h.state.width = 600;
  h.state.height = 400;
  h.els['image-layer'].classList.remove('hidden');
  const pending = h.context.predictCropCorners();
  await Promise.resolve();
  assert.equal(vm.runInContext('cropPredictionBusy', h.context), true);
  assert.equal(predictSpan.textContent, 'Finding corners…');
  const opening = h.context.openImage(1);
  await pending;
  assert.equal(abortSeen, true);
  assert.equal(vm.runInContext('cropPredictionBusy', h.context), false);
  assert.equal(predictSpan.textContent, 'Predict corners');
  assert.equal(h.state.points.length, 0);
  assert.equal(h.messages.length, 0);
  await opening;
  assert.equal(h.state.index, 1);
  assert.equal(h.state.points.length, 0);
});
