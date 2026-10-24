"""Synthetic bearer fixtures only; no provider, credential issuance or server process."""
import copy

import pytest

from conftest import LocalClient, control, leased, result
from talkabout.domain import Problem, token_hash


SESSION = '00000000-0000-4000-8000-000000000001'
OTHER = '00000000-0000-4000-8000-000000000002'


@pytest.mark.parametrize('verb',['heartbeat','skip','complete'])
def test_session_claim_cannot_drop_its_binding_on_later_requests(fixture,verb):
    store,core,rid=fixture;join(core,rid)
    task=next(t for t in core.read('fixture-a',rid)['tasks'] if t['state']=='queued' and t['assignee']=='a')
    claimed=core.mutation('fixture-a',rid,'tasks/'+task['id']+'/claim','bound-claim',
                         {'generation':1,'membership_revision':2,'session_id':SESSION})
    body=result(claimed) if verb=='complete' else {k:claimed[k] for k in ('generation','membership_revision','attempt')}
    before=copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem,match='session_required'):
        core.mutation('fixture-a',rid,'tasks/'+task['id']+'/'+verb,'omitted-session',body)
    assert store.rooms[rid]==before


def test_reconnected_session_cannot_inherit_another_sessions_lease(fixture):
    store,core,rid=fixture;join(core,rid)
    task=next(t for t in core.read('fixture-a',rid)['tasks'] if t['state']=='queued' and t['assignee']=='a')
    claimed=core.mutation('fixture-a',rid,'tasks/'+task['id']+'/claim','bound-claim',
                         {'generation':1,'membership_revision':2,'session_id':SESSION})
    join(core,rid,OTHER)
    before=copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem,match='stale_task_session'):
        core.mutation('fixture-a',rid,'tasks/'+task['id']+'/complete','new-session-old-lease',{**result(claimed),'session_id':OTHER})
    with pytest.raises(Problem,match='stale_session'):
        core.mutation('fixture-a',rid,'tasks/'+task['id']+'/complete','old-session-result',{**result(claimed),'session_id':SESSION})
    assert store.rooms[rid]==before


def test_legacy_claim_remains_legacy_even_when_a_client_session_exists(fixture):
    _,core,rid=fixture;join(core,rid)
    task=leased(core,rid)
    assert core.mutation('fixture-a',rid,'tasks/'+task['id']+'/complete','legacy-result',result(task))['task']['state']=='completed'


def test_expired_session_claim_cannot_reenter_through_legacy_contract(fixture):
    import time
    store,core,rid=fixture
    join(core,rid)
    task=next(t for t in core.read('fixture-a',rid)['tasks'] if t['state']=='queued' and t['assignee']=='a')
    claimed=core.mutation('fixture-a',rid,'tasks/'+task['id']+'/claim','first-bound',
                         {'generation':1,'membership_revision':2,'session_id':SESSION})
    with store.transaction(rid,'fixture-a') as (_,room):
        room['tasks'][task['id']]['lease_expires_at']=time.time()-1
    core.tick('fixture-a',rid)
    join(core,rid,OTHER)
    before=copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem,match='session_required'):
        core.mutation('fixture-a',rid,'tasks/'+task['id']+'/claim','downgrade',
                      {'generation':1,'membership_revision':2})
    assert store.rooms[rid]==before
    reclaimed=core.mutation('fixture-a',rid,'tasks/'+task['id']+'/claim','fresh-bound',
                           {'generation':1,'membership_revision':2,'session_id':OTHER})
    assert reclaimed['attempt']==claimed['attempt']+1
    assert reclaimed['participation_session_id']==OTHER
    with pytest.raises(Problem,match='session_required'):
        core.mutation('fixture-a',rid,'tasks/'+task['id']+'/complete','missing-after-reclaim',result(reclaimed))


def test_reusing_retired_uuid_cannot_revive_delayed_receipts(fixture):
    _, core, rid = fixture
    first = join(core, rid)
    join(core, rid, OTHER)
    # A restored old client configuration must not make its old cursor valid again.
    with pytest.raises(Problem, match='retired_session'):
        core.participation('fixture-a', rid, 'join', {
            'session_id': SESSION, 'previous_session_id': OTHER,
            'generation': 1, 'membership_revision': 2})
    with pytest.raises(Problem):
        ack(core, rid, first['snapshot_cursor'])


