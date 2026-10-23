"""New isolated synthetic browser fixture; no operating data or model access."""
import os
import time
import uvicorn
from talkabout.api import create_app
from talkabout.auth import bootstrap_human
from talkabout.core import Core
from talkabout.domain import AGENT_PERMISSIONS,HUMAN_PERMISSIONS,SELF_PERMISSIONS,token_hash
from talkabout.store import MemoryStore
from talkabout.text_files import PrivateTextStorage

store=MemoryStore()
human=bootstrap_human(store,'text-fixture','Fixture Owner','owner','fixture-password-not-real')
store.provision({'id':'file-agent','owner':human['owner'],'kind':'agent','display_name':'Synthetic Reader','handle':'synthetic-reader'},'fixture-file-agent',AGENT_PERMISSIONS|SELF_PERMISSIONS)
# Owner fixture bearer is restricted to this test application.
store.credentials[token_hash('fixture-file-human')]={'id':human['id'],'scopes':sorted(HUMAN_PERMISSIONS|{'rooms:create','agent:manage'}),'expires_at':time.time()+3600,'revoked':False}
core=Core(store)
room=core.create('fixture-file-human',{'topic':'Synthetic text attachment'},'fixture-room')
invite=core.mutation('fixture-file-human',room['id'],'memberships','agent-member',{'principal_id':'file-agent','permissions':sorted(AGENT_PERMISSIONS),'status':'pending'})
core.accept('fixture-file-agent',room['id'],'accept',{'expected_revision':invite['revision'],'acknowledge_private':True})
storage=PrivateTextStorage(os.environ['TALKABOUT_TEXT_FIXTURE_ROOT'],create=True)
uvicorn.run(create_app(store,start_scheduler=False,text_storage=storage),host='127.0.0.1',port=int(os.environ['TALKABOUT_FIXTURE_PORT']),access_log=False)
