"""Explicit human dispatch of a private work plan; the server never calls a provider."""
import copy
import json
import uuid

import pytest

from talkabout.domain import AGENT_PERMISSIONS, HUMAN_PERMISSIONS, Problem, token_hash
from talkabout.domain import PLAN_DISPATCH_STATE_BYTE_SLACK
from talkabout.identity import IdentityService


def node(name='one', assignee='a', deps=None, instruction='Owner-only secret instruction', **changes):
    return {'id': name, 'title': 'Secret plan title', 'instruction': instruction,
            'assignee': assignee, 'write_scope': 'analysis', 'depends_on': deps or [], **changes}


def write(core, rid, nodes, revision=0, key=None, token='fixture-human'):
    if key is None:
        key = 'plan-' + uuid.uuid4().hex
    return core.replace_work_plan(token, rid, key, {'expected_revision': revision, 'nodes': nodes})


def ready(store, *tokens):
    """Synthetic ready worker connections for the fixture agents."""
    for token in tokens:
        IdentityService(store).mutate(token, 'connection', 'ready-' + token,
                                      {'runtime': 'synthetic', 'state': 'idle'})


def clear_tasks(store, rid):
    """Synthetic setup: remove the fixture's automatic turn so dispatch can start."""
    if hasattr(store, 'rooms'):
        store.rooms[rid]['tasks'] = {}
        store.rooms[rid].pop('turn_requests', None)
        return
    with store.connect() as conn:
        conn.execute("UPDATE rooms SET payload = jsonb_set(payload - 'turn_requests', '{tasks}', '{}'::jsonb) "
                     "WHERE id = %s", (rid,))


def clear_run(store, rid):
    if hasattr(store, 'rooms'):
        store.rooms[rid].pop('_plan_dispatch', None)
        return
    with store.connect() as conn:
        conn.execute("UPDATE rooms SET payload = payload - '_plan_dispatch' WHERE id = %s", (rid,))


def generation(core, rid, token='fixture-human'):
    return core.read(token, rid)['generation']


def dispatch(core, rid, key='dispatch-1', plan_revision=1, room_generation=None, **changes):
    room = core.read('fixture-human', rid)
    body = {'expected_plan_revision': plan_revision,
            'expected_generation': room['generation'] if room_generation is None else room_generation,
            'acknowledge_plan_dispatch': True, 'acknowledge_plan_disclosure': True,
            'acknowledge_write_scope_unenforced': True, **changes}
    return core.dispatch_work_plan('fixture-human', rid, key, body)


def run(core, rid):
    return core.plan_dispatch('fixture-human', rid)['run']


def plan_task(core, rid, token='fixture-a', state='queued'):
    room = core.read(token, rid)
    return min((task for task in room['tasks'] if task['state'] == state),
               key=lambda task: task['slot'], default=None)


def claim(core, rid, key='claim', token='fixture-a', protocol='server_start_v1'):
    task = plan_task(core, rid, token)
    assert task is not None
    body = {'generation': task['generation'], 'membership_revision': task['membership_revision']}
    if protocol:
        body['execution_protocol'] = protocol
    return core.mutation(token, rid, 'tasks/' + task['id'] + '/claim', key + '-' + task['id'], body)


def complete(core, rid, task, key='complete', token='fixture-a', text='Synthetic result'):
    if task.get('execution_protocol') == 'server_start_v1':
        reservation = uuid.uuid4().hex + uuid.uuid4().hex
        body = {k: task[k] for k in ('attempt', 'generation', 'membership_revision')}
        body['reservation_id'] = reservation
        admitted = core.mutation(token, rid, 'tasks/' + task['id'] + '/reservations', key + '-reserve-' + task['id'], body)
        assert admitted.get('reserved') is True
        core.mutation(token, rid, 'tasks/' + task['id'] + '/start', key + '-start-' + task['id'], body)
    return core.mutation(token, rid, 'tasks/' + task['id'] + '/complete', key + '-' + task['id'],
                         {k: task[k] for k in ('attempt', 'generation', 'membership_revision')} |
                         {'result': {'client_message_id': 'result-' + task['id'], 'text': text,
                                     'reply_to': None, 'mentions': []}})


def tick(core, rid):
    core.tick('fixture-human', rid)


def tick_raw(store):
    """The real scheduler path (no per-room transaction bounds) for saturation tests."""
    store.tick_all()


def inject_events(store, rid, count):
    store.rooms[rid]['events'] = [
        {'event_id': 'synthetic-' + str(i), 'room_id': rid, 'seq': i + 1,
         'type': 'synthetic.saturation', 'created_at': 0.0} for i in range(count)]
    store.rooms[rid]['last_seq'] = count


def inject_keys(store, rid, count):
    store.rooms[rid]['keys'] = {'synthetic-key-' + str(i): {} for i in range(count)}


def pad_room_bytes(store, rid, target):
    from talkabout.domain import room_byte_size
    room = store.rooms[rid]
    room['keys']['byte-pad'] = {'pad': ''}
    for _ in range(4):
        delta = target - room_byte_size(room)
        if delta == 0:
            break
        pad = room['keys']['byte-pad']['pad']
        room['keys']['byte-pad']['pad'] = pad + 'x' * delta if delta > 0 else pad[:len(pad) + delta]
    assert room_byte_size(room) == target


def private(store, rid):
    if hasattr(store, 'rooms'):
        return copy.deepcopy(store.rooms[rid])
    with store.read_transaction(rid, 'fixture-human', include_events=True) as (_, room):
        return copy.deepcopy(room)


def inject_dispatch(store, rid, value):
    if hasattr(store, 'rooms'):
        store.rooms[rid]['_plan_dispatch'] = value
        return
    from psycopg.types.json import Jsonb
    with store.connect() as conn:
        conn.execute("UPDATE rooms SET payload = payload || %s WHERE id = %s",
                     (Jsonb({'_plan_dispatch': value}), rid))


def pg_update_payload(store, rid, change):
    from psycopg.types.json import Jsonb
    with store.connect() as conn:
        payload = conn.execute("SELECT payload FROM rooms WHERE id = %s", (rid,)).fetchone()['payload']
        change(payload)
        conn.execute("UPDATE rooms SET payload = %s WHERE id = %s", (Jsonb(payload), rid))


def pg_pad_payload(store, rid, target):
    import json as _json
    size = lambda payload: len(_json.dumps(payload, ensure_ascii=False).encode('utf-8'))

    def change(payload):
        payload.setdefault('keys', {})['pg-byte-pad'] = {'pad': ''}
        for _ in range(4):
            delta = target - size(payload)
            if delta == 0:
                break
            pad = payload['keys']['pg-byte-pad']['pad']
            payload['keys']['pg-byte-pad']['pad'] = pad + 'x' * delta if delta > 0 else pad[:len(pad) + delta]
        assert size(payload) == target

    pg_update_payload(store, rid, change)


def prepare(store, core, rid, nodes, *agents):
    """Clear automatic work, make agents ready, and write the owner plan."""
    clear_tasks(store, rid)
    ready(store, *agents)
    write(core, rid, nodes)
    return core.read('fixture-human', rid)


def test_dispatch_requires_owner_and_exact_contract(fixture):
    store, core, rid = fixture
    nodes = [node()]
    with pytest.raises(Problem, match='plan_missing'):
        dispatch(core, rid, 'none')
    write(core, rid, [])
    with pytest.raises(Problem, match='plan_dispatch_empty'):
        dispatch(core, rid, 'empty')
    write(core, rid, nodes, revision=1)
    for body in ({}, {'expected_plan_revision': True, 'expected_generation': 1},                 {'expected_plan_revision': 1, 'expected_generation': 1,
                  'acknowledge_plan_dispatch': True, 'acknowledge_plan_disclosure': True,
                  'acknowledge_write_scope_unenforced': False},
                 {'expected_plan_revision': 1, 'expected_generation': 1,
                  'acknowledge_plan_dispatch': True, 'acknowledge_plan_disclosure': True,
                  'acknowledge_write_scope_unenforced': True, 'extra': 1}):
        with pytest.raises(Problem, match='invalid_plan_dispatch_fields'):
            core.dispatch_work_plan('fixture-human', rid, 'bad-' + str(len(body)), body)
    with pytest.raises(Problem, match='work_plan_revision_conflict'):
        dispatch(core, rid, 'old-revision', plan_revision=99)
    with pytest.raises(Problem, match='stale_context'):
        dispatch(core, rid, 'old-generation', plan_revision=2, room_generation=99)
    with pytest.raises(Problem) as error:
        core.dispatch_work_plan('fixture-a', rid, 'agent', dispatch_body(core, rid))
    assert error.value.status == 403
    # Non-owner human with control scope is still refused.
    store.provision({'id': 'other', 'owner': 'different-owner', 'kind': 'human',
                     'display_name': 'Other', 'handle': 'other'}, 'fixture-other',
                    {'room:read', 'room:control'})
    store.rooms[rid]['members']['other'] = copy.deepcopy(store.rooms[rid]['members']['human'])
    with pytest.raises(Problem, match='forbidden'):
        core.dispatch_work_plan('fixture-other', rid, 'other', dispatch_body(core, rid))


