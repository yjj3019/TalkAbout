"""Plan admission counts authorizations, not outcomes or provider cost."""
import copy
from concurrent.futures import ThreadPoolExecutor

import pytest

from talkabout.bounds import _plan_dispatch_bounds
from talkabout.domain import Problem, grant_reservation, reservation_outcome
from test_work_plan_dispatch import claim, dispatch, node, prepare


def sample_run(**budget):
    return {'id': 'run', 'plan_revision': 1, 'generation': 1, 'epoch': 1,
            'state': 'running', 'started_at': 1, 'finished_at': None,
            'abort_reason': None, 'nodes': [], **budget}


def sample_task(plan=True):
    task = {'id': 'task', 'assignee': 'agent', 'state': 'leased', 'generation': 1,
            'membership_revision': 1, 'lease_expires_at': 100, 'deadline': 100, 'attempt': 1}
    if plan:
        task['_plan'] = {'run_id': 'run'}
    return task


def reserve(room, task, n):
    return grant_reservation(room, task, {'id': 'agent'},
                             {'attempt': task['attempt'], 'generation': 1, 'membership_revision': 1,
                              'reservation_id': f'{n:064x}'}, 1)


def room_with(run):
    return {'id': 'room', 'state': 'active', 'generation': 1, 'last_seq': 0,
            'events': [], '_plan_dispatch': run}


def test_budget_counts_new_attempts_not_replays_or_outcomes():
    run = sample_run(reservation_cap=2, reservations_granted=0)
    room, task = room_with(run), sample_task()
    assert reserve(room, task, 1)['execute']
    assert reserve(room, task, 1)['replay']
    assert reserve(room, task, 2)['reason'] == 'reservation_already_started'
    assert run['reservations_granted'] == 1
    reservation_outcome(room, task, {'id': 'agent'}, f'{1:064x}', {'state': 'completed'}, 2)
    task['attempt'] = 2
    assert reserve(room, task, 2)['execute']
    assert run['reservations_granted'] == 2
    task['attempt'] = 3
    before = copy.deepcopy(room), copy.deepcopy(task)
    assert reserve(room, task, 3)['reason'] == 'run_call_limit'
    assert (room, task) == before
    task['attempt'] = 2
    assert reserve(room, task, 2)['replay']
    assert reserve(room, task, 3)['reason'] == 'reservation_already_started'


def test_legacy_and_non_plan_tasks_not_retroactively_capped():
    room, task = room_with(sample_run()), sample_task()
    for attempt in range(1, 20):
        task['attempt'] = attempt
        assert reserve(room, task, attempt)['execute']
    assert 'reservations_granted' not in room['_plan_dispatch']
    room['_plan_dispatch'] = sample_run(reservation_cap=1, reservations_granted=1)
    assert reserve(room, sample_task(plan=False), 30)['execute']


@pytest.mark.parametrize('run', [None, sample_run(id='other'), sample_run(state='completed')])
def test_plan_run_must_match_running_run(run):
    room, task = room_with(run), sample_task()
    with pytest.raises(Problem, match='plan_dispatch_not_running'):
        reserve(room, task, 1)
    assert '_reservations' not in task


@pytest.mark.parametrize('budget', [
    {'reservation_cap': 1}, {'reservations_granted': 0},
    {'reservation_cap': True, 'reservations_granted': 0},
    {'reservation_cap': 0, 'reservations_granted': 0},
    {'reservation_cap': 17, 'reservations_granted': 0},
    {'reservation_cap': 1, 'reservations_granted': True},
    {'reservation_cap': 1, 'reservations_granted': -1},
    {'reservation_cap': 1, 'reservations_granted': 2},
])
def test_malformed_budget_fails_closed_at_admission_and_bounds(budget):
    run = sample_run(**budget)
    with pytest.raises(Problem, match='plan_dispatch_bounds'):
        _plan_dispatch_bounds({'_plan_dispatch': run})
    task = sample_task()
    with pytest.raises(Problem, match='plan_dispatch_bounds'):
        reserve(room_with(run), task, 1)
    assert '_reservations' not in task


def test_bounds_allow_legacy_and_valid_budgets_only():
    for budget in ({}, {'reservation_cap': 1, 'reservations_granted': 0},
                   {'reservation_cap': 16, 'reservations_granted': 16}):
        _plan_dispatch_bounds({'_plan_dispatch': sample_run(**budget)})
    with pytest.raises(Problem, match='plan_dispatch_bounds'):
        _plan_dispatch_bounds({'_plan_dispatch': sample_run(extra=1)})


def test_concurrent_admission_is_serialized_by_room_transaction(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node('one', 'a'), node('two', 'b')], 'fixture-a', 'fixture-b')
    dispatch(core, rid)
    store.rooms[rid]['_plan_dispatch'].update(reservation_cap=1, reservations_granted=0)
    tasks = [claim(core, rid, token='fixture-a'), claim(core, rid, token='fixture-b')]

    def admit(pair):
        token, task = pair
        body = {k: task[k] for k in ('attempt', 'generation', 'membership_revision')}
        body['reservation_id'] = ('a' if token == 'fixture-a' else 'b') * 64
        try:
            return core.mutation(token, rid, 'tasks/' + task['id'] + '/reservations', 'reserve', body)
        except Problem as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(admit, zip(('fixture-a', 'fixture-b'), tasks)))
    assert sum(isinstance(result, dict) and result.get('reserved') is True for result in results) == 1
    assert sum(isinstance(result, dict) and result.get('reason') == 'run_call_limit' for result in results) == 1
    assert store.rooms[rid]['_plan_dispatch']['reservations_granted'] == 1
