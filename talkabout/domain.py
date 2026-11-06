from __future__ import annotations

import copy
import hashlib
import json
import re
import time
import unicodedata
import uuid
from .retention import DEFAULT_RETENTION
from .modes import next_turn, valid_turn_request

AGENT_PERMISSIONS = {
    "room:read", "room:speak", "room:contribute", "task:claim",
    "task:heartbeat", "task:complete", "task:skip",
}
HUMAN_PERMISSIONS = {
    "room:read", "room:question", "room:advice", "room:manage_members",
    "room:control", "room:approve", "room:adopt", "room:contribute",
}
SELF_PERMISSIONS = {"agent:self_read", "agent:capabilities_write", "agent:name_propose"}


def participant_permissions(mode):
    return AGENT_PERMISSIONS if mode == 'co_creation' else AGENT_PERMISSIONS - {'room:contribute'}


class Problem(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


class CommittedProblem(Problem):
    """A rejection whose already-recorded state must survive the response.

    Raising a plain Problem rolls the transaction back. This subclass tells the
    room transaction to commit the mutation first (for example, marking a plan
    node failed before refusing a claim) and only then surface the error.
    """


def require(condition, code, status=409):
    if not condition:
        raise Problem(code, status)


def ident():
    return str(uuid.uuid4())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_name(display_name, handle):
    require(isinstance(display_name, str) and isinstance(handle, str), "invalid_name", 422)
    name = unicodedata.normalize("NFKC", display_name).strip()
    require(1 <= len(name) <= 48 and not any(unicodedata.category(c).startswith("C") for c in name), "invalid_name", 422)
    require(name.casefold() not in {"admin", "administrator", "human", "system", "관리자"}, "reserved_name", 422)
    require(bool(re.fullmatch(r"[a-z][a-z0-9-]{0,31}", handle)), "invalid_handle", 422)
    require(handle not in {"admin", "administrator", "human", "system"}, "reserved_name", 422)
    return name, name.casefold(), handle


def authorize(actor, room, permission, human=False):
    member = room["members"].get(actor["id"])
    require(member and member["status"] == "active" and "room:read" in member["permissions"], "not_found", 404)
    require(not actor.get("room_id") or actor["room_id"] == room["id"], "not_found", 404)
    require(permission in actor["scopes"] and permission in member["permissions"], "forbidden", 403)
    require(not human or actor["kind"] == "human", "human_required", 403)
    return member


EVENT_CAPACITY = 2048
KEY_CAPACITY = 2048
ROOM_BYTE_CAPACITY = 2097152
# While a plan run is running, ordinary writes may not consume the capacity that the
# abort path needs. Running bounds keep: keys <= 2040, events <= 2048,
# bytes <= 2 MiB - 64 KiB - 4 KiB (the 4 KiB state slack lets state-first transitions
# that omit their audit event still flush under the running cap).
# Dispatch must start stricter: its own transaction adds one replay key+response, one
# dispatch_started event + <=16 root task.queued events, and up to 16 nodes x 2000-char
# instructions x 4 UTF-8 bytes/char duplicated in run.nodes and task['_plan'] =
# 256,000 bytes plus JSON overhead (384 KiB start slack).
PLAN_DISPATCH_KEY_RESERVE = 8
PLAN_DISPATCH_BYTE_RESERVE = 65536
PLAN_DISPATCH_STATE_BYTE_SLACK = 4096
PLAN_DISPATCH_START_EVENT_SLACK = 17
PLAN_DISPATCH_START_BYTE_SLACK = 384 * 1024


def room_byte_size(room):
    return len(json.dumps(room, ensure_ascii=False).encode("utf-8"))


def emit(room, kind, **payload):
    require(len(room["events"]) < EVENT_CAPACITY, "event_capacity", 429)
    room["last_seq"] += 1
    event = {"event_id": ident(), "room_id": room["id"], "seq": room["last_seq"], "type": kind, "created_at": time.time(), **payload}
    room["events"].append(event)
    return event


def room_byte_limit(room):
    """Byte ceiling used by state-first plan emits (reserves abort/state space)."""
    if (room.get("_plan_dispatch") or {}).get("state") == "running":
        return ROOM_BYTE_CAPACITY - PLAN_DISPATCH_BYTE_RESERVE - PLAN_DISPATCH_STATE_BYTE_SLACK
    return ROOM_BYTE_CAPACITY - PLAN_DISPATCH_STATE_BYTE_SLACK


def plan_can_emit(room):
    """True when a plan audit event fits without pushing the room over its byte cap."""
    if len(room["events"]) >= EVENT_CAPACITY:
        return False
    return room_byte_size(room) + 2048 <= room_byte_limit(room)


def plan_state_emit(room, kind, **payload):
    """State-first emit: drop the audit event when capacity is exhausted, never the state."""
    if not plan_can_emit(room):
        return None
    return emit(room, kind, **payload)


def emit_best_effort(room, kind, **payload):
    """Compatibility alias: audit events may be dropped at capacity; state wins."""
    return plan_state_emit(room, kind, **payload)


def active(room):
    require(room["state"] == "active", "room_not_active")


DELIVERABLES = (None, "analysis", "change_proposal")


def new_room(actor, room_id, topic, mode="bounded_rounds", now=None, preparing=False, deliverable=None):
    require("rooms:create" in actor["scopes"] and not actor.get("room_id"), "forbidden", 403)
    require(mode in {"bounded_rounds", "free_discussion", "co_creation"}, "invalid_mode", 422)
    require(deliverable in DELIVERABLES, "invalid_deliverable", 422)
    require(isinstance(topic, str) and 1 <= len(topic) <= 2000, "invalid_topic", 422)
    perms = (AGENT_PERMISSIONS if actor["kind"] == "agent" else HUMAN_PERMISSIONS) & set(actor["scopes"])
    if actor["kind"] == "agent":
        require((AGENT_PERMISSIONS - {"room:contribute"}) <= perms, "creator_scope_insufficient", 403)
    room = {"id": room_id, "owner": actor["owner"], "topic": topic, "mode": mode, "deliverable": deliverable,
            "state": "preparing" if preparing else "active",
            "room_revision": 1, "generation": 1, "last_seq": 0, "next_slot": 0, "next_agent": 0,
            "deadline": None if preparing else (now or time.time()) + 900, "started_at": None if preparing else (now or time.time()),
            "duration_seconds": 900, "limits": {"messages": 40, "attempts": 3, "lease": 20, "task_seconds": 60, "rounds": 1},
            "members": {actor["id"]: {"status": "active", "revision": 1, "epoch": 1,
            "permissions": sorted(perms), "read_start_seq": 1, "greeted": False, "kind": actor["kind"]}},
            "events": [], "tasks": {}, "messages": {}, "artifacts": {}, "controls": {}, "keys": {}, "client_ids": {}}
    emit(room, "room.created", actor_id=actor["id"])
    return room


def patch_like(text):
    """Check only for two marker substrings; this does not validate a diff."""
    return isinstance(text, str) and '--- ' in text and '+++ ' in text


def deliverable_artifact_kind(room, text):
    """Classify a co-creation version; change proposals require marker substrings."""
    if room.get("deliverable") != "change_proposal":
        return "text"
    require(patch_like(text), "invalid_patch", 422)
    return "patch"


def retire_started_task(room, task, now, best_effort=False):
    """An authorization may have reached a runtime: never automatically repeat it."""
    if task.get("execution_protocol") != "server_start_v1":
        return False
    item = next((r for r in task.get("_reservations", [])
                 if r["attempt"] == task["attempt"] and r["state"] in {"started", "completed"}), None)
    if item is None:
        return False
    if item["state"] == "started":
        item.update(state="uncertain", reported_at=now, _server_uncertain=True)
    task.update(state="uncertain", lease_expires_at=0)
    if best_effort:
        emit_best_effort(room, "task.uncertain", task_id=task["id"], attempt=task["attempt"])
    else:
        emit(room, "task.uncertain", task_id=task["id"], attempt=task["attempt"])
    return True


def cancel_tasks(room, principal=None, best_effort=False):
    for task in room["tasks"].values():
        if task["state"] in {"queued", "leased"} and (principal is None or task["assignee"] == principal):
            if not retire_started_task(room, task, time.time(), best_effort=best_effort):
                task["state"] = "cancelled"
                if best_effort:
                    emit_best_effort(room, "task.cancelled", task_id=task["id"])
                else:
                    emit(room, "task.cancelled", task_id=task["id"])


def cancel_stale_turn_requests(room):
    """Cancel obsolete pending intents, never interpreting legacy entries as authority."""
    requests = room.get('turn_requests')
    if not requests:
        return False
    valid = [entry for entry in requests if valid_turn_request(room, entry)]
    cancelled = len(requests) - len(valid)
    if not cancelled:
        return False
    room['turn_requests'] = valid
    # No principal or request text: stale entries can predate membership changes.
    emit_best_effort(room, 'turn.requests_cancelled', count=cancelled, reason='stale_context')
    return True


def schedule(room, principals, now=None):
    """Advance timers/leases once. Returns True only when the room actually changed.

    The scheduler runs twice a second for every active room; callers use the return
    value to skip an unconditional UPDATE + NOTIFY (and the SSE traffic it caused).
    """
    now = time.time() if now is None else now
    # A plan dispatch is reconciled before any timer/auto-turn work so a paused,
    # closed, regenerated or stalled room can never leave a run 'running' in the
    # stored row. While a run is still running, automatic mode turns are suppressed.
    previous_run_state = (room.get("_plan_dispatch") or {}).get("state")
    changed = cancel_stale_turn_requests(room)
    changed = reconcile_plan_dispatch(room, now) or changed
    plan_finished = (previous_run_state == "running"
                     and (room.get("_plan_dispatch") or {}).get("state") != "running")
    if plan_finished and (room.get("_plan_dispatch") or {}).get("state") == "aborted":
        return True
    if room["state"] != "active":
        return changed
    if now >= room["deadline"] or len(room["messages"]) >= room["limits"]["messages"]:
        room["state"] = "closed"
        room["generation"] += 1
        room["room_revision"] += 1
        cancel_stale_turn_requests(room)
        cancel_tasks(room, best_effort=True)
        changed = abort_plan_dispatch(room, "room_closed", now) or changed
        emit_best_effort(room, "room.closed", actor_id="system", reason="limit")
        return True
    if plan_finished:
        # Persist terminal plan state before ordinary scheduling; deadline closure
        # above still takes precedence and does not admit another task.
        return True
    for task in room["tasks"].values():
        if task["state"] not in {"queued", "leased"}:
            continue
        expired = (now >= task["deadline"]
                   or (task["state"] == "leased" and now >= task["lease_expires_at"]))
        if task.get("purpose") == "plan_node" and expired:
            # A dispatched plan node is never automatically retried: an unknown
            # start (legacy claim, lost response) fails closed to expired, and a
            # started server reservation becomes uncertain. Either way the same
            # node is not re-queued for another attempt/worker.
            if retire_started_task(room, task, now, best_effort=True):
                changed = True
                continue
            task["state"] = "expired"
            emit_best_effort(room, "task.expired", task_id=task["id"])
            changed = True
            continue
        # The model-call budget counts only attempts that reached the runtime. A claim
        # lost before the call (stale generation, expired lease, crashed worker) costs a
        # claim, not a paid call, so it must not spend the budget; a hard ceiling still
        # bounds a worker that never starts, so nothing spins forever.
        started = len(task.get("_started_attempts") or ())
        exhausted = (started >= room["limits"]["attempts"]
                     or task["attempt"] >= room["limits"]["attempts"] * 2)
        if (now >= task["deadline"] or (task["state"] == "leased" and now >= task["lease_expires_at"])) and retire_started_task(room, task, now, best_effort=True):
            changed = True
            continue
        if now >= task["deadline"] or (task["state"] == "leased" and now >= task["lease_expires_at"] and exhausted):
            task["state"] = "expired"
            emit_best_effort(room, "task.expired", task_id=task["id"])
            changed = True
        elif task["state"] == "leased" and now >= task["lease_expires_at"]:
            if not plan_can_emit(room):
                continue
            task["state"] = "queued"
            emit(room, "task.queued", task_id=task["id"], assignee=task["assignee"], attempt=task["attempt"], generation=task["generation"], membership_revision=task["membership_revision"])
            changed = True
    if room.get('dispatch_held') or any(t["state"] in {"queued", "leased"} for t in room["tasks"].values()):
        return changed
    # A running plan dispatch pauses the normal turn scheduler. Once it reaches a
    # terminal state the loop below resumes exactly as before.
    if (room.get("_plan_dispatch") or {}).get("state") == "running":
        return changed
    eligible = sorted(pid for pid, m in room["members"].items() if m["status"] == "active"
                      and principals[pid]["kind"] == "agent" and participant_permissions(room['mode']) <= set(m["permissions"]))
    if not eligible:
        # No agent can be dispatched. The design closes such a room after a bounded wait
        # instead of leaving it active forever with nothing to do; the stall is reported
        # first so a human can tell "waiting" from "stuck".
        idle = room.get("dispatch_idle")
        if idle is None:
            idle = room["dispatch_idle"] = {"since": now, "reported": False}
            # The stall deadline lives in the payload, so the first observation must
            # be persisted even though it emits no event.
            changed = True
        waited = now - idle["since"]
        bound = max(1, room["limits"]["task_seconds"]) * 2
        if waited < bound:
            return changed
        if not idle["reported"]:
            idle["reported"] = True
            room["mode_status"] = "stalled"
            emit_best_effort(room, "room.stalled", actor_id="system", reason="no_eligible_members")
            return True
        room["state"] = "closed"
        room["generation"] += 1
        room["room_revision"] += 1
        cancel_stale_turn_requests(room)
        cancel_tasks(room, best_effort=True)
        abort_plan_dispatch(room, "room_closed", now)
        emit_best_effort(room, "room.closed", actor_id="system", reason="no_eligible_members")
        return True
    if room.pop("dispatch_idle", None) is not None:
        changed = True
    if room.get("mode_status") == "stalled":
        room["mode_status"] = "running"
        changed = True
    # next_turn mutates mode progress and may consume a request. Stage its entire
    # effect together with task+event: neither may survive a capacity deferral.
    candidate = room.copy()
    for key in ("mode_progress", "turn_requests", "tasks", "events"):
        if key in room:
            candidate[key] = copy.deepcopy(room[key]) if key == "mode_progress" else room[key].copy()
    turn = next_turn(candidate, eligible)
    if turn is None:
        if candidate.get("mode_status") == "round_limit":
            candidate["state"] = "closed"
            candidate["generation"] += 1
            candidate["room_revision"] += 1
            cancel_stale_turn_requests(candidate)
            abort_plan_dispatch(candidate, "room_closed", now)
            emit_best_effort(candidate, "room.closed", actor_id="system", reason="round_limit")
            if room_byte_size(candidate) > ROOM_BYTE_CAPACITY:
                return changed
            for key in ("mode_progress", "next_agent", "turn_requests", "mode_status", "state",
                        "generation", "room_revision", "events", "last_seq"):
                if key in candidate and candidate.get(key) != room.get(key):
                    room[key] = candidate[key]
            return True
        if room_byte_size(candidate) > ROOM_BYTE_CAPACITY:
            return changed
        candidate_changed = candidate != room
        if candidate_changed:
            for key in ("mode_progress", "next_agent", "turn_requests", "mode_status"):
                if key in candidate and candidate.get(key) != room.get(key):
                    room[key] = candidate[key]
        return changed or candidate_changed
    # Defer atomically at capacity: the candidate consumed a pending turn but
    # neither that consumption nor progress may commit without its task/event.
    if len(candidate["tasks"]) >= 256:
        return changed
    if len(candidate["events"]) >= EVENT_CAPACITY:
        return changed
    pid, purpose, phase, round_number = turn
    member = candidate["members"][pid]
    candidate["next_slot"] += 1
    task_id = ident()
    task = {"id": task_id, "room_id": candidate["id"], "slot": candidate["next_slot"], "assignee": pid,
            "purpose": purpose, "phase": phase, "round": round_number, "role": purpose, "state": "queued", "attempt": 0,
            "generation": candidate["generation"], "membership_revision": member["revision"],
            "deadline": min(candidate["deadline"], now + candidate["limits"]["task_seconds"]), "lease_expires_at": 0}
    candidate["tasks"][task_id] = task
    emit(candidate, "task.queued", task_id=task_id, assignee=pid, attempt=0, generation=task["generation"], membership_revision=task["membership_revision"])
    if room_byte_size(candidate) > ROOM_BYTE_CAPACITY:
        return changed
    for key in ("mode_progress", "next_agent", "turn_requests", "mode_status"):
        if key in candidate and candidate.get(key) != room.get(key):
            room[key] = candidate[key]
    room["next_slot"] = candidate["next_slot"]
    room["tasks"][task_id] = task
    room["events"].append(candidate["events"][-1])
    room["last_seq"] = candidate["last_seq"]
    return True


def schedule_call(room, principals, now=None):
    """Scheduler entry point shared by stores; capacity deferrals are handled in schedule."""
    return schedule(room, principals, now)


# Bounds outstanding plan tasks (queued or leased), not calls that may still be
# running externally after an uncertain outcome or abort.
PLAN_DISPATCH_MAX_PARALLEL = 4
PLAN_NODE_TERMINAL = {"completed", "failed", "blocked"}
PLAN_FAILURE_REASONS = {"expired", "cancelled", "skipped", "task_uncertain", "assignee_unavailable",
                        "generation_changed", "room_not_active", "room_closed",
                        "dependency_failed", "input_unavailable", "aborted"}


def _plan_mark_failed(room, run, node, reason):
    require(reason in PLAN_FAILURE_REASONS, "invalid_plan_failure", 500)
    node["state"] = "failed"
    node["failure"] = reason
    # State-first: a full audit ring may drop the event but must not roll back.
    emit_best_effort(room, "plan.node_failed", run_id=run["id"], node_id=node["id"],
                     assignee=node["assignee"], failure=reason)


def _plan_bindings(room, run, node):
    """Return only exact direct predecessor results, never inferred history."""
    dependencies = []
    for dep in node['depends_on']:
        predecessor = next((n for n in run['nodes'] if n['id'] == dep), None)
        source = room['tasks'].get(predecessor.get('task_id')) if predecessor else None
        binding = source.get('_plan_result') if source else None
        plan = source.get('_plan') if source else None
        msg = room['messages'].get(binding.get('message_id')) if isinstance(binding, dict) else None
        if (predecessor and predecessor['state'] == 'completed' and source
                and source.get('state') == 'completed' and isinstance(plan, dict)
                and plan.get('run_id') == run['id'] and plan.get('node_id') == dep
                and source.get('id') == predecessor.get('task_id')
                and source.get('assignee') == predecessor.get('assignee')
                and isinstance(binding, dict) and source.get('generation') == run['generation']
                and binding.get('run_id') == run['id'] and binding.get('node_id') == dep
                and binding.get('task_id') == source['id']
                and binding.get('generation') == source['generation']
                and binding.get('attempt') == source['attempt'] and msg
                and msg.get('actor_id') == source['assignee']):
            dependencies.append(copy.deepcopy(binding))
    return dependencies


def _required_inputs_available(room, run, node, member, bindings):
    required = node.get('required_predecessors')
    if required is None:
        return 'required_predecessors' not in node
    if (not isinstance(required, list) or not isinstance(node.get('depends_on'), list)
            or not all(isinstance(dep, str) and dep in node['depends_on'] for dep in required)
            or len(set(required)) != len(required) or not isinstance(bindings, list)):
        return False
    if not (member and member.get('status') == 'active' and member.get('kind') == 'agent'
            and participant_permissions(room['mode']) <= set(member.get('permissions') or ())):
        return False
    current = _plan_bindings(room, run, node)
    for dep in required:
        matches = [b for b in current if b.get('node_id') == dep]
        if len(matches) != 1 or bindings.count(matches[0]) != 1:
            return False
        msg = room['messages'].get(matches[0]['message_id'])
        if not msg or not isinstance(msg.get('seq'), int) or msg['seq'] < member.get('read_start_seq', float('inf')):
            return False
    return True


def _task_requires_input_protocol(room, task):
    plan = task.get('_plan')
    run = room.get('_plan_dispatch')
    return ((isinstance(plan, dict) and 'required_predecessors' in plan)
            or (isinstance(run, dict) and any(
                n.get('task_id') == task.get('id') and 'required_predecessors' in n
                for n in run.get('nodes', []) if isinstance(n, dict))))


def _check_task_required_inputs(room, task, member):
    if _task_requires_input_protocol(room, task):
        require(task.get('input_protocol') == 'required_predecessors_v1'
                and task.get('execution_protocol') == 'server_start_v1',
                'input_protocol_required', 409)
    plan = task.get('_plan')
    run = room.get('_plan_dispatch')
    if not isinstance(plan, dict):
        nodes = run.get('nodes', []) if isinstance(run, dict) else []
        require(not any(n.get('task_id') == task.get('id') and 'required_predecessors' in n
                        for n in nodes), 'input_unavailable', 409)
        return
    if not isinstance(run, dict):
        require('required_predecessors' not in plan, 'input_unavailable', 409)
        return
    node = next((n for n in run.get('nodes', []) if n.get('id') == plan.get('node_id')), None)
    # A run or task declaring the contract cannot silently downgrade it.
    if 'required_predecessors' not in plan and (not node or 'required_predecessors' not in node):
        return
    draft = room.get('_work_plan') or {}
    owner = room['members'].get(draft.get('_author'))
    require(node and run.get('state') == 'running' and run.get('id') == plan.get('run_id')
            and draft.get('revision') == run.get('plan_revision')
            and draft.get('_epoch') == run.get('epoch') and owner
            and owner.get('epoch') == run.get('epoch') and owner.get('status') == 'active'
            and {'room:read', 'room:control'} <= set(owner.get('permissions') or ())
            and run.get('generation') == room.get('generation') == task.get('generation')
            and node.get('task_id') == task.get('id') and node.get('assignee') == task.get('assignee')
            and member and member.get('revision') == task.get('membership_revision')
            and 'required_predecessors' in node and plan.get('required_predecessors') == node['required_predecessors']
            and _required_inputs_available(room, run, node, member, plan.get('dependencies')),
            'input_unavailable', 409)


def _plan_queue_node(room, run, node, now):
    """Queue exactly one pending node whose dependencies are all completed."""
    assignee = node["assignee"]
    member = room["members"].get(assignee)
    if not (member and member.get("status") == "active" and member.get("kind") == "agent"
            and participant_permissions(room["mode"]) <= set(member.get("permissions") or ())):
        _plan_mark_failed(room, run, node, "assignee_unavailable")
        return True
    if len(room["tasks"]) >= 256:
        _plan_mark_failed(room, run, node, "aborted")
        return True
    if not plan_can_emit(room):
        # No room for the queued audit event (events or bytes): fail closed without
        # creating a task whose creation could not be recorded (state-first policy).
        _plan_mark_failed(room, run, node, "aborted")
        return True
    task_id = ident()
    deadline = now + room["limits"]["task_seconds"]
    if room.get("deadline") is not None:
        deadline = min(deadline, room["deadline"])
    # Snapshot only exact, validated direct predecessor results. Missing legacy or
    # inconsistent bindings remain unavailable; never infer a result from history.
    dependencies = _plan_bindings(room, run, node)
    if not _required_inputs_available(room, run, node, member, dependencies):
        _plan_mark_failed(room, run, node, 'input_unavailable')
        return True
    task = {"id": task_id, "room_id": room["id"], "slot": room["next_slot"] + 1, "assignee": assignee,
            "purpose": "plan_node", "phase": "plan", "round": 0, "role": "plan_node",
            "state": "queued", "attempt": 0, "generation": room["generation"],
            "membership_revision": member["revision"], "deadline": deadline, "lease_expires_at": 0,
            **({"_requires_server_start": True} if room.get("_server_start_plan_run") == run["id"] else {}),
            "_plan": {"run_id": run["id"], "node_id": node["id"], "title": node["title"],
                      "instruction": node["instruction"], "write_scope": node["write_scope"],
                      "dependencies": dependencies,
                      **({'required_predecessors': copy.deepcopy(node['required_predecessors'])}
                         if 'required_predecessors' in node else {})}}
    # Deferred admissions happen in later transactions after the start-time byte
    # reserve may have been consumed. Account for the duplicated private plan text
    # before mutating state; an oversized task must fail closed, not roll back a tick.
    queued_node = {**node, "state": "queued", "task_id": task_id, "failure": None}
    node_growth = (len(json.dumps(queued_node, ensure_ascii=False).encode("utf-8"))
                   - len(json.dumps(node, ensure_ascii=False).encode("utf-8")))
    slot_growth = len(str(room["next_slot"] + 1)) - len(str(room["next_slot"]))
    task_bytes = (len(json.dumps({task_id: task}, ensure_ascii=False).encode("utf-8"))
                  + max(0, node_growth) + max(0, slot_growth) + 2048)
    if room_byte_size(room) + task_bytes > room_byte_limit(room):
        _plan_mark_failed(room, run, node, "aborted")
        return True
    room["next_slot"] += 1
    room["tasks"][task_id] = task
    node["state"] = "queued"
    node["task_id"] = task_id
    node["failure"] = None
    emit_best_effort(room, "task.queued", task_id=task_id, assignee=assignee, attempt=0,
                     generation=task["generation"], membership_revision=task["membership_revision"])
    return True


def queue_ready_plan_nodes(room, run, now):
    """Admit ready nodes in plan order using persisted, transaction-scoped slots.

    A node stays queued while its task is either queued or leased, so it occupies
    one slot until reconciliation observes a terminal task state. Older runs do
    not need a migration: their already queued nodes count against the same cap.
    """
    slots = max(0, PLAN_DISPATCH_MAX_PARALLEL - sum(
        node['state'] == 'queued' for node in run['nodes']))
    changed = False
    for node in run['nodes']:
        if slots == 0:
            break
        if node['state'] != 'pending':
            continue
        if not all(any(other['id'] == dep and other['state'] == 'completed'
                       for other in run['nodes']) for dep in node['depends_on']):
            continue
        changed = _plan_queue_node(room, run, node, now) or changed
        if node['state'] == 'queued':
            slots -= 1
    return changed


def _plan_block_dependents(room, run):
    """Block pending descendants of a failed/blocked node; never queue them."""
    changed = False
    blocked = {node["id"] for node in run["nodes"] if node["state"] in {"failed", "blocked"}}
    while True:
        grew = False
        for node in run["nodes"]:
            if node["state"] == "pending" and any(dep in blocked for dep in node["depends_on"]):
                node["state"] = "blocked"
                node["failure"] = "dependency_failed"
                blocked.add(node["id"])
                grew = changed = True
        if not grew:
            return changed


def abort_plan_dispatch(room, reason, now, node_failure='aborted'):
    """Abort the active run: only plan-node tasks are retired, nothing else."""
    run = room.get('_plan_dispatch')
    if not run or run.get('state') != 'running':
        return False
    require(node_failure in PLAN_FAILURE_REASONS, 'invalid_plan_failure', 500)
    for node in run['nodes']:
        if node['state'] in PLAN_NODE_TERMINAL:
            continue
        task = room['tasks'].get(node.get('task_id')) if node.get('task_id') else None
        if task and task.get('purpose') == 'plan_node' and task['state'] in {'queued', 'leased'}:
            # A started reservation may have reached a runtime: keep it uncertain.
            # Audit events are best effort: at the event ceiling the terminal state
            # must still be persisted, so an event_capacity failure cannot roll it back.
            if not retire_started_task(room, task, now, best_effort=True):
                task['state'] = 'cancelled'
                plan_state_emit(room, 'task.cancelled', task_id=task['id'])
        if node['state'] != 'blocked':
            node['state'] = 'failed'
            node['failure'] = node_failure
    run['state'] = 'aborted'
    run['finished_at'] = now
    run['abort_reason'] = reason
    plan_state_emit(room, 'plan.dispatch_aborted', run_id=run['id'], state='aborted')
    return True


def reconcile_plan_dispatch(room, now):
    """Advance a running plan run from task outcomes. Never retries or redispatches."""
    run = room.get("_plan_dispatch")
    if not run or run.get("state") != "running":
        return False
    plan = room.get("_work_plan")
    author = plan.get("_author") if plan else None
    owner_member = room["members"].get(author) if author else None
    if (plan is None or plan.get("_epoch") != run.get("epoch")
            or not owner_member or owner_member.get("kind") != "human"
            or owner_member.get("epoch") != run.get("epoch")
            or owner_member.get("status") != "active"
            # The author must keep the rights that make stop/read possible: a permission
            # edit keeps the epoch but must not leave a run the owner cannot abort.
            or not {"room:read", "room:control"} <= set(owner_member.get("permissions") or ())):
        # Ownership changed: retire any outstanding plan tasks before marking
        # the run complete, so the old epoch can never claim/execute them.
        return abort_plan_dispatch(room, "ownership_changed", now, "generation_changed")
    if room.get("state") != "active" or run.get("generation") != room.get("generation"):
        return abort_plan_dispatch(room, "room_not_active" if room.get("state") != "active"
                                   else "generation_changed", now)
    changed = False
    while True:
        step = False
        for node in run["nodes"]:
            if node["state"] != "queued":
                continue
            task = room["tasks"].get(node.get("task_id")) if node.get("task_id") else None
            if task is None:
                _plan_mark_failed(room, run, node, "aborted")
                step = True
            elif task["state"] == "completed":
                node["state"] = "completed"
                node["failure"] = None
                step = True
            elif task["state"] == "uncertain":
                _plan_mark_failed(room, run, node, "task_uncertain")
                step = True
            elif task["state"] in {"expired", "cancelled", "skipped"}:
                _plan_mark_failed(room, run, node, task["state"])
                step = True
            elif task["state"] == "failed":
                reason = "input_unavailable" if task.get('_failure_reason') == 'input_unavailable' else "aborted"
                _plan_mark_failed(room, run, node, reason)
                step = True
        step = _plan_block_dependents(room, run) or step
        if all(node["state"] in PLAN_NODE_TERMINAL for node in run["nodes"]):
            failed = any(node["state"] in {"failed", "blocked"} for node in run["nodes"])
            run["state"] = "partial" if failed else "completed"
            run["finished_at"] = now
            emit_best_effort(room, "plan.dispatch_finished", run_id=run["id"], state=run["state"])
            return True
        # Admit only ready nodes that fit; queued and leased tasks both hold a slot.
        step = queue_ready_plan_nodes(room, run, now) or step
        if not step:
            return changed
        changed = True


def replay(room, actor, operation, key, body):
    require(isinstance(key, str) and 1 <= len(key) <= 128, "idempotency_key_required", 422)
    require(not ({"actor_id", "agent_id", "role", "badge"} & body.keys()), "actor_field_forbidden", 422)
    fingerprint = digest([actor["id"], room["id"], operation, body])
    lookup = f"{actor['id']}:{operation}:{key}"
    previous = room["keys"].get(lookup)
    if previous:
        require(previous.get("epoch") == room["members"][actor["id"]]["epoch"], "idempotency_epoch_conflict")
        require(previous["digest"] == fingerprint, "idempotency_conflict")
        require("response" in previous and previous.get("response_until", float("inf")) > time.time(), "idempotency_retired", 410)
        return previous["response"], lookup, fingerprint
    require(len(room["keys"]) < KEY_CAPACITY, "idempotency_capacity", 429)
    return None, lookup, fingerprint


def save_response(room, lookup, fingerprint, response, epoch):
    room["keys"][lookup] = {"digest": fingerprint, "response": response, "epoch": epoch,
                           "response_until": time.time() + DEFAULT_RETENTION.response_seconds}
    return response


def fencing(room, member, task, actor, body, now):
    active(room)
    require(task and task["assignee"] == actor["id"], "not_found", 404)
    require(task["state"] == "leased" and now < task["lease_expires_at"] and now < task["deadline"], "stale_attempt")
    require(all(type(body.get(field)) is int for field in ("attempt", "generation", "membership_revision"))
            and body["attempt"] == task["attempt"] and body["generation"] == room["generation"] == task["generation"]
            and body["membership_revision"] == member["revision"] == task["membership_revision"], "stale_attempt")


RESERVATION_ID = re.compile(r"[0-9a-f]{64}")
RESERVATION_STATES = {"reserved", "started", "completed", "uncertain"}
MAX_RESERVATIONS = 8
PLAN_RESERVATION_CAP_MAX = 16


def valid_plan_reservation_budget(run):
    """Validate a present budget (legacy runs have neither field)."""
    cap = run.get("reservation_cap")
    count = run.get("reservations_granted")
    return (type(cap) is int and 1 <= cap <= PLAN_RESERVATION_CAP_MAX
            and type(count) is int and 0 <= count <= cap)


def grant_reservation(room, task, actor, body, now):
    """Grant at most one execution permission per task attempt, idempotently.

    The caller must not start a paid call unless `execute` is true. A replay of the same
    reservation id never becomes a second call, and a second id for an attempt that
    already holds a reservation is refused rather than double-charged.
    """
    active(room)
    require(not room.get("dispatch_held"), "dispatch_held", 409)
    require(task and task["assignee"] == actor["id"], "not_found", 404)
    require(task["state"] == "leased" and now < task["lease_expires_at"] and now < task["deadline"],
            "stale_attempt", 409)
    require(type(body.get("attempt")) is int and body["attempt"] == task["attempt"]
            and body.get("generation") == room["generation"] == task["generation"]
            and type(body.get("membership_revision")) is int
            and body["membership_revision"] == task["membership_revision"], "stale_attempt", 409)
    _check_task_required_inputs(room, task, room.get('members', {}).get(actor['id']))
    purpose = body.get("purpose", "task")
    require(purpose == "task", "invalid_reservation_purpose", 422)
    reservation_id = body.get("reservation_id")
    require(isinstance(reservation_id, str) and RESERVATION_ID.fullmatch(reservation_id),
            "invalid_reservation_id", 422)
    reservations = task.get("_reservations", [])
    existing = next((item for item in reservations if item["id"] == reservation_id), None)
    if existing:
        return {"execute": False, "reservation_id": reservation_id, "replay": True,
                "state": existing["state"],
                **({"reserved": existing["state"] == "reserved"} if task.get("execution_protocol") == "server_start_v1" else {})}
    if any(item["attempt"] == task["attempt"] for item in reservations):
        return {"execute": False, "reservation_id": reservation_id, "reason": "reservation_already_started"}
    run = None
    if "_plan" in task:
        plan = task["_plan"]
        run = room.get("_plan_dispatch")
        require(isinstance(plan, dict) and isinstance(plan.get("run_id"), str)
                and isinstance(run, dict) and run.get("state") == "running"
                and run.get("id") == plan["run_id"], "plan_dispatch_not_running", 409)
        budget_fields = {"reservation_cap", "reservations_granted"} & run.keys()
        if budget_fields:
            require(valid_plan_reservation_budget(run), "plan_dispatch_bounds", 422)
            if run["reservations_granted"] >= run["reservation_cap"]:
                return {"execute": False, "reservation_id": reservation_id, "reason": "run_call_limit"}
    # The enclosing room transaction serializes admission and rolls back on failure.
    if run is not None and "reservation_cap" in run:
        run["reservations_granted"] += 1
    reservations = task.setdefault("_reservations", reservations)
    reservations.append({"id": reservation_id, "attempt": task["attempt"], "purpose": purpose,
                         "state": "reserved", "created_at": now,
                         **({"execution_protocol": "server_start_v1"}
                            if task.get("execution_protocol") == "server_start_v1" else {})})
    if len(reservations) > MAX_RESERVATIONS:
        del reservations[:-MAX_RESERVATIONS]
    emit(room, "task.execution_reserved", task_id=task["id"], attempt=task["attempt"])
    protocol = task.get("execution_protocol") == "server_start_v1"
    return {"execute": not protocol, "reservation_id": reservation_id, "state": "reserved",
            **({"reserved": True} if protocol else {})}


def start_reservation(room, member, task, actor, body, now):
    """Authorize execution once; this is not evidence that a model actually ran."""
    require(task.get("execution_protocol") == "server_start_v1", "execution_protocol_required", 409)
    fencing(room, member, task, actor, body, now)
    _check_task_required_inputs(room, task, member)
    require(not room.get("dispatch_held"), "dispatch_held", 409)
    require(all(type(body.get(field)) is int for field in ("attempt", "generation", "membership_revision")),
            "stale_attempt", 409)
    reservation_id = body.get("reservation_id")
    require(isinstance(reservation_id, str) and RESERVATION_ID.fullmatch(reservation_id),
            "invalid_reservation_id", 422)
    item = next((r for r in task.get("_reservations", []) if r["id"] == reservation_id), None)
    require(item and item["attempt"] == task["attempt"], "reservation_required", 409)
    if item["state"] != "reserved":
        return {"execute": False, "reservation_id": reservation_id, "state": item["state"], "replay": True}
    item.update(state="started", started_at=now)
    started = task.setdefault("_started_attempts", [])
    if task["attempt"] not in started:
        started.append(task["attempt"])
    emit(room, "task.execution_started", task_id=task["id"], attempt=task["attempt"])
    return {"execute": True, "reservation_id": reservation_id, "state": "started"}


def reservation_outcome(room, task, actor, reservation_id, body, now):
    """Reconcile an outcome, even after room shutdown, without granting new work."""
    require(task and task["assignee"] == actor["id"], "not_found", 404)
    require(isinstance(reservation_id, str) and RESERVATION_ID.fullmatch(reservation_id),
            "not_found", 404)
    reservations = task.get("_reservations") or []
    item = next((row for row in reservations if row["id"] == reservation_id), None)
    require(item, "not_found", 404)
    state = body.get("state")
    require(state in {"completed", "uncertain"}, "invalid_reservation_outcome", 422)
    if item.get("execution_protocol") == "server_start_v1":
        require(item["state"] != "reserved", "execution_not_started", 409)
    if item["state"] not in {"reserved", "started"} and not item.get("_server_uncertain"):
        require(item["state"] == state, "reservation_outcome_conflict", 409)
        # A replayed outcome is just as terminal as the first one; the retry
        # contract must be present on every outcome reply, not only on the first.
        reason = (OUTCOME_RETRY_REASON_COMPLETED if item["state"] == "completed"
                  else OUTCOME_RETRY_REASON_UNKNOWN)
        return {"reservation_id": reservation_id, "state": item["state"], "replay": True,
                "retry_allowed": False, "retry_reason": reason}
    item.pop("_server_uncertain", None)
    item["state"] = state
    item["reported_at"] = now
    emit(room, "task.reservation_outcome", task_id=task["id"], attempt=item["attempt"], state=state)
    if state == "uncertain" and item["attempt"] == task["attempt"] and task["state"] in {"queued", "leased"}:
        # A started call with no outcome must never be retried automatically.
        task["state"] = "uncertain"
        task["lease_expires_at"] = 0
        emit(room, "task.uncertain", task_id=task["id"], attempt=task["attempt"])
    # Delivery contract: an outcome is terminal for this reservation. The server never
    # re-granted execution for it, so a worker cannot safely resubmit the same call.
    reason = OUTCOME_RETRY_REASON_COMPLETED if state == "completed" else OUTCOME_RETRY_REASON_UNKNOWN
    return {"reservation_id": reservation_id, "state": state,
            "retry_allowed": False, "retry_reason": reason}


RESOLUTION_REASONS = {"operator_review", "worker_lost", "duplicate_risk", "other"}
RESOLUTION_FIELDS = {"attempt", "ack_unknown_effects", "reason_code", "note"}


# Closed retry vocabulary for reservation outcomes (R6, session-peer 3-state
# contract): reporting an outcome is terminal for that reservation id, and an
# unknown-outcome call is never resubmitted. These tokens are the only values.
OUTCOME_RETRY_REASON_COMPLETED = "terminal"
OUTCOME_RETRY_REASON_UNKNOWN = "outcome_unknown_no_auto_retry"


def resolve_uncertain(room, task, actor, body, now):
    """Record a human acknowledgement for an uncertain attempt.

    This is bookkeeping only: it never requeues, completes or restarts the task,
    contacts a provider, or claims anything about whether the external call ran.
    """
    require(isinstance(body, dict) and set(body) <= RESOLUTION_FIELDS, "invalid_resolution", 422)
    require(task["state"] == "uncertain", "task_not_uncertain", 409)
    require(type(body.get("attempt")) is int and body["attempt"] == task["attempt"], "stale_resolution", 409)
    require(body.get("ack_unknown_effects") is True, "ack_required", 422)
    reason = body.get("reason_code")
    require(isinstance(reason, str) and reason in RESOLUTION_REASONS, "invalid_resolution", 422)
    note = body.get("note")
    if note is not None:
        require(isinstance(note, str) and len(note) <= 512 and note.isprintable(), "invalid_resolution", 422)
        try:
            note.encode("utf-8")
        except UnicodeEncodeError:
            raise Problem("invalid_resolution", 422) from None
    existing = task.get("resolution")
    if existing is not None:
        same = (existing["attempt"] == body["attempt"] and existing["reason_code"] == reason
                and existing.get("note") == note)
        require(same, "already_resolved", 409)
        return {"resolution": dict(existing), "replay": True}
    resolution = {"attempt": task["attempt"], "resolved_by": actor["id"], "resolved_at": now,
                  "reason_code": reason, "external_effect": "unknown"}
    if note is not None:
        resolution["note"] = note
    task["resolution"] = resolution
    emit(room, "task.uncertain_resolved", task_id=task["id"], attempt=task["attempt"], reason_code=reason,
         external_effect="unknown")
    return {"resolution": dict(resolution), "replay": False}


TASK_EVENT_STAGES = {
    "claimed", "preflight", "runtime_started", "prompt_submitted", "runtime_completed",
    "tool_blocked", "completed", "failed", "cancelled", "skipped",
}


def record_task_event(room, task_id, stage, now):
    """Append one bounded worker-reported execution stage to the task."""
    require(isinstance(stage, str) and stage in TASK_EVENT_STAGES, "invalid_event_stage", 422)
    task = room["tasks"].get(task_id)
    attempt = task["attempt"] if task else None
    events = room.setdefault("task_events", {}).setdefault(task_id, [])
    events.append({"stage": stage, "at": now, "attempt": attempt})
    if len(events) > 24:
        del events[:-24]
    if stage == "runtime_started" and task is not None and task.get("execution_protocol") != "server_start_v1":
        # A claim that never reached the runtime is not a failed model call. Only
        # attempts that actually started one may spend the model-call budget.
        started = task.setdefault("_started_attempts", [])
        if attempt not in started:
            started.append(attempt)


def message(room, actor, body, now, task=None, fingerprint=None):
    result = body.get("result", body)
    require(isinstance(result, dict), "invalid_result", 422)
    require(not ({"actor_id", "agent_id", "role", "badge"} & result.keys()), "actor_field_forbidden", 422)
    text, client_id = result.get("text"), result.get("client_message_id")
    require(isinstance(text, str) and 1 <= len(text) <= 16000 and isinstance(client_id, str) and 1 <= len(client_id) <= 128, "invalid_message", 422)
    reply = result.get("reply_to")
    require(reply is None or (reply in room["messages"] and room["messages"][reply]["seq"] >= room["members"][actor["id"]]["read_start_seq"]), "not_found", 404)
    mentions = result.get("mentions", [])
    require(isinstance(mentions, list) and all(isinstance(p, str) for p in mentions) and len(mentions) <= 4 and len(set(mentions)) == len(mentions), "invalid_mentions", 422)
    require(all(pid in room["members"] and room["members"][pid]["status"] == "active" and room["members"][pid]["kind"] == "agent" for pid in mentions), "invalid_mentions", 422)
    marker = actor["id"] + ":" + client_id
    fingerprint = fingerprint or digest(body)
    prior = room["client_ids"].get(marker)
    if prior:
        require(prior.get("epoch") == room["members"][actor["id"]]["epoch"], "client_message_epoch_conflict")
        require(prior["digest"] == fingerprint, "client_message_conflict")
        return room["messages"][prior["id"]]
    require(len(room["messages"]) < min(200, room["limits"]["messages"]), "message_capacity", 429)
    msg = {"id": ident(), "actor_id": actor["id"], "actor_kind": actor["kind"], "display_name": actor["display_name"],
           "text": text, "kind": "contribution" if task else body["kind"], "reply_to": reply, "mentions": mentions, "created_at": now}
    if result.get('attachment_refs'):
        require(actor['kind']=='human' and not task,'human_attachment_required',403)
        msg['attachment_refs']=[dict(ref) for ref in result['attachment_refs']]
    room["messages"][msg["id"]] = msg
    room["client_ids"][marker] = {"digest": fingerprint, "id": msg["id"], "epoch": room["members"][actor["id"]]["epoch"]}
    msg["seq"] = emit(room, "message.created", message_id=msg["id"])["seq"]
    return msg
