"""Synthetic R3 worker → runtime pool → HTTP message evidence contracts.

No external provider, executable, or network service is invoked. LocalClient uses
FastAPI's in-process TestClient; evidence is a worker assertion, not proof of work.
"""
from conftest import LocalClient, build_fixture
from talkabout.connector import RoomRuntimePool
from talkabout.runtime import WorkEvidence
from talkabout.store import MemoryStore
from talkabout.worker import Journal, Worker


class SyntheticEvidenceRuntime:
    def __init__(self):
        self.calls = 0
        self.last_work_evidence = WorkEvidence(1, 99, 0, 0)  # deliberately stale

    def run_with_evidence(self, context, cancel):
        assert not cancel.is_set()
        self.calls += 1
        if self.calls == 1:
            snapshot = WorkEvidence(1, 2, 1, 1)
        else:
            snapshot = WorkEvidence(1, 0, 3, 0)
        # If any layer reads ambient state instead of the returned snapshot, fail.
        self.last_work_evidence = WorkEvidence(1, 90 + self.calls, 0, 0)
        return f'synthetic result {self.calls}', snapshot

    def shutdown(self):
        return True


class LegacyRuntime:
    def __init__(self):
        self.calls = 0
        self.last_work_evidence = WorkEvidence(1, 5, 1, 0)

    def run(self, context, cancel):
        assert not cancel.is_set()
        self.calls += 1
        return f'legacy synthetic {self.calls}'

    def shutdown(self):
        return True


def isolated_room():
    store, core, _ = build_fixture(MemoryStore())
    # Do not ingest the fixture room's unrelated queued event.
    store.rooms.clear()
    room_id = core.create('fixture-a', {'topic': 'Synthetic work evidence', 'mode': 'free_discussion'}, 'pool-e2e')['id']
    return store, room_id


def request_turn(client, room_id, key):
    room = client.request('GET', f'rooms/{room_id}')
    return client.request('POST', f'rooms/{room_id}/turn-requests', {
        'generation': room['generation'], 'membership_revision': room['membership_revision']}, key)


def projected_messages(client, room_id):
    return client.request('GET', f'rooms/{room_id}')['messages']


def test_pool_evidence_is_returned_from_each_invocation_to_http_message(tmp_path):
    store, room_id = isolated_room()
    client = LocalClient(store)
    journal = Journal(tmp_path / 'pool-evidence.sqlite')
    runtime = SyntheticEvidenceRuntime()
    pool = RoomRuntimePool(lambda: runtime)
    try:
        worker = Worker(client, journal, pool)
        for turn in (1, 2):
            request_turn(client, room_id, f'turn-{turn}')
            assert worker.step() == 'completed'
        messages = projected_messages(client, room_id)
        assert [message['text'] for message in messages] == ['synthetic result 1', 'synthetic result 2']
        assert [message['work_evidence'] for message in messages] == [
            {'version': 1, 'command_items': 2, 'file_change_items': 1, 'failed_items': 1,
             'source': 'agent_reported_unverified'},
            {'version': 1, 'command_items': 0, 'file_change_items': 3, 'failed_items': 0,
             'source': 'agent_reported_unverified'},
        ]
        assert pool.calls == runtime.calls == 2
        assert len(pool.runtimes) == 1
        tasks = store.rooms[room_id]['tasks'].values()
        assert len(list(tasks)) == 2
        assert all(t['execution_protocol'] == 'server_start_v1' and t['state'] == 'completed'
                   and t['_reservations'][0]['state'] == 'completed' for t in tasks)
        assert sum(path.endswith('/start') for method, path in client.calls if method == 'POST') == 2
        assert not journal.outcomes()
    finally:
        pool.shutdown()
        journal.close()
        client.http.close()


def test_pool_legacy_runtime_fallback_does_not_forward_stale_evidence(tmp_path):
    store, room_id = isolated_room()
    client = LocalClient(store)
    journal = Journal(tmp_path / 'pool-legacy.sqlite')
    runtime = LegacyRuntime()
    pool = RoomRuntimePool(lambda: runtime)
    try:
        worker = Worker(client, journal, pool)
        for turn in (1, 2):
            request_turn(client, room_id, f'legacy-turn-{turn}')
            assert worker.step() == 'completed'
        messages = projected_messages(client, room_id)
        assert [message['text'] for message in messages] == ['legacy synthetic 1', 'legacy synthetic 2']
        assert all('work_evidence' not in message for message in messages)
        assert pool.calls == runtime.calls == 2
        assert sum(path.endswith('/start') for method, path in client.calls if method == 'POST') == 2
    finally:
        pool.shutdown()
        journal.close()
        client.http.close()
