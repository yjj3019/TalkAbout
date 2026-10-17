"""Synthetic metadata, injected errors and owned file handles; no providers."""
import errno
import io
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from talkabout.adapters import AdapterError
from talkabout.agent_runtime import StructuredRuntime, _JSONLTransport
from talkabout.trial_observation import TrialRecord, TrialRecordError, safe_io_failure, summarize_attempt


def record(tmp_path):
    (tmp_path/'attempt.json').write_text(json.dumps({'calls':[{'ordinal':3,'state':'reserved'}]}))
    return TrialRecord(tmp_path,'attempt.json')


@pytest.mark.parametrize('row,count', [
    ({'state':'reserved'},None),
    ({'state':'failed','native_process_starts':0},None),
    ({'state':'reserved','native_launch_state':'requested'},None),
    ({'state':'reserved','native_launch_state':'started'},1),
    ({'state':'failed','native_launch_state':'not_requested'},0),
    ({'state':'completed','native_process_starts':1,'outcome':'completed'},1),
])
def test_missing_or_incomplete_observation_is_never_default_zero(row,count):
    result=summarize_attempt(row)
    assert result['native_process_starts']==count and result['conservative_reservations']==1
    if row['state']=='reserved':assert not result['completion_recorded']


def test_safe_permission_context_omits_filename_message_and_traceback():
    error=PermissionError(errno.EACCES,'SYNTHETIC_PRIVATE_MESSAGE','SYNTHETIC_PRIVATE_PATH')
    error.winerror=32
    result=safe_io_failure(error,'replace')
    assert result=={'operation':'replace','category':'permission_denied','errno':13,'winerror':32}
    assert 'SYNTHETIC' not in json.dumps(result)


def test_runtime_keeps_safe_record_operation_and_runtime_event_separate():
    def observe(stage):
        raise TrialRecordError(PermissionError(13,'SYNTHETIC_PRIVATE_MESSAGE','SYNTHETIC_PRIVATE_PATH'),'replace')
    runtime=StructuredRuntime('codex',executable='synthetic.exe',observer=observe)
    with pytest.raises(AdapterError,match='runtime_observation_failed'):
        runtime._observe('native_started')
    assert runtime.native_launch_state=='started'
    assert runtime.last_observation_error['operation']=='replace'
    assert runtime.last_observation_error['event']=='native_started'
    assert 'SYNTHETIC' not in json.dumps(runtime.last_observation_error)


def test_read_and_replace_faults_have_distinct_safe_operation(tmp_path,monkeypatch):
    store=record(tmp_path)
    def denied(*args,**kwargs):raise PermissionError(13,'SYNTHETIC_PRIVATE_MESSAGE','SYNTHETIC_PRIVATE_PATH')
    with monkeypatch.context() as patch:
        patch.setattr(Path,'open',denied)
        with pytest.raises(TrialRecordError) as caught:store._read()
        assert caught.value.safe['operation']=='read'
    with monkeypatch.context() as patch:
        patch.setattr('talkabout.trial_observation.os.replace',denied)
        with pytest.raises(TrialRecordError) as caught:store.observe(3,'native_start_requested')
        assert caught.value.safe['operation']=='replace'
        assert 'SYNTHETIC' not in str(caught.value)
    assert store.read()['calls'][0]['state']=='reserved'
    assert store.path.with_name('attempt.json.pending').exists()  # preserve, do not erase evidence


def test_reserved_native_start_and_result_are_separate_durable_events(tmp_path):
    store=record(tmp_path)
    assert summarize_attempt(store.read()['calls'][0])['native_process_starts'] is None
    store.observe(3,'native_start_requested')
    assert summarize_attempt(store.read()['calls'][0])['native_process_starts'] is None
    store.observe(3,'native_started')
    row=store.read()['calls'][0]
    assert summarize_attempt(row)['native_process_starts']==1 and row['state']=='reserved'
    store.observe(3,'native_result_observed')
    assert not summarize_attempt(store.read()['calls'][0])['completion_recorded']


