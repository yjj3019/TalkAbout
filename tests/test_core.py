import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from talkabout.domain import AGENT_PERMISSIONS, Problem, emit
from conftest import control, leased, result


def test_scheduler_duplicate_and_complete_replay(fixture):
    store, core, rid = fixture
    for _ in range(5):
        store.tick_all()
    assert len(core.read("fixture-a", rid)["tasks"]) == 1
    task = leased(core, rid)
    body = result(task)
    path = "tasks/" + task["id"] + "/complete"
    original = core.mutation("fixture-a", rid, path, "complete", body)
    assert core.mutation("fixture-a", rid, path, "complete", body) == original
    assert core.mutation("fixture-a", rid, path, "another-key", body)["message"]["id"] == original["message"]["id"]
    assert len(core.read("fixture-human", rid)["messages"]) == 1
    with pytest.raises(Problem, match="idempotency_conflict"):
        core.mutation("fixture-a", rid, path, "complete", result(task, "different"))
    with pytest.raises(Problem, match="client_message_conflict"):
        core.mutation("fixture-a", rid, path, "third-key", result(task, "different"))


def test_expired_attempt_fenced(fixture):
    store, core, rid = fixture
    first = leased(core, rid)
    with store.transaction(rid, "fixture-human") as (_, room):
        room["tasks"][first["id"]]["lease_expires_at"] = time.time() - 1
    store.tick_all()
    second = core.mutation("fixture-a", rid, "tasks/" + first["id"] + "/claim", "second-claim",
                           {"generation": first["generation"], "membership_revision": first["membership_revision"]})
    assert second["attempt"] == 2
    with pytest.raises(Problem, match="stale_attempt"):
        core.mutation("fixture-a", rid, "tasks/" + first["id"] + "/complete", "late", result(first))


def test_pause_complete_race_and_replay(fixture):
    store, core, rid = fixture
    task = leased(core, rid)
    barrier = threading.Barrier(2)
    def pause():
        barrier.wait()
        return control(core, rid, "pause")
    def complete():
        barrier.wait()
        try:
            return core.mutation("fixture-a", rid, "tasks/" + task["id"] + "/complete", "race", result(task))
        except Problem as e:
            return e.code
    with ThreadPoolExecutor(2) as pool:
        pa, co = pool.submit(pause), pool.submit(complete)
        pa.result()
        posted = co.result()
    room = core.read("fixture-human", rid)
    assert room["state"] == "paused"
    assert len(room["messages"]) == (1 if isinstance(posted, dict) else 0)
    with pytest.raises(Problem):
        core.mutation("fixture-a", rid, "artifacts", "paused-artifact", {"generation": room["generation"], "membership_revision": 2, "text": "x"})
    control(core, rid, "resume")
    assert core.read("fixture-human", rid)["generation"] == 3
    control(core, rid, "close")
    with pytest.raises(Problem, match="room_closed"):
        control(core, rid, "resume")


def test_block_unblock_old_attempt_and_other_member(fixture):
    _, core, rid = fixture
    task = leased(core, rid)
    room = core.read("fixture-human", rid)
    generation = room["generation"]
    member = core.mutation("fixture-human", rid, "memberships/a", "block", {"expected_revision": 2, "permissions": sorted(AGENT_PERMISSIONS), "status": "blocked"})
    assert core.read("fixture-b", rid)["generation"] == generation
    with pytest.raises(Problem):
        core.read("fixture-a", rid)
    invitation = core.mutation("fixture-human", rid, "memberships/a", "unblock", {"expected_revision": member["revision"], "permissions": sorted(AGENT_PERMISSIONS), "status": "pending"})
    core.accept("fixture-a", rid, "accept-again", {"expected_revision": invitation["revision"], "acknowledge_private": True})
    with pytest.raises(Problem, match="stale_attempt"):
        core.mutation("fixture-a", rid, "tasks/" + task["id"] + "/complete", "old-result", result(task))


