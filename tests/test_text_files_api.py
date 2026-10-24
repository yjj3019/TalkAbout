"""Real attachment HTTP path with synthetic data and private temporary storage."""
import hashlib
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import LocalClient,control
from talkabout.api import create_app
from talkabout.domain import token_hash
from talkabout.file_migration import migrate_selected_rooms
from talkabout.text_files import MAX_BYTES,PrivateTextStorage,TextAttachments,ref_of
from talkabout.worker import Journal,SyntheticRuntime,Worker


@pytest.fixture
def text_api(fixture,tmp_path):
    store,_,rid=fixture
    storage=PrivateTextStorage(tmp_path/'private-text',create=True)
    app=create_app(store,start_scheduler=False,allow_fixture_bearer=True,text_storage=storage)
    http=TestClient(app)
    yield store,app.state.core,rid,storage,http
    http.close()


def request(http,method,path,body=None,token='fixture-human',key='request',content=None):
    headers={'Authorization':'Bearer '+token,'Idempotency-Key':key}
    if content is not None:headers['Content-Type']='application/octet-stream'
    return http.request(method,'/api/v1/'+path,headers=headers,**({'content':content} if content is not None else {'json':body}))


def upload(api,content=b'The fixture launch color is cobalt-blue.',readers=None,consent=True,name='source.txt',key='upload'):
    store,core,rid,storage,http=api
    body={'name':name,'size':len(content),'sha256':hashlib.sha256(content).hexdigest(),'media_type':'text/plain',
          'readers':['a'] if readers is None else readers,'allow_agent_processing':consent,'ack_unscanned':True}
    response=request(http,'POST',f'rooms/{rid}/uploads',body,key=key);assert response.status_code==200,response.text
    file=response.json();fid=file['file_id']
    response=request(http,'PUT',f'rooms/{rid}/uploads/{fid}/content',content=content,key=key+':content');assert response.status_code==200,response.text
    response=request(http,'POST',f'rooms/{rid}/uploads/{fid}/complete',{'expected_revision':response.json()['revision']},key=key+':complete');assert response.status_code==200,response.text
    file=response.json()
    assert file['state']=='validated_utf8_text' and file['malware_scan']=='not_run' and file['malware_safety']=='not_asserted'
    return file


def publish(api,file,key='message'):
    _,_,rid,_,http=api
    response=request(http,'POST',f'rooms/{rid}/messages',{'kind':'question','text':'Read the attached fixture.',
        'client_message_id':key,'attachment_refs':[ref_of(file)]},key=key)
    assert response.status_code==200,response.text
    return response.json()


def claim(api,key='claim',token='fixture-a'):
    _,_,rid,_,http=api
    room=request(http,'GET',f'rooms/{rid}',token=token).json()
    task=next(t for t in room['tasks'] if t['state']=='queued' and t['assignee']=='a')
    response=request(http,'POST',f"rooms/{rid}/tasks/{task['id']}/claim",
        {k:task[k] for k in ('generation','membership_revision')},token,key)
    assert response.status_code==200,response.text
    return response.json()


def read(api,task,file,key='read',token='fixture-a',**updates):
    _,_,rid,_,http=api
    body={k:task[k] for k in ('generation','membership_revision','attempt','lease_proof')}
    body.update(start=0,end=8000);body.update(updates)
    return request(http,'POST',f"rooms/{rid}/tasks/{task['id']}/files/{file['file_id']}/read",body,token,key)


def test_human_upload_message_worker_read_and_grounded_synthetic_reply(text_api,tmp_path):
    store,core,rid,storage,http=text_api
    file=upload(text_api);posted=publish(text_api,file)
    assert posted['attachment_refs']==[ref_of(file)]
    client=LocalClient(store);client.http.close();client.http=http
    class Observed(SyntheticRuntime):
        calls=0
        def run(self,context,cancel):
            self.calls+=1
            assert context['file_excerpts'][0]['text']=='The fixture launch color is cobalt-blue.'
            assert context['file_excerpts'][0]['untrusted_data'] is True
            assert 'fixture-a' not in json.dumps(context) and 'lease_proof' not in json.dumps(context)
            return super().run(context,cancel)
    runtime=Observed();journal=Journal(tmp_path/'worker.sqlite')
    try:assert Worker(client,journal,runtime).step()=='completed'
    finally:journal.close()
    messages=core.read('fixture-human',rid)['messages']
    assert runtime.calls==1 and 'cobalt-blue' in messages[-1]['text'] and 'Synthetic file read' in messages[-1]['text']
    download=request(http,'GET',f"rooms/{rid}/files/{file['file_id']}/download")
    assert download.status_code==200 and b'cobalt-blue' in download.content
    assert download.headers['x-content-type-options']=='nosniff'
    assert download.headers['content-disposition'].startswith('attachment;')
    assert not list((storage.root).glob('*.txt')) and len(list(storage.root.glob('*.blob')))==1


