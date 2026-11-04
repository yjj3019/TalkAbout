"""Synthetic-only opt-in invitation and membership contracts."""
import copy
import json
import time

import pytest
from fastapi.testclient import TestClient

from talkabout.api import create_app
from talkabout.domain import AGENT_PERMISSIONS, HUMAN_PERMISSIONS, SELF_PERMISSIONS, Problem


def check_membership_noop(store, core, rid):
    room = core.read('fixture-human', rid)
    task = next(t for t in room['tasks'] if t['state'] == 'queued')
    pid = task['assignee']
    member = room['members'][pid]
    token = 'fixture-' + pid
    core.participation(token, rid, 'join', {
        'session_id': '11111111-1111-4111-8111-111111111111',
        'generation': room['generation'], 'membership_revision': member['revision'],
    })
    core.mutation(token, rid, 'tasks/' + task['id'] + '/claim', 'noop-claim', {
        'generation': room['generation'], 'membership_revision': member['revision'],
    })
    with store.read_transaction(rid, 'fixture-human') as (_, original):
        before = copy.deepcopy(original)
    assert '_participation' in before['members'][pid]
    body = {'expected_revision': member['revision'], 'status': 'active',
            'permissions': list(reversed(member['permissions']))}
    first = core.mutation('fixture-human', rid, 'memberships/' + pid, 'noop-member', body)
    assert not any(k.startswith('_') for k in first)
    assert first['revision'] == member['revision']
    assert core.mutation('fixture-human', rid, 'memberships/' + pid, 'noop-member', body) == first
    with store.read_transaction(rid, 'fixture-human') as (_, current):
        assert current['members'] == before['members']
        assert current['tasks'] == before['tasks']
        assert current['events'] == before['events']
        assert current['last_seq'] == before['last_seq']
        assert len(current['keys']) == len(before['keys']) + 1
    with pytest.raises(Problem, match='membership_conflict'):
        core.mutation('fixture-human', rid, 'memberships/' + pid, 'stale-noop',
                      body | {'expected_revision': member['revision'] - 1})
    changed = body | {'permissions': [p for p in body['permissions'] if p != 'room:speak']}
    core.mutation('fixture-human', rid, 'memberships/' + pid, 'real-change', changed)
    with store.read_transaction(rid, 'fixture-human') as (_, after):
        assert after['tasks'][task['id']]['state'] == 'cancelled'
        assert after['members'][pid]['revision'] == member['revision'] + 1


def test_membership_noop_memory(fixture):
    check_membership_noop(*fixture)


def test_membership_noop_postgres(postgres_fixture):
    check_membership_noop(*postgres_fixture)


def _client(store, enabled=True):
    return TestClient(create_app(store, start_scheduler=False, allow_fixture_bearer=True,
                                 enable_agent_invites=enabled))


def _h(token):
    return {'Authorization': 'Bearer ' + token}


def _provision_target(store):
    store.provision({'id': 'c', 'owner': 'fixture-owner', 'kind': 'agent',
                     'display_name': 'Cedar', 'handle': 'cedar'},
                    'fixture-c', AGENT_PERMISSIONS | SELF_PERMISSIONS)


def _members(client, rid, token='fixture-human'):
    response = client.get('/api/v1/rooms/' + rid + '/memberships', headers=_h(token))
    assert response.status_code == 200, response.text
    return response.json()


