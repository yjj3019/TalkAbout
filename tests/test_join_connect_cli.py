"""Proposed combined join/connect contract; all provider and API activity is synthetic.

Assumptions: --room is optional on connect; when supplied it requires the
private acknowledgement. A stopped or leased connector must not accept an
invitation, and static connect validation happens before any membership or
local stop-state mutation. Standalone join's recovery semantics are unchanged.
"""
import sys

import pytest

from talkabout import cli
from talkabout.connector import ConnectorLease
from talkabout.worker import Journal
from tests.test_continuity_cli import configured


@pytest.fixture
def scenario(fixture, tmp_path, monkeypatch):
    store, core, accepted_room = fixture
    config, runtimes = configured(fixture, tmp_path, monkeypatch)
    room = core.create('fixture-human', {'topic': 'Synthetic invitation'}, 'combined-room')
    invitation = core.mutation('fixture-human', room['id'], 'memberships', 'combined-invite',
                               {'principal_id': 'a', 'permissions': ['room:read'], 'status': 'pending'})
    events = []
    make_client = cli.APIClient

    def client(*args):
        value = make_client(*args)
        request = value.request

        def tracked(method, path, body=None, key=None, params=None):
            events.append((method, path, body, key))
            return request(method, path, body, key, params)

        value.request = tracked
        return value

    monkeypatch.setattr(cli, 'APIClient', client)
    return config, runtimes, events, room['id'], invitation['revision'], accepted_room


def invoke(monkeypatch, config, room, *extra):
    monkeypatch.setattr(sys, 'argv', ['talkabout', 'connect', '--config', str(config),
                                     '--runtime', 'codex', '--room', room, *extra])
    return cli.main()


def accepts(events):
    return [event for event in events if event[0] == 'POST' and event[1].endswith('/memberships/me/accept')]


def test_pending_invitation_is_accepted_before_provider_check(scenario, monkeypatch):
    config, runtimes, events, room, revision, _ = scenario
    invoke(monkeypatch, config, room, '--acknowledge-private', '--approve-external')
    accepted = accepts(events)
    assert len(accepted) == 1
    assert accepted[0][1] == f'rooms/{room}/memberships/me/accept'
    assert accepted[0][2] == {'expected_revision': revision, 'acknowledge_private': True}
    assert accepted[0][3]
    check = next(i for i, event in enumerate(events) if event[1] == 'agents/me/runtime-checks')
    assert events.index(accepted[0]) < check
    assert sum(runtime.calls for runtime in runtimes) == 1


def test_already_accepted_room_connects_without_second_accept(scenario, monkeypatch):
    config, runtimes, events, _, _, room = scenario
    invoke(monkeypatch, config, room, '--acknowledge-private', '--approve-external')
    assert not accepts(events)
    assert sum(runtime.calls for runtime in runtimes) == 1


def test_prompt_credential_once_for_both_phases(scenario, monkeypatch, capsys):
    config, runtimes, events, room, _, _ = scenario
    prompts = []
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(sys.stdout, 'isatty', lambda: True)
    monkeypatch.setattr(cli.getpass, 'getpass', lambda prompt: prompts.append(prompt) or 'fixture-a')
    invoke(monkeypatch, config, room, '--acknowledge-private', '--approve-external', '--prompt-token')
    assert len(prompts) == 1 and len(accepts(events)) == 1
    assert sum(runtime.calls for runtime in runtimes) == 1
    output = capsys.readouterr()
    assert 'fixture-a' not in output.out + output.err


@pytest.mark.parametrize('flags', [
    ['--approve-external'],
    ['--acknowledge-private'],
    ['--acknowledge-private', '--approve-external', '--steps', '0'],
    ['--acknowledge-private', '--approve-external', '--max-model-calls', '0'],
    ['--acknowledge-private', '--approve-external', '--max-room-calls', '11'],
    ['--acknowledge-private', '--approve-external', '--codex-transport', 'app-server'],
    ['--acknowledge-private', '--approve-external', '--work-profile'],
])
def test_static_validation_precedes_any_mutation(scenario, monkeypatch, flags):
    config, runtimes, events, room, _, _ = scenario
    # An invalid invocation must not clear an existing stop via --resume-worker.
    journal = Journal(config.parent / 'worker.sqlite')
    with journal.db:
        journal.set_setting('stop', True)
    journal.close()
    with pytest.raises(SystemExit):
        invoke(monkeypatch, config, room, '--resume-worker', *flags)
    assert not [event for event in events if event[0] in ('POST', 'PUT', 'PATCH', 'DELETE')]
    assert not runtimes
    journal = Journal(config.parent / 'worker.sqlite')
    try:
        assert journal.stop_requested()
    finally:
        journal.close()


def test_missing_invitation_fails_before_provider(scenario, monkeypatch):
    config, runtimes, events, _, _, _ = scenario
    with pytest.raises(SystemExit):
        invoke(monkeypatch, config, 'missing-room', '--acknowledge-private', '--approve-external')
    assert not accepts(events)
    assert all(runtime.calls == 0 for runtime in runtimes)


def test_accept_failure_never_constructs_provider(scenario, monkeypatch):
    config, runtimes, events, room, _, _ = scenario
    make_client = cli.APIClient

    def failing_client(*args):
        value = make_client(*args)
        request = value.request

        def request_or_fail(method, path, body=None, key=None, params=None):
            if path.endswith('/memberships/me/accept'):
                events.append((method, path, body, key))
                raise RuntimeError('synthetic accept failure')
            return request(method, path, body, key, params)

        value.request = request_or_fail
        return value

    monkeypatch.setattr(cli, 'APIClient', failing_client)
    with pytest.raises((SystemExit, RuntimeError)):
        invoke(monkeypatch, config, room, '--acknowledge-private', '--approve-external')
    assert len(accepts(events)) == 1
    assert all(runtime.calls == 0 for runtime in runtimes)
    assert not any(event[1] == 'agents/me/runtime-checks' for event in events)


