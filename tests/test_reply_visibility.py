"""Synthetic contracts: reply identifiers stay behind each viewer's history boundary.

Stored messages keep their authoritative ``reply_to``. Only the read projection
drops an identifier when the parent message is at or before the viewer's
``read_start_seq`` (the viewer joined or reset after the parent was posted).
"""
import copy

from talkabout.domain import AGENT_PERMISSIONS, SELF_PERMISSIONS


def _post(core, rid, key, text, reply_to=None):
    return core.mutation('fixture-human', rid, 'messages', key, {
        'kind': 'question', 'client_message_id': key, 'text': text, 'reply_to': reply_to})


def _view(core, token, rid):
    return {message['id']: message for message in core.read(token, rid)['messages']}


def _join_agent(store, core, rid, pid):
    """Provision one synthetic agent and let it accept a human invitation."""
    store.provision({'id': pid, 'owner': 'fixture-owner', 'kind': 'agent',
                     'display_name': pid.upper(), 'handle': pid},
                    'fixture-' + pid, AGENT_PERMISSIONS | SELF_PERMISSIONS)
    member = core.mutation('fixture-human', rid, 'memberships', 'invite-' + pid,
                           {'principal_id': pid, 'permissions': sorted(AGENT_PERMISSIONS),
                            'status': 'pending'})
    return core.accept('fixture-' + pid, rid, 'accept-' + pid,
                       {'expected_revision': member['revision'], 'acknowledge_private': True})


def test_hidden_parent_identifier_is_nulled_for_late_viewer(fixture):
    store, core, rid = fixture
    root = _post(core, rid, 'root-1', 'root question')
    # The late viewer's boundary is (root.seq + 1), so it never saw the root.
    _join_agent(store, core, rid, 'c')
    reply = _post(core, rid, 'reply-1', 'answer to root', root['id'])
    second = _post(core, rid, 'reply-2', 'answer to answer', reply['id'])

    late = _view(core, 'fixture-c', rid)
    assert root['id'] not in late
    assert late[reply['id']]['reply_to'] is None
    assert late[reply['id']]['text'] == 'answer to root'
    assert late[second['id']]['reply_to'] == reply['id']
    # Non-reference metadata is untouched.
    assert late[reply['id']]['actor_id'] == 'human'
    assert late[reply['id']]['kind'] == 'question'
    assert late[reply['id']]['seq'] == reply['seq']

    for token in ('fixture-human', 'fixture-a', 'fixture-b'):
        view = _view(core, token, rid)
        assert view[reply['id']]['reply_to'] == root['id']
        assert view[second['id']]['reply_to'] == reply['id']


def test_same_reply_projection_across_read_paths(fixture):
    store, core, rid = fixture
    root = _post(core, rid, 'root-1', 'root question')
    _join_agent(store, core, rid, 'c')
    reply = _post(core, rid, 'reply-1', 'answer to root', root['id'])

    read = core.read('fixture-c', rid)
    snapshot = core.room_snapshot('fixture-c', rid)['room']
    listed = [room for room in core.rooms('fixture-c') if room['id'] == rid][0]
    for room in (read, snapshot, listed):
        messages = {message['id']: message for message in room['messages']}
        assert root['id'] not in messages
        assert messages[reply['id']]['reply_to'] is None
        assert messages[reply['id']]['seq'] == reply['seq']


def test_projection_does_not_mutate_stored_room(fixture):
    store, core, rid = fixture
    root = _post(core, rid, 'root-1', 'root question')
    _join_agent(store, core, rid, 'c')
    reply = _post(core, rid, 'reply-1', 'answer to root', root['id'])
    with store.read_transaction(rid, 'fixture-human') as (_, original):
        before = copy.deepcopy(original)

    assert _view(core, 'fixture-c', rid)[reply['id']]['reply_to'] is None

    with store.read_transaction(rid, 'fixture-human') as (_, current):
        assert current['messages'] == before['messages']
        assert current['messages'][reply['id']]['reply_to'] == root['id']
        assert current['events'] == before['events']
        assert current['last_seq'] == before['last_seq']
        assert current['room_revision'] == before['room_revision']
        assert current['generation'] == before['generation']
    # message.created events carry only the id, never the reply identifier.
    assert all('reply_to' not in event for event in before['events'])


def test_missing_parent_is_nulled_for_every_viewer(fixture):
    store, core, rid = fixture
    message = _post(core, rid, 'orphan-1', 'orphan question')
    # Simulate a stored reference whose parent no longer resolves.
    with store.lock:
        store.rooms[rid]['messages'][message['id']]['reply_to'] = 'no-such-message'

    for token in ('fixture-human', 'fixture-a'):
        assert _view(core, token, rid)[message['id']]['reply_to'] is None
    with store.read_transaction(rid, 'fixture-human') as (_, original):
        assert original['messages'][message['id']]['reply_to'] == 'no-such-message'


def test_plain_messages_keep_null_reference(fixture):
    store, core, rid = fixture
    message = _post(core, rid, 'plain-1', 'no reference')
    for token in ('fixture-human', 'fixture-a'):
        view = _view(core, token, rid)[message['id']]
        assert 'reply_to' in view and view['reply_to'] is None
