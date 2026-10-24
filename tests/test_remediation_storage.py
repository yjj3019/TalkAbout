"""Synthetic storage regressions; no operating DB, credentials or provider."""
import copy
import time

import pytest
from fastapi.testclient import TestClient

from talkabout.domain import Problem, ident
from talkabout.api import create_app
from talkabout.identity import IdentityService
from talkabout.domain import token_hash
from conftest import result


def test_expired_cursor_capacity_recovers_without_removing_live_or_other_actor(fixture):
    store, core, _ = fixture
    cursor = core.events('fixture-a')['next_cursor']
    for index in range(4095):
        store.cursors[f'expired-{index}'] = {'principal': 'a', 'expires_at': 0}
    store.cursors['other-actor-expired'] = {'principal': 'b', 'expires_at': 0}
    batch = core.events('fixture-a', cursor)
    assert batch['events'] == []
    assert store.cursor(cursor) is not None
    assert 'other-actor-expired' in store.cursors
    assert not any(key.startswith('expired-') for key in store.cursors)


def test_live_cursor_quota_stays_bounded(fixture):
    store, core, _ = fixture
    for index in range(4096):
        store.cursors[f'live-{index}'] = {'principal': 'a', 'expires_at': time.time()+600}
    with pytest.raises(Problem, match='cursor_capacity_archival_unsupported'):
        core.events('fixture-a')
    assert len(store.cursors) == 4096


def test_empty_poll_renews_the_same_immutable_cursor(fixture):
    store, core, _ = fixture
    first = core.events('fixture-a')['next_cursor']
    original = copy.deepcopy(store.cursor(first))
    for _ in range(8):
        batch = core.events('fixture-a', first)
        assert batch['events'] == [] and batch['next_cursor'] == first
    assert len(store.cursors) == 1
    current = store.cursor(first)
    assert current['offsets'] == original['offsets']
    assert current['expires_at'] >= original['expires_at']
    with pytest.raises(Problem, match='cursor_conflict'):
        store.save_cursor(first, {k:v for k,v in original.items() if k!='expires_at'} | {'offsets':{}})


def test_scheduler_capacity_error_is_room_local_and_rolls_back(fixture):
    store, core, rid = fixture
    bad = store.rooms[rid]
    healthy_id = ident()
    healthy = copy.deepcopy(bad)
    healthy.update(id=healthy_id, tasks={}, deadline=time.time()+600)
    store.rooms[healthy_id] = healthy
    bad['deadline'] = 0
    bad['events'] = [{'event_id':f'e{i}','room_id':rid,'seq':i+1,'type':'fixture','created_at':0} for i in range(2048)]
    bad['last_seq'] = 2048
    before = copy.deepcopy(bad)
    store.tick_all()
    assert store.rooms[rid] == before
    assert store.scheduler_errors == {rid:'event_capacity'}
    assert core.read('fixture-human',rid)['scheduler_error'] == 'event_capacity'
    assert any(t['state']=='queued' for t in store.rooms[healthy_id]['tasks'].values())


def test_scheduler_reports_blocked_rooms_and_recovers_without_restarting(fixture):
    store, _, rid = fixture
    bad=store.rooms[rid]
    bad['deadline']=0
    bad['events']=[{'event_id':f'e{i}','room_id':rid,'seq':i+1,'type':'fixture','created_at':0} for i in range(2048)]
    bad['last_seq']=2048
    with TestClient(create_app(store),base_url='http://127.0.0.1') as http:
        until=time.monotonic()+3
        while time.monotonic()<until:
            health=http.get('/health').json()
            if health['scheduler']=='degraded':break
            time.sleep(.01)
        assert health['scheduler']=='degraded' and health['blocked_rooms']==1
        assert rid not in str(health)
        with store.lock:
            # Repair only this synthetic fixture; production history is never deleted.
            bad=store.rooms[rid]
            bad['events']=bad['events'][:16]
            bad['last_seq']=16
        until=time.monotonic()+3
        while time.monotonic()<until:
            health=http.get('/health').json()
            if health['scheduler']=='running':break
            time.sleep(.01)
        assert health['scheduler']=='running'
        assert store.rooms[rid]['state']=='closed'


