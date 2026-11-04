"""Optional done_when criteria are private, inert proposal data."""
import copy
import json

import pytest

from conftest import leased
from talkabout.domain import Problem
from test_collaboration_proposals import proposal_fixture, body


SECRET = 'criterion private 901d1c'


def create(core, rid, task, key='criteria', **changes):
    return core.create_collaboration_proposal('fixture-a', rid, key, body(task, **changes))


def test_criteria_private_inert_copy_and_replay(proposal_fixture):
    store, core, rid = proposal_fixture
    task = leased(core, rid)
    criteria = [SECRET, 'second\ncriterion']
    receipt = create(core, rid, task, done_when=criteria)
    assert receipt['execution_started'] is False
    assert SECRET not in json.dumps(receipt)
    assert create(core, rid, task, done_when=criteria) == receipt
    with pytest.raises(Problem, match='idempotency_conflict'):
        create(core, rid, task, done_when=['different'])
    owner_row = core.collaboration_proposals('fixture-human', rid)['proposals'][0]
    assert owner_row['done_when'] == criteria
    owner_row['done_when'].append('changed returned list')
    criteria.append('changed caller list')
    assert core.collaboration_proposals('fixture-human', rid)['proposals'][0]['done_when'] == [SECRET, 'second\ncriterion']
    assert SECRET not in json.dumps(core.read('fixture-a', rid))
    assert SECRET not in json.dumps(core.room_snapshot('fixture-a', rid))
    assert SECRET not in json.dumps(store.rooms[rid]['keys'])
    with pytest.raises(Problem):
        core.collaboration_proposals('fixture-b', rid)
    decision = core.decide_collaboration_proposal('fixture-human', rid, 'decide', receipt['id'],
                                                 {'expected_revision': 1, 'decision': 'approve'})
    assert decision['execution_started'] is False
    assert store.rooms[rid]['_work_plan']['nodes'] == []
    assert not store.rooms[rid].get('_plan_dispatch')


def test_omission_stays_omitted(proposal_fixture):
    _, core, rid = proposal_fixture
    task = leased(core, rid)
    create(core, rid, task)
    assert 'done_when' not in core.collaboration_proposals('fixture-human', rid)['proposals'][0]


@pytest.mark.parametrize('criteria', [None, [], ['x'] * 9, 'x', {}, [None], [True], [''], [' \n\t'],
                                      ['x' * 201], ['bad\x00text'], ['bad\ud800text']])
def test_invalid_criteria_rejected_atomically(proposal_fixture, criteria):
    store, core, rid = proposal_fixture
    task = leased(core, rid)
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem, match='invalid_collaboration_proposal'):
        create(core, rid, task, done_when=criteria)
    assert store.rooms[rid] == before


def test_eight_long_criteria_accepted(proposal_fixture):
    _, core, rid = proposal_fixture
    task = leased(core, rid)
    criteria = ['x' * 200] * 8
    create(core, rid, task, done_when=criteria)
    assert core.collaboration_proposals('fixture-human', rid)['proposals'][0]['done_when'] == criteria


def test_utf8_total_limit_includes_criteria(proposal_fixture):
    store, core, rid = proposal_fixture
    task = leased(core, rid)
    # Each field is within character limits, but the JSON encoded body exceeds 8192 bytes.
    changes = {'summary': '한' * 1000, 'requested_output': '한' * 1000,
               'done_when': ['한' * 200] * 4}
    assert len(json.dumps(body(task, **changes), ensure_ascii=False).encode('utf-8')) > 8192
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem, match='invalid_collaboration_proposal'):
        create(core, rid, task, **changes)
    assert store.rooms[rid] == before


def test_authorization_precedes_replay(proposal_fixture):
    store, core, rid = proposal_fixture
    task = leased(core, rid)
    create(core, rid, task, done_when=[SECRET])
    store.revoke('fixture-a')
    with pytest.raises(Problem):
        create(core, rid, task, done_when=[SECRET])
