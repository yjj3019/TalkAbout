import io
import json
import threading
import time

import pytest

from talkabout.mcp import MCPServer
from talkabout.runtime import RuntimeTimeout, bounded_call, validate_adapter_policy
from talkabout.worker import Journal, SyntheticRuntime, Worker
from conftest import LocalClient
from mcp_contract import EXPECTED_INPUTS, assert_tool_contract


def ready(server):
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "fixture", "version": "1"}}})
    assert response["result"]["protocolVersion"] == "2025-11-25"
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_mcp_lifecycle_tools_and_no_secret_arguments(fixture):
    store, _, rid = fixture
    server = MCPServer(LocalClient(store))
    assert server.handle({"jsonrpc": "2.0", "id": 0, "method": "tools/list"})["error"]["code"] == -32000
    ready(server)
    tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assert_tool_contract(tools)
    rejected = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "read_room", "arguments": {"room_id": rid, "token": "fixture-forbidden"}}})
    assert rejected["error"]["code"] == -32602
    result = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "read_room", "arguments": {"room_id": rid}}})
    assert not result["result"]["isError"]


@pytest.mark.parametrize('name', EXPECTED_INPUTS)
def test_mcp_input_contract_rejected_before_api(name):
    class NoRequests:
        def __init__(self): self.calls = []
        def request(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            raise AssertionError('invalid MCP input reached API')
    client = NoRequests()
    server = MCPServer(client)
    ready(server)
    fields, optional = EXPECTED_INPUTS[name]
    values = {'string': 'fixture-value', 'integer': 1, 'boolean': True}
    arguments = {key: values[kind] for key, kind in fields.items() if key not in optional}
    def rejected(args):
        response = server.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                                  'params': {'name': name, 'arguments': args}})
        assert response['error']['code'] == -32602
        assert not client.calls
    # The configured credential supplies identity and authority, never tool input.
    for injected in ('token', 'actor_id', 'role', 'provider', 'credential_id'):
        rejected({**arguments, injected: 'fixture-forbidden'})
    for required in set(fields) - optional:
        rejected({key: value for key, value in arguments.items() if key != required})
    for key, kind in fields.items():
        # bool is an int subclass in Python, but is not an MCP integer.
        rejected({**arguments, key: True if kind == 'integer' else 1})


@pytest.mark.parametrize('field,value', [
    ('reply_to', ''), ('reply_to', 'x' * 129), ('reply_to', None),
    ('mentions', None), ('mentions', 'agent'), ('mentions', {}),
    ('mentions', ['a'] * 2), ('mentions', ['a', 'b', 'c', 'd', 'e']),
    ('mentions', ['']), ('mentions', ['x' * 129]), ('mentions', [1]),
    ('mentions', [None]), ('mentions', [['a']]),
])
def test_mcp_post_message_invalid_metadata_is_rejected_before_api(field, value):
    class NoRequests:
        def request(self, *args, **kwargs):
            raise AssertionError('invalid MCP input reached API')
    server = MCPServer(NoRequests())
    ready(server)
    body = {'room_id': 'room', 'session_id': 'session', 'generation': 1,
            'membership_revision': 1, 'task_id': 'task', 'attempt': 1,
            'text': 'synthetic', 'client_mutation_id': 'result', field: value}
    response = server.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                              'params': {'name': 'post_message', 'arguments': body}})
    assert response['error']['code'] == -32602


def test_mcp_post_metadata_boundary_forwarding_and_legacy():
    class CapturingClient:
        def __init__(self): self.calls = []
        def request(self, *args):
            self.calls.append(args)
            return {'ok': True}
    client = CapturingClient()
    server = MCPServer(client)
    ready(server)
    body = {'room_id': 'room', 'session_id': 'session', 'generation': 1,
            'membership_revision': 1, 'task_id': 'task', 'attempt': 1,
            'text': 'synthetic', 'client_mutation_id': 'result'}
    def call(arguments):
        return server.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                              'params': {'name': 'post_message', 'arguments': arguments}})
    assert not call(body)['result']['isError']
    legacy = client.calls[-1]
    assert legacy[1] == 'rooms/room/tasks/task/complete'
    assert legacy[2]['result'] == {'client_message_id': 'result', 'text': 'synthetic'}
    metadata = {'reply_to': 'r' * 128, 'mentions': ['a', 'b', 'c', 'm' * 128]}
    assert not call(body | metadata)['result']['isError']
    assert client.calls[-1][2]['result'] == legacy[2]['result'] | metadata
    assert client.calls[-1][3] == legacy[3]


