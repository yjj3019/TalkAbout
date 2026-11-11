"""클래스 룸(단톡방) 시나리오 계약 — 사람 조율로 공동 창작 문서 산출물을 채택까지.

The user story the platform realizes: an agent-founded class-room is handed to the
human owner, the human moderates the room (redirects the deliverable to a
document), agents discuss and create a document artifact, the human reviews via the
coordination projection and adopts; the room records the produced document and
closes. Synthetic fixtures only (no provider/browser acceptance).
"""
from conftest import LocalClient
from test_modes_handover import accept_body


def _run_phases(core, rid):
    roles = []
    for _ in range(3):
        task = next(t for t in core.read("fixture-a", rid)["tasks"] if t["state"] == "queued")
        claimed = core.mutation("fixture-a", rid, f"tasks/{task['id']}/claim", "claim-" + task["id"],
                               {"generation": task["generation"], "membership_revision": task["membership_revision"]})
        text = "문서 초안 초안모음" if task["purpose"] == "creator" else f"{task['purpose']} 토론 발언"
        core.mutation("fixture-a", rid, f"tasks/{task['id']}/complete", "complete-" + task["id"],
                     {"attempt": claimed["attempt"], "generation": claimed["generation"],
                      "membership_revision": claimed["membership_revision"],
                      "result": {"client_message_id": "msg-" + task["id"], "text": text, "reply_to": None, "mentions": []}})
        roles.append(task["purpose"])
    return roles


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
