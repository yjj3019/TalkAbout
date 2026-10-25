"""The stop flag may only report a stop it actually read.

A transient lock means "ask again", damage means "unknown", and neither is a human
asking to stop. These tests are the contract for that reversal.
"""
import sqlite3
import time

import pytest

import talkabout.worker as worker_module
from talkabout.worker import Journal, transient_sqlite_error


@pytest.fixture
def journal(tmp_path):
    value = Journal(tmp_path / 'stop-flag.sqlite')
    try:
        yield value
    finally:
        value.close()


class Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class ScriptedConnection:
    """Stands in for a connection whose reads are contended, damaged or healthy.

    `remaining` is shared across connections: the point of the retry is that a second
    connection can observe different state than a locked one did.
    """

    def __init__(self, remaining, row):
        self.remaining = remaining
        self.row = row
        self.closed = False

    def execute(self, *args, **kwargs):
        if self.remaining[0] > 0:
            self.remaining[0] -= 1
            raise sqlite3.OperationalError('database is locked')
        return Cursor(self.row)

    def close(self):
        self.closed = True


def install(monkeypatch, failures, row=None):
    created = []
    remaining = [failures]

    def connect(*args, **kwargs):
        connection = ScriptedConnection(remaining, row)
        created.append(connection)
        return connection

    monkeypatch.setattr('talkabout.worker.sqlite3.connect', connect)
    return created


def test_genuine_stop_is_reported(journal):
    journal.db.execute("INSERT OR REPLACE INTO settings VALUES('stop','true')")
    journal.db.commit()
    assert journal.stop_requested() is True


def test_absent_flag_is_not_a_stop(journal):
    assert journal.stop_requested() is False


def test_corrupt_flag_value_is_unknown_not_a_stop(journal):
    journal.db.execute("INSERT OR REPLACE INTO settings VALUES('stop','{not json')")
    journal.db.commit()
    # Unknown must not become a stop: the lease fence, not this guess, reclaims work.
    assert journal.stop_requested() is False


def test_transient_lock_is_retried_then_answered(journal, monkeypatch):
    created = install(monkeypatch, failures=2, row=('true',))
    assert journal.stop_requested() is True
    assert len(created) == 3
    assert all(connection.closed for connection in created)


def test_transient_lock_gives_up_within_the_patience_bound(journal, monkeypatch):
    monkeypatch.setattr(worker_module, 'STOP_READ_PATIENCE_SECONDS', 0.05)
    created = install(monkeypatch, failures=10_000)
    started = time.monotonic()
    assert journal.stop_requested() is False
    assert time.monotonic() - started < 2.0
    assert len(created) > 1
    assert all(connection.closed for connection in created)


def test_non_transient_error_is_not_retried(journal, monkeypatch):
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('database disk image is malformed')

    monkeypatch.setattr('talkabout.worker.sqlite3.connect', fail)
    assert journal.stop_requested() is False


def test_result_code_decides_before_text():
    busy = sqlite3.OperationalError('anything at all')
    busy.sqlite_errorcode = sqlite3.SQLITE_BUSY
    assert transient_sqlite_error(busy) is True
    other = sqlite3.OperationalError('anything at all')
    other.sqlite_errorcode = sqlite3.SQLITE_CORRUPT
    assert transient_sqlite_error(other) is False
    assert transient_sqlite_error(sqlite3.OperationalError('database is locked')) is True
    assert transient_sqlite_error(sqlite3.OperationalError('no such table')) is False


def test_step_reports_unconfirmed_when_the_queue_is_unreadable(journal, monkeypatch):
    """A readable stop flag plus an unreadable queue must not claim a clean stop."""
    journal.db.execute("INSERT OR REPLACE INTO settings VALUES('stop','true')")
    journal.db.commit()
    worker = worker_module.Worker.__new__(worker_module.Worker)
    worker.journal = journal
    worker.run_deadline = None

    def unreadable():
        raise sqlite3.OperationalError('database is locked')

    monkeypatch.setattr(journal, 'status', unreadable)
    assert worker.step() == 'stop_unconfirmed'
