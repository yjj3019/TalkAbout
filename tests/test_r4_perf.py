"""Round-4 performance contract tests.

These lock in the properties the R3 analysis measured:
* connections are reused instead of re-established per query,
* the scheduler does not rewrite/broadcast unchanged rooms,
* polling does not load room event history it never reads,
* static assets are compressed and revalidated instead of re-downloaded,
* bounded-capacity checks stay in force where they matter.
"""
import gzip
import json
import time as clock
from types import SimpleNamespace

import pytest

from talkabout.api import create_app
from talkabout.bounds import snapshot_bounds
from talkabout.domain import Problem, schedule, token_hash
from talkabout.pool import ConnectionPool, PoolClosed, PoolExhausted


class FakeConnection:
    def __init__(self, status=0):
        self.closed = False
        self.broken = False
        self.autocommit = False
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self.info = SimpleNamespace(transaction_status=status)

    def commit(self):
        self.commits += 1
        self.info.transaction_status = 0

    def rollback(self):
        self.rollbacks += 1
        self.info.transaction_status = 0

    def close(self):
        self.closes += 1
        self.closed = True


def test_pool_reuses_connections():
    created = []
    pool = ConnectionPool(lambda: created.append(FakeConnection()) or created[-1], max_size=2)
    first = None
    for _ in range(4):
        with pool.connection() as conn:
            if first is None:
                first = conn
            assert conn is first
    assert len(created) == 1
    assert pool.stats()["reused"] == 3


def test_pool_commits_on_success_and_rolls_back_on_failure():
    created = []
    pool = ConnectionPool(lambda: created.append(FakeConnection()) or created[-1], max_size=1)
    with pool.connection() as conn:
        pass
    assert conn.commits == 1

    with pytest.raises(RuntimeError):
        with pool.connection() as conn:
            raise RuntimeError("boom")
    assert conn.rollbacks == 1
    assert not conn.closed


def test_pool_discards_broken_connections():
    created = []
    pool = ConnectionPool(lambda: created.append(FakeConnection()) or created[-1], max_size=1)
    with pytest.raises(RuntimeError):
        with pool.connection() as conn:
            conn.broken = True
            raise RuntimeError("connection died")
    assert conn.closed
    with pool.connection() as second:
        assert second is not conn
    assert len(created) == 2


def test_pool_is_bounded_and_fails_closed():
    pool = ConnectionPool(FakeConnection, max_size=1, acquire_timeout=0.05)
    with pool.connection():
        with pytest.raises(PoolExhausted):
            with pool.connection():
                pass


def test_pool_sets_autocommit_per_checkout():
    pool = ConnectionPool(FakeConnection, max_size=1)
    with pool.connection(autocommit=True) as conn:
        assert conn.autocommit is True
    with pool.connection() as conn:
        assert conn.autocommit is False


def test_pool_refuses_after_close():
    pool = ConnectionPool(FakeConnection, max_size=1)
    pool.close()
    with pytest.raises(PoolClosed):
        with pool.connection():
            pass


def test_snapshot_projection_can_skip_event_history(fixture):
    store, core, room_id = fixture
    _, rooms, _ = store.snapshot("fixture-a", include_events=False)
    assert rooms[room_id].get("events", []) == []
    assert rooms[room_id]["last_seq"] >= 0
    _, full_rooms, _ = store.snapshot("fixture-a")
    assert isinstance(full_rooms[room_id]["events"], list)


def test_room_events_returns_only_the_delta(fixture):
    store, core, room_id = fixture
    core.mutation("fixture-human", room_id, "messages", "perf-delta-1",
                  {"text": "delta probe", "kind": "advice", "client_message_id": "perf-delta-1"})
    full = store.rooms[room_id]["events"]
    assert full, "expected at least one event"
    tail = full[-1]["seq"]
    assert store.room_events(room_id, tail, 16) == []
    earlier = full[-2]["seq"] if len(full) > 1 else 0
    delta = store.room_events(room_id, earlier, 16)
    assert [event["seq"] for event in delta] == [tail]


def test_idle_tick_does_not_mark_a_change(fixture):
    store, core, room_id = fixture
    assert store.tick_all() in (False, None) or store.tick_all() is False
    assert store.tick_all() is False


def test_schedule_reports_only_real_changes(fixture):
    store, core, room_id = fixture
    room = store.rooms[room_id]
    principals = store.principals
    assert schedule(room, principals, now=clock.time()) is False
    closed = schedule(room, principals, now=room["deadline"] + 1)
    assert closed is True
    assert room["state"] == "closed"


def test_idle_poll_takes_one_snapshot(fixture):
    store, core, room_id = fixture
    first = core.events("fixture-a")
    calls = []
    original = store.snapshot

    def counting(token, include_events=True):
        calls.append(include_events)
        return original(token, include_events)

    store.snapshot = counting
    try:
        idle = core.events("fixture-a", first["next_cursor"], wait_seconds=0)
    finally:
        store.snapshot = original
    assert idle["events"] == []
    assert calls == [False], "an idle poll must not take the second snapshot"


