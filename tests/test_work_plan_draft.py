"""A2 drafts are inert, owner-only metadata; they never enqueue work."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from talkabout.api import create_app
from talkabout.domain import Problem, token_hash


def node(name='one', assignee='a', deps=None, **changes):
    return {'id': name, 'title': 'Draft title', 'instruction': 'Draft-only secret instruction',
            'assignee': assignee, 'write_scope': 'analysis', 'depends_on': deps or [], **changes}


def write(core, rid, revision, nodes, key='plan-key', token='fixture-human'):
    return core.replace_work_plan(token, rid, key, {'expected_revision': revision, 'nodes': nodes})


def test_create_replace_replay_and_conflicts(fixture):
    store, core, rid = fixture
    assert core.work_plan('fixture-human', rid) == {'revision': 0, 'nodes': []}
    first = [node(), node('two', 'b', ['one'], write_scope='owner_approved_workspace')]
    assert write(core, rid, 0, first) == {'revision': 1}
    assert write(core, rid, 0, first) == {'revision': 1}
    assert core.work_plan('fixture-human', rid) == {'revision': 1, 'nodes': first}
    with pytest.raises(Problem, match='idempotency_conflict'):
        write(core, rid, 0, [node('other')])
    with pytest.raises(Problem, match='work_plan_revision_conflict'):
        write(core, rid, 0, [], 'stale')
    # Forward references are allowed as long as the graph is acyclic and internal.
    second = [node('two', 'b', ['one']), node()]
    assert write(core, rid, 1, second, 'next') == {'revision': 2}
    assert write(core, rid, 2, [], 'clear') == {'revision': 3}
    assert write(core, rid, 0, first) == {'revision': 1}  # historical receipt only, not current plan
    assert core.work_plan('fixture-human', rid) == {'revision': 3, 'nodes': []}
    assert 'Draft-only secret instruction' not in json.dumps(store.rooms[rid]['keys'])


def test_cas_concurrent_writers_allow_one(fixture):
    _, core, rid = fixture
    def update(index):
        try:
            return write(core, rid, 0, [node('n' + str(index))], 'key-' + str(index))
        except Problem as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(update, range(2)))
    assert outcomes.count({'revision': 1}) == 1
    assert outcomes.count('work_plan_revision_conflict') == 1


@pytest.mark.parametrize('nodes,code', [
    ([node('same'), node('same')], 'invalid_work_plan'),
    ([node('../bad')], 'invalid_work_plan'),
    ([node('one', deps=['outside'])], 'invalid_work_plan_dependency'),
    ([node('one', deps=['one'])], 'work_plan_cycle'),
    ([node('one', deps=['two']), node('two', deps=['one'])], 'work_plan_cycle'),
    ([node('one', deps=['two', 'two']), node('two')], 'invalid_work_plan'),
    ([node(assignee='human')], 'invalid_work_plan_assignee'),
    ([node(assignee='missing')], 'invalid_work_plan_assignee'),
    ([node(write_scope='../../some/path')], 'invalid_work_plan'),
    ([node(write_scope=[])], 'invalid_work_plan'),
    ([node(title=' ' * 5)], 'invalid_work_plan'),
    ([node(title='x' * 201)], 'invalid_work_plan'),
    ([node(instruction='x' * 2001)], 'invalid_work_plan'),
    ([node(instruction='inert\x00text')], 'invalid_work_plan'),
    ([node(instruction='unpaired\ud800')], 'invalid_work_plan'),
    ([node(**{'path': 'C:/secret'})], 'invalid_work_plan'),
    ([node('n' + str(i)) for i in range(17)], 'invalid_work_plan'),
])
def test_invalid_replacements_are_atomic(fixture, nodes, code):
    store, core, rid = fixture
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem, match=code):
        write(core, rid, 0, nodes, 'invalid')
    assert store.rooms[rid] == before


@pytest.mark.parametrize('body', [
    {}, {'expected_revision': True, 'nodes': []}, {'expected_revision': -1, 'nodes': []},
    {'expected_revision': 0, 'nodes': {}, 'extra': 'x'},
])
def test_invalid_body(fixture, body):
    _, core, rid = fixture
    with pytest.raises(Problem, match='invalid_work_plan'):
        core.replace_work_plan('fixture-human', rid, 'bad', body)


def test_removed_assignee_refused_and_existing_plan_remains_inert(fixture):
    store, core, rid = fixture
    write(core, rid, 0, [node()])
    store.rooms[rid]['members']['a']['status'] = 'left'
    with pytest.raises(Problem, match='invalid_work_plan_assignee'):
        write(core, rid, 1, [node()], 'changed')
    assert core.work_plan('fixture-human', rid)['revision'] == 1


def test_permissions_revocation_and_epoch_before_replay(fixture):
    store, core, rid = fixture
    plan = [node()]
    write(core, rid, 0, plan)
    with pytest.raises(Problem) as error:
        core.work_plan('fixture-a', rid)
    assert error.value.status == 403
    with pytest.raises(Problem) as error:
        write(core, rid, 0, plan, token='fixture-a')
    assert error.value.status == 403
    member = store.rooms[rid]['members']['human']
    member['permissions'].remove('room:control')
    with pytest.raises(Problem, match='forbidden'):
        write(core, rid, 0, plan)
    with pytest.raises(Problem, match='forbidden'):
        core.work_plan('fixture-human', rid)
    member['permissions'].append('room:control')
    member['epoch'] += 1
    with pytest.raises(Problem, match='not_found'):
        write(core, rid, 0, plan)
    with pytest.raises(Problem, match='not_found'):
        core.work_plan('fixture-human', rid)
    store.revoke('fixture-human')
    with pytest.raises(Problem) as error:
        write(core, rid, 0, plan)
    assert error.value.status == 401


def test_other_owner_cannot_read_or_write(fixture):
    store, core, rid = fixture
    store.provision({'id': 'other', 'owner': 'different-owner', 'kind': 'human',
                     'display_name': 'Other', 'handle': 'other'}, 'fixture-other', {'room:read', 'room:control'})
    store.rooms[rid]['members']['other'] = copy.deepcopy(store.rooms[rid]['members']['human'])
    for call in (lambda: core.work_plan('fixture-other', rid),
                 lambda: write(core, rid, 0, [], 'other', 'fixture-other')):
        with pytest.raises(Problem, match='forbidden'):
            call()


def test_same_workspace_other_human_and_rejoined_author_cannot_read_draft(fixture):
    store, core, rid = fixture
    write(core, rid, 0, [node()])
    owner = store.auth('fixture-human')['owner']
    store.provision({'id': 'otherhuman', 'owner': owner, 'kind': 'human',
                     'display_name': 'Other', 'handle': 'otherhuman'}, 'other-human', {'room:read', 'room:control'})
    store.rooms[rid]['members']['otherhuman'] = copy.deepcopy(store.rooms[rid]['members']['human'])
    for call in (lambda: core.work_plan('other-human', rid),
                 lambda: write(core, rid, 1, [], 'other-key', 'other-human')):
        with pytest.raises(Problem, match='not_found'):
            call()
    store.rooms[rid]['members']['human']['epoch'] += 1
    with pytest.raises(Problem, match='not_found'):
        core.work_plan('fixture-human', rid)


def test_closed_room_is_read_only_for_drafts(fixture):
    store, core, rid = fixture
    write(core, rid, 0, [node()])
    store.rooms[rid]['state'] = 'closed'
    assert core.work_plan('fixture-human', rid)['revision'] == 1
    with pytest.raises(Problem, match='room_closed'):
        write(core, rid, 1, [], 'closed')


def test_requires_read_scope_and_control_scope(fixture):
    store, core, rid = fixture
    credential = store.credentials[token_hash('fixture-human')]
    credential['scopes'].remove('room:read')
    with pytest.raises(Problem, match='forbidden'):
        core.work_plan('fixture-human', rid)
    credential['scopes'].append('room:read')
    credential['scopes'].remove('room:control')
    with pytest.raises(Problem, match='forbidden'):
        write(core, rid, 0, [node()])


@pytest.mark.parametrize('mode', ['bounded_rounds', 'free_discussion', 'co_creation'])
def test_plan_is_not_projected_scheduled_or_emitted(fixture, mode):
    store, core, rid = fixture
    store.rooms[rid]['mode'] = mode
    before = copy.deepcopy(store.rooms[rid])
    write(core, rid, 0, [node()])
    after = store.rooms[rid]
    for field in ('tasks', 'events', 'last_seq', 'room_revision', 'topic', 'mode', 'messages', 'artifacts'):
        assert after[field] == before[field]
    assert 'Draft-only secret instruction' not in json.dumps(core.read('fixture-a', rid))
    assert 'Draft-only secret instruction' not in json.dumps(core.read('fixture-human', rid))
    assert 'Draft-only secret instruction' not in json.dumps(core.rooms('fixture-a'))
    assert 'Draft-only secret instruction' not in json.dumps(core.room_snapshot('fixture-a', rid))
    assert 'Draft-only secret instruction' not in json.dumps(after['events'])
    core.tick('fixture-human', rid)
    assert 'Draft-only secret instruction' not in json.dumps(core.read('fixture-a', rid))


def test_postgres_atomic_compare_and_swap(postgres_fixture):
    _, core, rid = postgres_fixture
    def update(index):
        try:
            return write(core, rid, 0, [node('n' + str(index))], 'pg-key-' + str(index))
        except Problem as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(update, range(2)))
    assert outcomes.count({'revision': 1}) == 1
    assert outcomes.count('work_plan_revision_conflict') == 1
    assert core.work_plan('fixture-human', rid)['revision'] == 1


def test_http_revocation_blocks_replay_and_read(fixture):
    store, _, rid = fixture
    client = TestClient(create_app(store, start_scheduler=False, allow_fixture_bearer=True))
    path = f'/api/v1/rooms/{rid}/work-plan'
    headers = {'Authorization': 'Bearer fixture-human', 'Idempotency-Key': 'same'}
    body = {'expected_revision': 0, 'nodes': [node()]}
    assert client.post(path, headers=headers, json=body).status_code == 200
    store.revoke('fixture-human')
    assert client.post(path, headers=headers, json=body).status_code == 401
    assert client.get(path, headers=headers).status_code == 401


def test_http_routes_authorization_and_no_disclosure(fixture):
    store, core, rid = fixture
    client = TestClient(create_app(store, start_scheduler=False, allow_fixture_bearer=True))
    path = f'/api/v1/rooms/{rid}/work-plan'
    human = {'Authorization': 'Bearer fixture-human', 'Idempotency-Key': 'http-plan'}
    agent = {'Authorization': 'Bearer fixture-a'}
    assert client.get(path, headers=human).json() == {'revision': 0, 'nodes': []}
    body = {'expected_revision': 0, 'nodes': [node()]}
    assert client.post(path, headers=human, json=body).json() == {'revision': 1}
    assert client.post(path, headers=human, json=body).json() == {'revision': 1}
    assert client.get(path, headers=human).json()['nodes'] == body['nodes']
    assert client.get(path, headers=agent).status_code == 403
    assert client.post(path, headers={**agent, 'Idempotency-Key': 'other'}, json=body).status_code == 403
    assert client.put(path, headers=human, json=body).status_code == 404
    for suffix in ('', '/events', '/snapshot', '/tasks'):
        response = client.get(f'/api/v1/rooms/{rid}' + suffix, headers=agent)
        assert 'Draft-only secret instruction' not in response.text
