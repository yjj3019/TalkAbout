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


def test_cli_connect_integrates_check_then_real_adapter_contract(fixture,tmp_path,monkeypatch,capsys):
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
    cli.main()
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
