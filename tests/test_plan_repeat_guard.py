"""A saved plan revision is a one-shot dispatch, not a reusable start key."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from talkabout.domain import Problem
from test_work_plan_dispatch import (
    claim, complete, dispatch, dispatch_body, node, prepare, private, run, tick, write,
)


def footprint(store, rid):
    """Count actual plan work and start events, not just the public response."""
    room = private(store, rid)
    tasks = {key: value for key, value in room['tasks'].items()
             if value.get('purpose') == 'plan_node'}
    starts = [event for event in room['events'] if event['type'] == 'plan.dispatch_started']
    return tasks, starts


def finish(core, rid):
    complete(core, rid, claim(core, rid))
    tick(core, rid)
    assert run(core, rid)['state'] == 'completed'


def test_terminal_revision_rejects_new_key_but_replay_and_new_save_work(fixture):
    store, core, rid = fixture
    # Free discussion does not enqueue an unrelated automatic turn at completion.
    store.rooms[rid]['mode'] = 'free_discussion'
    nodes = [node()]
    prepare(store, core, rid, nodes, 'fixture-a')
    body = dispatch_body(core, rid)
    first = core.dispatch_work_plan('fixture-human', rid, 'first', body)
    finish(core, rid)
    before = footprint(store, rid)
    assert len(before[0]) == len(before[1]) == 1

    assert core.dispatch_work_plan('fixture-human', rid, 'first', body) == first
    with pytest.raises(Problem, match='plan_revision_already_dispatched') as error:
        core.dispatch_work_plan('fixture-human', rid, 'different-key', body)
    assert error.value.status == 409
    assert footprint(store, rid) == before
    assert run(core, rid)['state'] == 'completed'
    assert run(core, rid)['id'] == first['run']['id']

    # Even saving identical content is an explicit new revision, not a replay.
    write(core, rid, nodes, revision=1, key='explicit-save')
    second = dispatch(core, rid, 'second', plan_revision=2)
    assert second['run']['id'] != first['run']['id']
    assert second['run']['plan_revision'] == 2
    after = footprint(store, rid)
    assert len(after[0]) == len(after[1]) == 2


def test_delayed_old_body_cannot_start_after_revision_saved(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    stale = dispatch_body(core, rid)
    first = core.dispatch_work_plan('fixture-human', rid, 'old', stale)
    finish(core, rid)
    write(core, rid, [node('new')], revision=1, key='new-plan')
    before = footprint(store, rid)
    with pytest.raises(Problem, match='work_plan_revision_conflict') as error:
        core.dispatch_work_plan('fixture-human', rid, 'delayed', stale)
    assert error.value.status == 409
    assert footprint(store, rid) == before
    assert run(core, rid)['id'] == first['run']['id']
    assert run(core, rid)['state'] == 'completed'
    assert core.dispatch_work_plan('fixture-human', rid, 'old', stale) == first
    assert footprint(store, rid) == before


def test_generation_change_terminal_does_not_allow_same_revision(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    body = dispatch_body(core, rid)
    first = core.dispatch_work_plan('fixture-human', rid, 'generation-start', body)
    room = core.read('fixture-human', rid)
    pending = core.mutation('fixture-human', rid, 'controls', 'topic-request',
                            {'action': 'change_topic', 'parameters': {'topic': 'Changed topic'},
                             'expected_revision': room['room_revision']})
    core.mutation('fixture-human', rid, 'controls/' + pending['id'] + '/approve', 'topic-approve', {})
    terminal = run(core, rid)
    assert terminal['id'] == first['run']['id']
    assert terminal['state'] == 'aborted'
    assert terminal['abort_reason'] == 'generation_changed'
    before = footprint(store, rid)
    assert len(before[0]) == len(before[1]) == 1
    assert next(iter(before[0].values()))['state'] == 'cancelled'
    assert core.dispatch_work_plan('fixture-human', rid, 'generation-start', body) == first
    with pytest.raises(Problem, match='plan_revision_already_dispatched') as error:
        dispatch(core, rid, 'new-generation-key')
    assert error.value.status == 409
    assert footprint(store, rid) == before
    assert run(core, rid) == terminal


def exercise_concurrent_starts(test_fixture):
    store, core, rid = test_fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    body = dispatch_body(core, rid)
    barrier = Barrier(2)

    def start(key):
        barrier.wait(timeout=10)
        try:
            return core.dispatch_work_plan('fixture-human', rid, key, body)
        except Problem as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(start, 'race-' + str(i)) for i in range(2)]
        results = [future.result(timeout=30) for future in futures]
    successes = [result for result in results if isinstance(result, dict)]
    failures = [result for result in results if isinstance(result, Problem)]
    assert len(successes) == len(failures) == 1
    assert failures[0].status == 409
    assert failures[0].code in {'plan_dispatch_active', 'plan_revision_already_dispatched'}
    tasks, starts = footprint(store, rid)
    assert len(tasks) == len(starts) == 1
    assert run(core, rid)['id'] == successes[0]['run']['id']
    finish(core, rid)
    before = footprint(store, rid)
    with pytest.raises(Problem, match='plan_revision_already_dispatched'):
        dispatch(core, rid, 'after-race')
    assert footprint(store, rid) == before


def test_concurrent_starts_memory(fixture):
    exercise_concurrent_starts(fixture)


def test_concurrent_starts_postgres(postgres_fixture):
    exercise_concurrent_starts(postgres_fixture)
