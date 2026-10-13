import copy
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from conftest import LocalClient, result
from talkabout.core import Core
from talkabout.domain import HUMAN_PERMISSIONS, Problem
from conftest import control
from talkabout.bounds import room_bounds


def accept_body(room):
    return {"expected_room_revision": room["room_revision"], "expected_handover_revision": room["handover"]["revision"], "acknowledge_history": True}


def complete_next(core, rid):
    room = core.read("fixture-a", rid)
    task = next(t for t in room["tasks"] if t["state"] == "queued")
    claimed = core.mutation("fixture-a", rid, f"tasks/{task['id']}/claim", "claim-"+task["id"], {"generation": task["generation"], "membership_revision": task["membership_revision"]})
    response = core.mutation("fixture-a", rid, f"tasks/{task['id']}/complete", "complete-"+task["id"], result(claimed, task["purpose"]+" synthetic text"))
    return task, response


def test_handover_owner_only_revision_audit_and_replay(fixture):
    store, core, _ = fixture
    room = core.create("fixture-a", {"topic": "Synthetic handover"}, "agent-room")
    rid, body = room["id"], accept_body(room)
    with pytest.raises(Problem, match="not_found"):
        core.read("fixture-human", rid)
    offers = core.handovers("fixture-human")
    assert len(offers) == 1 and "topic" not in offers[0]
    with pytest.raises(Problem):
        core.accept_handover("fixture-a", rid, "spoof", body)
    with pytest.raises(Problem, match="handover_conflict"):
        core.accept_handover("fixture-human", rid, "stale", {**body, "expected_room_revision": 0})
    with pytest.raises(Problem, match="invalid_handover_fields"):
        core.accept_handover("fixture-human", rid, "badge", {**body, "role": "admin"})
    store.provision({"id": "other", "kind": "human", "owner": "other-workspace", "display_name": "Other", "handle": "other"}, "fixture-other", HUMAN_PERMISSIONS)
    with pytest.raises(Problem, match="not_found"):
        core.accept_handover("fixture-other", rid, "other", body)
    accepted = core.accept_handover("fixture-human", rid, "accept-owner", body)
    assert core.accept_handover("fixture-human", rid, "accept-owner", body) == accepted
    current = core.read("fixture-human", rid)
    assert current["generation"] == 2 and current["room_revision"] == 2
    assert current["members"]["human"]["status"] == "active"
    assert accepted["accepted_by"] == "human" and accepted["accepted_seq"] > 0
    assert core.handovers("fixture-human") == []
    core.mutation("fixture-human", rid, "memberships/human", "leave", {"expected_revision": 1, "status": "left", "permissions": sorted(HUMAN_PERMISSIONS)})
    with pytest.raises(Problem, match="not_found"):
        core.accept_handover("fixture-human", rid, "accept-owner", body)


def test_handover_and_unsupported_modes_through_api(fixture):
    store, core, _ = fixture
    room = core.create("fixture-a", {"topic": "Synthetic API handover"}, "api-agent-room")
    human = LocalClient(store, "fixture-human")
    assert human.request("GET", "admin/handovers")[0]["room_id"] == room["id"]
    assert human.request("POST", f"rooms/{room['id']}/handover/accept", accept_body(room), "accept")["state"] == "accepted"
    with pytest.raises(httpx.HTTPStatusError) as error:
        LocalClient(store).request("POST", f"rooms/{room['id']}/turn-requests", {"generation": 2, "membership_revision": 1}, "unsupported")
    assert error.value.response.json()["code"] == "unsupported_turn_request"


def test_free_discussion_requires_bounded_request_and_no_recursive_turns(fixture):
    _, core, _ = fixture
    room = core.create("fixture-a", {"topic": "Synthetic free", "mode": "free_discussion"}, "free")
    rid = room["id"]
    assert room["tasks"] == []
    body = {"generation": 1, "membership_revision": 1}
    first = core.mutation("fixture-a", rid, "turn-requests", "turn", body)
    assert core.mutation("fixture-a", rid, "turn-requests", "turn", body) == first
    with pytest.raises(Problem, match="turn_already_pending"):
        core.mutation("fixture-a", rid, "turn-requests", "another", body)
    task, _ = complete_next(core, rid)
    assert task["purpose"] == "requested_turn"
    assert not any(t["state"] == "queued" for t in core.read("fixture-a", rid)["tasks"])
    assert core.read("fixture-a", rid)["state"] == "active"


def test_structured_phase_order_and_round_close(fixture):
    _, core, _ = fixture
    room = core.create("fixture-a", {"topic": "Synthetic structured"}, "structured")
    phases = [complete_next(core, room["id"])[0]["phase"] for _ in range(3)]
    assert phases == ["proposal", "review", "synthesis"]
    current = core.read("fixture-a", room["id"])
    assert current["state"] == "closed" and current["mode_status"] == "round_limit"
    assert all(t["round"] == 1 for t in current["tasks"])