def dispatch_body(core, rid, plan_revision=1):
    room = core.read('fixture-human', rid)
    return {'expected_plan_revision': plan_revision, 'expected_generation': room['generation'],
            'acknowledge_plan_dispatch': True, 'acknowledge_plan_disclosure': True,
            'acknowledge_write_scope_unenforced': True}


def test_dispatch_held_and_room_busy(fixture):
    store, core, rid = fixture
    ready(store, 'fixture-a')
    write(core, rid, [node()])
    with pytest.raises(Problem, match='room_busy'):
        dispatch(core, rid, 'busy')
    clear_tasks(store, rid)
    room = core.read('fixture-human', rid)
    pending = core.mutation('fixture-human', rid, 'controls', 'hold-request',
                            {'action': 'hold_dispatch', 'expected_revision': room['room_revision']})
    core.mutation('fixture-human', rid, 'controls/' + pending['id'] + '/approve', 'hold-approve', {})
    with pytest.raises(Problem, match='dispatch_held'):
        dispatch(core, rid, 'held')
    assert run(core, rid) is None


def test_previous_epoch_plan_is_not_runnable(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    store.rooms[rid]['members']['human']['epoch'] += 1
    with pytest.raises(Problem, match='plan_missing'):
        dispatch(core, rid, 'stale-epoch')
    assert run(core, rid) is None


def test_run_bindings_fail_closed_after_author_or_epoch_change(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    first = dispatch(core, rid)
    store.rooms[rid]['_work_plan']['_author'] = 'someone-else'
    with pytest.raises(Problem, match='not_found'):
        run(core, rid)
    with pytest.raises(Problem, match='not_found'):
        core.abort_plan_dispatch('fixture-human', rid, 'bound-abort',
                                 {'run_id': first['run']['id'], 'acknowledge_abort': True})
    store.rooms[rid]['_work_plan']['_author'] = 'human'
    store.rooms[rid]['members']['human']['epoch'] += 1
    with pytest.raises(Problem, match='not_found'):
        run(core, rid)


def test_roots_only_and_sequential_dependency(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node('one'), node('two', deps=['one'])], 'fixture-a')
    response = dispatch(core, rid)
    assert response['tasks_queued'] == 1
    assert response['run']['state'] == 'running'
    assert [n['state'] for n in response['run']['nodes']] == ['queued', 'pending']
    task = plan_task(core, rid)
    assert task['purpose'] == 'plan_node'
    # Only the assignee sees the owner-authored node text; humans and peers do not.
    human = core.read('fixture-human', rid)
    assert '"plan_node":' not in json.dumps(human)
    peer = core.read('fixture-b', rid)
    assert '"plan_node":' not in json.dumps(peer)
    assert 'Owner-only secret instruction' not in json.dumps(human)
    claimed = claim(core, rid)
    complete(core, rid, claimed)
    tick(core, rid)
    assert run(core, rid)['nodes'][1]['state'] in {'queued', 'pending'}
    follow = plan_task(core, rid)
    assert follow is not None and follow['plan_node']['node_id'] == 'two'
    events = [event['type'] for event in private(store, rid)['events']]
    started_at = events.index('plan.dispatch_started')
    assert events.index('task.queued', started_at) > started_at


def test_parallel_roots_and_automatic_turn_suppression(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node('ra', 'a'), node('rb', 'b')], 'fixture-a', 'fixture-b')
    response = dispatch(core, rid)
    assert response['tasks_queued'] == 2
    tick(core, rid)
    room = core.read('fixture-human', rid)
    assert all(task['purpose'] == 'plan_node' for task in room['tasks'])


def test_four_slot_cap_counts_leases_and_refills_in_plan_order(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node('root-%d' % i) for i in range(6)], 'fixture-a')
    first = dispatch(core, rid, 'cap-dispatch')
    assert first['tasks_queued'] == 4
    assert [n['state'] for n in first['run']['nodes']] == ['queued'] * 4 + ['pending'] * 2
    assert plan_task(core, rid)['plan_node']['node_id'] == 'root-0'
    claimed = claim(core, rid, 'cap-claim')
    for _ in range(2):
        tick(core, rid)
        tasks = [t for t in private(store, rid)['tasks'].values() if t['purpose'] == 'plan_node']
        assert len(tasks) == 4
        assert sorted(t['state'] for t in tasks) == ['leased', 'queued', 'queued', 'queued']
    complete(core, rid, claimed, 'cap-complete')
    tick(core, rid)
    assert [n['state'] for n in run(core, rid)['nodes']] == ['completed'] + ['queued'] * 4 + ['pending']
    tasks = [t for t in private(store, rid)['tasks'].values() if t['purpose'] == 'plan_node']
    assert len(tasks) == 5
    assert sum(t['state'] in {'queued', 'leased'} for t in tasks) == 4
    assert dispatch(core, rid, 'cap-dispatch') == first
    assert len(private(store, rid)['tasks']) == 5
    complete(core, rid, claim(core, rid, 'cap-claim-next'), 'cap-complete-next')
    tick(core, rid)
    assert [n['state'] for n in run(core, rid)['nodes']] == ['completed'] * 2 + ['queued'] * 4
    assert len(private(store, rid)['tasks']) == 6


def test_legacy_over_cap_run_drains_without_cancelling_or_refilling(fixture, monkeypatch):
    import talkabout.domain as domain

    store, core, rid = fixture
    prepare(store, core, rid, [node('root-%d' % i) for i in range(7)], 'fixture-a')
    # Simulate a persisted run admitted by an older version without a cap.
    monkeypatch.setattr(domain, 'PLAN_DISPATCH_MAX_PARALLEL', 6)
    first = dispatch(core, rid, 'legacy-cap')
    assert first['tasks_queued'] == 6
    monkeypatch.setattr(domain, 'PLAN_DISPATCH_MAX_PARALLEL', 4)
    tick(core, rid)
    assert len(private(store, rid)['tasks']) == 6
    for index in range(2):
        complete(core, rid, claim(core, rid, 'legacy-claim-%d' % index), 'legacy-complete-%d' % index)
        tick(core, rid)
        assert len(private(store, rid)['tasks']) == 6
    complete(core, rid, claim(core, rid, 'legacy-claim-2'), 'legacy-complete-2')
    tick(core, rid)
    assert len(private(store, rid)['tasks']) == 7
    assert sum(n['state'] == 'queued' for n in run(core, rid)['nodes']) == 4


def test_postgres_four_slot_cap_and_refills(postgres_fixture):
    test_four_slot_cap_counts_leases_and_refills_in_plan_order(postgres_fixture)


