"""Explicit one-shot turn requests; no live service or provider."""
import json
import sys

import httpx
import pytest

from talkabout import cli
from talkabout.worker import APIClient, Journal, SyntheticRuntime, Worker


def room(rid, **overrides):
    return {'id': rid, 'topic': 'Topic', 'limits': {'messages': 100}, 'mode': 'free_discussion', 'state': 'active', 'tasks': [],
            'generation': 1, 'membership_revision': 2, 'last_seq': 3, **overrides}


class Client:
    def __init__(self, rooms=(), events=None):
        self.rooms, self.events, self.calls = rooms, events or [], []
    def request(self, method, path, body=None, key=None, params=None):
        self.calls.append((method, path, body, key))
        if path == 'agents/me': return {'id': 'agent', 'display_name': 'Agent'}
        if path == 'rooms': return self.rooms
        if method == 'GET' and path.startswith('rooms/'):
            found = next((item for item in self.rooms if item['id'] == path[6:]), None)
            if found is None:
                request = httpx.Request(method, 'https://example.test/' + path)
                raise httpx.HTTPStatusError('not found', request=request, response=httpx.Response(404, request=request))
            return found
        if path == 'agents/me/events': return {'events': self.events, 'next_cursor': 'cursor'}
        return {}
    def close(self): pass
    def report_terminal(self, *args): pass


def posts(client):
    return [call for call in client.calls if call[1].endswith('/turn-requests')]


def test_selected_room_only_and_explicit_id(tmp_path):
    client = Client([room('other'), room('target')])
    journal = Journal(tmp_path / 'j.sqlite')
    try:
        worker = Worker(client, journal, SyntheticRuntime())
        with pytest.raises(TypeError): worker.request_turns()
        with pytest.raises(ValueError): worker.request_turns('../other')
        worker.request_turns('target')
        assert [(c[0], c[1]) for c in posts(client)] == [('POST', 'rooms/target/turn-requests')]
        assert posts(client)[0][2] == {'generation': 1, 'membership_revision': 2}
    finally: journal.close()


@pytest.mark.parametrize('target', [None, room('target', state='paused'), room('target', state='closed'),
                                    room('target', mode='bounded_rounds'),
                                    room('target', tasks=[{'assignee': 'agent', 'state': 'queued'}]),
                                    room('target', state='preparing', tasks=[{'assignee': 'agent', 'state': 'queued'}])])
def test_ineligible_room_does_not_request(tmp_path, target):
    client = Client([room('other')] + ([target] if target else []))
    journal = Journal(tmp_path / 'j.sqlite')
    try:
        assert Worker(client, journal, SyntheticRuntime()).request_turns('target') is None
        assert not posts(client)
    finally: journal.close()


def test_request_key_is_bounded_and_unique_per_invocation(tmp_path):
    rid = 'a' * 128
    client = Client([room(rid)])
    journal = Journal(tmp_path / 'j.sqlite')
    try:
        Worker(client, journal, SyntheticRuntime()).request_turns(rid)
        Worker(client, journal, SyntheticRuntime()).request_turns(rid)
        keys = [call[3] for call in posts(client)]
        assert len(keys) == 2 and len(set(keys)) == 2
        assert all(len(key) <= 128 for key in keys)
    finally: journal.close()


def test_mention_only_does_not_request(tmp_path):
    client = Client([room('target')], [{'event_id': 'e', 'type': 'message.created', 'mentions': ['agent']}])
    journal = Journal(tmp_path / 'j.sqlite')
    try:
        worker = Worker(client, journal, SyntheticRuntime())
        assert worker.step() == 'waiting'
        assert not posts(client)
    finally: journal.close()


@pytest.mark.parametrize('status', [409, 503])
def test_worker_request_error_never_targets_another_room(tmp_path, status):
    class FailingClient(Client):
        def request(self, method, path, body=None, key=None, params=None):
            if path.endswith('/turn-requests'):
                self.calls.append((method, path, body, key))
                request = httpx.Request(method, 'https://example.test/' + path)
                response = httpx.Response(status, request=request)
                raise httpx.HTTPStatusError('failure', request=request, response=response)
            return super().request(method, path, body, key, params)
    client = FailingClient([room('target'), room('other')])
    journal = Journal(tmp_path / 'j.sqlite')
    try:
        worker = Worker(client, journal, SyntheticRuntime())
        if status == 409:
            worker.request_turns('target')
        else:
            with pytest.raises(httpx.HTTPStatusError): worker.request_turns('target')
        assert [call[1] for call in posts(client)] == ['rooms/target/turn-requests']
    finally: journal.close()


