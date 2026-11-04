"""Expired cursors must never consume the per-principal capacity budget for good.

The 4096 bound is a *live* cursor bound. A cursor expires on its own retention clock
(``DEFAULT_RETENTION.cursor_seconds``), the read path already treats a missing cursor
as the documented 410 ``cursor_gap`` resync, and a principal that only ever kept old
expired rows must therefore be able to save again. These tests pin:

* expired rows do not exhaust the bound (MemoryStore and PostgreSQL parity),
* 4096 live rows remain a hard rejection that removes nothing,
* an over-bound principal whose live rows already fill the bound is rejected without
  purging (the pre-check order the design asks for),
* only the saving principal's expired rows are reclaimed: other principals' rows,
  including their expired ones, and this principal's live rows all survive,
* the expiry -> missing -> resync semantics are unchanged after a reclaim,
* concurrent saves at the bound stay bounded and do not deadlock, including a
  PostgreSQL pool of exactly one connection.

Rows are seeded in bulk by the fixture helpers below; product code never seeds cursors.
"""

import threading
import time

import pytest

from talkabout.domain import Problem
from talkabout.store import MemoryStore, PostgresStore

CAP = 4096


def _payload(principal):
    return {"principal": principal, "scope": "seed", "view": "inbox",
            "offsets": {}, "epochs": {}, "rotation": 0}


def _memory(store):
    return isinstance(store, MemoryStore)


def seed(store, principal, *, live=0, expired=0):
    """Fixture-only bulk seed: live rows and rows already past their retention."""
    now = time.time()
    rows = [(f"seed-{principal}-live-{index}", principal, now + 3600) for index in range(live)]
    rows += [(f"seed-{principal}-expired-{index}", principal, now - 1) for index in range(expired)]
    if _memory(store):
        for key, pid, expires_at in rows:
            store.cursors[key] = {**_payload(pid), "expires_at": expires_at}
        return
    from psycopg.types.json import Jsonb
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO cursors(id,principal_id,payload,expires_at) VALUES(%s,%s,%s,%s)",
            [(key, pid, Jsonb(_payload(pid)), expires_at) for key, pid, expires_at in rows])


def expire(store, key):
    """Fixture-only: move one cursor past its retention window."""
    if _memory(store):
        store.cursors[key]["expires_at"] = 0
        return
    with store.connect() as conn:
        conn.execute("UPDATE cursors SET expires_at=0 WHERE id=%s", (key,))


def rows(store, principal, *, live_only=False):
    now = time.time()
    if _memory(store):
        return sum(1 for item in store.cursors.values()
                   if item["principal"] == principal and (not live_only or item.get("expires_at", 0) > now))
    with store.connect() as conn:
        if live_only:
            return conn.execute("SELECT count(*) AS n FROM cursors WHERE principal_id=%s AND expires_at>%s",
                                (principal, now)).fetchone()["n"]
        return conn.execute("SELECT count(*) AS n FROM cursors WHERE principal_id=%s",
                            (principal,)).fetchone()["n"]


def save(store, principal, key="probe"):
    store.save_cursor(key, _payload(principal))


@pytest.fixture(params=["memory", "postgres"])
def cursor_store(request):
    """Same contract on the in-memory store and the isolated PostgreSQL fixture."""
    if request.param == "memory":
        yield MemoryStore()
        return
    yield request.getfixturevalue("postgres_fixture")[0]


def test_expired_rows_do_not_exhaust_capacity(cursor_store):
    seed(cursor_store, "a", expired=CAP)
    assert rows(cursor_store, "a") == CAP
    assert rows(cursor_store, "a", live_only=True) == 0
    save(cursor_store, "a")
    assert rows(cursor_store, "a") == 1
    assert rows(cursor_store, "a", live_only=True) == 1


