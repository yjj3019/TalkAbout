"""Pure policy evaluation: opt-in, bounded and fail-closed."""
import copy

import pytest

from talkabout.collaboration_policy import create_policy, evaluate_policy, validate_policy


@pytest.fixture
def policy():
    return {'enabled': True, 'owner_id': 'human', 'owner_epoch': 2,
            'generation': 3, 'actor_id': 'a', 'target_id': 'b',
            'kinds': ['question', 'information'], 'expires_at': 100.0,
            'max_decisions': 4}


def evaluate(policy, **changes):
    inputs = {'owner_id': 'human', 'owner_epoch': 2, 'generation': 3,
              'actor_id': 'a', 'target_id': 'b', 'kind': 'question',
              'now': 50, 'consumed': 0}
    return evaluate_policy(policy, **(inputs | changes))


def create(policy, **changes):
    inputs = {'authenticated_owner_id': 'human', 'owner_epoch': 2,
              'generation': 3, 'now': 50}
    return create_policy(policy, **(inputs | changes))


def test_explicit_creation_and_detached_policy(policy):
    created = create(policy)
    assert created == policy and created is not policy
    created['kinds'].append('review')
    assert policy['kinds'] == ['question', 'information']
    for changes in ({'authenticated_owner_id': 'agent'}, {'owner_epoch': 3},
                    {'generation': 4}, {'now': 100}):
        assert create(policy, **changes) is None


@pytest.mark.parametrize('changes', [
    {'enabled': False}, {'enabled': 1}, {'enabled': None},
    {'kinds': ['review']}, {'kinds': ['question', 'execute']},
    {'kinds': []}, {'kinds': ['question', 'question']}, {'kinds': [None]},
    {'actor_id': 'b'}, {'actor_id': ''}, {'target_id': []},
    {'owner_epoch': True}, {'generation': -1}, {'max_decisions': 5},
    {'max_decisions': 0}, {'max_decisions': True}, {'expires_at': float('nan')},
    {'expires_at': float('inf')}, {'expires_at': 10**1000}, {'extra': 'execute'},
])
def test_bad_or_disabled_policy_fails_closed(policy, changes):
    policy.update(changes)
    assert create(policy) is None
    assert evaluate(policy) is None


def test_absent_and_missing_fields(policy):
    assert not validate_policy(None)
    assert evaluate(None) is None
    del policy['enabled']
    assert not validate_policy(policy)
    assert create(policy) is None
    assert evaluate(policy) is None


@pytest.mark.parametrize('changes', [
    {'kind': 'review'}, {'kind': 'execute'}, {'kind': []},
    {'actor_id': 'b'}, {'target_id': 'a'}, {'target_id': 'other'},
    {'owner_id': 'other'}, {'owner_epoch': 3}, {'generation': 4},
    {'now': 100}, {'now': float('nan')}, {'consumed': -1},
    {'consumed': True}, {'consumed': 4}, {'consumed': 5},
])
def test_mismatch_expiry_or_invalid_server_input_denied(policy, changes):
    assert evaluate(policy, **changes) is None


def test_allowed_kinds_and_cumulative_limit(policy):
    used = 0
    for kind in ('question', 'information', 'question', 'information'):
        result = evaluate(policy, kind=kind, consumed=used)
        assert result == {'triage': 'eligible', 'consumed': used + 1}
        used = result['consumed']
    assert evaluate(policy, consumed=used) is None
    policy['max_decisions'] = 2
    assert evaluate(policy, consumed=2) is None
    policy['kinds'] = ['question']
    assert evaluate(policy, kind='information') is None


def test_evaluation_has_no_effects(policy):
    original = copy.deepcopy(policy)
    assert evaluate(policy) == {'triage': 'eligible', 'consumed': 1}
    assert evaluate(policy, kind='execute') is None
    assert policy == original