def test_cap_skips_unready_dependency_and_abort_never_refills(fixture):
    store, core, rid = fixture
    nodes = [node('root-0'), node('dependent', deps=['root-0'])] + [node('root-%d' % i) for i in range(1, 6)]
    prepare(store, core, rid, nodes, 'fixture-a')
    first = dispatch(core, rid, 'cap-abort-dispatch')
    assert [n['state'] for n in first['run']['nodes']] == [
        'queued', 'pending', 'queued', 'queued', 'queued', 'pending', 'pending']
    complete(core, rid, claim(core, rid, 'dependency-claim'), 'dependency-complete')
    tick(core, rid)
    assert [n['state'] for n in run(core, rid)['nodes']] == [
        'completed', 'queued', 'queued', 'queued', 'queued', 'pending', 'pending']
    assert len(private(store, rid)['tasks']) == 5
    aborted = core.abort_plan_dispatch('fixture-human', rid, 'cap-abort',
                                       {'run_id': first['run']['id'], 'acknowledge_abort': True})
    tick(core, rid)
    room = private(store, rid)
    tasks = [t for t in room['tasks'].values() if t['purpose'] == 'plan_node']
    assert len(tasks) == 5
    assert sum(t['state'] in {'queued', 'leased'} for t in tasks) == 0
    assert run(core, rid)['state'] == 'aborted'
    assert core.abort_plan_dispatch('fixture-human', rid, 'cap-abort',
                                    {'run_id': first['run']['id'], 'acknowledge_abort': True}) == aborted


def test_failed_root_releases_slot_but_blocks_its_dependent(fixture):
    store, core, rid = fixture
    nodes = [node('failed', 'a'), node('dependent', 'b', ['failed'])] + [
        node('root-%d' % i, 'b') for i in range(5)]
    prepare(store, core, rid, nodes, 'fixture-a', 'fixture-b')
    first = dispatch(core, rid)
    assert [n['state'] for n in first['run']['nodes']] == [
        'queued', 'pending', 'queued', 'queued', 'queued', 'pending', 'pending']
    member = core.read('fixture-human', rid)['members']['a']
    core.mutation('fixture-human', rid, 'memberships/a', 'cap-remove-a',
                  {'expected_revision': member['revision'], 'status': 'left',
                   'permissions': sorted(AGENT_PERMISSIONS)})
    tick(core, rid)
    states = {n['id']: (n['state'], n['failure']) for n in run(core, rid)['nodes']}
    assert states['failed'] == ('failed', 'cancelled')
    assert states['dependent'] == ('blocked', 'dependency_failed')
    assert [states['root-%d' % i][0] for i in range(5)] == ['queued'] * 4 + ['pending']
    tasks = [t for t in private(store, rid)['tasks'].values() if t['purpose'] == 'plan_node']
    assert len(tasks) == 5
    assert sum(t['state'] in {'queued', 'leased'} for t in tasks) == 4
    tick(core, rid)
    assert len([t for t in private(store, rid)['tasks'].values() if t['purpose'] == 'plan_node']) == 5


def test_failure_blocks_descendants_and_sibling_continues_partial(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node('one', 'a'), node('two', 'b'), node('three', 'b', ['one'])],
            'fixture-a', 'fixture-b')
    dispatch(core, rid)
    task = plan_task(core, rid, 'fixture-a')
    member = core.read('fixture-human', rid)['members']['a']
    core.mutation('fixture-human', rid, 'memberships/a', 'remove-a',
                  {'expected_revision': member['revision'], 'status': 'left',
                   'permissions': sorted(AGENT_PERMISSIONS)})
    tick(core, rid)
    current = run(core, rid)
    states = {node['id']: (node['state'], node['failure']) for node in current['nodes']}
    assert states['one'] == ('failed', 'cancelled')
    assert states['three'][0] == 'blocked'
    assert states['three'][1] == 'dependency_failed'
    assert states['two'][0] == 'queued'
    follow = claim(core, rid, 'claim-b', 'fixture-b')
    complete(core, rid, follow, 'complete-b', 'fixture-b')
    tick(core, rid)
    final = run(core, rid)
    assert final['state'] == 'partial'
    assert {node['id']: node['state'] for node in final['nodes']}['two'] == 'completed'
    assert 'Secret plan title' not in json.dumps(final)
    assert '"instruction"' not in json.dumps(final)


def test_uncertain_task_marks_run_partial_and_blocks_dependents(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node('one'), node('two', deps=['one'])], 'fixture-a')
    dispatch(core, rid)
    claimed = claim(core, rid, protocol='server_start_v1')
    reservation = uuid.uuid4().hex + uuid.uuid4().hex
    body = {'attempt': claimed['attempt'], 'generation': claimed['generation'],
            'membership_revision': claimed['membership_revision'], 'reservation_id': reservation}
    core.mutation('fixture-a', rid, 'tasks/' + claimed['id'] + '/reservations', 'reserve', body)
    core.mutation('fixture-a', rid, 'tasks/' + claimed['id'] + '/start', 'start', body)
    core.mutation('fixture-a', rid,
                  'tasks/' + claimed['id'] + '/reservations/' + reservation + '/outcome',
                  'outcome', {'state': 'uncertain'})
    tick(core, rid)
    current = run(core, rid)
    states = {node['id']: (node['state'], node['failure']) for node in current['nodes']}
    assert states['one'] == ('failed', 'task_uncertain')
    assert states['two'] == ('blocked', 'dependency_failed')
    assert current['state'] == 'partial'
    # Resolving the uncertainty later never redispatches the run.
    resolution = {'attempt': claimed['attempt'], 'ack_unknown_effects': True, 'reason_code': 'other'}
    core.mutation('fixture-human', rid, 'tasks/' + claimed['id'] + '/resolve-uncertain',
                  'resolve', resolution)
    tick(core, rid)
    assert run(core, rid)['state'] == 'partial'
    assert plan_task(core, rid) is None


def test_abort_retires_queued_and_started_plan_tasks(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node('ra', 'a'), node('rb', 'b')], 'fixture-a', 'fixture-b')
    first = dispatch(core, rid)
    run_id = first['run']['id']
    started = claim(core, rid, 'abort-claim', 'fixture-b', protocol='server_start_v1')
    reservation = uuid.uuid4().hex + uuid.uuid4().hex
    body = {'attempt': started['attempt'], 'generation': started['generation'],
            'membership_revision': started['membership_revision'], 'reservation_id': reservation}
    core.mutation('fixture-b', rid, 'tasks/' + started['id'] + '/reservations', 'abort-reserve', body)
    core.mutation('fixture-b', rid, 'tasks/' + started['id'] + '/start', 'abort-start', body)
    aborted = core.abort_plan_dispatch('fixture-human', rid, 'abort-key',
                                       {'run_id': run_id, 'acknowledge_abort': True})
    assert aborted['run']['state'] == 'aborted'
    private_room = private(store, rid)
    queued_task = next(task for task in private_room['tasks'].values()
                       if task['purpose'] == 'plan_node' and task['assignee'] == 'a')
    assert queued_task['state'] == 'cancelled'
    started_task = private_room['tasks'][started['id']]
    assert started_task['state'] == 'uncertain'
    assert all(node['state'] in {'failed', 'blocked'} for node in aborted['run']['nodes'])
    # Replays return the cached public run; no new task, no automatic redispatch.
    replay = core.abort_plan_dispatch('fixture-human', rid, 'abort-key',
                                      {'run_id': run_id, 'acknowledge_abort': True})
    assert replay == aborted
    assert run(core, rid)['state'] == 'aborted'


