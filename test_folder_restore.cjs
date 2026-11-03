const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync(require('node:path').join(__dirname, 'app.js'), 'utf8');
const html = fs.readFileSync(require('node:path').join(__dirname, 'index.html'), 'utf8');
const COPY = 'Cropper needs you to pick this folder again before it can open your photos.';

function classListStub(el) {
  return {
    add(...names) { names.forEach((name) => { el._classes.add(name); }); },
    remove(...names) { names.forEach((name) => { el._classes.delete(name); }); },
    toggle(name, force) {
      const on = force == null ? !el._classes.has(name) : !!force;
      if (on) el._classes.add(name); else el._classes.delete(name);
    },
    contains(name) { return el._classes.has(name); },
  };
}

function element(extra = {}) {
  const el = {
    textContent: '',
    innerHTML: '',
    value: '',
    disabled: false,
    hidden: false,
    _classes: new Set(),
    children: [],
    dataset: {},
    scrollTop: 0,
    querySelector(selector) {
      if (selector === 'p' || selector === '#empty-state-copy') {
        return extra.copy || this;
      }
      if (selector === '.image-tile') return this.children.find((child) => child.className === 'image-tile') || null;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === 'img[data-object-url]') return [];
      if (selector === '.image-tile') return this.children.filter((child) => child.className === 'image-tile');
      return [];
    },
    replaceChildren(...nodes) { this.children = nodes; },
    append(...nodes) { this.children.push(...nodes); },
    addEventListener() {},
  };
  el.classList = classListStub(el);
  return Object.assign(el, extra);
}

function restoreHarness({ api, loadFolder } = {}) {
  const els = {
    'empty-state': element(),
    'empty-state-copy': element(),
    'empty-remembered-path': element({ _classes: new Set(['hidden']) }),
    'empty-open': element({
      textContent: 'Open image folder →',
      innerHTML: 'Open image folder <span>→</span>',
      className: 'button button-primary',
    }),
    'folder-path': element(),
    'folder-count': element({ textContent: '0' }),
    'image-count': element({ textContent: '0' }),
    images: element(),
    folders: element(),
    'image-layer': element({ _classes: new Set(['hidden']) }),
    'image-title': element(),
    'image-subtitle': element(),
    position: element(),
    photo: element(),
    toast: element(),
  };
  els['empty-state'].querySelector = (selector) => {
    if (selector === 'p' || selector === '#empty-state-copy') return els['empty-state-copy'];
    return null;
  };
  const storage = {};
  const toasts = [];
  const routes = [];
  const state = {
    folder: '',
    parent: null,
    images: [],
    folders: [],
    index: -1,
    folderNeedsPick: '',
    folderLoadId: 0,
    token: 'tok',
  };
  const context = {
    state,
    localStorage: {
      getItem(key) { return Object.prototype.hasOwnProperty.call(storage, key) ? storage[key] : null; },
      setItem(key, value) { storage[key] = String(value); },
    },
    $: (id) => els[id] || (els[id] = element()),
    toast(message) { toasts.push(message); },
    revokeDatasetUrl() {},
    clearEditor() {
      els['empty-state'].classList.remove('hidden');
      els['image-layer'].classList.add('hidden');
      const copy = state.folderNeedsPick
        ? COPY
        : (state.folder ? 'Double-click an image in the explorer to begin.' : 'Pick an image folder to start your batch.');
      els['empty-state-copy'].textContent = copy;
      els['image-subtitle'].textContent = copy;
    },
    renderFolders() {
      els.folders.replaceChildren();
    },
    renderImages() {
      els.images.replaceChildren();
      for (const image of state.images) {
        els.images.append({ className: 'image-tile', title: image.path });
      }
    },
    loadFolder: loadFolder || (async () => { throw new Error('loadFolder should not run'); }),
    api: async (route, body) => {
      routes.push(route);
      return api(route, body);
    },
  };
  const start = app.indexOf('const FOLDER_NEEDS_PICK_COPY');
  const end = app.indexOf('\nasync function loadFolder');
  assert.notEqual(start, -1, 'restore helpers are missing from app.js');
  assert.notEqual(end, -1);
  vm.runInNewContext(
    `${app.slice(start, end)}
exported = {
  FOLDER_NEEDS_PICK_COPY,
  isOutsideAllowedFolder,
  showFolderNeedsPick,
  handleOutsideAllowedFolder,
  restoreRememberedFolder,
};`,
    Object.assign(context, { exported: {}, URL: { revokeObjectURL() {} } }),
  );
  return { ...context.exported, state, els, storage, toasts, routes };
}

test('restoring an unregistered folder shows the prompt and zero tiles', async () => {
  assert.match(html, /id="empty-remembered-path"/);
  assert.match(html, /id="empty-open"/);
  assert.match(app, /\/api\/folder-status/);
  const remembered = '/photos/batch';
  const { restoreRememberedFolder, state, els, storage, routes } = restoreHarness({
    api: async (route) => {
      if (String(route).startsWith('/api/folder-status')) return { registered: false };
      throw new Error(`unexpected ${route}`);
    },
  });
  storage['cropper-last-folder'] = remembered;
  els.images.replaceChildren({ className: 'image-tile', title: 'stale' });
  await restoreRememberedFolder();
  assert.equal(routes.length, 1);
  assert.match(routes[0], /\/api\/folder-status\?path=/);
  assert.equal(state.images.length, 0);
  assert.equal(els.images.querySelectorAll('.image-tile').length, 0);
  assert.equal(els['empty-state-copy'].textContent, COPY);
  assert.equal(els['empty-remembered-path'].textContent, remembered);
  assert.equal(els['empty-remembered-path'].classList.contains('hidden'), false);
  assert.equal(els['folder-path'].value, remembered);
  assert.equal(els['empty-open'].textContent, 'Browse…');
  assert.equal(els['empty-open'].classList.contains('button-primary'), true);
  assert.equal(state.folderNeedsPick, remembered);
});

test('outside allowed folder on thumb or open switches to the re-pick empty state once', () => {
  const remembered = '/photos/batch';
  const { handleOutsideAllowedFolder, showFolderNeedsPick, state, els, toasts } = restoreHarness({
    api: async () => ({ registered: false }),
  });
  state.folder = remembered;
  state.images = [
    { path: `${remembered}/a.jpg`, name: 'a.jpg' },
    { path: `${remembered}/b.jpg`, name: 'b.jpg' },
  ];
  els.images.replaceChildren(
    { className: 'image-tile', title: 'a.jpg' },
    { className: 'image-tile', title: 'b.jpg' },
  );
  const first = handleOutsideAllowedFolder(new Error('That image is outside the allowed folder.'));
  const second = handleOutsideAllowedFolder(new Error('That image is outside the allowed folder.'));
  assert.equal(first, true);
  assert.equal(second, true);
  assert.equal(toasts.length, 0);
  assert.equal(state.images.length, 0);
  assert.equal(els.images.querySelectorAll('.image-tile').length, 0);
  assert.equal(els['empty-state-copy'].textContent, COPY);
  assert.equal(els['empty-remembered-path'].textContent, remembered);
  assert.equal(els['empty-open'].textContent, 'Browse…');
  showFolderNeedsPick(remembered);
  assert.equal(els.images.querySelectorAll('.image-tile').length, 0);
});
