"""In-memory API/worker journeys; fake runtime output is not provider acceptance."""
import time

import httpx
import pytest

from conftest import LocalClient
from talkabout.connector import verify_runtime
from talkabout.domain import AGENT_PERMISSIONS, SELF_PERMISSIONS
from talkabout.identity import IdentityService
from talkabout.worker import Journal, Worker
from test_connector_verification import EchoRuntime
from test_runtime_checks import begin, finish, heartbeat, issued, prepare


def test_room_projection_withholds_another_rooms_receipt(fixture):
    store, core, first_room = fixture
    service = IdentityService(store)
    other = prepare(core)
    first = issued(service, 'first-room', room_id=first_room)
    second = issued(service, 'second-room', room_id=other['id'])
    finish(service, begin(service, first['token']), first['token'])
    heartbeat(service, first['token'])
    finish(service, begin(service, second['token'], key='other-room'), second['token'])
    human = LocalClient(store, 'fixture-human')
    try:
        registry = next(a for a in human.request('GET', 'agents') if a['id'] == 'a')
        member = human.request('GET', 'rooms/'+first_room)['members']['a']
        assert registry['runtime_check']['status'] == 'passed'
        assert member['connection']['room_id'] == first_room and member['runtime_check'] is None
        assert not core._worker_ready(store.principals['a'], first_room, time.time())
        other_member = human.request('GET', 'rooms/'+other['id'])['members']['a']
        assert other_member['connection'] is None and other_member['runtime_check']['status'] == 'passed'
        assert not core._worker_ready(store.principals['a'], other['id'], time.time())
    finally:
        human.http.close()


def test_registration_to_first_response_reconnect_and_revocation(fixture, tmp_path):
    store, core, _ = fixture
    human = LocalClient(store, 'fixture-human')
    journal = Journal(tmp_path/'journey.sqlite')
    client = None
    try:
        body = {'display_name':'Journey Fixture', 'handle':'journey-fixture'}
        agent = human.request('POST', 'agents', body, 'register-journey')
        assert human.request('POST', 'agents', body, 'register-journey')['id'] == agent['id']
        assert agent['connection'] is None and agent['runtime_check'] is None
        room = human.request('POST', 'rooms', {'topic':'Synthetic journey contract'}, 'journey-room')
        rid = room['id']
        invitation = human.request('POST', 'rooms/'+rid+'/memberships',
            {'principal_id':agent['id'], 'permissions':sorted(AGENT_PERMISSIONS)}, 'invite-journey')
        credential = human.request('POST', 'agents/'+agent['id']+'/credentials',
            {'room_id':rid, 'scopes':sorted(AGENT_PERMISSIONS | SELF_PERMISSIONS), 'expires_in':300}, 'fixture-credential')
        client = LocalClient(store, credential['token'])
        assert any(i['room_id'] == rid for i in client.request('GET', 'agents/me/invitations'))
        client.request('POST', 'rooms/'+rid+'/memberships/'+agent['id']+'/accept',
            {'expected_revision':invitation['revision'], 'acknowledge_private':True}, 'join-journey')
        first_check = EchoRuntime()
        receipt, calls = verify_runtime(client, journal, first_check, 'codex', credential['token'], time.monotonic()+10)
        assert calls == first_check.calls == 1 and receipt['verification'] == 'worker_reported_test'
        client.request('PUT', 'agents/me/connection', {'runtime':'codex','state':'idle'}, 'contact')
        member = human.request('GET', 'rooms/'+rid)['members'][agent['id']]
        assert member['status'] == 'active' and member['response_evidence']['worker_reported'] == 0
        class FakeRoomRuntime:
            calls = 0
            def run(self, context, cancel):
                self.calls += 1
                return 'Synthetic journey contribution; no provider was invoked.'
        runtime = FakeRoomRuntime()
        assert Worker(client, journal, runtime).step() == 'completed' and runtime.calls == 1
        after = human.request('GET', 'rooms/'+rid)
        assert after['members'][agent['id']]['response_evidence']['worker_reported'] == 1
        assert len(after['messages']) == 1 and after['messages'][0]['runtime_check_id'] == receipt['id']
        fresh = EchoRuntime(); client.calls.clear()
        reused, calls = verify_runtime(client, journal, fresh, 'codex', credential['token'], time.monotonic()+10)
        assert reused['id'] == receipt['id'] and calls == fresh.calls == 0 and fresh.closed
        assert client.calls == [('GET', 'agents/me')]
        human.request('POST', 'agents/'+agent['id']+'/credentials/'+credential['credential_id']+'/revoke', {}, 'revoke-journey')
        rejected = EchoRuntime()
        with pytest.raises(httpx.HTTPStatusError) as error:
            verify_runtime(client, journal, rejected, 'codex', credential['token'], time.monotonic()+10)
        assert error.value.response.status_code == 401 and rejected.calls == 0
        member = human.request('GET', 'rooms/'+rid)['members'][agent['id']]
        assert member['connection']['state'] == 'stopped' and member['runtime_check']['status'] == 'invalidated'
        assert len(core.read('fixture-human', rid)['messages']) == 1
        replacement = human.request('POST', 'agents/'+agent['id']+'/credentials',
            {'room_id':rid, 'scopes':sorted(AGENT_PERMISSIONS | SELF_PERMISSIONS), 'expires_in':300}, 'fixture-replacement')
        client.http.close(); client = LocalClient(store, replacement['token'])
        replacement_check = EchoRuntime()
        newer, calls = verify_runtime(client, journal, replacement_check, 'codex', replacement['token'], time.monotonic()+10)
        assert newer['id'] != receipt['id'] and calls == replacement_check.calls == 1
        client.request('PUT', 'agents/me/connection', {'runtime':'codex','state':'idle'}, 'replacement-contact')
        after = human.request('GET', 'rooms/'+rid)
        assert after['members'][agent['id']]['runtime_check']['id'] == newer['id']
        assert after['members'][agent['id']]['response_evidence']['worker_reported'] == 0
        assert len(after['messages']) == 1  # A new check is not a new room response.
    finally:
        journal.close(); human.http.close()
        if client is not None: client.http.close()
