"""Submission-cue classification: is this reply a result or a "still working" note?

TalkAbout-owned design. The *idea* referenced is that a reply which only
acknowledges work ("working on it, details will follow") should not be treated
as the final result of a claimed turn, and that a fixed notice must stay inside a
byte budget; no external code was copied (docs/CLEAN_ROOM_IMPLEMENTATION.md).

Classification is a *cue on the caller-provided text only*. It is not a
verification that work happened, not a delivery guarantee and not provider
evidence. The caller decides what a 'proceeding' note is shown to; failing to
find a result cue keeps the note a note, it never promotes it.
"""

from .text_budget import truncate_with_notice

# A note is a note only when it is short and reads like "started, result later".
MAX_CUE_BYTES = 280
MAX_NOTE_BYTES = 1024
_MAX_NEWLINES = 2

# Words and phrases that already carry a result: a note that contains any of
# these is a final result even when it also promises more.
RESULT_MARKERS = (
    'done', 'finished', 'attached', 'complete', 'here is', 'here\'s',
    'http://', 'https://', '```',
    'called', 'sent', 'submitted', 'paid', 'booked', 'posted', 'saved',
    '완료', '보냈', '제출', '첨부', '예약했', '완성',
)

# How a note opens: "working on it", "checking this", "진행 중", ...
NOTE_OPENERS = (
    'working on', 'checking', 'looking into', 'starting', 'on it', 'stand by',
    '진행 중', '확인 중', '조사 중', '시작',
)

# Quick lead words that may precede an opener: "got it, checking".
NOTE_LEADS = ('ok', 'okay', 'got it', 'sure', 'ack', 'roger')

# "the report will follow" style promises that unambiguously defer the result.
PROMISE_HEADS = ('report', 'results', 'result', 'answer', 'findings', 'details',
                'summary', 'reply', 'analysis')
_PROMISE_TAILS = (' will follow', ' to follow', ' coming')


def _words(text):
    return text.lower()


def _contains_marker(text):
    lowered = text.lower()
    for marker in RESULT_MARKERS:
        if marker in lowered:
            return True
    return False


def _prefix_phrase(text, phrase):
    if not text.startswith(phrase):
        return False
    rest = text[len(phrase):]
    return rest == '' or not (rest[0].isalnum())


def _opens_with(text):
    s = text.lower().strip(' ,.!:;-')
    stripped = s
    for lead in NOTE_LEADS:
        if _prefix_phrase(s, lead):
            rest = s[len(lead):].strip(' ,.!:;-')
            if rest and any(_prefix_phrase(rest, opener) for opener in NOTE_OPENERS):
                return True
    return any(_prefix_phrase(s, opener) for opener in NOTE_OPENERS)


def _promises_later(text):
    lowered = ' '.join(_words(text).split())
    return any(head + tail in lowered for head in PROMISE_HEADS for tail in _PROMISE_TAILS)


def classify_reply(text, *, explicit_final=False):
    """Classify caller text as ``'proceeding'`` or ``'final'``.

    ``explicit_final`` always wins: the caller may mark a short note as the whole
    result. A blank body, an oversized body or a body with many line breaks is
    never a proceeding note. Any other body with a result marker is final. Only a
    short body that opens with a note cue or promises the result later and shows
    no marker is ``'proceeding'``.
    """
    if not isinstance(text, str):
        raise ValueError('text_not_str')
    if explicit_final:
        return 'final'
    raw = text.encode('utf-8')
    if raw == b'' or len(raw) > MAX_CUE_BYTES or text.count('\n') > _MAX_NEWLINES:
        return 'final'
    if _contains_marker(text):
        return 'final'
    if _opens_with(text) or _promises_later(text):
        return 'proceeding'
    return 'final'


def proceeding_note(text, limit=MAX_NOTE_BYTES):
    """Bound a proceeding note to ``limit`` bytes with a fixed truncation notice."""
    if not isinstance(limit, int) or limit < 0:
        raise ValueError('limit_not_nonnegative_int')
    return truncate_with_notice(text, limit)
