"""Explicit installer-only DB transaction; no password rotation or human creation."""
from pathlib import Path
import re
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from .store import PostgresStore

TABLES = ('principals','credentials','rooms','events','cursors','human_passwords','human_sessions','installation_setup')


def provision_database(owner_dsn, runtime_dsn, installation_id):
    if not re.fullmatch(r'[a-f0-9]{32}', installation_id):
        raise ValueError('invalid_installation_id')
    try:
        owner, runtime = conninfo_to_dict(owner_dsn), conninfo_to_dict(runtime_dsn)
        if owner['user']!='talkabout_owner' or runtime['user']!='talkabout_app' or not owner.get('password') or not runtime.get('password'):
            raise ValueError()
        if any(owner.get(k)!=runtime.get(k) for k in ('host','port','dbname')) or owner.get('dbname')!='talkabout':
            raise ValueError()
        # Private installation owner only. Never change cluster-wide logging settings.
        safe_owner=make_conninfo(owner_dsn, options='-clog_statement=none -clog_min_error_statement=panic -clog_parameter_max_length=0 -clog_parameter_max_length_on_error=0')
        with psycopg.connect(safe_owner) as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('talkabout:db-installation',0))")
            marker=conn.execute("SELECT to_regclass('public.installation_metadata')").fetchone()[0]
            role=conn.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname='talkabout_app'").fetchone()
            if marker:
                row=conn.execute('SELECT installation_id FROM public.installation_metadata WHERE id=1').fetchone()
                if not row or row[0]!=installation_id or not role or any(role):
                    raise ValueError()
                # Existing role: authenticate; never repair by changing its password.
                with psycopg.connect(runtime_dsn) as app:
                    if app.execute('SELECT current_user').fetchone()[0]!='talkabout_app': raise ValueError()
            else:
                if role or conn.execute("SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND left(n.nspname,3)<>'pg_' LIMIT 1").fetchone():
                    raise ValueError()
                if conn.execute("SELECT 1 FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') AND left(nspname,3)<>'pg_' LIMIT 1").fetchone():
                    raise ValueError()
                encrypted=conn.pgconn.encrypt_password(runtime['password'].encode(), b'talkabout_app', b'scram-sha-256').decode()
                conn.execute(sql.SQL('CREATE ROLE talkabout_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}').format(sql.Literal(encrypted)))
                conn.execute(Path(__file__).with_name('schema.sql').read_text())
                conn.execute('CREATE TABLE public.installation_metadata(id smallint PRIMARY KEY CHECK(id=1),installation_id text NOT NULL)')
                conn.execute('INSERT INTO public.installation_metadata VALUES(1,%s)',(installation_id,))
                conn.execute('GRANT CONNECT ON DATABASE talkabout TO talkabout_app')
                conn.execute('GRANT USAGE ON SCHEMA public TO talkabout_app')
                conn.execute('REVOKE CREATE ON SCHEMA public FROM talkabout_app')
                for table in TABLES:
                    conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE public.{} TO talkabout_app').format(sql.Identifier(table)))
        if not PostgresStore(runtime_dsn).ready(): raise ValueError()
        return {'database':'configured','passwords_returned':False,'human_created':False}
    except Exception:
        # Never expose DSNs, SQL containing a verifier, libpq messages or passwords.
        raise ValueError('database_identity_or_secret_mismatch_or_unavailable') from None
