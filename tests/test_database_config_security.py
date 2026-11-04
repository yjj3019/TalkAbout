"""Configuration validation is offline and must not disclose supplied secrets."""
import pytest
from psycopg.conninfo import conninfo_to_dict

from talkabout.config import database_config


@pytest.fixture(autouse=True)
def clean_unsafe_libpq_env(monkeypatch):
    import os
    for name in list(os.environ):
        if name.upper() in {"PGSERVICE", "PGSERVICEFILE", "PGOPTIONS", "PGHOSTADDR"}:
            monkeypatch.delenv(name)


@pytest.mark.parametrize("dsn", [
    "host=localhost hostaddr=203.0.113.1 user=u dbname=d password=SECRET",
    "host=localhost service=evil user=u dbname=d password=SECRET",
    "host=localhost options='-c search_path=evil' user=u dbname=d password=SECRET",
    "host=localhost application_name=SECRET user=u dbname=d",
    "host=localhost,evil.invalid user=u dbname=d password=SECRET",
    "host=localhost port=5432,5433 user=u dbname=d password=SECRET",
    "host=localhost user=u dbname='host=evil.invalid password=SECRET'",
    "postgresql://u:SECRET@localhost/d?hostaddr=203.0.113.1",
    "postgresql://u:SECRET@localhost/d?service=evil",
    "postgresql://u:SECRET@localhost/d?options=-c%20search_path%3Devil",
    "postgresql://u:SECRET@localhost,evil.invalid/d",
    "postgresql://u:SECRET@localhost/d?unknown_option=foo",
    "host=localhost user=u dbname=d password='SECRET",
])
def test_unsafe_conninfo_is_rejected_without_secrets(dsn):
    with pytest.raises(ValueError) as error:
        database_config(dsn)
    assert str(error.value) in {"invalid_database_configuration", "loopback_database_host_required"}
    assert "SECRET" not in str(error.value)
    assert error.value.__cause__ is None


@pytest.mark.parametrize("dsn,host,address", [
    ("host=localhost user=u dbname=d password=secret connect_timeout=0 sslmode=require", "localhost", "127.0.0.1"),
    ("postgresql://u:secret@127.0.0.1:5432/d?sslmode=disable", "127.0.0.1", "127.0.0.1"),
    ("postgresql://u:secret@[::1]/d", "::1", "::1"),
])
def test_native_loopback_is_pinned_without_dns(dsn, host, address, monkeypatch):
    monkeypatch.setenv("PGHOSTADDR", "203.0.113.1")
    fields = conninfo_to_dict(database_config(dsn))
    assert fields["host"] == host
    assert fields["hostaddr"] == address
    assert fields["connect_timeout"] == "5"
    assert fields["user"] == "u" and fields["dbname"] == "d"


@pytest.mark.parametrize("dsn", [
    "host=talkabout-db port=5432 user=talkabout_app dbname=talkabout password=secret sslmode=prefer",
    "postgresql://talkabout_app:secret@talkabout-db:5432/talkabout?connect_timeout=10",
])
def test_container_allowlist_remains_compatible(dsn):
    fields = conninfo_to_dict(database_config(dsn, allowed_hosts={"talkabout-db"}))
    assert fields["host"] == "talkabout-db"
    assert "hostaddr" not in fields
    assert fields["connect_timeout"] == "5"


@pytest.mark.parametrize("name", ["PGSERVICE", "PGSERVICEFILE", "PGOPTIONS", "pgoptions"])
def test_native_unsafe_environment_rejected_without_value(name, monkeypatch):
    monkeypatch.setenv(name, "SECRET")
    with pytest.raises(ValueError, match="^unsafe_libpq_environment$"):
        database_config("host=localhost user=u dbname=d")


@pytest.mark.parametrize("name", ["PGSERVICE", "PGSERVICEFILE", "PGOPTIONS"])
@pytest.mark.parametrize("command", ["init-db", "serve", "preflight", "setup-code", "bootstrap-human"])
def test_native_cli_rejects_before_store(name, command, monkeypatch, capsys):
    import sys
    from talkabout import cli
    monkeypatch.setenv(name, "SECRET")
    monkeypatch.setenv("TALKABOUT_DATABASE_URL", "host=localhost user=u dbname=d")
    argv = ["talkabout", command]
    if command == "bootstrap-human":
        argv += ["--workspace", "w", "--name", "n", "--handle", "h"]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(cli, "PostgresStore", lambda *a, **kw: pytest.fail("store constructed"))
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    output = capsys.readouterr().err
    assert "unsafe_libpq_environment" in output and "SECRET" not in output


def test_native_password_environment_allowed(monkeypatch):
    monkeypatch.setenv("PGPASSWORD", "secret")
    assert conninfo_to_dict(database_config("host=localhost user=u dbname=d"))["hostaddr"] == "127.0.0.1"


def test_multi_host_denied_even_if_explicitly_allowed():
    with pytest.raises(ValueError):
        database_config("host=localhost,evil user=u dbname=d", allowed_hosts={"localhost,evil"})
