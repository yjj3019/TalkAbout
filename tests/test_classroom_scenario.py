"""클래스 룸(단톡방) 시나리오 계약 — 사람 조율로 공동 창작 문서 산출물을 채택까지.

The user story the platform realizes: an agent-founded class-room is handed to the
human owner, the human moderates the room (redirects the deliverable to a
document), agents discuss and create a document artifact, the human reviews via the
coordination projection and adopts; the room records the produced document and
closes. Synthetic fixtures only (no provider/browser acceptance).
"""
from conftest import LocalClient
from talkabout.domain import AGENT_PERMISSIONS
from test_modes_handover import accept_body


def _run_phases(core, rid, creator_text="문서 초안 초안모음"):
    roles = []
    for _ in range(3):
        task = next(t for t in core.read("fixture-a", rid)["tasks"] if t["state"] == "queued")
        claimed = core.mutation("fixture-a", rid, f"tasks/{task['id']}/claim", "claim-" + task["id"],
                               {"generation": task["generation"], "membership_revision": task["membership_revision"]})
        text = creator_text if task["purpose"] == "creator" else f"{task['purpose']} 토론 발언"
        core.mutation("fixture-a", rid, f"tasks/{task['id']}/complete", "complete-" + task["id"],
                     {"attempt": claimed["attempt"], "generation": claimed["generation"],
                      "membership_revision": claimed["membership_revision"],
                      "result": {"client_message_id": "msg-" + task["id"], "text": text, "reply_to": None, "mentions": []}})
        roles.append(task["purpose"])
    return roles


def _free_turn(core, rid, token, text, key):
    room = core.read(token, rid)
    newroom = core.read(token, rid)
    core.mutation(token, rid, "turn-requests", key,
                 {"generation": newroom["generation"], "membership_revision": newroom["membership_revision"]})
    task = next(t for t in core.read(token, rid)["tasks"] if t["state"] == "queued")
    claimed = core.mutation(token, rid, f"tasks/{task['id']}/claim", key + "-claim",
                           {"generation": task["generation"], "membership_revision": task["membership_revision"]})
    core.mutation(token, rid, f"tasks/{task['id']}/complete", key + "-complete",
                 {"attempt": claimed["attempt"], "generation": claimed["generation"],
                  "membership_revision": claimed["membership_revision"],
                  "result": {"client_message_id": key + "-msg", "text": text, "reply_to": None, "mentions": []}})


def test_classroom_story_change_proposal_code_lesson(fixture):
    store, core, _ = fixture
    room = core.create("fixture-a", {"topic": "코드 산출물 수업", "mode": "co_creation", "deliverable": "change_proposal"}, "story-code")
    core.accept_handover("fixture-human", room["id"], "accept", accept_body(room))
    rid = room["id"]
    roles = _run_phases(core, rid, creator_text="--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-old\n+new\n")
    assert roles == ["researcher", "creator", "reviewer"]
    client = LocalClient(store, "fixture-human")
    current = client.request("GET", f"rooms/{rid}")
    artifact = current["artifacts"][0]
    assert artifact["versions"][-1]["text"].startswith("--- a/")
    body = {"generation": current["generation"], "membership_revision": current["membership_revision"],
            "expected_revision": artifact["revision"], "version_id": artifact["versions"][-1]["id"]}
    client.request("POST", f"rooms/{rid}/artifacts/{artifact['id']}/adoptions", body=body, key="adopt-code")
    final = client.request("GET", f"rooms/{rid}")
    assert final["mode_status"] == "adopted" and final["state"] == "closed"
    assert final["artifacts"][0]["adopted"] == artifact["versions"][-1]["id"]
    assert final["deliverable"] == "change_proposal"


