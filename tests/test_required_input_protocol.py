"""Required-input protocol admission and reference checks are not provider receipts."""
import copy
import hashlib
import uuid

import pytest

from talkabout.collaboration import shared_context
from talkabout.domain import Problem
from test_work_plan_dispatch import prepare, dispatch, node, claim, complete, plan_task


def setup_child(fixture, required=None):
    store, core, rid = fixture
    deps = ['one'] if required is None else []
    required = ['one'] if required is None else required
    nodes = ([node('one')] if deps else []) + [node('child', deps=deps, required_predecessors=required)]
    prepare(store, core, rid, nodes, 'fixture-a')
    dispatch(core, rid)
    if deps:
        complete(core, rid, claim(core, rid))
    return store, core, rid, plan_task(core, rid)


def opt_claim(core, rid, task, limit=8):
    body = {k: task[k] for k in ('generation', 'membership_revision')}
    body.update(execution_protocol='server_start_v1', input_protocol='required_predecessors_v1',
                required_predecessors_limit=limit)
    return core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/claim', uuid.uuid4().hex, body)


def tampered(manifest, index, *, omit=False):
    value = copy.deepcopy(manifest)
    if omit:
        value['sources'][index].pop('sha256')
    else:
        value['sources'][index]['sha256'] = '0' * 64
    return value


def test_empty_contract_rejects_legacy_claim(fixture):
    _, core, rid, task = setup_child(fixture, [])
    assert task['input_protocol_required'] is True
    with pytest.raises(Problem, match='input_protocol_required'):
        claim(core, rid)
    assert opt_claim(core, rid, task)['input_protocol'] == 'required_predecessors_v1'


def test_required_manifest_and_visibility(fixture):
    store, core, rid, task = setup_child(fixture)
    task = opt_claim(core, rid, task)
    room = core.read('fixture-a', rid)
    projected = next(t for t in room['tasks'] if t['id'] == task['id'])
    required_id = projected['plan_node']['required_predecessor_message_ids'][0]
    manifest = shared_context(room, projected)['input_manifest']
    index, source = next((index, value) for index, value in enumerate(manifest['sources'])
                         if value.get('kind') == 'message' and value.get('message_id') == required_id)
    text = next(m for m in room['messages'] if m['id'] == required_id)['text']
    # Content binding is exact UTF-8 bytes of the included range, not the whole message
    # when truncated and not a signature or delivery receipt.
    assert source['sha256'] == hashlib.sha256(text.encode('utf-8')[:source['utf8_end']]).hexdigest()
    fence = {k: task[k] for k in ('attempt', 'generation', 'membership_revision')}
    fence['reservation_id'] = uuid.uuid4().hex + uuid.uuid4().hex
    path = 'tasks/' + task['id']
    assert core.mutation('fixture-a', rid, path + '/reservations', 'reserve-protocol', fence)['reserved']
    with pytest.raises(Problem, match='input_unavailable'):
        core.mutation('fixture-a', rid, path + '/start', 'missing-protocol', fence)
    for suffix, bad in (('tampered', tampered(manifest, index)),
                        ('omitted', tampered(manifest, index, omit=True))):
        with pytest.raises(Problem, match='invalid_input_source') as error:
            core.mutation('fixture-a', rid, path + '/start', 'bad-sha-' + suffix,
                          {**fence, 'input_manifest': bad})
        assert error.value.status == 422
    assert core.mutation('fixture-a', rid, path + '/start', 'start-protocol', {**fence, 'input_manifest': manifest})['execute']
    result = {'client_message_id': 'child-result', 'text': 'result', 'reply_to': None, 'mentions': []}
    with pytest.raises(Problem, match='input_unavailable'):
        core.mutation('fixture-a', rid, path + '/complete', 'missing-completion', {**fence, 'result': result})
    for suffix, bad in (('tampered', tampered(manifest, index)),
                        ('omitted', tampered(manifest, index, omit=True))):
        with pytest.raises(Problem, match='invalid_input_source'):
            core.mutation('fixture-a', rid, path + '/complete', 'bad-complete-sha-' + suffix,
                          {**fence, 'result': {**result, 'input_manifest': bad}})
    result['input_manifest'] = manifest
    assert core.mutation('fixture-a', rid, path + '/complete', 'complete-protocol', {**fence, 'result': result})['task']['state'] == 'completed'


def test_projection_is_all_or_nothing(fixture):
    store, core, rid, task = setup_child(fixture)
    raw = store.rooms[rid]['tasks'][task['id']]
    binding = raw['_plan']['dependencies'][0]
    store.rooms[rid]['members']['a']['read_start_seq'] = store.rooms[rid]['messages'][binding['message_id']]['seq'] + 1
    view = core.read('fixture-a', rid)
    projected = next(t for t in view['tasks'] if t['id'] == task['id'])
    assert projected['plan_node']['required_predecessor_message_ids'] is None
    assert binding['message_id'] not in str(projected)


def test_legacy_leased_reservation_rejected(fixture):
    store, core, rid, task = setup_child(fixture, [])
    raw = store.rooms[rid]['tasks'][task['id']]
    raw.update(state='leased', attempt=1, lease_expires_at=raw['deadline'], execution_protocol='server_start_v1')
    fence = {k: raw[k] for k in ('attempt', 'generation', 'membership_revision')}
    fence['reservation_id'] = uuid.uuid4().hex + uuid.uuid4().hex
    with pytest.raises(Problem, match='input_protocol_required'):
        core.mutation('fixture-a', rid, 'tasks/' + task['id'] + '/reservations', 'legacy-reserve', fence)