def _mcp_claimed_turn(fixture):
    store, core, rid = fixture
    source = core.mutation('fixture-human', rid, 'messages', 'source',
                           {'kind': 'question', 'text': 'synthetic source', 'client_message_id': 'source'})
    client = LocalClient(store)
    server = MCPServer(client)
    ready(server)
    def call(name, **arguments):
        return server.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                              'params': {'name': name, 'arguments': arguments}})
    context = {'room_id': rid, 'session_id': 'c18d8c6f-05b4-4c12-b609-d43ed96645bb',
               'generation': 1, 'membership_revision': 2}
    joined = call('join_room', **context)['result']
    assert not joined['isError']
    room = json.loads(joined['content'][0]['text'])['room']
    task = next(t for t in room['tasks'] if t['state'] == 'queued')
    claimed = call('claim_turn', **context, task_id=task['id'], client_mutation_id='claim')['result']
    assert not claimed['isError']
    attempt = json.loads(claimed['content'][0]['text'])['attempt']
    body = {**context, 'task_id': task['id'], 'attempt': attempt,
            'text': 'synthetic reply', 'client_mutation_id': 'result'}
    return store, core, rid, source, client, call, body


def test_mcp_reply_mentions_complete_replay_and_no_auto_wake(fixture):
    store, core, rid, source, client, call, body = _mcp_claimed_turn(fixture)
    before = core.read('fixture-human', rid)
    result = call('post_message', **(body | {'reply_to': source['id'], 'mentions': ['b']}))
    assert not result['result']['isError']
    saved = json.loads(result['result']['content'][0]['text'])
    assert saved['message']['reply_to'] == source['id']
    assert saved['message']['mentions'] == ['b']
    assert call('post_message', **(body | {'reply_to': source['id'], 'mentions': ['b']}))['result'] == result['result']
    assert call('post_message', **(body | {'reply_to': source['id'], 'mentions': []}))['result']['isError']
    after = core.read('fixture-human', rid)
    # Completion can advance the room's ordinary scheduler. Explicit metadata
    # must not add work beyond a matching completion without reply/mentions.
    from conftest import build_fixture
    from talkabout.store import MemoryStore
    _, baseline_core, baseline_rid, _, baseline_client, baseline_call, baseline_body = _mcp_claimed_turn(build_fixture(MemoryStore()))
    assert not baseline_call('post_message', **baseline_body)['result']['isError']
    baseline = baseline_core.read('fixture-human', baseline_rid)
    shape = lambda room: sorted((t['assignee'], t['purpose'], t['state'], t['generation']) for t in room['tasks'])
    assert shape(after) == shape(baseline)
    assert len(after['messages']) == len(before['messages']) + 1
    assert not any('runtime' in str(path) for _, path in client.calls + baseline_client.calls)


@pytest.mark.parametrize('case', ['other_room', 'hidden', 'missing', 'human', 'inactive', 'unknown'])
def test_mcp_reply_mentions_preserve_server_authorization(fixture, case):
    import copy
    store, core, rid, source, client, call, body = _mcp_claimed_turn(fixture)
    if case == 'other_room':
        other = core.create('fixture-human', {'topic': 'synthetic other room'}, 'other')['id']
        foreign = core.mutation('fixture-human', other, 'messages', 'other-source',
                                {'kind': 'question', 'text': 'other source', 'client_message_id': 'other-source'})
        body['reply_to'] = foreign['id']
    elif case == 'hidden':
        with store.transaction(rid, 'fixture-human') as (_, room):
            room['members']['a']['read_start_seq'] = source['seq'] + 1
        body['reply_to'] = source['id']
    elif case == 'missing':
        body['reply_to'] = 'nonexistent-message'
    else:
        if case == 'inactive':
            with store.transaction(rid, 'fixture-human') as (_, room):
                room['members']['b']['status'] = 'removed'
        body['mentions'] = [{'human': 'human', 'inactive': 'b', 'unknown': 'absent'}[case]]
    before = copy.deepcopy(store.rooms[rid])
    result = call('post_message', **body)
    assert result['result']['isError']
    assert store.rooms[rid] == before


