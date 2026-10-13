"""Explicit isolated synthetic fixture. No published ports or operating credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import time
import uuid


def run(args, data=None, check=True):
    result = subprocess.run(['podman', *args], input=data, capture_output=True, timeout=120)
    if check and result.returncode:
        raise RuntimeError('podman_operation_failed_' + args[0])
    return result


def smoke(image):
    if not image.startswith('localhost/talkabout:'):
        raise ValueError('project_image_required')
    if run(['info', '--format', '{{.Host.Security.Rootless}}']).stdout.strip() != b'true':
        raise ValueError('rootless_required')
    identifier = uuid.uuid4().hex
    label = 'io.talkabout.fixture=' + identifier
    labels = ['--label', 'io.talkabout.project=talkabout', '--label', label]
    prefix = 'talkabout-fixture-' + identifier
    network, volume, db, app = (prefix + '-' + x for x in ('net', 'data', 'db', 'app'))
    init = prefix + '-init'
    password_names = [prefix + '-' + x for x in ('password', 'owner-url', 'app-url')]
    resources = [('container', app), ('container', init), ('container', db), *[('secret', n) for n in password_names], ('volume', volume), ('network', network)]
    def owned(kind, name):
        result = run([kind, 'inspect', name], check=False)
        if result.returncode:
            return False
        obj = json.loads(result.stdout)[0]
        metadata = obj.get('Config', {}).get('Labels', {}) if kind == 'container' else obj.get('Spec', {}).get('Labels', {}) if kind == 'secret' else obj.get('Labels', {})
        if metadata.get('io.talkabout.fixture') != identifier:
            raise RuntimeError('fixture_ownership_mismatch')
        return True
    for kind, name in resources:
        if run([kind, 'inspect', name], check=False).returncode == 0:
            raise RuntimeError('fixture_name_collision')
    result = {'status': 'FAIL', 'fixture_only': True, 'published_ports': False, 'checks': []}
    common = [*labels, '--network', network, '--cap-drop=all', '--security-opt=no-new-privileges', '--read-only', '--read-only-tmpfs=false', '--tmpfs=/tmp:rw,nosuid,nodev,size=32m', '--memory=512m', '--memory-swap=512m', '--cpus=1', '--pids-limit=128']
    def sql(statement, database='talkabout'):
        return run(['exec', '-i', db, 'psql', '-U', 'talkabout_owner', '-d', database, '-At', '-v', 'ON_ERROR_STOP=1'], statement.encode()).stdout.strip()
    def db_ready():
        for _ in range(40):
            if run(['exec', db, 'pg_isready', '-U', 'talkabout_owner', '-d', 'talkabout'], check=False).returncode == 0:
                return
            time.sleep(0.25)
        raise RuntimeError('fixture_database_deadline')
    def app_ready():
        for _ in range(40):
            if run(['exec', app, 'python', '-m', 'talkabout.container', 'healthcheck'], check=False).returncode == 0:
                return
            time.sleep(0.25)
        raise RuntimeError('fixture_app_deadline')
    try:
        owner_password, app_password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        values = [owner_password, f'host=talkabout-db port=5432 dbname=talkabout user=talkabout_owner password={owner_password}', f'host=talkabout-db port=5432 dbname=talkabout user=talkabout_app password={app_password}']
        for name, value in zip(password_names, values):
            run(['secret', 'create', *labels, name, '-'], value.encode())
        run(['network', 'create', '--internal', *labels, network])
        run(['volume', 'create', *labels, volume])
        postgres = json.loads((Path(__file__).parent/'images.lock.json').read_text())['images']['postgres']['reference']
        run(['run', '-d', '--name', db, *common, '--user=999:999', '--network-alias=talkabout-db', '--volume', volume+':/var/lib/postgresql:U', '--tmpfs=/var/run/postgresql:rw,nosuid,nodev,size=16m,uid=999,gid=999', '--secret', password_names[0]+',target=pg-password,uid=999,gid=999,mode=0400', '-e', 'POSTGRES_PASSWORD_FILE=/run/secrets/pg-password', '-e', 'POSTGRES_USER=talkabout_owner', '-e', 'POSTGRES_DB=talkabout', postgres, '-c', 'max_connections=30', '-c', 'shared_buffers=64MB'])
        db_ready()
        run(['run', '--rm', '--name', init, *common, '--user=10001:10001', '--secret', password_names[1]+',target=talkabout-db-url,uid=10001,gid=10001,mode=0400', image, 'init-db'])
        sql(f"CREATE ROLE talkabout_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '{app_password}'; GRANT CONNECT ON DATABASE talkabout TO talkabout_app; GRANT USAGE ON SCHEMA public TO talkabout_app; GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO talkabout_app; CREATE TABLE fixture_probe(value text); INSERT INTO fixture_probe VALUES ('synthetic-marker');")
        run(['run', '-d', '--name', app, *common, '--user=10001:10001', '--secret', password_names[2]+',target=talkabout-db-url,uid=10001,gid=10001,mode=0400', '--health-cmd=python -m talkabout.container healthcheck', image])
        app_ready()
        result['checks'].append('app_and_database_readiness')
        run(['stop', db])
        assert run(['exec', app, 'python', '-m', 'talkabout.container', 'healthcheck'], check=False).returncode == 1
        result['checks'].append('database_outage_readiness_failure')
        run(['start', db])
        db_ready()
        assert owned('container', app)
        run(['restart', app])
        app_ready()
        assert sql('SELECT value FROM fixture_probe;') == b'synthetic-marker'
        result['checks'].append('restart_and_persistent_data')
        backup = run(['exec', db, 'pg_dump', '-U', 'talkabout_owner', '-d', 'talkabout', '-Fc']).stdout
        sql('CREATE DATABASE talkabout_restore;')
        run(['exec', '-i', db, 'pg_restore', '--exit-on-error', '--no-owner', '-U', 'talkabout_owner', '-d', 'talkabout_restore'], backup)
        assert sql('SELECT value FROM fixture_probe;', 'talkabout_restore') == b'synthetic-marker'
        result['checks'].append('logical_backup_restore')
        result['backup_sha256'] = hashlib.sha256(backup).hexdigest()
        result['status'] = 'PASS'
    except Exception as error:
        result['error_type'] = type(error).__name__
        result['failure_phase'] = str(error) if str(error).startswith(('podman_operation_failed_', 'fixture_')) else 'contract_assertion'
        # Do not print SQL/DSN/credential-bearing stderr or exception bodies.
    finally:
        cleanup = True
        for kind, name in resources:
            try:
                if owned(kind, name):
                    command = [kind, 'rm', '--force', name] if kind == 'container' else [kind, 'rm', name]
                    run(command)
            except Exception:
                cleanup = False
        result['fixture_cleanup'] = cleanup
    print(json.dumps(result))
    return 0 if result['status'] == 'PASS' and result['fixture_cleanup'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    raise SystemExit(smoke(parser.parse_args().image))
