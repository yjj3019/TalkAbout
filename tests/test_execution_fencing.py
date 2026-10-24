"""Server task fences with isolated stores and synthetic runtimes only."""
import time

import pytest

from conftest import LocalClient, control
from talkabout.domain import Problem
from talkabout.worker import Journal, Worker


def test_lost_uncertain_journal_cannot_repeat_call_after_lease_expiry(fixture, tmp_path):
    store, core, rid = fixture
    calls = []
    class PossiblyAccepted:
        def run(self, context, cancel):
            calls.append(context['task_id'])
            if len(calls) == 1:
                raise RuntimeError('synthetic response lost after acceptance')
            return 'Must not be executed automatically'
    runtime = PossiblyAccepted()
    client = LocalClient(store)
    old = Journal(tmp_path / 'old.sqlite')
    try:
        with pytest.raises(RuntimeError, match='synthetic response lost'):
            Worker(client, old, runtime).step()
        assert old.status() == {'uncertain': 1}
    finally:
        old.close()
        client.http.close()
    with store.transaction(rid, 'fixture-human') as (_, room):
        room['tasks'][calls[0]]['lease_expires_at'] = time.time() - 1
    core.tick('fixture-human', rid)
    client = LocalClient(store)
    fresh = Journal(tmp_path / 'new.sqlite')
    try:
        outcome = Worker(client, fresh, runtime).step()
        assert len(calls) == 1, 'a new journal must not repeat a possibly accepted call'
        assert outcome in {'unavailable', 'waiting'}
        tasks = core.read('fixture-human', rid)['tasks']
        assert len(tasks) == 1 and tasks[0]['state'] == 'uncertain'
        assert tasks[0]['execution']['state'] == 'uncertain'
    finally:
        fresh.close()
        client.http.close()


def claim_protocol(core, rid, token='fixture-a'):
    task = next(t for t in core.read(token, rid)['tasks'] if t['state'] == 'queued')
    return core.mutation(token, rid, 'tasks/' + task['id'] + '/claim', 'claim-protocol',
        {k: task[k] for k in ('generation', 'membership_revision')} |
        {'execution_protocol': 'server_start_v1'})


def fence(task):
    return {k: task[k] for k in ('attempt', 'generation', 'membership_revision')}


def test_call_start_is_one_shot_and_completed_result_remains_deliverable(fixture):
    from conftest import result
    _, core, rid = fixture
    task = claim_protocol(core, rid)
    path = 'tasks/' + task['id'] + '/start'
    assert core.mutation('fixture-a', rid, path, 'start-once', fence(task))['execute'] is True
    assert core.mutation('fixture-a', rid, path, 'start-once', fence(task))['execute'] is False
    with pytest.raises(Problem, match='execution_already_started'):
        core.mutation('fixture-a', rid, path, 'different-start', fence(task))
    completed = core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/complete', 'saved-result', result(task))
    replayed = core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/complete', 'saved-result', result(task))
    assert completed['message']['id'] == replayed['message']['id']
    assert completed['task']['execution']['state'] == 'completed'


@pytest.mark.parametrize('boundary', ['pause', 'close', 'change_topic', 'hold_dispatch'])
def test_control_boundary_never_releases_an_uncertain_call(fixture, boundary):
    store, core, rid = fixture
    task = claim_protocol(core, rid)
    core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/start', 'start', fence(task))
    if boundary == 'change_topic':
        room = core.read('fixture-human', rid)
        proposed = core.mutation('fixture-human', rid, 'controls', 'topic',
            {'action': boundary, 'expected_revision': room['room_revision'], 'parameters': {'topic': 'Changed'}})
        core.mutation('fixture-human', rid, 'controls/' + proposed['id'] + '/approve', 'topic-approve', {})
    else:
        control(core, rid, boundary)
    with store.transaction(rid, 'fixture-human') as (_, room):
        room['tasks'][task['id']]['lease_expires_at'] = time.time() - 1
    core.tick('fixture-human', rid)
    if boundary == 'pause':
        control(core, rid, 'resume')
    elif boundary == 'hold_dispatch':
        control(core, rid, 'release_dispatch')
    current = core.read('fixture-human', rid)['tasks']
    assert len(current) == 1 and current[0]['state'] == 'uncertain'
    assert current[0]['execution']['state'] == 'uncertain'
    with pytest.raises(Problem):
        core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/claim', 'cannot-reclaim',
            {'generation': current[0]['generation'], 'membership_revision': current[0]['membership_revision'],
             'execution_protocol': 'server_start_v1'})