@pytest.mark.parametrize('stale', ['attempt', 'generation', 'membership_revision', 'session_id'])
def test_mcp_reply_mentions_never_bypass_claim_fencing(fixture, stale):
    import copy
    store, _, rid, source, _, call, body = _mcp_claimed_turn(fixture)
    body.update(reply_to=source['id'], mentions=['b'])
    if stale == 'session_id':
        body[stale] = '0d54f0ab-2d87-4449-876e-7ceefec251ad'
    else:
        body[stale] += 1
    before = copy.deepcopy(store.rooms[rid])
    assert call('post_message', **body)['result']['isError']
    assert store.rooms[rid] == before


class _InviteClient(LocalClient):
    """Synthetic client for the opt-in delegation feature; the shared fixture stays off."""

    def __init__(self, store, token='fixture-a'):
        from fastapi.testclient import TestClient
        from talkabout.api import create_app
        self.http = TestClient(create_app(store, start_scheduler=False, allow_fixture_bearer=True,
                                          enable_agent_invites=True))
        self.token, self.calls = token, []


def _call(server, name, **arguments):
    return server.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                          'params': {'name': name, 'arguments': arguments}})


def _invite_policy(fixture, human):
    """Human grants one small delegation to agent a; returns (room-bound token, context)."""
    from talkabout.domain import AGENT_PERMISSIONS, SELF_PERMISSIONS
    store, _, rid = fixture
    store.provision({'id': 'c', 'owner': 'fixture-owner', 'kind': 'agent',
                     'display_name': 'Cedar', 'handle': 'cedar'}, 'fixture-c', AGENT_PERMISSIONS | SELF_PERMISSIONS)
    member = human.request('GET', 'rooms/' + rid + '/memberships')['a']
    human.request('PATCH', 'rooms/' + rid + '/memberships/a',
                  {'expected_revision': member['revision'], 'status': 'active',
                   'permissions': sorted(AGENT_PERMISSIONS | {'room:invite_agents'})}, 'member-a-1')
    credential = human.request('POST', 'agents/a/credentials',
                               {'scopes': ['room:read', 'room:invite_agents'], 'room_id': rid, 'expires_in': 3600},
                               'cred-a-1')
    member = human.request('GET', 'rooms/' + rid + '/memberships')['a']
    room = human.request('GET', 'rooms/' + rid)
    policy = human.request('POST', 'rooms/' + rid + '/agent-invite-policy',
                           {'action': 'grant', 'inviter_id': 'a', 'expected_generation': room['generation'],
                            'expected_policy_revision': 0, 'target_ids': ['c'], 'inviter_epoch': member['epoch'],
                            'inviter_revision': member['revision'], 'expires_at': int(time.time()) + 600,
                            'max_uses': 1, 'acknowledge_topic_sharing': True}, 'policy-grant')
    return credential['token'], {'generation': room['generation'], 'epoch': member['epoch'],
                                 'revision': member['revision'], 'policy_revision': policy['revision']}


def _invite_arguments(rid, context, mutation='invite-c-1', target='c', policy_revision=None):
    return {'room_id': rid, 'target_id': target, 'expected_generation': context['generation'],
            'expected_membership_epoch': context['epoch'],
            'expected_membership_revision': context['revision'],
            'expected_policy_revision': (context['policy_revision'] if policy_revision is None else policy_revision),
            'client_mutation_id': mutation}


