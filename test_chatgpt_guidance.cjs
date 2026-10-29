const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/app.js', 'utf8');
const html = fs.readFileSync(__dirname + '/index.html', 'utf8');
const between = (a,b) => source.slice(source.indexOf(a), source.indexOf(b));

const FAMILY = '👨‍👩‍👧';
const FLAG = '🇬🇧';
const ACCENT = 'e\u0301';
const HINT = 'This text is sent to OpenAI with the image. Leave out private details like full names, addresses or dates of birth.';
const TRUNCATION = 'Guidance is limited to 500 characters, so we trimmed the extra text.';
const OVERRIDE = 'If this note conflicts with the batch context, follow this note.';

const helpers = vm.runInNewContext(
  between('function graphemeClusters(', 'function updateChatgptControls(') +
  '\n({graphemeClusters,stripGuidanceControls,sanitizeGuidance,guidanceLength,guidanceWasTruncated,snapshotGuidance,photoHasGuidance,applyGuidanceField,guidanceStore})',
  { state: { chatgpt: { guidance: { batch: '', photos: {} } } } }
);

test('AC4 grapheme counting, control stripping, trim and 500 cap match the backend rules', () => {
  const {graphemeClusters, stripGuidanceControls, sanitizeGuidance, guidanceLength, guidanceWasTruncated} = helpers;
  assert.equal(graphemeClusters('hello').length, 5);
  assert.equal(graphemeClusters(ACCENT).length, 1);
  assert.equal(graphemeClusters(FLAG).length, 1);
  assert.equal(graphemeClusters(FAMILY).length, 1);
  assert.equal(stripGuidanceControls('a\x00b\x07c'), 'abc');
  assert.equal(stripGuidanceControls('line1\nline2'), 'line1\nline2');
  assert.equal(sanitizeGuidance('  sailor hats  '), 'sailor hats');
  assert.equal(sanitizeGuidance(' \n\t '), '');
  assert.equal(sanitizeGuidance('a'.repeat(525)), 'a'.repeat(500));
  assert.equal(sanitizeGuidance(ACCENT.repeat(501)), ACCENT.repeat(500));
  assert.equal(sanitizeGuidance(FAMILY.repeat(501)), FAMILY.repeat(500));
  assert.equal(guidanceLength(ACCENT.repeat(500)), 500);
  assert.equal(guidanceWasTruncated('a'.repeat(501)), true);
  assert.equal(guidanceWasTruncated('a'.repeat(500)), false);
});

test('AC4 paste truncates with a visible notice; typing over the cap is trimmed without a notice', () => {
  const field = { value: 'a'.repeat(480) };
  const counter = {};
  const truncation = { classList: { toggle(name, hidden) { truncation.hidden = hidden; } } };
  helpers.applyGuidanceField(field, counter, truncation, true);
  assert.equal(counter.textContent, '480/500');
  assert.equal(truncation.hidden, true);
  field.value = 'a'.repeat(540);
  helpers.applyGuidanceField(field, counter, truncation, true);
  assert.equal(field.value, 'a'.repeat(500));
  assert.equal(counter.textContent, '500/500');
  assert.equal(truncation.hidden, false);
  field.value = 'a'.repeat(510);
  helpers.applyGuidanceField(field, counter, truncation, false);
  assert.equal(field.value, 'a'.repeat(500));
  assert.equal(truncation.hidden, true);
});

test('AC1 empty snapshot sends no guidance; AC5 freezes notes at run start', () => {
  const state = { chatgpt: { guidance: { batch: 'navy uniforms', photos: { a: 'white hat', b: 'pink roses', c: 'skip me' } } } };
  const local = vm.runInNewContext(
    between('function graphemeClusters(', 'function updateChatgptControls(') +
    '\n({sanitizeGuidance,snapshotGuidance,photoHasGuidance})',
    { state }
  );
  const empty = local.snapshotGuidance(['only']);
  assert.equal(empty.batch, '');
  assert.equal(empty.notes.only, '');
  const first = local.snapshotGuidance(['a', 'b']);
  assert.equal(first.batch, 'navy uniforms');
  assert.equal(first.notes.a, 'white hat');
  assert.equal(first.notes.b, 'pink roses');
  assert.equal(local.photoHasGuidance('a', first), true);
  state.chatgpt.guidance.batch = 'changed after start';
  state.chatgpt.guidance.photos.a = 'edited after start';
  state.chatgpt.guidance.photos.c = 'new selection';
  assert.equal(first.batch, 'navy uniforms');
  assert.equal(first.notes.a, 'white hat');
  assert.equal(first.notes.c, undefined);
  const later = local.snapshotGuidance(['a', 'b', 'c']);
  assert.equal(later.batch, 'changed after start');
  assert.equal(later.notes.c, 'new selection');
});