@pytest.mark.parametrize('change',['pause','revoke','expire','stop'])
def test_worker_rechecks_after_file_read_before_runtime(text_api,tmp_path,change):
    store,core,rid,_,http=text_api
    file=upload(text_api);publish(text_api,file)
    journal=Journal(tmp_path/'worker.sqlite')
    class Runtime(SyntheticRuntime):
        calls=0
        def run(self,context,cancel):self.calls+=1;return super().run(context,cancel)
    class ChangingClient(LocalClient):
        def request(self,method,path,body=None,key=None,params=None):
            result=super().request(method,path,body,key,params)
            if method=='POST' and path.endswith('/read'):
                if change=='pause':control(core,rid,'pause')
                elif change=='revoke':
                    current=store.rooms[rid]['text_files'][file['file_id']]
                    response=request(http,'PATCH',f"rooms/{rid}/files/{file['file_id']}/consent",{'expected_revision':current['revision'],'readers':[],'allow_agent_processing':False},key='revoke-mid-read')
                    assert response.status_code==200
                elif change=='expire':
                    for task in store.rooms[rid]['tasks'].values():
                        if task['state']=='leased':task['lease_expires_at']=time.time()-1
                else:
                    with journal.db:journal.db.execute("INSERT OR REPLACE INTO settings VALUES('stop','true')")
            return result
    client=ChangingClient(store);client.http.close();client.http=http;runtime=Runtime()
    try:assert Worker(client,journal,runtime).step() in {'stale','stopped'}
    finally:journal.close()
    assert runtime.calls==0


def test_consent_revoked_at_call_start_never_reaches_runtime(text_api,tmp_path):
    store,_,rid,_,http=text_api
    file=upload(text_api);publish(text_api,file)
    class Runtime(SyntheticRuntime):
        calls=0
        def run(self,context,cancel):
            self.calls+=1
            return super().run(context,cancel)
    class RevokingClient(LocalClient):
        revoked=False
        start_status=None
        def request(self,method,path,body=None,key=None,params=None):
            if method=='POST' and path.endswith('/start') and not self.revoked:
                self.revoked=True
                current=store.rooms[rid]['text_files'][file['file_id']]
                response=request(http,'PATCH',f"rooms/{rid}/files/{file['file_id']}/consent",
                    {'expected_revision':current['revision'],'readers':[],
                     'allow_agent_processing':False},key='revoke-before-call-start')
                assert response.status_code==200
            try:
                result=super().request(method,path,body,key,params)
            except httpx.HTTPStatusError as error:
                if method=='POST' and path.endswith('/start'):
                    self.start_status=error.response.status_code
                raise
            if method=='POST' and path.endswith('/start'):
                self.start_status=200
            return result
    client=RevokingClient(store);client.http.close();client.http=http
    runtime=Runtime();journal=Journal(tmp_path/'worker.sqlite')
    try:
        try:
            Worker(client,journal,runtime).step()
        except httpx.HTTPStatusError as error:
            # Inspect call delivery even if the old implementation only rejects
            # the later completion. The start-status assertion remains strict.
            assert error.response.status_code in {403,422}
    finally:
        journal.close()
    assert client.revoked
    assert runtime.calls==0
    assert client.start_status==403
    task=next(t for t in store.rooms[rid]['tasks'].values() if t['assignee']=='a')
    assert not task.get('execution')
    assert task['state']=='leased' and 'result_message_id' not in task


