"""JSON booleans and non-integers cannot impersonate lease fence versions."""
import copy

import pytest

from conftest import leased, result
from talkabout.domain import Problem


@pytest.mark.parametrize('field', ['generation', 'membership_revision'])
@pytest.mark.parametrize('value', [True, 1.0, '1', None])
def test_claim_rejects_non_integer_versions_without_mutation(fixture, field, value):
    store, core, rid = fixture
    task = next(task for task in core.read('fixture-a', rid)['tasks'] if task['state'] == 'queued')
    request = {name: task[name] for name in ('generation', 'membership_revision')}
    request[field] = value
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem) as error:
        core.mutation('fixture-a', rid, f"tasks/{task['id']}/claim", 'invalid-fence', request)
    assert error.value.code == 'stale_attempt'
    assert store.rooms[rid] == before


@pytest.mark.parametrize('action', ['heartbeat', 'skip', 'complete'])
@pytest.mark.parametrize('field', ['attempt', 'generation', 'membership_revision'])
@pytest.mark.parametrize('value', [True, 1.0, '1', None])
def test_fencing_rejects_non_integer_versions_without_mutation(fixture, action, field, value):
    store, core, rid = fixture
    task = leased(core, rid)
    request = result(task) if action == 'complete' else {
        name: task[name] for name in ('attempt', 'generation', 'membership_revision')
    }
    request[field] = value
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem) as error:
        core.mutation('fixture-a', rid, f"tasks/{task['id']}/{action}",
                      'invalid-fence', request)
    assert error.value.code == 'stale_attempt'
    assert store.rooms[rid] == before
