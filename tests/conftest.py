import pytest
from fastapi.testclient import TestClient

from talkabout.api import create_app
from talkabout.core import Core
from talkabout.domain import AGENT_PERMISSIONS, HUMAN_PERMISSIONS, SELF_PERMISSIONS
from talkabout.store import MemoryStore


@pytest.fixture
def fixture():
    store = MemoryStore()
    for pid, kind, name in [("human", "human", "Owner"), ("a", "agent", "Atlas"), ("b", "agent", "Birch")]:
        permissions = (HUMAN_PERMISSIONS | {"rooms:create", "agent:manage"}) if kind == "human" else (AGENT_PERMISSIONS | SELF_PERMISSIONS | {"rooms:create"})
        store.provision({"id": pid, "owner": "fixture-owner", "kind": kind, "display_name": name, "handle": name.lower()}, "fixture-" + pid, permissions)
    core = Core(store)
    room = core.create("fixture-human", {"topic": "Synthetic neutral topic"}, "room-one")
    for pid in ["a", "b"]:
        member = core.mutation("fixture-human", room["id"], "memberships", "invite-" + pid,
                               {"principal_id": pid, "permissions": sorted(AGENT_PERMISSIONS), "status": "pending"})
        core.accept("fixture-" + pid, room["id"], "accept", {"expected_revision": member["revision"], "acknowledge_private": True})
    return store, core, room["id"]


def leased(core, room_id, token="fixture-a"):
    room = core.read(token, room_id)
    task = next(t for t in room["tasks"] if t["state"] == "queued")
    return core.mutation(token, room_id, "tasks/" + task["id"] + "/claim", "claim-" + task["id"],
                         {"generation": task["generation"], "membership_revision": task["membership_revision"]})


def result(task, text="Fixture result"):
    return {k: task[k] for k in ("attempt", "generation", "membership_revision")} | {
        "result": {"client_message_id": "result-" + task["id"], "text": text, "reply_to": None, "mentions": []}}


def control(core, room_id, action):
    room = core.read("fixture-human", room_id)
    pending = core.mutation("fixture-human", room_id, "controls", action + "-request-" + str(room["room_revision"]),
                            {"action": action, "expected_revision": room["room_revision"]})
    return core.mutation("fixture-human", room_id, "controls/" + pending["id"] + "/approve", action + "-approve", {})


class LocalClient:
    def __init__(self, store, token="fixture-a"):
        self.http = TestClient(create_app(store, start_scheduler=False))
        self.token = token
        self.calls = []

    def request(self, method, path, body=None, key=None, params=None):
        import httpx
        self.calls.append((method, path))
        response = self.http.request(method, "/api/v1/" + path, json=body, params=params,
                                     headers={"Authorization": "Bearer " + self.token, **({"Idempotency-Key": key} if key else {})})
        response.raise_for_status()
        return response.json()
