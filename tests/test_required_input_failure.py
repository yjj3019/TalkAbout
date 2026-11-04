import copy
import json
import threading
import uuid
import httpx
import pytest
from conftest import LocalClient
from talkabout.collaboration import shared_context
from talkabout.domain import Problem
from talkabout.worker import Journal, Worker
from test_required_input_protocol import setup_child, opt_claim
from test_work_plan_dispatch import node, prepare, dispatch, claim, run


def fence(task):
    return {k: task[k] for k in ('attempt', 'generation', 'membership_revision')}


def test_failure_replay_and_descendants(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node('root', required_predecessors=[]), node('child', deps=['root'])], 'fixture-a')
    dispatch(core, rid)
    task = opt_claim(core, rid, next(t for t in core.read('fixture-a', rid)['tasks'] if t['state'] == 'queued'))
    path = 'tasks/' + task['id'] + '/input-unavailable'
    with pytest.raises(Problem):
        core.mutation('fixture-b', rid, path, 'other', fence(task))
    for field in fence(task):
        with pytest.raises(Problem):
            core.mutation('fixture-a', rid, path, field, {**fence(task), field: task[field] + 1})
    result = core.mutation('fixture-a', rid, path, 'success', fence(task))
    assert result['state'] == 'failed'
    assert core.mutation('fixture-a', rid, path, 'success', fence(task)) == result
    assert store.rooms[rid]['tasks'][task['id']]['_failure_reason'] == 'input_unavailable'
    assert [(n['state'], n['failure']) for n in run(core, rid)['nodes']] == [('failed', 'input_unavailable'), ('blocked', 'dependency_failed')]


def test_old_plan_and_legacy_reservation_rejected(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node('legacy')], 'fixture-a')
    dispatch(core, rid)
    task = claim(core, rid)
    path = 'tasks/' + task['id'] + '/input-unavailable'
    with pytest.raises(Problem, match='input_failure_not_allowed'):
        core.mutation('fixture-a', rid, path, 'legacy', fence(task))
    raw = store.rooms[rid]['tasks'][task['id']]
    raw['_plan']['required_predecessors'] = []
    raw['_reservations'] = [{'attempt': task['attempt'], 'state': 'reserved'}]
    with pytest.raises(Problem, match='execution_outcome_uncertain'):
        core.mutation('fixture-a', rid, path, 'reserved', fence(task))


def test_race_start_vs_report(fixture):
    store, core, rid, queued = setup_child(fixture, [])
    task = opt_claim(core, rid, queued)
    path = 'tasks/' + task['id']
    body = {**fence(task), 'reservation_id': uuid.uuid4().hex * 2}
    core.mutation('fixture-a', rid, path + '/reservations', 'reserve', body)
    room = core.read('fixture-a', rid)
    projection = next(t for t in room['tasks'] if t['id'] == task['id'])
    manifest = shared_context(room, projection)['input_manifest']
    barrier, results = threading.Barrier(2), []
    def invoke(suffix, key, payload):
        barrier.wait()
        try:
            results.append(core.mutation('fixture-a', rid, path + suffix, key, payload))
        except Problem:
            results.append(None)
    threads = [threading.Thread(target=invoke, args=('/start', 'start', {**body, 'input_manifest': manifest})),
               threading.Thread(target=invoke, args=('/input-unavailable', 'failure', fence(task)))]
    for t in threads: t.start()
    for t in threads: t.join()
    assert sum(value is not None for value in results) == 1
    raw = store.rooms[rid]['tasks'][task['id']]
    assert not (raw['state'] == 'failed' and raw['_reservations'][0]['state'] == 'started')


