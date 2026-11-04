"""Isolated contracts for the optional human memory ledger, never a provider call."""
import copy
import threading
import time

import pytest

from talkabout.domain import Problem
from talkabout.memory_repository import MAX_ACTIVE, MAX_FORGET_OPERATIONS, MAX_OPERATIONS, MAX_TOTAL


@pytest.fixture
def memory_postgres_fixture(postgres_fixture):
    import re
    from pathlib import Path
    store, _, _ = postgres_fixture
    # postgres_fixture already proves loopback, fixture marker and actual data dir.
    # Never execute the synthetic DDL against an unvalidated schema/search path.
    if not isinstance(store.schema, str) or not re.fullmatch(r'talkabout_test_[a-f0-9]{32}', store.schema):
        raise AssertionError('experimental_memory_schema_requires_owned_test_schema')
    ddl = Path(__file__).with_name('memory_fixture_schema.sql').read_text(encoding='utf-8')
    with store.connect() as conn:
        actual_schema = conn.execute('SELECT current_schema() AS name').fetchone()['name']
        if actual_schema != store.schema:
            raise AssertionError('experimental_memory_schema_search_path_mismatch')
        conn.execute(ddl)
    return postgres_fixture


def test_synthetic_store_opt_in_is_mandatory(fixture):
    store, _, rid = fixture
    with pytest.raises(Problem, match='synthetic_fixture_required'):
        with store.memory_transaction(rid, 'fixture-human'):
            pass
    for bad in (False, None, 1, 'true'):
        with pytest.raises(Problem, match='synthetic_fixture_required'):
            with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=bad):
                pass
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True):
        pass


def test_postgres_opt_in_and_test_schema_required_before_connect(monkeypatch):
    from talkabout.store import PostgresStore
    store = PostgresStore('unused', schema=None)
    monkeypatch.setattr(store, 'connect', lambda: (_ for _ in ()).throw(AssertionError('unexpected DB connection')))
    with pytest.raises(Problem, match='synthetic_fixture_required'):
        with store.memory_transaction('unused', 'unused'):
            pass
    for schema in (None, 'public', 'talkabout_test_' + '0'*31, 'talkabout_test_' + 'g'*32):
        store.schema = schema
        with pytest.raises(Problem, match='synthetic_fixture_schema_required'):
            with store.memory_transaction('unused', 'unused', synthetic_fixture=True):
                pass


def message(core, room_id, key, text='Synthetic source'):
    return core.mutation('fixture-human', room_id, 'messages', key,
                         {'kind': 'question', 'text': text, 'client_message_id': key})


def args(room, source, **changes):
    return {'key': 'approve-key', 'source_message_id': source['id'], 'source_seq': source['seq'],
            'snapshot_seq': room['last_seq'], 'expected_generation': room['generation'],
            'expected_membership_revision': room['members']['human']['revision'],
            'excerpt': source['text'][:280], 'confirm': True} | changes


def check_blind_forget_contract(store, core, rid):
    from talkabout.domain import HUMAN_PERMISSIONS
    source = message(core, rid, 'forget-source', 'Synthetic sensitive text')
    store.provision({'id': 'other-human', 'owner': 'fixture-owner', 'kind': 'human',
                     'display_name': 'Other Human', 'handle': 'other-human'},
                    'fixture-other-human', HUMAN_PERMISSIONS)
    with store.transaction(rid, 'fixture-human') as (_, room):
        room['members']['other-human'] = {'kind': 'human', 'status': 'active', 'revision': 1,
            'epoch': 1, 'permissions': sorted(HUMAN_PERMISSIONS), 'read_start_seq': 1, 'greeted': False}
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        own = repo.approve(**args(room, source))
        prior_revoked = repo.approve(**args(room, source, key='second-approve'))
        repo.revoke(key='ordinary-revoke', memory_id=prior_revoked['id'], expected_revision=1, confirm=True)
        legacy = repo.approve(**args(room, source, key='legacy-approve'))
        repo._records[legacy['id']].pop('_revocation_slot')
    with store.memory_transaction(rid, 'fixture-other-human', synthetic_fixture=True) as (_, room, repo):
        other = repo.approve(**(args(room, source) | {'expected_membership_revision': room['members']['other-human']['revision']}))
    missing = '00000000-0000-4000-8000-000000000000'
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        original_source = copy.deepcopy(room['messages'][source['id']])
        member = room['members']['human']
        original_floor, original_epoch = member['read_start_seq'], member['epoch']
        # The requester is still active but the original text and its old epoch
        # are no longer visible. Blind deletion must not inspect either.
        room['messages'][source['id']]['text'] = 'source changed'
        member.update(read_start_seq=source['seq']+1, epoch=original_epoch+1)
        before_room = copy.deepcopy(room)
        variants = [('own-forget', own['id']), ('other-forget', other['id']),
                    ('absent-forget', missing), ('revoked-forget', prior_revoked['id']),
                    ('legacy-forget', legacy['id'])]
        for key, mid in variants:
            assert repo.forget_own(key=key, memory_id=mid, confirm=True) == {'accepted': True}
        assert room == before_room
        assert repo._records[own['id']]['state'] == 'revoked' and repo._records[own['id']]['text'] is None
        assert repo._records[own['id']]['_revocation_slot']['audit']['action'] == 'forgotten'
        assert repo._records[other['id']]['text'] == source['text']
        assert repo._records[legacy['id']]['text'] == source['text']
        markers = [v for v in repo._operations.values() if v['operation'] == 'forget-own']
        assert len(markers) == len(variants)
        assert all(set(v) == {'actor_id', 'operation', 'key', 'request_digest', 'response_until'} for v in markers)
        assert all(source['text'] not in str(v) and 'memory_id' not in v for v in markers)
        # A no-op accepted for an absent ID cannot be re-executed if such an ID
        # is subsequently populated: the same key returns only its tombstone.
        repo._records[missing] = copy.deepcopy(repo._records[other['id']])
        repo._records[missing].update(id=missing, approved_by='human')
        repo._revocation_index[missing] = repo._index_value(missing)
        member['epoch'] += 1  # The same actor's prior no-op never re-executes after rejoin.
        assert repo.forget_own(key='absent-forget', memory_id=missing, confirm=True) == {'accepted': True}
        assert repo._records[missing]['state'] == 'approved'
        with pytest.raises(Problem, match='idempotency_conflict'):
            repo.forget_own(key='absent-forget', memory_id=own['id'], confirm=True)
        member.update(read_start_seq=original_floor, epoch=original_epoch)
        room['messages'][source['id']] = original_source


def test_blind_forget_contract_memory_store(fixture):
    check_blind_forget_contract(*fixture)


def test_blind_forget_contract_postgres(memory_postgres_fixture):
    check_blind_forget_contract(*memory_postgres_fixture)


def check_independent_forget_quotas(store, core, rid):
    source = message(core, rid, 'quota-forget-source')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        body = args(room, source)
        own = repo.approve(**body)
        for i in range(MAX_OPERATIONS - 1):
            key = 'regular-filler-' + str(i)
            repo._operations[('human', 'approve', key)] = {
                'actor_id': 'human', 'operation': 'approve', 'key': key, 'member_epoch': 1,
                'request_digest': '0'*64, 'memory_id': own['id'], 'result_revision': 1,
                'response_until': time.time()+300}
        for i in range(MAX_FORGET_OPERATIONS - 1):
            key = format(i, '064x')
            repo._operations[('human', 'forget-own', key)] = {
                'actor_id': 'human', 'operation': 'forget-own', 'key': key,
                'request_digest': '0'*64, 'response_until': time.time()+300}
    # Reopen to prove SQL loading handles both independently bounded populations.
    absent = '00000000-0000-4000-8000-000000000000'
    other = '00000000-0000-4000-8000-000000000001'
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
        assert sum(op != 'forget-own' for _, op, _ in repo._operations) == MAX_OPERATIONS
        assert sum(op == 'forget-own' for _, op, _ in repo._operations) == MAX_FORGET_OPERATIONS-1
        assert repo.approve(**body) == own  # regular replay at full regular quota
        assert repo.forget_own(key='quota-last', memory_id=absent, confirm=True) == {'accepted': True}
        assert repo.forget_own(key='quota-last', memory_id=absent, confirm=True) == {'accepted': True}
        for target in (own['id'], absent, other):
            with pytest.raises(Problem, match='memory_forget_capacity') as error:
                repo.forget_own(key='quota-new-'+target, memory_id=target, confirm=True)
            assert error.value.status == 429
        assert repo._records[own['id']]['state'] == 'approved'
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
        assert len(repo._operations) == MAX_OPERATIONS + MAX_FORGET_OPERATIONS
        assert repo.forget_own(key='quota-last', memory_id=absent, confirm=True) == {'accepted': True}


def test_forget_independent_quotas_memory_store(fixture):
    check_independent_forget_quotas(*fixture)


def test_forget_independent_quotas_postgres(memory_postgres_fixture):
    check_independent_forget_quotas(*memory_postgres_fixture)


