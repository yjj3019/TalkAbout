"""HTTP contract for private collaboration proposals using fixture bearers."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from conftest import leased
from talkabout.api import create_app


SUMMARY = 'private proposal source 49f0'
OUTPUT = 'private requested output 51a8'
INSTRUCTION = 'owner private instruction 7d42'


@pytest.fixture
def http_room(fixture):
    store, core, rid = fixture
    core.replace_work_plan('fixture-human', rid, 'initial-plan', {'expected_revision': 0, 'nodes': []})
    task = leased(core, rid)
    with TestClient(create_app(store, start_scheduler=False, allow_fixture_bearer=True)) as client:
        yield store, rid, task, client


def request(client, method, path, actor, body=None, key=None):
    headers = {'Authorization': 'Bearer fixture-' + actor}
    if key is not None:
        headers['Idempotency-Key'] = key
    return client.request(method, '/api/v1/' + path, headers=headers, json=body)


def proposal_body(task, **changes):
    return {'task_id': task['id'], **{k: task[k] for k in ('attempt', 'generation', 'membership_revision')},
            'target_id': 'b', 'kind': 'review', 'summary': SUMMARY, 'requested_output': OUTPUT, **changes}


def application(revision):
    return {'expected_proposal_revision': revision, 'expected_plan_revision': 1,
            'acknowledge_untrusted': True,
            'node': {'id': 'reviewed', 'title': 'Reviewed', 'instruction': INSTRUCTION,
                     'assignee': 'b', 'write_scope': 'analysis', 'depends_on': []}}


def create(client, rid, task):
    response = request(client, 'POST', f'rooms/{rid}/collaboration-proposals', 'a', proposal_body(task), 'create')
    assert response.status_code == 200, response.text
    return response.json()


def test_lifecycle_privacy_and_replay(http_room):
    store, rid, task, client = http_room
    base = f'rooms/{rid}/collaboration-proposals'
    before = copy.deepcopy(store.rooms[rid])
    created = create(client, rid, task)
    assert created['state'] == 'proposed' and created['execution_started'] is False
    replay = request(client, 'POST', base, 'a', proposal_body(task), 'create')
    assert replay.status_code == 200 and replay.json() == created
    listed = request(client, 'GET', base, 'human')
    assert listed.status_code == 200
    row = listed.json()['proposals'][0]
    assert row['id'] == created['id'] and row['summary'] == SUMMARY and row['requested_output'] == OUTPUT
    assert row['stale'] is False
    decision_path = f"{base}/{created['id']}/decision"
    decision_body = {'expected_revision': created['revision'], 'decision': 'approve'}
    decision = request(client, 'POST', decision_path, 'human', decision_body, 'decide')
    assert decision.status_code == 200, decision.text
    approved = decision.json()
    assert approved['state'] == 'approved' and approved['execution_started'] is False
    replay = request(client, 'POST', decision_path, 'human', decision_body, 'decide')
    assert replay.status_code == 200 and replay.json() == approved
    apply_path = f"{base}/{created['id']}/apply"
    body = application(approved['revision'])
    applied = request(client, 'POST', apply_path, 'human', body, 'apply')
    assert applied.status_code == 200, applied.text
    assert applied.json()['state'] == 'applied' and applied.json()['execution_started'] is False
    assert applied.json()['plan_revision'] == 2
    replay = request(client, 'POST', apply_path, 'human', body, 'apply')
    assert replay.status_code == 200 and replay.json() == applied.json()
    plan = request(client, 'GET', f'rooms/{rid}/work-plan', 'human')
    assert plan.status_code == 200 and plan.json()['nodes'] == [body['node']]
    expected_tasks = copy.deepcopy(before['tasks'])
    expected_tasks[task['id']]['_collaboration_proposal_attempts'] = [task['attempt']]
    assert store.rooms[rid]['tasks'] == expected_tasks
    for field in ('events', 'last_seq', 'room_revision', 'messages', 'artifacts'):
        assert store.rooms[rid][field] == before[field]
    for actor in ('human', 'a', 'b'):
        for path in (f'rooms/{rid}', f'rooms/{rid}/events', f'rooms/{rid}/snapshot', 'rooms', 'agents/me/events', 'agents/me/snapshot'):
            response = request(client, 'GET', path, actor)
            assert response.status_code == 200, (path, actor, response.text)
            for secret in (SUMMARY, OUTPUT, INSTRUCTION):
                assert secret not in response.text, (path, actor)
    for value in (created, approved, applied.json(), store.rooms[rid]['keys']):
        assert all(secret not in json.dumps(value) for secret in (SUMMARY, OUTPUT, INSTRUCTION))


@pytest.mark.parametrize('value', [[], {}])
@pytest.mark.parametrize('field', ['kind', 'decision'])
def test_unhashable_enums_return_422_atomically(http_room, field, value):
    store, rid, task, client = http_room
    base = f'rooms/{rid}/collaboration-proposals'
    if field == 'kind':
        path, actor, body = base, 'a', proposal_body(task, kind=value)
    else:
        proposal = create(client, rid, task)
        path, actor, body = f"{base}/{proposal['id']}/decision", 'human', {'expected_revision': proposal['revision'], 'decision': value}
    before = copy.deepcopy(store.rooms[rid])
    response = request(client, 'POST', path, actor, body, 'invalid')
    assert response.status_code == 422, response.text
    assert store.rooms[rid] == before


def test_acl_and_rejected_calls_are_atomic(http_room):
    store, rid, task, client = http_room
    base = f'rooms/{rid}/collaboration-proposals'
    before = copy.deepcopy(store.rooms[rid])
    for actor in ('human', 'b'):
        response = request(client, 'POST', base, actor, proposal_body(task), 'denied-' + actor)
        assert response.status_code in (403, 404), response.text
        assert store.rooms[rid] == before
    proposal = create(client, rid, task)
    decision_path = f"{base}/{proposal['id']}/decision"
    decision_body = {'expected_revision': proposal['revision'], 'decision': 'approve'}
    before = copy.deepcopy(store.rooms[rid])
    for actor in ('a', 'b'):
        for method, path, body in [('GET', base, None), ('POST', decision_path, decision_body),
                                   ('POST', f"{base}/{proposal['id']}/apply", application(proposal['revision']))]:
            response = request(client, method, path, actor, body, 'denied-' + actor)
            assert response.status_code == 403, response.text
            assert store.rooms[rid] == before
    response = request(client, 'POST', decision_path, 'human', {'expected_revision': 999, 'decision': 'approve'}, 'stale')
    assert response.status_code == 409 and store.rooms[rid] == before
    response = request(client, 'POST', f"{base}/{proposal['id']}/apply", 'human', application(proposal['revision']), 'unapproved')
    assert response.status_code == 409 and store.rooms[rid] == before


def test_unauthenticated_requests_cannot_read_or_decide(http_room):
    store, rid, task, client = http_room
    proposal = create(client, rid, task)
    base = f'/api/v1/rooms/{rid}/collaboration-proposals'
    before = copy.deepcopy(store.rooms[rid])
    # A reachable host or a known room/proposal identifier is not authority.
    for method, path, body in (
        ('GET', base, None),
        ('POST', base, proposal_body(task)),
        ('POST', f"{base}/{proposal['id']}/decision", {'expected_revision': 1, 'decision': 'approve'}),
        ('POST', f"{base}/{proposal['id']}/apply", application(2)),
    ):
        response = client.request(method, path, json=body, headers={'Idempotency-Key': 'no-auth'})
        assert response.status_code == 401, response.text
        assert store.rooms[rid] == before
        assert SUMMARY not in response.text and OUTPUT not in response.text


@pytest.mark.parametrize('stage', ['create', 'decision', 'apply'])
def test_missing_idempotency_key_is_rejected_atomically(http_room, stage):
    store, rid, task, client = http_room
    base = f'rooms/{rid}/collaboration-proposals'
    if stage == 'create':
        path, actor, body = base, 'a', proposal_body(task)
    else:
        proposal = create(client, rid, task)
        path, actor = f"{base}/{proposal['id']}/decision", 'human'
        body = {'expected_revision': proposal['revision'], 'decision': 'approve'}
        if stage == 'apply':
            response = request(client, 'POST', path, actor, body, 'decide')
            assert response.status_code == 200, response.text
            path = f"{base}/{proposal['id']}/apply"
            body = application(response.json()['revision'])
    before = copy.deepcopy(store.rooms[rid])
    response = request(client, 'POST', path, actor, body)
    assert response.status_code == 422, response.text
    assert store.rooms[rid] == before