def test_rotation_capacity_keeps_current_session_and_retry_is_free(fixture):
    import uuid
    store, core, rid = fixture
    join(core, rid)
    current = SESSION
    for i in range(64):
        fresh = str(uuid.UUID(int=i + 100))
        body = {'session_id': fresh, 'previous_session_id': current, 'generation': 1, 'membership_revision': 2}
        core.participation('fixture-a', rid, 'join', body)
        current = fresh
    before = copy.deepcopy(store.rooms[rid])
    assert core.participation('fixture-a', rid, 'join', body)['participation']['session_id'] == current
    with pytest.raises(Problem, match='session_rotation_capacity'):
        core.participation('fixture-a', rid, 'join', {**body, 'session_id': OTHER, 'previous_session_id': current})
    assert store.rooms[rid] == before
    assert '_retired_participation_ids' not in str(core.read('fixture-human', rid))


def test_postgres_participation_restart_rotation_and_hold(postgres_fixture):
    store, core, rid = postgres_fixture
    joined = join(core, rid)
    advice(core, rid)
    cursor = core.events('fixture-a', joined['snapshot_cursor'], target_room=rid, session_id=SESSION)['next_cursor']
    saved = ack(core, rid, cursor)
    from talkabout.core import Core
    from talkabout.store import PostgresStore
    restarted = Core(PostgresStore(store.dsn, store.schema))
    assert ack(restarted, rid, cursor) == saved
    join(restarted, rid, OTHER)
    with pytest.raises(Problem, match='retired_session'):
        restarted.participation('fixture-a', rid, 'join', {'session_id': SESSION, 'previous_session_id': OTHER,
                                'generation': 1, 'membership_revision': 2})
    with pytest.raises(Problem):
        ack(restarted, rid, cursor)
    control(restarted, rid, 'hold_dispatch')
    persisted = Core(PostgresStore(store.dsn, store.schema))
    assert persisted.read('fixture-human', rid)['dispatch_held'] is True
    with pytest.raises(Problem, match='dispatch_held'):
        leased(persisted, rid)
    store.revoke('fixture-a')
    with pytest.raises(Problem, match='unauthorized'):
        persisted.notifications('fixture-a', rid, OTHER)


def test_postgres_approval_audit_rollback_and_replay(postgres_fixture, monkeypatch):
    _, core, rid = postgres_fixture
    before = core.read('fixture-human', rid)
    pending = core.mutation('fixture-human', rid, 'controls', 'hold',
                             {'action': 'hold_dispatch', 'expected_revision': before['room_revision']})
    import talkabout.core as module
    original = module.emit
    def crash(room, kind, **payload):
        if kind == 'control.applied':
            raise RuntimeError('synthetic PG audit failure')
        return original(room, kind, **payload)
    monkeypatch.setattr(module, 'emit', crash)
    with pytest.raises(RuntimeError):
        core.mutation('fixture-human', rid, 'controls/'+pending['id']+'/approve', 'approve', {})
    assert core.read('fixture-human', rid)['dispatch_held'] is False
    assert core.read('fixture-human', rid)['room_revision'] == before['room_revision']
    monkeypatch.setattr(module, 'emit', original)
    saved = core.mutation('fixture-human', rid, 'controls/'+pending['id']+'/approve', 'approve', {})
    assert core.mutation('fixture-human', rid, 'controls/'+pending['id']+'/approve', 'approve', {}) == saved


def test_mcp_accept_and_claim_use_existing_actor_and_credential_scope(fixture):
    from talkabout.mcp import MCPServer
    from test_mcp_runtime import ready
    store, core, rid = fixture
    other = core.create('fixture-human', {'topic': 'Private invitation'}, 'invite-room')['id']
    pending = core.mutation('fixture-human', other, 'memberships', 'invite',
                            {'principal_id': 'a', 'permissions': sorted(core.read('fixture-a', rid)['members']['a']['permissions']), 'status': 'pending'})
    server = MCPServer(LocalClient(store))
    ready(server)
    def call(name, args):
        return server.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}})
    store.credentials[token_hash('fixture-a')]['room_id'] = rid
    args = {'room_id': other, 'expected_revision': pending['revision'], 'acknowledge_private': True, 'client_mutation_id': 'accept'}
    assert call('accept_invitation', args)['result']['isError']
    assert core.read('fixture-human', other)['members']['a']['status'] == 'pending'
    store.credentials[token_hash('fixture-a')]['room_id'] = other
    accepted = call('accept_invitation', args)
    assert not accepted['result']['isError'] and call('accept_invitation', args)['result'] == accepted['result']
    assert call('accept_invitation', {**args, 'actor_id': 'human'})['error']['code'] == -32602
    store.credentials[token_hash('fixture-a')]['room_id'] = rid
    join(core, rid)
    task = next(t for t in core.read('fixture-a', rid)['tasks'] if t['state'] == 'queued')
    store.credentials[token_hash('fixture-a')]['scopes'] = ['room:read']
    assert call('claim_turn', {'room_id': rid, 'session_id': SESSION, 'task_id': task['id'],
                'generation': 1, 'membership_revision': 2, 'client_mutation_id': 'claim'})['result']['isError']
    assert core.read('fixture-human', rid)['tasks'][0]['attempt'] == 0


