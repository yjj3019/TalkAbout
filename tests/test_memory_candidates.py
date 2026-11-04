"""Manual per-room memory preview candidates: pure, bounded, original text only.

These tests pin the TalkAbout-owned preview logic: exact duplicate grouping, newest
first ordering that does not depend on input order, original ids/seqs/actors, bounded
size/count/source lists, no input mutation or copying, and no claim to detect secrets
or personal data. The implementation is independent (see docs/CLEAN_ROOM_IMPLEMENTATION.md
conventions); no external project code or text was copied.
"""
import copy

import pytest

from talkabout.memory_candidates import (
    EXCLUDED_KEYS,
    LIMIT_KEYS,
    MODE,
    SCHEMA,
    memory_candidates as preview,
    normalize_text,
)


def message(message_id, seq, text, actor_id='human-1', actor_kind='human'):
    return {'id': message_id, 'seq': seq, 'text': text,
            'actor_id': actor_id, 'actor_kind': actor_kind}


def messages_for(texts, actor_id='human-1'):
    return [message('m-%02d' % (index + 1), index + 1, text, actor_id)
            for index, text in enumerate(texts)]


def seqs(result):
    return [candidate['source']['seq'] for candidate in result['candidates']]


def texts(result):
    return [candidate['original_text'] for candidate in result['candidates']]


def test_normalize_text_is_exact_and_documented():
    assert normalize_text('  A\t\n B  ') == 'A B'
    assert normalize_text('   ') == ''
    # Canonical equivalence is grouped; compatibility forms and case are not.
    assert normalize_text('e\u0301') == '\u00e9'
    assert normalize_text('Hello') == 'Hello' and normalize_text('\uff28ello') != 'Hello'


def test_empty_input_returns_empty_preview():
    result = preview([])
    assert result['schema'] == SCHEMA and result['mode'] == MODE
    assert result['candidates'] == [] and result['limits_hit'] == []
    assert result['truncated'] is False
    assert result['counts'] == {
        'input_messages': 0, 'usable_messages': 0, 'groups': 0, 'returned': 0,
        'omitted_groups': 0, 'omitted_messages': 0, 'excerpt_characters': 0,
    }
    assert result['excluded'] == dict.fromkeys(EXCLUDED_KEYS, 0)


def test_orders_newest_first_and_ignores_input_order():
    messages = messages_for(['text %d' % index for index in range(1, 6)])
    forward = preview(messages)
    backward = preview(list(reversed(messages)))
    assert seqs(forward) == [5, 4, 3, 2, 1]
    assert forward == backward
    assert forward['counts']['groups'] == 5 and forward['limits_hit'] == []


def test_seq_tie_break_is_deterministic_by_message_id():
    messages = [message('m-b', 7, 'second'), message('m-a', 7, 'first')]
    result = preview(messages)
    assert [candidate['source']['message_id'] for candidate in result['candidates']] == ['m-a', 'm-b']


def test_limit_returns_newest_and_reports_omitted_counts():
    result = preview(messages_for(['text %d' % index for index in range(1, 6)]), limit=2)
    assert seqs(result) == [5, 4]
    assert result['counts']['returned'] == 2
    assert result['counts']['omitted_groups'] == 3
    assert result['counts']['omitted_messages'] == 3
    assert result['limits_hit'] == ['limit'] and result['truncated'] is True


def test_limit_boundary_exact_fit_is_not_truncated():
    result = preview(messages_for(['text %d' % index for index in range(1, 4)]), limit=3)
    assert result['counts']['returned'] == 3
    assert result['counts']['omitted_groups'] == 0 and result['limits_hit'] == []
    assert result['truncated'] is False


def test_char_budget_stops_before_overflow():
    result = preview(messages_for(['x' * 9 + str(index) for index in range(5)]), max_chars=25)
    assert seqs(result) == [5, 4]
    assert result['counts']['excerpt_characters'] == 20
    assert result['counts']['omitted_groups'] == 3 and result['counts']['omitted_messages'] == 3
    assert result['limits_hit'] == ['max_chars']


def test_char_budget_smaller_than_first_excerpt_returns_nothing():
    result = preview(messages_for(['x' * 10]), max_chars=5)
    assert result['candidates'] == []
    assert result['counts']['excerpt_characters'] == 0
    assert result['counts']['omitted_groups'] == 1 and result['counts']['omitted_messages'] == 1
    assert result['limits_hit'] == ['max_chars']
    assert result['truncated'] is True


