"""Synthetic CLI parent used to verify cleanup after the parent itself exits."""
import json
from pathlib import Path
import sqlite3
import sys
import threading
import time
from types import SimpleNamespace

from talkabout.adapters import AdapterError, CLIRuntime
from talkabout.runtime import RuntimeTimeout, bounded_call


def worker_client():
    """Exercise real Core/Worker cancellation without importing the web test stack.

    HTTP integration is covered by test_worker_api; this subprocess measures
    containment, so FastAPI/OpenTelemetry cold imports must not consume its budget.
    """
    from talkabout.core import Core
    from talkabout.domain import AGENT_PERMISSIONS
    from talkabout.store import MemoryStore
    store = MemoryStore()
    token = 'fixture-process-agent'
    store.provision({'id': 'process-agent', 'owner': 'fixture-owner', 'kind': 'agent',
                     'display_name': 'Process Agent', 'handle': 'process-agent'},
                    token, AGENT_PERMISSIONS | {'rooms:create'})
    core = Core(store)
    core.create(token, {'topic': 'Synthetic process cancellation'}, 'process-room')
    class CoreClient:
        def request(self, method, path, body=None, key=None, params=None):
            if method == 'GET' and path == 'agents/me':
                return store.auth(token)
            if method == 'GET' and path == 'agents/me/events':
                return core.events(token, **(params or {}))
            parts = path.split('/')
            assert parts[0] == 'rooms' and len(parts) >= 2, (method, path)
            if method == 'GET':
                room = core.read(token, parts[1])
                if len(parts) == 2:
                    return room
                assert len(parts) == 4 and parts[2] == 'tasks', path
                return next(task for task in room['tasks'] if task['id'] == parts[3])
            assert method == 'POST', method
            return core.mutation(token, parts[1], '/'.join(parts[2:]), key, body)
    return CoreClient()


def timeout_boundary(mode, adapter, pidfile):
    """Advance the unchanged .7s deadline at a known synthetic launch stage.

    This tests timeout/containment ordering, not Python cold-start performance.
    Real-clock run-budget tests remain separate and unchanged.
    """
    import talkabout.processes as processes
    import talkabout.runtime as runtime
    stages = {'requested': False, 'created': False, 'resumed': False,
              'ready': False, 'stopped': False, 'popen_calls': 0, 'native_pids': []}
    initialized, expired, done = threading.Event(), threading.Event(), threading.Event()
    def monotonic():
        if not initialized.is_set():
            initialized.set()
            return 0.0
        return .7 if expired.is_set() else 0.0
    # Replace only this module's clock reference, never the process/adapter clocks.
    original_time = runtime.time
    if mode != 'timeout_cold_start':
        runtime.time = SimpleNamespace(monotonic=monotonic)
    original_start = processes.ProcessSupervisor.start
    original_popen = processes.subprocess.Popen
    original_resume = processes.resume_suspended
    cancellation = threading.Event()
    def popen(*args, **kwargs):
        stages['popen_calls'] += 1
        if mode == 'launch_failure':
            raise FileNotFoundError('synthetic executable unavailable')
        process = original_popen(*args, **kwargs)
        stages['created'] = True  # Windows process exists, still suspended.
        stages['native_pids'].append(process.pid)
        if mode == 'timeout_before_resume':
            assert initialized.wait(5), 'deadline was not initialized'
            expired.set()
            assert cancellation.wait(5), 'timeout did not cancel suspended launch'
        return process
    def resume(process):
        original_resume(process)
        stages['resumed'] = True
    def start(supervisor, command, cancel, **options):
        stages['requested'] = True
        if mode == 'timeout_before_launch':
            assert initialized.wait(5), 'deadline was not initialized'
            expired.set()
            assert cancel.wait(5), 'timeout did not cancel pending launch'
        return original_start(supervisor, command, cancel, **options)
    def expire_after_ready():
        assert initialized.wait(5), 'deadline was not initialized'
        until = time.monotonic() + 5
        while not Path(pidfile).exists() and time.monotonic() < until:
            time.sleep(.01)
        stages['ready'] = Path(pidfile).exists()
        expired.set()
    class Observed:
        def run(self, context, cancel):
            try: return adapter.run(context, cancel)
            finally: done.set()
        def shutdown(self): return adapter.shutdown()
    watcher = None
    processes.subprocess.Popen, processes.resume_suspended = popen, resume
    processes.ProcessSupervisor.start = start
    try:
        if mode == 'timeout_after_launch':
            watcher = threading.Thread(target=expire_after_ready, daemon=True)
            watcher.start()
        try:
            bounded_call(Observed(), {}, cancellation, .7)
            raise AssertionError('boundary did not fail closed')
        except RuntimeTimeout as error:
            assert mode != 'launch_failure' and error.runtime_stopped
            stages['stopped'] = True
        except AdapterError as error:
            assert mode == 'launch_failure' and error.code == 'runtime_unavailable'
        assert done.wait(5), 'runtime thread did not finish after cancellation'
        if watcher:
            watcher.join(5)
            assert not watcher.is_alive() and stages['ready'], 'synthetic readiness not observed'
        assert adapter.shutdown()
        stages['ready'] = Path(pidfile).exists()
        if mode == 'timeout_after_launch':
            assert stages['created'] and stages['resumed'] and stages['popen_calls'] == 1
        elif mode == 'timeout_before_resume':
            assert stages['created'] and not stages['resumed'] and not stages['ready']
        elif mode == 'timeout_cold_start':
            assert stages['stopped']
        else:
            assert not stages['created'] and not stages['resumed'] and not Path(pidfile).exists()
            assert stages['popen_calls'] == (1 if mode == 'launch_failure' else 0)
        print(json.dumps({'boundary': mode, **stages}), flush=True)
    finally:
        adapter.shutdown()
        runtime.time = original_time
        processes.subprocess.Popen, processes.resume_suspended = original_popen, original_resume
        processes.ProcessSupervisor.start = original_start


