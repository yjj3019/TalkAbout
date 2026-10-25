"""Boundary and vocabulary contracts for the journal SQLite storage self-check.

The judgement itself is a version comparison, so every edge of the affected window, the
two backported minor lines and the fix are pinned here. Warnings are asserted as exact
strings: that is how "no path, no environment value, no credential" is proven.
"""
import shutil
import uuid
from pathlib import Path

import pytest

from talkabout import storage_health
from talkabout.worker import Journal

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def scratch_dir():
    """Scratch directory under the workspace, removed afterwards.

    Not ``tmp_path``: a Windows conformance environment can deny file creation under the
    system temp directory, while the workspace is writable. Not ``mkdtemp`` either: its
    private DACL drops the inherited write permission, so ``mkdir`` here lets the directory
    inherit the workspace ACL. The ``/.talkabout-fixture-*/`` entry in .gitignore covers
    this prefix.
    """
    root = REPO_ROOT / ('.talkabout-fixture-storage-health-' + uuid.uuid4().hex)
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)

# (version, vulnerable?) -- the affected window is 3.7.0..3.51.2, fixed in 3.51.3, with
# backports in 3.50.7 and 3.44.6.
BOUNDARIES = [
    ((3, 6, 9), False),   # last release before the affected window
    ((3, 7, 0), True),    # first affected
    ((3, 44, 5), True),   # one patch before the 3.44 backport
    ((3, 44, 6), False),  # 3.44 backport
    ((3, 44, 9), False),  # inside the 3.44 line, after the backport
    ((3, 45, 0), True),   # 3.44 backport window ends
    ((3, 50, 6), True),   # one patch before the 3.50 backport
    ((3, 50, 7), False),  # 3.50 backport
    ((3, 50, 9), False),  # inside the 3.50 line, after the backport
    ((3, 51, 0), True),   # 3.50 backport window ends
    ((3, 51, 2), True),   # last affected
    ((3, 51, 3), False),  # fixed
]


@pytest.mark.parametrize('version,expected', BOUNDARIES,
                         ids=['.'.join(str(part) for part in version) for version, _ in BOUNDARIES])
def test_wal_reset_boundaries(version, expected):
    assert storage_health.wal_reset_vulnerable(version) is expected


def test_sqlite_version_matches_the_interpreter():
    import sqlite3

    assert storage_health.sqlite_version() == tuple(sqlite3.sqlite_version_info[:3])


@pytest.mark.parametrize('version', [(3, 50), [3, 50, 4], (3, 50, 4, 0)])
def test_short_and_extra_part_versions_are_normalised(version):
    assert storage_health.wal_reset_vulnerable(version) is True


@pytest.mark.parametrize('bad', [(), '3.50.4', (3, True), (3, -1, 0), 3.50, (3, 50, 'x')])
def test_unreadable_version_is_fail_closed(bad):
    # "not proven safe" is not "safe": an unreadable version must not claim to be fixed.
    assert storage_health.wal_reset_vulnerable(bad) is True


def test_missing_version_uses_the_linked_library(monkeypatch):
    # No argument is the documented sentinel for "ask the interpreter", never "unreadable".
    monkeypatch.setattr(storage_health, 'sqlite_version', lambda: None)
    assert storage_health.wal_reset_vulnerable() is True
    monkeypatch.setattr(storage_health, 'sqlite_version', lambda: (3, 51, 3))
    assert storage_health.wal_reset_vulnerable() is False


def test_describe_shape_and_vocabulary(monkeypatch):
    monkeypatch.setattr(storage_health, 'sqlite_version', lambda: (3, 50, 4))
    report = storage_health.describe()
    assert set(report) == {'sqlite_version', 'wal_reset_vulnerable', 'journal_policy', 'reason'}
    assert report == {
        'sqlite_version': '3.50.4',
        'wal_reset_vulnerable': True,
        'journal_policy': 'avoid_wal',
        'reason': 'wal_reset_bug_affected_range',
    }
    assert report['journal_policy'] in {'wal', 'wal_unverified', 'avoid_wal'}
    assert report['reason'] in {
        'wal_reset_bug_affected_range', 'wal_reset_bug_fixed_or_backported', 'sqlite_version_unavailable'}


