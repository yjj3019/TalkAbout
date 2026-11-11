"""POSIX group enumeration fails closed on unreadable /proc entries (platform-independent mocks)."""
import builtins
import errno

import pytest

from talkabout import processes


def fake_proc(monkeypatch, entries):
    """entries: pid -> bytes stat content, or an errno to raise on open."""
    monkeypatch.setattr(processes.os.path, 'isdir', lambda path: path == '/proc')
    monkeypatch.setattr(processes.os, 'listdir', lambda path: [str(pid) for pid in entries])
    real_open = builtins.open

    def fake_open(path, *args, **kwargs):
        pid = int(str(path).split('/')[2]) if str(path).startswith('/proc/') else None
        if pid not in entries:
            return real_open(path, *args, **kwargs)
        value = entries[pid]
        if isinstance(value, int):
            raise OSError(value, 'synthetic')
        import io
        return io.BytesIO(value)
    monkeypatch.setattr(builtins, 'open', fake_open)


def stat_line(pid, pgrp, state='S'):
    tail = [state, '1', str(pgrp)] + ['0'] * 16 + ['4242']
    return ('%d (x) ' % pid + ' '.join(tail)).encode()


def test_readable_members_are_listed_and_vanished_pids_are_skipped(monkeypatch):
    fake_proc(monkeypatch, {7001: stat_line(7001, 7000), 7002: errno.ENOENT, 7003: errno.ESRCH,
                            7004: stat_line(7004, 1), 7005: stat_line(7005, 7000, 'Z')})
    assert processes._proc_group_members(7000) == [7001]


@pytest.mark.parametrize('failure', [errno.EACCES, errno.EIO, b'garbage-without-fields'])
def test_unreadable_or_malformed_entry_fails_closed(monkeypatch, failure):
    fake_proc(monkeypatch, {7001: stat_line(7001, 7000), 7002: failure})
    assert processes._proc_group_members(7000) is None


def test_non_strict_stat_keeps_none_for_unreadable_entries(monkeypatch):
    fake_proc(monkeypatch, {7002: errno.EACCES})
    assert processes._proc_stat(7002) is None
    with pytest.raises(OSError):
        processes._proc_stat(7002, strict=True)
