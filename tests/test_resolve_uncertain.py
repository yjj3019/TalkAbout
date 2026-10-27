"""Human acknowledgement of an uncertain attempt is bookkeeping only (synthetic, no provider)."""
import copy
import time

import pytest

from conftest import LocalClient
from talkabout.domain import Problem

RID = 'a' * 64
GOOD = {'attempt': 1, 'ack_unknown_effects': True, 'reason_code': 'operator_review'}


def fence(task):
    return {k: task[k] for k in ('attempt', 'generation', 'membership_revision')}


def claim(core, rid):
    task = next(t for t in core.read('fixture-a', rid)['tasks'] if t['state'] == 'queued')
    body = {k: task[k] for k in ('generation', 'membership_revision')}
    body['execution_protocol'] = 'server_start_v1'
    return core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/claim', 'rc', body)


def act(core, rid, task, action, body=None, key=None, token='fixture-a'):
    return core.mutation(token, rid, 'tasks/' + task['id'] + '/' + action, key or action,
                         body if body is not None else {**fence(task), 'reservation_id': RID})


def make_uncertain(store, core, rid):
    task = claim(core, rid)
    act(core, rid, task, 'reservations')
    act(core, rid, task, 'start')
    store.rooms[rid]['tasks'][task['id']]['lease_expires_at'] = time.time() - 1
    store.tick_all()
    assert store.rooms[rid]['tasks'][task['id']]['state'] == 'uncertain'
    return task


def resolve(core, rid, task, body=None, key='res', token='fixture-human'):
    return core.mutation(token, rid, 'tasks/' + task['id'] + '/resolve-uncertain', key,
                         copy.deepcopy(GOOD if body is None else body))


def snapshot(store, rid):
    room = store.rooms[rid]
    return copy.deepcopy({k: v for k, v in room['tasks'].items()}), len(room['tasks'])


def test_owner_acknowledges_without_changing_task_outcome(fixture):
    store, core, rid = fixture
    task = make_uncertain(store, core, rid)
    before_tasks = len(store.rooms[rid]['tasks'])
    out = resolve(core, rid, task, {**GOOD, 'note': 'checked with the worker owner'})
    stored = store.rooms[rid]['tasks'][task['id']]
    assert stored['state'] == 'uncertain'
    assert out['replay'] is False
    assert out['resolution']['external_effect'] == 'unknown'
    assert out['resolution']['resolved_by'] == 'human'
    assert stored['resolution'] == out['resolution'] and stored['resolution']['note'] == 'checked with the worker owner'
    assert len(store.rooms[rid]['tasks']) == before_tasks
    events = [e for e in store.rooms[rid]['events'] if e['type'] == 'task.uncertain_resolved']
    assert len(events) == 1 and 'note' not in events[0] and events[0]['task_id'] == task['id']
    assert events[0]['external_effect'] == 'unknown' and events[0]['attempt'] == 1
    assert not any(e['type'] == 'task.queued' and e.get('task_id') == task['id'] and e.get('attempt') != 0
                   for e in store.rooms[rid]['events'] if e['seq'] > events[0]['seq'])


def test_resolved_attempt_still_cannot_run_complete_or_reserve_again(fixture):
    store, core, rid = fixture
    task = make_uncertain(store, core, rid)
    resolve(core, rid, task)
    for action, code in (('reservations', 'task_outcome_uncertain'), ('start', None), ('heartbeat', None)):
        with pytest.raises(Problem) as err:
            act(core, rid, task, action, key='after-' + action)
        if code:
            assert str(err.value) == code or code in str(err.value)
    with pytest.raises(Problem):
        act(core, rid, task, 'complete', {**fence(task), 'content': 'late result'}, key='after-complete')
    assert store.rooms[rid]['tasks'][task['id']]['state'] == 'uncertain'


def test_same_request_replays_and_different_request_conflicts(fixture):
    store, core, rid = fixture
    task = make_uncertain(store, core, rid)
    first = resolve(core, rid, task)
    again = resolve(core, rid, task, key='res')  # same idempotency key and body
    assert again == first
    other_key = resolve(core, rid, task, key='res-2')
    assert other_key['replay'] is True and other_key['resolution'] == first['resolution']
    with pytest.raises(Problem, match='already_resolved'):
        resolve(core, rid, task, {**GOOD, 'reason_code': 'worker_lost'}, key='res-3')
    assert len([e for e in store.rooms[rid]['events'] if e['type'] == 'task.uncertain_resolved']) == 1


