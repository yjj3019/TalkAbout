"""Shared TrialRecord and safe controller reporting with dummy files only."""
import ctypes
import json
import multiprocessing
import os
from pathlib import Path
import sys
import threading

import pytest

from talkabout.adapters import AdapterError
from talkabout.agent_runtime import StructuredRuntime
from talkabout.trial_observation import TrialRecord, TrialRecordError, safe_controller_failure, summarize_attempt


def make_store(directory, name='attempt.json'):
    (directory/name).write_text(json.dumps({'calls':[{'ordinal':3,'state':'reserved'}], 'revision':0}))
    return TrialRecord(directory,name)


def persist_failure(directory, error, operation, stage=None, attempt=None):
    """Synthetic controller: independent report, not the failed attempt JSON."""
    report=safe_controller_failure(error,operation,stage=stage,attempt=attempt)
    # Emit safe context first, even if the independent report sink also fails.
    print(json.dumps(report),file=sys.stderr,flush=True)
    target=directory/'controller.json'
    target.write_text('{}')
    controller=TrialRecord(directory,'controller.json')
    controller.update(lambda value:value.update(report))
    reread=controller.read()
    assert reread==report and reread['status']=='stopped'
    assert 'PRIVATE_SENTINEL' not in json.dumps(reread)
    return reread


def assert_location(report, operation):
    failure=report['failure']
    assert failure['operation']==operation
    location=failure['location']
    assert location['file'] in {'talkabout/trial_observation.py','tests/test_trial_controller_diagnostics.py'}
    assert not Path(location['file']).is_absolute() and location['line']>0


@pytest.mark.parametrize('operation',['read','write','replace'])
def test_controller_persists_permission_operation_and_relative_source(tmp_path,monkeypatch,operation):
    store=make_store(tmp_path);before=store.path.read_bytes()
    real_open=Path.open
    def denied(*args,**kwargs):
        error=PermissionError(13,'PRIVATE_SENTINEL_MESSAGE','PRIVATE_SENTINEL_PATH')
        error.winerror=32
        raise error
    def opened(path,*args,**kwargs):
        if operation=='read' and path==store.path or operation=='write' and path.name.endswith('.pending'):
            return denied()
        return real_open(path,*args,**kwargs)
    with monkeypatch.context() as patch:
        if operation=='replace':patch.setattr('talkabout.trial_observation.os.replace',denied)
        else:patch.setattr(Path,'open',opened)
        with pytest.raises(TrialRecordError) as caught:
            store.update(lambda value:value.update(revision=1))
    report=persist_failure(tmp_path,caught.value,'observer')
    assert_location(report,operation)
    assert report['failure']['errno']==13 and report['failure']['winerror']==32
    assert report['attempt']['native_process_starts'] is None
    assert report['attempt']['outcome']=='unknown'
    assert store.path.read_bytes()==before


def test_external_traceback_text_and_error_attributes_are_not_serialized(tmp_path):
    namespace={}
    exec(compile("def denied():\n raise PermissionError(13,'PRIVATE_SENTINEL_MESSAGE','PRIVATE_SENTINEL_PATH')",'PRIVATE_SENTINEL_EXTERNAL.py','exec'),namespace)
    try:namespace['denied']()
    except PermissionError as error:
        error.trial_cleanup_failure={'secret':'PRIVATE_SENTINEL'}
        report=persist_failure(tmp_path,error,'read')
    assert_location(report,'read')
    assert 'cleanup_failure' not in report


