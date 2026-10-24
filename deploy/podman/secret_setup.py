"""User-only interactive initial secret setup. Never called by server startup."""
import argparse
import getpass
import json
import subprocess
import sys

NAMES = ('talkabout-db-password', 'talkabout-db-init-url', 'talkabout-db-url')


def quoted(value):
    return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"


def dsn(user, password):
    return 'host=talkabout-db port=5432 dbname=talkabout user=' + user + ' password=' + quoted(password)


def password(label):
    value = getpass.getpass(label + ': ')
    if not 12 <= len(value) <= 512 or any(ord(c) < 32 for c in value):
        raise ValueError('password_length_or_control_character_rejected')
    if value != getpass.getpass('Confirm ' + label + ': '):
        raise ValueError('password_confirmation_mismatch')
    return value


def create_user_secrets(runner=subprocess.run):
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ValueError('interactive_user_terminal_required')
    info = runner(['podman', 'info', '--format', '{{.Host.Security.Rootless}}'], capture_output=True)
    if info.returncode or info.stdout.strip() != b'true':
        raise ValueError('rootless_podman_required')
    for name in NAMES:
        existing = runner(['podman', 'secret', 'inspect', name], capture_output=True)
        if existing.returncode == 0:
            raise ValueError('existing_secrets_preserved_no_automatic_rotation')
    print('User input only. Secret values are not logged. The default Podman file store is read-protected; encryption is not guaranteed.')
    owner = password('New dedicated database owner password')
    app = password('New dedicated runtime database password')
    if owner == app:
        raise ValueError('distinct_database_passwords_required')
    values = (owner, dsn('talkabout_owner', owner), dsn('talkabout_app', app))
    created = []
    for name, value in zip(NAMES, values):
        result = runner(['podman', 'secret', 'create', '--label', 'io.talkabout.project=talkabout', name, '-'],
                        input=value.encode('utf-8'), capture_output=True)
        if result.returncode:
            print(json.dumps({'status': 'partial_failure_existing_values_preserved', 'created_names': created}))
            raise ValueError('secret_creation_failed_no_values_disclosed')
        created.append(name)
    print(json.dumps({'status': 'user_created_secret_refs', 'names': created, 'containers_started': False, 'database_roles_created': False}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--create', action='store_true', help='Explicit interactive user action only; refuses existing refs')
    args = parser.parse_args()
    if not args.create:
        print(json.dumps({'status': 'awaiting_user_input', 'names': NAMES, 'values': 'not_generated'}))
        return 0
    try:
        create_user_secrets()
        return 0
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
