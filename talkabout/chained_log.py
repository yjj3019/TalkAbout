"""Hash-chained append-only record with tamper detection.

TalkAbout-owned design. The *idea* referenced is a log whose every entry links to
the previous one with a digest so edits become detectable by re-walking the
chain; no external code was copied (docs/CLEAN_ROOM_IMPLEMENTATION.md).

This is integrity *evidence*, not encryption: whoever can rewrite the store can
rewrite entries and re-chain them. It does not prove origin, authorization or
that an event's claimed actor really did anything. An intact chain means "the
stored sequence is self-consistent", never "the event happened".
"""

import hashlib
import json
import sqlite3

_MAX_EVENT_BYTES = 128
_MAX_ACTOR_BYTES = 128
_MAX_DETAIL_BYTES = 4096


def chain_hash(prev, *, event, actor, at_ms, detail):
    """Return the SHA-256 digest of one record linked to ``prev`` ('' for the first)."""
    if not isinstance(prev, str):
        raise ValueError('prev_not_str')
    if not isinstance(event, str) or not event or len(event.encode('utf-8')) > _MAX_EVENT_BYTES:
        raise ValueError('invalid_event')
    if not isinstance(actor, str) or not actor or len(actor.encode('utf-8')) > _MAX_ACTOR_BYTES:
        raise ValueError('invalid_actor')
    if not isinstance(detail, str) or len(detail.encode('utf-8')) > _MAX_DETAIL_BYTES:
        raise ValueError('invalid_detail')
    if not isinstance(at_ms, int):
        raise ValueError('at_ms_not_int')
    canonical = json.dumps({'prev': prev, 'event': event, 'actor': actor,
                           'at_ms': at_ms, 'detail': detail},
                          sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def extend(entries, *, event, actor, at_ms, detail):
    """Append one linked record dict to ``entries``; returns a new list."""
    prev = entries[-1]['hash'] if entries else ''
    digest = chain_hash(prev, event=event, actor=actor, at_ms=at_ms, detail=detail)
    return list(entries) + [{'prev': prev, 'hash': digest,
                            'event': event, 'actor': actor,
                            'at_ms': at_ms, 'detail': detail}]


def first_break(entries):
    """Return ``(ok, index)`` — ``(True, None)`` when the chain is intact.

    The first index whose ``prev`` differs from its predecessor's ``hash``, or
    whose hash does not recompute, is the break. Tampering is *detected*, never
    silently accepted. An empty list is intact.
    """
    if not isinstance(entries, list):
        raise ValueError('entries_not_list')
    expect = ''
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            return False, index
        if entry.get('prev') != expect:
            return False, index
        expected = chain_hash(expect, event=entry.get('event', object()),
                             actor=entry.get('actor', object()),
                             at_ms=entry.get('at_ms', None),
                             detail=entry.get('detail', object()))
        if entry.get('hash') != expected:
            return False, index
        expect = entry.get('hash')
    return True, None


class ChainedLog:
    """SQLite-backed chain with ``append`` and ``verify`` (standard-library sqlite3)."""

    def __init__(self, path=':memory:'):
        self._db = sqlite3.connect(path)
        self._db.execute(
            'CREATE TABLE IF NOT EXISTS chained_records ('
            ' seq INTEGER PRIMARY KEY AUTOINCREMENT,'
            ' at_ms INTEGER NOT NULL, event TEXT NOT NULL,'
            ' actor TEXT NOT NULL, detail TEXT NOT NULL,'
            ' prev TEXT NOT NULL, digest TEXT NOT NULL)')

    def append(self, *, event, actor, detail, at_ms):
        rows = self._db.execute(
            'SELECT digest FROM chained_records ORDER BY seq DESC LIMIT 1').fetchall()
        prev = rows[0][0] if rows else ''
        digest = chain_hash(prev, event=event, actor=actor, at_ms=at_ms, detail=detail)
        self._db.execute(
            'INSERT INTO chained_records (at_ms, event, actor, detail, prev, digest)'
            ' VALUES (?, ?, ?, ?, ?, ?)', (at_ms, event, actor, detail, prev, digest))
        self._db.commit()
        return prev, digest

    def verify(self):
        """``(ok, first_broken_seq)``; ``(True, None)`` when intact or empty."""
        rows = self._db.execute(
            'SELECT seq, at_ms, event, actor, detail, prev, digest'
            ' FROM chained_records ORDER BY seq').fetchall()
        expect = ''
        for seq, at_ms, event, actor, detail, prev, digest in rows:
            if prev != expect:
                return False, seq
            if chain_hash(prev, event=event, actor=actor, at_ms=at_ms, detail=detail) != digest:
                return False, seq
            expect = digest
        return True, None

    def close(self):
        self._db.close()
