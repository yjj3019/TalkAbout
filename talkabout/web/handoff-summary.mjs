/**
 * Read-only, explicitly loaded owner handoff snapshot. No automatic requests.
 * Host: createHandoffSummaryView({api, getContext, document}); mount a dedicated
 * container, call load only from a user gesture, render on context changes, and
 * clear immediately on room switch/logout. getContext must return fresh {room,
 * user, isOwner, authTicket, viewTicket, language}; tickets must change on auth
 * and room lifecycle changes, including leaving/re-entering the same room.
 * Server authorization remains authoritative. API paths are relative to /api/v1.
 * The injected API must itself bound response bytes before JSON parsing; this
 * module bounds the projected values and never renders arbitrary server strings.
 */
export const HANDOFF_SUMMARY_LABELS = {
  en: {
    title: 'Current run handoff summary', scope: 'Read-only request-time snapshot; not live. Load again to refresh. Unknown does not mean no effects occurred.',
    load: 'Load handoff summary', reload: 'Refresh handoff summary', unloaded: 'Not loaded.', loading: 'Loading…', stale: 'Context changed; load again.',
    denied: 'Handoff summary unavailable.', error: 'Could not load handoff summary.', invalid: 'Invalid handoff summary.', unknown: 'Unknown',
    noRun: 'No current run.', counts: 'Node counts', pending: 'Pending', queued: 'Queued', completed: 'Completed', failed: 'Failed', blocked: 'Blocked',
    uncertain: 'Uncertain tasks', acknowledged: 'Acknowledged uncertain tasks', associations: 'Unknown associations',
    warning: 'Only the current run is summarized; older runs and external effects may be missing. In-flight work can still have effects. Acknowledgement does not establish external effects. Status is not proof of quality or safety.',
  },
  ko: {
    title: '현재 실행 인계 요약', scope: '읽기 전용 요청 시점 스냅샷이며 실시간 상태가 아닙니다. 갱신하려면 다시 불러오세요. 알 수 없음은 영향이 없었다는 뜻이 아닙니다.',
    load: '인계 요약 불러오기', reload: '인계 요약 새로고침', unloaded: '아직 불러오지 않았습니다.', loading: '불러오는 중…', stale: '상태가 변경되었습니다. 다시 불러오세요.',
    denied: '인계 요약을 사용할 수 없습니다.', error: '인계 요약을 불러오지 못했습니다.', invalid: '잘못된 인계 요약입니다.', unknown: '알 수 없음',
    noRun: '현재 실행이 없습니다.', counts: '노드 수', pending: '대기', queued: '큐 등록', completed: '완료', failed: '실패', blocked: '차단',
    uncertain: '불확실한 작업', acknowledged: '확인된 불확실 작업', associations: '알 수 없는 연결',
    warning: '현재 실행만 요약하며 이전 실행과 외부 영향은 빠질 수 있습니다. 진행 중인 작업은 여전히 영향을 일으킬 수 있습니다. 확인은 외부 영향을 입증하지 않습니다. 상태는 품질이나 안전의 증거가 아닙니다.',
  },
};

