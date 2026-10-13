"""Explicit development capacities; paging/archival are not implemented."""
import json
from .domain import require


def room_bounds(room):
    require(len(room.get("events", [])) <= 2048, "event_capacity", 429)
    require(len(json.dumps(room, ensure_ascii=False).encode("utf-8")) <= 2097152, "room_byte_capacity", 413)


def snapshot_bounds(rooms, principals):
    require(len(rooms) <= 64, "room_list_capacity_paging_unsupported", 429)
    require(len(principals) <= 128, "principal_list_capacity_paging_unsupported", 429)
    for room in rooms.values():
        room_bounds(room)
