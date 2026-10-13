"""Development retention policy; cleanup is explicitly synthetic-fixture only."""
from dataclasses import dataclass
import time


@dataclass(frozen=True)
class RetentionPolicy:
    response_seconds: int = 3600
    cursor_seconds: int = 3600
    event_seconds: int = 86400

    def __post_init__(self):
        for value in (self.response_seconds, self.cursor_seconds, self.event_seconds):
            if type(value) is not int or not 60 <= value <= 604800:
                raise ValueError("retention_seconds_must_be_60_to_604800")


DEFAULT_RETENTION = RetentionPolicy()


def retire_responses(records, now):
    count = 0
    for record in records.values():
        # Legacy records have unknown age: retain until explicitly migrated.
        if record.get("response_until", float("inf")) <= now and "response" in record:
            del record["response"]
            count += 1
    return count


def compact_room_fixture(room, *, synthetic_fixture=False, now=None, policy=DEFAULT_RETENTION):
    if synthetic_fixture is not True:
        raise ValueError("synthetic_fixture_required")
    now = time.time() if now is None else now
    expired = 0
    for event in room["events"]:
        if event.get("created_at", float("inf")) > now - policy.event_seconds:
            break
        expired += 1
    if expired:
        room["first_retained_seq"] = room["events"][expired - 1]["seq"] + 1
        del room["events"][:expired]
    return {"events": expired, "response_payloads": retire_responses(room["keys"], now)}
