"""Manual memory preview is an authorized, non-persistent observation only."""
import copy

from fastapi.testclient import TestClient

from talkabout.api import create_app
from talkabout.auth import bootstrap_human
from talkabout.store import MemoryStore


def post_message(core, rid, key, text):
    return core.mutation('fixture-human', rid, 'messages', key,
                         {'kind': 'question', 'text': text, 'client_message_id': key})


def test_preview_http_is_manual_private_and_does_not_mutate(fixture):
    store, core, rid = fixture
    post_message(core, rid, 'preview-a', 'Use a table for this synthetic example.')
    post_message(core, rid, 'preview-b', 'Use a table for this synthetic example.')
    before = copy.deepcopy(store.rooms[rid])
    app = create_app(store, start_scheduler=False, allow_fixture_bearer=True)
    with TestClient(app) as client:
        path = f'/api/v1/rooms/{rid}/memories/preview'
        assert client.post(path, json={}, headers={'Authorization': 'Bearer fixture-a'}).status_code == 403
        assert client.post(path, json={}, headers={'Authorization': 'Bearer invalid'}).status_code == 401
        assert client.get(path, headers={'Authorization': 'Bearer fixture-human'}).status_code == 404
        assert client.post(path, json={'save': True}, headers={'Authorization': 'Bearer fixture-human'}).status_code == 422
        response = client.post(path, json={}, headers={'Authorization': 'Bearer fixture-human'})
        assert response.status_code == 200, response.text
        assert response.headers['cache-control'] == 'no-store'
        data = response.json()
        assert data['mode'] == 'preview_only' and data['room_id'] == rid
        matching = [item for item in data['candidates'] if item['original_text'] == 'Use a table for this synthetic example.']
        assert len(matching) == 1 and matching[0]['repetition']['count'] == 2
    assert store.rooms[rid] == before


def test_preview_respects_history_floor_and_excludes_attachment_messages(fixture):
    store, core, rid = fixture
    old = post_message(core, rid, 'preview-old', 'Old private fixture text.')
    current = post_message(core, rid, 'preview-current', 'Current fixture text.')
    attachment = post_message(core, rid, 'preview-attachment', 'Text with an attachment.')
    agent = post_message(core, rid, 'preview-agent', 'Potential attachment excerpt in agent response.')
    store.rooms[rid]['members']['human']['read_start_seq'] = old['seq'] + 1
    store.rooms[rid]['messages'][attachment['id']]['attachment_refs'] = [{'file_id': 'synthetic'}]
    store.rooms[rid]['messages'][agent['id']]['actor_kind'] = 'agent'
    result = core.memory_preview('fixture-human', rid)
    text = [candidate['original_text'] for candidate in result['candidates']]
    assert current['text'] in text
    assert old['text'] not in text
    assert attachment['text'] not in text
    assert agent['text'] not in text
    assert result['excluded_attachment_messages'] == 1
    assert result['excluded_agent_messages'] == 1


def test_preview_checks_workspace_membership_and_control_scope(fixture):
    import pytest
    from talkabout.domain import Problem
    store, core, rid = fixture
    store.rooms[rid]['members']['human']['permissions'].remove('room:control')
    with pytest.raises(Problem, match='forbidden'):
        core.memory_preview('fixture-human', rid)
    store.rooms[rid]['members']['human']['permissions'].append('room:control')
    store.rooms[rid]['members']['human']['status'] = 'removed'
    with pytest.raises(Problem, match='not_found'):
        core.memory_preview('fixture-human', rid)
    store.rooms[rid]['members']['human']['status'] = 'active'
    store.principals['human']['owner'] = 'other-workspace'
    with pytest.raises(Problem, match='not_found'):
        core.memory_preview('fixture-human', rid)
    store.principals['human']['owner'] = store.rooms[rid]['owner']
    from talkabout.domain import token_hash
    store.credentials[token_hash('fixture-human')]['room_id'] = 'another-room'
    with pytest.raises(Problem, match='not_found'):
        core.memory_preview('fixture-human', rid)


def test_postgres_preview_is_read_only(postgres_fixture):
    store, core, rid = postgres_fixture
    post_message(core, rid, 'pg-memory-preview', 'Synthetic PostgreSQL memory example.')
    with store.connect() as conn:
        before = conn.execute('SELECT last_event_seq, payload FROM rooms WHERE id=%s', (rid,)).fetchone()
        before_events = conn.execute('SELECT count(*) AS n FROM events WHERE room_id=%s', (rid,)).fetchone()['n']
    result = core.memory_preview('fixture-human', rid)
    assert any(candidate['original_text'] == 'Synthetic PostgreSQL memory example.' for candidate in result['candidates'])
    with store.connect() as conn:
        after = conn.execute('SELECT last_event_seq, payload FROM rooms WHERE id=%s', (rid,)).fetchone()
        after_events = conn.execute('SELECT count(*) AS n FROM events WHERE room_id=%s', (rid,)).fetchone()['n']
    assert before == after and before_events == after_events
    from psycopg.types.json import Jsonb
    from talkabout.domain import Problem, token_hash
    import pytest
    with store.connect() as conn:
        conn.execute('UPDATE credentials SET scopes=%s WHERE token_hash=%s',
                     (Jsonb(['room:control']), token_hash('fixture-human')))
    with pytest.raises(Problem, match='forbidden'):
        core.memory_preview('fixture-human', rid)


def test_preview_requires_human_session_origin_and_csrf():
    store = MemoryStore()
    bootstrap_human(store, 'preview-workspace', 'Preview Owner', 'owner', 'fixture-password-not-real')
    app = create_app(store, start_scheduler=False, allowed_hosts=['testserver'])
    with TestClient(app) as client:
        login = client.post('/api/v1/session', json={'workspace_id': 'preview-workspace', 'username': 'owner',
                            'password': 'fixture-password-not-real'}, headers={'Origin': 'http://testserver'})
        csrf = login.json()['csrf_token']
        headers = {'Origin': 'http://testserver', 'X-CSRF-Token': csrf, 'Idempotency-Key': 'preview-room'}
        room = client.post('/api/v1/rooms', json={'topic': 'Synthetic preview room'}, headers=headers).json()
        path = f"/api/v1/rooms/{room['id']}/memories/preview"
        assert client.post(path, json={}).status_code == 403
        assert client.post(path, json={}, headers={'Origin': 'http://testserver', 'X-CSRF-Token': 'wrong'}).status_code == 403
        assert client.post(path, json={}, headers=headers).status_code == 200
        # A control-only account is not allowed to read message contents.
        principal_id = next(iter(store.principals))
        store.principals[principal_id]['account_scopes'].remove('room:read')
        assert client.post(path, json={}, headers=headers).status_code == 403
