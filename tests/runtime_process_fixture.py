"""Synthetic CLI parent used to verify cleanup after the parent itself exits."""
import json
from pathlib import Path
import sqlite3
import sys
import threading
import time

from talkabout.adapters import CLIRuntime
from talkabout.runtime import RuntimeTimeout, bounded_call


def main():
    mode, pidfile = sys.argv[1:]
    child = ("import subprocess,sys,time,json,os; "
             "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)']); "
             "open(sys.argv[1],'w').write(json.dumps([os.getpid(),p.pid])); ")
    if mode == 'success': child += "print(json.dumps({'type':'result','result':'synthetic','is_error':False}))"
    elif mode == 'failure': child += 'sys.exit(1)'
    else: child += 'time.sleep(20)'
    class SyntheticCLI(CLIRuntime):
        def command(self): return [sys._base_executable, '-c', child, pidfile]
    adapter = SyntheticCLI('claude', executable=sys._base_executable)
    if mode in {'stop', 'heartbeat'}:
        # Exercise the real Worker cancellation paths, with an isolated MemoryStore.
        from tests.conftest import build_fixture, LocalClient
        from talkabout.store import MemoryStore
        from talkabout.worker import Worker, Journal
        store, _, _ = build_fixture(MemoryStore())
        journal = Journal(Path(pidfile).with_suffix('.sqlite'))
        client = LocalClient(store)
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
            bounded_call(adapter, {}, threading.Event(), .7 if mode == 'timeout' else 5)
            assert mode == 'success'
        except RuntimeTimeout as error:
            assert mode == 'timeout' and error.runtime_stopped
        except Exception:
            if mode != 'failure': raise
        finally: adapter.shutdown()


if __name__ == '__main__': main()