@pytest.mark.parametrize('legacy_read_record',[False,True])
def test_call_start_revalidates_read_ref_independently_of_read_key(text_api,legacy_read_record):
    store,core,rid,_,http=text_api
    file=upload(text_api);publish(text_api,file)
    queued=next(t for t in core.read('fixture-a',rid)['tasks'] if t['assignee']=='a' and t['state']=='queued')
    body={k:queued[k] for k in ('generation','membership_revision')}
    response=request(http,'POST',f"rooms/{rid}/tasks/{queued['id']}/claim",
                     {**body,'execution_protocol':'server_start_v1'},'fixture-a','modern-claim')
    assert response.status_code==200
    task=response.json()
    assert read(text_api,task,file,key='arbitrary-reader-key').status_code==200
    stored=store.rooms[rid]['tasks'][task['id']]
    if legacy_read_record:
        stored['_file_reads']['arbitrary-reader-key'].pop('ref')
        stored['_file_reads']['arbitrary-reader-key'].pop('attempt')
    current=store.rooms[rid]['text_files'][file['file_id']]
    assert request(http,'PATCH',f"rooms/{rid}/files/{file['file_id']}/consent",
                   {'expected_revision':current['revision'],'readers':[],
                    'allow_agent_processing':False},key='revoke-read-ref').status_code==200
    fence={k:task[k] for k in ('generation','membership_revision','attempt')}
    response=request(http,'POST',f"rooms/{rid}/tasks/{task['id']}/start",fence,'fixture-a','start-read-ref')
    assert response.status_code==403 and response.json()['code']=='task_input_revoked'
    assert not store.rooms[rid]['tasks'][task['id']].get('execution')


def test_call_start_ignores_unread_attachment_consent(text_api):
    store,core,rid,_,http=text_api
    first=upload(text_api,key='first');publish(text_api,first,key='first-message')
    second=upload(text_api,key='second');publish(text_api,second,key='second-message')
    queued=next(t for t in core.read('fixture-a',rid)['tasks'] if t['assignee']=='a' and t['state']=='queued')
    response=request(http,'POST',f"rooms/{rid}/tasks/{queued['id']}/claim",
        {**{k:queued[k] for k in ('generation','membership_revision')},
         'execution_protocol':'server_start_v1'},'fixture-a','read-one-claim')
    assert response.status_code==200
    task=response.json()
    assert read(text_api,task,first,key='read-first-only').status_code==200
    current=store.rooms[rid]['text_files'][second['file_id']]
    assert request(http,'PATCH',f"rooms/{rid}/files/{second['file_id']}/consent",
        {'expected_revision':current['revision'],'readers':[],
         'allow_agent_processing':False},key='revoke-unread-ref').status_code==200
    fence={k:task[k] for k in ('generation','membership_revision','attempt')}
    response=request(http,'POST',f"rooms/{rid}/tasks/{task['id']}/start",fence,'fixture-a','start-read-one')
    assert response.status_code==200 and response.json()['execute'] is True
    assert '_file_reads' not in json.dumps(core.read('fixture-a',rid))


def test_claim_proof_only_in_claim_reply_and_replay_not_database_or_snapshots(text_api):
    store,core,rid,_,http=text_api
    file=upload(text_api);publish(text_api,file);task=claim(text_api)
    proof=task['lease_proof'];assert len(proof)==64
    queued_body={k:task[k] for k in ('generation','membership_revision')}
    replayed=request(http,'POST',f"rooms/{rid}/tasks/{task['id']}/claim",queued_body,'fixture-a','claim').json()
    assert replayed['lease_proof']==proof
    assert proof not in json.dumps(store.rooms[rid])
    for path in [f'rooms/{rid}',f'rooms/{rid}/snapshot','agents/me/snapshot']:
        response=request(http,'GET',path,token='fixture-a')
        assert proof not in response.text and '_file_lease_hash' not in response.text
    assert read(text_api,task,file).status_code==200
    assert read(text_api,task,file,lease_proof='0'*64,key='wrong-owner').status_code==403


def test_same_agent_other_credential_cannot_steal_file_lease(text_api):
    store,_,rid,_,_=text_api
    file=upload(text_api);publish(text_api,file);task=claim(text_api)
    store.credentials[token_hash('fixture-second')]={**store.credentials[token_hash('fixture-a')],'expires_at':time.time()+60}
    assert read(text_api,task,file,token='fixture-second').status_code==403


@pytest.mark.parametrize('action',['pause','close','change_topic'])
def test_control_changes_block_file_read_before_model(text_api,action):
    _,core,rid,_,_=text_api;file=upload(text_api);publish(text_api,file);task=claim(text_api)
    if action=='change_topic':
        room=core.read('fixture-human',rid)
        pending=core.mutation('fixture-human',rid,'controls','topic',{'action':action,'expected_revision':room['room_revision'],'parameters':{'topic':'Changed'}})
        core.mutation('fixture-human',rid,'controls/'+pending['id']+'/approve','topic-approve',{})
    else:control(core,rid,action)
    assert read(text_api,task,file).status_code==409