def test_postgres_start_vs_failure_race_is_serialized(postgres_fixture):
    store, core, rid, queued = setup_child(postgres_fixture)
    task = opt_claim(core, rid, queued)
    room = core.read('fixture-a', rid)
    projection = next(t for t in room['tasks'] if t['id'] == task['id'])
    manifest = shared_context(room, projection)['input_manifest']
    body = {**fence(task), 'reservation_id': uuid.uuid4().hex * 2}
    path = 'tasks/' + task['id']
    core.mutation('fixture-a', rid, path + '/reservations', 'race-reserve', body)
    barrier, outcome = threading.Barrier(2), {}

    def invoke(label, suffix, key, payload):
        barrier.wait()
        try:
            outcome[label] = core.mutation('fixture-a', rid, path + suffix, key, payload)
        except Problem:
            outcome[label] = None

    threads = [threading.Thread(target=invoke, args=('start', '/start', 'race-start',
                                                     {**body, 'input_manifest': manifest})),
               threading.Thread(target=invoke, args=('failure', '/input-unavailable', 'race-failure',
                                                     fence(task)))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    final = next(t for t in core.read('fixture-a', rid)['tasks'] if t['id'] == task['id'])
    # The room row lock serializes the two transitions; exactly one commits and a
    # failed task can never also carry a started reservation.
    assert sum(value is not None for value in outcome.values()) == 1
    if final['state'] == 'failed':
        assert outcome['failure'] is not None and outcome['start'] is None
    else:
        assert final['state'] == 'leased'
        assert outcome['start'] is not None and outcome['failure'] is None


def test_worker_report_failure_is_unknown_and_never_runs(fixture, tmp_path):
    store, core, rid, task = setup_child(fixture)
    client = LocalClient(store)
    original, calls = client.request, []
    def request(method, path, body=None, key=None, params=None):
        if path.endswith('/input-unavailable'):
            calls.append((body, key))
            raise OSError('offline')
        response = original(method, path, body, key, params)
        if method == 'GET' and path == 'agents/me/events':
            response['events'] = [e for e in response['events'] if e.get('type') != 'task.queued' or e.get('task_id') == task['id']]
        if method == 'GET' and path == f'rooms/{rid}':
            response = copy.deepcopy(response)
            for item in response['tasks']:
                if item['id'] == task['id'] and item['state'] == 'leased':
                    item['plan_node']['required_predecessor_message_ids'] = None
        return response
    client.request = request
    class Runtime:
        def run(self, *args):
            raise AssertionError('provider called')
    journal = Journal(tmp_path / 'journal.sqlite')
    try:
        assert Worker(client, journal, Runtime()).step() == 'input_failure_report_unknown'
        row = journal.db.execute('SELECT state,payload FROM queue WHERE id=?', (task['id'],)).fetchone()
        payload = json.loads(row[1])
        assert row[0] == 'failed' and payload['error_code'] == 'required_input_unavailable'
        assert payload['input_failure_report'] == 'unknown'
        # Best-effort bounded retry: three attempts, one idempotency key, no runtime.
        assert len(calls) == 3
        assert {key for _, key in calls} == {payload['input_failure_key']}
        assert not any(path.endswith(('/reservations', '/start')) for _, path in client.calls)
    finally:
        journal.close()
        client.http.close()


def test_worker_input_failure_report_stops_on_4xx(fixture, tmp_path):
    import httpx
    store, core, rid, task = setup_child(fixture)
    client = LocalClient(store)
    original, calls = client.request, []

    def request(method, path, body=None, key=None, params=None):
        if path.endswith('/input-unavailable'):
            calls.append(key)
            request_ = httpx.Request('POST', 'http://test/' + path)
            raise httpx.HTTPStatusError('rejected', request=request_,
                                        response=httpx.Response(409, request=request_))
        response = original(method, path, body, key, params)
        if method == 'GET' and path == 'agents/me/events':
            response['events'] = [e for e in response['events']
                                  if e.get('type') != 'task.queued' or e.get('task_id') == task['id']]
        if method == 'GET' and path == f'rooms/{rid}':
            response = copy.deepcopy(response)
            for item in response['tasks']:
                if item['id'] == task['id'] and item['state'] == 'leased':
                    item['plan_node']['required_predecessor_message_ids'] = None
        return response

    client.request = request
    journal = Journal(tmp_path / 'journal.sqlite')

    class Runtime:
        def run(self, *args):
            raise AssertionError('provider called')

    try:
        assert Worker(client, journal, Runtime()).step() == 'input_failure_report_unknown'
        assert len(calls) == 1
        row = journal.db.execute('SELECT state,payload FROM queue WHERE id=?', (task['id'],)).fetchone()
        payload = json.loads(row[1])
        assert row[0] == 'failed' and payload['input_failure_report'] == 'unknown'
        assert calls[0] == payload['input_failure_key']
        assert not any(path.endswith(('/reservations', '/start')) for _, path in client.calls)
    finally:
        journal.close()
        client.http.close()


def test_late_report_on_expired_lease_fails_node(fixture):
    store, core, rid, queued = setup_child(fixture)
    task = opt_claim(core, rid, queued)
    raw = store.rooms[rid]['tasks'][task['id']]
    raw['lease_expires_at'] = 0
    core.tick('fixture-a', rid)
    assert store.rooms[rid]['tasks'][task['id']]['state'] == 'expired'
    result = core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/input-unavailable',
                           'late-failure', fence(task))
    assert result['state'] == 'failed'
    assert store.rooms[rid]['tasks'][task['id']]['_failure_reason'] == 'input_unavailable'
    node = next(n for n in run(core, rid)['nodes'] if n['id'] == 'child')
    assert (node['state'], node['failure']) == ('failed', 'input_unavailable')


