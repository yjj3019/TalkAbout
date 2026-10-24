import json
import time

import httpx
import pytest

from talkabout.worker import APIClient, Journal, SyntheticRuntime, Worker
from conftest import LocalClient


def test_api_impersonation_and_text_only(fixture):
    store, _, rid = fixture
    client = LocalClient(store)
    with pytest.raises(httpx.HTTPStatusError) as error:
        client.request("POST", f"rooms/{rid}/messages", {"kind": "advice", "actor_id": "human", "text": "x"}, "spoof")
    assert error.value.response.status_code in {403, 422}
    with pytest.raises(httpx.HTTPStatusError) as error:
        client.request("POST", f"rooms/{rid}/uploads", {}, "binary")
    assert error.value.response.json()["code"] == "feature_disabled"


def test_human_advice_does_not_create_task(fixture):
    store, core, rid = fixture
    before = len(store.rooms[rid]["tasks"])
    LocalClient(store, "fixture-human").request("POST", f"rooms/{rid}/messages", {"kind": "advice", "client_message_id": "advice-1", "text": "pause", "reply_to": None, "mentions": []}, "advice")
    assert len(store.rooms[rid]["tasks"]) == before
    assert core.read("fixture-human", rid)["state"] == "active"


def test_worker_response_loss_restart_does_not_repeat_runtime(fixture, tmp_path):
    store, core, rid = fixture
    class LostResponse(LocalClient):
        lost = False
        def request(self, method, path, body=None, key=None, params=None):
            response = super().request(method, path, body, key, params)
            if path.endswith("/complete") and not self.lost:
                self.lost = True
                raise httpx.ReadTimeout("synthetic lost response")
            return response
    class Counted(SyntheticRuntime):
        calls = 0
        def run(self, context, cancel):
            self.calls += 1
            assert "fixture-a" not in json.dumps(context)
            return super().run(context, cancel)
    runtime = Counted()
    journal = Journal(tmp_path / "journal.sqlite")
    with pytest.raises(httpx.ReadTimeout):
        Worker(LostResponse(store), journal, runtime).step()
    assert journal.status() == {"result_saved": 1}
    journal.close()
    restarted = Journal(tmp_path / "journal.sqlite")
    assert Worker(LocalClient(store), restarted, runtime).step() == "completed"
    assert runtime.calls == 1
    assert len(core.read("fixture-human", rid)["messages"]) == 1
    restarted.close()


def test_worker_durable_inbox_and_uncertain_call(fixture, tmp_path):
    store, _, _ = fixture
    journal = Journal(tmp_path / "journal.sqlite")
    worker = Worker(LocalClient(store), journal, SyntheticRuntime())
    worker.poll()
    tid, _, payload = journal.next()
    worker.poll()
    assert journal.status() == {"ready": 1}
    journal.save(tid, "call_started", payload)
    journal.close()
    recovered = Journal(tmp_path / "journal.sqlite")
    assert Worker(LocalClient(store), recovered, SyntheticRuntime()).step() == "uncertain"
    assert recovered.status() == {"uncertain": 1}
    recovered.close()


@pytest.mark.parametrize('boundary',['stop','pause','revoke','lease_expiry'])
def test_metadata_only_skip_recovery_respects_current_execution_fences(fixture,tmp_path,boundary):
    from conftest import control
    store,core,rid=fixture
    class NeverRun:
        calls=0
        def run(self,context,cancel):
            self.calls+=1
            raise AssertionError('metadata recovery must not run a model')
    runtime=NeverRun();journal=Journal(tmp_path/'skip-boundary.sqlite');client=LocalClient(store)
    try:
        worker=Worker(client,journal,runtime);worker.poll()
        tid,_,payload=journal.next()
        task=client.request('POST',f'rooms/{rid}/tasks/{tid}/claim',
                            {'generation':1,'membership_revision':2},'synthetic-skip-claim')
        skip={k:task[k] for k in ('generation','membership_revision','attempt')}
        payload.update(task=task,skip={**skip,'failure_code':'invalid_runtime_budget'},skip_key='synthetic-skip-once',runtime_started=False,safe_pre_call_retry=False)
        journal.save(tid,'skip_pending',payload)
        if boundary=='stop':
            with journal.db:journal.set_setting('stop',True)
        elif boundary=='pause':control(core,rid,'pause')
        elif boundary=='revoke':store.revoke('fixture-a')
        else:
            with store.transaction(rid,'fixture-a') as (_,room):room['tasks'][tid]['lease_expires_at']=time.time()-1
        before=len(client.calls)
        if boundary=='stop':
            assert worker.step(saved_result_only=True)=='stopped'
            assert journal.status()=={'skip_pending':1}
            assert len(client.calls)==before
        else:
            with pytest.raises(httpx.HTTPStatusError) as failure:worker.step(saved_result_only=True)
            assert failure.value.response.status_code==({'revoke':401}.get(boundary,409))
            assert journal.status()=={'failed':1}
        assert runtime.calls==0
        assert not any(path.endswith('/complete') for _,path in client.calls)
        assert core.read('fixture-human',rid)['messages']==[]
    finally:journal.close();client.http.close()


