"""Synthetic per-invocation work evidence: no provider or external process."""
import json
import sys
import threading

import pytest

from talkabout import agent_runtime as ar
from talkabout.adapters import AdapterError
from talkabout.runtime import WorkEvidence, RuntimeTimeout, bounded_call, bounded_call_with_evidence
from tests.test_work_profile_events import command, change, event, stream, workspace


class FakeTransport:
    def __init__(self, events, close_ok=True):
        self.events = list(events)
        self.close_ok = close_ok
        self.closed = False
    def send_text(self, *_): pass
    def receive(self, *_):
        if not self.events:
            raise AdapterError('runtime_outcome_unknown')
        return self.events.pop(0)
    def wait_success(self, *_): pass
    def check(self, cancel, deadline):
        if cancel.is_set(): raise AdapterError('runtime_cancelled')
    def close(self):
        self.closed = True
        return self.close_ok


@pytest.fixture
def structured(monkeypatch, workspace):
    monkeypatch.setattr(ar.StructuredRuntime, '_ensure_preflight', lambda self, cancel: None)
    monkeypatch.setattr(ar.StructuredRuntime, '_check_codex_exec_auth', lambda *args: None)
    monkeypatch.setattr(ar.StructuredRuntime, 'probe', lambda self: {'available': True, 'reason': None})
    monkeypatch.setattr(ar.StructuredRuntime, 'command', lambda self: ['synthetic'])
    def make(events, close_ok=True, work=True):
        transport = FakeTransport(events, close_ok)
        monkeypatch.setattr(ar, '_JSONLTransport', lambda *args, **kwargs: transport)
        value = ar.StructuredRuntime('codex', executable=sys.executable, work_profile=work,
            work_workspace=workspace if work else None)
        return value, transport
    return make


CTX = {'room_id': 'room', 'topic': 'synthetic', 'messages': []}


def test_aggregate_unique_terminal_items_and_private_output(structured):
    events = [event('started', command()), event('updated', command()),
        event('completed', command('completed', 9)),
        event('completed', command('declined', None, id='declined')),
        event('completed', change('failed'))]
    value, transport = structured(stream(events))
    text, evidence = bounded_call_with_evidence(value, CTX, threading.Event(), 5)
    assert text == 'done' and transport.closed
    assert evidence == WorkEvidence(1, 2, 1, 2)
    assert set(evidence._fields) == {'version', 'command_items', 'file_change_items', 'failed_items'}
    assert 'SECRET' not in json.dumps(evidence._asdict())
    with pytest.raises(AttributeError): evidence.version = 2


def test_zero_work_is_evidence_but_text_profile_has_none(structured):
    value, _ = structured(stream([]))
    assert bounded_call_with_evidence(value, CTX, threading.Event(), 5) == ('done', WorkEvidence(1, 0, 0, 0))
    value, _ = structured(stream([]), work=False)
    assert bounded_call_with_evidence(value, CTX, threading.Event(), 5) == ('done', None)


def test_cleanup_failure_and_turn_failure_produce_no_result(structured):
    value, transport = structured(stream([event('completed', change())]), close_ok=False)
    with pytest.raises(AdapterError, match='runtime_cleanup_unconfirmed'):
        bounded_call_with_evidence(value, CTX, threading.Event(), 5)
    assert transport.closed
    value, _ = structured([{'type': 'thread.started', 'thread_id': 't'}, {'type': 'turn.started'},
                           event('completed', change()), {'type': 'turn.failed'}])
    with pytest.raises(AdapterError):
        bounded_call_with_evidence(value, CTX, threading.Event(), 5)


def test_cancel_during_cleanup_has_no_evidence(structured):
    value, transport = structured(stream([event('completed', change())]))
    cancel = threading.Event()
    def close():
        transport.closed = True
        cancel.set()
        return True
    transport.close = close
    with pytest.raises(AdapterError, match='runtime_cancelled'):
        value.run_with_evidence(CTX, cancel)
    assert transport.closed


def test_summary_refuses_nonterminal_or_unknown_items(monkeypatch, structured):
    value, _ = structured(stream([]))
    monkeypatch.setattr(value, '_run', lambda *_: (setattr(value, 'last_work_evidence',
        {'outcomes': [{'kind': 'command_execution', 'status': 'in_progress'}]}) or 'text'))
    with pytest.raises(AdapterError, match='runtime_invalid_output'):
        bounded_call_with_evidence(value, CTX, threading.Event(), 5)


