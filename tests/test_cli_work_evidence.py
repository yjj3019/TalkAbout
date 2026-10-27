"""The canonical CLI passes only same-invocation evidence through its runtime pool."""
import json
import sys
import threading

import pytest

from talkabout import cli
from talkabout.connector import ConnectionCheckError, RoomRuntimePool
from talkabout.runtime import WorkEvidence, bounded_call_with_evidence
from tests.test_continuity_cli import configured


CONTEXT = {'room_id': 'room-a', 'generation': 1, 'membership_revision': 1,
           'task_id': 'task-a', 'task_attempt': 1, 'purpose': 'discussion', 'topic': 'synthetic topic'}
EVIDENCE = WorkEvidence(1, 2, 1, 1)


class EvidenceRuntime:
    def __init__(self):
        self.calls = 0
        self.last_work_evidence = {'secret': 'not-same-turn-evidence'}
        self.last_outcome = 'not_started'

    def run(self, context, cancel):
        raise AssertionError('must use evidence-aware operation')

    def run_with_evidence(self, context, cancel):
        self.calls += 1
        self.last_outcome = 'completed'
        return 'synthetic evidence result', EVIDENCE

    def preflight(self): pass
    def shutdown(self): return True
    def set_deadline(self, deadline): pass


class LegacyRuntime:
    def __init__(self):
        self.calls = 0
        self.last_work_evidence = {'secret': 'must-not-leak'}

    def run(self, context, cancel):
        self.calls += 1
        return 'synthetic legacy result'

    def shutdown(self): return True
    def set_deadline(self, deadline): pass


class SessionJournal:
    def __init__(self):
        self.calls = []
    def begin(self, context, runtime):
        self.calls.append(('begin', context['task_id']))
        return {'session_id': 'session', 'thread_id': 'thread'}
    def complete(self, context, runtime):
        self.calls.append(('complete', context['task_id'], runtime.session_id, runtime.thread_id))
    def uncertain(self, context, runtime, **kwargs):
        self.calls.append(('uncertain', context['task_id']))
    def blocked(self, error):
        self.calls.append(('blocked', str(error)))


def test_pool_forwards_exact_turn_evidence_and_session_completion():
    runtime, journal = EvidenceRuntime(), SessionJournal()
    pool = RoomRuntimePool(lambda: runtime, session_journal=journal)
    result = bounded_call_with_evidence(pool, CONTEXT, threading.Event(), 2)
    assert result == ('synthetic evidence result', EVIDENCE)
    assert runtime.calls == 1 and pool.calls == 1
    assert journal.calls == [('begin', 'task-a'), ('complete', 'task-a', 'session', 'thread')]
    assert not pool.active
    pool.shutdown()


def test_pool_legacy_fallback_never_reuses_ambient_evidence():
    runtime = LegacyRuntime()
    pool = RoomRuntimePool(lambda: runtime)
    assert bounded_call_with_evidence(pool, CONTEXT, threading.Event(), 2) == ('synthetic legacy result', None)
    assert runtime.calls == 1 and pool.calls == 1
    pool.shutdown()


def test_pool_evidence_is_invocation_local_across_turns():
    class Varying(EvidenceRuntime):
        def run_with_evidence(self, context, cancel):
            self.calls += 1
            return context['task_id'], WorkEvidence(1, self.calls, 0, 0)
    runtime = Varying()
    pool = RoomRuntimePool(lambda: runtime)
    first = pool.run_with_evidence(CONTEXT, threading.Event())
    second = pool.run_with_evidence({**CONTEXT, 'task_id': 'task-b'}, threading.Event())
    assert first == ('task-a', WorkEvidence(1, 1, 0, 0))
    assert second == ('task-b', WorkEvidence(1, 2, 0, 0))
    assert runtime.calls == 2
    pool.shutdown()