def join(core, rid, session=SESSION, token='fixture-a'):
    room = core.read(token, rid)
    return core.participation(token, rid, 'join', {
        'session_id': session, 'generation': room['generation'],
        'membership_revision': room['membership_revision'],
        **({'previous_session_id': SESSION} if session == OTHER else {})})


def ack(core, rid, cursor, state='read', session=SESSION, token='fixture-a'):
    room = core.read(token, rid)
    return core.participation(token, rid, 'ack', {
        'session_id': session, 'generation': room['generation'],
        'membership_revision': room['membership_revision'], 'cursor': cursor, 'state': state})


def advice(core, rid):
    return core.mutation('fixture-human', rid, 'messages', 'safe-advice',
                         {'kind': 'advice', 'client_message_id': 'safe-advice', 'text': 'PRIVATE CONTENT'})


def test_explicit_session_reconnect_ack_and_safe_notifications(fixture):
    store, core, rid = fixture
    joined = join(core, rid)
    assert joined['participation']['actor_id'] == 'a'
    assert joined['participation']['read_seq'] == 0
    assert token_hash('fixture-a') not in str(joined)
    item = advice(core, rid)
    before = copy.deepcopy(store.rooms[rid]['tasks'])
    notice = core.notifications('fixture-a', rid, SESSION)
    assert set(notice) == {'count', 'oldest_at', 'state', 'read_seq'}
    assert notice['count'] == 1 and 'PRIVATE CONTENT' not in str(notice) and 'human' not in str(notice)
    cursor = core.events('fixture-a', joined['snapshot_cursor'], target_room=rid, session_id=SESSION)['next_cursor']
    delivered = ack(core, rid, cursor, 'delivered')
    assert delivered['delivered_seq'] >= item['seq'] and delivered['read_seq'] == 0
    assert core.notifications('fixture-a', rid, SESSION)['count'] == 1
    read = ack(core, rid, cursor)
    assert ack(core, rid, cursor) == read
    assert core.notifications('fixture-a', rid, SESSION)['count'] == 0
    assert join(core, rid)['participation']['read_seq'] == read['read_seq']
    assert store.rooms[rid]['tasks'] == before


def test_busy_notifications_hold_without_dropping_and_admin_dispatch_hold(fixture):
    store, core, rid = fixture
    join(core, rid)
    task = leased(core, rid)
    advice(core, rid)
    assert core.notifications('fixture-a', rid, SESSION)['state'] == 'held'
    control(core, rid, 'hold_dispatch')
    assert core.read('fixture-human', rid)['dispatch_held'] is True
    assert core.read('fixture-a', rid)['generation'] == task['generation']
    core.mutation('fixture-a', rid, 'tasks/'+task['id']+'/complete', 'finish', result(task))
    assert not any(t['state'] == 'queued' for t in core.read('fixture-human', rid)['tasks'])
    assert core.notifications('fixture-a', rid, SESSION)['state'] == 'available'
    control(core, rid, 'release_dispatch')
    assert any(t['state'] == 'queued' for t in core.read('fixture-human', rid)['tasks'])


def test_dispatch_hold_rejects_unclaimed_work_but_not_completed_replay(fixture):
    _, core, rid = fixture
    control(core, rid, 'hold_dispatch')
    with pytest.raises(Problem, match='dispatch_held'):
        leased(core, rid)
    control(core, rid, 'release_dispatch')
    task = leased(core, rid)
    body = result(task)
    saved = core.mutation('fixture-a', rid, 'tasks/'+task['id']+'/complete', 'saved', body)
    control(core, rid, 'hold_dispatch')
    assert core.mutation('fixture-a', rid, 'tasks/'+task['id']+'/complete', 'saved', body) == saved


@pytest.mark.parametrize('change', ['session', 'generation', 'membership', 'credential', 'revoke'])
def test_stale_participation_is_rejected(fixture, change):
    store, core, rid = fixture
    joined = join(core, rid)
    if change == 'session':
        join(core, rid, OTHER)
    elif change == 'generation':
        control(core, rid, 'pause')
    elif change == 'membership':
        with store.transaction(rid, 'fixture-human') as (_, room):
            room['members']['a']['revision'] += 1
    elif change == 'credential':
        # Substitute an already provisioned synthetic fixture bearer, not a newly issued credential.
        store.credentials[token_hash('fixture-b')]['id'] = 'a'
    else:
        store.revoke('fixture-a')
    with pytest.raises(Problem):
        ack(core, rid, joined['snapshot_cursor'], token='fixture-b' if change == 'credential' else 'fixture-a')


