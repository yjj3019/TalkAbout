import copy
import threading
import time

import httpx
import pytest

from talkabout.domain import Problem, emit, replay
from talkabout.identity import IdentityService
from talkabout.retention import RetentionPolicy, compact_room_fixture
from talkabout.store import PostgresStore
from talkabout.worker import APIClient, Backpressure, Journal, RetryPolicy, StopRequested


def client(handler, policy=None):
    return APIClient("http://127.0.0.1", "synthetic-token", "http://127.0.0.1", httpx.MockTransport(handler), policy or RetryPolicy(base_seconds=0, max_seconds=0, budget_seconds=1))


@pytest.mark.parametrize("status", [401, 403, 404, 409, 410, 422])
def test_permanent_error_never_retries(status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"code": "fixture"})
    api = client(handler)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            api.request("POST", "fixture", {"v": 1}, "same-key")
        assert len(calls) == 1
    finally:
        api.close()


def test_transient_retry_same_key_body_and_bounded_exhaustion():
    calls = []
    def handler(request):
        calls.append((request.headers.get("Idempotency-Key"), request.content))
        if len(calls) == 1:
            raise httpx.ReadTimeout("synthetic response lost")
        return httpx.Response(503 if len(calls) == 2 else 200, json={"ok": True})
    api = client(handler)
    try:
        assert api.request("POST", "fixture", {"v": 1}, "stable") == {"ok": True}
        assert len(calls) == 3 and len(set(calls)) == 1
        calls.clear()
        with pytest.raises(httpx.ReadTimeout):
            api.request("POST", "fixture", {"v": 1})
        assert len(calls) == 1
    finally:
        api.close()
    exhausted = []
    api = client(lambda r: exhausted.append(r) or httpx.Response(503))
    try:
        with pytest.raises(httpx.HTTPStatusError):
            api.request("GET", "fixture")
        assert len(exhausted) == 3
    finally:
        api.close()


def test_retry_after_budget_and_stop_interrupts_backoff():
    calls = []
    api = client(lambda r: calls.append(r) or httpx.Response(429, headers={"Retry-After": "120"}))
    try:
        with pytest.raises(httpx.HTTPStatusError):
            api.request("GET", "fixture")
        assert len(calls) == 1
    finally:
        api.close()
    entered, stop = threading.Event(), threading.Event()
    api = client(lambda r: entered.set() or httpx.Response(503), RetryPolicy(base_seconds=1, max_seconds=1, budget_seconds=3))
    api.should_stop = stop.is_set
    outcome = []
    def run():
        try:
            api.request("GET", "fixture")
        except StopRequested:
            outcome.append("stopped")
    thread = threading.Thread(target=run)
    thread.start()
    assert entered.wait(1)
    stop.set()
    thread.join(timeout=0.3)
    assert not thread.is_alive() and outcome == ["stopped"]
    api.close()


@pytest.mark.parametrize("bounds", [dict(inbox_limit=1), dict(queue_limit=1, pending_limit=1), dict(pending_limit=1)])
def test_overflow_rolls_back_every_event_and_cursor(tmp_path, bounds):
    journal = Journal(tmp_path / "fixture.sqlite", **bounds)
    with journal.db:
        journal.set_setting("cursor", "old")
    batch = {"events": [{"event_id": f"e{i}", "type": "task.queued", "task_id": f"t{i}", "assignee": "a"} for i in range(2)], "next_cursor": "new"}
    with pytest.raises(Backpressure):
        journal.ingest(batch, "a")
    assert journal.setting("cursor") == "old"
    assert journal.status() == {} and journal.db.execute("SELECT count(*) FROM inbox").fetchone()[0] == 0
    batch["events"].pop()
    journal.ingest(batch, "a")
    journal.ingest(batch, "a")
    assert journal.status() == {"ready": 1} and journal.setting("cursor") == "new"
    journal.close()


