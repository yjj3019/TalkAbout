import concurrent.futures
import json
import time

import pytest
from fastapi.testclient import TestClient
from talkabout.api import create_app
from talkabout.auth import bootstrap_human
from talkabout.domain import Problem
from talkabout.setup import complete_setup, issue_code, terminal_issue
from talkabout.store import MemoryStore, PostgresStore


def body(code):
    return {'code': code, 'workspace_id': 'synthetic-install', 'display_name': 'Fixture Owner',
            'username': 'owner', 'password': 'synthetic-password-only', 'confirmation': 'synthetic-password-only'}


@pytest.mark.parametrize('name', [None, 'fixture-app', 'bad; command', '../private', 'x'*129])
def test_pending_public_container_hint_is_validated_and_disappears_after_owner(name):
    store = MemoryStore()
    client = TestClient(create_app(store, start_scheduler=False, allowed_hosts=['testserver'], setup_container_name=name))
    expected = {'setup_required': True}
    if name == 'fixture-app': expected['setup_container'] = name
    assert client.get('/api/v1/setup/status').json() == expected
    complete_setup(store, body(issue_code(store)))
    assert client.get('/api/v1/setup/status').json() == {'setup_required': False}


def test_locked_api_origin_csrf_gui_and_normal_login():
    store = MemoryStore()
    client = TestClient(create_app(store, start_scheduler=False, allowed_hosts=['testserver']))
    assert client.get('/').status_code == 200
    assert 'id="setup"' in client.get('/').text
    helper = client.get('/web/connect-agent.ps1')
    assert helper.status_code == 200
    assert 'TalkAbout connection helper' in helper.text
    assert helper.content.isascii() and not helper.content.startswith(b'\xef\xbb\xbf')
    assert client.get('/health').json() == {'status': 'alive'}
    for path in ['session', 'rooms', 'agents/me', 'events', 'rooms/fixture/stream',
                 'rooms/fixture/artifacts/file/versions/v/download']:
        assert client.get('/api/v1/'+path).status_code == 503
    assert client.get('/ready').status_code == 503
    assert client.post('/api/v1/setup/complete', json=body('wrong')).status_code == 403
    code = issue_code(store)
    assert code not in json.dumps(store.setup_state)
    csrf = client.get('/api/v1/setup/csrf').json()['csrf_token']
    assert 'httponly' in client.get('/api/v1/setup/csrf').headers['set-cookie'].lower()
    csrf = client.get('/api/v1/setup/csrf').json()['csrf_token']
    headers = {'Origin': 'http://testserver', 'X-CSRF-Token': csrf}
    assert client.post('/api/v1/setup/complete', json=body(code), headers={**headers, 'Origin': 'https://evil.invalid'}).status_code == 403
    assert client.post('/api/v1/setup/complete', json=body(code), headers={'Origin': 'http://testserver'}).status_code == 403
    assert client.post('/api/v1/setup/complete', json=body(code), headers=[(b'origin',b'http://testserver'),(b'x-csrf-token',b'\xff')]).status_code == 403
    assert client.post('/api/v1/setup/complete', json=body(code), headers=headers).json()['login_required']
    assert not store.setup_required()
    assert client.get('/web/connect-agent.ps1').status_code == 200
    assert client.get('/web/missing-asset.ps1').status_code == 404
    assert 'code_hash' not in store.setup_state
    assert client.post('/api/v1/setup/complete', json=body(code), headers=headers).status_code == 409
    response = client.post('/api/v1/session', json={'workspace_id':'synthetic-install', 'username':'owner', 'password':'synthetic-password-only'}, headers={'Origin':'http://testserver'})
    assert response.status_code == 200
    assert client.get('/api/v1/session').json()['user']['kind'] == 'human'


def test_expiry_limit_reissue_and_existing_owner():
    store = MemoryStore(); code = issue_code(store)
    with pytest.raises(Problem): issue_code(store)
    for _ in range(5):
        with pytest.raises(Problem) as error: complete_setup(store, body('synthetic-wrong'))
        assert error.value.code == 'setup_invalid'
    with pytest.raises(Problem) as error: complete_setup(store, body(code))
    assert error.value.code == 'setup_attempt_limit'
    replacement = issue_code(store)
    assert replacement != code and store.setup_state['failures'] == 0
    store.setup_state['expires_at'] = time.time()-1
    with pytest.raises(Problem): complete_setup(store, body(replacement))
    latest = issue_code(store)
    complete_setup(store, body(latest))
    with pytest.raises(Problem): issue_code(store)
    with pytest.raises(Problem): bootstrap_human(store, 'other', 'Other', 'other', 'synthetic-password-only')
    old = MemoryStore(); bootstrap_human(old, 'legacy', 'Legacy', 'legacy', 'synthetic-password-only')
    assert not old.setup_required()
    with pytest.raises(Problem): issue_code(old)
    with pytest.raises(Problem): complete_setup(old, body('any'))