@pytest.mark.parametrize('body,code', [
    ({'attempt': 1, 'reason_code': 'operator_review'}, 'ack_required'),
    ({**GOOD, 'ack_unknown_effects': 'true'}, 'ack_required'),
    ({**GOOD, 'reason_code': 'because'}, 'invalid_resolution'),
    ({**GOOD, 'extra': 1}, 'invalid_resolution'),
    ({**GOOD, 'note': 'x' * 513}, 'invalid_resolution'),
    ({**GOOD, 'note': 'bad\x00note'}, 'invalid_resolution'),
    ({**GOOD, 'note': 'lone\ud800surrogate'}, 'invalid_resolution'),
    ({**GOOD, 'attempt': 2}, 'stale_resolution'),
    ({**GOOD, 'attempt': True}, 'stale_resolution'),
])
def test_invalid_or_stale_requests_change_nothing(fixture, body, code):
    store, core, rid = fixture
    task = make_uncertain(store, core, rid)
    before = copy.deepcopy(store.rooms[rid]['tasks'][task['id']])
    with pytest.raises(Problem, match=code):
        resolve(core, rid, task, body)
    assert store.rooms[rid]['tasks'][task['id']] == before


def test_only_uncertain_tasks_can_be_acknowledged(fixture):
    store, core, rid = fixture
    task = claim(core, rid)
    with pytest.raises(Problem, match='task_not_uncertain'):
        resolve(core, rid, task)
    assert 'resolution' not in store.rooms[rid]['tasks'][task['id']]


def test_agents_non_owner_humans_and_missing_control_are_refused(fixture):
    store, core, rid = fixture
    task = make_uncertain(store, core, rid)
    room = store.rooms[rid]
    with pytest.raises(Problem):
        resolve(core, rid, task, token='fixture-a')  # the assignee agent may not clear its own uncertainty
    store.provision({'id': 'other', 'owner': 'different-owner', 'kind': 'human',
                     'display_name': 'Other', 'handle': 'other'}, 'fixture-other', {'room:read', 'room:control'})
    room['members']['other'] = copy.deepcopy(room['members']['human'])
    with pytest.raises(Problem, match='forbidden'):
        resolve(core, rid, task, token='fixture-other', key='other-owner')
    owner = store.auth('fixture-human')['owner']
    store.provision({'id': 'noctl', 'owner': owner, 'kind': 'human',
                     'display_name': 'No control', 'handle': 'noctl'}, 'no-control', {'room:read'})
    room['members']['noctl'] = copy.deepcopy(room['members']['human'])
    with pytest.raises(Problem, match='forbidden'):
        resolve(core, rid, task, token='no-control', key='no-control')
    assert 'resolution' not in room['tasks'][task['id']]
    assert not any(e['type'] == 'task.uncertain_resolved' for e in room['events'])


def test_resolution_capability_matches_owner_and_current_permissions(fixture):
    store, core, rid = fixture
    assert core.read('fixture-human', rid)['can_resolve_uncertain'] is True
    assert core.read('fixture-a', rid)['can_resolve_uncertain'] is False
    room = store.rooms[rid]
    store.provision({'id': 'other', 'owner': 'different-owner', 'kind': 'human',
                     'display_name': 'Other', 'handle': 'other'}, 'fixture-other', {'room:read', 'room:control'})
    room['members']['other'] = copy.deepcopy(room['members']['human'])
    # Snapshot filtering hides other-owner rooms; test projection directly as defense in depth.
    from talkabout.core import public_room
    assert public_room(room, store.auth('fixture-other'))['can_resolve_uncertain'] is False
    room['members']['human']['permissions'].remove('room:control')
    assert core.read('fixture-human', rid)['can_resolve_uncertain'] is False
    room['members']['human']['permissions'].append('room:control')
    from talkabout.domain import token_hash
    store.credentials[token_hash('fixture-human')]['scopes'].remove('room:control')
    assert core.read('fixture-human', rid)['can_resolve_uncertain'] is False


def test_resolution_is_visible_to_the_owner_and_closed_room_allows_it(fixture):
    store, core, rid = fixture
    task = make_uncertain(store, core, rid)
    store.rooms[rid]['state'] = 'closed'
    out = resolve(core, rid, task)
    visible = next(t for t in core.read('fixture-human', rid)['tasks'] if t['id'] == task['id'])
    assert visible['resolution'] == out['resolution'] and visible['state'] == 'uncertain'
    assert '_resolution' not in visible


def test_http_route_requires_idempotency_and_works_for_owner(fixture):
    store, core, rid = fixture
    task = make_uncertain(store, core, rid)
    human, agent = LocalClient(store, 'fixture-human'), LocalClient(store, 'fixture-a')
    path = f'rooms/{rid}/tasks/{task["id"]}/resolve-uncertain'
    with pytest.raises(Exception):  # Idempotency-Key is mandatory for mutations
        human.request('POST', path, GOOD)
    with pytest.raises(Exception):
        agent.request('POST', path, GOOD, key='http-agent')
    out = human.request('POST', path, GOOD, key='http-res')
    assert out['resolution']['external_effect'] == 'unknown' and out['task']['state'] == 'uncertain'
    assert out['task']['resolution'] == out['resolution']
