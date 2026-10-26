"""CLI storage is explicit; synthetic secrets never leave private input/API memory."""
import json
import sys
from types import SimpleNamespace

import pytest

from talkabout import cli


class StubVault:
    calls = []
    error = None

    def __init__(self, root):
        self.root = root

    def save(self, profile, token, binding):
        self.calls.append(('save', profile, token, binding))

    def load(self, profile, binding):
        self.calls.append(('load', profile, binding))
        if self.error:
            raise self.error
        return 'synthetic-vault-token'

    def list_profiles(self):
        return [{'profile': 'fixture', 'version': 1}]

    def delete(self, profile):
        self.calls.append(('delete', profile))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from talkabout import credential_vault
    StubVault.calls = []
    StubVault.error = None
    monkeypatch.setattr(credential_vault, 'CredentialVault', StubVault)
    config = tmp_path / 'agent.json'
    config.write_text(json.dumps({'base_url': 'https://fixture.invalid',
        'allowed_origin': 'https://fixture.invalid', 'journal_path': str(tmp_path / 'journal.sqlite')}))
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(sys.stdout, 'isatty', lambda: True)
    monkeypatch.setattr(cli.getpass, 'getpass', lambda _: 'synthetic-private-token')
    return config, tmp_path


def invoke(monkeypatch, *args):
    monkeypatch.setattr(sys, 'argv', ['talkabout', *map(str, args)])
    return cli.main()


def test_save_uses_hidden_input_and_never_prints_token(setup, monkeypatch, capsys):
    config, root = setup
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(sys.stdout, 'isatty', lambda: True)
    invoke(monkeypatch, 'vault', 'save', '--config', config, '--vault-dir', root, '--profile', 'fixture')
    assert StubVault.calls[0][:3] == ('save', 'fixture', 'synthetic-private-token')
    output = capsys.readouterr()
    assert 'synthetic-private-token' not in output.out + output.err
    assert json.loads(output.out)['vault'] == 'saved'
    assert not (root / 'journal.sqlite').exists()


@pytest.mark.parametrize('value', [None, [], 7, 'bad', {}])
def test_malformed_config_shape_is_sanitized(setup, monkeypatch, capsys, value):
    config, root = setup
    config.write_text(json.dumps(value))
    with pytest.raises(SystemExit):
        invoke(monkeypatch, 'vault', 'save', '--config', config, '--vault-dir', root, '--profile', 'fixture')
    output = capsys.readouterr()
    assert 'vault_configuration_invalid' in output.err
    assert 'Traceback' not in output.err
    assert not StubVault.calls


def test_list_and_delete_need_no_config_or_token(setup, monkeypatch, capsys):
    _, root = setup
    monkeypatch.setattr(cli.getpass, 'getpass', lambda _: pytest.fail('must not prompt'))
    invoke(monkeypatch, 'vault', 'list', '--vault-dir', root)
    assert json.loads(capsys.readouterr().out)['profiles'] == [{'profile': 'fixture', 'version': 1}]
    invoke(monkeypatch, 'vault', 'delete', '--vault-dir', root, '--profile', 'fixture')
    assert ('delete', 'fixture') in StubVault.calls


def test_save_refuses_noninteractive_input(setup, monkeypatch, capsys):
    config, root = setup
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: False)
    with pytest.raises(SystemExit):
        invoke(monkeypatch, 'vault', 'save', '--config', config, '--vault-dir', root, '--profile', 'fixture')
    assert not StubVault.calls
    assert 'synthetic-private-token' not in capsys.readouterr().err


def test_explicit_vault_load_does_not_fall_back_to_environment(setup, monkeypatch):
    from talkabout.credential_vault import VaultError
    config, root = setup
    parser = __import__('argparse').ArgumentParser()
    args = SimpleNamespace(vault_profile='fixture', vault_dir=root)
    monkeypatch.setenv('TALKABOUT_TOKEN', 'synthetic-environment-token')
    value = cli.agent_credential(args, json.loads(config.read_text()), parser)
    assert value == 'synthetic-vault-token'
    StubVault.error = VaultError('vault_decrypt_failed')
    with pytest.raises(SystemExit):
        cli.agent_credential(args, json.loads(config.read_text()), parser)


@pytest.mark.parametrize('extra', [[], ['--prompt-token'], ['--vault-profile', 'fixture']])
def test_default_and_prompt_do_not_create_vault(setup, monkeypatch, extra):
    config, root = setup
    if extra == ['--vault-profile', 'fixture']:
        with pytest.raises(SystemExit):
            invoke(monkeypatch, 'check', '--config', config, *extra)
        assert not StubVault.calls
        return
    monkeypatch.setenv('TALKABOUT_TOKEN', 'synthetic-environment-token')
    class Client:
        def __init__(self, *args): self.token = args[1]
        def request(self, *_): return {'id': 'a', 'kind': 'agent'}
        def close(self): pass
    monkeypatch.setattr(cli, 'APIClient', Client)
    invoke(monkeypatch, 'check', '--config', config, *extra)
    assert not StubVault.calls


def test_hidden_input_failure_never_falls_back_to_echo(setup, monkeypatch):
    import warnings
    config, root = setup
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(sys.stdout, 'isatty', lambda: True)
    def unavailable(_):
        warnings.warn('private input unavailable', cli.getpass.GetPassWarning)
        pytest.fail('must abort before fallback reads')
    monkeypatch.setattr(cli.getpass, 'getpass', unavailable)
    with pytest.raises(SystemExit):
        invoke(monkeypatch, 'vault', 'save', '--config', config, '--vault-dir', root, '--profile', 'fixture')
    assert not StubVault.calls


def test_check_passes_vault_token_only_to_api_memory(setup, monkeypatch):
    config, root = setup
    monkeypatch.setenv('TALKABOUT_TOKEN', 'synthetic-unrelated-environment-token')
    observed = []
    class Client:
        def __init__(self, base, token, origin): observed.append(token)
        def request(self, *_): return {'id': 'a', 'kind': 'agent'}
        def close(self): pass
    monkeypatch.setattr(cli, 'APIClient', Client)
    invoke(monkeypatch, 'check', '--config', config, '--vault-profile', 'fixture', '--vault-dir', root)
    assert observed == ['synthetic-vault-token']
    assert cli.os.environ['TALKABOUT_TOKEN'] == 'synthetic-unrelated-environment-token'
    assert 'synthetic-vault-token' not in config.read_text()


def test_prompt_and_vault_are_mutually_exclusive(setup, monkeypatch):
    config, root = setup
    with pytest.raises(SystemExit):
        invoke(monkeypatch, 'check', '--config', config, '--prompt-token',
               '--vault-profile', 'fixture', '--vault-dir', root)
    assert not StubVault.calls