@pytest.mark.parametrize('stream', ['stdin','stdout','stderr'])
def test_code_terminal_only(monkeypatch, capsys, stream):
    store = MemoryStore()
    for name in ('stdin','stdout','stderr'): monkeypatch.setattr('sys.'+name+'.isatty', lambda: True)
    monkeypatch.setattr('sys.'+stream+'.isatty', lambda: False)
    with pytest.raises(Problem): terminal_issue(store)
    assert not getattr(store, 'setup_state', {})
    assert capsys.readouterr().out == ''


def test_scheduler_waits_for_owner():
    store = MemoryStore(); ticks=[]
    store.tick_all = lambda: ticks.append(True)
    with TestClient(create_app(store, allowed_hosts=['testserver'])):
        time.sleep(.65)
        assert not ticks
        complete_setup(store, body(issue_code(store)))
        time.sleep(.65)
        assert ticks


def test_postgres_restart_failed_attempts_concurrency_and_transaction(postgres_fixture):
    store, _, _ = postgres_fixture
    # This fixture owns a UUID schema in the explicitly marked ephemeral database.
    assert store.schema.startswith('talkabout_test_')
    with store.connect() as conn:
        for table in ('events','rooms','credentials','human_sessions','human_passwords','principals'):
            conn.execute('DELETE FROM '+table)
    code = issue_code(store)
    for _ in range(2):
        with pytest.raises(Problem): complete_setup(store, body('synthetic-wrong'))
    restarted = PostgresStore(store.dsn, store.schema)
    assert restarted.setup_required()
    with restarted.installation() as (state, _, _): assert state['failures'] == 2
    import psycopg
    with store.connect() as conn:
        conn.execute('ALTER TABLE human_passwords ADD CONSTRAINT fixture_rollback CHECK(false)')
    with pytest.raises(psycopg.errors.CheckViolation): complete_setup(restarted, body(code))
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM principals').fetchone()['n'] == 0
        conn.execute('ALTER TABLE human_passwords DROP CONSTRAINT fixture_rollback')
    with restarted.installation() as (state, _, _): assert state['state'] == 'pending' and state['failures'] == 2
    def attempt(_):
        try: return complete_setup(PostgresStore(store.dsn,store.schema),body(code))['status']
        except Problem as error: return error.code
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(attempt,range(4)))
    assert results.count('complete') == 1 and results.count('setup_complete') == 3
    assert not PostgresStore(store.dsn,store.schema).setup_required()
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM principals WHERE payload->>'kind'='human'").fetchone()['n'] == 1
        records=conn.execute('SELECT payload FROM installation_setup').fetchall()
        assert code not in json.dumps(records) and 'code_hash' not in records[0]['payload']
    with pytest.raises(Problem): issue_code(restarted)


def test_postgres_persistent_expiry_limit_and_legacy_schema(postgres_fixture):
    store, _, _ = postgres_fixture
    # Old owned installation, without new table: no lock or owner reset.
    with store.connect() as conn: conn.execute('DROP TABLE installation_setup')
    assert not store.setup_required() and store.ready()
    store.initialize()
    with store.connect() as conn:
        for table in ('events','rooms','credentials','human_sessions','human_passwords','principals'):
            conn.execute('DELETE FROM '+table)
    code=issue_code(store)
    for _ in range(5):
        with pytest.raises(Problem): complete_setup(PostgresStore(store.dsn,store.schema),body('synthetic-wrong'))
    with pytest.raises(Problem) as error: complete_setup(PostgresStore(store.dsn,store.schema),body(code))
    assert error.value.code == 'setup_attempt_limit'
    code=issue_code(store)
    with store.installation() as (state,_,_): state['expires_at']=time.time()-1
    with pytest.raises(Problem) as error: complete_setup(PostgresStore(store.dsn,store.schema),body(code))
    assert error.value.code == 'setup_unavailable'
    assert store.setup_required()


def test_postgres_runtime_role_can_setup_without_ddl(postgres_fixture):
    import uuid
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    store,_,_=postgres_fixture
    with store.connect() as conn:
        for table in ('events','rooms','credentials','human_sessions','human_passwords','principals'):
            conn.execute('DELETE FROM '+table)
    role='fixture_runtime_'+uuid.uuid4().hex
    password='synthetic-runtime-password-only'
    try:
        with store.connect() as conn:
            conn.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}').format(sql.Identifier(role),sql.Literal(password)))
            conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(store.schema),sql.Identifier(role)))
            conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {} TO {}').format(sql.Identifier(store.schema),sql.Identifier(role)))
        runtime=PostgresStore(make_conninfo(store.dsn,user=role,password=password),store.schema)
        assert runtime.setup_required()
        complete_setup(runtime,body(issue_code(runtime)))
        assert not runtime.setup_required()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with runtime.connect() as conn: conn.execute('CREATE TABLE forbidden_ddl(id int)')
    finally:
        with store.connect() as conn:
            conn.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
