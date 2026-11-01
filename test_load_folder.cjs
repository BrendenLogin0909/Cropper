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
  const context = {
    state,
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
    clearChatgptPreview() {},
    updateChatgptControls() {},
    clearEnhancePreview() {},
    renderEnhanceControls() {},
    clearRestorationPreview() {},
    renderRestorationControls() {},
    clearEditor() {
      cleared = true;
      state.index = -1;
    },
    updateControls() {},
    refreshChatgptPending: refresh,
    openImage,
    api,
  };
  const start = app.indexOf('async function loadFolder');
  const end = app.indexOf('\nfunction renderFolders');
  const loadFolder = vm.runInNewContext(`${app.slice(start, end)}\nloadFolder`, context);
  return { loadFolder, state, wasCleared: () => cleared };
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
