import queue
import threading
import time


class RuntimeTimeout(Exception):
    pass


def bounded_call(runtime, context, cancel, timeout):
    """Bound waiting, never claim to forcibly stop remote side effects."""
    if timeout <= 0:
        raise RuntimeTimeout()
    output = queue.Queue(maxsize=1)
    def invoke():
        try:
            value = runtime.run(context, cancel)
            if not isinstance(value, str) or not 1 <= len(value) <= 16000:
                raise ValueError("invalid_runtime_output")
            output.put((True, value))
        except Exception as error:
            output.put((False, error))
    threading.Thread(target=invoke, daemon=True).start()
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or cancel.is_set():
            cancel.set()
            raise RuntimeTimeout()
        try:
            ok, value = output.get(timeout=min(0.02, remaining))
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
