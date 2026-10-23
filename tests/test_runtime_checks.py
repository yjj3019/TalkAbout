"""Authenticated worker reports are challenge receipts, never provider attestation."""
import json
import time

import httpx
import pytest

from conftest import LocalClient
from talkabout.domain import AGENT_PERMISSIONS, SELF_PERMISSIONS, Problem, token_hash
from talkabout.identity import IdentityService
from test_community_flow import control


def begin(service, token='fixture-a', runtime='codex', key='check'):
    return service.runtime_check(token, None, 'create', key, {'runtime': runtime})


def finish(service, check, token='fixture-a', key='complete', **changes):
    return service.runtime_check(token, check['id'], 'complete', key,
                                 {'response': check['challenge'], 'session_id': 'fixture-runtime-session', **changes})


def heartbeat(service, token='fixture-a', runtime='codex', state='idle'):
    return service.mutate(token, 'connection', 'heartbeat', {'runtime': runtime, 'state': state})


def issued(service, key='issued', **changes):
    return service.mutate('fixture-human', 'credentials/a', key,
                          {'scopes': sorted(AGENT_PERMISSIONS | SELF_PERMISSIONS), 'expires_in': 300, **changes})


def prepare(core):
    room = core.create('fixture-human', {'topic': 'Runtime check fixture', 'start_policy': 'manual'}, 'runtime-room')
    invitation = core.mutation('fixture-human', room['id'], 'memberships', 'invite',
                               {'principal_id': 'a', 'permissions': sorted(AGENT_PERMISSIONS)})
    core.accept('fixture-a', room['id'], 'accept', {'expected_revision': invitation['revision'], 'acknowledge_private': True})
    return room


@pytest.mark.parametrize('runtime', ['codex', 'claude', 'grok'])
def test_api_round_trip_and_public_metadata_has_no_challenge_or_binding(fixture, runtime):
    store, core, rid = fixture
    client = LocalClient(store)
    check = client.request('POST', 'agents/me/runtime-checks', {'runtime': runtime}, 'test')
    assert check['challenge'] in check['prompt'] and check['expires_at'] <= time.time() + 120
    assert client.request('POST', 'agents/me/runtime-checks', {'runtime': runtime}, 'test') == check
    pending = core.me('fixture-a')['runtime_check']
    assert pending['status'] == 'pending'
    receipt = client.request('POST', f"agents/me/runtime-checks/{check['id']}/complete",
                             {'response': check['challenge'], 'session_id': 'private-fixture-session', 'runtime_version': 'fixture-v1'}, 'done')
    assert receipt['status'] == 'passed' and receipt['verification'] == 'worker_reported_test'
    heartbeat(IdentityService(store), runtime=runtime)
    public = {'me': core.me('fixture-a'), 'agents': IdentityService(store).list('fixture-human'), 'room': core.read('fixture-human', rid)}
    serialized = json.dumps(public)
    for secret in [check['challenge'], 'private-fixture-session', token_hash('fixture-a'), '_credential_binding', '_challenge']:
        assert secret not in serialized
    assert core.read('fixture-human', rid)['members']['a']['runtime_check']['status'] == 'passed'
    assert 'runtime_check' not in core.read('fixture-b', rid)['members']['a']
    assert check['challenge'] not in json.dumps(store.principals)


def test_provider_poll_or_heartbeat_is_not_a_test_receipt(fixture):
    store, core, _ = fixture
    service = IdentityService(store)
    room = prepare(core)
    heartbeat(service)
    core.events('fixture-a')
    with pytest.raises(Problem, match='worker_connection_required'):
        control(core, room, 'start', 'unverified')
    check = begin(service)
    with pytest.raises(Problem, match='worker_connection_required'):
        control(core, room, 'start', 'pending')
    finish(service, check)
    heartbeat(service)
    assert store.principals['a']['runtime_check']['status'] == 'passed'
    control(core, room, 'start', 'verified')
    assert core.read('fixture-human', room['id'])['state'] == 'active'


def test_check_scope_actor_and_credential_binding(fixture):
    store, core, rid = fixture
    service = IdentityService(store)
    check = begin(service)
    with pytest.raises(Problem, match='not_found'):
        finish(service, check, token='fixture-b')
    with pytest.raises(Problem, match='forbidden'):
        begin(service, token='fixture-human')
    no_scope = issued(service, 'no-cap', scopes=['agent:self_read'])
    with pytest.raises(Problem, match='forbidden'):
        begin(service, no_scope['token'])
    different = issued(service, 'different', room_id=rid)
    with pytest.raises(Problem, match='runtime_check_binding'):
        finish(service, check, token=different['token'])
    scoped_check = begin(service, different['token'], key='scoped')
    finish(service, scoped_check, token=different['token'])
    heartbeat(service, token=different['token'])
    assert core._worker_ready(store.principals['a'], rid, time.time())
    assert not core._worker_ready(store.principals['a'], 'different-room', time.time())
    assert store.principals['a']['runtime_check']['expires_at'] <= different['expires_at']


