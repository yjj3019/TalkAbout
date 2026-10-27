"""Synthetic pre-grant work workspace checks; no provider or network calls."""
import pytest

from conftest import LocalClient
from talkabout import cli
from talkabout.adapters import AdapterError
from talkabout.worker import Journal, SyntheticRuntime, Worker


class TrackingClient(LocalClient):
    def __init__(self, store):
        super().__init__(store)
        self.requests = []

    def request(self, method, path, body=None, key=None, params=None):
        self.requests.append((method, path))
        return super().request(method, path, body, key, params)


class CountedRuntime(SyntheticRuntime):
    def __init__(self):
        self.calls = 0

    def run(self, context, cancel):
        self.calls += 1
        return super().run(context, cancel)


def grants(client):
    return [path for method, path in client.requests if method == 'POST'
            and (path.endswith('/reservations') or path.endswith('/start'))]


def test_real_worker_revalidation_failure_never_requests_reservation_or_start(fixture, tmp_path, monkeypatch):
    store, _, rid = fixture
    journal = Journal(tmp_path / 'worker.sqlite')
    client = TrackingClient(store)
    runtime = CountedRuntime()
    frozen = str(tmp_path / 'old-canonical')
    observed = []
    from talkabout import agent_runtime
    def validate(kind, transport, path):
        observed.append((kind, transport, path))
        raise AdapterError('runtime_work_workspace_missing')
    monkeypatch.setattr(agent_runtime, '_validated_work_workspace', validate)
    worker = Worker(client, journal, runtime, work_prompt_workspace=frozen,
                    pre_grant_preflight=lambda: cli.work_workspace_preflight('codex', 'exec-json', frozen))
    try:
        with pytest.raises(AdapterError, match='runtime_work_workspace_missing'):
            worker.step()
        assert observed == [('codex', 'exec-json', frozen)]
        assert grants(client) == [] and runtime.calls == 0
        assert not next(iter(store.rooms[rid]['tasks'].values())).get('_reservations')
        assert journal.status() == {'failed': 1}
    finally:
        journal.close()


def test_valid_preflight_uses_frozen_path_without_provider_construction(monkeypatch, tmp_path):
    from talkabout import agent_runtime
    original = str(tmp_path / 'canonical')
    observed = []
    def validate(kind, transport, path):
        observed.append((kind, transport, path))
        return tmp_path / 'canonical'
    monkeypatch.setattr(agent_runtime, '_validated_work_workspace', validate)
    monkeypatch.setattr(cli, 'provider_runtime', lambda *_args, **_kwargs: pytest.fail('provider constructed'))
    cli.work_workspace_preflight('codex', 'exec-json', original)
    assert observed == [('codex', 'exec-json', original)]


def test_revalidated_canonical_mismatch_fails_closed(monkeypatch, tmp_path):
    from talkabout import agent_runtime
    original = str(tmp_path / 'frozen')
    monkeypatch.setattr(agent_runtime, '_validated_work_workspace', lambda *_: tmp_path / 'changed')
    with pytest.raises(ValueError, match='runtime_work_workspace_mismatch'):
        cli.work_workspace_preflight('codex', 'exec-json', original)


def test_cli_wires_frozen_workspace_preflight(fixture, tmp_path, monkeypatch):
    import sys
    from tests.test_continuity_cli import configured
    config, made = configured(fixture, tmp_path, monkeypatch)
    observed = []
    original = str(tmp_path / 'configured-workspace')
    canonical = str(tmp_path / 'canonical-workspace')
    # The mocked initial provider returns the canonical value, as the real runtime does.
    original_factory = cli.provider_runtime
    def provider(*args, **kwargs):
        instance = original_factory(*args, **kwargs)
        instance.work_workspace = canonical
        return instance
    monkeypatch.setattr(cli, 'provider_runtime', provider)
    monkeypatch.setattr(cli, 'work_workspace_preflight', lambda *args: observed.append(args))
    class InspectWorker:
        def __init__(self, client, journal, runtime, **kwargs):
            assert kwargs['work_prompt_workspace'] == canonical
            self.preflight = kwargs['pre_grant_preflight']
        def step(self):
            count = len(made)
            self.preflight()
            assert len(made) == count
            return 'waiting'
    monkeypatch.setattr(cli, 'Worker', InspectWorker)
    monkeypatch.setattr(sys, 'argv', ['talkabout', 'run', '--config', str(config),
        '--runtime', 'codex', '--approve-external', '--max-model-calls', '2',
        '--work-profile', '--work-workspace', original])
    cli.main()
    assert observed == [('codex', 'exec-json', canonical)]
    assert made[0].options['work_workspace'] == original


def test_nonwork_worker_remains_unchanged(fixture, tmp_path):
    store, _, _ = fixture
    journal = Journal(tmp_path / 'nonwork.sqlite')
    client = TrackingClient(store)
    runtime = CountedRuntime()
    try:
        assert Worker(client, journal, runtime).step() == 'completed'
        assert runtime.calls == 1
        assert len(grants(client)) == 2
    finally:
        journal.close()


def test_valid_preflight_executes_before_grant(fixture, tmp_path):
    store, _, _ = fixture
    journal = Journal(tmp_path / 'ok.sqlite')
    client = TrackingClient(store)
    runtime = CountedRuntime()
    calls = []
    def preflight():
        calls.append('preflight')
        assert not grants(client)
    try:
        assert Worker(client, journal, runtime, pre_grant_preflight=preflight).step() == 'completed'
        assert calls == ['preflight'] and runtime.calls == 1
        assert len(grants(client)) == 2
    finally:
        journal.close()
