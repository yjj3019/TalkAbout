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


def new_room(actor, room_id, topic, mode="bounded_rounds", now=None, preparing=False):
    require("rooms:create" in actor["scopes"] and not actor.get("room_id"), "forbidden", 403)
    require(mode in {"bounded_rounds", "free_discussion", "co_creation"}, "invalid_mode", 422)
    require(isinstance(topic, str) and 1 <= len(topic) <= 2000, "invalid_topic", 422)
    perms = (AGENT_PERMISSIONS if actor["kind"] == "agent" else HUMAN_PERMISSIONS) & set(actor["scopes"])
    if actor["kind"] == "agent":
        require((AGENT_PERMISSIONS - {"room:contribute"}) <= perms, "creator_scope_insufficient", 403)
    room = {"id": room_id, "owner": actor["owner"], "topic": topic, "mode": mode, "state": "preparing" if preparing else "active",
            "room_revision": 1, "generation": 1, "last_seq": 0, "next_slot": 0, "next_agent": 0,
            "deadline": None if preparing else (now or time.time()) + 900, "started_at": None if preparing else (now or time.time()),
            "duration_seconds": 900, "limits": {"messages": 40, "attempts": 3, "lease": 20, "task_seconds": 60, "rounds": 1},
            "members": {actor["id"]: {"status": "active", "revision": 1, "epoch": 1,
            "permissions": sorted(perms), "read_start_seq": 1, "greeted": False, "kind": actor["kind"]}},
            "events": [], "tasks": {}, "messages": {}, "artifacts": {}, "controls": {}, "keys": {}, "client_ids": {}}
    emit(room, "room.created", actor_id=actor["id"])
    return room


def cancel_tasks(room, principal=None):
    for task in room["tasks"].values():
        if task["state"] in {"queued", "leased"} and (principal is None or task["assignee"] == principal):
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
    if stage == "runtime_started" and task is not None:
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
