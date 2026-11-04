import test from 'node:test';
import assert from 'node:assert/strict';
import {createCollaborationBoard} from '../talkabout/web/collaboration-board.mjs';

class Node {
  constructor(tag) { this.tag = tag; this.children = []; this._text = ''; this.hidden = false; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this._text = ''; this.children = items; }
}
let confirmations = [], confirmResult = true, keyCounter = 0;
const document = {createElement: tag => new Node(tag), defaultView:{confirm: message => {confirmations.push(message); return confirmResult;}, crypto:{randomUUID: () => 'key-' + ++keyCounter}}};
const buttons = root => { const result = []; const visit = n => { if (n.tag === 'button') result.push(n); for (const c of n.children) visit(c); }; visit(root); return result; };
const all = (root, predicate) => { const out = []; const visit = n => { if (predicate(n)) out.push(n); for (const c of n.children) visit(c); }; visit(root); return out; };
const click = (root, text) => { const button = buttons(root).find(b => b.textContent === text); assert.ok(button, text); button.onclick(); };
const tick = () => new Promise(resolve => setImmediate(resolve));
const approved = () => ({...proposal(), state:'approved', revision:2});
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; };
const proposal = (summary = 'secret') => ({id:'1', author_id:'a', target_id:'b', kind:'suggestion', summary, requested_output:'text', state:'proposed', revision:1, stale:false, created_at:123});
function setup(handler = async () => ({proposals:[proposal()]})) {
  const ctx = {isOwner:true, user:{id:'owner', kind:'human'}, authTicket:1, viewTicket:1,
    room:{id:'room /1', membership_revision:1, effective_permissions:['room:read'], members:{owner:{status:'active', epoch:1}}}};
  const calls = [], container = new Node('div');
  const board = createCollaborationBoard({document, getContext:() => ctx, api:(...args) => {calls.push(args); return handler(...args);}});
  board.mount(container);
  return {ctx, calls, container, board, root:() => container.children[0]};
}

test('explicit GET, inert fields, no automatic mutations', async () => {
  const h = setup(async () => ({proposals:[proposal('<img src=x onerror=alert(1)>')]}));
  assert.equal(h.calls.length, 0);
  assert.match(h.root().textContent, /Not loaded/);
  await h.board.load();
  assert.deepEqual(h.calls, [['rooms/room%20%2F1/collaboration-proposals', {method:'GET'}]]);
  assert.match(h.root().textContent, /<img src=x onerror=alert\(1\)>/);
  assert.match(h.root().textContent, /requested_output: text/);
  assert.equal(h.root().children[2].children[0].children[4].tag, 'p');
  h.board.render(); assert.equal(h.calls.length, 1);
  await h.board.load(); assert.equal(h.calls.length, 2);
});

test('optional completion criteria stay untrusted, bounded and inert', async () => {
  const h = setup(async () => ({proposals:[{...proposal(), done_when:['<img src=x>', 'Check tests']}]}));
  await h.board.load();
  assert.match(h.root().textContent, /<img src=x>/);
  assert.match(h.root().textContent, /검증되지 않음/);
  assert.equal(h.calls.length, 1);
  const bad = setup(async () => ({proposals:[{...proposal(), done_when: Array(9).fill('x')}]}));
  await bad.board.load();
  assert.match(bad.root().textContent, /Invalid response/);
});

test('decision confirmation, body, key, double click and no automatic retry', async () => {
  confirmations = []; confirmResult = false;
  const d = deferred();
  const h = setup((path, options) => options.method === 'GET' ? Promise.resolve({proposals:[proposal()]}) : d.promise);
  await h.board.load();
  let [approve] = buttons(h.root()); approve.onclick();
  assert.equal(h.calls.length, 1); assert.match(confirmations[0], /never starts tasks/);
  confirmResult = true; approve.onclick(); approve.onclick();
  assert.equal(h.calls.length, 2);
  assert.deepEqual(h.calls[1], ['rooms/room%20%2F1/collaboration-proposals/1/decision', {method:'POST', body:{expected_revision:1, decision:'approve'}, key:'key-1'}]);
  await h.board.load(); assert.equal(h.calls.length, 2);
  d.reject(new Error('network')); await new Promise(resolve => setImmediate(resolve));
  assert.match(h.root().textContent, /uncertain/); assert.equal(buttons(h.root()).length, 1);
  await h.board.load(); [approve] = buttons(h.root()); approve.onclick();
  assert.equal(h.calls.length, 4); assert.notEqual(h.calls[3][1].key, h.calls[1][1].key);
});

test('reject records only the decision and requires fresh load afterward', async () => {
  const h = setup((path, opts) => opts.method === 'GET' ? Promise.resolve({proposals:[proposal()]}) :
    Promise.resolve({id:'1', revision:2, state:'rejected', execution_started:false}));
  await h.board.load(); buttons(h.root())[1].onclick(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.calls[1][1].body.decision, 'reject');
  assert.match(h.root().textContent, /Decision recorded/); assert.equal(buttons(h.root()).length, 1);
});

