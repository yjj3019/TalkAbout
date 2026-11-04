// Synthetic DOM + injected API only. No server, database, provider or model call.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createWorkPlanView, WORK_PLAN_LABELS} from '../talkabout/web/work-plan.mjs';

const tick = () => new Promise(setImmediate);
const defer = () => {
  let resolve;
  const promise = new Promise((settle) => { resolve = settle; });
  return {promise, resolve};
};

class FakeNode {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this._text = '';
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.hidden = false;
    this.type = '';
    this.id = '';
    this.className = '';
    this.rows = 0;
    this.maxLength = 0;
    this.readOnly = false;
  }
  set textContent(value) {
    this._text = value === undefined || value === null ? '' : String(value);
    this.children = [];
  }
  get textContent() {
    return this._text + this.children
      .map((child) => (typeof child === 'string' ? child : (child && child.textContent !== undefined ? child.textContent : '')))
      .join('');
  }
  append(...items) {
    this.children.push(...items);
  }
  replaceChildren(...items) {
    this._text = '';
    this.children = items;
  }
  setAttribute() {}
  addEventListener() {}
}

const walk = (node, out = []) => {
  out.push(node);
  for (const child of node.children) if (child && typeof child === 'object') walk(child, out);
  return out;
};
const findById = (container, id) => walk(container).find((node) => node.id === id);
const text = (container) => container.textContent;

function node(id, title, extra = {}) {
  return {
    id,
    title,
    instruction: 'Instruction for ' + id,
    assignee: 'bot',
    write_scope: 'analysis',
    depends_on: [],
    ...extra,
  };
}

function roomA(extra = {}) {
  return {
    id: 'room-a',
    state: 'preparing',
    membership_revision: 3,
    effective_permissions: ['room:read', 'room:control'],
    members: {
      human: {kind: 'human', status: 'active', epoch: 0, permissions: ['room:read', 'room:control']},
      human2: {kind: 'human', status: 'active', epoch: 0, permissions: ['room:read', 'room:control']},
      bot: {kind: 'agent', status: 'active', epoch: 1, permissions: ['room:read']},
      bot2: {kind: 'agent', status: 'active', epoch: 0, permissions: ['room:read']},
      other: {kind: 'agent', status: 'pending', epoch: 0, permissions: ['room:read']},
    },
    ...extra,
  };
}

function harness(options = {}) {
  const calls = [];
  const handlers = new Map();
  const notices = [];
  const copied = [];
  const confirms = [];
  let keySeq = 0;
  let confirmAnswer = options.confirmAnswer ?? true;
  const document = {createElement: (tag) => new FakeNode(tag)};
  const context = {
    room: options.room || roomA(),
    user: options.user || {id: 'human', kind: 'human', scopes: ['room:read', 'room:control']},
    authTicket: 1,
    viewTicket: 1,
    language: 'ko',
  };
  const api = async (path, request = {}) => {
    const method = request.method || 'GET';
    calls.push({
      path,
      method,
      key: request.key,
      body: request.body === undefined ? null : JSON.parse(JSON.stringify(request.body)),
    });
    const handler = handlers.get(method + ' ' + path);
    if (!handler) {
      const error = new Error('no synthetic handler');
      error.status = 599;
      throw error;
    }
    return handler(request);
  };
  const view = createWorkPlanView({
    api,
    getContext: () => ({
      room: context.room,
      user: context.user,
      authTicket: context.authTicket,
      viewTicket: context.viewTicket,
      language: context.language,
    }),
    document,
    notify: (kind, key) => notices.push({kind, key}),
    confirm: (message) => { confirms.push(message); return confirmAnswer; },
    clipboard: async (value) => { copied.push(value); },
    uuid: () => 'key-' + (++keySeq),
  });
  const container = new FakeNode('div');
  const respond = (method, path, value) => handlers.set(method + ' ' + path, typeof value === 'function' ? value : async () => value);
  const fail = (method, path, status, code) => handlers.set(method + ' ' + path, async () => {
    const error = new Error('synthetic failure');
    error.status = status;
    if (code !== undefined) error.code = code;
    throw error;
  });
  const root = () => container.children[0];
  return {
    api, calls, handlers, notices, copied, confirms, container, view, context,
    respond, fail, root, document,
    setRoom: (next) => { context.room = next; },
    setUser: (next) => { context.user = next; },
    setConfirm: (value) => { confirmAnswer = value; },
  };
}

const planPath = (rid = 'room-a') => 'rooms/' + rid + '/work-plan';
const getCount = (h) => h.calls.filter((call) => call.method === 'GET').length;
const postCount = (h) => h.calls.filter((call) => call.method === 'POST').length;

async function mountAndLoad(h, container = h.container) {
  h.view.mount(container);
  const load = findById(container, 'wp-load');
  assert.ok(load, 'an explicit load button is rendered after mount');
  load.onclick();
  await tick();
}

const dispatchPath = (rid = 'room-a') => planPath(rid) + '/dispatch';
const abortPath = (rid = 'room-a') => dispatchPath(rid) + '/abort';
const dispatchRun = (extra = {}) => ({
  id: 'run-1',
  state: 'running',
  plan_revision: 2,
  generation: 7,
  started_at: 1700000000,
  finished_at: null,
  abort_reason: null,
  nodes: [{id: 'one', assignee: 'bot', write_scope: 'analysis', state: 'queued', task_id: 'task-1', failure: null}],
  write_scope_enforced: false,
  automatic_provider_call: false,
  plan_text_visible_to_assignees: true,
  evidence: 'worker_reported_unverified',
  ...extra,
});
const ackAll = (h) => {
  for (const name of ['queue', 'disclosure', 'unenforced']) {
    const input = findById(h.root(), 'wp-dispatch-ack-' + name);
    assert.ok(input, 'missing dispatch acknowledgement ' + name);
    input.checked = true;
    input.onchange();
  }
};
const dispatchCalls = (h) => h.calls.filter((call) => call.path.includes('/work-plan/dispatch'));

test('mount renders an unloaded panel and never fetches automatically', () => {
  const h = harness();
  h.view.mount(h.container);
  assert.equal(h.calls.length, 0, 'mount must not issue a GET');
  assert.equal(h.root().hidden, false);
  assert.ok(findById(h.root(), 'wp-load'));
  assert.equal(findById(h.root(), 'wp-edit'), undefined, 'no edit path before an explicit load');
  assert.doesNotMatch(text(h.container), /저장된 초안이 없습니다/);
});

test('hidden and no API call without room control', () => {
  const h = harness({
    user: {id: 'human', kind: 'human', scopes: ['room:read']},
    room: roomA({
      effective_permissions: ['room:read'],
      members: {human: {kind: 'human', status: 'active', epoch: 0}},
    }),
  });
  h.view.mount(h.container);
  assert.equal(h.root().hidden, true);
  assert.equal(h.calls.length, 0);
  assert.equal(text(h.container), '');
});

test('agent sessions never render the private draft', () => {
  const h = harness({user: {id: 'bot', kind: 'agent', scopes: ['room:read']}});
  h.respond('GET', planPath(), {revision: 1, nodes: [node('one', 'Secret plan')]});
  h.view.mount(h.container);
  assert.equal(h.root().hidden, true);
  assert.equal(h.calls.length, 0);
});

test('load renders the private draft', async () => {
  const h = harness();
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  await mountAndLoad(h);
  assert.equal(h.root().hidden, false);
  assert.match(text(h.container), /Draft one/);
  assert.match(text(h.container), /리비전: 2/);
  assert.equal(getCount(h), 1);
});

test('404 shows only the generic unavailable message and no draft text', async () => {
  const h = harness();
  h.fail('GET', planPath(), 404);
  await mountAndLoad(h);
  assert.match(text(h.container), /이 초안을 열람하거나 수정할 수 없습니다/);
  assert.doesNotMatch(text(h.container), /Draft/);
  assert.ok(findById(h.root(), 'wp-retry'));
});

test('403 shows only the generic forbidden message', async () => {
  const h = harness();
  h.fail('GET', planPath(), 403);
  await mountAndLoad(h);
  assert.match(text(h.container), /이 초안에 접근할 수 없습니다/);
  assert.doesNotMatch(text(h.container), /Draft/);
});

test('save sends the exact body, one fresh idempotency key and no retry', async () => {
  const h = harness();
  const draft = [node('one', 'Draft one')];
  h.respond('GET', planPath(), {revision: 1, nodes: draft});
  h.respond('POST', planPath(), {revision: 2});
  await mountAndLoad(h);
  findById(h.root(), 'wp-edit').onclick();
  findById(h.root(), 'wp-save').onclick();
  await tick();
  const posts = h.calls.filter((call) => call.method === 'POST');
  assert.equal(posts.length, 1);
  assert.deepEqual(posts[0].body, {expected_revision: 1, nodes: draft});
  assert.equal(posts[0].key, 'key-1');
  assert.match(text(h.container), /저장했습니다/);
});

