from __future__ import annotations

import copy
import time
from .participation import Participation, bound_session

from .identity import REAL_RUNTIMES, public_connection, public_runtime_check
from .text_files import (claim_proof, filter_refs, public_task, token_hash, validate_refs)

from .domain import (AGENT_PERMISSIONS, HUMAN_PERMISSIONS, Problem, active, authorize,
                     cancel_tasks, deliverable_artifact_kind, digest, emit, fencing, ident, message, new_room,
                     record_task_event, replay, require, save_response, schedule, participant_permissions)


def public_room(room, actor, principals=None):
    viewer_member = authorize(actor, room, "room:read")
    tasks = [public_task(t) for t in room["tasks"].values() if actor["kind"] == "human" or t["assignee"] == actor["id"]]
    recorded = room.get("task_events", {})
    for task in tasks:
        task["events"] = copy.deepcopy(recorded.get(task["id"], []))[-12:]
    for task in tasks:
        if 'attachment_refs' in task:task['attachment_refs']=filter_refs(actor,room,viewer_member,task['attachment_refs'])
    messages=[]
    for original in sorted(room['messages'].values(),key=lambda m:m['seq']):
        if original['seq']<viewer_member['read_start_seq']:continue
        msg=copy.deepcopy(original)
        if 'attachment_refs' in msg:msg['attachment_refs']=filter_refs(actor,room,viewer_member,msg['attachment_refs'])
        messages.append(msg)
    members = {pid: {k: copy.deepcopy(v) for k, v in member.items() if not k.startswith('_')}
               for pid, member in room["members"].items()}
    if principals:
        for pid, listed_member in members.items():
            principal = principals.get(pid, {})
            listed_member.update({k: principal.get(k) for k in ("display_name", "handle", "identity_revision")})
            if actor["kind"] == "human":
                receipt = room['members'][pid].get('_participation')
                receipt_valid = receipt and receipt['generation'] == room['generation'] and receipt['epoch'] == listed_member['epoch'] and receipt['membership_revision'] == listed_member['revision']
                listed_member['delivery_evidence'] = ({k: receipt[k] for k in ('delivered_seq', 'read_seq')}
                    if receipt_valid else None)
                connection = principal.get("connection")
                listed_member["connection"] = public_connection(principal) if connection and connection.get("room_id") in {None, room["id"]} else None
                check = principal.get("runtime_check")
                listed_member["runtime_check"] = public_runtime_check(principal) if check and check.get("_room_id") in {None, room["id"]} else None
                posted = [m for m in room["messages"].values() if m["actor_id"] == pid and m["actor_kind"] == "agent"
                          and m['seq']>=max(listed_member['read_start_seq'],viewer_member['read_start_seq'])]
                listed_member["published_contributions"] = len(posted)
                current=[m for m in posted if m.get('generation')==room['generation']
                         and m.get('membership_epoch')==listed_member['epoch']]
                listed_member['response_evidence']={
                    'worker_reported':sum(m.get('execution_evidence')=='worker_reported_runtime' and
                        m.get('runtime_check_id')==(principal.get('runtime_check') or {}).get('id') for m in current),
                    'synthetic':sum(m.get('execution_evidence')=='synthetic_test_only' for m in current),
                    'unverified':sum(m.get('execution_evidence','unverified_agent')=='unverified_agent' for m in current),
                    'last_response_at':max((m['created_at'] for m in current),default=None)}
    return {k: copy.deepcopy(room[k]) for k in ("id", "topic", "mode", "state", "generation", "room_revision", "limits", "last_seq")} | {
        "deliverable": room.get("deliverable"), "members": members,
        "messages": messages, "tasks": tasks,
        "artifacts": [{**a, "versions": [v for v in a["versions"] if v["seq"] >= viewer_member["read_start_seq"]]}
                      for a in room["artifacts"].values() if any(v["seq"] >= viewer_member["read_start_seq"] for v in a["versions"])],
        "membership_revision": viewer_member["revision"],
        "mode_status": room.get("mode_status", "running"), "mode_progress": copy.deepcopy(room.get("mode_progress", {})),
        "dispatch_held": room.get("dispatch_held", False),
        "pending_controls": sum(c['state'] == 'pending' and c.get('requested_seq', 0) >= viewer_member['read_start_seq']
                                for c in room['controls'].values()) if actor['kind'] == 'human' else None,
        "handover": copy.deepcopy(room.get("handover")),
        "deadline": room.get("deadline"), "started_at": room.get("started_at"),
        "effective_permissions": sorted(set(actor["scopes"]) & set(viewer_member["permissions"])),
        "pinned_message_id": room.get("pinned_message_id") if room.get("pinned_message_id") in room["messages"] and room["messages"][room["pinned_message_id"]]["seq"] >= viewer_member["read_start_seq"] else None}

