import time
from types import SimpleNamespace

import pytest
from conftest import LocalClient
from talkabout.connector import ConnectionCheckError, verify_runtime
from talkabout.worker import Journal


class EchoRuntime:
    session_id = 'synthetic-session'
    runtime_version = 'fixture-1'
    def __init__(self): self.calls = 0; self.closed = False
    def run(self, context, cancel):
        assert context['purpose'] == 'connection_test'
        self.calls += 1
        return context['challenge']
    def shutdown(self): self.closed = True; return True


def test_runtime_test_receipt_and_no_credentials_in_journal(fixture, tmp_path):
    store, _, _ = fixture
    client = LocalClient(store)
    with_journal = Journal(tmp_path/'journal.sqlite')
    runtime = EchoRuntime()
    try:
        receipt, calls = verify_runtime(client, with_journal, runtime, 'codex', 'fixture-a', time.monotonic()+5)
        assert receipt['status'] == 'passed' and calls == 1 and runtime.closed
        rows = with_journal.db.execute('select k,v from settings').fetchall()
        assert 'fixture-a' not in str(rows) and 'synthetic-session' not in str(rows)
    finally: with_journal.close()


@pytest.mark.parametrize('changed',[None,'expired','replaced','explicit-retry','new-token'])
def test_reconnect_reuses_only_exact_live_server_receipt(fixture,tmp_path,changed):
    from talkabout.domain import token_hash
    store,_,_=fixture;client=LocalClient(store);journal=Journal(tmp_path/'reconnect.sqlite')
    try:
        first,_=verify_runtime(client,journal,EchoRuntime(),'codex','fixture-a',time.monotonic()+5)
        credential='fixture-a'
        if changed=='expired':store.principals['a']['runtime_check']['expires_at']=time.time()-1
        elif changed=='replaced':store.principals['a']['runtime_check']['id']='different-check'
        elif changed=='new-token':
            credential='fixture-a-second'
            store.credentials[token_hash(credential)]={**store.credentials[token_hash('fixture-a')]}
            client=LocalClient(store,credential)
        fresh=EchoRuntime();client.calls.clear()
        receipt,calls=verify_runtime(client,journal,fresh,'codex',credential,time.monotonic()+5,changed=='explicit-retry')
        assert calls==(0 if changed is None else 1)
        assert fresh.calls==calls and fresh.closed
        if changed is None:
            assert receipt['id']==first['id']
            assert client.calls==[('GET','agents/me')]
    finally:journal.close()


@pytest.mark.parametrize('profile_change',['auth-mode','version','transport','executable'])
def test_reconnect_profile_change_requires_new_challenge(fixture,tmp_path,profile_change):
    store,_,_=fixture;client=LocalClient(store);journal=Journal(tmp_path/'profile.sqlite')
    try:
        verify_runtime(client,journal,EchoRuntime(),'codex','fixture-a',time.monotonic()+5)
        fresh=EchoRuntime()
        if profile_change=='auth-mode':fresh.auth_mode='api_key'
        elif profile_change=='version':fresh.runtime_version='fixture-2'
        elif profile_change=='transport':fresh.codex_transport='different-fixture-transport'
        else:fresh._verified_executable=('synthetic-executable',123,456,789)
        assert verify_runtime(client,journal,fresh,'codex','fixture-a',time.monotonic()+5)[1]==1
        assert fresh.calls==1
    finally:journal.close()


@pytest.mark.parametrize('prior_state',['response_saved','uncertain'])
def test_changed_profile_does_not_bypass_uncertain_or_saved_call(fixture,tmp_path,prior_state):
    store,_,_=fixture;client=LocalClient(store);journal=Journal(tmp_path/'saved-profile.sqlite')
    original=client.request
    def lost(method,path,*args,**kwargs):
        if path.endswith('/complete'):raise OSError('synthetic lost completion')
        return original(method,path,*args,**kwargs)
    try:
        client.request=lost
        with pytest.raises(OSError):verify_runtime(client,journal,EchoRuntime(),'codex','fixture-a',time.monotonic()+5)
        if prior_state=='uncertain':
            key=journal.db.execute("SELECT k FROM settings WHERE k LIKE 'runtime_check:%'").fetchone()[0]
            prior=journal.setting(key);prior['state']='uncertain';prior.pop('profile_digest',None)
            with journal.db:journal.set_setting(key,prior)
        client.request=original;fresh=EchoRuntime();fresh.auth_mode='api_key'
        with pytest.raises(ConnectionCheckError,match='profile_changed' if prior_state=='response_saved' else 'outcome_unknown'):
            verify_runtime(client,journal,fresh,'codex','fixture-a',time.monotonic()+5)
        assert fresh.calls==0
        assert verify_runtime(client,journal,fresh,'codex','fixture-a',time.monotonic()+5,True)[1]==1
    finally:journal.close()


