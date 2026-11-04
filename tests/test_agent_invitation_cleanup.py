"""Synthetic-only explicit cleanup of dead agent-origin pending invitations."""
import copy
import threading
import time

import pytest
from fastapi.testclient import TestClient

from talkabout.api import create_app
from talkabout.core import MEMBERSHIP_ID_MAX
from talkabout.domain import AGENT_PERMISSIONS, HUMAN_PERMISSIONS, SELF_PERMISSIONS, Problem, digest


def _client(store, enabled=True):
    return TestClient(create_app(store, start_scheduler=False, allow_fixture_bearer=True,
                                 enable_agent_invites=enabled))


def _h(token):
    return {'Authorization': 'Bearer ' + token}


def _members(client, rid, token='fixture-human'):
    response = client.get('/api/v1/rooms/' + rid + '/memberships', headers=_h(token))
    assert response.status_code == 200, response.text
    return response.json()


def _private(store, rid):
    # include_events=False also skips room_bounds in the PG store, so tests can
    # observe the deliberately corrupted tombstone before fail-closed recovery.
    with store.read_transaction(rid, 'fixture-human', include_events=False) as (_, room):
        return copy.deepcopy(room)


def _provision_target(store):
    store.provision({'id': 'c', 'owner': 'fixture-owner', 'kind': 'agent',
                     'display_name': 'Cedar', 'handle': 'cedar'},
                    'fixture-c', AGENT_PERMISSIONS | SELF_PERMISSIONS)


def _credential(client, rid, key):
    response = client.post('/api/v1/agents/a/credentials',
                           json={'scopes': ['room:read', 'room:invite_agents'],
                                 'room_id': rid, 'expires_in': 3600},
                           headers={**_h('fixture-human'), 'Idempotency-Key': key})
    assert response.status_code == 200, response.text
    return response.json()


def _invite(client, rid, token, key, policy_revision=1, target='c'):
    member = _members(client, rid)['a']
    room = client.get('/api/v1/rooms/' + rid, headers=_h('fixture-human')).json()
    return client.post('/api/v1/rooms/' + rid + '/agent-invitations',
                       json={'target_id': target, 'expected_generation': room['generation'],
                             'expected_membership_epoch': member['epoch'],
                             'expected_membership_revision': member['revision'],
                             'expected_policy_revision': policy_revision},
                       headers={**_h(token), 'Idempotency-Key': key})


def _setup(client, store, rid, max_uses=1):
    _provision_target(store)
    member = _members(client, rid)['a']
    patched = client.patch('/api/v1/rooms/' + rid + '/memberships/a',
                           json={'expected_revision': member['revision'], 'status': 'active',
                                 'permissions': sorted(AGENT_PERMISSIONS | {'room:invite_agents'})},
                           headers={**_h('fixture-human'), 'Idempotency-Key': 'member-a-1'})
    assert patched.status_code == 200, patched.text
    credential = _credential(client, rid, 'cred-a-1')
    room = client.get('/api/v1/rooms/' + rid, headers=_h('fixture-human')).json()
    member = _members(client, rid)['a']
    grant_body = {'action': 'grant', 'inviter_id': 'a',
                  'expected_generation': room['generation'],
                  'expected_policy_revision': 0, 'target_ids': ['c'],
                  'inviter_epoch': member['epoch'], 'inviter_revision': member['revision'],
                  'expires_at': int(time.time()) + 600, 'max_uses': max_uses,
                  'acknowledge_topic_sharing': True}
    granted = client.post('/api/v1/rooms/' + rid + '/agent-invite-policy', json=grant_body,
                          headers={**_h('fixture-human'), 'Idempotency-Key': 'policy-1'})
    assert granted.status_code == 200, granted.text
    invited = _invite(client, rid, credential['token'], 'invite-c-1')
    assert invited.status_code == 200, invited.text
    return {'credential': credential, 'grant': granted.json(), 'a': _members(client, rid)['a'],
            'generation': room['generation']}