def _grant_and_invite(client, store, rid):
    """Human grants one small delegation, then the room-bound agent invites."""
    _provision_target(store)
    member = _members(client, rid)['a']
    patched = client.patch('/api/v1/rooms/' + rid + '/memberships/a',
                           json={'expected_revision': member['revision'], 'status': 'active',
                                 'permissions': sorted(AGENT_PERMISSIONS | {'room:invite_agents'})},
                           headers={**_h('fixture-human'), 'Idempotency-Key': 'member-a-1'})
    assert patched.status_code == 200, patched.text
    credential = client.post('/api/v1/agents/a/credentials',
                             json={'scopes': ['room:read', 'room:invite_agents'],
                                   'room_id': rid, 'expires_in': 3600},
                             headers={**_h('fixture-human'), 'Idempotency-Key': 'cred-a-1'})
    assert credential.status_code == 200, credential.text
    token = credential.json()['token']
    member = _members(client, rid)['a']
    room = client.get('/api/v1/rooms/' + rid, headers=_h('fixture-human')).json()
    grant_body = {'action': 'grant', 'inviter_id': 'a',
                  'expected_generation': room['generation'],
                  'expected_policy_revision': 0, 'target_ids': ['c'],
                  'inviter_epoch': member['epoch'],
                  'inviter_revision': member['revision'],
                  'expires_at': int(time.time()) + 600, 'max_uses': 1,
                  'acknowledge_topic_sharing': True}
    granted = client.post('/api/v1/rooms/' + rid + '/agent-invite-policy',
                          json=grant_body,
                          headers={**_h('fixture-human'), 'Idempotency-Key': 'policy-1'})
    assert granted.status_code == 200, granted.text
    # Rejections must not consume the approved delegation.
    def _reject_reason(client, target, key, token=token):
        response = client.post('/api/v1/rooms/' + rid + '/agent-invitations',
                               json={'target_id': target, 'expected_generation': room['generation'],
                                     'expected_membership_epoch': member['epoch'],
                                     'expected_membership_revision': member['revision'],
                                     'expected_policy_revision': granted.json()['revision']},
                               headers={**_h(token), 'Idempotency-Key': key})
        assert response.status_code in {403, 404, 422}, response.text
    _reject_reason(client, 'a', 'rej-self')
    _reject_reason(client, 'b', 'rej-human')
    _reject_reason(client, 'zz', 'rej-missing')
    store.provision({'id': 'x', 'owner': 'fixture-other', 'kind': 'agent',
                     'display_name': 'Xavier', 'handle': 'xavier'},
                    'fixture-x', AGENT_PERMISSIONS)
    _reject_reason(client, 'x', 'rej-cross-workspace')
    other = client.post('/api/v1/rooms', json={'topic': 'Other synthetic room'},
                        headers={**_h('fixture-human'), 'Idempotency-Key': 'room-other'})
    assert other.status_code == 200, other.text
    cross_cred = client.post('/api/v1/agents/a/credentials',
                             json={'scopes': ['room:read', 'room:invite_agents'],
                                   'room_id': other.json()['id'], 'expires_in': 300},
                             headers={**_h('fixture-human'), 'Idempotency-Key': 'cred-a-cross'})
    assert cross_cred.status_code == 200, cross_cred.text
    rejected = client.post('/api/v1/rooms/' + rid + '/agent-invitations',
                           json={'target_id': 'c', 'expected_generation': room['generation'],
                                 'expected_membership_epoch': member['epoch'],
                                 'expected_membership_revision': member['revision'],
                                 'expected_policy_revision': granted.json()['revision']},
                           headers={**_h(cross_cred.json()['token']), 'Idempotency-Key': 'rej-cross-room'})
    assert rejected.status_code in {403, 404}, rejected.text
    invited = client.post('/api/v1/rooms/' + rid + '/agent-invitations',
                          json={'target_id': 'c', 'expected_generation': room['generation'],
                                'expected_membership_epoch': member['epoch'],
                                'expected_membership_revision': member['revision'],
                                'expected_policy_revision': granted.json()['revision']},
                          headers={**_h(token), 'Idempotency-Key': 'invite-c-1'})
    assert invited.status_code == 200, invited.text
    return credential.json(), room['generation'], invited.json(), granted.json(), grant_body


def _accept(client, rid, token='fixture-c', key='accept-c'):
    return client.post('/api/v1/rooms/' + rid + '/memberships/me/accept',
                       json={'expected_revision': 1, 'acknowledge_private': True},
                       headers={**_h(token), 'Idempotency-Key': key})


def _private(store, rid):
    """Private room payload is only inspectable for the synthetic memory store."""
    return (getattr(store, 'rooms', None) or {}).get(rid)


