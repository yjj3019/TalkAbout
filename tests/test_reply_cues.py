"""Reply-cue classification: result versus "still working" notes.

TalkAbout-owned contract; references the *idea* that a short acknowledgment
should not be mistaken for the final result and that classification is a cue on
text, never verification. Deterministic and pure.
"""
import pytest

from talkabout.reply_cues import MAX_CUE_BYTES, classify_reply, proceeding_note


def test_explicit_final_always_wins():
    assert classify_reply('Working on it', explicit_final=True) == 'final'
    assert classify_reply('', explicit_final=True) == 'final'
    assert classify_reply('on it', explicit_final=True) == 'final'


def test_blank_or_oversized_is_final():
    assert classify_reply('') == 'final'
    assert classify_reply('a' * (MAX_CUE_BYTES + 1)) == 'final'
    assert classify_reply('working on it\nsecond\nthird\nfourth') == 'final'


def test_result_marker_wins_over_opener():
    assert classify_reply('Working on it, here is the result') == 'final'
    assert classify_reply('on it\nsent the report') == 'final'
    assert classify_reply('확인 중, 완료') == 'final'


def test_openers_are_proceeding():
    assert classify_reply('Working on it') == 'proceeding'
    assert classify_reply('Got it, checking this') == 'proceeding'
    assert classify_reply('looking into that') == 'proceeding'
    assert classify_reply('진행 중') == 'proceeding'


def test_promising_later_is_proceeding():
    assert classify_reply('Full report will follow') == 'proceeding'
    assert classify_reply('details to follow') == 'proceeding'


def test_plain_text_is_final():
    assert classify_reply('The report is being prepared and will be sent tomorrow') == 'final'
    assert classify_reply('The venue is working on it') == 'final'
    assert classify_reply('I have no information about this') == 'final'


def test_short_result_is_final():
    assert classify_reply('no') == 'final'


def test_proceeding_note_is_bounded_and_pure():
    note, truncated = proceeding_note('Working on it, checking two things now', limit=32)
    assert len(note.encode('utf-8')) <= 32
    assert truncated is True
    out, _ = proceeding_note('Working on it', limit=200)
    assert out == 'Working on it' and truncated is True


def test_proceeding_note_validates_limit():
    with pytest.raises(ValueError):
        proceeding_note('x', limit=-1)
        proceeding_note('x', limit=1.5)
