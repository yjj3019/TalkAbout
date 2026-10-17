"""Opt-in operator trial metadata. No provider, credential or permission setup."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading
import time

OBSERVATION_STAGES = frozenset({
    'workspace_preparing', 'workspace_ready', 'auth_check_started', 'auth_check_completed',
    'native_start_requested', 'native_started', 'prompt_submission_requested',
    'prompt_submitted', 'native_result_observed', 'cleanup_completed',
})


def _project_location(error):
    """Keep only a Python source location inside this checkout, never traceback text."""
    root = Path(__file__).resolve().parent.parent
    location = None
    trace = error.__traceback__
    while trace is not None:
        try:
            resolved = Path(trace.tb_frame.f_code.co_filename).resolve()
            source = resolved.relative_to(root)
            if source.suffix == '.py' and resolved.is_file():
                location = {'file': source.as_posix(), 'line': trace.tb_lineno}
        except (OSError, ValueError):
            pass
        trace = trace.tb_next
    return location


def safe_io_failure(error, operation, *, include_location=False):
    if operation not in {'lock', 'read', 'write', 'replace', 'unlock', 'observer'}:
        raise ValueError('invalid_observation_operation')
    result = {'operation': operation,
            'category': 'permission_denied' if isinstance(error, PermissionError) else 'observation_failed',
            'errno': error.errno if type(getattr(error, 'errno', None)) is int else None,
            'winerror': error.winerror if type(getattr(error, 'winerror', None)) is int else None}
    if include_location:
        result['location'] = _project_location(error)
    return result


class TrialRecordError(Exception):
    def __init__(self, error, operation):
        self.safe = safe_io_failure(error, operation, include_location=True)
        super().__init__('trial_observation_unavailable')


def summarize_attempt(attempt):
    """Missing completion/counters are unknown, never an inferred zero launch."""
    launch = attempt.get('native_launch_state')
    count = attempt.get('native_process_starts')
    if launch == 'started' or type(count) is int and count == 1:
        observed = 1
    elif launch == 'not_requested' and attempt.get('state') in {'completed', 'failed'}:
        observed = 0
    else:
        observed = None
    outcome = attempt.get('outcome')
    if outcome == 'completed' and not (attempt.get('state') == 'completed' and observed == 1):
        outcome = 'unknown'
    if outcome == 'not_started' and observed != 0:
        outcome = 'unknown'
    return {'native_process_starts': observed,
            'completion_recorded': attempt.get('state') in {'completed', 'failed'},
            'outcome': outcome if outcome in {'completed', 'unknown', 'not_started'} else 'unknown',
            'conservative_reservations': 1}


def safe_controller_failure(error, operation, *, stage=None, attempt=None):
    """For a controller's independent failure report; never stringify the error.

    Supply the last in-memory runtime state if publishing an observation failed.
    An unreadable record is an empty snapshot (unknown), not a zero-start default.
    This formats metadata only: callers must persist it outside the failing record
    and stop. It neither retries a runtime nor modifies historical trial records.
    """
    if stage is not None and stage not in OBSERVATION_STAGES:
        raise ValueError('invalid_trial_observation')
    failure = dict(error.safe) if isinstance(error, TrialRecordError) else safe_io_failure(error, operation, include_location=True)
    if failure.get('location') is None:
        failure['location'] = _project_location(error)
    snapshot = attempt if isinstance(attempt, dict) else {}
    launch = snapshot.get('native_launch_state')
    result = {'status': 'stopped', 'failure': failure, 'stage': stage,
              'native_launch_state': launch if launch in {'not_requested', 'requested', 'started'} else 'unknown',
              'attempt': summarize_attempt(snapshot)}
    cleanup = getattr(error, 'trial_cleanup_failure', None) if isinstance(error, TrialRecordError) else None
    if cleanup is not None:
        result['cleanup_failure'] = cleanup
    return result


class TrialRecord:
    """Both readers and writers use the same lock before opening the JSON file.

    Use only an explicitly selected trial directory and curated safe metadata.
    A failed replacement preserves its .pending file for operator review; no
    model retry, permission change or stale-record deletion is performed.
    """
    def __init__(self, directory, name):
        if Path(name).name != name or not name.endswith('.json'):
            raise ValueError('invalid_trial_record_name')
        self.path = Path(directory).resolve() / name
        self.lock_path = self.path.with_suffix('.lock')
        if self.path.is_symlink() or self.lock_path.is_symlink():
            raise ValueError('invalid_trial_record_path')
        self.mutex = threading.RLock()

    @contextmanager
    def locked(self):
        with self.mutex:
            try:
                handle = self.lock_path.open('a+b')
                if handle.seek(0, 2) == 0:
                    handle.write(b'0'); handle.flush()
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    until = time.monotonic() + 2
                    while True:
                        try:
                            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                            break
                        except OSError:
                            if time.monotonic() >= until:
                                raise
                            time.sleep(.01)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX)
            except OSError as error:
                if 'handle' in locals(): handle.close()
                raise TrialRecordError(error, 'lock') from None
            body_error = None
            try:
                yield
            except BaseException as error:
                body_error = error
                raise
            finally:
                try:
                    if os.name == 'nt':
                        handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle, fcntl.LOCK_UN)
                except OSError as error:
                    if body_error is None:
                        raise TrialRecordError(error, 'unlock') from None
                    # Preserve the original failing operation when release also fails.
                    if isinstance(body_error, TrialRecordError):
                        body_error.trial_cleanup_failure = safe_io_failure(error, 'unlock', include_location=True)
                finally:
                    handle.close()

    def _read(self):
        try:
            with self.path.open('rb') as source:
                raw = source.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024: raise ValueError()
            value = json.loads(raw)
            if not isinstance(value, dict): raise ValueError()
            return value
        except (OSError, ValueError) as error:
            raise TrialRecordError(error, 'read') from None

    def read(self):
        with self.locked():
            return self._read()

    def observe(self, ordinal, stage):
        if type(ordinal) is not int or not 1 <= ordinal <= 9 or stage not in OBSERVATION_STAGES:
            raise ValueError('invalid_trial_observation')
        def append(value):
            row = next(item for item in value['calls'] if item['ordinal'] == ordinal)
            events = row.setdefault('observations', [])
            if len(events) >= 16: raise ValueError('trial_observation_limit')
            events.append(stage)
            if stage == 'native_start_requested': row['native_launch_state'] = 'requested'
            if stage == 'native_started': row['native_launch_state'] = 'started'
        self.update(append)

    def update(self, action):
        with self.locked():
            value = self._read()
            result = action(value)
            pending = self.path.with_name(self.path.name + '.pending')
            try:
                with pending.open('x', encoding='utf-8') as target:
                    json.dump(value, target, allow_nan=False)
                    target.flush(); os.fsync(target.fileno())
            except (OSError, ValueError, TypeError) as error:
                raise TrialRecordError(error, 'write') from None
            try:
                os.replace(pending, self.path)
            except OSError as error:
                raise TrialRecordError(error, 'replace') from None
            return result
