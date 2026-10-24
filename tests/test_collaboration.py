"""Synthetic shared-input provenance, never real model comprehension evidence."""
import copy
import json

import pytest

from conftest import LocalClient
from talkabout.domain import Problem
from talkabout.runtime import MAX_RUNTIME_INPUT, RuntimeInputError, runtime_prompt
from talkabout.worker import Journal, Worker


class Captured:
    def __init__(self, answer='Synthetic contribution'):
        self.contexts = []
        self.answer = answer

    def run(self, context, cancel):
        self.contexts.append(copy.deepcopy(context))
        return self.answer


def contribute(store, path, token='fixture-a', answer='Synthetic contribution'):
    client = LocalClient(store, token)
    journal = Journal(path)
    runtime = Captured(answer)
    try:
        outcome = Worker(client, journal, runtime).step()
        return outcome, runtime.contexts
    finally:
        journal.close()
        client.http.close()


def test_large_shared_input_is_bounded_and_old_pin_keeps_its_source(fixture, tmp_path):
    store, core, rid = fixture
    pinned = core.mutation('fixture-human', rid, 'messages', 'pin-message',
        {'kind': 'advice', 'client_message_id': 'pin', 'text': 'Important earlier constraint', 'mentions': [], 'reply_to': None})
    room = core.read('fixture-human', rid)
    pending = core.mutation('fixture-human', rid, 'controls', 'pin-control',
        {'action': 'pin', 'parameters': {'message_id': pinned['id']}, 'expected_revision': room['room_revision']})
    core.mutation('fixture-human', rid, 'controls/' + pending['id'] + '/approve', 'pin-approve', {})
    for index in range(10):
        core.mutation('fixture-human', rid, 'messages', 'message-' + str(index),
            {'kind': 'advice', 'client_message_id': 'message-' + str(index), 'text': '한글 🟦' * 2000, 'mentions': [], 'reply_to': None})
    for index in range(4):
        core.mutation('fixture-a', rid, 'artifacts', 'artifact-' + str(index),
            {'generation': 1, 'membership_revision': 2, 'text': 'x' * 64000, 'sources': ['https://example.invalid/source']})
    outcome, contexts = contribute(store, tmp_path / 'bounded.sqlite')
    assert outcome == 'completed' and len(contexts) == 1
    context = contexts[0]
    assert len(runtime_prompt(context).encode('utf-8')) <= MAX_RUNTIME_INPUT
    assert any(item['id'] == pinned['id'] for item in context['inputs'])
    assert context['input_manifest']['omissions']['messages']['count'] > 0
    assert all(item['untrusted_data'] for item in context['inputs'] + context['artifact_proposals'])
    assert all(item['version_id'] and item['revision'] and item['sources'] for item in context['artifact_proposals'])
    assert any(item['truncated'] for item in context['artifact_proposals'])
    assert context['input_manifest']['summary']['status'] == 'not_generated'