def test_evidence_is_bound_before_lock_released(monkeypatch, structured):
    value, _ = structured(stream([]))
    entered = threading.Event()
    proceed = threading.Event()
    class SlowSummary(dict):
        def get(self, key, default=None):
            entered.set()
            assert proceed.wait(3)
            return super().get(key, default)
    def run(context, cancel):
        value.last_work_evidence = SlowSummary({'outcomes': [{'kind': 'command_execution', 'status': 'completed'}]})
        return 'first'
    monkeypatch.setattr(value, '_run', run)
    result = []
    thread = threading.Thread(target=lambda: result.append(value.run_with_evidence(CTX, threading.Event())))
    thread.start()
    assert entered.wait(3)
    with pytest.raises(AdapterError, match='runtime_busy'):
        value.run(CTX, threading.Event())
    proceed.set()
    thread.join(3)
    assert not thread.is_alive() and result == [('first', WorkEvidence(1, 1, 0, 0))]


def test_legacy_fallback_never_reads_ambient_evidence():
    class Legacy:
        last_work_evidence = {'outcomes': [{'kind': 'command_execution', 'status': 'completed'}]}
        def run(self, context, cancel): return 'text'
    assert bounded_call_with_evidence(Legacy(), {}, threading.Event(), 1) == ('text', None)
    assert bounded_call(Legacy(), {}, threading.Event(), 1) == 'text'


def test_evidence_subclass_cannot_override_serialization():
    class Hostile(WorkEvidence):
        def _asdict(self): return {'secret': 'SYNTHETIC-SECRET'}
    class Bad:
        def run_with_evidence(self, context, cancel): return 'text', Hostile(1, 0, 0, 0)
    with pytest.raises(ValueError, match='invalid_runtime_evidence'):
        bounded_call_with_evidence(Bad(), {}, threading.Event(), 1)


def test_malformed_evidence_fails_closed():
    class Bad:
        def run_with_evidence(self, context, cancel): return 'text', {'version': 1}
    with pytest.raises(ValueError, match='invalid_runtime_evidence'):
        bounded_call_with_evidence(Bad(), {}, threading.Event(), 1)


def test_concurrent_bounded_calls_do_not_cross_contaminate(monkeypatch, structured):
    value, _ = structured(stream([]))
    entered, proceed = threading.Event(), threading.Event()
    def run(context, cancel):
        entered.set()
        assert proceed.wait(3)
        value.last_work_evidence = {'outcomes': [{'kind': 'file_change', 'status': 'failed'}]}
        return 'first'
    monkeypatch.setattr(value, '_run', run)
    result = []
    thread = threading.Thread(target=lambda: result.append(
        bounded_call_with_evidence(value, CTX, threading.Event(), 4)))
    thread.start()
    assert entered.wait(3)
    with pytest.raises(AdapterError, match='runtime_busy'):
        bounded_call_with_evidence(value, CTX, threading.Event(), 2)
    proceed.set()
    thread.join(3)
    assert not thread.is_alive() and result == [('first', WorkEvidence(1, 0, 1, 1))]


def test_returned_snapshot_survives_subsequent_turn(monkeypatch, structured):
    value, _ = structured(stream([]))
    def run(context, cancel):
        value.last_work_evidence = {'outcomes': context['outcomes']}
        return context['text']
    monkeypatch.setattr(value, '_run', run)
    first = bounded_call_with_evidence(value, {'text': 'first', 'outcomes': [
        {'kind': 'command_execution', 'status': 'failed'}]}, threading.Event(), 1)
    second = bounded_call_with_evidence(value, {'text': 'second', 'outcomes': [
        {'kind': 'file_change', 'status': 'completed'}]}, threading.Event(), 1)
    assert first == ('first', WorkEvidence(1, 1, 0, 1))
    assert second == ('second', WorkEvidence(1, 0, 1, 0))


def test_timeout_and_cancellation_never_return_evidence():
    class Slow:
        def run_with_evidence(self, context, cancel):
            cancel.wait(2)
            return 'too late', WorkEvidence(1, 1, 0, 0)
        def shutdown(self): return True
    with pytest.raises(RuntimeTimeout):
        bounded_call_with_evidence(Slow(), {}, threading.Event(), .03)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RuntimeTimeout):
        bounded_call_with_evidence(Slow(), {}, cancel, 1)
