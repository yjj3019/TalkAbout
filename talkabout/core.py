from __future__ import annotations

import copy
import hashlib
import json
import re
import time
import unicodedata
from .participation import Participation, bound_session

from .identity import REAL_RUNTIMES, public_connection, public_runtime_check
from .collaboration import input_fingerprint, project_manifest, project_task, validate_manifest
from .text_files import (claim_proof, filter_refs, public_task, token_hash, validate_refs)

from .domain import (AGENT_PERMISSIONS, EVENT_CAPACITY, HUMAN_PERMISSIONS, KEY_CAPACITY,
                     PLAN_DISPATCH_BYTE_RESERVE, PLAN_DISPATCH_KEY_RESERVE,
                     PLAN_DISPATCH_START_BYTE_SLACK, PLAN_DISPATCH_START_EVENT_SLACK,
                     PLAN_DISPATCH_STATE_BYTE_SLACK, ROOM_BYTE_CAPACITY, CommittedProblem, Problem, active, authorize,
                     cancel_tasks, cancel_stale_turn_requests, deliverable_artifact_kind, digest, emit, fencing, grant_reservation,
                     ident, message, new_room, reservation_outcome, resolve_uncertain, start_reservation,
                     record_task_event, replay, require, room_byte_size, save_response, schedule, participant_permissions,
                     abort_plan_dispatch as abort_plan_run, queue_ready_plan_nodes,
                     _task_requires_input_protocol, _check_task_required_inputs,
                     OUTCOME_RETRY_REASON_COMPLETED)


MEMBERSHIP_HWM_LIMIT = 128
MEMBERSHIP_ID_MAX = 2 ** 31 - 1

WORK_PLAN_ID = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,63}\Z')


def public_plan_run(run):
    """Project a run without owner plan text (title/instruction stay private)."""
    return {"id": run["id"], "state": run["state"], "plan_revision": run["plan_revision"],
            "generation": run["generation"], "started_at": run["started_at"],
            "finished_at": run["finished_at"], "abort_reason": run["abort_reason"],
            "nodes": [{key: copy.deepcopy(node.get(key)) for key in
                       ("id", "assignee", "write_scope", "state", "task_id", "failure")}
                      for node in run["nodes"]],
            "reservation_cap": run.get("reservation_cap"), "reservations_granted": run.get("reservations_granted"),
            "write_scope_enforced": False, "automatic_provider_call": False,
            "plan_text_visible_to_assignees": True, "evidence": "worker_reported_unverified"}


def validate_work_plan(nodes, room):
    """Validate inert owner draft metadata, not an execution or permission grant."""
    require(isinstance(nodes, list) and len(nodes) <= 16, 'invalid_work_plan', 422)
    ids, dependencies = set(), {}
    for node in nodes:
        fields = {'id', 'title', 'instruction', 'assignee', 'write_scope', 'depends_on'}
        require(isinstance(node, dict) and set(node) in (fields, fields | {'required_predecessors'}), 'invalid_work_plan', 422)
        node_id = node['id']
        require(isinstance(node_id, str) and WORK_PLAN_ID.fullmatch(node_id) and node_id not in ids, 'invalid_work_plan', 422)
        ids.add(node_id)
        for field, limit in (('title', 200), ('instruction', 2000)):
            value = node[field]
            require(isinstance(value, str) and 1 <= len(value) <= limit and bool(value.strip())
                    and all(not unicodedata.category(c).startswith('C') or c in '\n\t' for c in value), 'invalid_work_plan', 422)
        require(isinstance(node['write_scope'], str) and node['write_scope'] in ('analysis', 'owner_approved_workspace'), 'invalid_work_plan', 422)
        assignee = node['assignee']
        require(isinstance(assignee, str) and (member := room['members'].get(assignee))
                and member.get('kind') == 'agent' and member.get('status') == 'active', 'invalid_work_plan_assignee', 422)
        deps = node['depends_on']
        require(isinstance(deps, list) and len(deps) <= 15 and all(isinstance(d, str) and WORK_PLAN_ID.fullmatch(d) for d in deps)
                and len(deps) == len(set(deps)), 'invalid_work_plan', 422)
        if 'required_predecessors' in node:
            required = node['required_predecessors']
            require(isinstance(required, list) and len(required) <= 15
                    and all(isinstance(d, str) and d in deps for d in required)
                    and len(required) == len(set(required)), 'invalid_work_plan', 422)
        dependencies[node_id] = deps
    require(all(d in ids for deps in dependencies.values() for d in deps), 'invalid_work_plan_dependency', 422)
    remaining = {node_id: set(deps) for node_id, deps in dependencies.items()}
    while remaining:
        ready = {node_id for node_id, deps in remaining.items() if not deps}
        require(ready, 'work_plan_cycle', 422)
        remaining = {node_id: deps - ready for node_id, deps in remaining.items() if node_id not in ready}
    return copy.deepcopy(nodes)


def work_plan_authorize(actor, room):
    member = authorize(actor, room, 'room:control', human=True)
    authorize(actor, room, 'room:read', human=True)
    require(actor.get('owner') == room['owner'], 'forbidden', 403)
    return member


def validate_work_evidence(result):
    """Validate bounded worker assertions; these counters are not verified execution."""
    if 'work_evidence' not in result:
        return None
    value = result['work_evidence']
    fields = {'version', 'command_items', 'file_change_items', 'failed_items'}
    require(isinstance(value, dict) and set(value) == fields, 'invalid_work_evidence', 422)
    require(type(value['version']) is int and value['version'] == 1,
            'invalid_work_evidence', 422)
    counts = (value['command_items'], value['file_change_items'], value['failed_items'])
    require(all(type(n) is int and 0 <= n <= 128 for n in counts), 'invalid_work_evidence', 422)
    total = counts[0] + counts[1]
    require(total <= 128 and counts[2] <= total, 'invalid_work_evidence', 422)
    return {**value, 'source': 'agent_reported_unverified'}


def validate_execution_receipt(receipt, connection):
    """Bind a reported provider session to the authenticated runtime.

    This is a worker assertion that can be corroborated against the provider's
    own transcript/audit log; it is not proof that a model call occurred.
    """
    require(isinstance(receipt, dict) and {'runtime', 'session_id'} <= set(receipt)
            and set(receipt) <= {'runtime', 'session_id', 'usage'}, 'invalid_execution_receipt', 422)
    runtime = receipt['runtime']
    expected = (connection or {}).get('runtime')
    require(isinstance(runtime, str) and 1 <= len(runtime) <= 32
            and (expected is None or runtime == expected), 'invalid_execution_receipt', 422)
    session = receipt['session_id']
    require(isinstance(session, str) and 1 <= len(session) <= 256
            and session == session.strip() and session.isprintable(), 'invalid_execution_receipt', 422)
    validated = {'runtime': runtime, 'session_id': session}
    usage = receipt.get('usage')
    if usage is not None:
        require(isinstance(usage, dict) and set(usage) <= {'input_tokens', 'output_tokens'}
                and all(type(usage[key]) is int and 0 <= usage[key] <= 10 ** 9 for key in usage),
                'invalid_execution_receipt', 422)
        if usage:
            validated['usage'] = dict(usage)
    return validated


def validate_required_manifest(room, actor, principals, task, manifest):
    """Check reported full-range references, not provider receipt or comprehension."""
    _check_task_required_inputs(room, task, room['members'].get(actor['id']))
    require(manifest is not None, 'input_unavailable', 409)
    validated = validate_manifest(manifest, public_room(room, actor, principals), task)
    plan = task['_plan']
    for dep in plan['required_predecessors']:
        binding = next(b for b in plan['dependencies'] if b['node_id'] == dep)
        refs = [s for s in validated['sources'] if s.get('kind') == 'message'
                and s.get('message_id') == binding['message_id']]
        require(len(refs) == 1 and refs[0]['utf8_start'] == 0
                and refs[0]['utf8_end'] == refs[0]['total_utf8_bytes']
                and refs[0]['truncated'] is False, 'input_unavailable', 409)
    return validated