test('stale or decided proposals never offer actions; invalid stale fails closed', async () => {
  for (const p of [{...proposal(), stale:true}, {...proposal(), state:'approved'}, {...proposal(), stale:undefined}]) {
    const h = setup(async () => ({proposals:[p]})); await h.board.load();
    assert.equal(buttons(h.root()).length, 1);
  }
});

test('decision responses are fenced and errors purge data', async () => {
  for (const change of [h => {h.ctx.authTicket++;}, h => h.board.clear(), h => h.board.mount(h.container)]) {
    const d = deferred(); const h = setup((path, opts) => opts.method === 'GET' ? Promise.resolve({proposals:[proposal()]}) : d.promise);
    await h.board.load(); buttons(h.root())[1].onclick(); change(h); d.resolve({}); await new Promise(resolve => setImmediate(resolve));
    assert.doesNotMatch(h.container.textContent, /secret|Decision recorded/);
  }
  for (const status of [401, 403, 404, 409]) {
    const h = setup((path, opts) => opts.method === 'GET' ? Promise.resolve({proposals:[proposal()]}) : Promise.reject(Object.assign(new Error(), {status})));
    await h.board.load(); buttons(h.root())[1].onclick(); await new Promise(resolve => setImmediate(resolve));
    assert.doesNotMatch(h.container.textContent, /secret/); assert.equal(h.root().hidden, status === 401);
  }
});

function openEditor(h) { click(h.root(), 'Review for private plan'); }
function fill(h) {
  for (const [name, value] of Object.entries({id:'new_node', title:'Reviewed title', instruction:'Human instruction', assignee:'b', write_scope:'analysis', depends_on:'existing'})) {
    const field = all(h.root(), n => n.name === name)[0]; field.value = value; field.oninput();
  }
  const ack = all(h.root(), n => n.type === 'checkbox')[0]; ack.checked = true; ack.onchange();
}
function applySetup(handler) {
  const h = setup(handler); h.ctx.room.effective_permissions.push('room:control'); return h;
}

test('explicit revision and human-reviewed apply use CAS without dispatch', async () => {
  confirmResult = true;
  const h = applySetup(async (path, opts) => path.endsWith('/work-plan') ? {revision:3, nodes:[{id:'existing'}]} :
    opts.method === 'GET' ? {proposals:[approved()]} : {id:'1', revision:3, state:'applied', plan_revision:4, execution_started:false});
  await h.board.load(); openEditor(h);
  assert.match(h.root().textContent, /not an enforced file sandbox/);
  assert.match(h.root().textContent, /does not make a worker read-only/);
  assert.equal(h.calls.length, 1);
  assert.equal(all(h.root(), n => n.name === 'instruction')[0].value, '');
  fill(h); click(h.root(), 'Load private plan revision'); await tick();
  assert.deepEqual(h.calls[1], ['rooms/room%20%2F1/work-plan', {method:'GET'}]);
  click(h.root(), 'Apply reviewed node to private plan'); await tick();
  assert.deepEqual(h.calls[2][1].body, {expected_proposal_revision:2, expected_plan_revision:3, node:{id:'new_node', title:'Reviewed title', instruction:'Human instruction', assignee:'b', write_scope:'analysis', depends_on:['existing']}, acknowledge_untrusted:true});
  assert.match(h.calls[2][0], /\/1\/apply$/); assert.ok(h.calls[2][1].key);
  assert.match(h.root().textContent, /no tasks started/); assert.equal(h.calls.length, 3);
});

test('apply requires acknowledgement, confirmation, valid limits and no retry', async () => {
  const h = applySetup(async (path, opts) => path.endsWith('/work-plan') ? {revision:0,nodes:[]} : opts.method === 'GET' ? {proposals:[approved()]} : Promise.reject(Object.assign(new Error(), {status:409})));
  await h.board.load(); openEditor(h); click(h.root(), 'Load private plan revision'); await tick();
  click(h.root(), 'Apply reviewed node to private plan'); assert.equal(h.calls.length, 2);
  fill(h); let dep = all(h.root(), n => n.name === 'depends_on')[0]; dep.value = ''; dep.oninput();
  let title = all(h.root(), n => n.name === 'title')[0]; title.value = 'x'.repeat(201); title.oninput();
  click(h.root(), 'Apply reviewed node to private plan'); assert.equal(h.calls.length, 2);
  title.value = 'valid'; title.oninput(); confirmResult = false;
  click(h.root(), 'Apply reviewed node to private plan'); assert.equal(h.calls.length, 2);
  confirmResult = true; click(h.root(), 'Apply reviewed node to private plan'); await tick();
  assert.equal(h.calls.length, 3); assert.match(h.root().textContent, /uncertain/);
  assert.doesNotMatch(h.root().textContent, /Human instruction/);
});

