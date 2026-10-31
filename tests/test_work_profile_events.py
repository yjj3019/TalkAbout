"""Synthetic Codex work events: observations, never preventive sandbox evidence."""
import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from talkabout import agent_runtime as ar
from talkabout.adapters import AdapterError


@pytest.fixture
def workspace():
    package = Path(__file__).resolve().parents[1]
    for base in (Path(tempfile.gettempdir()), package.parent):
        try:
            path = Path(tempfile.mkdtemp(prefix='talkabout-work-events-', dir=base))
        except OSError:
            continue
        if package not in path.resolve().parents:
            try:
                yield str(path)
            finally:
                shutil.rmtree(path)
            return
        shutil.rmtree(path)
    pytest.skip('no writable directory outside the platform tree')


class Transport:
    def __init__(self, events): self.events = list(events)
    def send_text(self, *args): pass
    def receive(self, *args):
        if not self.events: raise AssertionError('unexpected receive')
        return self.events.pop(0)
    def wait_success(self, *args): return True


def command(status='in_progress', exit_code=None, **updates):
    return {'id': 'cmd', 'type': 'command_execution', 'command': 'SECRET-command',
            'aggregated_output': 'SECRET-output', 'status': status, 'exit_code': exit_code, **updates}


def change(status='completed', **updates):
    return {'id': 'file', 'type': 'file_change', 'changes': [{'path': 'SECRET-path', 'kind': 'update'}],
            'status': status, **updates}


def event(kind, item): return {'type': 'item.' + kind, 'item': item}


def stream(items):
    return [{'type': 'thread.started', 'thread_id': 'thread'}, {'type': 'turn.started'}, *items,
            event('completed', {'id': 'answer', 'type': 'agent_message', 'text': 'done'}),
            {'type': 'turn.completed'}]


def runtime(workspace, items, work=True):
    value = ar.StructuredRuntime('codex', executable=sys.executable,
        work_profile=work, work_workspace=workspace if work else None)
    value._transport = Transport(stream(items))
    value._cancel = threading.Event()
    value._deadline = time.monotonic() + 5
    return value


def test_command_and_completed_only_file_change_success(workspace):
    value = runtime(workspace, [event('started', command()), event('completed', command('completed', 0)),
                                event('completed', change())])
    assert value._codex_exec('synthetic') == 'done'
    evidence = value.last_work_evidence
    assert evidence['events'] == 3 and evidence['items'] == 2 and evidence['changes'] == 1
    assert evidence['outcomes'] == [{'kind': 'command_execution', 'status': 'completed', 'exit_code': 0},
                                    {'kind': 'file_change', 'status': 'completed', 'changes_count': 1}]
    assert 'SECRET' not in json.dumps(evidence)


@pytest.mark.parametrize('status,code', [('failed', 1), ('completed', 2), ('failed', None)])
def test_terminal_tool_failure_does_not_prove_turn_failure(workspace, status, code):
    value = runtime(workspace, [event('started', command()), event('completed', command(status, code))])
    assert value._codex_exec('synthetic') == 'done'
    assert value.last_work_evidence['outcomes'][0]['exit_code'] == code


@pytest.mark.parametrize('item', [
    command('declined', None), command('completed', None),
    {key: value for key, value in command('completed', 0).items() if key != 'exit_code'},
    change('failed', changes=[]),
])
def test_upstream_terminal_schema_values_without_started(workspace, item):
    value = runtime(workspace, [event('completed', item)])
    assert value._codex_exec('synthetic') == 'done'
    assert value.last_work_evidence['items'] == 1


def test_started_updated_completed_file_and_command(workspace):
    items = []
    for factory in (command, change):
        items += [event('started', factory('in_progress')), event('updated', factory('in_progress')),
                  event('completed', factory('completed', **({'exit_code': 0} if factory is command else {})))]
    value = runtime(workspace, items)
    assert value._codex_exec('synthetic') == 'done'
    assert value.last_work_evidence['events'] == 6


@pytest.mark.parametrize('items', [
    [event('started', command())],
    [event('started', {'id': 'warn', 'type': 'error'})],
    [event('updated', command())],
    [event('started', command('completed', 0))],
    [event('started', command()), event('started', command())],
    [event('completed', change()), event('completed', change())],
    [event('started', command()), event('completed', change(id='cmd'))],
    [event('started', command()), event('completed', {'id': 'cmd', 'type': 'error'})],
    [event('completed', {'id': 'cmd', 'type': 'error'}), event('started', command())],
    [event('completed', change(id='answer'))],
    [event('completed', {'id': 'answer', 'type': 'agent_message', 'text': 'earlier'})],
    [event('started', command()), event('completed', command('in_progress'))],
])
def test_bad_lifecycle_or_pending_turn_rejected(workspace, items):
    with pytest.raises(AdapterError, match='runtime_invalid_output'):
        runtime(workspace, items)._codex_exec('synthetic')


