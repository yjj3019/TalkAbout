from __future__ import annotations

import copy
import time

from .domain import (AGENT_PERMISSIONS, HUMAN_PERMISSIONS, Problem, active, authorize,
                     cancel_tasks, digest, emit, fencing, ident, message, new_room,
                     replay, require, save_response, schedule)


def public_room(room, actor, principals=None):
    viewer_member = authorize(actor, room, "room:read")
    tasks = [copy.deepcopy(t) for t in room["tasks"].values() if actor["kind"] == "human" or t["assignee"] == actor["id"]]
    members = copy.deepcopy(room["members"])
    if principals:
        for pid, listed_member in members.items():
            principal = principals.get(pid, {})
            listed_member.update({k: principal.get(k) for k in ("display_name", "handle", "identity_revision")})
    return {k: copy.deepcopy(room[k]) for k in ("id", "topic", "mode", "state", "generation", "room_revision", "limits", "last_seq")} | {"members": members,
        "messages": [m for m in room["messages"].values() if m["seq"] >= viewer_member["read_start_seq"]], "tasks": tasks,
        "artifacts": [{**a, "versions": [v for v in a["versions"] if v["seq"] >= viewer_member["read_start_seq"]]}
                      for a in room["artifacts"].values() if any(v["seq"] >= viewer_member["read_start_seq"] for v in a["versions"])],
        "membership_revision": viewer_member["revision"],
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
        ranges = {"messages": (1, 200), "attempts": (1, 3), "lease": (5, 60), "task_seconds": (5, 180)}
        require(all(type(limits[k]) is int and low <= limits[k] <= high for k, (low, high) in ranges.items()) and limits["lease"] <= limits["task_seconds"], "invalid_limits", 422)
        parameters = {"limits": limits}
    elif action == "pin":
        message_id = parameters.get("message_id")
        require(set(parameters) == {"message_id"} and isinstance(message_id, str) and message_id in room["messages"] and room["messages"][message_id]["seq"] >= member["read_start_seq"], "not_found", 404)
    else:
        require(not parameters, "invalid_parameters", 422)
    return parameters


class Core:
    def __init__(self, store):
        self.store = store

    def me(self, token):
        actor = self.store.auth(token)
        require("agent:self_read" in actor["scopes"] or actor["kind"] == "human", "forbidden", 403)
        return {k: actor.get(k) for k in ("id", "display_name", "handle", "kind", "identity_revision", "scopes", "capabilities", "model")} | {"agent_id": actor["id"] if actor["kind"] == "agent" else None}

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
        # Deterministic create identity makes lost responses replayable without returning a new room.
        room_id = digest([actor["id"], "create", key])[:32]
        if room_id in rooms:
            require(rooms[room_id]["create_digest"] == digest(body), "idempotency_conflict")
            return public_room(rooms[room_id], actor, principals)
        room = new_room(actor, room_id, body.get("topic"), body.get("mode", "bounded_rounds"))
        room["create_digest"] = digest(body)
        schedule(room, principals)
        room = self.store.create(token, room)
        return public_room(room, actor, principals)

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
            else:
                raise Problem("not_found", 404)
            human = bits[0] in {"messages", "memberships", "controls"} or verb == "adoptions"
            member = authorize(actor, room, permission, human)
            if bits[0] == "tasks" and verb == "complete":
                authorize(actor, room, "room:speak")
            previous, lookup, fingerprint = replay(room, actor, operation, key, body)
            if previous is not None:
                return previous
            now = time.time()
            if bits[0] == "tasks":
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
                        response = {"message": room["messages"][prior["id"]], "task": copy.deepcopy(task)}
                        return save_response(room, lookup, fingerprint, response, member["epoch"])
                if verb == "claim":
                    active(room)
                    require(task["state"] == "queued" and now < task["deadline"], "task_unavailable")
                    require(body.get("generation") == task["generation"] == room["generation"] and body.get("membership_revision") == task["membership_revision"] == member["revision"], "stale_attempt")
                    task["attempt"] += 1
                    task["state"] = "leased"
                    task["lease_expires_at"] = min(task["deadline"], now + room["limits"]["lease"])
                    emit(room, "task.claimed", task_id=target, attempt=task["attempt"])
                    response = copy.deepcopy(task)
                else:
                    require(verb in {"complete", "heartbeat", "skip"}, "not_found", 404)
                    fencing(room, member, task, actor, body, now)
                    if verb == "heartbeat":
                        task["lease_expires_at"] = min(task["deadline"], now + room["limits"]["lease"])
                        response = copy.deepcopy(task)
                    elif verb == "complete":
                        msg = message(room, actor, body, now, task, fingerprint)
                        task["state"] = "completed"
                        member["greeted"] = True
                        emit(room, "task.completed", task_id=target)
                        response = {"message": msg, "task": copy.deepcopy(task)}
                    else:
                        task["state"] = "skipped"
                        emit(room, "task.skipped", task_id=target, actor_id=actor["id"])
                        response = copy.deepcopy(task)
                    schedule(room, principals, now)
            elif bits[0] == "messages":
                require(room["state"] in {"active", "paused"}, "room_closed")
                response = message(room, actor, body, now, fingerprint=fingerprint)
                # Human messages never create extra recursive turns.
            elif bits[0] == "memberships":
                require(room["state"] in {"active", "paused"}, "room_closed")
                pid = body.get("principal_id") if len(bits) == 1 else target
                require(pid in principals and principals[pid]["owner"] == actor["owner"], "not_found", 404)
                old = room["members"].get(pid)
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
                    action = body.get("action")
                    require(action in {"pause", "resume", "close", "change_topic", "change_mode", "change_limits", "pin"}, "unsupported_control", 422)
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
                    if action == "pause":
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
                    if action != "pin":
                        room["generation"] += 1
                        cancel_tasks(room)
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
                    artifact = {"id": ident(), "revision": 0, "versions": [], "adopted": None}
                    room["artifacts"][artifact["id"]] = artifact
                if verb == "adoptions":
                    require(any(v["id"] == body.get("version_id") and v["seq"] >= member["read_start_seq"] for v in artifact["versions"]), "not_found", 404)
                    artifact["adopted"] = body["version_id"]
                else:
                    require(set(body) <= {"generation", "membership_revision", "expected_revision", "text", "sources", "caption"}, "invalid_artifact_fields", 422)
                    text = body.get("text")
                    require(isinstance(text, str) and 1 <= len(text) <= 64000, "text_only", 422)
                    require(isinstance(body.get("sources", []), list) and len(body.get("sources", [])) <= 20, "invalid_sources", 422)
                    artifact["versions"].append({"id": ident(), "seq": room["last_seq"] + 1, "text": text, "actor_id": actor["id"], "sources": body.get("sources", []), "ai_generated": actor["kind"] == "agent"})
                artifact["revision"] += 1
                emit(room, "artifact.changed", artifact_id=artifact["id"], actor_id=actor["id"])
                response = copy.deepcopy(artifact)
                response["versions"] = [v for v in response["versions"] if v["seq"] >= member["read_start_seq"]]
            return save_response(room, lookup, fingerprint, copy.deepcopy(response), member["epoch"])

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
            active(room)
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

    def events(self, token, cursor=None, wait_seconds=0, rebase=False, target_room=None):
        require(0 <= wait_seconds <= 10, "invalid_wait", 422)
        end = time.monotonic() + wait_seconds
        with self.store.listener() as wait:
            while True:
                actor, rooms, _ = self.store.snapshot(token)
                if target_room:
                    require(target_room in rooms, "not_found", 404)
                    authorize(actor, rooms[target_room], "room:read")
                    rooms = {target_room: rooms[target_room]}
                members, revision = self.scope(actor, rooms)
                old = self.store.cursor(cursor) if cursor else None
                require(not cursor or old is not None or rebase, "cursor_gap", 410)
                require(not old or old["principal"] == actor["id"], "invalid_cursor", 403)
                require(not old or old.get("view", "inbox") == (target_room or "inbox"), "invalid_cursor_view")
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
                    if history and offsets[rid] < history[0]["seq"] - 1:
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
                require(self.scope(latest_actor, latest_rooms)[1] == revision, "cursor_scope_changed")
                if events or time.monotonic() >= end or rebase:
                    payload = {"principal": actor["id"], "scope": revision, "view": target_room or "inbox", "offsets": offsets,
                               "epochs": {r: m["epoch"] for r, m in members.items()}, "rotation": rotation + 1}
                    next_cursor = ident()
                    self.store.save_cursor(next_cursor, payload)
                    return {"events": events, "next_cursor": next_cursor, "membership_set_revision": revision, "gaps": gaps,
                            "rooms": [public_room(rooms[r], actor) for r in members] if rebase else []}
                wait(min(0.25, max(0, end - time.monotonic())))

    def room_snapshot(self, token, room_id):
        actor, rooms, principals = self.store.snapshot(token)
        require(room_id in rooms, "not_found", 404)
        room = rooms[room_id]
        member = authorize(actor, room, "room:read")
        members, revision = self.scope(actor, {room_id: room})
        latest_actor, latest_rooms, _ = self.store.snapshot(token)
        require(room_id in latest_rooms, "not_found", 404)
        authorize(latest_actor, latest_rooms[room_id], "room:read")
        require(self.scope(latest_actor, {room_id: latest_rooms[room_id]})[1] == revision, "cursor_scope_changed")
        cursor = ident()
        self.store.save_cursor(cursor, {"principal": actor["id"], "scope": revision, "view": room_id,
                                       "offsets": {room_id: room["last_seq"]}, "epochs": {room_id: member["epoch"]}, "rotation": 0})
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
