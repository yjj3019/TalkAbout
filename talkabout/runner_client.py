"""Explicit PC runner controls. Importing never links an account or starts a CLI.

Tests inject fake clients, vault protection and runtimes. Native credential
storage and model execution require a separate, explicit user action.
"""
from __future__ import annotations

from contextlib import closing
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading

from .runtime import runtime_prompt


class RunnerError(RuntimeError):
    """Fixed non-sensitive code, never a credential or provider transcript."""


def _dpapi(data, decrypt=False):
    if os.name != 'nt':raise RunnerError('windows_secure_vault_required')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    output=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[ctypes.c_void_p]
    kernel.LocalFree.restype=ctypes.c_void_p
    operation=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,
                        ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    operation.restype=wintypes.BOOL
    try:
        if not operation(ctypes.byref(source),None,None,None,None,1,ctypes.byref(output)):
            raise RunnerError('secure_vault_operation_failed')
        return ctypes.string_at(output.data,output.size)
    finally:
        ctypes.memset(buffer,0,len(buffer))
        if output.data:
            ctypes.memset(output.data,0,output.size)
            kernel.LocalFree(output.data)


class WindowsGrantVault:
    """User-bound encrypted file in an explicitly chosen existing directory.

    Construction performs no I/O. Protect/unprotect injection is for fixtures.
    """
    def __init__(self,path,*,protect=None,unprotect=None):
        self.path=Path(path)
        self.protect=protect or _dpapi
        self.unprotect=unprotect or (lambda data:_dpapi(data,True))
        self.lock=threading.RLock()

    def read(self):
        with self.lock:
            if not self.path.exists():return None
            try:
                raw=self.path.read_bytes()
                if not 1<=len(raw)<=32768:raise ValueError()
                value=json.loads(self.unprotect(raw))
                if not isinstance(value,dict):raise ValueError()
                return value
            except Exception:
                raise RunnerError('secure_vault_unavailable') from None

    def write(self,value):
        with self.lock:
            temporary=self.path.with_name(self.path.name+'.pending')
            try:
                raw=json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
                if not 1<=len(raw)<=16384:raise ValueError()
                encrypted=self.protect(raw)
                if not isinstance(encrypted,bytes) or not encrypted or encrypted==raw:raise ValueError()
                with temporary.open('wb') as stream:
                    stream.write(encrypted);stream.flush();os.fsync(stream.fileno())
                os.replace(temporary,self.path)
            except Exception:
                raise RunnerError('secure_vault_unavailable') from None


class RunnerLedger:
    """Metadata in the existing durable worker journal, never credential values."""
    def __init__(self,path):
        self.path=str(path)
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS runner_calls(id TEXT PRIMARY KEY,state TEXT NOT NULL,outcome TEXT)')

    def begin(self,reservation_id):
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM runner_calls WHERE id=?',(reservation_id,)).fetchone():
                raise RunnerError('runner_call_already_reserved')
            if db.execute('SELECT count(*) FROM runner_calls').fetchone()[0]>=64:
                raise RunnerError('runner_call_history_capacity')
            db.execute('INSERT INTO runner_calls VALUES(?,?,NULL)',(reservation_id,'reservation_started'))

    def set_outcome(self,reservation_id,state):
        if state not in {'completed','uncertain'}:raise RunnerError('invalid_runner_outcome')
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('UPDATE runner_calls SET state=?,outcome=? WHERE id=?',(state,state,reservation_id))

    def pending_outcomes(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute('SELECT id,outcome FROM runner_calls WHERE outcome IS NOT NULL ORDER BY id').fetchall()

    def acknowledge_outcome(self,reservation_id):
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('UPDATE runner_calls SET outcome=NULL WHERE id=?',(reservation_id,))


class GrantedRuntime:
    """Reserve the server budget before the existing runtime; no automatic retry."""
    def __init__(self,runtime,control_client,ledger,grant_id,room_id):
        self.runtime,self.client,self.ledger=runtime,control_client,ledger
        self.grant_id,self.room_id=grant_id,room_id

    def __getattr__(self,name):return getattr(self.runtime,name)

    def run(self,context,cancel):
        runtime_prompt(context)
        purpose='connection_test' if context.get('purpose')=='connection_test' else 'task'
        if purpose=='task' and self.room_id is not None and context.get('room_id')!=self.room_id:
            raise RunnerError('runner_room_mismatch')
        identity=[self.grant_id,purpose,context.get('room_id'),context.get('generation'),
                  context.get('membership_revision'),context.get('task_id'),context.get('task_attempt'),
                  context.get('challenge') if purpose=='connection_test' else None]
        reservation_id=hashlib.sha256(json.dumps(identity,separators=(',',':')).encode()).hexdigest()
        self.ledger.begin(reservation_id)
        body={'reservation_id':reservation_id,'purpose':purpose,
              'room_id':self.room_id if purpose=='connection_test' else context.get('room_id')}
        if purpose=='task':
            body.update(task_id=context.get('task_id'),attempt=context.get('task_attempt'),
                        generation=context.get('generation'),membership_revision=context.get('membership_revision'))
        try:
            # A missing key prevents APIClient from retrying this reservation POST.
            reserved=self.client.request('POST','runner/reservations',body)
            if reserved.get('execute') is not True:raise RunnerError('runner_reservation_not_executable')
            if cancel.is_set():raise RunnerError('runner_cancelled_before_call')
            result=self.runtime.run(context,cancel)
        except BaseException:
            self.ledger.set_outcome(reservation_id,'uncertain')
            raise
        self.ledger.set_outcome(reservation_id,'completed')
        # A status-only delivery failure must not discard the available result.
        self.flush_outcomes()
        return result

    def flush_outcomes(self):
        for reservation_id,outcome in self.ledger.pending_outcomes():
            try:
                self.client.request('POST','runner/reservations/'+reservation_id+'/outcome',{'state':outcome})
            except Exception:
                return False
            self.ledger.acknowledge_outcome(reservation_id)
        return True
