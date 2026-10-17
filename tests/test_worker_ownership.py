"""Synthetic races only; no external provider or account access."""
import copy
import json
import sys
import time

import httpx
import pytest

from conftest import LocalClient, control
from talkabout.runtime import RunBudgetExceeded
from talkabout.worker import APIClient, Journal, StopRequested, SyntheticRuntime, Worker


def test_separate_journals_do_not_share_a_claim_response(fixture, tmp_path):
    store, core, rid = fixture
    queued = next(t for t in core.read('fixture-a', rid)['tasks'] if t['assignee']=='a')
    class StaleRead(LocalClient):
        def request(self, method, path, *args, **kwargs):
            if method=='GET' and path==f"rooms/{rid}/tasks/{queued['id']}":
                return copy.deepcopy(queued)
            return super().request(method,path,*args,**kwargs)
    journals=[Journal(tmp_path/'one.sqlite')];loser_keys=[]
    loser_runtime=type('Counted',(SyntheticRuntime,),{'calls':0,'run':lambda self,c,x:setattr(self,'calls',self.calls+1) or 'duplicate'})()
    class Winner(SyntheticRuntime):
        calls=0
        def run(self, context, cancel):
            self.calls+=1
            other=Journal(tmp_path/'two.sqlite')
            try:
                loser=Worker(StaleRead(store),other,loser_runtime)
                with pytest.raises(httpx.HTTPStatusError) as error:loser.step()
                assert error.value.response.status_code==409
                loser_keys.append(json.loads(other.db.execute('SELECT payload FROM queue LIMIT 1').fetchone()[0])['claim_key'])
            finally:other.close()
            return 'single synthetic contribution'
    winner_runtime=Winner()
    try:
        assert Worker(LocalClient(store),journals[0],winner_runtime).step()=='completed'
        assert winner_runtime.calls==1 and loser_runtime.calls==0
        assert len(core.read('fixture-human',rid)['messages'])==1
        payloads=[json.loads(j.db.execute('SELECT payload FROM queue LIMIT 1').fetchone()[0]) for j in journals]
        assert payloads[0]['claim_key']!=loser_keys[0]
    finally:
        for journal in journals:journal.close()


def test_lost_claim_response_reuses_its_durable_random_key(fixture,tmp_path):
    store,_,_=fixture
    class LostClaim(LocalClient):
        keys=[]
        def request(self,method,path,body=None,key=None,params=None):
            response=super().request(method,path,body,key,params)
            if path.endswith('/claim'):
                self.keys.append(key)
                if len(self.keys)==1:raise httpx.ReadTimeout('synthetic response loss')
            return response
    journal=Journal(tmp_path/'claim.sqlite');client=LostClaim(store)
    try:
        worker=Worker(client,journal,SyntheticRuntime())
        with pytest.raises(httpx.ReadTimeout):worker.step()
        assert journal.status()=={'claim_pending':1}
        assert Worker(client,journal,SyntheticRuntime()).step()=='completed'
        assert len(client.keys)==2 and client.keys[0]==client.keys[1]
    finally:journal.close()


@pytest.mark.parametrize('state',['claim_pending','claimed'])
def test_legacy_shared_claim_key_cannot_resume_a_model_call(fixture,tmp_path,state):
    store,_,_=fixture;journal=Journal(tmp_path/'legacy.sqlite');client=LocalClient(store)
    try:
        worker=Worker(client,journal,SyntheticRuntime());worker.poll()
        tid,_,payload=journal.next();payload['claim_key']=f'claim:{tid}:1'
        journal.save(tid,state,payload)
        assert worker.step()=='stale'
        assert not any(path.endswith('/claim') or path.endswith('/complete') for _,path in client.calls)
    finally:journal.close()