test('required predecessor selection round-trips and validates a removed dependency', async () => {
  const h = harness();
  const draft = [node('one', 'First'), node('two', 'Second', {depends_on: ['one'], required_predecessors: ['one']})];
  h.respond('GET', planPath(), {revision: 1, nodes: draft});
  h.respond('POST', planPath(), {revision: 2});
  await mountAndLoad(h);
  findById(h.root(), 'wp-edit').onclick();
  assert.equal(findById(h.root(), 'wp-required-1-one').checked, true);
  h.view.setLanguage('en');
  assert.equal(findById(h.root(), 'wp-required-1-one').checked, true);
  assert.ok(text(h.container).includes(WORK_PLAN_LABELS.en.workPlanRequiredHelp));
  const dep = findById(h.root(), 'wp-dep-1-one');
  dep.checked = false;
  dep.onchange();
  assert.equal(findById(h.root(), 'wp-required-1-one').checked, true);
  findById(h.root(), 'wp-save').onclick();
  await tick();
  assert.equal(postCount(h), 0);
  assert.ok(text(h.container).includes(WORK_PLAN_LABELS.en.workPlanInvalidRequired));
  const stale = findById(h.root(), 'wp-required-1-one');
  stale.checked = false;
  stale.onchange();
  findById(h.root(), 'wp-save').onclick();
  await tick();
  assert.deepEqual(h.calls.find((call) => call.method === 'POST').body.nodes[1].required_predecessors, []);
  assert.equal(Object.hasOwn(h.calls.find((call) => call.method === 'POST').body.nodes[0], 'required_predecessors'), false);
});

test('duplicate required predecessors block saving and malformed field is rejected on load', async () => {
  for (const required of [['one', 'one'], null]) {
    const h = harness();
    h.respond('GET', planPath(), {revision: 1, nodes: [node('one', 'First'), node('two', 'Second', {depends_on: ['one'], required_predecessors: required})]});
    await mountAndLoad(h);
    if (required === null) {
      assert.equal(findById(h.root(), 'wp-edit'), undefined);
      assert.match(text(h.container), /불러오지 못했습니다/);
    } else {
      findById(h.root(), 'wp-edit').onclick();
      findById(h.root(), 'wp-save').onclick();
      await tick();
      assert.equal(postCount(h), 0);
      assert.ok(text(h.container).includes(WORK_PLAN_LABELS.ko.workPlanInvalidRequired));
    }
  }
});

test('required predecessor selection creates the optional field only when chosen', async () => {
  const h = harness();
  h.respond('GET', planPath(), {revision: 1, nodes: [node('one', 'First'), node('two', 'Second', {depends_on: ['one']})]});
  h.respond('POST', planPath(), {revision: 2});
  await mountAndLoad(h);
  findById(h.root(), 'wp-edit').onclick();
  const required = findById(h.root(), 'wp-required-1-one');
  assert.equal(required.checked, false);
  required.checked = true;
  required.onchange();
  findById(h.root(), 'wp-save').onclick();
  await tick();
  assert.deepEqual(h.calls.find((call) => call.method === 'POST').body.nodes[1].required_predecessors, ['one']);
  findById(h.root(), 'wp-edit').onclick();
  assert.equal(findById(h.root(), 'wp-required-1-one').checked, true);
});

test('local validation blocks invalid drafts without a POST', async () => {
  const cases = [
    {mutate: (h) => { const id = findById(h.root(), 'wp-id-1'); id.value = 'one'; id.oninput(); }, message: /ID는 영문으로 시작/},
    {mutate: (h) => { const title = findById(h.root(), 'wp-title-0'); title.value = 'x'.repeat(201); title.oninput(); }, message: /제목은 1–200자/},
    {mutate: (h) => { const title = findById(h.root(), 'wp-title-0'); title.value = 'bad\u0000'; title.oninput(); }, message: /제어 문자/},
    {mutate: (h) => {
      const first = findById(h.root(), 'wp-dep-0-two');
      first.checked = true;
      first.onchange();
      const second = findById(h.root(), 'wp-dep-1-one');
      second.checked = true;
      second.onchange();
    }, message: /선행 관계에 순환이 있습니다/},
  ];
  for (const item of cases) {
    const h = harness();
    h.respond('GET', planPath(), {revision: 1, nodes: [node('one', 'Draft one'), node('two', 'Draft two')]});
    await mountAndLoad(h);
    findById(h.root(), 'wp-edit').onclick();
    item.mutate(h);
    findById(h.root(), 'wp-save').onclick();
    await tick();
    assert.equal(postCount(h), 0);
    assert.match(text(h.container), item.message);
  }
});

test('an inactive stored assignee is preserved, warned about and blocks saving', async () => {
  const h = harness();
  const draft = [node('one', 'Draft one', {assignee: 'bot'})];
  h.respond('GET', planPath(), {revision: 1, nodes: draft});
  h.respond('POST', planPath(), {revision: 2});
  await mountAndLoad(h);
  const room = h.context.room;
  room.members.bot.status = 'pending';
  room.members.bot2.status = 'active';
  findById(h.root(), 'wp-edit').onclick();
  const select = findById(h.root(), 'wp-assignee-0');
  assert.equal(select.value, 'bot', 'the inactive stored assignee is not silently replaced');
  assert.match(text(h.container), /담당자 에이전트가 현재 비활성/);
  findById(h.root(), 'wp-save').onclick();
  await tick();
  assert.equal(postCount(h), 0, 'saving must stay blocked until a human chooses an active agent');
  assert.match(text(h.container), /담당자는 이 방의 활성 에이전트/);
  select.value = 'bot2';
  select.onchange();
  findById(h.root(), 'wp-save').onclick();
  await tick();
  assert.equal(postCount(h), 1);
  assert.equal(h.calls.find((call) => call.method === 'POST').body.nodes[0].assignee, 'bot2');
});

test('409 refetches once, never retries the mutation and keeps the local edit', async () => {
  const h = harness();
  let reads = 0;
  h.respond('GET', planPath(), async () => {
    reads += 1;
    return reads === 1 ? {revision: 1, nodes: [node('one', 'Draft one')]} : {revision: 2, nodes: [node('one', 'Changed elsewhere')]};
  });
  h.fail('POST', planPath(), 409);
  await mountAndLoad(h);
  findById(h.root(), 'wp-edit').onclick();
  findById(h.root(), 'wp-save').onclick();
  await tick();
  assert.equal(postCount(h), 1);
  assert.equal(getCount(h), 2);
  assert.match(text(h.container), /다른 곳에서 초안이 수정되었습니다/);
  assert.ok(findById(h.root(), 'wp-title-0'), 'local editor is preserved for an explicit reload');
});

test('a conflict explanation that returns 403 drops the unsaved editor', async () => {
  const h = harness();
  let reads = 0;
  h.respond('GET', planPath(), async () => {
    reads += 1;
    if (reads === 1) return {revision: 1, nodes: [node('one', 'Secret draft one')]};
    const error = new Error('forbidden');
    error.status = 403;
    throw error;
  });
  h.fail('POST', planPath(), 409);
  await mountAndLoad(h);
  findById(h.root(), 'wp-edit').onclick();
  findById(h.root(), 'wp-save').onclick();
  await tick();
  assert.equal(findById(h.root(), 'wp-title-0'), undefined, 'editor must not survive a 403');
  assert.doesNotMatch(text(h.container), /Secret draft one/);
  assert.match(text(h.container), /이 초안에 접근할 수 없습니다/);
});

test('a conflict explanation that returns 404 drops the unsaved editor', async () => {
  const h = harness();
  let reads = 0;
  h.respond('GET', planPath(), async () => {
    reads += 1;
    if (reads === 1) return {revision: 1, nodes: [node('one', 'Secret draft one')]};
    const error = new Error('not found');
    error.status = 404;
    throw error;
  });
  h.fail('POST', planPath(), 409);
  await mountAndLoad(h);
  findById(h.root(), 'wp-edit').onclick();
  findById(h.root(), 'wp-save').onclick();
  await tick();
  assert.equal(findById(h.root(), 'wp-title-0'), undefined);
  assert.doesNotMatch(text(h.container), /Secret draft one/);
  assert.match(text(h.container), /이 초안을 열람하거나 수정할 수 없습니다/);
});

