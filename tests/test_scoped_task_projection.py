"""Mutation-only credentials retain task operations without room history disclosure."""
from fastapi.testclient import TestClient
from talkabout.api import create_app
from talkabout.domain import token_hash


def test_claim_only_credential_can_claim_and_replay(fixture):
    store, core, rid = fixture
    task = next(t for t in core.read('fixture-a', rid)['tasks'] if t['state'] == 'queued')
    store.credentials[token_hash('fixture-a')]['scopes'] = ['task:claim']
    with TestClient(create_app(store, start_scheduler=False, allow_fixture_bearer=True)) as client:
        path = f'/api/v1/rooms/{rid}/tasks/{task["id"]}/claim'
        headers = {'Authorization': 'Bearer fixture-a', 'Idempotency-Key': 'scoped-claim'}
        body = {'generation': task['generation'], 'membership_revision': task['membership_revision']}
        first = client.post(path, json=body, headers=headers)
        assert first.status_code == 200, first.text
        assert first.json()['id'] == task['id'] and first.json()['state'] == 'leased'
        replay = client.post(path, json=body, headers=headers)
        assert replay.status_code == 200 and replay.json() == first.json()
        assert client.get(f'/api/v1/rooms/{rid}', headers=headers).status_code == 403
