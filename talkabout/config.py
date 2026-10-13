"""Validated loopback deployment configuration; errors never contain the DSN."""
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def database_config(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("database_url_required")
    try:
        fields = conninfo_to_dict(value)
        port = int(fields.get("port", 5432))
    except Exception:
        raise ValueError("invalid_database_configuration") from None
    if fields.get("host") not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("loopback_database_host_required")
    if not 1 <= port <= 65535 or not fields.get("user") or not fields.get("dbname"):
        raise ValueError("invalid_database_configuration")
    return make_conninfo(value, connect_timeout=5)
