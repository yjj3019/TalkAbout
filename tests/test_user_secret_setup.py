import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from psycopg.conninfo import conninfo_to_dict

spec = importlib.util.spec_from_file_location('user_secret_setup', Path(__file__).resolve().parents[1]/'deploy/podman/secret_setup.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_dsn_quotes_special_user_input_without_printing():
    value = "synthetic password's \\ value"
    fields = conninfo_to_dict(module.dsn('talkabout_app', value))
    assert fields['password'] == value and fields['host'] == 'talkabout-db'


def test_no_secret_creation_without_a_direct_tty():
    with patch('sys.stdin.isatty', return_value=False), patch('getpass.getpass') as prompt:
        with pytest.raises(ValueError, match='interactive_user_terminal_required'):
            module.create_user_secrets()
        prompt.assert_not_called()


def test_existing_secrets_preserved_without_prompt_or_rotation():
    def runner(args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=b'true' if args[1]=='info' else b'[]')
    with patch('sys.stdin.isatty', return_value=True), patch('sys.stderr.isatty', return_value=True), patch('getpass.getpass') as prompt:
        with pytest.raises(ValueError, match='existing_secrets_preserved'):
            module.create_user_secrets(runner)
        prompt.assert_not_called()


def test_only_user_supplied_values_go_to_stdin_never_argv_or_output(capsys):
    calls=[]
    def runner(args, **kwargs):
        calls.append((args,kwargs))
        return SimpleNamespace(returncode=1 if args[1:3]==['secret','inspect'] else 0, stdout=b'true')
    owner, app = 'synthetic-owner-password', 'synthetic-runtime-password'
    with patch('sys.stdin.isatty', return_value=True), patch('sys.stderr.isatty', return_value=True), patch('getpass.getpass', side_effect=[owner,owner,app,app]):
        module.create_user_secrets(runner)
    output=capsys.readouterr()
    assert owner not in output.out+output.err and app not in output.out+output.err
    creates=[(args,kw) for args,kw in calls if args[1:3]==['secret','create']]
    assert len(creates)==3 and all('input' in kw for _,kw in creates)
    assert all(owner not in str(args) and app not in str(args) and '--replace' not in args for args,_ in creates)


def test_owner_and_runtime_passwords_must_differ_before_any_creation(capsys):
    calls=[]
    def runner(args,**kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1 if args[1:3]==['secret','inspect'] else 0,stdout=b'true')
    value='synthetic-identical-password'
    with patch('sys.stdin.isatty',return_value=True),patch('sys.stderr.isatty',return_value=True),patch('getpass.getpass',side_effect=[value]*4):
        with pytest.raises(ValueError,match='distinct_database_passwords_required'):
            module.create_user_secrets(runner)
    assert not any(args[1:3]==['secret','create'] for args in calls)
    output=capsys.readouterr()
    assert value not in output.out+output.err
