import os
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from talkabout.api import create_app
from talkabout.core import Core
from talkabout.domain import AGENT_PERMISSIONS, HUMAN_PERMISSIONS, SELF_PERMISSIONS
from talkabout.store import MemoryStore


_REPO_TEMPLATE = {'path': None}


class FakeWorkRepoGuard:
    """Stand-in for tests that need a work-profile runtime but not git itself.

    Starting git costs over a second per call on Windows and a real guard makes several
    calls per runtime. ``tests/test_work_repo.py`` covers the real guard.
    """
    base_commit = '0' * 40
    tampered = False

    def __init__(self, workspace):
        self.workspace = workspace

    def before_turn(self):
        return None

    def observe(self):
        return {'schema': 'talkabout.work_repo_observation/1', 'state': 'observed', 'base_commit': self.base_commit,
                'head_changed': False, 'changed_count': 0, 'changed': [], 'untracked_count': 0, 'untracked': [],
                'truncated': False, 'ignored_files': 'not_observed', 'evidence': 'fake_for_tests'}


@pytest.fixture
def fast_repo_guard(monkeypatch):
    from talkabout import agent_runtime
    monkeypatch.setattr(agent_runtime, 'WorkRepoGuard', FakeWorkRepoGuard)


def init_clean_repo(path):
    """Make ``path`` a git repository root with one empty commit and a clean tree.

    A work-profile directory must be one (threat review F3-a). Global/system git
    configuration is ignored so the result does not depend on the developer's machine.
    """
    git = shutil.which('git')
    if not git:
        pytest.skip('git is required for work-profile workspaces')
    if _REPO_TEMPLATE.get('path') is None:
        # Starting git twice per test is slow on Windows: build one template, then copy it.
        import atexit
        import tempfile
        template = tempfile.mkdtemp(prefix='talkabout-repo-template-')
        atexit.register(shutil.rmtree, template, True)
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'HOME', 'USERPROFILE'}}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0')
        for args in (['init', '-q'],
                     ['-c', 'user.name=t', '-c', 'user.email=t@example.invalid', '-c', 'commit.gpgsign=false',
                      'commit', '-q', '--allow-empty', '-m', 'initial']):
            subprocess.run([git, *args], cwd=template, env=env, check=True, capture_output=True, timeout=60)
        _REPO_TEMPLATE['path'] = template
    shutil.copytree(_REPO_TEMPLATE['path'], str(path), dirs_exist_ok=True)
    return path


@pytest.fixture
def fixture():
    return build_fixture(MemoryStore())


def build_fixture(store):
    for pid, kind, name in [("human", "human", "Owner"), ("a", "agent", "Atlas"), ("b", "agent", "Birch")]:
        permissions = (HUMAN_PERMISSIONS | {"rooms:create", "agent:manage", "credential:manage"}) if kind == "human" else (AGENT_PERMISSIONS | SELF_PERMISSIONS | {"rooms:create"})
        store.provision({"id": pid, "owner": "fixture-owner", "kind": kind, "display_name": name, "handle": name.lower()}, "fixture-" + pid, permissions)
    core = Core(store)
    room = core.create("fixture-human", {"topic": "Synthetic neutral topic"}, "room-one")
    for pid in ["a", "b"]:
        member = core.mutation("fixture-human", room["id"], "memberships", "invite-" + pid,
                               {"principal_id": pid, "permissions": sorted(AGENT_PERMISSIONS), "status": "pending"})
        core.accept("fixture-" + pid, room["id"], "accept", {"expected_revision": member["revision"], "acknowledge_private": True})
    return store, core, room["id"]


def pytest_addoption(parser):
    parser.addoption("--postgres-config", default=None, help="Explicit configuration path for an isolated ephemeral PostgreSQL fixture only")
    parser.addoption('--invite-browser-config',default=None,help='Explicit owned synthetic MemoryStore browser fixture configuration; never an operating URL')


@pytest.fixture
def postgres_fixture(request):
    import json
    import uuid
    from pathlib import Path
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from talkabout.store import PostgresStore
    config_path = request.config.getoption("--postgres-config")
    if not config_path:
        pytest.skip("NOTRUN: pass an explicit isolated PostgreSQL fixture config; no existing databases are probed")
    config = json.loads(Path(config_path).read_text())
    assert config["ephemeral_fixture"] is True and config["host"] == "127.0.0.1"
    dsn = make_conninfo(host=config["host"], port=config["port"], dbname="postgres", user=config["user"], password=config["password"], connect_timeout=5)
    schema = "talkabout_test_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as conn:
        actual_data_dir = Path(conn.execute("SHOW data_directory").fetchone()[0]).resolve()
        assert actual_data_dir == Path(config["data_directory"]).resolve()
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    store = PostgresStore(dsn, schema)
    try:
        store.initialize()
        yield build_fixture(store)
    finally:
        # The store now reuses pooled connections; a test must not leave them open.
        store.close()
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def leased(core, room_id, token="fixture-a"):
    room = core.read(token, room_id)
    task = next(t for t in room["tasks"] if t["state"] == "queued")
    return core.mutation(token, room_id, "tasks/" + task["id"] + "/claim", "claim-" + task["id"],
                         {"generation": task["generation"], "membership_revision": task["membership_revision"]})


def result(task, text="Fixture result"):
    return {k: task[k] for k in ("attempt", "generation", "membership_revision")} | {
        "result": {"client_message_id": "result-" + task["id"], "text": text, "reply_to": None, "mentions": []}}


def control(core, room_id, action):
    room = core.read("fixture-human", room_id)
    pending = core.mutation("fixture-human", room_id, "controls", action + "-request-" + str(room["room_revision"]),
                            {"action": action, "expected_revision": room["room_revision"]})
    return core.mutation("fixture-human", room_id, "controls/" + pending["id"] + "/approve", action + "-approve", {})


class LocalClient:
    def __init__(self, store, token="fixture-a"):
        self.http = TestClient(create_app(store, start_scheduler=False, allow_fixture_bearer=True))
        self.token = token
        self.calls = []

    def request(self, method, path, body=None, key=None, params=None):
        import httpx
        self.calls.append((method, path))
        response = self.http.request(method, "/api/v1/" + path, json=body, params=params,
                                     headers={"Authorization": "Bearer " + self.token, **({"Idempotency-Key": key} if key else {})})
        response.raise_for_status()
        return response.json()
