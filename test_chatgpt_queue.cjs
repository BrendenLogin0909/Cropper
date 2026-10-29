const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/app.js', 'utf8');
const queue = vm.runInNewContext(source.slice(source.indexOf('async function runChatgptQueue('), source.indexOf('async function generateChatgpt(')) + '\nrunChatgptQueue', { setTimeout, Date });
test('colourisation checkboxes remain usable during scanning and filter stays usable during generation', () => {
  const controls=Object.fromEntries(['colorize-filter','colorize-manual','colorize-scan','colorize-select'].map(id=>[id,{}]));
  const context={state:{folder:'photos',chatgpt:{busy:false}},colorScanning:true,colorScanFolder:'',$:id=>controls[id]};
  const update=vm.runInNewContext(source.slice(source.indexOf('function updateColorizeToolbarControls('),source.indexOf('function colorImageVisible('))+'\nupdateColorizeToolbarControls',context);
  update();
  assert.equal(controls['colorize-filter'].disabled,false);
  assert.equal(controls['colorize-manual'].disabled,false);
  assert.equal(controls['colorize-scan'].disabled,true);
  assert.equal(controls['colorize-select'].disabled,true);
  context.colorScanning=false;context.colorScanFolder='photos';update();
  assert.equal(controls['colorize-scan'].disabled,false);
  assert.equal(controls['colorize-select'].disabled,false);
  context.state.chatgpt.busy=true;update();
  assert.equal(controls['colorize-filter'].disabled,false);
  assert.equal(controls['colorize-manual'].disabled,false);
  assert.equal(controls['colorize-scan'].disabled,true);
  assert.equal(controls['colorize-select'].disabled,false);
  context.state.chatgpt.reviewBusy=true;update();
  assert.equal(controls['colorize-manual'].disabled,true);
});

test('colourisation only hides images after the current folder scan is ready', () => {
  const context = {
    state:{view:'chatgpt',folder:'photos'},chatgptOperation:'colorize',
    colorScanning:true,colorScanFolder:'',checkbox:{checked:true},
    colorCandidates:new Map([['bw',{suggested:true}],['colour',{suggested:false}]])
  };
  context.$=()=>context.checkbox;
  const visible=vm.runInNewContext(source.slice(source.indexOf('function colorImageVisible('),source.indexOf('function colorEligible('))+'\ncolorImageVisible',context);
  assert.equal(visible('colour'),true);
  context.colorScanning=false;
  assert.equal(visible('colour'),true); // Not scanned, or scan failed.
  context.colorScanFolder='previous-folder';
  assert.equal(visible('colour'),true);
  context.colorScanFolder='photos';
  assert.equal(visible('bw'),true);assert.equal(visible('colour'),false);
  context.colorScanning=true;
  assert.equal(visible('colour'),true); // Rescanning shows all photos again.
  context.colorScanning=false;context.checkbox.checked=false;
  assert.equal(visible('colour'),true);
  context.checkbox.checked=true;context.chatgptOperation='restore';
  assert.equal(visible('colour'),true);
});

test('colorization eligibility excludes colour and combined images by default', () => {
  const checkbox={checked:false};
  const eligible=vm.runInNewContext(source.slice(source.indexOf('function colorEligible('),source.indexOf('async function scanColorCandidates('))+'\ncolorEligible',{
    chatgptOperation:'colorize',colorScanFolder:'photos',state:{folder:'photos'},$:()=>checkbox,
    colorCandidates:new Map([['bw',{suggested:true}],['colour',{suggested:false}],['combined',{suggested:false,combined:true}]])
  });
  assert.equal(eligible('bw'),true);assert.equal(Boolean(eligible('colour')),false);assert.equal(Boolean(eligible('combined')),false);
  checkbox.checked=true;assert.equal(eligible('colour'),true);assert.equal(eligible('combined'),false);
});
test('accept during generation leaves generation running and keeps newly saved results', async () => {
  const reviewed = {id:'first', front_path:'first.png'};
  const current = {busy:true, review:reviewed, pending:[reviewed], selected:new Set(), queuedPaths:new Set(), preview:'first.png'};
  const state = {chatgpt:current, images:[{path:'first.png'}, {path:'second.png'}]};
  const finish = vm.runInNewContext(source.slice(source.indexOf('async function finishChatgpt('), source.indexOf('function renderPhotoLinks()')) + '\nfinishChatgpt', {
    state, api:async () => {current.pending.push({id:'second', front_path:'second.png'});},
    $:() => ({}), selectedPairs:new Set(), renderPhotoLinks:() => {}, updateChatgptControls:() => {}, renderImages:() => {}, showChatgptPreview:() => {},
    reviewChatgptItem:item => {current.review=item;}, toast:() => {}, offerChooseOutputFolder:() => false
  });
  await finish(true);
  assert.equal(current.busy, true); assert.equal(current.reviewBusy, false);
  assert.equal(current.pending.length, 1); assert.equal(current.review.id, 'second');
  assert.equal(state.images.length, 1);
});
test('three requests maximum, every result independently saved', async () => {
  let active = 0, peak = 0; const saved = [];
  const failure = await queue(Array.from({length:10}, (_, i) => i), {stop:false}, async path => {
    peak = Math.max(peak, ++active);
    await new Promise(resolve => setTimeout(resolve, 5)); active--; return path;
  }, item => saved.push(item), () => {});
  assert.equal(peak, 3); assert.equal(saved.length, 10); assert.equal(failure, '');
});

