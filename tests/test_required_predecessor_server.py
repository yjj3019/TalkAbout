"""Synthetic server-side availability gates; not proof of full worker delivery."""
import copy
import uuid

import pytest

from talkabout.domain import Problem
from test_work_plan_dispatch import claim as legacy_claim, complete, dispatch, node, prepare, run, write, plan_task


def claim(core, rid, limit=8):
    task = plan_task(core, rid)
    if not task.get('input_protocol_required'):
        return legacy_claim(core, rid)
    body = {k: task[k] for k in ('generation', 'membership_revision')}
    body.update(execution_protocol='server_start_v1', input_protocol='required_predecessors_v1')
    if limit is not None:
        body['required_predecessors_limit'] = limit
    return core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/claim', 'claim-' + task['id'], body)


def chain():
    return [node('one'), node('two'),
            node('child', deps=['one', 'two'], required_predecessors=['one'])]


def two_required(fixture):
    store, core, rid = fixture
    nodes = [node('one'), node('two'),
             node('child', deps=['one', 'two'], required_predecessors=['one', 'two'])]
    prepare(store, core, rid, nodes, 'fixture-a')
    dispatch(core, rid)
    complete(core, rid, claim(core, rid))
    complete(core, rid, claim(core, rid))
    task = plan_task(core, rid)
    assert task['plan_node']['required_predecessor_message_ids'] is not None
    return store, core, rid, task


@pytest.mark.parametrize('limit', [None, 1])
def test_claim_with_absent_or_too_small_capability_fails_node_early(fixture, limit):
    store, core, rid, task = two_required(fixture)
    with pytest.raises(Problem, match='required_input_unavailable') as error:
        claim(core, rid, limit=limit)
    assert error.value.status == 409
    raw = store.rooms[rid]['tasks'][task['id']]
    assert raw['state'] == 'failed' and raw['_failure_reason'] == 'input_unavailable'
    child = next(n for n in run(core, rid)['nodes'] if n['id'] == 'child')
    assert (child['state'], child['failure']) == ('failed', 'input_unavailable')


@pytest.mark.parametrize('limit', [2, 8])
def test_claim_with_sufficient_capability_proceeds(fixture, limit):
    store, core, rid, task = two_required(fixture)
    claimed = claim(core, rid, limit=limit)
    assert claimed['state'] == 'leased' and claimed['attempt'] == 1


@pytest.mark.parametrize('limit', [0, 16, '8', True])
def test_claim_rejects_out_of_range_capability(fixture, limit):
    store, core, rid, task = two_required(fixture)
    with pytest.raises(Problem, match='invalid_required_predecessors_limit') as error:
        claim(core, rid, limit=limit)
    assert error.value.status == 422
    assert store.rooms[rid]['tasks'][task['id']]['state'] == 'queued'


def test_postgres_insufficient_capability_failure_is_committed(postgres_fixture):
    """The early node failure must survive the 409 on a real PostgreSQL transaction."""
    _, core, rid, task = two_required(postgres_fixture)
    body = {k: task[k] for k in ('generation', 'membership_revision')}
    body.update(execution_protocol='server_start_v1', input_protocol='required_predecessors_v1',
                required_predecessors_limit=1)
    with pytest.raises(Problem, match='required_input_unavailable') as error:
        core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/claim', 'claim-limit', body)
    assert error.value.status == 409
    child = next(n for n in run(core, rid)['nodes'] if n['id'] == 'child')
    assert (child['state'], child['failure']) == ('failed', 'input_unavailable')
    projection = next(t for t in core.read('fixture-a', rid)['tasks'] if t['id'] == task['id'])
    assert projection['state'] == 'failed'


def test_validation_and_draft_roundtrip(fixture):
    store, core, rid = fixture
    bad = node('child', deps=['one'], required_predecessors=['other'])
    with pytest.raises(Problem, match='invalid_work_plan'):
        write(core, rid, [node('one'), bad])
    bad['required_predecessors'] = ['one', 'one']
    with pytest.raises(Problem, match='invalid_work_plan'):
        write(core, rid, [node('one'), bad])
    prepare(store, core, rid, chain(), 'fixture-a')
    assert core.work_plan('fixture-human', rid)['nodes'][2]['required_predecessors'] == ['one']
    dispatch(core, rid)
    assert store.rooms[rid]['_plan_dispatch']['nodes'][2]['required_predecessors'] == ['one']


def test_apply_preserves_required_field(fixture):
    from test_collaboration_plan_apply import setup, application, apply, node as applied_node
    store, core, rid, proposal, _ = setup(fixture)
    reviewed = applied_node(required_predecessors=['existing'])
    apply(core, rid, proposal, application(proposal, node=reviewed))
    assert core.work_plan('fixture-human', rid)['nodes'][1]['required_predecessors'] == ['existing']


def test_hidden_at_admission_fails_without_disclosure(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, chain(), 'fixture-a')
    dispatch(core, rid)
    first = complete(core, rid, claim(core, rid))
    store.rooms[rid]['members']['a']['read_start_seq'] = first['message']['seq'] + 1
    complete(core, rid, claim(core, rid))
    child = next(n for n in run(core, rid)['nodes'] if n['id'] == 'child')
    assert child['state'] == 'failed' and child['failure'] == 'input_unavailable'
    assert first['message']['id'] not in str(child)


def test_missing_at_admission_fails_closed(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, chain(), 'fixture-a')
    dispatch(core, rid)
    first = claim(core, rid)
    complete(core, rid, first)
    del store.rooms[rid]['tasks'][first['id']]['_plan_result']
    complete(core, rid, claim(core, rid))
    child = next(n for n in run(core, rid)['nodes'] if n['id'] == 'child')
    assert child['state'] == 'failed' and child['failure'] == 'input_unavailable'
    assert child['task_id'] is None


@pytest.mark.parametrize('stage', ['reservation', 'start'])
def test_revoked_visibility_rejects_transaction(fixture, stage):
    store, core, rid = fixture
    prepare(store, core, rid, [node('one'), node('child', deps=['one'], required_predecessors=['one'])], 'fixture-a')
    dispatch(core, rid)
    result = complete(core, rid, claim(core, rid))
    task = claim(core, rid)
    raw = store.rooms[rid]['tasks'][task['id']]
    assert raw['_plan']['required_predecessors'] == ['one']
    view = core.read('fixture-a', rid)
    projected = next(t['plan_node'] for t in view['tasks'] if t['id'] == task['id'])
    assert projected['required_predecessor_message_ids'] == [result['message']['id']]
    body = {k: task[k] for k in ('attempt', 'generation', 'membership_revision')}
    body['reservation_id'] = uuid.uuid4().hex + uuid.uuid4().hex
    path = 'tasks/' + task['id'] + '/reservations'
    if stage == 'start':
        assert core.mutation('fixture-a', rid, path, 'reserve-child', body)['reserved']
        path = 'tasks/' + task['id'] + '/start'
    store.rooms[rid]['members']['a']['read_start_seq'] = result['message']['seq'] + 1
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem, match='input_unavailable') as exc:
        core.mutation('fixture-a', rid, path, 'revoked-child', body)
    assert exc.value.status == 409
    assert store.rooms[rid] == before
    assert result['message']['id'] not in str(exc.value)
