"""Bounded pipe capture: output past the limit is never stored or written to disk."""
import io
import sys
import tempfile
import threading
import time

import pytest

from talkabout import adapters
from talkabout.adapters import AdapterError, execute_bounded
from talkabout.processes import BoundedCapture

windows_only = pytest.mark.skipif(sys.platform != 'win32', reason='containment is Windows-only')


class FakeProcess:
    def __init__(self, stdout=b'', stderr=b''):
        self.stdout, self.stderr = io.BytesIO(stdout), io.BytesIO(stderr)


class NoStop:
    def stop(self):
        return True


def test_capture_keeps_nothing_past_the_combined_limit():
    capture = BoundedCapture(FakeProcess(b'a' * 3_000_000, b'b' * 3_000_000), 1000)
    assert capture.settle(NoStop())
    assert capture.exceeded and len(capture.stdout) + len(capture.stderr) <= 1000


def test_capture_below_limit_is_exact():
    capture = BoundedCapture(FakeProcess(b'out', b'err'), 1000)
    assert capture.settle(NoStop()) and not capture.exceeded
    assert (bytes(capture.stdout), bytes(capture.stderr)) == (b'out', b'err')


@windows_only
@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_flood_is_stopped_without_growing_any_output_file(monkeypatch, stream):
    created = []
    real = tempfile.TemporaryFile
    monkeypatch.setattr(adapters.tempfile, 'TemporaryFile', lambda *a, **k: created.append(1) or real(*a, **k))
    flood = f"import sys\nwhile True: sys.{stream}.buffer.write(b'x' * 1048576); sys.{stream}.buffer.flush()"
    before, started = threading.active_count(), time.monotonic()
    with pytest.raises(AdapterError, match='^runtime_output_limit$'):
        execute_bounded([sys.executable, '-c', flood], '', threading.Event(), 20)
    assert time.monotonic() - started < 10
    assert created == [1]  # only the bounded stdin prompt file; no stdout/stderr file
    assert threading.active_count() <= before


@windows_only
def test_normal_output_and_failure_codes_are_unchanged():
    assert execute_bounded([sys.executable, '-c', "print('hello')"], '', threading.Event(), 10).strip() == 'hello'
    with pytest.raises(AdapterError, match='^runtime_failed$'):
        execute_bounded([sys.executable, '-c', 'import sys; sys.exit(3)'], '', threading.Event(), 10)
