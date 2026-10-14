"""Private container entrypoint. Reads a mounted secret; never initializes on serve."""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys
import urllib.request

from .api import create_app
from .config import database_config
from .store import PostgresStore

SECRET = Path('/run/secrets/talkabout-db-url')


def read_secret(path=SECRET, *, initialize=False):
    try:
        with Path(path).open('rb') as source:
            value = source.read(4097)
        if len(value) > 4096:
            raise ValueError()
        dsn = database_config(value.decode('utf-8').strip(), allowed_hosts={'talkabout-db'})
        from psycopg.conninfo import conninfo_to_dict
        fields = conninfo_to_dict(dsn)
        if set(fields) - {'host', 'port', 'user', 'dbname', 'password', 'connect_timeout', 'sslmode'}:
            raise ValueError()
        expected_user = 'talkabout_owner' if initialize else 'talkabout_app'
        if int(fields.get('port', 5432)) != 5432 or fields.get('dbname') != 'talkabout' or fields.get('user') != expected_user:
            raise ValueError()
        return dsn
    except Exception:
        raise ValueError('container_database_secret_invalid') from None


def healthcheck():
    try:
        # Never honor proxy environment variables for the private health probe.
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:8080/ready', timeout=5) as response:
            return 0 if response.status == 200 and json.load(response).get('status') == 'ready' else 1
    except Exception:
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description='TalkAbout private container entrypoint; no automatic schema/accounts')
    parser.add_argument('action', choices=['serve', 'preflight', 'init-db', 'provision-db', 'bootstrap-human', 'setup-code', 'healthcheck'])
    parser.add_argument('--installation-id')
    parser.add_argument('--workspace')
    parser.add_argument('--name')
    parser.add_argument('--handle')
    args = parser.parse_args(argv)
    if args.action == 'healthcheck':
        return healthcheck()
    if os.name != 'nt' and os.geteuid() == 0:
        parser.error('nonroot_container_user_required')
    try:
        store = PostgresStore(read_secret(initialize=args.action in {'init-db','provision-db'}))
    except ValueError as error:
        parser.error(str(error))
    if args.action == 'preflight':
        ready = store.ready()
        print(json.dumps({'database_schema': 'ready' if ready else 'not_ready', 'deployment': 'NOT_DEPLOYED'}))
        return 0 if ready else 1
    if args.action == 'init-db':
        store.initialize()
        return 0
    if args.action == 'provision-db':
        from .provision import provision_database
        try:
            print(json.dumps(provision_database(store.dsn, read_secret('/run/secrets/talkabout-runtime-url'), args.installation_id or '')))
        except ValueError as error:
            print(json.dumps({'code': str(error)}), file=sys.stderr)
            return 1
        return 0
    if args.action == 'setup-code':
        from .setup import terminal_issue
        terminal_issue(store)
        return 0
    if args.action == 'bootstrap-human':
        if not all((args.workspace, args.name, args.handle)):
            parser.error('workspace_name_handle_required')
        from .auth import bootstrap_human
        password = getpass.getpass('Initial human password (never logged): ')
        if password != getpass.getpass('Confirm password: '):
            parser.error('password_confirmation_mismatch')
        print(json.dumps(bootstrap_human(store, args.workspace, args.name, args.handle, password)))
        return 0
    # 0.0.0.0 exists only inside the private namespace; host publish is loopback.
    import uvicorn
    uvicorn.run(create_app(store, allowed_hosts=['127.0.0.1', 'localhost']), host='0.0.0.0', port=8080,
                access_log=False, proxy_headers=False, limit_concurrency=16, timeout_graceful_shutdown=10)
    return 0


if __name__ == '__main__':
    sys.exit(main())
