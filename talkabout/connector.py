"""Explicit, bounded runtime test. Transport authentication is not provider proof."""
import hashlib
import json
import threading
import time
import uuid

from .runtime import bounded_call, check_deadline


class ConnectionCheckError(Exception):
    pass


def verify_runtime(client, journal, runtime, kind, credential, deadline, retry_uncertain=False):
    """Run once; retain uncertain calls and replay only a saved completion, not a model turn.

    The server receipt is an authenticated worker report, not vendor attestation.
    No credential, provider transcript, or provider session id is printed.
    """
    check_deadline(deadline)
    if journal.stop_requested():
        raise ConnectionCheckError('connector_stopped')
    actor = client.request('GET', 'agents/me')
    if actor.get('kind') != 'agent':
        raise ConnectionCheckError('agent_credential_required')
    # A different token/agent must not reuse another connection's local test result.
    binding = hashlib.sha256((actor['id'] + ':' + kind + ':' + credential).encode()).hexdigest()
    runtime._connection_binding = binding
    key = 'runtime_check:' + binding
    prior = journal.setting(key)
    # Retain the original credential key so upgrading cannot bypass an uncertain
    # old call. Only completed receipts gain profile-bound reuse eligibility.
    profile=[getattr(runtime,'auth_mode','native'),getattr(runtime,'codex_transport',None),
             getattr(runtime,'runtime_version',None),getattr(runtime,'_verified_executable',None)]
    profile_digest=hashlib.sha256(json.dumps(profile,separators=(',',':')).encode()).hexdigest()
    same_profile=bool(prior and prior.get('profile_digest')==profile_digest)
    receipt=actor.get('runtime_check') or {}
    if (not retry_uncertain and same_profile and prior.get('state')=='completed'
            and receipt.get('id')==prior.get('id') and receipt.get('runtime')==kind
            and receipt.get('status')=='passed' and receipt.get('verification')=='worker_reported_test'
            and receipt.get('expires_at',0)>time.time()):
        runtime.shutdown()
        return receipt,0
    if prior and prior.get('state') in {'call_started', 'uncertain'} and not retry_uncertain:
        raise ConnectionCheckError('runtime_check_outcome_unknown_use_retry_check_explicitly')
    if prior and prior.get('state')=='response_saved' and not same_profile and not retry_uncertain:
        raise ConnectionCheckError('runtime_check_profile_changed_use_retry_check_explicitly')
    if prior and prior.get('state') == 'response_saved' and not (retry_uncertain and (prior['expires_at'] <= time.time() or not same_profile)):
        if prior['expires_at'] <= time.time():
            raise ConnectionCheckError('runtime_check_expired_use_retry_check_explicitly')
        check_deadline(deadline)
        receipt = client.request('POST', 'agents/me/runtime-checks/' + prior['id'] + '/complete',
                                 prior['completion'], prior['complete_key'])
        if receipt.get('status') != 'passed':
            raise ConnectionCheckError('runtime_check_not_passed')
        with journal.db:
            journal.set_setting(key, {'state': 'completed', 'id': prior['id'],'profile_digest':profile_digest})
        return receipt, 0
    # Only an exact current receipt can be reused; expired/replaced records need a fresh test.
    nonce = str(uuid.uuid4())
    check = client.request('POST', 'agents/me/runtime-checks', {'runtime': kind}, 'runtime-check:' + nonce)
    pending = {'state': 'call_started', 'id': check['id'], 'expires_at': check['expires_at'],'profile_digest':profile_digest,
               'complete_key': 'runtime-check-complete:' + nonce}
    with journal.db:
        journal.set_setting(key, pending)
    cancel, finished = threading.Event(), threading.Event()
    def watch_stop():
        while not finished.wait(.02):
            try:
                if journal.stop_requested():
                    cancel.set()
                    return
            except Exception:
                cancel.set()
                return
    watcher = threading.Thread(target=watch_stop, daemon=True)
    watcher.start()
    try:
        if journal.stop_requested():
            raise ConnectionCheckError('connector_stopped')
        response = bounded_call(runtime, {'purpose': 'connection_test', 'room_id': '__runtime_check__',
                                          'prompt': check['prompt'], 'challenge': check['challenge']},
                                cancel, min(90, max(0, deadline - time.monotonic())))  # real Claude cold start measured 36.6s
        check_deadline(deadline)
        if journal.stop_requested():
            raise ConnectionCheckError('connector_stopped')
        if response.strip() != check['challenge'] or not getattr(runtime, 'session_id', None):
            raise ConnectionCheckError('runtime_check_response_invalid')
        completion = {'response': response.strip(), 'session_id': runtime.session_id}
        version = getattr(runtime, 'runtime_version', None)
        if version:
            completion['runtime_version'] = version
        pending.update(state='response_saved', completion=completion)
        with journal.db:
            journal.set_setting(key, pending)
        receipt = client.request('POST', 'agents/me/runtime-checks/' + check['id'] + '/complete',
                                 completion, pending['complete_key'])
        if receipt.get('status') != 'passed':
            raise ConnectionCheckError('runtime_check_not_passed')
        with journal.db:
            journal.set_setting(key, {'state': 'completed', 'id': check['id'],'profile_digest':profile_digest})
        return receipt, 1
    except Exception:
        # A saved response is safe to resubmit under the same key. Never automatically recall AI.
        if pending['state'] != 'response_saved':
            pending['state'] = 'uncertain'
            with journal.db:
                journal.set_setting(key, pending)
            try:
                client.request('POST', 'agents/me/runtime-checks/' + check['id'] + '/fail',
                               {'error_code': 'runtime_failed'}, 'runtime-check-fail:' + nonce)
            except Exception:
                pass
        raise
    finally:
        finished.set()
        watcher.join(timeout=1.2)
        runtime.shutdown()


