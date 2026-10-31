"""Approval request identity and state helpers for the proposed A5/A6 gates.

Independent TalkAbout implementation. The problem framing (approve a concrete
external effect, separate decision/delivery/consumption, never auto-approve) was
informed by the reviewed products; the canonicalisation, digest rules, vocabulary
and state machine below were designed for this repository. No external source
code, table, string or fixture was copied. See docs/CLEAN_ROOM_IMPLEMENTATION.md.

This is a pure contract helper. It never authorizes by itself, never reads
storage, never mutates inputs and never calls a provider. Wiring it into an
authorization path requires the A5/A6 gates and a reviewed plan.
"""
import hashlib
import json
import math
import re

APPROVAL_SOURCES = ('human', 'saved_rule', 'timeout')
SOURCE_SET = frozenset(APPROVAL_SOURCES)

# Classified outcomes for one approval against one request. Only 'ready' is an
# affirmative answer; every other value is a refusal or an unknown.
APPROVAL_STATES = ('ready', 'missing', 'mismatch', 'expired', 'consumed', 'delivery_unknown')
STATE_SET = frozenset(APPROVAL_STATES)

MAX_IDENTIFIER = 128
MAX_ARGUMENT_BYTES = 8192
MAX_ARGUMENT_DEPTH = 6
MAX_COUNTER = 2 ** 31 - 1
TOOL_PATTERN = re.compile(r'^[a-z][a-z0-9_.-]{0,127}$')


class ApprovalInputError(ValueError):
    """Inputs cannot form a deterministic approval identity."""


def _identifier(value, limit=MAX_IDENTIFIER):
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ApprovalInputError('identifier_invalid')
    return value


def _counter(value):
    if type(value) is not int or not 0 <= value <= MAX_COUNTER:
        raise ApprovalInputError('counter_invalid')
    return value


def _normalise(value, depth):
    if depth > MAX_ARGUMENT_DEPTH:
        raise ApprovalInputError('arguments_too_deep')
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ApprovalInputError('arguments_not_canonical')
        return value
    if isinstance(value, list):
        return [_normalise(item, depth + 1) for item in value]
    if isinstance(value, dict):
        normalised = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ApprovalInputError('arguments_not_canonical')
            normalised[key] = _normalise(item, depth + 1)
        return normalised
    raise ApprovalInputError('arguments_not_canonical')


def canonical_arguments(arguments):
    """Deterministic, bounded JSON encoding of tool arguments.

    Mapping order is irrelevant to the result. Non-JSON values, non-string keys,
    non-finite floats, nesting beyond MAX_ARGUMENT_DEPTH and oversized payloads
    are refused instead of being coerced.
    """
    if not isinstance(arguments, dict):
        raise ApprovalInputError('arguments_not_canonical')
    normalised = _normalise(arguments, 0)
    try:
        encoded = json.dumps(normalised, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError):
        raise ApprovalInputError('arguments_not_canonical') from None
    if len(encoded.encode('ascii')) > MAX_ARGUMENT_BYTES:
        raise ApprovalInputError('arguments_too_large')
    return encoded


def effect_key(task_id, attempt, tool, arguments, occurrence=0):
    """Stable identity for one approved external effect.

    The caller supplies the server-issued task/attempt and an explicit occurrence
    counter; a provider tool-call id is deliberately *not* accepted as input. Two
    logically identical requests therefore share one key even if a provider
    renames its call id, and repeated identical calls stay distinguishable.
    """
    request = {
        'task_id': _identifier(task_id),
        'attempt': _counter(attempt),
        'tool': _validated_tool(tool),
        'occurrence': _counter(occurrence),
        'arguments': canonical_arguments(arguments),
    }
    envelope = json.dumps(request, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(envelope.encode('ascii')).hexdigest()


def _validated_tool(tool):
    if not isinstance(tool, str) or not TOOL_PATTERN.match(tool):
        raise ApprovalInputError('tool_invalid')
    return tool


def normalize_source(source):
    """Return a known approval source or refuse the value."""
    if source not in SOURCE_SET:
        raise ApprovalInputError('approval_source_invalid')
    return source


def approval_state(approval, *, task_id, attempt, tool, arguments, occurrence=0, now):
    """Classify one approval record against one request.

    Missing or malformed fields are never guessed. `saved_rule` and `timeout` keep
    their own source value and are never reported as a human decision. A missing
    delivery timestamp is `delivery_unknown`, not `ready`.
    """
    if not isinstance(approval, dict):
        return 'missing'
    if approval.get('source') not in SOURCE_SET:
        return 'missing'
    digest = approval.get('target_digest')
    if not isinstance(digest, str) or digest != effect_key(task_id, attempt, tool, arguments, occurrence):
        return 'mismatch'
    fence = approval.get('fence')
    if not isinstance(fence, dict):
        return 'missing'
    if fence.get('task_id') != task_id or fence.get('attempt') != attempt:
        return 'mismatch'
    if type(approval.get('decided_at')) not in (int, float):
        return 'missing'
    if approval.get('consumed_at') is not None:
        return 'consumed'
    expires_at = approval.get('expires_at')
    if type(expires_at) not in (int, float):
        return 'missing'
    if now >= expires_at:
        return 'expired'
    delivered_at = approval.get('delivered_at')
    if delivered_at is None:
        return 'delivery_unknown'
    if type(delivered_at) not in (int, float):
        return 'missing'
    return 'ready'


def ready(approval, **request):
    """Convenience predicate for the only affirmative classification."""
    return approval_state(approval, **request) == 'ready'