def test_artifact_conflict_and_room_isolation(fixture):
    _, core, rid = fixture
    artifact = core.mutation("fixture-a", rid, "artifacts", "first", {"generation": 1, "membership_revision": 2, "text": "original"})
    body = {"generation": 1, "membership_revision": 2, "expected_revision": 1, "text": "next"}
    def submit(key):
        try:
            return core.mutation("fixture-a", rid, "artifacts/" + artifact["id"] + "/versions", key, body)
        except Problem as exc:
            return exc.code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(submit, ["v1", "v2"]))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert "artifact_conflict" in results
    other = core.create("fixture-b", {"topic": "Other fixture room"}, "other")
    with pytest.raises(Problem):
        core.read("fixture-a", other["id"])
    with pytest.raises(Problem, match="not_found"):
        core.mutation("fixture-b", other["id"], "artifacts/" + artifact["id"] + "/versions", "cross-room", {**body, "membership_revision": 1})


def test_vector_cursor_commit_order_and_scope_rebase(fixture):
    store, core, rid = fixture
    other = core.create("fixture-a", {"topic": "Second fixture"}, "other")
    first = core.events("fixture-a")
    # Contract simulation only: PostgreSQL reverse-commit integration is a separate gate.
    for target in [other["id"], rid]:
        with store.transaction(target, "fixture-a") as (_, room):
            emit(room, "fixture.new")
    updated = core.events("fixture-a", first["next_cursor"])
    assert {e["room_id"] for e in updated["events"]} == {rid, other["id"]}
    core.create("fixture-a", {"topic": "Third fixture"}, "third")
    with pytest.raises(Problem, match="cursor_scope_changed"):
        core.events("fixture-a", updated["next_cursor"])
    assert len(core.events("fixture-a", updated["next_cursor"], rebase=True)["rooms"]) == 3


def test_revoked_token_reauthorizes_waiting_poll(fixture):
    store, core, _ = fixture
    cursor = core.events("fixture-a")["next_cursor"]
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(core.events, "fixture-a", cursor, 2)
        time.sleep(0.03)
        store.revoke("fixture-a")
        with pytest.raises(Problem, match="unauthorized"):
            pending.result(timeout=2)


def test_name_collision_and_reserved_name(fixture):
    store, _, _ = fixture
    with pytest.raises(Problem, match="identity_conflict"):
        store.provision({"id": "duplicate", "owner": "fixture-owner", "kind": "agent", "display_name": "ＡＴＬＡＳ", "handle": "another"}, "fixture-duplicate", AGENT_PERMISSIONS)
    with pytest.raises(Problem, match="reserved_name"):
        store.provision({"id": "fake-admin", "owner": "fixture-owner", "kind": "agent", "display_name": "Admin", "handle": "something"}, "fixture-fake", AGENT_PERMISSIONS)


def test_client_id_cannot_be_reused_for_another_task(fixture):
    _, core, rid = fixture
    first = leased(core, rid)
    body = result(first)
    core.mutation("fixture-a", rid, "tasks/" + first["id"] + "/complete", "first", body)
    other = leased(core, rid, "fixture-b")
    core.mutation("fixture-b", rid, "tasks/" + other["id"] + "/complete", "second", result(other))
    current = leased(core, rid)
    with pytest.raises(Problem, match="client_message_conflict"):
        core.mutation("fixture-a", rid, "tasks/" + current["id"] + "/complete", "third", body)

def test_execution_events_are_recorded_bounded_and_exposed(fixture):
    store, core, rid = fixture
    task = leased(core, rid)
    fence = {k: task[k] for k in ("attempt", "generation", "membership_revision")}
    path = "tasks/" + task["id"] + "/heartbeat"
    core.mutation("fixture-a", rid, path, "hb-one", {**fence, "events": ["runtime_started", "tool_blocked"]})
    view = core.read("fixture-a", rid)
    events = next(item for item in view["tasks"] if item["id"] == task["id"])["events"]
    assert [event["stage"] for event in events] == ["claimed", "runtime_started", "tool_blocked"]
    assert all(isinstance(event["at"], float) for event in events)
    with pytest.raises(Problem, match="invalid_event_stage"):
        core.mutation("fixture-a", rid, path, "hb-bad", {**fence, "events": ["shell"]})
    with pytest.raises(Problem, match="invalid_events"):
        core.mutation("fixture-a", rid, path, "hb-many", {**fence, "events": ["preflight"] * 9})
    for index in range(30):
        core.mutation("fixture-a", rid, path, f"hb-cap-{index}", {**fence, "events": ["preflight"]})
    capped = next(item for item in core.read("fixture-a", rid)["tasks"] if item["id"] == task["id"])["events"]
    assert len(capped) == 12
    with store.transaction(rid, "fixture-human") as (_, room):
        assert len(room["task_events"][task["id"]]) == 24
