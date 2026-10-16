from __future__ import annotations

import copy
import json
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .domain import Problem, ident, normalize_name, require, token_hash
from .retention import DEFAULT_RETENTION
from .bounds import room_bounds, snapshot_bounds
from .setup import SetupStore


class MemoryStore(SetupStore):
    """Contract-test store only; not a PostgreSQL concurrency substitute."""
    def __init__(self):
        self.principals, self.credentials, self.rooms, self.cursors = {}, {}, {}, {}
        self.passwords, self.sessions = {}, {}
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

    def credential_metadata(self, token, agent_id):
        with self.lock:
            actor = self.auth(token)
            require(actor['kind'] == 'human' and 'credential:manage' in actor['scopes'], 'forbidden', 403)
            principal = self.principals.get(agent_id)
            require(principal and principal['owner'] == actor['owner'] and principal['kind'] == 'agent', 'not_found', 404)
            return [{key: copy.deepcopy(c.get(key)) for key in ('credential_id', 'room_id', 'scopes', 'expires_at', 'revoked')}
                    for c in self.credentials.values() if c['id'] == agent_id and c.get('credential_id')]

    @contextmanager
    def transaction(self, room_id, token):
        with self.lock:
            actor = self.auth(token)
            require(room_id in self.rooms, "not_found", 404)
            room = copy.deepcopy(self.rooms[room_id])
            yield actor, room
            room_bounds(room)
            self.rooms[room_id] = room
        self.signal.set()

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

    def snapshot(self, token):
        with self.lock:
            actor = self.auth(token)
            rooms = {rid: r for rid, r in self.rooms.items() if r["owner"] == actor["owner"]}
            principals = {pid: p for pid, p in self.principals.items() if p["owner"] == actor["owner"]}
            snapshot_bounds(rooms, principals)
            return actor, copy.deepcopy(rooms), copy.deepcopy(principals)

    def cursor(self, cursor_id):
        with self.lock:
            saved = self.cursors.get(cursor_id)
            return copy.deepcopy(saved) if saved and saved.get("expires_at", 0) > time.time() else None

    def save_cursor(self, cursor_id, payload):
        with self.lock:
            require(sum(c["principal"] == payload["principal"] for c in self.cursors.values()) < 4096, "cursor_capacity_archival_unsupported", 429)
            self.cursors[cursor_id] = {**copy.deepcopy(payload), "expires_at": time.time() + DEFAULT_RETENTION.cursor_seconds}

    def tick_all(self):
        from .domain import schedule
        with self.lock:
            for room in self.rooms.values():
                schedule(room, self.principals)
        self.signal.set()

    @contextmanager
    def listener(self):
        def wait(timeout):
            self.signal.wait(timeout)
            self.signal.clear()
        yield wait
        self.signal.clear()


class PostgresStore(SetupStore):
    def credential_active(self, binding, principal_id, room_id=None, required_scopes=()):
        with self.connect() as conn:
            credential = conn.execute("SELECT principal_id,expires_at,revoked,room_id,scopes FROM credentials WHERE token_hash=%s", (binding,)).fetchone()
            return bool(credential and credential["principal_id"] == principal_id and not credential["revoked"]
                        and credential["expires_at"] > time.time()
                        and set(required_scopes) <= set(credential["scopes"])
                        and credential["room_id"] in {None, room_id})

    def credential_metadata(self, token, agent_id):
        with self.connect() as conn:
            actor = self._auth(conn, token)
            require(actor['kind'] == 'human' and 'credential:manage' in actor['scopes'], 'forbidden', 403)
            require(conn.execute("SELECT id FROM principals WHERE id=%s AND owner=%s AND payload->>'kind'='agent'", (agent_id, actor['owner'])).fetchone(), 'not_found', 404)
            return conn.execute('SELECT credential_id,room_id,scopes,expires_at,revoked FROM credentials WHERE principal_id=%s AND credential_id IS NOT NULL', (agent_id,)).fetchall()

    def __init__(self, dsn, schema=None):
        require(schema is None or re.fullmatch(r"talkabout_test_[a-f0-9]{32}", schema), "invalid_test_schema", 422)
        self.dsn, self.schema = dsn, schema

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

    def connect(self, autocommit=False):
        import psycopg
        from psycopg.rows import dict_row
        options = {"options": "-csearch_path=" + self.schema} if self.schema else {}
        return psycopg.connect(self.dsn, row_factory=dict_row, autocommit=autocommit, **options)

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
    def _room(conn, row):
        room = copy.deepcopy(row["payload"])
        room["last_seq"] = row["last_event_seq"]
        # shortcut: bounded P0 rooms load their event history; switch to paged event queries before raising room limits.
        room["events"] = [r["payload"] for r in conn.execute("SELECT payload FROM events WHERE room_id=%s ORDER BY seq LIMIT 2049", (row["id"],)).fetchall()]
        room_bounds(room)
        return room

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
    def transaction(self, room_id, token):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM rooms WHERE id=%s FOR UPDATE", (room_id,)).fetchone()
            require(row, "not_found", 404)
            actor = self._auth(conn, token)
            room = self._room(conn, row)
            yield actor, room
            self._flush(conn, room, row["last_event_seq"])

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

    def snapshot(self, token):
        with self.connect() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            actor = self._auth(conn, token)
            rows = conn.execute("SELECT * FROM rooms WHERE owner=%s ORDER BY id LIMIT 65", (actor["owner"],)).fetchall()
            require(len(rows) <= 64, "room_list_capacity_paging_unsupported", 429)
            rooms = {r["id"]: self._room(conn, r) for r in rows}
            principals = {r["id"]: r["payload"] for r in conn.execute("SELECT id,payload FROM principals WHERE owner=%s ORDER BY id LIMIT 129", (actor["owner"],)).fetchall()}
            snapshot_bounds(rooms, principals)
            return actor, rooms, principals

    def cursor(self, cursor_id):
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM cursors WHERE id=%s AND expires_at>%s", (cursor_id, time.time())).fetchone()
            return row["payload"] if row else None

    def save_cursor(self, cursor_id, payload):
        from psycopg.types.json import Jsonb
        with self.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (payload["principal"] + ":cursors",))
            require(conn.execute("SELECT count(*) AS n FROM cursors WHERE principal_id=%s", (payload["principal"],)).fetchone()["n"] < 4096, "cursor_capacity_archival_unsupported", 429)
            conn.execute("INSERT INTO cursors(id,principal_id,payload,expires_at) VALUES(%s,%s,%s,%s)", (cursor_id, payload["principal"], Jsonb(payload), time.time() + DEFAULT_RETENTION.cursor_seconds))

    def tick_all(self):
        from .domain import schedule
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
                schedule(room, principals)
                self._flush(conn, room, row["last_event_seq"])

    @contextmanager
    def listener(self):
        with self.connect(autocommit=True) as conn:
            conn.execute("LISTEN talkabout_changed")
            def wait(timeout):
                for _ in conn.notifies(timeout=timeout, stop_after=1):
                    break
            yield wait
