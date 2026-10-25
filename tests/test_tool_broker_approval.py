"""Approval admission and expiry: no request may wait for an answer that cannot come.

Memory-only. These tests never touch a provider, a shell or the filesystem.
"""
import pytest

from talkabout.foundation_authority import AuthoritySnapshot, ExecutionFence, FoundationError
from talkabout.tool_broker import FakeToolAdapter, ToolBroker, ToolPermission, ToolPolicy


@pytest.fixture
def broker_env():
    # The policy expires before the fence, so approval lifetime and execution lifetime are
    # distinguishable: the tighter bound is the one an approval must obey.
    fence = ExecutionFence('agent-a', 'room-a', 'task-a', 1, 1, 1, 'lease-a', 1, 2000)
    authority = {(fence.agent_id, fence.room_id, fence.task_id): AuthoritySnapshot(fence)}
    now = [100]
    adapter = FakeToolAdapter()
    broker = ToolBroker(lambda a, r, t: authority.get((a, r, t)),
                        lambda h, r: h == 'human-owner' and r == 'room-a',
                        adapter, clock=lambda: now[0])
    read = ToolPermission('fixture.read', 'fixture:document', 'read')
    write = ToolPermission('fixture.write', 'fixture:document', 'write')
    policy = ToolPolicy('agent-a', 'room-a', 1, 1000, 'fake_tools', frozenset({read, write}), 10)
    broker.set_policy(policy)
    return broker, fence, now, adapter, write, read


def test_write_with_no_responder_is_denied_at_admission(broker_env):
    broker, fence, _, adapter, write, _ = broker_env
    record = broker.request(fence, write, {'value': 'synthetic'}, 'request', responder_available=False)
    assert record.state == 'denied'
    assert record.error_code == 'no_user_can_answer'
    assert record.approved_by is None
    with pytest.raises(FoundationError):
        broker.execute(record.request.id, fence)
    assert adapter.effects == ()


def test_no_responder_does_not_block_a_read(broker_env):
    broker, fence, _, _, _, read = broker_env
    record = broker.request(fence, read, {}, 'request', responder_available=False)
    assert record.state == 'ready'
    assert record.error_code is None


def test_default_still_waits_for_a_human(broker_env):
    broker, fence, _, _, write, _ = broker_env
    record = broker.request(fence, write, {}, 'request')
    assert record.state == 'awaiting_approval'
    approved = broker.approve(record.request.id, 'human-owner')
    assert approved.state == 'ready' and approved.approved_by == 'human-owner'


def test_pending_approval_expires_at_the_request_deadline(broker_env):
    broker, fence, now, _, write, _ = broker_env
    record = broker.request(fence, write, {}, 'request')
    assert record.state == 'awaiting_approval'
    assert record.request.expires_at == 1000
    assert broker.expire_pending() == ()
    now[0] = 1000
    assert broker.expire_pending() == (record.request.id,)
    # Re-inspection would re-authorize an expired policy; the audit trail is the record.
    assert broker.events[-1][:2] == (record.request.id, 'expired')
    # Idempotent: a settled record is never moved twice.
    assert broker.expire_pending() == ()


def test_expired_approval_cannot_be_approved_or_executed(broker_env):
    broker, fence, now, adapter, write, _ = broker_env
    record = broker.request(fence, write, {}, 'request')
    now[0] = 1000
    broker.expire_pending()
    with pytest.raises(FoundationError):
        broker.approve(record.request.id, 'human-owner')
    with pytest.raises(FoundationError):
        broker.execute(record.request.id, fence)
    assert adapter.effects == ()


def test_expiry_sweep_leaves_settled_records_alone(broker_env):
    broker, fence, now, _, write, read = broker_env
    pending = broker.request(fence, write, {}, 'write')
    broker.request(fence, read, {}, 'read')
    approved = broker.approve(pending.request.id, 'human-owner')
    assert approved.state == 'ready'
    now[0] = 1000
    assert broker.expire_pending() == ()
    assert 'expired' not in [event[1] for event in broker.events]


def test_expiry_records_an_audit_event(broker_env):
    broker, fence, now, _, write, _ = broker_env
    record = broker.request(fence, write, {}, 'request')
    before = len(broker.events)
    now[0] = 1000
    broker.expire_pending()
    new_events = broker.events[before:]
    assert [event[1] for event in new_events] == ['expired']
    assert new_events[0][0] == record.request.id