test('a conflict explanation that returns 401 hides the panel and cannot be re-exposed', async () => {
  const h = harness();
  let reads = 0;
  h.respond('GET', planPath(), async () => {
    reads += 1;
    if (reads === 1) return {revision: 1, nodes: [node('one', 'Secret draft one')]};
    const error = new Error('unauthorized');
    error.status = 401;
    throw error;
  });
  h.fail('POST', planPath(), 409);
  await mountAndLoad(h);
  findById(h.root(), 'wp-edit').onclick();
  findById(h.root(), 'wp-save').onclick();
  await tick();
  assert.equal(findById(h.root(), 'wp-title-0'), undefined);
  assert.equal(h.root().hidden, true);
  assert.equal(text(h.container), '');
  h.view.render();
  assert.equal(h.root().hidden, true, 'a later render must not re-expose the panel');
  assert.equal(text(h.container), '');
  const before = getCount(h);
  h.view.load();
  await tick();
  assert.equal(getCount(h), before, 'no request after session loss');
});

test('double click sends exactly one save', async () => {
  const h = harness();
  const pending = defer();
  h.respond('GET', planPath(), {revision: 1, nodes: [node('one', 'Draft one')]});
  h.handlers.set('POST ' + planPath(), () => pending.promise);
  await mountAndLoad(h);
  findById(h.root(), 'wp-edit').onclick();
  findById(h.root(), 'wp-save').onclick();
  findById(h.root(), 'wp-save').onclick();
  pending.resolve({revision: 2});
  await tick();
  assert.equal(postCount(h), 1);
});

test('a late response for a previous mount never touches the mounted successor', async () => {
  const h = harness();
  const slow = defer();
  h.respond('GET', planPath('room-a'), () => slow.promise);
  h.view.mount(h.container);
  findById(h.root(), 'wp-load').onclick();
  assert.equal(h.root().hidden, false);
  h.view.clear();
  const next = new FakeNode('div');
  h.setRoom(roomA({
    id: 'room-b',
    membership_revision: 9,
    members: {human: {kind: 'human', status: 'active', epoch: 0}, bot: {kind: 'agent', status: 'active', epoch: 0}},
  }));
  h.respond('GET', planPath('room-b'), {revision: 1, nodes: [node('b1', 'Room B plan')]});
  await mountAndLoad(h, next);
  assert.match(text(next), /Room B plan/);
  slow.resolve({revision: 1, nodes: [node('a1', 'Room A secret plan')]});
  await tick();
  assert.match(text(next), /Room B plan/);
  assert.doesNotMatch(text(next), /Room A secret plan/);
  assert.equal(next.children[0].hidden, false, 'the successor mount is not cleared');
});

test('mounting a second panel without clear() purges the previous draft atomically', async () => {
  const h = harness();
  h.respond('GET', planPath('room-a'), {revision: 1, nodes: [node('a1', 'Secret draft A')]});
  await mountAndLoad(h);
  const selectA = findById(h.root(), 'wp-select-a1');
  selectA.checked = true;
  selectA.onchange();
  findById(h.root(), 'wp-preview').onclick();
  assert.match(findById(h.root(), 'wp-share-text').value, /Secret draft A/);

  const next = new FakeNode('div');
  h.setRoom(roomA({
    id: 'room-b',
    membership_revision: 9,
    members: {human: {kind: 'human', status: 'active', epoch: 0}, bot: {kind: 'agent', status: 'active', epoch: 0}},
  }));
  h.fail('GET', planPath('room-b'), 404);
  h.view.mount(next);
  assert.doesNotMatch(text(next), /Secret draft A/, 'the old draft must not render into the new root');
  assert.doesNotMatch(text(h.container), /Secret draft A/, 'the previous root is purged too');
  assert.equal(h.view.isDirty(), false, 'internal state was reset by the new mount');
  findById(next.children[0], 'wp-load').onclick();
  await tick();
  assert.doesNotMatch(text(next), /Secret draft A/);
  assert.match(text(next), /이 초안을 열람하거나 수정할 수 없습니다/);
});

test('a same-mount epoch change purges the rendered draft on render()', async () => {
  const h = harness();
  h.respond('GET', planPath(), {revision: 1, nodes: [node('one', 'Sensitive draft text')]});
  await mountAndLoad(h);
  assert.match(text(h.container), /Sensitive draft text/);
  h.context.room.members.human.epoch = 1;
  h.view.render();
  assert.doesNotMatch(text(h.container), /Sensitive draft text/);
  assert.match(text(h.container), /초안 표시를 지웠습니다/);
  assert.ok(findById(h.root(), 'wp-load'), 'the stale panel offers an explicit reload');
});

test('the same epoch but a different actor purges the rendered draft', async () => {
  const h = harness();
  h.respond('GET', planPath(), {revision: 1, nodes: [node('one', 'Sensitive draft text')]});
  await mountAndLoad(h);
  assert.match(text(h.container), /Sensitive draft text/);
  h.setUser({id: 'human2', kind: 'human', scopes: ['room:read', 'room:control']});
  h.view.render();
  assert.doesNotMatch(text(h.container), /Sensitive draft text/);
  assert.match(text(h.container), /초안 표시를 지웠습니다/);
});

test('a membership revision change invalidates an in-flight response and purges', async () => {
  const h = harness();
  const slow = defer();
  h.respond('GET', planPath(), () => slow.promise);
  h.view.mount(h.container);
  findById(h.root(), 'wp-load').onclick();
  h.context.room.membership_revision = 4;
  slow.resolve({revision: 1, nodes: [node('one', 'Stale plan text')]});
  await tick();
  assert.doesNotMatch(text(h.container), /Stale plan text/);
  assert.match(text(h.container), /초안 표시를 지웠습니다/);
});

test('an auth ticket change invalidates an in-flight response and purges', async () => {
  const h = harness();
  const slow = defer();
  h.respond('GET', planPath(), () => slow.promise);
  h.view.mount(h.container);
  findById(h.root(), 'wp-load').onclick();
  h.context.authTicket = 2;
  slow.resolve({revision: 1, nodes: [node('one', 'Stale plan text')]});
  await tick();
  assert.doesNotMatch(text(h.container), /Stale plan text/);
});

test('a plan view ticket change purges the rendered draft', async () => {
  const h = harness();
  h.respond('GET', planPath(), {revision: 1, nodes: [node('one', 'Sensitive draft text')]});
  await mountAndLoad(h);
  h.context.viewTicket = 2;
  h.view.render();
  assert.doesNotMatch(text(h.container), /Sensitive draft text/);
});

test('losing room control purges the rendered draft text', async () => {
  const h = harness();
  h.respond('GET', planPath(), {revision: 1, nodes: [node('one', 'Sensitive draft text')]});
  await mountAndLoad(h);
  assert.match(text(h.container), /Sensitive draft text/);
  h.context.room.effective_permissions = ['room:read'];
  h.view.render();
  assert.equal(h.root().hidden, true);
  assert.doesNotMatch(text(h.container), /Sensitive draft text/);
});

test('selection starts empty and preview/copy cover only selected nodes', async () => {
  const h = harness();
  h.respond('GET', planPath(), {
    revision: 1,
    nodes: [node('a', 'Draft A'), node('b', 'Draft B')],
  });
  await mountAndLoad(h);
  assert.equal(findById(h.root(), 'wp-preview').disabled, true);
  assert.equal(findById(h.root(), 'wp-copy').disabled, true);

  const selectA = findById(h.root(), 'wp-select-a');
  selectA.checked = true;
  selectA.onchange();
  findById(h.root(), 'wp-preview').onclick();
  const textarea = findById(h.root(), 'wp-share-text');
  assert.match(textarea.value, /Draft A/);
  assert.doesNotMatch(textarea.value, /Draft B/);
  assert.doesNotMatch(textarea.value, /Instruction for b/);
  assert.equal(h.copied.length, 0, 'nothing is copied without a click');

  findById(h.root(), 'wp-copy').onclick();
  await tick();
  assert.equal(h.copied.length, 1);
  assert.equal(h.copied[0], textarea.value);

  const selectAAgain = findById(h.root(), 'wp-select-a');
  selectAAgain.checked = false;
  selectAAgain.onchange();
  assert.equal(findById(h.root(), 'wp-preview').disabled, true);
  assert.equal(findById(h.root(), 'wp-share-text').value, '', 'deselection wipes the previous preview');
  findById(h.root(), 'wp-copy').onclick();
  await tick();
  assert.equal(h.copied.length, 1, 'a stale preview can never be copied');
});

test('language switch keeps unsaved textarea input and never refetches', async () => {
  const h = harness();
  h.respond('GET', planPath(), {revision: 1, nodes: [node('one', 'Draft one')]});
  await mountAndLoad(h);
  findById(h.root(), 'wp-edit').onclick();
  const title = findById(h.root(), 'wp-title-0');
  title.value = 'unsaved Korean text';
  title.oninput();
  const reads = getCount(h);
  h.view.setLanguage('en');
  assert.equal(findById(h.root(), 'wp-title-0').value, 'unsaved Korean text');
  assert.equal(getCount(h), reads);
  assert.match(text(h.container), /Save/);
});

