"""Ephemeral browser-test fixture; not an operating bootstrap or default app."""
import uvicorn
import os
import time
from talkabout.auth import bootstrap_human
from talkabout.api import create_app
from talkabout.store import MemoryStore
from talkabout.core import Core
from talkabout.domain import HUMAN_PERMISSIONS, token_hash

store = MemoryStore()
bootstrap_human(store, 'browser-fixture', 'Fixture Owner', 'owner', 'fixture-password-not-real')
manager = bootstrap_human(store, 'browser-manager-fixture', 'Fixture Manager', 'manager', 'fixture-password-not-real')
scopes = sorted(HUMAN_PERMISSIONS | {'rooms:create'})
store.principals[manager['id']]['account_scopes'] = scopes
store.credentials[token_hash('fixture-manager-only')] = {'id': manager['id'], 'scopes': scopes, 'expires_at': time.time()+3600, 'revoked': False}
Core(store).create('fixture-manager-only', {'topic': 'Synthetic manager view', 'start_policy': 'manual'}, 'fixture-manager-room')
uvicorn.run(create_app(store, start_scheduler=False), host='127.0.0.1', port=int(os.environ.get('TALKABOUT_FIXTURE_PORT', '8081')), access_log=False)
