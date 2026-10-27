"""Work-mode prompt is budgeted before any server execution authorization."""
import copy

import pytest

from conftest import LocalClient
from talkabout.agent_runtime import MAX_INPUT, StructuredRuntime, prompt_text, work_prompt_text
from talkabout.collaboration import ContextBudgetError, fit_context, shared_context
from talkabout.worker import Journal, Worker


def context_with_message():
    room = {'id': 'room', 'last_seq': 1, 'pinned_message_id': None,
            'messages': [{'id': 'm', 'actor_id': 'a', 'actor_kind': 'agent',
                          'display_name': 'a', 'seq': 1, 'kind': 'contribution',
                          'text': '한🟦' * 600}], 'artifacts': []}
    context = shared_context(room, {'id': 'task'})
    context.update(room_id='room', deliverable='change_proposal')
    return context


def full_prompt(context, workspace):
    return (work_prompt_text(workspace) + prompt_text(context, work_profile=True)).encode('utf-8')


def test_work_fit_accounts_exact_workspace_and_work_header_at_utf8_boundary():
    original = context_with_message()
    # A long, multibyte path and metadata bring the text within one character of
    # the limit. JSON escaping and changed manifest ranges must both be counted.
    workspace = 'C:\\' + '작업' * 2000
    original['input_manifest']['budget'] = {'max_serialized_utf8_bytes': MAX_INPUT}
    original['padding'] = ''
    original['padding'] = 'p' * (MAX_INPUT - len(full_prompt(original, workspace)) - 1)
    assert len(full_prompt(original, workspace)) == MAX_INPUT - 1
    unchanged = copy.deepcopy(original)
    fit_context(unchanged, work_workspace=workspace)
    assert unchanged == original
    oversized = copy.deepcopy(original)
    oversized['padding'] += '한'
    assert len(full_prompt(oversized, workspace)) == MAX_INPUT + 2
    fit_context(oversized, work_workspace=workspace)
    assert len(full_prompt(oversized, workspace)) <= MAX_INPUT
    item = oversized['inputs'][0]
    ref = oversized['input_manifest']['sources'][0]
    assert item['text'].encode('utf-8').decode('utf-8') == item['text']
    assert item['utf8_end'] == len(item['text'].encode('utf-8')) == ref['utf8_end']
    assert item['truncated'] is ref['truncated'] is True
    assert oversized['input_manifest']['budget']['max_serialized_utf8_bytes'] == MAX_INPUT


def test_legacy_prompt_fitting_does_not_include_work_header():
    context = context_with_message()
    context['input_manifest']['budget'] = {'max_serialized_utf8_bytes': MAX_INPUT}
    context['padding'] = ''
    context['padding'] = 'p' * (MAX_INPUT - len(prompt_text(context).encode('utf-8')))
    before = copy.deepcopy(context)
    fit_context(context)
    assert context == before
    assert len(prompt_text(context).encode('utf-8')) == MAX_INPUT
    assert len(full_prompt(context, 'C:\\work')) > MAX_INPUT


def test_untrimmable_work_prompt_fails_before_reservation_or_start(fixture, tmp_path):
    store, _, rid = fixture
    class RecordingClient(LocalClient):
        def request(self, method, path, body=None, key=None, params=None):
            if method == 'POST' and (path.endswith('/reservations') or path.endswith('/start')):
                pytest.fail('authorization must not be requested')
            return super().request(method, path, body, key, params)
    class NeverCalled:
        def run(self, context, cancel):
            pytest.fail('runtime must not be called')
    journal = Journal(tmp_path / 'over-budget.sqlite')
    # Artificial oversized path is passed by an explicit wrapper configuration;
    # no filesystem or provider operation is needed to exercise the preflight gate.
    worker = Worker(RecordingClient(store), journal, NeverCalled(), work_prompt_workspace='x' * MAX_INPUT)
    try:
        assert worker.step() == 'invalid_runtime_budget'
        assert worker.last_step_started_runtime is False
        assert journal.status() == {'failed': 1}
        assert not next(iter(store.rooms[rid]['tasks'].values())).get('_reservations')
    finally:
        journal.close()


def test_untrimmable_room_metadata_never_authorizes_work(fixture, tmp_path):
    store, _, rid = fixture
    paths = []
    class OversizedRoom(LocalClient):
        def request(self, method, path, body=None, key=None, params=None):
            paths.append((method, path))
            value = super().request(method, path, body, key, params)
            if method == 'GET' and path == f'rooms/{rid}':
                value['topic'] = '한' * MAX_INPUT
            return value
    class NeverCalled:
        def run(self, *_):
            pytest.fail('runtime must not be called')
    journal = Journal(tmp_path / 'room-metadata.sqlite')
    try:
        worker = Worker(OversizedRoom(store), journal, NeverCalled(), work_prompt_workspace='C:\\work')
        assert worker.step() == 'invalid_runtime_budget'
        assert not worker.last_step_started_runtime
        assert not any(method == 'POST' and (path.endswith('/reservations') or path.endswith('/start'))
                       for method, path in paths)
        assert not next(iter(store.rooms[rid]['tasks'].values())).get('_reservations')
    finally:
        journal.close()


def test_direct_structured_runtime_work_profile_is_detected(fixture, tmp_path):
    store, _, _ = fixture
    runtime = object.__new__(StructuredRuntime)
    runtime.work_profile = True
    runtime.work_workspace = 'x' * MAX_INPUT
    # Avoid provider invocation: budget must fail before runtime launch.
    runtime.run = lambda *_: pytest.fail('runtime must not be called')
    runtime.set_deadline = lambda *_: None
    journal = Journal(tmp_path / 'native.sqlite')
    try:
        worker = Worker(LocalClient(store), journal, runtime)
        assert worker.step() == 'invalid_runtime_budget'
        assert not worker.last_step_started_runtime
    finally:
        journal.close()


def test_invalid_unicode_context_is_fixed_error_before_serialization():
    context = context_with_message()
    context['padding'] = '\ud800'
    with pytest.raises(ContextBudgetError, match='invalid_runtime_context'):
        fit_context(context, work_workspace='C:\\work')
