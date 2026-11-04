from __future__ import annotations

import copy
import json
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .domain import CommittedProblem, Problem, ident, normalize_name, require, token_hash
from .retention import DEFAULT_RETENTION
from .bounds import room_bounds, snapshot_bounds
from .pool import ConnectionPool
from .setup import SetupStore


def _validate_connection_environment(dsn):
    """Recheck libpq inputs immediately before opening a new connection.

    This is not atomic with libpq reading the environment; the process and its
    environment must be trusted not to mutate concurrently.
    """
    from psycopg.conninfo import conninfo_to_dict
    from .config import validate_libpq_environment
    fields = conninfo_to_dict(dsn)
    loopback = {'localhost': '127.0.0.1', '127.0.0.1': '127.0.0.1', '::1': '::1'}
    host = fields.get('host')
    # Raw DSNs are accepted by the store too; only a matching explicit address
    # prevents ambient PGHOSTADDR from redirecting a nominal loopback host.
    validate_libpq_environment(container=host not in loopback or fields.get('hostaddr') != loopback[host])


class MemoryStore(SetupStore):
    """Contract-test store only; not a PostgreSQL concurrency substitute."""
    def __init__(self):
        self.principals, self.credentials, self.rooms, self.cursors = {}, {}, {}, {}
        self.passwords, self.sessions = {}, {}
        # Separate from room JSON, and transaction-local until commit.
        self.memory_records, self.memory_operations, self.memory_revoke_indexes = {}, {}, {}
        self.lock = threading.RLock()
        self.signal = threading.Event()

    def ready(self):
        # A contract fixture must never advertise production PostgreSQL readiness.
        return False

    def provision(self, principal, fixture_token, scopes):
        name, name_key, handle = normalize_name(principal["display_name"], principal["handle"])
        with self.lock:
            require(not any(p["owner"] == principal["owner"] and (p["name_key"] == name_key or p["handle"] == handle)
                            for p in self.principals.values()), "identity_conflict")
            self.principals[principal["id"]] = {**principal, "display_name": name, "name_key": name_key, "identity_revision": 1}
            self.credentials[token_hash(fixture_token)] = {"id": principal["id"], "scopes": sorted(scopes), "expires_at": time.time() + 3600, "revoked": False}

    def auth(self, token):
        with self.lock:
            if token.startswith("session:"):
                session = self.sessions.get(token_hash(token[8:]))
                require(session and session["expires_at"] > time.time(), "unauthorized", 401)
                principal = self.principals[session["principal_id"]]
                return copy.deepcopy({**principal, "scopes": principal["account_scopes"]})
            c = self.credentials.get(token_hash(token))
            require(c and not c["revoked"] and c["expires_at"] > time.time(), "unauthorized", 401)
            return copy.deepcopy({**self.principals[c["id"]], **c})

    def bootstrap(self, principal, record):
        with self.lock:
            require(not getattr(self, 'setup_state', {}), 'setup_managed_use_gui', 409)
            require(not any(p["owner"] == principal["owner"] and p["kind"] == "human" for p in self.principals.values()), "bootstrap_exists")
            self.principals[principal["id"]] = copy.deepcopy(principal)
            self.passwords[principal["id"]] = copy.deepcopy(record)

    def password_lookup(self, workspace, username):
        with self.lock:
            p = next((p for p in self.principals.values() if p["owner"] == workspace and p["handle"] == username and p["kind"] == "human"), None)
            return {"principal_id": p["id"], "record": self.passwords[p["id"]]} if p and p["id"] in self.passwords else None

    def save_session(self, hashed, record):
        with self.lock:
            self.sessions[hashed] = copy.deepcopy(record)

    def session_record(self, hashed):
        with self.lock:
            return copy.deepcopy(self.sessions.get(hashed))

    def rotate_csrf(self, hashed, csrf_hash):
        with self.lock:
            require(hashed in self.sessions, "unauthorized", 401)
            self.sessions[hashed]["csrf_hash"] = csrf_hash

    def logout(self, hashed):
        with self.lock:
            if hashed in self.sessions:
                self.sessions[hashed]["expires_at"] = 0
        self.signal.set()

    @contextmanager
    def identity_transaction(self, token):
        with self.lock:
            actor = self.auth(token)
            principals, credentials = copy.deepcopy(self.principals), copy.deepcopy(self.credentials)
            yield actor, principals, credentials
            require(len(principals) <= 128 and len(credentials) <= 512, "identity_capacity", 429)
            self.principals, self.credentials = principals, credentials
        self.signal.set()

    def revoke(self, token):
        with self.lock:
            self.credentials[token_hash(token)]["revoked"] = True
        self.signal.set()

    def credential_active(self, binding, principal_id, room_id=None, required_scopes=()):
        """Internal receipt validation; never expose token hashes through API payloads."""
        with self.lock:
            credential = self.credentials.get(binding)
            return bool(credential and credential["id"] == principal_id and not credential["revoked"]
                        and credential["expires_at"] > time.time()
                        and set(required_scopes) <= set(credential["scopes"])
                        and credential.get("room_id") in {None, room_id})

    def credentials_active(self, bindings):
        """Batch form of :meth:`credential_active`.

        A manual room start checks every candidate worker at once; calling the
        single form in a loop took one lock/round trip per candidate.
        """
        wanted = {binding for binding in bindings if binding}
        with self.lock:
            result = {}
            for binding in wanted:
                credential = self.credentials.get(binding)
                if credential:
                    state = copy.deepcopy(credential)
                    state["principal_id"] = state["id"]
                    # Normalize the optional scope so batch and single checks read the
                    # same keys regardless of how the credential was provisioned.
                    state["room_id"] = state.get("room_id")
                    result[binding] = state
            return result

    def credential_metadata(self, token, agent_id):
        with self.lock:
            actor = self.auth(token)
            require(actor['kind'] == 'human' and 'credential:manage' in actor['scopes'], 'forbidden', 403)
            principal = self.principals.get(agent_id)
            require(principal and principal['owner'] == actor['owner'] and principal['kind'] == 'agent', 'not_found', 404)
            return [{key: copy.deepcopy(c.get(key)) for key in ('credential_id', 'room_id', 'scopes', 'expires_at', 'revoked')}
                    for c in self.credentials.values() if c['id'] == agent_id and c.get('credential_id')]

    @contextmanager
    def transaction(self, room_id, token, include_credential_reader=False):
        pending = None
        with self.lock:
            actor = self.auth(token)
            require(room_id in self.rooms, "not_found", 404)
            room = copy.deepcopy(self.rooms[room_id])
            try:
                yield (actor, room, self.credentials_active) if include_credential_reader else (actor, room)
            except CommittedProblem as error:
                # State-first rejection: persist the recorded mutation, then report it.
                pending = error
            room_bounds(room)
            self.rooms[room_id] = room
        self.signal.set()
        if pending is not None:
            raise pending

    @contextmanager
    def invitation_transaction(self, room_id, token):
        """Serialize the room, identity view and credential checks together."""
        with self.lock:
            actor = self.auth(token)
            require(room_id in self.rooms, 'not_found', 404)
            room = copy.deepcopy(self.rooms[room_id])
            old_room = copy.deepcopy(room)
            require(actor.get('owner') == room.get('owner'), 'not_found', 404)
            principals = copy.deepcopy({pid: p for pid, p in self.principals.items()
                                        if p.get('owner') == actor['owner']})
            def issuer(binding):
                item = self.credentials.get(binding)
                return copy.deepcopy(item) if item else None
            checks = []
            yield actor, room, principals, issuer, checks
            for check in checks:
                check()
            latest = self.auth(token)
            require(latest['id'] == actor['id'] and latest.get('owner') == actor.get('owner')
                    and latest.get('room_id') == actor.get('room_id')
                    and set(latest['scopes']) == set(actor['scopes']), 'unauthorized', 401)
            room_bounds(room)
            changed = room != old_room
            if changed:
                self.rooms[room_id] = room
        if changed:
            self.signal.set()

    @contextmanager
    def memory_transaction(self, room_id, token, *, synthetic_fixture=False):
        """Explicit synthetic-only opt-in; this caller claim cannot identify real data.

        Not a public API or supported for real data. Results are provisional until
        the context exits successfully; callers must not externally expose an ACK
        before commit. Revocation audit is separate from room events; no room event
        is emitted.
        """
        require(synthetic_fixture is True, 'synthetic_fixture_required', 403)
        from .memory_repository import MemoryRepository
        with self.lock:
            actor = self.auth(token)
            require(room_id in self.rooms, 'not_found', 404)
            room = copy.deepcopy(self.rooms[room_id])
            records = copy.deepcopy(self.memory_records.get(room_id, {}))
            operations = copy.deepcopy(self.memory_operations.get(room_id, {}))
            indexes = copy.deepcopy(self.memory_revoke_indexes.get(room_id, {}))
            original = copy.deepcopy((room, records, operations, indexes))
            def reauth():
                try:
                    return self.auth(token)
                except KeyError:
                    raise Problem('unauthorized', 401) from None
            repo = MemoryRepository(actor, room, records, operations, indexes, reauth)
            changed = False
            try:
                repo._member()
                yield actor, room, repo
                repo._member()
                repo.validate()
                room_bounds(room)
                changed = (room, records, operations, indexes) != original
                repo._member()  # Last check before the in-memory commit.
                if changed:
                    self.rooms[room_id] = room
                    self.memory_records[room_id] = records
                    self.memory_operations[room_id] = operations
                    self.memory_revoke_indexes[room_id] = indexes
            finally:
                repo.close()
        if changed:
            self.signal.set()

    @contextmanager
    def read_transaction(self, room_id, token, include_events=True):
        with self.lock:
            actor=self.auth(token)
            require(room_id in self.rooms,'not_found',404)
            room = self.rooms[room_id]
            projection = room if include_events else {k: v for k, v in room.items() if k != 'events'}
            yield actor,copy.deepcopy(projection)

    def create(self, token, room):
        with self.lock:
            actor = self.auth(token)
            require("rooms:create" in actor["scopes"] and not actor.get("room_id"), "forbidden", 403)
            if room["id"] in self.rooms:
                existing = self.rooms[room["id"]]
                require(existing["create_digest"] == room["create_digest"], "idempotency_conflict")
                return copy.deepcopy(existing)
            require(set(room["members"][actor["id"]]["permissions"]) <= set(actor["scopes"]), "creator_scope_insufficient", 403)
            require(sum(r["owner"] == actor["owner"] and r["state"] != "closed" for r in self.rooms.values()) < 4, "room_quota", 429)
            require(sum(r["owner"] == actor["owner"] for r in self.rooms.values()) < 64, "room_list_capacity_paging_unsupported", 429)
            room_bounds(room)
            self.rooms[room["id"]] = copy.deepcopy(room)
        self.signal.set()
        return copy.deepcopy(room)

    def snapshot(self, token, include_events=True):
        with self.lock:
            actor = self.auth(token)
            rooms = {rid: (r if include_events else {k: v for k, v in r.items() if k != "events"})
                     for rid, r in self.rooms.items() if r["owner"] == actor["owner"]}
            principals = {pid: p for pid, p in self.principals.items() if p["owner"] == actor["owner"]}
            snapshot_bounds(rooms, principals, deep=include_events)
            return actor, copy.deepcopy(rooms), copy.deepcopy(principals)

    def room_events(self, room_id, after_seq, limit=16):
        with self.lock:
            room = self.rooms.get(room_id)
            if not room:
                return []
            return [copy.deepcopy(event) for event in room["events"] if event["seq"] > after_seq][:limit]

    def cursor(self, cursor_id):
        with self.lock:
            saved = self.cursors.get(cursor_id)
            return copy.deepcopy(saved) if saved and saved.get("expires_at", 0) > time.time() else None

    def save_cursor(self, cursor_id, payload):
        with self.lock:
            principal, now = payload["principal"], time.time()
            total = sum(c["principal"] == principal for c in self.cursors.values())
            if total >= 4096:
                # Expired rows must not lock a principal out permanently. Count the live
                # rows first and only when they are what frees the slot purge this
                # principal's expired rows: a principal already holding 4096 live
                # cursors is a real cap violation and keeps its expiry sweep. Other
                # principals are never touched, and nothing is removed unless the
                # save can then succeed, so a rejected save leaves the store unchanged
                # exactly like the PostgreSQL rollback.
                live = sum(c["principal"] == principal and c.get("expires_at", 0) > now
                           for c in self.cursors.values())
                if live < 4096:
                    for key in [key for key, c in self.cursors.items()
                                if c["principal"] == principal and c.get("expires_at", 0) <= now]:
                        del self.cursors[key]
                    total = sum(c["principal"] == principal for c in self.cursors.values())
            require(total < 4096, "cursor_capacity_archival_unsupported", 429)
            self.cursors[cursor_id] = {**copy.deepcopy(payload), "expires_at": now + DEFAULT_RETENTION.cursor_seconds}

    def tick_all(self):
        from .domain import schedule, schedule_call
        changed = False
        with self.lock:
            for room in self.rooms.values():
                if schedule_call(room, self.principals):
                    changed = True
        # Only wake SSE readers when the tick actually changed a room; an idle
        # scheduler must not turn into a broadcast heartbeat.
        if changed:
            self.signal.set()
        return changed

    @contextmanager
    def listener(self):
        def wait(timeout):
            self.signal.wait(timeout)
            self.signal.clear()
        yield wait
        self.signal.clear()


