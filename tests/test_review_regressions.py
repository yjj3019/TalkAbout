import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from talkabout.domain import AGENT_PERMISSIONS, Problem
from talkabout.worker import Journal, SyntheticRuntime, Worker
from conftest import LocalClient, leased, result


@pytest.mark.parametrize('order', [('a','human','b'),('b','human','a')])
@pytest.mark.parametrize('other_start', [1,999999])
def test_viewer_history_revision_independent_of_member_order(fixture, order, other_start):
    store, core, rid = fixture
    core.mutation('fixture-human', rid, 'messages', 'old', {'kind':'advice','client_message_id':'old','text':'old private text'})
    artifact = core.mutation('fixture-human',rid,'artifacts','old-version',{'generation':1,'membership_revision':1,'text':'old private version'})
    new = core.mutation('fixture-human',rid,'artifacts/'+artifact['id']+'/versions','new-version',{'generation':1,'membership_revision':1,'expected_revision':1,'text':'new allowed version'})
    core.mutation('fixture-human',rid,'messages','new',{'kind':'advice','client_message_id':'new','text':'new allowed text'})
    start = new['versions'][-1]['seq']
    with store.transaction(rid,'fixture-human') as (_, room):
        room['members']['a'].update(read_start_seq=start, revision=11)
        room['members']['b'].update(read_start_seq=other_start, revision=97)
        room['members'] = {pid:room['members'][pid] for pid in order}
    view = core.read('fixture-a',rid)
    assert view['membership_revision'] == 11
    assert [m['text'] for m in view['messages']] == ['new allowed text']
    assert [v['text'] for a in view['artifacts'] for v in a['versions']] == ['new allowed version']


def test_cached_artifact_replay_rejected_after_member_epoch_change(fixture):
    _, core, rid = fixture
    artifact = core.mutation('fixture-human',rid,'artifacts','owner-version',{'generation':1,'membership_revision':1,'text':'owner private version'})
    operation = 'artifacts/'+artifact['id']+'/versions'
    body = {'generation':1,'membership_revision':2,'expected_revision':1,'text':'agent contribution'}
    first = core.mutation('fixture-a',rid,operation,'old-key',body)
    assert core.mutation('fixture-a',rid,operation,'old-key',body) == first
    blocked = core.mutation('fixture-human',rid,'memberships/a','block',{'expected_revision':2,'permissions':sorted(AGENT_PERMISSIONS),'status':'blocked'})
    core.mutation('fixture-human',rid,'memberships/a','unblock',{'expected_revision':blocked['revision'],'permissions':sorted(AGENT_PERMISSIONS),'status':'active'})
    assert core.read('fixture-a',rid)['artifacts'] == []
    with pytest.raises(Problem,match='idempotency_epoch_conflict'):
        core.mutation('fixture-a',rid,operation,'old-key',body)


def test_old_client_message_id_cannot_bypass_new_epoch(fixture):
    _, core, rid = fixture
    task = leased(core,rid)
    body = result(task,'old own message')
    operation = 'tasks/'+task['id']+'/complete'
    core.mutation('fixture-a',rid,operation,'first',body)
    blocked = core.mutation('fixture-human',rid,'memberships/a','block',{'expected_revision':2,'permissions':sorted(AGENT_PERMISSIONS),'status':'blocked'})
    core.mutation('fixture-human',rid,'memberships/a','unblock',{'expected_revision':blocked['revision'],'permissions':sorted(AGENT_PERMISSIONS),'status':'active'})
    with pytest.raises(Problem,match='client_message_epoch_conflict'):
        core.mutation('fixture-a',rid,operation,'different-key',body)


def expire(store,rid,tid):
    with store.transaction(rid,'fixture-human') as (_, room):
        room['tasks'][tid]['lease_expires_at'] = time.time()-1
    store.tick_all()


