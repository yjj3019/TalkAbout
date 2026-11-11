"""Byte-budget helpers: fair per-piece allowances and truncation with a notice.

TalkAbout-owned design for packing several pieces of text into one byte budget.
The behaviour references the *idea* that a fixed truncation notice must itself
stay inside the budget and that small pieces must not be starved by a large one;
no external code was copied (docs/CLEAN_ROOM_IMPLEMENTATION.md).
"""

_NOTICE_TEXT = '[truncated: {shown} of {total} bytes]'


class BudgetError(ValueError):
    """Raised when a budget request is unsatisfiable (fail-closed, no clamping)."""


def notice_for(shown_bytes, total_bytes):
    """Return the fixed truncation notice for ``shown_bytes`` of ``total_bytes``."""
    if not isinstance(shown_bytes, int) or not isinstance(total_bytes, int):
        raise BudgetError('notice_counts_must_be_ints')
    if shown_bytes < 0 or total_bytes < 0 or shown_bytes > total_bytes:
        raise BudgetError('notice_counts_out_of_order')
    return _NOTICE_TEXT.format(shown=shown_bytes, total=total_bytes)


def truncate_with_notice(text, limit):
    """Cut ``text`` to at most ``limit`` UTF-8 bytes, appending a notice.

    The notice states the shown and total byte counts and its length is
    deducted from the budget first, so the result never exceeds ``limit``.
    A text that already fits is returned unchanged with ``truncated=False``.
    An empty text is never given a notice. A non-empty text that cannot fit
    even its notice raises BudgetError instead of silently dropping the notice.
    """
    if not isinstance(text, str):
        raise BudgetError('text_not_str')
    if not isinstance(limit, int) or limit < 0:
        raise BudgetError('limit_not_nonnegative_int')
    raw = text.encode('utf-8')
    if len(raw) <= limit:
        return text, False
    total = len(raw)
    # The notice itself may eat the whole budget for a tiny limit; anything
    # smaller is unsatisfiable for a non-empty text.
    floor = len(notice_for(0, total))
    if limit < floor:
        raise BudgetError('notice_does_not_fit')
    # Start from the widest possible shown count, then shrink it until the
    # notice for that count still fits. A truncation that lands mid-code-point
    # is pulled back to a UTF-8 boundary without inventing bytes.
    shown = limit - floor
    while shown + len(notice_for(shown, total)) > limit:
        shown -= 1
    cut = raw[:shown].decode('utf-8', errors='ignore')
    shown = len(cut.encode('utf-8'))
    return cut + notice_for(shown, total), True


def fit_lengths(lengths, budget, *, reserve=0):
    """Return per-piece content allowances summing to at most ``budget``.

    Each piece may show its whole length when the fair share allows it; the
    leftover budget goes to the longer pieces. ``reserve`` is the bytes each
    piece keeps for its own truncation notice, so a caller that cuts a piece
    with ``truncate_with_notice`` (a notice fit inside that piece's own
    allowance) can never exceed ``budget`` as a whole. A budget smaller than
    the total reserve is unsatisfiable.
    """
    if not isinstance(lengths, list) or not all(isinstance(n, int) and n >= 0 for n in lengths):
        raise BudgetError('lengths_must_be_nonnegative_ints')
    if not isinstance(budget, int) or budget < 0:
        raise BudgetError('budget_must_be_nonnegative_int')
    if not isinstance(reserve, int) or reserve < 0:
        raise BudgetError('reserve_must_be_nonnegative_int')
    if reserve * len(lengths) > budget:
        raise BudgetError('budget_less_than_total_reserve')
    content = budget - reserve * len(lengths)
    remaining = [n for n in lengths]
    order = sorted(range(len(lengths)), key=lambda i: lengths[i])
    caps = [0] * len(lengths)
    rest = content
    pending = list(order)
    while pending:
        share = rest // len(pending) if pending else 0
        leftover_round = []
        for i in pending:
            take = min(remaining[i], share)
            caps[i] += take
            rest -= take
            remaining[i] -= take
            if remaining[i] > 0:
                leftover_round.append(i)
        if not leftover_round or rest == 0:
            break
        pending = leftover_round
    return caps