def _check_happy(store, core, rid):
    with _client(store) as client:
        credential, generation, ack, policy_ack, grant_body = _grant_and_invite(client, store, rid)
        assert ack['accepted'] is True and ack['status'] == 'pending'
        members = _members(client, rid)
        assert members['c']['status'] == 'pending'
        assert sorted(members['c']['permissions']) == ['room:read']
        assert not any(name.startswith('_') for name in members['c'])
        payload = _private(store, rid)
        if payload is not None:
            marker = payload['members']['c']['_agent_invite']
            assert marker['inviter_id'] == 'a' and marker['policy_revision'] == 1

        me = client.get('/api/v1/rooms/' + rid + '/agent-invite-policy/me',
                        headers=_h(credential['token']))
        assert me.status_code == 200, me.text
        assert me.json()['remaining'] == 0 and me.json()['revision'] == 1
        # An invite-only credential must not read the approved allowlist.
        invite_only = client.post('/api/v1/agents/a/credentials',
                                  json={'scopes': ['room:invite_agents'], 'room_id': rid,
                                        'expires_in': 300},
                                  headers={**_h('fixture-human'), 'Idempotency-Key': 'cred-a-2'})
        assert invite_only.status_code == 200, invite_only.text
        assert client.get('/api/v1/rooms/' + rid + '/agent-invite-policy/me',
                          headers=_h(invite_only.json()['token'])).status_code == 403
        assert client.get('/api/v1/rooms/' + rid + '/agent-invite-policies',
                          headers=_h(credential['token'])).status_code == 403

        replay_policy = client.post('/api/v1/rooms/' + rid + '/agent-invite-policy',
                                    json=grant_body,
                                    headers={**_h('fixture-human'), 'Idempotency-Key': 'policy-1'})
        assert replay_policy.status_code == 200, replay_policy.text
        assert replay_policy.json() == policy_ack

        envelope = client.get('/api/v1/agents/me/invitations', headers=_h('fixture-c'))
        assert envelope.status_code == 200, envelope.text
        assert [item['membership_id'] for item in envelope.json()] == ['c']
        assert envelope.json()[0]['topic'] == 'Synthetic neutral topic'
        assert envelope.json()[0]['membership_revision'] == 1
        assert client.get('/api/v1/rooms/' + rid, headers=_h('fixture-c')).status_code == 404

        accepted = _accept(client, rid)
        assert accepted.status_code == 200, accepted.text
        body = accepted.json()
        assert body['status'] == 'active' and body['permissions'] == ['room:read']
        assert not any(name.startswith('_') for name in body)
        assert _members(client, rid)['c']['status'] == 'active'
        core.tick('fixture-human', rid)
        tasks = core.read('fixture-human', rid).get('tasks') or []
        if isinstance(tasks, dict):
            tasks = list(tasks.values())
        assert all(task.get('assignee_id') != 'c' and task.get('claimed_by') != 'c' for task in tasks)
        if payload is not None:
            payload = _private(store, rid)
            marker = payload['members']['c']['_agent_invite']
            assert marker['inviter_id'] == 'a' and 'credential_binding' not in marker
            assert marker['accepted_generation'] == generation

        upgrade = sorted(AGENT_PERMISSIONS - {'room:contribute'})
        blocked = client.patch('/api/v1/rooms/' + rid + '/memberships/c',
                               json={'expected_revision': body['revision'], 'status': 'active',
                                     'permissions': upgrade}, headers={**_h('fixture-human'), 'Idempotency-Key': 'member-c-1'})
        assert blocked.status_code == 422, blocked.text
        granted = client.patch('/api/v1/rooms/' + rid + '/memberships/c',
                               json={'expected_revision': body['revision'], 'status': 'active',
                                     'permissions': upgrade, 'acknowledge_external_execution': True},
                               headers={**_h('fixture-human'), 'Idempotency-Key': 'member-c-2'})
        assert granted.status_code == 200, granted.text
        assert granted.json()['permissions'] == upgrade

        replay = _accept(client, rid)
        assert replay.status_code == 200 and replay.json() == body
        # A credential without the read scope cannot replay the stored
        # acceptance, even for an already-active member.
        weak = client.post('/api/v1/agents/c/credentials',
                           json={'scopes': ['agent:self_read'], 'expires_in': 300},
                           headers={**_h('fixture-human'), 'Idempotency-Key': 'cred-c-weak'})
        assert weak.status_code == 200, weak.text
        denied = _accept(client, rid, token=weak.json()['token'], key='accept-c')
        assert denied.status_code == 403, denied.text
        assert denied.json()['code'] == 'invitation_unauthorised'

        if payload is not None:
            payload = _private(store, rid)
            blob = json.dumps(payload['keys'], ensure_ascii=False, default=str)
            assert '_agent_invite' not in blob and 'credential_binding' not in blob


