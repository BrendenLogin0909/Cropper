const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/app.js', 'utf8');
const between = (a,b) => source.slice(source.indexOf(a), source.indexOf(b));
const queueSource = between('async function runChatgptQueue(', 'async function generateChatgpt(');

function generatorHarness(operation, concurrency, request) {
  const paths = Array.from({length:100}, (_,i)=>`photo-${i}`);
  const current = {selected:new Set(paths), queuedPaths:new Set(), pending:[], review:null, preview:null};
  const state = {folder:'photos', images:paths.map(path=>({path})), chatgpt:current};
  const controls = {};
  const context = {state, photoLinks:[], colorEligible:()=>true, colorScanning:false,
    chatgptOperation:operation, colorScanFolder:'photos', chatgptConcurrency:concurrency,
    stageRoutingEnabled:()=>true, setTimeout, Date,
    $:id=>controls[id] ||= {}, renderImages:()=>{}, updateChatgptControls:()=>{},
    showChatgptPreview:()=>{}, toast:()=>{}, reviewChatgptItem:item=>{current.review=item;},
    api:async (route,body)=>request(body)};
  const generate = vm.runInNewContext(
    between('function chatgptReadyPaths(', 'function updateChatgptControls(') + queueSource +
    between('async function generateChatgpt(', 'async function finishChatgpt(') + '\ngenerateChatgpt', context);
  return {generate,current,controls};
}

test('Generate 100 processes every selected photo at 1, 3, 5 and 10 parallel requests in both AI tabs', async () => {
  for (const operation of ['restore','colorize']) for (const concurrency of [1,3,5,10]) {
    let active=0, peak=0;
    const sent=[];
    const harness=generatorHarness(operation,concurrency,async body=>{
      sent.push(body.front_path);
      assert.equal(body.operation,operation);
      peak=Math.max(peak,++active);
      await new Promise(resolve=>setTimeout(resolve,0));
      active--;
      return {id:body.front_path,front_path:body.front_path};
    });
    await harness.generate();
    assert.equal(sent.length,100); assert.equal(new Set(sent).size,100);
    assert.equal(peak,concurrency); assert.equal(harness.current.pending.length,100);
    assert.equal(harness.current.busy,false); assert.equal(harness.current.selected.size,0);
    assert.match(harness.controls['chatgpt-progress'].textContent,/100 of 100/);
  }
});

test('failure at photo ten does not abandon the other ninety; failed photo stays selected with its error', async () => {
  const calls=[];
  const harness=generatorHarness('restore',5,async body=>{
    calls.push(body.front_path);
    if(body.front_path==='photo-9') throw Object.assign(Error('Mock image failure'),{status:502});
    return {id:body.front_path,front_path:body.front_path};
  });
  await harness.generate();
  assert.equal(calls.length,100); assert.equal(harness.current.pending.length,99);
  assert.deepEqual([...harness.current.selected],['photo-9']);
  assert.equal(harness.current.failures.get('photo-9'),'Mock image failure');
  assert.match(harness.controls['chatgpt-progress'].textContent,/99 of 100.*1 failed/);
  assert.match(harness.controls['chatgpt-progress'].title,/photo-9: Mock image failure/);
});

test('account errors halt dispatch, drain active requests and preserve the rest for manual retry', async () => {
  for (const error of [{status:401},{status:403},{status:429,code:'insufficient_quota'},
    {status:429,type:'insufficient_quota',code:'billing_not_active'}]) {
    let calls=0;
    const harness=generatorHarness('restore',3,async body=>{
      calls++;
      await new Promise(resolve=>setTimeout(resolve,0));
      if(body.front_path==='photo-0') throw Object.assign(Error('Account action required'),error);
      return {id:body.front_path,front_path:body.front_path};
    });
    await harness.generate();
    assert.equal(calls,3); assert.equal(harness.current.pending.length,2);
    assert.equal(harness.current.selected.size,98);
    assert.match(harness.controls['chatgpt-progress'].textContent,/Stopped after 2 of 100/);
  }
});

test('sustained throttling recovers beyond three retries and respects a long Retry-After', async () => {
  let clock=0, calls=0;
  const queue=vm.runInNewContext(queueSource+'\nrunChatgptQueue',{setTimeout, Date:{now:()=>clock,parse:Date.parse}});
  const saved=[];
  const result=await queue(['first','second'],{stop:false},async path=>{
    calls++;
    if(calls<=5) throw Object.assign(Error('Slow down'),{status:429,retryAfter:300});
    return path;
  },item=>saved.push(item),()=>{},async ms=>{clock+=ms;},1);
  assert.equal(result,''); assert.equal(calls,7);
  assert.ok(clock>=5*300000); assert.deepEqual(saved,['first','second']);
});

test('Stop during rate-limit cooldown prevents retrying or starting remaining photos', async () => {
  let clock=0,calls=0; const current={stop:false};
  const queue=vm.runInNewContext(queueSource+'\nrunChatgptQueue',{setTimeout,Date:{now:()=>clock,parse:Date.parse}});
  await queue(['first','second'],current,async()=>{
    calls++;throw Object.assign(Error('Slow down'),{status:429});
  },()=>{},()=>{},async ms=>{clock+=ms;current.stop=true;},1);
  assert.equal(calls,1);
});

function reviewHarness(items,selectedId,accept,arriving=[]) {
  const current={pending:items,review:items.find(item=>item.id===selectedId),selected:new Set(),queuedPaths:new Set()};
  const context={state:{chatgpt:current,images:items.map(item=>({path:item.front_path}))},
    api:async()=>{current.pending.push(...arriving);},$:()=>({}),selectedPairs:new Set(),
    renderPhotoLinks:()=>{},renderImages:()=>{},updateChatgptControls:()=>{},showChatgptPreview:()=>{},
    reviewChatgptItem:item=>{current.review=item;},toast:()=>{}};
  const finish=vm.runInNewContext(between('async function finishChatgpt(', 'function confirmChatgptResubmit(')+'\nfinishChatgpt',context);
  return finish(accept).then(()=>current);
}
const pending = (...ids) => ids.map(id=>({id,front_path:`${id}.png`}));

test('Accept and Reject advance below a manually selected photo without returning to an earlier skipped photo', async () => {
  for(const accept of [true,false]) {
    const result=await reviewHarness(pending('skipped','second','third','fourth'),'third',accept);
    assert.equal(result.review.id,'fourth');
    assert.equal(result.pending[0].id,'skipped');
  }
});

test('at the bottom of the list, review advances to the nearest remaining image above', async () => {
  for(const accept of [true,false]) {
    const result=await reviewHarness(pending('skipped','second','third'),'third',accept);
    assert.equal(result.review.id,'second');
  }
});

test('Accept skips other attempts removed for the same source and selects the next surviving image below', async () => {
  const items=pending('skipped','attempt-1','attempt-2','next');
  items[2].front_path=items[1].front_path;
  const result=await reviewHarness(items,'attempt-1',true);
  assert.equal(result.review.id,'next'); assert.equal(result.pending.length,2);
});

test('review advances into a result that arrives during acceptance, and clears review after the last result', async () => {
  const result=await reviewHarness(pending('skipped','current'),'current',true,pending('arrived'));
  assert.equal(result.review.id,'arrived');
  const empty=await reviewHarness(pending('only'),'only',false);
  assert.equal(empty.review,null); assert.equal(empty.pending.length,0);
});