def test_classroom_story_free_discussion_opinions_then_human_close(fixture):
    store, core, _ = fixture
    room = core.create("fixture-human", {"topic": "수업 주제 정하기", "mode": "free_discussion"}, "story-free")
    rid = room["id"]
    for pid in ("a", "b"):
        member = core.mutation("fixture-human", rid, "memberships", f"invite-{pid}",
                              {"principal_id": pid, "permissions": sorted(AGENT_PERMISSIONS), "status": "pending"})
        core.accept("fixture-" + pid, rid, "accept", {"expected_revision": member["revision"], "acknowledge_private": True})
    _free_turn(core, rid, "fixture-a", "주제 A가 좋아", "ta")
    _free_turn(core, rid, "fixture-b", "주제 B로 결정", "tb")
    core.mutation("fixture-human", rid, "messages", "adv",
                 {"text": "두 의견 정리해서 마무리해줘", "kind": "advice", "client_message_id": "adv"})
    current = core.read("fixture-human", rid)
    texts = [m["text"] for m in current["messages"]]
    assert "주제 A가 좋아" in texts and "주제 B로 결정" in texts and "두 의견 정리해서 마무리해줘" in texts
    control = core.mutation("fixture-human", rid, "controls", "close-free",
                          {"action": "close", "expected_revision": current["room_revision"]})
    core.mutation("fixture-human", rid, f'controls/{control["id"]}/approve', "close-free-approve", {})
    final = core.read("fixture-human", rid)
    assert final["state"] == "closed"
    assert core.room_production("fixture-human", rid)["open_human_decisions"] == []


def test_classroom_story_mcp_code_proposal_submission_and_human_adoption(fixture):
    store, core, _ = fixture
    room = core.create("fixture-a", {"topic": "코드 산출물 제출", "mode": "co_creation", "deliverable": "change_proposal"}, "story-mcp")
    core.accept_handover("fixture-human", room["id"], "accept", accept_body(room))
    rid = room["id"]
    room = store.rooms[rid]
    artifact = core.mutation("fixture-a", rid, "artifacts", "mcp-patch",
                           {"generation": room["generation"], "membership_revision": room["members"]["a"]["revision"],
                            "text": "--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-old\n+new\n", "sources": [], "caption": ""})
    assert artifact["versions"][-1]["text"].startswith("--- a/")
    client = LocalClient(store, "fixture-human")
    current = client.request("GET", f"rooms/{rid}")
    art = next(a for a in current["artifacts"] if a["id"] == artifact["id"])
    assert art["versions"][-1]["ai_generated"] is True
    body = {"generation": current["generation"], "membership_revision": current["membership_revision"],
            "expected_revision": art["revision"], "version_id": art["versions"][-1]["id"]}
    client.request("POST", f"rooms/{rid}/artifacts/{art['id']}/adoptions", body=body, key="adopt-mcp")
    final = client.request("GET", f"rooms/{rid}")
    adopted = next(a for a in final["artifacts"] if a["id"] == art["id"])
    assert adopted["adopted"] == art["versions"][-1]["id"]


def test_classroom_story_human_moderated_co_creation_wall_to_wall(fixture):
    store, core, _ = fixture
    room = core.create("fixture-a", {"topic": "수업 산출물 만들기", "mode": "co_creation"}, "story-room")
    core.accept_handover("fixture-human", room["id"], "accept", accept_body(room))
    rid = room["id"]

    control = core.mutation("fixture-human", rid, "controls", "cd-story",
                          {"action": "change_deliverable", "parameters": {"deliverable": "analysis"},
                           "expected_revision": store.rooms[rid]["room_revision"]})
    core.mutation("fixture-human", rid, f'controls/{control["id"]}/approve', "cd-story-approve", {})
    assert store.rooms[rid]["deliverable"] == "analysis"

    roles = _run_phases(core, rid)
    assert roles == ["researcher", "creator", "reviewer"]

    client = LocalClient(store, "fixture-human")
    assert client.request("GET", f"rooms/{rid}/production")["open_human_decisions"] == ["release_creation_artifact"]
    hits = client.request("GET", f"rooms/{rid}/search", params={"q": "초안"})["results"]
    assert any("초안" in m["text"] for m in hits)
    current = client.request("GET", f"rooms/{rid}")
    artifact = current["artifacts"][0]
    body = {"generation": current["generation"], "membership_revision": current["membership_revision"],
            "expected_revision": artifact["revision"], "version_id": artifact["versions"][-1]["id"]}
    client.request("POST", f"rooms/{rid}/artifacts/{artifact['id']}/adoptions", body=body, key="adopt-story")
    final = client.request("GET", f"rooms/{rid}")
    assert final["mode_status"] == "adopted" and final["state"] == "closed"
    assert final["deliverable"] == "analysis" and final["artifacts"][0]["adopted"] == artifact["versions"][-1]["id"]
