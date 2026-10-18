"""Same selected controller from reservation through cleanup/report; no tokens."""
import io
import json
from pathlib import Path

import pytest

from talkabout.trial_controller import main, run_diagnostic


class SyntheticRuntime:
    synthetic_evidence = True
    observer = None
    native_launch_state = 'not_requested'
    last_outcome = 'unknown'
    starts = 0
    stopped = False
    def run(self, context, cancel):
        self.observer('workspace_preparing')
        self.native_launch_state = 'requested';self.observer('native_start_requested')
        self.native_launch_state = 'started';self.starts += 1;self.observer('native_started')
        self.observer('prompt_submitted');self.observer('native_result_observed')
        self.last_outcome = 'completed'
        return 'Synthetic answer'
    def shutdown(self): self.stopped = True;return True


def test_lifecycle_reserves_observes_closes_and_rereads_independent_report(tmp_path):
    runtime = SyntheticRuntime();directory = tmp_path/'new'
    report = run_diagnostic(runtime, {}, directory)
    assert report['status'] == 'completed' and report['attempt']['outcome'] == 'completed'
    assert report['attempt']['native_process_starts'] == 1 and runtime.stopped
    assert json.loads((directory/'controller.json').read_text()) == report
    assert json.loads((directory/'attempt.json').read_text())['calls'][0]['observations'][-1] == 'native_result_observed'


@pytest.mark.parametrize('fail_at,count', [('workspace_preparing',0),('native_started',1)])
def test_same_controller_retains_first_observer_failure_and_stops(tmp_path,monkeypatch,fail_at,count):
    from talkabout.trial_observation import TrialRecord
    runtime = SyntheticRuntime();directory = tmp_path/'new';stream = io.StringIO()
    original = TrialRecord.observe
    def observe(store, ordinal, stage):
        if stage == fail_at:
            def denied(*args):
                error = PermissionError(13,'PRIVATE_SENTINEL','PRIVATE_SENTINEL_PATH');error.winerror = 32;raise error
            with monkeypatch.context() as patch:
                patch.setattr('talkabout.trial_observation.os.replace',denied)
                return original(store,ordinal,stage)
        return original(store,ordinal,stage)
    monkeypatch.setattr(TrialRecord,'observe',observe)
    report = run_diagnostic(runtime, {}, directory, stream=stream)
    assert report['status'] == 'stopped' and runtime.starts == count and runtime.stopped
    assert report['failure']['operation'] == 'replace' and report['failure']['winerror'] == 32
    assert report['stage'] == fail_at
    assert report['attempt']['native_process_starts'] == count
    assert report['attempt']['outcome'] == 'unknown'
    assert 'PRIVATE_SENTINEL' not in stream.getvalue()
    assert json.loads((directory/'controller.json').read_text()) == report


def test_independent_sink_failure_keeps_safe_primary_stderr(tmp_path,monkeypatch):
    runtime = SyntheticRuntime();stream = io.StringIO()
    def denied(*args):raise PermissionError(13,'PRIVATE_SENTINEL','PRIVATE_SENTINEL_PATH')
    monkeypatch.setattr('talkabout.trial_observation.os.replace',denied)
    report = run_diagnostic(runtime, {}, tmp_path/'new', stream=stream)
    rows = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert rows[0]['failure'] == report['failure'] and report['status'] == 'stopped'
    assert 'independent_report_failure' in rows[1]
    assert report['attempt']['outcome'] == 'unknown' and runtime.stopped


def test_existing_result_directory_is_never_reopened_or_changed(tmp_path):
    path = tmp_path/'historical';path.mkdir();record = path/'attempt.json';record.write_text('preserved')
    runtime = SyntheticRuntime()
    report = run_diagnostic(runtime, {}, path, stream=io.StringIO())
    assert report['status'] == 'stopped' and runtime.starts == 0 and runtime.stopped
    assert record.read_text() == 'preserved'


@pytest.mark.skipif(__import__('os').name != 'nt',reason='Synthetic native Windows containment')
def test_selected_cli_uses_same_controller_with_actual_python_jsonl_child(tmp_path,capsys):
    assert main(['--synthetic','--directory',str(tmp_path/'selected')]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['evidence'] == 'synthetic'
    assert report['verification'] == 'SYNTHETIC_ONLY_NOT_PROVIDER_CONNECTION'
    assert report['status'] == 'completed' and not report['provider_attestation']


def test_selected_cli_has_no_implicit_actual_runtime_mode(tmp_path):
    with pytest.raises(SystemExit):main(['--directory',str(tmp_path/'denied')])
    assert not (tmp_path/'denied').exists()
