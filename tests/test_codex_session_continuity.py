"""No provider calls: scoped fake Codex state and durable local recovery."""
import hashlib
import json
import threading
import uuid

import pytest

from talkabout.codex_sessions import CodexSessionJournal,SessionRecoveryError,session_status,recovery_guidance
from talkabout.connector import ConnectorLease,ConnectionCheckError,RoomRuntimePool
from talkabout.worker import Journal


class Provider:
    def __init__(self):self.calls=[]


class FakeCodex:
    kind='codex';auth_mode='native';codex_transport='exec-json';runtime_version='0.163.0'
    _verified_executable=('synthetic-cli',1,1)
    blocked_values={'PRIVATE_CREDENTIAL'}
    def __init__(self,provider,subject='synthetic-stable-subject'):
        self.provider=provider;self._verified_account_subject=subject
        self.session_id=self.thread_id=None;self.last_outcome='not_started';self.stopped=False
    def preflight(self):pass
    def set_deadline(self,value):pass
    def run(self,context,cancel):
        self.provider.calls.append((context['task_id'],self.session_id))
        if self.session_id is None:self.session_id=self.thread_id='thread-'+uuid.uuid4().hex
        self.last_outcome='completed'
        return 'synthetic output'
    def shutdown(self):self.stopped=True;return True


def context(room='room-one',task='task-one',**extra):
    return dict(room_id=room,generation=1,membership_revision=1,task_id=task,task_attempt=1,**extra)


def open_pool(path,provider,*,subject='synthetic-stable-subject',binding=None,factory=None):
    journal=Journal(path);lease=ConnectorLease(path).acquire()
    store=CodexSessionJournal(journal,lease,binding or hashlib.sha256(b'synthetic-agent:credential').hexdigest())
    pool=RoomRuntimePool(factory or (lambda:FakeCodex(provider,subject)),session_journal=store)
    return journal,lease,pool


def close(journal,lease,pool):
    assert pool.shutdown();lease.close();journal.close()


def values(journal):
    return [json.loads(row[0]) for row in journal.db.execute("SELECT v FROM settings WHERE k LIKE 'provider_session:%'")]


def test_new_worker_restores_same_scoped_session_without_replaying_a_completed_turn(tmp_path):
    path=tmp_path/'journal.sqlite';provider=Provider()
    j,l,p=open_pool(path,provider)
    try:
        assert p.run(context(),threading.Event())=='synthetic output'
        saved=values(j)[0]['session_id']
    finally:close(j,l,p)
    j,l,p=open_pool(path,provider)
    try:
        assert p.run(context(task='task-two'),threading.Event())=='synthetic output'
        assert provider.calls==[('task-one',None),('task-two',saved)]
        with pytest.raises(SessionRecoveryError,match='result_recovery_required'):
            p.run(context(task='task-two'),threading.Event())
        assert len(provider.calls)==2
        assert not next(iter(p.runtimes.values())).stopped
        p.run(context(task='task-three'),threading.Event())
        assert provider.calls[-1]==('task-three',saved) and session_status(j)['last_error'] is None
    finally:close(j,l,p)


def test_identity_unavailable_allows_fresh_turn_but_blocks_automatic_resume(tmp_path):
    provider=Provider();path=tmp_path/'journal.sqlite'
    j,l,p=open_pool(path,provider,subject=None)
    try:p.run(context(),threading.Event())
    finally:close(j,l,p)
    j,l,p=open_pool(path,provider,subject=None)
    try:
        with pytest.raises(SessionRecoveryError,match='identity_unavailable'):
            p.run(context(task='task-two'),threading.Event())
        status=session_status(j)
        assert status['identity_unavailable']==1 and status['last_error']=='provider_session_identity_unavailable'
        assert len(provider.calls)==1
    finally:close(j,l,p)


@pytest.mark.parametrize('change',['account','credential','version','transport','executable'])
def test_changed_account_or_execution_profile_never_silently_starts_a_new_session(tmp_path,change):
    provider=Provider();path=tmp_path/'journal.sqlite'
    j,l,p=open_pool(path,provider)
    try:p.run(context(),threading.Event())
    finally:close(j,l,p)
    def factory():
        runtime=FakeCodex(provider,'other-stable-subject' if change=='account' else 'synthetic-stable-subject')
        if change=='version':runtime.runtime_version='new-version'
        if change=='transport':runtime.codex_transport='app-server'
        if change=='executable':runtime._verified_executable=('other-synthetic-cli',2,2)
        return runtime
    j,l,p=open_pool(path,provider,binding=hashlib.sha256(b'changed-credential').hexdigest() if change=='credential' else None,factory=factory)
    try:
        with pytest.raises(SessionRecoveryError,match='account_changed|binding_changed'):
            p.run(context(task='task-two'),threading.Event())
        assert len(provider.calls)==1
    finally:close(j,l,p)


