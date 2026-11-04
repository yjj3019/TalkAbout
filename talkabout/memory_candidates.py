"""Pure candidate selection for the manual per-room memory preview.

This module is the unwired logic behind a preview the *human* starts from the room
UI: it takes the room messages the caller has already filtered for read access,
groups exact duplicates, and returns bounded, newest-first candidates whose text is
the original message text (or an exact prefix of it). No summary, rewrite, model
call, network call, database write or application state is produced or changed.

Boundaries
- Preview only. Nothing here persists, approves, executes, schedules or applies a
  memory. The caller - a human-initiated preview path - decides what to do next. The
  module never decides whether a message is a command, an approval or "safe"; it has
  no classification, confidence or policy field at all.
- Original text only. Every returned ``original_text`` is either the verbatim source
  text object or ``text[:max_excerpt_chars]`` (an exact prefix). Nothing is trimmed,
  normalised or re-joined on output; grouping uses the original text. The output
  therefore *contains the original room text* and must be treated as sensitive
  display data: the caller decides who may request the preview.
- Access is the caller's job. The input must already be filtered by the caller's
  room/file/permission checks. This module does not check ACLs, principals, rooms or
  membership, and a candidate here never means "readable", "visible" or "allowed".
- Exact duplicate grouping only. Only code-point-identical Python text is grouped;
  whitespace, indentation, Unicode variants and case remain distinct. Every group
  exposes original source ids/seqs/actors, capped by ``max_sources``. Normalization
  is used only to reject blank or too-short input.
- No secret or personal-data detection. This module does not inspect, detect, redact,
  classify or guarantee anything about credentials, secrets or personal information,
  and it makes no security or privacy claim. Text is returned as given, including
  anything that looks like a credential or an instruction.
- Deterministic and bounded. Ordering is newest seq first, then message id, and does
  not depend on input order. ``limit``, ``max_chars``, ``max_excerpt_chars`` and
  ``max_sources`` bound the result; omitted groups/messages and the limits that were
  hit are reported. Group sources come only from input messages, so the total number
  of returned source entries never exceeds the number of usable input messages.
- No input copying or mutation. The caller's list/dict and message mappings are read
  in place: they are not copied, reordered, extended or modified. Only new output
  dictionaries are created.

Output shape (see ``memory_candidates``)::

    {"schema": "talkabout.memory_candidates/1", "mode": "preview_only",
     "limits": {...}, "counts": {...}, "limits_hit": [...], "truncated": bool,
     "excluded": {...},
     "candidates": [{"original_text": str, "truncated": bool,
                     "source": {"message_id", "seq", "actor_id", "actor_kind"},
                     "repetition": {"count", "distinct_texts", "sources_truncated",
                                    "sources": [...]}}]}
"""
import unicodedata
from collections.abc import Mapping

SCHEMA = 'talkabout.memory_candidates/1'
MODE = 'preview_only'

DEFAULT_LIMIT = 50
DEFAULT_MAX_CHARS = 8000
DEFAULT_MAX_EXCERPT_CHARS = 280
DEFAULT_MAX_SOURCES = 20
DEFAULT_MIN_CHARS = 1

# Fixed exclusion vocabulary. Counts are always present so a silent skip is visible.
EXCLUDED_KEYS = ('not_object', 'missing_text', 'blank_text', 'too_short',
                 'missing_id', 'missing_seq')
LIMIT_KEYS = ('limit', 'max_chars', 'max_excerpt_chars', 'max_sources')


def normalize_text(text):
    """Normalize text for blank and minimum-length checks, never for grouping."""
    return ' '.join(unicodedata.normalize('NFC', text).split())


def _optional_str(value):
    return value if isinstance(value, str) and value else None


def _int_param(name, value, minimum):
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return value


def _iter_messages(messages):
    """Iterate the caller's messages in place: no list/deep copy is made here."""
    if isinstance(messages, Mapping):
        # Room storage keeps messages as {id: message}; reading values is enough.
        return iter(messages.values())
    if isinstance(messages, (str, bytes, bytearray)):
        raise TypeError('messages must be an iterable of message mappings, not a string')
    return iter(messages)


def _source(entry):
    return {'message_id': entry['message_id'], 'seq': entry['seq'],
            'actor_id': entry['actor_id'], 'actor_kind': entry['actor_kind']}


