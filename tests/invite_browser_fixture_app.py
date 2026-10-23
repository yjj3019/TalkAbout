"""Explicit ephemeral browser fixture; no external provider or persistent store."""
import os
import uvicorn
from talkabout.api import create_app
from talkabout.auth import bootstrap_human
from talkabout.store import MemoryStore

store=MemoryStore()
bootstrap_human(store,'invite-browser-fixture','Synthetic owner','owner','fixture-password-not-real')
app=create_app(store,start_scheduler=False)

@app.get('/__invite_fixture__')
def fixture_identity():
    return {'synthetic_memory_fixture':True,'fixture_id':os.environ['TALKABOUT_INVITE_FIXTURE_ID']}

uvicorn.run(app,host='127.0.0.1',port=int(os.environ['TALKABOUT_FIXTURE_PORT']),access_log=False,log_level='error')