def test_protocol_completion_requires_start_but_precall_expiry_can_retry(fixture):
    from conftest import result
    store, core, rid = fixture
    task = claim_protocol(core, rid)
    with pytest.raises(Problem, match='execution_not_started'):
        core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/complete', 'no-start', result(task))
    with store.transaction(rid, 'fixture-human') as (_, room):
        room['tasks'][task['id']]['lease_expires_at'] = time.time() - 1
    core.tick('fixture-human', rid)
    current = core.read('fixture-a', rid)['tasks'][0]
    assert current['state'] == 'queued'
    with pytest.raises(Problem, match='execution_protocol_required'):
        core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/claim', 'downgrade',
            {k: current[k] for k in ('generation', 'membership_revision')})
    retried = core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/claim', 'safe-precall',
        {k: current[k] for k in ('generation', 'membership_revision')} |
        {'execution_protocol': 'server_start_v1'})
    assert retried['attempt'] == task['attempt'] + 1


def test_lost_call_start_response_never_enters_runtime_or_recovers_as_a_call(fixture, tmp_path):
    import httpx
    store, core, rid = fixture
    class LostStart(LocalClient):
        def request(self, method, path, body=None, key=None, params=None):
            response = super().request(method, path, body, key, params)
            if path.endswith('/start'):
                raise httpx.ReadTimeout('synthetic start response lost')
            return response
    class NeverRun:
        calls = 0
        def run(self, context, cancel):
            self.calls += 1
            raise AssertionError('a lost start response is never permission to call')
    runtime = NeverRun()
    client = LostStart(store)
    journal = Journal(tmp_path / 'lost-start.sqlite')
    try:
        with pytest.raises(httpx.ReadTimeout):
            Worker(client, journal, runtime).step()
        assert runtime.calls == 0 and journal.status() == {'uncertain': 1}
        task = core.read('fixture-human', rid)['tasks'][0]
        assert task['execution']['state'] == 'started'
    finally:
        journal.close()
        client.http.close()


@pytest.mark.parametrize('bad', [[], {}, True, 1, 'unknown'], ids=['list', 'dict', 'bool', 'int', 'unknown'])
def test_invalid_execution_protocol_is_rejected_without_claim(fixture, bad):
    _, core, rid = fixture
    task = core.read('fixture-a', rid)['tasks'][0]
    with pytest.raises(Problem) as error:
        core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/claim', 'bad-protocol',
            {k: task[k] for k in ('generation', 'membership_revision')} | {'execution_protocol': bad})
    assert error.value.status == 422
    assert core.read('fixture-a', rid)['tasks'][0]['state'] == 'queued'


def test_call_start_has_no_automatic_http_retry():
    import httpx
    from talkabout.worker import APIClient, RetryPolicy
    requests = []
    def lost(request):
        requests.append(request.url.path)
        raise httpx.ReadTimeout('synthetic start response loss')
    client = APIClient('http://127.0.0.1:12345', 'synthetic-only', 'http://127.0.0.1:12345',
        transport=httpx.MockTransport(lost), retry_policy=RetryPolicy(attempts=3, base_seconds=0))
    try:
        with pytest.raises(httpx.ReadTimeout):
            client.request('POST', 'rooms/room/tasks/task/start', {}, 'start-once')
        assert len(requests) == 1
    finally:
        client.close()


def test_concurrent_call_start_commits_one_record_and_cannot_skip_it(fixture):
    from concurrent.futures import ThreadPoolExecutor
    _, core, rid = fixture
    task = claim_protocol(core, rid)
    path = 'tasks/' + task['id'] + '/start'
    def start(key):
        try:
            return core.mutation('fixture-a', rid, path, key, fence(task))['execute']
        except Problem as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(start, ['start-one', 'start-two']))
    assert outcomes.count(True) == 1 and outcomes.count('execution_already_started') == 1
    with pytest.raises(Problem, match='execution_already_started'):
        core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/skip', 'cannot-discard', fence(task))
    with pytest.raises(Problem) as wrong_actor:
        core.mutation('fixture-b', rid, path, 'other-actor', fence(task))
    assert wrong_actor.value.status == 404


def test_call_start_storage_failure_rolls_back_the_record(fixture):
    store, core, rid = fixture
    task = claim_protocol(core, rid)
    with store.transaction(rid, 'fixture-human') as (_, room):
        room['events'].extend([dict(room['events'][-1])] * (2048 - len(room['events'])))
    with pytest.raises(Problem, match='event_capacity'):
        core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/start', 'cannot-commit', fence(task))
    current = core.read('fixture-a', rid)['tasks'][0]
    assert current['state'] == 'leased' and 'execution' not in current


