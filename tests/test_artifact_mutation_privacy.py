import copy
import json

import pytest

from talkabout.domain import Problem
from talkabout.store import token_hash


def setup_history(fixture):
    store, core, rid = fixture
    room = store.rooms[rid]
    member = room['members']['a']
    ref = {'file_id': 'f', 'version_id': 'fv', 'sha256': 'e' * 64}
    room['text_files'] = {'f': {**ref, 'uploader_id': 'human',
        'uploader_epoch': room['members']['human']['epoch'], 'readers': ['a'],
        'reader_epochs': {'a': member['epoch']}, 'allow_agent_processing': True,
        'state': 'validated_utf8_text'}}
    room['messages']['evidence'] = {'id': 'evidence', 'seq': 100, 'attachment_refs': [ref],
        'actor_id': 'human', 'actor_kind': 'human', 'text': 'evidence'}
    manifest = {'evidence': 'prepared_worker_input_not_model_read_receipt', 'sources': [
        {'kind': 'file_excerpt', 'source_id': 'secret-source', 'ref': ref}]}
    room['artifacts']['art'] = {'id': 'art', 'revision': 1, 'adopted': None, 'versions': [
        {'id': 'old', 'seq': 101, 'text': 'old text', 'sources': ['secret-source'],
         'input_references': manifest}]}
    room['last_seq'] = max(room['last_seq'], 101)
    body = {'generation': room['generation'], 'membership_revision': member['revision'],
            'expected_revision': 1, 'text': 'new text'}
    return store, core, rid, room, body


def test_initial_and_replay_apply_current_file_acl_and_snapshot(fixture):
    store, core, rid, room, body = setup_history(fixture)
    room['text_files']['f']['allow_agent_processing'] = False
    first = core.mutation('fixture-a', rid, 'artifacts/art/versions', 'key', body)
    assert first['versions'][0]['input_references']['sources'] == []
    room = store.rooms[rid]
    cached = room['keys']['a:artifacts/art/versions:key']['response']
    assert 'secret-source' not in json.dumps(cached) and 'old text' not in json.dumps(cached)
    room['text_files']['f']['allow_agent_processing'] = True
    visible = core.mutation('fixture-a', rid, 'artifacts/art/versions', 'key', body)
    room = store.rooms[rid]
    assert visible['versions'][0]['input_references']['sources'][0]['source_id'] == 'secret-source'
    room['text_files']['f']['allow_agent_processing'] = False
    room['artifacts']['art']['versions'].append({'id': 'later', 'seq': 103, 'text': 'later'})
    replayed = core.mutation('fixture-a', rid, 'artifacts/art/versions', 'key', body)
    assert replayed == first
    assert [v['id'] for v in replayed['versions']] == ['old', first['versions'][1]['id']]
    with pytest.raises(Problem, match='idempotency_conflict'):
        core.mutation('fixture-a', rid, 'artifacts/art/versions', 'key', {**body, 'text': 'different'})


def test_legacy_cache_and_sequence_visibility(fixture):
    store, core, rid, room, body = setup_history(fixture)
    first = core.mutation('fixture-a', rid, 'artifacts/art/versions', 'key', body)
    room = store.rooms[rid]
    entry = room['keys']['a:artifacts/art/versions:key']
    entry['response'] = copy.deepcopy(room['artifacts']['art'])
    room['members']['a']['read_start_seq'] = 102
    replayed = core.mutation('fixture-a', rid, 'artifacts/art/versions', 'key', body)
    room = store.rooms[rid]
    entry = room['keys']['a:artifacts/art/versions:key']
    assert [v['id'] for v in replayed['versions']] == [first['versions'][-1]['id']]
    assert entry['response']['_artifact_snapshot_v1'] is True
    assert 'secret-source' not in json.dumps(entry['response'])
    room['members']['a']['epoch'] += 1
    with pytest.raises(Problem, match='idempotency_epoch_conflict'):
        core.mutation('fixture-a', rid, 'artifacts/art/versions', 'key', body)


def test_contribute_only_cannot_mutate_or_replay(fixture):
    store, core, rid, room, body = setup_history(fixture)
    core.mutation('fixture-a', rid, 'artifacts/art/versions', 'key', body)
    room = store.rooms[rid]
    store.credentials[token_hash('fixture-a')]['scopes'] = ['room:contribute']
    before = copy.deepcopy(room)
    for key in ('key', 'new-key'):
        with pytest.raises(Problem):
            core.mutation('fixture-a', rid, 'artifacts/art/versions', key, body)
    assert room == before