def test_unknown_model_call_is_not_repeated_without_explicit_flag(fixture, tmp_path):
    store, _, _ = fixture
    client = LocalClient(store)
    journal = Journal(tmp_path/'journal.sqlite')
    class Broken(EchoRuntime):
        def run(self, context, cancel): self.calls += 1; raise RuntimeError('fixture-only')
    broken = Broken()
    try:
        with pytest.raises(RuntimeError): verify_runtime(client,journal,broken,'codex','fixture-a',time.monotonic()+5)
        fresh = EchoRuntime()
        with pytest.raises(ConnectionCheckError, match='outcome_unknown'):
            verify_runtime(client,journal,fresh,'codex','fixture-a',time.monotonic()+5)
        assert fresh.calls == 0
        assert verify_runtime(client,journal,fresh,'codex','fixture-a',time.monotonic()+5,True)[1] == 1
    finally: journal.close()


def test_saved_response_retries_completion_without_second_model_call(fixture,tmp_path):
    store, _, _ = fixture
    client = LocalClient(store)
    journal = Journal(tmp_path/'journal.sqlite')
    original = client.request
    def lost(method,path,*a,**k):
        if path.endswith('/complete'): raise OSError('fixture transport')
        return original(method,path,*a,**k)
    client.request = lost
    runtime = EchoRuntime()
    try:
        with pytest.raises(OSError): verify_runtime(client,journal,runtime,'codex','fixture-a',time.monotonic()+5)
        client.request = original
        fresh = EchoRuntime()
        receipt,calls = verify_runtime(client,journal,fresh,'codex','fixture-a',time.monotonic()+5)
        assert receipt['status']=='passed' and calls == 0 and fresh.calls == 0
    finally: journal.close()


def test_test_requires_challenge_and_session(fixture,tmp_path):
    store, _, _ = fixture
    journal = Journal(tmp_path/'journal.sqlite')
    class Wrong(EchoRuntime):
        def run(self, context, cancel): return 'not-the-challenge'
    try:
        with pytest.raises(ConnectionCheckError,match='response_invalid'):
            verify_runtime(LocalClient(store),journal,Wrong(),'claude','fixture-a',time.monotonic()+5)
    finally: journal.close()


def test_stopped_journal_never_invokes_provider(fixture,tmp_path):
    store,_,_ = fixture
    journal=Journal(tmp_path/'stopped.sqlite')
    with journal.db: journal.set_setting('stop', True)
    runtime=EchoRuntime()
    try:
        with pytest.raises(ConnectionCheckError,match='connector_stopped'):
            verify_runtime(LocalClient(store),journal,runtime,'codex','fixture-a',time.monotonic()+5)
        assert runtime.calls==0
    finally: journal.close()


def test_stop_during_test_cancels_without_completion(fixture,tmp_path):
    import threading
    from talkabout.runtime import RuntimeTimeout
    store,_,_=fixture
    journal=Journal(tmp_path/'stop-during.sqlite')
    client=LocalClient(store)
    class WaitRuntime(EchoRuntime):
        def run(self,context,cancel):
            self.calls+=1
            journal.stop_state('stop_requested')
            import sqlite3
            with sqlite3.connect(journal.path) as db:
                db.execute("INSERT OR REPLACE INTO settings VALUES('stop','true')")
            cancel.wait(2)
            return context['challenge']
    runtime=WaitRuntime()
    try:
        with pytest.raises((RuntimeTimeout,ConnectionCheckError)):
            verify_runtime(client,journal,runtime,'codex','fixture-a',time.monotonic()+5)
        assert runtime.closed
        assert not any(p.endswith('/complete') for _,p in client.calls)
    finally: journal.close()


def test_room_sessions_and_permission_epochs_are_isolated():
    from talkabout.connector import RoomRuntimePool
    created=[]
    class Runtime(EchoRuntime):
        def set_deadline(self,d): pass
        def run(self,context,cancel):return str(id(self))
    def factory():
        r=Runtime();created.append(r);return r
    pool=RoomRuntimePool(factory)
    event=__import__('threading').Event()
    one={'room_id':'one','generation':1,'membership_revision':1}
    assert pool.run(one,event)==pool.run(one,event)
    assert pool.run({'room_id':'two','generation':1,'membership_revision':1},event)!=pool.run(one,event)
    pool.run(dict(one,generation=2),event)
    assert created[0].closed
    assert pool.shutdown()