test('Generate sends the frozen batch context and per-photo note on the comments path', async () => {
  const paths = ['photo-a', 'photo-b'];
  const current = {selected:new Set(paths), queuedPaths:new Set(), pending:[], review:null, preview:null,
    guidance:{ batch:'1950s wedding party, navy uniforms', photos:{ 'photo-a':"bride's bouquet is pink roses", 'photo-b':'' } }};
  const state = {folder:'photos', images:paths.map(path=>({path})), chatgpt:current};
  const sent = [];
  const context = {state, photoLinks:[], colorEligible:()=>true, colorScanning:false,
    chatgptOperation:'restore', colorScanFolder:'photos', chatgptConcurrency:1,
    stageRoutingEnabled:()=>true, setTimeout, Date,
    $:()=>({}), renderImages:()=>{}, updateChatgptControls:()=>{},
    showChatgptPreview:()=>{}, toast:()=>{}, reviewChatgptItem:item=>{current.review=item;},
    offerChooseOutputFolder:()=>false,
    api:async (route,body)=>{ sent.push(body); return {id:body.front_path,front_path:body.front_path}; }};
  const generate = vm.runInNewContext(
    between('function chatgptReadyPaths(', 'function updateChatgptControls(') +
    between('async function runChatgptQueue(', 'async function generateChatgpt(') +
    between('async function generateChatgpt(', 'async function finishChatgpt(') + '\ngenerateChatgpt', context);
  const running = generate();
  await new Promise(resolve=>setImmediate(resolve));
  current.guidance.batch = 'mutated';
  current.guidance.photos['photo-a'] = 'mutated note';
  current.selected = new Set(['photo-extra']);
  await running;
  assert.equal(sent.length, 2);
  assert.equal(sent[0].batch_context, '1950s wedding party, navy uniforms');
  assert.equal(sent[0].photo_note, "bride's bouquet is pink roses");
  assert.equal(sent[1].batch_context, '1950s wedding party, navy uniforms');
  assert.equal(sent[1].photo_note, '');
  assert.equal(sent[0].operation, 'restore');
});

test('AC6 colourise uses the same guidance payload fields', async () => {
  const current = {selected:new Set(['mono']), queuedPaths:new Set(), pending:[], review:null, preview:null,
    guidance:{ batch:'', photos:{ mono:'man in black suit and white shirt, woman in blue dress' } }};
  const state = {folder:'photos', images:[{path:'mono'}], chatgpt:current};
  const sent = [];
  const context = {state, photoLinks:[], colorEligible:()=>true, colorScanning:false,
    chatgptOperation:'colorize', colorScanFolder:'photos', chatgptConcurrency:1,
    stageRoutingEnabled:()=>true, setTimeout, Date,
    $:()=>({}), renderImages:()=>{}, updateChatgptControls:()=>{},
    showChatgptPreview:()=>{}, toast:()=>{}, reviewChatgptItem:item=>{current.review=item;},
    offerChooseOutputFolder:()=>false,
    api:async (route,body)=>{ sent.push(body); return {id:body.front_path,front_path:body.front_path}; }};
  const generate = vm.runInNewContext(
    between('function chatgptReadyPaths(', 'function updateChatgptControls(') +
    between('async function runChatgptQueue(', 'async function generateChatgpt(') +
    between('async function generateChatgpt(', 'async function finishChatgpt(') + '\ngenerateChatgpt', context);
  await generate();
  assert.equal(sent[0].operation, 'colorize');
  assert.equal(sent[0].batch_context, '');
  assert.equal(sent[0].photo_note, 'man in black suit and white shirt, woman in blue dress');
});

test('AC11 deselecting a photo does not cancel its queued job; stop still works', async () => {
  const current = {selected:new Set(['a','b']), queuedPaths:new Set(), pending:[], review:null, preview:null,
    guidance:{ batch:'navy', photos:{ a:'hat', b:'rose' } }};
  const state = {folder:'photos', images:['a','b'].map(path=>({path})), chatgpt:current};
  const sent = [];
  let release, started;
  const gate = new Promise(resolve => { release = resolve; });
  const begin = new Promise(resolve => { started = resolve; });
  const context = {state, photoLinks:[], colorEligible:()=>true, colorScanning:false,
    chatgptOperation:'restore', colorScanFolder:'photos', chatgptConcurrency:1,
    stageRoutingEnabled:()=>true, setTimeout, Date,
    $:()=>({textContent:''}), renderImages:()=>{}, updateChatgptControls:()=>{},
    showChatgptPreview:()=>{}, toast:()=>{}, reviewChatgptItem:item=>{current.review=item;},
    offerChooseOutputFolder:()=>false,
    api:async (route,body)=>{
      sent.push(body);
      if (body.front_path === 'a') { started(); await gate; }
      return {id:body.front_path,front_path:body.front_path};
    }};
  const generate = vm.runInNewContext(
    between('function chatgptReadyPaths(', 'function updateChatgptControls(') +
    between('async function runChatgptQueue(', 'async function generateChatgpt(') +
    between('async function generateChatgpt(', 'async function finishChatgpt(') + '\ngenerateChatgpt', context);
  const running = generate();
  await begin;
  current.selected.delete('b');
  current.guidance.photos.b = 'changed';
  release();
  await running;
  assert.deepEqual(sent.map(item => item.front_path), ['a', 'b']);
  assert.equal(sent[1].photo_note, 'rose');
});