def test_describe_reports_safe_and_unverified(monkeypatch):
    monkeypatch.setattr(storage_health, 'sqlite_version', lambda: (3, 51, 3))
    assert storage_health.describe() == {
        'sqlite_version': '3.51.3',
        'wal_reset_vulnerable': False,
        'journal_policy': 'wal',
        'reason': 'wal_reset_bug_fixed_or_backported',
    }
    monkeypatch.setattr(storage_health, 'sqlite_version', lambda: None)
    assert storage_health.describe() == {
        'sqlite_version': 'unknown',
        'wal_reset_vulnerable': True,
        'journal_policy': 'wal_unverified',
        'reason': 'sqlite_version_unavailable',
    }


def test_warning_is_exact_and_single(capsys, monkeypatch):
    monkeypatch.setattr(storage_health, 'sqlite_version', lambda: (3, 50, 4))
    storage_health._reset_warning_state_for_tests()
    storage_health.warn_if_journal_is_exposed()
    storage_health.warn_if_journal_is_exposed()
    storage_health.warn_if_journal_is_exposed()
    captured = capsys.readouterr()
    assert captured.err == 'journal_sqlite_wal_reset_exposed 3.50.4\n'
    # No path, no environment value, no credential: only the token and a numeric version.
    assert '/' not in captured.err and '\\' not in captured.err
    assert captured.err.split() == ['journal_sqlite_wal_reset_exposed', '3.50.4']


@pytest.mark.parametrize('version', [(3, 51, 3), (3, 50, 7), (3, 44, 6), (3, 6, 9)])
def test_no_warning_when_the_version_is_not_exposed(capsys, monkeypatch, version):
    monkeypatch.setattr(storage_health, 'sqlite_version', lambda: version)
    storage_health._reset_warning_state_for_tests()
    storage_health.warn_if_journal_is_exposed()
    assert capsys.readouterr().err == ''


def test_unverified_version_stays_silent(capsys, monkeypatch):
    monkeypatch.setattr(storage_health, 'sqlite_version', lambda: None)
    storage_health._reset_warning_state_for_tests()
    storage_health.warn_if_journal_is_exposed()
    assert capsys.readouterr().err == ''


def test_reporting_failure_never_raises(capsys, monkeypatch):
    def boom():
        raise RuntimeError('synthetic failure')

    monkeypatch.setattr(storage_health, 'describe', boom)
    storage_health._reset_warning_state_for_tests()
    storage_health.warn_if_journal_is_exposed()  # must not raise
    assert capsys.readouterr().err == ''


def test_journal_startup_warns_once_without_changing_behaviour(capsys, scratch_dir, monkeypatch):
    monkeypatch.setattr(storage_health, 'sqlite_version', lambda: (3, 50, 4))
    storage_health._reset_warning_state_for_tests()
    journal = Journal(scratch_dir / 'exposed.sqlite')
    try:
        journal.set_setting('probe', {'ok': True})
        assert journal.setting('probe') == {'ok': True}
        Journal(scratch_dir / 'exposed-two.sqlite').close()
    finally:
        journal.close()
    assert capsys.readouterr().err == 'journal_sqlite_wal_reset_exposed 3.50.4\n'


def test_journal_startup_is_silent_when_the_version_is_fixed(capsys, scratch_dir, monkeypatch):
    monkeypatch.setattr(storage_health, 'sqlite_version', lambda: (3, 51, 3))
    storage_health._reset_warning_state_for_tests()
    journal = Journal(scratch_dir / 'fixed.sqlite')
    try:
        journal.set_setting('probe', 1)
        assert journal.setting('probe') == 1
    finally:
        journal.close()
    assert capsys.readouterr().err == ''