def check_saturated_forget(store, core, rid):
    import json
    from talkabout.domain import emit
    source = message(core, rid, 'saturated-forget-source')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        own = repo.approve(**args(room, source))
    with store.transaction(rid, 'fixture-human') as (_, room):
        room['members']['human']['read_start_seq'] = source['seq'] + 1
        room['members']['human']['epoch'] += 1
        while len(room['events']) < 2048:
            emit(room, 'fixture.padding')
        room['_synthetic_padding'] = ''
        size = len(json.dumps(room, ensure_ascii=False).encode('utf-8'))
        assert size < 2097152
        room['_synthetic_padding'] = 'x' * (2097152 - size)
        assert len(json.dumps(room, ensure_ascii=False).encode('utf-8')) == 2097152
    with store.read_transaction(rid, 'fixture-human') as (_, room):
        before = copy.deepcopy(room)
        assert len(room['events']) == 2048 and len(json.dumps(room, ensure_ascii=False).encode('utf-8')) == 2097152
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
        assert repo.forget_own(key='saturated-forget', memory_id=own['id'], confirm=True) == {'accepted': True}
        assert repo.forget_own(key='saturated-forget', memory_id=own['id'], confirm=True) == {'accepted': True}
        assert repo._records[own['id']]['text'] is None
    with store.read_transaction(rid, 'fixture-human') as (_, room):
        assert room == before
    assert store.room_events(rid, before['last_seq']) == []


def check_forget_does_not_reveal_active_capacity(store, core, rid):
    source = message(core, rid, 'hidden-capacity-source')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        memories = [repo.approve(**args(room, source, key='full-'+str(i))) for i in range(MAX_ACTIVE)]
    newer = message(core, rid, 'visible-capacity-source')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        room['members']['human']['read_start_seq'] = newer['seq']
        fresh = args(room, newer, key='new-capacity')
        def assert_full():
            with pytest.raises(Problem, match='memory_active_capacity') as error:
                repo.approve(**fresh)
            assert error.value.status == 429
        assert_full()
        assert repo.forget_own(key='hidden-actual', memory_id=memories[0]['id'], confirm=True) == {'accepted': True}
        assert repo._records[memories[0]['id']]['text'] is None
        assert_full()  # Successful blind deletion does not free an observable slot.
        damaged = repo._records[memories[0]['id']]
        old_id = damaged['id']
        damaged['id'] = 'corrupt-payload-id'
        assert_full()  # The dictionary-key index still consumes quota.
        damaged['id'] = old_id
        assert repo.forget_own(key='hidden-noop', memory_id='00000000-0000-4000-8000-000000000000', confirm=True) == {'accepted': True}
        assert_full()  # A no-op has the same externally observable quota result.
        assert sum(repo._occupies_active(mid, record) for mid, record in repo._records.items()) == MAX_ACTIVE


def test_blind_forget_active_quota_memory_store(fixture):
    check_forget_does_not_reveal_active_capacity(*fixture)


def test_blind_forget_active_quota_postgres(memory_postgres_fixture):
    check_forget_does_not_reveal_active_capacity(*memory_postgres_fixture)


def test_saturated_forget_memory_store(fixture):
    check_saturated_forget(*fixture)


def test_saturated_forget_postgres(memory_postgres_fixture, monkeypatch):
    store, core, rid = memory_postgres_fixture
    store.close()
    monkeypatch.setenv('TALKABOUT_DB_POOL_SIZE', '1')
    check_saturated_forget(store, core, rid)
    assert store.pool_stats()['max_size'] == 1


def test_forget_input_and_actor_validation(fixture):
    store, core, rid = fixture
    source = message(core, rid, 'forget-validation')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        own = repo.approve(**args(room, source))
        valid = {'key': 'valid-key', 'memory_id': own['id'], 'confirm': True}
        for patch in ({'confirm': False}, {'key': ''}, {'key': 'x'*129},
                      {'key': '😀'*33}, {'memory_id': 'not-a-uuid'}, {'memory_id': own['id'].upper()}):
            with pytest.raises(Problem, match='invalid_forget_request') as error:
                repo.forget_own(**(valid | patch))
            assert error.value.status == 422
        assert not any(op == 'forget-own' for _, op, _ in repo._operations)
    with pytest.raises(Problem):
        with store.memory_transaction(rid, 'fixture-a', synthetic_fixture=True):
            pass


def test_forget_memory_store_rollback(fixture):
    store, core, rid = fixture
    source = message(core, rid, 'forget-rollback')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        own = repo.approve(**args(room, source))
    before = (copy.deepcopy(store.rooms[rid]), copy.deepcopy(store.memory_records[rid]),
              copy.deepcopy(store.memory_operations[rid]))
    with pytest.raises(RuntimeError, match='abort'):
        with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
            assert repo.forget_own(key='failed-forget', memory_id=own['id'], confirm=True) == {'accepted': True}
            raise RuntimeError('abort')
    assert (store.rooms[rid], store.memory_records[rid], store.memory_operations[rid]) == before


def test_forget_postgres_write_failure_rolls_back_everything(memory_postgres_fixture):
    from psycopg.errors import CheckViolation
    store, core, rid = memory_postgres_fixture
    source = message(core, rid, 'pg-forget-rollback')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        own = repo.approve(**args(room, source))
    with store.connect() as conn:
        conn.execute("ALTER TABLE room_memory_operations ADD CONSTRAINT fixture_forget_reject CHECK (operation <> 'forget-own') NOT VALID")
    try:
        with pytest.raises(CheckViolation):
            with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
                assert repo.forget_own(key='failed-forget', memory_id=own['id'], confirm=True) == {'accepted': True}
    finally:
        with store.connect() as conn:
            conn.execute('ALTER TABLE room_memory_operations DROP CONSTRAINT fixture_forget_reject')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
        assert repo.get(own['id'])['text'] == source['text']
        assert repo._records[own['id']]['_revocation_slot']['state'] == 'reserved'
        assert not any(op == 'forget-own' for _, op, _ in repo._operations)
        assert repo.forget_own(key='failed-forget', memory_id=own['id'], confirm=True) == {'accepted': True}


