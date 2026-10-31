const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/app.js', 'utf8');
const between = (a,b) => source.slice(source.indexOf(a),source.indexOf(b));
const deferred = () => { let resolve, reject; const promise=new Promise((a,b)=>{resolve=a;reject=b;}); return {promise,resolve,reject}; };

function harness() {
  const first={id:'first',front_path:'first.png'}, second={id:'second',front_path:'second.png'};
  const current={pending:[first,second],review:first,selected:new Set(),queuedPaths:new Set(),preview:first.front_path};
  const controls={};
  const element=()=>({value:'',textContent:'',firstElementChild:{},parentElement:{lastChild:{nodeType:1}},
    style:{setProperty(){}},classList:{toggle(){}},setAttribute(){},addEventListener(){},
    querySelectorAll(){return [];},replaceChildren(){this.children=[];},append(item){(this.children ||= []).push(item);}});
  const requests=[], paid=[];
  const context={state:{view:'chatgpt',folder:'photos',images:[{path:first.front_path},{path:second.front_path}],chatgpt:current,enhance:{}},
    $:id=>controls[id] ||= element(), document:{querySelector:()=>element(),createElement:()=>element()},
    stageRoutingEnabled:()=>false,chatgptReadyPaths:()=>[],updateColorizeToolbarControls:()=>{},
    chatgptOperation:'restore',colorScanning:false,colorScanFolder:'photos',selectedPairs:new Set(),
    combinedReview:false,chatgptCommentDrafts:new Map(),renderPhotoLinks:()=>{},renderImages:()=>{},
    showChatgptPreview:()=>{},toast:()=>{},confirmChatgptResubmit:async()=>true,offerChooseOutputFolder:()=>false,
    api:async(route,body)=>{
      requests.push({route,body});
      if(route==='/api/chatgpt-resubmit') {const pending=deferred();paid.push(pending);return pending.promise;}
      if(route==='/api/chatgpt-accept' || route==='/api/chatgpt-reject') return {};
      throw Error('Unexpected route');
    }};
  const code=between('function renderChatgptFailures()', 'async function runChatgptQueue(') +
    between('function updateChatgptControls()', 'function clearChatgptPreview()') +
    between('function reviewChatgptItem(', 'function selectChatgptImage(') +
    between('async function finishChatgpt(', 'function confirmChatgptResubmit(') +
    between('async function resubmitChatgpt()', 'function renderPhotoLinks()');
  const functions=vm.runInNewContext(code+'\n({resubmitChatgpt,finishChatgpt,reviewChatgptItem,updateChatgptControls})',context);
  context.$('chatgpt-comment').value='White moustache';
  return {first,second,current,controls,requests,paid,context,...functions};
}

test('waiting for a modification locks only its photo and allows selection, approval and rejection of another image', async () => {
  for(const accept of [true,false]) {
    const h=harness(); const running=h.resubmitChatgpt(); await new Promise(resolve=>setImmediate(resolve));
    assert.equal(h.current.reviewBusy,false);
    assert.equal(h.current.resubmissions.has('first.png'),true);
    assert.equal(h.controls['chatgpt-accept'].disabled,true);
    assert.ok(h.controls['chatgpt-pending-list'].children.every(button=>!button.disabled));
    assert.match(h.controls['chatgpt-pending-list'].children[0].textContent,/Modifying/);
    h.reviewChatgptItem(h.second);
    assert.equal(h.current.review.id,'second');
    assert.equal(h.controls['chatgpt-accept'].disabled,false);
    await h.finishChatgpt(accept);
    assert.equal(h.requests.length,2); assert.equal(h.current.pending.some(item=>item.id==='second'),false);
    h.paid[0].resolve({id:'corrected',front_path:'first.png',attempt:2}); await running;
    assert.equal(h.current.pending.some(item=>item.id==='corrected'),true);
    assert.equal(h.current.resubmissions.size,0);
  }
});

test('a completed correction does not replace another selected comparison or its draft', async () => {
  const h=harness(); const running=h.resubmitChatgpt(); await new Promise(resolve=>setImmediate(resolve));
  h.reviewChatgptItem(h.second);
  h.controls['chatgpt-comment'].value='Keep this second-image draft';
  h.paid[0].resolve({id:'corrected',front_path:'first.png',attempt:2}); await running;
  assert.equal(h.current.review.id,'second');
  assert.equal(h.controls['chatgpt-comment'].value,'Keep this second-image draft');
  assert.equal(h.current.pending.length,3);
  assert.match(h.controls['chatgpt-resubmit-status'].textContent,/Reviewing attempt 1/);
});

