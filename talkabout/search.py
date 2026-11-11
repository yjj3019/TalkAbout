"""Room-message search with viewer visibility: literal terms, newest first.

TalkAbout-owned design. The *idea* referenced is a search whose matches are
limited to what the viewer can already read, never marks anything as seen, changes
no state and validates every input with closed bounds; no external code was copied
(docs/CLEAN_ROOM_IMPLEMENTATION.md). A match is a *projection of visible text*, not
evidence of delivery, comprehension or provider activity.
"""

import re

MAX_QUERY_BYTES = 4096
DEFAULT_LIMIT = 20
LIMIT_RANGE = (1, 50)

_FIELD_SET = frozenset(('seq', 'id', 'actor_id', 'actor_kind', 'text'))


class SearchError(ValueError):
    """Raised for malformed search input (fail-closed, no clamping)."""


def _words(query):
    # Python's \\w covers Unicode letters and digits, so Korean and other scripts
    # split on punctuation; punctuation-only queries yield no words -> no results.
    return [w.lower() for w in re.findall(r'\w+', query)]


def _project(message):
    return {field: message[field] for field in _FIELD_SET}


def room_search(messages, *, viewer_floor, query, limit=DEFAULT_LIMIT):
    """Return visible matches for ``query``, newest first (pure, no mutation).

    ``messages`` is an iterable of message dicts with ``seq``/``text``; only
    ``seq >= viewer_floor`` is visible. Every word (Unicode letters/digits) must
    match, case-insensitively. ``limit`` is 1..50. Results project only
    ``seq/message_id/actor_id/actor_kind/text`` and never mark anything seen.
    """
    if not isinstance(query, str):
        raise SearchError('query_not_str')
    if not query:
        raise SearchError('query_empty')
    if len(query.encode('utf-8')) > MAX_QUERY_BYTES:
        raise SearchError('query_too_long')
    if not isinstance(limit, int) or not (LIMIT_RANGE[0] <= limit <= LIMIT_RANGE[1]):
        raise SearchError('invalid_limit')
    words = _words(query)
    if not words:
        return {'results': []}
    matched = []
    for message in messages:
        if not isinstance(message, dict) or message.get('seq', -1) < viewer_floor:
            continue
        text = message.get('text')
        if not isinstance(text, str):
            continue
        lowered = text.lower()
        if all(word in lowered for word in words):
            matched.append(_project(message))
    matched.sort(key=lambda m: m['seq'], reverse=True)
    return {'results': matched[:limit]}
