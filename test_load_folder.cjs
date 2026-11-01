const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync(require('node:path').join(__dirname, 'app.js'), 'utf8');

function loadFolderHarness({ api, refresh, openImage }) {
  const els = {};
  const stub = () => ({
    textContent: '',
    value: '',
    disabled: false,
    classList: { add() {}, remove() {}, toggle() {} },
    replaceChildren() {},
    querySelector() { return { textContent: '' }; },
    scrollTop: 0,
  });
  const state = {
    folder: '',
    parent: null,
    images: [],
    folders: [],
    index: -1,
    points: [],
    rotation: 0,
    busy: false,
    folderLoadId: 0,
    chatgpt: {
      busy: false,
      reviewBusy: false,
      queuedPaths: new Set(),
      selected: new Set(),
      pending: [],
      recovered: [],
      preview: null,
      review: null,
    },
    enhance: { busy: false, selected: new Set() },
    restore: { busy: false, selected: new Set(), notNeeded: new Set(), marks: new Map() },
    view: 'crop',
  };
  let cleared = false;
  const posts = [];
  const context = {
    state,
    settings: { autoNext: false, aspect: 'auto' },
    localStorage: { setItem() {}, getItem() { return null; } },
    colorRequest: 0,
    colorScanning: false,
    colorScanFolder: '',
    colorCandidates: new Map(),
    selectedPairs: new Set(),
    $: (id) => els[id] ||= stub(),
    toast() {},
    renderFolders() {},
    renderImages() {},
    renderMerge() {},
    renderOverlay() {},
    clearChatgptPreview() {},
    updateChatgptControls() {},
    clearEnhancePreview() {},
    renderEnhanceControls() {},
    clearRestorationPreview() {},
    renderRestorationControls() {},
    clearEditor() {
      cleared = true;
      state.points = [];
      state.rotation = 0;
    },
    updateControls() {},
    validSettings() { return ''; },
    ensureOverwriteConfirmed: async () => ({}),
    refreshStageFolders: async () => {},
    cropAspectRatio() { return 'auto'; },
    stageRoutingEnabled() { return true; },
    offerChooseOutputFolder() { return false; },
    refreshChatgptPending: refresh,
    openImage,
    api: async (route, body) => {
      if (route === '/api/crop') posts.push(body);
      return api(route, body);
    },
  };
  const start = app.indexOf('async function loadFolder');
  const end = app.indexOf('\nfunction renderFolders');
  const orderedStart = app.indexOf('function ordered(');
  const orderedEnd = app.indexOf('\nfunction renderOverlay');
  const cropStart = app.indexOf('async function cropSave');
  const cropEnd = app.indexOf('\nfunction clearLiveCropPreview');
  const loadFolder = vm.runInNewContext(`${app.slice(start, end)}\nloadFolder`, context);
  const cropSave = vm.runInNewContext(
    `${app.slice(orderedStart, orderedEnd)}\n${app.slice(cropStart, cropEnd)}\ncropSave`,
    context,
  );
  return { loadFolder, cropSave, state, posts, wasCleared: () => cleared };
}

test('stale loadFolder cannot clear a photo opened during refreshChatgptPending', async () => {
  let releasePending;
  const pending = new Promise((resolve) => { releasePending = resolve; });
  let listed = false;
  const { loadFolder, state, wasCleared } = loadFolderHarness({
    api: async (route) => {
      if (route === '/api/begin-batch') return { batch_id: 'batch-1' };
      if (String(route).startsWith('/api/list')) {
        listed = true;
        return {
          path: '/photos',
          parent: null,
          images: [{ path: '/photos/a.png', name: 'a.png', bytes: 12 }],
          folders: [],
        };
      }
      throw new Error(`unexpected ${route}`);
    },
    refresh: async () => { await pending; },
    openImage: async (index) => {
      state.folderLoadId += 1;
      state.index = index;
    },
  });
  const loading = loadFolder('/photos');
  const deadline = Date.now() + 1000;
  while (!listed && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(listed, true);
  state.folderLoadId += 1;
  state.index = 0;
  releasePending();
  await loading;
  assert.equal(state.index, 0);
  assert.equal(wasCleared(), false);
});

test('a photo opened after loadFolder began is never cleared', async () => {
  let releasePending;
  const pending = new Promise((resolve) => { releasePending = resolve; });
  let listed = false;
  const { loadFolder, state, wasCleared } = loadFolderHarness({
    api: async (route) => {
      if (String(route).startsWith('/api/begin-batch')) return { batch_id: 'batch-1' };
      if (String(route).startsWith('/api/list')) {
        listed = true;
        return {
          path: '/photos',
          parent: null,
          images: [{ path: '/photos/a.png', name: 'a.png', bytes: 12 }],
          folders: [],
        };
      }
      throw new Error(`unexpected ${route}`);
    },
    refresh: async () => { await pending; },
    openImage: async (index) => { state.index = index; },
  });
  const loading = loadFolder('/photos');
  const deadline = Date.now() + 1000;
  while (!listed && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(listed, true);
  state.index = 0;
  releasePending();
  await loading;
  assert.equal(state.index, 0);
  assert.equal(wasCleared(), false);
});

test('changing folder clears crop points so save cannot write them onto the new file', async () => {
  const oldPoints = [
    { x: 0, y: 0 }, { x: 15, y: 0 }, { x: 15, y: 15 }, { x: 0, y: 15 },
  ];
  const { loadFolder, cropSave, state, posts, wasCleared } = loadFolderHarness({
    api: async (route) => {
      if (String(route).startsWith('/api/begin-batch')) return { batch_id: 'batch-2' };
      if (String(route).startsWith('/api/list')) {
        return {
          path: '/photos/sub',
          parent: '/photos',
          images: [{ path: '/photos/sub/b.png', name: 'b.png', bytes: 20 }],
          folders: [],
        };
      }
      if (route === '/api/crop') return { name: 'b_c.png', width: 16, height: 16 };
      throw new Error(`unexpected ${route}`);
    },
    refresh: async () => {},
    openImage: async (index) => { state.index = index; },
  });
  state.folder = '/photos';
  state.parent = null;
  state.images = [{ path: '/photos/a.png', name: 'a.png', bytes: 12 }];
  state.index = 0;
  state.points = oldPoints.map((point) => ({ ...point }));
  await loadFolder('/photos/sub');
  assert.equal(wasCleared(), true);
  assert.equal(state.index, -1);
  assert.deepEqual(state.points, []);
  await cropSave();
  assert.deepEqual(posts, []);
  assert.notEqual(state.images[0]?.path, '/photos/a.png');
});