test('resubmit preserves attempts and concurrent batch results; sends selected reference mode', async () => {
  const reviewed={id:'old',front_path:'front.png'};
  const current={busy:true,review:reviewed,pending:[reviewed]};
  const controls={'chatgpt-comment':{value:'White moustache.'},'chatgpt-resubmit-input':{value:'both'},'chatgpt-resubmit-status':{},'chatgpt-resubmit':{}};
  let payload;
  const context={state:{chatgpt:current},$:id=>controls[id],confirmChatgptResubmit:async()=>true,
    updateChatgptControls:()=>{},chatgptCommentDrafts:new Map(),toast:()=>{},
    api:async(route,data)=>{payload=data;current.pending.push({id:'other',front_path:'other.png'});return {id:'new',front_path:'front.png',attempt:2};},
    reviewChatgptItem:item=>{current.review=item;}};
  const resubmit=vm.runInNewContext(source.slice(source.indexOf('async function resubmitChatgpt('),source.indexOf('function renderPhotoLinks()'))+'\nresubmitChatgpt',context);
  await resubmit();
  assert.equal(payload.include_previous,true);
  assert.equal(payload.comment,'White moustache.');
  assert.equal(current.pending.length,3);
  assert.equal(current.review.id,'new');assert.equal(current.busy,true);assert.equal(current.reviewBusy,false);
});

test('failed resubmit retains current attempt and correction draft', async () => {
  const reviewed={id:'old',front_path:'front.png'};
  const current={busy:false,review:reviewed,pending:[reviewed]};
  const controls={'chatgpt-comment':{value:'White moustache.'},'chatgpt-resubmit-input':{value:'original'},'chatgpt-progress':{},'chatgpt-resubmit-status':{},'chatgpt-resubmit':{}};
  const context={state:{chatgpt:current},$:id=>controls[id],confirmChatgptResubmit:async()=>true,
    updateChatgptControls:()=>{},toast:()=>{},api:async()=>{throw Error('Mock failure');}};
  const resubmit=vm.runInNewContext(source.slice(source.indexOf('async function resubmitChatgpt('),source.indexOf('function renderPhotoLinks()'))+'\nresubmitChatgpt',context);
  await resubmit();assert.equal(current.pending.length,1);assert.equal(current.review.id,'old');
  assert.equal(current.reviewBusy,false);assert.equal(controls['chatgpt-comment'].value,'White moustache.');
  assert.match(controls['chatgpt-resubmit-status'].textContent,/Mock failure/);
});

test('in-page paid confirmation works without native window.confirm; cancel and escape never submit', async () => {
  let closed;
  const controls={'chatgpt-resubmit-summary':{},'chatgpt-resubmit-dialog':{
    addEventListener:(event,fn)=>{closed=fn;},showModal:()=>{},returnValue:''
  }};
  const confirmRequest=vm.runInNewContext(source.slice(source.indexOf('function confirmChatgptResubmit('),source.indexOf('async function resubmitChatgpt('))+'\nconfirmChatgptResubmit',{$:id=>controls[id]});
  let response=confirmRequest({front_path:'photo.png'},'White moustache.',false);
  assert.match(controls['chatgpt-resubmit-summary'].textContent,/White moustache/);
  assert.equal(controls['chatgpt-resubmit-dialog'].returnValue,'cancel');
  closed();assert.equal(await response,false);
  response=confirmRequest({front_path:'photo.png'},'White moustache.',true);
  controls['chatgpt-resubmit-dialog'].returnValue='send';closed();assert.equal(await response,true);
});

