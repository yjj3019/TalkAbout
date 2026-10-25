"""Scheduler bounds: the model-call budget counts calls, and silence is reported.

These are the server-side counterparts of two defects a review found: a claim that
never reached a runtime still spent the attempt budget, and a room whose agents were
all unavailable stayed active forever with nothing to dispatch.
"""
import uuid

import pytest

from talkabout.domain import AGENT_PERMISSIONS, Problem, record_task_event, schedule

PRINCIPALS = {'agent-b': {'kind': 'agent'}}


def make_room(mode='bounded_rounds', attempts=3, task_seconds=60, member_status='active'):
    return {
        'id': 'room-one', 'owner': 'owner', 'topic': 'synthetic', 'mode': mode, 'state': 'active',
        'room_revision': 1, 'generation': 1, 'last_seq': 0, 'next_slot': 0, 'next_agent': 0,
        'deadline': 100000, 'started_at': 0, 'duration_seconds': 900,
        'limits': {'messages': 40, 'attempts': attempts, 'lease': 20,
                   'task_seconds': task_seconds, 'rounds': 1},
        'members': {'agent-b': {'status': member_status, 'revision': 1, 'epoch': 1,
                                'permissions': sorted(AGENT_PERMISSIONS), 'read_start_seq': 1,
                                'greeted': False, 'kind': 'agent'}},
        'events': [], 'tasks': {}, 'messages': {}, 'artifacts': {}, 'controls': {},
        'keys': {}, 'client_ids': {},
    }


def leased_task(attempt, started=None):
    task = {'id': 'task-one', 'room_id': 'room-one', 'slot': 1, 'assignee': 'agent-b',
            'purpose': 'proposal', 'phase': 'proposal', 'round': 1, 'role': 'proposal',
            'state': 'leased', 'attempt': attempt, 'generation': 1, 'membership_revision': 1,
            'deadline': 100000, 'lease_expires_at': 10}
    if started is not None:
        task['_started_attempts'] = started
    return task


def test_paid_call_budget_is_not_spent_by_a_claim_that_never_started():
    room = make_room(attempts=3)
    room['tasks']['task-one'] = leased_task(attempt=3)  # three claims, zero runtime starts
    schedule(room, PRINCIPALS, now=50)
    assert room['tasks']['task-one']['state'] == 'queued'
    assert not [event for event in room['events'] if event['type'] == 'task.expired']


def test_attempts_that_did_start_a_call_exhaust_the_budget():
    room = make_room(attempts=3)
    room['tasks']['task-one'] = leased_task(attempt=3, started=[1, 2, 3])
    schedule(room, PRINCIPALS, now=50)
    assert room['tasks']['task-one']['state'] == 'expired'
    assert 'task.expired' in [event['type'] for event in room['events']]


def test_a_worker_that_never_starts_is_still_bounded():
    room = make_room(attempts=2)  # hard ceiling = attempt 4
    room['tasks']['task-one'] = leased_task(attempt=2 * 2)
    schedule(room, PRINCIPALS, now=50)
    assert room['tasks']['task-one']['state'] == 'expired'


def test_runtime_started_attempts_are_counted_once_per_attempt():
    room = make_room()
    room['tasks']['task-one'] = leased_task(attempt=2)
    record_task_event(room, 'task-one', 'claimed', 1)
    record_task_event(room, 'task-one', 'runtime_started', 2)
    record_task_event(room, 'task-one', 'runtime_started', 3)  # duplicate heartbeat stage
    assert room['tasks']['task-one']['_started_attempts'] == [2]
    assert room['task_events']['task-one'][0]['attempt'] == 2


def test_all_agents_unavailable_is_reported_then_closed():
    room = make_room(task_seconds=60)  # bound = 120s, close at 240s
    room['members']['agent-b']['status'] = 'blocked'
    schedule(room, PRINCIPALS, now=0)
    schedule(room, PRINCIPALS, now=100)
    assert room['state'] == 'active'            # bounded wait, not an instant close
    assert room.get('mode_status') != 'stalled'
    schedule(room, PRINCIPALS, now=130)
    assert room['state'] == 'active'
    assert room['mode_status'] == 'stalled'
    assert [event['type'] for event in room['events']] == ['room.stalled']
    assert room['events'][0]['reason'] == 'no_eligible_members'
    schedule(room, PRINCIPALS, now=250)
    assert room['state'] == 'closed'
    assert room['generation'] == 2
    assert [event['type'] for event in room['events']] == ['room.stalled', 'room.closed']
    assert room['events'][1]['reason'] == 'no_eligible_members'


def test_stall_is_cleared_when_an_agent_becomes_available_again():
    room = make_room(mode='free_discussion', task_seconds=60)
    room['members']['agent-b']['status'] = 'blocked'
    schedule(room, PRINCIPALS, now=0)
    schedule(room, PRINCIPALS, now=130)
    assert room['mode_status'] == 'stalled'
    room['members']['agent-b']['status'] = 'active'
    schedule(room, PRINCIPALS, now=140)
    assert room['state'] == 'active'
    assert room.get('mode_status') == 'running'
    assert 'dispatch_idle' not in room


def test_ack_refuses_a_resync_cursor_that_skipped_history(fixture):
    store, core, rid = fixture
    view = core.read('fixture-human', rid)
    body = {'session_id': str(uuid.uuid4()), 'generation': view['generation'],
            'membership_revision': view['members']['a']['revision']}
    joined = core.participation('fixture-a', rid, 'join', dict(body))['participation']
    delivered = core.events('fixture-a', target_room=rid, session_id=body['session_id'])
    cursor_id = delivered['next_cursor']
    # A resync cursor can jump offsets over events the response never carried.
    payload = store.cursor(cursor_id)
    payload['skipped_rooms'] = [rid]
    store.save_cursor(cursor_id, payload)
    with pytest.raises(Problem) as error:
        core.participation('fixture-a', rid, 'ack', {**body, 'cursor': cursor_id, 'state': 'delivered'})
    assert error.value.code == 'delivery_unproven'
    assert joined['delivered_seq'] == 0


def test_ack_accepts_a_cursor_that_carried_the_events(fixture):
    store, core, rid = fixture
    view = core.read('fixture-human', rid)
    body = {'session_id': str(uuid.uuid4()), 'generation': view['generation'],
            'membership_revision': view['members']['b']['revision']}
    core.participation('fixture-b', rid, 'join', dict(body))
    delivered = core.events('fixture-b', target_room=rid, session_id=body['session_id'])
    receipt = core.participation('fixture-b', rid, 'ack',
                                 {**body, 'cursor': delivered['next_cursor'], 'state': 'read'})
    assert receipt['delivered_seq'] >= 0 and 'read_seq' in receipt
