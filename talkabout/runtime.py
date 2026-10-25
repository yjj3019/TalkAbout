import queue
import threading
import time


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
    if timeout <= 0:
        raise RuntimeTimeout()
    output = queue.Queue(maxsize=1)
    execution_done = threading.Event()
    def invoke():
        try:
            value = runtime.run(context, cancel)
            if not isinstance(value, str) or not 1 <= len(value) <= 16000:
                raise ValueError("invalid_runtime_output")
            output.put((True, value))
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