def test_polling_never_requests_event_history(fixture):
    store, core, room_id = fixture
    seen = []
    original = store.snapshot

    def recording(token, include_events=True):
        seen.append(include_events)
        return original(token, include_events)

    store.snapshot = recording
    try:
        core.events("fixture-a", wait_seconds=0)
    finally:
        store.snapshot = original
    assert seen and all(flag is False for flag in seen)


def test_snapshot_bounds_can_skip_byte_serialisation():
    rooms = {"room": {"payload": "x" * 2097153, "events": []}}
    snapshot_bounds(rooms, {}, deep=False)
    with pytest.raises(Problem, match="room_byte_capacity"):
        snapshot_bounds(rooms, {}, deep=True)


def test_static_assets_are_compressed_and_revalidated():
    from talkabout.store import MemoryStore
    app = create_app(MemoryStore(), start_scheduler=False, allow_fixture_bearer=True)
    from fastapi.testclient import TestClient
    client = TestClient(app)
    response = client.get("/web/app.mjs", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"
    assert response.headers["cache-control"].startswith("public")
    etag = response.headers.get("etag")
    assert etag
    cached = client.get("/web/app.mjs", headers={"If-None-Match": etag})
    assert cached.status_code == 304
    assert cached.content == b""
    assert len(response.content) > 100000  # already decoded by the test client


def test_api_responses_stay_uncached():
    from talkabout.store import MemoryStore
    app = create_app(MemoryStore(), start_scheduler=False, allow_fixture_bearer=True)
    from fastapi.testclient import TestClient
    response = TestClient(app).get("/health")
    assert response.headers["cache-control"] == "no-store"


def test_postgres_pool_is_reused(postgres_fixture):
    store, core, room_id = postgres_fixture
    created_before = store.pool_stats()["created"]
    for _ in range(6):
        core.rooms("fixture-a")
    stats = store.pool_stats()
    assert stats["created"] - created_before <= 1
    assert stats["reused"] >= 5


def test_postgres_tick_is_a_no_op_without_changes(postgres_fixture):
    store, core, room_id = postgres_fixture
    assert store.tick_all() is False
    assert store.tick_all() is False


def test_postgres_room_events_only_returns_the_delta(postgres_fixture):
    store, core, room_id = postgres_fixture
    core.mutation("fixture-human", room_id, "messages", "perf-pg-delta",
                  {"text": "delta probe", "kind": "advice", "client_message_id": "perf-pg-delta"})
    _, rooms, _ = store.snapshot("fixture-a", include_events=False)
    tail = rooms[room_id]["last_seq"]
    assert store.room_events(room_id, tail, 16) == []
    delta = store.room_events(room_id, tail - 1, 16)
    assert [event["seq"] for event in delta] == [tail]


def test_room_fence_is_a_small_fenced_projection(fixture):
    from conftest import leased
    store, core, room_id = fixture
    leased(core, room_id)
    room = core.read("fixture-a", room_id)
    task = next(t for t in room["tasks"] if t["state"] == "leased")
    fence = core.room_fence("fixture-a", room_id, task["id"])
    assert fence["room_id"] == room_id
    assert fence["state"] == room["state"] == "active"
    assert fence["generation"] == room["generation"]
    assert fence["membership_revision"] == room["membership_revision"]
    assert set(fence) == {"room_id", "state", "generation", "membership_revision", "last_seq", "task"}
    assert fence["task"]["id"] == task["id"]
    assert fence["task"]["assignee"] == "a"
    assert fence["task"]["attempt"] == task["attempt"]
    # ~1 KB regardless of room size, versus the full projection this replaced.
    assert len(json.dumps(fence).encode()) < 1024
    # A vanished task is a null fence, not a 404: the worker still has to be able
    # to record a stale outcome instead of crashing mid-read.
    assert core.room_fence("fixture-a", room_id, "missing-task")["task"] is None


def test_credential_batch_matches_single_lookup(fixture):
    store, core, room_id = fixture
    binding = token_hash("fixture-a")
    assert store.credential_active(binding, "a", room_id, ()) is True
    rows = store.credentials_active([binding, token_hash("fixture-b"), None])
    assert set(rows) == {binding, token_hash("fixture-b")}
    assert rows[binding]["principal_id"] == "a"
    assert rows[binding]["room_id"] in {None, room_id}
    assert store.credential_active(token_hash("missing-token"), "a") is False
    assert store.credentials_active([]) == {}


def test_postgres_fence_and_batch_credentials(postgres_fixture):
    store, core, room_id = postgres_fixture
    fence = core.room_fence("fixture-a", room_id)
    assert fence["room_id"] == room_id and fence["task"] is None
    assert len(json.dumps(fence).encode()) < 1024
    room = core.read("fixture-a", room_id)
    assert fence["membership_revision"] == room["membership_revision"]
    assert fence["generation"] == room["generation"]
    binding = token_hash("fixture-a")
    rows = store.credentials_active([binding])
    assert rows[binding]["principal_id"] == "a"
    assert store.credential_active(binding, "a", room_id, ()) is True