def _purge(client, rid, principal_id, revision, key='purge-c-1', token='fixture-human'):
    return client.post('/api/v1/rooms/' + rid + '/memberships/' + principal_id + '/purge',
                       json={'expected_revision': revision, 'acknowledge_cleanup': True},
                       headers={**_h(token), 'Idempotency-Key': key})


def _inject_history(store, rid, value):
    """Synthetic corruption injection that deliberately bypasses room_bounds."""
    if hasattr(store, 'rooms'):
        store.rooms[rid]['_membership_hwm'] = value
        return
    from psycopg.types.json import Jsonb
    with store.connect() as conn:
        conn.execute("UPDATE rooms SET payload = payload || %s WHERE id = %s",
                     (Jsonb({'_membership_hwm': value}), rid))


def _remove_history(store, rid):
    """Restore a genuine legacy room (the field itself is absent)."""
    if hasattr(store, 'rooms'):
        store.rooms[rid].pop('_membership_hwm', None)
        return
    with store.connect() as conn:
        conn.execute("UPDATE rooms SET payload = payload - '_membership_hwm' WHERE id = %s", (rid,))


def _regrant(client, rid, previous_revision=1, key='policy-2', targets=('c',)):
    """A fresh human approval is the only way to re-invite after cleanup."""
    room = client.get('/api/v1/rooms/' + rid, headers=_h('fixture-human')).json()
    member = _members(client, rid)['a']
    body = {'action': 'grant', 'inviter_id': 'a',
            'expected_generation': room['generation'],
            'expected_policy_revision': previous_revision, 'target_ids': list(targets),
            'inviter_epoch': member['epoch'], 'inviter_revision': member['revision'],
            'expires_at': int(time.time()) + 600, 'max_uses': 1,
            'acknowledge_topic_sharing': True}
    granted = client.post('/api/v1/rooms/' + rid + '/agent-invite-policy', json=body,
                          headers={**_h('fixture-human'), 'Idempotency-Key': key})
    assert granted.status_code == 200, granted.text
    return granted.json()


def _check_off_live_denied(store, rid):
    with _client(store) as client:
        _setup(client, store, rid)
    with _client(store, enabled=False) as off:
        denied = _purge(off, rid, 'c', 1)
        assert denied.status_code == 409, denied.text
        assert denied.json()['code'] == 'invitation_still_live'
        # Existing feature-off contract is unchanged: the live row stays hidden.
        assert off.get('/api/v1/agents/me/invitations', headers=_h('fixture-c')).json() == []
        assert _private(store, rid)['members']['c']['status'] == 'pending'


def _check_off_dead_purge(store, rid):
    with _client(store) as client:
        state = _setup(client, store, rid)
        revoked = client.post('/api/v1/agents/a/credentials/' + state['credential']['credential_id'] + '/revoke',
                              json={}, headers={**_h('fixture-human'), 'Idempotency-Key': 'revoke-cred'})
        assert revoked.status_code == 200, revoked.text
    with _client(store, enabled=False) as off:
        purged = _purge(off, rid, 'c', 1)
        assert purged.status_code == 200, purged.text
        body = purged.json()
        assert body['purged'] is True and body['principal_id'] == 'c'
        assert body['previous_epoch'] == 1 and body['previous_revision'] == 1
        assert 'c' not in _members(off, rid)
        private = _private(store, rid)
        assert private['_membership_hwm']['c'] == {'epoch': 1, 'revision': 1}
        assert any('invite-c-1' in key for key in private['keys'])
        with store.read_transaction(rid, 'fixture-human', include_events=True) as (_, full):
            event = next(e for e in full['events'] if e['type'] == 'membership.purged')
            assert event['principal_id'] == 'c' and event['previous_revision'] == 1
            assert 'reason' not in event
        # Tombstones stay private: neither room nor membership projections expose them.
        assert '_membership_hwm' not in off.get('/api/v1/rooms/' + rid,
                                                headers=_h('fixture-human')).json()
        replay = _purge(off, rid, 'c', 1)
        assert replay.status_code == 200 and replay.json() == body
    with _client(store) as client:
        new_credential = _credential(client, rid, 'cred-a-2')
        granted = _regrant(client, rid)
        invited = _invite(client, rid, new_credential['token'], 'invite-c-2',
                          policy_revision=granted['revision'])
        assert invited.status_code == 200, invited.text
        member = _members(client, rid)['c']
        assert (member['epoch'], member['revision']) == (2, 2)
        assert _private(store, rid)['_membership_hwm']['c'] == {'epoch': 1, 'revision': 1}