def test_mcp_invite_policy_and_request_forwarding():
    from talkabout.domain import digest
    class Capturing:
        def __init__(self): self.calls = []
        def request(self, method, path, body=None, key=None, params=None):
            self.calls.append((method, path, body, key))
            return {'ok': True}
    client = Capturing()
    server = MCPServer(client)
    ready(server)
    assert not _call(server, 'get_agent_invite_policy', room_id='room')['result']['isError']
    assert client.calls[-1] == ('GET', 'rooms/room/agent-invite-policy/me', None, None)
    body = {'room_id': 'room', 'target_id': 'cedar', 'expected_generation': 2,
            'expected_membership_epoch': 1, 'expected_membership_revision': 3,
            'expected_policy_revision': 4, 'client_mutation_id': 'invite-1'}
    assert not _call(server, 'request_agent_invitation', **body)['result']['isError']
    method, path, sent, key = client.calls[-1]
    assert (method, path) == ('POST', 'rooms/room/agent-invitations')
    assert sent == {'target_id': 'cedar', 'expected_generation': 2, 'expected_membership_epoch': 1,
                    'expected_membership_revision': 3, 'expected_policy_revision': 4}
    assert key == 'mcp:' + digest(['request_agent_invitation', 'invite-1'])


@pytest.mark.parametrize('overrides', [
    {'target_id': ''}, {'target_id': 'x' * 129}, {'target_id': None},
    {'expected_generation': True}, {'client_mutation_id': ''},
])
def test_mcp_invite_request_rejected_before_api(overrides):
    class NoRequests:
        def request(self, *args, **kwargs):
            raise AssertionError('invalid MCP input reached API')
    server = MCPServer(NoRequests())
    ready(server)
    arguments = {'room_id': 'room', 'target_id': 'cedar', 'expected_generation': 1,
                 'expected_membership_epoch': 1, 'expected_membership_revision': 1,
                 'expected_policy_revision': 1, 'client_mutation_id': 'invite-1'} | overrides
    response = server.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                              'params': {'name': 'request_agent_invitation', 'arguments': arguments}})
    assert response['error']['code'] == -32602


def test_mcp_invite_tools_stay_off_by_default(fixture):
    store, _, rid = fixture
    server = MCPServer(LocalClient(store))
    ready(server)
    before = len(store.rooms[rid]['members'])
    assert _call(server, 'get_agent_invite_policy', room_id=rid)['result']['isError']
    assert _call(server, 'request_agent_invitation',
                 room_id=rid, target_id='b', expected_generation=1, expected_membership_epoch=1,
                 expected_membership_revision=1, expected_policy_revision=1,
                 client_mutation_id='invite-off')['result']['isError']
    assert len(store.rooms[rid]['members']) == before


def test_mcp_invite_request_records_pending_and_replays(fixture):
    store, core, rid = fixture
    human = _InviteClient(store, 'fixture-human')
    token, context = _invite_policy(fixture, human)
    server = MCPServer(_InviteClient(store, token))
    ready(server)
    policy = json.loads(_call(server, 'get_agent_invite_policy', room_id=rid)['result']['content'][0]['text'])
    assert policy['target_ids'] == ['c'] and policy['remaining'] == 1
    assert policy['generation'] == context['generation']
    assert policy['membership_revision'] == context['revision']
    first = _call(server, 'request_agent_invitation', **_invite_arguments(rid, context))
    assert not first['result']['isError']
    ack = json.loads(first['result']['content'][0]['text'])
    assert ack['accepted'] is True and ack['status'] == 'pending'
    room = core.read('fixture-human', rid)
    assert room['members']['c']['status'] == 'pending'
    assert sorted(room['members']['c']['permissions']) == ['room:read']
    assert _call(server, 'request_agent_invitation', **_invite_arguments(rid, context))['result'] == first['result']
    assert len(core.read('fixture-human', rid)['members']) == len(room['members'])
    # The ACK is not acceptance: the invitee still has to accept explicitly, and no model runs.
    assert core.read('fixture-human', rid)['members']['c']['status'] == 'pending'
    assert not any('runtime' in str(path) for _, path in server.client.calls)


