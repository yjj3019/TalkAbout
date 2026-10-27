"""Task-response projections must track current ACL, including cached snapshots."""
import copy

import pytest

from conftest import leased, result
from talkabout.domain import Problem, replay
from talkabout.response_projection import project_task_response


def source(kind, source_id, **fields):
    return {'kind': kind, 'source_id': source_id, **fields}


def fixture_with_history(fixture):
    store, core, rid = fixture
    claimed = leased(core, rid)
    room = store.rooms[rid]
    file = {'file_id': 'f', 'version_id': 'fv1', 'sha256': 'e' * 64,
            'uploader_id': 'human', 'uploader_epoch': room['members']['human']['epoch'],
            'readers': ['a'], 'reader_epochs': {'a': room['members']['a']['epoch']},
            'allow_agent_processing': True, 'state': 'validated_utf8_text'}
    ref = {key: file[key] for key in ('file_id', 'version_id', 'sha256')}
    room['text_files'] = {'f': file}
    room['messages']['m-old'] = {'id': 'm-old', 'seq': 100, 'attachment_refs': [ref],
                                  'actor_id': 'human', 'actor_kind': 'human', 'text': 'private old'}
    room['messages']['m-new'] = {'id': 'm-new', 'seq': 101, 'attachment_refs': [ref],
                                  'actor_id': 'a', 'actor_kind': 'agent', 'text': 'own visible'}
    manifest = {'evidence': 'prepared_worker_input_not_model_read_receipt', 'sources': [
        source('message', 'm-old-src', message_id='m-old'),
        source('artifact_version', 'old-art-src', artifact_id='artifact', version_id='old'),
        source('file_excerpt', 'file-src', ref=ref)]}
    room['artifacts']['artifact'] = {'id': 'artifact', 'revision': 2, 'adopted': None, 'versions': [
        {'id': 'old', 'seq': 100, 'text': 'private artifact'},
        {'id': 'new', 'seq': 101, 'text': 'visible artifact', 'sources': [x['source_id'] for x in manifest['sources']],
         'input_references': copy.deepcopy(manifest)}]}
    task = room['tasks'][claimed['id']]
    task.update(attachment_refs=[ref], input_manifest=copy.deepcopy(manifest),
                handoff={'message_id': 'm-old', 'text': 'do not leak'},
                review_target={'artifact_id': 'artifact', 'version_id': 'old'},
                result_message_id='m-old', _file_lease_hash='private-hash',
                _reservations=[{'id': 'private-reservation'}])
    response = {'task': copy.deepcopy(task), 'message': copy.deepcopy(room['messages']['m-new']),
                'artifact': copy.deepcopy(room['artifacts']['artifact'])}
    actor = store.auth('fixture-a')
    return room, actor, response, ref


def test_projects_both_task_and_completion_without_mutating_snapshot(fixture):
    room, actor, response, ref = fixture_with_history(fixture)
    original = copy.deepcopy(response)
    projected = project_task_response(room, actor, response)
    assert projected['task']['attachment_refs'] == [ref]
    assert not any(k.startswith('_') for k in projected['task'])
    assert projected['message']['text'] == 'own visible'
    assert len(projected['artifact']['versions']) == 2
    assert response == original
    standalone = project_task_response(room, actor, response['task'])
    assert standalone == projected['task']


def test_replay_snapshot_reprojects_revoked_history_and_file_acl(fixture):
    room, actor, response, ref = fixture_with_history(fixture)
    original = copy.deepcopy(response)
    room['members']['a']['read_start_seq'] = 101
    room['text_files']['f']['allow_agent_processing'] = False
    projected = project_task_response(room, actor, response)
    task = projected['task']
    assert task['attachment_refs'] == []
    assert task['handoff'] == {'result_available': False}
    assert task['review_target_available'] is False and 'review_target' not in task
    assert 'result_message_id' not in task
    assert task['input_manifest']['sources'] == []
    assert task['input_manifest']['redacted_source_count'] == 3
    assert projected['message']['attachment_refs'] == []
    version = projected['artifact']['versions'][0]
    assert version['id'] == 'new' and version['sources'] == []
    assert version['input_references']['redacted_source_count'] == 3
    assert response == original and response['task']['attachment_refs'] == [ref]