class RoomRuntimePool:
    """Bounded per-room/generation/membership sessions; never share native context across rooms."""
    def __init__(self, factory, limit=8, session_journal=None):
        self.factory, self.limit = factory, limit
        self.runtimes = {}
        self.calls = 0
        self.deadline = None
        self.closed = False
        self.lock = threading.RLock()
        self.session_journal = session_journal
        self.active = set()

    def run(self, context, cancel):
        with self.lock:
            if self.closed or cancel.is_set():
                raise ConnectionCheckError('connector_stopped')
            key = (context.get('room_id'), context.get('generation'), context.get('membership_revision'))
            if not key[0]:
                raise ConnectionCheckError('connector_room_required')
            if self.session_journal is not None and key in self.active:
                raise ConnectionCheckError('connector_session_busy')
            for old in list(self.runtimes):
                if old[0] == key[0] and old != key:
                    try:
                        stopped = self.runtimes[old].shutdown() is True
                    except Exception:
                        stopped = False
                    if not stopped:
                        self.closed = True
                        raise ConnectionCheckError('connector_runtime_stop_unconfirmed')
                    del self.runtimes[old]
            created = key not in self.runtimes
            if created:
                if len(self.runtimes) >= self.limit:
                    raise ConnectionCheckError('connector_session_limit')
                self.runtimes[key] = self.factory()
                if self.deadline is not None:
                    self.runtimes[key].set_deadline(self.deadline)
            runtime = self.runtimes[key]
            if self.session_journal is not None:
                try:
                    runtime.preflight()
                    checkpoint = self.session_journal.begin(context,runtime)
                    # A prior turn's terminal outcome is not evidence for this turn.
                    runtime.last_outcome = 'not_started'
                    if checkpoint:
                        runtime.session_id=checkpoint['session_id']
                        runtime.thread_id=checkpoint['thread_id']
                    self.active.add(key)
                except Exception as error:
                    try:self.session_journal.blocked(error)
                    except Exception:pass
                    if created:runtime.shutdown()
                    raise
            self.calls += 1
        if self.session_journal is None:
            return runtime.run(context, cancel)
        try:
            if self.closed or cancel.is_set():
                raise ConnectionCheckError('connector_stopped')
            result=runtime.run(context,cancel)
            if self.closed or cancel.is_set():
                raise ConnectionCheckError('connector_stopped')
            self.session_journal.complete(context,runtime)
            return result
        except BaseException:
            try:stopped=runtime.shutdown() is True
            except Exception:stopped=False
            try:self.session_journal.uncertain(context,runtime,stop_requested=cancel.is_set(),local_shutdown=stopped)
            except Exception:pass  # call_started remains a durable no-replay fence.
            raise
        finally:
            with self.lock:self.active.discard(key)

    def set_deadline(self, deadline):
        self.deadline = deadline
        for runtime in self.runtimes.values():
            runtime.set_deadline(deadline)

    def shutdown(self):
        with self.lock:
            self.closed = True
            runtimes = list(self.runtimes.values())
        stopped = True
        for runtime in runtimes:
            try:
                if runtime.shutdown() is not True:
                    stopped = False
            except Exception:
                stopped = False
        return stopped


class ConnectorLease:
    """OS-held exclusive writer lease for a journal. No stale PID-based unlock."""
    def __init__(self, journal_path):
        from pathlib import Path
        self.path = str(Path(journal_path).resolve()) + '.worker-lock'
        self.file = None

    def acquire(self):
        import os
        self.file = open(self.path, 'a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if self.file.seek(0, 2) == 0:
                    self.file.write(b'0'); self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            self.file.close(); self.file = None
            raise ConnectionCheckError('connector_already_running') from None
        return self

    def close(self):
        if self.file is not None:
            self.file.close(); self.file = None