def test_artifact_caption_is_preserved_in_storage_and_replay(fixture):
    store, core, rid = fixture
    body = {'generation':1,'membership_revision':2,'text':'Synthetic artifact','caption':'Source caption','sources':[]}
    created = core.mutation('fixture-a', rid, 'artifacts', 'caption-once', body)
    assert created['versions'][0]['caption'] == 'Source caption'
    assert core.mutation('fixture-a', rid, 'artifacts', 'caption-once', body) == created
    saved = core.read('fixture-a', rid)['artifacts'][0]
    assert saved['versions'][0]['caption'] == 'Source caption'
    with store.read_transaction(rid,'fixture-a') as (_,room):
        assert room['artifacts'][created['id']]['versions'][0]['caption'] == 'Source caption'


@pytest.mark.parametrize('outcome',['skip','expire'])
def test_co_creation_without_a_creator_result_is_failed_not_awaiting_adoption(fixture, outcome):
    store, core, _ = fixture
    created = core.create('fixture-a',{'topic':'Synthetic empty creation','mode':'co_creation'},'empty-creation')
    rid = created['id']
    for purpose in ['researcher','creator','reviewer']:
        task = next(t for t in core.read('fixture-a',rid)['tasks'] if t['state']=='queued')
        assert task['purpose'] == purpose
        if purpose=='creator' and outcome=='expire':
            with store.transaction(rid,'fixture-a') as (_,room):
                room['tasks'][task['id']]['deadline']=0
            core.tick('fixture-a',rid)
            continue
        claimed=core.mutation('fixture-a',rid,f"tasks/{task['id']}/claim",'claim-'+task['id'],
                              {'generation':task['generation'],'membership_revision':task['membership_revision']})
        body={key:claimed[key] for key in ('attempt','generation','membership_revision')}
        if purpose=='creator':
            core.mutation('fixture-a',rid,f"tasks/{task['id']}/skip",'skip-'+task['id'],body)
        else:
            core.mutation('fixture-a',rid,f"tasks/{task['id']}/complete",'complete-'+task['id'],result(claimed,purpose+' result'))
    current=core.read('fixture-a',rid)
    assert current['artifacts']==[]
    assert current['mode_status']=='no_artifact' and current['state']=='closed'
    with store.read_transaction(rid,'fixture-a') as (_,room):
        assert room['events'][-1]['type']=='room.closed'
        assert room['events'][-1]['reason']=='no_artifact'


def test_postgres_caption_and_replay_survive_store_recreation(postgres_fixture):
    from talkabout.core import Core
    from talkabout.store import PostgresStore
    test_artifact_caption_is_preserved_in_storage_and_replay(postgres_fixture)
    store,_,rid=postgres_fixture
    restored=Core(PostgresStore(store.dsn,store.schema))
    artifact=restored.read('fixture-a',rid)['artifacts'][0]
    assert artifact['versions'][0]['caption']=='Source caption'


@pytest.mark.parametrize('outcome',['skip','expire'])
def test_postgres_empty_creation_never_waits_for_adoption(postgres_fixture,outcome):
    test_co_creation_without_a_creator_result_is_failed_not_awaiting_adoption(postgres_fixture,outcome)


def test_postgres_cursor_prunes_only_own_expired_metadata_and_renews_immutable_id(postgres_fixture):
    from psycopg.types.json import Jsonb
    from talkabout.store import PostgresStore
    store,core,_=postgres_fixture
    first=core.events('fixture-a')['next_cursor']
    original=copy.deepcopy(store.cursor(first))
    with store.connect() as conn:
        conn.execute("INSERT INTO cursors(id,principal_id,payload,expires_at) SELECT 'expired-'||n,'a',%s,0 FROM generate_series(1,4095) n",(Jsonb({'principal':'a'}),))
        conn.execute("INSERT INTO cursors VALUES('other-expired','b',%s,0)",(Jsonb({'principal':'b'}),))
    for _ in range(3):
        response=core.events('fixture-a',first)
        assert response['events']==[] and response['next_cursor']==first
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM cursors WHERE principal_id='a'").fetchone()['n']==1
        assert conn.execute("SELECT count(*) AS n FROM cursors WHERE id='other-expired'").fetchone()['n']==1
    assert PostgresStore(store.dsn,store.schema).cursor(first)==original
    with pytest.raises(Problem,match='cursor_conflict'):
        store.save_cursor(first,{**original,'offsets':{}})
    with store.connect() as conn:
        conn.execute("INSERT INTO cursors(id,principal_id,payload,expires_at) SELECT 'live-'||n,'a',%s,%s FROM generate_series(1,4095) n",(Jsonb(original),time.time()+600))
    with pytest.raises(Problem,match='cursor_capacity_archival_unsupported'):
        core.events('fixture-a')


