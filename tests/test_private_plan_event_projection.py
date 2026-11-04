"""Plan audit event envelopes are public; their payload is never projected."""
import json

import pytest
from fastapi.testclient import TestClient

from talkabout.api import create_app
from talkabout.domain import Problem, emit


ENVELOPE = {'event_id', 'room_id', 'seq', 'type', 'created_at'}


def exercise_projection(store, core, rid):
    cursors = {token: core.events(token, target_room=rid)['next_cursor']
               for token in ('fixture-human', 'fixture-a')}
    with store.transaction(rid, 'fixture-human') as (_, room):
        first = emit(room, 'plan.dispatch_started', run_id='private-run', node_count=987)
        second = emit(room, 'plan.node_failed', run_id='private-run', node_id='secret-node',
                      assignee='a', failure='aborted', future_private_field='secret')
        task = emit(room, 'task.queued', task_id='public-task', assignee='a')
    for token, cursor in cursors.items():
        data = core.events(token, cursor, target_room=rid)
        assert [event['seq'] for event in data['events']] == [first['seq'], second['seq'], task['seq']]
        assert [event['event_id'] for event in data['events']] == [first['event_id'], second['event_id'], task['event_id']]
        assert all(set(event) == ENVELOPE for event in data['events'][:2])
        assert data['events'][2]['task_id'] == 'public-task'
        assert core.events(token, data['next_cursor'], target_room=rid)['events'] == []
    # Inbox and snapshot/rebase read the same projection.
    inbox = core.events('fixture-a', rebase=True)
    plans = [event for event in inbox['events'] if event['type'].startswith('plan.')]
    assert len(plans) == 2 and all(set(event) == ENVELOPE for event in plans)
    # SSE is an adapter over the identical core method, including cursor semantics.
    client = TestClient(create_app(store, start_scheduler=False, allow_fixture_bearer=True))
    headers = {'Authorization': 'Bearer fixture-a'}
    for path, params in ((f'/api/v1/rooms/{rid}/events', {'cursor': cursors['fixture-a']}),
                         ('/api/v1/agents/me/events', {}),
                         ('/api/v1/agents/me/snapshot', {})):
        response = client.get(path, params=params, headers=headers)
        assert response.status_code == 200
        plans = [event for event in response.json()['events'] if event['type'].startswith('plan.')]
        assert len(plans) == 2 and all(set(event) == ENVELOPE for event in plans)
    response = client.get(f'/api/v1/rooms/{rid}/stream', params={'duration_seconds': 0, 'cursor': cursors['fixture-human']},
                          headers={'Authorization': 'Bearer fixture-human'})
    assert response.status_code == 200
    batch = json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith('data: ')))
    assert all(set(event) == ENVELOPE for event in batch['events'][:2])
    assert batch['events'][2]['task_id'] == 'public-task'
    # Projection does not mutate persisted payloads, which the scheduler owns.
    raw = store.room_events(rid, first['seq'] - 1, 16)
    assert raw[0]['node_count'] == 987 and raw[1]['node_id'] == 'secret-node'


def test_projection_memory(fixture):
    exercise_projection(*fixture)


def test_projection_postgres(postgres_fixture):
    exercise_projection(*postgres_fixture)


def test_stale_owner_epoch_and_revoked_credential(fixture):
    store, core, rid = fixture
    with store.transaction(rid, 'fixture-human') as (_, room):
        room['_work_plan'] = {'revision': 1, 'nodes': [], '_author': 'human',
                              '_epoch': room['members']['human']['epoch']}
        event = emit(room, 'plan.node_failed', node_id='secret-node')
    store.rooms[rid]['members']['human']['epoch'] += 1
    with pytest.raises(Problem, match='not_found'):
        core.work_plan('fixture-human', rid)
    result = core.events('fixture-human', target_room=rid)
    projected = next(e for e in result['events'] if e['event_id'] == event['event_id'])
    assert set(projected) == ENVELOPE
    store.revoke('fixture-human')
    with pytest.raises(Problem) as error:
        core.events('fixture-human', target_room=rid)
    assert error.value.status == 401