def test_rooms_and_permission_epochs_are_isolated_and_stale_epoch_cannot_restore(tmp_path):
    provider=Provider();j,l,p=open_pool(tmp_path/'journal.sqlite',provider)
    try:
        p.run(context(),threading.Event());one=values(j)[0]['session_id']
        p.run(context(room='room-two'),threading.Event())
        assert provider.calls[-1][1] is None
        newer=context(task='task-new');newer['membership_revision']=2
        p.run(newer,threading.Event());assert provider.calls[-1][1] is None
        with pytest.raises(SessionRecoveryError,match='scope_stale'):
            p.run(context(task='stale'),threading.Event())
        assert len(provider.calls)==3 and one not in [row['session_id'] for row in values(j)]
    finally:close(j,l,p)


@pytest.mark.parametrize('state',['call_started','uncertain'])
def test_interrupted_checkpoint_never_recalls_provider(tmp_path,state):
    provider=Provider();j,l,p=open_pool(tmp_path/'journal.sqlite',provider)
    runtime=FakeCodex(provider);p.session_journal.begin(context(),runtime)
    if state=='uncertain':p.session_journal.uncertain(context(),runtime,stop_requested=True,local_shutdown=True)
    try:
        with pytest.raises(SessionRecoveryError,match='outcome_unknown'):
            p.run(context(task='another'),threading.Event())
        assert provider.calls==[]
    finally:close(j,l,p)


def test_cancelled_late_completion_is_uncertain_despite_local_shutdown_and_provider_terminal(tmp_path):
    provider=Provider();cancel=threading.Event()
    class Late(FakeCodex):
        def run(self,context,unused):
            answer=super().run(context,unused);cancel.set();return answer
    j,l,p=open_pool(tmp_path/'journal.sqlite',provider,factory=lambda:Late(provider))
    try:
        with pytest.raises(ConnectionCheckError,match='stopped'):p.run(context(),cancel)
        row=values(j)[0]
        assert row['state']=='uncertain' and row['provider_turn']=='completed' and row['local_shutdown'] is True and row['stop_requested']
    finally:close(j,l,p)


@pytest.mark.parametrize('boundary',['cancel_during_preflight','failure_before_dispatch'])
def test_previous_completion_is_not_reused_as_a_new_turn_terminal_status(tmp_path,boundary):
    provider=Provider();cancel=threading.Event()
    class Interrupted(FakeCodex):
        def preflight(self):
            if self.last_outcome=='completed' and boundary=='cancel_during_preflight':cancel.set()
        def run(self,ctx,cancellation):
            if provider.calls:raise ConnectionCheckError('synthetic_before_dispatch')
            return super().run(ctx,cancellation)
    j,l,p=open_pool(tmp_path/'journal.sqlite',provider,factory=lambda:Interrupted(provider))
    try:
        p.run(context(),threading.Event())
        with pytest.raises(ConnectionCheckError):p.run(context(task='second'),cancel)
        row=values(j)[0]
        assert len(provider.calls)==1 and row['state']=='uncertain' and row['provider_turn']=='unknown'
        assert row['stop_requested']==(boundary=='cancel_during_preflight') and row['local_shutdown'] is True
        with pytest.raises(SessionRecoveryError,match='outcome_unknown'):p.run(context(task='third'),threading.Event())
        assert len(provider.calls)==1
    finally:close(j,l,p)


def test_same_room_concurrent_call_is_rejected_without_cancelling_owner(tmp_path):
    provider=Provider();entered=threading.Event();release=threading.Event();errors=[]
    class Slow(FakeCodex):
        def run(self,ctx,cancel):
            entered.set();assert release.wait(5);return super().run(ctx,cancel)
    j,l,p=open_pool(tmp_path/'journal.sqlite',provider,factory=lambda:Slow(provider))
    def run():
        try:p.run(context(),threading.Event())
        except Exception as error:errors.append(type(error).__name__)
    thread=threading.Thread(target=run)
    try:
        thread.start();assert entered.wait(5)
        with pytest.raises(ConnectionCheckError,match='busy'):p.run(context(task='concurrent'),threading.Event())
        release.set();thread.join(5)
        assert not errors and len(provider.calls)==1
        with pytest.raises(ConnectionCheckError,match='already_running'):ConnectorLease(j.path).acquire()
    finally:
        release.set();thread.join(5);close(j,l,p)


