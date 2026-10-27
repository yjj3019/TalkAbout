import copy
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from talkabout.domain import Problem
from conftest import LocalClient, leased, result


def approve(core,rid,action,parameters,key):
    room=core.read('fixture-human',rid)
    pending=core.mutation('fixture-human',rid,'controls',key,{'action':action,'expected_revision':room['room_revision'],'parameters':parameters,'reason':'fixture confirmation'})
    return core.mutation('fixture-human',rid,'controls/'+pending['id']+'/approve',key+':approve',{})


def test_room_cursor_isolated_from_inbox_and_other_room(fixture):
    store,core,rid=fixture
    other=core.create('fixture-a',{'topic':'other fixture'},'other')
    inbox=core.events('fixture-a')['next_cursor']
    with pytest.raises(Problem,match='invalid_cursor_view'):
        core.events('fixture-a',inbox,target_room=rid)
    data=core.events('fixture-a',target_room=rid)
    assert {e['room_id'] for e in data['events']}=={rid}
    with pytest.raises(Problem,match='invalid_cursor_view'):
        core.events('fixture-a',data['next_cursor'],target_room=other['id'])
    with pytest.raises(Problem,match='invalid_cursor_view'):
        core.events('fixture-a',data['next_cursor'])


def test_snapshot_cursor_matches_snapshot_boundary(fixture):
    _,core,rid=fixture
    snapshot=core.room_snapshot('fixture-a',rid)
    assert snapshot['source_event_range']['to_seq']==snapshot['room']['last_seq']
    assert core.events('fixture-a',snapshot['snapshot_cursor'],target_room=rid)['events']==[]
    task=leased(core,rid)
    core.mutation('fixture-a',rid,'tasks/'+task['id']+'/complete','complete',result(task))
    events=core.events('fixture-a',snapshot['snapshot_cursor'],target_room=rid)['events']
    assert events and all(e['seq']>snapshot['room']['last_seq'] for e in events)


def test_room_poll_reauthorizes_revoke(fixture):
    store,core,rid=fixture
    cursor=core.room_snapshot('fixture-a',rid)['snapshot_cursor']
    with ThreadPoolExecutor(1) as pool:
        waiting=pool.submit(core.events,'fixture-a',cursor,2,False,rid)
        time.sleep(.03); store.revoke('fixture-a')
        with pytest.raises(Problem,match='unauthorized'): waiting.result(timeout=3)


def test_sse_batches_cursor_and_human_authorization(fixture):
    store,core,rid=fixture
    human=LocalClient(store,'fixture-human')
    response=human.http.get(f'/api/v1/rooms/{rid}/stream?duration_seconds=0',headers={'Authorization':'Bearer fixture-human'})
    assert response.status_code==200 and response.headers['content-type'].startswith('text/event-stream')
    blocks=response.text.strip().split('\n\n')
    update=next(b for b in blocks if 'event: room_update' in b)
    data=json.loads(next(line[6:] for line in update.splitlines() if line.startswith('data: ')))
    cursor=next(line[4:] for line in update.splitlines() if line.startswith('id: '))
    assert data['next_cursor']==cursor and data['events']
    assert core.events('fixture-human',cursor,target_room=rid)['events']==[]
    assert any('event: stream_end' in b for b in blocks)
    agent=LocalClient(store)
    assert agent.http.get(f'/api/v1/rooms/{rid}/stream?duration_seconds=0',headers={'Authorization':'Bearer fixture-a'}).status_code==403