test('completion cannot release an approval lock owned by another pending image', async () => {
  const h=harness(); const running=h.resubmitChatgpt(); await new Promise(resolve=>setImmediate(resolve));
  h.reviewChatgptItem(h.second);
  const acceptance=deferred();
  const originalApi=h.context.api;
  h.context.api=async(route,body)=>route==='/api/chatgpt-accept'?acceptance.promise:originalApi(route,body);
  const accepting=h.finishChatgpt(true);
  assert.equal(h.current.reviewBusy,true);
  h.paid[0].resolve({id:'corrected',front_path:'first.png',attempt:2}); await running;
  assert.equal(h.current.reviewBusy,true); assert.equal(h.current.review.id,'second');
  acceptance.resolve({});await accepting;
  assert.equal(h.current.reviewBusy,false);
});

test('no attempt for the same source may be accepted, rejected or resubmitted while modification is running', async () => {
  const h=harness(); const running=h.resubmitChatgpt(); await new Promise(resolve=>setImmediate(resolve));
  const alternate={id:'alternate',front_path:'first.png'};h.current.pending.push(alternate);
  h.reviewChatgptItem(alternate);
  h.controls['chatgpt-comment'].value='Another paid comment';
  await h.finishChatgpt(true);await h.finishChatgpt(false);await h.resubmitChatgpt();
  assert.equal(h.requests.length,1);
  assert.equal(h.controls['chatgpt-accept'].disabled,true);
  h.paid[0].resolve({id:'corrected',front_path:'first.png',attempt:2});await running;
  assert.equal(h.current.review.id,'alternate');
});

test('separate modifications can finish independently without releasing each other or changing the comparison', async () => {
  const h=harness();const first=h.resubmitChatgpt();await new Promise(resolve=>setImmediate(resolve));
  h.reviewChatgptItem(h.second);h.controls['chatgpt-comment'].value='Modify the second';
  const second=h.resubmitChatgpt();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(h.current.resubmissions.size,2);
  h.paid[0].resolve({id:'first-corrected',front_path:'first.png',attempt:2});await first;
  assert.equal(h.current.review.id,'second');assert.equal(h.current.resubmissions.size,1);
  assert.equal(h.controls['chatgpt-accept'].disabled,true);
  h.paid[1].resolve({id:'second-corrected',front_path:'second.png',attempt:2});await second;
  assert.equal(h.current.resubmissions.size,0);assert.equal(h.current.review.id,'second-corrected');
});

test('accept label names the other attempt count', () => {
  const h=harness();
  h.current.pending.push({id:'alt',front_path:'first.png'});
  h.current.pending.push({id:'alt2',front_path:'first.png'});
  h.updateChatgptControls();
  assert.equal(h.controls['chatgpt-accept'].textContent,'Accept this version and discard the other 2 attempts');
  h.current.pending=h.current.pending.filter(item=>item.id!=='alt2');
  h.updateChatgptControls();
  assert.equal(h.controls['chatgpt-accept'].textContent,'Accept this version and discard the other 1 attempt');
  h.reviewChatgptItem(h.second);
  h.updateChatgptControls();
  assert.equal(h.controls['chatgpt-accept'].textContent,'Accept and file result');
});

test('failed modification preserves the previous attempt and reports its error without overwriting another review', async () => {
  const h=harness();const running=h.resubmitChatgpt();await new Promise(resolve=>setImmediate(resolve));
  h.reviewChatgptItem(h.second);const status=h.controls['chatgpt-resubmit-status'].textContent;
  h.paid[0].reject(Error('Mock correction failure'));await running;
  assert.equal(h.current.review.id,'second');assert.equal(h.current.pending.length,2);
  assert.equal(h.controls['chatgpt-resubmit-status'].textContent,status);
  assert.equal(h.current.resubmissions.size,0);
  h.reviewChatgptItem(h.first);
  assert.match(h.controls['chatgpt-resubmit-status'].textContent,/Mock correction failure/);
  assert.equal(h.controls['chatgpt-comment'].disabled,false);
});