@pytest.mark.parametrize('text', ['x' * MAX_RUNTIME_INPUT, '한' * (MAX_RUNTIME_INPUT // 3)], ids=['ascii_bytes', 'utf8_bytes'])
def test_pure_input_limit_still_rejects_unbounded_utf8_context(text):
    with pytest.raises(RuntimeInputError, match='^invalid_runtime_budget$'):
        runtime_prompt({'room_id': 'synthetic', 'inputs': text})


@pytest.mark.parametrize('field,value', [('room_id', 'other-room'), ('actor_id', 'b'),
                                      ('seq', 999999), ('source_id', 'invented'), ('utf8_end', 1)])
def test_manifest_rejects_forged_or_non_utf8_boundary_refs_without_result(fixture, field, value):
    from conftest import leased, result
    from talkabout.collaboration import shared_context
    store, core, rid = fixture
    core.mutation('fixture-human', rid, 'messages', 'source',
        {'kind': 'advice', 'client_message_id': 'source', 'text': '한글 source', 'mentions': [], 'reply_to': None})
    task = leased(core, rid)
    manifest = shared_context(core.read('fixture-a', rid), task)['input_manifest']
    manifest['sources'][0][field] = value
    before = copy.deepcopy(store.rooms[rid])
    with pytest.raises(Problem, match='invalid_input_source'):
        core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/complete', 'forged',
                      {**result(task), 'input_manifest': manifest})
    assert store.rooms[rid] == before


def test_two_synthetic_agents_handoff_exact_review_and_human_adoption(fixture, tmp_path):
    from talkabout.core import Core
    from talkabout.domain import AGENT_PERMISSIONS, HUMAN_PERMISSIONS, SELF_PERMISSIONS
    from talkabout.store import MemoryStore
    store = MemoryStore()
    for pid, kind in [('human', 'human'), ('a', 'agent'), ('b', 'agent')]:
        scopes = HUMAN_PERMISSIONS if kind == 'human' else AGENT_PERMISSIONS | SELF_PERMISSIONS
        store.provision({'id': pid, 'kind': kind, 'owner': 'fixture-owner', 'display_name': 'Fixture Owner' if kind == 'human' else pid, 'handle': 'owner' if kind == 'human' else pid},
                        'fixture-' + pid, scopes | {'rooms:create'})
    core = Core(store)
    rid = core.create('fixture-human', {'topic': 'Synthetic shared knowledge', 'mode': 'co_creation'}, 'co-room')['id']
    for pid in ['a', 'b']:
        member = core.mutation('fixture-human', rid, 'memberships', 'invite-' + pid,
                              {'principal_id': pid, 'permissions': sorted(AGENT_PERMISSIONS), 'status': 'pending'})
        core.accept('fixture-' + pid, rid, 'accept', {'expected_revision': member['revision'], 'acknowledge_private': True})
    captures = []
    for index in range(6):
        room = core.read('fixture-human', rid)
        queued = next(t for t in room['tasks'] if t['state'] == 'queued')
        if index == 4:
            target = copy.deepcopy(queued['review_target'])
            core.mutation('fixture-a', rid, 'artifacts/' + target['artifact_id'] + '/versions', 'later-version',
                          {'generation': 1, 'membership_revision': 2, 'expected_revision': target['revision'],
                           'text': 'Newer text must not replace the assigned review target', 'sources': []})
        # A participant reconnects with its own durable journal, rather than
        # treating every historical queued event as a fresh execution request.
        outcome, contexts = contribute(store, tmp_path / ('agent-' + queued['assignee'] + '.sqlite'),
                                       'fixture-' + queued['assignee'], 'Blue synthetic fact ' + str(index))
        assert outcome == 'completed'
        assert contexts[0]['task_id']==queued['id']
        captures.append(contexts[0])
    assert any(m['text'] == 'Blue synthetic fact 0' for m in captures[1]['inputs'])
    assert captures[1]['handoff']['result_available'] is True
    assert captures[4]['review_target'] == target and captures[4]['review_target_available'] is True
    selected = next(v for v in captures[4]['artifact_proposals'] if v['version_id'] == target['version_id'])
    assert selected['text'] == 'Blue synthetic fact 3'
    room = core.read('fixture-human', rid)
    artifact = next(a for a in room['artifacts'] if a['id'] == target['artifact_id'])
    created = next(v for v in artifact['versions'] if v['text'] == 'Blue synthetic fact 2')
    assert created['sources'] and any(':message:' in source for source in created['sources'])
    assert created['input_references']['evidence'] == 'worker_reported_server_validated_refs_not_model_read_receipt'
    reviewed = next(m for m in room['messages'] if m['text'] == 'Blue synthetic fact 4')
    assert reviewed['review_target']['version_id'] == target['version_id']
    with pytest.raises(Problem, match='artifact_conflict'):
        core.mutation('fixture-human', rid, 'artifacts/' + artifact['id'] + '/adoptions', 'stale-adopt',
            {'generation': 1, 'membership_revision': 1, 'expected_revision': target['revision'], 'version_id': target['version_id']})
    adopted = core.mutation('fixture-human', rid, 'artifacts/' + artifact['id'] + '/adoptions', 'adopt-exact',
        {'generation': 1, 'membership_revision': 1, 'expected_revision': artifact['revision'], 'version_id': target['version_id']})
    assert adopted['adopted'] == target['version_id']
    assert core.read('fixture-human', rid)['mode_status'] == 'adopted'


def test_fresh_journal_drains_completed_history_without_reexecuting_it(fixture,tmp_path):
    from conftest import leased,result
    store,core,rid=fixture
    original=Captured('First synthetic result')
    first_client=LocalClient(store);first=Journal(tmp_path/'original.sqlite')
    try:
        assert Worker(first_client,first,original).step()=='completed'
        original_task=original.contexts[0]['task_id']
    finally:first.close();first_client.http.close()
    runtime=Captured('Next synthetic result')
    client=LocalClient(store);fresh=Journal(tmp_path/'explicit-new-journal.sqlite')
    try:
        worker=Worker(client,fresh,runtime)
        assert worker.step()=='unavailable'
        assert runtime.contexts==[]
        assert worker.step()=='waiting'
        other=leased(core,rid,'fixture-b')
        core.mutation('fixture-b',rid,'tasks/'+other['id']+'/complete','advance-other',result(other))
        assert worker.step()=='completed'
        assert len(runtime.contexts)==1 and runtime.contexts[0]['task_id']!=original_task
        assert sum(m['text']=='First synthetic result' for m in core.read('fixture-human',rid)['messages'])==1
    finally:fresh.close();client.http.close()


@pytest.mark.parametrize('protected',[False,True],ids=['ordinary','pinned'])
def test_impossible_metadata_budget_stops_without_a_nonshrinking_loop(monkeypatch,protected):
    import talkabout.collaboration as module
    context={'padding':'x'*(MAX_RUNTIME_INPUT+1),'inputs':[{
        'id':'message','text':'x'*64,'source_id':'source','total_utf8_bytes':64}],
        'input_manifest':{'sources':[{'source_id':'source','utf8_end':64,'truncated':False}]}}
    if protected:context['pinned_message_id']='message'
    calls=[]
    original=module.runtime_prompt
    def bounded_observer(value):
        calls.append(1)
        assert len(calls)<=16,'budget reduction must terminate rather than retry the same text'
        return original(value)
    monkeypatch.setattr(module,'runtime_prompt',bounded_observer)
    with pytest.raises(RuntimeInputError,match='^invalid_runtime_budget$'):
        module.fit_context(context)
    assert len(calls)<=9
    assert context['inputs'][0]['text']==('x'*64 if protected else '')


def test_file_selection_omissions_include_unread_authorized_refs_without_identifiers():
    from talkabout.collaboration import add_file_sources
    context={'room_id':'synthetic-room','input_manifest':{'sources':[],'omissions':{}}}
    excerpts=[{'ref':{'file_id':str(index),'version_id':'v','sha256':'synthetic'},
               'start':0,'end':10,'truncated':True} for index in range(4)]
    add_file_sources(context,excerpts,available_count=8)
    assert context['input_manifest']['omissions']['files']=={'count':4}
    assert len(context['input_manifest']['sources'])==4


def test_review_target_hidden_by_membership_history_is_not_projected(fixture):
    from talkabout.core import public_room
    store,_,rid=fixture
    from conftest import result,leased
    from talkabout.core import Core
    core=Core(store);task=leased(core,rid)
    saved=core.mutation('fixture-a',rid,'tasks/'+task['id']+'/complete','review-source',result(task))
    room=copy.deepcopy(store.rooms[rid])
    hidden={'artifact_id':'hidden-artifact','version_id':'hidden-version','revision':1}
    room['messages'][saved['message']['id']]['review_target']=hidden
    # The visible contribution may outlive a source the viewer cannot access.
    projected=public_room(room,store.auth('fixture-a'))
    message=next(m for m in projected['messages'] if m['id']==saved['message']['id'])
    assert 'review_target' not in message and message['review_target_unavailable'] is True
    assert 'hidden-version' not in json.dumps(projected)


def text_context(text='sample'):
    from talkabout.collaboration import shared_context
    room = {'id': 'synthetic-room', 'last_seq': 2, 'pinned_message_id': None, 'artifacts': [],
            'messages': [{'id': 'm', 'actor_id': 'peer', 'actor_kind': 'agent', 'display_name': 'Peer',
                          'kind': 'contribution', 'seq': 2, 'text': text}]}
    return {'room_id': room['id'], **shared_context(room, {})}


def test_serialized_budget_floor_terminates_when_metadata_alone_is_oversized(monkeypatch):
    from talkabout import collaboration
    context = text_context('x' * 64)
    context['metadata_pad'] = 'x' * (MAX_RUNTIME_INPUT + 64)
    checks = []
    original = collaboration.runtime_prompt
    def counted(value):
        checks.append(1)
        assert len(checks) <= 16, 'budget fitting failed to make progress'
        return original(value)
    monkeypatch.setattr(collaboration, 'runtime_prompt', counted)
    with pytest.raises(RuntimeInputError, match='^invalid_runtime_budget$'):
        collaboration.fit_context(context)
    assert 1 < len(checks) <= 16
    assert context['inputs'][0]['text'] == ''


def test_actual_serialized_json_escaping_budget_keeps_target_and_pin():
    from talkabout.collaboration import add_file_sources, fit_context, shared_context
    room = {'id': 'synthetic-room', 'last_seq': 20, 'pinned_message_id': 'm0',
            'messages': [{'id': 'm' + str(i), 'actor_id': 'peer', 'actor_kind': 'agent', 'display_name': 'Peer',
                          'kind': 'contribution', 'seq': i + 1, 'text': '\x00' * 16000} for i in range(8)],
            'artifacts': [{'id': 'a' + str(i), 'revision': 1, 'adopted': None,
                           'versions': [{'id': 'v' + str(i), 'version_revision': 1, 'actor_id': 'peer', 'seq': i + 10,
                                         'text': '\x00' * 64000, 'sources': []}]} for i in range(4)]}
    context = {'room_id': room['id'], **shared_context(room, {'review_target': {'artifact_id': 'a0', 'version_id': 'v0', 'revision': 1}})}
    context['file_excerpts'] = [{'ref': {'file_id': str(i), 'version_id': 'f' + str(i), 'sha256': '0' * 64},
                               'start': 0, 'end': 2000, 'line_start': 1, 'line_end': 1, 'truncated': True,
                               'text': '\x00' * 2000} for i in range(4)]
    add_file_sources(context, context['file_excerpts'], available_count=8)
    with pytest.raises(RuntimeInputError, match='invalid_runtime_budget'):
        runtime_prompt(context)
    fit_context(context)
    assert len(runtime_prompt(context).encode('utf-8')) <= MAX_RUNTIME_INPUT
    assert next(m for m in context['inputs'] if m['id'] == 'm0')['text']
    assert next(v for v in context['artifact_proposals'] if v['version_id'] == 'v0')['text']
    assert context['input_manifest']['omissions']['files']['count'] == 4
    assert len([s for s in context['input_manifest']['sources'] if s['kind'] == 'file_excerpt']) == 4
    for item in context['inputs'] + context['artifact_proposals']:
        assert item['utf8_end'] == len(item['text'].encode('utf-8'))
        assert item['truncated'] is True


@pytest.mark.parametrize('key', ['start', 'end'])
def test_file_excerpt_range_rejects_boolean_equal_to_integer(key):
    from talkabout.collaboration import source_id, validate_manifest
    ref = {'file_id': 'file', 'version_id': 'version', 'sha256': '0' * 64}
    task = {'id': 'task', 'attempt': 1, 'attachment_refs': [ref],
            '_file_reads': {'file-read:task:1:file': {'range': [0, 1]}}}
    room = {'id': 'room', 'last_seq': 1, 'messages': [], 'artifacts': [], 'tasks': [task]}
    source = {'kind': 'file_excerpt', 'room_id': 'room', 'ref': ref, 'start': 0, 'end': 1}
    source['source_id'] = source_id(source)
    source[key] = bool(source[key])
    with pytest.raises(Problem):
        validate_manifest({'snapshot_seq': 1, 'sources': [source]}, room, task)


@pytest.mark.parametrize('bad', [[], {}, True, None, 'x' * 129], ids=['list', 'dict', 'bool', 'null', 'long'])
@pytest.mark.parametrize('kind,key', [('message', 'message_id'), ('artifact_version', 'artifact_id'), ('artifact_version', 'version_id')])
def test_malformed_reference_identifiers_are_422_not_internal_errors(fixture, bad, kind, key):
    from conftest import leased, result
    from talkabout.collaboration import shared_context
    _, core, rid = fixture
    core.mutation('fixture-human', rid, 'messages', 'message', {'kind': 'advice', 'client_message_id': 'm', 'text': 'source'})
    core.mutation('fixture-a', rid, 'artifacts', 'artifact', {'generation': 1, 'membership_revision': 2, 'text': 'version'})
    task = leased(core, rid)
    manifest = shared_context(core.read('fixture-a', rid), task)['input_manifest']
    selected = next(source for source in manifest['sources'] if source['kind'] == kind)
    selected[key] = bad
    with pytest.raises(Problem) as failure:
        core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/complete', 'bad-id', {**result(task), 'input_manifest': manifest})
    assert failure.value.status == 422


@pytest.mark.parametrize('key', ['snapshot_seq', 'seq', 'utf8_start', 'utf8_end', 'total_utf8_bytes'])
def test_manifest_integer_fields_reject_booleans(fixture, key):
    from conftest import leased, result
    from talkabout.collaboration import shared_context
    _, core, rid = fixture
    core.mutation('fixture-human', rid, 'messages', 'message', {'kind': 'advice', 'client_message_id': 'm', 'text': 'x'})
    task = leased(core, rid)
    manifest = shared_context(core.read('fixture-a', rid), task)['input_manifest']
    (manifest if key == 'snapshot_seq' else manifest['sources'][0])[key] = True
    with pytest.raises(Problem) as failure:
        core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/complete', 'bool-ref', {**result(task), 'input_manifest': manifest})
    assert failure.value.status == 422


def test_version_revision_is_immutable_not_reported_head_revision(fixture):
    from conftest import leased, result
    from talkabout.collaboration import shared_context
    _, core, rid = fixture
    artifact = core.mutation('fixture-a', rid, 'artifacts', 'v1', {'generation': 1, 'membership_revision': 2, 'text': 'first'})
    core.mutation('fixture-a', rid, 'artifacts/' + artifact['id'] + '/versions', 'v2',
                  {'generation': 1, 'membership_revision': 2, 'expected_revision': 1, 'text': 'second'})
    task = leased(core, rid)
    context = shared_context(core.read('fixture-a', rid), task)
    source = context['input_manifest']['sources'][0]
    assert source['revision'] == 2
    source['revision'] = 1
    with pytest.raises(Problem, match='invalid_input_source'):
        core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/complete', 'wrong-revision', {**result(task), 'input_manifest': context['input_manifest']})


def test_history_boundary_redacts_task_message_and_artifact_source_metadata(fixture):
    from talkabout.collaboration import shared_context
    store, core, rid = fixture
    old = core.mutation('fixture-human', rid, 'messages', 'old', {'kind': 'advice', 'client_message_id': 'old', 'text': 'Old hidden text'})
    artifact = core.mutation('fixture-a', rid, 'artifacts', 'old-version', {'generation': 1, 'membership_revision': 2, 'text': 'Old hidden version'})
    target = {'artifact_id': artifact['id'], 'version_id': artifact['versions'][0]['id'], 'revision': 1}
    visible = core.mutation('fixture-human', rid, 'messages', 'new', {'kind': 'advice', 'client_message_id': 'new', 'text': 'Visible later result'})
    core.mutation('fixture-a', rid, 'artifacts/' + artifact['id'] + '/versions', 'visible-version',
                  {'generation': 1, 'membership_revision': 2, 'expected_revision': 1, 'text': 'Visible second version'})
    with store.transaction(rid, 'fixture-human') as (_, room):
        room['members']['a']['read_start_seq'] = visible['seq']
        room['members']['human']['read_start_seq'] = visible['seq']
        task = next(t for t in room['tasks'].values() if t['assignee'] == 'a')
        task['handoff'] = {'task_id': 'hidden-old-task', 'message_id': old['id'], 'result_available': True}
        task['review_target'] = target
        room['messages'][visible['id']]['review_target'] = target
    public = core.read('fixture-a', rid)
    task = next(t for t in public['tasks'] if t['assignee'] == 'a')
    assert task['handoff'] == {'result_available': False}
    assert 'review_target' not in task and 'review_target' not in public['messages'][0]
    context = shared_context(public, task)
    serialized = json.dumps(context)
    assert old['id'] not in serialized and target['version_id'] not in serialized and 'hidden-old-task' not in serialized
    assert public['artifacts'][0]['versions'][0]['version_revision'] == 2