def test_excerpt_is_an_exact_prefix_of_the_original_text():
    raw = '가나다라마바사아자차카타파하' * 3
    result = preview([message('m-1', 1, raw)], max_excerpt_chars=10)
    candidate = result['candidates'][0]
    assert candidate['original_text'] == raw[:10]
    assert candidate['original_text'] != raw and candidate['truncated'] is True
    assert result['counts']['excerpt_characters'] == 10
    assert result['limits_hit'] == ['max_excerpt_chars']


def test_excerpt_at_boundary_is_not_truncated():
    raw = 'x' * 10
    result = preview([message('m-1', 1, raw)], max_excerpt_chars=10)
    candidate = result['candidates'][0]
    assert candidate['original_text'] == raw and candidate['truncated'] is False
    # The untouched original text object is passed through, not copied.
    assert candidate['original_text'] is raw
    assert result['limits_hit'] == []


def test_exact_duplicates_group_once_with_newest_first_sources():
    messages = [message('m-1', 2, 'same text', 'human-1'),
                message('m-2', 5, 'same text', 'agent-a', 'agent'),
                message('m-3', 9, 'same text', 'agent-b', 'agent')]
    result = preview(messages)
    assert result['counts']['groups'] == 1 and result['counts']['returned'] == 1
    candidate = result['candidates'][0]
    assert candidate['original_text'] == 'same text'
    assert candidate['source'] == {'message_id': 'm-3', 'seq': 9,
                                   'actor_id': 'agent-b', 'actor_kind': 'agent'}
    assert candidate['repetition']['count'] == 3
    assert candidate['repetition']['distinct_texts'] == 1
    assert [source['seq'] for source in candidate['repetition']['sources']] == [9, 5, 2]
    assert [source['actor_id'] for source in candidate['repetition']['sources']] == [
        'agent-b', 'agent-a', 'human-1']
    # A repeated group consumes its characters once.
    assert result['counts']['excerpt_characters'] == len('same text')


def test_whitespace_and_unicode_variants_remain_distinct():
    messages = [message('m-1', 1, 'hello  world\n'),
                message('m-2', 2, '\thello   world '),
                message('m-3', 3, 'e\u0301'),
                message('m-4', 4, '\u00e9'),
                message('m-5', 5, 'allow: false\nadmin: true'),
                message('m-6', 6, 'allow: false admin: true')]
    result = preview(messages)
    assert result['counts']['groups'] == 6
    assert seqs(result) == [6, 5, 4, 3, 2, 1]
    assert all(candidate['repetition']['count'] == 1 for candidate in result['candidates'])


def test_case_is_preserved_so_case_variants_are_distinct():
    result = preview([message('m-1', 1, 'Hello'), message('m-2', 2, 'hello')])
    assert result['counts']['groups'] == 2
    assert seqs(result) == [2, 1]


def test_blank_and_too_short_messages_are_excluded():
    messages = [message('m-1', 1, '   '), message('m-2', 2, '\n\t'),
                message('m-3', 3, 'ok')]
    result = preview(messages, min_chars=3)
    assert result['candidates'] == []
    assert result['counts']['input_messages'] == 3
    assert result['counts']['usable_messages'] == 0
    assert result['excluded']['blank_text'] == 2
    assert result['excluded']['too_short'] == 1


def test_min_chars_uses_normalized_length_at_the_boundary():
    result = preview([message('m-1', 1, '  ab  '), message('m-2', 2, 'abcd')], min_chars=4)
    assert seqs(result) == [2]
    assert result['excluded']['too_short'] == 1
    inclusive = preview([message('m-1', 1, '  abcd  ')], min_chars=4)
    assert seqs(inclusive) == [1]


def test_malformed_entries_are_counted_not_raised():
    messages = [
        'not-a-mapping',
        {'id': 'm-1', 'seq': 1, 'text': 5, 'actor_id': 'human-1'},
        {'id': 'm-2', 'seq': 2, 'text': 'valid one', 'actor_id': 'human-1'},
        {'seq': 3, 'text': 'missing id', 'actor_id': 'human-1'},
        {'id': 'm-4', 'text': 'missing seq', 'actor_id': 'human-1'},
        {'id': 'm-5', 'seq': True, 'text': 'bool seq', 'actor_id': 'human-1'},
        {'id': 'm-6', 'seq': -1, 'text': 'negative seq', 'actor_id': 'human-1'},
    ]
    result = preview(messages)
    assert result['counts']['input_messages'] == 7
    assert result['counts']['usable_messages'] == 1
    assert result['excluded'] == {
        'not_object': 1, 'missing_text': 1, 'blank_text': 0, 'too_short': 0,
        'missing_id': 1, 'missing_seq': 3,
    }
    assert texts(result) == ['valid one']