def test_cursor_cannot_ack_other_actor_room_or_future_or_regress_read(fixture):
    _, core, rid = fixture
    first = join(core, rid)
    advice(core, rid)
    second = join(core, rid)
    ack(core, rid, second['snapshot_cursor'])
    assert ack(core, rid, first['snapshot_cursor'])['read_seq'] == second['room']['last_seq']
    cursor = core.events('fixture-b', target_room=rid)['next_cursor']
    with pytest.raises(Problem, match='invalid_cursor'):
        ack(core, rid, cursor)
    cursor = core.events('fixture-a')['next_cursor']
    with pytest.raises(Problem, match='invalid_cursor'):
        ack(core, rid, cursor)
    with pytest.raises(Problem):
        ack(core, rid, 'missing-cursor')


def test_api_participation_and_membership_invalidation(fixture):
    store, core, rid = fixture
    client = LocalClient(store)
    body = {'session_id': SESSION, 'generation': 1, 'membership_revision': 2}
    joined = client.request('POST', f'rooms/{rid}/participation/join', body)
    assert joined['participation']['session_id'] == SESSION
    assert client.request('GET', f'rooms/{rid}/notifications', params={'session_id': SESSION})['count'] == 0
    with store.transaction(rid, 'fixture-human') as (_, room):
        room['members']['a']['status'] = 'blocked'
    import httpx
    with pytest.raises(httpx.HTTPStatusError):
        client.request('GET', f'rooms/{rid}/notifications', params={'session_id': SESSION})


def test_participation_internal_bindings_never_projected(fixture):
    _, core, rid = fixture
    join(core, rid)
    assert '_participation' not in str(core.read('fixture-human', rid))
    assert '_credential_binding' not in str(core.room_snapshot('fixture-a', rid))


@pytest.mark.parametrize('failure_point', ['audit', 'schedule', 'save_response'])
def test_control_atomic_rollback_on_audit_failure_and_restart(fixture, monkeypatch, failure_point):
    store, core, rid = fixture
    room = core.read('fixture-human', rid)
    pending = core.mutation('fixture-human', rid, 'controls', 'request',
                            {'action': 'hold_dispatch', 'expected_revision': room['room_revision']})
    before = copy.deepcopy(store.rooms[rid])
    import talkabout.core as module
    original = module.emit
    schedule, save = module.schedule, module.save_response
    def fail(room, kind, **payload):
        if kind == 'control.applied' and failure_point == 'audit':
            raise RuntimeError('synthetic audit failure')
        return original(room, kind, **payload)
    monkeypatch.setattr(module, 'emit', fail)
    def crash(*args, **kwargs):
        raise RuntimeError('synthetic commit-boundary failure')
    if failure_point == 'schedule':
        monkeypatch.setattr(module, 'schedule', crash)
    if failure_point == 'save_response':
        monkeypatch.setattr(module, 'save_response', crash)
    with pytest.raises(RuntimeError):
        core.mutation('fixture-human', rid, 'controls/'+pending['id']+'/approve', 'approve', {})
    assert store.rooms[rid] == before
    monkeypatch.setattr(module, 'emit', original)
    monkeypatch.setattr(module, 'schedule', schedule)
    monkeypatch.setattr(module, 'save_response', save)
    from talkabout.core import Core
    restarted = Core(store)
    saved = restarted.mutation('fixture-human', rid, 'controls/'+pending['id']+'/approve', 'approve', {})
    assert restarted.mutation('fixture-human', rid, 'controls/'+pending['id']+'/approve', 'approve', {}) == saved
    assert sum(e['type'] == 'control.applied' for e in store.rooms[rid]['events']) == 1


def test_room_scoped_actor_cannot_use_other_room_or_impersonate(fixture):
    store, core, rid = fixture
    other = core.create('fixture-a', {'topic': 'Other private fixture'}, 'other-room')['id']
    store.credentials[token_hash('fixture-a')]['room_id'] = rid
    before = copy.deepcopy(store.rooms[other])
    for operation in [lambda: join(core, other), lambda: core.read('fixture-a', other),
                      lambda: core.mutation('fixture-a', other, 'artifacts', 'outside',
                                           {'generation': 1, 'membership_revision': 1, 'text': 'synthetic'}),
                      lambda: core.participation('fixture-a', other, 'ack',
                          {'session_id': SESSION, 'generation': 1, 'membership_revision': 1, 'cursor': 'none', 'state': 'read'})]:
        with pytest.raises(Problem):
            operation()
    assert store.rooms[other] == before
    with pytest.raises(Problem, match='invalid_fields'):
        core.participation('fixture-a', rid, 'join', {'session_id': SESSION, 'generation': 1,
                           'membership_revision': 2, 'actor_id': 'human'})


