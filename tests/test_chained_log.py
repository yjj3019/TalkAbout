"""Hash-chained append-only records and their verification.

TalkAbout-owned contract; references the *idea* of linking each entry to the
previous one so edits become detectable. Integrity evidence, not proof of origin.
"""
import pytest

from talkabout.chained_log import ChainedLog, chain_hash, extend, first_break


def test_empty_chain_is_intact():
    assert first_break([]) == (True, None)


def test_chain_links_entries():
    entries = []
    entries = extend(entries, event='msg', actor='a', at_ms=1, detail='count:1')
    entries = extend(entries, event='msg', actor='b', at_ms=2, detail='count:1')
    assert entries[1]['prev'] == entries[0]['hash']
    assert entries[0]['prev'] == ''
    assert first_break(entries) == (True, None)


def test_chain_is_deterministic():
    a = chain_hash('', event='e', actor='x', at_ms=5, detail='d')
    b = chain_hash('', event='e', actor='x', at_ms=5, detail='d')
    assert a == b and len(a) == 64


def test_edit_breaks_chain():
    entries = []
    entries = extend(entries, event='e1', actor='a', at_ms=1, detail='1')
    entries = extend(entries, event='e2', actor='b', at_ms=2, detail='2')
    bad = [dict(entries[0], detail='1 edited'), entries[1]]
    assert first_break(bad)[0] is False and first_break(bad)[1] == 0


def test_reorder_breaks_chain():
    entries = []
    entries = extend(entries, event='a', actor='x', at_ms=1, detail='1')
    entries = extend(entries, event='b', actor='x', at_ms=2, detail='2')
    entries = extend(entries, event='c', actor='x', at_ms=3, detail='3')
    scrambled = [entries[1], entries[0], entries[2]]
    assert first_break(scrambled)[0] is False


def test_hash_field_tamper_is_detected():
    entries = []
    entries = extend(entries, event='a', actor='x', at_ms=1, detail='1')
    tampered = [dict(entries[0], hash='0' * 64)]
    assert first_break(tampered)[0] is False


def test_invalid_records_fail_closed():
    with pytest.raises(ValueError):
        chain_hash('', event='', actor='a', at_ms=1, detail='d')
        chain_hash('', event='e', actor='', at_ms=1, detail='d')
        chain_hash('', event='e', actor='a', at_ms=1, detail='x' * 5000)
        chain_hash(None, event='e', actor='a', at_ms=1, detail='d')


def test_sqlite_log_appends_and_verifies():
    log = ChainedLog(':memory:')
    try:
        prev1, _ = log.append(event='e1', actor='a', detail='1', at_ms=1)
        prev2, _ = log.append(event='e2', actor='b', detail='2', at_ms=2)
        assert prev1 == ''
        assert prev2 != prev1 and len(prev2) == 64
        assert log.verify() == (True, None)
    finally:
        log.close()


def test_sqlite_tamper_detected_after_edit(tmp_path):
    path = str(tmp_path / 'chain.db')
    log = ChainedLog(path)
    log.append(event='e1', actor='a', detail='1', at_ms=1)
    log.append(event='e2', actor='b', detail='2', at_ms=2)
    log.close()
    import sqlite3
    db = sqlite3.connect(path)
    try:
        db.execute("UPDATE chained_records SET detail = '2 edited' WHERE seq = 1")
        db.commit()
    finally:
        db.close()
    log = ChainedLog(path)
    try:
        assert log.verify()[0] is False
    finally:
        log.close()
