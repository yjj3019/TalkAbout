"""The stop escort must be bounded: a shutdown nobody can prove is unconfirmed.

These tests never start a provider process. They prove the caller returns, not that
any remote side effect was recalled.
"""
import threading
import time

import pytest

import talkabout.runtime as runtime_module
from talkabout.runtime import RuntimeTimeout, bounded_call, bounded_shutdown


class HangingShutdown:
    def __init__(self):
        self.release = threading.Event()
        self.entered = threading.Event()

    def __call__(self):
        self.entered.set()
        self.release.wait(30)
        return True


class HangingRuntime:
    def __init__(self):
        self.release = threading.Event()
        self.shutdown_calls = 0

    def run(self, context, cancel):
        self.release.wait(30)
        return 'late synthetic result'

    def shutdown(self):
        self.shutdown_calls += 1
        self.release.wait(30)
        return True


def test_hanging_shutdown_returns_false_within_the_bound():
    hanging = HangingShutdown()
    started = time.monotonic()
    try:
        assert bounded_shutdown(hanging, 0.1) is False
        assert hanging.entered.wait(2)
        assert time.monotonic() - started < 2.0
    finally:
        hanging.release.set()


def test_shutdown_exception_is_unconfirmed_not_raised():
    def broken():
        raise RuntimeError('fixture shutdown failure')
    assert bounded_shutdown(broken, 0.5) is False


def test_shutdown_truthy_is_confirmed():
    assert bounded_shutdown(lambda: True, 0.5) is True


def test_shutdown_false_is_unconfirmed():
    assert bounded_shutdown(lambda: False, 0.5) is False


def test_escort_bound_is_read_at_call_time(monkeypatch):
    monkeypatch.setattr(runtime_module, 'SHUTDOWN_ESCORT_SECONDS', 0.05)
    hanging = HangingShutdown()
    try:
        started = time.monotonic()
        assert bounded_shutdown(hanging) is False
        assert time.monotonic() - started < 2.0
    finally:
        hanging.release.set()


def test_bounded_call_returns_when_shutdown_never_returns(monkeypatch):
    monkeypatch.setattr(runtime_module, 'SHUTDOWN_ESCORT_SECONDS', 0.1)
    runtime = HangingRuntime()
    cancel = threading.Event()
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeTimeout) as error:
            bounded_call(runtime, {}, cancel, 0.05)
        elapsed = time.monotonic() - started
        # The turn budget expired long ago; the escort bound is the only remaining wait.
        assert elapsed < 3.0
        assert error.value.runtime_stopped is False
        assert cancel.is_set()
        assert runtime.shutdown_calls == 1
    finally:
        runtime.release.set()


def test_cooperative_runtime_cancel_is_stopped_not_uncertain():
    cancel = threading.Event()
    started = threading.Event()
    class Cooperative:
        def run(self, context, signal):
            started.set()
            assert signal.wait(2)
            from talkabout.adapters import AdapterError
            raise AdapterError('runtime_cancelled')
        def shutdown(self):
            return True
    def request_stop():
        assert started.wait(1)
        cancel.set()
    stopper = threading.Thread(target=request_stop)
    stopper.start()
    try:
        with pytest.raises(RuntimeTimeout) as result:
            bounded_call(Cooperative(), {}, cancel, 5)
        assert result.value.runtime_stopped is True
    finally:
        stopper.join(timeout=1)


def test_completed_result_wins_stop_race_and_is_returned_for_durable_save():
    completed = threading.Event()
    release_shutdown = threading.Event()

    class ReturnedRuntime:
        def run(self, context, cancel):
            completed.set()
            return 'finished before stop was observed'

        def shutdown(self):
            # Hold the timeout/stop path long enough to prove run completed first.
            release_shutdown.wait(1)
            return True

    runtime = ReturnedRuntime()
    cancel = threading.Event()
    result = []

    def call():
        try:
            result.append(bounded_call(runtime, {}, cancel, 1))
        except BaseException as error:
            result.append(error)

    worker = threading.Thread(target=call)
    worker.start()
    assert completed.wait(1)
    cancel.set()
    # Runtime is already done. bounded_call must recover queued result rather than
    # raising RuntimeTimeout and discarding successful provider output.
    worker.join(timeout=1)
    release_shutdown.set()
    assert not worker.is_alive()
    assert result == ['finished before stop was observed']


def test_unconfirmed_stop_is_not_reported_as_stopped(monkeypatch):
    monkeypatch.setattr(runtime_module, 'SHUTDOWN_ESCORT_SECONDS', 0.1)
    class FalseShutdown:
        def run(self, context, cancel):
            cancel.wait(0.2)
            return 'late synthetic result'
        def shutdown(self):
            return False
    with pytest.raises(RuntimeTimeout) as error:
        bounded_call(FalseShutdown(), {}, threading.Event(), 0.02)
    assert error.value.runtime_stopped is False
