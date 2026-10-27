"""Worker persists only same-turn terminal aggregates; synthetic runtimes only."""
import copy
import json
import threading

import httpx
import pytest

from conftest import LocalClient
from talkabout import worker as worker_module
from talkabout.runtime import WorkEvidence
from talkabout.worker import Journal, SyntheticRuntime, Worker


EVIDENCE = {'version': 1, 'command_items': 2, 'file_change_items': 1, 'failed_items': 1}


class CompletedRuntime:
    def __init__(self):
        self.calls = 0

    def run_with_evidence(self, context, cancel):
        self.calls += 1
        return 'Synthetic completed work', WorkEvidence(**EVIDENCE)

    def run(self, context, cancel):
        raise AssertionError('evidence-aware call must not use legacy path')


class LegacyRuntime(SyntheticRuntime):
    calls = 0

    def run(self, context, cancel):
        self.calls += 1
        return super().run(context, cancel)


class Capture(LocalClient):
    def __init__(self, store):
        super().__init__(store)
        self.completions = []

    def request(self, method, path, body=None, key=None, params=None):
        if method == 'POST' and path.endswith('/complete'):
            self.completions.append((copy.deepcopy(body), key))
        return super().request(method, path, body, key, params)


def test_completed_evidence_and_outcome_intent_are_durable_before_report(fixture, tmp_path):
    store, core, rid = fixture
    journal = Journal(tmp_path / 'worker.sqlite')
    runtime = CompletedRuntime()

    class Inspect(Capture):
        def request(self, method, path, body=None, key=None, params=None):
            if path.endswith('/outcome'):
                tid, state, payload = journal.next()
                assert state == 'result_saved'
                assert payload['complete']['result']['work_evidence'] == EVIDENCE
                assert journal.outcomes()[0][2:] == (tid, 'completed')
            return super().request(method, path, body, key, params)

    client = Inspect(store)
    try:
        assert Worker(client, journal, runtime).step() == 'completed'
        assert runtime.calls == 1
        assert client.completions[0][0]['result']['work_evidence'] == EVIDENCE
        message = core.read('fixture-human', rid)['messages'][0]
        assert message['work_evidence'] == {**EVIDENCE, 'source': 'agent_reported_unverified'}
        assert journal.status() == {'done': 1}
    finally:
        journal.close()
        client.http.close()


def test_complete_response_loss_replays_identical_persisted_evidence_without_runtime_retry(fixture, tmp_path):
    store, core, rid = fixture
    path = tmp_path / 'worker.sqlite'
    runtime = CompletedRuntime()
    journal = Journal(path)

    class LoseComplete(Capture):
        def request(self, method, path, body=None, key=None, params=None):
            result = super().request(method, path, body, key, params)
            if method == 'POST' and path.endswith('/complete'):
                raise httpx.ReadTimeout('synthetic response lost after commit')
            return result

    first = LoseComplete(store)
    try:
        with pytest.raises(httpx.ReadTimeout):
            Worker(first, journal, runtime).step()
        tid, state, payload = journal.next()
        assert state == 'result_saved'
        assert payload['complete']['result']['work_evidence'] == EVIDENCE
        sent, key = first.completions[0]
        assert sent == payload['complete'] and key == payload['complete_key']
        assert runtime.calls == 1
    finally:
        journal.close()
        first.http.close()
    recovered = Journal(path)
    client = Capture(store)
    try:
        assert Worker(client, recovered, runtime).step(saved_result_only=True) == 'completed'
        assert client.completions == [(sent, key)]
        assert runtime.calls == 1 and recovered.status() == {'done': 1}
        messages = core.read('fixture-human', rid)['messages']
        assert len(messages) == 1
        assert messages[0]['work_evidence'] == {**EVIDENCE, 'source': 'agent_reported_unverified'}
    finally:
        recovered.close()
        client.http.close()


def test_no_evidence_legacy_runtime_uses_existing_monkeypatch_boundary(fixture, tmp_path, monkeypatch):
    store, core, rid = fixture
    runtime, observed = LegacyRuntime(), []
    call = worker_module.bounded_call
    def legacy(*args):
        observed.append(args[0])
        return call(*args)
    monkeypatch.setattr(worker_module, 'bounded_call', legacy)
    journal, client = Journal(tmp_path / 'worker.sqlite'), Capture(store)
    try:
        assert Worker(client, journal, runtime).step() == 'completed'
        assert observed == [runtime] and runtime.calls == 1
        assert 'work_evidence' not in client.completions[0][0]['result']
        assert 'work_evidence' not in core.read('fixture-human', rid)['messages'][0]
    finally:
        journal.close()
        client.http.close()


