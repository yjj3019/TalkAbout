"""Cross-boundary collaboration checks; only synthetic in-process fixtures."""
import copy

import pytest
from fastapi.testclient import TestClient

from talkabout.api import create_app
from test_collaboration_http import SUMMARY, OUTPUT, application, create, proposal_body
from conftest import leased
from talkabout.domain import Problem
from test_work_plan_dispatch import dispatch, node, prepare
from test_sessions import session_fixture, post


@pytest.fixture
def prepared(fixture):
    store, core, rid = fixture
    core.replace_work_plan('fixture-human', rid, 'security-plan', {'expected_revision': 0, 'nodes': []})
    task = leased(core, rid)
    store.provision({'id': 'outsider', 'owner': 'outsider-owner', 'kind': 'human',
                     'display_name': 'Outsider', 'handle': 'outsider'}, 'fixture-outsider',
                    {'room:read', 'room:control'})
    with TestClient(create_app(store, start_scheduler=False, allow_fixture_bearer=True)) as client:
        proposal = create(client, rid, task)
        yield store, rid, task, proposal, client


@pytest.mark.parametrize('operation', ['list', 'decision', 'apply'])
def test_authenticated_outsider_never_accesses_or_changes_private_proposals(prepared, operation):
    store, rid, task, proposal, client = prepared
    base = f'/api/v1/rooms/{rid}/collaboration-proposals'
    method, path, body = {
        'list': ('GET', base, None),
        'decision': ('POST', f"{base}/{proposal['id']}/decision", {'expected_revision': 1, 'decision': 'approve'}),
        'apply': ('POST', f"{base}/{proposal['id']}/apply", application(1)),
    }[operation]
    before = copy.deepcopy(store.rooms[rid])
    response = client.request(method, path, json=body,
                              headers={'Authorization': 'Bearer fixture-outsider', 'Idempotency-Key': 'outsider'})
    assert response.status_code in (403, 404)
    assert SUMMARY not in response.text and OUTPUT not in response.text
    assert store.rooms[rid] == before


def test_host_header_is_not_an_authentication_grant(prepared):
    store, rid, task, proposal, client = prepared
    before = copy.deepcopy(store.rooms[rid])
    # Even an allowed Host header and a known ID do not authorize access.
    response = client.get(f'/api/v1/rooms/{rid}/collaboration-proposals', headers={'Host': 'testserver'})
    assert response.status_code == 401
    assert SUMMARY not in response.text and OUTPUT not in response.text
    assert store.rooms[rid] == before


@pytest.mark.parametrize('operation', ['list', 'decision', 'apply'])
def test_agent_with_granted_control_cannot_convert_proposal_into_authority(prepared, operation):
    store, rid, task, proposal, client = prepared
    # Simulate an overprivileged agent without relying on the invitation UI.
    store.rooms[rid]['members']['a']['permissions'] = ['room:read', 'room:control', 'task:complete']
    before = copy.deepcopy(store.rooms[rid])
    base = f'/api/v1/rooms/{rid}/collaboration-proposals'
    method, path, body = {
        'list': ('GET', base, None),
        'decision': ('POST', f"{base}/{proposal['id']}/decision", {'expected_revision': 1, 'decision': 'approve'}),
        'apply': ('POST', f"{base}/{proposal['id']}/apply", application(1)),
    }[operation]
    response = client.request(method, path, json=body,
                              headers={'Authorization': 'Bearer fixture-a', 'Idempotency-Key': 'agent-control'})
    assert response.status_code in (403, 404)
    assert SUMMARY not in response.text and OUTPUT not in response.text
    assert store.rooms[rid] == before


@pytest.mark.parametrize('scope', ['analysis', 'owner_approved_workspace'])
def test_write_scope_is_declared_unenforced_and_acknowledgement_is_required(fixture, scope):
    store, core, rid = fixture
    prepare(store, core, rid, [node(write_scope=scope)], 'fixture-a')
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem) as exc:
        dispatch(core, rid, acknowledge_write_scope_unenforced=False)
    assert exc.value.status == 422
    assert store.rooms[rid] == before
    run = dispatch(core, rid)['run']
    assert run['write_scope_enforced'] is False
    assert run['nodes'][0]['write_scope'] == scope
    assert core.plan_dispatch('fixture-human', rid)['run']['write_scope_enforced'] is False