test('draft fields are rendered as text, never as markup', async () => {
  const h = harness();
  const payload = '<img src=x onerror="globalThis.__wpPwned=1">';
  h.respond('GET', planPath(), {revision: 1, nodes: [node('one', payload, {instruction: payload})]});
  delete globalThis.__wpPwned;
  await mountAndLoad(h);
  assert.match(text(h.container), /<img src=x onerror=/);
  assert.equal(globalThis.__wpPwned, undefined);
});

test('only the work-plan endpoints are called', async () => {
  const h = harness();
  h.respond('GET', planPath(), {revision: 1, nodes: [node('one', 'Draft one')]});
  h.respond('POST', planPath(), {revision: 2});
  await mountAndLoad(h);
  findById(h.root(), 'wp-edit').onclick();
  findById(h.root(), 'wp-save').onclick();
  await tick();
  assert.ok(h.calls.length >= 2);
  for (const call of h.calls) assert.equal(call.path, planPath());
});

test('a hostile room id is encoded into one path segment', async () => {
  const hostile = 'evil/../x?y=1#z';
  const h = harness({room: roomA({id: hostile})});
  const encoded = 'rooms/' + encodeURIComponent(hostile) + '/work-plan';
  h.respond('GET', encoded, {revision: 1, nodes: [node('one', 'Draft one')]});
  await mountAndLoad(h);
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].path, encoded);
  const segment = h.calls[0].path.replace(/^rooms\//, '').replace(/\/work-plan$/, '');
  assert.doesNotMatch(segment, /[/?#]/);
  assert.equal(h.calls[0].path.split('/').length, 3, 'no extra path segment is injected');
  assert.match(text(h.container), /Draft one/);
});

test('401 purges everything, hides the panel and a later render stays hidden', async () => {
  const h = harness();
  h.respond('GET', planPath(), {revision: 1, nodes: [node('one', 'Draft one')]});
  await mountAndLoad(h);
  assert.match(text(h.container), /Draft one/);
  h.fail('GET', planPath(), 401);
  h.view.load();
  await tick();
  assert.equal(h.root().hidden, true);
  assert.equal(text(h.container), '');
  h.view.render();
  assert.equal(h.root().hidden, true);
  assert.equal(text(h.container), '');
});

test('a 403 failure leaves no busy lock: an explicit reload recovers', async () => {
  const h = harness();
  h.respond('GET', planPath(), async () => ({revision: 1, nodes: [node('one', 'Draft one')]}));
  await mountAndLoad(h);
  h.fail('GET', planPath(), 403);
  h.view.reload();
  await tick();
  assert.match(text(h.container), /이 초안에 접근할 수 없습니다/);
  h.respond('GET', planPath(), {revision: 1, nodes: [node('one', 'Draft one')]});
  findById(h.root(), 'wp-retry').onclick();
  await tick();
  assert.match(text(h.container), /Draft one/);
});

test('a save disables every editing control and loses no typed input', async () => {
  const h = harness();
  const pending = defer();
  h.respond('GET', planPath(), {revision: 1, nodes: [node('one', 'Draft one')]});
  h.handlers.set('POST ' + planPath(), () => pending.promise);
  await mountAndLoad(h);
  findById(h.root(), 'wp-edit').onclick();
  const title = findById(h.root(), 'wp-title-0');
  title.value = 'final typed text';
  title.oninput();
  findById(h.root(), 'wp-save').onclick();
  assert.equal(findById(h.root(), 'wp-title-0').disabled, true);
  assert.equal(findById(h.root(), 'wp-instruction-0').disabled, true);
  assert.equal(findById(h.root(), 'wp-assignee-0').disabled, true);
  assert.equal(findById(h.root(), 'wp-add').disabled, true);
  assert.equal(findById(h.root(), 'wp-remove-0').disabled, true);
  assert.equal(findById(h.root(), 'wp-save').disabled, true);
  assert.equal(findById(h.root(), 'wp-cancel').disabled, true);
  // Typing during the request must never be collected or silently discarded.
  const during = findById(h.root(), 'wp-title-0');
  during.value = 'typing while saving';
  during.oninput();
  pending.resolve({revision: 2});
  await tick();
  assert.equal(h.calls.find((call) => call.method === 'POST').body.nodes[0].title, 'final typed text');
  assert.equal(h.view.isDirty(), false);
  assert.match(text(h.container), /final typed text/);
});

test('public load refuses to discard dirty edits and clears selection/preview', async () => {
  const h = harness();
  h.respond('GET', planPath(), {revision: 1, nodes: [node('one', 'Draft one')]});
  await mountAndLoad(h);
  findById(h.root(), 'wp-edit').onclick();
  const title = findById(h.root(), 'wp-title-0');
  title.value = 'unsaved change';
  title.oninput();
  h.setConfirm(false);
  const before = getCount(h);
  h.view.load();
  await tick();
  assert.equal(getCount(h), before, 'a refused confirmation must not fetch');
  assert.equal(findById(h.root(), 'wp-title-0').value, 'unsaved change');

  h.setConfirm(true);
  findById(h.root(), 'wp-cancel').onclick();
  const select = findById(h.root(), 'wp-select-one');
  select.checked = true;
  select.onchange();
  findById(h.root(), 'wp-preview').onclick();
  assert.notEqual(findById(h.root(), 'wp-share-text').value, '');
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  h.view.load();
  await tick();
  assert.equal(getCount(h), before + 1);
  assert.equal(findById(h.root(), 'wp-share-text').value, '', 'load drops the previous preview');
  assert.equal(findById(h.root(), 'wp-preview').disabled, true);
});

test('an unset new assignee shows a placeholder and blocks the save', async () => {
  const h = harness();
  h.respond('GET', planPath(), {revision: 1, nodes: [node('one', 'Draft one')]});
  h.respond('POST', planPath(), {revision: 2});
  await mountAndLoad(h);
  findById(h.root(), 'wp-edit').onclick();
  findById(h.root(), 'wp-add').onclick();
  const select = findById(h.root(), 'wp-assignee-1');
  assert.equal(select.value, '');
  assert.match(text(h.container), /담당자를 선택하세요/);
  const title = findById(h.root(), 'wp-title-1');
  title.value = 'Second';
  title.oninput();
  const instruction = findById(h.root(), 'wp-instruction-1');
  instruction.value = 'Do it';
  instruction.oninput();
  findById(h.root(), 'wp-save').onclick();
  await tick();
  assert.equal(postCount(h), 0, 'no POST until a human picks an assignee');
  assert.match(text(h.container), /담당자는 이 방의 활성 에이전트/);
  select.value = 'bot2';
  select.onchange();
  findById(h.root(), 'wp-save').onclick();
  await tick();
  assert.equal(postCount(h), 1);
  assert.equal(h.calls.find((call) => call.method === 'POST').body.nodes[1].assignee, 'bot2');
});

test('the local ASCII control subset never claims server parity', async () => {
  const h = harness();
  h.respond('GET', planPath(), {revision: 1, nodes: [node('one', 'Draft one')]});
  h.fail('POST', planPath(), 422);
  await mountAndLoad(h);
  findById(h.root(), 'wp-edit').onclick();
  const title = findById(h.root(), 'wp-title-0');
  title.value = 'zero\u200Bwidth';
  title.oninput();
  findById(h.root(), 'wp-save').onclick();
  await tick();
  assert.equal(postCount(h), 1, 'Cf passes the local subset; the server rejects it');
  assert.equal(h.calls.find((call) => call.method === 'POST').body.nodes[0].title, 'zero\u200Bwidth');
  assert.ok(findById(h.root(), 'wp-title-0'), 'the editor survives the server 422');
  assert.doesNotMatch(text(h.container), /제어 문자/);
  assert.match(text(h.container), /올바르지 않습니다/);
});

test('dispatch controls appear only after an explicit plan load and never fetch automatically', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  h.respond('GET', dispatchPath(), {run: null});
  h.view.mount(h.container);
  assert.equal(findById(h.root(), 'wp-dispatch-start'), undefined, 'no dispatch action before the plan is loaded');
  assert.equal(findById(h.root(), 'wp-dispatch-refresh'), undefined);
  assert.equal(h.calls.length, 0);
  const load = findById(h.root(), 'wp-load');
  load.onclick();
  await tick();
  assert.equal(dispatchCalls(h).length, 0, 'loading the plan does not fetch dispatch state');
  assert.ok(findById(h.root(), 'wp-dispatch-start'));
  assert.ok(findById(h.root(), 'wp-dispatch-refresh'));
  assert.match(text(h.container), /마지막으로 받은 응답 기준/);
  assert.equal(findById(h.root(), 'wp-dispatch-start').disabled, true, 'all three warnings are required');
  findById(h.root(), 'wp-dispatch-refresh').onclick();
  await tick();
  const gets = dispatchCalls(h).filter((call) => call.method === 'GET');
  assert.equal(gets.length, 1, 'the manual refresh is the only dispatch GET');
  assert.equal(gets[0].path, dispatchPath());
  assert.match(text(h.container), /실행 기록이 없습니다/);
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'POST').length, 0);
});

