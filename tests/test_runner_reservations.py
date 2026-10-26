"""Execution reservations: one paid call per task attempt, and no invented outcomes.

The server grants a reservation only to the worker holding the lease, refuses a second
one for the same attempt, and records what happened afterwards. Nothing here calls a
provider; the tests use a fake runtime and the in-memory store.
"""
import copy

import pytest

from conftest import LocalClient, control, leased, result
from talkabout.domain import MAX_RESERVATIONS, Problem, grant_reservation

RESERVATION = 'a' * 64


def fence(task):
    return {key: task[key] for key in ('attempt', 'generation', 'membership_revision')}


def reserve(core, rid, task, key='res-1', reservation_id=RESERVATION, **overrides):
    body = {**fence(task), 'purpose': 'task', 'reservation_id': reservation_id, **overrides}
    return core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/reservations', key, body)


def outcome(core, rid, task, reservation_id=RESERVATION, state='completed', key=None):
    return core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/reservations/' + reservation_id + '/outcome',
                         key or ('outcome-' + state), {'state': state})


def reservations_of(core, rid, task):
    return core.store.rooms[rid]['tasks'][task['id']].get('_reservations', [])


def test_a_reservation_is_granted_once_and_a_replay_never_becomes_a_second_call(fixture):
    _, core, rid = fixture
    task = leased(core, rid)
    first = reserve(core, rid, task)
    assert first['execute'] is True and first['state'] == 'reserved'
    replay = reserve(core, rid, task, key='res-1-replay')
    assert replay['execute'] is False and replay['replay'] is True
    assert len(reservations_of(core, rid, task)) == 1


def test_a_second_reservation_for_the_same_attempt_is_refused(fixture):
    _, core, rid = fixture
    task = leased(core, rid)
    assert reserve(core, rid, task)['execute'] is True
    second = reserve(core, rid, task, key='res-2', reservation_id='b' * 64)
    assert second['execute'] is False and second['reason'] == 'reservation_already_started'
    assert len(reservations_of(core, rid, task)) == 1


@pytest.mark.parametrize('field, value', [('attempt', 99), ('generation', 99), ('membership_revision', 99)])
def test_a_stale_fence_is_refused_without_touching_the_room(fixture, field, value):
    _, core, rid = fixture
    task = leased(core, rid)
    before = copy.deepcopy(core.store.rooms[rid])
    with pytest.raises(Problem, match='stale_attempt'):
        reserve(core, rid, task, **{field: value})
    assert core.store.rooms[rid] == before


def test_an_expired_lease_is_refused(fixture):
    store, core, rid = fixture
    task = leased(core, rid)
    store.rooms[rid]['tasks'][task['id']]['lease_expires_at'] = 1
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem, match='stale_attempt'):
        reserve(core, rid, task)
    assert store.rooms[rid] == before


def test_dispatch_hold_and_paused_rooms_refuse_reservations(fixture):
    store, core, rid = fixture
    task = leased(core, rid)
    store.rooms[rid]['dispatch_held'] = True
    with pytest.raises(Problem, match='dispatch_held'):
        reserve(core, rid, task)
    store.rooms[rid]['dispatch_held'] = False
    control(core, rid, 'pause')
    with pytest.raises(Problem, match='room_not_active'):
        reserve(core, rid, task)


def test_invalid_reservation_ids_and_purposes_are_refused(fixture):
    _, core, rid = fixture
    task = leased(core, rid)
    with pytest.raises(Problem, match='invalid_reservation_id'):
        reserve(core, rid, task, reservation_id='not-a-digest')
    with pytest.raises(Problem, match='invalid_reservation_purpose'):
        reserve(core, rid, task, purpose='connection_test')


