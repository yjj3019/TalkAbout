"""Work deliverables: a room can ask for analysis or a change proposal.

The platform still executes nothing. These tests pin the two server-side pieces that
make reviewable code work possible without opening an execution channel: the room's
deliverable, and a patch artifact that a human reviews and adopts.
"""
import pytest

from conftest import result
from talkabout.agent_runtime import prompt_text
from talkabout.domain import Problem, deliverable_artifact_kind, patch_like

PROSE = 'creator synthetic text'
DIFF = ('--- a/talkabout/example.py\n'
        '+++ b/talkabout/example.py\n'
        '@@ -1 +1 @@\n'
        '-old = 1\n'
        '+old = 2\n')


def accept_body(room):
    return {"expected_room_revision": room["room_revision"],
            "expected_handover_revision": room["handover"]["revision"],
            "acknowledge_history": True}


def complete_next(core, rid, text):
    room = core.read("fixture-a", rid)
    task = next(t for t in room["tasks"] if t["state"] == "queued")
    claimed = core.mutation("fixture-a", rid, f"tasks/{task['id']}/claim", "claim-" + task["id"],
                            {"generation": task["generation"], "membership_revision": task["membership_revision"]})
    return task, core.mutation("fixture-a", rid, f"tasks/{task['id']}/complete", "complete-" + task["id"],
                               result(claimed, text))


def creation_room(core, deliverable):
    body = {"topic": "Synthetic work room", "mode": "co_creation"}
    if deliverable is not None:
        body["deliverable"] = deliverable
    room = core.create("fixture-a", body, "work-" + str(deliverable))
    core.accept_handover("fixture-human", room["id"], "accept", accept_body(room))
    return room


@pytest.mark.parametrize('deliverable', [None, 'analysis', 'change_proposal'])
def test_room_deliverable_is_stored_and_exposed(fixture, deliverable):
    _, core, _ = fixture
    body = {"topic": "Synthetic deliverable"}
    if deliverable is not None:
        body["deliverable"] = deliverable
    room = core.create("fixture-human", body, "deliverable-" + str(deliverable))
    assert core.read("fixture-human", room["id"])["deliverable"] == deliverable


def test_unknown_deliverable_is_refused(fixture):
    _, core, _ = fixture
    with pytest.raises(Problem, match='^invalid_deliverable$'):
        core.create("fixture-human", {"topic": "Synthetic", "deliverable": "deploy_to_prod"}, "bad-deliverable")


def test_patch_shape_is_checked_without_applying_anything():
    assert patch_like(DIFF) is True
    assert patch_like(PROSE) is False
    assert patch_like(None) is False


def test_change_proposal_room_refuses_a_proposal_that_is_not_a_diff(fixture):
    _, core, _ = fixture
    room = creation_room(core, 'change_proposal')
    complete_next(core, room["id"], 'researcher synthetic text')
    with pytest.raises(Problem, match='^invalid_patch$'):
        complete_next(core, room["id"], PROSE)


def test_change_proposal_room_records_a_patch_artifact(fixture):
    _, core, _ = fixture
    room = creation_room(core, 'change_proposal')
    complete_next(core, room["id"], 'researcher synthetic text')
    _, response = complete_next(core, room["id"], DIFF)
    version = response["artifact"]["versions"][0]
    assert version["kind"] == 'patch'
    assert version["text"] == DIFF


@pytest.mark.parametrize('deliverable, expected_kind', [(None, 'text'), ('analysis', 'text')])
def test_other_deliverables_keep_text_artifacts(fixture, deliverable, expected_kind):
    _, core, _ = fixture
    room = creation_room(core, deliverable)
    complete_next(core, room["id"], 'researcher synthetic text')
    _, response = complete_next(core, room["id"], PROSE)
    assert response["artifact"]["versions"][0]["kind"] == expected_kind


def test_deliverable_kind_helper_is_driven_by_the_room():
    assert deliverable_artifact_kind({'deliverable': None}, PROSE) == 'text'
    assert deliverable_artifact_kind({'deliverable': 'analysis'}, PROSE) == 'text'
    assert deliverable_artifact_kind({'deliverable': 'change_proposal'}, DIFF) == 'patch'
    with pytest.raises(Problem, match='^invalid_patch$'):
        deliverable_artifact_kind({'deliverable': 'change_proposal'}, PROSE)


@pytest.mark.parametrize('deliverable, marker', [
    ('analysis', 'Deliverable: an analysis'),
    ('change_proposal', 'Deliverable: a change proposal as one unified diff'),
])
def test_prompt_states_the_deliverable(deliverable, marker):
    prompt = prompt_text({'room_id': 'room-one', 'deliverable': deliverable, 'topic': 'synthetic'})
    assert marker in prompt
    assert '"deliverable": "%s"' % deliverable in prompt
    # Neither work deliverable may claim the platform executes anything.
    assert 'execution channel' in prompt or 'applies nothing' in prompt


def test_prompt_without_deliverable_keeps_the_text_contract():
    prompt = prompt_text({'room_id': 'room-one', 'topic': 'synthetic'})
    assert 'Deliverable:' not in prompt
    assert prompt.startswith('Return only a text contribution.')
