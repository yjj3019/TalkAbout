"""Canonical CLI with synthetic in-process API/runtime fixtures only."""
import json
import os
import sys
from types import SimpleNamespace

import pytest

from talkabout import cli,agent_runtime
from tests.test_connector_verification import EchoRuntime,LocalClient


def configured(fixture,tmp_path,monkeypatch):
    store,core,rid=fixture
    config=tmp_path/'agent.json'
    config.write_text(json.dumps({'base_url':'https://fixture.invalid','allowed_origin':'https://fixture.invalid','journal_path':str(tmp_path/'worker.sqlite')}))
    monkeypatch.setenv('TALKABOUT_TOKEN','fixture-a')
    monkeypatch.setattr(cli,'os',SimpleNamespace(name='nt',environ=os.environ))
    def client(*args):
        value=LocalClient(store);value.close=value.http.close;return value
    monkeypatch.setattr(cli,'APIClient',client)
    made=[]
    class Runtime(EchoRuntime):
        kind='codex';auth_mode='native';codex_transport='exec-json';runtime_version='0.163.0'
        _verified_executable=('synthetic-cli',1,1);_verified_account_subject=None;thread_id='synthetic-thread'
        def __init__(self,*args,**kwargs):
            super().__init__();self.options=kwargs
            self.codex_transport=kwargs.get('codex_transport','exec-json');made.append(self)
        def probe(self):return {'available':True,'reason':None}
        def preflight(self):return self.probe()
        def set_deadline(self,value):pass
        def run(self,context,cancel):
            self.calls+=1;self.last_outcome='completed'
            return context.get('challenge','Synthetic room response')
    monkeypatch.setattr(agent_runtime,'StructuredRuntime',Runtime)
    class Worker:
        def __init__(self,client,journal,runtime,**kwargs):self.runtime=runtime;self.turn=0
        def step(self):
            self.turn+=1
            self.runtime.run({'room_id':rid,'generation':1,'membership_revision':1,'task_id':'task-'+str(self.turn),
                              'task_attempt':1,'purpose':'discussion'},__import__('threading').Event())
            return 'completed'
    monkeypatch.setattr(cli,'Worker',Worker)
    return config,made


@pytest.mark.parametrize('total,room,expected',[(1,None,0),(5,0,0),(2,5,1),(5,1,1),(3,2,2)])
def test_connection_check_and_room_caps_both_obey_total_ceiling(fixture,tmp_path,monkeypatch,capsys,total,room,expected):
    config,made=configured(fixture,tmp_path,monkeypatch)
    args=['talkabout','connect','--config',str(config),'--runtime','codex','--approve-external','--steps','10','--max-seconds','10']
    if total!=1:args+=['--max-model-calls',str(total)]
    if room is not None:args+=['--max-room-calls',str(room)]
    monkeypatch.setattr(sys,'argv',args);cli.main()
    output=[json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith('{')]
    budget=next(value for value in output if 'connection_check_calls' in value)
    assert budget['connection_check_calls']==1 and budget['total_calls_remaining']==total-1
    assert budget['room_calls_remaining']==min(total-1,room if room is not None else total-1)
    assert sum(runtime.calls for runtime in made)==1+expected
    assert all(runtime.codex_transport=='exec-json' for runtime in made)


def test_app_server_requires_explicit_gate_before_runtime_creation(fixture,tmp_path,monkeypatch):
    config,made=configured(fixture,tmp_path,monkeypatch)
    monkeypatch.setattr(sys,'argv',['talkabout','connect','--config',str(config),'--runtime','codex','--approve-external','--codex-transport','app-server'])
    with pytest.raises(SystemExit):cli.main()
    assert made==[]


def test_explicit_experimental_transport_is_passed_without_switching_exec_default(fixture,tmp_path,monkeypatch):
    config,made=configured(fixture,tmp_path,monkeypatch)
    monkeypatch.setattr(sys,'argv',['talkabout','connect','--config',str(config),'--runtime','codex','--approve-external',
                                   '--codex-transport','app-server','--experimental-app-server'])
    cli.main()
    assert made[0].options['codex_transport']=='app-server' and sum(runtime.calls for runtime in made)==1


