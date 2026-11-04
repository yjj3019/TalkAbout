"""Applying approved collaboration proposals changes only the private draft."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from conftest import leased
from talkabout.domain import Problem


PROPOSAL_SECRET = 'untrusted proposal secret 2e91'
OUTPUT_SECRET = 'untrusted requested output 5a70'
PLAN_SECRET = 'human reviewed private instruction 8cf2'


def node(name='reviewed', **changes):
    return {'id': name, 'title': 'Private reviewed title', 'instruction': PLAN_SECRET,
            'assignee': 'b', 'write_scope': 'analysis', 'depends_on': ['existing'], **changes}


def setup(store_core_room, decision='approve'):
    store, core, rid = store_core_room
    existing = node('existing', assignee='a', depends_on=[])
    core.replace_work_plan('fixture-human', rid, 'initial-plan',
                           {'expected_revision': 0, 'nodes': [existing]})
    task = leased(core, rid)
    proposal = core.create_collaboration_proposal('fixture-a', rid, 'proposal', {
        'task_id': task['id'], **{k: task[k] for k in ('attempt', 'generation', 'membership_revision')},
        'target_id': 'b', 'kind': 'review', 'summary': PROPOSAL_SECRET,
        'requested_output': OUTPUT_SECRET})
    if decision is not None:
        proposal = core.decide_collaboration_proposal('fixture-human', rid, 'decision', proposal['id'],
                    {'expected_revision': proposal['revision'], 'decision': decision})
    return store, core, rid, proposal, existing


def application(proposal, **changes):
    return {'expected_proposal_revision': proposal['revision'], 'expected_plan_revision': 1,
            'node': node(), 'acknowledge_untrusted': True, **changes}


def apply(core, rid, proposal, body=None, key='apply', token='fixture-human'):
    return core.apply_collaboration_proposal(token, rid, key, proposal['id'],
                                             application(proposal) if body is None else body)


def assert_denied_atomically(store, rid, call, status=None):
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem) as exc:
        call()
    if status is not None:
        assert exc.value.status == status
    assert store.rooms[rid] == before


def test_apply_appends_reviewed_node_and_replays_without_execution(fixture):
    store, core, rid, proposal, existing = setup(fixture)
    before = copy.deepcopy(store.rooms[rid])
    cursor = core.events('fixture-a')['next_cursor']
    body = application(proposal)
    receipt = apply(core, rid, proposal, body)
    assert receipt == {'id': proposal['id'], 'revision': proposal['revision'] + 1,
                       'state': 'applied', 'plan_revision': 2, 'execution_started': False}
    assert core.work_plan('fixture-human', rid) == {'revision': 2, 'nodes': [existing, body['node']]}
    row = core.collaboration_proposals('fixture-human', rid)['proposals'][0]
    assert row['state'] == 'applied' and row['revision'] == proposal['revision'] + 1
    assert apply(core, rid, proposal, body) == receipt
    assert_denied_atomically(store, rid, lambda: apply(core, rid, proposal,
                             application(proposal, node=node(title='changed'))), 409)
    assert_denied_atomically(store, rid, lambda: apply(core, rid, proposal, body, key='new'), 409)
    after = store.rooms[rid]
    for field in ('tasks', 'events', 'last_seq', 'room_revision', 'messages', 'artifacts'):
        assert after[field] == before[field], field
    assert after.get('_plan_dispatch') == before.get('_plan_dispatch')
    assert core.events('fixture-a', cursor)['events'] == []
    for projection in (after['keys'], receipt, core.read('fixture-a', rid),
                       core.read('fixture-human', rid), after['events']):
        text = json.dumps(projection)
        for secret in (PROPOSAL_SECRET, OUTPUT_SECRET, PLAN_SECRET, 'Private reviewed title'):
            assert secret not in text


@pytest.mark.parametrize('change', [
    {'expected_proposal_revision': True}, {'expected_proposal_revision': 0},
    {'expected_proposal_revision': -1}, {'expected_plan_revision': True},
    {'expected_plan_revision': -1}, {'acknowledge_untrusted': False},
    {'acknowledge_untrusted': 1}, {'acknowledge_untrusted': None},
    {'node': None}, {'node': []}, {'extra': 'unexpected'},
])
def test_strict_application_body_is_atomic(fixture, change):
    store, core, rid, proposal, _ = setup(fixture)
    assert_denied_atomically(store, rid, lambda: apply(core, rid, proposal, application(proposal, **change)), 422)


@pytest.mark.parametrize('missing', ['expected_proposal_revision', 'expected_plan_revision', 'node', 'acknowledge_untrusted'])
def test_missing_application_field_is_atomic(fixture, missing):
    store, core, rid, proposal, _ = setup(fixture)
    body = application(proposal)
    del body[missing]
    assert_denied_atomically(store, rid, lambda: apply(core, rid, proposal, body), 422)


@pytest.mark.parametrize('bad_node', [
    node('existing'), node('../invalid'), node(assignee='human'), node(assignee='missing'),
    node(depends_on=['missing']), node(depends_on=['reviewed']),
    node(write_scope='execute'), node(title=' '), node(instruction='bad\x00text'),
    node(instruction='bad\ud800text'), node(extra='unexpected'),
])
def test_node_is_validated_against_full_existing_plan(fixture, bad_node):
    store, core, rid, proposal, _ = setup(fixture)
    assert_denied_atomically(store, rid, lambda: apply(core, rid, proposal,
                             application(proposal, node=bad_node)), 422)


@pytest.mark.parametrize('decision', [None, 'reject'])
def test_only_approved_proposals_apply(fixture, decision):
    store, core, rid, proposal, _ = setup(fixture, decision)
    assert_denied_atomically(store, rid, lambda: apply(core, rid, proposal), 409)


@pytest.mark.parametrize('change', [
    {'expected_proposal_revision': 1}, {'expected_plan_revision': 0}, {'expected_plan_revision': 2},
])
def test_revision_conflicts_are_atomic(fixture, change):
    store, core, rid, proposal, _ = setup(fixture)
    assert_denied_atomically(store, rid, lambda: apply(core, rid, proposal, application(proposal, **change)), 409)


@pytest.mark.parametrize('member,field,value', [
    ('a', 'status', 'left'), ('b', 'status', 'left'),
    ('a', 'revision', 999), ('b', 'revision', 999),
    ('a', 'epoch', 999), ('b', 'epoch', 999),
])
def test_disabled_or_stale_proposal_member_is_rejected(fixture, member, field, value):
    store, core, rid, proposal, _ = setup(fixture)
    store.rooms[rid]['members'][member][field] = value
    assert_denied_atomically(store, rid, lambda: apply(core, rid, proposal), 409)


def test_stale_generation_and_active_run_are_rejected(fixture):
    store, core, rid, proposal, _ = setup(fixture)
    room = store.rooms[rid]
    room['generation'] += 1
    assert_denied_atomically(store, rid, lambda: apply(core, rid, proposal), 409)
    room['generation'] -= 1
    room['_plan_dispatch'] = {'state': 'running'}
    assert_denied_atomically(store, rid, lambda: apply(core, rid, proposal), 409)


def test_owner_authorization_and_revocation_precede_replay(fixture):
    store, core, rid, proposal, _ = setup(fixture)
    for token in ('fixture-a', 'fixture-b'):
        assert_denied_atomically(store, rid, lambda: apply(core, rid, proposal, token=token), 403)
    receipt = apply(core, rid, proposal)
    assert receipt['state'] == 'applied'
    store.revoke('fixture-human')
    assert_denied_atomically(store, rid, lambda: apply(core, rid, proposal), 401)


def test_missing_plan_and_closed_room_rejected(fixture):
    store, core, rid, proposal, _ = setup(fixture)
    plan = store.rooms[rid].pop('_work_plan')
    assert_denied_atomically(store, rid, lambda: apply(core, rid, proposal), 404)
    store.rooms[rid]['_work_plan'] = plan
    store.rooms[rid]['state'] = 'closed'
    assert_denied_atomically(store, rid, lambda: apply(core, rid, proposal), 409)


def race_apply(fixture):
    _, core, rid, proposal, existing = setup(fixture)
    def attempt(index):
        try:
            return apply(core, rid, proposal, application(proposal, node=node('candidate' + str(index))),
                         key='race-' + str(index))
        except Problem as exc:
            return exc
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, range(2)))
    assert sum(isinstance(result, dict) for result in outcomes) == 1
    errors = [result for result in outcomes if isinstance(result, Problem)]
    assert len(errors) == 1 and errors[0].status == 409
    plan = core.work_plan('fixture-human', rid)
    assert plan['revision'] == 2 and len(plan['nodes']) == 2 and plan['nodes'][0] == existing
    assert plan['nodes'][1]['id'] in {'candidate0', 'candidate1'}


def test_memory_concurrent_apply_is_cas(fixture):
    race_apply(fixture)


def test_postgres_concurrent_apply_is_cas(postgres_fixture):
    race_apply(postgres_fixture)
