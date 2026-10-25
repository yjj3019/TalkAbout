"""CLI gates for the work profile: the flags are explicit and refused when incomplete."""
import sys

import pytest

from talkabout import cli
from tests.test_continuity_cli import configured


def invoke(monkeypatch, extra):
    monkeypatch.setattr(sys, 'argv', ['talkabout', 'connect', *extra])
    cli.main()


def base(config, runtime='codex'):
    return ['--config', str(config), '--runtime', runtime, '--approve-external',
            '--steps', '5', '--max-seconds', '10']


def test_work_profile_without_workspace_is_refused(fixture, tmp_path, monkeypatch):
    config, _ = configured(fixture, tmp_path, monkeypatch)
    with pytest.raises(SystemExit):
        invoke(monkeypatch, base(config) + ['--work-profile'])


def test_workspace_without_work_profile_is_refused(fixture, tmp_path, monkeypatch):
    config, _ = configured(fixture, tmp_path, monkeypatch)
    with pytest.raises(SystemExit):
        invoke(monkeypatch, base(config) + ['--work-workspace', str(tmp_path)])


def test_work_profile_with_another_provider_is_refused(fixture, tmp_path, monkeypatch):
    config, _ = configured(fixture, tmp_path, monkeypatch)
    with pytest.raises(SystemExit):
        invoke(monkeypatch, base(config, runtime='claude')
               + ['--work-profile', '--work-workspace', str(tmp_path)])


def test_work_profile_is_forwarded_to_the_runtime(fixture, tmp_path, monkeypatch):
    config, made = configured(fixture, tmp_path, monkeypatch)
    invoke(monkeypatch, base(config) + ['--work-profile', '--work-workspace', str(tmp_path)])
    assert made, 'a runtime should have been built'
    options = made[0].options
    assert options['work_profile'] is True
    assert options['work_workspace'] == str(tmp_path)


def test_default_profile_does_not_set_work_options(fixture, tmp_path, monkeypatch):
    config, made = configured(fixture, tmp_path, monkeypatch)
    invoke(monkeypatch, base(config))
    assert made and 'work_profile' not in made[0].options
