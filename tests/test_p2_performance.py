"""Security and behavior contracts for the P2 performance projections."""
import copy
import time

import pytest
from fastapi.testclient import TestClient

from conftest import leased
from talkabout.api import create_app
from talkabout.domain import token_hash


def test_fence_http_respects_task_visibility_and_missing_tasks(fixture):
    store, core, room_id = fixture
    leased(core, room_id)
    own = next(t for t in core.read('fixture-a', room_id)['tasks'] if t['state'] == 'leased')
    other = copy.deepcopy(store.rooms[room_id]['tasks'][own['id']])
    other.update(id='other-task', assignee='b')
    store.rooms[room_id]['tasks']['other-task'] = other
    app = create_app(store, start_scheduler=False, allow_fixture_bearer=True)
    with TestClient(app) as client:
        def get(token, task):
            return client.get(f'/api/v1/rooms/{room_id}/fence', params={'task_id': task},
                              headers={'Authorization': 'Bearer ' + token})
        assert get('fixture-a', own['id']).json()['task']['id'] == own['id']
        assert get('fixture-a', 'other-task').json()['task'] is None
        assert get('fixture-a', 'missing-task').json()['task'] is None
        assert get('fixture-human', 'other-task').json()['task']['assignee'] == 'b'
        assert get('invalid-token', own['id']).status_code == 401
        store.credentials[token_hash('fixture-a')]['room_id'] = 'different-room'
        assert get('fixture-a', own['id']).status_code == 404
        store.credentials[token_hash('fixture-a')].pop('room_id')
        store.rooms[room_id]['members']['a']['status'] = 'removed'
        assert get('fixture-a', own['id']).status_code == 404
        store.rooms[room_id]['members']['a']['status'] = 'active'
        store.principals['a']['owner'] = 'other-workspace'
        assert get('fixture-a', own['id']).status_code == 404


@pytest.mark.parametrize('condition', ['valid', 'revoked', 'expired', 'wrong_principal',
                                        'wrong_room', 'missing_scope', 'missing_binding',
                                        'stale_connection', 'wrong_runtime_evidence'])
def test_worker_ready_batch_matches_single_credential_validation(fixture, condition):
    store, core, room_id = fixture
    now = time.time()
    binding = token_hash('fixture-a')
    principal = copy.deepcopy(store.principals['a'])
    principal['connection'] = {'_credential_binding': binding, 'state': 'idle', 'room_id': room_id,
                               'last_seen_at': now, 'runtime': 'synthetic',
                               'execution_evidence': 'synthetic_test_only'}
    credential = store.credentials[binding]
    required = {'room:read'}
    if condition == 'revoked': credential['revoked'] = True
    elif condition == 'expired': credential['expires_at'] = now - 1
    elif condition == 'wrong_principal': credential['id'] = 'b'
    elif condition == 'wrong_room': credential['room_id'] = 'other-room'
    elif condition == 'missing_scope': credential['scopes'] = []
    elif condition == 'missing_binding': principal['connection']['_credential_binding'] = 'missing'
    elif condition == 'stale_connection': principal['connection']['last_seen_at'] = now - 76
    elif condition == 'wrong_runtime_evidence': principal['connection']['execution_evidence'] = 'unverified'
    single = core._worker_ready(principal, room_id, now, required)
    batch = core._worker_ready(principal, room_id, now, required,
                               store.credentials_active([principal['connection']['_credential_binding']]))
    assert single == batch == (condition == 'valid')


def test_fence_reads_one_room_without_snapshot_or_events(fixture, monkeypatch):
    store, core, room_id = fixture
    original = store.read_transaction
    calls = []
    def read_room(rid, token, include_events=True):
        calls.append((rid, include_events))
        return original(rid, token, include_events=include_events)
    monkeypatch.setattr(store, 'read_transaction', read_room)
    def no_snapshot(*args, **kwargs):
        pytest.fail('fence must not load workspace snapshots')
    monkeypatch.setattr(store, 'snapshot', no_snapshot)
    assert core.room_fence('fixture-a', room_id)['room_id'] == room_id
    assert calls == [(room_id, False)]


def test_postgres_manual_start_with_single_connection(postgres_fixture, monkeypatch):
    from talkabout.core import Core
    from talkabout.domain import AGENT_PERMISSIONS
    from talkabout.identity import IdentityService
    from talkabout.store import PostgresStore
    original, _, _ = postgres_fixture
    monkeypatch.setenv('TALKABOUT_DB_POOL_SIZE', '1')
    monkeypatch.setenv('TALKABOUT_DB_POOL_TIMEOUT', '0.2')
    store = PostgresStore(original.dsn, original.schema)
    core = Core(store)
    try:
        room = core.create('fixture-human', {'topic': 'Single pool fixture', 'start_policy': 'manual'}, 'p2-pool')
        rid = room['id']
        invited = core.mutation('fixture-human', rid, 'memberships', 'p2-invite',
                                {'principal_id': 'a', 'permissions': sorted(AGENT_PERMISSIONS)})
        core.accept('fixture-a', rid, 'p2-accept',
                    {'expected_revision': invited['revision'], 'acknowledge_private': True})
        IdentityService(store).mutate('fixture-a', 'connection', 'p2-ready', {'runtime': 'synthetic', 'state': 'idle'})
        current = core.read('fixture-human', rid)
        lookups = []
        original_lookup = store.credentials_active
        def observed_lookup(bindings, conn=None):
            bindings = list(bindings)
            lookups.append((bindings, conn is not None))
            return original_lookup(bindings, conn=conn)
        monkeypatch.setattr(store, 'credentials_active', observed_lookup)
        pending = core.mutation('fixture-human', rid, 'controls', 'p2-start',
                                {'action': 'start', 'expected_revision': current['room_revision']})
        core.mutation('fixture-human', rid, 'controls/' + pending['id'] + '/approve', 'p2-approve', {})
        assert core.read('fixture-human', rid)['state'] == 'active'
        assert len(lookups) == 1 and lookups[0][1] is True
        assert store.pool_stats()['max_size'] == 1
    finally:
        store.close()