def test_abort_guards(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    with pytest.raises(Problem, match='plan_dispatch_not_running'):
        core.abort_plan_dispatch('fixture-human', rid, 'none', {'run_id': 'x', 'acknowledge_abort': True})
    dispatch(core, rid)
    with pytest.raises(Problem, match='plan_dispatch_conflict'):
        core.abort_plan_dispatch('fixture-human', rid, 'wrong',
                                 {'run_id': 'not-the-run', 'acknowledge_abort': True})
    with pytest.raises(Problem, match='invalid_plan_abort_fields'):
        core.abort_plan_dispatch('fixture-human', rid, 'bad', {'run_id': 'x', 'acknowledge_abort': False})


def test_generation_change_aborts_run_and_edit_is_rejected(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    dispatch(core, rid)
    with pytest.raises(Problem, match='plan_dispatch_active'):
        write(core, rid, [node('other')], revision=1, key='edit')
    room = core.read('fixture-human', rid)
    pending = core.mutation('fixture-human', rid, 'controls', 'topic-request',
                            {'action': 'change_topic', 'parameters': {'topic': 'Changed topic'},
                             'expected_revision': room['room_revision']})
    core.mutation('fixture-human', rid, 'controls/' + pending['id'] + '/approve', 'topic-approve', {})
    current = run(core, rid)
    assert current['state'] == 'aborted'
    assert current['abort_reason'] == 'generation_changed'
    assert all(node['state'] in {'failed', 'blocked'} for node in current['nodes'])
    assert all(node['failure'] == 'aborted' for node in current['nodes'] if node['state'] == 'failed')


def test_replay_returns_cached_public_run_while_get_returns_latest(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    first = dispatch(core, rid, key='same-key')
    claimed = claim(core, rid)
    complete(core, rid, claimed)
    tick(core, rid)
    assert run(core, rid)['state'] == 'completed'
    replay = dispatch(core, rid, key='same-key')
    assert replay == first
    assert replay['run']['state'] == 'running'
    assert run(core, rid)['state'] == 'completed'
    with pytest.raises(Problem, match='idempotency_conflict'):
        dispatch(core, rid, key='same-key', plan_revision=2)


def test_no_dispatch_keeps_plan_inert(fixture):
    store, core, rid = fixture
    clear_tasks(store, rid)
    ready(store, 'fixture-a')
    before = private(store, rid)
    write(core, rid, [node()])
    after = private(store, rid)
    assert after['tasks'] == before['tasks']
    assert after['events'] == before['events']
    assert '_plan_dispatch' not in after


def test_plan_text_is_not_disclosed_anywhere_but_assignee_task(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node(instruction='TOP-SECRET-INSTRUCTION')], 'fixture-a')
    dispatch(core, rid)
    tick(core, rid)
    current = private(store, rid)
    assert 'TOP-SECRET-INSTRUCTION' in json.dumps(current['tasks'])
    for token in ('fixture-human', 'fixture-b'):
        assert 'TOP-SECRET-INSTRUCTION' not in json.dumps(core.read(token, rid))
        assert 'TOP-SECRET-INSTRUCTION' not in json.dumps(core.rooms(token))
        assert 'TOP-SECRET-INSTRUCTION' not in json.dumps(core.room_snapshot(token, rid))
    assert 'TOP-SECRET-INSTRUCTION' in json.dumps(core.read('fixture-a', rid))
    assert 'TOP-SECRET-INSTRUCTION' not in json.dumps(private(store, rid)['events'])
    assert 'TOP-SECRET-INSTRUCTION' not in json.dumps(private(store, rid)['keys'])


def test_http_dispatch_routes_precede_room_projection(fixture):
    from fastapi.testclient import TestClient
    from talkabout.api import create_app
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    client = TestClient(create_app(store, start_scheduler=False, allow_fixture_bearer=True))
    human = {'Authorization': 'Bearer fixture-human', 'Idempotency-Key': 'http-dispatch'}
    agent = {'Authorization': 'Bearer fixture-a', 'Idempotency-Key': 'http-agent'}
    base = '/api/v1/rooms/' + rid + '/work-plan/dispatch'
    assert client.get(base, headers={'Authorization': 'Bearer fixture-human'}).json() == {'run': None}
    body = dispatch_body(core, rid)
    assert client.post(base, headers=agent, json=body).status_code == 403
    created = client.post(base, headers=human, json=body)
    assert created.status_code == 200, created.text
    run_id = created.json()['run']['id']
    assert client.get(base, headers={'Authorization': 'Bearer fixture-human'}).json()['run']['id'] == run_id
    aborted = client.post(base + '/abort', headers={**human, 'Idempotency-Key': 'http-abort'},
                          json={'run_id': run_id, 'acknowledge_abort': True})
    assert aborted.status_code == 200, aborted.text
    assert aborted.json()['run']['state'] == 'aborted'
    assert client.get(base, headers={'Authorization': 'Bearer fixture-a'}).status_code == 403


def test_completed_same_epoch_task_keeps_plan_node(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node(instruction='SAME-EPOCH-SECRET')], 'fixture-a')
    dispatch(core, rid)
    claimed = claim(core, rid)
    complete(core, rid, claimed)
    tick(core, rid)
    assert run(core, rid)['state'] == 'completed'
    task = next(item for item in core.read('fixture-a', rid)['tasks'] if item['id'] == claimed['id'])
    assert task['plan_node']['instruction'] == 'SAME-EPOCH-SECRET'


def test_rejoined_assignee_loses_old_epoch_plan_node(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node(instruction='OLD-EPOCH-SECRET')], 'fixture-a')
    first = dispatch(core, rid)
    core.abort_plan_dispatch('fixture-human', rid, 'rejoin-abort',
                             {'run_id': first['run']['id'], 'acknowledge_abort': True})
    old_task = next(item for item in private(store, rid)['tasks'].values()
                    if item.get('purpose') == 'plan_node')
    member = private(store, rid)['members']['a']
    # Synthetic re-invite: same principal, new epoch/revision.
    store.rooms[rid]['members']['a'].update(status='active', revision=member['revision'] + 1,
                                            epoch=member['epoch'] + 1)
    for token in ('fixture-a', 'fixture-b', 'fixture-human'):
        for projection in (core.read(token, rid), core.rooms(token),
                           core.room_snapshot(token, rid)):
            text = json.dumps(projection)
            assert 'OLD-EPOCH-SECRET' not in text
            if token != 'fixture-a':
                assert '"plan_node":' not in text
    old_public = next(item for item in core.read('fixture-a', rid)['tasks']
                      if item['id'] == old_task['id'])
    assert 'plan_node' not in old_public
    # A new dispatch in the new epoch exposes only the new task's plan_node.
    write(core, rid, [node('fresh', instruction='NEW-EPOCH-SECRET')], revision=1, key='rejoin-plan')
    dispatch(core, rid, key='rejoin-dispatch', plan_revision=2)
    fresh = plan_task(core, rid)
    assert fresh['plan_node']['instruction'] == 'NEW-EPOCH-SECRET'
    old_public = next(item for item in core.read('fixture-a', rid)['tasks']
                      if item['id'] == old_task['id'])
    assert 'plan_node' not in old_public
    assert 'OLD-EPOCH-SECRET' not in json.dumps(
        [item for item in core.read('fixture-a', rid)['tasks'] if item['id'] == old_task['id']])


def test_plan_dispatch_bounds_fail_closed(fixture):
    store, core, rid = fixture
    inject_dispatch(store, rid, {'id': 'x'})
    with pytest.raises(Problem) as error:
        tick(core, rid)
    assert error.value.code == 'plan_dispatch_bounds'
    assert error.value.status == 422


