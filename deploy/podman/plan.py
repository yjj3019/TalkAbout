"""Render Podman argv only. Never invokes Podman or changes host resources."""
import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
LABEL = 'io.talkabout.project=talkabout'
NETWORK = 'talkabout-private'
DB = 'talkabout-db'
APP = 'talkabout-app'
VOLUME = 'talkabout-pg18-data'


def plan(app_image, host_port=8080):
    if not re.fullmatch(r'localhost/talkabout:[a-f0-9]{40}', app_image):
        raise ValueError('source_sha_image_tag_required')
    if type(host_port) is not int or not 1024 <= host_port <= 65535:
        raise ValueError('unprivileged_host_port_required')
    images = json.loads((ROOT/'deploy/podman/images.lock.json').read_text())['images']
    common = ['--label', LABEL, '--network', NETWORK, '--cap-drop=all', '--security-opt=no-new-privileges', '--pids-limit=128', '--cpus=1', '--memory=512m', '--memory-swap=512m', '--read-only', '--read-only-tmpfs=false', '--tmpfs=/tmp:rw,nosuid,nodev,size=32m', '--log-opt=max-size=10mb']
    db = ['podman', 'run', '--detach', '--name', DB, *common, '--user=999:999', '--network-alias', DB,
          '--volume', VOLUME+':/var/lib/postgresql:U', '--tmpfs=/var/run/postgresql:rw,nosuid,nodev,size=16m,mode=1777',
          '--secret', 'talkabout-db-password,type=mount,target=pg-password,uid=999,gid=999,mode=0400',
          '--env', 'POSTGRES_PASSWORD_FILE=/run/secrets/pg-password', '--env', 'POSTGRES_USER=talkabout_owner', '--env', 'POSTGRES_DB=talkabout',
          '--health-cmd=pg_isready -U talkabout_owner -d talkabout', '--health-interval=30s', '--health-timeout=5s', '--health-retries=3', images['postgres']['reference'], '-c', 'max_connections=30', '-c', 'shared_buffers=64MB']
    app = ['podman', 'run', '--detach', '--name', APP, *common, '--user=10001:10001', f'--publish=127.0.0.1:{host_port}:8080',
           '--health-cmd=python -m talkabout.container healthcheck', '--health-interval=30s', '--health-timeout=10s', '--health-retries=3',
           '--secret', 'talkabout-db-url,type=mount,target=talkabout-db-url,uid=10001,gid=10001,mode=0400', app_image]
    return {'status': 'PLAN_ONLY_NOT_EXECUTED', 'rootless_required': True,
            'collision_policy': 'Abort if any named resource exists; never replace/prune existing resources.',
            'required_existing_secrets': ['talkabout-db-password', 'talkabout-db-url'],
            'commands': [['podman', 'network', 'create', '--internal', '--label', LABEL, NETWORK],
                         ['podman', 'volume', 'create', '--label', LABEL, VOLUME], db, app]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--app-image', required=True)
    parser.add_argument('--host-port', type=int, default=8080)
    args = parser.parse_args()
    print(json.dumps(plan(args.app_image, args.host_port), indent=2))
