"""Pool lifecycle regressions with fake runtimes only; no providers or services."""
import threading

import pytest

from talkabout.connector import ConnectionCheckError, RoomRuntimePool


class Runtime:
    def __init__(self):
        self.calls = self.shutdown_calls = 0
        self.stop_mode = 'confirmed'
    def run(self, context, cancel):
        self.calls += 1
        return 'Synthetic room result'
    def shutdown(self):
        self.shutdown_calls += 1
        if self.stop_mode == 'raises':
            raise RuntimeError('synthetic cleanup failure')
        return self.stop_mode == 'confirmed'


def make_pool():
    created = []
    def factory():
        value = Runtime(); created.append(value)
        return value
    return RoomRuntimePool(factory), created


def context(room='room-one', **changes):
    return {'room_id':room, 'generation':1, 'membership_revision':1, **changes}


@pytest.mark.parametrize('boundary', ['generation', 'membership_revision'])
@pytest.mark.parametrize('failure', ['unconfirmed', 'raises'])
def test_failed_epoch_cleanup_retains_runtime_and_prevents_new_dispatch(boundary, failure):
    pool, created = make_pool(); cancel = threading.Event()
    assert pool.run(context(), cancel) == 'Synthetic room result'
    original = created[0]; original.stop_mode = failure
    try:
        with pytest.raises(ConnectionCheckError, match='connector_runtime_stop_unconfirmed'):
            pool.run(context(**{boundary:2}), cancel)
        assert pool.closed and pool.calls == original.calls == 1
        assert len(created) == 1 and pool.runtimes == {('room-one',1,1):original}
        # A failed stop cannot be bypassed by restoring the old epoch or changing rooms.
        for target in (context(), context('room-two')):
            with pytest.raises(ConnectionCheckError, match='connector_stopped'):
                pool.run(target, cancel)
        assert len(created) == 1 and pool.calls == 1
        assert pool.shutdown() is False and original.shutdown_calls == 2
    finally:
        for runtime in created: runtime.stop_mode = 'confirmed'
        assert pool.shutdown() is True


@pytest.mark.parametrize('failure', ['unconfirmed', 'raises'])
def test_shutdown_attempts_every_room_even_after_one_cleanup_fails(failure):
    pool, created = make_pool()
    for room in ('room-one','room-two','room-three'):
        pool.run(context(room), threading.Event())
    created[0].stop_mode = failure
    try:
        assert pool.shutdown() is False
        assert pool.closed and [r.shutdown_calls for r in created] == [1,1,1]
        assert len(pool.runtimes) == 3  # Keep references so explicit cleanup can be retried.
        with pytest.raises(ConnectionCheckError, match='connector_stopped'):
            pool.run(context('room-four'), threading.Event())
        assert len(created) == 3 and pool.calls == 3
    finally:
        for runtime in created: runtime.stop_mode = 'confirmed'
        assert pool.shutdown() is True


@pytest.mark.parametrize('boundary', ['generation', 'membership_revision'])
def test_confirmed_epoch_cleanup_still_starts_an_isolated_runtime(boundary):
    pool, created = make_pool()
    try:
        pool.run(context(), threading.Event())
        pool.run(context(**{boundary:2}), threading.Event())
        assert not pool.closed and pool.calls == 2 and len(created) == 2
        assert created[0].shutdown_calls == 1 and created[1].calls == 1
        assert list(pool.runtimes.values()) == [created[1]]
    finally:
        assert pool.shutdown() is True


@pytest.mark.parametrize('work_outcome', ['completed', 'failed', 'saved'])
def test_cli_failed_cleanup_reports_failure_and_exits_nonzero_without_replaying_output(fixture, tmp_path, monkeypatch, work_outcome):
    import httpx
    import json
    import sys
    from conftest import LocalClient
    from talkabout import agent_runtime, cli
    store, core, rid = fixture
    client = LocalClient(store); client.close = client.http.close
    request = client.request; saved_keys = []
    def transport(method, path, body=None, key=None, params=None):
        if work_outcome == 'saved' and method == 'POST' and path.startswith('rooms/') and path.endswith('/complete'):
            saved_keys.append(key)
            raise httpx.ConnectError('synthetic completion unavailable')
        return request(method, path, body, key, params)
    client.request = transport
    client.report_terminal = lambda runtime, state, error_code=None: client.request(
        'PUT', 'agents/me/connection', {'runtime':runtime,'state':state,'error_code':error_code}, 'terminal-report')
    made = []
    class ProtocolFixture:
        session_id = 'synthetic-check-session'; runtime_version = 'fixture-1'
        def __init__(self, *args, **kwargs):
            self.calls = 0; self.room_used = False; made.append(self)
        def probe(self): return {'available':True,'reason':None}
        def preflight(self): return self.probe()
        def set_deadline(self, deadline): pass
        def run(self, context, cancel):
            self.calls += 1
            if context['purpose'] == 'connection_test': return context['challenge']
            self.room_used = True
            if work_outcome == 'failed': raise ValueError('synthetic work failure')
            return 'Synthetic response retained despite local cleanup failure.'
        def shutdown(self): return not self.room_used
    config = tmp_path/'agent.local.json'
    config.write_text(json.dumps({'base_url':'https://fixture.invalid','allowed_origin':'https://fixture.invalid',
                                  'journal_path':str(tmp_path/'worker.sqlite')}))
    monkeypatch.setenv('TALKABOUT_TOKEN','fixture-a')
    monkeypatch.setattr(cli,'APIClient',lambda *args:client)
    monkeypatch.setattr(agent_runtime,'StructuredRuntime',ProtocolFixture)
    monkeypatch.setattr(sys,'argv',['talkabout','connect','--config',str(config),'--runtime','codex',
        '--approve-external','--max-model-calls','2','--steps','1','--max-seconds','10'])
    with pytest.raises(SystemExit, match='runtime_cleanup_unconfirmed'):
        cli.main()
    assert len(made) == 2 and sum(runtime.calls for runtime in made) == 2
    assert store.principals['a']['connection']['state'] == 'failed'
    assert store.principals['a']['connection']['error_code'] == 'runtime_failed'
    messages = core.read('fixture-human',rid)['messages']
    assert len(messages) == (1 if work_outcome == 'completed' else 0)
    if messages: assert messages[0]['text'] == 'Synthetic response retained despite local cleanup failure.'
    from talkabout.worker import Journal
    journal = Journal(tmp_path/'worker.sqlite')
    try:
        expected_state = {'completed':'done','failed':'uncertain','saved':'result_saved'}[work_outcome]
        assert journal.status() == {expected_state:1}
        if work_outcome == 'saved':
            _,state,payload = journal.next()
            assert state == 'result_saved' and payload['complete_key'] == saved_keys[0]
            assert payload['complete']['result']['text'] == 'Synthetic response retained despite local cleanup failure.'
        assert journal.stop_requested() and journal.setting('stop_state') == 'unreachable'
    finally: journal.close()
    # Automatic restart must not turn a failed stop into another model invocation.
    fresh = LocalClient(store); fresh.close = fresh.http.close
    monkeypatch.setattr(cli,'APIClient',lambda *args:fresh)
    with pytest.raises(SystemExit, match='connector_stopped'):
        cli.main()
    assert sum(runtime.calls for runtime in made) == 2 and fresh.calls == []
    assert len(core.read('fixture-human',rid)['messages']) == len(messages)
