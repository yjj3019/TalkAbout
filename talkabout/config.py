"""Validated loopback deployment configuration; errors never contain the DSN."""
import os

from psycopg.conninfo import conninfo_to_dict, make_conninfo


_SAFE_FIELDS = frozenset({"host", "port", "user", "dbname", "password", "connect_timeout", "sslmode"})
_LOOPBACK_ADDRESSES = {"localhost": "127.0.0.1", "127.0.0.1": "127.0.0.1", "::1": "::1"}


def validate_libpq_environment(*, container=False):
    # Service definitions can inject arbitrary parameters; options can alter the
    # server session. A container hostname cannot pin hostaddr without resolving
    # DNS, so ambient PGHOSTADDR must also be rejected there. Match names
    # case-insensitively (including on Windows); never include values in errors.
    unsafe = {"PGSERVICE", "PGSERVICEFILE", "PGOPTIONS"}
    if container:
        unsafe.add("PGHOSTADDR")
    if any(name.upper() in unsafe for name in os.environ):
        raise ValueError("unsafe_libpq_environment")


def database_config(value, *, allowed_hosts=None):
    validate_libpq_environment(container=allowed_hosts is not None)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("database_url_required")
    try:
        fields = conninfo_to_dict(value)
        port = int(fields.get("port", 5432))
    except Exception:
        raise ValueError("invalid_database_configuration") from None
    if set(fields) - _SAFE_FIELDS:
        raise ValueError("invalid_database_configuration")
    host = fields.get("host")
    if not host or "," in host or host not in (allowed_hosts if allowed_hosts is not None else _LOOPBACK_ADDRESSES):
        raise ValueError("loopback_database_host_required")
    dbname = fields.get("dbname", "")
    # libpq may expand a dbname containing a conninfo or URI into connection
    # parameters, bypassing validation of the top-level fields.
    if (not 1 <= port <= 65535 or not fields.get("user") or not dbname
            or "=" in dbname or dbname.startswith(("postgresql://", "postgres://"))):
        raise ValueError("invalid_database_configuration")
    fields["port"] = str(port)
    fields["connect_timeout"] = "5"
    if host in _LOOPBACK_ADDRESSES:
        # Do not resolve localhost: hosts files, DNS and PGHOSTADDR can otherwise
        # redirect a nominally loopback connection to a remote machine.
        fields["hostaddr"] = _LOOPBACK_ADDRESSES[host]
    try:
        return make_conninfo(**fields)
    except Exception:
        raise ValueError("invalid_database_configuration") from None