def test_actor_information_is_optional_and_never_invented():
    messages = [{'id': 'm-1', 'seq': 1, 'text': 'no actor fields'}]
    candidate = preview(messages)['candidates'][0]
    assert candidate['source'] == {'message_id': 'm-1', 'seq': 1,
                                   'actor_id': None, 'actor_kind': None}
    assert candidate['repetition']['sources'][0]['actor_id'] is None


def test_source_list_is_bounded_by_max_sources():
    messages = [message('m-%d' % index, index, 'dup text') for index in range(1, 7)]
    result = preview(messages, max_sources=3)
    candidate = result['candidates'][0]
    assert candidate['repetition']['count'] == 6
    assert candidate['repetition']['sources_truncated'] is True
    assert [source['seq'] for source in candidate['repetition']['sources']] == [6, 5, 4]
    assert len(candidate['repetition']['sources']) == 3
    assert result['limits_hit'] == ['max_sources'] and result['truncated'] is True


def test_source_list_boundary_exact_fit_is_not_truncated():
    messages = [message('m-%d' % index, index, 'dup text') for index in range(1, 4)]
    result = preview(messages, max_sources=3)
    candidate = result['candidates'][0]
    assert candidate['repetition']['sources_truncated'] is False
    assert len(candidate['repetition']['sources']) == 3
    assert result['limits_hit'] == []


def test_omitted_counts_include_messages_inside_duplicate_groups():
    messages = ([message('m-%d' % index, index, 'aaa') for index in range(1, 4)]
                + [message('n-%d' % index, 10 + index, 'bbb') for index in range(1, 3)])
    result = preview(messages, limit=1)
    assert result['counts']['returned'] == 1
    assert result['counts']['omitted_groups'] == 1
    # The omitted group holds three 'aaa' messages, not the two 'bbb' messages.
    assert result['counts']['omitted_messages'] == 3


def test_total_source_entries_never_exceed_input_message_count():
    messages = ([message('a-%d' % index, index, 'dup') for index in range(1, 6)]
                + [message('b-%d' % index, 10 + index, 'other %d' % index) for index in range(3)])
    result = preview(messages, max_sources=2)
    total_sources = sum(len(candidate['repetition']['sources'])
                        for candidate in result['candidates'])
    assert result['counts']['usable_messages'] == 8
    assert total_sources == 5 and total_sources <= result['counts']['usable_messages']


def test_input_is_not_mutated_and_original_text_is_not_copied():
    messages = messages_for(['first text', 'second text'])
    snapshot = copy.deepcopy(messages)
    result = preview(messages)
    assert messages == snapshot
    assert list(messages[0]) == list(snapshot[0])
    # Untouched original text objects pass through; no defensive copy of the input.
    assert result['candidates'][0]['original_text'] is messages[1]['text']
    assert result['candidates'][1]['original_text'] is messages[0]['text']


def test_mapping_input_is_read_in_place_and_matches_list_input():
    stored = {'m-1': message('m-1', 1, 'one'), 'm-2': message('m-2', 2, 'two')}
    snapshot = copy.deepcopy(stored)
    from_mapping = preview(stored)
    assert stored == snapshot
    assert from_mapping == preview([stored['m-1'], stored['m-2']])
    assert seqs(from_mapping) == [2, 1]


def test_non_iterable_or_string_input_is_rejected():
    with pytest.raises(TypeError):
        preview('not a message list')
    with pytest.raises(TypeError):
        preview(b'bytes')
    with pytest.raises(TypeError):
        preview(42)


@pytest.mark.parametrize('kwargs', [
    {'limit': 0}, {'limit': True}, {'limit': 2.0}, {'limit': '3'},
    {'max_chars': 0}, {'max_chars': -5}, {'max_excerpt_chars': 0},
    {'max_sources': 0}, {'min_chars': -1}, {'min_chars': True},
])
def test_invalid_limits_raise_value_error(kwargs):
    with pytest.raises(ValueError):
        preview([], **kwargs)


