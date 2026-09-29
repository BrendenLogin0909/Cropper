const $ = id => document.getElementById(id);
const state = { token: '', home: '', folder: '', parent: null, images: [], folders: [], index: -1, points: [], width: 0, height: 0, rotation: 0, zoom: false, drag: -1, pointer: null, busy: false, requestId: 0, picker: null, pickerRequest: 0, view: 'crop', merge: { front: -1, back: -1, frontInfo: null, backInfo: null, busy: false }, enhance: { selected: new Set(), preview: -1, requestId: 0, previewUrl: null, busy: false, stop: false } };
const stored = JSON.parse(localStorage.getItem('cropper-settings') || '{}');
const settings = { mode: stored.mode || 'subfolder', subfolder: stored.subfolder || 'Cropped', folder: stored.folder || '', prefix: stored.prefix || '', suffix: stored.suffix || '', autoNext: stored.autoNext !== false, aspect: stored.aspect || 'auto', customAspect: stored.customAspect || '2:3' };
const storedMerge = JSON.parse(localStorage.getItem('cropper-merge-settings') || '{}');
const mergeSettings = { mode: storedMerge.mode || 'subfolder', subfolder: storedMerge.subfolder || 'Front and Back', folder: storedMerge.folder || '', prefix: storedMerge.prefix || '', suffix: storedMerge.suffix || '_front-back', rotation: String(storedMerge.rotation || 0), nameSource: storedMerge.nameSource || 'back' };
const storedEnhance = JSON.parse(localStorage.getItem('cropper-enhance-settings') || '{}');
const enhanceSettings = { mode: storedEnhance.mode || 'subfolder', subfolder: storedEnhance.subfolder || 'Enhanced', folder: storedEnhance.folder || '', prefix: storedEnhance.prefix || '', suffix: storedEnhance.suffix === undefined ? '_enhanced' : storedEnhance.suffix };
let toastTimer;
let cropPreviewUrl = null;

