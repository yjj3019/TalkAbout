"""Required messages reserve shared and serialized prompt budgets."""
import copy
import hashlib

import pytest

from talkabout import collaboration as c
from test_collaboration_context import artifact, message, room_fixture, version


def make(required, texts, *, artifacts=()):
    task = {'id': 't', 'assignee': 'a', 'attempt': 1, 'generation': 1,
            'membership_revision': 1, 'state': 'leased'}
    room = room_fixture(messages=[message('m%d' % i, i + 1, text)
                                  for i, text in enumerate(texts)], artifacts=artifacts)
    room.update(generation=1, membership_revision=1,
                tasks=[{**task, 'plan_node': {'required_predecessor_message_ids': required}}])
    return room, task


def test_reserves_complete_utf8_before_optional_artifacts_and_messages(monkeypatch):
    monkeypatch.setattr(c, 'MAX_SHARED_TEXT_BYTES', 15000)
    room, task = make(['m0'], ['한' * 3000, 'optional' * 2000],
                      artifacts=[artifact('a', 1, [version('v', 3, 'z' * 20000)])])
    context = c.shared_context(room, task)
    assert c.required_predecessors_available(['m0'], context, room)
    assert sum(x['utf8_end'] for x in context['inputs'] + context['artifact_proposals']) <= 15000
    assert context['artifact_proposals'][0]['utf8_end'] == 6000
    required = next(x for x in context['inputs'] if x['id'] == 'm0')
    required_ref = next(x for x in context['input_manifest']['sources'] if x.get('message_id') == 'm0')
    assert required_ref['sha256'] == hashlib.sha256(required['text'].encode('utf-8')).hexdigest()
    artifact_ref = next(x for x in context['input_manifest']['sources']
                        if x.get('kind') == 'artifact_version')
    assert artifact_ref['sha256'] == hashlib.sha256(
        context['artifact_proposals'][0]['text'].encode('utf-8')).hexdigest()
    c.fit_context(context)
    assert c.required_predecessors_available(['m0'], context, room)
    assert '_required_context' not in context


def test_fit_trims_optional_not_required(monkeypatch):
    room, task = make(['m0'], ['r' * 5000, 'o' * 5000])
    context = c.shared_context(room, task)
    base = copy.deepcopy(context)
    base.pop('_required_context')
    base['input_manifest']['budget'] = {'max_serialized_utf8_bytes': 0}
    monkeypatch.setattr(c, 'MAX_INPUT', c._serialized_size(base) - 3000)
    c.fit_context(context)
    assert c.required_predecessors_available(['m0'], context, room)
    assert next(x for x in context['inputs'] if x['id'] == 'm1')['truncated']


def test_serialized_budget_failure_preserves_required(monkeypatch):
    room, task = make(['m0'], ['r' * 5000])
    context = c.shared_context(room, task)
    monkeypatch.setattr(c, 'MAX_INPUT', 100)
    with pytest.raises(c.ContextBudgetError, match='^required_input_unavailable$'):
        c.fit_context(context)
    assert c.required_predecessors_available(['m0'], context, room)


@pytest.mark.parametrize('ids', [None, 'm0', [''], [['m0']], ['m0', 'm0'], ['missing'],
                                 ['m%d' % i for i in range(9)]])
def test_invalid_or_unavailable_required_ids_fail_safely(ids):
    room, task = make(ids, ['hello'] * 9)
    context = c.shared_context(room, task)
    with pytest.raises(c.ContextBudgetError, match='^required_input_unavailable$'):
        c.fit_context(context)


def test_shared_budget_insufficient_fails(monkeypatch):
    monkeypatch.setattr(c, 'MAX_SHARED_TEXT_BYTES', 10)
    room, task = make(['m0'], ['한' * 4])
    context = c.shared_context(room, task)
    with pytest.raises(c.ContextBudgetError, match='^required_input_unavailable$'):
        c.fit_context(context)


def test_absent_or_stale_plan_preserves_legacy():
    room, task = make(['m0'], ['x' * 10000])
    del room['tasks'][0]['plan_node']['required_predecessor_message_ids']
    baseline = c.shared_context(room, task)
    room['tasks'][0]['plan_node']['required_predecessor_message_ids'] = ['missing']
    room['tasks'][0]['attempt'] = 2
    assert c.shared_context(room, task) == baseline
    assert baseline['inputs'][0]['utf8_end'] == 6000
    assert '_required_context' not in baseline