def test_pre_call_crash_lease_expiry_reclaims_with_new_key(fixture,tmp_path):
    store, core, rid = fixture
    path = tmp_path/'precall.sqlite'
    journal = Journal(path)
    client = LocalClient(store)
    worker = Worker(client,journal,SyntheticRuntime())
    worker.poll()
    tid,_,payload = journal.next()
    task = client.request('GET',f'rooms/{rid}/tasks/{tid}')
    claim = {k:task[k] for k in ('generation','membership_revision')}
    claimed = client.request('POST',f'rooms/{rid}/tasks/{tid}/claim',claim,f'claim:{tid}:1')
    payload.update(task=claimed,runtime_started=False)
    journal.save(tid,'claimed',payload)
    journal.close()
    expire(store,rid,tid)
    assert next(e for e in reversed(store.rooms[rid]['events']) if e['type']=='task.queued')['assignee']=='a'
    class Counted(SyntheticRuntime):
        calls=0
        def run(self,context,cancel): self.calls+=1; return 'one safe retry'
    runtime=Counted()
    recovered=Journal(path)
    restarted=Worker(LocalClient(store),recovered,runtime)
    assert restarted.step()=='retry_queued'
    assert restarted.step()=='completed'
    assert runtime.calls==1 and store.rooms[rid]['tasks'][tid]['attempt']==2
    recovered.close()


def test_ambiguous_model_failure_not_reactivated_by_requeue(fixture,tmp_path):
    store,core,rid=fixture
    class FailedModel(SyntheticRuntime):
        calls=0
        def run(self,context,cancel): self.calls+=1; raise ValueError('fixture ambiguous call')
    runtime=FailedModel(); journal=Journal(tmp_path/'ambiguous.sqlite')
    worker=Worker(LocalClient(store),journal,runtime)
    with pytest.raises(ValueError): worker.step()
    tid=next(t['id'] for t in core.read('fixture-a',rid)['tasks'] if t['state']=='leased')
    expire(store,rid,tid)
    assert worker.step()=='waiting' and runtime.calls==1 and journal.status()=={'uncertain':1}
    journal.close()


@pytest.mark.parametrize('cooperative',[True,False])
def test_stop_during_runtime_never_publishes_and_reports_outcome(fixture,tmp_path,cooperative):
    store,core,rid=fixture
    path=tmp_path/'stop.sqlite'; started=threading.Event(); exited=threading.Event(); release=threading.Event()
    class Slow(SyntheticRuntime):
        def run(self,context,cancel):
            started.set()
            if cooperative: cancel.wait(2)
            else: release.wait(3)
            exited.set()
            return 'must not publish after stop'
    def run_worker():
        journal=Journal(path)
        try: return Worker(LocalClient(store),journal,Slow()).step()
        finally: journal.close()
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(run_worker)
        assert started.wait(3)
        control=Journal(path)
        with control.db:
            control.set_setting('stop',True)
            control.set_setting('stop_state','stop_requested')
        answer=future.result(timeout=3)
        assert answer==('stopped' if cooperative else 'stop_unconfirmed')
        assert control.setting('stop_state')==('stopped' if cooperative else 'unreachable')
        if cooperative: assert control.status() in ({'cancelled':1},{'stopped_before_submit':1})
        else: assert control.status()=={'uncertain':1}
        control.close()
        release.set()
    assert exited.wait(1) and core.read('fixture-human',rid)['messages']==[]


def test_requeue_reactivates_only_proven_pre_call_terminal_row(fixture,tmp_path):
    store,core,rid=fixture
    client=LocalClient(store); journal=Journal(tmp_path/'rearm.sqlite')
    worker=Worker(client,journal,SyntheticRuntime()); worker.poll()
    tid,_,payload=journal.next(); claimed=leased(core,rid)
    payload.update(task=claimed,runtime_started=False,safe_pre_call_retry=True)
    journal.save(tid,'failed',payload)
    expire(store,rid,tid)
    worker.poll()
    assert journal.status()=={'ready':1}
    assert worker.step()=='completed' and store.rooms[rid]['tasks'][tid]['attempt']==2
    journal.close()