def test_retired_idempotency_response_does_not_remove_execution_fence(fixture):
    store, core, rid = fixture
    task = claim_protocol(core, rid)
    path = 'tasks/' + task['id'] + '/start'
    core.mutation('fixture-a', rid, path, 'start-once', fence(task))
    with store.transaction(rid, 'fixture-human') as (_, room):
        room['keys'].clear()
    with pytest.raises(Problem, match='execution_already_started'):
        core.mutation('fixture-a', rid, path, 'start-once', fence(task))


def test_session_rotation_rejects_old_start_and_does_not_clear_execution(fixture):
    import uuid
    store, core, rid = fixture
    session, replacement = str(uuid.uuid4()), str(uuid.uuid4())
    room = core.read('fixture-a', rid)
    core.participation('fixture-a', rid, 'join', {'session_id': session,
        'generation': room['generation'], 'membership_revision': room['membership_revision']})
    task = next(t for t in room['tasks'] if t['state'] == 'queued')
    claimed = core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/claim', 'bound-claim',
        {'generation': room['generation'], 'membership_revision': room['membership_revision'],
         'session_id': session, 'execution_protocol': 'server_start_v1'})
    path = 'tasks/' + task['id'] + '/start'
    body = fence(claimed) | {'session_id': session}
    core.mutation('fixture-a', rid, path, 'bound-start', body)
    core.participation('fixture-a', rid, 'join', {'session_id': replacement, 'previous_session_id': session,
        'generation': room['generation'], 'membership_revision': room['membership_revision']})
    with pytest.raises(Problem, match='stale_session'):
        core.mutation('fixture-a', rid, path, 'bound-start', body)
    with store.transaction(rid, 'fixture-human') as (_, current):
        current['tasks'][task['id']]['lease_expires_at'] = time.time() - 1
    core.tick('fixture-human', rid)
    assert core.read('fixture-a', rid)['tasks'][0]['state'] == 'uncertain'


def test_postgres_lost_journal_and_lease_expiry_keep_the_server_fence(postgres_fixture, tmp_path):
    test_lost_uncertain_journal_cannot_repeat_call_after_lease_expiry(postgres_fixture, tmp_path)


def test_postgres_call_start_and_result_replay(postgres_fixture):
    test_call_start_is_one_shot_and_completed_result_remains_deliverable(postgres_fixture)


def test_expired_run_budget_does_not_wait_sixteen_seconds_for_heartbeat(fixture, tmp_path, monkeypatch):
    import threading
    from talkabout.runtime import RunBudgetExceeded
    store, _, _ = fixture
    client = LocalClient(store)
    client.http.__enter__()
    entered, release = threading.Event(), threading.Event()
    heartbeat, timeouts = [], []
    request = client.request
    def hold_response(method, path, *args, **kwargs):
        value = request(method, path, *args, **kwargs)
        if path.endswith('/heartbeat'):
            heartbeat.append(threading.current_thread())
            entered.set()
            release.wait(3)
        return value
    client.request = hold_response
    join = threading.Thread.join
    def bounded_join(thread, timeout=None):
        if heartbeat and thread is heartbeat[0]:
            timeouts.append(timeout)
            assert timeout is not None and timeout <= .05, 'cleanup must honor the expired run budget'
        return join(thread, timeout)
    monkeypatch.setattr(threading.Thread, 'join', bounded_join)
    class EndsAtBudget:
        def run(self, context, cancel):
            assert entered.wait(2), 'the real synthetic heartbeat must be in flight'
            worker.run_deadline = time.monotonic() - 1
            return 'Synthetic response at an expired deadline'
    journal = Journal(tmp_path / 'heartbeat-budget.sqlite')
    try:
        # Preparation is outside this targeted cleanup scenario. The runtime
        # expires the real deadline only after the heartbeat is known in flight.
        worker = Worker(client, journal, EndsAtBudget(), heartbeat_interval=.01)
        with pytest.raises(RunBudgetExceeded):
            worker.step()
        assert timeouts and journal.status() == {'uncertain': 1}
        assert not any(path.endswith('/complete') for _, path in client.calls)
    finally:
        release.set()
        for thread in heartbeat: join(thread, timeout=2)
        journal.close()
        client.http.__exit__(None, None, None)


def test_postgres_expired_budget_does_not_wait_for_heartbeat(postgres_fixture, tmp_path, monkeypatch):
    test_expired_run_budget_does_not_wait_sixteen_seconds_for_heartbeat(postgres_fixture, tmp_path, monkeypatch)
