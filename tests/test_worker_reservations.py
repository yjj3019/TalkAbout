"""R2: the worker asks the server before a paid call and reports what happened.

Every case here is about the same rule: `execute:true` is the only permission to
call the runtime, and a started call with no recorded outcome is `uncertain`,
never free and never retried.
"""
import httpx
import pytest

from talkabout.worker import Journal, SyntheticRuntime, Worker, reservation_id
from conftest import LocalClient, control


class Counted(SyntheticRuntime):
    calls = 0

    def run(self, context, cancel):
        self.calls += 1
        return super().run(context, cancel)


def reservations_of(store, rid):
    tasks = store.rooms[rid]['tasks']
    return next(iter(tasks.values())).get('_reservations', [])


def test_the_worker_calls_the_runtime_only_after_an_execute_true(fixture, tmp_path):
    store, core, rid = fixture
    journal = Journal(tmp_path / 'granted.sqlite')
    runtime = Counted()
    worker = Worker(LocalClient(store), journal, runtime)
    assert worker.step() == 'completed' and runtime.calls == 1
    stored = reservations_of(store, rid)
    assert len(stored) == 1 and stored[0]['state'] == 'completed'
    assert core.read('fixture-human', rid)['messages']
    journal.close()


def test_a_refused_reservation_never_reaches_the_runtime(fixture, tmp_path):
    store, core, rid = fixture

    class Refusing(LocalClient):
        def request(self, method, path, body=None, key=None, params=None):
            if method == 'POST' and path.endswith('/reservations'):
                return {'execute': False, 'reason': 'reservation_already_started'}
            return super().request(method, path, body, key, params)

    journal = Journal(tmp_path / 'refused.sqlite')
    runtime = Counted()
    worker = Worker(Refusing(store), journal, runtime)
    assert worker.step() == 'reservation_refused' and runtime.calls == 0
    assert core.read('fixture-human', rid)['messages'] == []
    assert journal.status() == {'reservation_refused': 1}
    # Not a pending row: a refused reservation must not loop forever.
    assert worker.step() == 'waiting'
    journal.close()


def test_a_lost_reservation_response_cannot_become_a_second_call(fixture, tmp_path):
    """The first response is lost after the server granted: the retry must replay."""
    store, core, rid = fixture

    class LostResponse(LocalClient):
        def request(self, method, path, body=None, key=None, params=None):
            result = super().request(method, path, body, key, params)
            if method == 'POST' and path.endswith('/reservations'):
                raise httpx.ReadTimeout('lost after the server answered')
            return result

    journal = Journal(tmp_path / 'lost.sqlite')
    runtime = Counted()
    worker = Worker(LostResponse(store), journal, runtime)
    with pytest.raises(httpx.ReadTimeout):
        worker.step()
    assert runtime.calls == 0
    journal.close()
    # A later run resumes the same attempt with the same deterministic id.
    recovered = Journal(tmp_path / 'lost.sqlite')
    restarted = Worker(LocalClient(store), recovered, runtime)
    assert restarted.step() == 'uncertain' and runtime.calls == 0
    assert next(iter(store.rooms[rid]['tasks'].values()))['state'] == 'uncertain'
    assert recovered.status() == {'uncertain': 1}
    recovered.close()


def test_a_failed_call_reports_uncertain_and_the_server_blocks_retry(fixture, tmp_path):
    store, core, rid = fixture

    class FailedModel(SyntheticRuntime):
        def run(self, context, cancel):
            raise ValueError('ambiguous provider failure')

    journal = Journal(tmp_path / 'failed.sqlite')
    worker = Worker(LocalClient(store), journal, FailedModel())
    with pytest.raises(ValueError):
        worker.step()
    stored = reservations_of(store, rid)
    assert len(stored) == 1 and stored[0]['state'] == 'uncertain'
    assert next(iter(store.rooms[rid]['tasks'].values()))['state'] == 'uncertain'
    assert worker.step() == 'waiting'
    journal.close()


def test_an_undeliverable_outcome_stays_queued_until_the_server_takes_it(fixture, tmp_path):
    store, core, rid = fixture
    dropped = []

    class FlakyOutcome(LocalClient):
        def request(self, method, path, body=None, key=None, params=None):
            if '/outcome' in path and len(dropped) < 1:
                dropped.append(path)
                raise httpx.ConnectError('fixture transport failure')
            return super().request(method, path, body, key, params)

    journal = Journal(tmp_path / 'flaky.sqlite')
    worker = Worker(FlakyOutcome(store), journal, Counted())
    assert worker.step() == 'completed'
    assert journal.outcomes() and dropped, 'the failed report must stay durable'
    # A later step drains the debt before claiming anything new.
    assert worker.step() == 'waiting'
    assert not journal.outcomes()
    stored = reservations_of(store, rid)
    assert len(stored) == 1 and stored[0]['state'] == 'completed'
    journal.close()


def test_reservation_ids_are_deterministic_and_attempt_scoped():
    first = reservation_id('room', 'task', 1, 2, 3)
    assert first == reservation_id('room', 'task', 1, 2, 3)
    assert len(first) == 64 and all(char in '0123456789abcdef' for char in first)
    assert first != reservation_id('room', 'task', 2, 2, 3)
    assert first != reservation_id('other-room', 'task', 1, 2, 3)