def test_pool_failed_evidence_call_is_uncertain_and_never_retried():
    class Failed(EvidenceRuntime):
        def run_with_evidence(self, context, cancel):
            self.calls += 1
            raise RuntimeError('synthetic failure')
    runtime, journal = Failed(), SessionJournal()
    pool = RoomRuntimePool(lambda: runtime, session_journal=journal)
    with pytest.raises(RuntimeError, match='synthetic failure'):
        bounded_call_with_evidence(pool, CONTEXT, threading.Event(), 2)
    assert runtime.calls == 1 and pool.calls == 1
    assert journal.calls == [('begin', 'task-a'), ('uncertain', 'task-a')]
    assert not pool.active
    pool.shutdown()


def test_pool_session_lock_refuses_parallel_turn_without_second_call():
    entered, release = threading.Event(), threading.Event()
    class Slow(EvidenceRuntime):
        def run_with_evidence(self, context, cancel):
            self.calls += 1
            entered.set()
            assert release.wait(2)
            return 'synthetic', EVIDENCE
    runtime, journal = Slow(), SessionJournal()
    pool = RoomRuntimePool(lambda: runtime, session_journal=journal)
    result = []
    thread = threading.Thread(target=lambda: result.append(pool.run_with_evidence(CONTEXT, threading.Event())))
    thread.start()
    try:
        assert entered.wait(2)
        with pytest.raises(ConnectionCheckError, match='connector_session_busy'):
            pool.run_with_evidence(CONTEXT, threading.Event())
    finally:
        release.set()
        thread.join(2)
        pool.shutdown()
    assert result == [('synthetic', EVIDENCE)] and runtime.calls == 1


def test_cli_synthetic_wrapper_legacy_fallback(fixture, tmp_path, monkeypatch):
    config, _ = configured(fixture, tmp_path, monkeypatch)
    seen = []
    class Worker:
        def __init__(self, client, journal, runtime, **kwargs): self.runtime = runtime
        def step(self):
            seen.append(bounded_call_with_evidence(self.runtime, CONTEXT, threading.Event(), 2))
            return 'completed'
    monkeypatch.setattr(cli, 'Worker', Worker)
    monkeypatch.setattr(sys, 'argv', ['talkabout', 'run', '--config', str(config), '--synthetic', '--steps', '1'])
    cli.main()
    assert len(seen) == 1 and isinstance(seen[0][0], str) and seen[0][1] is None


def test_cli_wrapper_forwards_evidence_through_pool(fixture, tmp_path, monkeypatch):
    from talkabout import agent_runtime
    config, _ = configured(fixture, tmp_path, monkeypatch)
    seen, instances = [], []
    class Runtime(EvidenceRuntime):
        kind = 'codex'
        auth_mode = 'native'
        codex_transport = 'exec-json'
        runtime_version = 'synthetic'
        _verified_executable = ('synthetic-cli', 1, 1)
        _verified_account_subject = None
        thread_id = 'synthetic-thread'
        session_id = 'synthetic-session'
        def __init__(self, *args, **kwargs):
            super().__init__()
            instances.append(self)
        def probe(self): return {'available': True, 'reason': None}
        def run(self, context, cancel):
            self.calls += 1
            self.last_outcome = 'completed'
            return context.get('challenge', 'legacy')
    monkeypatch.setattr(agent_runtime, 'StructuredRuntime', Runtime)
    class Worker:
        def __init__(self, client, journal, runtime, **kwargs): self.runtime = runtime
        def step(self):
            seen.append(bounded_call_with_evidence(self.runtime, CONTEXT, threading.Event(), 2))
            return 'completed'
    monkeypatch.setattr(cli, 'Worker', Worker)
    monkeypatch.setattr(sys, 'argv', ['talkabout', 'run', '--config', str(config), '--runtime', 'codex',
                                    '--approve-external', '--steps', '1', '--max-model-calls', '2'])
    cli.main()
    assert seen == [('synthetic evidence result', EVIDENCE)]
    assert len(instances) == 2 and instances[1].calls == 1