@pytest.mark.parametrize('loss',[None,'before','after'])
def test_preflight_rejection_is_no_call_and_drains_after_restart(fixture,tmp_path,loss,monkeypatch):
    from talkabout.runtime import RuntimeInputError
    store,core,rid=fixture
    for index in range(4):
        core.mutation('fixture-a',rid,'artifacts','large-'+str(index),
                      {'generation':1,'membership_revision':2,'text':'x'*64000,'sources':[]})
    # Real large-room input is now bounded by the collaboration builder. Exercise
    # this no-call failure/recovery contract independently of source selection.
    def rejected(_):raise RuntimeInputError('invalid_runtime_budget')
    monkeypatch.setattr('talkabout.worker.runtime_prompt',rejected)
    class NeverRun(SyntheticRuntime):
        calls=0
        def run(self,context,cancel):
            self.calls+=1
            raise AssertionError('oversized input must not enter runtime')
    runtime=NeverRun();path=tmp_path/'preflight.sqlite'
    client=LocalClient(store);journal=Journal(path)
    try:
        request=client.request
        def interrupted(method,path,body=None,key=None,params=None):
            if path.endswith('/skip') and loss=='before':raise httpx.ReadTimeout('synthetic pre-dispatch failure')
            response=request(method,path,body,key,params)
            if path.endswith('/skip') and loss=='after':raise httpx.ReadTimeout('synthetic lost acknowledgement')
            return response
        client.request=interrupted
        if loss:
            with pytest.raises(httpx.ReadTimeout):Worker(client,journal,runtime).step()
        else:
            assert Worker(client,journal,runtime).step()=='input_rejected'
            assert runtime.calls==0 and journal.status()=={'failed':1}
            task=next(t for t in store.rooms[rid]['tasks'].values() if t['assignee']=='a')
            assert task['state']=='skipped' and task['failure_code']=='invalid_runtime_budget'
            return
        assert runtime.calls==0 and journal.status()=={'skip_pending':1}
        tid,state,payload=journal.next()
        assert payload['runtime_started'] is False and payload['input_error']=='invalid_runtime_budget'
        assert not any(route.endswith('/complete') for _,route in client.calls)
    finally:journal.close();client.http.close()
    client=LocalClient(store);journal=Journal(path)
    try:
        assert Worker(client,journal,runtime).step(saved_result_only=True)=='input_rejected'
        assert runtime.calls==0 and journal.status()=={'failed':1}
        task=store.rooms[rid]['tasks'][tid]
        assert task['state']=='skipped' and task['failure_code']=='invalid_runtime_budget'
        assert core.read('fixture-human',rid)['messages']==[]
    finally:journal.close();client.http.close()


def test_heartbeat_runs_separately_and_failure_blocks_submit(fixture, tmp_path):
    store, _, _ = fixture
    class Slow(SyntheticRuntime):
        def run(self, context, cancel):
            cancel.wait(0.08)
            return "slow synthetic response"
    client = LocalClient(store)
    journal = Journal(tmp_path / "success.sqlite")
    assert Worker(client, journal, Slow(), heartbeat_interval=0.01).step() == "completed"
    assert any(path.endswith("/heartbeat") for _, path in client.calls)
    journal.close()


def test_heartbeat_failure_never_completes(fixture, tmp_path):
    store, core, rid = fixture
    class FailsHeartbeat(LocalClient):
        def request(self, method, path, body=None, key=None, params=None):
            if path.endswith("/heartbeat"):
                raise httpx.ConnectError("synthetic heartbeat outage")
            return super().request(method, path, body, key, params)
    class Slow(SyntheticRuntime):
        def run(self, context, cancel):
            cancel.wait(0.08)
            return "late synthetic response"
    journal = Journal(tmp_path / "failure.sqlite")
    assert Worker(FailsHeartbeat(store), journal, Slow(), heartbeat_interval=0.01).step() == "heartbeat_failed"
    assert core.read("fixture-human", rid)["messages"] == []
    journal.close()


def test_transport_allowlist_and_https():
    with pytest.raises(ValueError, match="https_required"):
        APIClient("http://example.invalid", "fixture", "http://example.invalid")
    with pytest.raises(ValueError, match="invalid_origin"):
        APIClient("https://example.invalid", "fixture", "https://other.invalid")