def test_observation_surfaces_leave_room_and_task_state_unchanged(fixture):
    store,core,rid=fixture
    task=leased(core,rid)
    room=store.rooms[rid]
    tasks,seq,count=copy.deepcopy(room['tasks']),room['last_seq'],len(room['events'])
    events=copy.deepcopy(room['events'])
    cursor=core.room_snapshot('fixture-human',rid)['snapshot_cursor']
    core.events('fixture-human',cursor,target_room=rid)
    core.room_snapshot('fixture-human',rid)
    human=LocalClient(store,'fixture-human')
    human.http.get(f'/api/v1/rooms/{rid}/stream?duration_seconds=0',headers={'Authorization':'Bearer fixture-human'})
    assert store.rooms[rid]['tasks']==tasks
    assert store.rooms[rid]['last_seq']==seq and len(store.rooms[rid]['events'])==count
    assert store.rooms[rid]['events']==events
    assert store.rooms[rid]['tasks'][task['id']]['state']=='leased'
    assert not store.rooms[rid]['tasks'][task['id']].get('_reservations')
    # Detaching the viewer neither cancels the running work nor blocks its normal life.
    completed=core.mutation('fixture-a',rid,'tasks/'+task['id']+'/complete','complete',result(task))
    assert completed['task']['state']=='completed'


def test_task_identity_is_stable_across_attempts_and_views(fixture):
    store,core,rid=fixture
    first=leased(core,rid)
    store.rooms[rid]['tasks'][first['id']]['lease_expires_at']=1
    store.tick_all()
    room=core.read('fixture-human',rid)
    requeued=next(t for t in room['tasks'] if t['id']==first['id'])
    assert requeued['state']=='queued' and requeued['attempt']==1
    second=core.mutation('fixture-a',rid,'tasks/'+first['id']+'/claim','claim-2',
                         {'generation':requeued['generation'],'membership_revision':requeued['membership_revision']})
    assert second['id']==first['id'] and second['attempt']==2 and second['state']=='leased'
    snapshot=core.room_snapshot('fixture-human',rid)['room']
    shown=next(t for t in snapshot['tasks'] if t['id']==first['id'])
    assert shown['attempt']==2 and shown['state']=='leased' and shown['id']==first['id']
    claimed=[e for e in core.events('fixture-human',None,target_room=rid)['events']
             if e['type']=='task.claimed' and e.get('task_id')==first['id']]
    assert [e['attempt'] for e in claimed]==[1,2]


def test_sse_reconnect_from_last_event_id_repeats_no_event(fixture):
    store,core,rid=fixture
    human=LocalClient(store,'fixture-human')
    headers={'Authorization':'Bearer fixture-human'}
    def room_update(response):
        block=next(b for b in response.text.strip().split('\n\n') if 'event: room_update' in b)
        data=json.loads(next(line[6:] for line in block.splitlines() if line.startswith('data: ')))
        cursor=next(line[4:] for line in block.splitlines() if line.startswith('id: '))
        return data,cursor
    first,cursor=room_update(human.http.get(f'/api/v1/rooms/{rid}/stream?duration_seconds=0',headers=headers))
    assert [e['event_id'] for e in first['events']]
    assert first['next_cursor']==cursor
    second,next_cursor=room_update(human.http.get(f'/api/v1/rooms/{rid}/stream?duration_seconds=0',headers={**headers,'Last-Event-ID':cursor}))
    assert second['events']==[]
    third,_=room_update(human.http.get(f'/api/v1/rooms/{rid}/stream?duration_seconds=0',headers={**headers,'Last-Event-ID':next_cursor}))
    assert third['events']==[]
    assert core.events('fixture-human',next_cursor,target_room=rid)['events']==[]


def test_sse_unusable_cursor_reports_access_changed_instead_of_skipping(fixture):
    store,core,rid=fixture
    human=LocalClient(store,'fixture-human')
    headers={'Authorization':'Bearer fixture-human'}
    foreign=core.events('fixture-a',target_room=rid)['next_cursor']
    for cursor in ('f'*32,foreign):
        response=human.http.get(f'/api/v1/rooms/{rid}/stream?duration_seconds=0&cursor={cursor}',headers=headers)
        assert response.status_code==200 and 'event: room_update' not in response.text
        block=next(b for b in response.text.strip().split('\n\n') if 'event: access_changed' in b)
        code=json.loads(next(line[6:] for line in block.splitlines() if line.startswith('data: ')))['code']
        assert code in {'cursor_gap','invalid_cursor','invalid_cursor_view'}


