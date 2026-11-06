"""Shared-input provenance: bounded text and server-validated references.

Nothing here proves a model read anything. These tests pin the boundary: what a worker
may send, what the server can re-check, and what is recorded when it cannot.
"""
import copy

import pytest

from conftest import LocalClient, leased, result
from talkabout.collaboration import (MAX_INPUT, ContextBudgetError, excerpt, fit_context,
                                     input_fingerprint, project_manifest, project_task, shared_context,
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


def message(mid, seq, text, actor='a', reply_to=None):
    return {'id': mid, 'actor_id': actor, 'actor_kind': 'agent', 'display_name': actor,
            'seq': seq, 'kind': 'contribution', 'text': text, 'reply_to': reply_to}


def test_input_fingerprint_avalanches_and_stays_distinct():
    # Closes the "암호학적 강도/충돌" NOTRUN note with evidence: 120
    # pairwise-distinct claimed sets and a one-character flip in one bound sha256
    # that must flip well over half of the 256 digest bits (avalanche).
    import secrets
    digests = {}
    for i in range(120):
        sources = [{'source_id': 's%d-%d' % (k, i), 'sha256': secrets.token_hex(32)} for k in range(3)]
        fp = input_fingerprint(sources)
        assert fp is not None and len(fp) == 64 and fp not in digests
        digests[fp] = sources
    base_fp, base_sources = next(iter(digests.items()))
    flipped = [dict(s) for s in base_sources]
    first = flipped[0]
    flipped[0]['sha256'] = ('0' if first['sha256'][0] != '0' else '1') + first['sha256'][1:]
    flipped_fp = input_fingerprint(flipped)
    assert flipped_fp != base_fp
    changed_bits = bin(int(base_fp, 16) ^ int(flipped_fp, 16)).count('1')
    assert changed_bits >= 100, changed_bits


def test_input_fingerprint_binds_exact_bytes_in_source_id_order():
    # R6: a deliverable's input_sha256 is deterministic over verified bytes.
    forward = [{'source_id': 's1', 'sha256': 'a' * 64}, {'source_id': 's2', 'sha256': 'b' * 64}]
    shuffled = list(reversed(forward))
    assert input_fingerprint(forward) == input_fingerprint(shuffled)
    assert len(input_fingerprint(forward)) == 64
    changed = [{'source_id': 's1', 'sha256': 'c' * 64}, {'source_id': 's2', 'sha256': 'b' * 64}]
    assert input_fingerprint(forward) != input_fingerprint(changed)


def test_input_fingerprint_has_no_separator_boundary_collisions():
    # Regression for the R6 simulation finding (2026-11-06): joining rows with
    # "\\n"/"\\t" let an embedded separator merge rows, so two *different*
    # claimed sets produced the same digest. Length-prefixed serialization must
    # keep them distinct.
    embedded = input_fingerprint([{'source_id': 'a', 'sha256': 'b\nc\td'}])
    split = input_fingerprint([{'source_id': 'a', 'sha256': 'b'},
                              {'source_id': 'c', 'sha256': 'd'}])
    assert embedded != split
    assert embedded is not None and split is not None
    lone_tab = input_fingerprint([{'source_id': 'x\ty', 'sha256': ''}])
    assert lone_tab is not None and lone_tab != input_fingerprint([{'source_id': 'x', 'sha256': 'y'}])


def test_input_fingerprint_is_none_only_when_no_source_is_claimed():
    # A claimed-but-unbound source must never compare equal to "nothing claimed".
    assert input_fingerprint([]) is None
    unbound = input_fingerprint([{'source_id': 's1'}])
    assert unbound is not None
    assert input_fingerprint([{'source_id': 's1', 'sha256': ''}]) == unbound
    empty_sha = input_fingerprint([{'source_id': 's1', 'sha256': ''}, {'source_id': 's2', 'sha256': '00' * 32}])
    assert empty_sha != input_fingerprint([{'source_id': 's2', 'sha256': '00' * 32}])


def test_validated_manifest_sources_carry_their_byte_binding():
    import hashlib
    digest = hashlib.sha256(b'one').hexdigest()
    room = room_fixture(messages=[message('m1', 1, 'one'), message('m2', 2, 'two')])
    manifest = {'snapshot_seq': 2,
               'sources': [{'kind': 'message', 'room_id': 'room-one', 'actor_id': 'a', 'seq': 1,
                            'message_id': 'm1', 'utf8_start': 0, 'utf8_end': 3,
                            'total_utf8_bytes': 3, 'truncated': False, 'sha256': digest,
                            'source_id': 'talkabout:room:room-one:message:m1'}]}
    verified = validate_manifest(manifest, room, {'id': 't'})['sources']
    assert verified[0]['sha256'] == digest
    assert input_fingerprint(verified) is not None


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


# --- Reply threads: only the newest messages may pull a few visible ancestors. ---

def thread(*, replies, count, pinned=None, handoff=None):
    messages = [message('m' + str(index), index + 1, 'line ' + str(index),
                        reply_to=replies.get(index)) for index in range(count)]
    room = room_fixture(messages=messages, pinned=pinned, last_seq=count + 1)
    task = {'id': 'task-one'}
    if handoff:
        task['handoff'] = {'message_id': handoff}
    return room, task


def test_newest_replies_pull_up_to_three_visible_ancestors():
    room, task = thread(replies={33: 'm32', 34: 'm33', 35: 'm34', 36: 'm35',
                                 38: 'm37', 39: 'm38'}, count=40)
    context = shared_context(room, task)
    ids = [item['id'] for item in context['inputs']]
    assert len(ids) == 8 and len(set(ids)) == 8
    assert ids == sorted(ids, key=lambda mid: int(mid[1:]))
    assert {'m36', 'm37', 'm38', 'm39'} <= set(ids)
    # Seeds are the newest four only; m36's chain contributes three nearest ancestors,
    # then the remaining slot is filled with the newest unselected message.
    assert {'m35', 'm34', 'm33'} <= set(ids)
    assert 'm32' in ids and 'm31' not in ids
    manifest = context['input_manifest']
    assert manifest['omissions']['messages']['count'] == len(room['messages']) - len(ids)
    assert manifest['event_range'][0] == min(item['seq'] for item in context['inputs'])
    assert manifest['evidence'] == 'prepared_worker_input_not_model_read_receipt'
    assert len({source['source_id'] for source in manifest['sources']}) == len(manifest['sources'])
    assert sum(item['utf8_end'] for item in context['inputs']) <= 72000
    assert all(item['utf8_end'] <= 6000 for item in context['inputs'])
    assert all('reply_to' not in item for item in context['inputs'])


def test_no_ancestors_are_imported_when_the_newest_four_have_no_reply():
    # An old, busy thread must not be revived: the newest four carry no reply_to.
    replies = {5: 'm4', 6: 'm5', 7: 'm6', 8: 'm7'}
    room, task = thread(replies=replies, count=20)
    context = shared_context(room, task)
    ids = {item['id'] for item in context['inputs']}
    assert ids == {'m' + str(index) for index in range(12, 20)}
    assert not ids & {'m5', 'm6', 'm7', 'm8'}


def test_deep_thread_keeps_only_three_nearest_ancestors():
    replies = {index: 'm' + str(index - 1) for index in range(1, 10)}
    room, task = thread(replies=replies, count=10)
    context = shared_context(room, task)
    ids = [item['id'] for item in context['inputs']]
    # Newest four are m6..m9; the chain continues to m5, m4, m3 only (three hops).
    assert {'m6', 'm7', 'm8', 'm9'} <= set(ids)
    assert {'m5', 'm4', 'm3'} <= set(ids)
    assert 'm1' not in ids and 'm0' not in ids
    assert len(ids) == 8


def test_missing_cyclic_and_forward_parents_are_ignored():
    room, task = thread(replies={7: 'm8', 8: 'm7', 6: 'm6', 5: 'ghost'}, count=9)
    context = shared_context(room, task)
    ids = [item['id'] for item in context['inputs']]
    assert len(ids) == 8 and len(set(ids)) == 8
    assert set(ids) <= {item['id'] for item in room['messages']}
    assert 'ghost' not in ids


def test_pinned_and_handoff_leave_only_remaining_slots_for_ancestors():
    replies = {index: 'm' + str(index - 1) for index in range(1, 20)}
    room, task = thread(replies=replies, count=20, pinned='m0', handoff='m1')
    context = shared_context(room, task)
    ids = [item['id'] for item in context['inputs']]
    assert len(ids) == 8
    assert {'m0', 'm1'} <= set(ids)
    # Four newest + pinned/handoff leave two slots: nearest ancestors only.
    assert {'m16', 'm17', 'm18', 'm19'} <= set(ids)
    assert {'m15', 'm14'} <= set(ids)
    assert 'm13' not in ids and 'm2' not in ids