def test_mcp_invite_request_requires_live_policy_and_room_binding(fixture):
    import copy
    store, core, rid = fixture
    human = _InviteClient(store, 'fixture-human')
    token, context = _invite_policy(fixture, human)
    server = MCPServer(_InviteClient(store, token))
    ready(server)
    before = copy.deepcopy(store.rooms[rid])
    assert _call(server, 'request_agent_invitation',
                 **_invite_arguments(rid, context, mutation='stale-policy',
                                     policy_revision=context['policy_revision'] + 1))['result']['isError']
    assert _call(server, 'request_agent_invitation',
                 **_invite_arguments(rid, context, mutation='not-listed', target='b'))['result']['isError']
    assert store.rooms[rid] == before
    other = human.request('POST', 'rooms', {'topic': 'Other synthetic room'}, 'room-other')
    cross = human.request('POST', 'agents/a/credentials',
                          {'scopes': ['room:read', 'room:invite_agents'], 'room_id': other['id'],
                           'expires_in': 300}, 'cred-a-cross')
    cross_server = MCPServer(_InviteClient(store, cross['token']))
    ready(cross_server)
    assert _call(cross_server, 'request_agent_invitation',
                 **_invite_arguments(rid, context, mutation='cross-room'))['result']['isError']
    assert store.rooms[rid] == before
    human.request('POST', 'rooms/' + rid + '/agent-invite-policy',
                  {'action': 'revoke', 'inviter_id': 'a', 'expected_generation': context['generation'],
                   'expected_policy_revision': context['policy_revision']}, 'policy-revoke')
    assert _call(server, 'request_agent_invitation',
                 **_invite_arguments(rid, context, mutation='after-revoke'))['result']['isError']
    # Every refusal above left no membership row behind.
    assert 'c' not in core.read('fixture-human', rid)['members']


def _turn_arguments(rid, generation, revision, mutation='turn-1'):
    return {'room_id': rid, 'generation': generation, 'membership_revision': revision,
            'client_mutation_id': mutation}


def _free_room(core, key='free-room'):
    room = core.create('fixture-a', {'topic': 'Synthetic free discussion', 'mode': 'free_discussion'}, key)
    return room['id']


def test_mcp_request_turn_forwards_context_only_body_and_mutation_key():
    from talkabout.domain import digest
    class Capturing:
        def __init__(self): self.calls = []
        def request(self, method, path, body=None, key=None, params=None):
            self.calls.append((method, path, body, key))
            return {'accepted': True}
    client = Capturing()
    server = MCPServer(client)
    ready(server)
    assert not _call(server, 'request_turn', **_turn_arguments('room', 2, 3))['result']['isError']
    assert client.calls[-1] == ('POST', 'rooms/room/turn-requests', {'generation': 2, 'membership_revision': 3},
                                'mcp:' + digest(['request_turn', 'turn-1']))
    # No session id exists in this contract and no extra key can reach the REST body.
    rejected = _call(server, 'request_turn', **(_turn_arguments('room', 2, 3) | {'session_id': 'fixture-session'}))
    assert rejected['error']['code'] == -32602 and len(client.calls) == 1


def test_mcp_request_turn_advertises_explicit_task_provider_and_cost():
    from talkabout.mcp import TOOLS
    tool = next(item for item in TOOLS if item['name'] == 'request_turn')
    description = tool['description'].lower()
    for phrase in ('explicitly request', 'pending turn task', 'external provider', 'incurring cost',
                   'not bound to a client session', 'never recurses'):
        assert phrase in description
    properties = tool['inputSchema']['properties']
    assert list(properties) == ['room_id', 'generation', 'membership_revision', 'client_mutation_id']
    assert tool['inputSchema']['required'] == list(properties)