def _check_topic_change(store, core, rid):
    with _client(store) as client:
        _, generation, _, _, grant_body = _grant_and_invite(client, store, rid)
        room = core.read('fixture-human', rid)
        requested = core.mutation('fixture-human', rid, 'controls', 'topic-' + str(room['room_revision']),
                                  {'action': 'change_topic', 'expected_revision': room['room_revision'],
                                   'parameters': {'topic': 'Synthetic replacement topic'}})
        core.mutation('fixture-human', rid, 'controls/' + requested['id'] + '/approve', 'topic-ok', {})
        assert core.read('fixture-human', rid)['generation'] == generation + 1
        assert client.get('/api/v1/agents/me/invitations', headers=_h('fixture-c')).json() == []
        assert _accept(client, rid).status_code == 403
        assert _members(client, rid)['c']['status'] == 'pending'
        stale = client.post('/api/v1/rooms/' + rid + '/agent-invite-policy', json=grant_body,
                            headers={**_h('fixture-human'), 'Idempotency-Key': 'policy-1'})
        assert stale.status_code == 409, stale.text


def _check_generation_mode(store, core, rid):
    with _client(store) as client:
        _, generation, _, _, _ = _grant_and_invite(client, store, rid)
        room = core.read('fixture-human', rid)
        requested = core.mutation('fixture-human', rid, 'controls', 'mode-' + str(room['room_revision']),
                                  {'action': 'change_mode', 'expected_revision': room['room_revision'],
                                   'parameters': {'mode': 'co_creation'}})
        core.mutation('fixture-human', rid, 'controls/' + requested['id'] + '/approve', 'mode-ok', {})
        assert core.read('fixture-human', rid)['generation'] == generation + 1
        assert client.get('/api/v1/agents/me/invitations', headers=_h('fixture-c')).json() == []
        assert _accept(client, rid).status_code == 403
        assert _members(client, rid)['c']['status'] == 'pending'


def _check_credential_revoke(store, rid):
    with _client(store) as client:
        credential, _, _, _, _ = _grant_and_invite(client, store, rid)
        assert client.get('/api/v1/agents/me/invitations', headers=_h('fixture-c')).json()
        revoked = client.post('/api/v1/agents/a/credentials/' + credential['credential_id'] + '/revoke',
                              json={}, headers={**_h('fixture-human'), 'Idempotency-Key': 'revoke-cred'})
        assert revoked.status_code == 200, revoked.text
        assert client.get('/api/v1/agents/me/invitations', headers=_h('fixture-c')).json() == []
        assert _accept(client, rid).status_code == 403
        assert _members(client, rid)['c']['status'] == 'pending'


def _check_policy_revoke(store, rid):
    with _client(store) as client:
        _, _, _, _, _ = _grant_and_invite(client, store, rid)
        latest = client.get('/api/v1/rooms/' + rid, headers=_h('fixture-human')).json()
        body = {'action': 'revoke', 'inviter_id': 'a',
                'expected_generation': latest['generation'], 'expected_policy_revision': 1}
        revoked = client.post('/api/v1/rooms/' + rid + '/agent-invite-policy', json=body,
                              headers={**_h('fixture-human'), 'Idempotency-Key': 'revoke-1'})
        assert revoked.status_code == 200, revoked.text
        assert client.get('/api/v1/agents/me/invitations', headers=_h('fixture-c')).json() == []
        assert _accept(client, rid).status_code == 403
        assert _members(client, rid)['c']['status'] == 'pending'
        replay = client.post('/api/v1/rooms/' + rid + '/agent-invite-policy', json=body,
                             headers={**_h('fixture-human'), 'Idempotency-Key': 'revoke-1'})
        assert replay.status_code == 200 and replay.json() == revoked.json()


def _check_feature_off(store, core, rid):
    with _client(store) as client:
        credential, _, _, _, _ = _grant_and_invite(client, store, rid)
    with _client(store, enabled=False) as off:
        assert off.get('/api/v1/rooms/' + rid + '/agent-invite-policy/me',
                       headers=_h(credential['token'])).status_code == 404
        assert off.get('/api/v1/agents/me/invitations', headers=_h('fixture-c')).json() == []
        denied = off.post('/api/v1/rooms/' + rid + '/memberships/me/accept',
                          json={'expected_revision': 1, 'acknowledge_private': True},
                          headers={**_h('fixture-c'), 'Idempotency-Key': 'accept-off'})
        assert denied.status_code == 404
        # Human-origin pending invitations keep their existing behavior.
        room = core.create('fixture-human', {'topic': 'Synthetic human invite room'}, 'room-human-invite')
        invited = off.post('/api/v1/rooms/' + room['id'] + '/memberships',
                           json={'principal_id': 'b', 'permissions': sorted(AGENT_PERMISSIONS),
                                 'status': 'pending'}, headers={**_h('fixture-human'), 'Idempotency-Key': 'human-invite-b'})
        assert invited.status_code == 200, invited.text
        listed = off.get('/api/v1/agents/me/invitations', headers=_h('fixture-b')).json()
        assert [item['room_id'] for item in listed] == [room['id']]


