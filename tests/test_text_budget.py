"""Byte-budget helpers: per-piece allowances and truncation with a fixed notice.

TalkAbout-owned contract; references the *idea* of a notice that stays inside
the budget and fair per-piece allowances. Deterministic and pure.
"""
import pytest

from talkabout.text_budget import BudgetError, fit_lengths, notice_for, truncate_with_notice


def test_fits_returns_unchanged():
    assert truncate_with_notice('hello', 100) == ('hello', False)
    assert truncate_with_notice('', 0) == ('', False)


def test_truncation_never_exceeds_limit_and_reports_counts():
    text = '가나다라마' * 3  # 45 UTF-8 bytes, longer than its notice floor
    floor = len(notice_for(0, 45).encode('utf-8'))
    out, truncated = truncate_with_notice(text, floor + 3)
    assert truncated is True
    assert len(out.encode('utf-8')) <= floor + 3
    shown = len(out.encode('utf-8')) - floor
    assert out.endswith(f'{shown} of 45 bytes]')
    assert out.startswith('가')


def test_notice_itself_counts_inside_limit():
    floor = len(notice_for(0, 50).encode('utf-8'))
    out, truncated = truncate_with_notice('a' * 50, floor + 1)
    assert truncated is True
    assert len(out.encode('utf-8')) <= floor + 1
    assert out.endswith('of 50 bytes]')
    # A limit that cannot even hold the notice fails closed.
    with pytest.raises(BudgetError):
        truncate_with_notice('a' * 50, floor - 1)


def test_truncation_cuts_on_utf8_boundary():
    text = '가나다라마' * 3  # 45 UTF-8 bytes
    floor = len(notice_for(0, 45).encode('utf-8'))
    for limit in range(floor, floor + 4):
        out, truncated = truncate_with_notice(text, limit)
        assert truncated is True
        assert len(out.encode('utf-8')) <= limit
        assert out.encode('utf-8').decode('utf-8') == out
        assert out.endswith('of 45 bytes]')
    # Below the notice floor the operation fails closed instead of dropping it.
    with pytest.raises(BudgetError):
        truncate_with_notice('가나다라마', 3)


def test_notice_counts_validate():
    assert notice_for(0, 5) == '[truncated: 0 of 5 bytes]'
    with pytest.raises(BudgetError):
        notice_for(6, 5)
        notice_for(-1, 5)
        notice_for(1.5, 5)


def test_fit_lengths_fair_shares_with_reserve():
    caps = fit_lengths([100, 10, 10], 100, reserve=8)
    assert caps == [56, 10, 10]
    assert sum(caps) <= 100
    assert sum(caps) == 100 - 8 * 3


def test_fit_lengths_leaves_small_pieces_whole_evenly():
    assert sorted(fit_lengths([5, 5, 5], 9)) == [3, 3, 3]
    assert sum(fit_lengths([5, 5, 5], 9)) == 9


def test_fit_lengths_budget_smaller_than_reserve_fails_closed():
    with pytest.raises(BudgetError):
        fit_lengths([1, 1], 1, reserve=1)


def test_fit_lengths_validates_inputs():
    with pytest.raises(BudgetError):
        fit_lengths([1, -1], 10)
        fit_lengths([1], -1)
        fit_lengths([1], 10, reserve=-1)
        fit_lengths('nope', 10)


def test_fit_lengths_all_fits_without_reserve_returns_lengths():
    assert fit_lengths([3, 4], 100) == [3, 4]