def control_parameters(action, parameters, room, member):
    require(isinstance(parameters, dict), "invalid_parameters", 422)
    if action == "change_topic":
        require(set(parameters) == {"topic"} and isinstance(parameters["topic"], str) and 1 <= len(parameters["topic"]) <= 2000, "invalid_topic", 422)
    elif action == "change_mode":
        require(set(parameters) == {"mode"} and isinstance(parameters["mode"], str) and parameters["mode"] in {"bounded_rounds", "free_discussion", "co_creation"}, "invalid_mode", 422)
    elif action == "change_limits":
        proposed = parameters.get("limits")
        require(set(parameters) == {"limits"} and isinstance(proposed, dict) and proposed and set(proposed) <= set(room["limits"]), "invalid_limits", 422)
        limits = {**room["limits"], **proposed}
        ranges = {"messages": (1, 200), "attempts": (1, 3), "lease": (5, 60), "task_seconds": (5, 180), "rounds": (1, 3)}
        require(all(type(limits[k]) is int and low <= limits[k] <= high for k, (low, high) in ranges.items()) and limits["lease"] <= limits["task_seconds"], "invalid_limits", 422)
        parameters = {"limits": limits}
    elif action == "pin":
        message_id = parameters.get("message_id")
        require(set(parameters) == {"message_id"} and isinstance(message_id, str) and message_id in room["messages"] and room["messages"][message_id]["seq"] >= member["read_start_seq"], "not_found", 404)
    else:
        require(not parameters, "invalid_parameters", 422)
    return parameters