def test_invalid_or_failed_runtime_evidence_never_persists_or_submits(fixture, tmp_path):
    store, core, rid = fixture
    class Invalid:
        def run_with_evidence(self, context, cancel):
            # Arbitrary objects or secret-bearing dicts cannot become evidence.
            return 'Synthetic', {'version': 1, 'command': 'SYNTHETIC-SECRET'}
    journal, client = Journal(tmp_path / 'worker.sqlite'), Capture(store)
    try:
        with pytest.raises(ValueError, match='invalid_runtime_evidence'):
            Worker(client, journal, Invalid()).step()
        assert journal.status() == {'uncertain': 1}
        assert client.completions == [] and core.read('fixture-human', rid)['messages'] == []
        row = journal.db.execute('SELECT payload FROM queue').fetchone()[0]
        assert 'SYNTHETIC-SECRET' not in row and 'work_evidence' not in row
    finally:
        journal.close()
        client.http.close()


def test_atomic_save_failure_never_persists_evidence_as_uncertain(fixture, tmp_path, monkeypatch):
    store, core, rid = fixture
    runtime = CompletedRuntime()
    journal, client = Journal(tmp_path / 'worker.sqlite'), Capture(store)
    original_queue = journal._queue_outcome
    def queue(reservation, room_id, task_id, state):
        if state == 'completed':
            journal.db.execute('INSERT INTO reservation_outcomes VALUES(?,?,?,?,?)',
                               (reservation, room_id, task_id, state, 1))
            raise ValueError('synthetic atomic failure')
        return original_queue(reservation, room_id, task_id, state)
    monkeypatch.setattr(journal, '_queue_outcome', queue)
    try:
        with pytest.raises(ValueError, match='synthetic atomic failure'):
            Worker(client, journal, runtime).step()
        assert runtime.calls == 1 and journal.status() == {'call_started': 1}
        row = json.loads(journal.db.execute('SELECT payload FROM queue').fetchone()[0])
        assert 'complete' not in row and 'work_evidence' not in json.dumps(row)
        assert client.completions == [] and core.read('fixture-human', rid)['messages'] == []
        assert all(outcome[3] != 'completed' for outcome in journal.outcomes())
    finally:
        journal.close()
        client.http.close()


def test_ambiguous_commit_preserves_result_for_recovery(fixture, tmp_path, monkeypatch):
    store, core, rid = fixture
    runtime = CompletedRuntime()
    journal, client = Journal(tmp_path / 'worker.sqlite'), Capture(store)
    original = journal.save_result
    def saved_then_error(*args):
        original(*args)
        raise OSError('synthetic post-commit failure')
    monkeypatch.setattr(journal, 'save_result', saved_then_error)
    try:
        with pytest.raises(OSError, match='post-commit'):
            Worker(client, journal, runtime).step()
        assert journal.status() == {'result_saved': 1}
        assert journal.next()[2]['complete']['result']['work_evidence'] == EVIDENCE
        assert Worker(client, journal, runtime).step(saved_result_only=True) == 'completed'
        assert runtime.calls == 1
        assert core.read('fixture-human', rid)['messages'][0]['work_evidence']['source'] == 'agent_reported_unverified'
    finally:
        journal.close()
        client.http.close()


def test_timeout_does_not_persist_late_completed_evidence(fixture, tmp_path):
    store, core, rid = fixture
    release, finished = threading.Event(), threading.Event()
    class Slow:
        def run_with_evidence(self, context, cancel):
            try:
                release.wait(3)
                return 'Synthetic late result', WorkEvidence(**EVIDENCE)
            finally:
                finished.set()
    journal, client = Journal(tmp_path / 'worker.sqlite'), Capture(store)
    try:
        assert Worker(client, journal, Slow(), runtime_timeout=.03).step() == 'runtime_timeout'
        assert journal.status() == {'uncertain': 1}
        release.set()
        assert finished.wait(2)
        assert client.completions == [] and core.read('fixture-human', rid)['messages'] == []
        row = journal.db.execute('SELECT payload FROM queue').fetchone()[0]
        assert 'work_evidence' not in row and 'Synthetic late result' not in row
        assert Worker(client, journal, Slow()).step() == 'waiting'
    finally:
        release.set()
        journal.close()
        client.http.close()
