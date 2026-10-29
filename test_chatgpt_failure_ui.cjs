const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/app.js', 'utf8');

test('failed filenames and reasons remain visible as text, including unsafe-looking filenames', () => {
  const controls = {};
  const current = {failures:new Map([
    ['C:\\photos\\family.png', 'The image service had a temporary failure. (HTTP 500; request req_example)'],
    ['C:\\photos\\<img onerror=alert(1)>.png', 'The image service rejected this photo during its safety review.']
  ])};
  const context = {state:{chatgpt:current}, $:id=>controls[id] ||= {
    hidden:null, classList:{toggle(name, hidden) { controls[id].hidden=hidden; }}
  }};
  const render = vm.runInNewContext(source.slice(source.indexOf('function renderChatgptFailures()'),
    source.indexOf('async function runChatgptQueue('))+'\nrenderChatgptFailures', context);
  render();
  assert.equal(controls['chatgpt-failures'].hidden,false);
  assert.equal(controls['chatgpt-failure-count'].textContent,2);
  assert.match(controls['chatgpt-failure-list'].textContent,/family.png:.*HTTP 500/);
  assert.match(controls['chatgpt-failure-list'].textContent,/<img onerror=alert\(1\)>.png:.*safety review/);
  assert.equal(controls['chatgpt-failure-list'].innerHTML,undefined);
  current.failures.clear();render();
  assert.equal(controls['chatgpt-failures'].hidden,true);
  assert.equal(controls['chatgpt-failure-list'].textContent,'');
});
