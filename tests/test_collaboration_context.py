"""Shared-input provenance: bounded text and server-validated references.

Nothing here proves a model read anything. These tests pin the boundary: what a worker
may send, what the server can re-check, and what is recorded when it cannot.
"""
import copy

import pytest

from conftest import LocalClient, leased, result
from talkabout.collaboration import (MAX_INPUT, ContextBudgetError, excerpt, fit_context,
                                     project_manifest, project_task, shared_context,
                                     validate_manifest)
from talkabout.domain import Problem
from talkabout.worker import Journal, Worker


class Captured:
    def __init__(self, answer='Synthetic contribution'):
        self.contexts = []
        self.answer = answer

    def run(self, context, cancel):
        self.contexts.append(copy.deepcopy(context))
        return self.answer


def room_fixture(*, messages=(), artifacts=(), pinned=None, last_seq=9):
    return {'id': 'room-one', 'last_seq': last_seq, 'pinned_message_id': pinned,
            'messages': list(messages), 'artifacts': list(artifacts)}


def message(mid, seq, text, actor='a'):
    return {'id': mid, 'actor_id': actor, 'actor_kind': 'agent', 'display_name': actor,
            'seq': seq, 'kind': 'contribution', 'text': text}


def artifact(aid, revision, versions, adopted=None):
    return {'id': aid, 'revision': revision, 'adopted': adopted, 'versions': versions}


def version(vid, seq, text, revision=1, kind='text', sources=()):
    return {'id': vid, 'seq': seq, 'text': text, 'kind': kind, 'actor_id': 'a',
            'sources': list(sources), 'version_revision': revision}


def test_excerpt_cuts_at_a_utf8_boundary_and_reports_the_range():
    text, limits = excerpt('한글🟦ab', 7)
    # 7 bytes ends inside the emoji: only the two complete Hangul characters survive.
    assert text == '한글'
    assert limits['utf8_start'] == 0 and limits['utf8_end'] == 6
    assert limits['total_utf8_bytes'] == len('한글🟦ab'.encode('utf-8'))
    assert limits['truncated'] is True
    whole, complete = excerpt('ok', 64)
    assert whole == 'ok' and complete['truncated'] is False


def test_shared_context_prioritises_pin_and_adopted_then_bounds_the_rest():
    messages = [message('m' + str(index), index + 1, 'history ' + str(index)) for index in range(12)]
    versions = [version('v1', 20, 'old text', revision=1),
                version('v2', 21, 'adopted text', revision=2),
                version('v3', 22, 'newest text', revision=3)]
    room = room_fixture(messages=messages, pinned='m3',
                        artifacts=[artifact('art', 3, versions, adopted='v2')])
    context = shared_context(room, {'id': 'task-one', 'state': 'leased'})
    assert [item['id'] for item in context['inputs']][0] == 'm3'
    assert len(context['inputs']) <= 8
    assert len(context['artifact_proposals']) <= 4
    assert [item['version_id'] for item in context['artifact_proposals']][:2] == ['v2', 'v3']
    assert context['input_manifest']['omissions']['messages']['count'] == len(messages) - len(context['inputs'])
    assert all(item['untrusted_data'] for item in context['inputs'] + context['artifact_proposals'])
    assert context['input_manifest']['summary']['status'] == 'not_generated'
    assert context['input_manifest']['evidence'] == 'prepared_worker_input_not_model_read_receipt'


def test_shared_context_truncates_large_text_and_keeps_source_ids():
    room = room_fixture(messages=[message('big', 1, 'x' * 100000)],
                        artifacts=[artifact('art', 1, [version('v1', 2, 'y' * 100000)])])
    context = shared_context(room, {'id': 'task-one'})
    assert len(context['input_manifest']['sources']) == 2
    assert any(item['truncated'] for item in context['inputs'] + context['artifact_proposals'])
    text_bytes = sum(item['utf8_end'] for item in context['inputs'] + context['artifact_proposals'])
    assert text_bytes <= 72000


def test_project_manifest_redacts_sources_outside_the_view():
    room = room_fixture(messages=[message('m1', 1, 'visible')])
    manifest = {'sources': [
        {'kind': 'message', 'room_id': 'room-one', 'message_id': 'm1', 'actor_id': 'a', 'seq': 1,
         'utf8_start': 0, 'utf8_end': 7, 'total_utf8_bytes': 7, 'truncated': False,
         'source_id': 'talkabout:room:room-one:message:m1'},
        {'kind': 'message', 'room_id': 'room-one', 'message_id': 'hidden', 'actor_id': 'a', 'seq': 2,
         'utf8_start': 0, 'utf8_end': 1, 'total_utf8_bytes': 1, 'truncated': False,
         'source_id': 'talkabout:room:room-one:message:hidden'}]}
    projected = project_manifest(manifest, room['messages'], [])
    assert [source['message_id'] for source in projected['sources']] == ['m1']
    assert projected['redacted_source_count'] == 1


