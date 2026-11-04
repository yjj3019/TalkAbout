"""Stored turn requests are fenced to the room and membership that authorized them."""
import copy

import pytest

from talkabout.domain import AGENT_PERMISSIONS, EVENT_CAPACITY, Problem, schedule
from conftest import control
from test_work_plan_dispatch import prepare, dispatch, node
from talkabout.modes import next_turn


def context():
    return {'mode': 'free_discussion', 'generation': 3, 'next_agent': 0,
            'members': {'a': {'status': 'active', 'epoch': 4, 'revision': 7},
                        'b': {'status': 'active', 'epoch': 2, 'revision': 5}},
            'turn_requests': []}


def request(pid='a', generation=3, epoch=4, revision=7):
    return {'principal_id': pid, 'generation': generation,
            'membership_epoch': epoch, 'membership_revision': revision}


@pytest.mark.parametrize('entry', [
    'a', request(generation=2), request(epoch=3), request(revision=6),
    {'principal_id': 'a'}, {'principal_id': 'a', 'generation': True,
                             'membership_epoch': 4, 'membership_revision': 7},
    {'principal_id': [], 'generation': 3, 'membership_epoch': 4, 'membership_revision': 7},
])
def test_stale_legacy_and_malformed_requests_do_not_authorize(entry):
    room = context()
    room['turn_requests'] = [entry]
    before = copy.deepcopy(room['turn_requests'])
    assert next_turn(room, ['a', 'b']) is None
    assert room['turn_requests'] == before  # no implicit deletion of retained entries


def test_correctly_bound_request_dispatches_and_consumes_only_itself():
    room = context()
    room['turn_requests'] = ['a', request(), request('b', epoch=2, revision=5)]
    assert next_turn(room, ['a', 'b'])[0] == 'a'
    assert room['turn_requests'] == ['a', request('b', epoch=2, revision=5)]


def test_round_robin_skips_stale_entry_without_consuming_it():
    room = context()
    room['turn_requests'] = [request(generation=2), request('b', epoch=2, revision=5)]
    assert next_turn(room, ['a', 'b'])[0] == 'b'
    assert room['turn_requests'] == [request(generation=2)]


def test_inactive_or_ineligible_member_cannot_use_request():
    room = context()
    room['turn_requests'] = [request()]
    room['members']['a']['status'] = 'removed'
    assert next_turn(room, ['a', 'b']) is None
    room['members']['a']['status'] = 'active'
    assert next_turn(room, ['b']) is None


def test_room_generation_change_cannot_reauthorize_old_request():
    room = context()
    room['turn_requests'] = [request()]
    room['generation'] += 1
    assert next_turn(room, ['a', 'b']) is None


def test_current_request_creates_task_with_same_fence(fixture):
    store, core, _ = fixture
    rid = core.create('fixture-a', {'topic': 'Synthetic', 'mode': 'free_discussion'}, 'current-turn')['id']
    room = store.rooms[rid]
    member = room['members']['a']
    body = {'generation': room['generation'], 'membership_revision': member['revision']}
    assert core.mutation('fixture-a', rid, 'turn-requests', 'current-request', body) == {'accepted': True}
    saved = store.rooms[rid]
    tasks = list(saved['tasks'].values())
    assert len(tasks) == 1
    assert tasks[0]['assignee'] == 'a' and tasks[0]['purpose'] == 'requested_turn'
    assert (tasks[0]['generation'], tasks[0]['membership_revision']) == (body['generation'], body['membership_revision'])
    assert saved['turn_requests'] == []