function toast(message, error = false) {
  const element = $('toast'); element.textContent = message; element.classList.toggle('error', error); element.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => element.classList.remove('show'), 4200);
}
function formatBytes(bytes) { return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`; }
function url(path, filePath) { return `${path}?path=${encodeURIComponent(filePath)}&token=${encodeURIComponent(state.token)}`; }
async function api(path, body) {
  const response = await fetch(path, { method: body === undefined ? 'GET' : 'POST', headers: { 'X-Cropper-Token': state.token, ...(body === null ? {} : body === undefined ? {} : { 'Content-Type': 'application/json' }) }, ...(body === undefined || body === null ? {} : { body: JSON.stringify(body) }) });
  const data = await response.json(); if (!response.ok) { const error = new Error(data.error || 'Something went wrong.'); error.code = data.code; error.path = data.path; throw error; } return data;
}
function saveSettings() {
  settings.mode = document.querySelector('input[name="mode"]:checked').value;
  settings.subfolder = $('subfolder').value.trim(); settings.folder = $('output-folder').value.trim();
  settings.prefix = $('prefix').value; settings.suffix = $('suffix').value; settings.autoNext = $('auto-next').checked;
  settings.aspect = $('crop-aspect').value; settings.customAspect = $('crop-aspect-custom').value.trim();
  localStorage.setItem('cropper-settings', JSON.stringify(settings));
  $('subfolder-field').classList.toggle('hidden', settings.mode !== 'subfolder');
  $('folder-field').classList.toggle('hidden', settings.mode !== 'folder');
  $('naming-fields').classList.toggle('hidden', settings.mode === 'replace');
  $('crop-aspect-custom-field').classList.toggle('hidden', settings.aspect !== 'custom');
  $('save-error').classList.add('hidden');
  updateControls();
}
function loadSettings() {
  (document.querySelector(`input[name="mode"][value="${settings.mode}"]`) || document.querySelector('input[value="subfolder"]')).checked = true;
  $('subfolder').value = settings.subfolder; $('output-folder').value = settings.folder;
  $('prefix').value = settings.prefix; $('suffix').value = settings.suffix; $('auto-next').checked = settings.autoNext;
  $('crop-aspect').value = settings.aspect; $('crop-aspect-custom').value = settings.customAspect;
  document.querySelectorAll('input[name="mode"], #subfolder, #output-folder, #prefix, #suffix, #auto-next, #crop-aspect, #crop-aspect-custom').forEach(input => input.addEventListener('input', saveSettings));
  saveSettings();
}
function validSettings() {
  if (settings.aspect === 'custom' && !parseAspect(settings.customAspect)) return 'Enter a custom proportion such as 2:3.';
  if (settings.mode === 'subfolder' && !settings.subfolder) return 'Enter a subfolder name.';
  if (settings.mode === 'folder' && !settings.folder) return 'Choose an output folder.';
  if (settings.mode === 'beside' && !settings.prefix && !settings.suffix) return 'Add a prefix or suffix for copies beside the original.';
  return '';
}
function parseAspect(value) {
  const match = String(value).trim().match(/^(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)$/);
  if (!match) return null;
  const ratio = Number(match[1]) / Number(match[2]);
  return Number.isFinite(ratio) && ratio >= 0.2 && ratio <= 5 ? ratio : null;
}
function cropAspectRatio() {
  if (settings.aspect === 'auto') return null;
  if (settings.aspect === 'custom') return parseAspect(settings.customAspect);
  const ratio = parseAspect(settings.aspect);
  if (!ratio) return null;
  const corners = ordered(state.points);
  if (!corners) return ratio;
  const distance = (a,b) => Math.hypot(a.x-b.x,a.y-b.y);
  const width = Math.max(distance(corners[0],corners[1]),distance(corners[2],corners[3]));
  const height = Math.max(distance(corners[1],corners[2]),distance(corners[3],corners[0]));
  return width >= height ? Math.max(ratio,1/ratio) : Math.min(ratio,1/ratio);
}
function saveMergeSettings() {
  mergeSettings.mode = document.querySelector('input[name="merge-mode"]:checked').value;
  mergeSettings.subfolder = $('merge-subfolder').value.trim(); mergeSettings.folder = $('merge-output-folder').value.trim();
  mergeSettings.prefix = $('merge-prefix').value; mergeSettings.suffix = $('merge-suffix').value; mergeSettings.rotation = $('merge-rotation').value;
  mergeSettings.nameSource = document.querySelector('input[name="merge-name-source"]:checked').value;
  localStorage.setItem('cropper-merge-settings', JSON.stringify(mergeSettings));
  $('merge-subfolder-field').classList.toggle('hidden', mergeSettings.mode !== 'subfolder');
  $('merge-folder-field').classList.toggle('hidden', mergeSettings.mode !== 'folder');
  renderMerge();
}
function loadMergeSettings() {
  (document.querySelector(`input[name="merge-mode"][value="${mergeSettings.mode}"]`) || document.querySelector('input[name="merge-mode"]')).checked = true;
  (document.querySelector(`input[name="merge-name-source"][value="${mergeSettings.nameSource}"]`) || document.querySelector('input[name="merge-name-source"]')).checked = true;
  $('merge-subfolder').value = mergeSettings.subfolder; $('merge-output-folder').value = mergeSettings.folder;
  $('merge-prefix').value = mergeSettings.prefix; $('merge-suffix').value = mergeSettings.suffix; $('merge-rotation').value = mergeSettings.rotation;
  document.querySelectorAll('input[name="merge-mode"], input[name="merge-name-source"], #merge-subfolder, #merge-output-folder, #merge-prefix, #merge-suffix, #merge-rotation').forEach(input => input.addEventListener('input', saveMergeSettings));
  saveMergeSettings();
}
function validMergeSettings() {
  if (mergeSettings.mode === 'subfolder' && !mergeSettings.subfolder) return 'Enter a subfolder name.';
  if (mergeSettings.mode === 'folder' && !mergeSettings.folder) return 'Choose an output folder.';
  if (mergeSettings.mode === 'beside' && !mergeSettings.prefix && !mergeSettings.suffix) return 'Add a prefix or suffix for copies beside the original.';
  return '';
}
function saveEnhanceSettings() {
  enhanceSettings.mode = document.querySelector('input[name="enhance-mode"]:checked').value;
  enhanceSettings.subfolder = $('enhance-subfolder').value.trim(); enhanceSettings.folder = $('enhance-output-folder').value.trim();
  enhanceSettings.prefix = $('enhance-prefix').value; enhanceSettings.suffix = $('enhance-suffix').value;
  localStorage.setItem('cropper-enhance-settings', JSON.stringify(enhanceSettings));
  $('enhance-subfolder-field').classList.toggle('hidden', enhanceSettings.mode !== 'subfolder');
  $('enhance-folder-field').classList.toggle('hidden', enhanceSettings.mode !== 'folder');
  renderEnhanceControls();
}
function loadEnhanceSettings() {
  (document.querySelector(`input[name="enhance-mode"][value="${enhanceSettings.mode}"]`) || document.querySelector('input[name="enhance-mode"]')).checked = true;
  $('enhance-subfolder').value = enhanceSettings.subfolder; $('enhance-output-folder').value = enhanceSettings.folder;
  $('enhance-prefix').value = enhanceSettings.prefix; $('enhance-suffix').value = enhanceSettings.suffix;
  document.querySelectorAll('input[name="enhance-mode"], #enhance-subfolder, #enhance-output-folder, #enhance-prefix, #enhance-suffix').forEach(input => input.addEventListener('input', saveEnhanceSettings));
  saveEnhanceSettings();
}
function validEnhanceSettings() {
  if (enhanceSettings.mode === 'subfolder' && !enhanceSettings.subfolder) return 'Enter a subfolder name.';
  if (enhanceSettings.mode === 'folder' && !enhanceSettings.folder) return 'Choose an output folder.';
  if (enhanceSettings.mode === 'beside' && !enhanceSettings.prefix && !enhanceSettings.suffix) return 'Add a prefix or suffix for copies beside the original.';
  return '';
}
function setView(view) {
  state.view = view;
  for (const name of ['crop', 'merge', 'enhance']) {
    const active = view === name;
    $(`${name}-workspace`).classList.toggle('hidden', !active); $(`${name}-settings`).classList.toggle('hidden', !active);
    $(`${name}-tab`).classList.toggle('active', active); $(`${name}-tab`).setAttribute('aria-selected', String(active));
  }
  $('explorer-foot').textContent = view === 'enhance' ? 'Click photos to select and preview' : view === 'merge' ? 'Click front, then back' : '↕ Browse files · double-click to open';
  renderImages(); if (view === 'merge') renderMerge(); if (view === 'enhance') renderEnhanceControls();
}
function selectionCard(label, image) {
  const card = document.createElement('div'); card.className = 'merge-card';
  const tag = document.createElement('span'); tag.textContent = label;
  const picture = document.createElement('img'); picture.alt = `${label}: ${image.name}`; picture.src = url('/api/image', image.path) + `&v=${image.modified}`;
  card.append(tag, picture); return card;
}
function renderMerge() {
  const { front, back, frontInfo, busy } = state.merge;
  const ready = front >= 0 && back >= 0; const problem = validMergeSettings();
  $('merge-save').disabled = !ready || busy || !!problem;
  $('merge-save-hint').textContent = busy ? 'Saving merged image…' : problem || (ready ? 'Ready to merge these two photos' : 'Select a front and a back to enable saving');
  const instructions = $('merge-instructions'); const preview = $('merge-preview'); preview.replaceChildren();
  if (front < 0) { instructions.classList.remove('hidden'); instructions.innerHTML = '<span class="merge-step-number">1</span><strong>Select the front</strong><small>Its shape decides whether the back goes beside or below it.</small>'; preview.classList.add('hidden'); $('merge-summary').textContent = 'Choose the front photo, then the back photo.'; return; }
  if (back < 0) { instructions.classList.remove('hidden'); instructions.innerHTML = '<span class="merge-step-number">2</span><strong>Now select its back</strong><small>Click the matching back image in the Explorer.</small>'; preview.classList.add('hidden'); $('merge-summary').textContent = `Front: ${state.images[front].name}. Select the back next.`; return; }
  instructions.classList.add('hidden'); preview.classList.remove('hidden');
  const stacked = !!frontInfo && frontInfo.width >= frontInfo.height; preview.classList.toggle('stack', stacked);
  preview.append(selectionCard('FRONT', state.images[front]));
  const divider = document.createElement('div'); divider.className = 'merge-divider'; divider.textContent = stacked ? '↓' : '→'; preview.append(divider);
  preview.append(selectionCard('BACK', state.images[back]));
  $('merge-summary').textContent = stacked ? 'Landscape front: the back will be placed below it.' : 'Portrait front: the back will be placed to its right.';
}
async function selectMergeImage(index) {
  if (index < 0 || index >= state.images.length || state.merge.busy) return;
  if (state.merge.front < 0 || state.merge.back >= 0) { state.merge.front = index; state.merge.back = -1; state.merge.backInfo = null; }
  else if (index !== state.merge.front) state.merge.back = index;
  else { toast('Choose a different image for the back.', true); return; }
  try {
    if (state.merge.front === index) state.merge.frontInfo = await api(`/api/info?path=${encodeURIComponent(state.images[index].path)}`);
    else state.merge.backInfo = await api(`/api/info?path=${encodeURIComponent(state.images[index].path)}`);
  } catch (error) { toast(error.message, true); }
  renderImages(); renderMerge();
}
async function mergeSave() {
  const { front, back } = state.merge; const problem = validMergeSettings();
  if (state.merge.busy || front < 0 || back < 0 || problem) { if (problem) toast(problem, true); return; }
  state.merge.busy = true; renderMerge();
  try {
    const result = await api('/api/merge', { front_path: state.images[front].path, back_path: state.images[back].path, back_rotation: Number(mergeSettings.rotation), settings: mergeSettings, name_source: mergeSettings.nameSource });
    toast(`Saved ${result.name} · ${result.width} × ${result.height} px`);
  } catch (error) { toast(error.message, true); }
  finally { state.merge.busy = false; renderMerge(); }
}
function renderEnhanceControls() {
  const count = state.enhance.selected.size;
  $('enhance-selection-count').textContent = `${count} selected`;
  $('enhance-save').disabled = !count || state.enhance.busy || !!validEnhanceSettings();
  $('enhance-save').firstElementChild.textContent = count === 1 ? 'Save enhanced copy' : `Save ${count} selected copies`;
  $('enhance-save-hint').textContent = state.enhance.busy ? 'Saving enhanced copies…' : validEnhanceSettings() || (count ? 'Review the preview, then save copies' : 'Select photos to enable saving');
  $('enhance-select-all').disabled = !state.images.length || state.enhance.busy;
  $('enhance-clear').disabled = !count || state.enhance.busy;
  $('enhance-stop').classList.toggle('hidden', !state.enhance.busy);
  $('enhance-stop').disabled = state.enhance.stop;
  $('enhance-stop').textContent = state.enhance.stop ? 'Stopping…' : 'Stop after current';
}
function refreshEnhanceTiles() {
  if (state.view !== 'enhance') return;
  document.querySelectorAll('#images .image-tile').forEach((tile, index) => {
    tile.classList.toggle('enhance-selected', state.enhance.selected.has(state.images[index].path));
    tile.classList.toggle('enhance-previewed', state.enhance.preview === index);
  });
}
function clearEnhancePreview() {
  ++state.enhance.requestId;
  if (state.enhance.previewUrl) URL.revokeObjectURL(state.enhance.previewUrl);
  state.enhance.previewUrl = null; state.enhance.preview = -1;
  $('enhance-before').removeAttribute('src'); $('enhance-after').removeAttribute('src');
  $('enhance-comparison').classList.add('hidden'); $('enhance-empty').classList.remove('hidden');
  $('enhance-subtitle').textContent = 'Open any folder, then select photos to preview and improve.';
  $('enhance-changes').textContent = 'Each image is analysed separately. Review the preview before saving.';
}
async function previewEnhance(index) {
  if (index < 0 || index >= state.images.length) return;
  const item = state.images[index]; const ticket = ++state.enhance.requestId;
  state.enhance.preview = index;
  $('enhance-empty').classList.add('hidden'); $('enhance-comparison').classList.remove('hidden');
  $('enhance-before').src = url('/api/enhance-original', item.path) + `&v=${item.modified}`;
  $('enhance-after').removeAttribute('src');
  $('enhance-subtitle').textContent = item.name;
  $('enhance-changes').textContent = 'Analysing this photo…';
  refreshEnhanceTiles();
  try {
    const response = await fetch(`/api/enhance-preview?path=${encodeURIComponent(item.path)}`, { headers: { 'X-Cropper-Token': state.token } });
    if (!response.ok) { const data = await response.json(); throw new Error(data.error || 'Could not generate the preview.'); }
    const changes = response.headers.get('X-Cropper-Adjustments') || 'Automatic correction applied';
    const blob = await response.blob();
    if (ticket !== state.enhance.requestId) return;
    if (state.enhance.previewUrl) URL.revokeObjectURL(state.enhance.previewUrl);
    state.enhance.previewUrl = URL.createObjectURL(blob);
    $('enhance-after').src = state.enhance.previewUrl;
    $('enhance-changes').textContent = changes === 'Already balanced' ? 'This photo appears balanced; no substantial correction was needed.' : `Applied to this photo: ${changes}. Preview may differ slightly from the saved file.`;
  } catch (error) { if (ticket === state.enhance.requestId) { $('enhance-changes').textContent = error.message; toast(error.message, true); } }
}
function selectEnhanceImage(index) {
  if (state.enhance.busy) return;
  const path = state.images[index].path;
  if (state.enhance.selected.has(path)) state.enhance.selected.delete(path); else state.enhance.selected.add(path);
  previewEnhance(index); renderEnhanceControls();
}
async function saveEnhancedBatch() {
  const problem = validEnhanceSettings();
  if (state.enhance.busy || !state.enhance.selected.size || problem) { if (problem) toast(problem, true); return; }
  const paths = state.images.map(image => image.path).filter(path => state.enhance.selected.has(path));
  const batchSettings = { ...enhanceSettings };
  state.enhance.busy = true; state.enhance.stop = false; renderEnhanceControls();
  let saved = 0; const failed = [];
  for (const [index, path] of paths.entries()) {
    if (state.enhance.stop) break;
    $('enhance-progress').textContent = `Saving ${index + 1} of ${paths.length}…`;
    try { await api('/api/enhance', { path, settings: batchSettings }); state.enhance.selected.delete(path); saved++; }
    catch (error) { failed.push(`${path.split(/[\\/]/).pop()}: ${error.message}`); if (error.code === 'permission_denied') state.enhance.stop = true; }
    refreshEnhanceTiles(); renderEnhanceControls();
  }
  const stopped = state.enhance.stop;
  state.enhance.busy = false; renderEnhanceControls();
  $('enhance-progress').textContent = `${saved} saved${failed.length ? ` · ${failed.length} failed` : ''}${stopped ? ' · batch stopped' : ''}`;
  if (failed.length) toast(failed[0], true); else toast(`${saved} enhanced ${saved === 1 ? 'copy' : 'copies'} saved${stopped ? ' before stopping' : ''}.`);
}
async function pickFolder(forOutput = false) {
  state.picker = { forOutput, path: (forOutput ? (forOutput === 'merge' ? mergeSettings.folder : forOutput === 'enhance' ? enhanceSettings.folder : settings.folder) : state.folder) || state.home, parent: null };
  $('folder-modal-title').textContent = forOutput ? 'Choose output folder' : 'Choose image folder';
  $('folder-modal').classList.remove('hidden');
  $('folder-modal-list').replaceChildren();
  $('folder-modal-message').textContent = 'Loading folders…';
  try {
    const locations = await api('/api/locations');
    for (const [target, items] of [['folder-shortcuts', locations.shortcuts], ['folder-drives', locations.drives]]) {
      const host = $(target); host.replaceChildren();
      for (const location of items) {
        const button = document.createElement('button'); button.textContent = location.name; button.title = location.path;
        button.addEventListener('click', () => loadPickerFolder(location.path)); host.append(button);
      }
    }
    await loadPickerFolder(state.picker.path);
  } catch (error) { $('folder-modal-message').textContent = error.message; $('folder-modal-message').classList.add('error'); }
}
async function loadPickerFolder(path) {
  if (!state.picker) return;
  const ticket = ++state.pickerRequest;
  $('folder-modal-message').textContent = 'Loading folders…'; $('folder-modal-message').classList.remove('error');
  try {
    const data = await api(`/api/folders?path=${encodeURIComponent(path)}`);
    if (ticket !== state.pickerRequest || !state.picker) return;
    state.picker.path = data.path; state.picker.parent = data.parent;
    $('folder-modal-path').value = data.path; $('folder-modal-current').textContent = data.path;
    $('folder-modal-up').disabled = !data.parent;
    const list = $('folder-modal-list'); list.replaceChildren();
    for (const folder of data.folders) {
      const button = document.createElement('button'); const glyph = document.createElement('span'); glyph.textContent = '▱';
      button.append(glyph, document.createTextNode(folder.name)); button.addEventListener('click', () => loadPickerFolder(folder.path)); list.append(button);
    }
    $('folder-modal-message').textContent = data.folders.length ? `${data.folders.length} folder${data.folders.length === 1 ? '' : 's'}` : 'No subfolders here. You can select this folder.';
  } catch (error) { $('folder-modal-message').textContent = error.message; $('folder-modal-message').classList.add('error'); }
}
function closePicker() { state.picker = null; ++state.pickerRequest; $('folder-modal').classList.add('hidden'); }
async function selectPickerFolder() {
  if (!state.picker) return;
  const { path, forOutput } = state.picker; closePicker();
  if (forOutput === 'merge') { $('merge-output-folder').value = path; saveMergeSettings(); }
  else if (forOutput === 'enhance') { $('enhance-output-folder').value = path; saveEnhanceSettings(); }
  else if (forOutput) { $('output-folder').value = path; saveSettings(); }
  else await loadFolder(path);
}
async function loadFolder(path, preserve = false) {
  if (state.enhance.busy) { toast('Stop or finish the enhancement batch before changing folders.', true); return; }
  try {
    const data = await api(`/api/list?path=${encodeURIComponent(path)}`);
    const prior = preserve && state.index >= 0 ? state.images[state.index]?.path : null;
    if (data.path !== state.folder) $('images').scrollTop = 0;
    state.folder = data.path; state.parent = data.parent; state.images = data.images; state.folders = data.folders;
    state.enhance.selected.clear(); clearEnhancePreview(); $('enhance-progress').textContent = ''; renderEnhanceControls();
    localStorage.setItem('cropper-last-folder', data.path); $('folder-path').value = data.path;
    $('folder-count').textContent = data.folders.length; $('image-count').textContent = data.images.length;
    $('go-parent').disabled = !data.parent;
    renderFolders(); renderImages();
    const priorIndex = data.images.findIndex(image => image.path === prior);
    if (priorIndex >= 0) await openImage(priorIndex);
    else { state.index = -1; clearEditor(); updateControls(); }
    state.merge = { front: -1, back: -1, frontInfo: null, backInfo: null, busy: false };
    if (state.view === 'merge') renderMerge();
    if (!data.images.length) toast('No supported images in this folder. Open a subfolder or choose another folder.');
  } catch (error) { toast(error.message, true); }
}
function renderFolders() {
  const host = $('folders'); host.replaceChildren();
  if (!state.folders.length) { const empty = document.createElement('div'); empty.className = 'no-items'; empty.textContent = 'No subfolders'; host.append(empty); return; }
  for (const folder of state.folders) {
    const item = document.createElement('button'); item.className = 'folder-item'; item.title = folder.path;
    const glyph = document.createElement('span'); glyph.textContent = '▱'; item.append(glyph, document.createTextNode(folder.name));
    item.addEventListener('click', () => loadFolder(folder.path)); host.append(item);
  }
}
function renderImages() {
  const host = $('images'); const previousScroll = host.scrollTop; host.replaceChildren();
  for (const [index, image] of state.images.entries()) {
    const tile = document.createElement('button'); tile.className = 'image-tile'; tile.title = state.view === 'enhance' ? `${image.name} — select and preview` : state.view === 'merge' ? `${image.name} — choose as front or back` : `${image.name} — double-click to open`;
    const picture = document.createElement('div'); picture.className = 'tile-photo';
    const thumbnail = document.createElement('img'); thumbnail.loading = 'lazy'; thumbnail.alt = ''; thumbnail.src = url('/api/thumb', image.path); picture.append(thumbnail);
    const name = document.createElement('div'); name.className = 'tile-name'; name.textContent = image.name;
    const meta = document.createElement('div'); meta.className = 'tile-meta'; meta.textContent = formatBytes(image.bytes);
    tile.append(picture, name, meta); tile.addEventListener('dblclick', () => { if (state.view === 'crop') openImage(index); });
    tile.addEventListener('click', () => { if (state.view === 'enhance') selectEnhanceImage(index); else if (state.view === 'merge') selectMergeImage(index); else { document.querySelectorAll('.image-tile.selected').forEach(el => el.classList.remove('selected')); tile.classList.add('selected'); } });
    tile.classList.toggle('merge-front', state.view === 'merge' && state.merge.front === index); tile.classList.toggle('merge-back', state.view === 'merge' && state.merge.back === index);
    tile.classList.toggle('enhance-selected', state.view === 'enhance' && state.enhance.selected.has(image.path));
    tile.classList.toggle('enhance-previewed', state.view === 'enhance' && state.enhance.preview === index);
    host.append(tile);
  }
  host.scrollTop = previousScroll;
}
function clearEditor() {
  state.points = []; state.rotation = 0; state.width = 0; state.height = 0; endZoom();
  $('image-layer').classList.add('hidden'); $('empty-state').classList.remove('hidden');
  $('image-title').textContent = 'Ready when you are'; $('image-subtitle').textContent = state.folder ? 'Double-click an image in the explorer to begin.' : 'Open a folder, then double-click an image to begin.';
  $('empty-state').querySelector('p').textContent = state.folder ? 'Double-click an image in the explorer to begin.' : 'Pick an image folder to start your batch.';
  $('position').textContent = `— / ${state.images.length || '—'}`; renderOverlay();
}
async function openImage(index) {
  if (index < 0 || index >= state.images.length) return;
  const ticket = ++state.requestId; state.index = index; state.points = []; state.rotation = 0; endZoom();
  const item = state.images[index];
  $('image-title').textContent = item.name; $('image-subtitle').textContent = `${formatBytes(item.bytes)}  ·  Loading image…`;
  $('position').textContent = `${index + 1} / ${state.images.length}`;
  document.querySelectorAll('.image-tile').forEach((tile, i) => tile.classList.toggle('active', i === index));
  try {
    const info = await api(`/api/info?path=${encodeURIComponent(item.path)}`);
    if (ticket !== state.requestId) return;
    state.width = info.width; state.height = info.height;
    const photo = $('photo');
    await new Promise((resolve, reject) => { photo.onload = resolve; photo.onerror = () => reject(new Error('Could not display this image.')); photo.src = url('/api/image', item.path) + `&v=${item.modified}`; });
    if (ticket !== state.requestId) return;
    $('empty-state').classList.add('hidden'); $('image-layer').classList.remove('hidden');
    $('image-subtitle').textContent = `${info.width.toLocaleString()} × ${info.height.toLocaleString()} px  ·  ${formatBytes(item.bytes)}`;
    fitImage(); renderOverlay(); updateControls();
  } catch (error) { toast(error.message, true); }
}
function fitImage() {
  if (state.index < 0 || !state.width) return;
  const frame = $('canvas-shell'); const pad = 42;
  const scale = Math.min((frame.clientWidth - pad) / state.width, (frame.clientHeight - pad) / state.height);
  const layer = $('image-layer'); layer.style.width = `${Math.max(1, state.width * scale)}px`; layer.style.height = `${Math.max(1, state.height * scale)}px`;
  $('overlay').setAttribute('viewBox', `0 0 ${state.width} ${state.height}`);
  renderOverlay();
}
function imagePoint(event) {
  const rect = $('image-layer').getBoundingClientRect();
  return { x: Math.max(0, Math.min(state.width - 1, (event.clientX - rect.left) / rect.width * state.width)), y: Math.max(0, Math.min(state.height - 1, (event.clientY - rect.top) / rect.height * state.height)) };
}
function ordered(points) {
  if (points.length !== 4) return null;
  const p = [...points].sort((a,b) => a.x === b.x ? a.y - b.y : a.x - b.x);
  const cross = (o,a,b) => (a.x-o.x)*(b.y-o.y)-(a.y-o.y)*(b.x-o.x);
  const lower = []; for (const point of p) { while (lower.length >= 2 && cross(lower.at(-2), lower.at(-1), point) <= 0) lower.pop(); lower.push(point); }
  const upper = []; for (const point of [...p].reverse()) { while (upper.length >= 2 && cross(upper.at(-2), upper.at(-1), point) <= 0) upper.pop(); upper.push(point); }
  const hull = lower.slice(0,-1).concat(upper.slice(0,-1)); if (hull.length !== 4) return null;
  const centre = { x: points.reduce((sum,p)=>sum+p.x,0)/4, y: points.reduce((sum,p)=>sum+p.y,0)/4 };
  const result = [...points].sort((a,b)=>Math.atan2(a.y-centre.y,a.x-centre.x)-Math.atan2(b.y-centre.y,b.x-centre.x));
  const start = result.reduce((best,p,i)=>p.x+p.y < result[best].x+result[best].y ? i : best,0);
  return result.slice(start).concat(result.slice(0,start));
}
function renderOverlay() {
  const svg = $('overlay'); svg.replaceChildren();
  const points = state.points; const pixelsPerScreen = state.width / (parseFloat($('image-layer').style.width) || state.width || 1);
  const stroke = Math.max(2, pixelsPerScreen * 2); const radius = Math.max(6, pixelsPerScreen * 8);
  const make = (name, attrs) => { const el = document.createElementNS('http://www.w3.org/2000/svg', name); for (const [key,value] of Object.entries(attrs)) el.setAttribute(key, value); svg.append(el); return el; };
  if (points.length >= 2) {
    const sequence = ordered(points) || points;
    make(points.length === 4 ? 'polygon' : 'polyline', { points: sequence.map(p=>`${p.x},${p.y}`).join(' '), fill: points.length === 4 ? '#59e4c523' : 'none', stroke: '#6af0d1', 'stroke-width': stroke, 'stroke-dasharray': points.length === 4 ? 'none' : `${stroke * 4} ${stroke * 3}`, 'vector-effect': 'non-scaling-stroke' });
  }
  points.forEach((point,index) => {
    make('circle', { cx: point.x, cy: point.y, r: radius + stroke, fill: '#071b19b0', stroke: '#65efce', 'stroke-width': stroke });
    const handle = make('circle', { cx: point.x, cy: point.y, r: radius, fill: '#d8fff5', 'data-point': index, class: 'handle' });
    handle.setAttribute('aria-label', `Corner ${index + 1}`);
  });
}
function updateControls() {
  const count = state.points.length; const complete = count === 4 && !!ordered(state.points);
  [...$('progress-dots').children].forEach((dot,index)=>dot.classList.toggle('done',index<count));
  $('step-title').textContent = count < 4 ? `Select corner ${count + 1} of 4` : complete ? 'Crop is ready' : 'Adjust the corners';
  $('step-detail').textContent = count < 4 ? 'Click anywhere on the image. Order does not matter.' : complete ? 'Drag any point to refine, then save.' : 'Corners must form a four-sided area.';
  $('canvas-help').textContent = count < 4 ? 'Click the four corners in any order' : complete ? 'Drag corners to refine · Enter to save' : 'Drag a point to make a four-sided area';
  $('undo').disabled = !count; $('reset').disabled = !count; $('rotate-left').disabled = state.index < 0; $('rotate-right').disabled = state.index < 0;
  $('save').disabled = !complete || state.busy || !!validSettings();
  $('crop-preview').disabled = !complete || state.busy || !!validSettings();
  $('select-whole').disabled = state.index < 0 || !state.width || state.busy;
  $('save-hint').textContent = state.busy ? 'Saving crop…' : validSettings() || (complete ? 'Ready to save the straightened image' : 'Select four corners to enable saving');
  $('prev').disabled = state.index <= 0; $('next').disabled = state.index < 0 || state.index >= state.images.length - 1;
}
function startZoom() {
  if (state.zoom || state.index < 0 || $('image-layer').classList.contains('hidden')) return;
  const layer = $('image-layer'); const rect = layer.getBoundingClientRect();
  const x = state.pointer ? Math.max(0, Math.min(rect.width, state.pointer.x - rect.left)) : rect.width / 2;
  const y = state.pointer ? Math.max(0, Math.min(rect.height, state.pointer.y - rect.top)) : rect.height / 2;
  layer.style.transformOrigin = `${x}px ${y}px`; layer.style.transform = 'scale(3)'; state.zoom = true; $('zoom-label').classList.remove('hidden');
}
function endZoom() { state.zoom = false; $('image-layer').style.transform = 'scale(1)'; $('zoom-label').classList.add('hidden'); }
async function cropSave() {
  if (state.busy || state.index < 0 || !ordered(state.points)) return;
  const problem = validSettings(); if (problem) { toast(problem,true); return; }
  state.busy = true; updateControls();
  const sourceIndex = state.index;
  try {
    const result = await api('/api/crop', { path: state.images[sourceIndex].path, points: state.points.map(p=>[p.x,p.y]), rotation: state.rotation, aspect_ratio: cropAspectRatio(), settings });
    $('save-error').classList.add('hidden');
    toast(`Saved ${result.name} · ${result.width} × ${result.height} px`);
    if (settings.autoNext && sourceIndex + 1 < state.images.length) await openImage(sourceIndex + 1);
    else if (settings.autoNext) toast(`Batch complete. Saved ${result.name}.`);
    else if (settings.mode === 'replace') await openImage(sourceIndex);
    else { state.points = []; state.rotation = 0; renderOverlay(); }
  } catch (error) {
    if (error.code === 'permission_denied') {
      $('save-error-detail').textContent = error.message;
      $('save-error').classList.remove('hidden');
    } else toast(error.message,true);
  }
  finally { state.busy = false; updateControls(); }
}
function closeCropPreview() {
  $('crop-preview-modal').classList.add('hidden');
  if (cropPreviewUrl) URL.revokeObjectURL(cropPreviewUrl);
  cropPreviewUrl = null; $('crop-preview-image').classList.add('hidden');
}
async function previewCrop() {
  if (!ordered(state.points) || validSettings()) return;
  $('crop-preview-modal').classList.remove('hidden');
  $('crop-preview-status').textContent = 'Preparing preview…';
  $('crop-preview-status').classList.remove('hidden');
  $('crop-preview-image').classList.add('hidden');
  try {
    const response = await fetch('/api/crop-preview', { method:'POST', headers:{'X-Cropper-Token':state.token,'Content-Type':'application/json'}, body:JSON.stringify({path:state.images[state.index].path,points:state.points.map(p=>[p.x,p.y]),rotation:state.rotation,aspect_ratio:cropAspectRatio()}) });
    if (!response.ok) throw new Error((await response.json()).error || 'Could not make the preview.');
    const blob = await response.blob();
    if ($('crop-preview-modal').classList.contains('hidden')) return;
    if (cropPreviewUrl) URL.revokeObjectURL(cropPreviewUrl);
    cropPreviewUrl = URL.createObjectURL(blob);
    $('crop-preview-image').src = cropPreviewUrl; $('crop-preview-image').classList.remove('hidden');
    $('crop-preview-status').classList.add('hidden');
  } catch (error) { $('crop-preview-status').textContent = error.message; }
}

function wire() {
  loadSettings();
  loadMergeSettings();
  loadEnhanceSettings();
  ['open-folder-top','folder-browse','empty-open'].forEach(id => $(id).addEventListener('click',()=>pickFolder()));
  $('output-browse').addEventListener('click',()=>pickFolder(true));
  $('merge-output-browse').addEventListener('click',()=>pickFolder('merge'));
  $('enhance-output-browse').addEventListener('click',()=>pickFolder('enhance'));
  $('crop-tab').addEventListener('click',()=>setView('crop'));
  $('merge-tab').addEventListener('click',()=>setView('merge'));
  $('enhance-tab').addEventListener('click',()=>setView('enhance'));
  $('enhance-select-all').addEventListener('click',()=>{ state.enhance.selected = new Set(state.images.map(image=>image.path)); refreshEnhanceTiles(); renderEnhanceControls(); if (state.images.length && state.enhance.preview < 0) previewEnhance(0); });
  $('enhance-clear').addEventListener('click',()=>{ state.enhance.selected.clear(); refreshEnhanceTiles(); renderEnhanceControls(); });
  $('enhance-save').addEventListener('click',saveEnhancedBatch);
  $('enhance-stop').addEventListener('click',()=>{ state.enhance.stop = true; renderEnhanceControls(); });
  $('merge-clear').addEventListener('click',()=>{ state.merge = { front: -1, back: -1, frontInfo: null, backInfo: null, busy: false }; renderImages(); renderMerge(); });
  $('merge-save').addEventListener('click',mergeSave);
  $('folder-modal-close').addEventListener('click',closePicker);
  $('folder-modal-cancel').addEventListener('click',closePicker);
  $('folder-modal').addEventListener('click',event=>{if(event.target===$('folder-modal'))closePicker();});
  $('folder-modal-up').addEventListener('click',()=>state.picker?.parent&&loadPickerFolder(state.picker.parent));
  $('folder-modal-go').addEventListener('click',()=>loadPickerFolder($('folder-modal-path').value.trim()));
  $('folder-modal-path').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();loadPickerFolder(event.currentTarget.value.trim());}});
  $('folder-modal-select').addEventListener('click',selectPickerFolder);
  $('save-error-browse').addEventListener('click',()=>{document.querySelector('input[name="mode"][value="folder"]').checked=true;saveSettings();pickFolder(true);});
  $('go-path').addEventListener('click',()=>loadFolder($('folder-path').value.trim()));
  $('folder-path').addEventListener('keydown',event=>{ if(event.key==='Enter') loadFolder(event.currentTarget.value.trim()); });
  $('go-parent').addEventListener('click',()=>state.parent&&loadFolder(state.parent));
  $('refresh').addEventListener('click',()=>state.folder&&loadFolder(state.folder,true));
  $('prev').addEventListener('click',()=>openImage(state.index-1)); $('next').addEventListener('click',()=>openImage(state.index+1));
  $('undo').addEventListener('click',()=>{state.points.pop();renderOverlay();updateControls();});
  $('reset').addEventListener('click',()=>{state.points=[];state.rotation=0;renderOverlay();updateControls();});
  $('select-whole').addEventListener('click',()=>{if(!state.width||!state.height)return;state.points=[{x:0,y:0},{x:state.width-1,y:0},{x:state.width-1,y:state.height-1},{x:0,y:state.height-1}];renderOverlay();updateControls();});
  $('rotate-left').addEventListener('click',()=>{state.rotation=((state.rotation-90+360)%360); if(state.rotation===270)state.rotation=-90; toast(`Output rotation: ${state.rotation}°`);});
  $('rotate-right').addEventListener('click',()=>{state.rotation=((state.rotation+90+360)%360); if(state.rotation===270)state.rotation=-90; toast(`Output rotation: ${state.rotation}°`);});
  $('save').addEventListener('click',cropSave);
  $('crop-preview').addEventListener('click',previewCrop);
  $('crop-preview-close').addEventListener('click',closeCropPreview);
  $('crop-preview-done').addEventListener('click',closeCropPreview);
  $('crop-preview-modal').addEventListener('click',event=>{if(event.target===$('crop-preview-modal'))closeCropPreview();});
  const layer = $('image-layer');
  layer.addEventListener('pointerdown',event=>{
    if(state.index<0||state.busy)return;
    const handle = event.target.closest?.('.handle');
    if(handle){state.drag=Number(handle.dataset.point);layer.setPointerCapture(event.pointerId);event.preventDefault();return;}
    if(state.points.length<4){state.points.push(imagePoint(event));renderOverlay();updateControls();}
  });
  layer.addEventListener('pointermove',event=>{state.pointer={x:event.clientX,y:event.clientY};if(state.drag>=0){state.points[state.drag]=imagePoint(event);renderOverlay();updateControls();}});
  layer.addEventListener('pointerup',()=>{state.drag=-1;}); layer.addEventListener('pointercancel',()=>{state.drag=-1;});
  document.addEventListener('keydown',event=>{
    if(!$('folder-modal').classList.contains('hidden')){if(event.key==='Escape')closePicker();return;}
    if(!$('crop-preview-modal').classList.contains('hidden')){if(event.key==='Escape')closeCropPreview();return;}
    if (state.view !== 'crop') return;
    if(event.key==='Control'){startZoom();return;}
    if(['INPUT','TEXTAREA'].includes(document.activeElement?.tagName))return;
    if(event.key==='ArrowLeft'){event.preventDefault();openImage(state.index-1);}
    else if(event.key==='ArrowRight'){event.preventDefault();openImage(state.index+1);}
    else if(event.key==='Enter'){event.preventDefault();cropSave();}
    else if(event.key==='Backspace'){event.preventDefault();state.points=[];renderOverlay();updateControls();}
    else if(event.key.toLowerCase()==='z'&&event.ctrlKey){event.preventDefault();state.points.pop();renderOverlay();updateControls();}
  });
  document.addEventListener('keyup',event=>{if(event.key==='Control')endZoom();}); window.addEventListener('blur',endZoom);
  $('canvas-shell').addEventListener('wheel',event=>{if(event.ctrlKey)event.preventDefault();},{passive:false});
  new ResizeObserver(fitImage).observe($('canvas-shell'));
  updateControls();
}
async function init() {
  try { const session = await fetch('/api/session').then(response=>response.json()); state.token=session.token; state.home=session.home; wire(); const last=localStorage.getItem('cropper-last-folder'); if(last) await loadFolder(last); }
  catch(error){toast(`Could not start Cropper: ${error.message}`,true);}
}
init();
