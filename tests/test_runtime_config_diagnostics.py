"""Offline contracts and deterministic EOF races; never launch a provider."""
import io
import json
from pathlib import Path
import threading
import tomllib
from types import SimpleNamespace

import pytest

from talkabout.adapters import AdapterError
from talkabout.agent_runtime import StructuredRuntime, _JSONLTransport, _safe_stderr_details


class PipeSupervisor:
    def __init__(self, stdout, stderr=b''):
        self.process = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(stdout), stderr=io.BytesIO(stderr))
    def start(self, *args, **kwargs):
        return self.process
    def stop(self):
        return True


def test_codex_overrides_match_pinned_0163_schema_contract():
    contract = json.loads((Path(__file__).parent / 'fixtures/codex_0163_overrides.json').read_text())
    runtime = StructuredRuntime('codex', executable='codex.exe')
    for session in [None, 'SYNTHETIC_SESSION_ID']:
        runtime.session_id = session
        command = runtime.command()
        values = [command[index + 1] for index, value in enumerate(command) if value == '-c']
        flattened = {}
        for setting in values:
            key, _ = setting.split('=', 1)
            value = tomllib.loads(setting)
            for part in key.split('.'):
                value = value[part]
            rule = contract['used_keys'][key]  # Unknown/legacy paths fail here.
            assert type(value).__name__ == {'boolean': 'bool', 'string': 'str'}[rule['type']]
            if 'enum' in rule:
                assert value in rule['enum']
            flattened[key] = value
        assert set(flattened) == set(contract['used_keys'])
        assert flattened['features.view_image'] is False and 'tools.view_image' not in flattened
        assert flattened['approval_policy'] == 'never' and flattened['web_search'] == 'disabled'
        assert flattened['skills.include_instructions'] is False
        assert all(value is (key == 'features.skip_host_skill_discovery')
                   for key, value in flattened.items() if key.startswith('features.'))
        assert '--ignore-user-config' in command and '--strict-config' in command
        assert command[command.index('--sandbox') + 1] == 'read-only' and command[-1] == '-'


@pytest.mark.parametrize('raw,expected', [
    # Exact public loader format from the pinned 0.163.0 source, not recovered stderr.
    (b'unknown configuration field `tools.view_image` in -c/--config override\n',
     ('unknown_field', 'tools.view_image', '', '')),
    (b'Error loading config: unknown field `view_image`\nin `tools`\n',
     ('unknown_field', 'tools.view_image', '', '')),
    (b'invalid type: string "SYNTHETIC_SECRET", expected a boolean\nin `features.view_image`\n',
     ('invalid_type', 'features.view_image', 'string', 'boolean')),
    (b'unknown variant `SYNTHETIC_SECRET`, expected one of `never`, `on-request`\nin `approval_policy`\n',
     ('invalid_enum', 'approval_policy', '', '')),
    (b"error: unexpected argument '--ignore-rules' found\n",
     ('invalid_option', '--ignore-rules', '', '')),
    (b"error: unrecognized option '--SYNTHETIC_SECRET'\n",
     ('invalid_option', '', '', '')),
    (b'unknown field `SYNTHETIC_SECRET`\nin `PRIVATE_SECRET_PATH`\n',
     ('unknown_field', '', '', '')),
])
def test_reported_config_details_only_return_allowlisted_fields(raw, expected):
    details = _safe_stderr_details(raw)
    assert expected in details
    assert 'synthetic_secret' not in json.dumps(sorted(details))
    assert 'private_secret_path' not in json.dumps(sorted(details))


def test_transport_handles_split_error_context_without_retaining_secret():
    stderr = b'x' * 4090 + b'unknown field `view_image`\nin `tools`\nSYNTHETIC_SECRET'
    transport = _JSONLTransport(PipeSupervisor(b'', stderr), ['synthetic'], '.', {}, threading.Event(), 1e20)
    transport.close()
    snapshot = transport.diagnostics()
    assert {'kind': 'unknown_field', 'key': 'tools.view_image'} in snapshot['stderr_reported_details']
    assert 'SYNTHETIC_SECRET' not in json.dumps(snapshot)
    assert len(snapshot['stderr_reported_details']) <= 32


def eof_race(monkeypatch, exit_at):
    clock = SimpleNamespace(now=100.0)
    def sleep(seconds):
        clock.now += seconds
    monkeypatch.setattr('talkabout.agent_runtime.time',
                        SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep))
    supervisor = PipeSupervisor(b'')
    supervisor.process.poll = lambda: 2 if clock.now >= exit_at else None
    transport = _JSONLTransport(supervisor, ['synthetic'], '.', {}, threading.Event(), 101)
    return clock, transport


def test_eof_observes_delayed_exit_but_never_accepts_a_completion(monkeypatch):
    clock, transport = eof_race(monkeypatch, 100.02)
    with pytest.raises(AdapterError, match='runtime_outcome_unknown'):
        transport.receive(threading.Event(), 101)
    transport.close()
    assert 100.02 <= clock.now <= 100.1
    assert transport.diagnostics()['native_exit_code_before_cleanup'] == 2
    assert transport.diagnostics()['stdout_frames'] == 0


def test_eof_exit_observation_stops_at_original_deadline(monkeypatch):
    clock, transport = eof_race(monkeypatch, 110)
    with pytest.raises(AdapterError, match='runtime_outcome_unknown'):
        transport.receive(threading.Event(), 100.01)
    transport.close()
    assert clock.now <= 100.01
    assert transport.diagnostics()['native_exit_code_before_cleanup'] is None


def test_eof_observation_remains_cancellable(monkeypatch):
    clock, transport = eof_race(monkeypatch, 110)
    cancel = threading.Event()
    transport.process.poll = lambda: cancel.set() or None
    with pytest.raises(AdapterError, match='runtime_cancelled'):
        transport.receive(cancel, 101)
    transport.close()
    assert clock.now == 100.0