def test_forget_expired_noop_and_cross_room(fixture):
    import hashlib
    store, core, rid = fixture
    source = message(core, rid, 'cross-forget-source')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        own = repo.approve(**args(room, source))
    another = core.create('fixture-human', {'topic': 'Synthetic second room'}, 'another-room')['id']
    with store.memory_transaction(another, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
        assert repo.forget_own(key='cross-room', memory_id=own['id'], confirm=True) == {'accepted': True}
        key_hash = hashlib.sha256(b'cross-room').hexdigest()
        repo._operations[('human', 'forget-own', key_hash)]['response_until'] = 0
        with pytest.raises(Problem, match='idempotency_retired') as error:
            repo.forget_own(key='cross-room', memory_id=own['id'], confirm=True)
        assert error.value.status == 410
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
        assert repo.get(own['id'])['text'] == source['text']


def check_expiry_reauthentication(store, core, rid, monkeypatch, kind):
    from types import SimpleNamespace
    from talkabout.domain import token_hash
    import talkabout.store as store_module
    source = message(core, rid, 'expiry-' + kind)
    current = time.time()
    deadline = current + 10
    if kind == 'session':
        scopes = store.auth('fixture-human')['scopes']
        if hasattr(store, 'principals'):
            store.principals['human']['account_scopes'] = list(scopes)
        else:
            from psycopg.types.json import Jsonb
            with store.connect() as conn:
                principal = conn.execute('SELECT payload FROM principals WHERE id=%s', ('human',)).fetchone()['payload']
                principal['account_scopes'] = list(scopes)
                conn.execute('UPDATE principals SET payload=%s WHERE id=%s', (Jsonb(principal), 'human'))
        store.save_session(token_hash('synthetic-expiry'), {'principal_id': 'human', 'expires_at': deadline, 'csrf_hash': 'synthetic'})
        token = 'session:synthetic-expiry'
    else:
        token = 'fixture-human'
        if hasattr(store, 'credentials'):
            store.credentials[token_hash(token)]['expires_at'] = deadline
        else:
            with store.connect() as conn:
                conn.execute('UPDATE credentials SET expires_at=%s WHERE token_hash=%s', (deadline, token_hash(token)))
    with store.read_transaction(rid, token) as (_, room):
        before_room = copy.deepcopy(room)
    clock = [current]
    monkeypatch.setattr(store_module, 'time', SimpleNamespace(time=lambda: clock[0]))
    with pytest.raises(Problem, match='unauthorized'):
        with store.memory_transaction(rid, token, synthetic_fixture=True) as (_, room, repo):
            body = args(room, source)
            receipt = repo.approve(**body)
            assert repo.get(receipt['id'])['text'] == source['text']
            clock[0] = deadline  # Exactly at expiry is invalid.
            calls = [lambda: repo.get(receipt['id']), lambda: repo.list(), lambda: repo.approve(**body),
                     lambda: repo.revoke(key='expired-revoke', memory_id=receipt['id'], expected_revision=1, confirm=True),
                     lambda: repo.forget_own(key='expired-forget', memory_id=receipt['id'], confirm=True)]
            for call in calls:
                with pytest.raises(Problem, match='unauthorized'):
                    call()  # Even if caught, the transaction's final check fails.
            clock[0] = current  # A clock correction cannot rescue this transaction.
    clock[0] = current
    with store.read_transaction(rid, token) as (_, room):
        assert room == before_room
    with store.memory_transaction(rid, token, synthetic_fixture=True) as (_, _, repo):
        assert not repo._records and not repo._operations
    clock[0] = deadline
    with pytest.raises(Problem, match='unauthorized'):
        with store.memory_transaction(rid, token, synthetic_fixture=True):
            pass
    clock[0] = current


@pytest.mark.parametrize('kind', ['credential', 'session'])
def test_expiry_reauthentication_memory_store(fixture, monkeypatch, kind):
    check_expiry_reauthentication(*fixture, monkeypatch, kind)


@pytest.mark.parametrize('kind', ['credential', 'session'])
def test_expiry_reauthentication_postgres(memory_postgres_fixture, monkeypatch, kind):
    store, core, rid = memory_postgres_fixture
    store.close()
    monkeypatch.setenv('TALKABOUT_DB_POOL_SIZE', '1')
    check_expiry_reauthentication(store, core, rid, monkeypatch, kind)
    assert store.pool_stats()['max_size'] == 1


def test_expiry_after_pg_writes_rolls_back(memory_postgres_fixture, monkeypatch):
    from types import SimpleNamespace
    import talkabout.store as store_module
    store, core, rid = memory_postgres_fixture
    source = message(core, rid, 'post-write-expiry')
    with store.read_transaction(rid, 'fixture-human') as (_, room):
        before_room = copy.deepcopy(room)
    clock = [time.time()]
    monkeypatch.setattr(store_module, 'time', SimpleNamespace(time=lambda: clock[0]))
    original = store._flush
    def expire_after_flush(*args, **kwargs):
        result = original(*args, **kwargs)
        clock[0] += 10000
        return result
    monkeypatch.setattr(store, '_flush', expire_after_flush)
    with pytest.raises(Problem, match='unauthorized'):
        with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
            repo.approve(**args(room, source))
    clock[0] -= 10000
    monkeypatch.setattr(store, '_flush', original)
    with store.read_transaction(rid, 'fixture-human') as (_, room):
        assert room == before_room
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
        assert not repo._records and not repo._operations


def check_approval_key_storage_and_legacy(store, core, rid, monkeypatch):
    import hashlib
    import talkabout.memory_repository as module
    source = message(core, rid, 'approval-key-storage')
    newer = message(core, rid, 'approval-key-newer')
    raw = 'synthetic-secret-approval-key'
    legacy_key = hashlib.sha256(b'key-with-same-hash-column').hexdigest()
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        body = args(room, source, key=raw)
        receipt = repo.approve(**body)
        assert repo.approve(**body) == receipt
        markers = [v for v in repo._operations.values() if v.get('operation') == 'approve:v1']
        assert len(markers) == 1 and markers[0]['key'] == hashlib.sha256(raw.encode()).hexdigest()
        assert raw not in str(repo._operations) and raw not in str(room['events']) and raw not in str(repo._records)
        # Build a genuine historical raw-key marker *before* persistence; no
        # production migration or schema changes are involved.
        old_body = args(room, source, key=legacy_key)
        old_receipt = repo.approve(**old_body)
        hashed_key = ('human', 'approve:v1', module._approval_key_hash(legacy_key))
        legacy = repo._operations.pop(hashed_key)
        legacy = {k: v for k, v in legacy.items() if k != 'key_check_sha256'}
        legacy.update(operation='approve', key=legacy_key)
        repo._operations[('human', 'approve', legacy_key)] = legacy
        assert repo.approve(**old_body) == old_receipt
        with pytest.raises(Problem, match='idempotency_conflict'):
            repo.approve(**(old_body | {'excerpt': 'different'}))
        expiry = legacy['response_until']
        legacy['response_until'] = 0
        with pytest.raises(Problem, match='idempotency_retired'):
            repo.approve(**old_body)
        legacy['response_until'] = expiry
        # A legacy raw key that happens to equal another key's hash remains in a
        # separate namespace; it cannot impersonate the new hashed operation.
        second_body = args(room, source, key='key-with-same-hash-column')
        second = repo.approve(**second_body)
        assert second['id'] != old_receipt['id']
        newer_receipt = repo.approve(**args(room, newer, key='newer-mixed-target'))
    if not hasattr(store, 'credentials'):
        with store.connect() as conn:
            rows = conn.execute('SELECT operation,key,payload FROM room_memory_operations WHERE room_id=%s', (rid,)).fetchall()
            assert raw not in str(rows)
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        assert repo.approve(**old_body) == old_receipt
        assert repo.approve(**second_body) == second
        # Both namespaces for the same key are ambiguous, regardless of which
        # target was requested, and both stored targets' ACLs are checked first.
        legacy = repo._operations[('human', 'approve', legacy_key)]
        existing = repo._operations[('human', 'approve:v1', module._approval_key_hash('key-with-same-hash-column'))]
        collision = copy.deepcopy(existing)
        collision.update(key=module._approval_key_hash(legacy_key), memory_id=newer_receipt['id'])
        repo._operations[('human', 'approve:v1', module._approval_key_hash(legacy_key))] = collision
        with pytest.raises(Problem, match='memory_operation_ambiguous'):
            repo.approve(**old_body)
        floor = room['members']['human']['read_start_seq']
        room['members']['human']['read_start_seq'] = newer['seq']
        with pytest.raises(Problem, match='not_found'):  # legacy hidden, hashed visible
            repo.approve(**(old_body | {'excerpt': 'different'}))
        legacy['memory_id'], collision['memory_id'] = newer_receipt['id'], old_receipt['id']
        with pytest.raises(Problem, match='not_found'):  # legacy visible, hashed hidden
            repo.approve(**old_body)
        room['members']['human']['read_start_seq'] = floor


def test_approval_key_storage_and_legacy_memory_store(fixture, monkeypatch):
    check_approval_key_storage_and_legacy(*fixture, monkeypatch)


def test_approval_key_storage_and_legacy_postgres(memory_postgres_fixture, monkeypatch):
    check_approval_key_storage_and_legacy(*memory_postgres_fixture, monkeypatch)


def check_approval_hash_collision_is_fail_closed(store, core, rid, monkeypatch):
    import talkabout.memory_repository as module
    source = message(core, rid, 'hash-collision')
    monkeypatch.setattr(module, '_approval_key_hash', lambda key: 'a' * 64)
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        body = args(room, source, key='collision-a')
        repo.approve(**body)
        with pytest.raises(Problem, match='idempotency_conflict'):
            repo.approve(**(body | {'key': 'collision-b'}))


def test_approval_hash_collision_memory_store(fixture, monkeypatch):
    check_approval_hash_collision_is_fail_closed(*fixture, monkeypatch)


def test_approval_hash_collision_postgres(memory_postgres_fixture, monkeypatch):
    check_approval_hash_collision_is_fail_closed(*memory_postgres_fixture, monkeypatch)


def check_live_auth_binding(store, core, rid, monkeypatch, change):
    from talkabout.domain import token_hash
    source = message(core, rid, 'auth-binding-' + change)
    with store.read_transaction(rid, 'fixture-human') as (_, room):
        before_room = copy.deepcopy(room)
    armed = [False]
    memory = hasattr(store, 'credentials')
    old_credential = copy.deepcopy(store.credentials[token_hash('fixture-human')]) if memory else None
    old_principal = copy.deepcopy(store.principals['human']) if memory else None
    def mutate_memory():
        credential = store.credentials[token_hash('fixture-human')]
        if change == 'principal':
            credential['id'] = 'a'
        elif change == 'owner':
            store.principals['human']['owner'] = 'another-owner'
        elif change == 'room':
            credential['room_id'] = 'another-room'
        else:
            credential['scopes'] = [s for s in credential['scopes'] if s != 'room:read']
    if memory:
        original = store.auth
        def current(token):
            if armed[0]:
                armed[0] = False
                mutate_memory()
            return original(token)
        monkeypatch.setattr(store, 'auth', current)
    else:
        from psycopg.types.json import Jsonb
        original = store._auth
        def current(conn, token):
            if armed[0]:
                armed[0] = False
                if change == 'principal':
                    conn.execute('UPDATE credentials SET principal_id=%s WHERE token_hash=%s', ('a', token_hash(token)))
                elif change == 'owner':
                    principal = conn.execute('SELECT payload FROM principals WHERE id=%s', ('human',)).fetchone()['payload']
                    principal['owner'] = 'another-owner'
                    conn.execute('UPDATE principals SET payload=%s WHERE id=%s', (Jsonb(principal), 'human'))
                elif change == 'room':
                    conn.execute('UPDATE credentials SET room_id=%s WHERE token_hash=%s', ('another-room', token_hash(token)))
                else:
                    scopes = conn.execute('SELECT scopes FROM credentials WHERE token_hash=%s', (token_hash(token),)).fetchone()['scopes']
                    conn.execute('UPDATE credentials SET scopes=%s WHERE token_hash=%s', (Jsonb([s for s in scopes if s != 'room:read']), token_hash(token)))
            return original(conn, token)
        monkeypatch.setattr(store, '_auth', current)
    expected = {'principal': 'unauthorized', 'owner': 'not_found', 'room': 'not_found', 'scope': 'forbidden'}[change]
    try:
        with pytest.raises(Problem, match=expected):
            with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
                repo.approve(**args(room, source))
                armed[0] = True
                with pytest.raises(Problem, match=expected):
                    repo.list()
    finally:
        if memory:
            store.credentials[token_hash('fixture-human')] = old_credential
            store.principals['human'] = old_principal
    with store.read_transaction(rid, 'fixture-human') as (_, room):
        assert room == before_room
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
        assert not repo._records and not repo._operations


@pytest.mark.parametrize('change', ['principal', 'owner', 'room', 'scope'])
def test_live_auth_binding_memory_store(fixture, monkeypatch, change):
    check_live_auth_binding(*fixture, monkeypatch, change)


@pytest.mark.parametrize('change', ['principal', 'owner', 'room', 'scope'])
def test_live_auth_binding_postgres(memory_postgres_fixture, monkeypatch, change):
    check_live_auth_binding(*memory_postgres_fixture, monkeypatch, change)


def check_ident_collision_preserves_tombstone(store, core, rid, monkeypatch):
    import talkabout.memory_repository as module
    source = message(core, rid, 'id-collision-source')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        original = repo.approve(**args(room, source))
        revoked = repo.revoke(key='old-revoke-key', memory_id=original['id'], expected_revision=1, confirm=True)
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        before = copy.deepcopy((room, repo._records, repo._revocation_index, repo._operations))
        monkeypatch.setattr(module, 'ident', lambda: original['id'])
        with pytest.raises(Problem, match='memory_id_collision'):
            repo.approve(**args(room, source, key='collision-approval'))
        assert (room, repo._records, repo._revocation_index, repo._operations) == before
        assert repo.revoke(key='old-revoke-key', memory_id=original['id'], expected_revision=1, confirm=True) == revoked
        assert (room, repo._records, repo._revocation_index, repo._operations) == before
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        assert (room, repo._records, repo._revocation_index, repo._operations) == before
        assert repo.revoke(key='old-revoke-key', memory_id=original['id'], expected_revision=1, confirm=True) == revoked


def test_ident_collision_preserves_tombstone_memory(fixture, monkeypatch):
    check_ident_collision_preserves_tombstone(*fixture, monkeypatch)


def test_ident_collision_preserves_tombstone_postgres(memory_postgres_fixture, monkeypatch):
    check_ident_collision_preserves_tombstone(*memory_postgres_fixture, monkeypatch)


def test_postgres_unexpected_new_pk_never_updates_existing(memory_postgres_fixture, monkeypatch):
    import talkabout.memory_repository as module
    store, core, rid = memory_postgres_fixture
    source = message(core, rid, 'db-unexpected-collision')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        original = repo.approve(**args(room, source))
        revoked = repo.revoke(key='preserve-used-key', memory_id=original['id'], expected_revision=1, confirm=True)
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        before = copy.deepcopy((room, repo._records, repo._revocation_index, repo._operations))
    # Simulate a stale/incorrect in-memory view while DB still holds the PK.
    monkeypatch.setattr(module, 'ident', lambda: original['id'])
    with pytest.raises(Problem, match='memory_id_collision'):
        with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
            repo._records.pop(original['id'])
            repo._revocation_index.pop(original['id'])
            repo.approve(**args(room, source, key='unexpected-db-pk'))
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        assert (room, repo._records, repo._revocation_index, repo._operations) == before
        assert repo.revoke(key='preserve-used-key', memory_id=original['id'], expected_revision=1, confirm=True) == revoked
    # Likewise a supposedly-new index cannot upsert an existing used index.
    with pytest.raises(Problem, match='memory_index_conflict'):
        with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
            repo._revocation_index[original['id']] = repo._index_value(original['id'])
            repo._created_index_ids.add(original['id'])
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        assert (room, repo._records, repo._revocation_index, repo._operations) == before
        assert repo.revoke(key='preserve-used-key', memory_id=original['id'], expected_revision=1, confirm=True) == revoked
    # A newly asserted PK must still insert even if its payload is byte-identical.
    from talkabout.memory_repository import _approval_key_hash
    with pytest.raises(Problem, match='memory_index_conflict'):
        with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
            repo._created_index_ids.add(original['id'])
    with pytest.raises(Problem, match='memory_operation_conflict'):
        with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
            repo._created_operation_keys.add(('human', 'approve:v1', _approval_key_hash('approve-key')))
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        assert (room, repo._records, repo._revocation_index, repo._operations) == before


@pytest.mark.parametrize('operation', ['approve', 'forget'])
def test_postgres_unexpected_marker_pk_rolls_back(memory_postgres_fixture, operation):
    import hashlib
    from talkabout.memory_repository import _approval_key_hash
    store, core, rid = memory_postgres_fixture
    source = message(core, rid, 'marker-pk-' + operation)
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        original = repo.approve(**args(room, source))
        if operation == 'forget':
            repo.forget_own(key='marker-existing', memory_id='00000000-0000-4000-8000-000000000000', confirm=True)
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        before = copy.deepcopy((room, repo._records, repo._revocation_index, repo._operations))
    with pytest.raises(Problem, match='memory_operation_conflict'):
        with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
            if operation == 'approve':
                repo._operations.pop(('human', 'approve:v1', _approval_key_hash('approve-key')))
                repo.approve(**args(room, source))
            else:
                repo._operations.pop(('human', 'forget-own', hashlib.sha256(b'marker-existing').hexdigest()))
                repo.forget_own(key='marker-existing', memory_id=original['id'], confirm=True)
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        assert (room, repo._records, repo._revocation_index, repo._operations) == before
        assert repo.get(original['id'])['text'] == source['text']


@pytest.mark.parametrize('table', ['room_memories', 'room_memory_revoke_index', 'room_memory_operations'])
def test_postgres_zero_row_update_rolls_back(memory_postgres_fixture, table):
    from psycopg import sql
    from talkabout.memory_repository import _approval_key_hash
    store, core, rid = memory_postgres_fixture
    source = message(core, rid, 'update-rowcount-' + table)
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        receipt = repo.approve(**args(room, source))
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        before = copy.deepcopy((room, repo._records, repo._revocation_index, repo._operations))
    with store.connect() as conn:
        conn.execute('CREATE FUNCTION fixture_skip_update() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END $$')
        conn.execute(sql.SQL('CREATE TRIGGER fixture_skip_update BEFORE UPDATE ON {} FOR EACH ROW EXECUTE FUNCTION fixture_skip_update()').format(sql.Identifier(table)))
    try:
        with pytest.raises(Problem, match='memory_storage_inconsistent'):
            with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
                if table == 'room_memory_operations':
                    repo._operations[('human', 'approve:v1', _approval_key_hash('approve-key'))]['response_until'] = 0
                else:
                    repo.revoke(key='skip-update', memory_id=receipt['id'], expected_revision=1, confirm=True)
    finally:
        with store.connect() as conn:
            conn.execute(sql.SQL('DROP TRIGGER fixture_skip_update ON {}').format(sql.Identifier(table)))
            conn.execute('DROP FUNCTION fixture_skip_update()')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        assert (room, repo._records, repo._revocation_index, repo._operations) == before


def check_index_slot_isolation(store, core, rid):
    source = message(core, rid, 'index-isolation')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        first = repo.approve(**args(room, source))
        second = repo.approve(**args(room, source, key='index-approval-two'))
        repo.revoke(key='used-key', memory_id=first['id'], expected_revision=1, confirm=True)
        first_index = copy.deepcopy(repo._revocation_index[first['id']])
        repo._records[first['id']]['_revocation_slot'] = {'schema': 999}
        with pytest.raises(Problem, match='memory_revoke_slot_unavailable'):
            repo.revoke(key='used-key', memory_id=second['id'], expected_revision=1, confirm=True)
        assert repo.revoke(key='independent-key', memory_id=second['id'], expected_revision=1, confirm=True) == {'id': second['id'], 'revision': 2}
        assert repo.revoke(key='independent-key', memory_id=second['id'], expected_revision=1, confirm=True)['revision'] == 2
        assert repo._revocation_index[first['id']] == first_index


def test_index_slot_isolation_memory(fixture):
    check_index_slot_isolation(*fixture)


def test_index_slot_isolation_postgres(memory_postgres_fixture):
    check_index_slot_isolation(*memory_postgres_fixture)


def check_index_damage_fail_closed(store, core, rid):
    source = message(core, rid, 'index-damage')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        body = args(room, source)
        first = repo.approve(**body)
        second = repo.approve(**args(room, source, key='damage-two'))
        repo.revoke(key='damage-used', memory_id=first['id'], expected_revision=1, confirm=True)
        original = copy.deepcopy(repo._revocation_index)
        changes = [
            lambda: repo._revocation_index.pop(first['id']),
            lambda: repo._revocation_index[first['id']].update(checksum='0'*64),
            lambda: repo._revocation_index[first['id']].update(state='invalid'),
            lambda: repo._revocation_index.update({second['id']: repo._index_value(second['id'], original[first['id']]['receipt'])}),
        ]
        for corrupt in changes:
            repo._revocation_index.clear()
            repo._revocation_index.update(copy.deepcopy(original))
            corrupt()
            calls = [lambda: repo.get(second['id']), lambda: repo.list(), lambda: repo.approve(**body),
                     lambda: repo.revoke(key='unused', memory_id=second['id'], expected_revision=1, confirm=True),
                     lambda: repo.forget_own(key='invalid-index', memory_id=second['id'], confirm=True)]
            for call in calls:
                with pytest.raises(Problem, match='memory_revoke_index_unavailable') as error:
                    call()
                assert error.value.status == 503
        repo._revocation_index.clear()
        repo._revocation_index.update(original)


def test_index_damage_memory(fixture):
    check_index_damage_fail_closed(*fixture)


def test_index_damage_postgres(memory_postgres_fixture):
    check_index_damage_fail_closed(*memory_postgres_fixture)


def check_stale_and_binding_fail_closed(store, core, rid):
    source = message(core, rid, 'stale-index')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        body = args(room, source)
        first = repo.approve(**body)
        second = repo.approve(**args(room, source, key='stale-two'))
        before = copy.deepcopy(repo._records[first['id']])
        repo.revoke(key='stale-used', memory_id=first['id'], expected_revision=1, confirm=True)
        used = copy.deepcopy(repo._revocation_index[first['id']])
        repo._records[first['id']] = copy.deepcopy(before)
        with pytest.raises(Problem, match='not_found'):
            repo.get(first['id'])
        assert all(r['id'] != first['id'] for r in repo.list())
        with pytest.raises(Problem, match='not_found'):
            repo.approve(**body)
        assert repo.forget_own(key='stale-noop', memory_id=first['id'], confirm=True) == {'accepted': True}
        assert repo._records[first['id']]['text'] == before['text'] and repo._revocation_index[first['id']] == used
        with pytest.raises(Problem, match='not_found'):
            repo.revoke(key='stale-used', memory_id=second['id'], expected_revision=1, confirm=True)
        repo._records[first['id']] = copy.deepcopy(before)
        for field, wrong in [('id', first['id']), ('room_id', 'wrong-room')]:
            original = repo._records[second['id']][field]
            repo._records[second['id']][field] = wrong
            with pytest.raises(Problem, match='not_found'):
                repo.get(second['id'])
            assert all(r['id'] != second['id'] for r in repo.list())
            with pytest.raises(Problem, match='not_found'):
                repo.approve(**args(room, source, key='stale-two'))
            with pytest.raises(Problem, match='not_found'):
                repo.revoke(key='binding-test', memory_id=second['id'], expected_revision=1, confirm=True)
            assert repo.forget_own(key='binding-'+field, memory_id=second['id'], confirm=True) == {'accepted': True}
            repo._records[second['id']][field] = original
        # Blind forgotten index blocks stale restoration without changing ACK or
        # freeing capacity, even when the payload ID/state are later corrupted.
        repo._records[first['id']] = before
        before_second = copy.deepcopy(repo._records[second['id']])
        assert repo.forget_own(key='blind-actual', memory_id=second['id'], confirm=True) == {'accepted': True}
        forgotten = copy.deepcopy(repo._revocation_index[second['id']])
        assert forgotten['state'] == 'forgotten'
        repo._records[second['id']] = before_second
        with pytest.raises(Problem, match='not_found'):
            repo.get(second['id'])
        with pytest.raises(Problem, match='not_found'):
            repo.approve(**args(room, source, key='stale-two'))
        assert repo.forget_own(key='blind-noop-after-restore', memory_id=second['id'], confirm=True) == {'accepted': True}
        assert repo._revocation_index[second['id']] == forgotten
        repo._records[second['id']].update(id='corrupt', state='revoked', text=None)
        assert repo._occupies_active(second['id'], repo._records[second['id']])
        repo._records[second['id']] = before_second


def test_stale_and_binding_memory(fixture):
    check_stale_and_binding_fail_closed(*fixture)


def test_stale_and_binding_postgres(memory_postgres_fixture):
    check_stale_and_binding_fail_closed(*memory_postgres_fixture)


def check_revoke_key_collision(store, core, rid, monkeypatch):
    import talkabout.memory_repository as module
    source = message(core, rid, 'revoke-key-collision')
    monkeypatch.setattr(module, '_revoke_key_hash', lambda key: 'b'*64)
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        receipt = repo.approve(**args(room, source))
        body = {'key': 'key-one', 'memory_id': receipt['id'], 'expected_revision': 1, 'confirm': True}
        repo.revoke(**body)
        with pytest.raises(Problem, match='idempotency_conflict'):
            repo.revoke(**(body | {'key': 'key-two'}))
        room['members']['human']['read_start_seq'] = source['seq'] + 1
        with pytest.raises(Problem, match='not_found'):
            repo.revoke(**(body | {'key': 'key-two'}))
        room['members']['human']['read_start_seq'] = source['seq']


def test_revoke_key_collision_memory(fixture, monkeypatch):
    check_revoke_key_collision(*fixture, monkeypatch)


def test_revoke_key_collision_postgres(memory_postgres_fixture, monkeypatch):
    check_revoke_key_collision(*memory_postgres_fixture, monkeypatch)


@pytest.mark.parametrize('action', ['revoke', 'forget'])
def test_postgres_index_write_rollback(memory_postgres_fixture, action):
    from psycopg.errors import CheckViolation
    store, core, rid = memory_postgres_fixture
    source = message(core, rid, 'index-write-rollback')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        receipt = repo.approve(**args(room, source))
    target_state = 'used' if action == 'revoke' else 'forgotten'
    with store.connect() as conn:
        conn.execute("ALTER TABLE room_memory_revoke_index ADD CONSTRAINT fixture_reject_index CHECK (payload->>'state' <> '" + target_state + "') NOT VALID")
    try:
        with pytest.raises(CheckViolation):
            with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
                if action == 'revoke':
                    repo.revoke(key='rollback-index', memory_id=receipt['id'], expected_revision=1, confirm=True)
                else:
                    repo.forget_own(key='rollback-index', memory_id=receipt['id'], confirm=True)
    finally:
        with store.connect() as conn:
            conn.execute('ALTER TABLE room_memory_revoke_index DROP CONSTRAINT fixture_reject_index')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
        assert repo.get(receipt['id'])['text'] == source['text']
        assert repo._revocation_index[receipt['id']]['state'] == 'reserved'
        assert not any(op == 'forget-own' for _, op, _ in repo._operations)
        if action == 'revoke':
            repo.revoke(key='rollback-index', memory_id=receipt['id'], expected_revision=1, confirm=True)
        else:
            repo.forget_own(key='rollback-index', memory_id=receipt['id'], confirm=True)


def test_approval_replay_revoke_and_no_text_event(fixture):
    store, core, rid = fixture
    source = message(core, rid, 'a', 'A synthetic fact')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        body = args(room, source)
        receipt = repo.approve(**body)
        assert repo.approve(**body) == receipt
        assert repo.get(receipt['id'])['text'] == source['text']
        assert len(repo.list()) == 1
        with pytest.raises(Problem, match='idempotency_conflict'):
            repo.approve(**(body | {'excerpt': 'other'}))
    event = store.rooms[rid]['events'][-1]
    assert event['type'] == 'memory.changed' and source['text'] not in str(event)
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
        assert repo.revoke(key='revoke-key', memory_id=receipt['id'], expected_revision=1, confirm=True) == {'id': receipt['id'], 'revision': 2}
        assert repo.revoke(key='revoke-key', memory_id=receipt['id'], expected_revision=1, confirm=True)['revision'] == 2
        with pytest.raises(Problem, match='not_found'):
            repo.approve(**body)  # A revoked memory cannot be exposed by approval replay.
        assert repo.list() == []
        with pytest.raises(Problem, match='not_found'):
            repo.get(receipt['id'])
    assert store.memory_records[rid][receipt['id']]['text'] is None
    assert source['text'] not in str(store.memory_operations[rid])
    with pytest.raises(Problem, match='memory_transaction_closed'):
        repo.list()


def check_replay_projection(store, core, rid):
    source = message(core, rid, 'replay-acl', 'Synthetic replay source')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        body = args(room, source)
        receipt = repo.approve(**body)
        revoke = {'key': 'replay-revoke', 'memory_id': receipt['id'], 'expected_revision': 1, 'confirm': True}
        source_id = source['id']
        original = copy.deepcopy(room['messages'][source_id])
        original_floor = room['members']['human']['read_start_seq']
        def deny_under_changed_source(call):
            variants = [
                lambda: room['members']['human'].update(read_start_seq=source['seq'] + 1),
                lambda: room['messages'][source_id].update(text='changed original'),
                lambda: room['messages'][source_id].update(seq=source['seq'] + 1),
                lambda: room['messages'][source_id].update(actor_id='other-human'),
                lambda: room['messages'][source_id].update(actor_kind='agent'),
                lambda: room['messages'][source_id].update(attachment_refs=[{'file_id': 'synthetic'}]),
                lambda: room['messages'].pop(source_id),
            ]
            for mutate in variants:
                room['messages'][source_id] = copy.deepcopy(original)
                room['members']['human']['read_start_seq'] = original_floor
                mutate()
                with pytest.raises(Problem, match='not_found'):
                    call()
            room['messages'][source_id] = copy.deepcopy(original)
            room['members']['human']['read_start_seq'] = original_floor
        assert repo.approve(**body) == receipt
        deny_under_changed_source(lambda: repo.approve(**body))
        revoked = repo.revoke(**revoke)
        assert revoked == {'id': receipt['id'], 'revision': 2}
        assert repo.revoke(**revoke) == revoked
        with pytest.raises(Problem, match='not_found'):
            repo.approve(**body)
        deny_under_changed_source(lambda: repo.revoke(**revoke))
        assert repo.revoke(**revoke) == revoked
        assert len([e for e in room['events'] if e['type'] == 'memory.changed']) == 1


def test_replay_projects_current_source_acl(fixture):
    check_replay_projection(*fixture)


def test_postgres_replay_projects_current_source_acl(memory_postgres_fixture):
    check_replay_projection(*memory_postgres_fixture)


def check_hidden_replay_error_priority(store, core, rid):
    from talkabout.domain import digest
    source = message(core, rid, 'hidden-priority')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        body = args(room, source)
        approved = repo.approve(**body)
        from talkabout.memory_repository import _approval_key_hash
        approve_marker = repo._operations[('human', 'approve:v1', _approval_key_hash(body['key']))]
        member = room['members']['human']
        original_floor, original_epoch = member['read_start_seq'], member['epoch']
        old_expiry = approve_marker['response_until']
        member.update(read_start_seq=source['seq']+1, epoch=original_epoch+1)
        approve_marker['response_until'] = 0
        with pytest.raises(Problem, match='not_found'):
            repo.approve(**(body | {'excerpt': 'different'}))
        member.update(read_start_seq=original_floor, epoch=original_epoch)
        approve_marker['response_until'] = old_expiry
        revoke = {'key': 'priority-revoke', 'memory_id': approved['id'], 'expected_revision': 1, 'confirm': True}
        repo.revoke(**revoke)
        slot_receipt = repo._records[approved['id']]['_revocation_slot']['receipt']
        old_expiry = slot_receipt['response_until']
        member.update(read_start_seq=source['seq']+1, epoch=original_epoch+1)
        slot_receipt['response_until'] = 0
        with pytest.raises(Problem, match='not_found'):
            repo.revoke(**(revoke | {'memory_id': 'different-valid-string'}))
        member.update(read_start_seq=original_floor, epoch=original_epoch)
        slot_receipt['response_until'] = old_expiry
        # A historical marker without any reserved slot uses the stored target,
        # not the potentially different memory_id supplied in this request.
        old = repo._records[approved['id']]
        old.pop('_revocation_slot')
        legacy_body = {'memory_id': approved['id'], 'expected_revision': 1, 'confirm': True}
        repo._operations[('human', 'revoke', 'legacy-priority')] = {
            'actor_id': 'human', 'operation': 'revoke', 'key': 'legacy-priority',
            'member_epoch': original_epoch, 'request_digest': digest(['human', rid, 'revoke', legacy_body]),
            'memory_id': approved['id'], 'result_revision': 2, 'response_until': 0}
        member.update(read_start_seq=source['seq']+1, epoch=original_epoch+1)
        with pytest.raises(Problem, match='not_found'):
            repo.revoke(key='legacy-priority', **(legacy_body | {'memory_id': 'different-valid-string'}))
        member.update(read_start_seq=original_floor, epoch=original_epoch)


def test_hidden_replay_error_priority_memory_store(fixture):
    check_hidden_replay_error_priority(*fixture)


def test_hidden_replay_error_priority_postgres(memory_postgres_fixture):
    check_hidden_replay_error_priority(*memory_postgres_fixture)


def test_source_authorization_and_fencing(fixture):
    store, core, rid = fixture
    source = message(core, rid, 'b', 'Visible exact text')
    with pytest.raises(Problem):
        with store.memory_transaction(rid, 'fixture-a', synthetic_fixture=True):
            pass
    from talkabout.domain import token_hash
    scopes = store.credentials[token_hash('fixture-human')]['scopes']
    scopes.remove('room:read')
    with pytest.raises(Problem, match='forbidden'):
        with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True):
            pass
    scopes.append('room:read')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        body = args(room, source)
        for updates, code in [({'confirm': False}, 'memory_confirmation_required'),
                              ({'excerpt': 'wrong'}, 'invalid_memory_excerpt'),
                              ({'source_seq': source['seq'] + 1}, 'invalid_memory_source'),
                              ({'expected_generation': -1}, 'stale_context'),
                              ({'expected_membership_revision': True}, 'stale_context')]:
            with pytest.raises(Problem, match=code):
                repo.approve(**(body | updates))
        room['members']['human']['read_start_seq'] = source['seq'] + 1
        with pytest.raises(Problem, match='memory_source_not_found'):
            repo.approve(**body)
        room['members']['human']['read_start_seq'] = source['seq']
        room['messages'][source['id']]['attachment_refs'] = [{'file_id': 'synthetic'}]
        with pytest.raises(Problem, match='memory_source_not_found'):
            repo.approve(**body)
        room['messages'][source['id']].pop('attachment_refs')
        room['messages'][source['id']]['actor_kind'] = 'agent'
        with pytest.raises(Problem, match='memory_source_not_found'):
            repo.approve(**body)