def test_held_connector_lease_prevents_accept_and_provider(scenario, monkeypatch):
    config, runtimes, events, room, _, _ = scenario
    lease = ConnectorLease(config.parent / 'worker.sqlite').acquire()
    try:
        with pytest.raises(SystemExit):
            invoke(monkeypatch, config, room, '--acknowledge-private', '--approve-external')
        assert not accepts(events) and not runtimes
    finally:
        lease.close()


def test_delayed_private_input_does_not_consume_run_deadline(scenario, monkeypatch):
    import time
    config, runtimes, events, room, _, _ = scenario
    real_monotonic = time.monotonic
    offset = [0]
    monkeypatch.setattr(cli.time, 'monotonic', lambda: real_monotonic() + offset[0])
    def prompt(parser):
        offset[0] = 600
        return 'fixture-a'
    monkeypatch.setattr(cli, 'private_credential', prompt)
    invoke(monkeypatch, config, room, '--acknowledge-private', '--approve-external', '--prompt-token')
    assert len(accepts(events)) == 1 and sum(runtime.calls for runtime in runtimes) == 1


def test_combined_preflight_failure_is_sanitized_before_accept(scenario, monkeypatch, capsys):
    from talkabout.adapters import AdapterError
    config, runtimes, events, room, _, _ = scenario
    def provider(*args, **kwargs):
        class Unsupported:
            def probe(self): return {'available': True, 'reason': None}
            def preflight(self): raise AdapterError('runtime_version_unsupported')
            def shutdown(self): return True
        return Unsupported()
    monkeypatch.setattr(cli, 'provider_runtime', provider)
    with pytest.raises(SystemExit, match='runtime_version_unsupported'):
        invoke(monkeypatch, config, room, '--acknowledge-private', '--approve-external')
    assert not accepts(events)
    assert 'Traceback' not in capsys.readouterr().err


def test_lost_accept_response_is_unconfirmed_then_rerun_reconciles(scenario, monkeypatch, capsys):
    config, runtimes, events, room, _, _ = scenario
    factory = cli.APIClient
    def lost_client(*args):
        client = factory(*args)
        request = client.request
        def lost(method, path, body=None, key=None, params=None):
            result = request(method, path, body, key, params)
            if path.endswith('/memberships/me/accept'):
                raise OSError('synthetic lost response')
            return result
        client.request = lost
        return client
    monkeypatch.setattr(cli, 'APIClient', lost_client)
    with pytest.raises(SystemExit, match='room_accept_unconfirmed'):
        invoke(monkeypatch, config, room, '--acknowledge-private', '--approve-external')
    assert len(accepts(events)) == 1 and all(runtime.calls == 0 for runtime in runtimes)
    monkeypatch.setattr(cli, 'APIClient', factory)
    invoke(monkeypatch, config, room, '--acknowledge-private', '--approve-external')
    assert len(accepts(events)) == 1
    assert 'already_accepted' in capsys.readouterr().out
    assert sum(runtime.calls for runtime in runtimes) == 1


def test_room_scoped_credential_completes_combined_join(fixture, tmp_path, monkeypatch, capsys):
    from talkabout.domain import AGENT_PERMISSIONS, SELF_PERMISSIONS
    from talkabout.identity import IdentityService
    from tests.test_connector_verification import LocalClient
    store, core, _ = fixture
    config, runtimes = configured(fixture, tmp_path, monkeypatch)
    room = core.create('fixture-human', {'topic': 'Scoped invitation'}, 'scoped-combined-room')
    core.mutation('fixture-human', room['id'], 'memberships', 'scoped-combined-invite',
                  {'principal_id': 'a', 'permissions': sorted(AGENT_PERMISSIONS), 'status': 'pending'})
    credential = IdentityService(store).mutate('fixture-human', 'credentials/a', 'scoped-combined-key',
        {'room_id': room['id'], 'scopes': sorted(AGENT_PERMISSIONS | SELF_PERMISSIONS), 'expires_in': 300})['token']
    monkeypatch.setattr(cli, 'private_credential', lambda parser: credential)
    def make_client(base, token, origin):
        client = LocalClient(store, token); client.close = client.http.close; return client
    monkeypatch.setattr(cli, 'APIClient', make_client)
    invoke(monkeypatch, config, room['id'], '--acknowledge-private', '--approve-external', '--prompt-token')
    assert core.read('fixture-human', room['id'])['members']['a']['status'] == 'active'
    assert sum(runtime.calls for runtime in runtimes) == 1
    output = capsys.readouterr()
    assert credential not in output.out + output.err
    journal = Journal(config.parent / 'worker.sqlite')
    try:
        assert credential not in str(journal.db.execute('select k,v from settings').fetchall())
    finally:
        journal.close()


def test_stop_fences_combined_operation_without_clearing_stop(scenario, monkeypatch):
    config, runtimes, events, room, _, _ = scenario
    journal = Journal(config.parent / 'worker.sqlite')
    with journal.db:
        journal.set_setting('stop', True)
        journal.set_setting('stop_state', 'stop_requested')
    journal.close()
    with pytest.raises(SystemExit):
        invoke(monkeypatch, config, room, '--acknowledge-private', '--approve-external')
    assert not accepts(events)
    assert sum(runtime.calls for runtime in runtimes) == 0
    journal = Journal(config.parent / 'worker.sqlite')
    try:
        assert journal.stop_requested() and journal.setting('stop_state') == 'stop_requested'
    finally:
        journal.close()
