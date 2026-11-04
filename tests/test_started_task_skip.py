"""A skip cannot advance scheduling after server-authorized execution."""
import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from conftest import leased
from talkabout.domain import Problem
from test_execution_start import RID, claim, fence, mutate, reserve


def skip(core, rid, task, body=None, key='skip'):
    return mutate(core, rid, task, 'skip', fence(task) if body is None else body, key)


@pytest.mark.parametrize('reported', [False, True])
def test_started_or_completed_reservation_rejects_skip_atomically(fixture, reported):
    store, core, rid = fixture
    task = claim(core, rid)
    reserve(core, rid, task)
    mutate(core, rid, task, 'start')
    if reported:
        mutate(core, rid, task, 'reservations/' + RID + '/outcome', {'state': 'completed'})
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem, match='execution_outcome_uncertain') as error:
        skip(core, rid, task)
    assert error.value.status == 409
    assert store.rooms[rid] == before


def test_skip_checks_fence_and_acl_first(fixture):
    store, core, rid = fixture
    task = claim(core, rid)
    reserve(core, rid, task)
    mutate(core, rid, task, 'start')
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem, match='stale_attempt'):
        skip(core, rid, task, {**fence(task), 'generation': 99})
    with pytest.raises(Problem):
        core.mutation('fixture-b', rid, 'tasks/' + task['id'] + '/skip', 'other', fence(task))
    assert store.rooms[rid] == before


def test_v1_reserved_can_skip_and_successful_replay_remains(fixture):
    _, core, rid = fixture
    task = claim(core, rid)
    reserve(core, rid, task)
    answer = skip(core, rid, task)
    assert answer['state'] == 'skipped'
    assert skip(core, rid, task) == answer


@pytest.mark.parametrize('state', ['reserved', 'started', 'completed'])
def test_legacy_executable_reservation_rejects_skip_atomically(fixture, state):
    store, core, rid = fixture
    task = leased(core, rid)
    granted = reserve(core, rid, task)
    assert granted['execute'] is True
    store.rooms[rid]['tasks'][task['id']]['_reservations'][0]['state'] = state
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem, match='execution_outcome_uncertain') as error:
        skip(core, rid, task)
    assert error.value.status == 409
    assert store.rooms[rid] == before


def test_legacy_claim_without_reservation_can_skip(fixture):
    _, core, rid = fixture
    task = leased(core, rid)
    assert skip(core, rid, task)['state'] == 'skipped'


def test_unmarked_reserved_item_is_not_assumed_to_be_v1(fixture):
    store, core, rid = fixture
    task = claim(core, rid)
    reserve(core, rid, task)
    del store.rooms[rid]['tasks'][task['id']]['_reservations'][0]['execution_protocol']
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem, match='execution_outcome_uncertain'):
        skip(core, rid, task)
    assert store.rooms[rid] == before


def test_cached_skip_cannot_bypass_current_started_reservation(fixture):
    store, core, rid = fixture
    task = claim(core, rid)
    reserve(core, rid, task)
    skip(core, rid, task)
    # Simulate an old cached response with inconsistent current live state.
    stored = store.rooms[rid]['tasks'][task['id']]
    stored['state'] = 'leased'
    stored['_reservations'][0]['state'] = 'started'
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem, match='execution_outcome_uncertain'):
        skip(core, rid, task)
    assert store.rooms[rid] == before


def test_skip_validates_idempotency_before_execution_guard(fixture):
    store, core, rid = fixture
    task = claim(core, rid)
    reserve(core, rid, task)
    skip(core, rid, task)
    stored = store.rooms[rid]['tasks'][task['id']]
    stored['state'] = 'leased'
    stored['_reservations'][0]['state'] = 'started'
    with pytest.raises(Problem, match='idempotency_conflict'):
        skip(core, rid, {**task, 'generation': task['generation'] + 1})
    with pytest.raises(Problem, match='idempotency_key_required'):
        core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/skip', '', fence(task))


def test_historical_skip_replay_survives_later_started_attempt(fixture):
    store, core, rid = fixture
    task = claim(core, rid)
    answer = skip(core, rid, task)
    stored = store.rooms[rid]['tasks'][task['id']]
    stored['attempt'] += 1
    stored['state'] = 'leased'
    stored['_reservations'] = [{'id': RID, 'attempt': stored['attempt'], 'state': 'started'}]
    before = copy.deepcopy(store.rooms[rid])
    assert skip(core, rid, task) == answer
    assert store.rooms[rid] == before
    with pytest.raises(Problem, match='execution_outcome_uncertain'):
        skip(core, rid, {**task, 'attempt': stored['attempt']}, key='new-skip')


def test_legacy_skip_replay_is_fenced_against_current_executable_reservation(fixture):
    store, core, rid = fixture
    task = leased(core, rid)
    answer = skip(core, rid, task)
    stored = store.rooms[rid]['tasks'][task['id']]
    stored['state'] = 'leased'
    stored['_reservations'] = [{'id': RID, 'attempt': task['attempt'], 'state': 'reserved'}]
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem, match='execution_outcome_uncertain'):
        skip(core, rid, task)
    assert store.rooms[rid] == before
    stored['attempt'] += 1
    stored['_reservations'][0]['attempt'] = stored['attempt']
    before = copy.deepcopy(store.rooms[rid])
    assert skip(core, rid, task) == answer
    assert store.rooms[rid] == before


def test_previous_attempt_reservation_does_not_block_skip(fixture):
    store, core, rid = fixture
    task = claim(core, rid)
    reserve(core, rid, task)
    store.rooms[rid]['tasks'][task['id']]['_reservations'][0].update(attempt=task['attempt'] - 1, state='started')
    assert skip(core, rid, task)['state'] == 'skipped'


@pytest.mark.parametrize('_', range(20))
def test_legacy_reservation_and_skip_serialize(fixture, _):
    store, core, rid = fixture
    task = leased(core, rid)
    barrier = Barrier(2)

    def run(action):
        barrier.wait()
        try:
            return reserve(core, rid, task) if action == 'reserve' else skip(core, rid, task)
        except Problem as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        granted, skipped = list(pool.map(run, ['reserve', 'skip']))
    current = store.rooms[rid]['tasks'][task['id']]
    if isinstance(granted, dict):
        assert granted['execute'] is True
        assert isinstance(skipped, Problem) and skipped.code == 'execution_outcome_uncertain'
        assert current['state'] == 'leased'
        assert current['_reservations'][0]['state'] == 'reserved'
    else:
        assert isinstance(granted, Problem) and granted.code == 'stale_attempt'
        assert skipped['state'] == current['state'] == 'skipped'
        assert not current.get('_reservations')


@pytest.mark.parametrize('_', range(20))
def test_start_and_skip_serialize(fixture, _):
    store, core, rid = fixture
    task = claim(core, rid)
    reserve(core, rid, task)
    barrier = Barrier(2)

    def run(action):
        barrier.wait()
        try:
            return mutate(core, rid, task, action, fence(task) if action == 'skip' else None)
        except Problem as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        started, skipped = list(pool.map(run, ['start', 'skip']))
    current = store.rooms[rid]['tasks'][task['id']]
    if isinstance(started, dict):
        assert started['execute'] is True
        assert isinstance(skipped, Problem) and skipped.code == 'execution_outcome_uncertain'
        assert current['state'] == 'leased'
    else:
        assert isinstance(started, Problem) and started.code == 'stale_attempt'
        assert skipped['state'] == current['state'] == 'skipped'
        assert current['_reservations'][0]['state'] == 'reserved'