def test_enqueue_replay_and_stale_generation_end_to_end(fixture):
    store, core, _ = fixture
    rid = core.create('fixture-a', {'topic': 'Synthetic', 'mode': 'free_discussion'}, 'fenced-room')['id']
    room = store.rooms[rid]
    room['dispatch_held'] = True
    member = room['members']['a']
    body = {'generation': room['generation'], 'membership_revision': member['revision']}
    receipt = core.mutation('fixture-a', rid, 'turn-requests', 'request-one', body)
    assert receipt == {'accepted': True}
    room = store.rooms[rid]
    assert room['turn_requests'] == [request('a', body['generation'], member['epoch'], member['revision'])]
    assert core.mutation('fixture-a', rid, 'turn-requests', 'request-one', body) == receipt
    assert len(store.rooms[rid]['turn_requests']) == 1
    with pytest.raises(Problem) as error:
        core.mutation('fixture-a', rid, 'turn-requests', 'request-two', body)
    assert error.value.code == 'turn_already_pending'
    room = store.rooms[rid]
    room['generation'] += 1
    room['dispatch_held'] = False
    schedule(room, store.principals)
    assert not room['tasks']
    assert room['turn_requests'] == []  # stale pending intent explicitly cancelled
    assert any(e['type'] == 'turn.requests_cancelled' for e in room['events'])


@pytest.mark.parametrize('changed', ['revision', 'epoch'])
def test_member_revision_or_epoch_change_fences_queued_request(fixture, changed):
    store, core, _ = fixture
    rid = core.create('fixture-a', {'topic': 'Synthetic', 'mode': 'free_discussion'}, 'fenced-member')['id']
    room = store.rooms[rid]
    room['dispatch_held'] = True
    m = room['members']['a']
    body = {'generation': room['generation'], 'membership_revision': m['revision']}
    core.mutation('fixture-a', rid, 'turn-requests', 'member-request', body)
    room = store.rooms[rid]
    room['members']['a'][changed] += 1
    room['dispatch_held'] = False
    schedule(room, store.principals)
    assert not room['tasks']
    assert room['turn_requests'] == []
    assert any(e['type'] == 'turn.requests_cancelled' for e in room['events'])


def make_pending(core, store, rid, key='pending'):
    view = core.read('fixture-a', rid)
    body = {'generation': view['generation'], 'membership_revision': view['membership_revision']}
    assert core.mutation('fixture-a', rid, 'turn-requests', key, body) == {'accepted': True}
    return body


def test_real_hold_release_and_pause_resume(fixture):
    store, core, rid = fixture
    store.rooms[rid]['tasks'] = {}
    store.rooms[rid]['mode'] = 'free_discussion'
    control(core, rid, 'hold_dispatch')
    first = make_pending(core, store, rid)
    assert len(store.rooms[rid]['turn_requests']) == 1
    control(core, rid, 'release_dispatch')
    assert any(t['purpose'] == 'requested_turn' for t in store.rooms[rid]['tasks'].values())
    store.rooms[rid]['tasks'] = {}
    control(core, rid, 'hold_dispatch')
    old = make_pending(core, store, rid, 'before-pause')
    control(core, rid, 'pause')
    assert store.rooms[rid]['turn_requests'] == []
    control(core, rid, 'resume')
    control(core, rid, 'release_dispatch')
    assert not store.rooms[rid]['tasks']
    assert core.mutation('fixture-a', rid, 'turn-requests', 'before-pause', old) == {'accepted': True}
    assert not store.rooms[rid]['tasks']  # replay is historical, not a new intent
    assert first == old


def test_real_membership_restriction_cancels_pending_while_held(fixture):
    store, core, rid = fixture
    store.rooms[rid]['tasks'] = {}
    store.rooms[rid]['mode'] = 'free_discussion'
    control(core, rid, 'hold_dispatch')
    make_pending(core, store, rid)
    member = core.read('fixture-human', rid)['members']['a']
    core.mutation('fixture-human', rid, 'memberships/a', 'restrict-turn',
                  {'expected_revision': member['revision'], 'status': 'active',
                   'permissions': sorted(AGENT_PERMISSIONS - {'task:claim'})})
    assert store.rooms[rid]['turn_requests'] == []
    assert not store.rooms[rid]['tasks']
    control(core, rid, 'release_dispatch')
    assert not store.rooms[rid]['tasks']


