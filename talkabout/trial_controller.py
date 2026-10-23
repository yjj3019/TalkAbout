"""Operator diagnostic lifecycle; no credential issuance or implicit provider launch."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading

from .adapters import AdapterError
from .trial_observation import TrialRecord, safe_controller_failure, summarize_attempt


def run_diagnostic(runtime, context, directory, *, stream=None):
    """Run one supplied runtime, shut it down, and independently publish safe state.

    The directory must be new. Historical reservations cannot be reopened/reset.
    Supplying a real runtime requires separate operator authorization; this function
    does not issue tokens, configure authentication, or expand any runtime budget.
    """
    stream = stream if stream is not None else sys.stderr
    record = report_store = None
    first_observer_error = []
    error = cleanup_error = None
    answer = None
    operation = 'write'
    stage = None
    evidence = 'synthetic' if getattr(runtime, 'synthetic_evidence', False) is True else 'unverified_runtime'

    def observe(event):
        nonlocal stage
        stage = event
        try:
            record.observe(1, event)
        except Exception as caught:
            if not first_observer_error:
                first_observer_error.append((caught, event))
            raise

    def snapshot():
        return {'state': 'failed', 'outcome': 'unknown',
                'native_launch_state': getattr(runtime, 'native_launch_state', None)}

    def emit(report):
        print(json.dumps(report, allow_nan=False), file=stream, flush=True)

    try:
        directory = Path(directory)
        directory.mkdir()  # Never reuse or overwrite an earlier diagnostic.
        record = TrialRecord(directory, 'attempt.json')
        report_store = TrialRecord(directory, 'controller.json')
        for store, initial in [(record, {'calls': [{'ordinal': 1, 'state': 'reserved'}]}), (report_store, {})]:
            with store.locked(), store.path.open('x', encoding='utf-8') as target:
                json.dump(initial, target); target.flush(); os.fsync(target.fileno())
        if runtime.observer is not None:
            raise ValueError('diagnostic_observer_already_set')
        runtime.observer = observe
        operation = 'observer'
        answer = runtime.run(context, threading.Event())
    except Exception as caught:
        error = caught
    finally:
        try:
            if runtime.shutdown() is not True:
                raise AdapterError('runtime_cleanup_unconfirmed')
        except Exception as caught:
            if error is None:
                error = caught
            else:
                cleanup_error = safe_controller_failure(caught, 'observer', stage='cleanup_completed', attempt=snapshot())

    if error is None:
        try:
            def complete(value):
                row = value['calls'][0]
                row.update(state='completed', outcome=getattr(runtime, 'last_outcome', None),
                           native_launch_state=getattr(runtime, 'native_launch_state', None))
            record.update(complete)
            attempt = summarize_attempt(record.read()['calls'][0])
            if attempt['outcome'] != 'completed':
                raise AdapterError('runtime_outcome_unknown')
            report = {'status': 'completed', 'attempt': attempt, 'stage': stage,
                      'response_characters': len(answer), 'provider_attestation': False, 'evidence': evidence}
            report_store.update(lambda value: value.update(report))
            if report_store.read() != report:
                raise ValueError('diagnostic_report_not_confirmed')
            return report
        except Exception as caught:
            error = caught

    if first_observer_error:
        error, stage = first_observer_error[0]
    report = safe_controller_failure(error, operation, stage=stage, attempt=snapshot())
    report['evidence'] = evidence
    if cleanup_error is not None:
        report['controller_cleanup_failure'] = cleanup_error
    # Emit before touching the independent sink, which may fail too.
    emit(report)
    if report_store is not None:
        try:
            report_store.update(lambda value: value.update(report))
            if report_store.read() != report:
                raise ValueError('diagnostic_report_not_confirmed')
        except Exception as caught:
            secondary = safe_controller_failure(caught, 'write', stage=stage, attempt=snapshot())
            emit({'independent_report_failure': secondary})
            report['independent_report_failure'] = secondary
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='Provider-free diagnostic controller check')
    parser.add_argument('--synthetic', action='store_true', required=True)
    parser.add_argument('--directory', type=Path, required=True, help='New owned result directory')
    args = parser.parse_args(argv)
    from .agent_runtime import StructuredRuntime
    class SyntheticRuntime(StructuredRuntime):
        synthetic_evidence = True
        def _ensure_preflight(self, cancel): pass
        def probe(self): return {'available': True, 'reason': None}
        def _check_codex_exec_auth(self, cancel, supervisor): pass
        def _environment(self):
            return {key: value for key, value in os.environ.items()
                    if key.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP'}}
        def command(self):
            events = [{'type': 'thread.started', 'thread_id': 'synthetic-thread'}, {'type': 'turn.started'},
                      {'type': 'item.completed', 'item': {'type': 'agent_message', 'id': 'synthetic-message', 'text': 'Synthetic diagnostic only.'}},
                      {'type': 'turn.completed'}]
            program = 'import sys,json;sys.stdin.buffer.read();events=' + repr(events) + ';[print(json.dumps(event),flush=True) for event in events]'
            return [sys._base_executable, '-I', '-S', '-u', '-c', program]
    runtime = SyntheticRuntime('codex', executable=sys._base_executable)
    report = run_diagnostic(runtime, {'room_id': 'synthetic-only', 'purpose': 'connection_test'}, args.directory)
    print(json.dumps({**report, 'verification': 'SYNTHETIC_ONLY_NOT_PROVIDER_CONNECTION'}), flush=True)
    return 0 if report['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