def memory_candidates(messages, *, limit=DEFAULT_LIMIT, max_chars=DEFAULT_MAX_CHARS,
                      max_excerpt_chars=DEFAULT_MAX_EXCERPT_CHARS,
                      max_sources=DEFAULT_MAX_SOURCES, min_chars=DEFAULT_MIN_CHARS):
    """Return the bounded, newest-first preview candidates for already-filtered messages.

    ``messages`` is an iterable of message mappings (or a ``{id: message}`` mapping)
    that the caller has already filtered for read access. Only ``id``, ``seq``,
    ``text``, ``actor_id`` and ``actor_kind`` are read; every other field is ignored
    and nothing is written back or copied.

    The result is a detached dict. Candidates are ordered newest seq first (ties by
    message id), exact duplicate groups count once, and ``counts.omitted_messages`` /
    ``counts.omitted_groups`` report what ``limit``/``max_chars`` removed.
    ``repetition.sources`` lists at most ``max_sources`` members and
    ``repetition.sources_truncated`` says whether the group had more.
    Invalid input entries are counted under ``excluded`` instead of raising. Invalid
    limit values raise ``ValueError``.

    ``original_text`` is the original message text (or an exact prefix); it can
    contain anything the room contains. This function makes no claim to detect or
    remove secrets or personal data.

    The function is pure: no I/O, no clock, no randomness, no mutation.
    """
    limit = _int_param('limit', limit, 1)
    max_chars = _int_param('max_chars', max_chars, 1)
    max_excerpt_chars = _int_param('max_excerpt_chars', max_excerpt_chars, 1)
    max_sources = _int_param('max_sources', max_sources, 1)
    min_chars = _int_param('min_chars', min_chars, 0)

    excluded = {key: 0 for key in EXCLUDED_KEYS}
    groups = {}
    usable = 0
    input_messages = 0
    for item in _iter_messages(messages):
        input_messages += 1
        if not isinstance(item, Mapping):
            excluded['not_object'] += 1
            continue
        text = item.get('text')
        if not isinstance(text, str):
            excluded['missing_text'] += 1
            continue
        key = normalize_text(text)
        if not key:
            excluded['blank_text'] += 1
            continue
        if len(key) < min_chars:
            excluded['too_short'] += 1
            continue
        message_id = item.get('id')
        if not isinstance(message_id, str) or not message_id:
            excluded['missing_id'] += 1
            continue
        seq = item.get('seq')
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            excluded['missing_seq'] += 1
            continue
        groups.setdefault(text, []).append({
            'message_id': message_id,
            'seq': seq,
            'actor_id': _optional_str(item.get('actor_id')),
            'actor_kind': _optional_str(item.get('actor_kind')),
            'text': text,
        })
        usable += 1

    for entries in groups.values():
        entries.sort(key=lambda entry: (-entry['seq'], entry['message_id']))
    ordered = sorted(groups.values(), key=lambda entries: (-entries[0]['seq'], entries[0]['message_id']))

    candidates = []
    limits_hit = []
    excerpt_characters = 0
    omitted_groups = 0
    omitted_messages = 0
    for position, entries in enumerate(ordered):
        if len(candidates) >= limit:
            limits_hit.append('limit')
            omitted_groups = len(ordered) - position
            omitted_messages = sum(len(rest) for rest in ordered[position:])
            break
        newest = entries[0]
        excerpt = newest['text']
        cut = False
        if len(excerpt) > max_excerpt_chars:
            excerpt = excerpt[:max_excerpt_chars]
            cut = True
            limits_hit.append('max_excerpt_chars')
        if excerpt_characters + len(excerpt) > max_chars:
            limits_hit.append('max_chars')
            omitted_groups = len(ordered) - position
            omitted_messages = sum(len(rest) for rest in ordered[position:])
            break
        excerpt_characters += len(excerpt)
        sources_truncated = len(entries) > max_sources
        if sources_truncated:
            limits_hit.append('max_sources')
        candidates.append({
            'original_text': excerpt,
            'truncated': cut,
            'source': _source(newest),
            'repetition': {
                'count': len(entries),
                'distinct_texts': len({entry['text'] for entry in entries}),
                'sources_truncated': sources_truncated,
                'sources': [_source(entry) for entry in entries[:max_sources]],
            },
        })

    limits_hit = list(dict.fromkeys(limits_hit))
    return {
        'schema': SCHEMA,
        'mode': MODE,
        'limits': {'limit': limit, 'max_chars': max_chars,
                   'max_excerpt_chars': max_excerpt_chars, 'max_sources': max_sources,
                   'min_chars': min_chars},
        'counts': {
            'input_messages': input_messages,
            'usable_messages': usable,
            'groups': len(ordered),
            'returned': len(candidates),
            'omitted_groups': omitted_groups,
            'omitted_messages': omitted_messages,
            'excerpt_characters': excerpt_characters,
        },
        'limits_hit': limits_hit,
        'truncated': bool(limits_hit),
        'excluded': excluded,
        'candidates': candidates,
    }
