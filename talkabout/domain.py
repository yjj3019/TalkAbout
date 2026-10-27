from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
import uuid
from .retention import DEFAULT_RETENTION
from .modes import next_turn

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


def emit(room, kind, **payload):
    require(len(room["events"]) < 2048, "event_capacity", 429)
    room["last_seq"] += 1
    event = {"event_id": ident(), "room_id": room["id"], "seq": room["last_seq"], "type": kind, "created_at": time.time(), **payload}
    room["events"].append(event)
    return event


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
    """True when a change proposal carries a unified diff, not just prose.

    This is a shape check, not an application: the platform never applies the patch.
    """
    return isinstance(text, str) and '--- ' in text and '+++ ' in text


def deliverable_artifact_kind(room, text):
    """Classify a co-creation version. A change proposal that is not a diff is refused."""
    if room.get("deliverable") != "change_proposal":
        return "text"
    require(patch_like(text), "invalid_patch", 422)
    return "patch"


def retire_started_task(room, task, now):
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
    emit(room, "task.uncertain", task_id=task["id"], attempt=task["attempt"])
    return True


def cancel_tasks(room, principal=None):
    for task in room["tasks"].values():
        if task["state"] in {"queued", "leased"} and (principal is None or task["assignee"] == principal):
            if not retire_started_task(room, task, time.time()):
                task["state"] = "cancelled"
                emit(room, "task.cancelled", task_id=task["id"])


def schedule(room, principals, now=None):
    now = time.time() if now is None else now
    if room["state"] != "active":
        return
    if now >= room["deadline"] or len(room["messages"]) >= room["limits"]["messages"]:
        room["state"] = "closed"
        room["generation"] += 1
        room["room_revision"] += 1
        cancel_tasks(room)
        emit(room, "room.closed", actor_id="system", reason="limit")
        return
    for task in room["tasks"].values():
        if task["state"] not in {"queued", "leased"}:
            continue
        # The model-call budget counts only attempts that reached the runtime. A claim
        # lost before the call (stale generation, expired lease, crashed worker) costs a
        # claim, not a paid call, so it must not spend the budget; a hard ceiling still
        # bounds a worker that never starts, so nothing spins forever.
        started = len(task.get("_started_attempts") or ())
        exhausted = (started >= room["limits"]["attempts"]
                     or task["attempt"] >= room["limits"]["attempts"] * 2)
        if (now >= task["deadline"] or (task["state"] == "leased" and now >= task["lease_expires_at"])) and retire_started_task(room, task, now):
            continue
        if now >= task["deadline"] or (task["state"] == "leased" and now >= task["lease_expires_at"] and exhausted):
            task["state"] = "expired"
            emit(room, "task.expired", task_id=task["id"])
        elif task["state"] == "leased" and now >= task["lease_expires_at"]:
            task["state"] = "queued"
            emit(room, "task.queued", task_id=task["id"], assignee=task["assignee"], attempt=task["attempt"], generation=task["generation"], membership_revision=task["membership_revision"])
    if room.get('dispatch_held') or any(t["state"] in {"queued", "leased"} for t in room["tasks"].values()):
        return
    eligible = sorted(pid for pid, m in room["members"].items() if m["status"] == "active"
                      and principals[pid]["kind"] == "agent" and participant_permissions(room['mode']) <= set(m["permissions"]))
    if not eligible:
        # No agent can be dispatched. The design closes such a room after a bounded wait
        # instead of leaving it active forever with nothing to do; the stall is reported
        # first so a human can tell "waiting" from "stuck".
        idle = room.setdefault("dispatch_idle", {"since": now, "reported": False})
        waited = now - idle["since"]
        bound = max(1, room["limits"]["task_seconds"]) * 2
        if waited < bound:
            return
        if not idle["reported"]:
            idle["reported"] = True
            room["mode_status"] = "stalled"
            emit(room, "room.stalled", actor_id="system", reason="no_eligible_members")
            return
        room["state"] = "closed"
        room["generation"] += 1
        room["room_revision"] += 1
        cancel_tasks(room)
        emit(room, "room.closed", actor_id="system", reason="no_eligible_members")
        return
    room.pop("dispatch_idle", None)
    if room.get("mode_status") == "stalled":
        room["mode_status"] = "running"
    turn = next_turn(room, eligible)
    if turn is None:
        if room.get("mode_status") == "round_limit":
            room["state"] = "closed"
            room["generation"] += 1
            room["room_revision"] += 1
            emit(room, "room.closed", actor_id="system", reason="round_limit")
        return
    require(len(room["tasks"]) < 256, "task_capacity", 429)
    pid, purpose, phase, round_number = turn
    member = room["members"][pid]
    room["next_slot"] += 1
    task_id = ident()
    task = {"id": task_id, "room_id": room["id"], "slot": room["next_slot"], "assignee": pid,
            "purpose": purpose, "phase": phase, "round": round_number, "role": purpose, "state": "queued", "attempt": 0,
            "generation": room["generation"], "membership_revision": member["revision"],
            "deadline": min(room["deadline"], now + room["limits"]["task_seconds"]), "lease_expires_at": 0}
    room["tasks"][task_id] = task
    emit(room, "task.queued", task_id=task_id, assignee=pid, attempt=0, generation=task["generation"], membership_revision=task["membership_revision"])


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
    require(len(room["keys"]) < 2048, "idempotency_capacity", 429)
    return None, lookup, fingerprint


