"""Optional criteria stay private proposal data; they do not cause execution."""
import copy
import json

import pytest

from conftest import leased
from talkabout.domain import Problem


def proposal(task, **extra):
    return {'task_id': task['id'], 'attempt': task['attempt'], 'generation': task['generation'],
            'membership_revision': task['membership_revision'], 'target_id': 'b', 'kind': 'review',
            'summary': 'Review', 'requested_output': 'Find risks', **extra}


def setup_proposal(fixture):
    store, core, rid = fixture
    core.replace_work_plan('fixture-human', rid, 'plan', {'expected_revision': 0, 'nodes': []})
    task = leased(core, rid)
    return store, core, rid, task


def test_criteria_private_replayed_and_no_execution(fixture):
    store, core, rid, task = setup_proposal(fixture)
    marker = 'private completion criterion 7ac3'
    payload = proposal(task, done_when=[marker])
    before_tasks = copy.deepcopy(store.rooms[rid]['tasks'])
    receipt = core.create_collaboration_proposal('fixture-a', rid, 'proposal', payload)
    assert receipt['execution_started'] is False
    assert core.create_collaboration_proposal('fixture-a', rid, 'proposal', payload) == receipt
    assert marker in json.dumps(core.collaboration_proposals('fixture-human', rid))
    assert marker not in json.dumps(core.read('fixture-a', rid))
    assert marker not in json.dumps(core.read('fixture-b', rid))
    after_tasks = copy.deepcopy(store.rooms[rid]['tasks'])
    for item in after_tasks.values():
        item.pop('_collaboration_proposal_attempts', None)
    assert after_tasks == before_tasks
    with pytest.raises(Problem) as exc:
        core.create_collaboration_proposal('fixture-a', rid, 'proposal', proposal(task, done_when=['changed']))
    assert exc.value.status == 409


def test_legacy_omission_keeps_row_shape(fixture):
    _, core, rid, task = setup_proposal(fixture)
    core.create_collaboration_proposal('fixture-a', rid, 'legacy', proposal(task))
    assert 'done_when' not in core.collaboration_proposals('fixture-human', rid)['proposals'][0]


@pytest.mark.parametrize('criteria', [[], [' '], ['x'] * 9, ['x' * 201], ['x\x00y'], ['bad\ud800'], [1], None])
def test_invalid_criteria_cannot_mutate(fixture, criteria):
    store, core, rid, task = setup_proposal(fixture)
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem) as exc:
        core.create_collaboration_proposal('fixture-a', rid, 'invalid', proposal(task, done_when=criteria))
    assert exc.value.status == 422
    assert store.rooms[rid] == before


def test_whole_body_byte_limit_includes_criteria(fixture):
    store, core, rid, task = setup_proposal(fixture)
    before = copy.deepcopy(store.rooms[rid])
    body = proposal(task, summary='한' * 1000, requested_output='글' * 1000, done_when=['한' * 200] * 4)
    assert len(json.dumps(body, ensure_ascii=False).encode('utf-8')) > 8192
    with pytest.raises(Problem) as exc:
        core.create_collaboration_proposal('fixture-a', rid, 'too-large', body)
    assert exc.value.status == 422
    assert store.rooms[rid] == before