def test_later_source_or_member_changes_hide_whole_memory(fixture):
    store, core, rid = fixture
    source = message(core, rid, 'c')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        receipt = repo.approve(**args(room, source))
    store.rooms[rid]['messages'][source['id']]['text'] = 'Changed'
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
        assert repo.list() == []
        with pytest.raises(Problem, match='not_found'):
            repo.get(receipt['id'])
    store.rooms[rid]['members']['human']['status'] = 'removed'
    with pytest.raises(Problem, match='not_found'):
        with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True):
            pass


def test_rollback_atomic_and_separate_from_room_payload(fixture):
    store, core, rid = fixture
    source = message(core, rid, 'd', 'Rollback text')
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(RuntimeError):
        with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
            repo.approve(**args(room, source))
            raise RuntimeError('abort')
    assert store.rooms[rid] == before and not store.memory_records.get(rid)
    assert not store.memory_operations.get(rid)
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        repo.approve(**args(room, source))
    assert 'memory_records' not in store.rooms[rid] and 'memory_operations' not in store.rooms[rid]


def test_limits_replay_expiry_and_epoch(fixture):
    from talkabout.memory_repository import _approval_key_hash
    store, core, rid = fixture
    source = message(core, rid, 'e', '😀' * 281)
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        body = args(room, source)
        first = repo.approve(**body)
        assert len(repo.get(first['id'])['text'].encode('utf8')) == 1120
        for i in range(1, MAX_ACTIVE):
            repo.approve(**(body | {'key': 'a-' + str(i)}))
        with pytest.raises(Problem, match='memory_active_capacity'):
            repo.approve(**(body | {'key': 'too-many'}))
        assert len(repo.list(limit=MAX_ACTIVE)) == MAX_ACTIVE
        with pytest.raises(Problem, match='invalid_memory_page'):
            repo.list(limit=101)
        assert MAX_TOTAL == 200 and MAX_OPERATIONS == 512
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        room['members']['human']['epoch'] += 1
        with pytest.raises(Problem, match='idempotency_epoch_conflict'):
            repo.approve(**body)
        room['members']['human']['epoch'] -= 1
        repo._operations[('human', 'approve:v1', _approval_key_hash(body['key']))]['response_until'] = 0
        with pytest.raises(Problem, match='idempotency_retired'):
            repo.approve(**body)