def _check_expired_grant_replay(store, rid):
    with _client(store) as client:
        _, _, _, _, _ = _grant_and_invite(client, store, rid)
        member = _members(client, rid)['a']
        room = client.get('/api/v1/rooms/' + rid, headers=_h('fixture-human')).json()
        body = {'action': 'grant', 'inviter_id': 'a', 'expected_generation': room['generation'],
                'expected_policy_revision': 1, 'target_ids': ['c'],
                'inviter_epoch': member['epoch'], 'inviter_revision': member['revision'],
                'expires_at': int(time.time()) + 1, 'max_uses': 1,
                'acknowledge_topic_sharing': True}
        granted = client.post('/api/v1/rooms/' + rid + '/agent-invite-policy', json=body,
                              headers={**_h('fixture-human'), 'Idempotency-Key': 'policy-2'})
        assert granted.status_code == 200, granted.text
        time.sleep(1.2)
        expired = client.post('/api/v1/rooms/' + rid + '/agent-invite-policy', json=body,
                              headers={**_h('fixture-human'), 'Idempotency-Key': 'policy-2'})
        assert expired.status_code == 403, expired.text
        assert expired.json()['code'] == 'invitation_policy_unavailable'
        assert client.get('/api/v1/agents/me/invitations', headers=_h('fixture-c')).json() == []
        assert _accept(client, rid).status_code == 403


def test_agent_invite_expired_grant_replay_memory(fixture):
    _check_expired_grant_replay(fixture[0], fixture[2])


def test_agent_invite_expired_grant_replay_postgres(postgres_fixture):
    _check_expired_grant_replay(postgres_fixture[0], postgres_fixture[2])


def test_agent_invite_happy_memory(fixture):
    _check_happy(*fixture)


def test_agent_invite_topic_change_memory(fixture):
    _check_topic_change(*fixture)


def test_agent_invite_generation_mode_memory(fixture):
    _check_generation_mode(*fixture)


def test_agent_invite_credential_revoke_memory(fixture):
    _check_credential_revoke(fixture[0], fixture[2])


def test_agent_invite_policy_revoke_memory(fixture):
    _check_policy_revoke(fixture[0], fixture[2])


def test_agent_invite_topic_change_postgres(postgres_fixture):
    _check_topic_change(*postgres_fixture)


def test_agent_invite_generation_mode_postgres(postgres_fixture):
    _check_generation_mode(*postgres_fixture)


def test_agent_invite_credential_revoke_postgres(postgres_fixture):
    _check_credential_revoke(postgres_fixture[0], postgres_fixture[2])


def test_agent_invite_policy_revoke_postgres(postgres_fixture):
    _check_policy_revoke(postgres_fixture[0], postgres_fixture[2])


def test_agent_invite_concurrent_accept_memory(fixture):
    import threading
    store, core, rid = fixture
    with _client(store) as client:
        _grant_and_invite(client, store, rid)
    results = []

    def run(key):
        with _client(store) as client:
            response = client.post('/api/v1/rooms/' + rid + '/memberships/me/accept',
                                   json={'expected_revision': 1, 'acknowledge_private': True},
                                   headers={**_h('fixture-c'), 'Idempotency-Key': key})
            results.append(response.status_code)
    threads = [threading.Thread(target=run, args=(key,)) for key in ('concurrent-1', 'concurrent-2')]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [200, 409], results
    members = None
    with _client(store) as client:
        members = _members(client, rid)
    assert members['c']['status'] == 'active'