def test_canonical_opt_in_reports_missing_identity_before_any_model_call(fixture,tmp_path,monkeypatch,capsys):
    config,made=configured(fixture,tmp_path,monkeypatch)
    args=['talkabout','connect','--config',str(config),'--runtime','codex','--approve-external','--persist-provider-sessions',
          '--max-model-calls','2','--max-room-calls','1','--steps','1','--max-seconds','10']
    monkeypatch.setattr(sys,'argv',args)
    for _ in range(2):
        with pytest.raises(SystemExit,match='provider_session_identity_unavailable'):
            cli.main()
    assert sum(runtime.calls for runtime in made)==0
    assert all(runtime.closed for runtime in made)
    monkeypatch.setattr(sys,'argv',['talkabout','status','--config',str(config)]);cli.main()
    rows=[json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith('{')]
    status=next(row['provider_sessions'] for row in rows if 'provider_sessions' in row)
    assert status['records']==0 and status['identity_unavailable']==0
    assert status['last_error']=='provider_session_identity_unavailable'
    recovery=status['recovery']
    assert recovery['code']=='provider_session_identity_unavailable' and recovery['automatic_retry'] is False
    assert all(recovery[key][language] for key in ('reason','next_action') for language in ('ko','en'))
    assert '--retry-check' in recovery['next_action']['en'] and 'preserve the journal' in recovery['next_action']['en']
    emitted=[row['provider_session_recovery'] for row in rows if 'provider_session_recovery' in row]
    assert emitted==[recovery,recovery]
    assert recovery['inspect_command']['example']=='python -m talkabout.cli status --config agent.local.json'
    assert 'same config' in recovery['inspect_command']['en']


@pytest.mark.parametrize('typed',[False,True])
def test_unknown_failures_never_echo_arbitrary_error_or_offer_session_retry(fixture,tmp_path,monkeypatch,capsys,typed):
    from talkabout.codex_sessions import SessionRecoveryError
    config,made=configured(fixture,tmp_path,monkeypatch)
    class FailedWorker:
        def __init__(self,*args,**kwargs):pass
        def step(self):
            error=SessionRecoveryError if typed else RuntimeError
            raise error('PRIVATE_RAW_ERROR')
    monkeypatch.setattr(cli,'Worker',FailedWorker)
    monkeypatch.setattr(sys,'argv',['talkabout','connect','--config',str(config),'--runtime','codex','--approve-external',
                                   '--max-model-calls','2','--steps','1'])
    with pytest.raises(SystemExit,match='worker_failed') as failure:cli.main()
    output=capsys.readouterr().out
    assert 'PRIVATE_RAW_ERROR' not in str(failure.value)+output and 'provider_session_recovery' not in output
    assert sum(runtime.calls for runtime in made)==1


@pytest.mark.parametrize('total,room',[(1,None),(2,0)])
def test_saved_completion_drains_at_total_or_room_cap_without_new_work(fixture,tmp_path,monkeypatch,capsys,total,room):
    import httpx
    from talkabout.worker import Journal,SyntheticRuntime,Worker
    store,core,rid=fixture;path=tmp_path/'worker.sqlite'
    journal=Journal(path);client=LocalClient(store);original=client.request
    def lost_response(method,resource,body=None,key=None,params=None):
        value=original(method,resource,body,key,params)
        if method=='POST' and resource.endswith('/complete'):raise httpx.ConnectError('synthetic lost completion response')
        return value
    client.request=lost_response
    try:
        with pytest.raises(httpx.ConnectError):Worker(client,journal,SyntheticRuntime()).step()
        _,state,payload=journal.next();assert state=='result_saved'
        complete_key=payload['complete_key'];cursor=journal.setting('cursor')
    finally:journal.close();client.http.close()
    config,made=configured(fixture,tmp_path,monkeypatch);make_client=cli.APIClient;calls=[]
    def tracked_client(*args):
        value=make_client(*args);request=value.request
        def track(method,resource,body=None,key=None,params=None):
            calls.append((method,resource,key));return request(method,resource,body,key,params)
        value.request=track;return value
    monkeypatch.setattr(cli,'APIClient',tracked_client);monkeypatch.setattr(cli,'Worker',Worker)
    args=['talkabout','connect','--config',str(config),'--runtime','codex','--approve-external',
          '--max-model-calls',str(total),'--steps','3','--request-turn']
    if room is not None:args+=['--max-room-calls',str(room)]
    monkeypatch.setattr(sys,'argv',args);cli.main()
    output=[json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith('{')]
    assert next(row for row in output if 'next_action' in row)['next_action']=='saved_result_recovery_only'
    journal=Journal(path)
    try:
        assert journal.status()=={'done':1} and journal.setting('cursor')==cursor
    finally:journal.close()
    assert made==[]  # Delivering a saved result does not even prepare a provider.
    assert [key for method,resource,key in calls if resource.startswith('rooms/') and resource.endswith('/complete')]==[complete_key]
    assert not any(resource.endswith(('/claim','/turn-requests','/events','/snapshot')) for _,resource,_ in calls)
    messages=core.read('fixture-human',rid)['messages']
    assert len(messages)==1 and messages[0]['execution_evidence']!='worker_reported_runtime'


