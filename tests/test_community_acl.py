import json
import time
import pytest
from talkabout.domain import AGENT_PERMISSIONS, SELF_PERMISSIONS, Problem
from talkabout.identity import IdentityService


def test_connection_is_authenticated_self_only_fixed_fields_and_not_provider_proof(fixture):
    store,core,rid=fixture;identity=IdentityService(store)
    observed=identity.mutate('fixture-a','connection','observe',{'runtime':'codex','state':'idle'})
    assert observed['last_seen_at']<=time.time() and observed['execution_evidence']=='worker_reported'
    assert core.read('fixture-human',rid)['members']['a']['connection']['runtime']=='codex'
    assert 'connection' not in core.read('fixture-b',rid)['members']['a']
    for token,body in [('fixture-human',{'runtime':'codex','state':'idle'}),('fixture-a',{'runtime':'codex','state':'idle','role':'admin'}),('fixture-a',{'runtime':'codex','state':'idle','last_seen_at':time.time()+9999}),('fixture-a',{'runtime':'codex','state':'failed','error_code':'arbitrary-private-message'})]:
        with pytest.raises(Problem):identity.mutate(token,'connection','invalid',body)
    store.revoke('fixture-a')
    with pytest.raises(Problem,match='unauthorized'):identity.mutate('fixture-a','connection','revoked',{'runtime':'codex','state':'idle'})


def test_credential_metadata_has_no_secret_hash_and_is_human_owned_only(fixture):
    store,core,rid=fixture;identity=IdentityService(store)
    issued=identity.mutate('fixture-human','credentials/a','issue',{'room_id':rid,'scopes':sorted(AGENT_PERMISSIONS|SELF_PERMISSIONS),'expires_in':60})
    metadata=identity.credential_metadata('fixture-human','a')
    assert metadata[0]['credential_id']==issued['credential_id']
    assert set(metadata[0])=={'credential_id','room_id','scopes','expires_at','revoked'}
    assert issued['token'] not in json.dumps(metadata) and 'token_hash' not in json.dumps(metadata)
    with pytest.raises(Problem):identity.credential_metadata('fixture-a','a')
    identity.mutate('fixture-human','credentials/a/'+issued['credential_id']+'/revoke','revoke',{})
    assert identity.credential_metadata('fixture-human','a')[0]['revoked']


def test_presence_observations_do_not_exhaust_identity_ledger(fixture):
    store,core,rid=fixture;identity=IdentityService(store)
    for i in range(2010):identity.mutate('fixture-a','connection',str(i),{'runtime':'synthetic','state':'idle'})
    assert len(store.principals['a']['identity_operations'])==0


def test_room_scoped_contact_cannot_make_another_room_ready(fixture):
    from test_community_flow import control
    store,core,rid=fixture;identity=IdentityService(store)
    other=core.create('fixture-human',{'topic':'Synthetic other room','start_policy':'manual'},'other-preparation')
    invite=core.mutation('fixture-human',other['id'],'memberships','invite-other',{'principal_id':'a','permissions':sorted(AGENT_PERMISSIONS)})
    core.accept('fixture-a',other['id'],'accept-other',{'expected_revision':invite['revision'],'acknowledge_private':True})
    issued=identity.mutate('fixture-human','credentials/a','room-scoped-observation',{'room_id':rid,'scopes':sorted(AGENT_PERMISSIONS|SELF_PERMISSIONS),'expires_in':60})
    identity.mutate(issued['token'],'connection','contact-original-room',{'runtime':'codex','state':'idle'})
    assert core.read('fixture-human',other['id'])['members']['a']['connection'] is None
    with pytest.raises(Problem,match='worker_connection_required'):control(core,core.read('fixture-human',other['id']),'start','wrong-room-contact')