def test_co_creation_role_proposal_review_and_human_adoption(fixture):
    _, core, _ = fixture
    room = core.create("fixture-a", {"topic": "Synthetic creation", "mode": "co_creation"}, "creation")
    core.accept_handover("fixture-human", room["id"], "accept", accept_body(room))
    roles = []
    for _ in range(3):
        task, response = complete_next(core, room["id"])
        roles.append(task["role"])
        if task["role"] == "creator":
            assert response["artifact"]["versions"][0]["text"] == "creator synthetic text"
    assert roles == ["researcher", "creator", "reviewer"]
    current = core.read("fixture-human", room["id"])
    assert current["mode_status"] == "awaiting_adoption" and current["state"] == "active"
    artifact = current["artifacts"][0]
    body = {"generation": current["generation"], "membership_revision": 1, "expected_revision": artifact["revision"], "version_id": artifact["versions"][0]["id"]}
    with pytest.raises(Problem, match="forbidden"):
        core.mutation("fixture-a", room["id"], f"artifacts/{artifact['id']}/adoptions", "agent-adopt", body)
    core.mutation("fixture-human", room["id"], f"artifacts/{artifact['id']}/adoptions", "adopt", body)
    assert core.read("fixture-human", room["id"])["mode_status"] == "adopted"
    assert core.read("fixture-human", room["id"])["state"] == "closed"


def test_capacity_errors_are_explicit_and_rollback(fixture):
    store, core, rid = fixture
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem, match="invalid_sources"):
        core.mutation("fixture-a", rid, "artifacts", "bad-source", {"generation": 1, "membership_revision": 2, "text": "x", "sources": [{"arbitrary": "object"}]})
    assert store.rooms[rid] == before
    with pytest.raises(Problem, match="room_byte_capacity"):
        room_bounds({"events": [], "payload": "x" * 2097153})
    for i in range(65):
        store.rooms[f"closed-{i}"] = {**copy.deepcopy(before), "id": f"closed-{i}", "state": "closed"}
    with pytest.raises(Problem, match="room_list_capacity_paging_unsupported"):
        core.rooms("fixture-human")


def test_paused_free_turn_cannot_submit_and_deadline_closes(fixture):
    store, core, _ = fixture
    room = core.create("fixture-a", {"topic": "Synthetic paused free", "mode": "free_discussion"}, "pause-free")
    rid = room["id"]
    core.accept_handover("fixture-human", rid, "accept", accept_body(room))
    core.mutation("fixture-a", rid, "turn-requests", "request", {"generation": 2, "membership_revision": 1})
    task = next(t for t in core.read("fixture-a", rid)["tasks"] if t["state"] == "queued")
    claimed = core.mutation("fixture-a", rid, f"tasks/{task['id']}/claim", "claim", {"generation": 2, "membership_revision": 1})
    control(core, rid, "pause")
    with pytest.raises(Problem, match="room_not_active"):
        core.mutation("fixture-a", rid, f"tasks/{task['id']}/complete", "late", result(claimed))
    assert core.read("fixture-human", rid)["messages"] == []
    control(core, rid, "resume")
    store.rooms[rid]["deadline"] = 0
    core.tick("fixture-human", rid)
    assert core.read("fixture-human", rid)["state"] == "closed"


def test_co_creation_requires_contribution_scope(fixture):
    store, core, _ = fixture
    room = core.create("fixture-a", {"topic": "Synthetic scope", "mode": "co_creation"}, "scope-create")
    rid = room["id"]
    core.accept_handover("fixture-human", rid, "accept", accept_body(room))
    member = core.read("fixture-human", rid)["members"]["a"]
    core.mutation("fixture-human", rid, "memberships/a", "restrict", {"expected_revision": member["revision"], "permissions": [p for p in member["permissions"] if p != "room:contribute"], "status": "active"})
    assert not any(t["state"] in {"queued", "leased"} for t in core.read("fixture-a", rid)["tasks"])


def test_postgres_handover_same_key_concurrency_and_modes(postgres_fixture):
    _, core, _ = postgres_fixture
    room = core.create("fixture-a", {"topic": "Synthetic concurrent handover", "mode": "co_creation"}, "pg-create")
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: core.accept_handover("fixture-human", room["id"], "same", accept_body(room)), range(2)))
    assert results[0] == results[1]
    for phase in ("researcher", "creator", "reviewer"):
        assert complete_next(core, room["id"])[0]["phase"] == phase
    assert core.read("fixture-human", room["id"])["mode_status"] == "awaiting_adoption"