test('cancelled correction confirmation displays not-sent status and makes no API call', async () => {
  const current={review:{id:'old',front_path:'front.png'},pending:[]};let calls=0;
  const controls={'chatgpt-comment':{value:'White moustache.'},'chatgpt-resubmit-input':{value:'original'},'chatgpt-resubmit-status':{},'chatgpt-resubmit':{}};
  const resubmit=vm.runInNewContext(source.slice(source.indexOf('async function resubmitChatgpt('),source.indexOf('function renderPhotoLinks()'))+'\nresubmitChatgpt',{
    state:{chatgpt:current},$:id=>controls[id],confirmChatgptResubmit:async()=>false,
    updateChatgptControls:()=>{},api:async()=>{calls++;}
  });
  await resubmit();assert.equal(calls,0);assert.equal(current.reviewBusy,false);
  assert.match(controls['chatgpt-resubmit-status'].textContent,/Not sent/);
});
test('configured concurrency is respected', async () => {
  for (const limit of [1,5,10]) {
    let active = 0, peak = 0;
    await queue(Array.from({length:12}, (_,i) => i), {stop:false}, async path => {
      peak = Math.max(peak, ++active);
      await new Promise(resolve => setTimeout(resolve, 2)); active--; return path;
    }, () => {}, () => {}, undefined, limit);
    assert.equal(peak, limit);
  }
});
test('queue worker handles paths appended during processing', async () => {
  const paths=[1,2,3], saved=[];
  await queue(paths,{stop:false},async path => {await new Promise(resolve=>setTimeout(resolve,2));return path;},item=>{saved.push(item);if(item===1)paths.push(4,5);},()=>{});
  assert.equal(saved.length,5);
});
test('auto queue reuses pending results and deduplicates fronts for the next batch', () => {
  const current={busy:true,stop:false,batchPaths:['a'],queuedPaths:new Set(),total:1,selected:new Set(),pending:[{front_path:'existing'}]};
  const enqueue=vm.runInNewContext(source.slice(source.indexOf('function queueApprovedPair('),source.indexOf('function photoPairCard('))+'\nqueueApprovedPair',{
    state:{chatgpt:current},colorEligible:()=>true,toast:()=>{},updateChatgptControls:()=>{},renderImages:()=>{},generateChatgpt:()=>{throw new Error('must not restart batch');},Set
  });
  enqueue({front_path:'b'});enqueue({front_path:'b'});enqueue({front_path:'existing',composite_path:'back'});
  assert.equal(current.total,1);assert.equal(current.batchPaths.length,1);
  assert.equal(current.queuedPaths.size,1);assert.equal(current.queuedPaths.has('b'),true);
  assert.equal(current.pending[0].link.composite_path,'back');
});
test('photos selected during generation wait for the next paid batch', async () => {
  const current={selected:new Set(['a']),queuedPaths:new Set(),batchPaths:null,pending:[],review:null,busy:false,reviewBusy:false,stop:false,preview:null};
  const state={folder:'photos',images:['a','b','c'].map(path=>({path})),chatgpt:current};
  const calls=[];
  let releaseFirst, firstStarted;
  const firstGate=new Promise(resolve=>{releaseFirst=resolve;});
  const started=new Promise(resolve=>{firstStarted=resolve;});
  const context={state,photoLinks:[],colorEligible:()=>true,colorScanning:false,chatgptOperation:'restore',colorScanFolder:'photos',chatgptConcurrency:1,stageRoutingEnabled:()=>true,
    $:()=>({textContent:''}),renderImages:()=>{},updateChatgptControls:()=>{},showChatgptPreview:()=>{},toast:()=>{},
    offerChooseOutputFolder:()=>false,
    reviewChatgptItem:item=>{current.review=item;},
    api:async (endpoint,body)=>{calls.push(body.front_path);if(body.front_path==='a'){firstStarted();await firstGate;}return {id:body.front_path,front_path:body.front_path};},
    runChatgptQueue:async (paths,batch,request,saved)=>{for(const path of paths)saved(await request(path),path);return '';}};
  const helper=source.slice(source.indexOf('function chatgptReadyPaths('),source.indexOf('function updateChatgptControls('));
  const generator=source.slice(source.indexOf('async function generateChatgpt('),source.indexOf('async function finishChatgpt('));
  const generate=vm.runInNewContext(helper+generator+'\ngenerateChatgpt',context);
  const running=generate();
  await started;
  current.selected=new Set(['b','c']);
  await generate();
  assert.equal(current.busy,true);
  assert.deepEqual(Array.from(current.batchPaths),['a']);
  assert.deepEqual(Array.from(current.queuedPaths),['b','c']);
  assert.deepEqual(calls,['a']);
  releaseFirst();await running;
  assert.deepEqual(calls,['a','b','c']);
  assert.equal(current.busy,false);
  assert.equal(current.queuedPaths.size,0);
  assert.equal(current.pending.length,3);
});
test('active, queued, and pending photos cannot be selected for another batch', () => {
  const state={images:['a','b','c','d'].map(path=>({path})),chatgpt:{pending:[{front_path:'c'}],batchPaths:['a'],queuedPaths:new Set(['b'])}};
  const ready=vm.runInNewContext(source.slice(source.indexOf('function chatgptReadyPaths('),source.indexOf('function updateChatgptControls('))+'\nchatgptReadyPaths',{
    state,photoLinks:[],colorEligible:()=>true
  });
  assert.deepEqual(Array.from(ready(new Set(['a','b','c','d']))),['d']);
});
test('a next photo can be selected while a GPT batch is running', () => {
  const current={busy:true,reviewBusy:false,selected:new Set(),batchPaths:['a'],anchor:-1,review:null,preview:null};
  const state={images:[{path:'a'},{path:'b'}],chatgpt:current};
  const select=vm.runInNewContext(source.slice(source.indexOf('function selectChatgptImage('),source.indexOf('async function runChatgptQueue('))+'\nselectChatgptImage',{
    state,colorEligible:()=>true,chatgptReadyPaths:selection=>Array.from(selection).filter(path=>path!=='a'),
    toast:()=>{throw new Error('selection should be allowed');},showChatgptPreview:()=>{},renderImages:()=>{},updateChatgptControls:()=>{},$:()=>({textContent:''}),Set
  });
  select(1,{shiftKey:false,ctrlKey:false,metaKey:false});
  assert.deepEqual(Array.from(current.selected),['b']);
});
test('manual thumbnail pairs choose front then back, suggest layout, and reset for the next pair', async () => {
  const paths=['front-landscape','back-one','front-portrait','back-two'];
  const tiles=paths.map(path=>({dataset:{path},classList:{toggle(){}},setAttribute(){},querySelector(){return {textContent:''};}}));
  const elements={
    'photo-pairs-manual-gallery':{children:tiles,scrollIntoView(){}},
    'photo-pairs-manual-preview':{replaceChildren(){},scrollIntoView(){}},
    'photo-pairs-manual-step':{textContent:''}
  };
  const cards=[];
  const context={manualPairFront:null,manualPairBack:null,manualPairRequest:0,$:id=>elements[id],
    colorEligible:()=>true,toast:()=>{},
    api:async path=>path.includes('front-landscape')?{width:120,height:80}:{width:80,height:120},
    photoPairCard:(item,target,onApproved)=>cards.push({item,onApproved})};
  const choose=vm.runInNewContext(source.slice(source.indexOf('function suggestedManualPairLayout('),source.indexOf('function photoPairCard('))+'\nselectManualPairImage',context);
  await choose('front-landscape');await choose('back-one');
  assert.equal(cards[0].item.layout,'below');
  assert.equal(cards[0].item.front_path,'front-landscape');
  assert.equal(cards[0].item.composite_path,'back-one');
  cards[0].onApproved();
  assert.equal(context.manualPairFront,null);
  await choose('front-portrait');await choose('back-two');
  assert.equal(cards[1].item.layout,'right');
  assert.equal(cards[1].item.front_path,'front-portrait');
});
test('stop prevents new work but preserves all three active results', async () => {
  const current = {stop:false}, saved = [];
  await queue([1,2,3,4,5,6], current, async path => {
    await new Promise(resolve => setTimeout(resolve, 5)); current.stop = true; return path;
  }, item => saved.push(item), () => {});
  assert.equal(saved.length, 3);
});
test('an image failure is reported while all remaining photos are dispatched', async () => {
  const saved = []; let calls = 0;
  const failure = await queue([1,2,3,4,5], {stop:false}, async path => {
    calls++; await new Promise(resolve => setTimeout(resolve, 5));
    if (path === 1) throw new Error('failed'); return path;
  }, item => saved.push(item), () => {});
  assert.equal(calls, 5); assert.equal(saved.length, 4); assert.equal(failure, '');
});
test('quota error is not automatically retried', async () => {
  let calls = 0;
  await queue([1], {stop:false}, async () => {
    calls++; throw Object.assign(new Error('quota'), {status:429, code:'insufficient_quota'});
  }, () => {}, () => {});
  assert.equal(calls, 1);
});
test('rate limit is not retried by the browser queue after the server responded', async () => {
  let calls = 0; const saved = []; const failed = [];
  const failure = await queue([1], {stop:false}, async () => {
    calls++; throw Object.assign(new Error('rate limited'), {status:429, retryAfter:5});
  }, item => saved.push(item), () => {}, undefined, 3, (path, error) => failed.push(error.message));
  assert.equal(calls, 1); assert.equal(saved.length, 0); assert.equal(failed.length, 1); assert.equal(failure, '');
});
test('500 and 401 are not retried by the browser queue', async () => {
  for (const status of [500, 401]) {
    let calls = 0;
    const failure = await queue([1], {stop:false}, async () => {
      calls++; throw Object.assign(new Error('fail'), {status, code: status === 401 ? 'invalid_api_key' : 'server_error'});
    }, () => {}, () => {});
    assert.equal(calls, 1);
    if (status === 401) assert.match(failure, /fail/);
  }
});
