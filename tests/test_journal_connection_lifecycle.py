import sqlite3

import pytest

from talkabout.worker import Journal


@pytest.fixture
def journal(tmp_path):
    value = Journal(tmp_path / 'owned.sqlite')
    try:
        yield value
    finally:
        value.close()


@pytest.fixture
def tracked(journal, monkeypatch):
    original = sqlite3.connect
    retained = []  # Strong references prevent GC from masking missing closes.
    counts = {'created': 0, 'live': 0, 'peak': 0}

    class Connection(sqlite3.Connection):
        closed = False

        def close(self):
            try:
                return super().close()
            finally:
                if not self.closed:
                    self.closed = True
                    counts['live'] -= 1

    def connect(*args, **kwargs):
        kwargs['factory'] = Connection
        value = original(*args, **kwargs)
        retained.append(value)
        counts['created'] += 1
        counts['live'] += 1
        counts['peak'] = max(counts['peak'], counts['live'])
        return value

    monkeypatch.setattr('talkabout.worker.sqlite3.connect', connect)
    try:
        yield retained, counts, Connection
    finally:
        for value in retained:
            value.close()


def assert_closed(connection):
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        sqlite3.Connection.execute(connection, 'SELECT 1')


@pytest.mark.parametrize('operation', ['read', 'write'])
def test_stop_helper_closes_after_return(journal, tracked, operation):
    retained, counts, _ = tracked
    if operation == 'read':
        assert journal.stop_requested() is False
    else:
        journal.stop_state('stopped')
        assert journal.setting('stop_state') == 'stopped'  # Commit is retained.
    assert counts == {'created': 1, 'live': 0, 'peak': 1}
    assert_closed(retained[0])


@pytest.mark.parametrize('operation', ['read', 'write'])
def test_repeated_stop_helper_has_no_connection_accumulation(journal, tracked, operation):
    retained, counts, _ = tracked
    for number in range(100):
        if operation == 'read':
            assert journal.stop_requested() is False
        else:
            journal.stop_state(str(number))
        assert counts['live'] == 0
        assert_closed(retained[-1])
    assert counts == {'created': 100, 'live': 0, 'peak': 1}
    if operation == 'write':
        assert journal.setting('stop_state') == '99'


def test_stop_read_closes_after_query_error_without_claiming_a_stop(journal, tracked, monkeypatch):
    retained, counts, connection_type = tracked

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('synthetic query failure')

    monkeypatch.setattr(connection_type, 'execute', fail)
    # A failing read is not a stop request. It used to raise, and the recovery path
    # treated that as "stop" and committed a live turn as cancelled.
    assert journal.stop_requested() is False
    assert counts == {'created': 1, 'live': 0, 'peak': 1}
    assert_closed(retained[0])


def test_stop_write_keeps_propagating_query_errors(journal, tracked, monkeypatch):
    retained, counts, connection_type = tracked

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('synthetic query failure')

    monkeypatch.setattr(connection_type, 'execute', fail)
    with pytest.raises(sqlite3.OperationalError, match='synthetic query failure'):
        journal.stop_state('must-not-persist')
    assert counts == {'created': 1, 'live': 0, 'peak': 1}
    assert_closed(retained[0])
    assert journal.setting('stop_state') is None
