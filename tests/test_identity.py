import json
import time

import httpx
import pytest

from talkabout.domain import AGENT_PERMISSIONS, SELF_PERMISSIONS, Problem, token_hash
from talkabout.identity import IdentityService
from conftest import LocalClient, control, leased, result


def test_registration_and_name_revision_history(fixture):
    store, core, rid = fixture
    client = LocalClient(store, "fixture-human")
    body = {"display_name": "Cedar", "handle": "cedar"}
    registered = client.request("POST", "agents", body, "register")
    assert client.request("POST", "agents", body, "register")["id"] == registered["id"]
    with pytest.raises(httpx.HTTPStatusError) as conflict:
        client.request("POST", "agents", {"display_name": "ＣＥＤＡＲ", "handle": "other"}, "duplicate")
    assert conflict.value.response.status_code == 409
    task = leased(core, rid)
    core.mutation("fixture-a", rid, "tasks/" + task["id"] + "/complete", "original-name", result(task))
    renamed = client.request("PATCH", "agents/a/identity", {"display_name": "Atlas Two", "handle": "atlas-two", "expected_revision": 1}, "rename")
    assert renamed["id"] == "a" and renamed["identity_revision"] == 2 and renamed["name_history"][0]["display_name"] == "Atlas"
    assert core.read("fixture-human", rid)["messages"][0]["display_name"] == "Atlas"
    with pytest.raises(httpx.HTTPStatusError):
        client.request("PATCH", "agents/a/identity", {"display_name": "Old", "handle": "old", "expected_revision": 1}, "stale")


def test_capabilities_and_name_proposals_cannot_expand_permissions(fixture):
    store, _, _ = fixture
    client = LocalClient(store)
    capabilities = client.request("PUT", "agents/me/capabilities", {"http": True, "persistent_worker": True, "text": True, "model_label": "fixture model"}, "caps")
    assert capabilities["model"]["verification"] == "self_reported"
    original = store.auth("fixture-a")["scopes"]
    proposed = client.request("POST", "agents/a/name-proposals", {"display_name": "Proposed Atlas", "handle": "proposed-atlas"}, "proposal")
    assert proposed["state"] == "pending" and store.auth("fixture-a")["display_name"] == "Atlas"
    for path, body in [("agents/b/name-proposals", {"display_name": "Fake", "handle": "fake"}),
                       ("agents/me/capabilities", {"model_label": "x", "badge": "verified"}),
                       ("agents/a/credentials", {"scopes": ["room:control"], "expires_in": 30})]:
        with pytest.raises(httpx.HTTPStatusError):
            client.request("PUT" if "capabilities" in path else "POST", path, body, path)
    assert store.auth("fixture-a")["scopes"] == original


def test_ephemeral_credential_hash_rotation_revoke(fixture):
    store, _, _ = fixture
    service = IdentityService(store)
    scopes = sorted(AGENT_PERMISSIONS | SELF_PERMISSIONS)
    body = {"scopes": scopes, "expires_in": 30}
    issued = service.mutate("fixture-human", "credentials/a", "fixture-issue", body)
    raw = issued["token"]
    assert len(raw) >= 40 and store.auth(raw)["id"] == "a"
    assert raw not in json.dumps(store.credentials) and raw not in json.dumps(store.principals)
    replay = service.mutate("fixture-human", "credentials/a", "fixture-issue", body)
    assert replay["token_not_recoverable"] and "token" not in replay
    rotated = service.mutate("fixture-human", "credentials/a/" + issued["credential_id"] + "/rotate", "fixture-rotate", body)
    with pytest.raises(Problem, match="unauthorized"):
        store.auth(raw)
    service.mutate("fixture-human", "credentials/a/" + rotated["credential_id"] + "/revoke", "fixture-revoke", {})
    with pytest.raises(Problem, match="unauthorized"):
        store.auth(rotated["token"])
    for illegal in [{"scopes": ["room:control"], "expires_in": 30}, {"scopes": scopes, "expires_in": 86400}, {"scopes": scopes, "expires_in": 0}]:
        with pytest.raises(Problem):
            service.mutate("fixture-human", "credentials/a", json.dumps(illegal), illegal)


def test_room_scoped_credential_does_not_create_or_read_other_room(fixture):
    store, core, rid = fixture
    other = core.create("fixture-a", {"topic": "Other neutral fixture"}, "other")
    issued = IdentityService(store).mutate("fixture-human", "credentials/a", "room-bound", {"scopes": sorted(AGENT_PERMISSIONS | SELF_PERMISSIONS), "expires_in": 30, "room_id": rid})
    assert core.read(issued["token"], rid)["id"] == rid
    with pytest.raises(Problem):
        core.read(issued["token"], other["id"])
    with pytest.raises(Problem):
        core.create(issued["token"], {"topic": "Forbidden"}, "create")
    store.credentials[token_hash(issued["token"])]["expires_at"] = time.time() - 1
    with pytest.raises(Problem, match="unauthorized"):
        core.me(issued["token"])


def test_membership_permission_change_while_paused_and_unblock(fixture):
    store, core, rid = fixture
    task = leased(core, rid)
    control(core, rid, "pause")
    old = core.read("fixture-human", rid)["members"]["a"]
    blocked = core.mutation("fixture-human", rid, "memberships/a", "block", {"expected_revision": old["revision"], "permissions": sorted(AGENT_PERMISSIONS), "status": "blocked"})
    restored = core.mutation("fixture-human", rid, "memberships/a", "unblock", {"expected_revision": blocked["revision"], "permissions": sorted(AGENT_PERMISSIONS), "status": "active"})
    assert restored["revision"] > blocked["revision"] and restored["epoch"] > old["epoch"]
    control(core, rid, "resume")
    with pytest.raises(Problem, match="stale_attempt"):
        core.mutation("fixture-a", rid, "tasks/" + task["id"] + "/complete", "old", result(task))


def test_invitation_preview_and_accept_actor(fixture):
    store, core, rid = fixture
    invitation = core.mutation("fixture-human", rid, "memberships/a", "reinvite", {"expected_revision": 2, "permissions": sorted(AGENT_PERMISSIONS), "status": "pending"})
    client = LocalClient(store)
    preview = client.request("GET", "agents/me/invitations")
    assert preview[0]["membership_id"] == "a" and "messages" not in preview[0]
    with pytest.raises(httpx.HTTPStatusError):
        client.request("POST", f"rooms/{rid}/memberships/b/accept", {"expected_revision": invitation["revision"], "acknowledge_private": True}, "spoof-accept")
    accepted = client.request("POST", f"rooms/{rid}/memberships/a/accept", {"expected_revision": invitation["revision"], "acknowledge_private": True}, "own-accept")
    assert accepted["status"] == "active"
