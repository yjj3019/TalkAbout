"""Installation bootstrap, separate from human sessions and agent credentials."""
import hmac
import secrets
import sys
import time
from contextlib import contextmanager

from .domain import Problem, ident, normalize_name, require, token_hash
from .auth import ADMIN_SCOPES, password_record


class SetupStore:
    @contextmanager
    def installation(self):
        if hasattr(self, 'lock'):
            with self.lock:
                state = dict(getattr(self, 'setup_state', {}))
                legacy = any(p['kind'] == 'human' for p in self.principals.values())
                def insert(principal, record):
                    self.principals[principal['id']] = principal
                    self.passwords[principal['id']] = record
                yield state, legacy, insert
                self.setup_state = state
        else:
            from psycopg.types.json import Jsonb
            with self.connect() as conn:
                # Global installation lock, also shared with legacy CLI bootstrap.
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('talkabout:installation',0))")
                row = conn.execute('SELECT payload FROM installation_setup WHERE id=1 FOR UPDATE').fetchone()
                state = dict(row['payload']) if row else {}
                legacy = bool(conn.execute("SELECT id FROM principals WHERE payload->>'kind'='human' LIMIT 1").fetchone())
                def insert(principal, record):
                    conn.execute('INSERT INTO principals(id,owner,name_key,handle,payload) VALUES(%s,%s,%s,%s,%s)',
                                 (principal['id'], principal['owner'], principal['name_key'], principal['handle'], Jsonb(principal)))
                    conn.execute('INSERT INTO human_passwords(principal_id,record) VALUES(%s,%s)', (principal['id'], Jsonb(record)))
                yield state, legacy, insert
                if state != (row['payload'] if row else {}):
                    conn.execute('INSERT INTO installation_setup(id,payload) VALUES(1,%s) ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload', (Jsonb(state),))

    def setup_required(self):
        # Existing installations without the new table remain usable if already owned.
        if not hasattr(self, 'lock'):
            with self.connect() as conn:
                if conn.execute("SELECT id FROM principals WHERE payload->>'kind'='human' LIMIT 1").fetchone():
                    return False
        with self.installation() as (state, legacy, _):
            return not legacy and state.get('state') != 'complete'


def issue_code(store, ttl=900):
    require(isinstance(ttl, int) and 60 <= ttl <= 3600, 'invalid_setup_ttl', 422)
    with store.installation() as (state, legacy, _):
        require(not legacy and state.get('state') != 'complete', 'setup_already_initialized', 409)
        require(not state or state.get('expires_at', 0) <= time.time() or state.get('failures', 0) >= 5,
                'setup_code_active', 409)
        raw = secrets.token_urlsafe(32)
        state.clear()
        state.update(state='pending', code_hash=token_hash(raw), expires_at=time.time()+ttl, failures=0)
    return raw


def complete_setup(store, body):
    require(set(body) == {'code', 'workspace_id', 'display_name', 'username', 'password', 'confirmation'}, 'invalid_setup', 422)
    require(isinstance(body['code'], str) and len(body['code']) <= 128, 'invalid_setup', 422)
    workspace = body['workspace_id']
    require(isinstance(workspace, str) and 1 <= len(workspace) <= 128, 'invalid_workspace', 422)
    name, name_key, handle = normalize_name(body['display_name'], body['username'])
    require(body['password'] == body['confirmation'], 'password_confirmation_mismatch', 422)
    # Validate cheaply before code checking. Derive only after the locked code check.
    require(isinstance(body['password'], str) and 12 <= len(body['password']) <= 1024, 'invalid_password', 422)
    error = None
    with store.installation() as (state, legacy, insert):
        if legacy or state.get('state') == 'complete':
            error = Problem('setup_complete', 409)
        elif not state or state.get('expires_at', 0) <= time.time():
            error = Problem('setup_unavailable', 403)
        elif state.get('failures', 0) >= 5:
            error = Problem('setup_attempt_limit', 429)
        elif not hmac.compare_digest(state['code_hash'], token_hash(body['code'])):
            state['failures'] += 1
            error = Problem('setup_invalid', 403)
        else:
            principal = {'id': ident(), 'owner': workspace, 'kind': 'human', 'display_name': name,
                         'name_key': name_key, 'handle': handle, 'identity_revision': 1,
                         'account_scopes': sorted(ADMIN_SCOPES)}
            insert(principal, password_record(body['password']))
            state.clear()
            state.update(state='complete', completed_at=time.time(), principal_id=principal['id'])
    # Failed attempts must commit rather than roll back with the error.
    if error:
        raise error
    return {'status': 'complete', 'login_required': True}


def terminal_issue(store):
    require(sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty(), 'private_terminal_required', 403)
    # Operator explicitly invokes this once in their private terminal. Never log it.
    print('One-time setup code (15 minutes, 5 failed attempts; not a password):')
    print(issue_code(store))