test('three acknowledgements gate the exact dispatch body and a single idempotent POST', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  h.respond('POST', dispatchPath(), {run: dispatchRun()});
  await mountAndLoad(h);
  let start = findById(h.root(), 'wp-dispatch-start');
  start.onclick();
  await tick();
  assert.equal(postCount(h), 0, 'no POST before every warning is acknowledged');
  assert.equal(h.confirms.length, 0, 'no confirm before every warning is acknowledged');
  findById(h.root(), 'wp-dispatch-ack-queue').checked = true;
  findById(h.root(), 'wp-dispatch-ack-queue').onchange();
  findById(h.root(), 'wp-dispatch-ack-disclosure').checked = true;
  findById(h.root(), 'wp-dispatch-ack-disclosure').onchange();
  start = findById(h.root(), 'wp-dispatch-start');
  assert.equal(start.disabled, true);
  start.onclick();
  await tick();
  assert.equal(postCount(h), 0);
  findById(h.root(), 'wp-dispatch-ack-unenforced').checked = true;
  findById(h.root(), 'wp-dispatch-ack-unenforced').onchange();
  start = findById(h.root(), 'wp-dispatch-start');
  assert.equal(start.disabled, false);
  start.onclick();
  await tick();
  const posts = h.calls.filter((call) => call.method === 'POST');
  assert.equal(posts.length, 1);
  assert.deepEqual(posts[0].body, {
    expected_plan_revision: 2,
    expected_generation: 7,
    acknowledge_plan_dispatch: true,
    acknowledge_plan_disclosure: true,
    acknowledge_write_scope_unenforced: true,
  });
  assert.equal(posts[0].key, 'key-1');
  assert.equal(h.confirms.length, 1);
  assert.match(h.confirms[0], /리비전: 2/);
  assert.match(h.confirms[0], /방 세대: 7/);
  assert.match(text(h.container), /실행 중/);
  assert.ok(findById(h.root(), 'wp-dispatch-abort'), 'a running run offers an explicit stop');
  assert.match(text(h.container), /서버는 provider를 호출하지 않습니다/);
  assert.match(text(h.container), /task는 워커가 claim해야 실행됩니다/);
  assert.match(text(h.container), /write_scope는 서버가 강제하지 않습니다/);
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'GET').length, 0, 'starting never auto-polls');
});

test('a duplicate dispatch click sends exactly one POST and disables all controls while busy', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  const gate = defer();
  h.respond('POST', dispatchPath(), () => gate.promise);
  await mountAndLoad(h);
  ackAll(h);
  findById(h.root(), 'wp-dispatch-start').onclick();
  await tick();
  assert.equal(findById(h.root(), 'wp-dispatch-start').disabled, true);
  assert.equal(findById(h.root(), 'wp-dispatch-ack-queue').disabled, true);
  findById(h.root(), 'wp-dispatch-start').onclick();
  await tick();
  assert.equal(h.calls.filter((call) => call.method === 'POST').length, 1, 'busy state blocks a duplicate POST');
  gate.resolve({run: dispatchRun()});
  await tick();
  await tick();
  assert.match(text(h.container), /실행 중/);
});

test('dispatch 409 reports not started, keeps the draft, and never retries automatically', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  h.fail('POST', dispatchPath(), 409);
  await mountAndLoad(h);
  ackAll(h);
  findById(h.root(), 'wp-dispatch-start').onclick();
  await tick();
  await tick();
  assert.equal(h.calls.filter((call) => call.method === 'POST').length, 1);
  assert.match(text(h.container), /시작되지 않았습니다/);
  assert.match(text(h.container), /Draft one/, 'a conflict does not purge the saved draft');
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'GET').length, 0, 'a conflict never auto-refreshes');
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'POST').length, 1);
});

test('abort requires confirmation, sends only the exact abort body and keeps the previous result', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  h.respond('GET', dispatchPath(), {run: dispatchRun()});
  h.respond('POST', abortPath(), {run: dispatchRun({state: 'aborted', finished_at: 1700000100})});
  await mountAndLoad(h);
  ackAll(h);
  findById(h.root(), 'wp-dispatch-refresh').onclick();
  await tick();
  const before = dispatchCalls(h).length;
  h.setConfirm(false);
  findById(h.root(), 'wp-dispatch-abort').onclick();
  await tick();
  assert.equal(h.calls.filter((call) => call.method === 'POST').length, 0, 'declining the abort confirmation sends nothing');
  h.setConfirm(true);
  findById(h.root(), 'wp-dispatch-abort').onclick();
  await tick();
  const posts = h.calls.filter((call) => call.method === 'POST');
  assert.equal(posts.length, 1);
  assert.deepEqual(posts[0].body, {run_id: 'run-1', acknowledge_abort: true});
  assert.equal(posts[0].key, 'key-1', 'the abort mutation also uses an idempotency key');
  assert.match(h.confirms.at(-1), /실행 중인 계획을 중지/);
  assert.match(text(h.container), /중지됨/);
  assert.match(text(h.container), /task-1/, 'previous result stays until an explicit refresh');
  assert.equal(findById(h.root(), 'wp-dispatch-start').disabled, true,
    'an abort response is only a snapshot; refresh is required before another start');
  assert.match(text(h.container), /추가 시작·중지 전에/);
  assert.equal(dispatchCalls(h).length, before + 1, 'no automatic GET after the abort response');
  h.respond('GET', dispatchPath(), {run: dispatchRun({state: 'aborted', finished_at: 1700000100})});
  findById(h.root(), 'wp-dispatch-refresh').onclick();
  await tick();
  assert.equal(dispatchCalls(h).length, before + 2, 'only the explicit refresh fetches');
  assert.match(text(h.container), /run-1 · 중지됨/);
  assert.equal(findById(h.root(), 'wp-dispatch-start').disabled, true,
    'a terminal same-revision run also needs the re-run acknowledgement');
  const repeat = findById(h.root(), 'wp-dispatch-repeat-ack');
  assert.ok(repeat, 'same-revision re-run offers an extra confirmation');
  repeat.checked = true;
  repeat.onchange();
  assert.equal(findById(h.root(), 'wp-dispatch-start').disabled, true);
  h.view.render();
  assert.match(text(h.container), /task-1/);
});

test('abort 409 reports not stopped and never retries automatically', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  h.respond('GET', dispatchPath(), {run: dispatchRun()});
  h.fail('POST', abortPath(), 409);
  await mountAndLoad(h);
  ackAll(h);
  findById(h.root(), 'wp-dispatch-refresh').onclick();
  await tick();
  findById(h.root(), 'wp-dispatch-abort').onclick();
  await tick();
  await tick();
  assert.equal(h.calls.filter((call) => call.method === 'POST').length, 1);
  assert.match(text(h.container), /중지되지 않았습니다/);
  assert.match(text(h.container), /실행 중/);
  assert.ok(findById(h.root(), 'wp-dispatch-abort'), 'the still-running run can be retried explicitly');
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'GET').length, 1);
});

for (const [status, pattern] of [[403, /접근할 수 없습니다/], [404, /열람하거나 수정할 수 없습니다/]]) {
  test('dispatch status ' + status + ' purges the plan content immediately', async () => {
    const h = harness({room: roomA({generation: 7})});
    h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
    h.fail('GET', dispatchPath(), status);
    await mountAndLoad(h);
    findById(h.root(), 'wp-dispatch-refresh').onclick();
    await tick();
    await tick();
    assert.match(text(h.container), pattern);
    assert.doesNotMatch(text(h.container), /Draft one/);
    assert.equal(dispatchCalls(h).filter((call) => call.method === 'GET').length, 1, 'no automatic retry');
  });
}

test('dispatch 401 purges everything and cannot be re-exposed by render', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  h.fail('GET', dispatchPath(), 401);
  await mountAndLoad(h);
  findById(h.root(), 'wp-dispatch-refresh').onclick();
  await tick();
  assert.equal(h.root().hidden, true);
  assert.equal(text(h.container), '');
  h.view.render();
  assert.equal(h.root().hidden, true);
  assert.equal(text(h.container), '');
});

test('a dirty draft blocks dispatch before any confirmation or POST', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  await mountAndLoad(h);
  findById(h.root(), 'wp-edit').onclick();
  const title = findById(h.root(), 'wp-title-0');
  title.value = 'Unsaved dispatch';
  title.oninput();
  ackAll(h);
  const start = findById(h.root(), 'wp-dispatch-start');
  assert.equal(start.disabled, true);
  start.onclick();
  await tick();
  assert.equal(h.calls.filter((call) => call.method === 'POST').length, 0);
  assert.equal(h.confirms.length, 0);
  assert.match(text(h.container), /저장되지 않은 초안/);
});

