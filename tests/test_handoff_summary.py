"""Pure current-dispatch summaries; no store, API, scheduler or provider."""
import copy
import json

import pytest

from talkabout.handoff import project_handoff


def room():
    return {'_plan_dispatch': {'id': 'current', 'generation': 1, 'state': 'aborted', 'nodes': [
        {'id': 'a', 'state': 'failed', 'task_id': 't1', 'assignee': 'agent', 'instruction': 'SECRET PLAN'},
        {'id': 'b', 'state': 'blocked', 'task_id': None},
        {'id': 'c', 'state': 'completed', 'task_id': 't2', 'assignee': 'agent'}]},
        'tasks': {
            't1': {'id': 't1', 'purpose': 'plan_node', 'state': 'uncertain', 'attempt': 1, 'generation': 1, 'assignee': 'agent',
                   '_plan': {'run_id': 'current', 'node_id': 'a'}, 'result': 'SECRET RESULT'},
            't2': {'id': 't2', 'purpose': 'plan_node', 'state': 'completed', 'generation': 1, 'assignee': 'agent',
                   '_plan': {'run_id': 'current', 'node_id': 'c'}},
            'old': {'id': 'old', 'purpose': 'plan_node', 'state': 'uncertain',
                    '_plan': {'run_id': 'old-run', 'node_id': 'old'}},
        }, 'messages': {'secret': 'SECRET MESSAGE'}, 'keys': {'SECRET KEY': 'token'}}


def test_empty_and_current_run_counts_without_mutation_or_leaks():
    assert project_handoff({}) == {'run': None}
    source = room()
    before = copy.deepcopy(source)
    out = project_handoff(source)
    assert out == {'run': {'node_counts': {'pending': 0, 'queued': 0, 'completed': 1,
                                           'failed': 1, 'blocked': 1, 'unknown': 0},
                           'uncertain_tasks': 1, 'acknowledged_uncertain_tasks': 0,
                           'unknown_associations': 0}}
    assert source == before
    encoded = json.dumps(out)
    for secret in ('SECRET', 'current', 'old-run', 't1', 'token'):
        assert secret not in encoded
    out['run']['node_counts']['failed'] = 999
    assert source == before


def test_acknowledgement_is_not_removal_of_uncertainty():
    source = room()
    source['tasks']['t1']['resolution'] = {'attempt': 1, 'external_effect': 'unknown',
                                            'note': 'SECRET NOTE'}
    summary = project_handoff(source)['run']
    assert summary['uncertain_tasks'] == summary['acknowledged_uncertain_tasks'] == 1
    assert 'SECRET' not in json.dumps(summary)
    source['tasks']['t1']['resolution']['attempt'] = 2
    assert project_handoff(source)['run']['acknowledged_uncertain_tasks'] == 0


@pytest.mark.parametrize('change', [
    lambda r: r['tasks']['t1']['_plan'].update(run_id='old'),
    lambda r: r['tasks']['t1']['_plan'].update(node_id='wrong'),
    lambda r: r['tasks']['t1'].update(id='other'),
    lambda r: r['tasks']['t1'].update(purpose='other'),
    lambda r: r['tasks'].pop('t1'),
    lambda r: r['_plan_dispatch']['nodes'][0].update(task_id=['t1']),
    lambda r: r['_plan_dispatch']['nodes'][1].update(task_id='t1'),
    lambda r: r['_plan_dispatch']['nodes'][1].update(id='a'),
])
def test_bad_association_is_unknown_not_inferred(change):
    source = room()
    change(source)
    before = copy.deepcopy(source)
    summary = project_handoff(source)['run']
    assert summary['unknown_associations'] >= 1
    assert summary['uncertain_tasks'] == 0
    assert source == before


def test_missing_and_malformed_data_are_not_silent_success():
    source = room()
    source['_plan_dispatch']['nodes'].extend([None, {'id': 'd', 'state': 'queued', 'task_id': None},
                                               {'id': 'e', 'state': 'mystery', 'task_id': None}])
    summary = project_handoff(source)['run']
    assert summary['node_counts']['unknown'] == 2
    assert summary['unknown_associations'] == 3
    assert project_handoff({'_plan_dispatch': {'id': 'r', 'nodes': None}})['run']['unknown_associations'] >= 1
