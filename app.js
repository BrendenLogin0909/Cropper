const $ = id => document.getElementById(id);
const state = { token: '', home: '', folder: '', parent: null, images: [], folders: [], index: -1, points: [], width: 0, height: 0, rotation: 0, zoom: false, drag: -1, pointer: null, busy: false, requestId: 0, picker: null, pickerRequest: 0, view: 'crop', merge: { front: -1, back: -1, frontInfo: null, backInfo: null, busy: false }, enhance: { selected: new Set(), preview: -1, requestId: 0, previewUrl: null, busy: false, stop: false }, restore: { selected: new Set(), notNeeded: new Set(), preview: -1, requestId: 0, previewUrl: null, busy: false, stop: false, resultsFolder: '', marks: new Map(), brush: false, drawing: null, showMarks: false } };
state.chatgpt = { selected: new Set(), queuedPaths: new Set(), batchPaths: null, anchor: -1, preview: null, pending: [], recovered: [], review: null, busy: false, total: 0, completed: 0, stop: false };
const RESTORE_INFO = {
  dust: { title: 'Dust repair', output: '1.6. TBP Auto-Improved', archive: '2. Cropped', description: 'Removes only isolated marks and short scratches the app can identify with high confidence. Review faces, clothing details, and textured areas in the preview.' },
  noise: { title: 'Noise reduction', output: '1.6. TBP Auto-Improved', archive: '2. Cropped', description: 'Measures fine grain and colour speckles in each photo. It skips clearer photos and uses a limited, detail-preserving correction where noise is present.' },
  sharpen: { title: 'Detail & clarity', output: '1.6. TBP Auto-Improved', archive: '2. Cropped', description: 'Finds photos with faded gray tones or soft edges, then restores a measured tonal range and clarifies existing detail. Clear photos are skipped. It cannot recreate detail missing from an out-of-focus image.' }
};
const stored = JSON.parse(localStorage.getItem('cropper-settings') || '{}');
const settings = { autoNext: stored.autoNext !== false, aspect: stored.aspect || 'auto', customAspect: stored.customAspect || '2:3' };
const storedMerge = JSON.parse(localStorage.getItem('cropper-merge-settings') || '{}');
const mergeSettings = { rotation: String(storedMerge.rotation || 0), nameSource: storedMerge.nameSource || 'back' };
const enhanceSettings = {};
let toastTimer;
let resetEnhanceZoom = () => {};
let resetRestoreZoom = () => {};
let resetChatgptZoom = () => {};
let chatgptZoomKey = '';
const chatgptCommentDrafts = new Map();
let chatgptConcurrency = [1,3,5,10].includes(Number(localStorage.getItem('cropper-chatgpt-concurrency'))) ? Number(localStorage.getItem('cropper-chatgpt-concurrency')) : 3;
let photoLinks = [], selectedPairs = new Set(), combinedReview = false;
let manualPairFront = null, manualPairBack = null, manualPairRequest = 0;
let chatgptOperation = 'restore', colorCandidates = new Map(), colorScanning = false, colorScanFolder = '', colorRequest = 0;
function stageRoutingEnabled() { return true; }
function updateStageRoutingDisplay() {
  const flat = stageRoutingEnabled();
  document.body.classList.toggle('flat-stage-routing', flat);
  $('crop-stage-routing-note').classList.toggle('hidden', !flat);
  document.querySelector('#enhance-settings .settings-intro').textContent = flat ?
    'Flat batch: process 1.5. TBP Cropped. Preserve the source in sibling 2. Cropped; save the improved copy in 1.6. TBP Auto-Improved with _e.' :
    'Sources are archived in sibling 2. Cropped; results go to 1.6. TBP Auto-Improved.';
  $('enhance-finish').classList.toggle('hidden', !flat);
  $('chatgpt-finish').classList.toggle('hidden', !flat);
}
async function finishStageSelection(kind) {
  if (!stageRoutingEnabled() || state.busy || state.enhance.busy || state.chatgpt.busy || state.chatgpt.reviewBusy || state.chatgpt.queuedPaths.size) {
    toast('Finish or stop active processing and clear queued requests before filing unchanged photos.', true); return;
  }
  const selection = kind === 'enhance' ? state.enhance.selected : state.chatgpt.selected;
  const paths = [...selection]; if (!paths.length) return;
  state.chatgpt.reviewBusy = true; renderEnhanceControls(); updateChatgptControls();
  try {
    const preview = await api('/api/stage-finish', {paths, dry_run:true});
    const destinations = [...new Set(preview.items.flatMap(item => item.snapshots.map(path => path.split(/[\\/]/).slice(-2,-1)[0])))];
    if (!window.confirm(`Finish ${preview.items.length} file(s), including linked pair members, WITHOUT more processing?\n\nUnchanged snapshots will be saved in:\n${destinations.join('\n')}\n\nQueue sources will then be removed. No API calls or charges. Pending results must be reviewed first.`)) return;
    const result = await api('/api/stage-finish', {paths, dry_run:false});
    const finished = new Set(result.finished);
    state.images = state.images.filter(image => !finished.has(image.path));
    for (const path of finished) {state.enhance.selected.delete(path);state.chatgpt.selected.delete(path);selectedPairs.delete(path);}
    clearEnhancePreview(); state.chatgpt.preview=null; showChatgptPreview();renderImages();renderPhotoLinks();
    toast(`${finished.size} files finished with unchanged stage snapshots.${result.errors.length ? ' '+result.errors[0] : ''}`, !!result.errors.length);
  } catch(error) {toast(error.message,true);}
  finally {state.chatgpt.reviewBusy=false;renderEnhanceControls();updateChatgptControls();}
}
async function refreshStageFolders() {
  const folder = state.folder;
  if (!folder) return;
  try {
    const listing = await api(`/api/folders?path=${encodeURIComponent(folder)}`);
    if (folder !== state.folder) return;
    state.folders=listing.folders;$('folder-count').textContent=listing.folders.length;renderFolders();
  } catch(error) { toast(`Files saved, but folder list could not refresh: ${error.message}`,true); }
}
function updateColorizeToolbarControls() {
  $('colorize-filter').disabled = false;
  $('colorize-manual').disabled = !!state.chatgpt.reviewBusy;
  $('colorize-scan').disabled = !state.folder || state.chatgpt.busy || colorScanning;
  $('colorize-select').disabled = colorScanning ||
    !state.folder || colorScanFolder !== state.folder;
}
function colorImageVisible(path) {
  const filterReady = !colorScanning && Boolean(state.folder) && colorScanFolder === state.folder;
  return state.view !== 'chatgpt' || chatgptOperation !== 'colorize' ||
    !$('colorize-filter').checked || !filterReady || Boolean(colorCandidates.get(path)?.suggested);
}
function colorEligible(path) { return chatgptOperation !== 'colorize' || (colorScanFolder === state.folder && (colorCandidates.get(path)?.suggested || ($('colorize-manual').checked && !colorCandidates.get(path)?.combined))); }
async function scanColorCandidates() {
  if (!state.folder || state.chatgpt.busy) return;
  const folder = state.folder, ticket = ++colorRequest;
  colorScanning = true; colorScanFolder = ''; renderImages(); updateChatgptControls();
  $('colorize-status').textContent = 'Checking thumbnails locally… showing all photos until ready. No paid API requests.';
  try {
    const response = await api('/api/colorization-candidates', {path:folder});
    if (ticket !== colorRequest || folder !== state.folder) return;
    colorCandidates = new Map(response.items.map(item => [item.path,item])); colorScanFolder = folder;
    state.chatgpt.selected = new Set([...state.chatgpt.selected].filter(colorEligible));
    $('colorize-status').textContent = `${response.items.filter(item=>item.suggested).length} suggested fronts. Detection is approximate; check sepia and faded colour carefully.`;
  } catch(error) { $('colorize-status').textContent = `${error.message} — load the updated server before colorizing.`; }
  finally { if(ticket===colorRequest){colorScanning=false;renderImages();renderPhotoLinks();updateChatgptControls();} }
}
function switchChatgptOperation(operation) {
  if(state.chatgpt.busy || state.chatgpt.reviewBusy || state.chatgpt.resubmissions?.size) {toast('Wait for active image requests before switching image operations. You can still review other pending images.',true);return;}
  if(chatgptOperation !== operation && state.chatgpt.queuedPaths.size) {toast('Start or clear queued photos before switching image operations.',true);return;}
  const changed = chatgptOperation !== operation;
  chatgptOperation = operation;
  if(changed) {state.chatgpt.selected.clear();state.chatgpt.review=null;state.chatgpt.preview=null;state.chatgpt.anchor=-1;selectedPairs.clear();combinedReview=false;clearChatgptPreview();$('photo-pairs-suggestions').replaceChildren();$('photo-pairs-manual-preview').replaceChildren();$('photo-pairs-auto-queue').checked=false;}
  setView('chatgpt'); refreshChatgptPending();
  if(operation==='colorize' && colorScanFolder!==state.folder) scanColorCandidates();
}
let cropPreviewUrl = null;
let cropPreviewTimer = null;
let cropPreviewRequest = 0;

