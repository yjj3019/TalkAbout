"""Memory-only fake-tool foundation; intentionally absent from app/runtime wiring."""
from dataclasses import asdict, dataclass, replace
import json
import threading
import time
import uuid

from .foundation_authority import (ExecutionFence, FoundationError, canonical,
                                   check_fence, digest, expiry, identifier, positive, require)


@dataclass(frozen=True)
class ToolPermission:
    tool: str
    target: str
    action: str

    def __post_init__(self):
        identifier(self.tool); identifier(self.target)
        require(self.action in {'read', 'write'}, 'invalid_action')


@dataclass(frozen=True)
class ToolPolicy:
    agent_id: str
    room_id: str
    revision: int
    expires_at: float
    profile: str = 'text_only'
    permissions: frozenset[ToolPermission] = frozenset()
    max_calls: int = 3
    max_output_chars: int = 4096

    def __post_init__(self):
        identifier(self.agent_id); identifier(self.room_id)
        positive(self.revision); expiry(self.expires_at)
        require(self.profile in {'text_only', 'fake_tools'}, 'unsupported_profile')
        object.__setattr__(self, 'permissions', frozenset(self.permissions))
        require(len(self.permissions) <= 32 and all(isinstance(p, ToolPermission) for p in self.permissions), 'invalid_permissions')
        require(self.profile != 'text_only' or not self.permissions, 'text_only_tools_denied')
        positive(self.max_calls, 100); positive(self.max_output_chars, 16384)


@dataclass(frozen=True)
class ApprovalRequest:
    id: str
    fence: ExecutionFence
    permission: ToolPermission
    arguments_json: str
    request_digest: str
    intent_digest: str
    expires_at: float


@dataclass(frozen=True)
class Invocation:
    request: ApprovalRequest
    state: str
    approved_by: str | None = None
    result_json: str | None = None
    error_code: str | None = None


class UnknownResult(Exception):
    pass


class FakeToolAdapter:
    """Only records synthetic effects in memory. No filesystem/network/process APIs."""
    actions = {'fixture.read': 'read', 'fixture.write': 'write'}

    def __init__(self):
        self._lock = threading.Lock()
        self._effects = []

    @property
    def effects(self):
        with self._lock:
            return tuple(self._effects)

    def invoke(self, request):
        require(self.actions.get(request.permission.tool) == request.permission.action, 'unknown_tool')
        arguments = json.loads(request.arguments_json)
        with self._lock:
            if request.permission.action == 'write':
                self._effects.append((request.id, request.permission.target, request.arguments_json))
        if arguments.get('uncertain_after_effect'):
            raise UnknownResult()
        return {'provenance': 'synthetic', 'untrusted_data': True, 'target': request.permission.target,
                'value': arguments.get('value', 'fixture result')}