@pytest.mark.parametrize('item', [
    command(status='unknown'), command(exit_code=True), command(command=None),
    command(aggregated_output=[]), command(extra='unrecognized'), command(id=''), command(id='x' * 257), command(status=[]),
    command(id='x\n'), command('completed', 2 ** 31), change(status='declined'),
    change(changes='bad'), change(changes=[{'path': '', 'kind': 'add'}]),
    change(changes=[{'path': 'a', 'kind': 'rename'}]), change(changes=[{'path': 'a', 'kind': []}]),
    change(changes=[{'path': 'a', 'kind': 'add', 'extra': 1}]),
])
def test_malformed_schemas_rejected(workspace, item):
    phase = 'started' if item['type'] == 'command_execution' and item['status'] == 'in_progress' else 'completed'
    with pytest.raises(AdapterError, match='runtime_invalid_output'):
        runtime(workspace, [event(phase, item)])._codex_exec('synthetic')


def todo(items=None, **updates):
    return {'id': 'plan', 'type': 'todo_list', 'items': items if items is not None else
            [{'text': 'SECRET-plan', 'completed': False}], **updates}


def test_bounded_todo_snapshots_are_not_execution_evidence(workspace):
    value = runtime(workspace, [event('started', todo()), event('updated', todo([])),
                                event('completed', todo([{'text': 'SECRET-plan', 'completed': True}]))])
    assert value._codex_exec('synthetic') == 'done'
    assert 'SECRET' not in json.dumps(value.last_work_evidence)
    assert value.last_work_evidence['items'] == 0


@pytest.mark.parametrize('items', [
    [event('started', todo())], [event('updated', todo())], [event('completed', todo())],
    [event('started', todo()), event('started', todo())],
    [event('started', todo()), event('completed', todo()), event('updated', todo())],
    [event('started', todo()), event('completed', change(id='plan'))],
    [event('started', todo([{'text': 'x', 'completed': 1}]))],
    [event('started', todo([{'text': 1, 'completed': True}]))],
    [event('started', todo(extra=True))],
])
def test_invalid_todo_lifecycle_and_schema(workspace, items):
    with pytest.raises(AdapterError, match='runtime_invalid_output'):
        runtime(workspace, items)._codex_exec('synthetic')


def test_todo_bounds_and_text_policy(workspace, monkeypatch):
    with pytest.raises(AdapterError, match='runtime_output_limit'):
        runtime(workspace, [event('started', todo([{'text': 'x', 'completed': False}] * 129))])._codex_exec('synthetic')
    with pytest.raises(AdapterError, match='runtime_tool_policy_violation'):
        runtime(workspace, [event('started', todo())], work=False)._codex_exec('synthetic')
    monkeypatch.setattr(ar, 'MAX_WORK_BYTES', 1)
    with pytest.raises(AdapterError, match='runtime_output_limit'):
        runtime(workspace, [event('started', todo())])._codex_exec('synthetic')


@pytest.mark.parametrize('kind', ['mcp_tool_call', 'web_search', 'unknown_tool'])
def test_unknown_tools_refused_even_in_work_profile(workspace, kind):
    with pytest.raises(AdapterError, match='runtime_tool_policy_violation'):
        runtime(workspace, [event('completed', {'id': 'x', 'type': kind})])._codex_exec('synthetic')


@pytest.mark.parametrize('item', [command(), change()])
def test_default_text_profile_still_refuses_work_items(workspace, item):
    with pytest.raises(AdapterError, match='runtime_tool_policy_violation'):
        runtime(workspace, [event('started', item)], work=False)._codex_exec('synthetic')


@pytest.mark.parametrize('bound,limit,items', [
    ('MAX_WORK_ITEMS', 1, [event('completed', change()), event('completed', change(id='second'))]),
    ('MAX_WORK_EVENTS', 1, [event('started', command()), event('completed', command('completed', 0))]),
    ('MAX_WORK_CHANGES', 1, [event('completed', change(changes=[{'path': 'a', 'kind': 'add'}, {'path': 'b', 'kind': 'add'}]))]),
    ('MAX_WORK_BYTES', 1, [event('completed', change())]),
    ('MAX_LINE', 1, [event('completed', change())]),
])
def test_bounds_fail_closed(workspace, monkeypatch, bound, limit, items):
    monkeypatch.setattr(ar, bound, limit)
    with pytest.raises(AdapterError, match='runtime_output_limit'):
        runtime(workspace, items)._codex_exec('synthetic')


def test_exact_item_and_change_bounds_accepted(workspace, monkeypatch):
    monkeypatch.setattr(ar, 'MAX_WORK_ITEMS', 1)
    monkeypatch.setattr(ar, 'MAX_WORK_EVENTS', 1)
    monkeypatch.setattr(ar, 'MAX_WORK_CHANGES', 1)
    value = runtime(workspace, [event('completed', change())])
    assert value._codex_exec('synthetic') == 'done'


