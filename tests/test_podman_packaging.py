import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from psycopg.conninfo import conninfo_to_dict

from talkabout.config import database_config
from talkabout.container import read_secret, healthcheck

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('podman_plan', ROOT/'deploy/podman/plan.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_separate_db_secret_requires_exact_private_host(tmp_path):
    secret = tmp_path/'fixture-secret'
    secret.write_text('host=talkabout-db port=5432 user=talkabout_app dbname=talkabout password=synthetic-only')
    assert conninfo_to_dict(read_secret(secret))['host'] == 'talkabout-db'
    with pytest.raises(ValueError, match='loopback_database_host_required'):
        database_config(secret.read_text())
    for value in ('host=external.invalid user=x dbname=x', 'host=talkabout-db port=5433 user=x dbname=x', 'host=talkabout-db user=talkabout_owner dbname=talkabout', 'host=talkabout-db hostaddr=203.0.113.1 user=talkabout_app dbname=talkabout', 'x'*4097):
        secret.write_text(value)
        with pytest.raises(ValueError, match='container_database_secret_invalid') as error:
            read_secret(secret)
        assert value not in str(error.value)
    secret.write_text('host=talkabout-db user=talkabout_owner dbname=talkabout password=synthetic-only')
    assert conninfo_to_dict(read_secret(secret, initialize=True))['user'] == 'talkabout_owner'


def test_missing_secret_and_health_failure_are_sanitized(tmp_path):
    with pytest.raises(ValueError, match='container_database_secret_invalid'):
        read_secret(tmp_path/'missing')
    with patch('urllib.request.build_opener', side_effect=RuntimeError('synthetic secret must not print')):
        assert healthcheck() == 1


def test_plan_never_executes_and_limits_names_network_and_mounts():
    plan = module.plan('localhost/talkabout:'+'a'*40)
    assert plan['status'] == 'PLAN_ONLY_NOT_EXECUTED' and plan['rootless_required']
    network, volume, db, app = plan['commands']
    assert '--internal' in network and module.NETWORK in network and module.VOLUME in volume
    for command in (db, app):
        assert '--cap-drop=all' in command and '--security-opt=no-new-privileges' in command
        assert '--read-only' in command and '--memory=512m' in command and '--pids-limit=128' in command
        assert not any('privileged' in a or 'network=host' in a or 'podman.sock' in a or 'docker.sock' in a for a in command)
    assert '--publish=127.0.0.1:8080:8080' in app
    assert not any('publish' in a for a in db)
    assert '--user=10001:10001' in app and '--user=999:999' in db
    assert module.VOLUME+':/var/lib/postgresql:U' in db
    assert 'POSTGRES_PASSWORD_FILE=/run/secrets/pg-password' in db
    assert not any(a.startswith('POSTGRES_PASSWORD=') for a in db)
    assert '--publish=127.0.0.1:18080:8080' in module.plan('localhost/talkabout:'+'a'*40, 18080)['commands'][-1]
    with pytest.raises(ValueError, match='unprivileged_host_port_required'):
        module.plan('localhost/talkabout:'+'a'*40, 80)


def test_official_images_and_build_context_match_lock():
    images = json.loads((ROOT/'deploy/podman/images.lock.json').read_text())['images']
    containerfile = (ROOT/'Containerfile').read_text()
    assert containerfile.count(images['python']['reference']) == 2
    assert images['postgres']['reference'] in (ROOT/'deploy/podman/quadlet/talkabout-db.container.example').read_text()
    assert 'USER 10001:10001' in containerfile and 'HEALTHCHECK ' in containerfile
    assert 'COPY . ' not in containerfile
    assert [line for line in containerfile.splitlines() if line.startswith('ARG ')] == ['ARG SOURCE_SHA']
    ignore = (ROOT/'.containerignore').read_text().splitlines()
    assert ignore[0] == '**' and '!talkabout/*.py' in ignore
    assert not any(line.startswith('!') and any(x in line for x in ('.git', '.env', 'tests', 'secrets')) for line in ignore)
    for name in ('python', 'postgres'):
        assert images[name]['reference'].startswith('docker.io/library/')
        assert len(images[name]['digest'].split(':')[1]) == 64


def test_quadlet_is_reference_only_without_automatic_boot_activation():
    for path in (ROOT/'deploy/podman/quadlet').glob('*.example'):
        text = path.read_text()
        assert '[Install]' not in text and 'WantedBy=' not in text
        assert 'io.talkabout.project=talkabout' in text
        assert 'Network=host' not in text and 'SecurityLabelDisable' not in text
    with pytest.raises(ValueError, match='source_sha_image_tag_required'):
        module.plan('localhost/talkabout:latest')


def test_documented_build_supplies_revision_argument_without_external_label_injection():
    import shlex
    sha='a'*40
    commands=[line for line in (ROOT/'deploy/podman/README.md').read_text(encoding='utf-8').splitlines() if line.startswith('podman build ')]
    assert len(commands)==1
    argv=shlex.split(commands[0].replace('$SOURCE_SHA',sha))
    assert argv[:2]==['podman','build'] and '--label' not in argv
    assert argv[argv.index('--build-arg')+1]=='SOURCE_SHA='+sha
    assert argv[argv.index('--tag')+1]=='localhost/talkabout:'+sha
    assert argv[argv.index('--file')+1]=='Containerfile' and argv[-1]=='.'
    containerfile=(ROOT/'Containerfile').read_text()
    runtime=containerfile.split(' AS build',1)[1].split('FROM ',1)[1]
    assert 'ARG SOURCE_SHA' in runtime and 'LABEL org.opencontainers.image.revision="${SOURCE_SHA}"' in runtime
    assert 'test "${#SOURCE_SHA}" -eq 40' in runtime
