"""Server-start authorization is one-shot, conservative, and opt-in."""
import copy
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from conftest import LocalClient, control, leased, result
from talkabout.domain import Problem

RID = 'a' * 64


def fence(task):
    return {k: task[k] for k in ('attempt', 'generation', 'membership_revision')}


def claim(core, rid, protocol='server_start_v1', key='r3-claim'):
    task = next(t for t in core.read('fixture-a', rid)['tasks'] if t['state'] == 'queued')
    body = {k: task[k] for k in ('generation', 'membership_revision')}
    if protocol is not None:
        body['execution_protocol'] = protocol
    return core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/claim', key, body)


def mutate(core, rid, task, action, body=None, key=None):
    return core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/' + action,
                         key or action, body if body is not None else {**fence(task), 'reservation_id': RID})


def reserve(core, rid, task):
    return mutate(core, rid, task, 'reservations')


def stored(store, rid, task):
    return store.rooms[rid]['tasks'][task['id']]


def test_reservation_does_not_authorize_and_same_key_start_is_one_shot(fixture):
    store, core, rid = fixture
    task = claim(core, rid)
    assert task['execution_protocol'] == 'server_start_v1'
    first = reserve(core, rid, task)
    assert first == {'execute': False, 'reservation_id': RID, 'state': 'reserved', 'reserved': True}
    assert reserve(core, rid, task)['reserved'] is True
    assert not stored(store, rid, task).get('_started_attempts')
    with ThreadPoolExecutor(2) as pool:
        answers = list(pool.map(lambda _: mutate(core, rid, task, 'start', key='same-key'), range(2)))
    assert sorted(a['execute'] for a in answers) == [False, True]
    assert mutate(core, rid, task, 'start', key='same-key')['execute'] is False
    assert stored(store, rid, task)['_started_attempts'] == [1]
    assert not any(':tasks/' + task['id'] + '/start:' in key for key in store.rooms[rid]['keys'])


@pytest.mark.parametrize('field', ['attempt', 'generation', 'membership_revision'])
def test_start_rejects_stale_fences_atomically(fixture, field):
    store, core, rid = fixture
    task = claim(core, rid)
    reserve(core, rid, task)
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem, match='stale_attempt'):
        mutate(core, rid, task, 'start', {**fence(task), 'reservation_id': RID, field: 99})
    assert store.rooms[rid] == before


@pytest.mark.parametrize('condition,code', [('hold', 'dispatch_held'), ('pause', 'room_not_active'),
                                            ('expired', 'stale_attempt'), ('member', 'stale_attempt')])
def test_start_checks_current_server_context(fixture, condition, code):
    store, core, rid = fixture
    task = claim(core, rid)
    reserve(core, rid, task)
    room = store.rooms[rid]
    if condition == 'hold':
        room['dispatch_held'] = True
    elif condition == 'pause':
        room['state'] = 'paused'
    elif condition == 'member':
        room['members']['a']['revision'] += 1
    else:
        stored(store, rid, task)['lease_expires_at'] = 1
    with pytest.raises(Problem, match=code):
        mutate(core, rid, task, 'start')
    assert not stored(store, rid, task).get('_started_attempts')


def test_completion_requires_server_start_not_worker_event(fixture):
    store, core, rid = fixture
    task = claim(core, rid)
    reserve(core, rid, task)
    mutate(core, rid, task, 'heartbeat', {**fence(task), 'events': ['runtime_started']})
    assert not stored(store, rid, task).get('_started_attempts')
    with pytest.raises(Problem, match='execution_not_started'):
        mutate(core, rid, task, 'complete', result(task))
    with pytest.raises(Problem, match='execution_not_started'):
        mutate(core, rid, task, 'reservations/' + RID + '/outcome', {'state': 'completed'})
    mutate(core, rid, task, 'start')
    assert mutate(core, rid, task, 'complete', result(task))['task']['state'] == 'completed'


@pytest.mark.parametrize('expiry', ['lease', 'task', 'room'])
@pytest.mark.parametrize('reported', [False, True])
def test_scheduler_never_requeues_authorized_execution(fixture, expiry, reported):
    store, core, rid = fixture
    task = claim(core, rid)
    reserve(core, rid, task)
    mutate(core, rid, task, 'start')
    if reported:
        mutate(core, rid, task, 'reservations/' + RID + '/outcome', {'state': 'completed'})
    room = store.rooms[rid]
    target = stored(store, rid, task)
    if expiry == 'room':
        room['deadline'] = time.time() - 1
    else:
        target['lease_expires_at' if expiry == 'lease' else 'deadline'] = time.time() - 1
    store.tick_all()
    assert stored(store, rid, task)['state'] == 'uncertain'
    store.tick_all()
    assert stored(store, rid, task)['attempt'] == 1
    assert not any(e['type'] == 'task.queued' and e.get('task_id') == task['id'] and e.get('attempt') == 1
                   for e in room['events'])