def test_consent_and_delete_rechecked_even_for_cached_reads(text_api):
    _,_,rid,_,http=text_api;file=upload(text_api);publish(text_api,file);task=claim(text_api)
    first=read(text_api,task,file);assert first.status_code==200
    assert read(text_api,task,file).json()==first.json()
    revoked=request(http,'PATCH',f"rooms/{rid}/files/{file['file_id']}/consent",
        {'expected_revision':file['revision'],'readers':[],'allow_agent_processing':False},key='revoke')
    assert revoked.status_code==200
    assert read(text_api,task,file).status_code==404
    deleted=request(http,'DELETE',f"rooms/{rid}/files/{file['file_id']}",{'expected_revision':revoked.json()['revision']},key='delete')
    assert deleted.status_code==200
    for suffix in ['text','download']:
        assert request(http,'GET',f"rooms/{rid}/files/{file['file_id']}/{suffix}").status_code==404


def test_room_agent_task_and_provider_consent_are_independent_gates(text_api):
    store,core,rid,_,http=text_api
    file=upload(text_api,readers=[],consent=False);publish(text_api,file);task=claim(text_api)
    assert task['attachment_refs']==[] and read(text_api,task,file).status_code==404
    assert request(http,'GET',f"rooms/{rid}/files/{file['file_id']}/download",token='fixture-a').status_code==403
    foreign=core.create('fixture-human',{'topic':'Separate room'},'separate')['id']
    assert request(http,'GET',f"rooms/{foreign}/files/{file['file_id']}/download").status_code==404
    forged=request(http,'POST',f'rooms/{rid}/uploads',{'name':'x.txt','size':1,'sha256':hashlib.sha256(b'x').hexdigest(),
        'media_type':'text/plain','readers':['a'],'allow_agent_processing':False,'ack_unscanned':True},key='no-consent')
    assert forged.status_code==422 and forged.json()['code']=='agent_processing_consent_required'


def test_idempotency_read_budget_and_storage_integrity(text_api):
    store,_,rid,storage,http=text_api;file=upload(text_api,b'x'*30000);publish(text_api,file);task=claim(text_api)
    first=read(text_api,task,file);assert len(first.json()['text'])==8000 and first.json()['truncated']
    for _ in range(2):assert read(text_api,task,file).status_code==200
    assert store.rooms[rid]['tasks'][task['id']]['_file_chars']==8000
    assert read(text_api,task,file,key='read-two',start=8000,end=30000).status_code==200
    assert read(text_api,task,file,key='read-three',start=16000,end=30000).status_code==200
    assert read(text_api,task,file,key='read-four',start=24000,end=30000).status_code==429
    (storage.root/(file['version_id']+'.blob')).write_bytes(b'corrupt')
    assert request(http,'GET',f"rooms/{rid}/files/{file['file_id']}/download").status_code==503


def test_deleted_attachments_keep_history_without_consuming_active_slots(text_api):
    store,_,rid,storage,http=text_api
    first=upload(text_api,key='historical-file')
    current=store.rooms[rid]['text_files'][first['file_id']]
    for index in range(16):
        store.rooms[rid]['text_files']['deleted-'+str(index)]={**current,'file_id':'deleted-'+str(index),'state':'deleted'}
    assert request(http,'DELETE',f"rooms/{rid}/files/{first['file_id']}",{'expected_revision':current['revision']},key='remove-file').status_code==200
    second=upload(text_api,b'Another permitted attachment.',key='replacement-file')
    assert second['file_id']!=first['file_id']
    assert len(store.rooms[rid]['text_files'])==18
    assert store.rooms[rid]['text_files'][first['file_id']]['state']=='deleted'
    assert request(http,'GET',f"rooms/{rid}/files/{first['file_id']}/download").status_code==404
    assert len(list(storage.root.glob('*.blob')))==2