def test_worker_treats_definitive_claim_rejection_as_local_failure(fixture, tmp_path):
    store, core, rid, task = setup_child(fixture)
    client = LocalClient(store)
    original = client.request

    def request(method, path, body=None, key=None, params=None):
        if method == 'POST' and path.endswith('/claim'):
            raise httpx.HTTPStatusError(
                'rejected', request=httpx.Request('POST', 'http://fixture'),
                response=httpx.Response(409, json={'code': 'required_input_unavailable'}))
        response = original(method, path, body, key, params)
        if method == 'GET' and path == 'agents/me/events':
            response['events'] = [event for event in response['events']
                                  if event.get('type') != 'task.queued' or event.get('task_id') == task['id']]
        return response

    client.request = request

    class Runtime:
        def run(self, *args):
            raise AssertionError('provider called')

    journal = Journal(tmp_path / 'journal.sqlite')
    try:
        assert Worker(client, journal, Runtime()).step() == 'required_input_unavailable'
        state, payload = journal.db.execute('SELECT state,payload FROM queue WHERE id=?', (task['id'],)).fetchone()
        assert state == 'failed'
        payload = json.loads(payload)
        assert payload['error_code'] == 'required_input_unavailable'
        assert payload['input_failure_report'] == 'server_rejected_claim'
        assert not any(path.endswith(('/reservations', '/start', '/input-unavailable'))
                       for _, path in client.calls)
    finally:
        journal.close()
        client.http.close()


def test_late_report_rejected_after_started_reservation(fixture):
    store, core, rid, queued = setup_child(fixture)
    task = opt_claim(core, rid, queued)
    room = core.read('fixture-a', rid)
    projection = next(t for t in room['tasks'] if t['id'] == task['id'])
    manifest = shared_context(room, projection)['input_manifest']
    body = {**fence(task), 'reservation_id': uuid.uuid4().hex * 2}
    path = 'tasks/' + task['id']
    core.mutation('fixture-a', rid, path + '/reservations', 'late-reserve', body)
    core.mutation('fixture-a', rid, path + '/start', 'late-start',
                  {**body, 'input_manifest': manifest})
    store.rooms[rid]['tasks'][task['id']]['lease_expires_at'] = 0
    with pytest.raises(Problem, match='stale_attempt'):
        core.mutation('fixture-a', rid, path + '/input-unavailable', 'late-started', fence(task))
    assert store.rooms[rid]['tasks'][task['id']]['state'] != 'failed'