test('plan and apply responses are fenced across permission and room changes', async () => {
  for (const phase of ['plan', 'apply']) {
    const d = deferred();
    const h = applySetup((path, opts) => path.endsWith('/work-plan') ? (phase === 'plan' ? d.promise : Promise.resolve({revision:1,nodes:[{id:'existing'}]})) : opts.method === 'GET' ? Promise.resolve({proposals:[approved()]}) : d.promise);
    await h.board.load(); openEditor(h); fill(h); click(h.root(), 'Load private plan revision');
    if (phase === 'apply') { await tick(); click(h.root(), 'Apply reviewed node to private plan'); }
    h.ctx.room.effective_permissions = ['room:read']; h.board.render();
    d.resolve(phase === 'plan' ? {revision:1,nodes:[{id:'existing'}]} : {id:'1',revision:3,state:'applied',plan_revision:2,execution_started:false}); await tick();
    assert.doesNotMatch(h.container.textContent, /secret|Human instruction|Applied to private plan/);
  }
});

test('plan and apply errors fail closed, and full or malformed plans cannot apply', async () => {
  for (const status of [401,403,404,409,422,500]) {
    for (const phase of ['plan','apply']) {
      const h = applySetup((path, opts) => path.endsWith('/work-plan') ? (phase === 'plan' ? Promise.reject(Object.assign(new Error(), {status})) : Promise.resolve({revision:1,nodes:[{id:'existing'}]})) : opts.method === 'GET' ? Promise.resolve({proposals:[approved()]}) : Promise.reject(Object.assign(new Error(), {status})));
      await h.board.load(); openEditor(h); fill(h); click(h.root(), 'Load private plan revision'); await tick();
      if (phase === 'apply') { click(h.root(), 'Apply reviewed node to private plan'); await tick(); }
      assert.doesNotMatch(h.container.textContent, /secret|Human instruction/);
      assert.equal(h.root().hidden, status === 401);
      assert.equal(h.calls.length, phase === 'plan' ? 2 : 3);
    }
  }
  for (const plan of [{revision:true,nodes:[]}, {revision:1,nodes:Array.from({length:17}, (_,i) => ({id:'n'+i}))}, {revision:1,nodes:Array.from({length:16}, (_,i) => ({id:'n'+i}))}]) {
    const h = applySetup(async path => path.endsWith('/work-plan') ? plan : {proposals:[approved()]});
    await h.board.load(); openEditor(h); fill(h); click(h.root(), 'Load private plan revision'); await tick();
    if (plan.nodes.length === 16) click(h.root(), 'Apply reviewed node to private plan');
    assert.equal(h.calls.length, 2);
  }
  const h = setup(async () => ({proposals:Array.from({length:33}, () => proposal())}));
  await h.board.load(); assert.match(h.root().textContent, /Invalid response/);
});

test('only owner human with active membership may load', async () => {
  const h = setup();
  for (const change of [() => {h.ctx.isOwner = false;}, () => {h.ctx.isOwner = true; h.ctx.user.kind = 'agent';}, () => {h.ctx.user.kind = 'human'; h.ctx.room.members.owner.status = 'inactive';}]) {
    change(); h.board.render(); await h.board.load(); assert.equal(h.root().hidden, true); assert.equal(h.calls.length, 0);
  }
});

test('401, 403 and 404 purge already displayed data', async () => {
  for (const status of [401,403,404]) {
    let fail = false;
    const h = setup(async () => { if (fail) throw Object.assign(new Error('denied'), {status}); return {proposals:[proposal()]}; });
    await h.board.load(); assert.match(h.container.textContent, /secret/);
    fail = true; await h.board.load(); assert.doesNotMatch(h.container.textContent, /secret/);
    assert.equal(h.root().hidden, status === 401);
    if (status === 401) { const n = h.calls.length; await h.board.load(); assert.equal(h.calls.length, n); }
  }
});

test('late responses never restore data across context changes, clear or remount', async () => {
  for (const change of [h => {h.ctx.room.members.owner.epoch++; h.board.render();}, h => {h.ctx.authTicket++;}, h => h.board.clear(), h => h.board.mount(h.container), h => h.board.destroy()]) {
    const d = deferred(), h = setup(() => d.promise);
    const pending = h.board.load(); change(h); d.resolve({proposals:[proposal()]}); await pending;
    assert.doesNotMatch(h.container.textContent, /secret/);
  }
});

test('stale failure cannot purge a newer mount or response', async () => {
  const d = deferred(); let count = 0;
  const h = setup(() => ++count === 1 ? d.promise : Promise.resolve({proposals:[proposal('new')]}));
  const first = h.board.load(); h.board.mount(h.container); await h.board.load();
  d.reject(Object.assign(new Error('late'), {status:401})); await first;
  assert.match(h.container.textContent, /new/); assert.equal(h.root().hidden, false);
});

test('malformed responses are not displayed; permission loss purges', async () => {
  let result = {proposals:[proposal()]}; const h = setup(async () => result);
  await h.board.load(); h.ctx.isOwner = false; h.board.render(); assert.doesNotMatch(h.container.textContent, /secret/);
  h.ctx.isOwner = true; result = {proposals:[{summary:'bad'}]}; await h.board.load();
  assert.match(h.container.textContent, /Invalid response/); assert.doesNotMatch(h.container.textContent, /bad/);
});
