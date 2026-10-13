import json
import time

import pytest
from fastapi.testclient import TestClient

from talkabout.api import create_app
from talkabout.auth import COOKIE, bootstrap_human
from talkabout.domain import Problem, token_hash
from talkabout.store import MemoryStore

class SessionFixture:
    def __init__(self, *values): self.values = values
    def __iter__(self): return iter(self.values)
    def __repr__(self): return "<synthetic human session fixture; secrets omitted>"


@pytest.fixture
def session_fixture():
    store = MemoryStore()
    actor = bootstrap_human(store, "fixture-workspace", "Fixture Owner", "owner", "fixture-password-not-real")
    client = TestClient(create_app(store, start_scheduler=False, allowed_hosts=["testserver"]))
    response = client.post("/api/v1/session", json={"workspace_id": "fixture-workspace", "username": "owner", "password": "fixture-password-not-real"}, headers={"Origin": "http://testserver"})
    assert response.status_code == 200
    csrf = response.json()["csrf_token"]
    return SessionFixture(store, client, actor, csrf, response)


def post(client, path, body, csrf, key="fixture"):
    return client.post("/api/v1/" + path, json=body, headers={"Origin": "http://testserver", "X-CSRF-Token": csrf, "Idempotency-Key": key})


def test_session_cookie_secret_storage_and_csrf(session_fixture):
    store, client, actor, csrf, response = session_fixture
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert COOKIE not in response.json() and "token" not in response.json()
    raw = client.cookies.get(COOKIE)
    assert raw not in json.dumps(store.sessions) and "fixture-password-not-real" not in json.dumps(store.passwords)
    assert client.post("/api/v1/rooms", json={"topic": "fixture"}).status_code == 403
    assert post(client, "rooms", {"topic": "fixture"}, "wrong").status_code == 403
    assert post(client, "rooms", {"topic": "fixture"}, csrf).status_code == 200


def test_origin_host_and_login_body_impersonation(session_fixture):
    _, client, _, _, _ = session_fixture
    fresh = TestClient(client.app)
    body = {"workspace_id": "fixture-workspace", "username": "owner", "password": "fixture-password-not-real"}
    assert fresh.post("/api/v1/session", json=body, headers={"Origin": "http://evil.invalid"}).status_code == 403
    assert fresh.post("/api/v1/session", json={**body, "role": "admin"}, headers={"Origin": "http://testserver"}).status_code == 422
    assert fresh.get("/", headers={"Host": "evil.invalid"}).status_code == 400
    assert fresh.get("/api/v1/rooms", headers={"X-Admin": "true", "X-Demo-User": "owner"}).status_code == 401


def test_logout_and_expiry(session_fixture):
    store, client, _, csrf, _ = session_fixture
    assert post(client, "session/logout", {}, csrf).status_code == 200
    assert client.get("/api/v1/rooms").status_code == 401
    login = client.post("/api/v1/session", json={"workspace_id": "fixture-workspace", "username": "owner", "password": "fixture-password-not-real"}, headers={"Origin": "http://testserver"})
    assert login.status_code == 200
    store.sessions[token_hash(client.cookies.get(COOKIE))]["expires_at"] = time.time() - 1
    assert client.get("/api/v1/rooms").status_code == 401


def test_permission_revocation_after_read(session_fixture):
    store, client, actor, csrf, _ = session_fixture
    room = post(client, "rooms", {"topic": "fixture"}, csrf).json()
    assert client.get("/api/v1/rooms/" + room["id"]).status_code == 200
    store.principals[actor["id"]]["account_scopes"] = ["room:read"]
    assert post(client, "rooms/" + room["id"] + "/controls", {"action": "pause", "expected_revision": 1}, csrf).status_code == 403


def test_bootstrap_is_explicit_once_and_fixture_bearer_not_default(session_fixture):
    store, _, _, _, _ = session_fixture
    with pytest.raises(Problem, match="bootstrap_exists"):
        bootstrap_human(store, "fixture-workspace", "Another Owner", "other", "fixture-password-not-real")
    from conftest import build_fixture
    legacy_store, _, _ = build_fixture(MemoryStore())
    client = TestClient(create_app(legacy_store, start_scheduler=False, allowed_hosts=["testserver"]))
    assert client.get("/api/v1/rooms", headers={"Authorization": "Bearer fixture-human"}).status_code == 403
    assert client.get("/api/v1/rooms", headers={"Authorization": "Bearer fixture-a"}).status_code == 200


def test_https_cookie_and_login_throttle(session_fixture):
    store, _, _, _, _ = session_fixture
    client = TestClient(create_app(store, start_scheduler=False, allowed_hosts=["testserver"]), base_url="https://testserver")
    body = {"workspace_id": "fixture-workspace", "username": "owner", "password": "fixture-password-not-real"}
    assert "Secure" in client.post("/api/v1/session", json=body, headers={"Origin": "https://testserver"}).headers["set-cookie"]
    wrong = {**body, "password": "wrong-fixture-password"}
    for _ in range(5):
        assert client.post("/api/v1/session", json=wrong, headers={"Origin": "https://testserver"}).status_code == 401
    assert client.post("/api/v1/session", json=wrong, headers={"Origin": "https://testserver"}).status_code == 429