class PostgresStore(SetupStore):
    @contextmanager
    def read_transaction(self, room_id, token, include_events=True):
        with self.connect() as conn:
            row=conn.execute('SELECT * FROM rooms WHERE id=%s FOR SHARE',(room_id,)).fetchone()
            require(row,'not_found',404)
            yield self._auth(conn,token),self._room(conn,row,include_events=include_events)

    def credential_active(self, binding, principal_id, room_id=None, required_scopes=()):
        with self.connect() as conn:
            credential = conn.execute("SELECT principal_id,expires_at,revoked,room_id,scopes FROM credentials WHERE token_hash=%s", (binding,)).fetchone()
            return bool(credential and credential["principal_id"] == principal_id and not credential["revoked"]
                        and credential["expires_at"] > time.time()
                        and set(required_scopes) <= set(credential["scopes"])
                        and credential["room_id"] in {None, room_id})

    def credentials_active(self, bindings, conn=None):
        """Read credential rows in one query, optionally on the caller's transaction."""
        wanted = sorted({binding for binding in bindings if binding})
        if not wanted:
            return {}
        if conn is None:
            with self.connect() as connection:
                return self.credentials_active(wanted, conn=connection)
        rows = conn.execute("SELECT token_hash,principal_id,expires_at,revoked,room_id,scopes "
                            "FROM credentials WHERE token_hash = ANY(%s)", (wanted,)).fetchall()
        return {row["token_hash"]: row for row in rows}

    def credential_metadata(self, token, agent_id):
        with self.connect() as conn:
            actor = self._auth(conn, token)
            require(actor['kind'] == 'human' and 'credential:manage' in actor['scopes'], 'forbidden', 403)
            require(conn.execute("SELECT id FROM principals WHERE id=%s AND owner=%s AND payload->>'kind'='agent'", (agent_id, actor['owner'])).fetchone(), 'not_found', 404)
            return conn.execute('SELECT credential_id,room_id,scopes,expires_at,revoked FROM credentials WHERE principal_id=%s AND credential_id IS NOT NULL', (agent_id,)).fetchall()

    def __init__(self, dsn, schema=None):
        require(schema is None or re.fullmatch(r"talkabout_test_[a-f0-9]{32}", schema), "invalid_test_schema", 422)
        self.dsn, self.schema = dsn, schema
        # Connections are expensive to establish (TCP + SCRAM) and a single request
        # used to open several; reuse them through a bounded process-local pool.
        self._pool = None
        self._pool_lock = threading.Lock()
        self._ticks = 0

    def ready(self):
        try:
            with self.connect() as conn:
                # Read-only schema probes catch absent/incompatible tables as well as DB outage.
                for query in (
                    "SELECT id,owner,payload FROM principals LIMIT 0",
                    "SELECT token_hash,credential_id,room_id,expires_at,revoked FROM credentials LIMIT 0",
                    "SELECT id,last_event_seq,payload FROM rooms LIMIT 0",
                    "SELECT room_id,seq,event_id,payload FROM events LIMIT 0",
                    "SELECT id,principal_id,expires_at FROM cursors LIMIT 0",
                    "SELECT principal_id,record FROM human_passwords LIMIT 0",
                    "SELECT token_hash,principal_id,expires_at,csrf_hash FROM human_sessions LIMIT 0",
                ):
                    conn.execute(query)
                if not conn.execute("SELECT id FROM principals WHERE payload->>'kind'='human' LIMIT 1").fetchone():
                    conn.execute('SELECT id,payload FROM installation_setup LIMIT 0')
            return True
        except Exception:
            return False

    def compact_fixture(self, *, synthetic_fixture=False, now=None):
        """Explicit test-schema cleanup only; never a production maintenance API."""
        require(synthetic_fixture is True and self.schema is not None, "synthetic_fixture_required", 403)
        from .retention import compact_room_fixture, retire_responses
        from psycopg.types.json import Jsonb
        now = time.time() if now is None else now
        counts = {"events": 0, "response_payloads": 0, "cursors": 0}
        with self.connect() as conn:
            for row in conn.execute("SELECT id,payload FROM rooms ORDER BY id FOR UPDATE").fetchall():
                room = row["payload"]
                room["events"] = [r["payload"] for r in conn.execute("SELECT payload FROM events WHERE room_id=%s ORDER BY seq", (row["id"],)).fetchall()]
                result = compact_room_fixture(room, synthetic_fixture=True, now=now)
                for key, value in result.items():
                    counts[key] += value
                floor = room.get("first_retained_seq", 1)
                room.pop("events")
                conn.execute("UPDATE rooms SET payload=%s WHERE id=%s", (Jsonb(room), row["id"]))
                conn.execute("DELETE FROM events WHERE room_id=%s AND seq<%s", (row["id"], floor))
            for row in conn.execute("SELECT id,payload FROM principals ORDER BY id FOR UPDATE").fetchall():
                principal = row["payload"]
                counts["response_payloads"] += retire_responses(principal.get("identity_operations", {}), now)
                conn.execute("UPDATE principals SET payload=%s WHERE id=%s", (Jsonb(principal), row["id"]))
            counts["cursors"] = conn.execute("DELETE FROM cursors WHERE expires_at<=%s", (now,)).rowcount
        return counts

    def _connection_options(self):
        return {"options": "-csearch_path=" + self.schema} if self.schema else {}

    def _connection_pool(self):
        if self._pool is None:
            with self._pool_lock:
                if self._pool is None:
                    def factory():
                        import psycopg
                        from psycopg.rows import dict_row
                        _validate_connection_environment(self.dsn)
                        return psycopg.connect(self.dsn, row_factory=dict_row, **self._connection_options())
                    self._pool = ConnectionPool.from_environment(factory)
        return self._pool

    @contextmanager
    def connect(self, autocommit=False, pooled=True):
        if not pooled:
            # Session-scoped state (LISTEN, session advisory locks) must never be
            # handed to another caller, so the listener path keeps its own socket.
            import psycopg
            from psycopg.rows import dict_row
            _validate_connection_environment(self.dsn)
            with psycopg.connect(self.dsn, row_factory=dict_row, autocommit=autocommit, **self._connection_options()) as conn:
                yield conn
            return
        with self._connection_pool().connection(autocommit=autocommit) as conn:
            yield conn

    def close(self):
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.close()

    def pool_stats(self):
        pool = self._pool
        return pool.stats() if pool is not None else {"created": 0, "reused": 0, "discarded": 0,
                                                      "waited": 0, "free": 0, "checked_out": 0,
                                                      "max_size": 0}

    def initialize(self):
        with self.connect() as conn:
            conn.execute(Path(__file__).with_name("schema.sql").read_text())

    @staticmethod
    def _auth(conn, token):
        if token.startswith("session:"):
            row = conn.execute("SELECT s.*,p.payload FROM human_sessions s JOIN principals p ON p.id=s.principal_id WHERE s.token_hash=%s FOR SHARE OF s", (token_hash(token[8:]),)).fetchone()
            require(row and row["expires_at"] > time.time(), "unauthorized", 401)
            return {**row["payload"], "scopes": row["payload"]["account_scopes"]}
        c = conn.execute("SELECT c.*,p.payload FROM credentials c JOIN principals p ON p.id=c.principal_id WHERE c.token_hash=%s FOR SHARE OF c", (token_hash(token),)).fetchone()
        require(c and not c["revoked"] and c["expires_at"] > time.time(), "unauthorized", 401)
        return {**c["payload"], "scopes": c["scopes"], "room_id": c["room_id"]}

    def auth(self, token):
        with self.connect() as conn:
            return self._auth(conn, token)

    def bootstrap(self, principal, record):
        from psycopg.types.json import Jsonb
        with self.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('talkabout:installation',0))")
            if conn.execute("SELECT to_regclass('installation_setup') AS name").fetchone()['name']:
                require(not conn.execute('SELECT id FROM installation_setup WHERE id=1').fetchone(), 'setup_managed_use_gui', 409)
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (principal["owner"] + ":identity",))
            require(not conn.execute("SELECT id FROM principals WHERE owner=%s AND payload->>'kind'='human'", (principal["owner"],)).fetchone(), "bootstrap_exists")
            conn.execute("INSERT INTO principals(id,owner,name_key,handle,payload) VALUES(%s,%s,%s,%s,%s)", (principal["id"], principal["owner"], principal["name_key"], principal["handle"], Jsonb(principal)))
            conn.execute("INSERT INTO human_passwords(principal_id,record) VALUES(%s,%s)", (principal["id"], Jsonb(record)))

    def password_lookup(self, workspace, username):
        with self.connect() as conn:
            return conn.execute("SELECT h.principal_id,h.record FROM human_passwords h JOIN principals p ON p.id=h.principal_id WHERE p.owner=%s AND p.handle=%s AND p.payload->>'kind'='human'", (workspace, username)).fetchone()

    def save_session(self, hashed, record):
        with self.connect() as conn:
            conn.execute("INSERT INTO human_sessions(token_hash,principal_id,expires_at,csrf_hash) VALUES(%s,%s,%s,%s)", (hashed, record["principal_id"], record["expires_at"], record["csrf_hash"]))

    def session_record(self, hashed):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM human_sessions WHERE token_hash=%s", (hashed,)).fetchone()

    def rotate_csrf(self, hashed, csrf_hash):
        with self.connect() as conn:
            conn.execute("UPDATE human_sessions SET csrf_hash=%s WHERE token_hash=%s AND expires_at>%s", (csrf_hash, hashed, time.time()))

    def logout(self, hashed):
        with self.connect() as conn:
            conn.execute("UPDATE human_sessions SET expires_at=0 WHERE token_hash=%s", (hashed,))
            conn.execute("NOTIFY talkabout_changed")

    @contextmanager
    def identity_transaction(self, token):
        from psycopg.types.json import Jsonb
        from psycopg.errors import UniqueViolation
        try:
            with self.connect() as conn:
                if token.startswith("session:"):
                    row = conn.execute("SELECT p.owner FROM human_sessions s JOIN principals p ON p.id=s.principal_id WHERE s.token_hash=%s", (token_hash(token[8:]),)).fetchone()
                else:
                    row = conn.execute("SELECT p.owner FROM credentials c JOIN principals p ON p.id=c.principal_id WHERE c.token_hash=%s", (token_hash(token),)).fetchone()
                require(row, "unauthorized", 401)
                # Serialize workspace identity mutations before acquiring credential locks.
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (row["owner"] + ":identity",))
                actor = self._auth(conn, token)
                principals = {r["id"]: r["payload"] for r in conn.execute("SELECT id,payload FROM principals WHERE owner=%s ORDER BY id LIMIT 129 FOR UPDATE", (actor["owner"],)).fetchall()}
                require(len(principals) <= 128, "principal_list_capacity_paging_unsupported", 429)
                rows = conn.execute("SELECT c.* FROM credentials c JOIN principals p ON p.id=c.principal_id WHERE p.owner=%s ORDER BY c.token_hash LIMIT 513 FOR UPDATE OF c", (actor["owner"],)).fetchall()
                require(len(rows) <= 512, "credential_capacity", 429)
                credentials = {r["token_hash"]: {"id": r["principal_id"], "credential_id": r["credential_id"] or ident(),
                               "scopes": r["scopes"], "expires_at": r["expires_at"], "revoked": r["revoked"], "room_id": r["room_id"]} for r in rows}
                yield actor, principals, credentials
                require(len(principals) <= 128 and len(credentials) <= 512, "identity_capacity", 429)
                for pid, principal in principals.items():
                    conn.execute("INSERT INTO principals(id,owner,name_key,handle,payload) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET name_key=excluded.name_key,handle=excluded.handle,payload=excluded.payload",
                                 (pid, principal["owner"], principal["name_key"], principal["handle"], Jsonb(principal)))
                for hashed, credential in credentials.items():
                    conn.execute("INSERT INTO credentials(token_hash,principal_id,credential_id,scopes,expires_at,revoked,room_id) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(token_hash) DO UPDATE SET credential_id=excluded.credential_id,scopes=excluded.scopes,expires_at=excluded.expires_at,revoked=excluded.revoked,room_id=excluded.room_id",
                                 (hashed, credential["id"], credential.get("credential_id") or ident(), Jsonb(credential["scopes"]), credential["expires_at"], credential["revoked"], credential.get("room_id")))
                conn.execute("NOTIFY talkabout_changed")
        except UniqueViolation:
            raise Problem("identity_conflict") from None

    def provision(self, principal, fixture_token, scopes):
        from psycopg.types.json import Jsonb
        name, name_key, handle = normalize_name(principal["display_name"], principal["handle"])
        payload = {**principal, "display_name": name, "name_key": name_key, "identity_revision": 1}
        with self.connect() as conn:
            conn.execute("INSERT INTO principals(id,owner,name_key,handle,payload) VALUES(%s,%s,%s,%s,%s)",
                         (principal["id"], principal["owner"], name_key, handle, Jsonb(payload)))
            conn.execute("INSERT INTO credentials(token_hash,principal_id,scopes,expires_at) VALUES(%s,%s,%s,%s)",
                         (token_hash(fixture_token), principal["id"], Jsonb(sorted(scopes)), time.time() + 3600))

    def revoke(self, token):
        with self.connect() as conn:
            conn.execute("UPDATE credentials SET revoked=true WHERE token_hash=%s", (token_hash(token),))
            conn.execute("NOTIFY talkabout_changed")

    @staticmethod
    def _room(conn, row, include_events=True):
        room = copy.deepcopy(row["payload"])
        room["last_seq"] = row["last_event_seq"]
        if include_events:
            # shortcut: bounded P0 rooms load their event history; switch to paged event queries before raising room limits.
            room["events"] = [r["payload"] for r in conn.execute("SELECT payload FROM events WHERE room_id=%s ORDER BY seq LIMIT 2049", (row["id"],)).fetchall()]
            room_bounds(room)
        else:
            # Read-only projections (snapshot, SSE polling) do not consume the event
            # history: every write already validated the room through _flush. Loading
            # up to 2,049 payloads per poll was pure overhead.
            room["events"] = []
        return room

    def room_events(self, room_id, after_seq, limit=16):
        """Return only the events a cursor still needs, instead of the whole history."""
        with self.connect() as conn:
            rows = conn.execute("SELECT payload FROM events WHERE room_id=%s AND seq>%s ORDER BY seq LIMIT %s",
                                (room_id, after_seq, max(1, int(limit)))).fetchall()
            return [row["payload"] for row in rows]

    @staticmethod
    def _flush(conn, room, prior_seq):
        from psycopg.types.json import Jsonb
        room_bounds(room)
        payload = {k: v for k, v in room.items() if k != "events"}
        conn.execute("UPDATE rooms SET payload=%s,last_event_seq=%s WHERE id=%s", (Jsonb(payload), room["last_seq"], room["id"]))
        for e in room["events"]:
            if e["seq"] > prior_seq:
                conn.execute("INSERT INTO events(room_id,seq,event_id,payload) VALUES(%s,%s,%s,%s)", (room["id"], e["seq"], e["event_id"], Jsonb(e)))
        conn.execute("NOTIFY talkabout_changed")

    @contextmanager
    def transaction(self, room_id, token, include_credential_reader=False):
        pending = None
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM rooms WHERE id=%s FOR UPDATE", (room_id,)).fetchone()
            require(row, "not_found", 404)
            actor = self._auth(conn, token)
            room = self._room(conn, row)
            reader = lambda bindings: self.credentials_active(bindings, conn=conn)
            try:
                yield (actor, room, reader) if include_credential_reader else (actor, room)
            except CommittedProblem as error:
                # State-first rejection: flush before surfacing the problem.
                pending = error
            self._flush(conn, room, row["last_event_seq"])
        if pending is not None:
            raise pending

    @contextmanager
    def invitation_transaction(self, room_id, token):
        """Lock identity before room; all checks share the same connection."""
        with self.connect() as conn:
            if token.startswith('session:'):
                candidate = conn.execute('SELECT p.owner FROM human_sessions s JOIN principals p ON p.id=s.principal_id WHERE s.token_hash=%s', (token_hash(token[8:]),)).fetchone()
            else:
                candidate = conn.execute('SELECT p.owner FROM credentials c JOIN principals p ON p.id=c.principal_id WHERE c.token_hash=%s', (token_hash(token),)).fetchone()
            require(candidate, 'unauthorized', 401)
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (candidate['owner'] + ':identity',))
            row = conn.execute('SELECT * FROM rooms WHERE id=%s FOR UPDATE', (room_id,)).fetchone()
            require(row, 'not_found', 404)
            actor = self._auth(conn, token)
            room = self._room(conn, row)
            old_room = copy.deepcopy(room)
            require(actor.get('owner') == candidate['owner'] == room.get('owner'), 'not_found', 404)
            rows = conn.execute('SELECT id,payload FROM principals WHERE owner=%s ORDER BY id LIMIT 129 FOR SHARE', (candidate['owner'],)).fetchall()
            require(len(rows) <= 128, 'principal_list_capacity_paging_unsupported', 429)
            principals = {item['id']: item['payload'] for item in rows}
            require(actor['id'] in principals, 'unauthorized', 401)
            def issuer(binding):
                credential = conn.execute('SELECT principal_id,scopes,expires_at,room_id,revoked FROM credentials WHERE token_hash=%s FOR SHARE', (binding,)).fetchone()
                return ({**credential, 'id': credential['principal_id']} if credential else None)
            checks = []
            yield actor, room, principals, issuer, checks
            def revalidate():
                current = self._auth(conn, token)
                require(current['id'] == actor['id'] and current.get('owner') == actor.get('owner')
                        and current.get('room_id') == actor.get('room_id')
                        and set(current['scopes']) == set(actor['scopes']), 'unauthorized', 401)
                for check in checks:
                    check()
            revalidate()
            if room != old_room:
                self._flush(conn, room, row['last_event_seq'])
            revalidate()

    @contextmanager
    def memory_transaction(self, room_id, token, *, synthetic_fixture=False):
        """Experimental fixture-only; not public API or supported for real data.

        Explicit opt-in and a validated test schema are mandatory, but these alone
        do not prove a database is ephemeral. Results are provisional until the
        context exits successfully and must not be exposed externally before commit.
        Approval may emit a room event; revocation/forget use a reserved audit slot without a room event. No provider
        calls, DDL or implicit migration. Retention and operator deletion are unresolved.
        """
        require(synthetic_fixture is True, 'synthetic_fixture_required', 403)
        require(isinstance(self.schema, str) and re.fullmatch(r'talkabout_test_[a-f0-9]{32}', self.schema),
                'synthetic_fixture_schema_required', 403)
        from psycopg.errors import UndefinedTable, InsufficientPrivilege, UniqueViolation
        from psycopg.types.json import Jsonb
        from .memory_repository import MemoryRepository, MAX_TOTAL, MAX_OPERATIONS, MAX_FORGET_OPERATIONS
        with self.connect() as conn:
            row = conn.execute('SELECT * FROM rooms WHERE id=%s FOR UPDATE', (room_id,)).fetchone()
            require(row, 'not_found', 404)
            # identity_transaction takes principal locks before credential writes.
            # Discover the id without trusting it, then lock the principal first;
            # _auth subsequently locks and revalidates the session/credential itself.
            if token.startswith('session:'):
                binding = conn.execute('SELECT principal_id FROM human_sessions WHERE token_hash=%s',
                                       (token_hash(token[8:]),)).fetchone()
            else:
                binding = conn.execute('SELECT principal_id FROM credentials WHERE token_hash=%s',
                                       (token_hash(token),)).fetchone()
            require(binding, 'unauthorized', 401)
            principal = conn.execute('SELECT id,payload FROM principals WHERE id=%s FOR SHARE',
                                     (binding['principal_id'],)).fetchone()
            require(principal, 'unauthorized', 401)
            actor = self._auth(conn, token)
            require(actor.get('id') == principal['id'], 'unauthorized', 401)
            if token.startswith('session:'):
                actor = {**principal['payload'], 'scopes': principal['payload']['account_scopes']}
            else:
                actor = {**principal['payload'], 'scopes': actor['scopes'], 'room_id': actor.get('room_id')}
            room = self._room(conn, row)
            # Deny before probing the optional tables, avoiding a schema-existence
            # side channel to callers without human room access.
            from .domain import authorize
            require(actor.get('owner') == room.get('owner'), 'not_found', 404)
            authorize(actor, room, 'room:read', human=True)
            authorize(actor, room, 'room:control', human=True)
            try:
                saved = conn.execute('SELECT id,payload FROM room_memories WHERE room_id=%s LIMIT %s',
                                     (room_id, MAX_TOTAL + 1)).fetchall()
                markers = conn.execute('SELECT actor_id,operation,key,payload FROM room_memory_operations WHERE room_id=%s LIMIT %s',
                                       (room_id, MAX_OPERATIONS + MAX_FORGET_OPERATIONS + 1)).fetchall()
                index_rows = conn.execute('SELECT memory_id,payload FROM room_memory_revoke_index WHERE room_id=%s LIMIT %s',
                                          (room_id, MAX_TOTAL + 1)).fetchall()
            except (UndefinedTable, InsufficientPrivilege):
                raise Problem('memory_storage_unavailable', 503) from None
            require(len(saved) <= MAX_TOTAL and len(index_rows) <= MAX_TOTAL
                    and sum(r['operation'] != 'forget-own' for r in markers) <= MAX_OPERATIONS
                    and sum(r['operation'] == 'forget-own' for r in markers) <= MAX_FORGET_OPERATIONS,
                    'memory_capacity', 429)
            records = {r['id']: r['payload'] for r in saved}
            operations = {(r['actor_id'], r['operation'], r['key']): r['payload'] for r in markers}
            indexes = {r['memory_id']: r['payload'] for r in index_rows}
            old_records, old_operations, old_room, old_indexes = copy.deepcopy(records), copy.deepcopy(operations), copy.deepcopy(room), copy.deepcopy(indexes)
            # Reauthenticate on the already locked connection, never a second pool
            # checkout. Principal then session/credential lock order stays unchanged.
            repo = MemoryRepository(actor, room, records, operations, indexes, lambda: self._auth(conn, token))
            try:
                repo._member()
                yield actor, room, repo
                repo._member()
                repo.validate()
                room_bounds(room)
                for mid, record in records.items():
                    if mid in repo._created_memory_ids or old_records.get(mid) != record:
                        if mid in repo._created_memory_ids or mid not in old_records:
                            try:
                                conn.execute('INSERT INTO room_memories(room_id,id,approved_seq,payload) VALUES(%s,%s,%s,%s)',
                                             (room_id, mid, record['approved_seq'], Jsonb(record)))
                            except UniqueViolation:
                                raise Problem('memory_id_collision', 409) from None
                        else:
                            changed = conn.execute('UPDATE room_memories SET approved_seq=%s,payload=%s WHERE room_id=%s AND id=%s',
                                                   (record['approved_seq'], Jsonb(record), room_id, mid))
                            require(changed.rowcount == 1, 'memory_storage_inconsistent', 503)
                for (pid, operation, key), marker in operations.items():
                    operation_key = (pid, operation, key)
                    if operation_key in repo._created_operation_keys or old_operations.get(operation_key) != marker:
                        if operation_key in repo._created_operation_keys or operation_key not in old_operations:
                            try:
                                conn.execute('INSERT INTO room_memory_operations(room_id,actor_id,operation,key,payload) VALUES(%s,%s,%s,%s,%s)',
                                             (room_id, pid, operation, key, Jsonb(marker)))
                            except UniqueViolation:
                                raise Problem('memory_operation_conflict', 409) from None
                        else:
                            changed = conn.execute('UPDATE room_memory_operations SET payload=%s WHERE room_id=%s AND actor_id=%s AND operation=%s AND key=%s',
                                                   (Jsonb(marker), room_id, pid, operation, key))
                            require(changed.rowcount == 1, 'memory_storage_inconsistent', 503)
                for mid, index in indexes.items():
                    if mid in repo._created_index_ids or old_indexes.get(mid) != index:
                        if mid in repo._created_index_ids or mid not in old_indexes:
                            try:
                                conn.execute('INSERT INTO room_memory_revoke_index(room_id,memory_id,payload) VALUES(%s,%s,%s)',
                                             (room_id, mid, Jsonb(index)))
                            except UniqueViolation:
                                raise Problem('memory_index_conflict', 409) from None
                        else:
                            changed = conn.execute('UPDATE room_memory_revoke_index SET payload=%s WHERE room_id=%s AND memory_id=%s',
                                                   (Jsonb(index), room_id, mid))
                            require(changed.rowcount == 1, 'memory_storage_inconsistent', 503)
                if room != old_room:
                    self._flush(conn, room, row['last_event_seq'])
                # Final check after all writes; any failure rolls back the transaction.
                # The actual COMMIT can still happen later as the connection exits.
                repo._member()
            finally:
                repo.close()

    def create(self, token, room):
        from psycopg.types.json import Jsonb
        with self.connect() as conn:
            conn.execute("INSERT INTO rooms(id,owner,payload) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING", (room["id"], room["owner"], Jsonb({})))
            row = conn.execute("SELECT * FROM rooms WHERE id=%s FOR UPDATE", (room["id"],)).fetchone()
            actor = self._auth(conn, token)
            require("rooms:create" in actor["scopes"] and not actor.get("room_id"), "forbidden", 403)
            if row["payload"]:
                existing = self._room(conn, row)
                require(existing["create_digest"] == room["create_digest"], "idempotency_conflict")
                return existing
            require(set(room["members"][actor["id"]]["permissions"]) <= set(actor["scopes"]), "creator_scope_insufficient", 403)
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (actor["owner"],))
            count = conn.execute("SELECT count(*) AS n FROM rooms WHERE owner=%s AND payload->>'state' != 'closed'", (actor["owner"],)).fetchone()["n"]
            require(count < 4, "room_quota", 429)
            require(conn.execute("SELECT count(*) AS n FROM rooms WHERE owner=%s", (actor["owner"],)).fetchone()["n"] <= 64, "room_list_capacity_paging_unsupported", 429)
            self._flush(conn, room, 0)
            return room

    def snapshot(self, token, include_events=True):
        with self.connect() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            actor = self._auth(conn, token)
            rows = conn.execute("SELECT * FROM rooms WHERE owner=%s ORDER BY id LIMIT 65", (actor["owner"],)).fetchall()
            require(len(rows) <= 64, "room_list_capacity_paging_unsupported", 429)
            rooms = {r["id"]: self._room(conn, r, include_events=include_events) for r in rows}
            principals = {r["id"]: r["payload"] for r in conn.execute("SELECT id,payload FROM principals WHERE owner=%s ORDER BY id LIMIT 129", (actor["owner"],)).fetchall()}
            snapshot_bounds(rooms, principals, deep=include_events)
            return actor, rooms, principals

    def cursor(self, cursor_id):
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM cursors WHERE id=%s AND expires_at>%s", (cursor_id, time.time())).fetchone()
            return row["payload"] if row else None

    def save_cursor(self, cursor_id, payload):
        from psycopg.types.json import Jsonb
        principal = payload["principal"]
        with self.connect() as conn:
            # The transaction-scoped advisory lock serializes this principal's saves, so
            # the live count, the bounded purge and the insert below are atomic and the
            # purge can never race another save for the same principal.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (principal + ":cursors",))
            now = time.time()
            total = conn.execute("SELECT count(*) AS n FROM cursors WHERE principal_id=%s", (principal,)).fetchone()["n"]
            if total >= 4096:
                # Only expired rows of this principal may be reclaimed, and only while
                # the live count leaves room: 4096 live cursors stays a hard 429. If the
                # save is still rejected the transaction rolls back the purge, matching
                # the in-memory store which never removes rows for a rejected save.
                live = conn.execute("SELECT count(*) AS n FROM cursors WHERE principal_id=%s AND expires_at>%s",
                                    (principal, now)).fetchone()["n"]
                if live < 4096:
                    conn.execute("DELETE FROM cursors WHERE principal_id=%s AND expires_at<=%s", (principal, now))
                    total = conn.execute("SELECT count(*) AS n FROM cursors WHERE principal_id=%s", (principal,)).fetchone()["n"]
            require(total < 4096, "cursor_capacity_archival_unsupported", 429)
            conn.execute("INSERT INTO cursors(id,principal_id,payload,expires_at) VALUES(%s,%s,%s,%s)", (cursor_id, principal, Jsonb(payload), now + DEFAULT_RETENTION.cursor_seconds))

    def tick_all(self):
        from .domain import schedule, schedule_call
        self._ticks += 1
        changed = False
        with self.connect() as conn:
            ids = [r["id"] for r in conn.execute("SELECT id FROM rooms WHERE payload->>'state'='active' ORDER BY id LIMIT 65").fetchall()]
            require(len(ids) <= 64, "scheduler_capacity", 429)
        for rid in ids:
            with self.connect() as conn:
                row = conn.execute("SELECT * FROM rooms WHERE id=%s FOR UPDATE SKIP LOCKED", (rid,)).fetchone()
                if not row:
                    continue
                principals = {p["id"]: p["payload"] for p in conn.execute("SELECT id,payload FROM principals WHERE owner=%s ORDER BY id LIMIT 129", (row["owner"],)).fetchall()}
                require(len(principals) <= 128, "principal_list_capacity_paging_unsupported", 429)
                room = self._room(conn, row)
                # An unchanged room must not be rewritten or broadcast every tick:
                # that alone woke every SSE reader in the workspace twice a second.
                if schedule_call(room, principals):
                    self._flush(conn, room, row["last_event_seq"])
                    changed = True
        if self._ticks % 120 == 0:
            # Cursor rows are created per SSE batch; expire them on a slow cadence
            # instead of scanning an ever-growing table.
            with self.connect() as conn:
                conn.execute("DELETE FROM cursors WHERE expires_at<=%s", (time.time(),))
        return changed

    @contextmanager
    def listener(self):
        with self.connect(autocommit=True, pooled=False) as conn:
            conn.execute("LISTEN talkabout_changed")
            def wait(timeout):
                for _ in conn.notifies(timeout=timeout, stop_after=1):
                    break
            yield wait
