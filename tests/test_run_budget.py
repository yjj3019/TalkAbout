import sys
import threading
import time

import httpx
import pytest
from conftest import LocalClient
from talkabout.adapters import CLIRuntime
from talkabout.runtime import RunBudgetExceeded
from talkabout.worker import APIClient, Journal, Worker


class DelayedRuntime:
    def __init__(self): self.calls = 0
    def run(self, context, cancel):
        self.calls += 1
        cancel.wait(1.4)
        return 'late synthetic result'


def test_one_second_run_budget_never_submits_long_runtime(fixture, tmp_path):
    store, core, rid = fixture
    client, runtime = LocalClient(store), DelayedRuntime()
    journal = Journal(tmp_path/'budget.sqlite')
    worker = Worker(client, journal, runtime, run_deadline=time.monotonic()+1)
    try:
        assert worker.step() == 'runtime_timeout'
        assert runtime.calls == 1
        assert not any(path.endswith('/complete') for _, path in client.calls)
        assert core.read('fixture-human', rid)['messages'] == []
    finally: journal.close()


def test_slow_preflight_cannot_launch_runtime_after_deadline(fixture, tmp_path):
    store, _, _ = fixture
    client, runtime = LocalClient(store), DelayedRuntime()
    original = client.request
    def delayed(method, path, *args, **kwargs):
        value = original(method, path, *args, **kwargs)
        if path.startswith('rooms/') and path.count('/') == 1: time.sleep(.2)
        return value
    client.request = delayed
    journal = Journal(tmp_path/'preflight.sqlite')
    try:
        worker = Worker(client, journal, runtime, run_deadline=time.monotonic()+.15)
        with pytest.raises(RunBudgetExceeded): worker.step()
        assert runtime.calls == 0
        assert not any(path.endswith('/complete') for _, path in client.calls)
    finally: journal.close()


def test_deadline_is_rechecked_immediately_before_completion(fixture, tmp_path):
    store, _, _ = fixture
    client = LocalClient(store)
    journal = Journal(tmp_path/'near-submit.sqlite')
    class Fast:
        def run(self, context, cancel): return 'synthetic'
    worker = Worker(client, journal, Fast(), run_deadline=time.monotonic()+2)
    original = journal.save
    def save(task, state, payload):
        original(task, state, payload)
        if state == 'result_saved': worker.run_deadline = time.monotonic()-1
    journal.save = save
    try:
        with pytest.raises(RunBudgetExceeded): worker.step()
        assert journal.status() == {'result_saved': 1}
        assert not any(path.endswith('/complete') for _, path in client.calls)
    finally: journal.close()


def test_http_timeout_is_capped_and_slow_response_is_rejected():
    observed=[]
    def handler(request):
        observed.append(request.extensions['timeout']['read'])
        time.sleep(.08)
        return httpx.Response(200, json={'id':'synthetic'})
    client = APIClient('https://fixture.invalid', 'synthetic', 'https://fixture.invalid', transport=httpx.MockTransport(handler))
    client.deadline = time.monotonic()+.05
    try:
        with pytest.raises(RunBudgetExceeded): client.request('GET', 'agents/me')
        assert 0 < observed[0] <= .05
        with pytest.raises(RunBudgetExceeded): client.request('GET', 'agents/me')
        assert len(observed) == 1
    finally: client.close()


def test_runtime_rejects_expired_budget_before_spawn():
    runtime = CLIRuntime('claude', executable=sys.executable, run_deadline=time.monotonic()-1)
    with pytest.raises(RunBudgetExceeded): runtime.run({}, threading.Event())
    assert runtime.calls == 0


def test_cli_run_includes_preflight_in_one_second_budget(fixture, tmp_path, monkeypatch, capsys):
    import json
    from talkabout import cli
    store, _, _ = fixture
    client, runtime = LocalClient(store), DelayedRuntime()
    original = client.request
    def request(method, path, *args, **kwargs):
        if path == 'agents/me/capabilities': time.sleep(.4)
        return original(method, path, *args, **kwargs)
    client.request = request
    client.close = client.http.close
    config = tmp_path/'agent.local.json'
    config.write_text(json.dumps({'base_url':'https://fixture.invalid', 'allowed_origin':'https://fixture.invalid',
                                  'journal_path':str(tmp_path/'cli.sqlite')}))
    monkeypatch.setenv('TALKABOUT_TOKEN', 'fixture-a')
    monkeypatch.setattr(cli, 'APIClient', lambda *args: client)
    monkeypatch.setattr(cli, 'SyntheticRuntime', lambda: runtime)
    monkeypatch.setattr(sys, 'argv', ['talkabout', 'run', '--config', str(config), '--synthetic', '--steps', '1', '--max-seconds', '1'])
    started = time.monotonic()
    cli.main()
    assert time.monotonic()-started < 1.35
    assert runtime.calls == 1
    assert not any(path.endswith('/complete') for _, path in client.calls)
    assert 'runtime_timeout' in capsys.readouterr().out
