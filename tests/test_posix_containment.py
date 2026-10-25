"""POSIX containment: the group must be gone before "stopped" is reported.

POSIX-only. The project's CI runs on Windows, so these tests are exercised by the
dedicated Linux job in .github/workflows/safe-regression.yml; on Windows they skip.
"""
import os
import subprocess
import sys
import time

import pytest

from talkabout.processes import (ManagedProcess, ProcessCleanupError, _proc_stat,
                                 stop_posix_group)

pytestmark = pytest.mark.skipif(os.name == 'nt', reason='POSIX containment is Linux/macOS only')

GRANDCHILD = '''
import subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'])
open(sys.argv[1], 'w').write(str(child.pid))
time.sleep(60)
'''

LEADER_EXITS = '''
import subprocess, sys
child = subprocess.Popen([sys.executable, '-c', 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'])
open(sys.argv[1], 'w').write(str(child.pid))
'''


def alive(pid):
    stat = _proc_stat(pid)
    return stat is not None and stat['state'] != 'Z'


def wait_for(path, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return int(path.read_text().strip())
        time.sleep(.02)
    raise AssertionError('grandchild pid was never written')


def test_new_session_makes_the_child_its_own_group_leader(tmp_path):
    process = ManagedProcess([sys.executable, '-c', 'import time; time.sleep(5)'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert process.pgid == process.process.pid
        assert os.getpgid(process.process.pid) == process.pgid
        assert process.pgid != os.getpgrp()
    finally:
        process.stop()


def test_stop_removes_a_sigterm_ignoring_descendant(tmp_path):
    pidfile = tmp_path / 'grandchild.pid'
    process = ManagedProcess([sys.executable, '-c', GRANDCHILD, str(pidfile)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    grandchild = None
    try:
        grandchild = wait_for(pidfile)
        assert alive(grandchild)
    finally:
        process.stop()
    assert not alive(grandchild)


def test_group_is_swept_after_the_leader_exits(tmp_path):
    """An exited leader does not end the group: the descendant still has to go."""
    pidfile = tmp_path / 'grandchild.pid'
    process = ManagedProcess([sys.executable, '-c', LEADER_EXITS, str(pidfile)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    grandchild = wait_for(pidfile)
    deadline = time.monotonic() + 5
    while process.process.poll() is None and time.monotonic() < deadline:
        time.sleep(.02)
    assert process.process.poll() is not None, 'leader should have exited'
    try:
        assert alive(grandchild)
    finally:
        process.stop()
    assert not alive(grandchild)


def test_stop_is_idempotent_and_bounded(tmp_path):
    pidfile = tmp_path / 'grandchild.pid'
    process = ManagedProcess([sys.executable, '-c', GRANDCHILD, str(pidfile)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for(pidfile)
        started = time.monotonic()
        assert process.stop() is True
        elapsed = time.monotonic() - started
        # Parent TERM, group TERM, group KILL, survivor check: bounded end to end.
        assert elapsed < 9.0
        assert process.stop() is True
    finally:
        try:
            process.stop()
        except ProcessCleanupError:
            pass


def test_unreadable_process_table_fails_closed(monkeypatch):
    process = ManagedProcess([sys.executable, '-c', 'import time; time.sleep(30)'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    monkeypatch.setattr('talkabout.processes._proc_group_members', lambda pgid: None)
    try:
        with pytest.raises(ProcessCleanupError, match='^process_exit_unconfirmed$'):
            process.stop()
    finally:
        monkeypatch.undo()
        try:
            process.stop()
        except ProcessCleanupError:
            pass


def test_reused_pid_is_refused_before_any_signal():
    process = ManagedProcess([sys.executable, '-c', 'import time; time.sleep(30)'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # A different start time is exactly what PID reuse looks like.
        process.started = (process.started or 0) + 12345
        with pytest.raises(ProcessCleanupError, match='^process_stop_refused_pid_reuse$'):
            process.stop()
        assert process.process.poll() is None  # nothing was signalled
    finally:
        process.started = None if process.started is None else process.started - 12345
        try:
            process.stop()
        except ProcessCleanupError:
            pass


def test_own_group_is_never_signalled():
    class Stub:
        pid = os.getpid()
        def poll(self):
            return None
    with pytest.raises(ProcessCleanupError, match='^posix_group_unverified$'):
        stop_posix_group(Stub(), os.getpgrp(), None)


def test_invalid_group_is_refused():
    class Stub:
        pid = 1
        def poll(self):
            return None
    for pgid in (0, 1, -5, None, 'x'):
        with pytest.raises(ProcessCleanupError, match='^posix_group_unverified$'):
            stop_posix_group(Stub(), pgid, None)


def test_shell_and_creationflags_are_refused():
    for options in ({'shell': True}, {'creationflags': 0}):
        with pytest.raises(ProcessCleanupError, match='^posix_containment_options_unsupported$'):
            ManagedProcess([sys.executable, '-c', 'pass'], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, **options)