def test_total_and_operation_capacity_fixture(fixture):
    store, core, rid = fixture
    source = message(core, rid, 'f')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        body = args(room, source)
        # Synthetic pre-existing tombstones exercise bounds without 200 API operations.
        for i in range(MAX_TOTAL):
            repo._records[str(i)] = {'state': 'revoked', 'text': None}
            repo._revocation_index[str(i)] = repo._index_value(str(i))
        with pytest.raises(Problem, match='memory_capacity'):
            repo.approve(**body)
        repo._records.clear()
        repo._revocation_index.clear()
        for i in range(MAX_OPERATIONS):
            repo._operations[('human', 'approve', str(i))] = {'member_epoch': 1}
        with pytest.raises(Problem, match='memory_operation_capacity'):
            repo.approve(**body)
        repo._operations.clear()


def saturate_and_revoke(store, core, rid):
    import json
    from talkabout.domain import emit
    source = message(core, rid, 'saturation-source', 'Synthetic bounded source')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        receipt = repo.approve(**args(room, source))
    # Saturate operations independently without overwriting the real approve marker.
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
        for i in range(MAX_OPERATIONS - len(repo._operations)):
            key = 'filler-' + str(i)
            repo._operations[('human', 'approve', key)] = {
                'actor_id': 'human', 'member_epoch': 1, 'operation': 'approve', 'key': key,
                'request_digest': '0' * 64, 'memory_id': receipt['id'],
                'result_revision': 1, 'response_until': time.time() + 300}
        assert len(repo._operations) == MAX_OPERATIONS
    # Use only synthetic room content; exactly fill the real serialization bound
    # AND all 2048 event slots before attempting revocation.
    with store.transaction(rid, 'fixture-human') as (_, room):
        while len(room['events']) < 2048:
            emit(room, 'fixture.padding')
        room['_synthetic_padding'] = ''
        size = len(json.dumps(room, ensure_ascii=False).encode('utf-8'))
        assert size < 2097152
        room['_synthetic_padding'] = 'x' * (2097152 - size)
        assert len(json.dumps(room, ensure_ascii=False).encode('utf-8')) == 2097152
    with store.read_transaction(rid, 'fixture-human') as (_, room):
        original = copy.deepcopy(room)
        assert len(room['events']) == 2048
        assert len(json.dumps(room, ensure_ascii=False).encode('utf-8')) == 2097152
        last_seq = room['last_seq']
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
        assert len(repo._operations) == MAX_OPERATIONS
        assert repo.revoke(key='saturated-revoke', memory_id=receipt['id'], expected_revision=1, confirm=True) == {'id': receipt['id'], 'revision': 2}
        assert repo.revoke(key='saturated-revoke', memory_id=receipt['id'], expected_revision=1, confirm=True) == {'id': receipt['id'], 'revision': 2}
        assert len(repo._operations) == MAX_OPERATIONS
        slot = repo._records[receipt['id']]['_revocation_slot']
        assert slot['state'] == 'used' and slot['audit']['action'] == 'revoked'
        assert source['text'] not in str(slot) and 'saturated-revoke' not in str(slot)
    with store.read_transaction(rid, 'fixture-human') as (_, room):
        assert room == original and room['last_seq'] == last_seq
    assert store.room_events(rid, last_seq) == []  # Room event watchers see nothing.


