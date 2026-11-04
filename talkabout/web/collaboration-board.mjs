/**
 * Explicitly loaded collaboration proposals and owner decisions.
 *
 * Integration: createCollaborationBoard({api, getContext, document}) returns
 * {mount, load, render, clear, destroy}. api(path, {method:'GET'}) uses the
 * app's API helper (path relative to /api/v1; errors have numeric .status).
 * getContext() must return a fresh {room, user, isOwner, authTicket, viewTicket}.
 * isOwner is true only for the current authenticated human room owner; the
 * server remains the authorization authority. Tickets must change on session and
 * room-view lifecycle changes respectively, including leave/rejoin of the same
 * room. room.members[user.id].epoch and room.membership_revision are tracked.
 * Call render on context/permission changes, clear immediately on logout or room
 * switch, and mount only into a dedicated container. No request is automatic.
 * A 401 locks the view until mount; 403/404 clear its data. The response is a
 * request-time snapshot, not a live view. All server values are inert text.
 */
export function createCollaborationBoard({api, getContext, document: doc = globalThis.document} = {}) {
  if (typeof api !== 'function' || typeof getContext !== 'function' || !doc) {
    throw new TypeError('collaboration-board requires api, getContext and document');
  }
  let container = null, root = null, serial = 0, bound = null, proposals = null;
  let status = 'unloaded', blocked = false;
  let deciding = false, editor = null, plan = null;
  const idPattern = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;

  function snapshot() {
    const c = getContext();
    const r = c?.room, u = c?.user, member = r?.members?.[u?.id];
    if (!c || c.isOwner !== true || u?.kind !== 'human' || !u.id || !r?.id ||
        c.authTicket == null || c.viewTicket == null || !member || member.status !== 'active' ||
        member.epoch == null || r.membership_revision == null ||
        !Array.isArray(r.effective_permissions) || !r.effective_permissions.includes('room:read')) return null;
    return [r.id, u.id, c.authTicket, c.viewTicket, member.epoch, r.membership_revision, r.effective_permissions.includes('room:control')];
  }
  const same = (a, b) => !!a && !!b && a.every((v, i) => v === b[i]);
  function purge(next = 'unloaded') {
    ++serial;
    bound = null;
    proposals = null;
    deciding = false;
    editor = plan = null;
    status = next;
  }
  function node(tag, text) {
    const n = doc.createElement(tag);
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function render() {
    if (!root) return;
    const current = snapshot();
    if (bound && !same(bound, current)) purge('stale');
    root.replaceChildren();
    root.hidden = blocked || !current;
    if (root.hidden) return;
    root.append(node('h3', 'Collaboration proposals'));
    root.append(node('p', 'Untrusted agent suggestion; verify source and context. Snapshot only; refresh explicitly. Decisions never start tasks. / 신뢰할 수 없는 에이전트 제안입니다. 출처를 확인하세요. 결정은 기록만 하며 작업을 시작하지 않습니다.'));
    const messages = {unloaded: 'Not loaded.', loading: 'Loading…', stale: 'Context changed; load again.',
      forbidden: 'Access denied.', missing: 'Proposals unavailable.', error: 'Could not load proposals.',
      invalid: 'Invalid response.', deciding: 'Recording decision…', applying: 'Applying reviewed node…',
      applied: 'Applied to private plan; no tasks started. Load again to see the current snapshot.',
      rejected: 'Apply rejected as invalid; load again and review. No automatic retry.',
      decided: 'Decision recorded; load again to see the current snapshot. / 결정이 기록되었습니다.',
      uncertain: 'Decision outcome may be uncertain or snapshot changed; load again before deciding. No automatic retry. / 결과를 확인하려면 다시 불러오세요.',
      loaded: proposals?.length ? '' : 'No proposals.'};
    if (messages[status]) root.append(node('p', messages[status]));
    if (proposals) {
      const list = node('ul');
      for (const proposal of proposals) {
        const item = node('li');
        for (const field of ['id', 'author_id', 'target_id', 'kind', 'summary', 'requested_output', 'state', 'created_at']) {
          item.append(node('p', field + ': ' + (proposal[field] ?? '')));
        }
        if (proposal.done_when) {
          const criteria = node('ul');
          for (const criterion of proposal.done_when) criteria.append(node('li', criterion));
          item.append(node('p', 'Proposed completion criteria / 제안된 완료 조건 (untrusted / 검증되지 않음)'), criteria);
        }
        item.append(node('p', 'task: ' + (proposal.task_id ?? 'unknown') + ' · attempt: ' + (proposal.attempt ?? 'unknown') + ' · generation: ' + (proposal.generation ?? 'unknown')));
        item.append(node('p', 'revision: ' + proposal.revision + ' · stale: ' + proposal.stale + ' (lease completion alone does not mark stale)'));
        if (proposal.state === 'proposed' && proposal.stale === false) {
          for (const decision of ['approve', 'reject']) {
            const action = node('button', decision === 'approve' ? 'Approve / 승인' : 'Reject / 거절');
            action.type = 'button'; action.disabled = deciding;
            action.onclick = () => { void decide(proposal, decision); };
            item.append(action);
          }
        }
        if (proposal.state === 'approved' && proposal.stale === false && current[6]) {
          const action = node('button', 'Review for private plan');
          action.type = 'button'; action.disabled = deciding;
          action.onclick = () => {
            if (deciding || status !== 'loaded' || !same(bound, snapshot()) || !proposals?.includes(proposal)) return;
            editor = {proposal, values: {id:'', title:'', instruction:'', assignee:'', write_scope:'analysis', depends_on:''}, acknowledged:false};
            plan = null; render();
          };
          item.append(action);
        }
        list.append(item);
      }
      root.append(list);
    }
    if (editor && current[6]) renderEditor();
    const button = node('button', proposals ? 'Refresh proposals' : 'Load proposals');
    button.type = 'button'; button.id = 'collaboration-board-load';
    button.disabled = status === 'loading' || deciding;
    button.onclick = () => { void load(); };
    root.append(button);
  }
  function renderEditor() {
    const box = node('section');
    box.append(node('h4', 'Human-reviewed private plan node'));
    box.append(node('p', 'Proposal text is untrusted. Write and review every field yourself; applying changes the private plan only and never dispatches tasks.'));
    box.append(node('p', 'write_scope is a declaration, not an enforced file sandbox. analysis does not make a worker read-only. / 쓰기 범위는 선언일 뿐 파일 접근을 강제하지 않으며 analysis도 읽기 전용을 보장하지 않습니다.'));
    for (const field of ['id', 'title', 'instruction', 'assignee', 'write_scope', 'depends_on']) {
      const label = node('label', field + (field === 'depends_on' ? ' (comma-separated IDs)' : '') + ': ');
      const input = node(field === 'instruction' ? 'textarea' : field === 'write_scope' ? 'select' : 'input');
      input.name = field;
      if (field === 'write_scope') for (const value of ['analysis', 'owner_approved_workspace']) {
        const option = node('option', value); option.value = value; input.append(option);
      }
      input.value = editor.values[field];
      input.disabled = deciding;
      input.oninput = input.onchange = () => { if (editor) editor.values[field] = input.value; };
      label.append(input); box.append(label);
    }
    const loadPlan = node('button', 'Load private plan revision'); loadPlan.type = 'button';
    loadPlan.disabled = deciding; loadPlan.onclick = () => { void loadPlanRevision(); }; box.append(loadPlan);
    box.append(node('p', plan ? 'Loaded private plan revision: ' + plan.revision + ' (' + plan.ids.length + '/16 nodes)' : 'Load the private plan revision explicitly before applying.'));
    const label = node('label', 'I acknowledge the proposal is untrusted and I have reviewed my node. ');
    const ack = node('input'); ack.type = 'checkbox'; ack.checked = editor.acknowledged; ack.disabled = deciding;
    ack.onchange = () => { if (editor) editor.acknowledged = ack.checked; }; label.append(ack); box.append(label);
    const apply = node('button', 'Apply reviewed node to private plan'); apply.type = 'button';
    apply.disabled = deciding || !plan; apply.onclick = () => { void applyReviewed(); }; box.append(apply);
    root.append(box);
  }
  async function loadPlanRevision() {
    const snap = snapshot(), selected = editor;
    if (!root || blocked || deciding || !selected || !snap?.[6] || !same(bound, snap)) return;
    const token = serial; deciding = true; plan = null; render();
    try {
      const data = await api('rooms/' + encodeURIComponent(snap[0]) + '/work-plan', {method:'GET'});
      if (token !== serial) return;
      if (!same(snap, snapshot()) || editor !== selected) { purge('stale'); render(); return; }
      const ids = Array.isArray(data?.nodes) ? data.nodes.map(n => n?.id) : null;
      if (!Number.isSafeInteger(data?.revision) || data.revision < 0 || !ids || ids.length > 16 ||
          ids.some(id => typeof id !== 'string' || !idPattern.test(id)) || new Set(ids).size !== ids.length) {
        purge('invalid'); render(); return;
      }
      plan = {revision:data.revision, ids}; deciding = false; render();
    } catch (error) {
      if (token !== serial) return;
      if (!same(snap, snapshot())) { purge('stale'); render(); return; }
      purge(error?.status === 403 ? 'forbidden' : error?.status === 404 ? 'missing' : 'error');
      if (error?.status === 401) blocked = true;
      render();
    }
  }
  async function applyReviewed() {
    const snap = snapshot(), selected = editor, loadedPlan = plan;
    if (!root || blocked || deciding || status !== 'loaded' || !snap?.[6] || !same(bound, snap) ||
        !selected || !loadedPlan || !proposals?.includes(selected.proposal) || selected.proposal.state !== 'approved' || selected.proposal.stale !== false || !selected.acknowledged) return;
    const v = selected.values, deps = v.depends_on.trim() ? v.depends_on.split(',').map(s => s.trim()) : [];
    const printable = s => !/[\p{C}]/u.test(s.replace(/[\n\t]/g, ''));
    if (!idPattern.test(v.id) || loadedPlan.ids.includes(v.id) || loadedPlan.ids.length >= 16 ||
        !v.title.trim() || [...v.title].length > 200 || !printable(v.title) ||
        !v.instruction.trim() || [...v.instruction].length > 2000 || !printable(v.instruction) ||
        !v.assignee || !['analysis','owner_approved_workspace'].includes(v.write_scope) ||
        deps.length > 15 || new Set(deps).size !== deps.length || deps.some(d => !loadedPlan.ids.includes(d))) {
      root.append(node('p', 'Invalid node or plan is full; check fields and dependencies.')); return;
    }
    const body = {expected_proposal_revision:selected.proposal.revision, expected_plan_revision:loadedPlan.revision,
      node:{id:v.id, title:v.title, instruction:v.instruction, assignee:v.assignee, write_scope:v.write_scope, depends_on:deps}, acknowledge_untrusted:true};
    const token = serial, before = JSON.stringify(selected.values);
    const confirmApply = doc.defaultView?.confirm?.bind(doc.defaultView) ?? globalThis.confirm;
    if (typeof confirmApply !== 'function' || !confirmApply('Apply this human-reviewed node to the private plan? Proposal content is untrusted. No tasks will be dispatched.')) return;
    if (token !== serial || !same(snap, snapshot()) || deciding || editor !== selected || plan !== loadedPlan || !selected.acknowledged || JSON.stringify(selected.values) !== before) return;
    const crypto = doc.defaultView?.crypto ?? globalThis.crypto;
    if (typeof crypto?.randomUUID !== 'function') { purge('error'); render(); return; }
    const key = crypto.randomUUID(); deciding = true; status = 'applying'; render();
    try {
      const result = await api('rooms/' + encodeURIComponent(snap[0]) + '/collaboration-proposals/' + encodeURIComponent(selected.proposal.id) + '/apply', {method:'POST', body, key});
      if (token !== serial) return;
      if (!same(snap, snapshot())) { purge('stale'); render(); return; }
      const ok = result?.id === selected.proposal.id && result.revision === selected.proposal.revision + 1 && result.state === 'applied' && result.plan_revision === loadedPlan.revision + 1 && result.execution_started === false;
      purge(ok ? 'applied' : 'uncertain'); render();
    } catch (error) {
      if (token !== serial) return;
      if (!same(snap, snapshot())) { purge('stale'); render(); return; }
      purge(error?.status === 403 ? 'forbidden' : error?.status === 404 ? 'missing' : error?.status === 422 ? 'rejected' : 'uncertain');
      if (error?.status === 401) blocked = true;
      render();
    }
  }
  function valid(data) {
    const fields = ['id', 'author_id', 'target_id', 'kind', 'summary', 'requested_output', 'state', 'created_at'];
    if (!data || !Array.isArray(data.proposals) || data.proposals.length > 32) return null;
    const result = [];
    for (const p of data.proposals) {
      if (!p || typeof p !== 'object' || Array.isArray(p) ||
          fields.some(k => typeof p[k] !== 'string' && p[k] !== null && !(k === 'created_at' && typeof p[k] === 'number' && Number.isFinite(p[k]))) ||
          typeof p.id !== 'string' || !p.id || !Number.isSafeInteger(p.revision) || p.revision < 1 || typeof p.stale !== 'boolean') return null;
      if (p.task_id !== undefined && (typeof p.task_id !== 'string' || !Number.isSafeInteger(p.attempt) || !Number.isSafeInteger(p.generation))) return null;
      if (p.done_when !== undefined && (!Array.isArray(p.done_when) || p.done_when.length < 1 || p.done_when.length > 8 ||
          p.done_when.some(item => typeof item !== 'string' || !item.trim() || Array.from(item).length > 200))) return null;
      result.push({...Object.fromEntries(fields.map(k => [k, p[k]])), revision: p.revision, stale: p.stale,
                   task_id:p.task_id, attempt:p.attempt, generation:p.generation,
                   ...(p.done_when === undefined ? {} : {done_when: [...p.done_when]})});
    }
    return result;
  }
  async function decide(proposal, decision) {
    const snap = snapshot();
    if (!root || blocked || deciding || status !== 'loaded' || !same(bound, snap) ||
        !proposals?.includes(proposal) || proposal.state !== 'proposed' || proposal.stale !== false) return;
    const token = serial;
    const confirmDecision = doc.defaultView?.confirm?.bind(doc.defaultView) ?? globalThis.confirm;
    if (typeof confirmDecision !== 'function' || !confirmDecision(
      (decision === 'approve' ? 'Approve / 승인' : 'Reject / 거절') +
      '? This records the decision only and never starts tasks. / 결정만 기록하며 작업을 시작하지 않습니다.')) return;
    if (token !== serial || !same(snap, snapshot()) || deciding || !proposals?.includes(proposal)) return;
    // A cryptographic key is mandatory; do not send if it cannot be created.
    const crypto = doc.defaultView?.crypto ?? globalThis.crypto;
    if (typeof crypto?.randomUUID !== 'function') { purge('error'); render(); return; }
    const key = crypto.randomUUID();
    deciding = true;
    status = 'deciding';
    render();
    try {
      const result = await api('rooms/' + encodeURIComponent(snap[0]) + '/collaboration-proposals/' + encodeURIComponent(proposal.id) + '/decision',
        {method: 'POST', body: {expected_revision: proposal.revision, decision}, key});
      if (token !== serial) return;
      if (!same(snap, snapshot())) { purge('stale'); render(); return; }
      const confirmed = result?.id === proposal.id && result.revision === proposal.revision + 1 &&
        result.state === (decision === 'approve' ? 'approved' : 'rejected') && result.execution_started === false;
      purge(confirmed ? 'decided' : 'uncertain'); render();
    } catch (error) {
      if (token !== serial) return;
      if (!same(snap, snapshot())) { purge('stale'); render(); return; }
      const code = error?.status;
      purge(code === 403 ? 'forbidden' : code === 404 ? 'missing' : 'uncertain');
      if (code === 401) blocked = true;
      render();
    }
  }
  async function load() {
    if (!root || blocked || deciding) return;
    const snap = snapshot();
    if (!snap) { purge(); render(); return; }
    if (status === 'loading' && same(bound, snap)) return;
    purge('loading');
    bound = snap;
    const token = serial;
    render();
    try {
      const data = await api('rooms/' + encodeURIComponent(snap[0]) + '/collaboration-proposals', {method: 'GET'});
      if (token !== serial) return;
      if (!same(snap, snapshot())) { purge('stale'); render(); return; }
      proposals = valid(data);
      status = proposals ? 'loaded' : 'invalid';
      render();
    } catch (error) {
      if (token !== serial) return;
      if (!same(snap, snapshot())) { purge('stale'); render(); return; }
      const code = error?.status;
      purge(code === 403 ? 'forbidden' : code === 404 ? 'missing' : 'error');
      if (code === 401) blocked = true;
      render();
    }
  }
  function clear() { purge(); render(); }
  function mount(element) {
    if (!element || typeof element.replaceChildren !== 'function') throw new TypeError('container required');
    if (container) container.replaceChildren();
    purge(); blocked = false; container = element;
    root = node('section'); root.className = 'collaboration-board';
    container.replaceChildren(root); render();
  }
  function destroy() { purge(); if (container) container.replaceChildren(); container = root = null; blocked = false; }
  return {mount, load, render, clear, destroy};
}
