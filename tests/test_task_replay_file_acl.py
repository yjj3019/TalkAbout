"""HTTP idempotency replays project file references through current consent."""
import copy
import json

import pytest

from conftest import result
from talkabout.collaboration import add_file_sources, shared_context
from talkabout.text_files import ref_of
from test_text_files_api import text_api, upload, publish, claim, read, request


SECRET = 'SYNTHETIC confidential file excerpt: ultraviolet-lantern'
RESULT = 'Unrelated synthetic task result remains visible.'


def revoke(api, file):
    _, _, rid, _, http = api
    response = request(http, 'PATCH', f"rooms/{rid}/files/{file['file_id']}/consent",
                       {'expected_revision': file['revision'], 'readers': [],
                        'allow_agent_processing': False}, key='revoke-file-consent')
    assert response.status_code == 200, response.text


def assert_ref_hidden(value, ref):
    serialized = json.dumps(value, sort_keys=True)
    assert SECRET not in serialized
    assert ref['file_id'] not in serialized
    assert ref['version_id'] not in serialized
    assert ref['sha256'] not in serialized


def test_same_key_claim_replay_reprojects_current_file_consent(text_api):
    _, _, rid, _, http = text_api
    file = upload(text_api, content=SECRET.encode())
    ref = ref_of(file)
    publish(text_api, file)
    task = claim(text_api, key='claim-before-revoke')
    assert task['attachment_refs'] == [ref]
    first = read(text_api, task, file, key='claim-test-read')
    assert first.status_code == 200 and first.json()['text'] == SECRET

    revoke(text_api, file)
    body = {key: task[key] for key in ('generation', 'membership_revision')}
    replay = request(http, 'POST', f"rooms/{rid}/tasks/{task['id']}/claim", body,
                     token='fixture-a', key='claim-before-revoke')
    assert replay.status_code == 200, replay.text
    projected = replay.json()
    assert projected['id'] == task['id'] and projected['attempt'] == task['attempt']
    assert projected['lease_proof'] == task['lease_proof']
    assert projected['attachment_refs'] == []
    assert_ref_hidden(projected, ref)
    # Even a cached read using the original proof and key must respect revocation.
    denied = read(text_api, task, file, key='claim-test-read')
    assert denied.status_code == 404
    assert SECRET not in denied.text


@pytest.mark.parametrize('replay_key', ['complete-before-revoke', 'another-key'])
def test_completion_replay_reprojects_file_manifest_and_preserves_result(text_api, replay_key):
    _, _, rid, _, http = text_api
    file = upload(text_api, content=SECRET.encode())
    ref = ref_of(file)
    publish(text_api, file)
    task = claim(text_api, key='completion-claim')
    chunk = read(text_api, task, file, key=f"file-read:{task['id']}:{task['attempt']}:{file['file_id']}")
    assert chunk.status_code == 200 and chunk.json()['text'] == SECRET
    room = request(http, 'GET', f'rooms/{rid}', token='fixture-a')
    assert room.status_code == 200
    context = {'room_id': rid, **shared_context(room.json(), task)}
    add_file_sources(context, [chunk.json()])
    manifest = context['input_manifest']
    assert any(source['kind'] == 'file_excerpt' and source['ref'] == ref
               for source in manifest['sources'])
    body = result(task, RESULT)
    body['result']['input_manifest'] = copy.deepcopy(manifest)
    path = f"rooms/{rid}/tasks/{task['id']}/complete"
    completed = request(http, 'POST', path, body, token='fixture-a', key='complete-before-revoke')
    assert completed.status_code == 200, completed.text
    original = completed.json()
    assert original['message']['text'] == RESULT
    assert original['task']['attachment_refs'] == [ref]
    assert any(source['kind'] == 'file_excerpt' and source['ref'] == ref
               for source in original['task']['input_manifest']['sources'])

    revoke(text_api, file)
    replay = request(http, 'POST', path, body, token='fixture-a', key=replay_key)
    assert replay.status_code == 200, replay.text
    projected = replay.json()
    assert projected['message']['text'] == RESULT
    assert projected['message']['id'] == original['message']['id']
    assert projected['task']['id'] == task['id'] and projected['task']['state'] == 'completed'
    assert projected['task']['attachment_refs'] == []
    assert all(source['kind'] != 'file_excerpt'
               for source in projected['task']['input_manifest']['sources'])
    assert projected['task']['input_manifest']['redacted_source_count'] >= 1
    assert_ref_hidden(projected, ref)
    denied = read(text_api, task, file,
                  key=f"file-read:{task['id']}:{task['attempt']}:{file['file_id']}")
    assert denied.status_code in {404, 409}
    assert SECRET not in denied.text
