"""The handoff endpoint is an authorized, read-only aggregate projection."""
import copy
import json

from fastapi.testclient import TestClient

from talkabout.api import create_app
from talkabout.domain import HUMAN_PERMISSIONS


def headers(token):
    return {'Authorization': 'Bearer ' + token}


def synthetic_run(store, rid):
    room = store.rooms[rid]
    epoch = room['members']['human']['epoch']
    room['_work_plan'] = {'_author': 'human', '_epoch': epoch, 'nodes': [], 'revision': 1}
    room['_plan_dispatch'] = {'id': 'private-run-id', 'epoch': epoch, 'generation': room['generation'],
                              'state': 'aborted', 'nodes': [
        {'id': 'private-node-id', 'state': 'failed', 'task_id': 'private-task-id',
         'assignee': 'a', 'instruction': 'PRIVATE INSTRUCTION'},
        {'id': 'blocked-node-id', 'state': 'blocked', 'task_id': None}]}
    room['tasks']['private-task-id'] = {
        'id': 'private-task-id', 'purpose': 'plan_node', 'state': 'uncertain', 'attempt': 1,
        'generation': room['generation'], 'assignee': 'a',
        '_plan': {'run_id': 'private-run-id', 'node_id': 'private-node-id'},
        'resolution': {'attempt': 1, 'external_effect': 'unknown', 'note': 'PRIVATE NOTE'}}


def test_handoff_http_empty_and_aborted_uncertain_no_mutation(fixture, monkeypatch):
    store, core, rid = fixture
    path = f'/api/v1/rooms/{rid}/work-plan/dispatch/handoff'
    monkeypatch.setattr(store, 'setup_required', lambda: False)
    app = create_app(store, start_scheduler=False, allow_fixture_bearer=True)
    with TestClient(app) as client:
        empty = client.get(path, headers=headers('fixture-human'))
        assert empty.status_code == 200 and empty.json() == {'run': None}
        synthetic_run(store, rid)
        before = copy.deepcopy(store.rooms[rid])
        # A read must not enter the write transaction (which reconciles/schedules).
        def forbidden_write(*args, **kwargs):
            raise AssertionError('handoff must not enter write transaction')
        monkeypatch.setattr(store, 'transaction', forbidden_write)
        response = client.get(path, headers=headers('fixture-human'))
        assert response.status_code == 200, response.text
        assert response.headers['cache-control'] == 'no-store'
        assert response.json() == {'run': {'node_counts': {'pending': 0, 'queued': 0, 'completed': 0,
            'failed': 1, 'blocked': 1, 'unknown': 0}, 'uncertain_tasks': 1,
            'acknowledged_uncertain_tasks': 1, 'unknown_associations': 0}}
        encoded = json.dumps(response.json())
        for secret in ('private-run-id', 'private-node-id', 'private-task-id', 'blocked-node-id',
                       'PRIVATE', rid):
            assert secret not in encoded
        assert store.rooms[rid] == before
        assert client.post(path, headers=headers('fixture-human'), json={}).status_code == 404


def test_handoff_authorization_and_epoch_match_dispatch(fixture, monkeypatch):
    store, core, rid = fixture
    monkeypatch.setattr(store, 'setup_required', lambda: False)
    synthetic_run(store, rid)
    store.provision({'id': 'other', 'owner': 'fixture-owner', 'kind': 'human',
                     'display_name': 'Other', 'handle': 'other'}, 'fixture-other', HUMAN_PERMISSIONS)
    member = copy.deepcopy(store.rooms[rid]['members']['human'])
    store.rooms[rid]['members']['other'] = member
    path = f'/api/v1/rooms/{rid}/work-plan/dispatch/handoff'
    with TestClient(create_app(store, start_scheduler=False, allow_fixture_bearer=True)) as client:
        assert client.get(path, headers=headers('fixture-a')).status_code == 403
        assert client.get(path, headers=headers('invalid')).status_code == 401
        assert client.get(path, headers=headers('fixture-other')).status_code == 404
        room = store.rooms[rid]
        original = copy.deepcopy(room)
        for change in (
            lambda r: r['_plan_dispatch'].update(epoch=999),
            lambda r: r['_work_plan'].update(_epoch=999),
            lambda r: r['_work_plan'].update(_author='other'),
            lambda r: r.pop('_work_plan'),
        ):
            store.rooms[rid] = copy.deepcopy(original)
            change(store.rooms[rid])
            before = copy.deepcopy(store.rooms[rid])
            response = client.get(path, headers=headers('fixture-human'))
            assert response.status_code == 404, response.text
            assert store.rooms[rid] == before


def test_postgres_handoff_read_does_not_mutate(postgres_fixture):
    store, core, rid = postgres_fixture
    with store.connect() as conn:
        before = conn.execute('SELECT last_event_seq, payload FROM rooms WHERE id=%s', (rid,)).fetchone()
        events_before = conn.execute('SELECT count(*) AS n FROM events WHERE room_id=%s', (rid,)).fetchone()['n']
    assert core.plan_handoff('fixture-human', rid) == {'run': None}
    with store.connect() as conn:
        after = conn.execute('SELECT last_event_seq, payload FROM rooms WHERE id=%s', (rid,)).fetchone()
        events_after = conn.execute('SELECT count(*) AS n FROM events WHERE room_id=%s', (rid,)).fetchone()['n']
    assert before == after and events_before == events_after
