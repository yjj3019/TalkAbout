"""Best-effort exact plan result selection, not provider delivery evidence."""
import copy

from talkabout.collaboration import MAX_MESSAGES, shared_context
from talkabout.core import public_room
from test_work_plan_dispatch import prepare, dispatch, node, claim, complete, plan_task


def test_chain_exact_binding_and_input_provenance(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node('first'), node('second', deps=['first'])], 'fixture-a')
    dispatch(core, rid)
    first = claim(core, rid)
    result = complete(core, rid, first, text='exact result')
    mid = result['message']['id']
    second = claim(core, rid)
    room = core.read('fixture-a', rid)
    snapshot = next(t for t in room['tasks'] if t['id'] == second['id'])
    assert snapshot['plan_node']['predecessor_message_ids'] == [mid]
    context = shared_context(room, second)
    item = next(x for x in context['inputs'] if x['id'] == mid)
    assert item['text'] == 'exact result' and item['untrusted_data']
    ref = next(x for x in context['input_manifest']['sources'] if x.get('message_id') == mid)
    assert ref['utf8_end'] == len('exact result')
    assert context['input_manifest']['evidence'] == 'prepared_worker_input_not_model_read_receipt'
    assert '_plan_result' not in str(result)
    assert '_plan_result' not in str(core.read('fixture-human', rid))


def test_mixed_visibility_and_fence(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node('one'), node('two', deps=['one'])], 'fixture-a')
    dispatch(core, rid)
    result = complete(core, rid, claim(core, rid))
    second = claim(core, rid)
    raw = store.rooms[rid]
    raw['members']['a']['read_start_seq'] = result['message']['seq'] + 1
    view = core.read('fixture-a', rid)
    plan = next(t for t in view['tasks'] if t['id'] == second['id'])['plan_node']
    assert plan['predecessor_message_ids'] == []
    assert result['message']['id'] not in str(plan)
    assert result['message']['id'] not in str(shared_context(view, second))
    stale = copy.deepcopy(second)
    stale['attempt'] += 1
    assert result['message']['id'] not in str(shared_context(view, stale))


def test_mixed_fanin_visibility_and_legacy_missing(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node('one'), node('two'), node('child', deps=['one', 'two'])], 'fixture-a')
    dispatch(core, rid)
    first = complete(core, rid, claim(core, rid), text='hidden predecessor')
    second = complete(core, rid, claim(core, rid), text='visible predecessor')
    child = claim(core, rid)
    room = store.rooms[rid]
    room['members']['a']['read_start_seq'] = second['message']['seq']
    view = core.read('fixture-a', rid)
    plan = next(t['plan_node'] for t in view['tasks'] if t['id'] == child['id'])
    assert plan['predecessor_message_ids'] == [second['message']['id']]
    assert first['message']['id'] not in str(plan)
    context = shared_context(view, child)
    assert second['message']['id'] in [x['id'] for x in context['inputs']]
    assert first['message']['id'] not in str(context)
    # Legacy or lost binding cannot be guessed from otherwise visible messages.
    room['tasks'][child['id']]['_plan']['dependencies'] = []
    view = core.read('fixture-a', rid)
    assert next(t['plan_node'] for t in view['tasks'] if t['id'] == child['id'])['predecessor_message_ids'] == []


def test_missing_binding_is_not_reconstructed(fixture):
    store, core, rid = fixture
    prepare(store, core, rid, [node('one'), node('two'), node('child', deps=['one', 'two'])], 'fixture-a')
    dispatch(core, rid)
    first_task = claim(core, rid)
    first = complete(core, rid, first_task)
    del store.rooms[rid]['tasks'][first_task['id']]['_plan_result']
    second = complete(core, rid, claim(core, rid), text='other result')
    child = claim(core, rid)
    view = core.read('fixture-a', rid)
    plan = next(t['plan_node'] for t in view['tasks'] if t['id'] == child['id'])
    assert plan['predecessor_message_ids'] == [second['message']['id']]
    assert first['message']['id'] not in str(plan)


def test_fanin_cap_and_omissions():
    def msg(i):
        return {'id': str(i), 'seq': i + 1, 'text': str(i), 'actor_id': 'a',
                'actor_kind': 'agent', 'display_name': 'A', 'kind': 'message'}
    task = {'id': 't', 'assignee': 'a', 'attempt': 1, 'generation': 1,
            'membership_revision': 1, 'state': 'leased'}
    current = {**task, 'plan_node': {'predecessor_message_ids': [str(i) for i in range(12)]}}
    room = {'id': 'r', 'generation': 1, 'membership_revision': 1, 'last_seq': 20,
            'tasks': [current], 'messages': [msg(i) for i in range(20)], 'artifacts': []}
    context = shared_context(room, task)
    assert [m['id'] for m in context['inputs']] == [str(i) for i in range(MAX_MESSAGES)]
    assert context['input_manifest']['omissions']['messages']['count'] == 20 - MAX_MESSAGES
    assert len(context['input_manifest']['sources']) == MAX_MESSAGES
    stale = {**task, 'attempt': 2}
    assert '0' not in [m['id'] for m in shared_context(room, stale)['inputs']]
