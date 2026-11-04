"""Connection admission checks use fakes; no database connections are made."""
import os
from types import SimpleNamespace

import psycopg
import pytest

from talkabout import provision
from talkabout.store import PostgresStore


@pytest.fixture(autouse=True)
def clean_libpq(monkeypatch):
    for name in list(os.environ):
        if name.upper() in {'PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE', 'PGOPTIONS'}:
            monkeypatch.delenv(name)


class FakeConnection:
    def __init__(self):
        self.autocommit = False
        self.info = SimpleNamespace(transaction_status=0)
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True


@pytest.mark.parametrize('pooled', [False, True])
@pytest.mark.parametrize('variable', ['PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE', 'PGOPTIONS', 'pgservice'])
def test_late_unsafe_environment_rejects_before_connect(monkeypatch, pooled, variable):
    calls = []
    monkeypatch.setattr(psycopg, 'connect', lambda *a, **k: calls.append((a, k)))
    store = PostgresStore('host=talkabout-db user=u dbname=d')
    monkeypatch.setenv(variable, 'secret-should-not-leak')
    with pytest.raises(ValueError, match='^unsafe_libpq_environment$'):
        with store.connect(pooled=pooled):
            pytest.fail('must not connect')
    assert calls == []
    store.close()


@pytest.mark.parametrize('pooled', [False, True])
def test_loopback_allows_hostaddr_but_rejects_unsafe_options(monkeypatch, pooled):
    calls = []
    def connect(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeConnection()
    monkeypatch.setattr(psycopg, 'connect', connect)
    store = PostgresStore('host=127.0.0.1 hostaddr=127.0.0.1 user=u dbname=d')
    monkeypatch.setenv('PGHOSTADDR', '192.0.2.1')
    with store.connect(pooled=pooled):
        pass
    assert len(calls) == 1
    store.close()
    monkeypatch.setenv('PGOPTIONS', '-c search_path=attacker')
    with pytest.raises(ValueError, match='^unsafe_libpq_environment$'):
        with store.connect(pooled=pooled):
            pytest.fail('must not connect')
    assert len(calls) == 1
    store.close()


@pytest.mark.parametrize('pooled', [False, True])
def test_raw_unpinned_loopback_rejects_hostaddr_injection(monkeypatch, pooled):
    calls = []
    monkeypatch.setattr(psycopg, 'connect', lambda *a, **k: calls.append((a, k)))
    store = PostgresStore('host=127.0.0.1 user=u dbname=d')
    monkeypatch.setenv('PGHOSTADDR', '192.0.2.1')
    with pytest.raises(ValueError, match='^unsafe_libpq_environment$'):
        with store.connect(pooled=pooled):
            pytest.fail('must not connect')
    assert calls == []
    store.close()


def test_pool_revalidates_each_new_physical_connection(monkeypatch):
    calls = []
    def connect(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeConnection()
    monkeypatch.setattr(psycopg, 'connect', connect)
    store = PostgresStore('host=talkabout-db user=u dbname=d')
    try:
        with store.connect() as first:
            monkeypatch.setenv('PGHOSTADDR', '192.0.2.1')
            with pytest.raises(ValueError, match='^unsafe_libpq_environment$'):
                with store.connect():
                    pytest.fail('second connection must be refused')
            assert len(calls) == 1
        # Reusing an already established socket does not invoke libpq again.
        with store.connect() as reused:
            assert reused is first
        assert len(calls) == 1
    finally:
        store.close()


OWNER = 'host=talkabout-db port=5432 user=talkabout_owner dbname=talkabout password=owner-secret'
RUNTIME = 'host=talkabout-db port=5432 user=talkabout_app dbname=talkabout password=runtime-secret'


def test_provision_refuses_unsafe_environment_before_owner_connection(monkeypatch):
    calls = []
    monkeypatch.setattr(psycopg, 'connect', lambda *a, **kw: calls.append(a))
    monkeypatch.setenv('PGHOSTADDR', 'secret')
    with pytest.raises(ValueError, match='^database_identity_or_secret_mismatch_or_unavailable$'):
        provision.provision_database(OWNER, RUNTIME, 'a' * 32)
    assert calls == []


def test_provision_rechecks_before_runtime_connection(monkeypatch):
    calls = []
    class Row:
        def __init__(self, row): self.row = row
        def fetchone(self): return self.row
    class OwnerConnection(FakeConnection):
        def execute(self, query, *args):
            if 'pg_advisory' in query:
                return Row((1,))
            if 'to_regclass' in query:
                return Row(('public.installation_metadata',))
            if 'FROM pg_roles' in query:
                return Row((False,) * 5)
            if 'SELECT installation_id' in query:
                monkeypatch.setenv('PGOPTIONS', 'secret')
                return Row(('a' * 32,))
            pytest.fail('unexpected query')
    def connect(*args, **kwargs):
        calls.append(args)
        return OwnerConnection()
    monkeypatch.setattr(psycopg, 'connect', connect)
    with pytest.raises(ValueError, match='^database_identity_or_secret_mismatch_or_unavailable$'):
        provision.provision_database(OWNER, RUNTIME, 'a' * 32)
    assert len(calls) == 1
