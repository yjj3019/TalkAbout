"""Fresh synthetic GUI fixture; no provider, account files or operating state."""
import os,time
import uvicorn
from talkabout.api import create_app
from talkabout.auth import bootstrap_human
from talkabout.core import Core
from talkabout.domain import HUMAN_PERMISSIONS,token_hash
from talkabout.store import MemoryStore
store=MemoryStore()
human=bootstrap_human(store,'readiness-fixture','Fixture Owner','owner','fixture-password-not-real')
store.credentials[token_hash('fixture-seed-only')]={'id':human['id'],'scopes':sorted(HUMAN_PERMISSIONS|{'rooms:create'}),'expires_at':time.time()+3600,'revoked':False}
Core(store).create('fixture-seed-only',{'topic':'Synthetic readiness room','start_policy':'manual'},'prepare-only')
uvicorn.run(create_app(store,start_scheduler=False),host='127.0.0.1',port=int(os.environ['TALKABOUT_FIXTURE_PORT']),access_log=False)
