const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const code=fs.readFileSync(__dirname+'/app.js','utf8');
function harness(api) {
  const state={index:0,width:600,height:400,busy:false,requestId:1,points:[],images:[{path:'photo.jpg',modified:1}]};
  const messages=[];
  const context={state,api,$:()=>({classList:{contains:()=>false}}),endZoom(){},updateControls(){},renderOverlay(){},toast:m=>messages.push(m)};
  vm.createContext(context);
  vm.runInContext(code.slice(code.indexOf('let cropPredictionCache ='),code.indexOf('function selectWholeImage(')),context);
  return {state,messages,predict:()=>vm.runInContext('predictCropCorners()',context),clear:()=>vm.runInContext('clearCropPoints()',context)};
}
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
test('old backend gives an explicit restart message without modifying points',async()=>{
  const h=harness(async()=>{throw new Error('Not found.');});
  await h.predict();assert.match(h.messages[0],/backend restart/);assert.equal(h.state.points.length,0);
});
test('shortcuts preserve text entry, Shift zoom and do not intercept Ctrl',()=>{
  const keys=code.slice(code.indexOf("document.addEventListener('keydown'"));
  assert.ok(keys.indexOf("'INPUT','TEXTAREA','SELECT'")<keys.indexOf("event.key==='Shift'"));
  assert.match(keys,/event.key==='Shift'/);
  assert.match(keys,/event.ctrlKey\|\|event.metaKey/);
  assert.match(keys,/key==='z'/);
  assert.match(keys,/key==='q'/);
  assert.match(keys,/clearCropPoints\(\)/);
  assert.match(keys,/state.points.pop\(\)/);
  assert.doesNotMatch(keys,/event.key==='Control'/);
  assert.doesNotMatch(keys,/event.key.toLowerCase\(\)==='a'&&event.ctrlKey/);
  assert.doesNotMatch(keys,/predictCropCorners\(\);\}/);
  assert.match(keys,/ArrowLeft.*openImage\(state.index-1\)/);
  assert.match(keys,/ArrowRight.*openImage\(state.index\+1\)/);
  const html=fs.readFileSync(__dirname+'/index.html','utf8');
  assert.match(html,/SHIFT[\s\S]*hold to zoom/);
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
