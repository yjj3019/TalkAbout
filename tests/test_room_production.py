"""Human coordination projection: closed decision vocabulary, read-only.

TalkAbout-owned contract (idea: the person moderating an agent class-room needs a
dashboard of what is producing and what only they can decide). Pure projection,
no mutation, no model call; see docs/CLEAN_ROOM_IMPLEMENTATION.md.
"""
import copy

import pytest

from conftest import LocalClient
from talkabout.domain import Problem
from talkabout.production import DECISION_TITLES, HUMAN_DECISION_SET, room_production


def synthetic_room(**overrides):
    room = {'id': 'room-one', 'state': 'active', 'mode': 'bounded_rounds', 'mode_status': None,
            'deliverable': None, 'last_seq': 7, 'controls': {}, 'artifacts': {}, 'dispatch_held': False}
    room.update(overrides)
    return room


def synthetic_artifact(aid='art-1', **overrides):
    artifact = {'id': aid, 'revision': 1, 'versions': [{'id': 'v1', 'seq': 6, 'ai_generated': True}], 'adopted': None}
    artifact.update(overrides)
    return artifact


def test_decision_vocabulary_is_closed_and_stable():
    assert len(HUMAN_DECISION_SET) == 4 and len(DECISION_TITLES) == 4
    assert sorted(DECISION_TITLES) == sorted(HUMAN_DECISION_SET)


def test_idle_active_room_needs_no_human_decision():
    projection = room_production(synthetic_room(), membership_revision=1)
    assert projection['open_human_decisions'] == []
    assert projection['artifacts'] == []
    assert projection['room_state'] == 'active'


def test_pending_controls_resume_and_dispatch_are_reported():
    room = synthetic_room(state='paused', dispatch_held=True,
                          controls={'c1': {'id': 'c1', 'state': 'pending', 'action': 'start'}})
    decisions = room_production(room, membership_revision=2)['open_human_decisions']
    assert decisions == ['approve_pending_controls', 'release_dispatch', 'resume_or_close']


def test_co_creation_draft_awaits_adoption_only_when_pointing_at_a_real_artifact():
    room = synthetic_room(mode='co_creation', mode_status='awaiting_adoption',
                          creation_artifact_id='art-1', artifacts={'art-1': synthetic_artifact()})
    decisions = room_production(room, membership_revision=1)['open_human_decisions']
    assert decisions == ['release_creation_artifact']
    dangling = synthetic_room(mode='co_creation', mode_status='awaiting_adoption', creation_artifact_id='none')
    assert room_production(dangling, membership_revision=1)['open_human_decisions'] == []


def test_artifact_summary_is_counts_and_ids_not_content():
    room = synthetic_room(artifacts={
        'art-1': synthetic_artifact(adopted='v1', versions=[{'id': 'v1', 'seq': 6, 'ai_generated': True}]),
        'art-2': synthetic_artifact('art-2', revision=0, versions=[]),
    })
    projection = room_production(room, membership_revision=1)
    assert projection['artifacts'] == [
        {'artifact_id': 'art-1', 'revision': 1, 'version_count': 1, 'latest_ai_generated': True, 'adopted_version': 'v1'},
        {'artifact_id': 'art-2', 'revision': 0, 'version_count': 0, 'latest_ai_generated': None, 'adopted_version': None},
    ]
    assert 'text' not in str(projection['artifacts'])


def test_projection_is_detached_and_does_not_mutate_inputs():
    room = synthetic_room(controls={'c1': {'id': 'c1', 'state': 'pending', 'action': 'start'}})
    before = copy.deepcopy(room)
    room_production(room, membership_revision=5)
    assert room == before


def test_room_production_route_is_human_read_only_and_detached(fixture):
    store, core, rid = fixture
    room = store.rooms[rid]
    control = core.mutation('fixture-human', rid, 'controls', 'hold-1',
                           {'action': 'hold_dispatch', 'parameters': {}, 'expected_revision': room['room_revision']})
    room = store.rooms[rid]
    client = LocalClient(store, 'fixture-human')
    assert client.request('GET', f'rooms/{rid}/production')['open_human_decisions'] == ['approve_pending_controls']
    core.mutation('fixture-human', rid, f'controls/{control["id"]}/approve', 'hold-1-approve', {})
    room = store.rooms[rid]
    before = copy.deepcopy(room)
    projection = client.request('GET', f'rooms/{rid}/production')
    assert store.rooms[rid] == before
    assert projection['room_id'] == rid and projection['open_human_decisions'] == ['release_dispatch']
    assert projection['membership_revision'] == store.rooms[rid]['members']['human']['revision']
    with pytest.raises(Problem):
        core.room_production('fixture-a', rid)
