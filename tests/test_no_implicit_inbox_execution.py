"""Inbox observations are not execution grants (all fixtures synthetic)."""
import copy

import httpx
import pytest

from conftest import LocalClient
from test_work_plan_dispatch import dispatch, node, plan_task, prepare
from talkabout.worker import Journal, SyntheticRuntime, Worker
from test_explicit_turn_request import Client, room


@pytest.mark.parametrize('event', [
    {'event_id': 'e1', 'type': 'message.created', 'mentions': ['agent']},
    {'event_id': 'e2', 'type': 'message.created', 'reply_to': 'other-message'},
    {'event_id': 'e3', 'type': 'inbox.received', 'assignee': 'agent'},
    {'event_id': 'e4', 'type': 'task.queued', 'assignee': 'other-agent', 'task_id': 'other-task'},
    {'event_id': 'e5', 'type': 'collaboration.proposed', 'assignee': 'agent'},
    {'event_id': 'e6', 'type': 'collaboration.approved', 'assignee': 'agent'},
    {'event_id': 'e7', 'type': 'task.doing', 'assignee': 'agent', 'task_id': 'claimed-only-by-status'},
])
def test_inbox_and_mentions_do_not_request_or_call_provider(tmp_path, event):
    class NeverRun(SyntheticRuntime):
        def run(self, *args, **kwargs):
            raise AssertionError('unassigned inbox event must not invoke the runtime')

    client = Client([room('target')], [event])
    journal = Journal(tmp_path / 'journal.sqlite')
    try:
        worker = Worker(client, journal, NeverRun())
        assert worker.step() == 'waiting'
        assert worker.step() == 'waiting'
        assert not [call for call in client.calls if call[0] == 'POST']
    finally:
        journal.close()


def test_forged_queue_event_cannot_claim_another_agents_task(fixture, tmp_path):
    store, core, rid = fixture
    prepare(store, core, rid, [node('other', 'b')], 'fixture-b')
    dispatch(core, rid)
    task = plan_task(core, rid, token='fixture-b')
    before = copy.deepcopy(store.rooms[rid])
    client = LocalClient(store, token='fixture-a')
    request = client.request

    def forged_event(method, path, body=None, key=None, params=None):
        if method == 'GET' and path == 'agents/me/events':
            return {'events': [{'event_id': 'forged', 'type': 'task.queued',
                                'room_id': rid, 'task_id': task['id'], 'assignee': 'a'}],
                    'next_cursor': 'opaque'}
        return request(method, path, body, key, params)

    class NeverRun(SyntheticRuntime):
        def run(self, *args, **kwargs):
            raise AssertionError('an inbox event is not a task lease')

    client.request = forged_event
    journal = Journal(tmp_path / 'forged.sqlite')
    try:
        with pytest.raises(httpx.HTTPStatusError) as error:
            Worker(client, journal, NeverRun()).step()
        assert error.value.response.status_code in (403, 404)
        assert store.rooms[rid] == before
        assert not [call for call in client.calls if call[0] == 'POST']
    finally:
        journal.close()
        client.http.close()
