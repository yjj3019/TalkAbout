import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

from talkabout.api import create_app
from talkabout.config import database_config
from talkabout.store import MemoryStore, PostgresStore


@pytest.mark.parametrize("dsn", [None, "", "not a connection string", "host=external.invalid user=fixture dbname=fixture", "host=127.0.0.1 port=0 user=fixture dbname=fixture", "host=127.0.0.1 user=fixture"])
def test_invalid_configuration_does_not_echo_dsn(dsn):
    with pytest.raises(ValueError) as error:
        database_config(dsn)
    assert str(error.value) in {"database_url_required", "invalid_database_configuration", "loopback_database_host_required"}


def test_configuration_forces_bounded_connection_timeout():
    fields = conninfo_to_dict(database_config("host=127.0.0.1 user=fixture dbname=fixture password=synthetic-only connect_timeout=0"))
    assert fields["connect_timeout"] == "5"


def test_fixture_or_unavailable_db_is_not_ready():
    with TestClient(create_app(MemoryStore(), start_scheduler=False, allowed_hosts=["testserver"])) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
    assert PostgresStore("host=127.0.0.1 port=1 user=synthetic dbname=synthetic connect_timeout=1").ready() is False


def test_postgres_real_loopback_health_readiness_and_shutdown(postgres_fixture):
    store, _, _ = postgres_fixture
    assert store.ready() is True
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(store), host="127.0.0.1", port=port, access_log=False, log_level="error", timeout_graceful_shutdown=2))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2) as client:
            assert client.get("/health").json()["scheduler"] == "running"
            assert client.get("/ready").json() == {"status": "ready", "deployment": "prototype"}
            assert client.get("/api/v1/rooms").status_code == 401
            assert client.get("/").status_code == 200
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
    assert not thread.is_alive()