def public_room(room, actor, principals=None):
    viewer_member = authorize(actor, room, "room:read")
    tasks = []
    for raw_task in room["tasks"].values():
        if actor["kind"] != "human" and raw_task["assignee"] != actor["id"]:
            continue
        task = public_task(raw_task)
        plan_node = raw_task.get('_plan')
        if actor['kind'] == 'agent' and raw_task['assignee'] == actor['id'] and _task_requires_input_protocol(room, raw_task):
            task['input_protocol_required'] = True
        if (actor["kind"] == "agent" and raw_task["assignee"] == actor["id"]
                and raw_task.get('membership_revision') == viewer_member['revision']
                and isinstance(plan_node, dict)):
            # Owner-authored plan text is exposed only to the assignee on its own task,
            # and only while the viewer still holds the membership revision that created
            # it. A revision is monotonic for the same principal across re-invites and
            # permission changes, so it binds the projection to the task's epoch.
            # Missing/mismatched revisions fail closed (no plan_node).
            task['plan_node'] = {key: copy.deepcopy(plan_node.get(key)) for key in
                                 ('run_id', 'node_id', 'title', 'instruction', 'write_scope')}
            # Disclose only IDs already visible to this assignee, never hidden IDs
            # or the number of missing/legacy results.
            visible_ids = []
            for binding in plan_node.get('dependencies', []):
                msg = room['messages'].get(binding.get('message_id')) if isinstance(binding, dict) else None
                if msg and msg['seq'] >= viewer_member['read_start_seq']:
                    visible_ids.append(msg['id'])
            task['plan_node']['predecessor_message_ids'] = visible_ids
            if _task_requires_input_protocol(room, raw_task):
                task['input_protocol_required'] = True
                try:
                    # Projection must be all-or-nothing under current bindings and visibility.
                    check_task = {**raw_task, 'input_protocol': 'required_predecessors_v1',
                                  'execution_protocol': 'server_start_v1'}
                    _check_task_required_inputs(room, check_task, viewer_member)
                    required = plan_node['required_predecessors']
                    task['plan_node']['required_predecessor_message_ids'] = [
                        next(b['message_id'] for b in plan_node['dependencies'] if b['node_id'] == dep)
                        for dep in required]
                except (Problem, KeyError, StopIteration, TypeError):
                    task['plan_node']['required_predecessor_message_ids'] = None
        tasks.append(task)
    recorded = room.get("task_events", {})
    for task in tasks:
        task["events"] = copy.deepcopy(recorded.get(task["id"], []))[-12:]
    for task in tasks:
        if 'attachment_refs' in task:task['attachment_refs']=filter_refs(actor,room,viewer_member,task['attachment_refs'])
    messages=[]
    for original in sorted(room['messages'].values(),key=lambda m:m['seq']):
        if original['seq']<viewer_member['read_start_seq']:continue
        msg=copy.deepcopy(original)
        # A reply may point at a parent this viewer cannot see (the viewer joined or
        # reset after the parent); keep the reply text, drop only the identifier.
        parent=room['messages'].get(msg.get('reply_to'))
        if msg.get('reply_to') and (parent is None or parent['seq']<viewer_member['read_start_seq']):
            msg['reply_to']=None
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
    artifacts = [{**copy.deepcopy(a), "versions": [copy.deepcopy(v) for v in a["versions"] if v["seq"] >= viewer_member["read_start_seq"]]}
                 for a in room["artifacts"].values() if any(v["seq"] >= viewer_member["read_start_seq"] for v in a["versions"])]
    for artifact in artifacts:
        if artifact.get("adopted") not in {version["id"] for version in artifact["versions"]}:
            artifact["adopted"] = None
    tasks = [project_task(task, messages, artifacts) for task in tasks]
    for artifact in artifacts:
        for version in artifact["versions"]:
            references = version.get("input_references")
            if not references:
                continue
            # Redact recorded references at the same boundary as the text they describe.
            allowed_files = filter_refs(actor, room, viewer_member,
                                        [ref["ref"] for ref in references.get("sources", [])
                                         if ref.get("kind") == "file_excerpt"])
            version["input_references"] = project_manifest(references, messages, artifacts, allowed_files)
            version["sources"] = [source["source_id"] for source in version["input_references"]["sources"]]
    return {k: copy.deepcopy(room[k]) for k in ("id", "topic", "mode", "state", "generation", "room_revision", "limits", "last_seq")} | {
        "deliverable": room.get("deliverable"), "members": members,
        "messages": messages, "tasks": tasks,
        "artifacts": artifacts,
        "membership_revision": viewer_member["revision"],
        "mode_status": room.get("mode_status", "running"), "mode_progress": copy.deepcopy(room.get("mode_progress", {})),
        "dispatch_held": room.get("dispatch_held", False),
        "pending_controls": sum(c['state'] == 'pending' and c.get('requested_seq', 0) >= viewer_member['read_start_seq']
                                for c in room['controls'].values()) if actor['kind'] == 'human' else None,
        "handover": copy.deepcopy(room.get("handover")),
        "deadline": room.get("deadline"), "started_at": room.get("started_at"),
        "effective_permissions": sorted(set(actor["scopes"]) & set(viewer_member["permissions"])),
        "can_resolve_uncertain": (actor["kind"] == "human" and actor.get("owner") == room["owner"]
                                  and {"room:read", "room:control"} <= set(actor["scopes"])
                                  and "room:control" in viewer_member["permissions"]),
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
    def __init__(self, store, text_files=None, *, enable_agent_invites=False):
        self.store = store
        self.text_files = text_files
        self.enable_agent_invites = enable_agent_invites is True

    def _worker_ready(self, principal, room_id, now, required=None, credential_rows=None):
        connection = principal.get("connection", {})
        binding = connection.get("_credential_binding")
        scopes = required if required is not None else participant_permissions('bounded_rounds')
        if credential_rows is not None:
            # Batch path: the caller already fetched every candidate credential in
            # one query instead of one query per candidate.
            state = credential_rows.get(binding) if binding else None
            credential_ok = bool(state and state["principal_id"] == principal["id"] and not state["revoked"]
                                 and state["expires_at"] > time.time()
                                 and set(scopes) <= set(state["scopes"])
                                 and state["room_id"] in {None, room_id})
        else:
            credential_ok = bool(binding and self.store.credential_active(binding, principal["id"], room_id, scopes))
        if not (binding and credential_ok and connection.get("state") in {"idle", "running"}
                and connection.get("room_id") in {None, room_id}
                and 0 <= now - connection.get("last_seen_at", 0) <= 75):
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

    @staticmethod
    def _membership_history_int(value):
        # bool is an int subclass, so require an exact int to keep the tombstone
        # well-typed; overflow/corruption fails closed instead of wrapping.
        require(type(value) is int and 0 < value <= MEMBERSHIP_ID_MAX,
                'invalid_membership_history', 409)
        return value

    def _next_membership_identity(self, room, principal_id, epoch, revision):
        """New rows must outrank any tombstoned identity for the same principal."""
        if '_membership_hwm' not in room:
            # Legacy room: the field itself is absent. An explicit null is corrupt.
            return epoch, revision
        history = room['_membership_hwm']
        require(isinstance(history, dict), 'invalid_membership_history', 409)
        if principal_id not in history:
            return epoch, revision
        entry = history[principal_id]
        require(isinstance(entry, dict) and set(entry) == {'epoch', 'revision'},
                'invalid_membership_history', 409)
        high_epoch = self._membership_history_int(entry['epoch'])
        high_revision = self._membership_history_int(entry['revision'])
        epoch = max(epoch, high_epoch + 1)
        revision = max(revision, high_revision + 1)
        require(epoch <= MEMBERSHIP_ID_MAX and revision <= MEMBERSHIP_ID_MAX,
                'membership_history_exhausted', 429)
        return epoch, revision

    @staticmethod
    def _issuer_credential(marker, issuer):
        """issuer() returns id-keyed memory rows and principal_id-keyed PG rows."""
        binding = (marker or {}).get('credential_binding')
        return issuer(binding) if binding else None

    def _agent_invite_chain_live(self, room, member, principals, credential):
        """Flag-independent check of the original delegated grant chain.

        Cleanup uses this to decide staleness even while the feature is off: a
        flag alone must never make a live invitation look stale.
        """
        marker = member.get('_agent_invite')
        if not marker:
            return False
        inviter_id = marker.get('inviter_id')
        inviter_member = room['members'].get(inviter_id) or {}
        inviter = principals.get(inviter_id) or {}
        policy = room.get('_agent_invite_policies', {}).get(inviter_id) or {}
        credential = credential or {}
        credential_principal = credential.get('principal_id') or credential.get('id')
        return bool(
            policy.get('active') and policy.get('expires_at', 0) > time.time()
            and policy.get('generation') == room.get('generation')
            and marker.get('policy_revision') == policy.get('revision')
            and inviter_member.get('status') == 'active'
            and inviter_member.get('epoch') == marker.get('inviter_epoch')
            and inviter_member.get('revision') == marker.get('inviter_revision')
            and inviter.get('kind') == 'agent'
            and credential_principal == inviter_id
            and credential.get('revoked') is False
            and credential.get('expires_at', 0) > time.time()
            and {'room:read', 'room:invite_agents'} <= set(credential.get('scopes') or ())
            and credential.get('room_id') == room['id'])

    def _agent_invite_visible(self, room, member, principals, credentials):
        """Fail-closed visibility for agent-origin pending rows.

        A pending row is listed only while its grant chain still holds: the
        inviter membership, policy, generation and topic come from the same
        snapshot; the inviter credential is then read in a separate batch query,
        so a revoke racing that read may only be reflected on the next call (no
        stronger guarantee is implied). When any check fails the row is hidden so
        a retired or stale invitation cannot keep exposing the topic. Invitee's
        own ACL is unchanged here; human-origin pending rows are unaffected.
        """
        marker = member.get('_agent_invite')
        if not marker:
            return True
        if not self.enable_agent_invites:
            return False
        return self._agent_invite_chain_live(room, member, principals,
                                             credentials.get(marker.get('credential_binding')))

    def invitations(self, token):
        # The listing only needs room/membership state; events are never read.
        actor, rooms, principals = self.store.snapshot(token, include_events=False)
        require("agent:self_read" in actor["scopes"] or actor["kind"] == "human", "forbidden", 403)
        pending = [(rid, r) for rid, r in rooms.items()
                   if actor["id"] in r["members"] and r["members"][actor["id"]]["status"] == "pending"
                   and (not actor.get("room_id") or actor["room_id"] == rid)]
        bindings = {r["members"][actor["id"]].get("_agent_invite", {}).get("credential_binding")
                    for _, r in pending if r["members"][actor["id"]].get("_agent_invite")}
        # Batch the credential lookup: one query, no nested transaction/connection.
        credentials = self.store.credentials_active(bindings) if bindings else {}
        return [{"room_id": rid, "membership_id": actor["id"], "topic": r["topic"], "mode": r["mode"],
                 "membership_revision": r["members"][actor["id"]]["revision"],
                 "notice": "Private by default; authorized human room members can read; special operator access is disabled"}
                for rid, r in pending
                if self._agent_invite_visible(r, r["members"][actor["id"]], principals, credentials)]

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
            epoch, revision = self._next_membership_identity(room, actor['id'], 1, 1)
            member = {"status": "active", "kind": "human", "revision": revision, "epoch": epoch, "permissions": sorted(HUMAN_PERMISSIONS & set(actor["scopes"])), "read_start_seq": 1, "greeted": False}
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

    def room_fence(self, token, room_id, task_id=None):
        """Minimal re-check projection for workers: room revisions plus one task lease.

        A worker that just read attachment excerpts only needs to re-confirm consent
        and fencing. Fetching the full room projection for that check meant messages,
        artifacts and task events (up to ~124 KB) for a handful of fields; this
        payload stays around 1 KB regardless of room size.
        """
        with self.store.read_transaction(room_id, token, include_events=False) as (actor, room):
            # snapshot() previously restricted rooms to the actor's workspace.
            require(room['owner'] == actor['owner'], 'not_found', 404)
            member = authorize(actor, room, "room:read")
            fence = {"room_id": room_id, "state": room["state"], "generation": room["generation"],
                     "membership_revision": member["revision"], "last_seq": room["last_seq"], "task": None}
            if task_id is not None:
                task = room["tasks"].get(task_id)
                # Match public_room's task visibility: agents may see only their own tasks.
                if task and (actor["kind"] == "human" or task.get("assignee") == actor["id"]):
                    fence["task"] = {k: copy.deepcopy(task.get(k)) for k in (
                        "id", "state", "assignee", "attempt", "generation", "membership_revision",
                        "lease_expires_at", "deadline")}
            return fence

    def memory_preview(self, token, room_id):
        """Return a human-requested, non-persistent preview of visible message excerpts.

        This does not create memories, call models, read attachments or grant
        permission to send any content to a runtime.
        """
        from .memory_candidates import memory_candidates
        with self.store.read_transaction(room_id, token, include_events=False) as (actor, room):
            require(actor.get('owner') == room.get('owner'), 'not_found', 404)
            authorize(actor, room, 'room:read', human=True)
            member = authorize(actor, room, 'room:control', human=True)
            floor = member['read_start_seq']
            visible = [message for message in room['messages'].values() if message['seq'] >= floor]
            # Agent contributions can reproduce attachment excerpts without carrying
            # attachment_refs themselves. Exclude all agent messages in this first
            # stage; even human text may quote sensitive material, so no content
            # safety or complete provenance detection is claimed.
            human = [message for message in visible if message.get('actor_kind') == 'human']
            eligible = [message for message in human if not message.get('attachment_refs')]
            result = memory_candidates(eligible)
            result['room_id'] = room_id
            result['snapshot_seq'] = room['last_seq']
            result['excluded_agent_messages'] = len(visible) - len(human)
            result['excluded_attachment_messages'] = len(human) - len(eligible)
            return result

    def work_plan(self, token, room_id):
        with self.store.read_transaction(room_id, token) as (actor, room):
            member = work_plan_authorize(actor, room)
            plan = room.get('_work_plan')
            if plan is None:
                return {'revision': 0, 'nodes': []}
            require(plan.get('_author') == actor['id'] and plan.get('_epoch') == member['epoch'], 'not_found', 404)
            return {'revision': plan['revision'], 'nodes': copy.deepcopy(plan['nodes'])}

    def replace_work_plan(self, token, room_id, key, body):
        with self.store.transaction(room_id, token) as (actor, room):
            member = work_plan_authorize(actor, room)
            current = room.get('_work_plan')
            if current is not None:
                require(current.get('_author') == actor['id'] and current.get('_epoch') == member['epoch'], 'not_found', 404)
            require(isinstance(body, dict), 'invalid_work_plan', 422)
            try:
                previous, lookup, fingerprint = replay(room, actor, 'work-plan', key, body)
            except UnicodeEncodeError:
                raise Problem('invalid_work_plan', 422) from None
            if previous is not None:
                return copy.deepcopy(previous)
            require(room['state'] in {'preparing', 'active', 'paused'}, 'room_closed')
            plan_dispatch = room.get('_plan_dispatch')
            require(not (plan_dispatch and plan_dispatch.get('state') == 'running'),
                    'plan_dispatch_active', 409)
            require(set(body) == {'expected_revision', 'nodes'} and type(body['expected_revision']) is int
                    and body['expected_revision'] >= 0, 'invalid_work_plan', 422)
            current = room.get('_work_plan', {'revision': 0, 'nodes': []})
            require(body['expected_revision'] == current['revision'], 'work_plan_revision_conflict')
            nodes = validate_work_plan(body['nodes'], room)
            revision = current['revision'] + 1
            room['_work_plan'] = {'revision': revision, 'nodes': nodes,
                                  '_author': actor['id'], '_epoch': member['epoch']}
            # Never cache the confidential plan or its text in the generic replay ring.
            return save_response(room, lookup, fingerprint, {'revision': revision}, member['epoch'])

    def collaboration_proposals(self, token, room_id):
        """Owner-only explicit read; proposals are untrusted and never execute work."""
        with self.store.read_transaction(room_id, token) as (actor, room):
            member = work_plan_authorize(actor, room)
            rows = room.get('_collaboration_proposals', [])
            visible = [copy.deepcopy(row) for row in rows
                       if row['owner_id'] == actor['id'] and row['owner_epoch'] == member['epoch']]
            for row in visible:
                author = room['members'].get(row['author_id']) or {}
                target = room['members'].get(row['target_id']) or {}
                row['stale'] = (row['generation'] != room['generation']
                                or author.get('status') != 'active' or author.get('epoch') != row['author_epoch']
                                or author.get('revision') != row['author_revision']
                                or target.get('status') != 'active' or target.get('epoch') != row['target_epoch']
                                or target.get('revision') != row['target_revision'])
            return {'proposals': visible}

    def create_collaboration_proposal(self, token, room_id, key, body):
        fields = {'task_id', 'attempt', 'generation', 'membership_revision', 'target_id',
                  'kind', 'summary', 'requested_output'}
        require(isinstance(body, dict) and fields <= set(body) <= fields | {'done_when'},
                'invalid_collaboration_proposal', 422)
        if 'done_when' in body:
            from .proposal_done_when import valid_done_when
            require(valid_done_when(body['done_when']), 'invalid_collaboration_proposal', 422)
        require(all(type(body[name]) is int and body[name] > 0 for name in
                    ('attempt', 'generation', 'membership_revision')),
                'invalid_collaboration_proposal', 422)
        require(all(isinstance(body[name], str) and 1 <= len(body[name]) <= limit
                    for name, limit in (('task_id', 64), ('target_id', 128),
                                        ('summary', 1000), ('requested_output', 1000)))
                and all(body[name].strip() and not any(unicodedata.category(ch).startswith('C') and ch not in '\n\r\t'
                                                       for ch in body[name])
                        for name in ('summary', 'requested_output'))
                and isinstance(body['kind'], str) and body['kind'] in {'question', 'review', 'information'},
                'invalid_collaboration_proposal', 422)
        try:
            encoded = json.dumps(body, ensure_ascii=False).encode('utf-8')
        except (UnicodeEncodeError, ValueError):
            raise Problem('invalid_collaboration_proposal', 422) from None
        require(len(encoded) <= 8192, 'invalid_collaboration_proposal', 422)
        with self.store.transaction(room_id, token) as (actor, room):
            member = authorize(actor, room, 'task:complete')
            require(actor['kind'] == 'agent', 'agent_required', 403)
            previous, lookup, fingerprint = replay(room, actor, 'collaboration-proposal', key, body)
            if previous is not None:
                return copy.deepcopy(previous)
            task = room['tasks'].get(body['task_id'])
            fencing(room, member, task, actor, body, time.time())
            target = room['members'].get(body['target_id'])
            require(target and target.get('status') == 'active' and target.get('kind') == 'agent'
                    and body['target_id'] != actor['id'], 'invalid_collaboration_target', 422)
            owner_plan = room.get('_work_plan') or {}
            owner_id = owner_plan.get('_author')
            owner = room['members'].get(owner_id) if owner_id else None
            require(owner and owner_plan.get('_epoch') == owner.get('epoch')
                    and owner.get('status') == 'active' and owner.get('kind') == 'human'
                    and {'room:read', 'room:control'} <= set(owner.get('permissions') or ()),
                    'collaboration_owner_unavailable', 409)
            rows = room.setdefault('_collaboration_proposals', [])
            require(len(rows) < 32, 'collaboration_proposal_capacity', 429)
            require(body['attempt'] not in task.get('_collaboration_proposal_attempts', [])
                    and not any(row['author_id'] == actor['id'] and row['task_id'] == body['task_id']
                                and row['attempt'] == body['attempt'] for row in rows),
                    'collaboration_proposal_exists', 409)
            attempts = task.setdefault('_collaboration_proposal_attempts', [])
            require(len(attempts) < 8, 'collaboration_proposal_capacity', 429)
            attempts.append(body['attempt'])
            row = {'id': ident(), 'revision': 1, 'state': 'proposed', 'author_id': actor['id'],
                   'author_epoch': member['epoch'], 'author_revision': member['revision'],
                   'owner_id': owner_id, 'owner_epoch': owner['epoch'],
                   'task_id': body['task_id'], 'attempt': body['attempt'], 'generation': body['generation'],
                   'target_id': body['target_id'], 'target_epoch': target['epoch'],
                   'target_revision': target['revision'], 'kind': body['kind'], 'summary': body['summary'],
                   'requested_output': body['requested_output'], 'created_at': time.time()}
            if 'done_when' in body:
                row['done_when'] = copy.deepcopy(body['done_when'])
            rows.append(row)
            return save_response(room, lookup, fingerprint, {'id': row['id'], 'revision': 1,
                                 'state': 'proposed', 'execution_started': False}, member['epoch'])

    def decide_collaboration_proposal(self, token, room_id, key, proposal_id, body):
        require(isinstance(body, dict) and set(body) == {'expected_revision', 'decision'}
                and type(body['expected_revision']) is int and body['expected_revision'] > 0
                and isinstance(body['decision'], str) and body['decision'] in {'approve', 'reject'}, 'invalid_collaboration_decision', 422)
        with self.store.transaction(room_id, token) as (actor, room):
            member = work_plan_authorize(actor, room)
            previous, lookup, fingerprint = replay(room, actor, 'collaboration-decision:' + proposal_id, key, body)
            if previous is not None:
                return copy.deepcopy(previous)
            row = next((item for item in room.get('_collaboration_proposals', [])
                        if item['id'] == proposal_id and item['owner_id'] == actor['id']
                        and item['owner_epoch'] == member['epoch']), None)
            require(row, 'not_found', 404)
            author = room['members'].get(row['author_id']) or {}
            target = room['members'].get(row['target_id']) or {}
            require(row['state'] == 'proposed' and row['revision'] == body['expected_revision']
                    and row['generation'] == room['generation']
                    and author.get('status') == 'active' and author.get('epoch') == row['author_epoch']
                    and author.get('revision') == row['author_revision']
                    and target.get('status') == 'active' and target.get('epoch') == row['target_epoch']
                    and target.get('revision') == row['target_revision'], 'collaboration_proposal_conflict', 409)
            row['state'] = 'approved' if body['decision'] == 'approve' else 'rejected'
            row['revision'] += 1
            return save_response(room, lookup, fingerprint, {'id': row['id'], 'revision': row['revision'],
                                 'state': row['state'], 'execution_started': False}, member['epoch'])

    def apply_collaboration_proposal(self, token, room_id, key, proposal_id, body):
        """Append a human-written node to a private draft; never dispatch it."""
        fields = {'expected_proposal_revision', 'expected_plan_revision', 'node', 'acknowledge_untrusted'}
        require(isinstance(body, dict) and set(body) == fields
                and type(body['expected_proposal_revision']) is int and body['expected_proposal_revision'] > 0
                and type(body['expected_plan_revision']) is int and body['expected_plan_revision'] >= 0
                and body['acknowledge_untrusted'] is True and isinstance(body['node'], dict),
                'invalid_collaboration_application', 422)
        with self.store.transaction(room_id, token) as (actor, room):
            member = work_plan_authorize(actor, room)
            try:
                previous, lookup, fingerprint = replay(room, actor, 'collaboration-apply:' + proposal_id, key, body)
            except UnicodeEncodeError:
                raise Problem('invalid_collaboration_application', 422) from None
            if previous is not None:
                return copy.deepcopy(previous)
            row = next ( (item for item in room.get('_collaboration_proposals', [])
                          if item['id'] == proposal_id and item['owner_id'] == actor['id']
                          and item['owner_epoch'] == member['epoch']), None)
            require(row, 'not_found', 404)
            author = room['members'].get(row['author_id']) or {}
            target = room['members'].get(row['target_id']) or {}
            require(row['state'] == 'approved' and row['revision'] == body['expected_proposal_revision']
                    and row['generation'] == room['generation']
                    and author.get('status') == 'active' and author.get('epoch') == row['author_epoch']
                    and author.get('revision') == row['author_revision']
                    and target.get('status') == 'active' and target.get('epoch') == row['target_epoch']
                    and target.get('revision') == row['target_revision'], 'collaboration_proposal_conflict', 409)
            require(room['state'] in {'preparing', 'active', 'paused'}, 'room_closed')
            require((room.get('_plan_dispatch') or {}).get('state') != 'running', 'plan_dispatch_active', 409)
            plan = room.get('_work_plan')
            require(plan and plan.get('_author') == actor['id'] and plan.get('_epoch') == member['epoch'],
                    'not_found', 404)
            require(plan['revision'] == body['expected_plan_revision'], 'work_plan_revision_conflict', 409)
            nodes = validate_work_plan(plan['nodes'] + [body['node']], room)
            plan['nodes'] = nodes
            plan['revision'] += 1
            row['state'] = 'applied'
            row['revision'] += 1
            receipt = {'id': row['id'], 'revision': row['revision'], 'state': 'applied',
                       'plan_revision': plan['revision'], 'execution_started': False}
            return save_response(room, lookup, fingerprint, receipt, member['epoch'])

    def plan_dispatch(self, token, room_id):
        with self.store.read_transaction(room_id, token) as (actor, room):
            member = work_plan_authorize(actor, room)
            run = room.get('_plan_dispatch')
            if run is None:
                return {'run': None}
            plan = room.get('_work_plan')
            require(run.get('epoch') == member['epoch'] and plan is not None
                    and plan.get('_author') == actor['id'] and plan.get('_epoch') == member['epoch'],
                    'not_found', 404)
            return {'run': public_plan_run(run)}

    def plan_handoff(self, token, room_id):
        from .handoff import project_handoff
        with self.store.read_transaction(room_id, token) as (actor, room):
            member = work_plan_authorize(actor, room)
            run = room.get('_plan_dispatch')
            if run is not None:
                plan = room.get('_work_plan')
                require(run.get('epoch') == member['epoch'] and plan is not None
                        and plan.get('_author') == actor['id'] and plan.get('_epoch') == member['epoch'],
                        'not_found', 404)
            return project_handoff(room)

    def dispatch_work_plan(self, token, room_id, key, body):
        fields = {'expected_plan_revision', 'expected_generation', 'acknowledge_plan_dispatch',
                  'acknowledge_plan_disclosure', 'acknowledge_write_scope_unenforced'}
        require(isinstance(body, dict) and set(body) == fields
                and type(body.get('expected_plan_revision')) is int and body['expected_plan_revision'] >= 0
                and type(body.get('expected_generation')) is int
                and all(body.get(name) is True for name in
                        ('acknowledge_plan_dispatch', 'acknowledge_plan_disclosure',
                         'acknowledge_write_scope_unenforced')),
                'invalid_plan_dispatch_fields', 422)
        _, _, principals = self.store.snapshot(token)
        with self.store.transaction(room_id, token, include_credential_reader=True) as (actor, room, credential_reader):
            member = work_plan_authorize(actor, room)
            previous, lookup, fingerprint = replay(room, actor, 'plan-dispatch', key, body)
            if previous is not None:
                return copy.deepcopy(previous)
            active(room)
            # Start stricter than the running bounds: this transaction itself adds one
            # key+response, one started event + <=16 queued events, and <=384 KiB
            # (16 nodes x 2000-char x 4-byte instructions duplicated in run and tasks).
            require(len(room['events']) <= EVENT_CAPACITY - PLAN_DISPATCH_START_EVENT_SLACK,
                    'event_capacity', 429)
            require(len(room.get('keys', {})) <= KEY_CAPACITY - PLAN_DISPATCH_KEY_RESERVE - 1,
                    'idempotency_capacity', 429)
            require(room_byte_size(room) <= ROOM_BYTE_CAPACITY - PLAN_DISPATCH_BYTE_RESERVE
                    - PLAN_DISPATCH_STATE_BYTE_SLACK - PLAN_DISPATCH_START_BYTE_SLACK,
                    'room_byte_capacity', 413)
            require(not room.get('dispatch_held'), 'dispatch_held', 409)
            plan = room.get('_work_plan')
            # A plan from another author or an earlier membership epoch is not runnable.
            require(plan and plan.get('_author') == actor['id'] and plan.get('_epoch') == member['epoch'],
                    'plan_missing', 404)
            require(plan['nodes'], 'plan_dispatch_empty', 422)
            require(body['expected_plan_revision'] == plan['revision'], 'work_plan_revision_conflict', 409)
            require(body['expected_generation'] == room['generation'], 'stale_context', 409)
            current = room.get('_plan_dispatch')
            require(not (current and current.get('state') == 'running'), 'plan_dispatch_active', 409)
            # An ambiguous older start cannot be followed by a distinct start of the
            # same plan revision, even across reloads or clients. Replay above remains
            # safe; a human must explicitly save a new revision before a repeat.
            require(not (current and current.get('epoch') == member['epoch']
                         and current.get('plan_revision') == plan['revision']),
                    'plan_revision_already_dispatched', 409)
            cancel_stale_turn_requests(room)
            require(not any(task['state'] in {'queued', 'leased'} for task in room['tasks'].values())
                    and not room.get('turn_requests'), 'room_busy', 409)
            now = time.time()
            required = participant_permissions(room['mode'])
            for node in plan['nodes']:
                assignee = room['members'].get(node['assignee'])
                require(assignee and assignee.get('kind') == 'agent' and assignee.get('status') == 'active'
                        and required <= set(assignee.get('permissions') or ()), 'plan_assignee_required', 422)
            distinct = sorted({node['assignee'] for node in plan['nodes']})
            bindings = [((principals.get(pid) or {}).get('connection') or {}).get('_credential_binding')
                        for pid in distinct]
            credential_rows = credential_reader(bindings)
            require(all(self._worker_ready(principals.get(pid) or {}, room_id, now, required, credential_rows)
                        for pid in distinct), 'worker_connection_required', 409)
            require(len(room['tasks']) + len(plan['nodes']) <= 256, 'task_capacity', 429)
            nodes = [{'id': node['id'], 'title': node['title'], 'instruction': node['instruction'],
                      'assignee': node['assignee'], 'write_scope': node['write_scope'],
                      'depends_on': copy.deepcopy(node['depends_on']),
                      **({'required_predecessors': copy.deepcopy(node['required_predecessors'])}
                         if 'required_predecessors' in node else {}), 'state': 'pending',
                      'task_id': None, 'failure': None} for node in plan['nodes']]
            run = {'id': ident(), 'plan_revision': plan['revision'], 'generation': room['generation'],
                   'epoch': member['epoch'], 'state': 'running', 'started_at': now,
                   'finished_at': None, 'abort_reason': None, 'nodes': nodes,
                   'reservation_cap': 16, 'reservations_granted': 0}
            room['_plan_dispatch'] = run
            # Only runs created under this contract tag subsequently queued tasks.
            room['_server_start_plan_run'] = run['id']
            # The start event precedes the root task.queued events so consumers see
            # the run before any of its tasks.
            emit(room, 'plan.dispatch_started', run_id=run['id'], node_count=len(nodes))
            queue_ready_plan_nodes(room, run, now)
            queued = sum(node['state'] == 'queued' for node in nodes)
            # Only the public projection is cached: the private run/task/plan text must
            # never end up in the generic replay ring.
            response = {'run': public_plan_run(run), 'tasks_queued': queued}
            return save_response(room, lookup, fingerprint, copy.deepcopy(response), member['epoch'])

    def abort_plan_dispatch(self, token, room_id, key, body):
        require(isinstance(body, dict) and set(body) == {'run_id', 'acknowledge_abort'}
                and isinstance(body.get('run_id'), str) and 1 <= len(body['run_id']) <= 64
                and body.get('acknowledge_abort') is True, 'invalid_plan_abort_fields', 422)
        with self.store.transaction(room_id, token) as (actor, room):
            member = work_plan_authorize(actor, room)
            previous, lookup, fingerprint = replay(room, actor, 'plan-dispatch-abort', key, body)
            if previous is not None:
                return copy.deepcopy(previous)
            run = room.get('_plan_dispatch')
            require(run and run.get('state') == 'running', 'plan_dispatch_not_running', 409)
            plan = room.get('_work_plan')
            require(run.get('epoch') == member['epoch'] and plan is not None
                    and plan.get('_author') == actor['id'] and plan.get('_epoch') == member['epoch'],
                    'not_found', 404)
            require(body['run_id'] == run['id'], 'plan_dispatch_conflict', 409)
            abort_plan_run(room, 'aborted', time.time())
            response = {'run': public_plan_run(run)}
            return save_response(room, lookup, fingerprint, copy.deepcopy(response), member['epoch'])

    def tick(self, token, room_id):
        _, _, principals = self.store.snapshot(token)
        with self.store.transaction(room_id, token) as (actor, room):
            authorize(actor, room, "room:read")
            schedule(room, principals)

    def mutation(self, token, room_id, operation, key, body):
        _, _, principals = self.store.snapshot(token)
        with self.store.transaction(room_id, token, include_credential_reader=True) as (actor, room, credential_reader):
            bits = operation.split("/")
            target = bits[1] if len(bits) > 1 else None
            verb = bits[-1]
            resolving = bits[0] == "tasks" and len(bits) == 3 and verb == "resolve-uncertain"
            if bits[0] == "tasks":
                if resolving:
                    permission = "room:control"
                elif len(bits) > 2 and bits[2] == "reservations":
                    # A reservation is part of claiming work; its outcome is part of doing it.
                    permission = "task:claim" if len(bits) == 3 else "task:complete"
                else:
                    permission = "task:claim" if verb in {"start", "input-unavailable"} else "task:" + verb
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
            human = bits[0] in {"messages", "memberships", "controls"} or verb == "adoptions" or resolving
            member = authorize(actor, room, permission, human)
            if bits[0] == 'artifacts':
                # Artifact snapshots contain prior contributions; contribution alone
                # must never grant visibility into them, including on replay.
                authorize(actor, room, 'room:read')
            if resolving:
                # Same ownership rule as the private work-plan draft: only the room owner resolves.
                authorize(actor, room, "room:read", human=True)
                require(actor.get("owner") == room["owner"], "forbidden", 403)
            if bits[0] == 'artifacts' and actor['kind'] == 'agent' and member.get('_participation'):
                require('session_id' in body, 'session_required')
            reconciling_outcome = bits[0] == 'tasks' and len(bits) == 5 and bits[2] == 'reservations' and verb == 'outcome'
            if bits[0] in {'tasks', 'artifacts'} and 'session_id' in body and not reconciling_outcome:
                bound_session(token, body, room, member)
            if bits[0] == "tasks" and verb == "complete":
                authorize(actor, room, "room:speak")
                completion_task = room['tasks'].get(target)
                if completion_task and completion_task.get('assignee') == actor['id']:
                    if completion_task.get('_requires_server_start'):
                        require(completion_task.get('execution_protocol') == 'server_start_v1',
                                'execution_protocol_required', 409)
                        require(any(item['attempt'] == completion_task['attempt']
                                    and item['state'] in {'started', 'completed'}
                                    and ('reservation_id' not in body or item['id'] == body['reservation_id'])
                                    for item in completion_task.get('_reservations', [])),
                                'execution_not_started', 409)
            try:
                previous, lookup, fingerprint = replay(room, actor, operation, key, body)
            except UnicodeEncodeError:
                # A surrogate in the resolution body cannot be hashed or stored; refuse it
                # before any state changes. Other operations keep their existing behavior.
                if not resolving:
                    raise
                raise Problem("invalid_resolution", 422) from None
            if bits[0] == 'tasks' and len(bits) == 3 and verb in {'skip', 'input-unavailable'}:
                skip_task = room['tasks'].get(target)
                if (skip_task and body.get('attempt') == skip_task.get('attempt')
                        and any(item.get('attempt') == skip_task.get('attempt')
                                and (item.get('state') in {'started', 'completed'}
                                     or (item.get('state') == 'reserved'
                                         and not (skip_task.get('execution_protocol') == 'server_start_v1'
                                                  and item.get('execution_protocol') == 'server_start_v1')))
                                for item in skip_task.get('_reservations', []))):
                    # Replay validation takes precedence, but a valid cached response
                    # must not bypass the current execution fence.
                    fencing(room, member, skip_task, actor, body, time.time())
                    raise Problem('execution_outcome_uncertain', 409)
            execution_operation = bits[0] == "tasks" and len(bits) > 2 and (bits[2] == "reservations" or verb == "start")
            if previous is not None and not execution_operation:
                if bits[0] == 'tasks' and verb == 'claim':
                    replay_task = room['tasks'].get(target)
                    if replay_task and _task_requires_input_protocol(room, replay_task):
                        require(body.get('input_protocol') == 'required_predecessors_v1'
                                and body.get('execution_protocol') == 'server_start_v1',
                                'input_protocol_required', 409)
                        _check_task_required_inputs(room, replay_task, member)
                if bits[0] == 'tasks':
                    from .response_projection import project_task_response
                    previous = project_task_response(room, actor, previous)
                elif bits[0] == 'artifacts':
                    from .response_projection import artifact_replay_metadata, project_artifact_response
                    # Old rows may contain full snapshots; replace them only after
                    # identity/epoch checks and successful current projection.
                    projected = project_artifact_response(room, actor, previous)
                    if not previous.get('_artifact_snapshot_v1'):
                        room['keys'][lookup]['response'] = artifact_replay_metadata(previous)
                    previous = projected
                if self.text_files and bits[0]=='tasks' and verb=='claim' and 'lease_id' in previous:
                    return {**previous,'lease_proof':claim_proof(token,room_id,previous,key)}
                return previous
            now = time.time()
            if bits[0] == "turn-requests":
                active(room)
                plan_run = room.get('_plan_dispatch')
                require(not (plan_run and plan_run.get('state') == 'running'),
                        'plan_dispatch_active', 409)
                require(actor["kind"] == "agent" and room["mode"] == "free_discussion", "unsupported_turn_request", 422)
                require(set(body) == {"generation", "membership_revision"}
                        and type(body['generation']) is int and type(body['membership_revision']) is int
                        and body["generation"] == room["generation"] and body["membership_revision"] == member["revision"], "stale_context")
                cancel_stale_turn_requests(room)
                requests = room.setdefault("turn_requests", [])
                require(not any(entry['principal_id'] == actor['id'] for entry in requests)
                        and not any(t["assignee"] == actor["id"] and t["state"] in {"queued", "leased"} for t in room["tasks"].values()), "turn_already_pending")
                require(len(requests) < 16 and room.get("request_count", 0) < 64, "turn_request_capacity", 429)
                requests.append({'principal_id': actor['id'], 'generation': room['generation'],
                                 'membership_epoch': member['epoch'], 'membership_revision': member['revision']})
                room["request_count"] = room.get("request_count", 0) + 1
                emit(room, "turn.requested", actor_id=actor["id"])
                schedule(room, principals)
                response = {"accepted": True}
            elif resolving:
                task = room["tasks"].get(target)
                require(task, "not_found", 404)
                result = resolve_uncertain(room, task, actor, body, now)
                response = {"task": copy.deepcopy(task), **result}
                # The run must reflect this saved mutation in the same transaction.
                schedule(room, principals, now)
            elif bits[0] == "tasks" and len(bits) > 2 and bits[2] == "reservations":
                task = room["tasks"].get(target)
                require(task and task["assignee"] == actor["id"], "not_found", 404)
                if len(bits) == 3:
                    # A task whose call outcome is unknown never receives a new call.
                    require(task["state"] != "uncertain", "task_outcome_uncertain", 409)
                    require(body.get("membership_revision") == member["revision"], "stale_attempt", 409)
                    response = grant_reservation(room, task, actor, body, now)
                else:
                    response = reservation_outcome(room, task, actor, bits[3], body, now)
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
                        saved = save_response(room, lookup, fingerprint, response, member["epoch"])
                        from .response_projection import project_task_response
                        return project_task_response(room, actor, saved)
                if verb == "claim":
                    active(room)
                    require(not room.get('dispatch_held'), 'dispatch_held')
                    require(task["state"] == "queued" and now < task["deadline"], "task_unavailable")
                    require(all(type(body.get(field)) is int for field in ("generation", "membership_revision"))
                            and body["generation"] == task["generation"] == room["generation"]
                            and body["membership_revision"] == task["membership_revision"] == member["revision"], "stale_attempt")
                    required_protocol = _task_requires_input_protocol(room, task)
                    if required_protocol:
                        # Strict claim contract: only the protocol fields and the
                        # optional negotiated capability are accepted here.
                        require(isinstance(body, dict) and set(body) <=
                                {'generation', 'membership_revision', 'execution_protocol',
                                 'input_protocol', 'required_predecessors_limit', 'session_id'},
                                'invalid_claim_fields', 422)
                    limit = body.get('required_predecessors_limit')
                    require(limit is None or (type(limit) is int and 1 <= limit <= 15),
                            'invalid_required_predecessors_limit', 422)
                    protocol = body.get("execution_protocol")
                    require(protocol is None or (isinstance(protocol, str) and protocol == "server_start_v1"),
                            "invalid_execution_protocol", 422)
                    require(not task.get('_requires_server_start') or protocol == 'server_start_v1',
                            'execution_protocol_required', 409)
                    input_protocol = body.get('input_protocol')
                    require(input_protocol is None or input_protocol == 'required_predecessors_v1',
                            'invalid_input_protocol', 422)
                    if required_protocol:
                        require(input_protocol == 'required_predecessors_v1' and protocol == 'server_start_v1',
                                'input_protocol_required', 409)
                    if input_protocol is not None:
                        task['input_protocol'] = input_protocol
                    if required_protocol:
                        task['execution_protocol'] = protocol
                        _check_task_required_inputs(room, task, member)
                        plan = task.get('_plan')
                        required = plan.get('required_predecessors') if isinstance(plan, dict) else None
                        required_count = len(required) if isinstance(required, list) else 0
                        if limit is None or limit < required_count:
                            # The worker negotiated a capability smaller than the
                            # exact contract. Fail the node before any lease so it is
                            # never handed to a worker that cannot prepare it. The
                            # recorded failure is committed before the 409 is raised.
                            task.update(state='failed', lease_expires_at=0,
                                        _failure_reason='input_unavailable')
                            record_task_event(room, target, 'failed', now)
                            schedule(room, principals, now)
                            raise CommittedProblem('required_input_unavailable', 409)
                    # A task cannot downgrade its opted-in safety contract on a retry.
                    if protocol is not None:
                        task["execution_protocol"] = protocol
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
                elif verb == 'input-unavailable' and len(bits) == 3:
                    require(isinstance(body, dict) and set(body) == {'attempt', 'generation', 'membership_revision'},
                            'invalid_input_failure', 422)
                    if task['state'] == 'leased' and now < task['lease_expires_at'] and now < task['deadline']:
                        fencing(room, member, task, actor, body, now)
                    else:
                        # A locally journaled input failure may be reported after the
                        # lease expired or the plan task was already marked expired.
                        # Accept exactly one such late report while the exact attempt
                        # fence and assignee still match; the started/reserved outcome
                        # guard above has already rejected an executed attempt.
                        require(task['assignee'] == actor['id'], 'not_found', 404)
                        require(task['state'] in {'queued', 'leased', 'expired'}
                                and all(type(body.get(field)) is int
                                        for field in ('attempt', 'generation', 'membership_revision'))
                                and body['attempt'] == task['attempt']
                                and body['generation'] == room['generation'] == task['generation']
                                and body['membership_revision'] == member['revision'] == task['membership_revision'],
                                'stale_attempt', 409)
                    require(actor['kind'] == 'agent' and task.get('purpose') == 'plan_node'
                            and _task_requires_input_protocol(room, task), 'input_failure_not_allowed', 409)
                    task.update(state='failed', lease_expires_at=0, _failure_reason='input_unavailable')
                    record_task_event(room, target, 'failed', now)
                    response = copy.deepcopy(task)
                    schedule(room, principals, now)
                elif verb == "start":
                    if _task_requires_input_protocol(room, task):
                        validate_required_manifest(room, actor, principals, task, body.get('input_manifest'))
                    response = start_reservation(room, member, task, actor, body, now)
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
                        require(not task.get('_requires_server_start') or
                                task.get('execution_protocol') == 'server_start_v1',
                                'execution_protocol_required', 409)
                        if task.get("execution_protocol") == "server_start_v1":
                            require(any(item["attempt"] == task["attempt"]
                                        and item["state"] in {"started", "completed"}
                                        and ("reservation_id" not in body or item["id"] == body["reservation_id"])
                                        for item in task.get("_reservations", [])), "execution_not_started", 409)
                        if room["mode"] == "co_creation" and task["purpose"] == "creator":
                            authorize(actor, room, "room:contribute")
                        work_evidence = validate_work_evidence(result)
                        # A worker may report what it was given. The server re-checks every
                        # reference; absence means "unverified legacy input", not a failure.
                        manifest = result.get('input_manifest')
                        inputs = (validate_required_manifest(room, actor, principals, task, manifest)
                                  if _task_requires_input_protocol(room, task) else
                                  validate_manifest(manifest, public_room(room, actor, principals), task))
                        receipt_entry = None
                        reported_receipt = result.get('execution_receipt')
                        if reported_receipt is not None:
                            # A provider session handle makes the report corroborable
                            # against provider logs; it never proves delivery or reading.
                            connection = principals[actor['id']].get('connection') or {}
                            receipt_entry = validate_execution_receipt(reported_receipt, connection)
                            receipt_entry.update(
                                attempt=task['attempt'], reported_at=now,
                                text_sha256=hashlib.sha256(result['text'].encode('utf-8')).hexdigest())
                            receipts = task.setdefault('_execution_receipts', [])
                            require(len(receipts) < 8, 'execution_receipt_capacity', 429)
                            receipts.append(receipt_entry)
                        msg = message(room, actor, body, now, task, fingerprint)
                        if receipt_entry is not None:
                            msg['execution_receipt'] = {
                                'runtime': receipt_entry['runtime'],
                                'session_sha256': hashlib.sha256(
                                    receipt_entry['session_id'].encode('utf-8')).hexdigest(),
                                'text_sha256': receipt_entry['text_sha256'],
                                **({'usage': dict(receipt_entry['usage'])}
                                   if 'usage' in receipt_entry else {})}
                        if work_evidence is not None:
                            msg['work_evidence'] = work_evidence
                        task["input_manifest"] = inputs
                        msg["input_evidence"] = inputs["evidence"]
                        principal=principals[actor['id']];connection=principal.get('connection') or {}
                        same_credential=connection.get('_credential_binding')==token_hash(token)
                        verified=(same_credential and connection.get('runtime')!='synthetic'
                                  and self._worker_ready(principal,room_id,now,credential_rows=credential_reader([token_hash(token)])))
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
                            input_fp = input_fingerprint(inputs["sources"])
                            artifact["versions"].append({"id": ident(), "seq": room["last_seq"] + 1, "text": msg["text"], "kind": kind, "actor_id": actor["id"],
                                                         "sources": [source["source_id"] for source in inputs["sources"]],
                                                         "input_references": copy.deepcopy(inputs),
                                                         "version_revision": artifact["revision"] + 1, "ai_generated": True,
                                                         **({"input_sha256": input_fp} if input_fp else {})})
                            artifact["revision"] += 1
                            emit(room, "artifact.changed", artifact_id=aid, actor_id=actor["id"])
                            proposal = copy.deepcopy(artifact)
                            proposal["versions"] = [v for v in proposal["versions"] if v["seq"] >= member["read_start_seq"]]
                        if task.get('purpose') == 'plan_node' and isinstance(task.get('_plan'), dict):
                            task['_plan_result'] = {'run_id': task['_plan'].get('run_id'),
                                'node_id': task['_plan'].get('node_id'), 'task_id': task['id'],
                                'generation': task['generation'], 'attempt': task['attempt'],
                                'message_id': msg['id']}
                        task["state"] = "completed"
                        record_task_event(room, target, "completed", now)
                        member["greeted"] = True
                        emit(room, "task.completed", task_id=target)
                        response = {"message": msg, "task": copy.deepcopy(task),
                                    "retry_allowed": False, "retry_reason": OUTCOME_RETRY_REASON_COMPLETED}
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
                # While the feature is off, an existing inviter keeps its already-held invite
                # scope when its other participation permissions are edited; new grants stay denied.
                held = set(old.get('permissions', [])) if old else set()
                optional = ({'room:invite_agents'}
                            if self.enable_agent_invites or 'room:invite_agents' in held else set())
                permitted = ((AGENT_PERMISSIONS | optional)
                             if principals[pid]['kind'] == 'agent' else HUMAN_PERMISSIONS)
                require(perms <= permitted and "room:read" in perms, "invalid_permissions", 422)
                status = body.get("status", old["status"] if old else "pending")
                require(status in {"pending", "blocked", "left", "active"}, "invalid_status", 422)
                require(status != "active" or (old and old["status"] in {"active", "blocked"}), "invitation_required", 422)
                origin = old.get('_agent_invite') if old else None
                if origin:
                    if old['status'] == 'pending':
                        require(perms == {'room:read'}, 'pending_permission_change_forbidden', 403)
                    require(not (old['status'] == 'blocked' and status == 'active'), 'invitation_required', 422)
                    if perms - set(old['permissions']):
                        require(old['status'] == status == 'active' and body.get('acknowledge_external_execution') is True,
                                'execution_acknowledgement_required', 422)
                if old and status == old['status'] and perms == set(old['permissions']):
                    # A no-op must not invalidate task leases, session fences or
                    # pending participation receipts; never return private fields.
                    response = {name: copy.deepcopy(value) for name, value in old.items()
                                if not name.startswith('_')}
                    return save_response(room, lookup, fingerprint, response, member['epoch'])
                reset_read = not old or old["status"] != status
                epoch = (old or {}).get("epoch", 0) + (1 if reset_read else 0)
                revision = (old or {}).get("revision", 0) + 1
                epoch, revision = self._next_membership_identity(room, pid, epoch, revision)
                room["members"][pid] = {"status": status, "kind": principals[pid]["kind"], "permissions": sorted(perms), "revision": revision,
                                        "epoch": epoch, "read_start_seq": room["last_seq"] + 1 if reset_read else old["read_start_seq"], "greeted": False}
                if origin:
                    room['members'][pid]['_agent_invite'] = copy.deepcopy(origin)
                cancel_tasks(room, pid)
                emit(room, "membership.changed", principal_id=pid, actor_id=actor["id"])
                schedule(room, principals)
                response = {name: copy.deepcopy(value) for name, value in room['members'][pid].items()
                            if not name.startswith('_')}
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
                        # One credential lookup for all candidates instead of one per worker.
                        credential_rows = credential_reader(
                            (principals.get(pid, {}).get("connection") or {}).get("_credential_binding")
                            for pid, _ in candidates)
                        require(all(self._worker_ready(principals.get(pid, {}), room['id'], now, required, credential_rows)
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
                    artifact["versions"].append({"id": ident(), "seq": room["last_seq"] + 1, "text": text, "actor_id": actor["id"], "sources": body.get("sources", []), "version_revision": artifact["revision"] + 1, "ai_generated": actor["kind"] == "agent"})
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
            if execution_operation:
                # A reservation/start answer is computed fresh every time and never cached: a
                # replayed execute:true would be the double call the reservation forbids.
                return response
            if bits[0] == 'artifacts':
                from .response_projection import artifact_replay_metadata, project_artifact_response
                projected = project_artifact_response(room, actor, response)
                save_response(room, lookup, fingerprint, artifact_replay_metadata(response), member['epoch'])
                return projected
            saved=save_response(room, lookup, fingerprint, copy.deepcopy(response), member["epoch"])
            if bits[0] == 'tasks':
                from .response_projection import project_task_response
                saved = project_task_response(room, actor, saved)
            if self.text_files and bits[0]=='tasks' and verb=='claim':
                return {**saved,'lease_proof':claim_proof(token,room_id,saved,key)}
            return saved

    def purge_membership(self, token, room_id, principal_id, key, body):
        """Human-only cleanup of one truly dead agent-origin pending invitation.

        The feature flag is deliberately not consulted for staleness: the same
        transaction validates the original policy/generation/inviter/issuer chain
        and only genuinely impossible invitations can be removed. Live pending
        rows, active/blocked/left rows and human-origin rows are never purged.
        """
        require(isinstance(body, dict) and set(body) == {'expected_revision', 'acknowledge_cleanup'}
                and type(body.get('expected_revision')) is int and body['acknowledge_cleanup'] is True,
                'invalid_purge_fields', 422)
        with self.store.invitation_transaction(room_id, token) as (actor, room, principals, issuer, checks):
            actor_member = authorize(actor, room, 'room:manage_members', human=True)
            # Both the scope and the membership permission must allow reading;
            # manage_members alone is not enough for this destructive projection.
            authorize(actor, room, 'room:read', human=True)
            require(room['state'] in {'preparing', 'active', 'paused'}, 'room_closed')
            require(isinstance(principal_id, str) and 1 <= len(principal_id) <= 128, 'not_found', 404)
            target = principals.get(principal_id)
            require(target and target.get('kind') == 'agent', 'not_found', 404)
            # The target is part of the idempotency identity even though the public
            # API body stays {expected_revision, acknowledge_cleanup}; otherwise the
            # same key/body could replay A's result for a different B.
            replay_body = {'expected_revision': body['expected_revision'],
                           'acknowledge_cleanup': body['acknowledge_cleanup'],
                           'principal_id': principal_id}
            previous, lookup, fingerprint = replay(room, actor, 'memberships-purge', key, replay_body)
            if previous is not None:
                return previous
            member = room['members'].get(principal_id)
            require(member and member.get('status') == 'pending', 'purge_requires_pending', 409)
            marker = member.get('_agent_invite')
            require(marker, 'purge_human_origin_forbidden', 409)
            require(body['expected_revision'] == member['revision'], 'membership_conflict')
            credential = self._issuer_credential(marker, issuer)
            require(not self._agent_invite_chain_live(room, member, principals, credential),
                    'invitation_still_live', 409)
            # Capture the row's identity BEFORE deletion; the precommit check must
            # never re-read the removed row as if it were still pending.
            captured = {'marker': copy.deepcopy(marker),
                        'epoch': self._membership_history_int(member.get('epoch')),
                        'revision': self._membership_history_int(member.get('revision'))}
            if '_membership_hwm' not in room:
                history = {}
            else:
                history = room['_membership_hwm']
                require(isinstance(history, dict), 'invalid_membership_history', 409)
            if principal_id not in history:
                require(len(history) < MEMBERSHIP_HWM_LIMIT, 'membership_history_capacity', 429)
                base_epoch = base_revision = 0
            else:
                prior = history[principal_id]
                require(isinstance(prior, dict) and set(prior) == {'epoch', 'revision'},
                        'invalid_membership_history', 409)
                base_epoch = self._membership_history_int(prior['epoch'])
                base_revision = self._membership_history_int(prior['revision'])
            high_epoch = max(base_epoch, captured['epoch'])
            high_revision = max(base_revision, captured['revision'])
            require(high_epoch < MEMBERSHIP_ID_MAX and high_revision < MEMBERSHIP_ID_MAX,
                    'membership_history_exhausted', 429)
            history[principal_id] = {'epoch': high_epoch, 'revision': high_revision}
            room['_membership_hwm'] = history
            del room['members'][principal_id]
            emit(room, 'membership.purged', principal_id=principal_id, actor_id=actor['id'],
                 previous_status='pending', previous_epoch=captured['epoch'],
                 previous_revision=captured['revision'],
                 history_epoch=high_epoch, history_revision=high_revision)
            response = {'purged': True, 'principal_id': principal_id, 'previous_status': 'pending',
                        'previous_epoch': captured['epoch'], 'previous_revision': captured['revision'],
                        'notice': 'Stale read-only invitation row removed; an explicit new invitation is required.'}
            save_response(room, lookup, fingerprint, copy.deepcopy(response), actor_member['epoch'])

            def recheck():
                # Only the captured marker/issuer chain is revalidated; the row is
                # intentionally absent, so no pending-status assertion is made.
                latest = self._issuer_credential(captured['marker'], issuer)
                require(not self._agent_invite_chain_live(
                    room, {'_agent_invite': captured['marker']}, principals, latest),
                    'invitation_still_live', 409)
            checks.append(recheck)
            return response

    def _invitation_headroom(self, room):
        require(len(room['members']) <= 14 and len(room['events']) <= 2016
                and len(room['keys']) <= 2016, 'invitation_capacity', 429)
        require(len(json.dumps(room, ensure_ascii=False).encode('utf-8')) <= 2097152 - 65536,
                'invitation_capacity', 429)

    @staticmethod
    def _public_member(member):
        return {name: copy.deepcopy(value) for name, value in member.items() if not name.startswith('_')}

    @staticmethod
    def _require_invite_credential(actor, room_id):
        """Delegated invitation actions need one explicit room-bound credential."""
        require(actor.get('kind') == 'agent'
                and {'room:read', 'room:invite_agents'} <= set(actor.get('scopes') or ())
                and actor.get('room_id') == room_id, 'invitation_credential_unbound', 403)

    def _live_invite_policy(self, room, actor, member, expected_revision=None):
        require(actor['kind'] == 'agent', 'forbidden', 403)
        self._require_invite_credential(actor, room['id'])
        authorize(actor, room, 'room:invite_agents')
        policy = room.get('_agent_invite_policies', {}).get(actor['id'])
        require(policy and policy.get('active') and policy.get('expires_at', 0) > time.time()
                and policy.get('generation') == room['generation']
                and policy.get('inviter_epoch') == member['epoch']
                and policy.get('inviter_revision') == member['revision'], 'invitation_policy_unavailable', 403)
        require(expected_revision is None or policy['revision'] == expected_revision,
                'invitation_policy_conflict', 409)
        return policy

    def invitation_policy_me(self, token, room_id):
        require(self.enable_agent_invites, 'feature_disabled', 404)
        with self.store.read_transaction(room_id, token, include_events=False) as (actor, room):
            # read_transaction is room-scoped; snapshot-style workspace filtering is explicit here.
            require(actor.get('owner') == room.get('owner'), 'not_found', 404)
            member = authorize(actor, room, 'room:invite_agents')
            policy = self._live_invite_policy(room, actor, member)
            return {'revision': policy['revision'], 'target_ids': list(policy['target_ids']),
                    'expires_at': policy['expires_at'], 'remaining': policy['max_uses'] - policy['used'],
                    'membership_epoch': member['epoch'], 'membership_revision': member['revision'],
                    'generation': room['generation']}

    def invitation_policies(self, token, room_id):
        require(self.enable_agent_invites, 'feature_disabled', 404)
        with self.store.read_transaction(room_id, token, include_events=False) as (actor, room):
            require(actor.get('owner') == room.get('owner'), 'not_found', 404)
            authorize(actor, room, 'room:manage_members', human=True)
            require('room:read' in actor['scopes'], 'forbidden', 403)
            return copy.deepcopy(list(room.get('_agent_invite_policies', {}).values()))

    def set_invitation_policy(self, token, room_id, key, body):
        require(self.enable_agent_invites, 'feature_disabled', 404)
        require(isinstance(body, dict), 'invalid_invitation_policy', 422)
        action = body.get('action')
        base = {'action', 'inviter_id', 'expected_generation', 'expected_policy_revision'}
        fields = base | {'target_ids', 'inviter_epoch', 'inviter_revision', 'expires_at', 'max_uses', 'acknowledge_topic_sharing'}
        require((action == 'revoke' and set(body) == base) or (action == 'grant' and set(body) == fields),
                'invalid_invitation_policy', 422)
        inviter_id = body.get('inviter_id')
        require(isinstance(inviter_id, str) and 1 <= len(inviter_id) <= 128
                and type(body['expected_generation']) is int and type(body['expected_policy_revision']) is int,
                'invalid_invitation_policy', 422)
        with self.store.invitation_transaction(room_id, token) as (actor, room, principals, issuer, checks):
            member = authorize(actor, room, 'room:manage_members', human=True)
            require('room:read' in actor['scopes'], 'forbidden', 403)
            require(room['state'] in {'preparing', 'active', 'paused'}, 'room_closed')
            require(body['expected_generation'] == room['generation'], 'stale_context')
            policies = room.setdefault('_agent_invite_policies', {})
            old = policies.get(inviter_id)
            previous, lookup, fingerprint = replay(room, actor, 'agent-invite-policy', key, body)
            if previous is not None:
                if action == 'revoke':
                    require(old and old['revision'] == previous['revision']
                            and old.get('active') is False, 'invitation_policy_conflict')
                    return previous
                inviter = principals.get(inviter_id) or {}
                inviter_member = room['members'].get(inviter_id) or {}
                require(old and old['revision'] == previous['revision']
                        and old.get('active') and old.get('expires_at', 0) > time.time()
                        and old.get('generation') == room['generation']
                        and inviter.get('kind') == 'agent'
                        and inviter_member.get('status') == 'active'
                        and 'room:invite_agents' in inviter_member.get('permissions', [])
                        and inviter_member.get('epoch') == old.get('inviter_epoch')
                        and inviter_member.get('revision') == old.get('inviter_revision'),
                        'invitation_policy_unavailable', 403)
                return previous
            revision = old['revision'] if old else 0
            require(body['expected_policy_revision'] == revision, 'invitation_policy_conflict')
            if action == 'revoke':
                require(old, 'invitation_policy_unavailable', 404)
                policy = {**old, 'revision': revision + 1, 'active': False}
            else:
                target_ids = body['target_ids']
                require(isinstance(target_ids, list) and 1 <= len(target_ids) <= 4
                        and all(isinstance(pid, str) and 1 <= len(pid) <= 128 for pid in target_ids)
                        and len(set(target_ids)) == len(target_ids) and inviter_id not in target_ids
                        and type(body['inviter_epoch']) is int and type(body['inviter_revision']) is int
                        and type(body['expires_at']) is int and time.time() < body['expires_at'] <= time.time() + 3600
                        and type(body['max_uses']) is int and 1 <= body['max_uses'] <= min(4, len(target_ids))
                        and body['acknowledge_topic_sharing'] is True, 'invalid_invitation_policy', 422)
                inviter = principals.get(inviter_id)
                inviter_member = room['members'].get(inviter_id)
                require(inviter and inviter.get('kind') == 'agent' and inviter_member and inviter_member['status'] == 'active'
                        and 'room:invite_agents' in inviter_member['permissions']
                        and inviter_member['epoch'] == body['inviter_epoch']
                        and inviter_member['revision'] == body['inviter_revision'], 'invitation_policy_unavailable', 404)
                require(all(pid in principals and principals[pid].get('kind') == 'agent' for pid in target_ids),
                        'invitation_target_unavailable', 404)
                require(old or len(policies) < 16, 'invitation_capacity', 429)
                policy = {'inviter_id': inviter_id, 'inviter_epoch': body['inviter_epoch'],
                          'inviter_revision': body['inviter_revision'], 'revision': revision + 1,
                          'generation': room['generation'],
                          'target_ids': list(target_ids), 'expires_at': body['expires_at'],
                          'max_uses': body['max_uses'], 'used': 0, 'active': True}
            policies[inviter_id] = policy
            emit(room, 'agent_invite_policy.changed', actor_id=actor['id'], inviter_id=inviter_id)
            response = {name: policy[name] for name in ('inviter_id', 'revision', 'active', 'expires_at', 'max_uses', 'used')}
            save_response(room, lookup, fingerprint, response, member['epoch'])
            self._invitation_headroom(room)
            if action == 'grant':
                checks.append(lambda: require(policy['expires_at'] > time.time(), 'invitation_policy_unavailable', 403))
            return response

    def invite_agent(self, token, room_id, key, body):
        require(self.enable_agent_invites, 'feature_disabled', 404)
        fields = {'target_id', 'expected_generation', 'expected_membership_epoch',
                  'expected_membership_revision', 'expected_policy_revision'}
        require(isinstance(body, dict) and set(body) == fields and isinstance(body['target_id'], str)
                and 1 <= len(body['target_id']) <= 128
                and all(type(body[name]) is int for name in fields - {'target_id'}), 'invalid_invitation', 422)
        with self.store.invitation_transaction(room_id, token) as (actor, room, principals, issuer, checks):
            member = authorize(actor, room, 'room:invite_agents')
            # Delegated invitations require an explicitly room-bound credential;
            # a workspace-wide token must not carry this grant.
            self._require_invite_credential(actor, room_id)
            require(room['state'] in {'preparing', 'active'}, 'room_not_accepting')
            require(body['expected_generation'] == room['generation']
                    and body['expected_membership_epoch'] == member['epoch']
                    and body['expected_membership_revision'] == member['revision'], 'stale_context')
            policy = self._live_invite_policy(room, actor, member, body['expected_policy_revision'])
            target_id = body['target_id']
            require(target_id in policy['target_ids'], 'invitation_target_unavailable', 404)
            previous, lookup, fingerprint = replay(room, actor, 'agent-invitations', key, body)
            if previous is not None:
                return previous
            require(policy['used'] < policy['max_uses'], 'invitation_capacity', 429)
            target = principals.get(target_id)
            require(target and target.get('kind') == 'agent' and target_id != actor['id']
                    and target_id not in room['members'], 'invitation_target_unavailable', 404)
            epoch, revision = self._next_membership_identity(room, target_id, 1, 1)
            room['members'][target_id] = {
                'status': 'pending', 'kind': 'agent', 'permissions': ['room:read'],
                'revision': revision, 'epoch': epoch, 'read_start_seq': room['last_seq'] + 1, 'greeted': False,
                '_agent_invite': {'inviter_id': actor['id'], 'inviter_epoch': member['epoch'],
                                  'inviter_revision': member['revision'], 'policy_revision': policy['revision'],
                                  'credential_binding': token_hash(token)},
            }
            policy['used'] += 1
            emit(room, 'membership.changed', principal_id=target_id, actor_id=actor['id'])
            # This ACK only records a read-only pending row; acceptance is a
            # separate confirmation by the invited principal.
            response = {'accepted': True, 'status': 'pending',
                        'notice': 'Read-only pending invitation recorded; the invitee must still accept it explicitly.'}
            save_response(room, lookup, fingerprint, response, member['epoch'])
            self._invitation_headroom(room)
            checks.append(lambda: self._live_invite_policy(room, actor, member, policy['revision']))
            return response

    def _require_live_agent_invite(self, room, principals, issuer, marker):
        """Deny an agent-origin acceptance unless every delegated grant is live.

        Checks the human-approved policy revision and generation, the inviter's
        active membership epoch/revision, and the inviter's room-bound
        credential (read + invite scope, unrevoked, unexpired). Failure denies
        only the acceptance; the pending row is preserved.
        """
        require(self.enable_agent_invites, 'feature_disabled', 404)
        inviter_id = marker.get('inviter_id')
        policy = room.get('_agent_invite_policies', {}).get(inviter_id) or {}
        require(policy.get('active') and policy.get('expires_at', 0) > time.time()
                and policy.get('generation') == room.get('generation')
                and marker.get('policy_revision') == policy.get('revision'),
                'invitation_policy_unavailable', 403)
        inviter_member = room['members'].get(inviter_id) or {}
        require(inviter_member.get('status') == 'active'
                and inviter_member.get('epoch') == marker.get('inviter_epoch')
                and inviter_member.get('revision') == marker.get('inviter_revision'),
                'invitation_policy_unavailable', 403)
        inviter = principals.get(inviter_id) or {}
        require(inviter.get('kind') == 'agent', 'invitation_policy_unavailable', 403)
        credential = issuer(marker.get('credential_binding'))
        require(credential and credential.get('id') == inviter_id
                and credential.get('revoked') is False
                and credential.get('expires_at', 0) > time.time()
                and {'room:read', 'room:invite_agents'} <= set(credential.get('scopes') or ())
                and credential.get('room_id') == room['id'],
                'invitation_policy_unavailable', 403)

    def accept(self, token, room_id, key, body):
        with self.store.invitation_transaction(room_id, token) as (actor, room, principals, issuer, checks):
            member = room["members"].get(actor["id"])
            require(member and member["status"] in {"pending", "active"}, "invitation_unavailable")
            require(not actor.get("room_id") or actor["room_id"] == room_id, "forbidden", 403)
            marker = member.get("_agent_invite")
            if marker:
                # The invitee's own ACL is checked before any replay, so a
                # credential that lost read scope cannot resurrect a stored
                # acceptance response; policy revalidation is only needed for
                # the pending transition below.
                require(actor.get("kind") == "agent"
                        and "room:read" in set(actor.get("scopes") or ())
                        and actor.get("room_id") in (None, room_id),
                        "invitation_unauthorised", 403)
            previous, lookup, fingerprint = replay(room, actor, "accept", key, body)
            if previous is not None:
                return previous
            require(member["status"] == "pending", "invitation_unavailable")
            require(room["state"] in {"preparing", "active"}, "room_not_accepting")
            require(body.get("expected_revision") == member["revision"] and body.get("acknowledge_private") is True, "invitation_confirmation_required", 422)
            recheck = None
            if marker:
                self._require_live_agent_invite(room, principals, issuer, marker)
                # Capture grants before the private marker drops the binding;
                # the pre-commit recheck must still verify the issuer.
                binding = marker.get("credential_binding")
                inviter_id = marker.get("inviter_id")
                policy_revision = marker.get("policy_revision")
                inviter_epoch = marker.get("inviter_epoch")
                inviter_revision = marker.get("inviter_revision")
                member["status"] = "active"
                member["revision"] += 1
                member["read_start_seq"] = room["last_seq"] + 1
                provenance = {name: copy.deepcopy(value) for name, value in marker.items()
                              if name != "credential_binding"}
                provenance["accepted_generation"] = room["generation"]
                provenance["accepted_at"] = time.time()
                member["_agent_invite"] = provenance

                def recheck(binding=binding, inviter_id=inviter_id, policy_revision=policy_revision,
                            inviter_epoch=inviter_epoch, inviter_revision=inviter_revision):
                    policy = room.get("_agent_invite_policies", {}).get(inviter_id) or {}
                    require(policy.get("active") and policy.get("expires_at", 0) > time.time()
                            and policy.get("generation") == room.get("generation")
                            and policy.get("revision") == policy_revision,
                            "invitation_policy_unavailable", 403)
                    inviter_member = room["members"].get(inviter_id) or {}
                    require(inviter_member.get("status") == "active"
                            and inviter_member.get("epoch") == inviter_epoch
                            and inviter_member.get("revision") == inviter_revision,
                            "invitation_policy_unavailable", 403)
                    credential = issuer(binding)
                    require(credential and credential.get("id") == inviter_id
                            and credential.get("revoked") is False
                            and credential.get("expires_at", 0) > time.time()
                            and {"room:read", "room:invite_agents"} <= set(credential.get("scopes") or ())
                            and credential.get("room_id") == room["id"],
                            "invitation_policy_unavailable", 403)
                checks.append(recheck)
            else:
                member["status"] = "active"
                member["revision"] += 1
                member["read_start_seq"] = room["last_seq"] + 1
            emit(room, "membership.accepted", principal_id=actor["id"])
            schedule(room, principals)
            return save_response(room, lookup, fingerprint, self._public_member(member), member["epoch"])

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
                # The polling projection does not need event history: at up to
                # 2,049 payloads per room it dominated the idle cost. Only the events
                # after each cursor offset are fetched, and only when there are any.
                actor, rooms, _ = self.store.snapshot(token, include_events=False)
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
                    floor = rooms[rid].get("first_retained_seq", 1)
                    if offsets[rid] < floor - 1:
                        require(rebase, "cursor_gap", 410)
                        offsets[rid] = rooms[rid]["last_seq"]
                        gaps.append(rid)
                    selected = self.store.room_events(rid, offsets[rid], 16)
                    # Plan audit payloads contain private draft metadata.  Keep only
                    # the generic event envelope for every reader, including the
                    # owner: historical events cannot prove the current plan's
                    # author and membership epoch. Never mutate stored events.
                    events.extend(
                        {k: event[k] for k in ("event_id", "room_id", "seq", "type", "created_at") if k in event}
                        if event.get("type", "").startswith("plan.") else event
                        for event in selected
                    )
                    if selected:
                        offsets[rid] = selected[-1]["seq"]
                if events or rebase:
                    # Re-check the authorization scope only when this response actually
                    # carries content or a fresh projection. An idle poll would otherwise
                    # pay for a second full snapshot every 250 ms.
                    latest_actor, latest_rooms, _ = self.store.snapshot(token, include_events=False)
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

    def room_diagnostics(self, token, room_id):
        """Read-only waiting-reason projection: no mutation, no event, no model call."""
        actor, rooms, _ = self.store.snapshot(token)
        require(room_id in rooms, "not_found", 404)
        room = rooms[room_id]
        member = authorize(actor, room, "room:read", human=True)
        from .observation import room_diagnostics
        return room_diagnostics(room, membership_revision=member["revision"])