test('dispatch snapshot changes purge the run state without an automatic refetch', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  h.respond('GET', dispatchPath(), {run: dispatchRun()});
  await mountAndLoad(h);
  findById(h.root(), 'wp-dispatch-refresh').onclick();
  await tick();
  assert.match(text(h.container), /task-1/);
  const gets = dispatchCalls(h).filter((call) => call.method === 'GET').length;
  h.setRoom({...roomA({generation: 7}), membership_revision: 4});
  h.view.render();
  assert.doesNotMatch(text(h.container), /task-1/);
  assert.match(text(h.container), /방 상태·멤버십 또는 세션이 바뀌어/);
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'GET').length, gets, 'purge does not refetch dispatch');
});

test('a late dispatch response after a snapshot change is discarded', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  const gate = defer();
  h.respond('GET', dispatchPath(), () => gate.promise);
  await mountAndLoad(h);
  findById(h.root(), 'wp-dispatch-refresh').onclick();
  await tick();
  h.setRoom({...roomA({generation: 7, id: 'room-b'})});
  gate.resolve({run: dispatchRun()});
  await tick();
  await tick();
  assert.doesNotMatch(text(h.container), /task-1/);
  assert.match(text(h.container), /방 상태·멤버십 또는 세션이 바뀌어/);
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'GET').length, 1, 'no retry after the late discard');
});

test('dispatch node id and failure strings stay inert text', async () => {
  const hostile = '<img src=x onerror=alert(1)>';
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  h.respond('GET', dispatchPath(), {run: dispatchRun({
    state: 'partial',
    nodes: [{id: hostile, assignee: hostile, write_scope: 'analysis', state: 'failed', task_id: hostile, failure: hostile}],
  })});
  await mountAndLoad(h);
  findById(h.root(), 'wp-dispatch-refresh').onclick();
  await tick();
  assert.ok(text(h.container).includes(hostile), 'hostile node data is shown as text');
  assert.equal(walk(h.root()).some((item) => item.tag === 'img'), false);
});

test('dispatch language switch relabels the panel without fetching', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  h.respond('GET', dispatchPath(), {run: dispatchRun({nodes: [
    {id: 'one', assignee: 'bot', write_scope: 'analysis', state: 'queued', task_id: 'task-1', failure: null},
  ]})});
  await mountAndLoad(h);
  findById(h.root(), 'wp-dispatch-refresh').onclick();
  await tick();
  const before = dispatchCalls(h).length;
  h.view.setLanguage('en');
  assert.match(text(h.container), /Plan execution/);
  assert.match(text(h.container), /The server does not call a provider/);
  assert.match(text(h.container), /Per-node execution status/);
  assert.equal(dispatchCalls(h).length, before, 'language selection never refetches');
});

test('dispatch only uses the three contracted endpoints', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  h.respond('GET', dispatchPath(), {run: dispatchRun()});
  h.respond('POST', dispatchPath(), {run: dispatchRun()});
  h.respond('POST', abortPath(), {run: dispatchRun({state: 'aborted'})});
  await mountAndLoad(h);
  findById(h.root(), 'wp-dispatch-refresh').onclick();
  await tick();
  ackAll(h);
  findById(h.root(), 'wp-dispatch-start').onclick();
  await tick();
  findById(h.root(), 'wp-dispatch-abort').onclick();
  await tick();
  const allowed = new Set([planPath(), dispatchPath(), abortPath()]);
  for (const call of h.calls) assert.ok(allowed.has(call.path), call.path);
  assert.equal(h.calls.some((call) => /messages|tasks|artifacts/.test(call.path)), false);
});

const dispatchErrorCases = [
  [409, 'worker_connection_required', 'workPlanDispatchWorkerConnectionRequired'],
  [409, 'room_busy', 'workPlanDispatchRoomBusy'],
  [409, 'plan_dispatch_active', 'workPlanDispatchAlreadyActive'],
  [409, 'dispatch_held', 'workPlanDispatchHeld'],
  [409, 'work_plan_revision_conflict', 'workPlanDispatchRevisionConflict'],
  [409, 'stale_context', 'workPlanDispatchStaleContext'],
  [422, 'plan_assignee_required', 'workPlanDispatchAssigneeRequired'],
  [429, 'task_capacity', 'workPlanDispatchCapacity'],
];

test('dispatch start failures map preserved error.code values to distinct labels', async () => {
  for (const [status, code, labelKey] of dispatchErrorCases) {
    const h = harness({room: roomA({generation: 7})});
    h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
    h.fail('POST', dispatchPath(), status, code);
    await mountAndLoad(h);
    ackAll(h);
    findById(h.root(), 'wp-dispatch-start').onclick();
    await tick();
    await tick();
    assert.ok(text(h.container).includes(WORK_PLAN_LABELS.ko[labelKey]), code + ' maps to its label');
    assert.equal(h.calls.filter((call) => call.method === 'POST').length, 1, code + ' sends one POST');
    assert.equal(dispatchCalls(h).filter((call) => call.method === 'GET').length, 0, code + ' never auto-refreshes');
    await tick();
    assert.equal(h.calls.filter((call) => call.method === 'POST').length, 1, code + ' never retries automatically');
  }
});

test('an unknown dispatch error code falls back to the general failure message', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  h.fail('POST', dispatchPath(), 422, 'unlisted_dispatch_error');
  await mountAndLoad(h);
  ackAll(h);
  findById(h.root(), 'wp-dispatch-start').onclick();
  await tick();
  await tick();
  assert.ok(text(h.container).includes(WORK_PLAN_LABELS.ko.workPlanDispatchFailed));
  assert.equal(h.calls.filter((call) => call.method === 'POST').length, 1);
  await tick();
  assert.equal(h.calls.filter((call) => call.method === 'POST').length, 1);
});

test('a 404 plan_missing code explains the failure while still purging draft text', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  h.fail('POST', dispatchPath(), 404, 'plan_missing');
  await mountAndLoad(h);
  ackAll(h);
  findById(h.root(), 'wp-dispatch-start').onclick();
  await tick();
  await tick();
  assert.ok(text(h.container).includes(WORK_PLAN_LABELS.ko.workPlanDispatchMissing));
  assert.doesNotMatch(text(h.container), /Draft one/);
  assert.equal(h.calls.filter((call) => call.method === 'POST').length, 1);
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'GET').length, 0);
});

test('dispatch refresh and abort failures also use the preserved error.code label', async () => {
  const refresh = harness({room: roomA({generation: 7})});
  refresh.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  refresh.fail('GET', dispatchPath(), 409, 'room_busy');
  await mountAndLoad(refresh);
  findById(refresh.root(), 'wp-dispatch-refresh').onclick();
  await tick();
  await tick();
  assert.ok(text(refresh.container).includes(WORK_PLAN_LABELS.ko.workPlanDispatchRoomBusy));
  assert.equal(dispatchCalls(refresh).filter((call) => call.method === 'GET').length, 1);
  assert.equal(dispatchCalls(refresh).filter((call) => call.method === 'POST').length, 0);

  const abort = harness({room: roomA({generation: 7})});
  abort.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  abort.respond('GET', dispatchPath(), {run: dispatchRun()});
  abort.fail('POST', abortPath(), 409, 'work_plan_revision_conflict');
  await mountAndLoad(abort);
  findById(abort.root(), 'wp-dispatch-refresh').onclick();
  await tick();
  findById(abort.root(), 'wp-dispatch-abort').onclick();
  await tick();
  await tick();
  assert.ok(text(abort.container).includes(WORK_PLAN_LABELS.ko.workPlanDispatchRevisionConflict));
  assert.equal(dispatchCalls(abort).filter((call) => call.method === 'POST').length, 1);
  assert.equal(dispatchCalls(abort).filter((call) => call.method === 'GET').length, 1);
  await tick();
  assert.equal(dispatchCalls(abort).filter((call) => call.method === 'POST').length, 1, 'abort code failure is not retried');
});

test('start success reports the queued task count and warns when it is zero', async () => {
  for (const [queued, pattern] of [[0, /큐에 넣은 task가 0개/], [2, /큐에 넣은 task: 2/]]) {
    const h = harness({room: roomA({generation: 7})});
    h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
    h.respond('POST', dispatchPath(), {run: dispatchRun(), tasks_queued: queued});
    await mountAndLoad(h);
    ackAll(h);
    findById(h.root(), 'wp-dispatch-start').onclick();
    await tick();
    await tick();
    assert.match(text(h.container), pattern);
    assert.equal(h.calls.filter((call) => call.method === 'POST').length, 1);
    assert.equal(dispatchCalls(h).filter((call) => call.method === 'GET').length, 0);
  }
});