def test_project_task_drops_unavailable_result_and_review_metadata():
    task = {'id': 'task-one', 'result_message_id': 'hidden', 'handoff': {'message_id': 'hidden'},
            'review_target': {'artifact_id': 'art', 'version_id': 'gone'}}
    projected = project_task(task, [], [])
    assert 'result_message_id' not in projected
    assert projected['handoff'] == {'result_available': False}
    assert 'review_target' not in projected and projected['review_target_available'] is False


def test_fit_context_shrinks_text_but_not_metadata():
    room = room_fixture(messages=[message('m' + str(index), index + 1, 'z' * 40000) for index in range(8)],
                        artifacts=[artifact('art', 1, [version('v1', 20, 'w' * 40000)])])
    context = shared_context(room, {'id': 'task-one'})
    context['big_padding'] = 'p' * MAX_INPUT
    with pytest.raises(ContextBudgetError, match='^invalid_runtime_budget$'):
        fit_context(context)


def test_manifest_rejects_forged_or_out_of_scope_references(fixture):
    _, core, rid = fixture
    core.mutation('fixture-human', rid, 'messages', 'source',
                  {'kind': 'advice', 'client_message_id': 'source', 'text': '한글 source',
                   'mentions': [], 'reply_to': None})
    task = leased(core, rid)
    manifest = copy.deepcopy(shared_context(core.read('fixture-a', rid), task)['input_manifest'])
    assert manifest['sources'], 'the worker context should reference at least the message'
    for field, value in [('room_id', 'other-room'), ('actor_id', 'b'), ('seq', 999999),
                         ('source_id', 'invented'), ('utf8_end', 1)]:
        forged = copy.deepcopy(manifest)
        forged['sources'][0][field] = value
        before = copy.deepcopy(core.store.rooms[rid])
        with pytest.raises(Problem, match='invalid_input_source'):
            core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/complete', 'forged-' + field,
                          {**result(task), 'result': {**result(task)['result'], 'input_manifest': forged}})
        assert core.store.rooms[rid] == before, field + ' must not mutate the room'


def test_legacy_completion_without_a_manifest_is_recorded_as_unverified(fixture):
    _, core, rid = fixture
    task = leased(core, rid)
    core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/complete', 'legacy-complete', result(task))
    room = core.read('fixture-human', rid)
    posted = next(m for m in room['messages'] if m['kind'] == 'contribution')
    assert posted['input_evidence'] == 'legacy_input_unverified'
    stored = next(t for t in room['tasks'] if t['id'] == task['id'])
    assert stored['input_manifest']['evidence'] == 'legacy_input_unverified'


def test_worker_sends_a_manifest_the_server_validates(fixture, tmp_path):
    store, core, rid = fixture
    core.mutation('fixture-human', rid, 'messages', 'context',
                  {'kind': 'advice', 'client_message_id': 'context', 'text': 'Synthetic context',
                   'mentions': [], 'reply_to': None})
    runtime = Captured()
    client = LocalClient(store)
    journal = Journal(tmp_path / 'worker.sqlite')
    try:
        assert Worker(client, journal, runtime).step() == 'completed'
    finally:
        journal.close()
        client.http.close()
    context = runtime.contexts[0]
    assert context['input_manifest']['evidence'] == 'prepared_worker_input_not_model_read_receipt'
    assert any(item['source_id'] for item in context['inputs'])
    room = core.read('fixture-human', rid)
    posted = next(m for m in room['messages'] if m.get('input_evidence'))
    assert posted['input_evidence'] == 'worker_reported_server_validated_refs_not_model_read_receipt'
    stored = next(t for t in room['tasks'] if t.get('input_manifest'))
    assert stored['input_manifest']['evidence'] == 'worker_reported_server_validated_refs_not_model_read_receipt'
    assert stored['input_manifest']['sources']


def test_artifact_versions_carry_a_stable_revision_number(fixture):
    _, core, rid = fixture
    created = core.mutation('fixture-a', rid, 'artifacts', 'artifact-one',
                            {'generation': 1, 'membership_revision': 2, 'text': 'first text', 'sources': []})
    artifact_id = created['id'] if 'id' in created else created['artifact']['id']
    appended = core.mutation('fixture-a', rid, 'artifacts/' + artifact_id + '/versions', 'artifact-two',
                             {'generation': 1, 'membership_revision': 2, 'expected_revision': 1,
                              'text': 'second text', 'sources': []})
    versions = appended['versions'] if 'versions' in appended else appended['artifact']['versions']
    assert [version['version_revision'] for version in versions] == [1, 2]
