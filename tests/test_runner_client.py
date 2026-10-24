"""Isolated fake controls/runtimes; no DPAPI, real credential or provider use."""
import json
import threading

import pytest

from talkabout.runner_client import GrantedRuntime,RunnerError,RunnerLedger,WindowsGrantVault


class FakeControl:
    def __init__(self,*,lost_reservation=False,lost_outcome=False):
        self.calls=[];self.lost_reservation=lost_reservation;self.lost_outcome=lost_outcome
    def request(self,method,path,body=None,key=None):
        self.calls.append((method,path,body,key))
        assert key is None
        if path=='runner/reservations':
            if self.lost_reservation:raise OSError('synthetic response loss')
            return {'execute':True}
        if self.lost_outcome:raise OSError('synthetic outcome response loss')
        return {'accepted':True}


class FakeRuntime:
    def __init__(self):self.calls=0
    def run(self,context,cancel):self.calls+=1;return 'Synthetic result'


def context():
    return {'room_id':'fixture-room','generation':1,'membership_revision':2,
            'task_id':'fixture-task','task_attempt':1,'purpose':'proposal'}


def test_vault_construction_is_inert_and_fake_protection_never_saves_plaintext(tmp_path):
    path=tmp_path/'synthetic.vault'
    protect=lambda value:b'FAKE:'+bytes(byte^0x5a for byte in value)
    unprotect=lambda value:bytes(byte^0x5a for byte in value[5:])
    vault=WindowsGrantVault(path,protect=protect,unprotect=unprotect)
    assert not path.exists()
    value={'control_token':'SYNTHETIC_ONLY_VALUE'}
    vault.write(value)
    assert b'SYNTHETIC_ONLY_VALUE' not in path.read_bytes()
    assert vault.read()==value
    original=path.read_bytes()
    broken=WindowsGrantVault(path,protect=lambda value:value)
    with pytest.raises(RunnerError,match='secure_vault_unavailable'):broken.write({'other':'SYNTHETIC'})
    assert path.read_bytes()==original


def test_reserved_call_cannot_repeat_after_reconstructing_ledger_and_runtime(tmp_path):
    path=tmp_path/'worker.sqlite';client=FakeControl();runtime=FakeRuntime()
    wrapped=GrantedRuntime(runtime,client,RunnerLedger(path),'grant','fixture-room')
    assert wrapped.run(context(),threading.Event())=='Synthetic result'
    restored=GrantedRuntime(runtime,client,RunnerLedger(path),'grant','fixture-room')
    with pytest.raises(RunnerError,match='runner_call_already_reserved'):
        restored.run(context(),threading.Event())
    assert runtime.calls==1
    assert sum(call[1]=='runner/reservations' for call in client.calls)==1


def test_unknown_reservation_never_enters_runtime_or_retries_after_restart(tmp_path):
    path=tmp_path/'worker.sqlite';client=FakeControl(lost_reservation=True);runtime=FakeRuntime()
    with pytest.raises(OSError):
        GrantedRuntime(runtime,client,RunnerLedger(path),'grant','fixture-room').run(context(),threading.Event())
    assert runtime.calls==0
    with pytest.raises(RunnerError,match='runner_call_already_reserved'):
        GrantedRuntime(runtime,FakeControl(),RunnerLedger(path),'grant','fixture-room').run(context(),threading.Event())
    assert runtime.calls==0
    assert RunnerLedger(path).pending_outcomes()[0][1]=='uncertain'


def test_status_delivery_failure_preserves_result_and_recovers_only_metadata(tmp_path):
    path=tmp_path/'worker.sqlite';client=FakeControl(lost_outcome=True);runtime=FakeRuntime()
    assert GrantedRuntime(runtime,client,RunnerLedger(path),'grant','fixture-room').run(context(),threading.Event())=='Synthetic result'
    assert RunnerLedger(path).pending_outcomes()[0][1]=='completed'
    recovered=FakeControl()
    assert GrantedRuntime(runtime,recovered,RunnerLedger(path),'grant','fixture-room').flush_outcomes() is True
    assert all(call[1].endswith('/outcome') for call in recovered.calls)
    assert runtime.calls==1 and RunnerLedger(path).pending_outcomes()==[]


def test_room_mismatch_and_oversize_stop_before_reservation_or_runtime(tmp_path):
    client=FakeControl();runtime=FakeRuntime();ledger=RunnerLedger(tmp_path/'worker.sqlite')
    wrapped=GrantedRuntime(runtime,client,ledger,'grant','fixture-room')
    with pytest.raises(RunnerError,match='runner_room_mismatch'):
        wrapped.run({**context(),'room_id':'other-room'},threading.Event())
    with pytest.raises(ValueError,match='invalid_runtime_budget'):
        wrapped.run({**context(),'padding':'x'*200000},threading.Event())
    assert client.calls==[] and runtime.calls==0