class ToolBroker:
    def __init__(self, resolve_authority, authorize_human, adapter=None, clock=time.time, capacity=128):
        self.resolve_authority, self.authorize_human, self.clock = resolve_authority, authorize_human, clock
        self.adapter = adapter if adapter is not None else FakeToolAdapter()
        require(type(self.adapter) is FakeToolAdapter, 'real_adapters_disabled')
        positive(capacity, 1024)
        self.capacity = capacity
        self._policies, self._records, self._keys, self._unknown, self._calls = {}, {}, {}, set(), {}
        self._events = []
        self._revoked = set()
        self._lock = threading.RLock()

    @property
    def events(self):
        with self._lock:
            return tuple(self._events)

    def set_policy(self, policy):
        """Trusted host configuration only; never exposed as a model tool."""
        require(isinstance(policy, ToolPolicy), 'invalid_policy')
        with self._lock:
            key = (policy.agent_id, policy.room_id)
            old = self._policies.get(key)
            require(old is None or old == policy or policy.revision > old.revision, 'policy_revision_conflict')
            require(key in self._policies or len(self._policies) < self.capacity, 'policy_capacity')
            self._policies[key] = policy

    def _authorize(self, fence, permission):
        check_fence(fence, self.resolve_authority, self.clock())
        policy = self._policies.get((fence.agent_id, fence.room_id))
        require(policy is not None and policy.profile == 'fake_tools', 'tools_disabled')
        require(policy.revision == fence.policy_revision and policy.expires_at > self.clock(), 'policy_expired_or_changed')
        require(permission in policy.permissions, 'tool_target_denied')
        require(self.adapter.actions.get(permission.tool) == permission.action, 'unknown_tool')
        return policy

    def list_tools(self, fence):
        with self._lock:
            check_fence(fence, self.resolve_authority, self.clock())
            policy = self._policies.get((fence.agent_id, fence.room_id))
            if policy is None or policy.profile == 'text_only':
                return ()
            return tuple(sorted((p for p in policy.permissions if self._authorize(fence, p)),
                                key=lambda p: (p.tool, p.target, p.action)))

    def request(self, fence, permission, arguments, key, responder_available=True):
        identifier(key)
        require(isinstance(fence, ExecutionFence) and isinstance(permission, ToolPermission) and isinstance(arguments, dict), 'invalid_request')
        arguments_json = canonical(arguments)
        fingerprint = digest(canonical({'fence': asdict(fence), 'permission': asdict(permission),
                                        'arguments_json': arguments_json}, 16384))
        intent = digest(canonical({'agent': fence.agent_id, 'room': fence.room_id,
                                   'permission': asdict(permission), 'arguments_json': arguments_json}, 16384))
        with self._lock:
            policy = self._authorize(fence, permission)
            lookup = (fence.agent_id, fence.room_id, key)
            if lookup in self._keys:
                prior = self._records[self._keys[lookup]]
                require(prior.request.request_digest == fingerprint, 'idempotency_conflict')
                return prior
            require(intent not in self._unknown, 'uncertain_requires_reconciliation')
            require(len(self._records) < self.capacity, 'invocation_capacity')
            request = ApprovalRequest(uuid.uuid4().hex, fence, permission, arguments_json, fingerprint, intent,
                                      min(policy.expires_at, fence.expires_at))
            if permission.action == 'write' and responder_available is not True:
                # No human can answer: hold nothing open. A write approval with no responder
                # deadlocks until some unrelated deadline, so it fails closed at admission
                # instead. Absence of a responder is never consent.
                record = Invocation(request, 'denied', error_code='no_user_can_answer')
            else:
                record = Invocation(request, 'awaiting_approval' if permission.action == 'write' else 'ready')
            self._records[request.id], self._keys[lookup] = record, request.id
            self._events.append((request.id, record.state, fence.agent_id, self.clock(), request.request_digest))
            return record

    def _get(self, invocation_id):
        identifier(invocation_id)
        require(invocation_id in self._records, 'invocation_not_found')
        return self._records[invocation_id]

    def _transition(self, record, state, audit_actor='broker', **changes):
        updated = replace(record, state=state, **changes)
        self._records[record.request.id] = updated
        self._events.append((record.request.id, state, audit_actor, self.clock(), record.request.request_digest))
        return updated

    def expire_pending(self, now=None):
        """Terminal state for approvals nobody answered before the request expired.

        An approval request carries min(policy, fence) expiry, but nothing observed it:
        a write left in awaiting_approval stayed there for the life of the process. The
        sweep is idempotent, never touches a record a human already settled, and returns
        only the invocation ids it moved.
        """
        moment = self.clock() if now is None else now
        expired = []
        with self._lock:
            for invocation_id, record in list(self._records.items()):
                if record.state != 'awaiting_approval' or record.request.expires_at > moment:
                    continue
                self._transition(record, 'expired', error_code='approval_expired')
                expired.append(invocation_id)
        return tuple(expired)

    def inspect(self, invocation_id, fence):
        with self._lock:
            record = self._get(invocation_id)
            require(record.request.fence == fence, 'invocation_not_found')
            self._authorize(fence, record.request.permission)
            return record

    def approve(self, invocation_id, human_id):
        identifier(human_id)
        with self._lock:
            record = self._get(invocation_id)
            require(record.state == 'awaiting_approval', 'invalid_transition')
            self._authorize(record.request.fence, record.request.permission)
            require(self.authorize_human(human_id, record.request.fence.room_id) is True, 'human_approval_denied')
            return self._transition(record, 'ready', audit_actor=human_id, approved_by=human_id)

    def cancel(self, invocation_id, fence):
        with self._lock:
            record = self.inspect(invocation_id, fence)
            require(record.state in {'ready', 'awaiting_approval'}, 'invalid_transition')
            return self._transition(record, 'cancelled', audit_actor=fence.agent_id)

    def revoke_approval(self, invocation_id, human_id):
        identifier(human_id)
        with self._lock:
            record=self._get(invocation_id)
            require(self.authorize_human(human_id,record.request.fence.room_id) is True,'human_approval_denied')
            if invocation_id in self._revoked:return record
            require(record.state in {'awaiting_approval','ready','executing'},'invalid_transition')
            self._revoked.add(invocation_id)
            if record.state=='executing':
                self._events.append((invocation_id,'revocation_requested',human_id,self.clock(),record.request.request_digest))
                return record
            return self._transition(record,'cancelled',audit_actor=human_id)

    def _approval_current(self, record):
        require(record.request.id not in self._revoked,'approval_revoked')
        if record.request.permission.action=='write':
            require(record.approved_by is not None
                    and self.authorize_human(record.approved_by,record.request.fence.room_id) is True,'approval_required')

    def execute(self, invocation_id, fence):
        """Trusted dispatcher entrypoint; it receives IDs created by request()."""
        with self._lock:
            record = self._get(invocation_id)
            require(record.request.fence == fence, 'invocation_not_found')
            require(record.state == 'ready', 'invalid_transition')
            policy = self._authorize(record.request.fence, record.request.permission)
            self._approval_current(record)
            budget = (policy.agent_id, policy.room_id, policy.revision)
            require(self._calls.get(budget, 0) < policy.max_calls, 'tool_budget_exhausted')
            self._calls[budget] = self._calls.get(budget, 0) + 1
            record = self._transition(record, 'executing')
        try:
            result = canonical(self.adapter.invoke(record.request), policy.max_output_chars)
            with self._lock:
                self._authorize(record.request.fence, record.request.permission)
                self._approval_current(record)
                return self._transition(record, 'succeeded', result_json=result)
        except Exception as error:
            # An adapter may have acted before raising or losing authority.
            with self._lock:
                uncertain = record.request.permission.action == 'write'
                if uncertain:
                    self._unknown.add(record.request.intent_digest)
                code = error.code if isinstance(error, FoundationError) else 'adapter_result_unknown'
                return self._transition(record, 'unknown' if uncertain else 'failed', error_code=code)