class Core(Participation):
    def __init__(self, store, text_files=None):
        self.store = store
        self.text_files = text_files

    def _worker_ready(self, principal, room_id, now, required=None):
        connection = principal.get("connection", {})
        binding = connection.get("_credential_binding")
        if not (binding and connection.get("state") in {"idle", "running"}
                and connection.get("room_id") in {None, room_id}
                and 0 <= now - connection.get("last_seen_at", 0) <= 75
                and self.store.credential_active(binding, principal["id"], room_id, required if required is not None else participant_permissions('bounded_rounds'))):
            return False
        if connection.get("runtime") == "synthetic":
            return connection.get("execution_evidence") == "synthetic_test_only"
        check = principal.get("runtime_check", {})
        return (connection.get("runtime") in REAL_RUNTIMES and check.get("status") == "passed"
                and check.get("runtime") == connection["runtime"] and check.get("expires_at", 0) > now
                and check.get("_credential_binding") == binding and check.get("_room_id") in {None, room_id}
                and check.get("verification") == "worker_reported_test")

    def me(self, token):
        actor = self.store.auth(token)
        require("agent:self_read" in actor["scopes"] or actor["kind"] == "human", "forbidden", 403)
        return {k: actor.get(k) for k in ("id", "display_name", "handle", "kind", "identity_revision", "scopes", "capabilities", "model")} | {"agent_id": actor["id"] if actor["kind"] == "agent" else None, "connection": public_connection(actor), "runtime_check": public_runtime_check(actor)}

    def invitations(self, token):
        actor, rooms, principals = self.store.snapshot(token)
        require("agent:self_read" in actor["scopes"] or actor["kind"] == "human", "forbidden", 403)
        return [{"room_id": rid, "membership_id": actor["id"], "topic": r["topic"], "mode": r["mode"],
                 "membership_revision": r["members"][actor["id"]]["revision"],
                 "notice": "Private by default; authorized human room members can read; special operator access is disabled"}
                for rid, r in rooms.items() if actor["id"] in r["members"] and r["members"][actor["id"]]["status"] == "pending"
                and (not actor.get("room_id") or actor["room_id"] == rid)]

    def rooms(self, token):
        actor, rooms, principals = self.store.snapshot(token)
        result = []
        for room in rooms.values():
            try:
                result.append(public_room(room, actor, principals))
            except Problem as exc:
                if exc.status not in {403, 404}:
                    raise
        return result

    def create(self, token, body, key):
        actor, rooms, principals = self.store.snapshot(token)
        require(key and len(key) <= 128, "idempotency_key_required", 422)
        require(not ({"actor_id", "agent_id", "role", "badge"} & body.keys()), "actor_field_forbidden", 422)
        require(set(body) <= {"topic", "mode", "start_policy", "deliverable"}, "invalid_room_fields", 422)
        require(body.get("start_policy", "automatic") in {"automatic", "manual"}, "invalid_start_policy", 422)
        require(body.get("start_policy") != "manual" or actor["kind"] == "human", "human_start_required", 403)
        require(body.get("deliverable") in (None, "analysis", "change_proposal"), "invalid_deliverable", 422)
        # Deterministic create identity makes lost responses replayable without returning a new room.
        room_id = digest([actor["id"], "create", key])[:32]
        if room_id in rooms:
            require(rooms[room_id]["create_digest"] == digest(body), "idempotency_conflict")
            return public_room(rooms[room_id], actor, principals)
        room = new_room(actor, room_id, body.get("topic"), body.get("mode", "bounded_rounds"),
                        preparing=body.get("start_policy") == "manual", deliverable=body.get("deliverable"))
        room["create_digest"] = digest(body)
        if actor["kind"] == "agent":
            owners = [p["id"] for p in principals.values() if p["owner"] == actor["owner"] and p["kind"] == "human"]
            if len(owners) == 1:
                room["handover"] = {"target_human_id": owners[0], "creator_id": actor["id"], "state": "pending", "revision": 1,
                                    "notice": "Acceptance grants the workspace owner human full room history and administration membership."}
                emit(room, "handover.offered", target_human_id=owners[0], actor_id=actor["id"])
        schedule(room, principals)
        room = self.store.create(token, room)
        return public_room(room, actor, principals)

    def handovers(self, token):
        actor, rooms, _ = self.store.snapshot(token)
        require(actor["kind"] == "human" and "room:manage_members" in actor["scopes"] and not actor.get("room_id"), "forbidden", 403)
        return [{"room_id": r["id"], "room_revision": r["room_revision"], "handover_revision": r["handover"]["revision"], "creator_id": r["handover"]["creator_id"], "notice": r["handover"]["notice"]}
                for r in rooms.values() if r["owner"] == actor["owner"] and r.get("handover", {}).get("target_human_id") == actor["id"] and r["handover"]["state"] == "pending" and r["state"] != "closed"]

    def accept_handover(self, token, room_id, key, body):
        _, _, principals = self.store.snapshot(token)
        with self.store.transaction(room_id, token) as (actor, room):
            h = room.get("handover")
            require(actor["kind"] == "human" and actor["owner"] == room["owner"] and not actor.get("room_id") and h and h["target_human_id"] == actor["id"], "not_found", 404)
            require({"room:read", "room:manage_members", "room:control", "room:approve"} <= set(actor["scopes"]), "forbidden", 403)
            require(set(body) == {"expected_room_revision", "expected_handover_revision", "acknowledge_history"}, "invalid_handover_fields", 422)
            if h["state"] == "accepted":
                member = authorize(actor, room, "room:manage_members", True)
                previous, _, _ = replay(room, actor, "handover", key, body)
                require(previous is not None, "handover_unavailable")
                return previous
            require(room["state"] in {"active", "paused"}, "room_closed")
            require(body["expected_room_revision"] == room["room_revision"] and body["expected_handover_revision"] == h["revision"], "handover_conflict")
            require(body["acknowledge_history"] is True, "history_acknowledgement_required", 422)
            require(actor["id"] not in room["members"] and len(room["members"]) < 16, "handover_membership_conflict")
            member = {"status": "active", "kind": "human", "revision": 1, "epoch": 1, "permissions": sorted(HUMAN_PERMISSIONS & set(actor["scopes"])), "read_start_seq": 1, "greeted": False}
            room["members"][actor["id"]] = member
            _, lookup, fingerprint = replay(room, actor, "handover", key, body)
            h.update(state="accepted", revision=h["revision"] + 1, accepted_by=actor["id"], accepted_at=time.time())
            room["room_revision"] += 1
            room["generation"] += 1
            cancel_tasks(room)
            room.pop("mode_progress", None)
            room["mode_status"] = "running"
            h["accepted_seq"] = emit(room, "handover.accepted", actor_id=actor["id"], generation=room["generation"])["seq"]
            schedule(room, principals)
            return save_response(room, lookup, fingerprint, copy.deepcopy(h), member["epoch"])

    def read(self, token, room_id):
        actor, rooms, principals = self.store.snapshot(token)
        require(room_id in rooms, "not_found", 404)
        return public_room(rooms[room_id], actor, principals)

    def tick(self, token, room_id):
        _, _, principals = self.store.snapshot(token)
        with self.store.transaction(room_id, token) as (actor, room):
            authorize(actor, room, "room:read")
            schedule(room, principals)

    def mutation(self, token, room_id, operation, key, body):
        _, _, principals = self.store.snapshot(token)
        with self.store.transaction(room_id, token) as (actor, room):
            bits = operation.split("/")
            target = bits[1] if len(bits) > 1 else None
            verb = bits[-1]
            if bits[0] == "tasks":
                permission = "task:" + verb
            elif bits[0] == "messages":
                require(body.get("kind") in {"question", "advice"}, "invalid_kind", 422)
                permission = "room:" + body["kind"]
            elif bits[0] == "memberships":
                permission = "room:manage_members"
            elif bits[0] == "controls":
                permission = "room:approve" if verb == "approve" else "room:control"
            elif bits[0] == "artifacts":
                permission = "room:adopt" if verb == "adoptions" else "room:contribute"
            elif bits[0] == "turn-requests":
                permission = "room:speak"
            else:
                raise Problem("not_found", 404)
            human = bits[0] in {"messages", "memberships", "controls"} or verb == "adoptions"
            member = authorize(actor, room, permission, human)
            if bits[0] == 'artifacts' and actor['kind'] == 'agent' and member.get('_participation'):
                require('session_id' in body, 'session_required')
            if bits[0] in {'tasks', 'artifacts'} and 'session_id' in body:
                bound_session(token, body, room, member)
            if bits[0] == "tasks" and verb == "complete":
                authorize(actor, room, "room:speak")
            previous, lookup, fingerprint = replay(room, actor, operation, key, body)
            if previous is not None:
                if self.text_files and bits[0]=='tasks' and verb=='claim' and 'lease_id' in previous:
                    return {**previous,'lease_proof':claim_proof(token,room_id,previous,key)}
                return previous
            now = time.time()
            if bits[0] == "turn-requests":
                active(room)
                require(actor["kind"] == "agent" and room["mode"] == "free_discussion", "unsupported_turn_request", 422)
                require(set(body) == {"generation", "membership_revision"} and body["generation"] == room["generation"] and body["membership_revision"] == member["revision"], "stale_context")
                requests = room.setdefault("turn_requests", [])
                require(actor["id"] not in requests and not any(t["assignee"] == actor["id"] and t["state"] in {"queued", "leased"} for t in room["tasks"].values()), "turn_already_pending")
                require(len(requests) < 16 and room.get("request_count", 0) < 64, "turn_request_capacity", 429)
                requests.append(actor["id"])
                room["request_count"] = room.get("request_count", 0) + 1
                emit(room, "turn.requested", actor_id=actor["id"])
                schedule(room, principals)
                response = {"accepted": True}
            elif bits[0] == "tasks":
                task = room["tasks"].get(target)
                require(task and task["assignee"] == actor["id"], "not_found", 404)
                if verb == "complete":
                    result = body.get("result", {})
                    require(isinstance(result, dict), "invalid_result", 422)
                    marker = actor["id"] + ":" + str(result.get("client_message_id"))
                    prior = room["client_ids"].get(marker)
                    if prior:
                        require(prior.get("epoch") == member["epoch"], "client_message_epoch_conflict")
                        require(prior["digest"] == fingerprint, "client_message_conflict")
                        response = {"message": room["messages"][prior["id"]], "task": public_task(task)}
                        return save_response(room, lookup, fingerprint, response, member["epoch"])
                if verb == "claim":
                    active(room)
                    require(not room.get('dispatch_held'), 'dispatch_held')
                    require(task["state"] == "queued" and now < task["deadline"], "task_unavailable")
                    require(body.get("generation") == task["generation"] == room["generation"] and body.get("membership_revision") == task["membership_revision"] == member["revision"], "stale_attempt")
                    task["attempt"] += 1
                    task["state"] = "leased"
                    task["lease_expires_at"] = min(task["deadline"], now + room["limits"]["lease"])
                    if self.text_files:
                        task['lease_id']=ident()
                        task['_file_credential_binding']=token_hash(token)
                        task['_file_lease_hash']=token_hash(claim_proof(token,room_id,task,key))
                        visible=sorted((m for m in room['messages'].values() if m['seq']>=member['read_start_seq']),key=lambda m:m['seq'])[-8:]
                        refs={r['file_id']:r for m in visible for r in filter_refs(actor,room,member,m.get('attachment_refs',[]))}
                        task['attachment_refs']=list(refs.values())[:8]
                    emit(room, "task.claimed", task_id=target, attempt=task["attempt"])
                    record_task_event(room, target, "claimed", now)
                    response = copy.deepcopy(task)
                else:
                    require(verb in {"complete", "heartbeat", "skip"}, "not_found", 404)
                    fencing(room, member, task, actor, body, now)
                    if verb == "heartbeat":
                        task["lease_expires_at"] = min(task["deadline"], now + room["limits"]["lease"])
                        events = body.get("events", [])
                        require(isinstance(events, list) and len(events) <= 8
                                and all(isinstance(item, str) for item in events), "invalid_events", 422)
                        for stage in events:
                            record_task_event(room, target, stage, now)
                        response = copy.deepcopy(task)
                    elif verb == "complete":
                        if room["mode"] == "co_creation" and task["purpose"] == "creator":
                            authorize(actor, room, "room:contribute")
                        msg = message(room, actor, body, now, task, fingerprint)
                        principal=principals[actor['id']];connection=principal.get('connection') or {}
                        same_credential=connection.get('_credential_binding')==token_hash(token)
                        verified=same_credential and connection.get('runtime')!='synthetic' and self._worker_ready(principal,room_id,now)
                        synthetic=same_credential and connection.get('runtime')=='synthetic'
                        msg.update(execution_evidence='worker_reported_runtime' if verified else 'synthetic_test_only' if synthetic else 'unverified_agent',
                                   runtime_check_id=(principal.get('runtime_check') or {}).get('id') if verified else None,
                                   generation=room['generation'],membership_epoch=member['epoch'])
                        proposal = None
                        if room["mode"] == "co_creation" and task["purpose"] == "creator":
                            aid = room.get("creation_artifact_id")
                            if not aid:
                                require(len(room["artifacts"]) < 16, "artifact_capacity", 429)
                                aid = ident()
                                room["creation_artifact_id"] = aid
                                room["artifacts"][aid] = {"id": aid, "revision": 0, "versions": [], "adopted": None}
                            artifact = room["artifacts"][aid]
                            require(len(artifact["versions"]) < 16, "version_capacity", 429)
                            # A change-proposal room must receive a diff; anything else is refused
                            # before it becomes a version, and the platform never applies it.
                            kind = deliverable_artifact_kind(room, msg["text"])
                            artifact["versions"].append({"id": ident(), "seq": room["last_seq"] + 1, "text": msg["text"], "kind": kind, "actor_id": actor["id"], "sources": [], "ai_generated": True})
                            artifact["revision"] += 1
                            emit(room, "artifact.changed", artifact_id=aid, actor_id=actor["id"])
                            proposal = copy.deepcopy(artifact)
                            proposal["versions"] = [v for v in proposal["versions"] if v["seq"] >= member["read_start_seq"]]
                        task["state"] = "completed"
                        record_task_event(room, target, "completed", now)
                        member["greeted"] = True
                        emit(room, "task.completed", task_id=target)
                        response = {"message": msg, "task": copy.deepcopy(task)}
                        if proposal:
                            response["artifact"] = proposal
                    else:
                        task["state"] = "skipped"
                        emit(room, "task.skipped", task_id=target, actor_id=actor["id"])
                        response = copy.deepcopy(task)
                    schedule(room, principals, now)
            elif bits[0] == "messages":
                if body.get('attachment_refs'):
                    require(self.text_files is not None,'feature_disabled',409)
                    validate_refs(actor,room,member,body['attachment_refs'])
                require(room["state"] in {"preparing", "active", "paused"}, "room_closed")
                response = message(room, actor, body, now, fingerprint=fingerprint)
                # Human messages never create extra recursive turns.
            elif bits[0] == "memberships":
                require(room["state"] in {"preparing", "active", "paused"}, "room_closed")
                pid = body.get("principal_id") if len(bits) == 1 else target
                require(pid in principals and principals[pid]["owner"] == actor["owner"], "not_found", 404)
                old = room["members"].get(pid)
                require(old or len(room["members"]) < 16, "membership_capacity", 429)
                if old:
                    require(body.get("expected_revision") == old["revision"], "membership_conflict")
                proposed = body.get("permissions", [])
                require(isinstance(proposed, list) and all(isinstance(p, str) for p in proposed), "invalid_permissions", 422)
                perms = set(proposed)
                permitted = AGENT_PERMISSIONS if principals[pid]["kind"] == "agent" else HUMAN_PERMISSIONS
                require(perms <= permitted and "room:read" in perms, "invalid_permissions", 422)
                status = body.get("status", old["status"] if old else "pending")
                require(status in {"pending", "blocked", "left", "active"}, "invalid_status", 422)
                require(status != "active" or (old and old["status"] in {"active", "blocked"}), "invitation_required", 422)
                reset_read = not old or old["status"] != status
                epoch = (old or {}).get("epoch", 0) + (1 if reset_read else 0)
                room["members"][pid] = {"status": status, "kind": principals[pid]["kind"], "permissions": sorted(perms), "revision": (old or {}).get("revision", 0) + 1,
                                        "epoch": epoch, "read_start_seq": room["last_seq"] + 1 if reset_read else old["read_start_seq"], "greeted": False}
                cancel_tasks(room, pid)
                emit(room, "membership.changed", principal_id=pid, actor_id=actor["id"])
                schedule(room, principals)
                response = copy.deepcopy(room["members"][pid])
            elif bits[0] == "controls":
                if verb != "approve":
                    require(len(room["controls"]) < 128, "control_capacity", 429)
                    action = body.get("action")
                    require(action in {"start", "pause", "resume", "close", "change_topic", "change_mode", "change_limits", "pin", "hold_dispatch", "release_dispatch"}, "unsupported_control", 422)
                    parameters = control_parameters(action, body.get("parameters", {}), room, member)
                    reason = body.get("reason", "")
                    require(isinstance(reason, str) and len(reason) <= 512, "invalid_reason", 422)
                    require(body.get("expected_revision") == room["room_revision"], "room_conflict")
                    require(room["state"] != "closed", "room_closed")
                    control = {"id": ident(), "actor_id": actor["id"], "action": action, "parameters": parameters, "reason": reason,
                               "expected_revision": room["room_revision"], "state": "pending", "created_at": now}
                    room["controls"][control["id"]] = control
                    control["requested_seq"] = emit(room, "control.requested", control_id=control["id"], actor_id=actor["id"])["seq"]
                    response = copy.deepcopy(control)
                else:
                    authorize(actor, room, "room:control", True)
                    control = room["controls"].get(target)
                    require(control and control["state"] == "pending", "control_unavailable")
                    require(control["expected_revision"] == room["room_revision"], "room_conflict")
                    action = control["action"]
                    require(room["state"] != "closed", "room_closed")
                    if action in {'hold_dispatch', 'release_dispatch'}:
                        require(room['state'] in {'active', 'preparing', 'paused'}, 'invalid_transition')
                        room['dispatch_held'] = action == 'hold_dispatch'
                    elif action == "start":
                        require(room["state"] == "preparing", "invalid_transition")
                        candidates = [(pid, m) for pid, m in room['members'].items() if m['kind']=='agent' and m['status']=='active'
                                      and participant_permissions('bounded_rounds') <= set(m['permissions'])]
                        require(candidates, 'participants_required')
                        required = participant_permissions(room['mode'])
                        require(all(required <= set(m['permissions']) for _, m in candidates), 'participant_permissions_required')
                        require(all(self._worker_ready(principals.get(pid, {}), room['id'], now, required)
                                    for pid, _ in candidates), 'worker_connection_required')
                        room.update(state="active", started_at=now, deadline=now + room.get("duration_seconds", 900))
                    elif action == "pause":
                        require(room["state"] == "active", "invalid_transition")
                        room["state"] = "paused"
                    elif action == "resume":
                        require(room["state"] == "paused", "invalid_transition")
                        room["state"] = "active"
                    elif action == "close":
                        room["state"] = "closed"
                    elif action == "change_topic":
                        text = control["parameters"].get("topic")
                        require(isinstance(text, str) and 1 <= len(text) <= 2000, "invalid_topic", 422)
                        room["topic"] = text
                    elif action == "change_mode":
                        room["mode"] = control["parameters"]["mode"]
                    elif action == "change_limits":
                        room["limits"] = copy.deepcopy(control["parameters"]["limits"])
                    elif action == "pin":
                        control_parameters(action, control["parameters"], room, member)
                        room["pinned_message_id"] = control["parameters"]["message_id"]
                    if action not in {"pin", "hold_dispatch", "release_dispatch"}:
                        room["generation"] += 1
                        cancel_tasks(room)
                    if action in {"change_topic", "change_mode"}:
                        room.pop("mode_progress", None)
                        room.pop("creation_artifact_id", None)
                        room["turn_requests"] = []
                        room["mode_status"] = "running"
                    room["room_revision"] += 1
                    control.update(state="applied", approver_id=actor["id"], applied_at=now)
                    control["applied_seq"] = emit(room, "control.applied", control_id=control["id"], actor_id=actor["id"], generation=room["generation"])["seq"]
                    schedule(room, principals)
                    response = copy.deepcopy(control)
            else:
                active(room)
                require(body.get("generation") == room["generation"] and body.get("membership_revision") == member["revision"], "stale_context")
                artifact = room["artifacts"].get(target) if len(bits) > 1 else None
                if len(bits) > 1:
                    require(artifact, "not_found", 404)
                    require(any(v["seq"] >= member["read_start_seq"] for v in artifact["versions"]), "not_found", 404)
                    require(body.get("expected_revision") == artifact["revision"], "artifact_conflict")
                else:
                    require(len(room["artifacts"]) < 16, "artifact_capacity", 429)
                    artifact = {"id": ident(), "revision": 0, "versions": [], "adopted": None}
                    room["artifacts"][artifact["id"]] = artifact
                if verb == "adoptions":
                    require(any(v["id"] == body.get("version_id") and v["seq"] >= member["read_start_seq"] for v in artifact["versions"]), "not_found", 404)
                    artifact["adopted"] = body["version_id"]
                else:
                    require(set(body) <= {"generation", "membership_revision", "expected_revision", "text", "sources", "caption", "session_id"}, "invalid_artifact_fields", 422)
                    text = body.get("text")
                    require(isinstance(text, str) and 1 <= len(text) <= 64000, "text_only", 422)
                    require(isinstance(body.get("sources", []), list) and len(body.get("sources", [])) <= 20, "invalid_sources", 422)
                    require(all(isinstance(s, str) and len(s) <= 2048 for s in body.get("sources", [])), "invalid_sources", 422)
                    require(isinstance(body.get("caption", ""), str) and len(body.get("caption", "")) <= 2000, "invalid_caption", 422)
                    require(len(artifact["versions"]) < 16, "version_capacity", 429)
                    artifact["versions"].append({"id": ident(), "seq": room["last_seq"] + 1, "text": text, "actor_id": actor["id"], "sources": body.get("sources", []), "ai_generated": actor["kind"] == "agent"})
                artifact["revision"] += 1
                emit(room, "artifact.changed", artifact_id=artifact["id"], actor_id=actor["id"])
                if verb == "adoptions" and room["mode"] == "co_creation" and room.get("mode_status") == "awaiting_adoption" and artifact["id"] == room.get("creation_artifact_id"):
                    room["state"] = "closed"
                    room["generation"] += 1
                    room["room_revision"] += 1
                    room["mode_status"] = "adopted"
                    emit(room, "room.closed", actor_id=actor["id"], reason="artifact_adopted")
                response = copy.deepcopy(artifact)
                response["versions"] = [v for v in response["versions"] if v["seq"] >= member["read_start_seq"]]
            if bits[0]=='tasks':
                if response.get('id')==target:response=public_task(response)
                elif 'task' in response:response={**response,'task':public_task(response['task'])}
            saved=save_response(room, lookup, fingerprint, copy.deepcopy(response), member["epoch"])
            if self.text_files and bits[0]=='tasks' and verb=='claim':
                return {**saved,'lease_proof':claim_proof(token,room_id,saved,key)}
            return saved

    def accept(self, token, room_id, key, body):
        _, _, principals = self.store.snapshot(token)
        with self.store.transaction(room_id, token) as (actor, room):
            member = room["members"].get(actor["id"])
            require(member and member["status"] in {"pending", "active"}, "invitation_unavailable")
            require(not actor.get("room_id") or actor["room_id"] == room_id, "forbidden", 403)
            previous, lookup, fingerprint = replay(room, actor, "accept", key, body)
            if previous is not None:
                return previous
            require(member["status"] == "pending", "invitation_unavailable")
            require(room["state"] in {"preparing", "active"}, "room_not_accepting")
            require(body.get("expected_revision") == member["revision"] and body.get("acknowledge_private") is True, "invitation_confirmation_required", 422)
            member["status"] = "active"
            member["revision"] += 1
            member["read_start_seq"] = room["last_seq"] + 1
            emit(room, "membership.accepted", principal_id=actor["id"])
            schedule(room, principals)
            return save_response(room, lookup, fingerprint, copy.deepcopy(member), member["epoch"])

    @staticmethod
    def scope(actor, rooms):
        members = {}
        for rid, room in sorted(rooms.items()):
            try:
                m = authorize(actor, room, "room:read")
                members[rid] = {"revision": m["revision"], "epoch": m["epoch"], "start": m["read_start_seq"]}
            except Problem:
                pass
        return members, digest([actor["id"], actor.get("room_id"), sorted(actor["scopes"]), members])

    def events(self, token, cursor=None, wait_seconds=0, rebase=False, target_room=None, session_id=None):
        require(0 <= wait_seconds <= 10, "invalid_wait", 422)
        end = time.monotonic() + wait_seconds
        with self.store.listener() as wait:
            while True:
                actor, rooms, _ = self.store.snapshot(token)
                if target_room:
                    require(target_room in rooms, "not_found", 404)
                    authorize(actor, rooms[target_room], "room:read")
                    rooms = {target_room: rooms[target_room]}
                if session_id is not None:
                    require(target_room is not None, 'invalid_cursor_view')
                    r = rooms[target_room]
                    m = authorize(actor, r, 'room:read')
                    bound_session(token, {'session_id': session_id, 'generation': r['generation'], 'membership_revision': m['revision']}, r, m)
                members, revision = self.scope(actor, rooms)
                old = self.store.cursor(cursor) if cursor else None
                require(not cursor or old is not None or rebase, "cursor_gap", 410)
                require(not old or old["principal"] == actor["id"], "invalid_cursor", 403)
                require(not old or old.get("view", "inbox") == (target_room or "inbox"), "invalid_cursor_view")
                require(not session_id or not old or (old.get('participation_session') == session_id
                        and old.get('_credential_binding') == token_hash(token)), 'invalid_cursor', 403)
                require(rebase or not old or old["scope"] == revision, "cursor_scope_changed")
                offsets = {}
                gaps = []
                for rid, m in members.items():
                    same_epoch = old and old.get("epochs", {}).get(rid) == m["epoch"]
                    offsets[rid] = old["offsets"].get(rid, m["start"] - 1) if same_epoch else m["start"] - 1
                    if rebase and cursor and not old:
                        offsets[rid] = rooms[rid]["last_seq"]
                        gaps.append(rid)
                events = []
                room_ids = list(members)
                rotation = (old or {}).get("rotation", 0)
                room_ids = room_ids[rotation % max(1, len(room_ids)):] + room_ids[:rotation % max(1, len(room_ids))]
                for rid in room_ids:
                    history = rooms[rid]["events"]
                    floor = rooms[rid].get("first_retained_seq", history[0]["seq"] if history else 1)
                    if offsets[rid] < floor - 1:
                        require(rebase, "cursor_gap", 410)
                        offsets[rid] = rooms[rid]["last_seq"]
                        gaps.append(rid)
                    selected = [e for e in history if e["seq"] > offsets[rid]][:16]
                    events.extend(selected)
                    if selected:
                        offsets[rid] = selected[-1]["seq"]
                latest_actor, latest_rooms, _ = self.store.snapshot(token)
                if target_room:
                    require(target_room in latest_rooms, "not_found", 404)
                    authorize(latest_actor, latest_rooms[target_room], "room:read")
                    latest_rooms = {target_room: latest_rooms[target_room]}
                    if session_id is not None:
                        r = latest_rooms[target_room]
                        m = authorize(latest_actor, r, 'room:read')
                        bound_session(token, {'session_id': session_id, 'generation': r['generation'], 'membership_revision': m['revision']}, r, m)
                require(self.scope(latest_actor, latest_rooms)[1] == revision, "cursor_scope_changed")
                if events or time.monotonic() >= end or rebase:
                    payload = {"principal": actor["id"], "scope": revision, "view": target_room or "inbox", "offsets": offsets,
                               "epochs": {r: m["epoch"] for r, m in members.items()}, "rotation": rotation + 1}
                    if gaps:
                        # These offsets jumped over events this response never carried. An
                        # acknowledgment of such a cursor would claim a delivery the server
                        # did not produce, so the rooms where content was skipped are marked.
                        payload["skipped_rooms"] = sorted(set(gaps))
                    if session_id is not None:
                        payload.update(participation_session=session_id, _credential_binding=token_hash(token))
                    next_cursor = ident()
                    self.store.save_cursor(next_cursor, payload)
                    return {"events": events, "next_cursor": next_cursor, "membership_set_revision": revision, "gaps": gaps,
                            "rooms": [public_room(rooms[r], actor) for r in members] if rebase else []}
                wait(min(0.25, max(0, end - time.monotonic())))

    def room_snapshot(self, token, room_id, session_id=None):
        actor, rooms, principals = self.store.snapshot(token)
        require(room_id in rooms, "not_found", 404)
        room = rooms[room_id]
        member = authorize(actor, room, "room:read")
        members, revision = self.scope(actor, {room_id: room})
        latest_actor, latest_rooms, _ = self.store.snapshot(token)
        require(room_id in latest_rooms, "not_found", 404)
        authorize(latest_actor, latest_rooms[room_id], "room:read")
        require(self.scope(latest_actor, {room_id: latest_rooms[room_id]})[1] == revision, "cursor_scope_changed")
        if session_id is not None:
            latest = latest_rooms[room_id]
            m = authorize(latest_actor, latest, 'room:read')
            bound_session(token, {'session_id': session_id, 'generation': latest['generation'], 'membership_revision': m['revision']}, latest, m)
        cursor = ident()
        self.store.save_cursor(cursor, {"principal": actor["id"], "scope": revision, "view": room_id,
                                        "offsets": {room_id: room["last_seq"]}, "epochs": {room_id: member["epoch"]}, "rotation": 0,
                                        **({'participation_session': session_id, '_credential_binding': token_hash(token)} if session_id is not None else {})})
        return {"room": public_room(room, actor, principals), "snapshot_cursor": cursor,
                "source_event_range": {"from_seq": member["read_start_seq"], "to_seq": room["last_seq"]}}

    def interventions(self, token, room_id):
        actor, rooms, _ = self.store.snapshot(token)
        require(room_id in rooms, "not_found", 404)
        room = rooms[room_id]
        member = authorize(actor, room, "room:read", human=True)
        messages = [{"kind": m["kind"], "actor_id": m["actor_id"], "message_id": m["id"], "text": m["text"], "created_at": m["created_at"]}
                    for m in room["messages"].values() if m["actor_kind"] == "human" and m["seq"] >= member["read_start_seq"]]
        controls = [{"kind": "control", **copy.deepcopy(c)} for c in room["controls"].values() if c.get("requested_seq", 0) >= member["read_start_seq"]]
        return sorted(messages + controls, key=lambda item: item["created_at"])
