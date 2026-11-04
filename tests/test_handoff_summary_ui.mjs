import test from 'node:test';
import assert from 'node:assert/strict';
import {createHandoffSummaryView} from '../talkabout/web/handoff-summary.mjs';

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this._text = ''; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
}
const doc = {createElement: tag => new Element(tag)};
const defer = () => { let resolve; const promise = new Promise(a => {resolve = a;}); return {promise, resolve}; };
const fixture = () => ({run: {node_counts: {pending:1, queued:2, completed:3, failed:0, blocked:0, unknown:1}, uncertain_tasks:2, acknowledged_uncertain_tasks:1, unknown_associations:1}});
function harness(handler = async () => fixture()) {
  const calls = [];
  const context = {room: {id: 'r/a', membership_revision: 1, effective_permissions: ['room:read', 'room:control'], members: {h: {status:'active', epoch: 1}}},
    user: {id:'h', kind:'human'}, isOwner: true, authTicket: 1, viewTicket: 1, language:'en'};
  const root = new Element('div');
  const view = createHandoffSummaryView({document: doc, getContext: () => context, api: async (...args) => { calls.push(args); return handler(...args); }});
  view.mount(root); return {view, root, context, calls};
}
test('explicit GET only, owner gating, and language changes do not fetch', async () => {
  const h = harness();
  assert.equal(h.calls.length, 0); h.view.render(); h.view.setLanguage('ko');
  assert.match(h.root.textContent, /현재 실행/); assert.equal(h.calls.length, 0);
  await h.view.load(); assert.deepEqual(h.calls[0], ['rooms/r%2Fa/work-plan/dispatch/handoff', {method:'GET'}]);
  assert.match(h.root.textContent, /알 수 없음: 1/); assert.match(h.root.textContent, /불확실한 작업: 2/);
  h.context.isOwner = false; h.view.render(); assert.equal(h.root.children[0].hidden, true);
  await h.view.load(); assert.equal(h.calls.length, 1);
});
test('late responses cannot cross auth, room, mount or membership boundaries', async () => {
  for (const mutate of [h => h.context.authTicket++, h => h.context.viewTicket++, h => h.context.room.id = 'other',
    h => h.context.room.members.h.epoch++, h => h.context.room.membership_revision++, h => h.context.isOwner = false,
    h => h.context.room.effective_permissions = ['room:read'],
    h => h.view.mount(h.root), h => h.view.clear(), h => h.view.destroy()]) {
    const d = defer(), h = harness(() => d.promise); const pending = h.view.load(); mutate(h);
    d.resolve(fixture()); await pending; assert.doesNotMatch(h.root.textContent, /Node counts/);
  }
});
test('authorization failure clears data and 401 requires remount', async () => {
  const h = harness(async () => { throw Object.assign(new Error('SECRET'), {status: 401}); });
  await h.view.load(); assert.equal(h.root.children[0].hidden, true); await h.view.load(); assert.equal(h.calls.length, 1);
  assert.doesNotMatch(h.root.textContent, /SECRET/); h.view.mount(h.root); await h.view.load(); assert.equal(h.calls.length, 2);
});
test('bounded projection ignores secret content and displays only aggregate numbers', async () => {
  const data = fixture(); data.run.message = '<img src=x onerror=alert(1)>SECRET'; data.run.node_counts.note = 'SECRET'; data.secret = 'SECRET';
  const h = harness(async () => data); await h.view.load();
  assert.match(h.root.textContent, /Unknown: 1/); assert.match(h.root.textContent, /Uncertain tasks: 2/);
  assert.match(h.root.textContent, /Unknown does not mean no effects/); assert.doesNotMatch(h.root.textContent, /SECRET|<img/);
  assert.match(h.root.textContent, /Acknowledgement does not establish external effects/);
  h.view.setLanguage('ko'); assert.match(h.root.textContent, /외부 영향을 입증하지 않습니다/); assert.equal(h.calls.length, 1);
});
test('invalid counts are rejected and null run is distinct from unknown', async () => {
  for (const change of [d => d.run.node_counts.pending = 17, d => d.run.node_counts.pending = '<script>', d => d.run.uncertain_tasks = -1,
    d => d.run.acknowledged_uncertain_tasks = 3, d => d.run.unknown_associations = 20, d => delete d.run.node_counts.unknown]) {
    const d = fixture(); change(d); const h = harness(async () => d); await h.view.load(); assert.match(h.root.textContent, /Invalid handoff summary/); assert.doesNotMatch(h.root.textContent, /<script>/);
  }
  const h = harness(async () => ({run: null})); await h.view.load(); assert.match(h.root.textContent, /No current run/); assert.doesNotMatch(h.root.textContent, /Node counts/);
});
