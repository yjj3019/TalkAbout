"""Process containment: a Windows kill-on-close job, and a POSIX lifetime ladder.

Neither path is a sandbox. Windows confines a child to a job object that dies with the
supervisor; POSIX proves the child's process group is gone before reporting success, and
reports an unconfirmed stop instead of assuming one. POSIX containment currently covers
lifetime only: killing the supervisor itself is not handled (see docs/POSIX_CONTAINMENT_PLAN.md).
"""
import os
import signal
import subprocess
import threading
import time


class ProcessCleanupError(Exception):
    pass


# The POSIX ladder is bounded end to end: parent-first SIGTERM, then the group, then
# SIGKILL, then a survivor re-check that must come back empty before "stopped" is true.
POSIX_TERM_GRACE_SECONDS = 2.0
POSIX_KILL_GRACE_SECONDS = 2.0
POSIX_VERIFY_SECONDS = 1.0


if os.name == 'nt':
    import ctypes
    from ctypes import wintypes as w

    class BasicLimits(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64), ('flags', w.DWORD),
                    ('min_working_set', ctypes.c_size_t), ('max_working_set', ctypes.c_size_t),
                    ('active_limit', w.DWORD), ('affinity', ctypes.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]

    class IOCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ('reads', 'writes', 'other', 'read_bytes', 'write_bytes', 'other_bytes')]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [('basic', BasicLimits), ('io', IOCounters), ('process_memory', ctypes.c_size_t),
                    ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]

    class Accounting(ctypes.Structure):
        _fields_ = [(name, ctypes.c_int64) for name in ('user', 'kernel', 'period_user', 'period_kernel')] + [
            (name, w.DWORD) for name in ('page_faults', 'total', 'active', 'terminated')]

    class ThreadEntry(ctypes.Structure):
        _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('thread', w.DWORD), ('owner', w.DWORD),
                    ('base_priority', w.LONG), ('delta_priority', w.LONG), ('flags', w.DWORD)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    for name, arguments, result in [
        ('CreateJobObjectW', [ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
        ('SetInformationJobObject', [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
        ('AssignProcessToJobObject', [w.HANDLE, w.HANDLE], w.BOOL),
        ('TerminateJobObject', [w.HANDLE, w.UINT], w.BOOL),
        ('QueryInformationJobObject', [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p], w.BOOL),
        ('CreateToolhelp32Snapshot', [w.DWORD, w.DWORD], w.HANDLE),
        ('Thread32First', [w.HANDLE, ctypes.POINTER(ThreadEntry)], w.BOOL),
        ('Thread32Next', [w.HANDLE, ctypes.POINTER(ThreadEntry)], w.BOOL),
        ('OpenThread', [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        ('ResumeThread', [w.HANDLE], w.DWORD), ('CloseHandle', [w.HANDLE], w.BOOL),
        ('OpenProcess', [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        ('WaitForSingleObject', [w.HANDLE, w.DWORD], w.DWORD),
    ]:
        function = getattr(kernel, name); function.argtypes = arguments; function.restype = result

    def resume_suspended(process):
        snapshot = kernel.CreateToolhelp32Snapshot(4, 0)
        if snapshot == ctypes.c_void_p(-1).value: raise ProcessCleanupError('thread_snapshot_failed')
        try:
            entry = ThreadEntry(); entry.size = ctypes.sizeof(entry)
            available = kernel.Thread32First(snapshot, ctypes.byref(entry))
            while available:
                if entry.owner == process.pid:
                    thread = kernel.OpenThread(2, False, entry.thread)
                    if not thread: raise ProcessCleanupError('thread_open_failed')
                    try:
                        if kernel.ResumeThread(thread) == 0xffffffff: raise ProcessCleanupError('thread_resume_failed')
                    finally: kernel.CloseHandle(thread)
                    return
                available = kernel.Thread32Next(snapshot, ctypes.byref(entry))
            raise ProcessCleanupError('primary_thread_missing')
        finally: kernel.CloseHandle(snapshot)


def stop_windows_job(job, process):
    """Synchronize descendant exit, including after the group leader has exited."""
    handles = []
    try:
        size = 8 + ctypes.sizeof(ctypes.c_size_t) * 4096
        members = ctypes.create_string_buffer(size)
        if not kernel.QueryInformationJobObject(job, 3, members, size, None):
            raise ProcessCleanupError('job_members_query_failed')
        count = ctypes.c_uint32.from_buffer(members, 4).value
        for index in range(count):
            pid = ctypes.c_size_t.from_buffer(members, 8 + index * ctypes.sizeof(ctypes.c_size_t)).value
            handle = kernel.OpenProcess(0x100000, False, pid)
            if handle: handles.append(handle)
        if not kernel.TerminateJobObject(job, 1): raise ProcessCleanupError('job_termination_failed')
        deadline = time.monotonic() + 5
        while True:
            accounting = Accounting()
            if not kernel.QueryInformationJobObject(job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                raise ProcessCleanupError('job_query_failed')
            if accounting.active == 0: break
            if time.monotonic() >= deadline: raise ProcessCleanupError('job_cleanup_timeout')
            time.sleep(.01)
        for handle in handles:
            remaining = max(0, int((deadline - time.monotonic()) * 1000))
            if kernel.WaitForSingleObject(handle, remaining) != 0:
                raise ProcessCleanupError('process_exit_unconfirmed')
        process.wait(timeout=1)
    finally:
        for handle in handles: kernel.CloseHandle(handle)
        # Even an unconfirmed cleanup must close the kill-on-close job, never abandon it.
        kernel.CloseHandle(job)


def _proc_stat(pid):
    """Return state/ppid/pgrp/start-time for a pid from Linux /proc, or None.

    The start time is what makes a PID comparison meaningful: PID reuse between the
    launch and the stop would otherwise let the ladder signal an unrelated process.
    """
    if not os.path.isdir('/proc'):
        return None
    try:
        with open('/proc/%d/stat' % pid, 'rb') as handle:
            raw = handle.read(8192)
    except OSError:
        return None
    try:
        text = raw.decode('utf-8', 'replace')
        fields = text[text.rindex(')') + 2:].split()
        return {'state': fields[0], 'ppid': int(fields[1]), 'pgrp': int(fields[2]), 'start': int(fields[19])}
    except (ValueError, IndexError):
        return None


def _proc_group_members(pgid):
    """Live (non-zombie) pids in *pgid*, excluding this supervisor.

    Returns None when the process table cannot be enumerated. That difference matters:
    an unreadable table cannot prove the group is gone, so the caller must fail closed
    rather than report a clean stop.
    """
    if not os.path.isdir('/proc'):
        return None
    members = []
    try:
        names = os.listdir('/proc')
    except OSError:
        return None
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        if pid in {os.getpid(), pgid}:
            continue
        stat = _proc_stat(pid)
        if stat is None or stat['pgrp'] != pgid or stat['state'] == 'Z':
            continue
        members.append(pid)
    return members


def stop_posix_group(process, pgid, started):
    """Terminate *pgid* and prove it empty. Raises when the proof fails.

    The leader is signalled first so a cooperative process can shut down in order;
    descendants that inherited the group are then escalated to SIGKILL. Every signal
    target is re-read from the process table immediately before use, and nothing is
    signalled for a group this process belongs to.
    """
    if not isinstance(pgid, int) or pgid <= 1:
        raise ProcessCleanupError('posix_group_unverified')
    if pgid == os.getpgrp() or pgid == os.getpid():
        # A failed start_new_session must never turn the stop into a self-kill.
        raise ProcessCleanupError('posix_group_unverified')
    live = _proc_stat(process.pid)
    if live is not None and started is not None and live['start'] != started:
        raise ProcessCleanupError('process_stop_refused_pid_reuse')
    if live is None and process.poll() is None:
        raise ProcessCleanupError('process_stop_refused_unidentified')

    def signal_one(pid, number):
        stat = _proc_stat(pid)
        if stat is None or stat['state'] == 'Z':
            return
        try:
            os.kill(pid, number)
        except ProcessLookupError:
            return
        except PermissionError:
            raise ProcessCleanupError('process_stop_refused_permission') from None

    signal_one(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + POSIX_TERM_GRACE_SECONDS
    while time.monotonic() < deadline and process.poll() is None:
        time.sleep(.02)

    for number, grace in ((signal.SIGTERM, POSIX_TERM_GRACE_SECONDS),
                          (signal.SIGKILL, POSIX_KILL_GRACE_SECONDS)):
        members = _proc_group_members(pgid)
        if members is None:
            raise ProcessCleanupError('process_exit_unconfirmed')
        if not members:
            break
        for pid in members:
            signal_one(pid, number)
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            remaining = _proc_group_members(pgid)
            if remaining is None:
                raise ProcessCleanupError('process_exit_unconfirmed')
            if not remaining:
                break
            time.sleep(.02)

    try:
        process.wait(timeout=POSIX_VERIFY_SECONDS)
    except subprocess.TimeoutExpired:
        raise ProcessCleanupError('process_exit_unconfirmed') from None
    deadline = time.monotonic() + POSIX_VERIFY_SECONDS
    while True:
        survivors = _proc_group_members(pgid)
        if survivors is None:
            raise ProcessCleanupError('process_exit_unconfirmed')
        if not survivors:
            return True
        if time.monotonic() >= deadline:
            raise ProcessCleanupError('process_exit_unconfirmed')
        time.sleep(.02)


class ManagedProcess:
    """A process must enter its containment before it can execute user/runtime code."""
    def __init__(self, command, cancel=None, deadline=None, **options):
        self.lock = threading.RLock(); self.job = None; self.closed = False
        self.pgid = None; self.started = None
        if (cancel and cancel.is_set()) or (deadline is not None and time.monotonic() >= deadline):
            raise ProcessCleanupError('process_cancelled_before_launch')
        if os.name == 'nt':
            self.job = kernel.CreateJobObjectW(None, None)
            if not self.job: raise ProcessCleanupError('job_creation_failed')
            limits = ExtendedLimits(); limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE, no breakaway.
            if not kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                kernel.CloseHandle(self.job); self.job = None
                raise ProcessCleanupError('job_limits_failed')
            options['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | 4  # CREATE_SUSPENDED
        else:
            if options.get('shell') or 'creationflags' in options:
                # A shell would spawn outside the group we just created and own.
                raise ProcessCleanupError('posix_containment_options_unsupported')
            options['start_new_session'] = True
        try:
            self.process = subprocess.Popen(command, **options)
            if os.name == 'nt':
                if not kernel.AssignProcessToJobObject(self.job, int(self.process._handle)):
                    raise ProcessCleanupError('job_assignment_failed')
                if (cancel and cancel.is_set()) or (deadline is not None and time.monotonic() >= deadline):
                    raise ProcessCleanupError('process_cancelled_before_resume')
                resume_suspended(self.process)
            else:
                # start_new_session makes the child the leader of a new group and session.
                self.pgid = self.process.pid
                self.started = (_proc_stat(self.process.pid) or {}).get('start')
                try:
                    actual_group = os.getpgid(self.process.pid)
                except ProcessLookupError:
                    # Already exited between spawn and verification; the session guarantee
                    # still holds for the group we recorded at launch.
                    actual_group = self.pgid
                if self.pgid == os.getpgrp() or actual_group != self.pgid:
                    raise ProcessCleanupError('posix_group_unverified')
        except Exception:
            process = getattr(self, 'process', None)
            if process and process.poll() is None:
                process.kill(); process.wait(timeout=3)
            if self.job: kernel.CloseHandle(self.job); self.job = None
            raise

    def stop(self):
        with self.lock:
            if self.closed: return True
            process = self.process
            if os.name == 'nt':
                job, self.job = self.job, None
                if job is None: raise ProcessCleanupError('previous_cleanup_unconfirmed')
                stop_windows_job(job, process)
            else:
                stop_posix_group(process, self.pgid, self.started)
            self.closed = True
            return True


class ProcessSupervisor:
    """Serialize launch against shutdown; a cancelled call can never launch late."""
    def __init__(self):
        self.lock = threading.RLock(); self.closed = False; self.child = None

    def start(self, command, cancel, deadline=None, **options):
        with self.lock:
            if self.closed or cancel.is_set(): raise ProcessCleanupError('process_cancelled')
            self.child = ManagedProcess(command, cancel=cancel, deadline=deadline, **options)
            return self.child.process

    def stop(self):
        with self.lock:
            self.closed = True
            return self.child.stop() if self.child else True
