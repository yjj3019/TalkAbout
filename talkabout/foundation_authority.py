"""Unwired internal contracts. Callers must supply trusted authority resolvers."""
from dataclasses import dataclass
import hashlib
import json
import math
import re


class FoundationError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise FoundationError(code)


def identifier(value):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value), 'invalid_identifier')
    return value


def expiry(value):
    require(type(value) in {int, float} and math.isfinite(value) and value > 0, 'invalid_expiry')


def positive(value, ceiling=1000000):
    require(type(value) is int and 1 <= value <= ceiling, 'invalid_limit')


def canonical(value, limit=8192):
    try:
        text = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise FoundationError('invalid_json') from None
    require(len(text) <= limit, 'json_limit')
    return text


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class ExecutionFence:
    agent_id: str
    room_id: str
    task_id: str
    generation: int
    membership_revision: int
    attempt: int
    lease_id: str
    policy_revision: int
    expires_at: float

    def __post_init__(self):
        for value in (self.agent_id, self.room_id, self.task_id, self.lease_id):
            identifier(value)
        for value in (self.generation, self.membership_revision, self.attempt, self.policy_revision):
            positive(value)
        expiry(self.expires_at)

    def identity(self):
        return (self.agent_id, self.room_id, self.task_id, self.generation,
                self.membership_revision, self.attempt, self.lease_id, self.policy_revision)


@dataclass(frozen=True)
class AuthoritySnapshot:
    fence: ExecutionFence
    active: bool = True


def check_fence(fence, resolver, now):
    require(isinstance(fence, ExecutionFence) and fence.expires_at > now, 'execution_expired')
    current = resolver(fence.agent_id, fence.room_id, fence.task_id)
    require(isinstance(current, AuthoritySnapshot) and current.active is True, 'execution_denied')
    require(current.fence.identity() == fence.identity(), 'execution_changed')
    require(current.fence.expires_at > now, 'execution_expired')
    return current
