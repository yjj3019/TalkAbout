"""Completion criteria are bounded untrusted text, never execution grants."""
import pytest

from talkabout.proposal_done_when import valid_done_when


@pytest.mark.parametrize('criteria', [
    ['x'], ['x' * 200], ['한글 결과', 'line\nnext', 'tab\there'], ['x'] * 8,
])
def test_accept_bounded_nonblank_criteria(criteria):
    assert valid_done_when(criteria) is True


@pytest.mark.parametrize('criteria', [
    None, [], ['x'] * 9, '', 'text', ('x',), {}, [None], [True], [1],
    [''], ['  \n\t'], ['x' * 201], ['x\x00y'], ['x\u200by'], ['bad\ud800'],
    ['x', ''], ['x', 'y' * 201],
])
def test_reject_nonconforming_criteria(criteria):
    assert valid_done_when(criteria) is False