def test_mcp_request_turn_records_one_request_and_replays(fixture):
    store, core, _ = fixture
    rid = _free_room(core)
    server = MCPServer(LocalClient(store))
    ready(server)
    room = core.read('fixture-a', rid)
    arguments = _turn_arguments(rid, room['generation'], room['members']['a']['revision'])
    first = _call(server, 'request_turn', **arguments)
    assert not first['result']['isError']
    assert json.loads(first['result']['content'][0]['text']) == {'accepted': True}
    room = core.read('fixture-a', rid)
    assert set(room['members']) == {'a'}
    assert [(task['assignee'], task['purpose']) for task in room['tasks']] == [('a', 'requested_turn')]
    replay = _call(server, 'request_turn',
                   **_turn_arguments(rid, room['generation'], room['members']['a']['revision']))
    assert replay['result'] == first['result']
    assert len(core.read('fixture-a', rid)['tasks']) == 1
    # A different key while a turn is already pending is refused and creates nothing.
    second = _call(server, 'request_turn',
                   **_turn_arguments(rid, room['generation'], room['members']['a']['revision'], 'turn-2'))
    assert second['result']['isError']
    assert len(core.read('fixture-a', rid)['tasks']) == 1


def test_mcp_request_turn_rejections_leave_room_unchanged(fixture):
    import copy
    from talkabout.domain import AGENT_PERMISSIONS, SELF_PERMISSIONS, token_hash
    store, core, fixture_rid = fixture
    rid = _free_room(core, 'free-rejections')
    room = core.read('fixture-a', rid)
    generation, revision = room['generation'], room['members']['a']['revision']
    server = MCPServer(LocalClient(store))
    ready(server)
    before = copy.deepcopy(store.rooms[rid])
    for arguments in (
        _turn_arguments(rid, generation + 1, revision, 'stale-generation'),
        _turn_arguments(rid, generation, revision + 1, 'stale-revision'),
        _turn_arguments('missing-room', generation, revision, 'missing-room'),
    ):
        assert _call(server, 'request_turn', **arguments)['result']['isError']
    assert store.rooms[rid] == before
    # The default fixture room is bounded_rounds; the REST route refuses, so the tool does too.
    structured = core.read('fixture-human', fixture_rid)
    assert _call(server, 'request_turn', **_turn_arguments(
        fixture_rid, structured['generation'], structured['members']['a']['revision'],
        'structured'))['result']['isError']
    # Credential scope: room:speak missing.
    credential = store.credentials[token_hash('fixture-a')]
    scopes = list(credential['scopes'])
    credential['scopes'].remove('room:speak')
    assert _call(server, 'request_turn', **_turn_arguments(rid, generation, revision, 'no-scope'))['result']['isError']
    credential['scopes'][:] = scopes
    # A read-only active member cannot request the floor.
    store.rooms[rid]['members']['b'] = {'status': 'active', 'kind': 'agent', 'permissions': ['room:read'],
                                        'revision': 1, 'epoch': 1, 'read_start_seq': 1, 'greeted': False}
    read_only = MCPServer(LocalClient(store, 'fixture-b'))
    ready(read_only)
    assert _call(read_only, 'request_turn', **_turn_arguments(rid, generation, 1, 'read-only'))['result']['isError']
    # A pending (read-only) invitee is not an active member yet.
    store.provision({'id': 'c', 'owner': 'fixture-owner', 'kind': 'agent',
                     'display_name': 'Cedar', 'handle': 'cedar'}, 'fixture-c', AGENT_PERMISSIONS | SELF_PERMISSIONS)
    store.rooms[rid]['members']['c'] = {'status': 'pending', 'kind': 'agent', 'permissions': ['room:read'],
                                        'revision': 1, 'epoch': 1, 'read_start_seq': 1, 'greeted': False}
    pending = MCPServer(LocalClient(store, 'fixture-c'))
    ready(pending)
    assert _call(pending, 'request_turn', **_turn_arguments(rid, generation, 1, 'pending-invitee'))['result']['isError']
    # A closed room is read-only for this operation.
    store.rooms[rid]['state'] = 'closed'
    assert _call(server, 'request_turn', **_turn_arguments(rid, generation, revision, 'closed-room'))['result']['isError']
    assert core.read('fixture-a', rid)['tasks'] == []