test('a lost start response keeps its idempotency key and locks new work until an explicit refresh', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  h.fail('POST', dispatchPath(), undefined);
  await mountAndLoad(h);
  ackAll(h);
  findById(h.root(), 'wp-dispatch-start').onclick();
  await tick();
  await tick();
  assert.ok(text(h.container).includes(WORK_PLAN_LABELS.ko.workPlanDispatchUnknown));
  assert.equal(findById(h.root(), 'wp-dispatch-start').disabled, true);
  assert.equal(findById(h.root(), 'wp-dispatch-refresh').disabled, false);
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'POST').length, 1);
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'GET').length, 0, 'a lost response never auto-polls');
  findById(h.root(), 'wp-dispatch-start').onclick();
  await tick();
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'POST').length, 1, 'unknown state blocks a new execution');

  h.respond('GET', dispatchPath(), {run: null});
  findById(h.root(), 'wp-dispatch-refresh').onclick();
  await tick();
  await tick();
  assert.doesNotMatch(text(h.container), /결과 불명/);
  assert.equal(findById(h.root(), 'wp-dispatch-start').disabled, false);

  h.respond('POST', dispatchPath(), {run: dispatchRun()});
  findById(h.root(), 'wp-dispatch-start').onclick();
  await tick();
  await tick();
  const posts = dispatchCalls(h).filter((call) => call.method === 'POST');
  assert.equal(posts.length, 2);
  assert.equal(posts[0].key, posts[1].key, 'the replay after an explicit refresh uses the same key');
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'GET').length, 1);
  assert.match(text(h.container), /마지막으로 받은 응답 기준/);
  assert.equal(findById(h.root(), 'wp-dispatch-start').disabled, true);
  assert.equal(findById(h.root(), 'wp-dispatch-abort').disabled, true);
});

test('old terminal refresh before delayed commit retains the exact start intent through replay conflicts', async () => {
  for (const code of ['plan_dispatch_active', 'idempotency_key_retired']) {
    const h = harness({room: roomA({generation: 7})});
    const commit = defer();
    let committed = false;
    let attempts = 0;
    h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
    const oldRun = dispatchRun({id: 'old-run', state: 'completed'});
    h.respond('GET', dispatchPath(), () => ({run: committed ? dispatchRun({id: 'new-run', state: 'completed'}) : oldRun}));
    h.respond('POST', dispatchPath(), async () => {
      attempts++;
      if (attempts === 1) {
        commit.promise.then(() => { committed = true; });
        throw new Error('connection lost before server commit');
      }
      if (attempts === 2) {
        const error = new Error(code);
        error.status = 409;
        error.code = code;
        throw error;
      }
      return {run: dispatchRun({id: 'new-run', state: 'completed'})};
    });
    await mountAndLoad(h);
    ackAll(h);
    findById(h.root(), 'wp-dispatch-start').onclick();
    await tick();
    findById(h.root(), 'wp-dispatch-refresh').onclick();
    await tick();
    assert.match(text(h.container), /old-run/);
    let repeat = findById(h.root(), 'wp-dispatch-repeat-ack');
    repeat.checked = true;
    repeat.onchange();
    // The original generation must remain in both the confirmation and replay.
    h.context.room.generation = 8;
    findById(h.root(), 'wp-dispatch-start').onclick();
    await tick();
    assert.ok(text(h.container).includes(WORK_PLAN_LABELS.ko.workPlanDispatchUnknown));
    assert.equal(findById(h.root(), 'wp-dispatch-start').disabled, true);
    assert.equal(findById(h.root(), 'wp-reload').disabled, true);
    h.view.load();
    commit.resolve();
    await tick();
    findById(h.root(), 'wp-dispatch-refresh').onclick();
    await tick();
    repeat = findById(h.root(), 'wp-dispatch-repeat-ack');
    repeat.checked = true;
    repeat.onchange();
    findById(h.root(), 'wp-dispatch-start').onclick();
    await tick();
    const posts = dispatchCalls(h).filter((call) => call.method === 'POST');
    assert.equal(posts.length, 3);
    for (const post of posts) {
      assert.equal(post.key, posts[0].key, code);
      assert.deepEqual(post.body, posts[0].body, code);
    }
    assert.match(h.confirms.at(-1), /방 세대: 7/);
    assert.equal(h.calls.filter((call) => call.path === planPath() && call.method === 'GET').length, 1);
  }
});

test('a malformed 2xx start without a run is unknown and replays the same key', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  h.respond('POST', dispatchPath(), {tasks_queued: 0});
  await mountAndLoad(h);
  ackAll(h);
  findById(h.root(), 'wp-dispatch-start').onclick();
  await tick();
  await tick();
  assert.ok(text(h.container).includes(WORK_PLAN_LABELS.ko.workPlanDispatchUnknown));
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'GET').length, 0);
  h.respond('GET', dispatchPath(), {run: null});
  findById(h.root(), 'wp-dispatch-refresh').onclick();
  await tick();
  h.respond('POST', dispatchPath(), {run: dispatchRun()});
  findById(h.root(), 'wp-dispatch-start').onclick();
  await tick();
  await tick();
  const posts = dispatchCalls(h).filter((call) => call.method === 'POST');
  assert.equal(posts.length, 2);
  assert.equal(posts[0].key, posts[1].key);
});

test('a definitive 4xx discards the start intent so the next start uses a new key', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  h.fail('POST', dispatchPath(), 409, 'plan_dispatch_active');
  await mountAndLoad(h);
  ackAll(h);
  findById(h.root(), 'wp-dispatch-start').onclick();
  await tick();
  await tick();
  assert.ok(text(h.container).includes(WORK_PLAN_LABELS.ko.workPlanDispatchAlreadyActive));
  assert.equal(findById(h.root(), 'wp-dispatch-start').disabled, false);
  h.respond('POST', dispatchPath(), {run: dispatchRun()});
  findById(h.root(), 'wp-dispatch-start').onclick();
  await tick();
  await tick();
  const posts = dispatchCalls(h).filter((call) => call.method === 'POST');
  assert.equal(posts.length, 2);
  assert.notEqual(posts[0].key, posts[1].key, 'a definitive failure discards the key');
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'GET').length, 0);
});

test('a lost abort response keeps its key, locks both actions and replays after refresh', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  h.respond('GET', dispatchPath(), {run: dispatchRun()});
  h.fail('POST', abortPath(), undefined);
  await mountAndLoad(h);
  ackAll(h);
  findById(h.root(), 'wp-dispatch-refresh').onclick();
  await tick();
  findById(h.root(), 'wp-dispatch-abort').onclick();
  await tick();
  await tick();
  assert.ok(text(h.container).includes(WORK_PLAN_LABELS.ko.workPlanDispatchUnknown));
  assert.equal(findById(h.root(), 'wp-dispatch-abort').disabled, true);
  assert.equal(findById(h.root(), 'wp-dispatch-start').disabled, true);
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'POST').length, 1);
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'GET').length, 1);
  findById(h.root(), 'wp-dispatch-abort').onclick();
  await tick();
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'POST').length, 1);
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'GET').length, 1, 'no automatic refresh');

  h.respond('GET', dispatchPath(), {run: dispatchRun()});
  findById(h.root(), 'wp-dispatch-refresh').onclick();
  await tick();
  await tick();
  assert.doesNotMatch(text(h.container), /결과 불명/);
  assert.equal(findById(h.root(), 'wp-dispatch-abort').disabled, false);

  h.respond('POST', abortPath(), {run: dispatchRun({state: 'aborted', finished_at: 1700000100})});
  findById(h.root(), 'wp-dispatch-abort').onclick();
  await tick();
  await tick();
  const posts = dispatchCalls(h).filter((call) => call.method === 'POST');
  assert.equal(posts.length, 2);
  assert.equal(posts[0].key, posts[1].key, 'the abort replay reuses the same key');
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'GET').length, 2);
  assert.equal(findById(h.root(), 'wp-dispatch-start').disabled, true,
    'an abort response still requires an explicit refresh before another start');
  assert.match(text(h.container), /추가 시작·중지 전에/);
});

test('abort 409 plan_dispatch_not_running reports already stopped and discards the key', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  h.respond('GET', dispatchPath(), {run: dispatchRun()});
  h.fail('POST', abortPath(), 409, 'plan_dispatch_not_running');
  await mountAndLoad(h);
  ackAll(h);
  findById(h.root(), 'wp-dispatch-refresh').onclick();
  await tick();
  findById(h.root(), 'wp-dispatch-abort').onclick();
  await tick();
  await tick();
  assert.ok(text(h.container).includes(WORK_PLAN_LABELS.ko.workPlanDispatchAlreadyStopped));
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'GET').length, 1);
  h.respond('POST', abortPath(), {run: dispatchRun({state: 'aborted', finished_at: 1700000100})});
  findById(h.root(), 'wp-dispatch-abort').onclick();
  await tick();
  const posts = dispatchCalls(h).filter((call) => call.method === 'POST');
  assert.equal(posts.length, 2);
  assert.notEqual(posts[0].key, posts[1].key, 'a definitive abort error discards its key');
});