def test_full_run_failure_remains_unknown_and_stops_transport(workspace, monkeypatch):
    value = ar.StructuredRuntime('codex', executable=sys.executable, work_profile=True, work_workspace=workspace)
    monkeypatch.setattr(value, '_ensure_preflight', lambda cancel: None)
    monkeypatch.setattr(value, '_check_codex_exec_auth', lambda *args: None)
    monkeypatch.setattr(value, 'probe', lambda: {'available': True, 'reason': None})
    monkeypatch.setattr(value, 'command', lambda: ['synthetic'])
    transport = Transport(stream([event('started', command())]))
    transport.check = lambda *args: None
    transport.close = lambda: True
    transport.diagnostics = lambda: {}
    monkeypatch.setattr(ar, '_JSONLTransport', lambda *args, **kwargs: transport)
    interrupted = []
    monkeypatch.setattr(value, '_interrupt', lambda: interrupted.append(True))
    with pytest.raises(AdapterError, match='runtime_invalid_output'):
        value.run({'room_id': 'synthetic-room'}, threading.Event())
    assert value.last_outcome == 'unknown' and value._failed and interrupted == [True]
    assert value.last_work_evidence['outcomes'][0]['status'] == 'in_progress'
    assert 'SECRET' not in json.dumps(value.last_work_evidence)


def test_evidence_resets_each_turn(workspace):
    value = runtime(workspace, [event('completed', change())])
    assert value._codex_exec('first') == 'done'
    assert value.last_work_evidence['items'] == 1
    value._transport = Transport(stream([]))
    assert value._codex_exec('second') == 'done'
    assert value.last_work_evidence == {'events': 0, 'items': 0, 'changes': 0, 'observed_bytes': 0, 'outcomes': []}


# A0 versioned schema fixture: synthetic Codex 0.164 item surface frozen by this
# repository. Provider execution is NOTRUN; the fixture detects drift, it does not
# claim compatibility with any installed binary.
SCHEMA_FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'codex_exec_0164_items.json'
SCHEMA_FIXTURE_DATA = json.loads(SCHEMA_FIXTURE.read_text(encoding='utf-8'))


def test_versioned_exec_item_schema_fixture_matches_parser(workspace):
    data = SCHEMA_FIXTURE_DATA
    assert data['schema_version'] == 'codex-cli-0.164'
    value = runtime(workspace, data['allowed_items'])
    assert value._codex_exec('synthetic') == 'done'
    evidence = value.last_work_evidence
    expected = data['expected']
    assert {key: evidence[key] for key in ('events', 'items', 'changes')} == expected
    assert 'SECRET' not in json.dumps(evidence)


@pytest.mark.parametrize('case', SCHEMA_FIXTURE_DATA['rejected'],
                         ids=[case['name'] for case in SCHEMA_FIXTURE_DATA['rejected']])
def test_versioned_exec_rejected_schema_stays_rejected(workspace, case):
    with pytest.raises(AdapterError, match=case['error']):
        runtime(workspace, [event(case['phase'], case['item'])])._codex_exec('synthetic')


def test_malformed_item_after_completed_tool_is_unknown_not_success(workspace, monkeypatch):
    """Observation failure must not be promoted to a successful turn (P0-1 contract)."""
    value = ar.StructuredRuntime('codex', executable=sys.executable,
                                 work_profile=True, work_workspace=workspace)
    monkeypatch.setattr(value, '_ensure_preflight', lambda cancel: None)
    monkeypatch.setattr(value, '_check_codex_exec_auth', lambda *args: None)
    monkeypatch.setattr(value, 'probe', lambda: {'available': True, 'reason': None})
    monkeypatch.setattr(value, 'command', lambda: ['synthetic'])
    transport = Transport([{'type': 'thread.started', 'thread_id': 'thread'},
                           {'type': 'turn.started'},
                           event('completed', command('completed', 0)),
                           event('completed', {'id': 'answer', 'type': 'agent_message'})])
    transport.check = lambda *args: None
    transport.close = lambda: True
    transport.diagnostics = lambda: {}
    monkeypatch.setattr(ar, '_JSONLTransport', lambda *args, **kwargs: transport)
    interrupted = []
    monkeypatch.setattr(value, '_interrupt', lambda: interrupted.append(True))
    with pytest.raises(AdapterError, match='runtime_invalid_output'):
        value.run({'room_id': 'synthetic-room'}, threading.Event())
    assert value.last_outcome == 'unknown' and value._failed and interrupted == [True]
    assert value.last_work_evidence['outcomes'] == [
        {'kind': 'command_execution', 'status': 'completed', 'exit_code': 0}]
    assert 'SECRET' not in json.dumps(value.last_work_evidence)
