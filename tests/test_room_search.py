"""Room-message search: visibility floor, literal terms, read-only (human).

TalkAbout-owned contract. References the *idea* that search shows only what the
viewer can already read and never marks anything seen; no external code was
copied. Matches are projections of visible text, not verification.
"""
import copy

import pytest

from conftest import LocalClient
from talkabout.domain import Problem
from talkabout.search import LIMIT_RANGE, MAX_QUERY_BYTES, room_search, _words


def message(seq, text, **extra):
    base = {'id': f'm{seq}', 'seq': seq, 'actor_id': 'human', 'actor_kind': 'human', 'text': text}
    base.update(extra)
    return base


def test_punctuation_only_queries_match_nothing():
    assert room_search([message(1, 'alpha beta')], viewer_floor=1, query='!!!') == {'results': []}
    assert _words('a,b. c!!') == ['a', 'b', 'c']


def test_all_terms_must_match_case_insensitively():
    haystack = [message(1, 'Alpha release notes'), message(2, 'beta release'), message(3, 'alpha only')]
    results = room_search(haystack, viewer_floor=1, query='release alpha')
    assert [m['seq'] for m in results['results']] == [1]
    assert [m['seq'] for m in room_search(haystack, viewer_floor=1, query='BETA')['results']] == [2]


def test_pre_floor_messages_are_invisible():
    haystack = [message(1, 'secret phrase'), message(2, 'phrase now visible')]
    results = room_search(haystack, viewer_floor=2, query='phrase')
    assert [m['seq'] for m in results['results']] == [2]


def test_newest_first_and_limit_applies():
    haystack = [message(seq, f'needle {seq}') for seq in range(1, 6)]
    results = room_search(haystack, viewer_floor=1, query='needle', limit=2)
    assert [m['seq'] for m in results['results']] == [5, 4]


def test_projection_fields_only_and_no_mutation():
    haystack = [message(1, 'alpha', display_name='Hidden')]
    before = copy.deepcopy(haystack)
    results = room_search(haystack, viewer_floor=1, query='alpha')
    assert set(results['results'][0]) == {'seq', 'id', 'actor_id', 'actor_kind', 'text'}
    assert haystack == before


def test_validates_inputs_fail_closed():
    with pytest.raises(ValueError):
        room_search([message(1, 'x')], viewer_floor=1, query='')
        room_search([message(1, 'x')], viewer_floor=1, query='a' * (MAX_QUERY_BYTES + 1))
        room_search([message(1, 'x')], viewer_floor=1, query='x', limit=0)
        room_search([message(1, 'x')], viewer_floor=1, query='x', limit=LIMIT_RANGE[1] + 1)
        room_search([message(1, 'x')], viewer_floor=1, query=None)


def test_room_search_route_matches_visible_messages_and_stays_read_only(fixture):
    store, core, rid = fixture
    for key, text, kind in [('s-1', 'alpha design note', 'advice'), ('s-2', 'beta report', 'advice'), ('s-3', 'alpha beta mix', 'advice')]:
        core.mutation('fixture-human', rid, 'messages', key, {'text': text, 'kind': kind, 'client_message_id': key})
    room = store.rooms[rid]
    before = copy.deepcopy(room)
    client = LocalClient(store, 'fixture-human')
    results = client.request('GET', f'rooms/{rid}/search', params={'q': 'alpha'})['results']
    seqs = [m['seq'] for m in results]
    assert seqs == sorted(seqs, reverse=True) and seqs == [m['seq'] for m in room['messages'].values() if 'alpha' in m['text']][::-1]
    assert store.rooms[rid] == before
    limited = client.request('GET', f'rooms/{rid}/search', params={'q': 'alpha', 'limit': '1'})['results']
    assert [m['seq'] for m in limited] == [seqs[0]]
    assert store.rooms[rid] == before


def test_room_search_route_rejects_invalid_and_unprivileged(fixture):
    store, core, rid = fixture
    core.mutation('fixture-human', rid, 'messages', 'pos', {'text': 'alpha design note', 'kind': 'advice', 'client_message_id': 'pos'})
    room = store.rooms[rid]
    alpha = [m['seq'] for m in room['messages'].values() if m['text'] == 'alpha design note']
    assert alpha and core.room_search('fixture-human', rid, 'alpha', limit=1)['results'][0]['seq'] == alpha[0]
    with pytest.raises(Problem):
        core.room_search('fixture-human', rid, '')
    with pytest.raises(Problem):
        core.room_search('fixture-human', rid, 'x', limit=0)
    with pytest.raises(Problem):
        core.room_search('fixture-a', rid, 'x')
    client = LocalClient(store, 'fixture-human')
    assert client.request('GET', f'rooms/{rid}/search', params={'q': '!'}) == {'results': []}
