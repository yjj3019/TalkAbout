"""조율 control 'change_deliverable': 사람이 산출물(문서/코드) 종류를 방향 전환으로 변경.

TalkAbout-owned contract: the person moderating a class-room may redirect what the
room produces (deliverable) through the same human-only control/approve flow; it
never applies a patch and never calls a model (docs/CLEAN_ROOM_IMPLEMENTATION.md).
"""
import pytest

from conftest import LocalClient
from talkabout.domain import Problem


def test_change_deliverable_is_recognized_and_applied_via_human_approval(fixture):
    store, core, rid = fixture
    room = store.rooms[rid]
    control = core.mutation('fixture-human', rid, 'controls', 'cd-1',
                           {'action': 'change_deliverable', 'parameters': {'deliverable': 'analysis'},
                            'expected_revision': room['room_revision']})
    room = store.rooms[rid]
    assert room['controls'][control['id']]['state'] == 'pending'
    applied = core.mutation('fixture-human', rid, f'controls/{control["id"]}/approve', 'cd-1-approve', {})
    room = store.rooms[rid]
    assert room['deliverable'] == 'analysis'
    assert applied['state'] == 'applied'
    assert room['generation'] > control['expected_revision']


def test_change_deliverable_refuses_unknown_kinds_and_resets_creation_target(fixture):
    store, core, rid = fixture
    room = store.rooms[rid]
    room['mode'] = 'co_creation'
    room['deliverable'] = None
    room['creation_artifact_id'] = 'art-1'
    room['mode_status'] = 'awaiting_adoption'
    room['artifacts']['art-1'] = {'id': 'art-1', 'revision': 1, 'versions': [], 'adopted': None}
    store.rooms[rid] = room
    with pytest.raises(Problem):
        core.mutation('fixture-human', rid, 'controls', 'cd-bad',
                     {'action': 'change_deliverable', 'parameters': {'deliverable': 'secret_plan'},
                      'expected_revision': room['room_revision']})
    control = core.mutation('fixture-human', rid, 'controls', 'cd-2',
                           {'action': 'change_deliverable', 'parameters': {'deliverable': 'change_proposal'},
                            'expected_revision': room['room_revision']})
    core.mutation('fixture-human', rid, f'controls/{control["id"]}/approve', 'cd-2-approve', {})
    room = store.rooms[rid]
    assert room['deliverable'] == 'change_proposal'
    assert 'creation_artifact_id' not in room


def test_change_deliverable_is_human_only(fixture):
    store, core, rid = fixture
    with pytest.raises(Problem):
        core.mutation('fixture-a', rid, 'controls', 'cd-a',
                     {'action': 'change_deliverable', 'parameters': {'deliverable': 'analysis'},
                      'expected_revision': store.rooms[rid]['room_revision']})


def test_change_deliverable_route_flows_through_the_public_api(fixture):
    store, core, rid = fixture
    client = LocalClient(store, 'fixture-human')
    room = store.rooms[rid]
    control = client.request('POST', f'rooms/{rid}/controls', body={'action': 'change_deliverable', 'parameters': {'deliverable': 'analysis'},
                             'expected_revision': room['room_revision']}, key='cd-route')
    applied = client.request('POST', f'rooms/{rid}/controls/{control["id"]}/approve', body={}, key='cd-route-approve')
    assert applied['state'] == 'applied' and applied['action'] == 'change_deliverable'
    assert store.rooms[rid]['deliverable'] == 'analysis'