def test_legacy_file_lease_validates_an_explicit_session(text_api):
    from tests.test_participation import SESSION,OTHER
    store,core,rid,_,_=text_api
    file=upload(text_api);publish(text_api,file)
    task=claim(text_api)
    core.participation('fixture-a',rid,'join',{'generation':1,'membership_revision':2,'session_id':SESSION})
    core.participation('fixture-a',rid,'join',{'generation':1,'membership_revision':2,'session_id':OTHER,'previous_session_id':SESSION})
    assert read(text_api,task,file,key='legacy-omitted').status_code==200
    before=store.rooms[rid]['tasks'][task['id']]['_file_chars']
    response=read(text_api,task,file,key='legacy-retired',session_id=SESSION)
    assert response.status_code==409 and response.json()['code']=='stale_session'
    assert store.rooms[rid]['tasks'][task['id']]['_file_chars']==before
    assert read(text_api,task,file,key='legacy-current',session_id=OTHER).status_code==200


def test_session_bound_file_lease_cannot_drop_or_replace_its_session(text_api):
    from tests.test_participation import SESSION,OTHER
    store,core,rid,_,http=text_api
    file=upload(text_api);publish(text_api,file)
    core.participation('fixture-a',rid,'join',{'generation':1,'membership_revision':2,'session_id':SESSION})
    task=next(t for t in core.read('fixture-a',rid)['tasks'] if t['state']=='queued' and t['assignee']=='a')
    response=request(http,'POST',f"rooms/{rid}/tasks/{task['id']}/claim",
                     {'generation':1,'membership_revision':2,'session_id':SESSION},'fixture-a','session-bound-claim')
    assert response.status_code==200
    task=response.json()
    assert read(text_api,task,file,session_id=SESSION).status_code==200
    before=store.rooms[rid]['tasks'][task['id']]['_file_chars']
    assert read(text_api,task,file,key='missing-session').json()['code']=='session_required'
    core.participation('fixture-a',rid,'join',{'generation':1,'membership_revision':2,'session_id':OTHER,'previous_session_id':SESSION})
    assert read(text_api,task,file,key='old-session',session_id=SESSION).json()['code']=='stale_session'
    assert read(text_api,task,file,key='new-session',session_id=OTHER).json()['code']=='stale_task_session'
    assert store.rooms[rid]['tasks'][task['id']]['_file_chars']==before


@pytest.mark.parametrize('name,data,mime',[
    ('../outside.txt',b'x','text/plain'),('image.png',b'PNG','image/png'),('document.pdf',b'%PDF-1.7','application/pdf'),
    ('notes.txt',b'\xff','text/plain'),('notes.txt',b'x\0y','text/plain'),('notes.txt',b'<script>alert(1)</script>','text/plain'),
    ('notes.txt',b'%PDF-1.7 test','text/plain'),('notes.txt',b'x'*(MAX_BYTES+1),'text/plain'),
],ids=['traversal','image','pdf','invalid-utf8','nul','active-html','renamed-pdf','oversize'])
def test_reject_unsupported_or_invalid_bytes_without_publishing(text_api,name,data,mime):
    _,_,rid,storage,http=text_api
    body={'name':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'media_type':mime,
          'readers':[],'allow_agent_processing':False,'ack_unscanned':True}
    init=request(http,'POST',f'rooms/{rid}/uploads',body)
    if init.status_code==200:
        received=request(http,'PUT',f"rooms/{rid}/uploads/{init.json()['file_id']}/content",content=data,key='content')
        assert received.status_code in {413,415}
    else:assert init.status_code in {413,415,422}
    assert not list(storage.root.iterdir())


def test_fake_scanner_state_cannot_unlock_actual_download(text_api):
    store,_,rid,_,http=text_api;file=upload(text_api)
    store.rooms[rid]['text_files'][file['file_id']]['state']='fixture_ready'
    assert request(http,'GET',f"rooms/{rid}/files/{file['file_id']}/download").status_code==409


def test_revoked_token_and_conflicting_consent_revision_fail_closed(text_api):
    store,core,rid,_,http=text_api
    file=upload(text_api);publish(text_api,file);task=claim(text_api)
    body={'expected_revision':file['revision'],'readers':[],'allow_agent_processing':False}
    assert request(http,'PATCH',f"rooms/{rid}/files/{file['file_id']}/consent",body,key='first-consent').status_code==200
    assert request(http,'PATCH',f"rooms/{rid}/files/{file['file_id']}/consent",body,key='second-consent').status_code==409
    assert next(t for t in core.read('fixture-a',rid)['tasks'] if t['id']==task['id'])['attachment_refs']==[]
    store.credentials[token_hash('fixture-a')]['revoked']=True
    assert read(text_api,task,file).status_code==401


