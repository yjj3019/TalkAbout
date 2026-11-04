"""Unwired, record-only opt-in policy; no persistence or external effects.

All arguments are server inputs, not claims to trust from a proposal. In particular,
only an authenticated current human owner may create/replace a policy, and the
server must derive the owner, epoch, generation, proposal and time itself.

Atomic caller contract: within one transaction/lock, load the current policy and
its persistent cumulative consumption, current owner/epoch/generation and the
pending proposal; independently enforce normal proposal eligibility, membership,
revision and authorization fences; call evaluate_policy; if allowed, atomically
record a distinct automatic triage marker and increment the same policy's counter.
It must never be treated as a human approval or make a proposal applicable.
Use a unique proposal/decision receipt to make retries idempotent, and compare the
policy identity/version and all fences before committing. Never reset usage on a
retry, stale read, or policy update: creating a fresh policy requires a new explicit
human opt-in and a separately persisted identity/counter. Otherwise concurrent
callers or replacement can exceed the cap. A returned result is not itself an
approval, dispatch, task creation, provider invocation, or authorization grant.
"""
import math
import re

MAX_AUTO_DECISIONS = 4
_ALLOWED_KINDS = frozenset(('question', 'information'))
_FIELDS = frozenset(('enabled', 'owner_id', 'owner_epoch', 'generation', 'actor_id',
                     'target_id', 'kinds', 'expires_at', 'max_decisions'))
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')


def _identifier(value):
    return type(value) is str and _ID.fullmatch(value) is not None


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _time(value):
    if type(value) not in (int, float) or value <= 0:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def validate_policy(policy):
    """Return whether an exact, well-formed policy is safe to store or evaluate."""
    if type(policy) is not dict or policy.keys() != _FIELDS:
        return False
    kinds = policy['kinds']
    return (type(policy['enabled']) is bool
            and all(_identifier(policy[k]) for k in ('owner_id', 'actor_id', 'target_id'))
            and policy['actor_id'] != policy['target_id']
            and _integer(policy['owner_epoch']) and _integer(policy['generation'])
            and type(kinds) is list and 1 <= len(kinds) <= len(_ALLOWED_KINDS)
            and all(type(k) is str and k in _ALLOWED_KINDS for k in kinds)
            and len(set(kinds)) == len(kinds)
            and _time(policy['expires_at'])
            and _integer(policy['max_decisions'], 1)
            and policy['max_decisions'] <= MAX_AUTO_DECISIONS)


def create_policy(spec, *, authenticated_owner_id, owner_epoch, generation, now):
    """Validate an explicit owner opt-in and return a detached policy, or None.

    Authentication and fresh authority lookup are the caller's responsibility.
    A disabled policy is valid for validation but cannot be created as an opt-in.
    """
    if (not validate_policy(spec) or spec['enabled'] is not True
            or not _identifier(authenticated_owner_id)
            or not _integer(owner_epoch) or not _integer(generation) or not _time(now)
            or spec['owner_id'] != authenticated_owner_id
            or spec['owner_epoch'] != owner_epoch or spec['generation'] != generation
            or spec['expires_at'] <= now):
        return None
    return {**spec, 'kinds': list(spec['kinds'])}


def evaluate_policy(policy, *, owner_id, owner_epoch, generation, actor_id,
                    target_id, kind, now, consumed):
    """Return a non-authorizing triage marker and next cumulative count, or None.

    The supplied current authority, pending proposal fields and cumulative count
    must be loaded from server-controlled state, never from an untrusted client.
    This function neither verifies their provenance nor mutates any input.
    """
    if (not validate_policy(policy) or policy['enabled'] is not True
            or not all(_identifier(x) for x in (owner_id, actor_id, target_id))
            or not _integer(owner_epoch) or not _integer(generation)
            or not _integer(consumed) or not _time(now)
            or type(kind) is not str or kind not in _ALLOWED_KINDS
            or owner_id != policy['owner_id'] or owner_epoch != policy['owner_epoch']
            or generation != policy['generation'] or actor_id != policy['actor_id']
            or target_id != policy['target_id'] or actor_id == target_id
            or kind not in policy['kinds'] or now >= policy['expires_at']
            or consumed >= policy['max_decisions']):
        return None
    return {'triage': 'eligible', 'consumed': consumed + 1}
