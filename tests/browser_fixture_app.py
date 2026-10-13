"""Ephemeral browser-test fixture; not an operating bootstrap or default app."""
import uvicorn
from talkabout.auth import bootstrap_human
from talkabout.api import create_app
from talkabout.store import MemoryStore

store = MemoryStore()
bootstrap_human(store, 'browser-fixture', 'Fixture Owner', 'owner', 'fixture-password-not-real')
uvicorn.run(create_app(store, start_scheduler=False), host='127.0.0.1', port=8081, access_log=False)