def test_the_reservation_ring_is_bounded_by_the_documented_capacity():
    room = {'id': 'room-one', 'state': 'active', 'generation': 1, 'last_seq': 0, 'events': []}
    task = {'id': 'task', 'assignee': 'agent', 'state': 'leased', 'generation': 1,
            'membership_revision': 1, 'lease_expires_at': 10_000, 'deadline': 10_000, 'attempt': 0}
    room['tasks'] = {'task': task}
    actor = {'id': 'agent', 'kind': 'agent'}
    for attempt in range(1, MAX_RESERVATIONS + 4):
        task['attempt'] = attempt
        granted = grant_reservation(room, task, actor,
                                    {'attempt': attempt, 'generation': 1, 'membership_revision': 1,
                                     'purpose': 'task', 'reservation_id': ('%064d' % attempt)[-64:]}, 1000)
        assert granted['execute'] is True
    assert len(task['_reservations']) == MAX_RESERVATIONS
    assert [item['attempt'] for item in task['_reservations']] == \
           list(range(4, MAX_RESERVATIONS + 4))


def test_an_uncertain_outcome_blocks_retry_and_is_recorded_once(fixture):
    _, core, rid = fixture
    task = leased(core, rid)
    assert reserve(core, rid, task)['execute'] is True
    reported = outcome(core, rid, task, state='uncertain')
    assert reported['state'] == 'uncertain'
    stored = core.store.rooms[rid]['tasks'][task['id']]
    assert stored['state'] == 'uncertain'
    assert len([item for item in stored['_reservations'] if item['state'] == 'uncertain']) == 1
    # No automatic retry, and a contradictory report is refused.
    with pytest.raises(Problem, match='task_outcome_uncertain'):
        reserve(core, rid, task, key='res-again')
    with pytest.raises(Problem, match='reservation_outcome_conflict'):
        outcome(core, rid, task, state='completed', key='contradiction')


def test_a_completed_outcome_leaves_the_task_claimed_for_the_normal_completion(fixture):
    _, core, rid = fixture
    task = leased(core, rid)
    assert reserve(core, rid, task)['execute'] is True
    assert outcome(core, rid, task, state='completed')['state'] == 'completed'
    stored = core.store.rooms[rid]['tasks'][task['id']]
    assert stored['state'] == 'leased'
    assert outcome(core, rid, task, state='completed', key='outcome-replay')['replay'] is True
    core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/complete', 'finish', result(task))
    room = core.read('fixture-human', rid)
    assert next(t for t in room['tasks'] if t['id'] == task['id'])['state'] == 'completed'


def test_an_outcome_for_an_unreserved_call_is_not_accepted(fixture):
    _, core, rid = fixture
    task = leased(core, rid)
    with pytest.raises(Problem, match='not_found'):
        outcome(core, rid, task, reservation_id='c' * 64)
    assert not reservations_of(core, rid, task)


def test_every_refusal_leaves_no_reservation_behind(fixture):
    _, core, rid = fixture
    task = leased(core, rid)
    for bad in ({'attempt': 99}, {'purpose': 'connection_test'}, {'reservation_id': 'short'}):
        with pytest.raises(Problem):
            reserve(core, rid, task, **bad)
    assert not reservations_of(core, rid, task)
    assert not any(event['type'] == 'task.execution_reserved' for event in core.store.rooms[rid]['events'])

def test_a_repeated_reservation_request_is_never_answered_from_the_cache(fixture):
    """A cached execute:true would be exactly the double call the reservation forbids."""
    _, core, rid = fixture
    task = leased(core, rid)
    first = reserve(core, rid, task, key='same-key')
    assert first['execute'] is True
    replayed = reserve(core, rid, task, key='same-key')
    assert replayed['execute'] is False and replayed['replay'] is True
    assert len(reservations_of(core, rid, task)) == 1

def test_the_http_gate_accepts_the_reservation_routes(fixture):
    """The route regexes in api.py are part of the contract; a typo here is a silent 404."""
    store, core, rid = fixture
    task = leased(core, rid)
    client = LocalClient(store)
    body = {**fence(task), 'purpose': 'task', 'reservation_id': RESERVATION}
    granted = client.request('POST', 'rooms/' + rid + '/tasks/' + task['id'] + '/reservations', body, 'http-reserve')
    assert granted['execute'] is True
    reported = client.request('POST', 'rooms/' + rid + '/tasks/' + task['id'] + '/reservations/'
                              + RESERVATION + '/outcome', {'state': 'completed'}, 'http-outcome')
    assert reported['state'] == 'completed'