def test_saturation_revoke_memory_store(fixture):
    saturate_and_revoke(*fixture)


def test_saturation_revoke_postgres(memory_postgres_fixture, monkeypatch):
    store, core, rid = memory_postgres_fixture
    store.close()
    monkeypatch.setenv('TALKABOUT_DB_POOL_SIZE', '1')
    saturate_and_revoke(store, core, rid)
    assert store.pool_stats()['max_size'] == 1


def test_revocation_key_conflicts_and_epoch(fixture):
    store, core, rid = fixture
    source = message(core, rid, 'key-conflicts')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        first = repo.approve(**args(room, source))
        second = repo.approve(**args(room, source, key='second-approval'))
        body = {'key': 'single-revoke-key', 'memory_id': first['id'], 'expected_revision': 1, 'confirm': True}
        result = repo.revoke(**body)
        assert repo.revoke(**body) == result
        with pytest.raises(Problem, match='idempotency_conflict'):
            repo.revoke(**(body | {'expected_revision': 9}))
        with pytest.raises(Problem, match='idempotency_conflict'):
            repo.revoke(**(body | {'memory_id': second['id']}))
        room['members']['human']['epoch'] += 1
        with pytest.raises(Problem, match='idempotency_epoch_conflict'):
            repo.revoke(**body)
        room['members']['human']['epoch'] -= 1
        repo._records[first['id']]['_revocation_slot']['receipt']['response_until'] = 0
        repo._revocation_index[first['id']] = repo._index_value(first['id'], repo._records[first['id']]['_revocation_slot']['receipt'])
        with pytest.raises(Problem, match='idempotency_retired'):
            repo.revoke(**body)