def _check_policy_get_access(store, rid):
    with _client(store) as client:
        credential, *_ = _grant_and_invite(client, store, rid)
        me = client.get('/api/v1/rooms/' + rid + '/agent-invite-policy/me',
                        headers=_h(credential['token']))
        assert me.status_code == 200, me.text
        assert set(me.json()) == {'revision', 'target_ids', 'expires_at', 'remaining',
                                  'membership_epoch', 'membership_revision', 'generation'}
        assert me.json()['remaining'] == 0 and 'events' not in me.json()
        # The agent view is agent-only, and the admin view is human-only.
        assert client.get('/api/v1/rooms/' + rid + '/agent-invite-policy/me',
                          headers=_h('fixture-human')).status_code == 403
        policies = client.get('/api/v1/rooms/' + rid + '/agent-invite-policies',
                              headers=_h('fixture-human'))
        assert policies.status_code == 200 and policies.json()[0]['revision'] == 1
        # read_transaction is room-scoped, so cross-workspace must be explicitly not found.
        store.provision({'id': 'h2', 'owner': 'fixture-other', 'kind': 'human',
                         'display_name': 'Other', 'handle': 'other'}, 'fixture-h2', HUMAN_PERMISSIONS)
        assert client.get('/api/v1/rooms/' + rid + '/agent-invite-policies',
                          headers=_h('fixture-h2')).status_code == 404
        assert client.get('/api/v1/agents/me/invitations', headers=_h('fixture-h2')).json() == []


def _check_off_retention(store, rid):
    with _client(store) as client:
        member = _members(client, rid)['a']
        granted = client.patch('/api/v1/rooms/' + rid + '/memberships/a',
                               json={'expected_revision': member['revision'], 'status': 'active',
                                     'permissions': ['room:read', 'room:invite_agents']},
                               headers={**_h('fixture-human'), 'Idempotency-Key': 'off-grant-a'})
        assert granted.status_code == 200, granted.text
    store.provision({'id': 'd', 'owner': 'fixture-owner', 'kind': 'agent',
                     'display_name': 'Dana', 'handle': 'dana'}, 'fixture-d', AGENT_PERMISSIONS)
    with _client(store, enabled=False) as off:
        current = _members(off, rid)['a']
        retained = off.patch('/api/v1/rooms/' + rid + '/memberships/a',
                             json={'expected_revision': current['revision'], 'status': 'active',
                                   'permissions': sorted(AGENT_PERMISSIONS | {'room:invite_agents'})},
                             headers={**_h('fixture-human'), 'Idempotency-Key': 'off-retain-a'})
        assert retained.status_code == 200, retained.text
        assert 'room:invite_agents' in retained.json()['permissions']
        assert 'room:contribute' in retained.json()['permissions']
        b = _members(off, rid)['b']
        denied_b = off.patch('/api/v1/rooms/' + rid + '/memberships/b',
                             json={'expected_revision': b['revision'], 'status': 'active',
                                   'permissions': sorted(AGENT_PERMISSIONS | {'room:invite_agents'})},
                             headers={**_h('fixture-human'), 'Idempotency-Key': 'off-add-b'})
        assert denied_b.status_code == 422, denied_b.text
        denied_d = off.patch('/api/v1/rooms/' + rid + '/memberships/d',
                             json={'permissions': ['room:read', 'room:invite_agents'],
                                   'status': 'pending'},
                             headers={**_h('fixture-human'), 'Idempotency-Key': 'off-add-d'})
        assert denied_d.status_code == 422, denied_d.text
        after = _members(off, rid)
        assert 'room:invite_agents' not in after['b']['permissions'] and 'd' not in after
        # Actual invite/policy operations stay feature-disabled while off.
        disabled = off.post('/api/v1/rooms/' + rid + '/agent-invitations',
                            json={'target_id': 'd', 'expected_generation': 1,
                                  'expected_membership_epoch': 0,
                                  'expected_membership_revision': 0},
                            headers={**_h('fixture-human'), 'Idempotency-Key': 'off-invite'})
        assert disabled.status_code == 404
        assert off.get('/api/v1/rooms/' + rid + '/agent-invite-policy/me',
                       headers=_h('fixture-human')).status_code == 404


def test_agent_invite_policy_get_access_memory(fixture):
    _check_policy_get_access(fixture[0], fixture[2])


def test_agent_invite_off_retention_memory(fixture):
    _check_off_retention(fixture[0], fixture[2])


def test_agent_invite_policy_get_access_postgres(postgres_fixture):
    _check_policy_get_access(postgres_fixture[0], postgres_fixture[2])


def test_agent_invite_off_retention_postgres(postgres_fixture):
    _check_off_retention(postgres_fixture[0], postgres_fixture[2])


def test_agent_invite_feature_off_memory(fixture):
    _check_feature_off(*fixture)


def test_agent_invite_happy_postgres(postgres_fixture):
    _check_happy(*postgres_fixture)


def test_agent_invite_stale_and_revoked_postgres(postgres_fixture):
    _check_topic_change(*postgres_fixture)


def test_agent_invite_feature_off_postgres(postgres_fixture):
    _check_feature_off(*postgres_fixture)