def test_unstarted_reservation_can_requeue_but_old_start_cannot_authorize(fixture):
    store, core, rid = fixture
    first = claim(core, rid)
    reserve(core, rid, first)
    stored(store, rid, first)['lease_expires_at'] = 1
    store.tick_all()
    second = claim(core, rid, key='retry')
    assert second['attempt'] == 2
    with pytest.raises(Problem, match='stale_attempt'):
        mutate(core, rid, first, 'start')
    with pytest.raises(Problem, match='reservation_required'):
        mutate(core, rid, second, 'start')


def test_old_attempt_uncertain_outcome_does_not_poison_current_attempt(fixture):
    store, core, rid = fixture
    first = leased(core, rid)  # legacy permits retry after reservation
    reserve(core, rid, first)
    stored(store, rid, first)['lease_expires_at'] = 1
    store.tick_all()
    second = claim(core, rid, key='r3-retry')
    # A historical legacy reservation remains reconcilable on an opted-in task.
    mutate(core, rid, first, 'reservations/' + RID + '/outcome', {'state': 'uncertain'})
    assert stored(store, rid, second)['state'] == 'leased'
    assert stored(store, rid, second)['attempt'] == 2


def test_outcome_can_reconcile_after_close_without_authorizing_work(fixture):
    store, core, rid = fixture
    task = claim(core, rid)
    reserve(core, rid, task)
    mutate(core, rid, task, 'start')
    control(core, rid, 'close')
    assert stored(store, rid, task)['state'] == 'uncertain'
    # Stale session context may accompany a late outcome; it cannot authorize work.
    body = {'state': 'completed', 'session_id': 'obsolete', **fence(task)}
    assert mutate(core, rid, task, 'reservations/' + RID + '/outcome', body)['state'] == 'completed'
    assert stored(store, rid, task)['state'] == 'uncertain'
    with pytest.raises(Problem, match='room_not_active'):
        mutate(core, rid, task, 'start')


def test_legacy_claim_reservation_and_completion_are_unchanged(fixture):
    _, core, rid = fixture
    task = claim(core, rid, protocol=None)
    assert 'execution_protocol' not in task
    assert reserve(core, rid, task)['execute'] is True
    assert mutate(core, rid, task, 'complete', result(task))['task']['state'] == 'completed'


def test_legacy_completion_needs_no_reservation(fixture):
    _, core, rid = fixture
    task = leased(core, rid)
    assert mutate(core, rid, task, 'complete', result(task))['task']['state'] == 'completed'


def test_http_start_route_and_same_key_replay(fixture):
    store, core, rid = fixture
    client = LocalClient(store)
    queued = next(t for t in core.read('fixture-a', rid)['tasks'] if t['state'] == 'queued')
    path = 'rooms/' + rid + '/tasks/' + queued['id']
    task = client.request('POST', path + '/claim', {k: queued[k] for k in ('generation', 'membership_revision')} |
                          {'execution_protocol': 'server_start_v1'}, 'http-claim')
    body = {**fence(task), 'reservation_id': RID}
    assert client.request('POST', path + '/reservations', body, 'http-reserve')['reserved'] is True
    assert client.request('POST', path + '/start', body, 'http-start')['execute'] is True
    assert client.request('POST', path + '/start', body, 'http-start')['execute'] is False
    assert client.request('POST', path + '/complete', result(task), 'http-complete')['task']['state'] == 'completed'


@pytest.mark.parametrize('protocol', ['unknown', [], {}])
def test_invalid_protocol_is_rejected_without_claiming(fixture, protocol):
    store, core, rid = fixture
    with pytest.raises(Problem, match='invalid_execution_protocol'):
        claim(core, rid, protocol=protocol)
    assert next(iter(store.rooms[rid]['tasks'].values()))['state'] == 'queued'


def test_start_requires_matching_reservation_and_full_fence(fixture):
    _, core, rid = fixture
    task = claim(core, rid)
    with pytest.raises(Problem, match='reservation_required'):
        mutate(core, rid, task, 'start')
    reserve(core, rid, task)
    with pytest.raises(Problem, match='reservation_required'):
        mutate(core, rid, task, 'start', {**fence(task), 'reservation_id': 'b' * 64})
    for field in fence(task):
        body = {**fence(task), 'reservation_id': RID}
        del body[field]
        with pytest.raises(Problem, match='stale_attempt'):
            mutate(core, rid, task, 'start', body)


def test_completed_outcome_allows_completion_but_not_another_start(fixture):
    _, core, rid = fixture
    task = claim(core, rid)
    reserve(core, rid, task)
    mutate(core, rid, task, 'start')
    mutate(core, rid, task, 'reservations/' + RID + '/outcome', {'state': 'completed'})
    assert mutate(core, rid, task, 'start')['execute'] is False
    assert mutate(core, rid, task, 'complete', result(task))['task']['state'] == 'completed'