def test_connector_exclusive_journal_lease(tmp_path):
    from talkabout.connector import ConnectorLease
    path=tmp_path/'worker.sqlite'
    first=ConnectorLease(path).acquire()
    try:
        with pytest.raises(ConnectionCheckError,match='already_running'):
            ConnectorLease(path).acquire()
    finally: first.close()
    second=ConnectorLease(path).acquire()
    second.close()


def test_cli_check_is_read_only_and_preserves_runtime_receipt(fixture,tmp_path,monkeypatch,capsys):
    import sys,json
    from talkabout import cli
    from talkabout.identity import IdentityService
    store,_,_=fixture
    client=LocalClient(store)
    journal=Journal(tmp_path/'prepare.sqlite')
    verify_runtime(client,journal,EchoRuntime(),'codex','fixture-a',time.monotonic()+5)
    journal.close()
    before=IdentityService(store).list('fixture-human')[0]['runtime_check']
    config=tmp_path/'agent.json'
    config.write_text(json.dumps({'base_url':'https://fixture.invalid','allowed_origin':'https://fixture.invalid','journal_path':str(tmp_path/'cli.sqlite')}))
    client.close=client.http.close
    client.calls.clear()
    monkeypatch.setenv('TALKABOUT_TOKEN','fixture-a')
    monkeypatch.setattr(cli,'APIClient',lambda *a:client)
    monkeypatch.setattr(sys,'argv',['talkabout','check','--config',str(config)])
    cli.main()
    assert client.calls==[('GET','agents/me')]
    assert IdentityService(store).list('fixture-human')[0]['runtime_check']==before


@pytest.mark.parametrize("stopped", [False, True], ids=["fresh", "explicit-resume"])
def test_cli_connect_integrates_check_then_real_adapter_contract(fixture,tmp_path,monkeypatch,capsys,stopped):
    import os,sys,json
    from talkabout import cli,agent_runtime
    store,core,rid=fixture
    client=LocalClient(store)
    client.close=client.http.close
    runtimes=[]
    class ProtocolFixture(EchoRuntime):
        def __init__(self,*args,**kwargs): super().__init__();runtimes.append(self)
        def probe(self):return {'available':True,'reason':None}
        def preflight(self):return self.probe()
        def set_deadline(self,d):pass
        def run(self,context,cancel):
            self.calls+=1
            return context['challenge'] if context['purpose']=='connection_test' else 'Synthetic adapter integration output'
    config=tmp_path/'agent.json'
    config.write_text(json.dumps({'base_url':'https://fixture.invalid','allowed_origin':'https://fixture.invalid','journal_path':str(tmp_path/'cli.sqlite')}))
    monkeypatch.setenv('TALKABOUT_TOKEN','fixture-a')
    monkeypatch.setattr(cli,'os',SimpleNamespace(name='nt',environ=os.environ))
    monkeypatch.setattr(cli,'APIClient',lambda *a:client)
    monkeypatch.setattr(agent_runtime,'StructuredRuntime',ProtocolFixture)
    monkeypatch.setattr(sys,'argv',['talkabout','connect','--config',str(config),'--runtime','codex','--approve-external','--max-model-calls','2','--steps','1','--max-seconds','10'])
    if stopped:
        journal = Journal(tmp_path/'cli.sqlite')
        with journal.db:
            journal.set_setting('stop', True)
            journal.set_setting('stop_state', 'stop_requested')
        journal.close()
        with pytest.raises(SystemExit, match='connector_stopped'):
            cli.main()
        assert all(runtime.calls == 0 and runtime.closed for runtime in runtimes)
        assert client.calls == []
        journal = Journal(tmp_path/'cli.sqlite')
        assert journal.stop_requested()
        journal.close()
        runtimes.clear()
        client = LocalClient(store)
        client.close = client.http.close
        sys.argv.append('--resume-worker')
    cli.main()
    journal = Journal(tmp_path/'cli.sqlite')
    assert not journal.stop_requested()
    journal.close()
    assert len(runtimes)==2 and all(r.closed for r in runtimes)
    assert sum(r.calls for r in runtimes)==2
    assert core.read('fixture-human',rid)['messages'][0]['text']=='Synthetic adapter integration output'
    assert 'worker_reported_test' in capsys.readouterr().out