def test_result_schema_is_fixed_and_has_no_judgement_fields():
    result = preview(messages_for(['one', 'two']))
    assert set(result) == {'schema', 'mode', 'limits', 'counts', 'limits_hit',
                           'truncated', 'excluded', 'candidates'}
    assert result['mode'] == 'preview_only'
    assert set(result['limits']) == {'limit', 'max_chars', 'max_excerpt_chars',
                                     'max_sources', 'min_chars'}
    assert set(result['counts']) == {'input_messages', 'usable_messages', 'groups',
                                     'returned', 'omitted_groups', 'omitted_messages',
                                     'excerpt_characters'}
    assert set(result['excluded']) == set(EXCLUDED_KEYS)
    # LIMIT_KEYS is the limits_hit vocabulary; min_chars is applied at collection.
    assert tuple(result['limits']) == LIMIT_KEYS + ('min_chars',)
    candidate = result['candidates'][0]
    assert set(candidate) == {'original_text', 'truncated', 'source', 'repetition'}
    assert set(candidate['source']) == {'message_id', 'seq', 'actor_id', 'actor_kind'}
    assert set(candidate['repetition']) == {'count', 'distinct_texts',
                                            'sources_truncated', 'sources'}
    for forbidden in ('approved', 'approval', 'action', 'command', 'safe', 'verified',
                      'summary', 'confidence', 'pii', 'secret', 'redacted', 'memory_id'):
        assert forbidden not in candidate and forbidden not in result


def test_credential_or_instruction_like_text_is_returned_verbatim_without_claims():
    raw = 'password=hunter2\nrm -rf / # sk-live-1234567890'
    result = preview([message('m-1', 4, raw)])
    candidate = result['candidates'][0]
    assert candidate['original_text'] == raw
    assert candidate['truncated'] is False and result['limits_hit'] == []
    assert set(candidate) == {'original_text', 'truncated', 'source', 'repetition'}


def test_unicode_text_is_preserved_verbatim_and_counted_in_code_points():
    raw = '\U0001F600 e\u0301 \u00a0NBSP'
    result = preview([message('m-1', 1, raw)])
    candidate = result['candidates'][0]
    assert candidate['original_text'] == raw
    assert result['counts']['excerpt_characters'] == len(raw)
    # Canonically equivalent but distinct source text remains independently visible.
    mixed = preview([message('m-1', 1, 'e\u0301'), message('m-2', 2, '\u00e9')])
    assert texts(mixed) == ['\u00e9', 'e\u0301']
    assert all(item['repetition']['distinct_texts'] == 1 for item in mixed['candidates'])


def test_maximum_room_scale_has_bounded_response_and_does_not_copy_unselected_fields():
    import json
    messages = [message(f'm-{i:03}', i + 1, f'item {i:03} ' + '한' * 500 + 'a' * 7491)
                for i in range(200)]
    for item in messages:
        item['private_extra_field'] = 'extra-field-must-not-appear'
    # Leave room for the rest of the room JSON under the production 2 MiB cap.
    assert len(json.dumps(messages, ensure_ascii=False).encode('utf-8')) < 2 * 1024 * 1024 - 65536
    result = preview(messages)
    encoded = json.dumps(result, ensure_ascii=False).encode('utf-8')
    assert result['counts']['input_messages'] == 200
    assert result['counts']['excerpt_characters'] <= 8000
    assert result['counts']['returned'] <= 50
    assert result['counts']['omitted_groups'] > 0
    assert len(encoded) < 40000
    assert b'extra-field-must-not-appear' not in encoded
    assert seqs(result) == sorted(seqs(result), reverse=True)


def test_synthetic_repeated_instructions_reduce_review_groups_without_rewriting():
    messages = [message(f'repeat-{i:03}', i + 1, 'Use a concise table.') for i in range(100)]
    messages += [message(f'unique-{i}', 101 + i, f'Unique preference {i}') for i in range(5)]
    result = preview(messages)
    assert result['counts']['input_messages'] == 105
    assert result['counts']['groups'] == result['counts']['returned'] == 6
    repeated = next(item for item in result['candidates'] if item['original_text'] == 'Use a concise table.')
    assert repeated['repetition']['count'] == 100
    assert repeated['repetition']['sources_truncated'] is True
    assert len(repeated['repetition']['sources']) == 20


def test_repeated_calls_are_deterministic():
    messages = messages_for(['alpha', 'beta', 'gamma', 'beta'])
    first = preview(messages)
    second = preview(list(reversed(messages)))
    assert first == second