def test_future_cursor_and_revoked_actor_are_never_receipted(fixture):
    store, core, rid = fixture
    joined = join(core, rid)
    payload = copy.deepcopy(store.cursors[joined['snapshot_cursor']])
    payload['offsets'][rid] = core.read('fixture-a', rid)['last_seq'] + 100
    store.cursors['future'] = payload
    with pytest.raises(Problem, match='invalid_cursor'):
        ack(core, rid, 'future')
    store.revoke('fixture-a')
    with pytest.raises(Problem, match='unauthorized'):
        core.notifications('fixture-a', rid, SESSION)


def test_old_session_cursor_and_claim_rejected_after_replacement(fixture):
    _, core, rid = fixture
    old = join(core, rid)
    task = leased(core, rid)
    join(core, rid, OTHER)
    with pytest.raises(Problem, match='invalid_cursor'):
        ack(core, rid, old['snapshot_cursor'], session=OTHER)
    with pytest.raises(Problem, match='invalid_cursor'):
        core.events('fixture-a', old['snapshot_cursor'], target_room=rid, session_id=OTHER)
    with pytest.raises(Problem, match='stale_session'):
        core.mutation('fixture-a', rid, 'tasks/'+task['id']+'/complete', 'old-result',
                      {**result(task), 'session_id': SESSION})
    with pytest.raises(Problem, match='session_conflict'):
        join(core, rid)


@pytest.mark.parametrize('state', [None, [], {}, 1, True, 'completed', 'approved'])
def test_receipt_rejects_invalid_or_execution_states(fixture, state):
    _, core, rid = fixture
    joined = join(core, rid)
    with pytest.raises(Problem, match='invalid_receipt'):
        ack(core, rid, joined['snapshot_cursor'], state=state)


def test_stale_proposal_cannot_omit_binding_after_session_join(fixture):
    _, core, rid = fixture
    join(core, rid)
    body = {'generation': 1, 'membership_revision': 2, 'text': 'synthetic'}
    with pytest.raises(Problem, match='session_required'):
        core.mutation('fixture-a', rid, 'artifacts', 'missing', body)
    join(core, rid, OTHER)
    with pytest.raises(Problem, match='stale_session'):
        core.mutation('fixture-a', rid, 'artifacts', 'old', {**body, 'session_id': SESSION})
    assert core.mutation('fixture-a', rid, 'artifacts', 'new', {**body, 'session_id': OTHER})['revision'] == 1


def test_mcp_session_turn_and_ack_do_not_execute_provider(fixture):
    import json
    from talkabout.mcp import MCPServer
    from test_mcp_runtime import ready
    store, core, rid = fixture
    client = LocalClient(store)
    server = MCPServer(client)
    ready(server)
    def call(name, **arguments):
        reply = server.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                               'params': {'name': name, 'arguments': arguments}})
        assert not reply.get('error') and not reply['result']['isError'], reply
        return json.loads(reply['result']['content'][0]['text'])
    context = {'room_id': rid, 'session_id': SESSION, 'generation': 1, 'membership_revision': 2}
    joined = call('join_room', **context)
    assert set(call('list_participants', room_id=rid)) == {'human', 'a', 'b'}
    call('ack_room', **context, cursor=joined['snapshot_cursor'], state='delivered')
    task = next(t for t in joined['room']['tasks'] if t['state'] == 'queued')
    claim = call('claim_turn', **context, task_id=task['id'], client_mutation_id='claim')
    args = {**context, 'task_id': task['id'], 'attempt': claim['attempt'], 'text': 'SYNTHETIC', 'client_mutation_id': 'result'}
    saved = call('post_message', **args)
    assert call('post_message', **args) == saved
    assert saved['message']['execution_evidence'] == 'unverified_agent'
    assert len(core.read('fixture-human', rid)['messages']) == 1
    events = call('poll_room', room_id=rid, session_id=SESSION, cursor=joined['snapshot_cursor'])
    assert events['events']
    call('ack_room', **context, cursor=events['next_cursor'], state='read')
    assert call('room_notifications', room_id=rid, session_id=SESSION)['count'] == 0
