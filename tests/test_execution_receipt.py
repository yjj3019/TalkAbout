"""Execution receipts bind a reported provider session; they are not attestation."""
import hashlib
import uuid

import pytest

from talkabout.domain import Problem
from talkabout.worker import Journal, SyntheticRuntime, Worker
from conftest import LocalClient
from test_work_plan_dispatch import (claim, complete, dispatch, node, plan_task, prepare,
                                     ready, run)


def sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def finish(core, rid, task, *, text='Synthetic result', receipt=None, usage=None, token='fixture-a'):
    """Claim-adjacent completion with an optional reported execution receipt."""
    body = {k: task[k] for k in ('attempt', 'generation', 'membership_revision')}
    if task.get('execution_protocol') == 'server_start_v1':
        reservation = uuid.uuid4().hex + uuid.uuid4().hex
        admitted = core.mutation(token, rid, 'tasks/' + task['id'] + '/reservations',
                                 'reserve-' + task['id'], {**body, 'reservation_id': reservation})
        assert admitted.get('reserved') is True
        core.mutation(token, rid, 'tasks/' + task['id'] + '/start', 'start-' + task['id'],
                      {**body, 'reservation_id': reservation})
    result = {'client_message_id': 'result-' + task['id'], 'text': text,
              'reply_to': None, 'mentions': []}
    if receipt is not None:
        result['execution_receipt'] = receipt
    return core.mutation(token, rid, 'tasks/' + task['id'] + '/complete', 'finish-' + task['id'],
                         {**body, 'result': result})


def queued(fixture, connections=()):
    store, core, rid = fixture
    prepare(store, core, rid, [node('one')], 'fixture-a')
    dispatch(core, rid)
    if connections:
        ready(store, *connections)
    return store, core, rid, claim(core, rid)


def last_message(core, rid, token='fixture-human'):
    return core.read(token, rid)['messages'][-1]


def test_receipt_binds_session_and_text_digests(fixture):
    store, core, rid, task = queued(fixture)
    finish(core, rid, task, text='Provider answer one',
           receipt={'runtime': 'synthetic', 'session_id': 'thread-abc'})
    message = last_message(core, rid)
    assert message['execution_receipt'] == {
        'runtime': 'synthetic', 'session_sha256': sha256('thread-abc'),
        'text_sha256': sha256('Provider answer one')}
    raw = store.rooms[rid]['tasks'][task['id']]['_execution_receipts']
    assert len(raw) == 1 and raw[0]['session_id'] == 'thread-abc'
    assert raw[0]['attempt'] == 1 and raw[0]['reported_at'] > 0


def test_receipt_usage_is_bounded_and_recorded(fixture):
    store, core, rid, task = queued(fixture)
    finish(core, rid, task, receipt={'runtime': 'synthetic', 'session_id': 'session-1',
                                    'usage': {'input_tokens': 7, 'output_tokens': 3}})
    assert last_message(core, rid)['execution_receipt']['usage'] == {
        'input_tokens': 7, 'output_tokens': 3}


def test_receipt_runtime_must_match_authenticated_connection(fixture):
    store, core, rid, task = queued(fixture, connections=('fixture-a',))
    with pytest.raises(Problem, match='invalid_execution_receipt') as error:
        finish(core, rid, task, receipt={'runtime': 'codex', 'session_id': 'thread-abc'})
    assert error.value.status == 422
    assert store.rooms[rid]['tasks'][task['id']]['state'] == 'leased'


@pytest.mark.parametrize('receipt', [
    {'runtime': 'synthetic', 'session_id': ''},
    {'runtime': 'synthetic', 'session_id': 'x' * 257},
    {'runtime': 'synthetic', 'session_id': ' padded '},
    {'runtime': 'synthetic', 'session_id': 'line\nbreak'},
    {'runtime': 'synthetic'},
    {'runtime': 'synthetic', 'session_id': 'session-1', 'extra': True},
    {'runtime': 'synthetic', 'session_id': 'session-1', 'usage': {'input_tokens': True}},
    {'runtime': 'synthetic', 'session_id': 'session-1', 'usage': {'cached_tokens': 4}},
    {'runtime': 'synthetic', 'session_id': 'session-1', 'usage': {'output_tokens': -1}},
    {'runtime': 'synthetic', 'session_id': 'session-1', 'usage': {'output_tokens': 10 ** 9 + 1}},
    {'runtime': 'x' * 33, 'session_id': 'session-1'},
])
def test_malformed_receipts_are_rejected(fixture, receipt):
    store, core, rid, task = queued(fixture)
    with pytest.raises(Problem, match='invalid_execution_receipt') as error:
        finish(core, rid, task, receipt=receipt)
    assert error.value.status == 422


def test_legacy_report_without_receipt_still_completes(fixture):
    store, core, rid, task = queued(fixture)
    finish(core, rid, task)
    assert 'execution_receipt' not in last_message(core, rid)
    assert '_execution_receipts' not in store.rooms[rid]['tasks'][task['id']]


def test_receipt_capacity_is_bounded(fixture):
    store, core, rid, task = queued(fixture)
    store.rooms[rid]['tasks'][task['id']]['_execution_receipts'] = [
        {'runtime': 'synthetic', 'session_id': 'old-' + str(i)} for i in range(8)]
    with pytest.raises(Problem, match='execution_receipt_capacity') as error:
        finish(core, rid, task, receipt={'runtime': 'synthetic', 'session_id': 'session-1'})
    assert error.value.status == 429


def test_worker_forwards_runtime_receipt(fixture, tmp_path):
    store, core, rid = fixture
    class ReceiptRuntime(SyntheticRuntime):
        def execution_receipt(self):
            return {'runtime': 'synthetic', 'session_id': 'session-worker'}

    journal = Journal(tmp_path / 'journal.sqlite')
    try:
        worker = Worker(LocalClient(store), journal, ReceiptRuntime())
        for _ in range(4):
            if worker.step() == 'completed':
                break
        message = core.read('fixture-human', rid)['messages'][0]
        assert message['execution_receipt']['session_sha256'] == sha256('session-worker')
    finally:
        journal.close()


def test_worker_omits_receipt_without_runtime_capability(fixture, tmp_path):
    store, core, rid = fixture
    journal = Journal(tmp_path / 'journal.sqlite')
    try:
        worker = Worker(LocalClient(store), journal, SyntheticRuntime())
        for _ in range(4):
            if worker.step() == 'completed':
                break
        assert 'execution_receipt' not in core.read('fixture-human', rid)['messages'][0]
    finally:
        journal.close()


def test_postgres_receipt_is_stored_and_projected(postgres_fixture):
    """The receipt must survive a real PostgreSQL room transaction."""
    _, core, rid, task = queued(postgres_fixture)
    finish(core, rid, task, text='Postgres answer',
           receipt={'runtime': 'synthetic', 'session_id': 'thread-pg',
                    'usage': {'input_tokens': 2, 'output_tokens': 1}})
    receipt = last_message(core, rid)['execution_receipt']
    assert receipt['session_sha256'] == sha256('thread-pg')
    assert receipt['text_sha256'] == sha256('Postgres answer')
    assert receipt['usage'] == {'input_tokens': 2, 'output_tokens': 1}


def test_run_projection_stays_aggregate_only(fixture):
    store, core, rid, task = queued(fixture)
    finish(core, rid, task, receipt={'runtime': 'synthetic', 'session_id': 'thread-abc'})
    status = run(core, rid)
    assert 'thread-abc' not in str(status)