def test_closed_or_foreign_journal_owner_is_rejected(tmp_path):
    provider=Provider();j,l,p=open_pool(tmp_path/'journal.sqlite',provider)
    try:
        l.close()
        with pytest.raises(SessionRecoveryError,match='owner_required'):p.run(context(),threading.Event())
        assert provider.calls==[]
    finally:close(j,l,p)


def test_journal_contains_only_digests_native_ids_and_state_not_secrets(tmp_path):
    provider=Provider();j,l,p=open_pool(tmp_path/'journal.sqlite',provider)
    try:
        ctx=context();ctx['inputs']=[{'text':'PRIVATE_CREDENTIAL and private prompt'}]
        p.run(ctx,threading.Event())
        serialized=json.dumps(values(j))
        for forbidden in ['PRIVATE_CREDENTIAL','private prompt','synthetic-stable-subject','synthetic-cli','room-one','task-one']:
            assert forbidden not in serialized
        status_json=json.dumps(session_status(j))
        assert '"session_id"' not in status_json and values(j)[0]['session_id'] not in status_json
    finally:close(j,l,p)


def test_invalid_or_secret_native_id_is_never_committed_as_completed(tmp_path):
    provider=Provider()
    class Bad(FakeCodex):
        def run(self,ctx,cancel):
            answer=super().run(ctx,cancel);self.session_id='PRIVATE_CREDENTIAL';return answer
    j,l,p=open_pool(tmp_path/'journal.sqlite',provider,factory=lambda:Bad(provider))
    try:
        with pytest.raises(SessionRecoveryError,match='identifier_invalid'):p.run(context(),threading.Event())
        assert values(j)[0]['state']=='uncertain' and 'PRIVATE_CREDENTIAL' not in json.dumps(values(j))
    finally:close(j,l,p)


def test_result_saved_restart_replays_only_same_completion_key_not_model(fixture,tmp_path):
    import httpx
    from talkabout.worker import Worker
    from tests.test_connector_verification import LocalClient
    store,core,rid=fixture;provider=Provider();path=tmp_path/'worker.sqlite'
    j,l,p=open_pool(path,provider,subject=None)
    client=LocalClient(store);original=client.request;keys=[]
    def lose_response(method,resource,body=None,key=None,params=None):
        value=original(method,resource,body,key,params)
        if method=='POST' and resource.endswith('/complete'):
            keys.append(key)
            raise httpx.ConnectError('synthetic completion response lost')
        return value
    client.request=lose_response
    try:
        worker=Worker(client,j,p)
        with pytest.raises(httpx.ConnectError):worker.step()
        assert j.next()[1]=='result_saved' and len(provider.calls)==1
    finally:
        close(j,l,p);client.http.close()
    j,l,p=open_pool(path,provider,subject=None)
    client=LocalClient(store);original=client.request
    def submit(method,resource,body=None,key=None,params=None):
        if method=='POST' and resource.endswith('/complete'):keys.append(key)
        return original(method,resource,body,key,params)
    client.request=submit
    try:
        assert Worker(client,j,p).step()=='completed'
        assert len(provider.calls)==1 and len(keys)==2 and keys[0]==keys[1]
        assert len(core.read('fixture-human',rid)['messages'])==1
        assert not p.runtimes  # No provider session restoration needed for saved results.
    finally:
        close(j,l,p);client.http.close()


def test_recovery_guidance_is_fixed_bilingual_and_never_echoes_unknown_text(tmp_path):
    assert recovery_guidance('PRIVATE_TOKEN arbitrary provider error') is None
    provider=Provider();j,l,p=open_pool(tmp_path/'journal.sqlite',provider,subject=None)
    try:
        assert session_status(j)['recovery'] is None
        p.run(context(),threading.Event())
        assert session_status(j)['recovery']['code']=='provider_session_identity_unavailable'
        with j.db:
            key=j.db.execute("SELECT k FROM settings WHERE k LIKE 'provider_session:%'").fetchone()[0]
            row=values(j)[0];row['state']='uncertain';p.session_journal._write(j.db,key,row)
        guidance=session_status(j)['recovery']
        assert guidance['code']=='provider_session_outcome_unknown' and guidance['automatic_retry'] is False
        for code in ('provider_session_result_recovery_required','provider_session_binding_changed'):
            explanation=recovery_guidance(code)
            assert all(explanation[key][lang] for key in ('reason','next_action') for lang in ('ko','en'))
        j.set_setting('provider_session_last_error','PRIVATE_TOKEN')
        assert 'PRIVATE_TOKEN' not in json.dumps(session_status(j))
    finally:close(j,l,p)