def _check_accepted_pending_purge(store, core, rid):
    with _client(store) as client:
        state = _setup(client, store, rid)
        room = client.get('/api/v1/rooms/' + rid, headers=_h('fixture-human')).json()
        accepted = client.post('/api/v1/rooms/' + rid + '/memberships/me/accept',
                               json={'expected_revision': 1, 'acknowledge_private': True},
                               headers={**_h('fixture-c'), 'Idempotency-Key': 'accept-c'})
        assert accepted.status_code == 200, accepted.text
        active = accepted.json()
        # An accepted row no longer carries the credential binding, so its chain can
        # be genuinely dead even before any cleanup request.
        assert 'credential_binding' not in _private(store, rid)['members']['c']['_agent_invite']
        session_id = '11111111-1111-4111-8111-111111111111'
        joined = core.participation('fixture-c', rid, 'join',
                                    {'session_id': session_id, 'generation': room['generation'],
                                     'membership_revision': active['revision']})
        old_cursor = core.events('fixture-c', None, target_room=rid,
                                 session_id=session_id)['next_cursor']
        pending = client.patch('/api/v1/rooms/' + rid + '/memberships/c',
                               json={'expected_revision': active['revision'], 'status': 'pending',
                                     'permissions': ['room:read']},
                               headers={**_h('fixture-human'), 'Idempotency-Key': 'member-c-pending'})
        assert pending.status_code == 200, pending.text
        row = _private(store, rid)['members']['c']
        purged = _purge(client, rid, 'c', row['revision'], key='purge-c-2')
        assert purged.status_code == 200, purged.text
        hwm = _private(store, rid)['_membership_hwm']['c']
        assert (hwm['epoch'], hwm['revision']) == (row['epoch'], row['revision'])
        invited = _invite(client, rid, state['credential']['token'], 'invite-c-2',
                          policy_revision=_regrant(client, rid)['revision'])
        assert invited.status_code == 200, invited.text
        member = _members(client, rid)['c']
        assert member['status'] == 'pending'
        assert member['epoch'] > row['epoch'] and member['revision'] > row['revision']
        # The old accept body/key must not be replayed into the new pending row.
        old_accept = client.post('/api/v1/rooms/' + rid + '/memberships/me/accept',
                                 json={'expected_revision': 1, 'acknowledge_private': True},
                                 headers={**_h('fixture-c'), 'Idempotency-Key': 'accept-c'})
        assert old_accept.status_code != 200, old_accept.text
        assert _members(client, rid)['c']['status'] == 'pending'
        new_revision = _members(client, rid)['c']['revision']
        reaccepted = client.post('/api/v1/rooms/' + rid + '/memberships/me/accept',
                                 json={'expected_revision': new_revision, 'acknowledge_private': True},
                                 headers={**_h('fixture-c'), 'Idempotency-Key': 'accept-c-new'})
        assert reaccepted.status_code == 200, reaccepted.text
        # Even after the recreated row is accepted, the old body/key stays rejected.
        old_again = client.post('/api/v1/rooms/' + rid + '/memberships/me/accept',
                                json={'expected_revision': 1, 'acknowledge_private': True},
                                headers={**_h('fixture-c'), 'Idempotency-Key': 'accept-c'})
        assert old_again.status_code >= 400, old_again.text
        # An old participation session/cursor cannot be revived against the new row.
        with pytest.raises(Problem):
            core.events('fixture-c', old_cursor, target_room=rid, session_id=session_id)
        with pytest.raises(Problem):
            core.participation('fixture-c', rid, 'ack',
                               {'session_id': session_id, 'generation': room['generation'],
                                'membership_revision': reaccepted.json()['revision'],
                                'cursor': old_cursor, 'state': 'read'})