test('Rerun sends edited guidance with the correction and keeps Edit off the paid path', async () => {
  const reviewed = {id:'old', front_path:'front.png', batch_context:'navy uniforms', photo_note:'white hat'};
  const current = {busy:false, review:reviewed, pending:[reviewed], guidanceEdit:true};
  const controls = {
    'chatgpt-comment':{value:'Keep the eyes unchanged.'},
    'chatgpt-resubmit-input':{value:'original'},
    'chatgpt-resubmit-status':{},
    'chatgpt-resubmit':{},
    'chatgpt-progress':{},
    'guidance-used-batch':{value:'edited batch'},
    'guidance-used-note':{value:'edited note'}
  };
  let payload;
  const resubmit = vm.runInNewContext(
    source.slice(source.indexOf('async function resubmitChatgpt('), source.indexOf('function renderPhotoLinks()')) + '\nresubmitChatgpt',
    {state:{chatgpt:current}, $:id=>controls[id], confirmChatgptResubmit:async()=>true,
      updateChatgptControls:()=>{}, chatgptCommentDrafts:new Map(), toast:()=>{},
      api:async (route,data)=>{payload=data; return {id:'new',front_path:'front.png',attempt:2};},
      reviewChatgptItem:item=>{current.review=item;}}
  );
  await resubmit();
  assert.equal(payload.comment, 'Keep the eyes unchanged.');
  assert.equal(payload.batch_context, 'edited batch');
  assert.equal(payload.photo_note, 'edited note');
});

test('Wren copy, privacy hint, truncation notice and data-testids are present', () => {
  assert.match(html, /Guidance \(optional\)/);
  assert.match(html, /Tell the AI what the photo can't show: true colours, details that are hidden or washed out, or who's who\./);
  assert.match(html, /Guidance for this photo/);
  assert.match(html, /Context for all \{?N\}?0 photos|Context for all 0 photos/);
  assert.match(html, /Note for this photo \(overrides the batch context\)/);
  assert.match(html, /e\.g\. 1950s wedding party, navy uniforms/);
  assert.match(html, /Guidance used/);
  assert.match(html, new RegExp(HINT.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  assert.match(html, new RegExp(TRUNCATION.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  for (const id of [
    'guidance-panel', 'guidance-heading', 'guidance-explainer',
    'guidance-single-field', 'guidance-single-counter', 'guidance-single-hint', 'guidance-single-truncation',
    'guidance-batch-field', 'guidance-batch-counter', 'guidance-batch-hint', 'guidance-batch-truncation',
    'guidance-photo-list', 'guidance-batch-notes-count',
    'guidance-used-batch', 'guidance-used-batch-counter', 'guidance-used-batch-truncation',
    'guidance-used-note', 'guidance-used-note-counter', 'guidance-used-note-truncation',
    'guidance-used-hint', 'guidance-rerun-edit'
  ]) {
    assert.match(html, new RegExp(`data-testid="${id}"`));
  }
  assert.match(source, /data-testid', 'guidance-photo-field'/);
  assert.match(source, /data-testid', 'guidance-photo-counter'/);
  assert.match(source, /data-testid', 'guidance-photo-truncation'/);
  assert.match(source, /data-testid', 'guidance-photo-hint'/);
  assert.match(source, /e\.g\. man in black suit and white shirt, woman in blue dress/);
  assert.match(source, /e\.g\. sailor hats, mostly lost to overexposure/);
  assert.match(source, /e\.g\. bride's bouquet is pink roses/);
});

test('Prompt-order contract is documented next to the comments plumbing', () => {
  const restore = fs.readFileSync(__dirname + '/chatgpt_restore.py', 'utf8');
  assert.match(restore, /Preservation rules, then batch context, then per-photo note, then rerun corrections/);
  assert.match(restore, new RegExp(OVERRIDE.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  assert.doesNotMatch(restore, /C:[\\/]Users/);
});