def test_cli_preflight_failure_precedes_server_challenge(fixture,tmp_path,monkeypatch):
    import os,sys,json
    from talkabout import cli,agent_runtime
    from talkabout.adapters import AdapterError
    store,_,_=fixture
    client=LocalClient(store);client.close=client.http.close
    class Unsupported(EchoRuntime):
        def __init__(self,*a,**k):super().__init__()
        def probe(self):return {'available':True,'reason':None}
        def preflight(self):raise AdapterError('runtime_version_unsupported')
    config=tmp_path/'agent.json'
    config.write_text(json.dumps({'base_url':'https://fixture.invalid','allowed_origin':'https://fixture.invalid','journal_path':str(tmp_path/'cli.sqlite')}))
    monkeypatch.setenv('TALKABOUT_TOKEN','fixture-a')
    monkeypatch.setattr(cli,'os',SimpleNamespace(name='nt',environ=os.environ))
    monkeypatch.setattr(cli,'APIClient',lambda *a:client)
    monkeypatch.setattr(agent_runtime,'StructuredRuntime',Unsupported)
    monkeypatch.setattr(sys,'argv',['talkabout','connect','--config',str(config),'--runtime','codex','--approve-external'])
    with pytest.raises(SystemExit,match='runtime_version_unsupported'):cli.main()
    assert not any('runtime-checks' in path for _,path in client.calls)


@pytest.mark.parametrize('command,membership', [('check', None), ('join', 'pending'),
                                               ('join', 'accepted')],
                         ids=['check', 'join-pending', 'join-already-accepted'])
def test_cli_recovery_preserves_stop_without_model(tmp_path, monkeypatch, capsys, command, membership):
    import json
    import sys
    import httpx
    from talkabout import cli, agent_runtime
    from talkabout.worker import APIClient

    journal_path = tmp_path/'stopped.sqlite'
    journal = Journal(journal_path)
    with journal.db:
        journal.set_setting('stop', True)
        journal.set_setting('stop_state', 'stop_requested')
    journal.close()
    config = tmp_path/'agent.json'
    config.write_text(json.dumps({'base_url': 'https://fixture.invalid',
                                 'allowed_origin': 'https://fixture.invalid',
                                 'journal_path': str(journal_path)}))
    requests = []
    def handler(request):
        path = request.url.path.removeprefix('/api/v1/')
        requests.append((request.method, path))
        if path == 'agents/me':
            return httpx.Response(200, json={'id': 'fixture-a', 'kind': 'agent'})
        if path == 'agents/me/invitations':
            return httpx.Response(200, json=[{'room_id': 'fixture-room', 'membership_revision': 3}]
                                  if membership == 'pending' else [])
        if path == 'rooms':
            return httpx.Response(200, json=[{'id': 'fixture-room', 'state': 'active'}])
        assert path == 'rooms/fixture-room/memberships/me/accept'
        assert request.method == 'POST' and request.headers['Idempotency-Key']
        assert json.loads(request.content) == {'expected_revision': 3, 'acknowledge_private': True}
        return httpx.Response(200, json={})
    def no_model(*args, **kwargs):
        pytest.fail('Recovery commands must never construct a model runtime')
    monkeypatch.setattr(agent_runtime, 'StructuredRuntime', no_model)
    monkeypatch.setattr(cli, 'SyntheticRuntime', no_model)
    monkeypatch.setattr(cli, 'APIClient', lambda *args: APIClient(*args, transport=httpx.MockTransport(handler)))
    monkeypatch.setenv('TALKABOUT_TOKEN', 'fixture-a')
    argv = ['talkabout', command, '--config', str(config)]
    if command == 'join':
        argv += ['--room', 'fixture-room', '--acknowledge-private']
    monkeypatch.setattr(sys, 'argv', argv)
    cli.main()
    assert json.loads(capsys.readouterr().out)['external_runtime'] == 'NOTRUN'
    expected = [('GET', 'agents/me')] if command == 'check' else [
        ('GET', 'agents/me/invitations'),
        ('POST', 'rooms/fixture-room/memberships/me/accept') if membership == 'pending' else ('GET', 'rooms')]
    assert requests == expected
    journal = Journal(journal_path)
    try:
        assert journal.stop_requested()
        assert journal.setting('stop_state') == 'stop_requested'
    finally:
        journal.close()