def test_production_auth_does_not_treat_reachable_host_or_human_bearer_as_authority(fixture):
    store, core, rid = fixture
    core.replace_work_plan('fixture-human', rid, 'prod-security-plan', {'expected_revision': 0, 'nodes': []})
    task = leased(core, rid)
    with TestClient(create_app(store, start_scheduler=False), base_url='http://127.0.0.1') as client:
        base = f'/api/v1/rooms/{rid}/collaboration-proposals'
        before = copy.deepcopy(store.rooms[rid])
        for headers, status in (({}, 401), ({'Authorization': 'Bearer fixture-human'}, 403)):
            response = client.get(base, headers=headers)
            assert response.status_code == status
            assert SUMMARY not in response.text and OUTPUT not in response.text
            assert store.rooms[rid] == before
        response = client.post(base, json=proposal_body(task), headers={
            'Authorization': 'Bearer fixture-a', 'Idempotency-Key': 'prod-create'})
        assert response.status_code == 200
        proposal = response.json()
        before = copy.deepcopy(store.rooms[rid])
        response = client.post(f"{base}/{proposal['id']}/decision",
                               json={'expected_revision': 1, 'decision': 'approve'},
                               headers={'Authorization': 'Bearer fixture-a', 'Idempotency-Key': 'prod-denied'})
        assert response.status_code == 403
        assert store.rooms[rid] == before


@pytest.mark.parametrize('operation', ['decision', 'apply'])
def test_human_session_mutation_requires_origin_and_csrf(session_fixture, operation):
    store, client, actor, csrf, _ = session_fixture
    room = post(client, 'rooms', {'topic': 'synthetic'}, csrf).json()
    rid = room['id']
    base = f'/api/v1/rooms/{rid}/collaboration-proposals/proposal-id'
    path, body = (f'{base}/decision', {'expected_revision': 1, 'decision': 'approve'}) if operation == 'decision' else (f'{base}/apply', application(1))
    before = copy.deepcopy(store.rooms[rid])
    for headers in ({'Origin': 'http://evil.invalid', 'X-CSRF-Token': csrf},
                    {'Origin': 'http://testserver'},
                    {'Origin': 'http://testserver', 'X-CSRF-Token': 'invalid'}):
        response = client.post(path, json=body, headers={**headers, 'Idempotency-Key': 'csrf-test'})
        assert response.status_code == 403
        assert store.rooms[rid] == before
    response = client.post(path, json=body, headers={'Origin': 'http://testserver',
                           'X-CSRF-Token': csrf, 'Idempotency-Key': 'csrf-test'})
    assert response.status_code == 404  # authenticated request reaches the proposal lookup
    assert store.rooms[rid] == before


def test_explicit_lan_host_allowlist_does_not_bypass_authentication(fixture):
    # Host filtering is not an authentication mechanism: even if an operator
    # explicitly permits a LAN host, the private endpoint still requires a
    # credential and valid room membership. This is an in-process HTTP test,
    # not a claim that exposing the service on a LAN is safe.
    store, core, rid = fixture
    core.replace_work_plan('fixture-human', rid, 'lan-plan', {'expected_revision': 0, 'nodes': []})
    store.provision({'id': 'lan-outsider', 'owner': 'other-owner', 'kind': 'human',
                     'display_name': 'Outsider', 'handle': 'lan-outsider'}, 'lan-outsider-token',
                    {'room:read', 'room:control'})
    before = copy.deepcopy(store.rooms[rid])
    with TestClient(create_app(store, start_scheduler=False, allowed_hosts=['192.0.2.1'],
                               allow_fixture_bearer=True), base_url='http://192.0.2.1') as client:
        path = f'/api/v1/rooms/{rid}/collaboration-proposals'
        assert client.get(path).status_code == 401
        response = client.get(path, headers={'Authorization': 'Bearer lan-outsider-token'})
        assert response.status_code in (403, 404)
        assert store.rooms[rid] == before


def test_default_host_policy_rejects_lan_host_even_with_valid_agent_token(fixture):
    store, core, rid = fixture
    core.replace_work_plan('fixture-human', rid, 'default-host-plan', {'expected_revision': 0, 'nodes': []})
    before = copy.deepcopy(store.rooms[rid])
    with TestClient(create_app(store, start_scheduler=False), base_url='http://192.0.2.1') as client:
        response = client.get(f'/api/v1/rooms/{rid}', headers={'Authorization': 'Bearer fixture-a'})
        assert response.status_code == 400
        assert store.rooms[rid] == before


def test_untrusted_host_rejected_without_room_mutation(prepared):
    store, rid, task, proposal, client = prepared
    before = copy.deepcopy(store.rooms[rid])
    response = client.get(f'/api/v1/rooms/{rid}/collaboration-proposals',
                          headers={'Host': 'attacker.example', 'Authorization': 'Bearer fixture-human'})
    assert response.status_code == 400
    assert SUMMARY not in response.text and OUTPUT not in response.text
    assert store.rooms[rid] == before
