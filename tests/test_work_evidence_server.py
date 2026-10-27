"""Synthetic work counters are bounded worker assertions, never verified facts."""
import copy

import pytest
from fastapi.testclient import TestClient

from conftest import leased, result
from talkabout.api import create_app
from talkabout.domain import AGENT_PERMISSIONS, Problem


GOOD = {'version': 1, 'command_items': 2, 'file_change_items': 1, 'failed_items': 1}
SOURCE = 'agent_reported_unverified'
RID = 'a' * 64


def api(client, method, path, token, body=None, key=None):
    return client.request(method, '/api/v1/' + path, json=body,
                          headers={'Authorization': 'Bearer ' + token,
                                   **({'Idempotency-Key': key} if key else {})})


def start(core, room_id):
    task = next(t for t in core.read('fixture-a', room_id)['tasks'] if t['state'] == 'queued')
    task = core.mutation('fixture-a', room_id, 'tasks/' + task['id'] + '/claim', 'claim-r3',
        {'generation': task['generation'], 'membership_revision': task['membership_revision'],
         'execution_protocol': 'server_start_v1'})
    fence = {k: task[k] for k in ('attempt', 'generation', 'membership_revision')}
    for action in ('reservations', 'start'):
        core.mutation('fixture-a', room_id, 'tasks/' + task['id'] + '/' + action, action,
                      {**fence, 'reservation_id': RID})
    return task


def payload(task, value=GOOD):
    body = result(task)
    body['result']['work_evidence'] = copy.deepcopy(value)
    return body


@pytest.mark.parametrize('value', [None, [], 1, {},
    {'version': True, 'command_items': 0, 'file_change_items': 0, 'failed_items': 0},
    {'version': 2, 'command_items': 0, 'file_change_items': 0, 'failed_items': 0},
    {'version': 1, 'command_items': True, 'file_change_items': 0, 'failed_items': 0},
    {'version': 1, 'command_items': -1, 'file_change_items': 0, 'failed_items': 0},
    {'version': 1, 'command_items': 129, 'file_change_items': 0, 'failed_items': 0},
    {'version': 1, 'command_items': 128, 'file_change_items': 1, 'failed_items': 0},
    {'version': 1, 'command_items': 1, 'file_change_items': 0, 'failed_items': 2},
    {'version': 1, 'command_items': 0, 'file_change_items': 0, 'failed_items': 0, 'source': 'verified'},
    {'version': 1, 'command_items': 0, 'file_change_items': 0},
    {'version': 1, 'command_items': 0.0, 'file_change_items': 0, 'failed_items': 0},
    {'version': 1, 'command_items': 0, 'file_change_items': 0, 'failed_items': False},
])
def test_bad_evidence_is_422_and_rolls_back(fixture, value):
    store, core, rid = fixture
    task = start(core, rid)
    before = copy.deepcopy(store.rooms[rid])
    with TestClient(create_app(store, start_scheduler=False, allow_fixture_bearer=True)) as client:
        response = api(client, 'POST', f'rooms/{rid}/tasks/{task["id"]}/complete', 'fixture-a', payload(task, value), 'complete-bad')
    assert response.status_code == 422
    assert 'invalid_work_evidence' in response.text
    assert store.rooms[rid] == before


@pytest.mark.parametrize('counts', [(0, 0, 0), (128, 0, 128), (0, 128, 128), (64, 64, 128)])
def test_boundary_values_attached_only_to_message(fixture, counts):
    store, core, rid = fixture
    task = start(core, rid)
    value = dict(zip(('command_items', 'file_change_items', 'failed_items'), counts)) | {'version': 1}
    answer = core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/complete', 'done', payload(task, value))
    assert answer['message']['work_evidence'] == {**value, 'source': SOURCE}
    assert 'work_evidence' not in answer['task']
    room = core.read('fixture-human', rid)
    assert next(m for m in room['messages'] if m['id'] == answer['message']['id'])['work_evidence'] == {**value, 'source': SOURCE}
    assert all('work_evidence' not in t for t in room['tasks'])
    assert all('work_evidence' not in event for event in store.rooms[rid]['events'])


def test_fences_and_r3_start_checked_before_evidence(fixture):
    _, core, rid = fixture
    task = next(t for t in core.read('fixture-a', rid)['tasks'] if t['state'] == 'queued')
    task = core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/claim', 'r3-claim',
        {'generation': task['generation'], 'membership_revision': task['membership_revision'], 'execution_protocol': 'server_start_v1'})
    with pytest.raises(Problem, match='execution_not_started'):
        core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/complete', 'unstarted', payload(task, None))
    stale = payload(task, None)
    stale['attempt'] += 1
    with pytest.raises(Problem, match='stale_attempt'):
        core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/complete', 'stale', stale)


