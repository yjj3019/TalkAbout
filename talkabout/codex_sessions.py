"""Opt-in local Codex checkpoints. No auth-file access or provider dispatch."""
from contextlib import closing, contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3


class SessionRecoveryError(Exception):
    pass


_ERRORS=frozenset('provider_session_'+suffix for suffix in (
    'identifier_invalid','binding_unavailable','owner_required','codex_only','scope_missing','profile_unverified',
    'record_invalid','outcome_unknown','binding_changed','account_changed','scope_stale','identity_unavailable',
    'result_recovery_required','terminal_unconfirmed','completion_fenced'))


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',value):
        raise SessionRecoveryError('provider_session_identifier_invalid')
    return value


class CodexSessionJournal:
    """Use the existing settings table and the journal's OS-held writer lease.

    Only a trusted adapter's verified stable provider subject may enable resume.
    Current native adapters intentionally supply None: email/auth/home do not
    establish that identity. No caller/CLI option accepts a guessed subject.
    """
    def __init__(self,journal,lease,credential_binding):
        if not isinstance(credential_binding,str) or not re.fullmatch('[a-f0-9]{64}',credential_binding):
            raise SessionRecoveryError('provider_session_binding_unavailable')
        self.path=str(Path(journal.path).resolve())
        self.lease=lease
        self.binding=credential_binding
        self._owned()

    def _owned(self):
        handle=getattr(self.lease,'file',None)
        if handle is None or handle.closed or str(Path(self.lease.path).resolve())!=self.path+'.worker-lock':
            raise SessionRecoveryError('provider_session_owner_required')

    @contextmanager
    def _transaction(self):
        self._owned()
        with closing(sqlite3.connect(self.path,timeout=1)) as connection,connection:
            connection.execute('PRAGMA synchronous=FULL')
            connection.execute('BEGIN IMMEDIATE')
            yield connection

    def _scope(self,context,runtime):
        if getattr(runtime,'kind',None)!='codex':
            raise SessionRecoveryError('provider_session_codex_only')
        for field in ('generation','membership_revision','task_attempt'):
            if type(context.get(field)) is not int or context[field]<0:
                raise SessionRecoveryError('provider_session_scope_missing')
        room=_identifier(context.get('room_id'));task=_identifier(context.get('task_id'))
        profile=[runtime.kind,getattr(runtime,'auth_mode',None),getattr(runtime,'codex_transport',None),
                 getattr(runtime,'runtime_version',None),getattr(runtime,'_verified_executable',None)]
        if not profile[3] or not profile[4]:
            raise SessionRecoveryError('provider_session_profile_unverified')
        subject=getattr(runtime,'_verified_account_subject',None)
        account=_digest(subject) if isinstance(subject,str) and subject else None
        return 'provider_session:'+_digest(room),{'binding':self.binding,'profile':_digest(profile),'account':account,
            'generation':context['generation'],'membership_revision':context['membership_revision'],
            'turn':_digest([task,context['task_attempt']])}

    @staticmethod
    def _read(connection,key):
        row=connection.execute('SELECT v FROM settings WHERE k=?',(key,)).fetchone()
        if not row:return None
        try:
            if len(row[0])>4096:raise ValueError()
            value=json.loads(row[0])
            if not isinstance(value,dict):raise ValueError()
            if value.get('state') not in {'call_started','completed','uncertain'}:raise ValueError()
            for name in ('generation','membership_revision'):
                if type(value.get(name)) is not int or value[name]<0:raise ValueError()
            for name in ('binding','profile','turn'):
                if not isinstance(value.get(name),str) or not re.fullmatch('[a-f0-9]{64}',value[name]):raise ValueError()
            if 'account' not in value:raise ValueError()
            if value['account'] is not None and not re.fullmatch('[a-f0-9]{64}',value['account']):raise ValueError()
            return value
        except (ValueError,TypeError):
            raise SessionRecoveryError('provider_session_record_invalid') from None

    @staticmethod
    def _write(connection,key,value):
        connection.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps(value,allow_nan=False)))

    def begin(self,context,runtime):
        key,scope=self._scope(context,runtime);checkpoint=None
        with self._transaction() as connection:
            old=self._read(connection,key)
            if old:
                if old['state']!='completed':
                    raise SessionRecoveryError('provider_session_outcome_unknown')
                if any(old[name]!=scope[name] for name in ('binding','profile')):
                    raise SessionRecoveryError('provider_session_binding_changed')
                if old['account'] is not None and old['account']!=scope['account']:
                    raise SessionRecoveryError('provider_session_account_changed')
                epoch=(scope['generation'],scope['membership_revision'])
                previous=(old['generation'],old['membership_revision'])
                if epoch<previous:raise SessionRecoveryError('provider_session_scope_stale')
                if epoch==previous:
                    if scope['account'] is None or old['account'] is None:
                        raise SessionRecoveryError('provider_session_identity_unavailable')
                    if scope['turn']==old['turn']:
                        raise SessionRecoveryError('provider_session_result_recovery_required')
                    checkpoint={name:_identifier(old.get(name)) for name in ('session_id','thread_id')}
            self._write(connection,key,{**scope,'state':'call_started','provider_turn':'unknown','local_shutdown':None})
        return checkpoint

    def complete(self,context,runtime):
        key,scope=self._scope(context,runtime)
        if getattr(runtime,'last_outcome',None)!='completed':
            raise SessionRecoveryError('provider_session_terminal_unconfirmed')
        ids={name:_identifier(getattr(runtime,name,None)) for name in ('session_id','thread_id')}
        if any(secret and secret in value for secret in getattr(runtime,'blocked_values',()) for value in ids.values()):
            raise SessionRecoveryError('provider_session_identifier_invalid')
        with self._transaction() as connection:
            old=self._read(connection,key)
            if not old or old.get('state')!='call_started' or any(old[name]!=scope[name] for name in scope):
                raise SessionRecoveryError('provider_session_completion_fenced')
            self._write(connection,key,{**scope,**ids,'state':'completed','provider_turn':'completed','local_shutdown':None,
                'resume_blocked_reason':'provider_session_identity_unavailable' if scope['account'] is None else None})
            connection.execute("DELETE FROM settings WHERE k='provider_session_last_error'")

    def uncertain(self,context,runtime,*,stop_requested,local_shutdown):
        key,scope=self._scope(context,runtime)
        with self._transaction() as connection:
            old=self._read(connection,key)
            if old and old['state']=='call_started' and old['turn']==scope['turn']:
                old.update(state='uncertain',provider_turn='completed' if getattr(runtime,'last_outcome',None)=='completed' else 'unknown',
                           stop_requested=bool(stop_requested),local_shutdown=local_shutdown)
                self._write(connection,key,old)

    def blocked(self,error):
        code=str(error)
        if code not in _ERRORS:code='provider_session_record_invalid'
        with self._transaction() as connection:
            self._write(connection,'provider_session_last_error',code)


def session_status(journal):
    """No native IDs, subjects, credential hashes or profile paths in CLI status."""
    result={'records':0,'completed':0,'uncertain':0,'identity_unavailable':0}
    error=journal.setting('provider_session_last_error')
    result['last_error']=error if isinstance(error,str) and error in _ERRORS else None
    for row in journal.db.execute("SELECT v FROM settings WHERE k LIKE 'provider_session:%'"):
        result['records']+=1
        try:
            value=json.loads(row[0])
            if value.get('state')=='completed':result['completed']+=1
            else:result['uncertain']+=1
            if value.get('resume_blocked_reason'):result['identity_unavailable']+=1
        except (ValueError,AttributeError):result['uncertain']+=1
    return result