def main():
    mode, pidfile = sys.argv[1:]
    child = ("import subprocess,sys,time,json,os; "
             "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)']); "
             "f=open(sys.argv[1]+'.pending','w'); json.dump([os.getpid(),p.pid],f); f.close(); "
             "os.replace(sys.argv[1]+'.pending',sys.argv[1]); ")
    if mode == 'success': child += "print(json.dumps({'type':'result','result':'synthetic','is_error':False}))"
    elif mode == 'failure': child += 'sys.exit(1)'
    else: child += 'time.sleep(20)'
    class SyntheticCLI(CLIRuntime):
        def command(self): return [sys._base_executable, '-c', child, pidfile]
    adapter = SyntheticCLI('claude', executable=sys._base_executable)
    if mode in {'launch_failure', 'timeout_before_launch', 'timeout_before_resume',
                'timeout_after_launch', 'timeout_cold_start'}:
        timeout_boundary(mode, adapter, pidfile)
    elif mode in {'stop', 'heartbeat'}:
        # Exercise the real Worker cancellation paths, with an isolated MemoryStore.
        from talkabout.worker import Worker, Journal
        journal = Journal(Path(pidfile).with_suffix('.sqlite'))
        client = worker_client()
        original = client.request
        def request(method, path, *args, **kwargs):
            # Inject failure only after the synthetic descendants are observable.
            # An early heartbeat can legitimately cancel before they are created.
            if mode == 'heartbeat' and path.endswith('/heartbeat') and Path(pidfile).exists():
                raise RuntimeError('synthetic heartbeat failure')
            return original(method, path, *args, **kwargs)
        client.request = request
        def stop():
            until = time.monotonic() + 5
            while not Path(pidfile).exists() and time.monotonic() < until: time.sleep(.01)
            with sqlite3.connect(journal.path) as db:
                db.execute("INSERT OR REPLACE INTO settings VALUES('stop','true')")
        if mode == 'stop': threading.Thread(target=stop, daemon=True).start()
        try:
            outcome = Worker(client, journal, adapter, heartbeat_interval=.4, runtime_timeout=5).step()
            assert outcome == ('stopped' if mode == 'stop' else 'heartbeat_failed'), outcome
        finally: journal.close(); adapter.shutdown()
    else:
        try:
            bounded_call(adapter, {}, threading.Event(), 5)
            assert mode == 'success'
        except AdapterError as error:
            assert mode == 'failure' and error.code == 'runtime_failed'
        finally: adapter.shutdown()


if __name__ == '__main__': main()
