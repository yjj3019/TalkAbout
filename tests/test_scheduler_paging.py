"""The PostgreSQL scheduler must page through active rooms and isolate one room's capacity failure.

Before: a single read capped at 65 rows plus a global ``scheduler_capacity`` error meant the 65th
active room stopped scheduling for every room, and one owner over 128 principals aborted the
whole tick. These tests drive ``tick_all`` against a fake connection, so they check the paging
and isolation logic, not SQL validity; the real-PostgreSQL suite in CI covers the SQL.
"""
from contextlib import contextmanager

import pytest

from talkabout import domain, store as store_module
from talkabout.domain import Problem
from talkabout.store import MAX_SCHEDULED_ROOMS, SCHEDULER_ROOM_PAGE, PostgresStore


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(self, owner_of, principal_counts):
        self.rooms = sorted(owner_of)
        self.owner_of = owner_of
        self.principal_counts = principal_counts
        self.page_reads = 0

    def execute(self, sql, params=()):
        if "FROM rooms WHERE payload->>'state'='active'" in sql:
            after, limit = params
            self.page_reads += 1
            return Rows([{'id': room} for room in self.rooms if room > after][:limit])
        if 'FOR UPDATE SKIP LOCKED' in sql:
            room = params[0]
            return Rows([{'id': room, 'owner': self.owner_of[room], 'last_event_seq': 0}])
        if 'FROM principals WHERE owner' in sql:
            owner = params[0]
            return Rows([{'id': f'{owner}-{i}', 'payload': {}} for i in range(self.principal_counts.get(owner, 1))])
        return Rows([])


def build(room_count, owners=None, principal_counts=None):
    owner_of = {f'room-{i:05d}': (owners or {}).get(i, 'owner') for i in range(room_count)}
    connection = FakeConnection(owner_of, principal_counts or {})

    class FakeStore(PostgresStore):
        def __init__(self):
            self._ticks = 0
            self.scheduled = []

        @contextmanager
        def connect(self, *args, **kwargs):
            yield connection

        def _room(self, conn, row):
            return {'id': row['id']}

        def _flush(self, conn, room, seq):
            return None

    return FakeStore(), connection


@pytest.fixture
def scheduled(monkeypatch):
    seen = []

    def schedule_call(room, principals):
        seen.append(room['id'])
        return False

    monkeypatch.setattr(domain, 'schedule_call', schedule_call)
    return seen


def test_every_active_room_is_scheduled_across_several_pages(scheduled):
    count = SCHEDULER_ROOM_PAGE * 3 + 7
    store, connection = build(count)
    store.tick_all()
    assert scheduled == sorted(f'room-{i:05d}' for i in range(count))
    assert connection.page_reads == 4, 'three full pages and one short page'


def test_a_full_last_page_is_followed_by_one_empty_read(scheduled):
    store, connection = build(SCHEDULER_ROOM_PAGE * 2)
    store.tick_all()
    assert len(scheduled) == SCHEDULER_ROOM_PAGE * 2 and connection.page_reads == 3


def test_more_rooms_than_the_old_cap_no_longer_stop_the_scheduler(scheduled):
    store, _ = build(65)
    store.tick_all()
    assert len(scheduled) == 65


def test_exceeding_the_total_ceiling_is_an_explicit_error_not_a_silent_cut(scheduled):
    store, _ = build(MAX_SCHEDULED_ROOMS + 1)
    with pytest.raises(Problem) as error:
        store.tick_all()
    assert error.value.code == 'scheduler_capacity' and error.value.status == 429
    assert scheduled == [], 'no room is scheduled from a truncated list'


def test_one_owner_over_the_principal_bound_does_not_block_other_rooms(scheduled):
    store, _ = build(4, owners={1: 'crowded'}, principal_counts={'crowded': 129})
    store.tick_all()
    assert scheduled == ['room-00000', 'room-00002', 'room-00003']
    assert store.skipped_rooms == 1


def test_a_later_clean_tick_clears_the_skipped_count(scheduled):
    store, connection = build(2, owners={0: 'crowded'}, principal_counts={'crowded': 129})
    store.tick_all()
    assert store.skipped_rooms == 1
    connection.principal_counts['crowded'] = 1
    store.tick_all()
    assert store.skipped_rooms == 0


def test_only_capacity_refusals_are_isolated(monkeypatch):
    store, _ = build(3)

    def schedule_call(room, principals):
        if room['id'] == 'room-00001':
            raise Problem('some_bug', 500)
        return False

    monkeypatch.setattr(domain, 'schedule_call', schedule_call)
    with pytest.raises(Problem) as error:
        store.tick_all()
    assert error.value.code == 'some_bug'