def test_tombstones_block_reexecution_and_full_gap(fixture):
    store, core, rid = fixture
    cursor = core.events("fixture-a")["next_cursor"]
    with store.transaction(rid, "fixture-human") as (_, room):
        emit(room, "fixture.unread")
        for event in room["events"]:
            event["created_at"] = 0
        for record in room["keys"].values():
            record["response_until"] = 0
        compact_room_fixture(room, synthetic_fixture=True)
    room = store.rooms[rid]
    assert room["events"] == [] and room["first_retained_seq"] == room["last_seq"] + 1
    actor = store.auth("fixture-human")
    key = "human:memberships:invite-a"
    before = copy.deepcopy(room)
    body = {"principal_id": "a", "permissions": sorted(room["members"]["a"]["permissions"]), "status": "pending"}
    with pytest.raises(Problem, match="idempotency_retired"):
        replay(room, actor, "memberships", "invite-a", body)
    assert room == before and "digest" in room["keys"][key] and "response" not in room["keys"][key]
    with pytest.raises(Problem, match="cursor_gap"):
        core.events("fixture-a", cursor)
    assert core.events("fixture-a", cursor, rebase=True)["gaps"] == [rid]
    emit(room, "fixture.after_gap")
    assert room["events"][0]["seq"] == room["first_retained_seq"]


def test_identity_ttl_and_cursor_ttl(fixture):
    store, core, _ = fixture
    identity = IdentityService(store)
    body = {"display_name": "Fixture New", "handle": "fixture-new"}
    identity.mutate("fixture-human", "register", "register-once", body)
    store.principals["human"]["identity_operations"]["register:register-once"]["response_until"] = 0
    with pytest.raises(Problem, match="idempotency_retired"):
        identity.mutate("fixture-human", "register", "register-once", body)
    cursor = core.events("fixture-a")["next_cursor"]
    store.cursors[cursor]["expires_at"] = 0
    with pytest.raises(Problem, match="cursor_gap"):
        core.events("fixture-a", cursor)


def test_explicit_fixture_guard_and_bounds(fixture):
    with pytest.raises(ValueError, match="synthetic_fixture_required"):
        compact_room_fixture(fixture[0].rooms[fixture[2]])
    with pytest.raises(Problem, match="synthetic_fixture_required"):
        PostgresStore("never-connect").compact_fixture(synthetic_fixture=True)
    for value in (0, 59, True, 604801):
        with pytest.raises(ValueError):
            RetentionPolicy(cursor_seconds=value)
    with pytest.raises(ValueError):
        RetryPolicy(attempts=6)
    with pytest.raises(ValueError):
        RetryPolicy(budget_seconds=float("nan"))


def test_postgres_fixture_retention_is_atomic(postgres_fixture):
    store, core, rid = postgres_fixture
    cursor = core.events("fixture-a")["next_cursor"]
    with store.transaction(rid, "fixture-human") as (_, room):
        emit(room, "fixture.unread")
        # Existing event rows are immutable: backdate the fixture clock instead.
    _, rooms, _ = store.snapshot("fixture-human")
    latest = rooms[rid]["last_seq"]
    counts = store.compact_fixture(synthetic_fixture=True, now=time.time() + 604801)
    assert counts["events"] == latest and counts["cursors"] >= 1
    with pytest.raises(Problem, match="cursor_gap"):
        core.events("fixture-a", cursor)
    _, rooms, _ = store.snapshot("fixture-human")
    assert rooms[rid]["events"] == [] and rooms[rid]["first_retained_seq"] == latest + 1
    with pytest.raises(Problem, match="idempotency_retired"):
        core.accept("fixture-a", rid, "accept", {"expected_revision": 1, "acknowledge_private": True})
    with store.transaction(rid, "fixture-human") as (_, room):
        emit(room, "fixture.after_cleanup")
    _, rooms, _ = store.snapshot("fixture-human")
    assert rooms[rid]["events"][0]["seq"] == latest + 1
