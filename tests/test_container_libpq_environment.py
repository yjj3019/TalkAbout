"""Container libpq environment checks never connect to a database."""
import os

import pytest

from talkabout import container
from talkabout.config import database_config


@pytest.fixture(autouse=True)
def clean_unsafe_libpq_env(monkeypatch):
    for name in list(os.environ):
        if name.upper() in {"PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS"}:
            monkeypatch.delenv(name)


@pytest.mark.parametrize("name", ["PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS", "pghostaddr", "pgservice"])
@pytest.mark.parametrize("action", ["serve", "preflight", "init-db", "provision-db", "bootstrap-human", "setup-code"])
def test_container_refuses_unsafe_environment_before_store(name, action, monkeypatch, capsys):
    monkeypatch.setenv(name, "SECRET")
    monkeypatch.setattr(container.os, "name", "nt")
    monkeypatch.setattr(container, "PostgresStore", lambda *a, **kw: pytest.fail("store constructed"))
    monkeypatch.setattr(container, "read_secret", lambda *a, **kw: pytest.fail("secret read"))
    with pytest.raises(SystemExit) as error:
        container.main([action])
    assert error.value.code == 2
    output = capsys.readouterr().err
    assert "unsafe_libpq_environment" in output
    assert "SECRET" not in output
    assert os.environ[name] == "SECRET"


@pytest.mark.parametrize("name", ["PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS"])
def test_container_secret_rejects_unsafe_environment(name, monkeypatch, tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("host=talkabout-db user=talkabout_app dbname=talkabout password=SECRET", encoding="utf-8")
    monkeypatch.setenv(name, "")
    with pytest.raises(ValueError, match="^container_database_secret_invalid$"):
        container.read_secret(secret)


def test_container_password_environment_allowed(monkeypatch, tmp_path):
    monkeypatch.setenv("PGPASSWORD", "secret")
    secret = tmp_path / "secret"
    secret.write_text("host=talkabout-db user=talkabout_app dbname=talkabout", encoding="utf-8")
    assert "host=talkabout-db" in container.read_secret(secret)
    assert database_config("host=talkabout-db user=u dbname=d", allowed_hosts={"talkabout-db"})


def test_healthcheck_does_not_need_database_environment(monkeypatch):
    monkeypatch.setenv("PGHOSTADDR", "invalid")
    monkeypatch.setattr(container, "healthcheck", lambda: 17)
    assert container.main(["healthcheck"]) == 17
