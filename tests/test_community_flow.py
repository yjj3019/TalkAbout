import time
import pytest
from talkabout.core import Core
from talkabout.domain import AGENT_PERMISSIONS, Problem, schedule
from talkabout.identity import IdentityService


def control(core, room, action, key):
    pending=core.mutation('fixture-human',room['id'],'controls',key,{'action':action,'expected_revision':room['room_revision']})
    return core.mutation('fixture-human',room['id'],'controls/'+pending['id']+'/approve',key+'-approve',{})


def test_manual_room_prepares_without_deadline_or_tasks_and_starts_explicitly(fixture):
    store,core,client=fixture
    room=core.create('fixture-human',{'topic':'Synthetic topic','start_policy':'manual'},'prepare-room')
    assert room['state']=='preparing' and room['deadline'] is None and not room['tasks']
    schedule(store.rooms[room['id']],store.principals,time.time()+100000)
    assert store.rooms[room['id']]['state']=='preparing'
    with pytest.raises(Problem):control(core,room,'start','empty-start')
    invited=core.mutation('fixture-human',room['id'],'memberships','invite-a',{'principal_id':'a','permissions':sorted(AGENT_PERMISSIONS)})
    core.accept('fixture-a',room['id'],'accept-a',{'expected_revision':invited['revision'],'acknowledge_private':True})
    assert not core.read('fixture-human',room['id'])['tasks']
    with pytest.raises(Problem,match='worker_connection_required'):control(core,core.read('fixture-human',room['id']),'start','no-worker')
    IdentityService(store).mutate('fixture-a','connection','worker-ready',{'runtime':'synthetic','state':'idle'})
    control(core,core.read('fixture-human',room['id']),'start','start-now')
    started=core.read('fixture-human',room['id'])
    assert started['state']=='active' and started['deadline']>time.time() and started['tasks']
    assert started['generation']==room['generation']+1


def test_legacy_creation_remains_automatic_and_manual_is_human_only(fixture):
    store,core,client=fixture
    assert core.create('fixture-human',{'topic':'Legacy'},'legacy-room')['state']=='active'
    with pytest.raises(Problem):core.create('fixture-a',{'topic':'Agent','start_policy':'manual'},'forbidden-manual')


@pytest.mark.parametrize('mode',['free_discussion','bounded_rounds','co_creation'])
@pytest.mark.parametrize('contribute',[False,True])
def test_mode_permissions_match_start_readiness_and_scheduler(fixture,mode,contribute):
    store,core,_=fixture
    room=core.create('fixture-human',{'topic':'Mode permission fixture','mode':mode,'start_policy':'manual'},'mode-prepare')
    permissions=AGENT_PERMISSIONS if contribute else AGENT_PERMISSIONS-{'room:contribute'}
    invitation=core.mutation('fixture-human',room['id'],'memberships','invite-mode',{'principal_id':'a','permissions':sorted(permissions)})
    core.accept('fixture-a',room['id'],'accept-mode',{'expected_revision':invitation['revision'],'acknowledge_private':True})
    IdentityService(store).mutate('fixture-a','connection','mode-contact',{'runtime':'synthetic','state':'idle'})
    if mode=='co_creation' and not contribute:
        with pytest.raises(Problem,match='participant_permissions_required'):control(core,core.read('fixture-human',room['id']),'start','missing-contribute')
        current=core.read('fixture-human',room['id'])
        assert current['state']=='preparing' and not current['tasks']
        store.rooms[room['id']]['members']['a']['permissions'].append('room:contribute')
    control(core,core.read('fixture-human',room['id']),'start','mode-start')
    started=core.read('fixture-human',room['id'])
    assert started['state']=='active'
    if mode=='free_discussion':
        member=started['members']['a']
        core.mutation('fixture-a',room['id'],'turn-requests','explicit-turn',{'generation':started['generation'],'membership_revision':member['revision']})
        started=core.read('fixture-human',room['id'])
    assert len(started['tasks'])==1 and started['tasks'][0]['assignee']=='a'


def test_postgres_preparation_connection_and_start_persist_without_new_ddl(postgres_fixture):
    from talkabout.store import PostgresStore
    store,core,_=postgres_fixture
    room=core.create('fixture-human',{'topic':'Synthetic persisted preparation','start_policy':'manual'},'persistent-preparation')
    invitation=core.mutation('fixture-human',room['id'],'memberships','persistent-invite',{'principal_id':'a','permissions':sorted(AGENT_PERMISSIONS)})
    core.accept('fixture-a',room['id'],'persistent-accept',{'expected_revision':invitation['revision'],'acknowledge_private':True})
    IdentityService(store).mutate('fixture-a','connection','persistent-contact',{'runtime':'synthetic','state':'idle'})
    restarted=Core(PostgresStore(store.dsn,store.schema))
    restored=restarted.read('fixture-human',room['id'])
    assert restored['state']=='preparing' and restored['deadline'] is None and not restored['tasks']
    assert restored['members']['a']['connection']['runtime']=='synthetic'
    control(restarted,restored,'start','persistent-start')
    again=Core(PostgresStore(store.dsn,store.schema)).read('fixture-human',room['id'])
    assert again['state']=='active' and again['started_at']<again['deadline'] and again['tasks']
    identity=IdentityService(store)
    issued=identity.mutate('fixture-human','credentials/a','metadata-issue',{'room_id':room['id'],'scopes':sorted(AGENT_PERMISSIONS),'expires_in':30})
    with store.connect() as conn:
        before=conn.execute('SELECT xmin::text AS version FROM credentials WHERE credential_id=%s',(issued['credential_id'],)).fetchone()['version']
    metadata=identity.credential_metadata('fixture-human','a')
    listed=next(item for item in metadata if item['credential_id']==issued['credential_id'])
    assert 'token_hash' not in listed and 'token' not in listed
    with store.connect() as conn:
        after=conn.execute('SELECT xmin::text AS version FROM credentials WHERE credential_id=%s',(issued['credential_id'],)).fetchone()['version']
    assert after==before