@pytest.mark.parametrize('state', ['started', 'uncertain'])
def test_fresh_journal_refuses_connection_check_while_server_execution_is_unresolved(fixture,tmp_path,monkeypatch,state):
    store, core, rid = fixture
    task = next(task for task in core.read('fixture-a', rid)['tasks'] if task['state'] == 'queued')
    claimed = core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/claim', 'server-claim',
        {k: task[k] for k in ('generation', 'membership_revision')} | {'execution_protocol': 'server_start_v1'})
    core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/start', 'server-start',
        {k: claimed[k] for k in ('attempt', 'generation', 'membership_revision')})
    if state == 'uncertain':
        with store.transaction(rid, 'fixture-human') as (_, room):
            room['tasks'][task['id']]['lease_expires_at'] = __import__('time').time() - 1
        core.tick('fixture-human', rid)
    config, made = configured(fixture, tmp_path, monkeypatch)
    monkeypatch.setattr(sys, 'argv', ['talkabout', 'connect', '--config', str(config), '--runtime', 'codex', '--approve-external'])
    with pytest.raises(SystemExit, match='server_execution_unresolved'):
        cli.main()
    assert made == []


@pytest.mark.parametrize('bad', [None, {}, [{'tasks': None}], [{'tasks': [None]}],
    [{'tasks': [{'state': 'leased', 'execution': []}]}], [{'tasks': [{'state': 'leased', 'execution': {'state': []}}]}]],
    ids=['null', 'object', 'tasks-null', 'task-null', 'execution-list', 'execution-state-list'])
def test_malformed_execution_snapshot_blocks_provider_preparation(fixture,tmp_path,monkeypatch,bad):
    config, made = configured(fixture, tmp_path, monkeypatch)
    factory = cli.APIClient
    def client(*args):
        value = factory(*args)
        original = value.request
        value.request = lambda method, path, *a, **kw: bad if path == 'rooms' else original(method, path, *a, **kw)
        return value
    monkeypatch.setattr(cli, 'APIClient', client)
    monkeypatch.setattr(sys, 'argv', ['talkabout', 'connect', '--config', str(config), '--runtime', 'codex', '--approve-external'])
    with pytest.raises(SystemExit, match='server_execution_state_unavailable'):
        cli.main()
    assert made == []


def test_unavailable_execution_snapshot_never_falls_back_to_provider(fixture,tmp_path,monkeypatch):
    import httpx
    config, made = configured(fixture, tmp_path, monkeypatch)
    factory = cli.APIClient
    def client(*args):
        value = factory(*args)
        original = value.request
        def request(method, path, *a, **kw):
            if path == 'rooms': raise httpx.ReadTimeout('synthetic state read failed')
            return original(method, path, *a, **kw)
        value.request = request
        return value
    monkeypatch.setattr(cli, 'APIClient', client)
    monkeypatch.setattr(sys, 'argv', ['talkabout', 'connect', '--config', str(config), '--runtime', 'codex', '--approve-external'])
    with pytest.raises(httpx.ReadTimeout): cli.main()
    assert made == []


def test_input_rejection_recovery_never_constructs_or_checks_a_provider(fixture,tmp_path,monkeypatch,capsys):
    """Recovery contract after explicit pre-call rejection, independent of excerpt policy."""
    from talkabout.worker import Journal,SyntheticRuntime,Worker
    from talkabout.runtime import RuntimeInputError
    import talkabout.worker as worker_module
    store,core,rid=fixture
    path=tmp_path/'worker.sqlite';journal=Journal(path);client=LocalClient(store)
    request=client.request
    def unavailable(method,path,body=None,key=None,params=None):
        if path.endswith('/skip'):raise __import__('httpx').ReadTimeout('synthetic before skip dispatch')
        return request(method,path,body,key,params)
    client.request=unavailable
    try:
        def rejected_input(context):raise RuntimeInputError('invalid_runtime_budget')
        with monkeypatch.context() as preparation:
            preparation.setattr(worker_module,'runtime_prompt',rejected_input)
            with pytest.raises(__import__('httpx').ReadTimeout):Worker(client,journal,SyntheticRuntime()).step()
        assert journal.status()=={'skip_pending':1}
        cursor=journal.setting('cursor')
    finally:journal.close();client.http.close()
    config,made=configured(fixture,tmp_path,monkeypatch)
    monkeypatch.setattr(cli,'Worker',Worker)
    monkeypatch.setattr(sys,'argv',['talkabout','connect','--config',str(config),'--runtime','codex','--approve-external',
                                   '--max-model-calls','1','--max-room-calls','0','--steps','3'])
    cli.main()
    assert made==[]
    output=capsys.readouterr().out
    assert 'input_rejected' in output and '"model_calls": 0' in output
    journal=Journal(path)
    try:assert journal.status()=={'failed':1} and journal.setting('cursor')==cursor
    finally:journal.close()
    assert core.read('fixture-human',rid)['messages']==[]


def test_unsupported_persistent_native_identity_fails_before_connection_model_call(fixture,tmp_path,monkeypatch):
    config,made=configured(fixture,tmp_path,monkeypatch)
    monkeypatch.setattr(sys,'argv',['talkabout','connect','--config',str(config),'--runtime','codex','--approve-external',
                                   '--persist-provider-sessions','--max-model-calls','2','--steps','1'])
    with pytest.raises(SystemExit,match='provider_session_identity_unavailable'):
        cli.main()
    assert len(made)==1 and made[0].calls==0
    assert fixture[0].principals['a'].get('runtime_check') is None