def test_automatic_turns_resume_after_run_terminal(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    dispatch(core, rid)
    tick(core, rid)
    assert all(task['purpose'] == 'plan_node' for task in core.read('fixture-human', rid)['tasks'])
    claimed = claim(core, rid)
    complete(core, rid, claimed)
    tick(core, rid)
    assert run(core, rid)['state'] == 'completed'
    tick(core, rid)
    tasks = core.read('fixture-human', rid)['tasks']
    assert any(task['purpose'] != 'plan_node' for task in tasks)
    events = private(store, rid)['events']
    assert sum(event['type'] == 'plan.dispatch_started' for event in events) == 1
    assert sum(event['type'] == 'plan.dispatch_finished' for event in events) == 1


def test_running_run_suppresses_auto_turn_even_without_queued_tasks(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    dispatch(core, rid)
    # Synthetic state: a pending node whose dependency can never complete means no
    # task is queued/leased, yet the run is still running.
    store.rooms[rid]['_plan_dispatch']['nodes'][0]['depends_on'] = ['ghost']
    store.rooms[rid]['_plan_dispatch']['nodes'][0]['state'] = 'pending'
    store.rooms[rid]['_plan_dispatch']['nodes'][0]['task_id'] = None
    store.rooms[rid]['tasks'] = {}
    tick(core, rid)
    assert core.read('fixture-human', rid)['tasks'] == []
    assert run(core, rid)['state'] == 'running'


def test_turn_request_is_rejected_while_run_running(fixture):
    store, core, rid = fixture
    store.rooms[rid]['mode'] = 'free_discussion'
    prepare(store, core, rid, [node()], 'fixture-a')
    dispatch(core, rid)
    with pytest.raises(Problem, match='plan_dispatch_active'):
        core.mutation('fixture-a', rid, 'turn-requests', 'turn-request',
                      {'generation': core.read('fixture-a', rid)['generation'],
                       'membership_revision': core.read('fixture-a', rid)['membership_revision']})


def test_legacy_plan_task_expires_without_requeue(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node('one'), node('two', deps=['one'])], 'fixture-a')
    dispatch(core, rid)
    task = plan_task(core, rid)
    store.rooms[rid]['tasks'][task['id']].pop('_requires_server_start')
    claimed = claim(core, rid, 'legacy-claim', protocol=None)
    store.rooms[rid]['tasks'][claimed['id']]['lease_expires_at'] = 0
    tick(core, rid)
    assert private(store, rid)['tasks'][claimed['id']]['state'] == 'expired'
    assert [task['state'] for task in private(store, rid)['tasks'].values()] == ['expired']
    tick(core, rid)
    current = run(core, rid)
    states = {node['id']: (node['state'], node['failure']) for node in current['nodes']}
    assert states['one'] == ('failed', 'expired')
    assert states['two'] == ('blocked', 'dependency_failed')
    assert current['state'] == 'partial'
    with pytest.raises(Problem, match='task_unavailable'):
        core.mutation('fixture-a', rid, 'tasks/' + claimed['id'] + '/claim', 'second-claim',
                      {'generation': claimed['generation'],
                       'membership_revision': claimed['membership_revision']})


def test_started_plan_task_expires_to_uncertain_without_requeue(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node('one'), node('two', deps=['one'])], 'fixture-a')
    dispatch(core, rid)
    claimed = claim(core, rid, 'started-claim', protocol='server_start_v1')
    reservation = uuid.uuid4().hex + uuid.uuid4().hex
    body = {'attempt': claimed['attempt'], 'generation': claimed['generation'],
            'membership_revision': claimed['membership_revision'], 'reservation_id': reservation}
    core.mutation('fixture-a', rid, 'tasks/' + claimed['id'] + '/reservations', 'started-reserve', body)
    core.mutation('fixture-a', rid, 'tasks/' + claimed['id'] + '/start', 'started-start', body)
    store.rooms[rid]['tasks'][claimed['id']]['lease_expires_at'] = 0
    tick(core, rid)
    assert private(store, rid)['tasks'][claimed['id']]['state'] == 'uncertain'
    tick(core, rid)
    current = run(core, rid)
    states = {node['id']: (node['state'], node['failure']) for node in current['nodes']}
    assert states['one'] == ('failed', 'task_uncertain')
    assert states['two'] == ('blocked', 'dependency_failed')
    assert current['state'] == 'partial'


def test_general_task_lease_expiry_still_requeues(fixture):
    store, core, rid = fixture
    task = next(value for value in store.rooms[rid]['tasks'].values() if value['state'] == 'queued')
    claimed = core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/claim', 'general-claim',
                            {'generation': task['generation'],
                             'membership_revision': task['membership_revision']})
    store.rooms[rid]['tasks'][claimed['id']]['lease_expires_at'] = 0
    tick(core, rid)
    requeued = private(store, rid)['tasks'][claimed['id']]
    assert requeued['state'] == 'queued'
    assert requeued['purpose'] == task['purpose']


def test_owner_epoch_change_aborts_run(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    dispatch(core, rid)
    queued = plan_task(core, rid)
    store.rooms[rid]['members']['human']['epoch'] += 1
    tick(core, rid)
    current = private(store, rid)['_plan_dispatch']
    assert current['state'] == 'aborted'
    assert current['abort_reason'] == 'ownership_changed'
    assert all(node['failure'] == 'generation_changed' for node in current['nodes'])
    assert private(store, rid)['tasks'][queued['id']]['state'] == 'cancelled'
    with pytest.raises(Problem, match='task_unavailable'):
        core.mutation('fixture-a', rid, 'tasks/' + queued['id'] + '/claim', 'late-claim',
                      {'generation': queued['generation'],
                       'membership_revision': queued['membership_revision']})
    with pytest.raises(Problem, match='not_found'):
        run(core, rid)


def test_owner_epoch_change_retires_started_plan_task_uncertain(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node('ra', 'a'), node('rb', 'b')], 'fixture-a', 'fixture-b')
    dispatch(core, rid)
    started = claim(core, rid, 'owner-started', 'fixture-b', protocol='server_start_v1')
    reservation = uuid.uuid4().hex + uuid.uuid4().hex
    body = {'attempt': started['attempt'], 'generation': started['generation'],
            'membership_revision': started['membership_revision'], 'reservation_id': reservation}
    core.mutation('fixture-b', rid, 'tasks/' + started['id'] + '/reservations', 'owner-reserve', body)
    core.mutation('fixture-b', rid, 'tasks/' + started['id'] + '/start', 'owner-start', body)
    queued = plan_task(core, rid, 'fixture-a')
    store.rooms[rid]['members']['human']['epoch'] += 1
    tick(core, rid)
    current = private(store, rid)['_plan_dispatch']
    assert current['state'] == 'aborted' and current['abort_reason'] == 'ownership_changed'
    tasks = private(store, rid)['tasks']
    assert tasks[queued['id']]['state'] == 'cancelled'
    assert tasks[started['id']]['state'] == 'uncertain'
    assert all(node['failure'] == 'generation_changed' for node in current['nodes'])


