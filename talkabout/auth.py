import base64
import hashlib
import hmac
import secrets
import threading
import time

from .domain import HUMAN_PERMISSIONS, ident, normalize_name, require, token_hash

COOKIE = "talkabout_session"
ITERATIONS = 600000
ADMIN_SCOPES = HUMAN_PERMISSIONS | {"rooms:create", "agent:manage", "credential:manage"}


def password_record(password):
    require(isinstance(password, str) and 12 <= len(password) <= 1024, "invalid_password", 422)
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return {"salt": base64.b64encode(salt).decode(), "verifier": base64.b64encode(derived).decode(), "iterations": ITERATIONS}


def verify_password(password, record):
    if not isinstance(password, str) or len(password) > 1024:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(record["salt"]), record["iterations"])
    return hmac.compare_digest(actual, base64.b64decode(record["verifier"]))


def bootstrap_human(store, workspace, display_name, handle, password):
    """Explicit operator operation only; never invoked by server startup."""
    require(isinstance(workspace, str) and 1 <= len(workspace) <= 128, "invalid_workspace", 422)
    name, name_key, handle = normalize_name(display_name, handle)
    principal = {"id": ident(), "owner": workspace, "kind": "human", "display_name": name, "name_key": name_key,
                 "handle": handle, "identity_revision": 1, "account_scopes": sorted(ADMIN_SCOPES)}
    store.bootstrap(principal, password_record(password))
    return {k: principal[k] for k in ("id", "owner", "display_name", "handle")}


class HumanAuth:
    # Failed-login windows are tracked per workspace:username. Evicting a live
    # window would let an attacker with many usernames reset a victim's counter,
    # so only expired windows leave the table and a full table fails closed.
    MAX_TRACKED_KEYS = 1024
    WINDOW_SECONDS = 60
    MAX_ATTEMPTS = 5

    def __init__(self, store, ttl=900):
        self.store, self.ttl = store, ttl
        self.failures = {}
        self.lock = threading.Lock()
        self.dummy = password_record("synthetic-dummy-verifier-only")

    def login(self, body):
        require(set(body) <= {"workspace_id", "username", "password"}, "invalid_login", 422)
        workspace, username = body.get("workspace_id"), body.get("username")
        require(isinstance(workspace, str) and len(workspace) <= 128 and isinstance(username, str) and len(username) <= 32, "invalid_login", 422)
        key = workspace + ":" + username
        with self.lock:
            now = time.monotonic()
            recent = [t for t in self.failures.get(key, []) if now - t < self.WINDOW_SECONDS]
            require(len(recent) < self.MAX_ATTEMPTS, "login_rate_limit", 429)
            if key not in self.failures:
                if len(self.failures) >= self.MAX_TRACKED_KEYS:
                    expired = [name for name, stamps in self.failures.items()
                               if not any(now - stamp < self.WINDOW_SECONDS for stamp in stamps)]
                    for name in expired:
                        del self.failures[name]
                require(len(self.failures) < self.MAX_TRACKED_KEYS, "login_capacity", 429)
            self.failures[key] = recent + [now]
        found = self.store.password_lookup(workspace, username)
        verified = verify_password(body.get("password"), found["record"] if found else self.dummy)
        require(found and verified, "unauthorized", 401)
        raw, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.store.save_session(token_hash(raw), {"principal_id": found["principal_id"], "expires_at": time.time() + self.ttl, "csrf_hash": token_hash(csrf)})
        actor = self.store.auth("session:" + raw)
        with self.lock:
            self.failures.pop(key, None)
        return raw, {"user": self.public_user(actor), "csrf_token": csrf, "expires_at": time.time() + self.ttl}

    @staticmethod
    def public_user(actor):
        return {k: actor[k] for k in ("id", "display_name", "handle", "kind", "scopes")}

    def session(self, raw):
        actor = self.store.auth("session:" + raw)
        csrf = secrets.token_urlsafe(32)
        self.store.rotate_csrf(token_hash(raw), token_hash(csrf))
        return {"user": self.public_user(actor), "csrf_token": csrf}

    def check_csrf(self, raw, supplied):
        require(isinstance(supplied, str) and len(supplied) <= 128, "csrf_required", 403)
        session = self.store.session_record(token_hash(raw))
        require(session and session["expires_at"] > time.time(), "unauthorized", 401)
        require(hmac.compare_digest(session["csrf_hash"], token_hash(supplied)), "csrf_invalid", 403)
