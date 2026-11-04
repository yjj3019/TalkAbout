"""Plan protocol admission protects cooperating callers; it is not a provider firewall."""
import copy

import pytest

from conftest import LocalClient
from talkabout.domain import Problem
from talkabout.worker import Journal, Worker
from test_work_plan_dispatch import claim, complete, dispatch, node, plan_task, prepare
from test_worker_plan_context import RecordingRuntime


def result_body(task):
    return {k: task[k] for k in ('attempt', 'generation', 'membership_revision')} | {
        'result': {'client_message_id': 'gate-result', 'text': 'result', 'reply_to': None, 'mentions': []}}


def test_refused_legacy_claim_and_unstarted_completion_do_not_mutate(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    dispatch(core, rid)
    task = plan_task(core, rid)
    assert store.rooms[rid]['tasks'][task['id']]['_requires_server_start'] is True
    assert '_requires_server_start' not in str(core.read('fixture-a', rid))
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem, match='execution_protocol_required'):
        claim(core, rid, protocol=None)
    assert store.rooms[rid] == before
    with pytest.raises(Problem, match='invalid_execution_protocol'):
        claim(core, rid, key='bad-protocol', protocol='legacy')
    assert store.rooms[rid] == before
    leased = claim(core, rid)
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem, match='execution_not_started'):
        core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/complete', 'unstarted', result_body(leased))
    assert store.rooms[rid] == before
    # A malformed or preexisting lease must not bypass the completion check either.
    store.rooms[rid]['tasks'][task['id']].pop('execution_protocol')
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem, match='execution_protocol_required'):
        core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/complete', 'legacy-complete', result_body(leased))
    assert store.rooms[rid] == before


def test_old_running_run_and_untagged_leases_remain_compatible(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node('one'), node('two', deps=['one'])], 'fixture-a')
    dispatch(core, rid)
    room = store.rooms[rid]
    room.pop('_server_start_plan_run')
    task = plan_task(core, rid)
    room['tasks'][task['id']].pop('_requires_server_start')
    leased = claim(core, rid, protocol=None)
    complete(core, rid, leased)
    next_task = plan_task(core, rid)
    assert '_requires_server_start' not in store.rooms[rid]['tasks'][next_task['id']]
    complete(core, rid, claim(core, rid, key='legacy-next', protocol=None), key='legacy-done')


def test_new_run_deferred_tasks_tagged(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node('one'), node('two', deps=['one'])], 'fixture-a')
    dispatch(core, rid)
    complete(core, rid, claim(core, rid))
    task = plan_task(core, rid)
    assert store.rooms[rid]['tasks'][task['id']]['_requires_server_start'] is True


def test_real_worker_claims_reserves_starts_and_completes(fixture, tmp_path):
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    dispatch(core, rid)
    target = plan_task(core, rid)['id']
    client = LocalClient(store)
    original = client.request
    def request(method, path, body=None, key=None, params=None):
        response = original(method, path, body, key, params)
        if method == 'GET' and path == 'agents/me/events':
            response['events'] = [e for e in response['events'] if e.get('type') != 'task.queued' or e.get('task_id') == target]
        return response
    client.request = request
    journal = Journal(tmp_path / 'gate.sqlite')
    runtime = RecordingRuntime()
    try:
        assert Worker(client, journal, runtime).step() == 'completed'
        assert len(runtime.contexts) == 1
        task = store.rooms[rid]['tasks'][target]
        assert task['execution_protocol'] == 'server_start_v1'
        assert task['state'] == 'completed'
        assert task['_reservations'][0]['state'] in {'started', 'completed'}
    finally:
        journal.close()
        client.http.close()