@pytest.mark.skipif(os.name!='nt',reason='Windows owned-file sharing semantics')
def test_uncoordinated_reader_reproduces_windows_replace_denial(tmp_path):
    store=record(tmp_path)
    # No ACL changes: this owned synthetic read handle denies delete/rename.
    with store.path.open('rb'):
        with pytest.raises(TrialRecordError) as caught:
            store.observe(3,'native_start_requested')
    assert caught.value.safe['operation']=='replace'
    assert caught.value.safe['category']=='permission_denied'
    assert store.read()['calls'][0]['state']=='reserved'


def test_cooperating_reader_and_writer_do_not_overlap_open_file(tmp_path):
    store=record(tmp_path);entered=threading.Event();done=threading.Event();errors=[]
    def write():
        entered.set()
        try:store.observe(3,'native_start_requested')
        except Exception as error:errors.append(type(error).__name__)
        finally:done.set()
    with store.locked(),store.path.open('rb'):
        thread=threading.Thread(target=write);thread.start();assert entered.wait(1)
        assert not done.is_set()
    thread.join(3)
    assert done.is_set() and not errors
    assert store.read()['calls'][0]['native_launch_state']=='requested'


def test_observer_failure_before_and_after_process_creation_is_distinguishable():
    for fail_at,starts,launch in [('native_start_requested',0,'not_requested'),('native_started',1,'started')]:
        runtime=StructuredRuntime('codex',executable='synthetic.exe',observer=lambda stage:None)
        def observe(stage):
            if stage==fail_at:raise PermissionError(13,'SYNTHETIC_PRIVATE_MESSAGE','SYNTHETIC_PRIVATE_PATH')
        runtime.observer=observe
        class Supervisor:
            count=0
            def start(self,*args,**kwargs):
                self.count+=1
                return SimpleNamespace(stdin=io.BytesIO(),stdout=io.BytesIO(),stderr=io.BytesIO())
            def stop(self):return True
        supervisor=Supervisor()
        with pytest.raises(AdapterError,match='runtime_observation_failed'):
            _JSONLTransport(supervisor,['synthetic'],'.',{},threading.Event(),time.monotonic()+1,observer=runtime._observe)
        assert supervisor.count==starts and runtime.native_launch_state==launch
        assert runtime.last_observation_error['event']==fail_at
        assert 'SYNTHETIC' not in json.dumps(runtime.last_observation_error)
        assert supervisor.stop() is True


@pytest.mark.parametrize('fail_at,expected', [('native_start_requested',0),('native_started',1)])
def test_runtime_observation_failure_closes_owned_supervisor_without_replay(monkeypatch,fail_at,expected):
    import sys
    class Supervisor:
        starts=0
        stopped=False
        def start(self,*args,**kwargs):
            self.starts+=1
            return SimpleNamespace(stdin=io.BytesIO(),stdout=io.BytesIO(),stderr=io.BytesIO())
        def stop(self):self.stopped=True;return True
    supervisor=Supervisor()
    monkeypatch.setattr('talkabout.agent_runtime.ProcessSupervisor',lambda:supervisor)
    def observe(stage):
        if stage==fail_at:raise PermissionError(13,'SYNTHETIC_PRIVATE_MESSAGE','SYNTHETIC_PRIVATE_PATH')
    runtime=StructuredRuntime('codex',executable=sys.executable,observer=observe)
    monkeypatch.setattr(runtime,'_ensure_preflight',lambda cancel:None)
    monkeypatch.setattr(runtime,'probe',lambda:{'available':True,'reason':None})
    monkeypatch.setattr(runtime,'_check_codex_exec_auth',lambda *args:None)
    try:
        with pytest.raises(AdapterError,match='runtime_observation_failed'):
            runtime.run({'room_id':'synthetic-room'},threading.Event())
        assert supervisor.starts==expected and supervisor.stopped
        # A constructor can fail after start, before the old calls counter moves.
        assert runtime.calls==0
        summary=summarize_attempt({'state':'failed','native_launch_state':runtime.native_launch_state})
        assert summary['native_process_starts']==expected
        with pytest.raises(AdapterError,match='runtime_outcome_unknown'):
            runtime.run({'room_id':'synthetic-room'},threading.Event())
        assert supervisor.starts==expected
    finally:runtime.shutdown()
