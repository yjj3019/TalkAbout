import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from talkabout.domain import Problem, emit, token_hash
from talkabout.identity import IdentityService
from conftest import control, leased, result


def test_postgres_two_rooms_reverse_commit_cursor(postgres_fixture):
    store, core, rid = postgres_fixture
    other = core.create("fixture-a", {"topic": "Second PostgreSQL fixture"}, "other")
    cursor = core.events("fixture-a")["next_cursor"]
    entered, release = threading.Event(), threading.Event()
    def slower():
        with store.transaction(rid, "fixture-a") as (_, room):
            emit(room, "fixture.slower")
            entered.set()
            assert release.wait(3)
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(slower)
        assert entered.wait(3)
        with store.transaction(other["id"], "fixture-a") as (_, room):
            emit(room, "fixture.faster")
        first = core.events("fixture-a", cursor)
        assert [e["type"] for e in first["events"]] == ["fixture.faster"]
        release.set()
        pending.result(timeout=3)
    second = core.events("fixture-a", first["next_cursor"])
    assert [e["type"] for e in second["events"]] == ["fixture.slower"]


def test_postgres_same_room_lock_serializes_events(postgres_fixture):
    store, core, rid = postgres_fixture
    entered, release, second_acquired = threading.Event(), threading.Event(), threading.Event()
    def first():
        with store.transaction(rid, "fixture-a") as (_, room):
            emit(room, "fixture.first")
            entered.set()
            assert release.wait(3)
    def second():
        assert entered.wait(3)
        with store.transaction(rid, "fixture-b") as (_, room):
            second_acquired.set()
            emit(room, "fixture.second")
    with ThreadPoolExecutor(2) as pool:
        a, b = pool.submit(first), pool.submit(second)
        assert entered.wait(3)
        time.sleep(0.03)
        assert not second_acquired.is_set()
        release.set()
        a.result(timeout=3)
        b.result(timeout=3)
    events = core.events("fixture-a")["events"]
    chosen = [e for e in events if e["type"].startswith("fixture.")]
    assert [e["type"] for e in chosen] == ["fixture.first", "fixture.second"]
    assert chosen[1]["seq"] == chosen[0]["seq"] + 1


def test_postgres_pause_complete_race(postgres_fixture):
    _, core, rid = postgres_fixture
    task = leased(core, rid)
    barrier = threading.Barrier(2)
    def complete():
        barrier.wait()
        try:
            return core.mutation("fixture-a", rid, "tasks/" + task["id"] + "/complete", "complete", result(task))
        except Problem:
            return None
    def pause():
        barrier.wait()
        return control(core, rid, "pause")
    with ThreadPoolExecutor(2) as pool:
        a, b = pool.submit(complete), pool.submit(pause)
        posted = a.result(timeout=5)
        b.result(timeout=5)
    room = core.read("fixture-human", rid)
    assert room["state"] == "paused"
    assert len(room["messages"]) == (1 if posted else 0)
    with pytest.raises(Problem):
        core.mutation("fixture-a", rid, "tasks/" + task["id"] + "/complete", "after-pause", result(task, "new"))


def test_postgres_revocation_wakes_poll(postgres_fixture):
    store, core, _ = postgres_fixture
    cursor = core.events("fixture-a")["next_cursor"]
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(core.events, "fixture-a", cursor, 2)
        time.sleep(0.05)
        store.revoke("fixture-a")
        with pytest.raises(Problem, match="unauthorized"):
            pending.result(timeout=3)


def test_postgres_identity_issue_hash_and_replay(postgres_fixture):
    store, _, _ = postgres_fixture
    service = IdentityService(store)
    registered = service.mutate("fixture-human", "register", "register", {"display_name": "Cedar", "handle": "cedar"})
    body = {"scopes": ["agent:self_read", "room:read"], "expires_in": 30}
    issued = service.mutate("fixture-human", "credentials/" + registered["id"], "issue", body)
    assert store.auth(issued["token"])["id"] == registered["id"]
    with store.connect() as conn:
        row = conn.execute("SELECT token_hash FROM credentials WHERE credential_id=%s", (issued["credential_id"],)).fetchone()
        assert row["token_hash"] == token_hash(issued["token"])
        assert issued["token"] not in str(conn.execute("SELECT payload FROM principals").fetchall())
    assert "token" not in service.mutate("fixture-human", "credentials/" + registered["id"], "issue", body)
    service.mutate("fixture-human", "credentials/" + registered["id"] + "/" + issued["credential_id"] + "/revoke", "revoke", {})
    with pytest.raises(Problem, match="unauthorized"):
        store.auth(issued["token"])


def test_postgres_artifact_conflict(postgres_fixture):
    _, core, rid = postgres_fixture
    artifact = core.mutation("fixture-a", rid, "artifacts", "first", {"text": "first", "generation": 1, "membership_revision": 2})
    body = {"text": "next", "generation": 1, "membership_revision": 2, "expected_revision": 1}
    def update(key):
        try:
            return core.mutation("fixture-a", rid, "artifacts/" + artifact["id"] + "/versions", key, body)
        except Problem as exc:
            return exc.code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(update, ["update-a", "update-b"]))
    assert sum(isinstance(v, dict) for v in results) == 1
    assert "artifact_conflict" in results


def test_postgres_human_session_identity_and_logout(postgres_fixture):
    store, core, _ = postgres_fixture
    from talkabout.auth import HumanAuth, bootstrap_human
    owner = bootstrap_human(store, "session-fixture-owner", "Session Owner", "session-owner", "fixture-password-not-real")
    auth = HumanAuth(store)
    raw, data = auth.login({"workspace_id": "session-fixture-owner", "username": "session-owner", "password": "fixture-password-not-real"})
    actor = store.auth("session:" + raw)
    assert actor["id"] == owner["id"] and actor["kind"] == "human"
    auth.check_csrf(raw, data["csrf_token"])
    registered = IdentityService(store).mutate("session:" + raw, "register", "session-register", {"display_name": "Session Agent", "handle": "session-agent"})
    assert registered["kind"] == "agent"
    room = core.create("session:" + raw, {"topic": "Session fixture"}, "session-room")
    assert core.read("session:" + raw, room["id"])["id"] == room["id"]
    with store.connect() as conn:
        records = conn.execute("SELECT token_hash,csrf_hash FROM human_sessions").fetchall()
        assert raw not in str(records) and data["csrf_token"] not in str(records)
    store.logout(token_hash(raw))
    with pytest.raises(Problem, match="unauthorized"):
        core.me("session:" + raw)
