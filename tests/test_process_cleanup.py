import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest
from talkabout.adapters import AdapterError, CLIRuntime, execute_bounded
from talkabout.processes import ManagedProcess, ProcessSupervisor, ProcessCleanupError
from talkabout.runtime import RuntimeTimeout, bounded_call


def alive(pid):
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle: return False
        try: return kernel.WaitForSingleObject(handle, 0) == 258
        finally: kernel.CloseHandle(handle)
    try: os.kill(pid, 0); return True
    except ProcessLookupError: return False


@pytest.mark.skipif(os.name != 'nt', reason='External CLI containment is supported only on Windows; POSIX fails closed')
@pytest.mark.parametrize('mode', ['success', 'failure', 'timeout', 'stop', 'heartbeat'])
def test_no_descendants_survive_cli_parent_exit(mode, tmp_path):
    pidfile = tmp_path/'owned-pids.json'
    # The test parent also has containment, so a failed assertion cannot leak fixtures.
    parent = ManagedProcess([sys.executable, '-m', 'tests.runtime_process_fixture', mode, str(pidfile)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = parent.process.communicate(timeout=15)
        assert parent.process.returncode == 0, stderr.decode(errors='replace')
        assert pidfile.exists(), 'synthetic runtime never launched'
        assert all(not alive(pid) for pid in json.loads(pidfile.read_text()))
    finally: parent.stop()


def test_shutdown_before_launch_is_fail_closed():
    supervisor = ProcessSupervisor(); supervisor.stop()
    with pytest.raises(ProcessCleanupError):
        supervisor.start([sys.executable, '-c', 'raise AssertionError()'], threading.Event())
    runtime = CLIRuntime('claude', executable=sys.executable)
    runtime.shutdown()
    with pytest.raises(AdapterError, match='runtime_cancelled'): runtime.run({}, threading.Event())
    assert runtime.calls == 0


@pytest.mark.skipif(os.name != 'nt', reason='POSIX CLI execution fails closed')
def test_fast_stderr_and_combined_output_limit():
    for program in ["import sys; sys.stderr.write('x'*2200000)",
                    "import sys; sys.stdout.write('x'*1100000); sys.stderr.write('x'*1100000)"]:
        with pytest.raises(AdapterError, match='runtime_output_limit'):
            execute_bounded([sys._base_executable, '-c', program], '', threading.Event(), 3)


def test_shutdown_during_slow_report_prevents_late_runtime_launch():
    source = CLIRuntime('claude', executable=sys.executable)
    release, done = threading.Event(), threading.Event()
    class Reported:
        def run(self, context, cancel):
            release.wait(2)
            try: return source.run(context, cancel)
            finally: done.set()
        def shutdown(self): return source.shutdown()
    try:
        with pytest.raises(RuntimeTimeout) as error:
            bounded_call(Reported(), {}, threading.Event(), .05)
        assert error.value.runtime_stopped
    finally:
        release.set()
        assert done.wait(2)
    assert source.calls == 0


def test_containment_failure_prevents_execution(monkeypatch, tmp_path):
    import talkabout.processes as processes
    marker = tmp_path/'must-not-exist'
    if os.name == 'nt':
        monkeypatch.setattr(processes.kernel, 'AssignProcessToJobObject', lambda *args: False)
    else:
        assert os.name != 'nt'  # Unsupported platform must fail before Popen.
    with pytest.raises(ProcessCleanupError):
        ManagedProcess([sys._base_executable, '-c', 'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text("bad")', str(marker)])
    assert not marker.exists()


def test_posix_detached_descendant_program_cannot_launch(monkeypatch, tmp_path):
    import talkabout.processes as processes
    calls = []
    # This program would escape a plain process group. It must never reach Popen.
    program = "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)'],start_new_session=True)"
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError('unsupported platform launched a process')
    with monkeypatch.context() as patch:
        patch.setattr(processes.os, 'name', 'posix')
        patch.setattr(processes.subprocess, 'Popen', forbidden)
        with pytest.raises(ProcessCleanupError, match='runtime_containment_unsupported'):
            ManagedProcess([sys.executable, '-c', program])
        with pytest.raises(AdapterError, match='runtime_containment_unsupported'):
            execute_bounded([sys.executable, '-c', program], '', threading.Event(), 1)
    assert calls == []