def _check_accept_purge_race(store, rid, live):
    with _client(store) as client:
        state = _setup(client, store, rid)
        if not live:
            revoked = client.post('/api/v1/agents/a/credentials/' + state['credential']['credential_id'] + '/revoke',
                                  json={}, headers={**_h('fixture-human'), 'Idempotency-Key': 'revoke-cred'})
            assert revoked.status_code == 200, revoked.text
    outcomes = {}

    def accept_call():
        with _client(store) as client:
            outcomes['accept'] = client.post(
                '/api/v1/rooms/' + rid + '/memberships/me/accept',
                json={'expected_revision': 1, 'acknowledge_private': True},
                headers={**_h('fixture-c'), 'Idempotency-Key': 'race-accept'})

    def purge_call():
        with _client(store) as client:
            outcomes['purge'] = _purge(client, rid, 'c', 1, key='race-purge')

    threads = [threading.Thread(target=accept_call), threading.Thread(target=purge_call)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    accepted = outcomes['accept']
    purged = outcomes['purge']
    if live:
        assert accepted.status_code == 200, accepted.text
        assert purged.status_code == 409, purged.text
        assert purged.json()['code'] in {'invitation_still_live', 'purge_requires_pending', 'membership_conflict'}
        assert _private(store, rid)['members']['c']['status'] == 'active'
    else:
        assert purged.status_code == 200, purged.text
        assert accepted.status_code >= 400, accepted.text
        assert 'c' not in _private(store, rid)['members']


def _check_purge_idempotency_targets(store, core, rid):
    with _client(store) as client:
        state = _setup(client, store, rid)
        store.provision({'id': 'd', 'owner': 'fixture-owner', 'kind': 'agent',
                         'display_name': 'Dune', 'handle': 'dune'},
                        'fixture-d', AGENT_PERMISSIONS | SELF_PERMISSIONS)
        granted = _regrant(client, rid, previous_revision=1, key='policy-d', targets=('d',))
        invited = _invite(client, rid, state['credential']['token'], 'invite-d-1',
                          policy_revision=granted['revision'], target='d')
        assert invited.status_code == 200, invited.text
        revoked = client.post('/api/v1/agents/a/credentials/' + state['credential']['credential_id'] + '/revoke',
                              json={}, headers={**_h('fixture-human'), 'Idempotency-Key': 'revoke-cred'})
        assert revoked.status_code == 200, revoked.text
        first = _purge(client, rid, 'c', 1, key='shared-purge')
        assert first.status_code == 200, first.text
        # Same key/body for another target must not replay A's success.
        second = _purge(client, rid, 'd', 1, key='shared-purge')
        assert second.status_code == 409, second.text
        assert second.json()['code'] == 'idempotency_conflict'
        assert 'd' in _private(store, rid)['members']
        # The original retry still replays A's stored response.
        retry = _purge(client, rid, 'c', 1, key='shared-purge')
        assert retry.status_code == 200 and retry.json() == first.json()
        fresh = _purge(client, rid, 'd', 1, key='fresh-d')
        assert fresh.status_code == 200, fresh.text


def _check_legacy_lookup_collision(store, rid):
    with _client(store) as client:
        state = _setup(client, store, rid)
        revoked = client.post('/api/v1/agents/a/credentials/' + state['credential']['credential_id'] + '/revoke',
                              json={}, headers={**_h('fixture-human'), 'Idempotency-Key': 'revoke-cred'})
        assert revoked.status_code == 200, revoked.text
        actor_id = store.auth('fixture-human')['id']
        lookup = actor_id + ':memberships-purge:legacy-key'
        old_body = {'expected_revision': 1, 'acknowledge_cleanup': True}
        with store.transaction(rid, 'fixture-human') as (_, room):
            room['keys'][lookup] = {
                'digest': digest([actor_id, rid, 'memberships-purge', old_body]),
                'response': {'purged': True}, 'epoch': room['members'][actor_id]['epoch'],
                'response_until': time.time() + 600}
        denied = _purge(client, rid, 'c', 1, key='legacy-key')
        assert denied.status_code == 409, denied.text
        assert denied.json()['code'] == 'idempotency_conflict'
        assert 'c' in _private(store, rid)['members']


def _check_manage_only_forbidden(store, rid):
    with _client(store) as client:
        state = _setup(client, store, rid)
        store.provision({'id': 'h2', 'owner': 'fixture-owner', 'kind': 'human',
                         'display_name': 'H2', 'handle': 'h2'},
                        'fixture-h2', {'room:manage_members'})
        invited = client.post('/api/v1/rooms/' + rid + '/memberships',
                              json={'principal_id': 'h2', 'permissions': sorted(HUMAN_PERMISSIONS),
                                    'status': 'pending'},
                              headers={**_h('fixture-human'), 'Idempotency-Key': 'h2-member'})
        assert invited.status_code == 200, invited.text
        accepted = client.post('/api/v1/rooms/' + rid + '/memberships/me/accept',
                               json={'expected_revision': invited.json()['revision'],
                                     'acknowledge_private': True},
                               headers={**_h('fixture-h2'), 'Idempotency-Key': 'accept-h2'})
        assert accepted.status_code == 200, accepted.text
        revoked = client.post('/api/v1/agents/a/credentials/' + state['credential']['credential_id'] + '/revoke',
                              json={}, headers={**_h('fixture-human'), 'Idempotency-Key': 'revoke-cred'})
        assert revoked.status_code == 200, revoked.text
        denied = _purge(client, rid, 'c', 1, key='h2-purge', token='fixture-h2')
        assert denied.status_code == 403, denied.text
        assert 'c' in _private(store, rid)['members']


def _check_guards_and_capacity(store, core, rid):
    with _client(store) as client:
        # Body shape: no free-form reason, explicit acknowledgement, exact int.
        live = _setup(client, store, rid)
        for body, key in [
            ({'expected_revision': 1, 'acknowledge_cleanup': True, 'reason': 'x'}, 'bad-extra'),
            ({'expected_revision': 1, 'acknowledge_cleanup': False}, 'bad-ack'),
            ({'expected_revision': True, 'acknowledge_cleanup': True}, 'bad-bool'),
        ]:
            rejected = client.post('/api/v1/rooms/' + rid + '/memberships/c/purge', json=body,
                                   headers={**_h('fixture-human'), 'Idempotency-Key': key})
            assert rejected.status_code == 422, rejected.text
            assert rejected.json()['code'] == 'invalid_purge_fields'
        # Active rows are never purge candidates.
        active = _purge(client, rid, 'a', _members(client, rid)['a']['revision'], key='purge-a')
        assert active.status_code == 409, active.text
        assert active.json()['code'] == 'purge_requires_pending'
        # A non-human principal cannot run cleanup.
        agent = _purge(client, rid, 'a', _members(client, rid)['a']['revision'],
                       key='purge-agent', token='fixture-a')
        assert agent.status_code in {403, 404}, agent.text
        # Human-origin pending rows are not eligible either.
        new_room = core.create('fixture-human', {'topic': 'Synthetic cleanup guards'}, 'room-cleanup-guards')
        invited = client.post('/api/v1/rooms/' + new_room['id'] + '/memberships',
                              json={'principal_id': 'b', 'permissions': sorted(AGENT_PERMISSIONS),
                                    'status': 'pending'},
                              headers={**_h('fixture-human'), 'Idempotency-Key': 'human-invite-b'})
        assert invited.status_code == 200, invited.text
        human_origin = _purge(client, new_room['id'], 'b', invited.json()['revision'], key='purge-human')
        assert human_origin.status_code == 409, human_origin.text
        assert human_origin.json()['code'] == 'purge_human_origin_forbidden'
        # Kill the chain, then hit the bounded tombstone capacity.
        revoked = client.post('/api/v1/agents/a/credentials/' + live['credential']['credential_id'] + '/revoke',
                              json={}, headers={**_h('fixture-human'), 'Idempotency-Key': 'revoke-cred'})
        assert revoked.status_code == 200, revoked.text
        _inject_history(store, rid, {'p%d' % i: {'epoch': 1, 'revision': 1} for i in range(128)})
        full = _purge(client, rid, 'c', 1, key='purge-full')
        assert full.status_code == 429, full.text
        assert full.json()['code'] == 'membership_history_capacity'
        assert 'c' in _private(store, rid)['members']
        # Non-safe-integer or overflowing tombstones fail closed without deleting.
        _inject_history(store, rid, {'c': {'epoch': True, 'revision': 1}})
        malformed = _purge(client, rid, 'c', 1, key='purge-malformed')
        assert malformed.status_code in {409, 422}, malformed.text
        assert malformed.json()['code'] in {'invalid_membership_history', 'membership_hwm_bounds'}
        assert 'c' in _private(store, rid)['members']
        _inject_history(store, rid, {'c': {'epoch': MEMBERSHIP_ID_MAX, 'revision': 1}})
        overflow = _purge(client, rid, 'c', 1, key='purge-overflow')
        assert overflow.status_code == 429, overflow.text
        assert overflow.json()['code'] == 'membership_history_exhausted'
        assert 'c' in _private(store, rid)['members']
        # A human-created new member also outranks the tombstone.
        _inject_history(store, rid, {'c': {'epoch': 1, 'revision': 1}})
        purged = _purge(client, rid, 'c', 1, key='purge-c-3')
        assert purged.status_code == 200, purged.text
        replaced = client.post('/api/v1/rooms/' + rid + '/memberships',
                               json={'principal_id': 'c', 'permissions': sorted(AGENT_PERMISSIONS),
                                     'status': 'pending'},
                               headers={**_h('fixture-human'), 'Idempotency-Key': 'human-new-c'})
        assert replaced.status_code == 200, replaced.text
        assert (replaced.json()['epoch'], replaced.json()['revision']) == (2, 2)


def _check_purge_race(store, core, rid):
    with _client(store) as client:
        state = _setup(client, store, rid)
        revoked = client.post('/api/v1/agents/a/credentials/' + state['credential']['credential_id'] + '/revoke',
                              json={}, headers={**_h('fixture-human'), 'Idempotency-Key': 'revoke-cred'})
        assert revoked.status_code == 200, revoked.text
    results = []

    def run(key):
        try:
            core.purge_membership('fixture-human', rid, 'c', key,
                                  {'expected_revision': 1, 'acknowledge_cleanup': True})
            results.append(('ok', 200))
        except Problem as exc:
            results.append(('error', exc.status))

    threads = [threading.Thread(target=run, args=('race-' + str(i),)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [item[0] for item in results].count('ok') == 1, results
    assert 'c' not in _private(store, rid)['members']


def _check_malformed_history(store, core, rid):
    with _client(store) as client:
        state = _setup(client, store, rid)
        revoked = client.post('/api/v1/agents/a/credentials/' + state['credential']['credential_id'] + '/revoke',
                              json={}, headers={**_h('fixture-human'), 'Idempotency-Key': 'revoke-cred'})
        assert revoked.status_code == 200, revoked.text
        malformed = [None, {'c': None}, [], {'c': {}}, {'c': {'revision': 1}},
                     {'c': {'epoch': True, 'revision': 1}},
                     {'c': {'epoch': 1, 'revision': 1, 'extra': 2}}]
        for index, value in enumerate(malformed):
            _inject_history(store, rid, value)
            failed = _purge(client, rid, 'c', 1, key='purge-bad-' + str(index))
            # Memory fail-closes in Core; PG fail-closes earlier in _room/room_bounds.
            assert failed.status_code in {409, 422}, (value, failed.text)
            assert failed.json()['code'] in {'invalid_membership_history', 'membership_hwm_bounds'}, value
            assert 'c' in _private(store, rid)['members']
        # Legacy means the field is absent; explicit null is not legacy.
        _remove_history(store, rid)
        purged = _purge(client, rid, 'c', 1, key='purge-legacy-none')
        assert purged.status_code == 200, purged.text
        assert _private(store, rid)['_membership_hwm']['c'] == {'epoch': 1, 'revision': 1}
        # The same corruption must also fail closed on every membership-create path.
        for index, value in enumerate(({'c': None}, {'c': {}})):
            _inject_history(store, rid, value)
            blocked = client.post('/api/v1/rooms/' + rid + '/memberships',
                                  json={'principal_id': 'c', 'permissions': sorted(AGENT_PERMISSIONS),
                                        'status': 'pending'},
                                  headers={**_h('fixture-human'), 'Idempotency-Key': 'human-corrupt-' + str(index)})
            assert blocked.status_code in {409, 422}, blocked.text
            assert blocked.json()['code'] in {'invalid_membership_history', 'membership_hwm_bounds'}
        _inject_history(store, rid, {'c': {'epoch': 1, 'revision': 1}})
        replaced = client.post('/api/v1/rooms/' + rid + '/memberships',
                               json={'principal_id': 'c', 'permissions': sorted(AGENT_PERMISSIONS),
                                     'status': 'pending'},
                               headers={**_h('fixture-human'), 'Idempotency-Key': 'human-ok-c'})
        assert replaced.status_code == 200, replaced.text
        assert (replaced.json()['epoch'], replaced.json()['revision']) == (2, 2)


def test_cleanup_malformed_history_memory(fixture):
    _check_malformed_history(fixture[0], fixture[1], fixture[2])


def test_cleanup_malformed_history_postgres(postgres_fixture):
    _check_malformed_history(postgres_fixture[0], postgres_fixture[1], postgres_fixture[2])


def test_cleanup_off_live_denied_memory(fixture):
    _check_off_live_denied(fixture[0], fixture[2])


def test_cleanup_off_live_denied_postgres(postgres_fixture):
    _check_off_live_denied(postgres_fixture[0], postgres_fixture[2])


def test_cleanup_off_dead_purge_memory(fixture):
    _check_off_dead_purge(fixture[0], fixture[2])


def test_cleanup_off_dead_purge_postgres(postgres_fixture):
    _check_off_dead_purge(postgres_fixture[0], postgres_fixture[2])


def test_cleanup_accepted_pending_memory(fixture):
    _check_accepted_pending_purge(*fixture)


def test_cleanup_accepted_pending_postgres(postgres_fixture):
    _check_accepted_pending_purge(*postgres_fixture)


def test_cleanup_guards_memory(fixture):
    _check_guards_and_capacity(fixture[0], fixture[1], fixture[2])


def test_cleanup_guards_postgres(postgres_fixture):
    _check_guards_and_capacity(postgres_fixture[0], postgres_fixture[1], postgres_fixture[2])


def test_cleanup_idempotency_targets_memory(fixture):
    _check_purge_idempotency_targets(fixture[0], fixture[1], fixture[2])


def test_cleanup_idempotency_targets_postgres(postgres_fixture):
    _check_purge_idempotency_targets(postgres_fixture[0], postgres_fixture[1], postgres_fixture[2])


def test_cleanup_legacy_lookup_collision_memory(fixture):
    _check_legacy_lookup_collision(fixture[0], fixture[2])


def test_cleanup_manage_only_forbidden_memory(fixture):
    _check_manage_only_forbidden(fixture[0], fixture[2])


def test_cleanup_manage_only_forbidden_postgres(postgres_fixture):
    _check_manage_only_forbidden(postgres_fixture[0], postgres_fixture[2])


def test_cleanup_accept_purge_race_live_memory(fixture):
    _check_accept_purge_race(fixture[0], fixture[2], live=True)


def test_cleanup_accept_purge_race_dead_memory(fixture):
    _check_accept_purge_race(fixture[0], fixture[2], live=False)


def test_cleanup_accept_purge_race_live_postgres(postgres_fixture):
    _check_accept_purge_race(postgres_fixture[0], postgres_fixture[2], live=True)


def test_cleanup_accept_purge_race_dead_postgres(postgres_fixture):
    _check_accept_purge_race(postgres_fixture[0], postgres_fixture[2], live=False)


def test_cleanup_race_memory(fixture):
    _check_purge_race(fixture[0], fixture[1], fixture[2])


def test_cleanup_race_postgres(postgres_fixture):
    _check_purge_race(postgres_fixture[0], postgres_fixture[1], postgres_fixture[2])
