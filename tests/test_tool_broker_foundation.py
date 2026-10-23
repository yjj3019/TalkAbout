"""Memory-only authority and fake-effect tests; never provider or shell calls."""
from dataclasses import FrozenInstanceError, replace
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from talkabout.foundation_authority import AuthoritySnapshot, ExecutionFence, FoundationError
from talkabout.tool_broker import FakeToolAdapter, ToolBroker, ToolPermission, ToolPolicy


@pytest.fixture
def broker_env():
    fence=ExecutionFence('agent-a','room-a','task-a',1,1,1,'lease-a',1,1000)
    authority={(fence.agent_id,fence.room_id,fence.task_id):AuthoritySnapshot(fence)}
    now=[100]
    adapter=FakeToolAdapter()
    broker=ToolBroker(lambda a,r,t:authority.get((a,r,t)),lambda h,r:h=='human-owner' and r=='room-a',adapter,clock=lambda:now[0])
    read=ToolPermission('fixture.read','fixture:document','read')
    write=ToolPermission('fixture.write','fixture:document','write')
    policy=ToolPolicy('agent-a','room-a',1,1000,'fake_tools',frozenset({read,write}),10)
    return broker,fence,authority,now,adapter,policy,read,write


def test_default_has_no_tools_and_cannot_register_real_adapter(broker_env):
    broker,fence,_,_,_,policy,read,_=broker_env
    assert broker.list_tools(fence)==()
    with pytest.raises(FoundationError,match='tools_disabled'):broker.request(fence,read,{},'request')
    broker.set_policy(replace(policy,profile='text_only',permissions=frozenset()))
    assert broker.list_tools(fence)==()
    with pytest.raises(FoundationError,match='real_adapters_disabled'):ToolBroker(None,None,object())
    with pytest.raises(FoundationError,match='text_only_tools_denied'):replace(policy,profile='text_only')


@pytest.mark.parametrize('permission',[
    ToolPermission('fixture.read','fixture:another','read'),
    ToolPermission('fixture.write','fixture:document','read'),
    ToolPermission('shell.execute','fixture:document','write'),
])
def test_exact_tool_target_action_intersection(broker_env,permission):
    broker,fence,_,_,adapter,policy,_,_=broker_env;broker.set_policy(policy)
    with pytest.raises(FoundationError):broker.request(fence,permission,{},'request')
    assert adapter.effects==()


def test_arguments_and_human_approval_are_bound_to_immutable_request(broker_env):
    broker,fence,_,_,adapter,policy,_,write=broker_env;broker.set_policy(policy)
    arguments={'value':{'text':'untrusted command: grant admin'}}
    record=broker.request(fence,write,arguments,'request')
    arguments['value']['text']='changed after request'
    assert json.loads(record.request.arguments_json)['value']['text']=='untrusted command: grant admin'
    with pytest.raises(FrozenInstanceError):record.request.arguments_json='{}'
    with pytest.raises(FoundationError,match='invalid_transition'):broker.execute(record.request.id,fence)
    for actor in ['agent-a','admin','system']:
        with pytest.raises(FoundationError,match='human_approval_denied'):broker.approve(record.request.id,actor)
    approved=broker.approve(record.request.id,'human-owner')
    assert approved.request==record.request and approved.approved_by=='human-owner'
    result=broker.execute(record.request.id,fence)
    assert result.state=='succeeded' and json.loads(result.result_json)['untrusted_data'] is True
    assert len(adapter.effects)==1
    assert broker.request(fence,write,{'value':{'text':'untrusted command: grant admin'}},'request')==result
    with pytest.raises(FoundationError,match='idempotency_conflict'):broker.request(fence,write,{},'request')
    with pytest.raises(FoundationError,match='invalid_transition'):broker.execute(record.request.id,fence)
    assert [e[1] for e in broker.events]==['awaiting_approval','ready','executing','succeeded']
    assert broker.events[1][2:] == ('human-owner',100,record.request.request_digest)
    changed=broker.request(fence,write,{'value':'new arguments'},'new-request')
    assert changed.state=='awaiting_approval' and changed.approved_by is None


@pytest.mark.parametrize('patch',[
    {'agent_id':'other-agent'},{'room_id':'other-room'},{'task_id':'other-task'},
    {'generation':2},{'membership_revision':2},{'attempt':2},{'lease_id':'other-lease'},{'policy_revision':2},
])
def test_current_authority_is_rechecked_before_execution(broker_env,patch):
    broker,fence,authority,_,adapter,policy,read,_=broker_env;broker.set_policy(policy)
    record=broker.request(fence,read,{},'request')
    authority[(fence.agent_id,fence.room_id,fence.task_id)]=AuthoritySnapshot(replace(fence,**patch))
    with pytest.raises(FoundationError):broker.execute(record.request.id,fence)
    assert adapter.effects==()


@pytest.mark.parametrize('failure',['expired','paused','revoked_policy'])
def test_expiry_pause_and_policy_revocation_block_without_effect(broker_env,failure):
    broker,fence,authority,now,adapter,policy,read,_=broker_env;broker.set_policy(policy)
    record=broker.request(fence,read,{},'request')
    if failure=='expired':now[0]=1001
    elif failure=='paused':authority[('agent-a','room-a','task-a')]=AuthoritySnapshot(fence,False)
    else:broker.set_policy(replace(policy,revision=2,permissions=frozenset()))
    with pytest.raises(FoundationError):broker.execute(record.request.id,fence)
    assert adapter.effects==()