def test_check_replay_supersession_expiry_and_result_bounds(fixture):
    store, _, _ = fixture
    service = IdentityService(store)
    old = begin(service)
    with pytest.raises(Problem, match='idempotency_conflict'):
        begin(service, runtime='grok')
    check = begin(service, key='replacement')
    with pytest.raises(Problem, match='runtime_check_superseded'):
        begin(service)
    with pytest.raises(Problem, match='not_found'):
        finish(service, old)
    for changes in [{'response': 'x' * 4097}, {'session_id': ''}, {'session_id': 2}, {'runtime_version': 'x' * 129}, {'verified': True}]:
        with pytest.raises(Problem):
            finish(service, check, **changes)
    receipt = finish(service, check)
    assert finish(service, check) == receipt
    with pytest.raises(Problem, match='idempotency_conflict'):
        finish(service, check, response='counterfeit')
    with pytest.raises(Problem, match='runtime_check_unavailable'):
        finish(service, check, key='replay-with-new-key')
    store.principals['a']['runtime_check']['expires_at'] = time.time() - 1
    assert service.list('fixture-human')[0]['runtime_check']['status'] == 'expired'
    with pytest.raises(Problem, match='runtime_check_unavailable'):
        finish(service, check)
    new = begin(service, key='expired-pending')
    store.principals['a']['runtime_check']['expires_at'] = time.time() - 1
    with pytest.raises(Problem, match='runtime_check_unavailable'):
        finish(service, new)


def test_counterfeit_fields_and_mismatch_cannot_assert_verification(fixture):
    store, core, rid = fixture
    service = IdentityService(store)
    for body in [{'runtime': 'synthetic'}, {'runtime': 'codex', 'status': 'passed'}, {'runtime': 'codex', 'actor_id': 'b'}]:
        with pytest.raises(Problem, match='invalid_runtime_check'):
            service.runtime_check('fixture-a', None, 'create', 'fake', body)
    with pytest.raises(Problem, match='invalid_connection'):
        service.mutate('fixture-a', 'connection', 'fake', {'runtime': 'codex', 'state': 'idle', 'runtime_check': {'status': 'passed'}})
    heartbeat(service)
    check = begin(service)
    mismatch = finish(service, check, response='made up')
    assert mismatch['status'] == 'failed' and mismatch['error_code'] == 'challenge_mismatch'
    assert not core._worker_ready(store.principals['a'], rid, time.time())
    check = begin(service, key='failure')
    failed = service.runtime_check('fixture-a', check['id'], 'fail', 'timeout', {'error_code': 'runtime_timeout'})
    assert failed['status'] == 'failed'
    assert service.runtime_check('fixture-a', check['id'], 'fail', 'timeout', {'error_code': 'runtime_timeout'}) == failed


@pytest.mark.parametrize('change', ['runtime', 'credential', 'revoke', 'rotate', 'expiry', 'stale', 'direct_revoke', 'failed'])
def test_readiness_dies_on_changed_or_revoked_connection(fixture, change):
    store, core, rid = fixture
    service = IdentityService(store)
    credential = issued(service)
    token = credential['token']
    check = begin(service, token)
    finish(service, check, token)
    heartbeat(service, token)
    assert core._worker_ready(store.principals['a'], rid, time.time())
    if change == 'runtime':
        heartbeat(service, token, runtime='grok')
    elif change == 'credential':
        heartbeat(service, issued(service, 'new')['token'])
    elif change in {'revoke', 'rotate'}:
        body = {} if change == 'revoke' else {'scopes': sorted(AGENT_PERMISSIONS | SELF_PERMISSIONS), 'expires_in': 300}
        service.mutate('fixture-human', f"credentials/a/{credential['credential_id']}/{change}", 'replace', body)
    elif change == 'expiry':
        store.credentials[token_hash(token)]['expires_at'] = time.time() - 1
    elif change == 'stale':
        store.principals['a']['connection']['last_seen_at'] = time.time() - 76
    elif change == 'direct_revoke':
        store.revoke(token)
    else:
        heartbeat(service, token, state='failed')
    assert not core._worker_ready(store.principals['a'], rid, time.time())


def test_synthetic_only_has_explicit_test_evidence(fixture):
    store, core, rid = fixture
    service = IdentityService(store)
    observed = heartbeat(service, runtime='synthetic')
    assert observed['execution_evidence'] == 'synthetic_test_only'
    assert core._worker_ready(store.principals['a'], rid, time.time())
    assert core.me('fixture-a')['runtime_check'] is None


