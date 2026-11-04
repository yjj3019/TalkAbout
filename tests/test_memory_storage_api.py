"""Unfinished memory storage must not be exposed through the public API."""
from fastapi.testclient import TestClient

from talkabout.api import create_app


def test_unfinished_storage_and_runtime_injection_are_not_public(fixture):
    store, core, rid = fixture
    app = create_app(store, start_scheduler=False, allow_fixture_bearer=True)
    headers = {'Authorization': 'Bearer fixture-human', 'Idempotency-Key': 'synthetic-memory'}
    with TestClient(app) as client:
        path = f'/api/v1/rooms/{rid}/memories'
        assert client.get(path, headers=headers).status_code == 404
        assert client.post(path, json={'confirm': True}, headers=headers).status_code == 404
        assert client.post(path + '/00000000-0000-4000-8000-000000000000/revoke',
                           json={'confirm': True, 'expected_revision': 1}, headers=headers).status_code == 404
        assert client.post(path + '/00000000-0000-4000-8000-000000000000/forget-own',
                           json={'confirm': True}, headers=headers).status_code == 404
    assert not store.memory_records


def test_default_postgres_schema_has_no_experimental_memory_tables(postgres_fixture):
    store, _, _ = postgres_fixture
    with store.connect() as conn:
        names = conn.execute("SELECT to_regclass('room_memories') AS memories, "
                             "to_regclass('room_memory_operations') AS operations").fetchone()
    assert names['memories'] is None and names['operations'] is None
    assert store.ready()
