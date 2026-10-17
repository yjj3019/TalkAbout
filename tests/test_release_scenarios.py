"""S1/S2 integration with labelled synthetic runtimes, never provider evidence."""
import json
import pytest
from conftest import LocalClient,control
from talkabout.worker import Journal,SyntheticRuntime,Worker


class Counted(SyntheticRuntime):
    def __init__(self,label):self.label=label;self.calls=0
    def run(self,context,cancel):
        self.calls+=1
        return 'SYNTHETIC '+self.label+': '+super().run(context,cancel)


def test_s2_two_labelled_workers_pause_recover_and_converse(fixture,tmp_path):
    store,core,rid=fixture
    runtimes={pid:Counted(label) for pid,label in [('a','Codex fixture'),('b','Claude fixture')]}
    journals={pid:Journal(tmp_path/(pid+'.sqlite')) for pid in runtimes}
    workers={pid:Worker(LocalClient(store,'fixture-'+pid),journals[pid],runtimes[pid]) for pid in runtimes}
    for pid in workers:workers[pid].client.request('PUT','agents/me/connection',{'runtime':'synthetic','state':'idle'},'test-report')
    try:
        assert workers['a'].step()=='completed'
        control(core,rid,'pause')
        before=sum(runtime.calls for runtime in runtimes.values())
        result=workers['b'].step()
        assert result in {'unavailable','stale','waiting'}
        if result!='waiting':assert workers['b'].safe_to_continue_after(result)
        assert sum(runtime.calls for runtime in runtimes.values())==before
        control(core,rid,'resume')
        for _ in range(8):
            for worker in workers.values():worker.step()
        messages=core.read('fixture-human',rid)['messages']
        assert {'a','b'}<={m['actor_id'] for m in messages}
        assert all('SYNTHETIC' in m['text'] for m in messages)
        assert len({m['id'] for m in messages})==len(messages)
        assert any('Codex fixture' in m['text'] for m in messages) and any('Claude fixture' in m['text'] for m in messages)
        assert all(m['execution_evidence']=='synthetic_test_only' for m in messages)
    finally:
        for journal in journals.values():journal.close()


def test_post_call_state_is_never_safe_to_drain(fixture,tmp_path):
    store,_,_=fixture;journal=Journal(tmp_path/'worker.sqlite')
    try:
        worker=Worker(LocalClient(store),journal,Counted('test'))
        worker.last_step_started_runtime=True
        assert not worker.safe_to_continue_after('stale')
        assert not worker.safe_to_continue_after('unavailable')
        worker.last_step_started_runtime=False
        assert not worker.safe_to_continue_after('uncertain')
        assert not worker.safe_to_continue_after('heartbeat_failed')
        assert worker.safe_to_continue_after('stale')
    finally:journal.close()


def test_cli_drains_pre_call_pause_race_and_recovers_without_model_repeat(fixture,tmp_path,monkeypatch,capsys):
    import sys
    from talkabout import cli
    store,core,rid=fixture
    from talkabout.domain import AGENT_PERMISSIONS
    store.rooms.clear()
    rid=core.create('fixture-human',{'topic':'Single synthetic participant'},'single-room')['id']
    invitation=core.mutation('fixture-human',rid,'memberships','single-invite',{'principal_id':'a','permissions':sorted(AGENT_PERMISSIONS),'status':'pending'})
    core.accept('fixture-a',rid,'single-accept',{'expected_revision':invitation['revision'],'acknowledge_private':True})
    class RacingClient(LocalClient):
        paused=False
        def request(self,method,path,body=None,key=None,params=None):
            response=super().request(method,path,body,key,params)
            if not self.paused and method=='GET' and '/tasks/' in path:
                self.paused=True;control(core,rid,'pause')
            return response
    client=RacingClient(store);client.close=client.http.close
    client.report_terminal=lambda kind,state,error=None:client.request('PUT','agents/me/connection',{'runtime':kind,'state':state,'error_code':error},'terminal')
    class ResumingWorker(Worker):
        steps=0
        def step(self):
            self.steps+=1
            if self.steps==2:control(core,rid,'resume')
            return super().step()
    runtime=Counted('CLI pause/recovery fixture')
    monkeypatch.setattr(cli,'APIClient',lambda *a:client);monkeypatch.setattr(cli,'Worker',ResumingWorker)
    monkeypatch.setattr(cli,'SyntheticRuntime',lambda:runtime);monkeypatch.setenv('TALKABOUT_TOKEN','fixture-a')
    config=tmp_path/'agent.json';config.write_text(json.dumps({'base_url':'https://fixture.invalid','allowed_origin':'https://fixture.invalid','journal_path':str(tmp_path/'agent.sqlite')}))
    monkeypatch.setattr(sys,'argv',['talkabout','run','--synthetic','--steps','2','--config',str(config)])
    cli.main()
    assert runtime.calls==1 and len(core.read('fixture-human',rid)['messages'])==1
    assert store.principals['a']['connection']['state']=='stopped'
    output=capsys.readouterr().out
    assert 'stale' in output and 'completed' in output


@pytest.mark.parametrize('kind',['unverified','synthetic','worker-report'])
def test_response_evidence_is_server_derived_and_current(fixture,tmp_path,kind):
    import time
    from conftest import leased,result
    from test_connector_verification import EchoRuntime
    from talkabout.connector import verify_runtime
    store,core,rid=fixture;client=LocalClient(store)
    if kind=='worker-report':
        journal=Journal(tmp_path/'check.sqlite')
        try:verify_runtime(client,journal,EchoRuntime(),'codex','fixture-a',time.monotonic()+5)
        finally:journal.close()
        client.request('PUT','agents/me/connection',{'runtime':'codex','state':'idle'},'connection')
    elif kind=='synthetic':client.request('PUT','agents/me/connection',{'runtime':'synthetic','state':'idle'},'connection')
    task=leased(core,rid)
    body=result(task);body['result']['execution_evidence']='forged-provider-verified'
    posted=core.mutation('fixture-a',rid,'tasks/'+task['id']+'/complete','complete',body)['message']
    expected={'unverified':'unverified_agent','synthetic':'synthetic_test_only','worker-report':'worker_reported_runtime'}[kind]
    assert posted['execution_evidence']==expected
    evidence=core.read('fixture-human',rid)['members']['a']['response_evidence']
    assert evidence['worker_reported']==(1 if kind=='worker-report' else 0)
    assert evidence['synthetic']==(1 if kind=='synthetic' else 0)
    if kind=='worker-report':
        store.principals['a']['runtime_check']['id']='replacement-receipt'
        assert core.read('fixture-human',rid)['members']['a']['response_evidence']['worker_reported']==0
    control(core,rid,'pause');control(core,rid,'resume')
    evidence=core.read('fixture-human',rid)['members']['a']['response_evidence']
    assert evidence['worker_reported']==0 and evidence['synthetic']==0 and evidence['last_response_at'] is None
