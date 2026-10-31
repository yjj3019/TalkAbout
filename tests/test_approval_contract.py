"""Approval identity and state contract: pure functions, no storage or provider.

The vocabulary and rules are TalkAbout-owned; see docs/CLEAN_ROOM_IMPLEMENTATION.md.
"""
import copy

import pytest

from talkabout.approval import (
    APPROVAL_SOURCES, APPROVAL_STATES, ApprovalInputError, SOURCE_SET, STATE_SET,
    approval_state, canonical_arguments, effect_key, normalize_source, ready,
)


def record(**overrides):
    base = {
        'source': 'human',
        'target_digest': effect_key('task-one', 0, 'files.write', {'path': 'a.txt'}),
        'fence': {'task_id': 'task-one', 'attempt': 0},
        'decided_at': 100.0,
        'delivered_at': 101.0,
        'expires_at': 200.0,
        'consumed_at': None,
    }
    base.update(overrides)
    return base


def request(**overrides):
    base = {'task_id': 'task-one', 'attempt': 0, 'tool': 'files.write',
            'arguments': {'path': 'a.txt'}, 'occurrence': 0, 'now': 150.0}
    base.update(overrides)
    return base


def test_vocabularies_are_closed():
    assert list(APPROVAL_SOURCES) == ['human', 'saved_rule', 'timeout']
    assert set(APPROVAL_STATES) == STATE_SET and len(APPROVAL_STATES) == len(STATE_SET)
    assert 'ready' in STATE_SET and 'unknown' not in STATE_SET


def test_effect_key_is_deterministic_and_order_insensitive():
    first = effect_key('task-one', 0, 'files.write', {'path': 'a.txt', 'mode': 'update'})
    second = effect_key('task-one', 0, 'files.write', {'mode': 'update', 'path': 'a.txt'})
    assert first == second and len(first) == 64 and int(first, 16) >= 0
    assert canonical_arguments({'b': 1, 'a': 2}) == '{"a":2,"b":1}'


def test_effect_key_separates_identity_axes():
    base = effect_key('task-one', 0, 'files.write', {'path': 'a.txt'}, 0)
    variants = [
        effect_key('task-two', 0, 'files.write', {'path': 'a.txt'}, 0),
        effect_key('task-one', 1, 'files.write', {'path': 'a.txt'}, 0),
        effect_key('task-one', 0, 'files.read', {'path': 'a.txt'}, 0),
        effect_key('task-one', 0, 'files.write', {'path': 'b.txt'}, 0),
        effect_key('task-one', 0, 'files.write', {'path': 'a.txt'}, 1),
    ]
    assert len({base, *variants}) == 6


def test_effect_key_does_not_take_provider_call_ids():
    """Provider ids are not an input axis: identity comes from the server request."""
    import inspect
    parameters = list(inspect.signature(effect_key).parameters)
    assert parameters == ['task_id', 'attempt', 'tool', 'arguments', 'occurrence']


@pytest.mark.parametrize('arguments', [
    {'nested': {'x': [1, 2, {'y': None}]}},
    {'text': 'synthetic'},
    {'flag': True, 'count': 1},
    {'empty': {}},
])
def test_canonical_arguments_accept_json_values(arguments):
    assert canonical_arguments(arguments)


@pytest.mark.parametrize('arguments', [
    [], 'text', None, {1: 'non-string-key'}, {'value': {1, 2}},
    {'value': float('nan')}, {'value': float('inf')}, {'value': object()},
])
def test_canonical_arguments_refuse_non_json_values(arguments):
    with pytest.raises(ApprovalInputError):
        canonical_arguments(arguments)


def test_canonical_arguments_are_bounded():
    with pytest.raises(ApprovalInputError, match='arguments_too_large'):
        canonical_arguments({'value': 'x' * 9000})
    deep = current = {}
    for _ in range(8):
        current['next'] = {}
        current = current['next']
    with pytest.raises(ApprovalInputError, match='arguments_too_deep'):
        canonical_arguments(deep)


@pytest.mark.parametrize('tool', ['Files.write', 'files write', '1tool', '', 'x' * 129, 'tool/../x'])
def test_invalid_tools_are_refused(tool):
    with pytest.raises(ApprovalInputError, match='tool_invalid'):
        effect_key('task-one', 0, tool, {})


def test_counters_and_identifiers_are_type_strict():
    with pytest.raises(ApprovalInputError):
        effect_key('', 0, 'files.write', {})
    with pytest.raises(ApprovalInputError):
        effect_key('task-one', True, 'files.write', {})
    with pytest.raises(ApprovalInputError):
        effect_key('task-one', 0, 'files.write', {}, -1)


def test_sources_never_collapse_to_human():
    for source in APPROVAL_SOURCES:
        assert normalize_source(source) == source
    assert normalize_source('saved_rule') != 'human' and normalize_source('timeout') != 'human'
    for value in ['model', 'auto', 'Human', None, '']:
        with pytest.raises(ApprovalInputError):
            normalize_source(value)


def test_ready_requires_canonical_digest_fence_delivery_and_expiry():
    assert ready(record(), **request()) is True
    assert approval_state(record(), **request()) == 'ready'


@pytest.mark.parametrize('approval,expected', [
    (None, 'missing'),
    ({}, 'missing'),
    (record(source='model'), 'missing'),
    (record(target_digest='0' * 64), 'mismatch'),
    (record(fence={'task_id': 'task-other', 'attempt': 0}), 'mismatch'),
    (record(fence={'task_id': 'task-one', 'attempt': 1}), 'mismatch'),
    (record(decided_at=None), 'missing'),
    (record(delivered_at=None), 'delivery_unknown'),
    (record(consumed_at=120.0), 'consumed'),
    (record(expires_at=120.0), 'expired'),
    (record(expires_at=None), 'missing'),
])
def test_state_machine_is_closed_and_never_guesses(approval, expected):
    state = approval_state(approval, **request())
    assert state == expected and state in STATE_SET


def test_consumed_wins_over_delivery_and_expiry():
    approval = record(consumed_at=120.0, delivered_at=None, expires_at=110.0)
    assert approval_state(approval, **request()) == 'consumed'


def test_approval_state_does_not_mutate_inputs():
    approval, req = record(), request()
    before = (copy.deepcopy(approval), copy.deepcopy(req))
    approval_state(approval, **req)
    assert (approval, req) == before


def test_saved_rule_and_timeout_are_visible_but_not_human():
    for source in ('saved_rule', 'timeout'):
        approval = record(source=source)
        assert approval_state(approval, **request()) == 'ready'
        assert approval['source'] != 'human' and approval['source'] in SOURCE_SET
