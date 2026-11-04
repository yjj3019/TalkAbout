"""Required predecessor protocol through real server projections and a local worker."""
import copy
import json

import pytest

from conftest import LocalClient
from talkabout.worker import Journal, Worker
from test_work_plan_dispatch import claim, complete, dispatch, node, plan_task, prepare


class RecordingRuntime:
    def __init__(self):
        self.contexts = []

    def run(self, context, cancel):
        assert not cancel.is_set()
        self.contexts.append(copy.deepcopy(context))
        return 'child result'


class TargetClient(LocalClient):
    def __init__(self, store, rid, target, fault=None):
        super().__init__(store)
        self.rid, self.target, self.fault = rid, target, fault
        self.claim_body = None
        self.projection = None

    def request(self, method, path, body=None, key=None, params=None):
        response = super().request(method, path, body, key, params)
        if method == 'GET' and path == 'agents/me/events':
            response['events'] = [event for event in response['events']
                                  if event.get('type') != 'task.queued' or event.get('task_id') == self.target]
        if method == 'POST' and path.endswith('/claim'):
            self.claim_body = copy.deepcopy(body)
            if self.fault == 'old-server':
                response.pop('input_protocol', None)
        if method == 'GET' and path == f'rooms/{self.rid}':
            current = next(t for t in response['tasks'] if t['id'] == self.target)
            if current['state'] == 'leased':
                self.projection = copy.deepcopy(current)
                if self.fault == 'missing-projection':
                    current.pop('plan_node', None)
        return response


def setup_plan(fixture, empty=False):
    store, core, rid = fixture
    nodes = ([node('child', required_predecessors=[])] if empty else
             [node('parent'), node('child', deps=['parent'], required_predecessors=['parent'])])
    prepare(store, core, rid, nodes, 'fixture-a')
    dispatch(core, rid)
    predecessor = None
    if not empty:
        predecessor = complete(core, rid, claim(core, rid), text='Complete predecessor: α\nsecond line; exact ending.')['message']
    task = plan_task(core, rid)
    assert task['input_protocol_required'] is True
    return store, core, rid, task['id'], predecessor


@pytest.mark.parametrize('case', ['chain', 'empty', 'old-server', 'missing-projection'])
def test_required_protocol_end_to_end(fixture, tmp_path, case):
    store, core, rid, target, predecessor = setup_plan(fixture, empty=case == 'empty')
    client = TargetClient(store, rid, target, fault=case)
    journal = Journal(tmp_path / 'worker.sqlite')
    runtime = RecordingRuntime()
    try:
        outcome = Worker(client, journal, runtime).step()
        assert client.claim_body['input_protocol'] == 'required_predecessors_v1'
        assert client.claim_body['execution_protocol'] == 'server_start_v1'
        assert client.claim_body['required_predecessors_limit'] == 8
        if case == 'old-server':
            assert outcome == 'input_protocol_not_supported'
        elif case == 'missing-projection':
            assert outcome == 'required_input_unavailable'
        else:
            assert outcome == 'completed'
            assert len(runtime.contexts) == 1
            assert client.projection['input_protocol_required'] is True
            ids = client.projection['plan_node']['required_predecessor_message_ids']
            assert ids == ([] if predecessor is None else [predecessor['id']])
            assert runtime.contexts[0]['task_id'] == target
            if predecessor is not None:
                inputs = [item for item in runtime.contexts[0]['inputs'] if item['id'] == predecessor['id']]
                assert len(inputs) == 1
                assert inputs[0]['text'] == predecessor['text']
            assert any(method == 'POST' and path.endswith('/reservations') for method, path in client.calls)
            assert any(method == 'POST' and path.endswith('/start') for method, path in client.calls)
            assert next(t for t in core.read('fixture-a', rid)['tasks'] if t['id'] == target)['state'] == 'completed'
        if case in {'old-server', 'missing-projection'}:
            assert runtime.contexts == []
            assert not any(method == 'POST' and path.endswith(('/reservations', '/start', '/complete'))
                           for method, path in client.calls)
            state, payload = journal.db.execute('SELECT state,payload FROM queue WHERE id=?', (target,)).fetchone()
            assert state == 'failed'
            if case == 'missing-projection':
                assert json.loads(payload)['error_code'] == 'required_input_unavailable'
    finally:
        journal.close()
        client.http.close()