function toast(message, error = false) {
  const element = $('toast'); element.textContent = message; element.classList.toggle('error', error); element.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => element.classList.remove('show'), 4200);
}
function offerChooseOutputFolder(error) {
  if (error?.code === 'file_locked') {
    toast(error.message || 'Cropper cannot write this file. Close it in other programs and retry.', true);
    return true;
  }
  if (error?.code !== 'permission_denied') return false;
  const detail = error.message || 'Cropper cannot write to this location.';
  if ($('save-error-detail')) $('save-error-detail').textContent = detail;
  $('save-error')?.classList.remove('hidden');
  if ($('output-permission-detail')) $('output-permission-detail').textContent = detail;
  $('output-permission')?.classList.remove('hidden');
  return true;
}
async function setOutputFolder(path) {
  try {
    applyServerSettings(await api('/api/settings', { output_folder: path, guide_seen: true }));
    if ($('settings-output-folder')) $('settings-output-folder').value = path;
    $('output-permission')?.classList.add('hidden');
    $('save-error')?.classList.add('hidden');
    toast(`Output folder set to ${path}. Try saving again.`);
  } catch (error) { toast(error.message, true); }
}
function formatBytes(bytes) { return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`; }
function url(path, filePath) { return `${path}?path=${encodeURIComponent(filePath)}&token=${encodeURIComponent(state.token)}`; }
async function api(path, body) {
  if ((body?.stage_routing || body?.settings?.stage_routing || path === '/api/stage-finish') && !state.stageRoutingAvailable) {
    throw new Error('Flat stage routing needs the updated server. Finish active batches, restart Cropper on 8767, then refresh. No request was sent.');
  }
  const response = await fetch(path, { method: body === undefined ? 'GET' : 'POST', headers: { 'X-Cropper-Token': state.token, ...(body === null ? {} : body === undefined ? {} : { 'Content-Type': 'application/json' }) }, ...(body === undefined || body === null ? {} : { body: JSON.stringify(body) }) });
  const data = await response.json(); if (!response.ok) { const error = new Error(data.error || 'Something went wrong.'); error.status = response.status; error.retryAfter = data.retry_after; error.code = data.code; error.type = data.type; error.requestId = data.request_id; error.path = data.path; throw error; } return data;
}
function saveSettings() {
  settings.autoNext = $('auto-next').checked;
  settings.aspect = $('crop-aspect').value; settings.customAspect = $('crop-aspect-custom').value.trim();
  localStorage.setItem('cropper-settings', JSON.stringify(settings));
  $('crop-aspect-custom-field').classList.toggle('hidden', settings.aspect !== 'custom');
  $('save-error').classList.add('hidden'); updateControls();
}
function loadSettings() {
  $('auto-next').checked = settings.autoNext;
  $('crop-aspect').value = settings.aspect; $('crop-aspect-custom').value = settings.customAspect;
  document.querySelectorAll('#auto-next, #crop-aspect, #crop-aspect-custom').forEach(input => input.addEventListener('input', saveSettings));
  saveSettings();
}
function validSettings() {
  return settings.aspect === 'custom' && !parseAspect(settings.customAspect) ? 'Enter a custom proportion such as 2:3.' : '';
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
  mergeSettings.rotation = $('merge-rotation').value;
  mergeSettings.nameSource = document.querySelector('input[name="merge-name-source"]:checked').value;
  localStorage.setItem('cropper-merge-settings', JSON.stringify(mergeSettings)); renderMerge();
}
function loadMergeSettings() {
  (document.querySelector(`input[name="merge-name-source"][value="${mergeSettings.nameSource}"]`) || document.querySelector('input[name="merge-name-source"]')).checked = true;
  $('merge-rotation').value = mergeSettings.rotation;
  document.querySelectorAll('input[name="merge-name-source"], #merge-rotation').forEach(input => input.addEventListener('input', saveMergeSettings));
  saveMergeSettings();
}
function validMergeSettings() { return ''; }
function loadEnhanceSettings() { renderEnhanceControls(); }
function validEnhanceSettings() { return ''; }
function setView(view) {
  if (!['crop', 'merge', 'enhance', 'chatgpt'].includes(view)) return;
  if ((state.restore.busy || state.enhance.busy || state.chatgpt.busy || state.chatgpt.reviewBusy || state.chatgpt.resubmissions?.size) && view !== state.view) { toast('Wait for active image requests before switching tabs. You can still review other pending images.', true); return; }
  const previous = state.view;
  state.view = view;
  for (const name of ['crop', 'merge', 'enhance', 'chatgpt']) {
    const active = view === name;
    $(`${name}-workspace`).classList.toggle('hidden', !active); $(`${name}-settings`).classList.toggle('hidden', !active);
    $(`${name}-tab`).classList.toggle('active', active); $(`${name}-tab`).setAttribute('aria-selected', String(active));
  }
  $('chatgpt-tab').classList.toggle('active', view==='chatgpt' && chatgptOperation==='restore');
  $('chatgpt-tab').setAttribute('aria-selected',String(view==='chatgpt' && chatgptOperation==='restore'));
  $('colorize-tab').classList.toggle('active', view==='chatgpt' && chatgptOperation==='colorize');
  $('colorize-tab').setAttribute('aria-selected',String(view==='chatgpt' && chatgptOperation==='colorize'));
  const restoring = !!RESTORE_INFO[view];
  $('restore-workspace').classList.toggle('hidden', !restoring); $('restore-settings').classList.toggle('hidden', !restoring);
  if (restoring && previous !== view) {
    state.restore.selected.clear(); state.restore.notNeeded.clear(); state.restore.marks.clear(); state.restore.brush = false; state.restore.showMarks = false;
    clearRestorationPreview(); $('restore-progress').textContent = ''; $('restore-open-results').classList.add('hidden');
    const info = RESTORE_INFO[view];
    $('restore-title').textContent = info.title; $('restore-settings-title').textContent = info.title;
    $('restore-description').textContent = info.description; $('restore-output-name').textContent = info.output; $('restore-archive-name').textContent = info.archive;
  }
  $('explorer-foot').textContent = view === 'chatgpt' ? 'Click · Shift-click range · Ctrl-click individual' : view === 'enhance' || restoring ? 'Click photos to select and preview' : view === 'merge' ? 'Click front, then back' : '↕ Browse files · double-click to open';
  renderImages(); if (view === 'merge') renderMerge(); if (view === 'enhance') renderEnhanceControls(); if (restoring) renderRestorationControls(); if (view === 'chatgpt') updateChatgptControls();
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
    const result = await api('/api/merge', { front_path: state.images[front].path, back_path: state.images[back].path, back_rotation: Number(mergeSettings.rotation), settings:{...mergeSettings,stage_routing:stageRoutingEnabled()}, name_source: mergeSettings.nameSource });
    toast(`Saved ${result.name} · ${result.width} × ${result.height} px`);
    await refreshStageFolders();
  } catch (error) { if (!offerChooseOutputFolder(error)) toast(error.message, true); }
  finally { state.merge.busy = false; renderMerge(); }
}
function renderEnhanceControls() {
  const count = state.enhance.selected.size;
  $('enhance-finish').disabled = !count || state.enhance.busy || state.chatgpt.busy || !!state.chatgpt.reviewBusy;
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
  state.enhance.previewUrl = null; state.enhance.preview = -1; resetEnhanceZoom();
  $('enhance-before').removeAttribute('src'); $('enhance-after').removeAttribute('src');
  $('enhance-comparison').classList.add('hidden'); $('enhance-empty').classList.remove('hidden');
  $('enhance-subtitle').textContent = 'Open any folder, then select photos to preview and improve.';
  $('enhance-changes').textContent = 'Each image is analysed separately. Review the preview before saving.';
}
async function previewEnhance(index) {
  if (index < 0 || index >= state.images.length) return;
  const item = state.images[index]; const ticket = ++state.enhance.requestId;
  if (state.enhance.preview !== index) resetEnhanceZoom();
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
    $('enhance-changes').textContent = changes === 'No safe adjustment detected' ? 'No safe automatic adjustment was detected for this photo. A stronger or manual edit may help.' : `Applied to this photo: ${changes}. Preview may differ slightly from the saved file.`;
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
  const batchSettings = { ...enhanceSettings, stage_routing:stageRoutingEnabled() };
  state.enhance.busy = true; state.enhance.stop = false; renderEnhanceControls();
  let saved = 0; const failed = []; const archived = new Set();
  for (const [index, path] of paths.entries()) {
    if (archived.has(path)) continue;
    if (state.enhance.stop) break;
    $('enhance-progress').textContent = `Saving ${index + 1} of ${paths.length}…`;
    try { const result=await api('/api/enhance', { path, settings: batchSettings }); for(const moved of result.source_paths || [path]) {state.enhance.selected.delete(moved);archived.add(moved);} saved++; }
    catch (error) { failed.push(`${path.split(/[\\/]/).pop()}: ${error.message}`); if (offerChooseOutputFolder(error)) state.enhance.stop = true; }
    refreshEnhanceTiles(); renderEnhanceControls();
  }
  const stopped = state.enhance.stop;
  state.enhance.busy = false; renderEnhanceControls();
  if (archived.size) {
    state.images = state.images.filter(image => !archived.has(image.path));
    $('image-count').textContent = state.images.length;
    clearEnhancePreview(); renderImages(); renderEnhanceControls();
  }
  $('enhance-progress').textContent = `${saved} saved and original${saved === 1 ? '' : 's'} archived${failed.length ? ` · ${failed.length} failed` : ''}${stopped ? ' · batch stopped' : ''}`;
  if (failed.length) toast(failed[0], true); else toast(`${saved} enhanced ${saved === 1 ? 'copy' : 'copies'} saved; source snapshots filed in ${'2. Cropped'}${stopped ? ' before stopping' : ''}.`);
}
function renderRestorationControls() {
  const count = state.restore.selected.size;
  const useful = [...state.restore.selected].filter(path => !state.restore.notNeeded.has(path)).length;
  $('restore-selection-count').textContent = `${count} selected${count > useful ? ` · ${count - useful} no change needed` : ''}`;
  $('restore-save').disabled = !useful || state.restore.busy;
  $('restore-save').firstElementChild.textContent = !count ? 'Save selected photos' : count === 1 ? 'Save restored photo' : `Save ${count} selected photos`;
  $('restore-save-hint').textContent = state.restore.busy ? 'Processing selected photos…' : count && !useful ? 'No useful change for the selected photo; choose another image or tab' : count ? 'Review the preview, then save the batch' : 'Select photos to enable saving';
  const dust = state.view === 'dust'; const previewPath = state.images[state.restore.preview]?.path;
  for (const id of ['restore-mark-scratch', 'restore-clear-marks', 'restore-show-marks']) $(id).classList.toggle('hidden', !dust);
  $('restore-mark-scratch').disabled = !dust || !previewPath || state.restore.busy;
  $('restore-clear-marks').disabled = !dust || !previewPath || !state.restore.marks.get(previewPath)?.length || state.restore.busy;
  $('restore-show-marks').disabled = !dust || !previewPath || state.restore.busy;
  $('restore-mark-scratch').setAttribute('aria-pressed', String(dust && state.restore.brush));
  $('restore-mark-scratch').textContent = state.restore.brush ? 'Done marking' : 'Mark a scratch';
  $('restore-show-marks').setAttribute('aria-pressed', String(dust && state.restore.showMarks));
  $('restore-show-marks').textContent = state.restore.showMarks ? 'Show result' : 'Show repair marks';
  $('restore-before-wrap').classList.toggle('brush-active', dust && state.restore.brush && !state.restore.busy);
  $('restore-select-all').disabled = !state.images.length || state.restore.busy;
  $('restore-clear').disabled = !count || state.restore.busy;
  $('restore-stop').classList.toggle('hidden', !state.restore.busy);
  $('restore-stop').disabled = state.restore.stop;
  $('restore-stop').textContent = state.restore.stop ? 'Stopping…' : 'Stop after current';
}
function refreshRestorationTiles() {
  if (!RESTORE_INFO[state.view]) return;
  document.querySelectorAll('#images .image-tile').forEach((tile, index) => {
    tile.classList.toggle('restore-selected', state.restore.selected.has(state.images[index].path));
    tile.classList.toggle('restore-previewed', state.restore.preview === index);
    tile.classList.toggle('restore-not-needed', state.restore.notNeeded.has(state.images[index].path));
  });
}
function clearRestorationPreview() {
  ++state.restore.requestId;
  if (state.restore.previewUrl) URL.revokeObjectURL(state.restore.previewUrl);
  state.restore.previewUrl = null; state.restore.preview = -1; resetRestoreZoom();
  state.restore.drawing = null; drawBrushCanvas();
  $('restore-before').removeAttribute('src'); $('restore-after').removeAttribute('src');
  $('restore-comparison').classList.add('hidden'); $('restore-empty').classList.remove('hidden');
  $('restore-subtitle').textContent = 'Open a folder, then select photos to preview.';
  $('restore-changes').textContent = 'Each image is analysed separately. Review the preview before saving.';
  $('restore-changes').classList.remove('no-useful-change');
  $('restore-after-label').textContent = 'AFTER';
}
async function previewRestoration(index) {
  if (index < 0 || index >= state.images.length || !RESTORE_INFO[state.view]) return;
  const item = state.images[index]; const operation = state.view; const ticket = ++state.restore.requestId;
  if (state.restore.preview !== index) resetRestoreZoom();
  state.restore.preview = index;
  $('restore-empty').classList.add('hidden'); $('restore-comparison').classList.remove('hidden');
  $('restore-before').onload = drawBrushCanvas;
  $('restore-before').src = url('/api/enhance-original', item.path) + `&v=${item.modified}`;
  $('restore-after').removeAttribute('src'); $('restore-subtitle').textContent = item.name;
  $('restore-changes').textContent = 'Analysing this photo…'; refreshRestorationTiles();
  $('restore-after-label').textContent = state.restore.showMarks && operation === 'dust' ? 'DETECTED MARKS' : 'AFTER';
  renderRestorationControls(); drawBrushCanvas();
  try {
    const response = await fetch('/api/restore-preview', { method: 'POST', headers: { 'X-Cropper-Token': state.token, 'Content-Type': 'application/json' }, body: JSON.stringify({ path: item.path, operation, strokes: state.restore.marks.get(item.path) || [], show_marks: state.restore.showMarks && operation === 'dust' }) });
    if (!response.ok) { const data = await response.json(); throw new Error(data.error || 'Could not generate the preview.'); }
    const changes = response.headers.get('X-Cropper-Adjustments') || 'Restoration preview ready';
    const useful = response.headers.get('X-Cropper-Useful') !== 'false';
    const blob = await response.blob();
    if (ticket !== state.restore.requestId) return;
    if (useful) state.restore.notNeeded.delete(item.path); else state.restore.notNeeded.add(item.path);
    if (state.restore.previewUrl) URL.revokeObjectURL(state.restore.previewUrl);
    state.restore.previewUrl = URL.createObjectURL(blob); $('restore-after').src = state.restore.previewUrl;
    $('restore-changes').textContent = useful ? changes : `${changes}. Skip this step for this photo.`;
    $('restore-changes').classList.toggle('no-useful-change', !useful);
    refreshRestorationTiles(); renderRestorationControls();
  } catch (error) { if (ticket === state.restore.requestId) { $('restore-changes').textContent = error.message; toast(error.message, true); } }
}
function brushPoint(event) {
  const canvas = $('restore-brush-canvas'); const image = $('restore-before'); const rect = canvas.getBoundingClientRect();
  if (!image.naturalWidth || !image.naturalHeight || !rect.width || !rect.height) return null;
  const scale = Math.min(rect.width / image.naturalWidth, rect.height / image.naturalHeight);
  const width = image.naturalWidth * scale, height = image.naturalHeight * scale;
  const left = (rect.width - width) / 2, top = (rect.height - height) / 2;
  const x = (event.clientX - rect.left - left) / width, y = (event.clientY - rect.top - top) / height;
  return x >= 0 && x <= 1 && y >= 0 && y <= 1 ? [x, y] : null;
}
function drawBrushCanvas() {
  const canvas = $('restore-brush-canvas'); const image = $('restore-before'); const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(rect.width * ratio); canvas.height = Math.round(rect.height * ratio);
  const ctx = canvas.getContext('2d'); ctx.scale(ratio, ratio); ctx.clearRect(0, 0, rect.width, rect.height);
  const path = state.images[state.restore.preview]?.path; const strokes = state.restore.marks.get(path) || [];
  if (!strokes.length || !image.naturalWidth || !image.naturalHeight) return;
  const scale = Math.min(rect.width / image.naturalWidth, rect.height / image.naturalHeight);
  const width = image.naturalWidth * scale, height = image.naturalHeight * scale;
  const left = (rect.width - width) / 2, top = (rect.height - height) / 2;
  ctx.strokeStyle = '#ff4c59'; ctx.fillStyle = '#ff4c59'; ctx.lineWidth = 5; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  for (const stroke of strokes) {
    ctx.beginPath(); stroke.forEach(([x, y], index) => index ? ctx.lineTo(left + x * width, top + y * height) : ctx.moveTo(left + x * width, top + y * height));
    ctx.stroke(); if (stroke.length === 1) { const [x, y] = stroke[0]; ctx.beginPath(); ctx.arc(left + x * width, top + y * height, 3, 0, Math.PI * 2); ctx.fill(); }
  }
}
function selectRestorationImage(index) {
  if (state.restore.busy) return;
  const path = state.images[index].path;
  if (state.restore.selected.has(path)) state.restore.selected.delete(path); else state.restore.selected.add(path);
  previewRestoration(index); renderRestorationControls();
}
async function saveRestorationBatch() {
  if (state.restore.busy || ![...state.restore.selected].some(path => !state.restore.notNeeded.has(path)) || !RESTORE_INFO[state.view]) return;
  const operation = state.view; const info = RESTORE_INFO[operation];
  const paths = state.images.map(image => image.path).filter(path => state.restore.selected.has(path));
  state.restore.busy = true; state.restore.stop = false; renderRestorationControls();
  let saved = 0, skipped = 0; const failed = []; const archived = new Set();
  for (const [index, path] of paths.entries()) {
    if (state.restore.stop) break;
    $('restore-progress').textContent = `Saving ${index + 1} of ${paths.length}…`;
    try {
      const result = await api('/api/restore', { path, operation, strokes: state.restore.marks.get(path) || [] });
      state.restore.selected.delete(path);
      if (result.skipped) { state.restore.notNeeded.add(path); skipped++; }
      else {
        archived.add(path); saved++;
        state.restore.marks.delete(path);
        state.restore.resultsFolder = result.path.slice(0, Math.max(result.path.lastIndexOf('/'), result.path.lastIndexOf('\\')));
      }
    } catch (error) {
      failed.push(`${path.split(/[\\/]/).pop()}: ${error.message}`);
      if (offerChooseOutputFolder(error)) state.restore.stop = true;
    }
    refreshRestorationTiles(); renderRestorationControls();
  }
  const stopped = state.restore.stop;
  state.restore.busy = false;
  if (archived.size) {
    const current = state.images[state.index]?.path;
    state.images = state.images.filter(image => !archived.has(image.path));
    $('image-count').textContent = state.images.length;
    clearRestorationPreview();
    if (archived.has(current)) { state.index = -1; clearEditor(); updateControls(); }
    try { const folders = await api(`/api/folders?path=${encodeURIComponent(state.folder)}`); state.folders = folders.folders; $('folder-count').textContent = folders.folders.length; renderFolders(); }
    catch (error) { toast(error.message, true); }
    renderImages(); $('restore-open-results').classList.remove('hidden');
  }
  renderRestorationControls();
  $('restore-progress').textContent = `${saved} saved to ${info.output}; ${skipped} skipped because no useful change was found${failed.length ? ` · ${failed.length} failed` : ''}${stopped ? ' · stopped' : ''}`;
  if (failed.length) toast(failed[0], true); else toast(saved ? `${saved} ${saved === 1 ? 'photo' : 'photos'} saved to ${info.output}; ${skipped} skipped.` : `${skipped} skipped; no files were changed.`);
}
async function pickFolder(forOutput = false) {
  state.picker = { path: ($('settings-output-folder')?.value || state.folder || state.home), parent: null, forOutput };
  $('folder-modal-title').textContent = forOutput ? 'Choose another output folder' : 'Choose image folder';
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
  if (forOutput) { await setOutputFolder(path); return; }
  await loadFolder(path);
}
async function loadFolder(path, preserve = false) {
  if (state.chatgpt.busy || state.chatgpt.reviewBusy || state.chatgpt.resubmissions?.size) { toast('Wait for active image requests before changing folders. You can still review other pending images.', true); return; }
  if (state.chatgpt.queuedPaths.size && path !== state.folder) { toast('Start or clear queued GPT photos before changing folders.', true); return; }
  if (state.enhance.busy) { toast('Stop or finish the enhancement batch before changing folders.', true); return; }
  if (state.restore.busy) { toast('Stop or finish the restoration batch before changing folders.', true); return; }
  try {
    const data = await api(`/api/list?path=${encodeURIComponent(path)}`);
    const prior = preserve && state.index >= 0 ? state.images[state.index]?.path : null;
    if (data.path !== state.folder) $('images').scrollTop = 0;
    state.folder = data.path; state.parent = data.parent; state.images = data.images; state.folders = data.folders;
    colorRequest++;colorScanning=false;colorScanFolder='';colorCandidates.clear();selectedPairs.clear();
    $('photo-pairs-suggestions').replaceChildren();$('photo-pairs-manual-preview').replaceChildren();
    state.chatgpt.selected.clear(); state.chatgpt.anchor = -1; state.chatgpt.preview = null; state.chatgpt.review = null; clearChatgptPreview(); updateChatgptControls();
    state.enhance.selected.clear(); clearEnhancePreview(); $('enhance-progress').textContent = ''; renderEnhanceControls();
    state.restore.selected.clear(); state.restore.notNeeded.clear(); state.restore.marks.clear(); clearRestorationPreview(); $('restore-progress').textContent = ''; $('restore-open-results').classList.add('hidden'); renderRestorationControls();
    localStorage.setItem('cropper-last-folder', data.path); $('folder-path').value = data.path;
    $('folder-count').textContent = data.folders.length; $('image-count').textContent = data.images.length;
    $('go-parent').disabled = !data.parent;
    renderFolders(); renderImages();
    await refreshChatgptPending();
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
    if(!colorImageVisible(image.path)) continue;
    const tile = document.createElement('button'); tile.className = 'image-tile'; tile.title = state.view === 'chatgpt' ? `${image.name} — click, Shift-click or Ctrl-click to select` : state.view === 'enhance' || RESTORE_INFO[state.view] ? `${image.name} — select and preview` : state.view === 'merge' ? `${image.name} — choose as front or back` : `${image.name} — double-click to open`;
    const picture = document.createElement('div'); picture.className = 'tile-photo';
    const thumbnail = document.createElement('img'); thumbnail.loading = 'lazy'; thumbnail.alt = ''; thumbnail.src = url('/api/thumb', image.path); picture.append(thumbnail);
    const name = document.createElement('div'); name.className = 'tile-name'; name.textContent = image.name;
    const meta = document.createElement('div'); meta.className = 'tile-meta'; meta.textContent = state.view==='chatgpt' && chatgptOperation==='colorize' ? (colorCandidates.get(image.path)?.kind || 'Not scanned') : formatBytes(image.bytes);
    tile.append(picture, name, meta); tile.addEventListener('dblclick', () => { if (state.view === 'crop') openImage(index); });
    tile.addEventListener('click', event => { if (state.view === 'chatgpt') selectChatgptImage(index, event); else if (state.view === 'enhance') selectEnhanceImage(index); else if (RESTORE_INFO[state.view]) selectRestorationImage(index); else if (state.view === 'merge') selectMergeImage(index); else { document.querySelectorAll('.image-tile.selected').forEach(el => el.classList.remove('selected')); tile.classList.add('selected'); } });
    tile.classList.toggle('merge-front', state.view === 'merge' && state.merge.front === index); tile.classList.toggle('merge-back', state.view === 'merge' && state.merge.back === index);
    tile.classList.toggle('enhance-selected', state.view === 'enhance' && state.enhance.selected.has(image.path));
    tile.classList.toggle('enhance-previewed', state.view === 'enhance' && state.enhance.preview === index);
    tile.classList.toggle('restore-selected', !!RESTORE_INFO[state.view] && state.restore.selected.has(image.path));
    tile.classList.toggle('restore-previewed', !!RESTORE_INFO[state.view] && state.restore.preview === index);
    tile.classList.toggle('restore-not-needed', !!RESTORE_INFO[state.view] && state.restore.notNeeded.has(image.path));
    tile.classList.toggle('enhance-selected', state.view === 'chatgpt' && state.chatgpt.selected.has(image.path));
    tile.classList.toggle('chatgpt-queued', state.view === 'chatgpt' && state.chatgpt.queuedPaths.has(image.path));
    tile.classList.toggle('enhance-previewed', state.view === 'chatgpt' && state.chatgpt.preview === image.path);
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
  clearLiveCropPreview();
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
  const preview = $('live-preview-image');
  preview.style.height = layer.style.height; preview.style.maxHeight = layer.style.height;
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
  $('canvas-help').textContent = count < 4 ? 'Ctrl+A predicts corners · For several photos, place one point inside the intended print first' : complete ? 'Drag corners to refine · Ctrl+A cycles outlines · Enter to save' : 'Drag a point to make a four-sided area';
  $('undo').disabled = !count; $('reset').disabled = !count; $('rotate-left').disabled = state.index < 0; $('rotate-right').disabled = state.index < 0;
  $('save').disabled = !complete || state.busy || !!validSettings();
  $('select-whole').disabled = state.index < 0 || !state.width || state.busy;
  $('predict-corners').disabled = state.index < 0 || !state.width || state.busy || cropPredictionBusy;
  $('predict-corners').querySelector('span').textContent = cropPredictionBusy ? 'Finding corners…' : 'Predict corners';
  $('save-hint').textContent = state.busy ? 'Saving crop…' : validSettings() || (complete ? 'Ready to save the straightened image' : 'Select four corners to enable saving');
  $('prev').disabled = state.index <= 0; $('next').disabled = state.index < 0 || state.index >= state.images.length - 1;
  scheduleLiveCropPreview();
}
function startZoom() {
  if (state.zoom || state.index < 0 || $('image-layer').classList.contains('hidden')) return;
  const layer = $('image-layer'); const rect = layer.getBoundingClientRect();
  const x = state.pointer ? Math.max(0, Math.min(rect.width, state.pointer.x - rect.left)) : rect.width / 2;
  const y = state.pointer ? Math.max(0, Math.min(rect.height, state.pointer.y - rect.top)) : rect.height / 2;
  layer.style.transformOrigin = `${x}px ${y}px`; layer.style.transform = 'scale(3)'; state.zoom = true; $('zoom-label').classList.remove('hidden');
}
function endZoom() { state.zoom = false; $('image-layer').style.transform = 'scale(1)'; $('zoom-label').classList.add('hidden'); }
let cropPredictionCache = null;
let cropPredictionBusy = false;
let cropPredictionRequest = 0;
async function predictCropCorners() {
  if (state.index < 0 || !state.width || state.busy || cropPredictionBusy || $('image-layer').classList.contains('hidden')) return;
  const item = state.images[state.index], ticket = state.requestId;
  const target = state.points.length === 1 ? [state.points[0].x,state.points[0].y] : null;
  const baseKey = JSON.stringify([item.path,item.modified]);
  const key = JSON.stringify([baseKey,target]);
  endZoom();
  cropPredictionBusy = true; updateControls();
  const request = ++cropPredictionRequest;
  try {
    if (!cropPredictionCache || cropPredictionCache.baseKey !== baseKey || (target && cropPredictionCache.key !== key)) {
      let result;
      try { result = await api('/api/crop-corners', {path:item.path,target}); }
      catch (error) {
        if (error.message === 'Not found.') throw new Error('The new corner detector needs an idle Cropper backend restart. F5 alone cannot activate it.');
        throw error;
      }
      if (ticket !== state.requestId || request !== cropPredictionRequest) return;
      cropPredictionCache = {key,baseKey,candidates:result.candidates,index:-1};
    }
    if (ticket !== state.requestId || request !== cropPredictionRequest) return;
    const cache = cropPredictionCache;
    if (!cache.candidates.length) { toast('No reliable print outline found. Place corners manually; no points were changed.',true); return; }
    cache.index = (cache.index+1) % cache.candidates.length;
    state.points = cache.candidates[cache.index].points.map(([x,y]) => ({x:Math.max(0,Math.min(state.width-1,x)),y:Math.max(0,Math.min(state.height-1,y))}));
    renderOverlay(); updateControls();
    toast(`Outline ${cache.index+1} of ${cache.candidates.length}. Ctrl+A cycles alternatives; Ctrl+Q clears. Always check the corners.`);
  } catch (error) { toast(error.message,true); }
  finally { cropPredictionBusy = false; updateControls(); }
}
function clearCropPoints() {
  if (state.busy) return;
  cropPredictionRequest++; cropPredictionCache = null;
  state.points = []; endZoom(); renderOverlay(); updateControls();
}
function selectWholeImage() {
  if (!state.width || !state.height || state.busy) return;
  state.points = [{ x: 0, y: 0 }, { x: state.width - 1, y: 0 }, { x: state.width - 1, y: state.height - 1 }, { x: 0, y: state.height - 1 }];
  renderOverlay(); updateControls();
}
async function cropSave() {
  if (state.busy || state.index < 0 || !ordered(state.points)) return;
  const problem = validSettings(); if (problem) { toast(problem,true); return; }
  state.busy = true; updateControls();
  const sourceIndex = state.index;
  try {
    const result = await api('/api/crop', { path: state.images[sourceIndex].path, points: state.points.map(p=>[p.x,p.y]), rotation: state.rotation, aspect_ratio: cropAspectRatio(), settings:{...settings,stage_routing:stageRoutingEnabled()} });
    $('save-error').classList.add('hidden');
    toast(`Saved ${result.name} · ${result.width} × ${result.height} px`);
    await refreshStageFolders();
    if (result.source_moved) {
      state.images.splice(sourceIndex,1);state.index=-1;state.points=[];renderImages();
      if (state.images.length && settings.autoNext) await openImage(Math.min(sourceIndex,state.images.length-1));
      else { clearEditor(); if (!state.images.length) $('image-title').textContent='Batch complete'; }
    }
    else if (settings.autoNext && sourceIndex + 1 < state.images.length) await openImage(sourceIndex + 1);
    else if (settings.autoNext) toast(`Batch complete. Saved ${result.name}.`);
    else { state.points = []; state.rotation = 0; renderOverlay(); }
  } catch (error) {
    if (!offerChooseOutputFolder(error)) toast(error.message,true);
  }
  finally { state.busy = false; updateControls(); }
}
function clearLiveCropPreview(message = 'Select four corners to see the result here.') {
  ++cropPreviewRequest; clearTimeout(cropPreviewTimer);
  if (cropPreviewUrl) URL.revokeObjectURL(cropPreviewUrl);
  cropPreviewUrl = null; $('live-preview-image').removeAttribute('src'); $('live-preview-image').classList.add('hidden');
  $('live-preview-status').textContent = message; $('live-preview-status').classList.remove('hidden'); $('live-preview-ratio').textContent = '—';
}
function previewRatioLabel() {
  if (settings.aspect === 'auto') return 'Measured ratio';
  if (settings.aspect === 'custom') return settings.customAspect || 'Custom ratio';
  return settings.aspect;
}
function scheduleLiveCropPreview() {
  const ready = state.index >= 0 && !state.busy && !!ordered(state.points);
  if (!ready) { clearLiveCropPreview(); return; }
  const problem = validSettings();
  if (problem) { clearLiveCropPreview(problem); return; }
  clearTimeout(cropPreviewTimer);
  const ticket = ++cropPreviewRequest;
  $('live-preview-status').textContent = 'Updating preview…'; $('live-preview-status').classList.remove('hidden');
  $('live-preview-ratio').textContent = previewRatioLabel();
  const source = state.images[state.index];
  const points = state.points.map(point => [point.x, point.y]); const rotation = state.rotation; const aspectRatio = cropAspectRatio();
  cropPreviewTimer = setTimeout(() => renderLiveCropPreview(ticket, source, points, rotation, aspectRatio), 180);
}
async function renderLiveCropPreview(ticket, source, points, rotation, aspectRatio) {
  try {
    const response = await fetch('/api/crop-preview', { method:'POST', headers:{'X-Cropper-Token':state.token,'Content-Type':'application/json'}, body:JSON.stringify({path:source.path,points,rotation,aspect_ratio:aspectRatio}) });
    if (!response.ok) throw new Error((await response.json()).error || 'Could not make the preview.');
    const blob = await response.blob();
    if (ticket !== cropPreviewRequest) return;
    if (cropPreviewUrl) URL.revokeObjectURL(cropPreviewUrl);
    cropPreviewUrl = URL.createObjectURL(blob);
    $('live-preview-image').src = cropPreviewUrl; $('live-preview-image').classList.remove('hidden'); $('live-preview-status').classList.add('hidden');
  } catch (error) { if (ticket === cropPreviewRequest) $('live-preview-status').textContent = error.message; }
}

// Before and after previews share one zoom: scroll zooms both around the
// pointer, drag pans both, double-click resets. Both images fill identically
// sized panes with object-fit: contain, so one transform lines them up.
function syncedZoom(beforeId, afterId, paused = () => false) {
  const images = [$(beforeId), $(afterId)];
  const frames = images.map(image => {
    if (image.parentElement.classList.contains('restore-before-wrap')) { image.parentElement.classList.add('zoom-frame'); return image.parentElement; }
    const frame = document.createElement('div'); frame.className = 'zoom-frame'; image.replaceWith(frame); frame.append(image); return frame;
  });
  const view = { scale: 1, x: 0, y: 0 }; let drag = null;
  const apply = () => {
    for (const image of images) image.style.transform = view.scale === 1 ? '' : `translate(${view.x}px, ${view.y}px) scale(${view.scale})`;
    for (const frame of frames) frame.classList.toggle('zoomed', view.scale > 1);
  };
  const clamp = frame => {
    view.x = Math.min(0, Math.max(frame.clientWidth * (1 - view.scale), view.x));
    view.y = Math.min(0, Math.max(frame.clientHeight * (1 - view.scale), view.y));
  };
  const reset = () => { view.scale = 1; view.x = 0; view.y = 0; drag = null; apply(); };
  for (const frame of frames) {
    frame.title = 'Scroll to zoom both photos · drag to move · double-click to reset';
    frame.addEventListener('wheel', event => {
      if (paused()) return;
      event.preventDefault();
      const rect = frame.getBoundingClientRect(); const x = event.clientX - rect.left, y = event.clientY - rect.top;
      const scale = Math.min(12, Math.max(1, view.scale * Math.exp(-event.deltaY * 0.0015)));
      view.x = x - (x - view.x) * scale / view.scale; view.y = y - (y - view.y) * scale / view.scale; view.scale = scale;
      clamp(frame); apply();
    }, { passive: false });
    frame.addEventListener('pointerdown', event => {
      if (view.scale === 1 || event.button !== 0 || paused()) return;
      drag = { x: event.clientX - view.x, y: event.clientY - view.y }; frame.setPointerCapture(event.pointerId); frame.classList.add('dragging'); event.preventDefault();
    });
    frame.addEventListener('pointermove', event => { if (!drag) return; view.x = event.clientX - drag.x; view.y = event.clientY - drag.y; clamp(frame); apply(); });
    const end = () => { drag = null; frame.classList.remove('dragging'); };
    frame.addEventListener('pointerup', end); frame.addEventListener('pointercancel', end);
    frame.addEventListener('dblclick', () => { if (!paused()) reset(); });
  }
  return reset;
}
function chatgptReadyPaths(selection, includeQueued = false) {
  const current = state.chatgpt;
  const blocked = new Set([
    ...current.pending.map(item => item.front_path),
    ...(current.batchPaths || []),
    ...(!includeQueued ? current.queuedPaths : [])
  ]);
  const compositePaths = new Set(photoLinks.map(link => link.composite_path));
  return state.images.map(image => image.path).filter(path => selection.has(path) && !blocked.has(path) && !compositePaths.has(path) && (colorEligible(path) || (includeQueued && current.queuedPaths.has(path))));
}
function updateChatgptControls() {
  renderChatgptFailures();
  const current = state.chatgpt;
  const correction = current.resubmissions?.get(current.review?.front_path);
  const reviewLocked = !!current.reviewBusy || !!correction;
  const flat = stageRoutingEnabled();
  $('chatgpt-finish').disabled = !current.selected.size || current.busy || !!current.reviewBusy || state.enhance.busy || !!current.queuedPaths.size;
  const validQueued = new Set(chatgptReadyPaths(current.queuedPaths, true));
  for (const path of current.queuedPaths) if (!validQueued.has(path)) current.queuedPaths.delete(path);
  const colorizing=chatgptOperation==='colorize';
  $('chatgpt-title').textContent=colorizing?'ChatGPT Image Colorization':'ChatGPT Image Restoration';
  document.querySelector('#chatgpt-workspace .editor-title p').textContent=colorizing?'Colour only: original brightness and detail are retained locally. Colours are plausible estimates; review before accepting.':'Restore standalone photos or approved linked pairs. Only the front goes to AI; the back is preserved locally.';
  document.querySelector('#chatgpt-settings h2').textContent=colorizing?'Colour only · preserve detail':'Faithful photo edit';
  document.querySelector('#chatgpt-settings .eyebrow').textContent=colorizing?'CHATGPT IMAGE COLORIZATION':'CHATGPT IMAGE RESTORATION';
  if (flat) document.querySelector('#chatgpt-settings .settings-intro').textContent = colorizing ?
    'Accepted colourised PNGs go to sibling 4.1.1. AI Colourised with _col. Earlier versions and skipped-stage snapshots are preserved. Colours are estimates; source brightness and detail are retained.' :
    'Accepted AI PNGs go to sibling 1.7. TBP AI-Improved with _ai. Incoming versions go to 3. Auto-Improved, or 2. Cropped and 3.2. Skipped Auto-Improved. Rejected results leave sources untouched.';
  $('colorize-toolbar').classList.toggle('hidden',!colorizing);
  document.querySelector('#chatgpt-pairs-open + .aspect-note').textContent=`Confirm links before generating. One paid front ${colorizing?'colorization':'restoration'} produces two saved versions.`;
  updateColorizeToolbarControls();
  const queueLabel=$('photo-pairs-auto-queue').parentElement;
  if(queueLabel.lastChild.nodeType===3) queueLabel.lastChild.textContent=` Queue the front for paid AI ${colorizing?'colorization':'restoration'} immediately when I approve a link`;
  const filing=$('photo-pairs-dialog').querySelectorAll(':scope > p')[1];
  if (flat && filing) filing.textContent = `New paired results follow flat stage routing together. Accepted ${colorizing?'colourised files append _col in 4.1.1. AI Colourised':'AI files append _ai in 1.7. TBP AI-Improved'}. All results, including older pending attempts, use this same flat workflow.`;
  $('chatgpt-concurrency').disabled = current.busy;
  $('chatgpt-pairs-open').disabled = !!current.reviewBusy;
  $('chatgpt-pairs-generate').disabled = !!current.reviewBusy || !chatgptReadyPaths(selectedPairs).length || colorScanning || (colorizing && colorScanFolder!==state.folder);
  $('chatgpt-review-combined').classList.toggle('hidden', !current.review?.link);
  $('chatgpt-review-combined').textContent = combinedReview ? 'View standalone front' : 'View rebuilt front + back';
  const eligible = chatgptReadyPaths(current.selected).length;
  const queued = chatgptReadyPaths(current.queuedPaths, true).length;
  $('chatgpt-selection').textContent = `${current.busy ? `${current.completed} of ${current.total} generated · ` : ''}${eligible} selected · ${queued} queued · ${current.pending.length} pending review${current.resubmissions?.size ? ` · ${current.resubmissions.size} modifications running` : ''}`;
  const keyHint = $('chatgpt-key-hint');
  if (keyHint?.classList) keyHint.classList.toggle('hidden', state.hasKey !== false);
  $('chatgpt-generate').disabled = !(eligible || (!current.busy && queued)) || current.reviewBusy || colorScanning || (colorizing && colorScanFolder!==state.folder) || state.hasKey === false;
  $('chatgpt-generate').firstElementChild.textContent = current.busy
    ? `Queue ${eligible} for next batch (paid)`
    : `Generate ${eligible + queued} ${eligible + queued === 1 ? 'photo' : 'photos'} (paid)`;
  $('chatgpt-accept').classList.toggle('hidden', !current.review);
  $('chatgpt-reject').classList.toggle('hidden', !current.review);
  $('chatgpt-accept').disabled = reviewLocked;
  $('chatgpt-reject').disabled = reviewLocked;
  const otherAttempts = current.review ? current.pending.filter(item => item.front_path === current.review.front_path && item.id !== current.review.id).length : 0;
  $('chatgpt-accept').textContent = otherAttempts === 1 ? 'Accept this version and discard the other 1 attempt'
    : otherAttempts > 1 ? `Accept this version and discard the other ${otherAttempts} attempts`
    : 'Accept and file result';
  const recovered = current.recovered || [];
  $('chatgpt-recovered')?.classList.toggle('hidden', !recovered.length);
  if ($('chatgpt-recovered-count')) $('chatgpt-recovered-count').textContent = recovered.length;
  const recoveredList = $('chatgpt-recovered-list');
  if (recoveredList?.replaceChildren) {
    recoveredList.replaceChildren();
    for (const item of recovered) {
      const row = document.createElement('div');
      row.className = 'recovered-item';
      const title = document.createElement('div');
      const name = (item.front_path || item.id).split(/[\\/]/).pop();
      title.textContent = item.image_readable
        ? `${name} · ${item.reason}`
        : `${name} · damaged · ${item.reason}`;
      row.append(title);
      if (item.notes?.length) {
        const notes = document.createElement('p');
        notes.className = 'recovered-notes';
        notes.textContent = item.notes.join(' / ');
        row.append(notes);
      }
      const actions = document.createElement('div');
      actions.className = 'chatgpt-zoom-tools';
      if (item.can_keep) {
        const keep = document.createElement('button');
        keep.className = 'button button-quiet';
        keep.textContent = 'Keep';
        keep.addEventListener('click', () => resolveRecovered(item, true));
        actions.append(keep);
      }
      const discard = document.createElement('button');
      discard.className = 'button button-quiet';
      discard.textContent = 'Discard';
      discard.addEventListener('click', () => resolveRecovered(item, false));
      actions.append(discard);
      row.append(actions);
      recoveredList.append(row);
    }
  }
  $('chatgpt-review-actions').classList.toggle('hidden', !current.review);
  $('chatgpt-modify').disabled = reviewLocked;
  $('chatgpt-modify').setAttribute('aria-expanded', String(!!current.review && !!current.correctionOpen));
  $('chatgpt-modify').textContent = current.correctionOpen ? 'Hide modifications' : 'Modify image';
  $('chatgpt-correction').classList.toggle('hidden', !current.review || !current.correctionOpen);
  $('chatgpt-comment').disabled = reviewLocked;
  $('chatgpt-resubmit-input').disabled = reviewLocked;
  $('chatgpt-resubmit').disabled = !current.review || reviewLocked || !$('chatgpt-comment').value.trim();
  $('chatgpt-resubmit').textContent = correction ? 'Waiting for corrected image…' : 'Resubmit with following comment (paid)';
  if (correction) $('chatgpt-resubmit-status').textContent = correction.status;
  $('chatgpt-select-all').disabled = !chatgptReadyPaths(new Set(state.images.map(image => image.path))).length || !!current.reviewBusy;
  $('chatgpt-clear').disabled = !current.selected.size || !!current.reviewBusy;
  $('chatgpt-clear-queued').classList.toggle('hidden', !queued);
  $('chatgpt-clear-queued').disabled = !!current.reviewBusy;
  $('chatgpt-stop').classList.toggle('hidden', !current.busy);
  $('chatgpt-stop').disabled = current.stop;
  $('chatgpt-stop').textContent = current.stop ? 'Waiting for active requests…' : 'Stop new requests';
  $('chatgpt-pending-count').textContent = current.pending.length;
  const list = $('chatgpt-pending-list'); list.replaceChildren();
  for (const item of current.pending) {
    const button = document.createElement('button'); button.textContent = `${item.front_path.split(/[\\/]/).pop()} · Attempt ${item.attempt || 1}${current.resubmissions?.has(item.front_path) ? ' · Modifying…' : ''}`;
    button.title = [item.front_path, ...(item.comments || [])].join('\n'); button.classList.toggle('active', current.review?.id === item.id);
    button.disabled = !!current.reviewBusy; button.addEventListener('click', () => reviewChatgptItem(item)); list.append(button);
  }
}
function clearChatgptPreview() {
  $('chatgpt-original').removeAttribute('src'); $('chatgpt-result').removeAttribute('src');
  $('chatgpt-preview').classList.add('hidden'); $('chatgpt-empty').classList.remove('hidden');
  $('chatgpt-result').classList.add('hidden'); $('chatgpt-divider').classList.add('hidden'); $('chatgpt-slider').classList.add('hidden');
}
function showChatgptPreview() {
  const current = state.chatgpt;
  const zoomKey = `${current.preview || ''}:${current.review?.id || ''}`;
  if (zoomKey !== chatgptZoomKey) { chatgptZoomKey = zoomKey; resetChatgptZoom(); }
  if (!current.preview) { clearChatgptPreview(); return; }
  $('chatgpt-original').src = url('/api/image', combinedReview && current.review?.link ? current.review.link.composite_path : current.preview);
  $('chatgpt-preview-label').textContent = current.review ? `ORIGINAL ←  DRAG TO COMPARE  → AI ${chatgptOperation==='colorize'?'COLORIZED':'RESTORED'} · ${current.preview.split(/[\\/]/).pop()}` : `ORIGINAL · ${current.preview.split(/[\\/]/).pop()}`;
  const comparing = !!current.review;
  if (comparing) $('chatgpt-result').src = `/api/chatgpt-preview?id=${encodeURIComponent(current.review.id)}&part=${combinedReview ? 'combined' : 'front'}&token=${encodeURIComponent(state.token)}`;
  else $('chatgpt-result').removeAttribute('src');
  $('chatgpt-result').classList.toggle('hidden', !comparing);
  $('chatgpt-divider').classList.toggle('hidden', !comparing);
  $('chatgpt-slider').classList.toggle('hidden', !comparing);
  $('chatgpt-empty').classList.add('hidden'); $('chatgpt-preview').classList.remove('hidden');
}
async function refreshChatgptPending() {
  if (!state.folder) return;
  try {
    const response = await api(`/api/chatgpt-pending?path=${encodeURIComponent(state.folder)}`);
    state.chatgpt.pending = response.items.filter(item=>(item.operation || 'restore')===chatgptOperation);
    state.chatgpt.recovered = response.recovered || [];
    try { photoLinks = (await api(`/api/photo-links?path=${encodeURIComponent(state.folder)}`)).links; renderPhotoLinks(); }
    catch (_) { photoLinks = []; renderPhotoLinks(); }
    if(chatgptOperation==='colorize' && colorScanFolder!==state.folder && !colorScanning) scanColorCandidates();
    updateChatgptControls();
  } catch (error) { toast(`Could not load pending results: ${error.message}`, true); }
}
function reviewChatgptItem(item) {
  if (state.chatgpt.reviewBusy) return;
  state.chatgpt.review = item; state.chatgpt.preview = item.front_path;
  state.chatgpt.correctionOpen = false;
  $('chatgpt-comment').value = chatgptCommentDrafts.get(item.id) || '';
  $('chatgpt-comment').title = item.comments?.length ? `Previous corrections: ${item.comments.join(' / ')}` : '';
  $('chatgpt-resubmit-status').textContent = item.resubmitNotice || `Reviewing attempt ${item.attempt || 1}. No corrected request is currently being sent.`;
  combinedReview = false;
  $('chatgpt-slider').value = 50; $('chatgpt-preview').style.setProperty('--split', '50%');
  showChatgptPreview(); renderImages(); updateChatgptControls();
  if (!state.chatgpt.busy) $('chatgpt-progress').textContent = 'Drag the divider left or right. Accept or reject this locally saved result; no new API call is made.';
}
function selectChatgptImage(index, event) {
  const current = state.chatgpt;
  if (current.reviewBusy) { toast('Finish the current review action before changing selection.', true); return; }
  const path = state.images[index].path;
  if(!colorEligible(path)){toast('Not a suggested monochrome front. Enable manual uncertain-image selection if detection missed it.',true);return;}
  if (!chatgptReadyPaths(new Set([path])).length) { toast('This photo is already processing, queued, or pending review.', true); return; }
  if (event.shiftKey && current.anchor >= 0) {
    if (!event.ctrlKey && !event.metaKey) current.selected.clear();
    for (let i = Math.min(current.anchor, index); i <= Math.max(current.anchor, index); i++) if(chatgptReadyPaths(new Set([state.images[i].path])).length) current.selected.add(state.images[i].path);
  } else if (event.ctrlKey || event.metaKey) {
    if (current.selected.has(path)) current.selected.delete(path); else current.selected.add(path);
    current.anchor = index;
  } else {
    current.selected = new Set([path]); current.anchor = index;
  }
  current.preview = path; current.review = null;
  showChatgptPreview(); renderImages(); updateChatgptControls();
  if (!current.busy) $('chatgpt-progress').textContent = `Ready. Up to ${chatgptConcurrency} parallel paid API edits.`;
}
function renderChatgptFailures() {
  const failures = state.chatgpt.failures || new Map();
  $('chatgpt-failures').classList.toggle('hidden', !failures.size);
  $('chatgpt-failure-count').textContent = failures.size;
  $('chatgpt-failure-list').textContent = [...failures].map(([path, message]) =>
    `${path.split(/[\\/]/).pop()}: ${message}`).join('\n\n');
  const retry = $('chatgpt-retry-failed');
  if (retry?.classList) retry.classList.toggle('hidden', !failures.size);
}
async function runChatgptQueue(paths, current, request, saved, progress, wait = ms => new Promise(resolve => setTimeout(resolve, ms)), concurrency = 3, failed = () => {}) {
  let next = 0, active = 0, failure = '';
  async function worker() {
    while (!current.stop && !failure && next < paths.length) {
      const path = paths[next++];
      if (current.stop || failure) return;
      active++; progress(active, path);
      try {
        const item = await request(path);
        saved(item, path);
      } catch (error) {
        const accountError = [401, 403].includes(error.status) ||
          [error.code, error.type].some(code => ['insufficient_quota', 'billing_hard_limit_reached', 'billing_not_active', 'usage_limit_reached', 'invalid_api_key', 'model_not_found'].includes(code));
        failed(path, error);
        // Server already retried a rate-limit 429 once. Offer a visible retry; do not loop here.
        if (accountError) failure = error.message;
      } finally { active--; progress(active, path, false); }
    }
  }
  await Promise.all(Array.from({ length: Math.min(Math.max(1, Math.min(10, Math.floor(concurrency) || 3)), paths.length) }, worker));
  return failure;
}
async function generateChatgpt(selection = state.chatgpt.selected) {
  const current = state.chatgpt;
  if (current.reviewBusy || colorScanning || (chatgptOperation === 'colorize' && colorScanFolder !== state.folder)) return;
  const chosen = chatgptReadyPaths(selection);
  if (current.busy) {
    for (const path of chosen) { current.queuedPaths.add(path); current.selected.delete(path); }
    if (chosen.length) $('chatgpt-progress').textContent = `${chosen.length} added to the next paid batch. ${current.queuedPaths.size} queued; the current batch continues.`;
    renderImages(); updateChatgptControls(); return;
  }
  let paths = chatgptReadyPaths(new Set([...current.queuedPaths, ...selection]), true);
  if (!paths.length) return;
  current.queuedPaths.clear();
  current.busy = true; current.stop = false;
  current.failures = new Map();
  const concurrency = chatgptConcurrency;
  const stageRouting = stageRoutingEnabled();
  let summary = '';
  let allGenerated = 0, allPlanned = 0;
  while (paths.length) {
    for (const path of paths) current.selected.delete(path);
    current.total = paths.length; current.completed = 0; current.batchPaths = paths;
    const savedPaths = new Set();
    renderImages(); updateChatgptControls();
    const operation = chatgptOperation;
    const failure = await runChatgptQueue(paths, current,
      path => api('/api/chatgpt-generate', { front_path: path, operation, stage_routing:stageRouting }),
      (item, path) => { current.pending.push(item); savedPaths.add(path); current.completed++; updateChatgptControls(); },
      (active, path, cooling) => {
        if (!current.preview) { current.preview = path; showChatgptPreview(); renderImages(); }
        $('chatgpt-progress').textContent = `${current.completed} of ${current.total} saved locally · ${current.failures.size} failed · ${active} active · ${current.stop ? 'Stopping; active requests will finish' : cooling ? 'Rate limited; waiting before retry' : `Up to ${concurrency} parallel requests`}`;
      }, undefined, concurrency, (path, error) => { current.failures.set(path, error.message); if (typeof offerChooseOutputFolder === 'function') offerChooseOutputFolder(error); updateChatgptControls(); });
    const generated = current.completed, planned = current.total, stopped = current.stop;
    allGenerated += generated; allPlanned += planned;
    current.batchPaths = null;
    for (const path of paths) if (!savedPaths.has(path)) current.selected.add(path);
    if (failure || stopped) {
      if (failure) toast(failure, true);
      summary = failure ? `Stopped after ${allGenerated} of ${allPlanned}: ${failure}. Unprocessed photos remain selected; queued photos can be started manually.` : `Stopped after ${allGenerated} of ${allPlanned}. Unprocessed photos remain selected; queued photos were cleared.`;
      break;
    }
    paths = chatgptReadyPaths(current.queuedPaths, true);
    current.queuedPaths.clear();
    if (paths.length) $('chatgpt-progress').textContent = `Starting ${paths.length} queued ${paths.length === 1 ? 'photo' : 'photos'} as the next paid batch.`;
    else summary = `Generation complete: ${allGenerated} of ${allPlanned} saved locally.${current.failures.size ? ` ${current.failures.size} failed photos remain selected for manual retry.` : ''} Review pending results separately.`;
  }
  current.busy = false; current.total = 0; current.stop = false; current.batchPaths = null;
  if (current.review || current.reviewBusy) { /* Keep the user's active comparison. */ }
  else if (current.pending.length) reviewChatgptItem(current.pending[0]);
  else { current.preview = [...current.selected][0] || null; showChatgptPreview(); }
  renderImages(); updateChatgptControls();
  $('chatgpt-progress').textContent = summary;
  $('chatgpt-progress').title = [...current.failures].map(([path, message]) => `${path}: ${message}`).join('\n');
  if (current.failures.size && !summary.startsWith('Stopped')) {
    const [path, message] = current.failures.entries().next().value;
    toast(`${path.split(/[\\/]/).pop()}: ${message} See Failed photos to retry.`, true);
  }
}
function retryFailedChatgpt() {
  const current = state.chatgpt;
  const failures = current.failures;
  if (!failures?.size || current.busy || current.reviewBusy || state.hasKey === false) return;
  const paths = new Set(failures.keys());
  current.failures = new Map();
  for (const path of paths) current.selected.add(path);
  generateChatgpt(paths);
}
async function resolveRecovered(item, keep) {
  try {
    await api(keep ? '/api/chatgpt-recovered-keep' : '/api/chatgpt-recovered-discard', { id: item.id });
    toast(keep ? 'Recovered result kept for review.' : 'Recovered result discarded.');
    await refreshChatgptPending();
    showChatgptPreview();
  } catch (error) { toast(error.message, true); }
}
async function finishChatgpt(accept) {
  const current = state.chatgpt;
  if (!current.review || current.reviewBusy || current.resubmissions?.has(current.review.front_path)) return;
  const reviewed = current.review;
  current.reviewBusy = true; updateChatgptControls();
  try {
    await api(accept ? '/api/chatgpt-accept' : '/api/chatgpt-reject', { id: reviewed.id });
    const path = reviewed.front_path;
    const reviewIndex = current.pending.findIndex(item => item.id === reviewed.id);
    const nextCandidates = reviewIndex < 0 ? current.pending : [
      ...current.pending.slice(reviewIndex + 1),
      ...current.pending.slice(0, reviewIndex).reverse()
    ];
    current.pending = current.pending.filter(item => accept ? item.front_path !== path : item.id !== reviewed.id);
    current.review = null;
    if (accept) { state.images = state.images.filter(image => image.path !== path && image.path !== reviewed.link?.composite_path); $('image-count').textContent = state.images.length; current.selected.delete(path); current.queuedPaths.delete(path); selectedPairs.delete(path); renderPhotoLinks(); }
    current.reviewBusy = false;
    current.anchor = -1;
    const nextReview = nextCandidates.find(item => current.pending.some(pending => pending.id === item.id));
    if (nextReview) reviewChatgptItem(nextReview);
    else { current.preview = [...current.selected][0] || null; showChatgptPreview(); }
    renderImages(); updateChatgptControls();
    if (!current.busy) $('chatgpt-progress').textContent = accept ? 'Accepted: PNG filed and original archived. Review the next pending image when ready.' : 'Rejected: original untouched. Review the next pending image when ready.';
  } catch (error) { if (typeof offerChooseOutputFolder !== 'function' || !offerChooseOutputFolder(error)) toast(error.message, true); if (!current.busy) $('chatgpt-progress').textContent = error.message; }
  finally { current.reviewBusy = false; updateChatgptControls(); }
}
function confirmChatgptResubmit(reviewed, comment, includePrevious) {
  const dialog = $('chatgpt-resubmit-dialog');
  $('chatgpt-resubmit-summary').textContent = `${reviewed.front_path.split(/[\\/]/).pop()}\n${includePrevious ? 'Original + current AI attempt' : 'Original only'} + correction:\n${comment}`;
  dialog.returnValue = 'cancel';
  return new Promise(resolve => {
    const onClose = () => resolve(dialog.returnValue === 'send');
    dialog.addEventListener('close', onClose, {once:true});
    try { dialog.showModal(); }
    catch (error) { dialog.removeEventListener('close', onClose); resolve(false); }
  });
}
async function resubmitChatgpt() {
  const current = state.chatgpt;
  const reviewed = current.review;
  const comment = $('chatgpt-comment').value.trim();
  if (!reviewed || current.reviewBusy || current.resubmissions?.has(reviewed.front_path) || !comment) return;
  const includePrevious = $('chatgpt-resubmit-input').value === 'both';
  current.reviewBusy = true; updateChatgptControls();
  let confirmationLocked = true;
  const status = $('chatgpt-resubmit-status');
  status.textContent = 'Awaiting your confirmation. No request has been sent yet.';
  try {
    if (!await confirmChatgptResubmit(reviewed, comment, includePrevious)) {
      status.textContent = 'Not sent: confirmation was cancelled or could not open. No paid request was made.';
      return;
    }
    const job = {status: 'Corrected request sent. Waiting for AI and local save… You can select and approve other pending images while it runs. Keep this page open.'};
    (current.resubmissions ||= new Map()).set(reviewed.front_path, job);
    current.reviewBusy = false; confirmationLocked = false;
    status.textContent = job.status;
    updateChatgptControls();
    if (!current.busy) $('chatgpt-progress').textContent = status.textContent;
    const item = await api('/api/chatgpt-resubmit', {id:reviewed.id, comment, include_previous:includePrevious});
    if (!item?.id || !item.attempt) throw new Error('The server did not return a saved attempt. Refresh Pending Review before trying again.');
    if (!current.pending.some(pending => pending.id === item.id)) current.pending.push(item);
    chatgptCommentDrafts.delete(reviewed.id);
    current.resubmissions.delete(reviewed.front_path);
    reviewed.resubmitNotice = `Attempt ${item.attempt} saved in Pending Review. Earlier attempts remain available.`;
    if (!current.reviewBusy && current.review?.id === reviewed.id) {
      reviewChatgptItem(item);
      status.textContent = `Attempt ${item.attempt} saved and opened for review. Earlier attempts remain in Pending Review.`;
    }
    toast(`${reviewed.front_path.split(/[\\/]/).pop()}: attempt ${item.attempt} saved in Pending Review.`);
  } catch (error) {
    reviewed.resubmitNotice = `No new attempt received: ${error.message}\nDo not repeat the paid request until you have checked Pending Review.`;
    if (current.review?.id === reviewed.id) status.textContent = reviewed.resubmitNotice;
    toast(error.message, true);
    if (!current.busy && current.review?.id === reviewed.id) $('chatgpt-progress').textContent = error.message;
  } finally {
    current.resubmissions?.delete(reviewed.front_path);
    if (confirmationLocked) current.reviewBusy = false;
    updateChatgptControls();
  }
}
function renderPhotoLinks() {
  const list = $('chatgpt-pair-list'); list.replaceChildren();
  const available = new Set(state.images.map(image => image.path));
  selectedPairs = new Set([...selectedPairs].filter(path => available.has(path)));
  for (const link of photoLinks.filter(item => available.has(item.front_path) && available.has(item.composite_path) && colorEligible(item.front_path))) {
    const label = document.createElement('label'), checkbox = document.createElement('input');
    checkbox.type = 'checkbox'; checkbox.checked = selectedPairs.has(link.front_path); checkbox.disabled = !!state.chatgpt.reviewBusy;
    checkbox.addEventListener('change', () => { if (checkbox.checked) selectedPairs.add(link.front_path); else selectedPairs.delete(link.front_path); updateChatgptControls(); });
    label.append(checkbox, document.createTextNode(` ${link.front_path.split(/[\\/]/).pop()} ↔ back (approved)`)); list.append(label);
  }
  updateChatgptControls();
}
function queueApprovedPair(link) {
  const current=state.chatgpt;
  if(!colorEligible(link.front_path)){toast('Link saved, but this front is not suggested for colorization. No paid request started.',true);return;}
  const pending=current.pending.find(item => item.front_path===link.front_path);
  if(pending) { pending.link=link; toast('Existing AI result linked for review; no extra API call.'); return; }
  if(current.busy) {
    if(current.batchPaths.includes(link.front_path)) {toast('Link saved for the current batch; no extra API call.');return;}
    if(current.stop) {toast('Batch is stopping. Pair saved; select it for the next batch.',true);return;}
    if(!current.queuedPaths.has(link.front_path)) current.queuedPaths.add(link.front_path);
    updateChatgptControls();renderImages();toast('Approved front queued for the next paid batch.');
  } else generateChatgpt(new Set([link.front_path]));
}
function suggestedManualPairLayout(info) { return info.width >= info.height ? 'below' : 'right'; }
function updatePhotoPairApproveLabels() {
  for (const button of $('photo-pairs-dialog').querySelectorAll('button[data-pair-approve]')) {
    if (!button.disabled) button.textContent = $('photo-pairs-auto-queue').checked ? 'Approve link + queue AI (paid)' : 'Approve link (free)';
  }
}
function updateManualPairGallery() {
  for (const tile of $('photo-pairs-manual-gallery').children) {
    const role = tile.dataset.path === manualPairFront ? 'Front' : tile.dataset.path === manualPairBack ? 'Back' : '';
    tile.classList.toggle('manual-front', role === 'Front');
    tile.classList.toggle('manual-back', role === 'Back');
    tile.setAttribute('aria-pressed', String(Boolean(role)));
    tile.querySelector('.photo-pair-role').textContent = role;
  }
  const name = path => path?.split(/[\\/]/).pop();
  $('photo-pairs-manual-step').textContent = !manualPairFront
    ? 'Select a standalone front, then its combined front/back image.'
    : !manualPairBack
      ? `Front: ${name(manualPairFront)}. Now select its combined front/back image.`
      : `Front: ${name(manualPairFront)} · Back: ${name(manualPairBack)}. Review the suggested layout and boundary, then approve.`;
}
function resetManualPairSelection() {
  manualPairFront = manualPairBack = null;
  ++manualPairRequest;
  $('photo-pairs-manual-preview').replaceChildren();
  updateManualPairGallery();
}
function renderManualPairGallery() {
  const gallery = $('photo-pairs-manual-gallery'); gallery.replaceChildren();
  for (const image of state.images) {
    const tile = document.createElement('button'); tile.type = 'button'; tile.className = 'photo-pair-thumb';
    tile.dataset.path = image.path; tile.title = image.name;
    const picture = document.createElement('img'); picture.loading = 'lazy'; picture.alt = ''; picture.src = url('/api/thumb', image.path);
    const role = document.createElement('span'); role.className = 'photo-pair-role';
    const name = document.createElement('span'); name.className = 'photo-pair-thumb-name'; name.textContent = image.name;
    tile.append(picture, role, name); tile.addEventListener('click', () => selectManualPairImage(image.path)); gallery.append(tile);
  }
  updateManualPairGallery();
}
async function selectManualPairImage(path) {
  if (!manualPairFront || path === manualPairFront) {
    if (path === manualPairFront) { resetManualPairSelection(); return; }
    if (!colorEligible(path)) { toast('This image is not eligible as a colorization front. Enable uncertain images manually if needed.', true); return; }
    manualPairFront = path; manualPairBack = null; ++manualPairRequest;
    $('photo-pairs-manual-preview').replaceChildren(); updateManualPairGallery(); return;
  }
  manualPairBack = path;
  const front = manualPairFront, request = ++manualPairRequest;
  const preview = $('photo-pairs-manual-preview'); preview.replaceChildren(); updateManualPairGallery();
  try {
    const info = await api(`/api/info?path=${encodeURIComponent(front)}`);
    if (request !== manualPairRequest || front !== manualPairFront || path !== manualPairBack) return;
    photoPairCard({front_path:front, composite_path:path, split:50, layout:suggestedManualPairLayout(info)}, preview, () => {
      resetManualPairSelection();
      $('photo-pairs-manual-gallery').scrollIntoView({block:'nearest'});
    });
    preview.scrollIntoView({block:'nearest'});
  } catch (error) {
    if (request !== manualPairRequest) return;
    manualPairBack = null; updateManualPairGallery(); toast(error.message, true);
  }
}
function photoPairCard(item, target, onApproved) {
  const card = document.createElement('div'); card.className = 'photo-pair-card';
  const pictures = document.createElement('div'); pictures.className = 'photo-pair-pictures';
  let combined, boundary;
  for (const [path, title] of [[item.front_path, 'Standalone front — sent to AI'], [item.composite_path, 'Combined — rebuilt locally, NOT sent to AI']]) {
    const panel = document.createElement('div'), heading = document.createElement('p'), frame = document.createElement('div'), image = document.createElement('img');
    heading.textContent = `${title}: ${path.split(/[\\/]/).pop()}`;
    image.src = url('/api/image', path); image.alt = title; frame.append(image); panel.append(heading, frame); pictures.append(panel);
    if (path === item.composite_path) { combined = frame; frame.className = 'photo-pair-composite'; boundary = document.createElement('div'); boundary.className = 'photo-pair-boundary'; frame.append(boundary); }
  }
  const controls = document.createElement('div'); controls.className = 'photo-pair-controls';
  const layout = document.createElement('select');
  for (const [value,text] of [['right','Back on right'],['below','Back below']]) { const option = document.createElement('option'); option.value=value; option.textContent=text; layout.append(option); }
  layout.value=item.layout;
  const split = document.createElement('input'); split.type='number'; split.min=10; split.max=90; split.value=item.split; split.setAttribute('aria-label','Front ends at percent');
  const draw = () => { boundary.style.cssText = layout.value === 'right' ? `left:${split.value}%;top:0;bottom:0;width:2px` : `top:${split.value}%;left:0;right:0;width:100%;height:2px;bottom:auto`; };
  layout.addEventListener('change',draw); split.addEventListener('input',draw); draw();
  const approve = document.createElement('button'); approve.className='button button-primary'; approve.dataset.pairApprove='true';
  approve.addEventListener('click', async () => {
    approve.disabled=true;
    const autoQueue=$('photo-pairs-auto-queue').checked;
    try { const link = await api('/api/photo-link', {...item, split:Number(split.value), layout:layout.value}); photoLinks = photoLinks.filter(old => old.front_path !== link.front_path); photoLinks.push(link); selectedPairs.add(link.front_path); const pending=state.chatgpt.pending.find(item=>item.front_path===link.front_path);if(pending)pending.link=link;renderPhotoLinks(); approve.textContent='Link approved ✓'; layout.disabled=split.disabled=true;if(autoQueue)queueApprovedPair(link);if(onApproved)onApproved(); }
    catch(error) {toast(error.message,true);approve.disabled=false;}
  });
  controls.append(layout, document.createTextNode('Front ends at %:'), split, approve); card.append(pictures,controls); target.append(card); updatePhotoPairApproveLabels();
}
function wire() {
  $('enhance-finish').addEventListener('click',()=>finishStageSelection('enhance'));
  $('chatgpt-finish').addEventListener('click',()=>finishStageSelection('chatgpt'));
  $('chatgpt-pairs-open').addEventListener('click', () => {
    renderManualPairGallery(); resetManualPairSelection();
    $('photo-pairs-dialog').showModal();
  });
  $('photo-pairs-close').addEventListener('click', () => $('photo-pairs-dialog').close());
  $('photo-pairs-auto-queue').addEventListener('change', updatePhotoPairApproveLabels);
  $('photo-pairs-scan').addEventListener('click', async () => {
    $('photo-pairs-scan').disabled=true; $('photo-pairs-status').textContent='Comparing thumbnails locally… no API calls.';
    try { const response=await api('/api/photo-pair-suggestions',{path:state.folder}); const items=response.items.filter(item=>colorEligible(item.front_path));const list=$('photo-pairs-suggestions');list.replaceChildren();for(const item of items) photoPairCard(item,list);$('photo-pairs-status').textContent=`${items.length} suggestions for this operation. Check each pair and boundary before approval. Use manual pairing if missing.`; }
    catch(error) {$('photo-pairs-status').textContent=`${error.message} — this feature needs the updated server.`;}
    finally {$('photo-pairs-scan').disabled=false;}
  });
  $('photo-pairs-manual-reset').addEventListener('click', resetManualPairSelection);
  $('chatgpt-pairs-generate').addEventListener('click', () => generateChatgpt(new Set(selectedPairs)));
  $('chatgpt-review-combined').addEventListener('click', () => {combinedReview=!combinedReview; resetChatgptZoom();showChatgptPreview();updateChatgptControls();});
  resetEnhanceZoom = syncedZoom('enhance-before', 'enhance-after');
  resetRestoreZoom = syncedZoom('restore-before', 'restore-after', () => state.restore.brush);
  loadSettings();
  loadMergeSettings();
  loadEnhanceSettings();
  ['open-folder-top','folder-browse','empty-open'].forEach(id => $(id).addEventListener('click',()=>pickFolder()));
  $('crop-tab').addEventListener('click',()=>setView('crop'));
  $('merge-tab').addEventListener('click',()=>setView('merge'));
  $('enhance-tab').addEventListener('click',()=>setView('enhance'));
  $('chatgpt-tab').addEventListener('click',()=>switchChatgptOperation('restore'));
  $('colorize-tab').addEventListener('click',()=>switchChatgptOperation('colorize'));
  $('colorize-scan').addEventListener('click',scanColorCandidates);
  $('colorize-filter').addEventListener('change',renderImages);
  $('colorize-manual').addEventListener('change',()=>{state.chatgpt.selected=new Set([...state.chatgpt.selected].filter(colorEligible));renderImages();renderPhotoLinks();updateChatgptControls();});
  $('colorize-select').addEventListener('click',()=>{state.chatgpt.selected=new Set(chatgptReadyPaths(new Set(state.images.filter(image=>colorCandidates.get(image.path)?.suggested).map(image=>image.path))));renderImages();updateChatgptControls();});
  $('chatgpt-select-all').addEventListener('click', () => { state.chatgpt.selected = new Set(chatgptReadyPaths(new Set(state.images.map(image=>image.path)))); state.chatgpt.anchor = -1; state.chatgpt.preview = [...state.chatgpt.selected][0] || null; state.chatgpt.review = null; showChatgptPreview(); renderImages(); updateChatgptControls(); });
  $('chatgpt-clear').addEventListener('click', () => { state.chatgpt.selected.clear(); state.chatgpt.anchor = -1; if (!state.chatgpt.review) {state.chatgpt.preview = null; clearChatgptPreview();} renderImages(); updateChatgptControls(); });
  $('chatgpt-clear-queued').addEventListener('click', () => { state.chatgpt.queuedPaths.clear(); renderImages(); updateChatgptControls(); });
  $('chatgpt-stop').addEventListener('click', () => { state.chatgpt.stop = true; state.chatgpt.queuedPaths.clear(); renderImages(); updateChatgptControls(); });
  $('chatgpt-generate').addEventListener('click', () => generateChatgpt());
  $('chatgpt-retry-failed').addEventListener('click', retryFailedChatgpt);
  $('open-settings').addEventListener('click', () => openAppSettings());
  $('app-settings-close').addEventListener('click', () => $('app-settings-dialog').close());
  $('settings-save').addEventListener('click', saveAppSettings);
  $('settings-test-key').addEventListener('click', testAppKey);
  $('settings-clear-key').addEventListener('click', clearAppKey);
  $('settings-output-browse')?.addEventListener('click', () => pickFolder(true));
  $('choose-output-folder')?.addEventListener('click', () => pickFolder(true));
  $('save-choose-output')?.addEventListener('click', () => pickFolder(true));
  $('settings-filename-template')?.addEventListener('input', updateFilenamePreview);
  $('settings-backup-originals')?.addEventListener('change', () => {
    if (!$('settings-backup-originals').checked) $('settings-replace-original').checked = false;
  });
  $('settings-replace-original')?.addEventListener('change', () => {
    if ($('settings-replace-original').checked) $('settings-backup-originals').checked = true;
  });
  $('first-run-dialog').addEventListener('close', () => {
    api('/api/settings', {guide_seen: true}).catch(() => {});
    if ($('first-run-dialog').returnValue === 'settings') openAppSettings();
  });
  $('chatgpt-slider').addEventListener('input', () => $('chatgpt-preview').style.setProperty('--split', `${$('chatgpt-slider').value}%`));
  const comparisonStage = document.querySelector('#chatgpt-preview .chatgpt-image-stage');
  $('chatgpt-concurrency').value = chatgptConcurrency;
  $('chatgpt-concurrency').addEventListener('change', () => {
    chatgptConcurrency = Number($('chatgpt-concurrency').value);
    localStorage.setItem('cropper-chatgpt-concurrency', chatgptConcurrency);
  });
  let zoom = 1, panX = 0, panY = 0, panning = null;
  const applyZoom = () => {
    panX = Math.max(-comparisonStage.clientWidth * (zoom - 1) / 2, Math.min(comparisonStage.clientWidth * (zoom - 1) / 2, panX));
    panY = Math.max(-comparisonStage.clientHeight * (zoom - 1) / 2, Math.min(comparisonStage.clientHeight * (zoom - 1) / 2, panY));
    comparisonStage.style.setProperty('--zoom', zoom);
    comparisonStage.style.setProperty('--pan-x', `${panX}px`);
    comparisonStage.style.setProperty('--pan-y', `${panY}px`);
    comparisonStage.style.cursor = zoom > 1 ? 'grab' : 'ew-resize';
    $('chatgpt-zoom-label').textContent = `${Math.round(zoom * 100)}%`;
  };
  const changeZoom = factor => { zoom = Math.max(1, Math.min(8, zoom * factor)); applyZoom(); };
  resetChatgptZoom = () => { zoom = 1; panX = panY = 0; applyZoom(); };
  $('chatgpt-zoom-in').addEventListener('click', () => changeZoom(1.25));
  $('chatgpt-zoom-out').addEventListener('click', () => changeZoom(0.8));
  $('chatgpt-zoom-fit').addEventListener('click', resetChatgptZoom);
  comparisonStage.addEventListener('wheel', event => { event.preventDefault(); changeZoom(event.deltaY < 0 ? 1.1 : 1 / 1.1); }, {passive:false});
  let swiping = false;
  const positionComparison = event => {
    const rect = comparisonStage.getBoundingClientRect();
    const percent = Math.max(0, Math.min(100, Math.round((event.clientX - rect.left) / rect.width * 100)));
    $('chatgpt-slider').value = percent;
    $('chatgpt-preview').style.setProperty('--split', `${percent}%`);
  };
  comparisonStage.addEventListener('pointerdown', event => {
    if (event.target === $('chatgpt-slider')) return;
    if (zoom > 1 && event.target !== $('chatgpt-divider')) {
      panning = {x:event.clientX - panX, y:event.clientY - panY}; comparisonStage.setPointerCapture(event.pointerId); return;
    }
    if (!state.chatgpt.review) return;
    swiping = true; comparisonStage.setPointerCapture(event.pointerId); positionComparison(event);
  });
  comparisonStage.addEventListener('pointermove', event => { if (panning) { panX = event.clientX - panning.x; panY = event.clientY - panning.y; applyZoom(); } else if (swiping) positionComparison(event); });
  comparisonStage.addEventListener('pointerup', () => { swiping = false; panning = null; });
  comparisonStage.addEventListener('pointercancel', () => { swiping = false; panning = null; });
  $('chatgpt-accept').addEventListener('click', () => finishChatgpt(true));
  $('chatgpt-reject').addEventListener('click', () => finishChatgpt(false));
  $('chatgpt-modify').addEventListener('click', () => {
    if (!state.chatgpt.review || state.chatgpt.reviewBusy || state.chatgpt.resubmissions?.has(state.chatgpt.review.front_path)) return;
    state.chatgpt.correctionOpen = !state.chatgpt.correctionOpen;
    updateChatgptControls();
    if (state.chatgpt.correctionOpen) {
      $('chatgpt-comment').scrollIntoView({block:'nearest', behavior:'smooth'});
      $('chatgpt-comment').focus({preventScroll:true});
    }
  });
  $('chatgpt-resubmit').addEventListener('click', resubmitChatgpt);
  $('chatgpt-comment').addEventListener('input', () => {
    if (state.chatgpt.review) chatgptCommentDrafts.set(state.chatgpt.review.id, $('chatgpt-comment').value);
    $('chatgpt-resubmit').disabled = !state.chatgpt.review || !!state.chatgpt.reviewBusy || state.chatgpt.resubmissions?.has(state.chatgpt.review.front_path) || !$('chatgpt-comment').value.trim();
  });
  $('enhance-select-all').addEventListener('click',()=>{ state.enhance.selected = new Set(state.images.map(image=>image.path)); refreshEnhanceTiles(); renderEnhanceControls(); if (state.images.length && state.enhance.preview < 0) previewEnhance(0); });
  $('enhance-clear').addEventListener('click',()=>{ state.enhance.selected.clear(); refreshEnhanceTiles(); renderEnhanceControls(); });
  $('enhance-save').addEventListener('click',saveEnhancedBatch);
  $('enhance-stop').addEventListener('click',()=>{ state.enhance.stop = true; renderEnhanceControls(); });
  $('restore-select-all').addEventListener('click',()=>{ state.restore.selected = new Set(state.images.map(image=>image.path).filter(path=>!state.restore.notNeeded.has(path))); refreshRestorationTiles(); renderRestorationControls(); if (state.images.length && state.restore.preview < 0) previewRestoration(0); });
  $('restore-clear').addEventListener('click',()=>{ state.restore.selected.clear(); refreshRestorationTiles(); renderRestorationControls(); });
  $('restore-save').addEventListener('click',saveRestorationBatch);
  $('restore-stop').addEventListener('click',()=>{ state.restore.stop = true; renderRestorationControls(); });
  $('restore-open-results').addEventListener('click',()=>state.restore.resultsFolder&&loadFolder(state.restore.resultsFolder));
  $('restore-mark-scratch').addEventListener('click',()=>{ state.restore.brush = !state.restore.brush; resetRestoreZoom(); renderRestorationControls(); });
  $('restore-show-marks').addEventListener('click',()=>{ state.restore.showMarks = !state.restore.showMarks; renderRestorationControls(); if (state.restore.preview >= 0) previewRestoration(state.restore.preview); });
  $('restore-clear-marks').addEventListener('click',()=>{ const path = state.images[state.restore.preview]?.path; if (!path) return; state.restore.marks.delete(path); drawBrushCanvas(); renderRestorationControls(); previewRestoration(state.restore.preview); });
  const brushCanvas = $('restore-brush-canvas');
  brushCanvas.addEventListener('pointerdown',event=>{
    if (state.view !== 'dust' || !state.restore.brush || state.restore.busy || state.restore.preview < 0) return;
    const point = brushPoint(event); if (!point) return;
    const path = state.images[state.restore.preview].path;
    const strokes = state.restore.marks.get(path) || []; strokes.push([point]); state.restore.marks.set(path, strokes);
    state.restore.drawing = strokes[strokes.length - 1]; brushCanvas.setPointerCapture(event.pointerId);
    event.preventDefault(); drawBrushCanvas(); renderRestorationControls();
  });
  brushCanvas.addEventListener('pointermove',event=>{
    if (!state.restore.drawing) return; const point = brushPoint(event); if (!point) return;
    const last = state.restore.drawing[state.restore.drawing.length - 1];
    if (Math.hypot(point[0]-last[0],point[1]-last[1]) > 0.002) { state.restore.drawing.push(point); drawBrushCanvas(); }
  });
  const finishBrush = ()=>{ if (!state.restore.drawing) return; state.restore.drawing = null; if (state.restore.preview >= 0) previewRestoration(state.restore.preview); };
  brushCanvas.addEventListener('pointerup',finishBrush); brushCanvas.addEventListener('pointercancel',finishBrush);
  $('merge-clear').addEventListener('click',()=>{ state.merge = { front: -1, back: -1, frontInfo: null, backInfo: null, busy: false }; renderImages(); renderMerge(); });
  $('merge-save').addEventListener('click',mergeSave);
  $('folder-modal-close').addEventListener('click',closePicker);
  $('folder-modal-cancel').addEventListener('click',closePicker);
  $('folder-modal').addEventListener('click',event=>{if(event.target===$('folder-modal'))closePicker();});
  $('folder-modal-up').addEventListener('click',()=>state.picker?.parent&&loadPickerFolder(state.picker.parent));
  $('folder-modal-go').addEventListener('click',()=>loadPickerFolder($('folder-modal-path').value.trim()));
  $('folder-modal-path').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();loadPickerFolder(event.currentTarget.value.trim());}});
  $('folder-modal-select').addEventListener('click',selectPickerFolder);
  $('go-path').addEventListener('click',()=>loadFolder($('folder-path').value.trim()));
  $('folder-path').addEventListener('keydown',event=>{ if(event.key==='Enter') loadFolder(event.currentTarget.value.trim()); });
  $('go-parent').addEventListener('click',()=>state.parent&&loadFolder(state.parent));
  $('refresh').addEventListener('click',()=>state.folder&&loadFolder(state.folder,true));
  $('prev').addEventListener('click',()=>openImage(state.index-1)); $('next').addEventListener('click',()=>openImage(state.index+1));
  $('undo').addEventListener('click',()=>{state.points.pop();renderOverlay();updateControls();});
  $('reset').addEventListener('click',()=>{state.points=[];state.rotation=0;renderOverlay();updateControls();});
  $('select-whole').addEventListener('click',selectWholeImage);
  $('predict-corners').addEventListener('click',predictCropCorners);
  $('rotate-left').addEventListener('click',()=>{state.rotation=((state.rotation-90+360)%360); if(state.rotation===270)state.rotation=-90; toast(`Output rotation: ${state.rotation}°`); updateControls();});
  $('rotate-right').addEventListener('click',()=>{state.rotation=((state.rotation+90+360)%360); if(state.rotation===270)state.rotation=-90; toast(`Output rotation: ${state.rotation}°`); updateControls();});
  $('save').addEventListener('click',cropSave);
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
    if (state.view !== 'crop') return;
    if(['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName) || document.activeElement?.isContentEditable || document.querySelector('dialog[open]'))return;
    if(event.key==='Control'){startZoom();return;}
    if(state.busy)return;
    if(event.key.toLowerCase()==='a'&&event.ctrlKey){event.preventDefault();if(!event.repeat)predictCropCorners();}
    else if(event.key.toLowerCase()==='q'&&event.ctrlKey){event.preventDefault();clearCropPoints();}
    else if(event.key==='ArrowLeft'){event.preventDefault();openImage(state.index-1);}
    else if(event.key==='ArrowRight'){event.preventDefault();openImage(state.index+1);}
    else if(event.key==='Enter'){event.preventDefault();cropSave();}
    else if(event.key==='Backspace'){event.preventDefault();state.points=[];renderOverlay();updateControls();}
    else if(event.key.toLowerCase()==='z'&&event.ctrlKey){event.preventDefault();state.points.pop();renderOverlay();updateControls();}
  });
  document.addEventListener('keyup',event=>{if(event.key==='Control')endZoom();}); window.addEventListener('blur',endZoom);
  $('canvas-shell').addEventListener('wheel',event=>{if(event.ctrlKey)event.preventDefault();},{passive:false});
  new ResizeObserver(fitImage).observe($('canvas-shell'));
  new ResizeObserver(drawBrushCanvas).observe($('restore-before-wrap'));
  updateStageRoutingDisplay();updateControls();
}
function updateFilenamePreview() {
  const template = $('settings-filename-template')?.value.trim() || '{name}{suffix}{ext}';
  const preview = $('settings-filename-preview');
  if (!preview) return;
  try {
    const sample = template.replaceAll('{name}', 'photo').replaceAll('{date}', '20261029').replaceAll('{index}', '1').replaceAll('{ext}', '.png').replaceAll('{suffix}', '_c');
    if (!sample || /[<>:"/\\|?*\x00-\x1f]/.test(sample) || sample.includes('/') || sample.includes('\\')) {
      preview.textContent = 'Filename template produces invalid characters.';
      return;
    }
    preview.textContent = `Preview: ${sample}`;
  } catch (_) { preview.textContent = 'Filename template supports {name}, {date}, {index} and {ext}.'; }
}
function applyServerSettings(data) {
  state.hasKey = data.has_key !== false;
  state.keySource = data.key_source;
  if ($('settings-model')) $('settings-model').value = data.model || 'gpt-image-2.5-sunburst';
  if ($('settings-quality')) $('settings-quality').value = data.quality || 'medium';
  if ($('settings-filename-template')) $('settings-filename-template').value = data.filename_template || '{name}{suffix}{ext}';
  if ($('settings-conflict-policy')) $('settings-conflict-policy').value = data.conflict_policy || 'skip';
  if ($('settings-replace-original')) $('settings-replace-original').checked = !!data.replace_original;
  if ($('settings-backup-originals')) $('settings-backup-originals').checked = data.backup_originals !== false;
  if ($('settings-output-folder')) $('settings-output-folder').value = data.output_folder || '';
  updateFilenamePreview();
  const env = data.key_source === 'env';
  const warning = $('settings-keyring-warning');
  if (warning) {
    if (data.keyring_error) {
      warning.textContent = data.keyring_error;
      warning.classList.remove('hidden');
    } else {
      warning.textContent = '';
      warning.classList.add('hidden');
    }
  }
  if ($('settings-api-key')) {
    $('settings-api-key').value = '';
    $('settings-api-key').disabled = env;
    $('settings-api-key').placeholder = data.key_masked ? `Saved key ${data.key_masked}` : 'Paste key — it is never shown in full';
  }
  if ($('settings-clear-key')) $('settings-clear-key').disabled = env || !data.has_key;
  if ($('settings-key-masked')) {
    $('settings-key-masked').textContent = data.has_key
      ? (env ? 'Using OPENAI_API_KEY from the environment. Only the last four characters are shown.' : `Saved key: ${data.key_masked}. Only the last four characters are shown.`)
      : 'No key saved. AI restore is disabled until you add one, or set OPENAI_API_KEY.';
  }
  if ($('settings-key-source')) $('settings-key-source').textContent = env
    ? 'OPENAI_API_KEY is set in the environment and takes precedence over a saved key.'
    : '';
  if (typeof updateChatgptControls === 'function') try { updateChatgptControls(); } catch (_) {}
}
async function openAppSettings() {
  try { applyServerSettings(await api('/api/settings')); }
  catch (error) { toast(error.message, true); }
  $('settings-key-status').textContent = '';
  try { $('app-settings-dialog').showModal(); }
  catch (_) { $('app-settings-dialog').setAttribute('open', ''); }
}
async function saveAppSettings() {
  const payload = {
    model: $('settings-model').value.trim(),
    quality: $('settings-quality').value,
    guide_seen: true,
    filename_template: $('settings-filename-template')?.value.trim() || '{name}{suffix}{ext}',
    conflict_policy: $('settings-conflict-policy')?.value || 'skip',
    replace_original: !!$('settings-replace-original')?.checked,
    backup_originals: $('settings-backup-originals') ? $('settings-backup-originals').checked : true,
    output_folder: $('settings-output-folder')?.value.trim() || '',
  };
  const input = $('settings-api-key');
  const typed = input ? input.value.trim() : '';
  if (typed) payload.api_key = typed;
  if (input) input.value = '';
  try {
    const saved = await api('/api/settings', payload);
    payload.api_key = '';
    applyServerSettings(saved);
    $('settings-key-status').textContent = 'Settings saved.';
    toast('Settings saved.');
  } catch (error) { $('settings-key-status').textContent = error.message; toast(error.message, true); }
}
async function testAppKey() {
  const payload = { api_key: $('settings-api-key').value.trim() };
  $('settings-key-status').textContent = 'Testing key…';
  try {
    const result = await api('/api/settings/test-key', payload);
    $('settings-key-status').textContent = result.message;
  } catch (error) { $('settings-key-status').textContent = error.message; }
}
async function clearAppKey() {
  try {
    applyServerSettings(await api('/api/settings/clear-key', {}));
    $('settings-key-status').textContent = 'Saved key removed.';
    toast('Saved API key cleared.');
  } catch (error) { $('settings-key-status').textContent = error.message; toast(error.message, true); }
}
async function init() {
  try {
    const session = await fetch('/api/session').then(response => {
      if (!response.ok) throw new Error('Session is only available on localhost.');
      return response.json();
    });
    state.token = session.token; state.home = session.home;
    state.stageRoutingAvailable = session.stage_routing_version >= 2;
    state.hasKey = session.has_key !== false;
    wire();
    const last = localStorage.getItem('cropper-last-folder');
    if (last) await loadFolder(last);
    if (!state.stageRoutingAvailable && stageRoutingEnabled()) toast('Flat stage routing is installed but needs an idle Cropper server restart on 8767 before new saves.', true);
    try {
      const settings = await api('/api/settings');
      applyServerSettings(settings);
      if (settings.keyring_error) toast(settings.keyring_error, true);
      if (!settings.guide_seen) {
        try { $('first-run-dialog').showModal(); }
        catch (_) {}
      }
    } catch (_) {}
  }
  catch(error){toast(`Could not start Cropper: ${error.message}`,true);}
}
init();