def test_mcp_request_turn_honours_pending_and_total_request_limits(fixture):
    store, core, _ = fixture
    rid = _free_room(core, 'free-quota')
    room = core.read('fixture-a', rid)
    generation, revision = room['generation'], room['members']['a']['revision']
    server = MCPServer(LocalClient(store))
    ready(server)
    # Synthetic valid pending intents occupy the bounded queue; stale legacy
    # entries are canceled before admission and no longer consume slots.
    pending = store.rooms[rid]
    member = pending['members']['a']
    pending['turn_requests'] = [{'principal_id': 'a', 'generation': generation,
                                 'membership_epoch': member['epoch'], 'membership_revision': revision}] * 16
    assert _call(server, 'request_turn',
                 **_turn_arguments(rid, generation, revision, 'pending-cap'))['result']['isError']
    store.rooms[rid]['turn_requests'] = []
    store.rooms[rid]['request_count'] = 64
    assert _call(server, 'request_turn',
                 **_turn_arguments(rid, generation, revision, 'total-cap'))['result']['isError']
    assert core.read('fixture-a', rid)['tasks'] == []


def test_mcp_scope_pause_idempotency_and_stdio(fixture):
    store, core, rid = fixture
    server = MCPServer(LocalClient(store))
    ready(server)
    other = core.create('fixture-b', {'topic': 'Private other fixture'}, 'other')
    for name in ('read_room', 'list_participants'):
        denied = server.handle({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
                                'params': {'name': name, 'arguments': {'room_id': other['id']}}})
        assert denied['result']['isError']
    message = {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "propose_text", "arguments": {"room_id": rid, "text": "fixture text", "generation": 1, "membership_revision": 2, "client_mutation_id": "fixture-artifact"}}}
    a, b = server.handle(message), server.handle(message)
    assert a["result"] == b["result"] and len(core.read("fixture-a", rid)["artifacts"]) == 1
    from conftest import control
    control(core, rid, "pause")
    message["params"]["arguments"]["client_mutation_id"] = "after-pause"
    assert server.handle(message)["result"]["isError"]
    output = io.StringIO()
    server.serve(io.StringIO('{invalid}\n'+json.dumps({"jsonrpc":"2.0","id":8,"method":"ping"})+'\n'), output)
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert replies[0]["error"]["code"] == -32700 and replies[1]["result"] == {}
    assert "fixture-a" not in output.getvalue()


def test_bounded_runtime_timeout_cancellation_and_error():
    release, finished = threading.Event(), threading.Event()
    class IgnoresCancel:
        def run(self, context, cancel):
            release.wait(2)
            finished.set()
            return "late"
    cancel = threading.Event()
    try:
        with pytest.raises(RuntimeTimeout) as error:
            bounded_call(IgnoresCancel(), {}, cancel, 0.02)
        # Prove the caller returns while an uncooperative runtime is still active,
        # instead of asserting a brittle 70ms Windows thread-scheduling threshold.
        assert cancel.is_set() and not finished.is_set() and not error.value.runtime_stopped
    finally:
        release.set()
        assert finished.wait(1)
    class Fails:
        def run(self, context, cancel): raise ValueError("fixture error")
    with pytest.raises(ValueError): bounded_call(Fails(), {}, threading.Event(), 1)
    for policy in [{"approval_bypass":True},{"external":True,"tool_limits_verified":False},{"allowed_tools":["shell"]}]:
        with pytest.raises(ValueError): validate_adapter_policy(policy)


def test_worker_timeout_never_publishes(fixture, tmp_path):
    store, core, rid = fixture
    class Slow(SyntheticRuntime):
        def run(self, context, cancel):
            cancel.wait(0.1)
            return "late fixture"
    journal = Journal(tmp_path / "timeout.sqlite")
    assert Worker(LocalClient(store), journal, Slow(), runtime_timeout=0.02).step() == "runtime_timeout"
    assert journal.status() == {"uncertain": 1} and not core.read("fixture-human", rid)["messages"]
    journal.close()
