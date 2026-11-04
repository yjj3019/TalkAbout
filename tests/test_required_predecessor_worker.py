"""Required predecessor checks run on the final worker prompt, before authorization."""
import copy
import json

import pytest

from conftest import LocalClient
from talkabout import collaboration
from talkabout.worker import Journal, Worker
from test_work_plan_dispatch import claim, complete, dispatch, node, plan_task, prepare


class Runtime:
    def __init__(self):
        self.contexts = []

    def run(self, context, cancel):
        self.contexts.append(copy.deepcopy(context))
        return 'result'


@pytest.mark.parametrize('case', ['missing', 'truncated', 'fitted', 'malformed', 'legacy', 'complete'])
def test_required_predecessor_worker(fixture, tmp_path, monkeypatch, case):
    store, core, rid = fixture
    prepare(store, core, rid, [node('first'), node('second', deps=['first'])], 'fixture-a')
    dispatch(core, rid)
    text = 'x' * (6500 if case == 'truncated' else 2000)
    predecessor = complete(core, rid, claim(core, rid), text=text)['message']
    target = plan_task(core, rid)['id']
    client = LocalClient(store)
    original = client.request

    def request(method, path, body=None, key=None, params=None):
        response = original(method, path, body, key, params)
        if method == 'GET' and path == 'agents/me/events':
            response['events'] = [e for e in response['events']
                                  if e.get('type') != 'task.queued' or e.get('task_id') == target]
        if method == 'GET' and path == f'rooms/{rid}':
            response = copy.deepcopy(response)
            current = next(t for t in response['tasks'] if t['id'] == target)
            if current['state'] == 'leased':
                plan = current['plan_node']
                if case == 'legacy':
                    plan.pop('required_predecessor_message_ids', None)
                else:
                    plan['required_predecessor_message_ids'] = (
                        ['missing-id'] if case == 'missing' else None if case == 'malformed'
                        else [predecessor['id']])
        return response

    client.request = request
    if case == 'fitted':
        # Enough room for metadata but not for the complete predecessor text.
        monkeypatch.setattr(collaboration, 'MAX_INPUT', 2500)
    journal = Journal(tmp_path / 'required.sqlite')
    runtime = Runtime()
    try:
        outcome = Worker(client, journal, runtime).step()
        if case in {'legacy', 'complete', 'truncated'}:
            assert outcome == 'completed'
            assert len(runtime.contexts) == 1
        else:
            assert outcome == 'required_input_unavailable'
            assert not runtime.contexts
            assert not any(method == 'POST' and path.endswith(('/reservations', '/start'))
                           for method, path in client.calls)
            row = journal.db.execute('SELECT state,payload FROM queue WHERE id=?', (target,)).fetchone()
            assert row[0] == 'failed'
            assert json.loads(row[1])['error_code'] == 'required_input_unavailable'
    finally:
        journal.close()
        client.http.close()