def test_message_capacity_leaves_started_task_uncertain_not_wait_forever(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    dispatch(core, rid)
    claimed = claim(core, rid)
    # Synthetic: completion sees 200 messages while schedule's close threshold is 300,
    # so the capacity error cannot be masked by the room closing in the same tick.
    store.rooms[rid]['limits']['messages'] = 300
    store.rooms[rid]['messages'] = {
        'm' + str(i): {'id': 'm' + str(i), 'seq': i + 1, 'actor_id': 'human', 'actor_kind': 'human',
                       'text': 'synthetic', 'created_at': 0.0, 'reply_to': None, 'mentions': [],
                       'generation': 1, 'membership_epoch': 1, 'membership_revision': 1}
        for i in range(250)}
    with pytest.raises(Problem, match='message_capacity'):
        complete(core, rid, claimed)
    leased = plan_task(core, rid, state='leased')
    assert leased is not None
    store.rooms[rid]['tasks'][leased['id']]['deadline'] = 0
    store.rooms[rid]['tasks'][leased['id']]['lease_expires_at'] = 0
    tick(core, rid)
    tick(core, rid)
    current = run(core, rid)
    assert current['state'] == 'partial'
    assert current['nodes'][0]['failure'] == 'task_uncertain'


def test_abort_survives_event_reserve_boundary(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    first = dispatch(core, rid)
    queued = plan_task(core, rid)
    inject_events(store, rid, 2048)
    aborted = core.abort_plan_dispatch('fixture-human', rid, 'saturated-abort',
                                       {'run_id': first['run']['id'], 'acknowledge_abort': True})
    assert aborted['run']['state'] == 'aborted'
    room = private(store, rid)
    assert room['_plan_dispatch']['state'] == 'aborted'
    assert room['tasks'][queued['id']]['state'] == 'cancelled'
    assert len(room['events']) <= 2048
    with pytest.raises(Problem, match='task_unavailable'):
        core.mutation('fixture-a', rid, 'tasks/' + queued['id'] + '/claim', 'saturated-claim',
                      {'generation': queued['generation'],
                       'membership_revision': queued['membership_revision']})


def test_running_bounds_reject_event_overrun_without_blocking_abort(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    first = dispatch(core, rid)
    queued = plan_task(core, rid)
    revision = core.read('fixture-human', rid)['room_revision']
    inject_events(store, rid, 2049)
    with pytest.raises(Problem, match='event_capacity') as error:
        core.mutation('fixture-human', rid, 'controls', 'event-overrun',
                      {'action': 'change_topic', 'parameters': {'topic': 'Changed topic'},
                       'expected_revision': revision})
    assert error.value.status == 429
    assert private(store, rid)['controls'] == {}
    inject_events(store, rid, 2048)
    core.abort_plan_dispatch('fixture-human', rid, 'after-overrun',
                             {'run_id': first['run']['id'], 'acknowledge_abort': True})
    assert private(store, rid)['tasks'][queued['id']]['state'] == 'cancelled'


def test_dispatch_rejects_when_event_start_headroom_is_insufficient(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    inject_events(store, rid, 2048 - 17 + 1)
    with pytest.raises(Problem, match='event_capacity') as error:
        dispatch(core, rid, 'no-headroom')
    assert error.value.status == 429
    assert private(store, rid).get('_plan_dispatch') is None
    assert run(core, rid) is None


def test_dispatch_event_start_boundary_flushes_within_running_bounds(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    inject_events(store, rid, 2048 - 17)
    dispatch(core, rid, 'event-boundary')
    room = private(store, rid)
    assert room['_plan_dispatch']['state'] == 'running'
    assert len(room['events']) <= 2048


def test_dispatch_key_reserve_boundary_and_abort(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    inject_keys(store, rid, 2048 - 8 - 1)
    dispatch(core, rid, 'key-boundary')
    running = private(store, rid)
    assert running['_plan_dispatch']['state'] == 'running'
    assert len(running['keys']) <= 2048 - 8
    queued = plan_task(core, rid)
    with pytest.raises(Problem, match='idempotency_capacity') as error:
        core.mutation('fixture-a', rid, 'tasks/' + queued['id'] + '/claim', 'key-overrun',
                      {'generation': queued['generation'], 'execution_protocol': 'server_start_v1',
                       'membership_revision': queued['membership_revision']})
    assert error.value.status == 429
    assert private(store, rid)['tasks'][queued['id']]['state'] == 'queued'
    core.abort_plan_dispatch('fixture-human', rid, 'key-abort',
                             {'run_id': running['_plan_dispatch']['id'], 'acknowledge_abort': True})
    assert private(store, rid)['tasks'][queued['id']]['state'] == 'cancelled'


def test_dispatch_rejects_key_reserve_boundary_plus_one(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    inject_keys(store, rid, 2048 - 8)
    with pytest.raises(Problem, match='idempotency_capacity') as error:
        dispatch(core, rid, 'key-full')
    assert error.value.status == 429
    assert private(store, rid).get('_plan_dispatch') is None


def test_dispatch_byte_start_boundary_and_abort(fixture):
    from talkabout.domain import ROOM_BYTE_CAPACITY, PLAN_DISPATCH_BYTE_RESERVE, PLAN_DISPATCH_START_BYTE_SLACK, PLAN_DISPATCH_STATE_BYTE_SLACK
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    pad_room_bytes(store, rid, ROOM_BYTE_CAPACITY - PLAN_DISPATCH_BYTE_RESERVE
                   - PLAN_DISPATCH_STATE_BYTE_SLACK - PLAN_DISPATCH_START_BYTE_SLACK)
    dispatch(core, rid, 'byte-boundary')
    room = private(store, rid)
    assert room['_plan_dispatch']['state'] == 'running'
    from talkabout.domain import room_byte_size
    assert room_byte_size(room) <= ROOM_BYTE_CAPACITY - PLAN_DISPATCH_BYTE_RESERVE - PLAN_DISPATCH_STATE_BYTE_SLACK
    core.abort_plan_dispatch('fixture-human', rid, 'byte-abort',
                             {'run_id': room['_plan_dispatch']['id'], 'acknowledge_abort': True})
    assert private(store, rid)['_plan_dispatch']['state'] == 'aborted'


def max_plan_nodes(count=16, chars=2000):
    """Worst-case plan: max nodes, max-length 4-byte UTF-8 instructions."""
    instruction = '\U0001f600' * chars
    return [node('max-%d' % i, instruction=instruction, title='Max node %d' % i)
            for i in range(count)]


def test_dispatch_max_plan_at_byte_start_boundary_flushes_and_aborts(fixture):
    from talkabout.domain import (PLAN_DISPATCH_BYTE_RESERVE, PLAN_DISPATCH_START_BYTE_SLACK, PLAN_DISPATCH_STATE_BYTE_SLACK,
                                  ROOM_BYTE_CAPACITY, room_byte_size)
    store, core, rid = fixture
    prepare(store, core, rid, max_plan_nodes(), 'fixture-a')
    boundary = ROOM_BYTE_CAPACITY - PLAN_DISPATCH_BYTE_RESERVE - PLAN_DISPATCH_STATE_BYTE_SLACK - PLAN_DISPATCH_START_BYTE_SLACK
    pad_room_bytes(store, rid, boundary)
    dispatch(core, rid, 'max-plan-boundary')
    room = private(store, rid)
    assert room['_plan_dispatch']['state'] == 'running'
    assert len(room['tasks']) == 4
    assert room_byte_size(room) <= ROOM_BYTE_CAPACITY - PLAN_DISPATCH_BYTE_RESERVE - PLAN_DISPATCH_STATE_BYTE_SLACK
    core.abort_plan_dispatch('fixture-human', rid, 'max-plan-abort',
                             {'run_id': room['_plan_dispatch']['id'], 'acknowledge_abort': True})
    final = private(store, rid)
    assert final['_plan_dispatch']['state'] == 'aborted'
    assert all(task['state'] == 'cancelled' for task in final['tasks'].values())


def test_dispatch_max_plan_at_byte_start_boundary_plus_one_is_rejected(fixture):
    from talkabout.domain import (PLAN_DISPATCH_BYTE_RESERVE, PLAN_DISPATCH_START_BYTE_SLACK, PLAN_DISPATCH_STATE_BYTE_SLACK,
                                  ROOM_BYTE_CAPACITY)
    store, core, rid = fixture
    prepare(store, core, rid, max_plan_nodes(), 'fixture-a')
    boundary = ROOM_BYTE_CAPACITY - PLAN_DISPATCH_BYTE_RESERVE - PLAN_DISPATCH_STATE_BYTE_SLACK - PLAN_DISPATCH_START_BYTE_SLACK
    pad_room_bytes(store, rid, boundary + 1)
    with pytest.raises(Problem, match='room_byte_capacity') as error:
        dispatch(core, rid, 'max-plan-full')
    assert error.value.status == 413
    assert private(store, rid).get('_plan_dispatch') is None


def test_dispatch_rejects_byte_start_boundary_plus_one(fixture):
    from talkabout.domain import (ROOM_BYTE_CAPACITY, PLAN_DISPATCH_BYTE_RESERVE,
                                  PLAN_DISPATCH_START_BYTE_SLACK, PLAN_DISPATCH_STATE_BYTE_SLACK)
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    pad_room_bytes(store, rid, ROOM_BYTE_CAPACITY - PLAN_DISPATCH_BYTE_RESERVE
                   - PLAN_DISPATCH_STATE_BYTE_SLACK - PLAN_DISPATCH_START_BYTE_SLACK + 1)
    with pytest.raises(Problem, match='room_byte_capacity') as error:
        dispatch(core, rid, 'byte-full')
    assert error.value.status == 413
    assert private(store, rid).get('_plan_dispatch') is None


def test_ownership_auto_stop_survives_event_reserve_boundary(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    dispatch(core, rid)
    queued = plan_task(core, rid)
    inject_events(store, rid, 2048)
    store.rooms[rid]['members']['human']['epoch'] += 1
    tick(core, rid)
    room = private(store, rid)
    assert room['_plan_dispatch']['state'] == 'aborted'
    assert room['_plan_dispatch']['abort_reason'] == 'ownership_changed'
    assert room['tasks'][queued['id']]['state'] == 'cancelled'
    assert len(room['events']) <= 2048


def test_saturated_event_ring_still_saves_expired_failed_blocked_partial(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node('one'), node('two', deps=['one'])], 'fixture-a')
    dispatch(core, rid)
    claimed = claim(core, rid, 'saturated-expiry')
    store.rooms[rid]['tasks'][claimed['id']]['lease_expires_at'] = 0
    inject_events(store, rid, 2048)
    tick_raw(store)  # expiry transition is best effort and saved
    assert private(store, rid)['tasks'][claimed['id']]['state'] == 'expired'
    assert len(private(store, rid)['events']) == 2048
    before = private(store, rid)
    tick_raw(store)  # terminal transitions save; the auto turn waits for capacity
    current = private(store, rid)
    states = {node['id']: (node['state'], node['failure']) for node in current['_plan_dispatch']['nodes']}
    assert states['one'] == ('failed', 'expired')
    assert states['two'] == ('blocked', 'dependency_failed')
    assert current['_plan_dispatch']['state'] == 'partial'
    assert len(current['events']) == 2048
    assert not any(task['purpose'] != 'plan_node' for task in current['tasks'].values())
    assert current['next_slot'] == before['next_slot']
    assert current['mode_progress'] == before['mode_progress']
    # A later full-ring tick also preserves the turn and creates no task.
    tick_raw(store)
    deferred = private(store, rid)
    assert deferred['tasks'] == current['tasks']
    assert deferred['next_slot'] == current['next_slot']
    assert deferred['mode_progress'] == current['mode_progress']
    # With capacity restored, one task and its matching event appear together.
    inject_events(store, rid, 2031)
    tick_raw(store)
    restored = private(store, rid)
    ordinary = [task for task in restored['tasks'].values() if task['purpose'] != 'plan_node']
    assert len(ordinary) == 1
    assert restored['events'][-1]['type'] == 'task.queued'
    assert restored['events'][-1]['task_id'] == ordinary[0]['id']


def test_free_turn_request_is_not_consumed_at_event_capacity(fixture):
    store, core, rid = fixture
    room = store.rooms[rid]
    room['tasks'] = {}
    room['mode'] = 'free_discussion'
    member = room['members']['a']
    pending = {'principal_id': 'a', 'generation': room['generation'],
               'membership_epoch': member['epoch'], 'membership_revision': member['revision']}
    room['turn_requests'] = [pending]
    before_agent = room['next_agent']
    inject_events(store, rid, 2048)
    tick_raw(store)
    assert room['turn_requests'] == [pending]
    assert room['next_agent'] == before_agent
    assert room['tasks'] == {}
    inject_events(store, rid, 2031)
    tick_raw(store)
    assert room['turn_requests'] == []
    task = next(iter(room['tasks'].values()))
    assert room['events'][-1]['task_id'] == task['id']


def test_auto_turn_byte_capacity_defers_without_consuming_progress(fixture):
    from talkabout.domain import ROOM_BYTE_CAPACITY

    store, core, rid = fixture
    prepare(store, core, rid, [node('one')], 'fixture-a')
    dispatch(core, rid)
    room = store.rooms[rid]
    task_id = room['_plan_dispatch']['nodes'][0]['task_id']
    room['tasks'][task_id]['state'] = 'completed'
    tick_raw(store)  # terminal plan state commits separately
    pad_room_bytes(store, rid, ROOM_BYTE_CAPACITY - 100)
    before = private(store, rid)
    tick_raw(store)
    after = private(store, rid)
    assert after['tasks'] == before['tasks']
    assert after['events'] == before['events']
    assert after['next_slot'] == before['next_slot']
    assert after['mode_progress'] == before['mode_progress']
    after_pad = store.rooms[rid]['keys']['byte-pad']['pad']
    store.rooms[rid]['keys']['byte-pad']['pad'] = after_pad[:-4096]
    tick_raw(store)
    result = private(store, rid)
    added = [task for task in result['tasks'].values() if task['purpose'] != 'plan_node']
    assert len(added) == 1
    assert result['events'][-1]['type'] == 'task.queued'
    assert result['events'][-1]['task_id'] == added[0]['id']


def test_postgres_event_capacity_defers_auto_turn(postgres_fixture):
    from psycopg.types.json import Jsonb

    store, core, rid = postgres_fixture
    prepare(store, core, rid, [node('one')], 'fixture-a')
    dispatch(core, rid)
    def complete_plan(payload):
        task_id = payload['_plan_dispatch']['nodes'][0]['task_id']
        payload['tasks'][task_id]['state'] = 'completed'
    pg_update_payload(store, rid, complete_plan)
    with store.connect() as conn:
        last = conn.execute('SELECT last_event_seq FROM rooms WHERE id = %s', (rid,)).fetchone()['last_event_seq']
        for seq in range(last + 1, 2049):
            event = {'event_id': uuid.uuid4().hex, 'room_id': rid, 'seq': seq,
                     'type': 'synthetic.saturation', 'created_at': 0.0}
            conn.execute('INSERT INTO events (room_id, seq, event_id, payload) VALUES (%s, %s, %s, %s)',
                         (rid, seq, event['event_id'], Jsonb(event)))
        conn.execute('UPDATE rooms SET last_event_seq = 2048 WHERE id = %s', (rid,))
    store.tick_all()
    terminal = private(store, rid)
    assert terminal['_plan_dispatch']['state'] == 'completed'
    assert not any(t['purpose'] != 'plan_node' for t in terminal['tasks'].values())
    store.tick_all()
    deferred = private(store, rid)
    assert deferred['tasks'] == terminal['tasks']
    assert deferred['mode_progress'] == terminal['mode_progress']
    with store.connect() as conn:
        conn.execute('DELETE FROM events WHERE room_id = %s AND seq > 2030', (rid,))
        conn.execute('UPDATE rooms SET last_event_seq = 2030 WHERE id = %s', (rid,))
    store.tick_all()
    restored = private(store, rid)
    ordinary = [t for t in restored['tasks'].values() if t['purpose'] != 'plan_node']
    assert len(ordinary) == 1
    assert restored['events'][-1]['type'] == 'task.queued'
    assert restored['events'][-1]['task_id'] == ordinary[0]['id']


def test_postgres_started_ordinary_retirement_flushes(postgres_fixture):
    store, core, rid = postgres_fixture
    def setup(payload):
        task = next(task for task in payload['tasks'].values() if task['state'] == 'queued')
        task.update(state='leased', attempt=1, lease_expires_at=0, execution_protocol='server_start_v1',
                    _reservations=[{'attempt': 1, 'state': 'started'}])
        payload['dispatch_held'] = True
    pg_update_payload(store, rid, setup)
    store.tick_all()
    result = private(store, rid)
    assert next(iter(result['tasks'].values()))['state'] == 'uncertain'
    assert result['events'][-1]['type'] == 'task.uncertain'


def test_started_ordinary_task_retirement_is_persisted_when_held(fixture):
    from talkabout.domain import schedule

    store, core, rid = fixture
    room = store.rooms[rid]
    task = next(task for task in room['tasks'].values() if task['state'] == 'queued')
    task.update(state='leased', attempt=1, lease_expires_at=0, execution_protocol='server_start_v1',
                _reservations=[{'attempt': 1, 'state': 'started'}])
    room['dispatch_held'] = True
    assert schedule(room, store.principals) is True
    assert task['state'] == 'uncertain'
    assert room['events'][-1]['type'] == 'task.uncertain'


def test_ordinary_expiry_is_saved_at_event_capacity(fixture):
    store, core, rid = fixture
    room = store.rooms[rid]
    task = next(task for task in room['tasks'].values() if task['state'] == 'queued')
    task['deadline'] = 0
    room['dispatch_held'] = True
    inject_events(store, rid, 2048)
    tick_raw(store)
    assert task['state'] == 'expired'
    assert len(room['events']) == 2048
    assert room['events'][-1]['type'] == 'synthetic.saturation'


def test_plan_task_preflight_includes_node_and_slot_growth(fixture, monkeypatch):
    import talkabout.domain as domain

    store, core, rid = fixture
    prepare(store, core, rid, [node('one'), node('two')], 'fixture-a')
    dispatch(core, rid)
    room = store.rooms[rid]
    run_state = room['_plan_dispatch']
    target = run_state['nodes'][1]
    old_id = target['task_id']
    del room['tasks'][old_id]
    target.update(state='pending', task_id=None)
    room['next_slot'] = 9
    monkeypatch.setattr(domain, 'ident', lambda: 'a' * 32)
    base = domain.room_byte_size(room)
    room_copy = copy.deepcopy(room)
    copy_run = room_copy['_plan_dispatch']
    domain._plan_queue_node(room_copy, copy_run, copy_run['nodes'][1], 0)
    # At the precise old preflight boundary, updated node/slot bytes must count.
    new_task = room_copy['tasks']['a' * 32]
    old_estimate = len(json.dumps({'a' * 32: new_task}, ensure_ascii=False).encode('utf-8'))
    monkeypatch.setattr(domain, 'room_byte_limit', lambda _: base + old_estimate + 2048)
    domain._plan_queue_node(room, run_state, target, 0)
    assert target['state'] == 'failed'
    assert target['task_id'] is None
    assert 'a' * 32 not in room['tasks']


def test_deferred_large_node_fails_closed_at_byte_limit(fixture):
    from talkabout.domain import ROOM_BYTE_CAPACITY, PLAN_DISPATCH_BYTE_RESERVE

    store, core, rid = fixture
    nodes = [node('root-%d' % i, instruction='😀' * 2000) for i in range(5)]
    prepare(store, core, rid, nodes, 'fixture-a')
    dispatch(core, rid)
    room = store.rooms[rid]
    first = room['_plan_dispatch']['nodes'][0]
    room['tasks'][first['task_id']]['state'] = 'completed'
    pad_room_bytes(store, rid, ROOM_BYTE_CAPACITY - PLAN_DISPATCH_BYTE_RESERVE
                   - PLAN_DISPATCH_STATE_BYTE_SLACK - 3000)
    tick_raw(store)
    result = private(store, rid)
    deferred = result['_plan_dispatch']['nodes'][4]
    assert (deferred['state'], deferred['failure'], deferred['task_id']) == ('failed', 'aborted', None)
    assert len(result['tasks']) == 4


def test_saturated_ready_node_is_failed_closed_without_creating_a_task(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node('one'), node('two', deps=['one']), node('three', deps=['two'])],
            'fixture-a')
    dispatch(core, rid)
    root = plan_task(core, rid)
    store.rooms[rid]['tasks'][root['id']]['state'] = 'completed'
    inject_events(store, rid, 2048)
    tick_raw(store)
    room = private(store, rid)
    nodes = {node['id']: node for node in room['_plan_dispatch']['nodes']}
    assert nodes['one']['state'] == 'completed'
    assert (nodes['two']['state'], nodes['two']['failure']) == ('failed', 'aborted')
    assert nodes['two']['task_id'] is None
    assert nodes['three']['state'] == 'blocked'
    assert room['_plan_dispatch']['state'] == 'partial'
    plan_tasks = [task for task in room['tasks'].values() if task['purpose'] == 'plan_node']
    assert [task['id'] for task in plan_tasks] == [root['id']]
    assert len(room['events']) == 2048


def test_plan_transition_event_counts_unchanged_normally(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node('one'), node('two', deps=['one'])], 'fixture-a')
    dispatch(core, rid)
    claimed = claim(core, rid)
    complete(core, rid, claimed)
    tick(core, rid)
    types = [event['type'] for event in private(store, rid)['events']]
    started = types.index('plan.dispatch_started')
    plan_types = types[started:]
    assert plan_types.count('plan.node_failed') == 0
    assert plan_types.count('plan.dispatch_finished') == 0
    assert plan_types.count('task.queued') == 2
    complete(core, rid, claim(core, rid, 'normal-two'), 'complete-two')
    tick(core, rid)
    types = [event['type'] for event in private(store, rid)['events']]
    assert types.count('plan.dispatch_finished') == 1
    assert types.count('plan.node_failed') == 0
    assert run(core, rid)['state'] == 'completed'


def test_abort_event_order_is_unchanged_normally(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    first = dispatch(core, rid)
    core.abort_plan_dispatch('fixture-human', rid, 'ordered-abort',
                             {'run_id': first['run']['id'], 'acknowledge_abort': True})
    types = [event['type'] for event in private(store, rid)['events']]
    assert types[-2:] == ['task.cancelled', 'plan.dispatch_aborted']
    assert types.count('plan.dispatch_aborted') == 1


def test_prompt_text_marks_plan_node_as_owner_data():
    from talkabout.agent_runtime import prompt_text
    context = {'room_id': 'r', 'plan_node': {'node_id': 'one', 'title': 't',
                                             'instruction': 'i', 'write_scope': 'analysis',
                                             'run_id': 'run'}}
    text = prompt_text(context)
    assert 'owner-authored work instruction' in text
    assert 'does not enforce any workspace boundary' in text
    assert 'owner-authored work instruction' not in prompt_text({'room_id': 'r'})


def test_postgres_dispatch_claim_complete_next_node(postgres_fixture):
    store, core, rid = postgres_fixture
    prepare(store, core, rid, [node('one'), node('two', deps=['one'])], 'fixture-a')
    response = dispatch(core, rid)
    assert response['tasks_queued'] == 1
    claimed = claim(core, rid)
    assert claimed['purpose'] == 'plan_node'
    complete(core, rid, claimed)
    tick(core, rid)
    follow = plan_task(core, rid)
    assert follow is not None and follow['plan_node']['node_id'] == 'two'
    complete(core, rid, claim(core, rid, 'claim-two'), 'complete-two')
    tick(core, rid)
    assert run(core, rid)['state'] == 'completed'


def test_postgres_key_and_byte_reserve_boundary_abort(postgres_fixture):
    from talkabout.domain import (PLAN_DISPATCH_BYTE_RESERVE, PLAN_DISPATCH_KEY_RESERVE,
                                  PLAN_DISPATCH_START_BYTE_SLACK, ROOM_BYTE_CAPACITY)
    store, core, rid = postgres_fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    pg_update_payload(store, rid, lambda payload: payload.update(
        {'keys': {'pg-key-' + str(i): {} for i in range(2048 - PLAN_DISPATCH_KEY_RESERVE)}}))
    with pytest.raises(Problem, match='idempotency_capacity'):
        dispatch(core, rid, 'pg-key-full')
    pg_update_payload(store, rid, lambda payload: payload.update(
        {'keys': {'pg-key-' + str(i): {} for i in range(2048 - PLAN_DISPATCH_KEY_RESERVE - 1)}}))
    dispatch(core, rid, 'pg-key-boundary')
    running = private(store, rid)
    assert running['_plan_dispatch']['state'] == 'running'
    assert len(running['keys']) <= 2048 - PLAN_DISPATCH_KEY_RESERVE
    queued = plan_task(core, rid)
    core.abort_plan_dispatch('fixture-human', rid, 'pg-key-abort',
                             {'run_id': running['_plan_dispatch']['id'], 'acknowledge_abort': True})
    assert private(store, rid)['tasks'][queued['id']]['state'] == 'cancelled'
    # Byte boundary: reset keys, write a new plan, then pad to the start boundary.
    write(core, rid, [node('byte')], revision=1, key='pg-byte-plan')
    pg_update_payload(store, rid, lambda payload: payload.update(
        {'keys': {'pg-key-' + str(i): {} for i in range(10)}}))
    byte_start = ROOM_BYTE_CAPACITY - PLAN_DISPATCH_BYTE_RESERVE - PLAN_DISPATCH_STATE_BYTE_SLACK - PLAN_DISPATCH_START_BYTE_SLACK
    pg_pad_payload(store, rid, byte_start + 1)
    with pytest.raises(Problem, match='room_byte_capacity') as error:
        dispatch(core, rid, 'pg-byte-full')
    assert error.value.status == 413
    assert private(store, rid)['_plan_dispatch']['state'] == 'aborted'


def test_owner_permission_loss_stops_run_and_never_resumes(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node()], 'fixture-a')
    dispatch(core, rid)
    queued = plan_task(core, rid)
    # A permission edit keeps the epoch but removes the rights that make abort possible.
    store.rooms[rid]['members']['human']['permissions'] = sorted(HUMAN_PERMISSIONS - {'room:control'})
    tick(core, rid)
    room = private(store, rid)
    assert room['_plan_dispatch']['state'] == 'aborted'
    assert room['_plan_dispatch']['abort_reason'] == 'ownership_changed'
    assert room['tasks'][queued['id']]['state'] == 'cancelled'
    with pytest.raises(Problem, match='task_unavailable'):
        core.mutation('fixture-a', rid, 'tasks/' + queued['id'] + '/claim', 'after-loss',
                      {'generation': queued['generation'],
                       'membership_revision': queued['membership_revision']})
    with pytest.raises(Problem) as error:
        core.plan_dispatch('fixture-human', rid)
    assert error.value.status == 403
    # Restoring the permission must not resume the aborted run.
    store.rooms[rid]['members']['human']['permissions'] = sorted(HUMAN_PERMISSIONS)
    tick(core, rid)
    assert private(store, rid)['_plan_dispatch']['state'] == 'aborted'
    assert core.plan_dispatch('fixture-human', rid)['run']['state'] == 'aborted'