def test_revoke_memory_store_rollback(fixture):
    store, core, rid = fixture
    source = message(core, rid, 'revoke-rollback')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        receipt = repo.approve(**args(room, source))
    old_room = copy.deepcopy(store.rooms[rid])
    old_records = copy.deepcopy(store.memory_records[rid])
    old_operations = copy.deepcopy(store.memory_operations[rid])
    with pytest.raises(RuntimeError, match='cancel'):
        with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
            repo.revoke(key='rollback-revoke', memory_id=receipt['id'], expected_revision=1, confirm=True)
            raise RuntimeError('cancel')
    assert store.rooms[rid] == old_room
    assert store.memory_records[rid] == old_records and store.memory_operations[rid] == old_operations
    assert store.memory_records[rid][receipt['id']]['_revocation_slot']['state'] == 'reserved'


def check_legacy_and_corrupt_slots(store, core, rid):
    source = message(core, rid, 'legacy-slot')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        body = args(room, source)
        receipt = repo.approve(**body)
        del repo._records[receipt['id']]['_revocation_slot']
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
        with pytest.raises(Problem, match='not_found'):
            repo.get(receipt['id'])
        assert repo.list() == []
        with pytest.raises(Problem, match='not_found'):
            repo.approve(**body)
        with pytest.raises(Problem, match='memory_revoke_slot_unavailable'):
            repo.revoke(key='legacy-revoke', memory_id=receipt['id'], expected_revision=1, confirm=True)
        assert repo._records[receipt['id']]['text'] == source['text']
    # Corrupt structures are not silently replaced with a new reservation.
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
        repo._records[receipt['id']]['_revocation_slot'] = {'schema': 999}
        with pytest.raises(Problem, match='memory_revoke_slot_unavailable'):
            repo.revoke(key='corrupt-revoke', memory_id=receipt['id'], expected_revision=1, confirm=True)
        repo._records[receipt['id']].pop('_revocation_slot')
    # A historical revoke marker is not silently upgraded into a reserved slot.
    from talkabout.domain import digest
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
        old_body = {'memory_id': receipt['id'], 'expected_revision': 1, 'confirm': True}
        repo._records[receipt['id']].update(state='revoked', text=None, revision=2)
        repo._operations[('human', 'revoke', 'old-key')] = {
            'actor_id': 'human', 'operation': 'revoke', 'key': 'old-key', 'member_epoch': 1,
            'request_digest': digest(['human', rid, 'revoke', old_body]),
            'memory_id': receipt['id'], 'result_revision': 2, 'response_until': time.time()+300}
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
        with pytest.raises(Problem, match='memory_revoke_slot_unavailable'):
            repo.revoke(key='old-key', **old_body)


def test_legacy_and_corrupt_slots_memory_store(fixture):
    check_legacy_and_corrupt_slots(*fixture)


def test_legacy_and_corrupt_slots_postgres(memory_postgres_fixture):
    check_legacy_and_corrupt_slots(*memory_postgres_fixture)


def test_postgres_atomicity_persistence_and_single_pool(memory_postgres_fixture, monkeypatch):
    store, core, rid = memory_postgres_fixture
    source = message(core, rid, 'pg-a', 'Synthetic Postgres source')
    store.close()
    monkeypatch.setenv('TALKABOUT_DB_POOL_SIZE', '1')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        body = args(room, source)
        receipt = repo.approve(**body)
    assert store.pool_stats()['max_size'] == 1
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
        assert repo.get(receipt['id'])['text'] == source['text']
    from psycopg.errors import CheckViolation
    # The real SQL write fails after the context body; transaction rollback must
    # preserve both the approved text and the reserved audit/receipt together.
    with store.connect() as conn:
        conn.execute("ALTER TABLE room_memories ADD CONSTRAINT fixture_no_revoke CHECK (payload->>'state' <> 'revoked') NOT VALID")
    try:
        with pytest.raises(CheckViolation):
            with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
                repo.revoke(key='pg-revoke', memory_id=receipt['id'], expected_revision=1, confirm=True)
    finally:
        with store.connect() as conn:
            conn.execute('ALTER TABLE room_memories DROP CONSTRAINT fixture_no_revoke')
    with store.connect() as conn:
        before_retry = conn.execute('SELECT payload FROM room_memories WHERE room_id=%s AND id=%s', (rid, receipt['id'])).fetchone()['payload']
        assert before_retry['_revocation_slot'] == {'schema': 1, 'state': 'reserved', 'audit': None, 'receipt': None}
        assert before_retry['text'] == source['text']
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
        assert repo.get(receipt['id'])['text'] == source['text']
        repo.revoke(key='pg-revoke', memory_id=receipt['id'], expected_revision=1, confirm=True)
    with store.connect() as conn:
        record = conn.execute('SELECT payload FROM room_memories WHERE room_id=%s AND id=%s', (rid, receipt['id'])).fetchone()['payload']
        assert record['text'] is None and record['state'] == 'revoked'
        operations = conn.execute('SELECT payload FROM room_memory_operations WHERE room_id=%s', (rid,)).fetchall()
        assert source['text'] not in str(operations)
        events = conn.execute('SELECT payload FROM events WHERE room_id=%s AND payload->>\'type\'=%s', (rid, 'memory.changed')).fetchall()
        assert len(events) == 1 and source['text'] not in str(events)
        assert record['_revocation_slot']['state'] == 'used'
        assert source['text'] not in str(record['_revocation_slot'])


def test_postgres_missing_optional_table_does_not_change_readiness(postgres_fixture):
    store, core, rid = postgres_fixture
    assert store.ready()
    with pytest.raises(Problem, match='memory_storage_unavailable'):
        with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True):
            pass


def test_postgres_principal_lock_and_current_grant(memory_postgres_fixture):
    store, core, rid = memory_postgres_fixture
    source = message(core, rid, 'pg-lock')
    started = threading.Event()
    done = threading.Event()
    result = []
    with store.connect() as conn:
        principal = conn.execute('SELECT payload FROM principals WHERE id=%s FOR UPDATE', ('human',)).fetchone()['payload']
        def run():
            started.set()
            try:
                with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
                    repo.approve(**args(room, source))
            except Exception as error:
                result.append(error)
            finally:
                done.set()
        thread = threading.Thread(target=run)
        thread.start()
        assert started.wait(2)
        # A locked principal prevents the authorization transaction completing.
        assert not done.wait(0.2)
        from psycopg.types.json import Jsonb
        # Existing identity mutation locks the principal before credentials. The
        # memory transaction must not already hold a credential lock here, or this
        # lock would time out / form an ABBA cycle.
        conn.execute("SET LOCAL lock_timeout = '1s'")
        conn.execute('SELECT token_hash FROM credentials WHERE principal_id=%s FOR UPDATE', ('human',))
        principal['owner'] = 'different-owner'
        conn.execute('UPDATE principals SET payload=%s WHERE id=%s', (Jsonb(principal), 'human'))
    thread.join(5)
    assert not thread.is_alive() and result and isinstance(result[0], Problem)
    assert result[0].code == 'not_found'