@pytest.mark.parametrize('value',['61','-1','abc',''])
def test_stream_duration_must_be_a_bounded_float(fixture,value):
    store,core,rid=fixture
    human=LocalClient(store,'fixture-human')
    response=human.http.get(f'/api/v1/rooms/{rid}/stream?duration_seconds={value}',headers={'Authorization':'Bearer fixture-human'})
    assert response.status_code==422 and response.json()['code']=='invalid_stream_duration'


def test_mode_limits_invalidate_generation_and_old_tasks(fixture):
    _,core,rid=fixture
    old=leased(core,rid)
    approve(core,rid,'change_mode',{'mode':'co_creation'},'mode')
    room=core.read('fixture-human',rid)
    assert room['mode']=='co_creation' and room['generation']==2
    with pytest.raises(Problem):
        core.mutation('fixture-a',rid,'tasks/'+old['id']+'/complete','stale',result(old))
    approve(core,rid,'change_limits',{'limits':{'messages':8,'lease':10}},'limits')
    room=core.read('fixture-human',rid)
    assert room['limits']['messages']==8 and room['limits']['lease']==10 and room['generation']==3
    for parameters in [{'limits':{'messages':0}},{'limits':{'lease':True}},{'limits':{'wall_seconds':1}},{'limits':{'lease':50,'task_seconds':10}}]:
        with pytest.raises(Problem,match='invalid_limits'):
            core.mutation('fixture-human',rid,'controls',json.dumps(parameters),{'action':'change_limits','expected_revision':room['room_revision'],'parameters':parameters})


def test_interventions_and_pin_do_not_change_generation(fixture):
    _,core,rid=fixture
    advice=core.mutation('fixture-human',rid,'messages','advice',{'kind':'advice','client_message_id':'advice','text':'fixture advice'})
    approve(core,rid,'pin',{'message_id':advice['id']},'pin')
    room=core.read('fixture-human',rid)
    assert room['pinned_message_id']==advice['id'] and room['generation']==1
    items=core.interventions('fixture-human',rid)
    assert {i['kind'] for i in items}=={'control','advice'}
    assert next(i for i in items if i['kind']=='control')['state']=='applied'
    with pytest.raises(Problem,match='human_required'): core.interventions('fixture-a',rid)


def test_adoption_cannot_select_hidden_past_version(fixture):
    store,core,rid=fixture
    original=core.mutation('fixture-human',rid,'artifacts','original',{'text':'old private','generation':1,'membership_revision':1})
    old=original['versions'][0]
    updated=core.mutation('fixture-human',rid,'artifacts/'+original['id']+'/versions','new',{'text':'current text','generation':1,'membership_revision':1,'expected_revision':1})
    latest=updated['versions'][-1]
    with store.transaction(rid,'fixture-human') as (_,room):
        room['members']['human']['read_start_seq']=latest['seq']
        room['members']['human']['epoch']+=1
    body={'generation':1,'membership_revision':1,'expected_revision':2,'version_id':old['id']}
    with pytest.raises(Problem,match='not_found'):
        core.mutation('fixture-human',rid,'artifacts/'+original['id']+'/adoptions','hidden',body)
    adopted=core.mutation('fixture-human',rid,'artifacts/'+original['id']+'/adoptions','visible',{**body,'version_id':latest['id']})
    assert adopted['adopted']==latest['id'] and adopted['revision']==3


def test_effective_permissions_follow_current_account_scope(fixture):
    store,core,rid=fixture
    hashed=next(k for k,c in store.credentials.items() if c['id']=='human')
    store.credentials[hashed]['scopes']=['room:read']
    room=core.read('fixture-human',rid)
    assert room['effective_permissions']==['room:read']


def test_api_room_endpoints_and_foreign_scope(fixture):
    store,core,rid=fixture
    client=LocalClient(store)
    assert client.request('GET',f'rooms/{rid}/snapshot')['room']['id']==rid
    assert client.request('GET',f'rooms/{rid}/events')['events']
    other=core.create('fixture-b',{'topic':'private fixture'},'other')
    with pytest.raises(httpx.HTTPStatusError): client.request('GET',f'rooms/{other["id"]}/events')