def test_pending_checks_are_bounded_without_identity_operation_growth(fixture):
    store, _, _ = fixture
    service = IdentityService(store)
    for i in range(128):
        begin(service, key=f'check-{i}')
    assert len(store.principals['a']['runtime_check_requests']) == 128
    with pytest.raises(Problem, match='runtime_check_quota'):
        begin(service, key='over-quota')
    assert not store.principals['a'].get('identity_operations')


def test_postgres_receipt_survives_restart_and_revocation(postgres_fixture):
    from talkabout.core import Core
    from talkabout.store import PostgresStore
    store, _, rid = postgres_fixture
    service = IdentityService(store)
    credential = issued(service)
    check = begin(service, credential['token'])
    finish(service, check, credential['token'])
    heartbeat(service, credential['token'])
    restored = PostgresStore(store.dsn, store.schema)
    _, _, principals = restored.snapshot('fixture-human')
    assert Core(restored)._worker_ready(principals['a'], rid, time.time())
    restored.revoke(credential['token'])
    assert not Core(restored)._worker_ready(principals['a'], rid, time.time())


def test_all_accepted_workers_must_be_ready_and_pending_invites_do_not_block(fixture):
    store, core, _ = fixture
    service = IdentityService(store)
    room = prepare(core)
    heartbeat(service, runtime='synthetic')
    invitation = core.mutation('fixture-human', room['id'], 'memberships', 'invite-b',
                               {'principal_id': 'b', 'permissions': sorted(AGENT_PERMISSIONS)})
    core.accept('fixture-b', room['id'], 'accept-b', {'expected_revision': invitation['revision'], 'acknowledge_private': True})
    with pytest.raises(Problem, match='worker_connection_required'):
        control(core, room, 'start', 'one-ready')
    heartbeat(service, token='fixture-b', runtime='synthetic')
    control(core, room, 'start', 'both-ready')
    assert core.read('fixture-human', room['id'])['state'] == 'active'


def test_capability_only_credential_cannot_make_room_worker_ready(fixture):
    store, core, rid = fixture
    service = IdentityService(store)
    token = issued(service, scopes=['agent:capabilities_write'])['token']
    check = begin(service, token)
    finish(service, check, token)
    heartbeat(service, token)
    assert not core._worker_ready(store.principals['a'], rid, time.time())


def test_pending_invitation_does_not_block_verified_participants(fixture):
    store, core, _ = fixture
    room = prepare(core)
    service = IdentityService(store)
    check = begin(service)
    finish(service, check)
    heartbeat(service)
    core.mutation('fixture-human', room['id'], 'memberships', 'invite-b-pending',
                  {'principal_id': 'b', 'permissions': sorted(AGENT_PERMISSIONS)})
    control(core, room, 'start', 'pending-excluded')
    assert core.read('fixture-human', room['id'])['state'] == 'active'


@pytest.mark.parametrize('body', [{'runtime': []}, {'runtime': {}}, {'runtime': None}])
def test_malformed_runtime_json_is_validation_error(fixture, body):
    store, _, _ = fixture
    client = LocalClient(store)
    with pytest.raises(httpx.HTTPStatusError) as error:
        client.request('POST', 'agents/me/runtime-checks', body, 'malformed')
    assert error.value.response.status_code == 422


def test_start_ledger_retires_known_keys_and_prunes_expired_history(fixture):
    store, _, _ = fixture
    service = IdentityService(store)
    old = begin(service, key='old')
    principal = store.principals['a']
    old_request = principal['runtime_check']['_request_id']
    principal['runtime_check_requests'][old_request]['expires_at'] = time.time() - 1
    with pytest.raises(Problem, match='idempotency_retired') as retired:
        begin(service, key='old')
    assert retired.value.status == 410
    for i in range(127):
        begin(service, key=f'bounded-{i}')
    with pytest.raises(Problem, match='runtime_check_quota'):
        begin(service, key='quota')
    principal = store.principals['a']
    current_request = principal['runtime_check']['_request_id']
    for request_id, record in principal['runtime_check_requests'].items():
        if request_id != current_request:
            record.update(expires_at=time.time() - 2, retain_until=time.time() - 1)
    fresh = begin(service, key='new-after-retention')
    assert fresh['id'] != old['id']
    history = store.principals['a']['runtime_check_requests']
    assert len(history) == 2 and current_request in history and old_request not in history
    assert begin(service, key='new-after-retention') == fresh


def test_legacy_start_ledger_migrates_to_expiring_tombstones(fixture):
    store, _, _ = fixture
    service = IdentityService(store)
    check = begin(service, key='old-format')
    request_id = store.principals['a']['runtime_check']['_request_id']
    store.principals['a']['runtime_check_requests'] = [request_id]
    assert begin(service, key='old-format') == check
    begin(service, key='new-format')
    history = store.principals['a']['runtime_check_requests']
    assert isinstance(history, dict) and history[request_id]['retain_until'] > time.time()
    with pytest.raises(Problem, match='runtime_check_superseded'):
        begin(service, key='old-format')