def test_independent_report_write_failure_keeps_original_safe_stderr(tmp_path,monkeypatch,capsys):
    try:raise PermissionError(13,'PRIVATE_SENTINEL_ORIGINAL','PRIVATE_SENTINEL_PATH')
    except PermissionError as original:
        def denied(*args,**kwargs):raise PermissionError(13,'PRIVATE_SENTINEL_REPORT','PRIVATE_SENTINEL_PATH')
        with monkeypatch.context() as patch:
            patch.setattr('talkabout.trial_observation.os.replace',denied)
            with pytest.raises(TrialRecordError) as secondary:
                persist_failure(tmp_path,original,'read')
    emitted=json.loads(capsys.readouterr().err)
    assert_location(emitted,'read')
    assert emitted['attempt']['native_process_starts'] is None and emitted['attempt']['outcome']=='unknown'
    assert 'PRIVATE_SENTINEL' not in json.dumps(emitted)
    assert secondary.value.safe['operation']=='replace'
    assert (tmp_path/'controller.json.pending').exists()


@pytest.mark.parametrize('row',[
    {'state':'reserved','outcome':'completed'},
    {'state':'failed','native_process_starts':0,'outcome':'completed'},
    {'state':'completed','outcome':'completed'},
    {'state':'reserved','native_launch_state':'started','outcome':'completed'},
    {'state':'failed','native_launch_state':'requested','outcome':'not_started'},
])
def test_inconsistent_or_missing_completion_never_becomes_success(row):
    summary=summarize_attempt(row)
    assert summary['outcome']=='unknown' and summary['conservative_reservations']==1
    if row.get('native_launch_state')!='started':assert summary['native_process_starts'] is None


@pytest.mark.parametrize('stage,launch,count',[
    ('native_start_requested','not_requested',0),
    ('native_started','started',1),
])
def test_failed_observer_keeps_live_boundary_when_record_publication_failed(tmp_path,monkeypatch,stage,launch,count):
    store=make_store(tmp_path)
    captured=[]
    def observe(event):
        try:store.observe(3,event)
        except TrialRecordError as error:
            captured.append(error)
            raise
    runtime=StructuredRuntime('codex',executable='never-executed',observer=observe)
    def denied(*args,**kwargs):raise PermissionError(13,'PRIVATE_SENTINEL_MESSAGE','PRIVATE_SENTINEL_PATH')
    with monkeypatch.context() as patch:
        patch.setattr('talkabout.trial_observation.os.replace',denied)
        with pytest.raises(AdapterError,match='runtime_observation_failed'):
            runtime._observe(stage)
    # Runtime state survives failed persistence and is the controller snapshot.
    assert runtime.native_launch_state==launch
    assert summarize_attempt(store.read()['calls'][0])['native_process_starts'] is None
    report=persist_failure(tmp_path,captured[0],'observer',stage,{'state':'failed','native_launch_state':runtime.native_launch_state,'outcome':'unknown'})
    assert_location(report,'replace')
    assert report['stage']==stage and report['native_launch_state']==launch
    assert report['attempt']['native_process_starts']==count and report['attempt']['outcome']=='unknown'
    assert runtime.shutdown()


def child_cooperating_writer(directory,entered,go,done,results):
    try:
        store=TrialRecord(directory,'attempt.json');entered.set()
        if not go.wait(15):raise RuntimeError('synthetic_start_timeout')
        for _ in range(12):
            assert isinstance(store.read()['revision'],int)
            store.update(lambda value:value.update(revision=value['revision']+1))
        results.put({'ok':True})
    except Exception as error:
        results.put(safe_controller_failure(error,'observer'))
    finally:done.set()


def test_independent_processes_share_reader_writer_lock_without_lost_updates(tmp_path):
    store=make_store(tmp_path);context=multiprocessing.get_context('spawn')
    entered=[context.Event() for _ in range(2)];done=[context.Event() for _ in range(2)]
    results=context.Queue();go=context.Event()
    children=[context.Process(target=child_cooperating_writer,args=(str(tmp_path),entered[i],go,done[i],results)) for i in range(2)]
    try:
        with store.locked(),store.path.open('rb'):
            for child in children:child.start()
            assert all(event.wait(15) for event in entered)
            go.set()
            assert not any(event.is_set() for event in done)
        for child in children:child.join(20)
        assert all(not child.is_alive() and child.exitcode==0 for child in children)
        assert [results.get(timeout=2) for _ in children]==[{'ok':True},{'ok':True}]
        row=store.read()
        assert row['revision']==24 and row['calls'][0]['state']=='reserved'
        assert summarize_attempt(row['calls'][0])['native_process_starts'] is None
        assert not store.path.with_name('attempt.json.pending').exists()
    finally:
        for child in children:
            if child.pid is not None:
                if child.is_alive():child.terminate()
                child.join(5)
        results.close();results.join_thread()