def test_stale_and_legacy_canceled_even_busy_and_at_event_capacity(fixture):
    store, core, rid = fixture
    room = store.rooms[rid]
    room['mode'] = 'free_discussion'
    room['turn_requests'] = ['a', request('a', generation=room['generation'] + 1)]
    room['events'] = [{'event_id': str(i), 'seq': i + 1, 'type': 'synthetic'} for i in range(EVENT_CAPACITY)]
    room['last_seq'] = EVENT_CAPACITY
    tasks = copy.deepcopy(room['tasks'])
    assert schedule(room, store.principals)
    assert room['turn_requests'] == [] and room['tasks'] == tasks
    assert len(room['events']) == EVENT_CAPACITY


def test_stale_queue_does_not_block_new_request_or_plan(fixture):
    store, core, rid = fixture
    room = store.rooms[rid]
    room['tasks'] = {}
    room['mode'] = 'free_discussion'
    room['dispatch_held'] = True
    room['turn_requests'] = ['legacy'] * 16
    make_pending(core, store, rid)
    room = store.rooms[rid]
    assert len(room['turn_requests']) == 1 and room['turn_requests'][0]['principal_id'] == 'a'
    assert any(e['type'] == 'turn.requests_cancelled' for e in room['events'])

    # Admission prunes stale pending entries in the same plan transaction.
    prepare(store, core, rid, [node()], 'fixture-a')
    room = store.rooms[rid]
    room['dispatch_held'] = False
    room['turn_requests'] = ['legacy']
    assert dispatch(core, rid)['run']['state'] == 'running'
    assert store.rooms[rid]['turn_requests'] == []


@pytest.mark.parametrize('field', ['generation', 'membership_revision'])
def test_boolean_fence_is_rejected(fixture, field):
    store, core, _ = fixture
    rid = core.create('fixture-a', {'topic': 'Synthetic', 'mode': 'free_discussion'}, 'bool-fence')['id']
    room = store.rooms[rid]
    body = {'generation': room['generation'], 'membership_revision': room['members']['a']['revision']}
    body[field] = True
    with pytest.raises(Problem, match='stale_context'):
        core.mutation('fixture-a', rid, 'turn-requests', 'bool-fence', body)
    assert not store.rooms[rid]['tasks']


def test_full_task_capacity_defers_pending_turn(fixture):
    store, core, _ = fixture
    rid = core.create('fixture-a', {'topic': 'Synthetic', 'mode': 'free_discussion'}, 'full-room')['id']
    room = store.rooms[rid]
    member = room['members']['a']
    pending = request('a', room['generation'], member['epoch'], member['revision'])
    room['turn_requests'] = [pending]
    room['tasks'] = {str(i): {'id': str(i), 'state': 'completed'} for i in range(256)}
    before_requests, before_tasks, before_slot = copy.deepcopy(room['turn_requests']), copy.deepcopy(room['tasks']), room['next_slot']
    other = core.create('fixture-a', {'topic': 'Healthy', 'mode': 'free_discussion'}, 'healthy-room')['id']
    healthy = store.rooms[other]
    healthy_member = healthy['members']['a']
    healthy['turn_requests'] = [request('a', healthy['generation'], healthy_member['epoch'], healthy_member['revision'])]
    store.tick_all()
    assert room['turn_requests'] == before_requests and room['tasks'] == before_tasks
    assert room['next_slot'] == before_slot
    assert any(task['state'] == 'queued' for task in store.rooms[other]['tasks'].values())


def test_deadline_closure_cancels_pending_in_same_tick(fixture):
    store, core, _ = fixture
    rid = core.create('fixture-a', {'topic': 'Synthetic', 'mode': 'free_discussion'}, 'deadline-room')['id']
    room = store.rooms[rid]
    member = room['members']['a']
    room['turn_requests'] = [request('a', room['generation'], member['epoch'], member['revision'])]
    schedule(room, store.principals, now=room['deadline'] + 1)
    assert room['state'] == 'closed' and room['turn_requests'] == []
    assert not room['tasks']


def test_paused_scheduler_cancels_stale_without_execution(fixture):
    store, core, rid = fixture
    room = store.rooms[rid]
    room['state'] = 'paused'
    room['turn_requests'] = ['legacy']
    tasks = copy.deepcopy(room['tasks'])
    assert schedule(room, store.principals)
    assert room['turn_requests'] == [] and room['tasks'] == tasks