export function createHandoffSummaryView({api, getContext, document: doc = globalThis.document} = {}) {
  if (typeof api !== 'function' || typeof getContext !== 'function' || !doc) throw new TypeError('api, getContext and document required');
  let container = null, root = null, serial = 0, bound = null, data = null, status = 'unloaded', blocked = false, language = null;
  function snapshot() {
    const c = getContext(), r = c?.room, u = c?.user, m = r?.members?.[u?.id];
    if (c?.isOwner !== true || u?.kind !== 'human' || typeof u.id !== 'string' || !u.id ||
        typeof r?.id !== 'string' || !r.id || c.authTicket == null || c.viewTicket == null ||
        !m || m.status !== 'active' || m.epoch == null || r.membership_revision == null ||
        !Array.isArray(r.effective_permissions) || !r.effective_permissions.includes('room:read') ||
        !r.effective_permissions.includes('room:control')) return null;
    return [r.id, u.id, c.authTicket, c.viewTicket, m.epoch, r.membership_revision, r.effective_permissions.includes('room:control')];
  }
  const same = (a, b) => !!a && !!b && a.every((v, i) => v === b[i]);
  function purge(next = 'unloaded') { ++serial; bound = null; data = null; status = next; }
  const node = (tag, text) => { const el = doc.createElement(tag); if (text !== undefined) el.textContent = text; return el; };
  function labels() { return HANDOFF_SUMMARY_LABELS[(language || getContext()?.language) === 'ko' ? 'ko' : 'en']; }
  function render() {
    if (!root) return;
    const current = snapshot();
    if (bound && !same(bound, current)) purge('stale');
    root.replaceChildren(); root.hidden = blocked || !current;
    if (root.hidden) return;
    const t = labels();
    root.append(node('h3', t.title), node('p', t.scope));
    if (t[status]) root.append(node('p', t[status]));
    if (data) renderData(data, t);
    const button = node('button', data ? t.reload : t.load);
    button.type = 'button'; button.id = 'handoff-summary-load'; button.disabled = status === 'loading';
    button.onclick = () => { void load(); }; root.append(button);
  }
  // Response projection is deliberately explicit: do not display plan, task,
  // message, artifact, provider, error, credential or other arbitrary content.
  function project(response) {
    if (!response || typeof response !== 'object' || Array.isArray(response) || !Object.hasOwn(response, 'run')) return null;
    if (response.run === null) return {run: null};
    const run = response.run, counts = run?.node_counts;
    if (!run || typeof run !== 'object' || Array.isArray(run) || !counts || typeof counts !== 'object' || Array.isArray(counts)) return null;
    // At most 16 nodes; association uncertainty can exceed the node count by a
    // few structural inconsistencies. Reject rather than truncate malformed data.
    const fields = ['pending', 'queued', 'completed', 'failed', 'blocked', 'unknown'];
    const number = (v, max) => Number.isSafeInteger(v) && v >= 0 && v <= max;
    if (fields.some(k => !number(counts[k], 16)) || fields.reduce((sum, k) => sum + counts[k], 0) > 16 ||
        !number(run.uncertain_tasks, 16) || !number(run.acknowledged_uncertain_tasks, run.uncertain_tasks) ||
        !number(run.unknown_associations, 19)) return null;
    return {run: {counts: Object.fromEntries(fields.map(k => [k, counts[k]])),
      uncertain: run.uncertain_tasks, acknowledged: run.acknowledged_uncertain_tasks, associations: run.unknown_associations}};
  }
  function renderData(value, t) {
    if (!value.run) { root.append(node('p', t.noRun)); return; }
    root.append(node('p', t.warning), node('h4', t.counts));
    const list = node('ul');
    for (const key of ['pending', 'queued', 'completed', 'failed', 'blocked', 'unknown']) list.append(node('li', t[key] + ': ' + value.run.counts[key]));
    for (const key of ['uncertain', 'acknowledged', 'associations']) list.append(node('li', t[key] + ': ' + value.run[key]));
    root.append(list);
  }
  async function load() {
    if (!root || blocked || status === 'loading') return;
    const snap = snapshot();
    if (!snap) { purge(); render(); return; }
    purge('loading'); bound = snap; const token = serial; render();
    try {
      const response = await api('rooms/' + encodeURIComponent(snap[0]) + '/work-plan/dispatch/handoff', {method: 'GET'});
      if (token !== serial) return;
      if (!same(snap, snapshot())) { purge('stale'); render(); return; }
      data = project(response); status = data ? 'loaded' : 'invalid'; render();
    } catch (error) {
      if (token !== serial) return;
      if (!same(snap, snapshot())) { purge('stale'); render(); return; }
      purge(error?.status === 403 || error?.status === 404 ? 'denied' : 'error');
      if (error?.status === 401) blocked = true;
      render();
    }
  }
  function clear() { purge(); render(); }
  function mount(element) {
    if (!element || typeof element.replaceChildren !== 'function') throw new TypeError('dedicated container required');
    if (container) container.replaceChildren();
    purge(); blocked = false; container = element; root = node('section'); root.className = 'handoff-summary';
    container.replaceChildren(root); render();
  }
  function destroy() { purge(); if (container) container.replaceChildren(); container = root = null; blocked = false; }
  function setLanguage(value) { language = value === 'ko' ? 'ko' : 'en'; render(); }
  return {mount, load, render, clear, destroy, setLanguage};
}
