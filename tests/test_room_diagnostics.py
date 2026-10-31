"""Read-only waiting-reason projection: closed vocabulary, no mutation, no model call.

These tests pin the TalkAbout-owned implementation. The vocabulary and rules are
not copied from another project; see docs/CLEAN_ROOM_IMPLEMENTATION.md.
"""
import copy
import time

import pytest

from conftest import LocalClient
from talkabout.domain import Problem
from talkabout.observation import REASON_SET, WAITING_REASONS, room_diagnostics, waiting_reasons


def synthetic_room(**overrides):
    room = {'id': 'room-one', 'state': 'active', 'dispatch_held': False, 'last_seq': 7, 'tasks': {}}
    room.update(overrides)
    return room


def synthetic_task(**overrides):
    task = {'id': 'task-one', 'state': 'queued', 'assignee': 'agent-a', 'attempt': 0,
            'deadline': 10_000.0, 'lease_expires_at': None, '_reservations': []}
    task.update(overrides)
    return task


def test_vocabulary_is_closed_and_stable():
    assert set(WAITING_REASONS) == REASON_SET and len(WAITING_REASONS) == len(REASON_SET)
    assert 'awaiting_human_resolution' in REASON_SET and 'agent_busy' not in REASON_SET


def test_queued_task_reports_claim_wait_without_other_noise():
    assert waiting_reasons(synthetic_room(), synthetic_task(), now=100) == ['awaiting_worker_claim']


def test_room_level_reasons_precede_task_reasons():
    assert waiting_reasons(synthetic_room(state='preparing', dispatch_held=True), synthetic_task(), now=100) == [
        'room_not_active', 'dispatch_held', 'awaiting_worker_claim']


def test_leased_task_with_live_lease_and_expired_lease():
    room, now = synthetic_room(), 100
    live = synthetic_task(state='leased', lease_expires_at=200)
    expired = synthetic_task(state='leased', lease_expires_at=50)
    assert waiting_reasons(room, live, now=now) == ['worker_leased']
    assert waiting_reasons(room, expired, now=now) == ['lease_expired']


def test_reservation_awaiting_start_grant_is_visible_only_for_current_attempt():
    room = synthetic_room()
    reserved = synthetic_task(_reservations=[{'id': 'r1', 'attempt': 0, 'state': 'reserved'}])
    started = synthetic_task(_reservations=[{'id': 'r1', 'attempt': 0, 'state': 'started'}])
    other_attempt = synthetic_task(_reservations=[{'id': 'r1', 'attempt': 9, 'state': 'reserved'}])
    assert waiting_reasons(room, reserved, now=100) == ['awaiting_worker_claim', 'awaiting_start_grant']
    assert waiting_reasons(room, started, now=100) == ['awaiting_worker_claim']
    assert waiting_reasons(room, other_attempt, now=100) == ['awaiting_worker_claim']


def test_passed_deadline_is_reported_but_does_not_change_state():
    task = synthetic_task(deadline=50)
    reasons = waiting_reasons(synthetic_room(), task, now=100)
    assert reasons == ['awaiting_worker_claim', 'deadline_passed']
    assert task['state'] == 'queued'


def test_uncertain_is_human_resolution_not_retry():
    task = synthetic_task(state='uncertain', lease_expires_at=0)
    room = synthetic_room(dispatch_held=True)
    assert waiting_reasons(room, task, now=100) == ['awaiting_human_resolution']
    assert set(waiting_reasons(room, task, now=100)) <= REASON_SET


@pytest.mark.parametrize('state', ['completed', 'failed', 'cancelled', 'skipped'])
def test_terminal_states_have_no_waiting_reasons(state):
    assert waiting_reasons(synthetic_room(), synthetic_task(state=state), now=100) == []


def test_unknown_state_is_not_guessed():
    assert waiting_reasons(synthetic_room(), synthetic_task(state='mystery'), now=100) == []


def test_projection_is_detached_and_does_not_mutate_inputs():
    room = synthetic_room(tasks={
        'task-b': synthetic_task(id='task-b', deadline=50),
        'task-a': synthetic_task(id='task-a', state='leased', lease_expires_at=200),
    })
    before = copy.deepcopy(room)
    projection = room_diagnostics(room, membership_revision=3, now=100)
    assert room == before
    assert projection['room_id'] == 'room-one' and projection['last_seq'] == 7
    assert projection['membership_revision'] == 3 and projection['dispatch_held'] is False
    assert [task['task_id'] for task in projection['tasks']] == ['task-a', 'task-b']
    assert any(task['waiting_reasons'] == ['worker_leased'] for task in projection['tasks'])


def test_room_diagnostics_requires_authorized_human_and_stays_read_only(fixture):
    store, core, rid = fixture
    room = store.rooms[rid]
    task_id = 'task-fixture'
    room['tasks'][task_id] = {
        'id': task_id, 'room_id': rid, 'assignee': 'a', 'state': 'queued', 'attempt': 0,
        'generation': room['generation'],
        'membership_revision': room['members']['a']['revision'],
        'deadline': time.time() + 3600,
    }
    before = copy.deepcopy(room)
    projection = core.room_diagnostics('fixture-human', rid)
    assert store.rooms[rid] == before
    assert projection['room_state'] == 'active'
    injected = next(task for task in projection['tasks'] if task['task_id'] == task_id)
    assert injected == {
        'task_id': task_id, 'state': 'queued', 'assignee': 'a', 'attempt': 0,
        'waiting_reasons': ['awaiting_worker_claim'],
    }
    with pytest.raises(Problem):
        core.room_diagnostics('fixture-a', rid)


def test_room_diagnostics_route_returns_the_same_read_only_projection(fixture):
    store, core, rid = fixture
    client = LocalClient(store, 'fixture-human')
    projection = client.request('GET', f'rooms/{rid}/diagnostics')
    assert projection['room_id'] == rid and isinstance(projection['tasks'], list)
    assert all(set(task['waiting_reasons']) <= REASON_SET for task in projection['tasks'])
    assert projection['last_seq'] == core.read('fixture-human', rid)['last_seq']

