"""Owned child containment: Windows kill-on-close job; POSIX session/process group."""
import os
import signal
import subprocess
import sys
import threading
import time


class ProcessCleanupError(Exception):
    pass


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


def enable_subreaper():
    if sys.platform.startswith('linux'):
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0: raise ProcessCleanupError('subreaper_unavailable')


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


class ManagedProcess:
    """A process must enter its containment before it can execute user/runtime code."""
    def __init__(self, command, cancel=None, deadline=None, **options):
        self.lock = threading.RLock(); self.job = None; self.closed = False
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
            enable_subreaper(); options['start_new_session'] = True
        try:
            self.process = subprocess.Popen(command, **options)
            if os.name == 'nt':
                if not kernel.AssignProcessToJobObject(self.job, int(self.process._handle)):
                    raise ProcessCleanupError('job_assignment_failed')
                if (cancel and cancel.is_set()) or (deadline is not None and time.monotonic() >= deadline):
                    raise ProcessCleanupError('process_cancelled_before_resume')
                resume_suspended(self.process)
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
                # Always target the owned group, even if poll() has reaped its leader already.
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try: os.killpg(process.pid, sig)
                    except ProcessLookupError: pass
                    if sig == signal.SIGTERM: time.sleep(.05)
                process.wait(timeout=3)
                deadline = time.monotonic() + 3
                while True:
                    try:
                        while os.waitpid(-process.pid, os.WNOHANG)[0]: pass
                    except ChildProcessError: pass
                    try: os.killpg(process.pid, 0)
                    except ProcessLookupError: break
                    if time.monotonic() >= deadline: raise ProcessCleanupError('group_cleanup_timeout')
                    time.sleep(.01)
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