@pytest.mark.parametrize('change', ['deleted', 'reader_removed', 'reader_epoch', 'no_visible_reference'])
def test_current_file_visibility_is_rechecked(fixture, change):
    room, actor, response, _ = fixture_with_history(fixture)
    file = room['text_files']['f']
    if change == 'deleted': file['state'] = 'deleted'
    elif change == 'reader_removed': file['readers'] = []
    elif change == 'reader_epoch': file['reader_epochs']['a'] += 1
    else:
        for message in room['messages'].values():
            message['attachment_refs'] = []
    projected = project_task_response(room, actor, response)
    assert projected['task']['attachment_refs'] == []
    assert all(s['kind'] != 'file_excerpt' for s in projected['task']['input_manifest']['sources'])
    assert projected['message']['attachment_refs'] == []
    assert all(s['kind'] != 'file_excerpt'
               for s in projected['artifact']['versions'][-1]['input_references']['sources'])


def test_does_not_refresh_or_add_post_response_artifact_versions(fixture):
    room, actor, response, _ = fixture_with_history(fixture)
    room['artifacts']['artifact']['versions'].append({'id': 'later', 'seq': 102, 'text': 'later'})
    room['artifacts']['artifact']['revision'] = 3
    projected = project_task_response(room, actor, response)
    assert projected['artifact']['revision'] == 2
    assert [v['id'] for v in projected['artifact']['versions']] == ['old', 'new']
    projected['task']['attachment_refs'].clear()
    projected['artifact']['versions'].clear()
    assert response['task']['attachment_refs'] and len(response['artifact']['versions']) == 2


def test_hidden_completion_message_and_artifact_are_not_returned(fixture):
    room, actor, response, _ = fixture_with_history(fixture)
    room['members']['a']['read_start_seq'] = 102
    projected = project_task_response(room, actor, response)
    assert projected['message'] is None
    assert 'artifact' not in projected


@pytest.mark.parametrize('change', ['revoked', 'wrong_actor', 'room_scope'])
def test_viewer_authorization_is_never_weakened(fixture, change):
    room, actor, response, _ = fixture_with_history(fixture)
    if change == 'revoked': room['members']['a']['status'] = 'removed'
    if change == 'read_scope': actor['scopes'] = [p for p in actor['scopes'] if p != 'room:read']
    if change == 'wrong_actor': actor['id'] = 'b'
    if change == 'room_scope': actor['room_id'] = 'different-room'
    with pytest.raises(Problem): project_task_response(room, actor, response)


def test_operation_only_credential_gets_no_history_or_file_metadata(fixture):
    room, actor, response, _ = fixture_with_history(fixture)
    actor['scopes'] = ['task:complete']
    projected = project_task_response(room, actor, response)
    assert projected['message'] is None and 'artifact' not in projected
    assert projected['task']['attachment_refs'] == []
    assert projected['task']['input_manifest']['sources'] == []
    assert 'review_target' not in projected['task']


def test_cached_projection_never_mixes_different_actor_tasks(fixture):
    room, actor, response, _ = fixture_with_history(fixture)
    response['task']['assignee'] = 'b'
    # Authoritative current task belongs to a, but a mismatched historical snapshot
    # must not be accepted as an otherwise-valid response.
    with pytest.raises(Problem): project_task_response(room, actor, response)


def test_epoch_and_body_idempotency_remain_owned_by_replay(fixture):
    room, actor, response, _ = fixture_with_history(fixture)
    operation = 'tasks/' + response['task']['id'] + '/complete'
    body = result(response['task'])
    previous, key, fingerprint = replay(room, actor, operation, 'same', body)
    assert previous is None
    from talkabout.domain import save_response
    save_response(room, key, fingerprint, response, room['members']['a']['epoch'])
    previous, _, _ = replay(room, actor, operation, 'same', body)
    room['members']['a']['read_start_seq'] = 102
    assert project_task_response(room, actor, previous)['message'] is None
    assert previous['message']['text'] == 'own visible'
    with pytest.raises(Problem, match='idempotency_conflict'):
        replay(room, actor, operation, 'same', result(response['task'], 'changed'))
    room['members']['a']['epoch'] += 1
    with pytest.raises(Problem, match='idempotency_epoch_conflict'):
        replay(room, actor, operation, 'same', body)