@pytest.mark.skipif(os.name!='nt',reason='Native Windows suspended creation and Job containment')
@pytest.mark.parametrize('fail_at,expected_launch,count',[
    ('native_start_requested','not_requested',0),
    ('native_started','started',1),
])
def test_real_python_child_boundary_and_record_denial_keep_controller_context(tmp_path,monkeypatch,fail_at,expected_launch,count):
    import talkabout.processes as processes
    store=make_store(tmp_path);captured=[];children=[]
    def observe(stage):
        try:
            if stage==fail_at:
                with monkeypatch.context() as patch:
                    def denied(*args,**kwargs):raise PermissionError(13,'PRIVATE_SENTINEL','PRIVATE_SENTINEL_PATH')
                    patch.setattr('talkabout.trial_observation.os.replace',denied)
                    store.observe(3,stage)
            else:store.observe(3,stage)
        except TrialRecordError as error:
            captured.append(error)
            raise
    runtime=StructuredRuntime('codex',executable=sys._base_executable,observer=observe)
    runtime.timeout=5
    monkeypatch.setattr(runtime,'_ensure_preflight',lambda cancel:None)
    monkeypatch.setattr(runtime,'probe',lambda:{'available':True,'reason':None})
    monkeypatch.setattr(runtime,'_check_codex_exec_auth',lambda *args:None)
    monkeypatch.setattr(runtime,'_environment',lambda:{k:v for k,v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','COMSPEC','PATHEXT'}})
    command=[sys._base_executable,'-I','-S','-u','-c','import time; time.sleep(10)']
    monkeypatch.setattr(runtime,'command',lambda:command)
    popen=processes.subprocess.Popen
    def start(arguments,*args,**kwargs):
        assert arguments==command  # No provider executable or command substitution.
        process=popen(arguments,*args,**kwargs);children.append(process);return process
    monkeypatch.setattr(processes.subprocess,'Popen',start)
    try:
        with pytest.raises(AdapterError,match='runtime_observation_failed'):
            runtime.run({'room_id':'synthetic-only'},threading.Event())
        assert len(children)==count and all(child.poll() is not None for child in children)
        assert runtime.calls==0 and not runtime._prompt_sent
        assert runtime.native_launch_state==expected_launch
        report=persist_failure(tmp_path,captured[0],'observer',runtime.last_observation_error['event'],{'state':'failed','native_launch_state':runtime.native_launch_state,'outcome':'unknown'})
        assert_location(report,'replace')
        # Cleanup may itself fail to publish because .pending is preserved; primary stays.
        assert report['failure']['operation']=='replace'
        assert report['stage']==fail_at
        assert report['attempt']['native_process_starts']==count
        assert report['attempt']['outcome']=='unknown'
        assert store.read()['calls'][0]['state']=='reserved'
        first_failure=dict(runtime.last_observation_error)
        with pytest.raises(AdapterError,match='runtime_outcome_unknown'):
            runtime.run({'room_id':'synthetic-only'},threading.Event())
        assert len(children)==count
        assert runtime.native_launch_state==expected_launch and runtime.last_observation_error==first_failure
    finally:assert runtime.shutdown()


@pytest.mark.skipif(os.name!='nt',reason='Windows byte lock injection')
def test_lock_denial_is_reported_without_entering_record_io(tmp_path,monkeypatch):
    import msvcrt
    store=make_store(tmp_path);before=store.path.read_bytes()
    def denied(*args,**kwargs):
        error=PermissionError(13,'PRIVATE_SENTINEL_LOCK','PRIVATE_SENTINEL_PATH')
        error.winerror=33
        raise error
    ticks=iter([0,3])
    with monkeypatch.context() as patch:
        patch.setattr(msvcrt,'locking',denied)
        patch.setattr('talkabout.trial_observation.time.monotonic',lambda:next(ticks))
        with pytest.raises(TrialRecordError) as caught:store.read()
    report=persist_failure(tmp_path,caught.value,'read')
    assert_location(report,'lock')
    assert report['failure']['winerror']==33
    assert store.path.read_bytes()==before


@pytest.mark.skipif(os.name!='nt',reason='Windows owned-file sharing semantics')
def test_uncoordinated_reader_denial_preserves_evidence_and_identifies_replace(tmp_path):
    store=make_store(tmp_path);original=store.path.read_bytes()
    with store.path.open('rb'):
        with pytest.raises(TrialRecordError) as caught:
            store.observe(3,'native_start_requested')
    report=persist_failure(tmp_path,caught.value,'observer','native_start_requested')
    assert_location(report,'replace')
    assert report['failure']['category']=='permission_denied' and report['failure']['errno']==13
    assert store.path.read_bytes()==original and store.path.with_name('attempt.json.pending').exists()
    # Evidence is preserved and further updates fail closed instead of retrying.
    with pytest.raises(TrialRecordError) as second:store.observe(3,'native_started')
    assert second.value.safe['operation']=='write'
    assert store.path.read_bytes()==original


@pytest.mark.skipif(os.name!='nt',reason='Windows owned-file sharing semantics')
def test_exclusive_read_denial_has_relative_operation_and_unknown_summary(tmp_path):
    from ctypes import wintypes as w
    store=make_store(tmp_path)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,ctypes.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
    kernel.CreateFileW.restype=w.HANDLE
    kernel.CloseHandle.argtypes=[w.HANDLE];kernel.CloseHandle.restype=w.BOOL
    handle=kernel.CreateFileW(str(store.path),0x80000000,0,None,3,0x80,None)
    assert handle and handle!=ctypes.c_void_p(-1).value
    try:
        with pytest.raises(TrialRecordError) as caught:store.read()
        report=persist_failure(tmp_path,caught.value,'read')
    finally:assert kernel.CloseHandle(handle)
    assert_location(report,'read')
    assert report['attempt']['native_process_starts'] is None and report['attempt']['outcome']=='unknown'
    assert store.read()['calls'][0]['state']=='reserved'


@pytest.mark.skipif(os.name!='nt',reason='Windows byte lock release injection')
def test_secondary_unlock_denial_does_not_erase_primary_read_failure(tmp_path,monkeypatch):
    import msvcrt
    store=make_store(tmp_path);real_lock=msvcrt.locking
    def locking(fd,mode,count):
        if mode==msvcrt.LK_UNLCK:
            raise PermissionError(13,'PRIVATE_SENTINEL_UNLOCK','PRIVATE_SENTINEL_PATH')
        return real_lock(fd,mode,count)
    def failed_read():
        try:raise PermissionError(13,'PRIVATE_SENTINEL_READ','PRIVATE_SENTINEL_PATH')
        except PermissionError as error:raise TrialRecordError(error,'read') from None
    with monkeypatch.context() as patch:
        patch.setattr(msvcrt,'locking',locking);patch.setattr(store,'_read',failed_read)
        with pytest.raises(TrialRecordError) as caught:store.read()
    report=persist_failure(tmp_path,caught.value,'read')
    assert_location(report,'read')
    assert report['cleanup_failure']['operation']=='unlock'
    assert report['cleanup_failure']['location']['file']=='tests/test_trial_controller_diagnostics.py'