def test_postgres_session_read_scope_is_required(memory_postgres_fixture):
    from psycopg.types.json import Jsonb
    from talkabout.domain import token_hash
    store, _, rid = memory_postgres_fixture
    with store.connect() as conn:
        principal = conn.execute('SELECT payload FROM principals WHERE id=%s', ('human',)).fetchone()['payload']
        credential = conn.execute('SELECT scopes FROM credentials WHERE principal_id=%s', ('human',)).fetchone()['scopes']
        principal['account_scopes'] = [scope for scope in credential if scope != 'room:read']
        conn.execute('UPDATE principals SET payload=%s WHERE id=%s', (Jsonb(principal), 'human'))
    store.save_session(token_hash('synthetic-session'), {'principal_id': 'human', 'expires_at': time.time()+300, 'csrf_hash': 'fixture'})
    with pytest.raises(Problem, match='forbidden'):
        with store.memory_transaction(rid, 'session:synthetic-session', synthetic_fixture=True):
            pass


def test_postgres_concurrent_same_key_is_one_commit(memory_postgres_fixture):
    store, core, rid = memory_postgres_fixture
    source = message(core, rid, 'pg-race')
    # Inputs are taken before either worker's transaction starts.
    _, rooms, _ = store.snapshot('fixture-human')
    body = args(rooms[rid], source)
    gate = threading.Barrier(3)
    results, errors = [], []
    def run():
        gate.wait()
        try:
            with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
                results.append(repo.approve(**body))
        except Exception as error:
            errors.append(error)
    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    gate.wait()
    for thread in threads:
        thread.join(5)
    assert all(not thread.is_alive() for thread in threads) and not errors
    assert len(results) == 2 and results[0] == results[1]
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM room_memories WHERE room_id=%s', (rid,)).fetchone()['n'] == 1
        assert conn.execute('SELECT count(*) AS n FROM room_memory_operations WHERE room_id=%s', (rid,)).fetchone()['n'] == 1
        assert conn.execute("SELECT count(*) AS n FROM events WHERE room_id=%s AND payload->>'type'='memory.changed'", (rid,)).fetchone()['n'] == 1


def test_postgres_concurrent_blind_forget(memory_postgres_fixture):
    store, core, rid = memory_postgres_fixture
    source = message(core, rid, 'pg-forget-race')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        own = repo.approve(**args(room, source))
    gate = threading.Barrier(3)
    results, errors = [], []
    def run():
        gate.wait()
        try:
            with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
                results.append(repo.forget_own(key='forget-race-key', memory_id=own['id'], confirm=True))
        except Exception as error:
            errors.append(error)
    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    gate.wait()
    for thread in threads:
        thread.join(5)
    assert all(not thread.is_alive() for thread in threads) and not errors
    assert results == [{'accepted': True}, {'accepted': True}]
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM room_memory_operations WHERE room_id=%s AND operation='forget-own'", (rid,)).fetchone()['n'] == 1
        row = conn.execute('SELECT payload FROM room_memories WHERE room_id=%s AND id=%s', (rid, own['id'])).fetchone()['payload']
        assert row['text'] is None and row['_revocation_slot']['audit']['action'] == 'forgotten'
        assert conn.execute("SELECT count(*) AS n FROM events WHERE room_id=%s AND payload->>'type'='memory.changed'", (rid,)).fetchone()['n'] == 1


def test_postgres_concurrent_revoke_receipts(memory_postgres_fixture):
    store, core, rid = memory_postgres_fixture
    source = message(core, rid, 'pg-revoke-race')
    with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
        first = repo.approve(**args(room, source))
        second = repo.approve(**args(room, source, key='race-approval-two'))
    def race(memory_id, keys):
        gate = threading.Barrier(3)
        results, errors = [], []
        def run(key):
            gate.wait()
            try:
                with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
                    results.append(repo.revoke(key=key, memory_id=memory_id, expected_revision=1, confirm=True))
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=run, args=(key,)) for key in keys]
        for thread in threads:
            thread.start()
        gate.wait()
        for thread in threads:
            thread.join(5)
        assert all(not thread.is_alive() for thread in threads)
        return results, errors
    same, errors = race(first['id'], ('same-key', 'same-key'))
    assert not errors and same == [{'id': first['id'], 'revision': 2}] * 2
    different, errors = race(second['id'], ('key-one', 'key-two'))
    assert different == [{'id': second['id'], 'revision': 2}]
    assert len(errors) == 1 and isinstance(errors[0], Problem) and errors[0].code == 'not_found'
    with store.connect() as conn:
        records = conn.execute('SELECT payload FROM room_memories WHERE room_id=%s', (rid,)).fetchall()
        assert len(records) == 2 and all(r['payload']['_revocation_slot']['state'] == 'used' for r in records)
        assert conn.execute("SELECT count(*) AS n FROM events WHERE room_id=%s AND payload->>'type'='memory.changed'", (rid,)).fetchone()['n'] == 2


def test_postgres_revocation_wins_before_approval(memory_postgres_fixture):
    store, core, rid = memory_postgres_fixture
    source = message(core, rid, 'pg-membership-race')
    entered, done, results = threading.Event(), threading.Event(), []
    with store.transaction(rid, 'fixture-human') as (_, room):
        body = args(room, source)
        room['members']['human']['status'] = 'removed'
        def approve():
            entered.set()
            try:
                with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
                    repo.approve(**body)
            except Exception as error:
                results.append(error)
            finally:
                done.set()
        thread = threading.Thread(target=approve)
        thread.start()
        assert entered.wait(2) and not done.wait(0.2)
    thread.join(5)
    assert not thread.is_alive() and len(results) == 1 and isinstance(results[0], Problem)
    assert results[0].code == 'not_found'
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM room_memories WHERE room_id=%s', (rid,)).fetchone()['n'] == 0


def test_postgres_minimum_role_can_approve_but_cannot_delete(memory_postgres_fixture, monkeypatch):
    from contextlib import contextmanager
    import uuid
    from psycopg import sql
    from psycopg.errors import InsufficientPrivilege
    store, core, rid = memory_postgres_fixture
    source = message(core, rid, 'pg-role', 'Synthetic role source')
    role = 'talkabout_test_memory_' + uuid.uuid4().hex
    # memory_postgres_fixture verifies loopback, ephemeral fixture marker and actual data
    # directory before creating its unique schema; this role exists only there.
    with store.connect() as conn:
        conn.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
        conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(store.schema), sql.Identifier(role)))
        for table in ('rooms', 'principals', 'credentials', 'human_sessions'):
            conn.execute(sql.SQL('GRANT SELECT,UPDATE ON {} TO {}').format(sql.Identifier(table), sql.Identifier(role)))
        conn.execute(sql.SQL('GRANT SELECT,INSERT ON events TO {}').format(sql.Identifier(role)))
        for table in ('room_memories', 'room_memory_operations', 'room_memory_revoke_index'):
            conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE ON {} TO {}').format(sql.Identifier(table), sql.Identifier(role)))
    normal_connect = store.connect
    @contextmanager
    def limited_connect(*a, **kw):
        with normal_connect(*a, **kw) as conn:
            conn.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(role)))
            yield conn
    try:
        monkeypatch.setattr(store, 'connect', limited_connect)
        with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, room, repo):
            receipt = repo.approve(**args(room, source))
        with store.memory_transaction(rid, 'fixture-human', synthetic_fixture=True) as (_, _, repo):
            assert repo.get(receipt['id'])['text'] == source['text']
            repo.revoke(key='limited-revoke', memory_id=receipt['id'], expected_revision=1, confirm=True)
        with pytest.raises(InsufficientPrivilege):
            with limited_connect() as conn:
                conn.execute('DELETE FROM room_memories WHERE room_id=%s', (rid,))
    finally:
        monkeypatch.setattr(store, 'connect', normal_connect)
        with normal_connect() as conn:
            conn.execute(sql.SQL('REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {} FROM {}').format(sql.Identifier(store.schema), sql.Identifier(role)))
            conn.execute(sql.SQL('REVOKE ALL PRIVILEGES ON SCHEMA {} FROM {}').format(sql.Identifier(store.schema), sql.Identifier(role)))
            conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_default_install_has_no_memory_tables_or_grants():
    import inspect
    from pathlib import Path
    from talkabout import provision
    schema = Path(provision.__file__).with_name('schema.sql').read_text(encoding='utf-8')
    text = inspect.getsource(provision.provision_database)
    for table in ('room_memories', 'room_memory_operations', 'room_memory_revoke_index'):
        assert table not in schema
        assert table not in provision.TABLES
        assert table not in text