@pytest.mark.parametrize('change',['pause','change_topic','lease_owner'])
def test_latest_room_snapshot_fences_model_call(fixture,tmp_path,change):
    store,core,rid=fixture
    class ChangedRoom(LocalClient):
        changed=False
        def request(self,method,path,*args,**kwargs):
            if method=='GET' and path==f'rooms/{rid}' and not self.changed:
                self.changed=True
                if change=='pause':control(core,rid,'pause')
                elif change=='change_topic':
                    room=core.read('fixture-human',rid)
                    request=core.mutation('fixture-human',rid,'controls','new-topic',{'action':'change_topic','expected_revision':room['room_revision'],'parameters':{'topic':'Changed synthetic topic'}})
                    core.mutation('fixture-human',rid,'controls/'+request['id']+'/approve','approve-topic',{})
                else:
                    task=next(t for t in store.rooms[rid]['tasks'].values() if t['assignee']=='a')
                    task['attempt']+=1
            return super().request(method,path,*args,**kwargs)
    class NeverRun:
        def run(self,*args):pytest.fail('obsolete work reached the model')
    journal=Journal(tmp_path/'latest.sqlite')
    try:
        assert Worker(ChangedRoom(store),journal,NeverRun()).step()=='stale'
        assert core.read('fixture-human',rid)['messages']==[]
    finally:journal.close()


@pytest.mark.parametrize('fence',['stop','deadline'])
def test_terminal_report_has_only_a_bounded_status_cleanup_exception(fence):
    requests=[]
    def handler(request):requests.append(request);return httpx.Response(200,json={'ok':True})
    client=APIClient('https://fixture.invalid','synthetic','https://fixture.invalid',transport=httpx.MockTransport(handler))
    if fence=='stop':client.should_stop=lambda:True
    else:client.deadline=time.monotonic()-1
    error=StopRequested if fence=='stop' else RunBudgetExceeded
    try:
        with pytest.raises(error):client.request('POST','rooms/example/tasks/example/claim',{},'blocked')
        client.report_terminal('synthetic','stopped')
        with pytest.raises(error):client.request('GET','rooms')
        with pytest.raises(ValueError):client.report_terminal('synthetic','running')
        assert len(requests)==1
        request=requests[0]
        assert request.method=='PUT' and request.url.path=='/api/v1/agents/me/connection'
        assert json.loads(request.content)['state']=='stopped'
        assert request.extensions['timeout']['read']==2
    finally:client.close()


@pytest.mark.parametrize('outcome',['stop','deadline','failed'])
def test_cli_reports_terminal_state_after_shutdown(fixture,tmp_path,monkeypatch,outcome):
    from talkabout import cli
    store,_,_=fixture;local=LocalClient(store);requests=[];shutdown=[]
    def handler(request):
        requests.append((request.method,request.url.path,json.loads(request.content) if request.content else None))
        response=local.http.request(request.method,request.url.path,content=request.content,headers={**dict(request.headers),'host':'testserver'})
        return httpx.Response(response.status_code,json=response.json())
    client=APIClient('https://fixture.invalid','fixture-a','https://fixture.invalid',transport=httpx.MockTransport(handler))
    real_terminal=client.report_terminal
    def terminal(*args):assert shutdown==[True];return real_terminal(*args)
    client.report_terminal=terminal
    class Runtime:
        def shutdown(self):shutdown.append(True);return True
    class FakeWorker:
        def __init__(self,client,journal,runtime,**kwargs):self.client,self.journal=client,journal
        def step(self):
            with self.journal.db:self.journal.set_setting('stop',True)
            if outcome=='deadline':self.client.deadline=time.monotonic()-1;raise RunBudgetExceeded()
            if outcome=='failed':raise RuntimeError('synthetic failure')
            return 'stopped'
    config=tmp_path/'agent.json';config.write_text(json.dumps({'base_url':'https://fixture.invalid','allowed_origin':'https://fixture.invalid','journal_path':str(tmp_path/'cli.sqlite')}))
    monkeypatch.setattr(cli,'APIClient',lambda *a:client);monkeypatch.setattr(cli,'SyntheticRuntime',Runtime);monkeypatch.setattr(cli,'Worker',FakeWorker)
    monkeypatch.setenv('TALKABOUT_TOKEN','fixture-a');monkeypatch.setattr(sys,'argv',['talkabout','run','--synthetic','--steps','1','--config',str(config)])
    if outcome=='failed':
        with pytest.raises(SystemExit):cli.main()
    else:cli.main()
    assert requests[-1][1]=='/api/v1/agents/me/connection'
    assert requests[-1][2]['state']==('failed' if outcome=='failed' else 'stopped')
    assert not any('/tasks/' in path for _,path,_ in requests)