def test_postgres_scheduler_rolls_back_one_room_and_continues_others(postgres_fixture):
    store,core,rid=postgres_fixture
    healthy=core.create('fixture-a',{'topic':'Synthetic healthy room'},'healthy-room')['id']
    with store.transaction(rid,'fixture-human') as (_,room):
        room['deadline']=0
        while len(room['events'])<2048:
            from talkabout.domain import emit
            emit(room,'synthetic.capacity')
    with store.transaction(healthy,'fixture-a') as (_,room):room['deadline']=0
    with store.read_transaction(rid,'fixture-human') as (_,room):before=copy.deepcopy(room)
    store.tick_all()
    assert store.scheduler_errors=={rid:'event_capacity'}
    with store.read_transaction(rid,'fixture-human') as (_,room):assert room==before
    assert core.read('fixture-a',healthy)['state']=='closed'


def test_retained_inactive_credentials_do_not_consume_active_quota(fixture):
    store, _, _=fixture
    for index in range(512):
        store.credentials[token_hash(f'retained-expired-{index}')]={'id':'a','credential_id':f'retained-{index}','scopes':['room:read'],'expires_at':0,'revoked':True}
    before=len(store.credentials)
    identity=IdentityService(store)
    issued=identity.mutate('fixture-human','credentials/a','after-inactive-history',{'scopes':['room:read'],'expires_in':30})
    assert store.auth(issued['token'])['id']=='a'
    assert len(store.credentials)==before+1
    identity.mutate('fixture-human','credentials/a/retained-0/revoke','revoke-old-history',{})
    assert store.credentials[token_hash('retained-expired-0')]['revoked'] is True


def test_credential_active_quota_is_workspace_local(fixture):
    store, _, _=fixture
    store.provision({'id':'outside','kind':'agent','owner':'other-workspace','display_name':'Outside','handle':'outside'},'fixture-outside',{'room:read'})
    for index in range(511):
        store.credentials[token_hash(f'outside-active-{index}')]={'id':'outside','credential_id':f'outside-{index}','scopes':['room:read'],'expires_at':time.time()+600,'revoked':False}
    issued=IdentityService(store).mutate('fixture-human','credentials/a','own-workspace-only',{'scopes':['room:read'],'expires_in':30})
    assert store.auth(issued['token'])['owner']=='fixture-owner'
    assert len(store.credentials)==516


def test_active_credential_quota_is_still_enforced(fixture):
    store, _, _=fixture
    for index in range(509):
        store.credentials[token_hash(f'own-active-{index}')]={'id':'a','credential_id':f'own-{index}','scopes':['room:read'],'expires_at':time.time()+600,'revoked':False}
    before=copy.deepcopy(store.credentials)
    with pytest.raises(Problem,match='identity_capacity|credential_capacity'):
        IdentityService(store).mutate('fixture-human','credentials/a','at-active-quota',{'scopes':['room:read'],'expires_in':30})
    assert store.credentials==before


def test_postgres_transaction_expiry_between_auth_and_active_query_is_401(monkeypatch):
    from contextlib import contextmanager
    from talkabout.store import PostgresStore
    class Rows:
        def __init__(self,rows):self.rows=rows
        def fetchone(self):return self.rows[0] if self.rows else None
        def fetchall(self):return self.rows
    class Connection:
        def execute(self,query,args=()):
            if query.startswith('SELECT p.owner'):return Rows([{'owner':'synthetic-owner'}])
            if query.startswith('SELECT id,payload'):return Rows([{'id':'agent','payload':{'id':'agent','owner':'synthetic-owner'}}])
            if query.startswith('SELECT c.*'):
                # Authentication succeeded just before expiry; active-only query now excludes it.
                assert 'p.owner=%s AND (' in query
                return Rows([])
            assert query.startswith('SELECT pg_advisory_xact_lock')
            return Rows([])
    @contextmanager
    def connect():yield Connection()
    store=PostgresStore.__new__(PostgresStore)
    monkeypatch.setattr(store,'connect',connect)
    monkeypatch.setattr(store,'_auth',lambda conn,token:{'id':'agent','owner':'synthetic-owner'})
    with pytest.raises(Problem,match='unauthorized') as failure:
        with store.identity_transaction('synthetic-expiring-bearer'):
            pytest.fail('expired bearer must not reach credential-indexing caller')
    assert failure.value.status==401