def save_response(room, lookup, fingerprint, response, epoch):
    room["keys"][lookup] = {"digest": fingerprint, "response": response, "epoch": epoch,
                           "response_until": time.time() + DEFAULT_RETENTION.response_seconds}
    return response


def fencing(room, member, task, actor, body, now):
    active(room)
    require(task and task["assignee"] == actor["id"], "not_found", 404)
    require(task["state"] == "leased" and now < task["lease_expires_at"] and now < task["deadline"], "stale_attempt")
    require(body.get("attempt") == task["attempt"] and body.get("generation") == room["generation"] == task["generation"]
            and body.get("membership_revision") == member["revision"] == task["membership_revision"], "stale_attempt")


RESERVATION_ID = re.compile(r"[0-9a-f]{64}")
RESERVATION_STATES = {"reserved", "started", "completed", "uncertain"}
MAX_RESERVATIONS = 8


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
    purpose = body.get("purpose", "task")
    require(purpose == "task", "invalid_reservation_purpose", 422)
    reservation_id = body.get("reservation_id")
    require(isinstance(reservation_id, str) and RESERVATION_ID.fullmatch(reservation_id),
            "invalid_reservation_id", 422)
    reservations = task.setdefault("_reservations", [])
    existing = next((item for item in reservations if item["id"] == reservation_id), None)
    if existing:
        return {"execute": False, "reservation_id": reservation_id, "replay": True,
                "state": existing["state"],
                **({"reserved": existing["state"] == "reserved"} if task.get("execution_protocol") == "server_start_v1" else {})}
    if any(item["attempt"] == task["attempt"] for item in reservations):
        return {"execute": False, "reservation_id": reservation_id, "reason": "reservation_already_started"}
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
        return {"reservation_id": reservation_id, "state": item["state"], "replay": True}
    item.pop("_server_uncertain", None)
    item["state"] = state
    item["reported_at"] = now
    emit(room, "task.reservation_outcome", task_id=task["id"], attempt=item["attempt"], state=state)
    if state == "uncertain" and item["attempt"] == task["attempt"] and task["state"] in {"queued", "leased"}:
        # A started call with no outcome must never be retried automatically.
        task["state"] = "uncertain"
        task["lease_expires_at"] = 0
        emit(room, "task.uncertain", task_id=task["id"], attempt=task["attempt"])
    return {"reservation_id": reservation_id, "state": state}


RESOLUTION_REASONS = {"operator_review", "worker_lost", "duplicate_risk", "other"}
RESOLUTION_FIELDS = {"attempt", "ack_unknown_effects", "reason_code", "note"}


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
