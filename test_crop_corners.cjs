const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const code=fs.readFileSync(__dirname+'/app.js','utf8');
function harness(api) {
  const state={index:0,width:600,height:400,busy:false,requestId:1,points:[],images:[{path:'photo.jpg',modified:1,bytes:10}],rotation:0};
  const messages=[];
  const predictSpan={textContent:'Predict corners'};
  const context={
    state,api,settings:{autoNext:false},
    $:(id)=>({
      classList:{contains:()=>false,add(){},remove(){}},
      querySelector:()=>id==='predict-corners'?predictSpan:{textContent:''},
    }),
    endZoom(){},updateControls(){
      predictSpan.textContent=vm.runInContext('cropPredictionBusy',context)?'Finding corners…':'Predict corners';
    },
    renderOverlay(){},toast:m=>messages.push(m),
    Map,AbortController,JSON,Set,Date,
    ordered:(points)=>points&&points.length===4,
    validSettings:()=>'',
    cropAspectRatio:()=>null,
    stageRoutingEnabled:()=>true,
    ensureOverwriteConfirmed:async()=>({}),
    refreshStageFolders:async()=>{},
    offerChooseOutputFolder:()=>false,
    renderImages(){},
    clearEditor(){},
    openImage:async()=>{},
  };
  vm.createContext(context);
  vm.runInContext(code.slice(code.indexOf('const CROP_PREDICTION_CACHE_LIMIT'),code.indexOf('function clearLiveCropPreview(')),context);
  return {
    state,messages,context,predictSpan,
    predict:()=>vm.runInContext('predictCropCorners()',context),
    clear:()=>vm.runInContext('clearCropPoints()',context),
    cropSave:()=>vm.runInContext('cropSave()',context),
    forget:(path)=>vm.runInContext('forgetPredictionsForPath('+JSON.stringify(path)+')',context),
  };
}
test('client prediction cache is a bounded Map and predict passes an AbortSignal',()=>{
  assert.match(code,/CROP_PREDICTION_CACHE_LIMIT = 32/);
  assert.match(code,/cropPredictionCache = new Map/);
  assert.match(code,/signal: controller.signal/);
  assert.match(code,/cropPredictionAbort.abort/);
  assert.match(code,/forgetPredictionsForPath\(state\.images\[sourceIndex\]\.path\)/);
  assert.match(code,/async function openImage\(index\) \{\r?\n  if \(index < 0[^\r\n]+\r?\n  invalidateInFlightPredict\(\);/);
  assert.match(code,/if \(state\.restore\.busy\) \{ toast\('Stop or finish the restoration batch before changing folders\.', true\); return; \}\r?\n  invalidateInFlightPredict\(\);/);
});
test('cycles candidates without additional requests; clears all points',async()=>{
  let calls=0;
  const h=harness(async()=>{calls++;return {candidates:[{points:[[10,10],[300,10],[300,300],[10,300]]},{points:[[320,10],[550,10],[550,300],[320,300]]}]};});
  await h.predict();assert.equal(h.state.points[0].x,10);
  await h.predict();assert.equal(h.state.points[0].x,320);assert.equal(calls,1);
  h.clear();assert.equal(h.state.points.length,0);
  await h.predict();assert.equal(calls,2);
});
test('one manually placed point targets the intended photo',async()=>{
  let sent;
  const h=harness(async(endpoint,body)=>{sent=body;return {candidates:[]};});
  h.state.points=[{x:150,y:200}];await h.predict();
  assert.deepEqual(Array.from(sent.target),[150,200]);assert.equal(h.state.points.length,1);
});
test('late response cannot change a different image or undo clear',async()=>{
  let release;
  const h=harness(()=>new Promise(r=>release=r));
  const request=h.predict();h.state.requestId++;
  release({candidates:[{points:[[1,1],[2,1],[2,2],[1,2]]}]});await request;
  assert.equal(h.state.points.length,0);
});
test('late predict for image A is dropped after image B is current',async()=>{
  let release;
  const h=harness(()=>new Promise(r=>release=r));
  const request=h.predict();
  h.state.images[0]={path:'other.jpg',modified:2};
  h.state.width=800;h.state.height=600;
  release({candidates:[{points:[[1,1],[2,1],[2,2],[1,2]]}]});await request;
  assert.equal(h.state.points.length,0);
});
test('in-place crop save drops the client Map so the next predict refetches',async()=>{
  let calls=0;
  const h=harness(async(url)=>{
    if(String(url).includes('/api/crop-corners')){calls+=1;return {candidates:[{points:[[10,10],[300,10],[300,300],[10,300]]}]};}
    if(String(url)==='/api/crop') return {name:'photo.jpg',path:'photo.jpg',width:10,height:10,source_moved:false};
    return {};
  });
  await h.predict();
  assert.equal(calls,1);
  h.state.points=[{x:10,y:10},{x:300,y:10},{x:300,y:300},{x:10,y:300}];
  await h.cropSave();
  await h.predict();
  assert.equal(calls,2);
});
test('cancelled predict aborts the in-flight request',async()=>{
  let abortSeen=false;
  const h=harness((url,body,options)=>new Promise((_,reject)=>{
    const signal=options&&options.signal;
    if(!signal)return;
    const fail=()=>{abortSeen=true;const error=new Error('The operation was aborted.');error.name='AbortError';reject(error);};
    if(signal.aborted){fail();return;}
    signal.addEventListener('abort',fail);
  }));
  h.state.images=[{path:'photo.jpg',modified:1,bytes:10}];
  const request=h.predict();
  h.clear();
  await request;
  assert.equal(abortSeen,true);
  assert.equal(h.state.points.length,0);
});
test('old backend gives an explicit restart message without modifying points',async()=>{
  const h=harness(async()=>{throw new Error('Not found.');});
  await h.predict();assert.match(h.messages[0],/backend restart/);assert.equal(h.state.points.length,0);
});
test('shortcuts preserve text entry and Shift zoom, and bind Ctrl+A to predict',()=>{
  const keys=code.slice(code.indexOf("document.addEventListener('keydown'"));
  assert.ok(keys.indexOf("'INPUT','TEXTAREA','SELECT'")<keys.indexOf("event.key==='Shift'"));
  assert.match(keys,/event.key==='Shift'/);
  assert.match(keys,/event.ctrlKey\|\|event.metaKey/);
  assert.match(keys,/key==='z'/);
  assert.match(keys,/key==='q'/);
  assert.match(keys,/clearCropPoints\(\)/);
  assert.match(keys,/undoCropPoint\(\)/);
  assert.doesNotMatch(keys,/event.key==='Control'/);
  assert.match(keys,/key==='a'/);
  assert.match(keys,/!event.repeat\)predictCropCorners\(\)/);
  assert.match(keys,/event.shiftKey\)selectWholeImage\(\)/);
  assert.match(keys,/key==='p'/);
  assert.match(keys,/key==='w'/);
  assert.match(keys,/key==='c'/);
  assert.match(keys,/key==='r'/);
  assert.match(keys,/key==='d'/);
  assert.doesNotMatch(keys,/key==='s'/);
  assert.match(keys,/event.isComposing \|\| event.keyCode === 229/);
  assert.match(keys,/ArrowLeft.*openImage\(state.index-1\)/);
  assert.match(keys,/ArrowRight.*openImage\(state.index\+1\)/);
  const html=fs.readFileSync(__dirname+'/index.html','utf8');
  assert.match(html,/SHIFT[\s\S]*hold to zoom/);
  assert.match(html,/<kbd>P<\/kbd> predict corners/);
  assert.match(html,/<kbd>W<\/kbd> whole image/);
  assert.match(html,/<kbd>Z<\/kbd> undo/);
  assert.match(html,/<kbd>C<\/kbd> clear points/);
  assert.match(html,/<kbd>R<\/kbd> rotate/);
  assert.match(html,/<kbd>A<\/kbd> previous/);
  assert.match(html,/<kbd>D<\/kbd> next/);
  assert.doesNotMatch(html,/<kbd>Ctrl<\/kbd><kbd>A<\/kbd> predict corners/);
  assert.doesNotMatch(html,/<kbd>Ctrl<\/kbd><kbd>Shift<\/kbd><kbd>A<\/kbd> whole image/);
  assert.doesNotMatch(html,/<kbd>S<\/kbd>/);
  assert.match(html,/Choose back/);
  assert.doesNotMatch(html,/Runs on this computer/);
  assert.match(html,/data-testid="processing-location-crop">Local/);
  assert.match(html,/data-testid="processing-location-enhance">Local/);
  assert.match(html,/data-testid="processing-location-chatgpt">OpenAI, uses your API key/);
});
test('merge keeps front by path and opens Choose back from the current folder',()=>{
  assert.match(code,/merge: \{ front: null, back: null/);
  assert.match(code,/function emptyMerge\(\)/);
  assert.match(code,/pickFolder\(false, true\)/);
  assert.match(code,/pickNativeFolder/);
  assert.match(code,/\/api\/pick-folder/);
  assert.doesNotMatch(code,/state.merge = \{ front: -1/);
  assert.match(code,/front_path: front.path, back_path: back.path/);
});