def test_read_idempotency_key_cannot_be_reused_for_different_file(text_api):
    first=upload(text_api,key='first');publish(text_api,first,key='first-message')
    second=upload(text_api,b'A second distinct source.',key='second');publish(text_api,second,key='second-message')
    task=claim(text_api)
    assert read(text_api,task,first,key='shared-read').status_code==200
    assert read(text_api,task,second,key='shared-read').status_code==409


def test_rejoined_agent_requires_fresh_file_processing_consent(text_api):
    store,core,rid,_,http=text_api
    file=upload(text_api);publish(text_api,file)
    member=core.read('fixture-human',rid)['members']['a']
    core.mutation('fixture-human',rid,'memberships/a','block-reader',{'expected_revision':member['revision'],'permissions':member['permissions'],'status':'blocked'})
    member=core.read('fixture-human',rid)['members']['a']
    invitation=core.mutation('fixture-human',rid,'memberships/a','reinvite-reader',{'expected_revision':member['revision'],'permissions':member['permissions'],'status':'pending'})
    core.accept('fixture-a',rid,'reaccept-reader',{'expected_revision':invitation['revision'],'acknowledge_private':True})
    publish(text_api,file,key='republish-after-rejoin')
    assert core.read('fixture-a',rid)['messages'][-1].get('attachment_refs')==[]
    assert request(http,'GET',f'rooms/{rid}/files',token='fixture-a').json()==[]
    current=request(http,'GET',f'rooms/{rid}/files').json()[0]
    consent=request(http,'PATCH',f"rooms/{rid}/files/{file['file_id']}/consent",{'expected_revision':current['revision'],'readers':['a'],'allow_agent_processing':True},key='fresh-consent')
    assert consent.status_code==200
    assert request(http,'GET',f'rooms/{rid}/files',token='fixture-a').json()[0]['file_id']==file['file_id']


def test_additive_migration_preserves_old_messages_and_is_idempotent(text_api):
    store,core,rid,_,_=text_api
    core.mutation('fixture-human',rid,'messages','old-message',{'kind':'advice','text':'Old content','client_message_id':'old'})
    before=json.dumps(store.rooms[rid]['messages'],sort_keys=True)
    assert migrate_selected_rooms(store,'fixture-human',[rid])['schema_version']==1
    assert migrate_selected_rooms(store,'fixture-human',[rid])['rooms']==[rid]
    assert json.dumps(store.rooms[rid]['messages'],sort_keys=True)==before


def test_postgres_attachment_round_trip_and_migration(postgres_fixture,tmp_path):
    from talkabout.store import PostgresStore
    store,_,rid=postgres_fixture
    storage=PrivateTextStorage(tmp_path/'private-text',create=True)
    app=create_app(store,start_scheduler=False,allow_fixture_bearer=True,text_storage=storage)
    with TestClient(app) as http:
        api=(store,app.state.core,rid,storage,http)
        migrate_selected_rooms(store,'fixture-human',[rid]);file=upload(api);publish(api,file);task=claim(api)
        assert 'cobalt-blue' in read(api,task,file).json()['text']
        restored=PostgresStore(store.dsn,store.schema)
        with TestClient(create_app(restored,start_scheduler=False,allow_fixture_bearer=True,text_storage=storage)) as again:
            assert request(again,'GET',f"rooms/{rid}/files/{file['file_id']}/download").status_code==200


@pytest.mark.parametrize('scenario',['worker_race','read_key','unread'])
def test_postgres_call_start_attachment_consent(postgres_fixture,tmp_path,scenario):
    store,_,rid=postgres_fixture
    storage=PrivateTextStorage(tmp_path/'private-text',create=True)
    app=create_app(store,start_scheduler=False,allow_fixture_bearer=True,text_storage=storage)
    migrate_selected_rooms(store,'fixture-human',[rid])
    with TestClient(app) as http:
        api=(store,app.state.core,rid,storage,http)
        if scenario=='worker_race':
            test_consent_revoked_at_call_start_never_reaches_runtime(api,tmp_path)
        elif scenario=='read_key':
            test_call_start_revalidates_read_ref_independently_of_read_key(api,False)
        else:
            test_call_start_ignores_unread_attachment_consent(api)