def test_transport_never_retries_turn_post():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(503, request=request, json={})
    client = APIClient('https://example.test', 'token', 'https://example.test', transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(httpx.HTTPStatusError):
            client.request('POST', 'rooms/target/turn-requests', {}, 'key')
        assert len(calls) == 1
    finally: client.close()


def setup_cli(tmp_path, monkeypatch, flags, *, fail=False):
    config = tmp_path / 'agent.json'
    config.write_text(json.dumps({'base_url': 'https://example.test', 'allowed_origin': 'https://example.test',
                                  'journal_path': str(tmp_path / 'j.sqlite')}))
    client = Client([room('other'), room('target')])
    monkeypatch.setenv('TALKABOUT_TOKEN', 'fake')
    monkeypatch.setattr(cli, 'APIClient', lambda *args: client)
    class FakeWorker:
        instances = []
        def __init__(self, *args, **kwargs):
            self.requests, self.steps = [], 0
            self.instances.append(self)
        def request_turns(self, rid):
            self.requests.append(rid)
            if fail: raise RuntimeError('unknown outcome')
        def step(self):
            self.steps += 1
            return 'completed'
    monkeypatch.setattr(cli, 'Worker', FakeWorker)
    monkeypatch.setattr(sys, 'argv', ['talkabout', 'run', '--config', str(config), '--synthetic', '--steps', '4'] + flags)
    return client, FakeWorker


@pytest.mark.parametrize('flags,expected', [([], []), (['--request-turn', '--request-turn-room', 'target'], ['target'])])
def test_cli_single_request_across_steps(tmp_path, monkeypatch, flags, expected):
    _, worker = setup_cli(tmp_path, monkeypatch, flags)
    cli.main()
    assert worker.instances[0].requests == expected
    assert worker.instances[0].steps == 4


def test_cli_error_does_not_retry(tmp_path, monkeypatch):
    _, worker = setup_cli(tmp_path, monkeypatch, ['--request-turn', '--request-turn-room', 'target'], fail=True)
    with pytest.raises(SystemExit, match='worker_failed'): cli.main()
    assert worker.instances[0].requests == ['target']
    assert worker.instances[0].steps == 0


@pytest.mark.parametrize('flags', [['--request-turn'], ['--request-turn-room', 'target'],
                                   ['--request-turn', '--request-turn-room', '../target']])
def test_cli_rejects_missing_or_invalid_target_before_worker(tmp_path, monkeypatch, flags):
    _, worker = setup_cli(tmp_path, monkeypatch, flags)
    with pytest.raises(SystemExit): cli.main()
    assert not worker.instances


@pytest.mark.parametrize('post_result', ['ok', 'conflict', 'timeout'])
def test_cli_waits_for_active_once(tmp_path, monkeypatch, post_result):
    client, _ = setup_cli(tmp_path, monkeypatch, ['--request-turn', '--request-turn-room', 'target'])
    events = []
    class TrackedWorker(Worker):
        def request_turns(self, rid):
            events.append('check')
            return super().request_turns(rid)
        def step(self, **kwargs):
            events.append('step')
            return super().step(**kwargs)
    monkeypatch.setattr(cli, 'Worker', TrackedWorker)
    observations = iter([room('target', state='preparing'), room('target', state='preparing'), room('target', generation=2)])
    original = client.request
    def request(method, path, body=None, key=None, params=None):
        if path == 'rooms/target' and method == 'GET':
            client.calls.append((method, path, body, key))
            return next(observations)
        if path.endswith('/turn-requests') and post_result != 'ok':
            client.calls.append((method, path, body, key))
            req = httpx.Request(method, 'https://example.test/' + path)
            if post_result == 'timeout': raise httpx.ReadTimeout('timeout', request=req)
            raise httpx.HTTPStatusError('conflict', request=req, response=httpx.Response(409, request=req))
        return original(method, path, body, key, params)
    client.request = request
    if post_result == 'timeout':
        with pytest.raises(SystemExit, match='worker_failed'): cli.main()
    else:
        cli.main()
    assert len(posts(client)) == 1
    assert events[:5] == ['check', 'step', 'check', 'step', 'check']
    assert sum(call[1] == 'rooms/target' for call in client.calls) == 3
    assert all(call[0] == 'GET' for call in client.calls if call[1] == 'rooms/target')


def test_cli_preparing_step_limit_never_posts(tmp_path, monkeypatch, capsys):
    client, _ = setup_cli(tmp_path, monkeypatch, ['--request-turn', '--request-turn-room', 'target'])
    steps = []
    class TrackedWorker(Worker):
        def step(self, **kwargs):
            steps.append('step')
            return 'completed'
    monkeypatch.setattr(cli, 'Worker', TrackedWorker)
    client.rooms = [room('target', state='preparing')]
    cli.main()
    assert 'turn_request_not_completed' in capsys.readouterr().out
    assert not posts(client)
    assert len(steps) == 4
    assert sum(call[1] == 'rooms/target' for call in client.calls) == 4


def test_cli_preparing_time_limit_reports_pending(tmp_path, monkeypatch, capsys):
    client, _ = setup_cli(tmp_path, monkeypatch, ['--request-turn', '--request-turn-room', 'target', '--max-seconds', '1'])
    clock = [100.0]
    class TimedWorker(Worker):
        def step(self, **kwargs):
            clock[0] += 2
            return 'completed'
    monkeypatch.setattr(cli, 'Worker', TimedWorker)
    monkeypatch.setattr(cli.time, 'monotonic', lambda: clock[0])
    client.rooms = [room('target', state='preparing')]
    cli.main()
    output = capsys.readouterr().out
    assert 'run_budget_exhausted' in output
    assert 'turn_request_not_completed' in output
    assert not posts(client)
    assert sum(call[1] == 'rooms/target' for call in client.calls) == 1


@pytest.mark.parametrize('changed', [
    {'membership_revision': 3}, {'topic': 'Changed'}, {'mode': 'bounded_rounds'},
    {'limits': {'messages': 50}}, {'generation': 2}, {'state': 'paused'},
])
def test_pending_intent_cancels_on_pre_activation_change(tmp_path, monkeypatch, changed):
    client, _ = setup_cli(tmp_path, monkeypatch, ['--request-turn', '--request-turn-room', 'target'])
    class TrackedWorker(Worker):
        def step(self, **kwargs):
            # Events do not restore cancelled explicit intent.
            return 'completed'
    monkeypatch.setattr(cli, 'Worker', TrackedWorker)
    observations = iter([room('target', state='preparing'),
                         room('target', **({'state': 'preparing'} | changed)),
                         room('target', generation=2)])
    original = client.request
    def request(method, path, body=None, key=None, params=None):
        if method == 'GET' and path == 'rooms/target':
            client.calls.append((method, path, body, key))
            return next(observations)
        return original(method, path, body, key, params)
    client.request = request
    cli.main()
    assert not posts(client)
    assert sum(call[1] == 'rooms/target' for call in client.calls) == 2


@pytest.mark.parametrize('change', [
    {'membership_revision': 3}, {'topic': 'Changed'}, {'mode': 'bounded_rounds'},
    {'limits': {'messages': 50}}, {'generation': 3},
])
def test_activation_requires_original_context(tmp_path, change):
    client = Client([room('target', state='preparing')])
    journal = Journal(tmp_path / 'j.sqlite')
    try:
        worker = Worker(client, journal, SyntheticRuntime())
        assert worker.request_turns('target') == 'preparing'
        client.rooms = [room('target', **({'generation': 2} | change))]
        assert worker.request_turns('target') is None
        client.rooms = [room('target', generation=2)]
        assert worker.request_turns('target') is None
        assert not posts(client)
    finally:
        journal.close()


def test_revocation_then_rejoin_never_rearms(tmp_path):
    client = Client([room('target', state='preparing')])
    journal = Journal(tmp_path / 'j.sqlite')
    try:
        worker = Worker(client, journal, SyntheticRuntime())
        assert worker.request_turns('target') == 'preparing'
        client.rooms = []
        assert worker.request_turns('target') is None
        client.rooms = [room('target', generation=2, membership_revision=3)]
        assert worker.request_turns('target') is None
        assert not posts(client)
    finally:
        journal.close()


def test_targeted_lookup_works_with_local_client(fixture, tmp_path):
    from conftest import LocalClient
    store, _, rid = fixture
    client = LocalClient(store)
    journal = Journal(tmp_path / 'j.sqlite')
    try:
        Worker(client, journal, SyntheticRuntime()).request_turns(rid)
        assert ('GET', 'rooms/' + rid) in client.calls
        assert ('GET', 'rooms') not in client.calls
    finally:
        journal.close()


def test_startup_active_attempt_is_one_shot(tmp_path):
    client = Client([room('target')])
    journal = Journal(tmp_path / 'j.sqlite')
    try:
        worker = Worker(client, journal, SyntheticRuntime())
        worker.request_turns('target')
        worker.request_turns('target')
        assert len(posts(client)) == 1
        assert all(call[1] != 'rooms' for call in client.calls)
    finally:
        journal.close()
