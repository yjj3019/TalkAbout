"""Exercise plan context and fencing through the real cooperative Worker path."""
import copy

import pytest

from conftest import LocalClient
from talkabout.agent_runtime import prompt_text
from talkabout.worker import Journal, Worker
from test_work_plan_dispatch import claim, complete, dispatch, node, plan_task, prepare


class RecordingRuntime:
    def __init__(self):
        self.contexts = []

    def run(self, context, cancel):
        assert not cancel.is_set()
        self.contexts.append(copy.deepcopy(context))
        return 'worker result'


class ObservingClient(LocalClient):
    def __init__(self, store, room_id, stale=False):
        super().__init__(store)
        self.room_id = room_id
        self.stale = stale
        self.claimed = None
        self.projected = None

    def request(self, method, path, body=None, key=None, params=None):
        result = super().request(method, path, body, key, params)
        if method == 'POST' and path.endswith('/claim'):
            self.claimed = copy.deepcopy(result)
            # Claim is deliberately not a source of private plan context.
            assert 'plan_node' not in result
        if method == 'GET' and path == f'rooms/{self.room_id}' and self.claimed:
            result = copy.deepcopy(result)
            current = next(t for t in result['tasks'] if t['id'] == self.claimed['id'])
            self.projected = copy.deepcopy(current)
            if self.stale:
                # Simulate a newer projection racing with the worker's claim.
                current['attempt'] += 1
        return result


@pytest.mark.parametrize('stale,hidden', [(False, False), (True, False), (False, True)],
                         ids=['matching', 'stale-attempt', 'hidden-predecessor'])
def test_worker_uses_fenced_room_plan_projection(fixture, tmp_path, stale, hidden):
    store, core, rid = fixture
    prepare(store, core, rid, [node('first'), node('second', deps=['first'],
                                                 instruction='current owner instruction')], 'fixture-a')
    dispatch(core, rid)
    predecessor = complete(core, rid, claim(core, rid), text='predecessor result')
    if hidden:
        store.rooms[rid]['members']['a']['read_start_seq'] = predecessor['message']['seq'] + 1
    client = ObservingClient(store, rid, stale=stale)
    journal = Journal(tmp_path / 'worker-plan.sqlite')
    runtime = RecordingRuntime()
    try:
        worker = Worker(client, journal, runtime)
        # Ignore fixture events for tasks removed by prepare, retaining real dispatch events.
        target = plan_task(core, rid)['id']
        original_request = client.request

        def target_events(method, path, body=None, key=None, params=None):
            response = original_request(method, path, body, key, params)
            if method == 'GET' and path == 'agents/me/events':
                response['events'] = [event for event in response['events']
                                      if event.get('type') != 'task.queued' or event.get('task_id') == target]
            return response

        client.request = target_events
        outcome = worker.step()
        assert client.claimed is not None and client.projected is not None
        assert client.claimed['id'] == client.projected['id']
        plan = client.projected['plan_node']
        assert plan['node_id'] == 'second'
        assert plan['predecessor_message_ids'] == ([] if hidden else [predecessor['message']['id']])
        if stale:
            assert outcome == 'stale'
            assert runtime.contexts == []
            assert not any(path.endswith(('/reservations', '/start', '/complete'))
                           for method, path in client.calls if method == 'POST')
            assert journal.status().get('failed')
        else:
            assert outcome == 'completed'
            assert len(runtime.contexts) == 1
            context = runtime.contexts[0]
            assert context['task_id'] == client.claimed['id']
            expected_notice = ('Predecessor inputs are best-effort: they may be missing, inaccessible, '
                               'or truncated. Do not assume they are complete.')
            assert context['plan_node'] == {**{key: plan.get(key) for key in
                                            ('node_id', 'title', 'instruction', 'write_scope', 'run_id')},
                                            'predecessor_inputs_notice': expected_notice}
            assert expected_notice in prompt_text(context)
            assert context['plan_node']['instruction'] == 'current owner instruction'
            if hidden:
                assert predecessor['message']['id'] not in prompt_text(context)
                assert 'predecessor_message_ids' not in context['plan_node']
            else:
                assert any(item['id'] == predecessor['message']['id'] and item['text'] == 'predecessor result'
                           for item in context['inputs'])
    finally:
        journal.close()
        client.http.close()