test('a terminal same-revision run cannot be freshly started', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  h.respond('GET', dispatchPath(), {
    run: dispatchRun({state: 'completed', finished_at: 1700000100}),
  });
  h.respond('POST', dispatchPath(), {run: dispatchRun()});
  await mountAndLoad(h);
  ackAll(h);
  findById(h.root(), 'wp-dispatch-refresh').onclick();
  await tick();
  assert.match(text(h.container), /run-1 · 완료/);
  assert.ok(text(h.container).includes(WORK_PLAN_LABELS.ko.workPlanDispatchRepeatWarning));
  assert.ok(text(h.container).includes(WORK_PLAN_LABELS.ko.workPlanDispatchRepeatAck));
  assert.equal(findById(h.root(), 'wp-dispatch-start').disabled, true, 'the extra re-run acknowledgement is required');
  findById(h.root(), 'wp-dispatch-start').onclick();
  await tick();
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'POST').length, 0);
  assert.equal(h.confirms.length, 0, 'no confirmation before the extra acknowledgement');
  const repeat = findById(h.root(), 'wp-dispatch-repeat-ack');
  assert.ok(repeat);
  assert.equal(repeat.disabled, true);
  repeat.checked = true;
  repeat.onchange();
  assert.equal(findById(h.root(), 'wp-dispatch-start').disabled, true);
  findById(h.root(), 'wp-dispatch-start').onclick();
  await tick();
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'POST').length, 0);
});

test('a start response is a snapshot; another action waits for an explicit refresh that replaces it', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  h.respond('POST', dispatchPath(), {run: dispatchRun()});
  await mountAndLoad(h);
  ackAll(h);
  findById(h.root(), 'wp-dispatch-start').onclick();
  await tick();
  await tick();
  assert.match(text(h.container), /마지막으로 받은 응답 기준/);
  assert.match(text(h.container), /추가 시작·중지 전에/);
  assert.equal(findById(h.root(), 'wp-dispatch-start').disabled, true);
  assert.equal(findById(h.root(), 'wp-dispatch-abort').disabled, true);
  const before = dispatchCalls(h).length;
  findById(h.root(), 'wp-dispatch-start').onclick();
  await tick();
  findById(h.root(), 'wp-dispatch-abort').onclick();
  await tick();
  assert.equal(dispatchCalls(h).length, before, 'no extra POST or GET before the explicit refresh');
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'GET').length, 0);

  h.respond('GET', dispatchPath(), {
    run: dispatchRun({state: 'completed', finished_at: 1700000100}),
  });
  findById(h.root(), 'wp-dispatch-refresh').onclick();
  await tick();
  await tick();
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'GET').length, 1);
  assert.match(text(h.container), /완료/);
  assert.equal(findById(h.root(), 'wp-dispatch-abort'), undefined);
  assert.equal(findById(h.root(), 'wp-dispatch-start').disabled, true);
});

test('the disclosure agreement and start confirmation warn about re-quoting in agent output', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  h.respond('POST', dispatchPath(), {run: dispatchRun()});
  await mountAndLoad(h);
  const disclosure = WORK_PLAN_LABELS.ko.workPlanDispatchAckDisclosure;
  assert.match(disclosure, /공개 결과·메시지로 다시 인용/);
  assert.match(disclosure, /방 구성원에게 노출·기록/);
  assert.ok(text(h.container).includes(disclosure), 'the expanded agreement is rendered as text');
  findById(h.root(), 'wp-dispatch-ack-queue').checked = true;
  findById(h.root(), 'wp-dispatch-ack-queue').onchange();
  findById(h.root(), 'wp-dispatch-ack-unenforced').checked = true;
  findById(h.root(), 'wp-dispatch-ack-unenforced').onchange();
  assert.equal(findById(h.root(), 'wp-dispatch-start').disabled, true);
  findById(h.root(), 'wp-dispatch-start').onclick();
  await tick();
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'POST').length, 0);
  assert.equal(h.confirms.length, 0);
  findById(h.root(), 'wp-dispatch-ack-disclosure').checked = true;
  findById(h.root(), 'wp-dispatch-ack-disclosure').onchange();
  assert.equal(findById(h.root(), 'wp-dispatch-start').disabled, false);
  findById(h.root(), 'wp-dispatch-start').onclick();
  await tick();
  await tick();
  assert.ok(h.confirms.at(-1).includes(WORK_PLAN_LABELS.ko.workPlanDispatchConfirmDisclosure));
  assert.ok(text(h.container).includes(WORK_PLAN_LABELS.ko.workPlanDispatchPlanDisclosure));
});

test('partial run explains failed, blocked and uncertain nodes in both languages without extra requests', async () => {
  const h = harness({room: roomA({generation: 7})});
  h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
  const entry = (id, state, failure, task_id = null) => ({id, state, failure, task_id, assignee: 'bot', write_scope: 'analysis'});
  h.respond('GET', dispatchPath(), {run: dispatchRun({state: 'partial', nodes: [
    entry('one', 'failed', 'task_uncertain', 'task-1'), entry('two', 'blocked', 'dependency_failed'),
    entry('three', 'failed', null), entry('four', 'blocked', null), entry('five', 'completed', null),
  ]})});
  await mountAndLoad(h);
  findById(h.root(), 'wp-dispatch-refresh').onclick();
  await tick();
  const calls = h.calls.length;
  for (const lang of ['ko', 'en']) {
    h.view.setLanguage(lang);
    const labels = WORK_PLAN_LABELS[lang];
    for (const key of ['workPlanDispatchPartialHelp', 'workPlanDispatchUncertainTaskHelp',
      'workPlanDispatchDependencyHelp', 'workPlanDispatchFailedHelp', 'workPlanDispatchBlockedHelp']) {
      assert.ok(text(h.container).includes(labels[key]), lang + ': ' + key);
    }
    assert.match(text(h.container), /task_uncertain/);
    assert.match(text(h.container), /dependency_failed/);
  }
  assert.equal(h.calls.length, calls);
  assert.equal(dispatchCalls(h).filter((call) => call.method === 'POST').length, 0);
});

test('uncertain start and stop explain original-key replay after explicit refresh', async () => {
  for (const action of ['start', 'abort']) {
    const h = harness({room: roomA({generation: 7})});
    h.respond('GET', planPath(), {revision: 2, nodes: [node('one', 'Draft one')]});
    h.respond('GET', dispatchPath(), {run: action === 'abort' ? dispatchRun() : null});
    h.fail('POST', action === 'start' ? dispatchPath() : abortPath(), undefined);
    await mountAndLoad(h);
    ackAll(h);
    if (action === 'abort') { findById(h.root(), 'wp-dispatch-refresh').onclick(); await tick(); }
    findById(h.root(), 'wp-dispatch-' + action).onclick();
    await tick();
    assert.ok(text(h.container).includes(WORK_PLAN_LABELS.ko.workPlanDispatchUnknown));
    findById(h.root(), 'wp-dispatch-refresh').onclick();
    await tick();
    const before = h.calls.length;
    const key = action === 'start' ? 'workPlanDispatchReplayStartHelp' : 'workPlanDispatchReplayAbortHelp';
    assert.ok(text(h.container).includes(WORK_PLAN_LABELS.ko[key]));
    h.view.setLanguage('en');
    assert.ok(text(h.container).includes(WORK_PLAN_LABELS.en[key]));
    assert.equal(h.calls.length, before);
  }
});

test('all labels exist in both languages and warn about dependency IDs', () => {
  const ko = Object.keys(WORK_PLAN_LABELS.ko);
  const en = Object.keys(WORK_PLAN_LABELS.en);
  assert.deepEqual(ko, en);
  assert.ok(ko.length >= 40);
  for (const key of ko) {
    assert.equal(typeof WORK_PLAN_LABELS.ko[key], 'string', key);
    assert.equal(typeof WORK_PLAN_LABELS.en[key], 'string', key);
    assert.ok(WORK_PLAN_LABELS.ko[key].length > 0, key);
    assert.ok(WORK_PLAN_LABELS.en[key].length > 0, key);
  }
  assert.match(WORK_PLAN_LABELS.ko.workPlanShareWarning, /선행 노드 ID/);
  assert.match(WORK_PLAN_LABELS.en.workPlanShareWarning, /dependency IDs/);
  assert.match(WORK_PLAN_LABELS.ko.workPlanScope, /작성한 사람/);
  assert.match(WORK_PLAN_LABELS.ko.workPlanScope, /epoch/);
  assert.match(WORK_PLAN_LABELS.en.workPlanScope, /who wrote it/);
  assert.match(WORK_PLAN_LABELS.en.workPlanScope, /membership epoch/);
});