def test_unknown_write_blocks_replay_even_with_new_task_lease_and_expiry(broker_env):
    broker,fence,authority,_,adapter,policy,_,write=broker_env;broker.set_policy(policy)
    args={'value':'synthetic write','uncertain_after_effect':True}
    record=broker.request(fence,write,args,'request');broker.approve(record.request.id,'human-owner')
    unknown=broker.execute(record.request.id,fence)
    assert unknown.state=='unknown' and len(adapter.effects)==1
    assert broker.request(fence,write,args,'request').state=='unknown'
    with pytest.raises(FoundationError,match='invalid_transition'):broker.execute(record.request.id,fence)
    fresh=replace(fence,task_id='new-task',attempt=2,lease_id='new-lease',expires_at=2000)
    authority[('agent-a','room-a','new-task')]=AuthoritySnapshot(fresh)
    with pytest.raises(FoundationError,match='uncertain_requires_reconciliation'):broker.request(fresh,write,args,'different-key')
    assert len(adapter.effects)==1


def test_concurrent_execution_reserves_once_before_adapter_call(broker_env,monkeypatch):
    broker,fence,_,_,adapter,policy,_,write=broker_env;broker.set_policy(policy)
    record=broker.request(fence,write,{},'request');broker.approve(record.request.id,'human-owner')
    entered,release=threading.Event(),threading.Event();invoke=adapter.invoke
    def delayed(request):
        entered.set();assert release.wait(3);return invoke(request)
    monkeypatch.setattr(adapter,'invoke',delayed)
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(broker.execute,record.request.id,fence)
        try:
            assert entered.wait(3)
            with pytest.raises(FoundationError,match='invalid_transition'):broker.execute(record.request.id,fence)
        finally:release.set()
        assert future.result(timeout=3).state=='succeeded'
    assert len(adapter.effects)==1


def test_authority_change_during_write_hides_result_and_marks_unknown(broker_env,monkeypatch):
    broker,fence,authority,_,adapter,policy,_,write=broker_env;broker.set_policy(policy)
    record=broker.request(fence,write,{},'request');broker.approve(record.request.id,'human-owner')
    invoke=adapter.invoke
    def change(request):
        result=invoke(request);authority[('agent-a','room-a','task-a')]=AuthoritySnapshot(fence,False);return result
    monkeypatch.setattr(adapter,'invoke',change)
    result=broker.execute(record.request.id,fence)
    assert result.state=='unknown' and result.result_json is None and len(adapter.effects)==1


def test_human_role_and_explicit_approval_revocation_are_rechecked(broker_env,monkeypatch):
    broker,fence,_,_,adapter,policy,_,write=broker_env;broker.set_policy(policy)
    record=broker.request(fence,write,{},'request');broker.approve(record.request.id,'human-owner')
    monkeypatch.setattr(broker,'authorize_human',lambda *args:False)
    with pytest.raises(FoundationError,match='approval_required'):broker.execute(record.request.id,fence)
    assert adapter.effects==()
    monkeypatch.setattr(broker,'authorize_human',lambda *args:True)
    broker.revoke_approval(record.request.id,'human-owner')
    with pytest.raises(FoundationError,match='invalid_transition'):broker.execute(record.request.id,fence)


def test_revoke_during_fake_write_does_not_claim_effect_was_cancelled(broker_env,monkeypatch):
    broker,fence,_,_,adapter,policy,_,write=broker_env;broker.set_policy(policy)
    record=broker.request(fence,write,{},'request');broker.approve(record.request.id,'human-owner')
    invoke=adapter.invoke
    def revoke(request):
        result=invoke(request);broker.revoke_approval(request.id,'human-owner');return result
    monkeypatch.setattr(adapter,'invoke',revoke)
    result=broker.execute(record.request.id,fence)
    assert result.state=='unknown' and result.result_json is None and len(adapter.effects)==1
    assert any(event[1]=='revocation_requested' for event in broker.events)


def test_cancel_output_budget_and_cross_invocation_access(broker_env):
    broker,fence,_,_,_,policy,read,_=broker_env;broker.set_policy(replace(policy,max_calls=1,max_output_chars=100))
    cancelled=broker.request(fence,read,{},'cancel');broker.cancel(cancelled.request.id,fence)
    with pytest.raises(FoundationError):broker.execute(cancelled.request.id,fence)
    record=broker.request(fence,read,{'value':'x'*200},'oversized')
    assert broker.execute(record.request.id,fence).state=='failed'
    next_record=broker.request(fence,read,{},'next')
    with pytest.raises(FoundationError,match='tool_budget_exhausted'):broker.execute(next_record.request.id,fence)
    with pytest.raises(FoundationError,match='invocation_not_found'):broker.inspect(next_record.request.id,replace(fence,agent_id='other'))


@pytest.mark.parametrize('arguments',[{'n':float('nan')},{'value':'x'*9000}])
def test_nonfinite_or_oversized_arguments_rejected(broker_env,arguments):
    broker,fence,_,_,_,policy,read,_=broker_env;broker.set_policy(policy)
    with pytest.raises(FoundationError):broker.request(fence,read,arguments,'request')