def test_http_replay_history_and_rejoin_visibility(fixture):
    store, core, rid = fixture
    task = start(core, rid)
    body = payload(task)
    path = f'rooms/{rid}/tasks/{task["id"]}/complete'
    with TestClient(create_app(store, start_scheduler=False, allow_fixture_bearer=True)) as client:
        first = api(client, 'POST', path, 'fixture-a', body, 'complete-key')
        assert first.status_code == 200
        assert first.json()['message']['work_evidence'] == {**GOOD, 'source': SOURCE}
        again = api(client, 'POST', path, 'fixture-a', body, 'complete-key')
        assert again.status_code == 200 and again.json() == first.json()
        changed = copy.deepcopy(body)
        changed['result']['work_evidence']['command_items'] += 1
        conflict = api(client, 'POST', path, 'fixture-a', changed, 'complete-key')
        assert conflict.status_code == 409
        other_key = api(client, 'POST', path, 'fixture-a', body, 'other-key')
        assert other_key.status_code == 200 and other_key.json()['message'] == first.json()['message']
        conflicting_message = api(client, 'POST', path, 'fixture-a', changed, 'other-conflicting-key')
        assert conflicting_message.status_code == 409
        visible = api(client, 'GET', f'rooms/{rid}', 'fixture-b').json()
        assert any(m.get('work_evidence') == {**GOOD, 'source': SOURCE} for m in visible['messages'])
        member = visible['members']['b']
        left = api(client, 'PATCH', f'rooms/{rid}/memberships/b', 'fixture-human',
                   {'expected_revision': member['revision'], 'status': 'left', 'permissions': sorted(AGENT_PERMISSIONS)}, 'leave-b')
        assert left.status_code == 200
        invited = api(client, 'PATCH', f'rooms/{rid}/memberships/b', 'fixture-human',
                      {'expected_revision': left.json()['revision'], 'status': 'pending', 'permissions': sorted(AGENT_PERMISSIONS)}, 'reinvite-b')
        assert invited.status_code == 200
        accepted = api(client, 'POST', f'rooms/{rid}/memberships/me/accept', 'fixture-b',
                       {'expected_revision': invited.json()['revision'], 'acknowledge_private': True}, 'reaccept-b')
        assert accepted.status_code == 200
        rejoined = api(client, 'GET', f'rooms/{rid}', 'fixture-b')
        assert rejoined.status_code == 200
        assert all(m['id'] != first.json()['message']['id'] for m in rejoined.json()['messages'])
        human = api(client, 'GET', f'rooms/{rid}', 'fixture-human').json()
        assert any(m['id'] == first.json()['message']['id'] and m['work_evidence']['source'] == SOURCE for m in human['messages'])
        author = human['members']['a']
        left = api(client, 'PATCH', f'rooms/{rid}/memberships/a', 'fixture-human',
                   {'expected_revision': author['revision'], 'status': 'left', 'permissions': sorted(AGENT_PERMISSIONS)}, 'leave-a')
        assert left.status_code == 200
        invited = api(client, 'PATCH', f'rooms/{rid}/memberships/a', 'fixture-human',
                      {'expected_revision': left.json()['revision'], 'status': 'pending', 'permissions': sorted(AGENT_PERMISSIONS)}, 'reinvite-a')
        assert invited.status_code == 200
        accepted = api(client, 'POST', f'rooms/{rid}/memberships/me/accept', 'fixture-a',
                       {'expected_revision': invited.json()['revision'], 'acknowledge_private': True}, 'reaccept-a')
        assert accepted.status_code == 200
        for replay_key in ('complete-key', 'fresh-replay-key'):
            replayed = api(client, 'POST', path, 'fixture-a', body, replay_key)
            assert replayed.status_code in (403, 404, 409, 410)
            assert 'work_evidence' not in replayed.text


@pytest.mark.parametrize('permission', ['task:complete', 'room:speak'])
def test_revoked_permission_blocks_cached_and_fresh_replay(fixture, permission):
    store, core, rid = fixture
    task = start(core, rid)
    body = payload(task)
    path = f'rooms/{rid}/tasks/{task["id"]}/complete'
    with TestClient(create_app(store, start_scheduler=False, allow_fixture_bearer=True)) as client:
        assert api(client, 'POST', path, 'fixture-a', body, 'original').status_code == 200
        member = core.read('fixture-human', rid)['members']['a']
        permissions = sorted(set(member['permissions']) - {permission})
        revised = api(client, 'PATCH', f'rooms/{rid}/memberships/a', 'fixture-human',
                      {'expected_revision': member['revision'], 'status': 'active', 'permissions': permissions}, 'revoke')
        assert revised.status_code == 200
        for key in ('original', 'new-replay'):
            response = api(client, 'POST', path, 'fixture-a', body, key)
            assert response.status_code == 403
            assert 'work_evidence' not in response.text


def test_late_failure_rolls_back_message_and_evidence(fixture, monkeypatch):
    store, core, rid = fixture
    task = start(core, rid)
    before = copy.deepcopy(store.rooms[rid])
    import talkabout.core as core_module
    original = core_module.record_task_event
    def fail_after_message(room, task_id, stage, now):
        if stage == 'completed':
            assert any('work_evidence' in m for m in room['messages'].values())
            raise RuntimeError('synthetic late failure')
        return original(room, task_id, stage, now)
    monkeypatch.setattr(core_module, 'record_task_event', fail_after_message)
    with pytest.raises(RuntimeError, match='late failure'):
        core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/complete', 'late', payload(task))
    assert store.rooms[rid] == before


def test_legacy_result_without_evidence_remains_valid(fixture):
    _, core, rid = fixture
    task = leased(core, rid)
    answer = core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/complete', 'legacy', result(task))
    assert 'work_evidence' not in answer['message']
