"""Explicit development capacities; paging/archival are not implemented."""
import json
from .domain import (EVENT_CAPACITY, KEY_CAPACITY, PLAN_DISPATCH_BYTE_RESERVE,
                     PLAN_DISPATCH_KEY_RESERVE, PLAN_DISPATCH_STATE_BYTE_SLACK, ROOM_BYTE_CAPACITY,
                     require, room_byte_size, valid_plan_reservation_budget)


_MEMBERSHIP_HWM_LIMIT = 128
_MEMBERSHIP_ID_MAX = 2 ** 31 - 1


def _membership_history_bounds(room):
    if '_membership_hwm' not in room:
        return
    history = room['_membership_hwm']
    # Explicit null is corrupt, not legacy; only the absent field is compatible.
    require(isinstance(history, dict) and len(history) <= _MEMBERSHIP_HWM_LIMIT,
            "membership_hwm_bounds", 422)
    for principal_id, entry in history.items():
        require(isinstance(principal_id, str) and isinstance(entry, dict)
                and set(entry) == {"epoch", "revision"}, "membership_hwm_bounds", 422)
        for value in entry.values():
            require(isinstance(value, int) and not isinstance(value, bool)
                    and 0 < value <= _MEMBERSHIP_ID_MAX, "membership_hwm_bounds", 422)


_PLAN_RUN_FIELDS = {"id", "plan_revision", "generation", "epoch", "state", "started_at",
                    "finished_at", "abort_reason", "nodes"}
_PLAN_NODE_FIELDS = {"id", "title", "instruction", "assignee", "write_scope", "depends_on",
                     "state", "task_id", "failure"}
_PLAN_RUN_STATES = {"running", "completed", "partial", "aborted"}
_PLAN_NODE_STATES = {"pending", "queued", "completed", "failed", "blocked"}
_PLAN_FAILURES = {"expired", "cancelled", "skipped", "task_uncertain", "assignee_unavailable",
                  "generation_changed", "room_not_active", "room_closed",
                  "dependency_failed", "input_unavailable", "aborted"}


def _int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _plan_dispatch_bounds(room):
    if '_plan_dispatch' not in room:
        return
    run = room['_plan_dispatch']
    require(isinstance(run, dict) and set(run) in (
        _PLAN_RUN_FIELDS, _PLAN_RUN_FIELDS | {"reservation_cap", "reservations_granted"}),
        "plan_dispatch_bounds", 422)
    if "reservation_cap" in run:
        require(valid_plan_reservation_budget(run), "plan_dispatch_bounds", 422)
    require(isinstance(run['id'], str) and 1 <= len(run['id']) <= 64, "plan_dispatch_bounds", 422)
    require(_int(run['plan_revision']) and _int(run['generation']) and _int(run['epoch']),
            "plan_dispatch_bounds", 422)
    require(run['state'] in _PLAN_RUN_STATES, "plan_dispatch_bounds", 422)
    require(isinstance(run['started_at'], (int, float)) and not isinstance(run['started_at'], bool)
            and (run['finished_at'] is None or (isinstance(run['finished_at'], (int, float))
                                                and not isinstance(run['finished_at'], bool))),
            "plan_dispatch_bounds", 422)
    require(run['abort_reason'] is None or (isinstance(run['abort_reason'], str)
                                            and 1 <= len(run['abort_reason']) <= 64),
            "plan_dispatch_bounds", 422)
    nodes = run['nodes']
    require(isinstance(nodes, list) and len(nodes) <= 16, "plan_dispatch_bounds", 422)
    for node in nodes:
        require(isinstance(node, dict) and set(node) in (_PLAN_NODE_FIELDS, _PLAN_NODE_FIELDS | {'required_predecessors'}), "plan_dispatch_bounds", 422)
        require(isinstance(node['id'], str) and 1 <= len(node['id']) <= 64, "plan_dispatch_bounds", 422)
        require(isinstance(node['title'], str) and 1 <= len(node['title']) <= 200, "plan_dispatch_bounds", 422)
        require(isinstance(node['instruction'], str) and 1 <= len(node['instruction']) <= 2000,
                "plan_dispatch_bounds", 422)
        require(isinstance(node['assignee'], str) and 1 <= len(node['assignee']) <= 128,
                "plan_dispatch_bounds", 422)
        require(node['write_scope'] in {"analysis", "owner_approved_workspace"},
                "plan_dispatch_bounds", 422)
        require(isinstance(node['depends_on'], list) and len(node['depends_on']) <= 15
                and all(isinstance(dep, str) and 1 <= len(dep) <= 64 for dep in node['depends_on']),
                "plan_dispatch_bounds", 422)
        if 'required_predecessors' in node:
            required = node['required_predecessors']
            require(isinstance(required, list) and len(required) <= 15
                    and all(isinstance(dep, str) and dep in node['depends_on'] for dep in required)
                    and len(required) == len(set(required)), "plan_dispatch_bounds", 422)
        require(node['state'] in _PLAN_NODE_STATES, "plan_dispatch_bounds", 422)
        require(node['task_id'] is None or (isinstance(node['task_id'], str)
                                            and 1 <= len(node['task_id']) <= 64),
                "plan_dispatch_bounds", 422)
        require(node['failure'] is None or node['failure'] in _PLAN_FAILURES,
                "plan_dispatch_bounds", 422)


def room_bounds(room):
    _membership_history_bounds(room)
    _plan_dispatch_bounds(room)
    running = (room.get("_plan_dispatch") or {}).get("state") == "running"
    if running:
        require(len(room.get("keys", {})) <= KEY_CAPACITY - PLAN_DISPATCH_KEY_RESERVE,
                "idempotency_capacity", 429)
        require(len(room.get("events", [])) <= EVENT_CAPACITY, "event_capacity", 429)
        require(room_byte_size(room) <= ROOM_BYTE_CAPACITY - PLAN_DISPATCH_BYTE_RESERVE
                - PLAN_DISPATCH_STATE_BYTE_SLACK, "room_byte_capacity", 413)
        return
    require(len(room.get("keys", {})) <= KEY_CAPACITY, "idempotency_capacity", 429)
    require(len(room.get("events", [])) <= EVENT_CAPACITY, "event_capacity", 429)
    require(room_byte_size(room) <= ROOM_BYTE_CAPACITY, "room_byte_capacity", 413)


def snapshot_bounds(rooms, principals, *, deep=True):
    require(len(rooms) <= 64, "room_list_capacity_paging_unsupported", 429)
    require(len(principals) <= 128, "principal_list_capacity_paging_unsupported", 429)
    if not deep:
        # Read-only projections: every room was validated by _flush when written, so
        # re-serialising the whole room on each poll only burns CPU (71% of the
        # measured rooms() cost at 1.32 MB). Capacity counts stay checked.
        for room in rooms.values():
            require(len(room.get("events", [])) <= 2048, "event_capacity", 429)
        return
    for room in rooms.values():
        room_bounds(room)
