import queue
import threading
import time
from typing import NamedTuple


class WorkEvidence(NamedTuple):
    """Immutable, versioned worker-reported terminal-item counts, not verification.

    failed_items counts items with terminal status failed or declined; a completed
    command with a nonzero exit code is not reclassified as failed.
    """
    version: int
    command_items: int
    file_change_items: int
    failed_items: int


class RunBudgetExceeded(Exception):
    def __init__(self):
        super().__init__('run_budget_exhausted')


def check_deadline(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise RunBudgetExceeded()


class BudgetedClient:
    def __init__(self, client, deadline):
        self.client, self.deadline = client, deadline

    def request(self, *args, **kwargs):
        check_deadline(self.deadline)
        value = self.client.request(*args, **kwargs)
        check_deadline(self.deadline)
        return value


class RuntimeTimeout(Exception):
    def __init__(self, runtime_stopped=False):
        self.runtime_stopped = runtime_stopped
        super().__init__("runtime_wait_stopped")


# The stop path runs after the turn budget is gone, so it cannot borrow that budget:
# it needs its own bound. An unbounded runtime.shutdown() is the last way a bounded
# call can hang forever, and a stop nobody can prove is reported as unconfirmed.
SHUTDOWN_ESCORT_SECONDS = 5.0


def bounded_shutdown(shutdown, bound=None):
    """Run shutdown() out of band and never wait past the escort bound.

    True only when shutdown() returned a truthy value within the bound. A timeout,
    an exception or a falsey return is False, which callers must report as an
    unconfirmed stop instead of claiming the runtime stopped.
    """
    limit = SHUTDOWN_ESCORT_SECONDS if bound is None else bound
    done = queue.Queue(maxsize=1)

    def invoke():
        try:
            done.put(bool(shutdown()))
        except BaseException:
            done.put(False)

    threading.Thread(target=invoke, daemon=True).start()
    try:
        return done.get(timeout=max(0.0, limit))
    except queue.Empty:
        return False


def bounded_call(runtime, context, cancel, timeout):
    """Bound waiting, never claim to forcibly stop remote side effects."""
    return _bounded_call(runtime, context, cancel, timeout, with_evidence=False)


def bounded_call_with_evidence(runtime, context, cancel, timeout):
    """Return text and optional immutable aggregate bound to this invocation.

    Only runtimes implementing run_with_evidence may supply evidence; there is no
    fallback to mutable ambient attributes. Exceptions, timeout and cancellation
    return no result or evidence. The aggregate is observational, not verification.
    """
    return _bounded_call(runtime, context, cancel, timeout, with_evidence=True)


def _bounded_call(runtime, context, cancel, timeout, *, with_evidence):
    if timeout <= 0:
        raise RuntimeTimeout()
    output = queue.Queue(maxsize=1)
    execution_done = threading.Event()
    def invoke():
        try:
            operation = getattr(runtime, 'run_with_evidence', None) if with_evidence else None
            if callable(operation):
                value = operation(context, cancel)
                if not isinstance(value, tuple) or len(value) != 2:
                    raise ValueError('invalid_runtime_evidence')
                text, evidence = value
                if evidence is not None and (type(evidence) is not WorkEvidence
                        or type(evidence.version) is not int or evidence.version != 1
                        or any(type(count) is not int or count < 0 for count in
                               (evidence.command_items, evidence.file_change_items, evidence.failed_items))
                        or evidence.command_items + evidence.file_change_items > 128
                        or evidence.failed_items > evidence.command_items + evidence.file_change_items):
                    raise ValueError('invalid_runtime_evidence')
            else:
                text, evidence = runtime.run(context, cancel), None
            if not isinstance(text, str) or not 1 <= len(text) <= 16000:
                raise ValueError("invalid_runtime_output")
            output.put((True, (text, evidence) if with_evidence else text))
        except Exception as error:
            output.put((False, error))
        finally:
            execution_done.set()
    threading.Thread(target=invoke, daemon=True).start()
    deadline = time.monotonic() + timeout
    def stop():
        cancel.set()
        shutdown = getattr(runtime, 'shutdown', None)
        if callable(shutdown):
            stopped = bounded_shutdown(shutdown)
        else:
            execution_done.wait(0.02)
            stopped = execution_done.is_set()
        raise RuntimeTimeout(stopped)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or cancel.is_set():
            stop()
        try:
            ok, value = output.get(timeout=min(0.02, remaining))
            if cancel.is_set() or time.monotonic() >= deadline:
                stop()
            if ok:
                return value
            raise value
        except queue.Empty:
            continue


def validate_adapter_policy(policy):
    if not isinstance(policy, dict) or policy.get("approval_bypass") or policy.get("sandbox_bypass"):
        raise ValueError("unsafe_adapter_policy")
    if policy.get("external") and not policy.get("tool_limits_verified"):
        raise ValueError("unverified_external_adapter")
    if policy.get("allowed_tools", []) != []:
        raise ValueError("p0_tools_disabled")
