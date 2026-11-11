/* 클래스 룸(단톡방) 조율 패널 — talkabout/web 셸용 모듈.
   패널 동작은 서버의 폐쇄 카탈로그(talkabout/classroom_panel.py, PANEL_ACTIONS/
   PANEL_READS)에 바인딩하며, 미등재 동작은 만들지 않는다. 읽기 폴링은
   production/search만 사용(무변경). 실제 브라우저 수용 테스트는 NOTRUN.
   서버는 헤더(동일 출처)로 인증하며 이 모듈은 api 객체만 사용한다. */
export function createClassroomPanel({api, room, isOwner}) {
  const root = document.getElementById('classroom-panel-container');
  if (!root || !room) return null;
  const panel = document.createElement('section');
  panel.className = 'classroom-panel';
  root.replaceChildren(panel);
  const refresh = () => api.request('GET', `rooms/${room.id}/production`).then((state) => {
    panel.replaceChildren(render(state));
  }).catch(() => {
    panel.replaceChildren(info('production 불러오기 실패 (읽기 전용)'));
  });
  const submit = (action, parameters) => {
    if (!isOwner) return;
    api.request('POST', `rooms/${room.id}/controls`,
      {action, parameters, expected_revision: null}).then(refresh);
  };
  const adopt = (artifactId, revision, versionId, generation, membershipRevision) => {
    if (!isOwner) return;
    api.request('POST', `rooms/${room.id}/artifacts/${artifactId}/adoptions`,
      {generation, membership_revision: membershipRevision, expected_revision: revision, version_id: versionId}).then(refresh);
  };
  function render(state) {
    const block = document.createElement('div');
    block.append(heading(state));
    block.append(renderDecisions(state.open_human_decisions || []));
    block.append(renderArtifacts(state.artifacts || [], state));
    block.append(renderSearch(room.id));
    return block;
  }
  function heading(state) {
    const h = document.createElement('div');
    h.className = 'classroom-panel-head';
    h.textContent = `${state.mode} · ${state.mode_status || 'running'} · 산출물: ${state.deliverable || '지정 안 함'}`;
    return h;
  }
  function renderDecisions(decisions) {
    const box = document.createElement('div');
    box.textContent = '사람 결정: ' + (decisions.join(', ') || '없음');
    const row = document.createElement('div');
    for (const [label, action, parameters] of [
      ['일시중지', 'pause', {}], ['재개', 'resume', {}], ['종료', 'close', {}],
      ['문서 산출물', 'change_deliverable', {deliverable: 'analysis'}],
      ['코드 산출물', 'change_deliverable', {deliverable: 'change_proposal'}]]) {
      const button = document.createElement('button');
      button.textContent = label;
      button.disabled = panel.dataset.owner !== 'true';
      button.onclick = () => submit(action, parameters);
      row.append(button);
    }
    box.append(row);
    return box;
  }
  function renderArtifacts(artifacts, state) {
    const box = document.createElement('div');
    if (!artifacts.length) { box.textContent = '산출물 없음'; return box; }
    for (const art of artifacts) {
      const line = document.createElement('div');
      const adopted = art.adopted_version ? ` · 채택 ${art.adopted_version}` : '';
      line.textContent = `${art.artifact_id} · 버전 ${art.version_count} · ai=${art.latest_ai_generated}${adopted}`;
      const disable = !isOwner || !art.adopted_version;
      const button = document.createElement('button');
      button.textContent = '채택';
      button.disabled = disable;
      button.onclick = () => api.fetchRoom && api.request('GET', `rooms/${room.id}`).then((current) => {
        const fresh = (current.artifacts || []).find((a) => a.id === art.artifact_id);
        const version = fresh && fresh.versions && fresh.versions[fresh.versions.length - 1];
        if (fresh && version) adopt(fresh.id, fresh.revision, version.id, current.generation, current.membership_revision);
      });
      line.append(button);
      box.append(line);
    }
    return box;
  }
  function renderSearch(roomId) {
    const box = document.createElement('div');
    const input = document.createElement('input');
    input.placeholder = '대화 검색 (읽기 전용)';
    input.onchange = (event) => {
      api.request('GET', `rooms/${roomId}/search`, {q: event.target.value}).then((out) => {
        const list = document.createElement('div');
        list.textContent = (out.results || []).map((r) => `${r.seq}: ${r.text}`).join(' | ') || '일치 없음';
        box.replaceChildren(list);
      });
    };
    box.append(input);
    return box;
  }
  function info(text) {
    const d = document.createElement('div');
    d.textContent = text;
    return d;
  }
  panel.dataset.owner = isOwner ? 'true' : 'false';
  refresh();
  return panel;
}

export function maybeMountClassroomPanel(api, room, user) {
  if (!document.getElementById('classroom-panel-container')) return null;
  return createClassroomPanel({api, room, isOwner: user && user.kind === 'human' && room.can_resolve_uncertain === true});
}