def test_live_rows_keep_the_hard_cap_and_remove_nothing(cursor_store):
    seed(cursor_store, "a", live=CAP)
    with pytest.raises(Problem, match="cursor_capacity_archival_unsupported"):
        save(cursor_store, "a")
    assert rows(cursor_store, "a") == CAP
    assert rows(cursor_store, "a", live_only=True) == CAP


def test_over_cap_with_full_live_rows_is_rejected_without_purge(cursor_store):
    # Live rows already fill the bound, so reclaiming the expired rows could not admit
    # the save: the live count is checked first and nothing is deleted.
    seed(cursor_store, "a", live=CAP + 800, expired=100)
    with pytest.raises(Problem, match="cursor_capacity_archival_unsupported"):
        save(cursor_store, "a")
    assert rows(cursor_store, "a") == CAP + 900


def test_only_this_principal_expired_rows_are_reclaimed(cursor_store):
    seed(cursor_store, "a", live=96, expired=CAP - 96)
    seed(cursor_store, "b", live=10)
    seed(cursor_store, "c", expired=5)
    save(cursor_store, "a")
    assert rows(cursor_store, "a") == 97
    assert rows(cursor_store, "a", live_only=True) == 97
    assert rows(cursor_store, "b") == 10
    # Another principal's expired rows are not this save's to reclaim.
    assert rows(cursor_store, "c") == 5


def test_expired_cursor_still_reads_as_missing_after_reclaim(cursor_store):
    save(cursor_store, "a", "live")
    save(cursor_store, "a", "doomed")
    expire(cursor_store, "doomed")
    # This is the read path behind the documented 410 cursor_gap resync.
    assert cursor_store.cursor("doomed") is None
    assert cursor_store.cursor("live")["principal"] == "a"
    seed(cursor_store, "a", expired=CAP - 2)
    save(cursor_store, "a", "after")
    assert cursor_store.cursor("after")["principal"] == "a"
    assert cursor_store.cursor("doomed") is None
    assert cursor_store.cursor("live")["principal"] == "a"


def test_concurrent_saves_at_the_bound_stay_bounded(cursor_store):
    seed(cursor_store, "a", live=CAP - 16, expired=16)
    saved, rejected = [], []

    def worker(index):
        try:
            save(cursor_store, "a", f"probe-{index}")
            saved.append(index)
        except Problem as error:
            rejected.append(error.code)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert not any(thread.is_alive() for thread in threads)
    assert len(saved) == 16 and not rejected
    assert rows(cursor_store, "a", live_only=True) == CAP
    assert rows(cursor_store, "a") == CAP
    with pytest.raises(Problem, match="cursor_capacity_archival_unsupported"):
        save(cursor_store, "a", "overflow")
    assert rows(cursor_store, "a", live_only=True) == CAP


def test_core_events_keeps_gap_semantics_after_reclaim():
    """End-to-end: a reclaimed cursor is still an expired cursor, so 410 stays 410."""
    from conftest import build_fixture

    store, core, _ = build_fixture(MemoryStore())
    stale = core.events("fixture-a")["next_cursor"]
    expire(store, stale)
    seed(store, "a", expired=CAP - 1)
    assert rows(store, "a") == CAP
    core.events("fixture-a")  # bound reached with only expired rows: reclaim, then save
    with pytest.raises(Problem, match="cursor_gap"):
        core.events("fixture-a", stale)


def test_postgres_pool_of_one_reclaims_without_deadlock(postgres_fixture, monkeypatch):
    """A single pooled connection must be enough for the reclaim path."""
    store, _, _ = postgres_fixture
    monkeypatch.setenv("TALKABOUT_DB_POOL_SIZE", "1")
    single = PostgresStore(store.dsn, store.schema)
    try:
        assert single._connection_pool().stats()["max_size"] == 1
        seed(single, "a", expired=CAP)
        save(single, "a")
        assert rows(single, "a") == 1
        seed(single, "b", live=CAP)
        with pytest.raises(Problem, match="cursor_capacity_archival_unsupported"):
            save(single, "b")
        assert rows(single, "b") == CAP
    finally:
        single.close()
