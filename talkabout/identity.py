import copy
import hmac
import secrets
import time
from .retention import DEFAULT_RETENTION

from .domain import AGENT_PERMISSIONS, SELF_PERMISSIONS, digest, ident, normalize_name, require, token_hash

AGENT_SCOPES = AGENT_PERMISSIONS | SELF_PERMISSIONS | {"rooms:create"}


REAL_RUNTIMES = {"codex", "claude", "grok"}
CHECK_SECONDS = 120
RECEIPT_SECONDS = 900
CHECK_START_RETENTION_SECONDS = 3600


def public_connection(principal):
    connection = principal.get("connection")
    return {k: copy.deepcopy(v) for k, v in connection.items() if not k.startswith("_")} if connection else None


def public_runtime_check(principal):
    check = principal.get("runtime_check")
    if not check:
        return None
    result = {k: copy.deepcopy(check.get(k)) for k in
              ("id", "runtime", "status", "last_checked_at", "expires_at", "verification", "error_code")}
    if result["status"] in {"pending", "passed"} and result["expires_at"] <= time.time():
        result["status"] = "expired"
    return result


def public_identity(principal):
    return {k: copy.deepcopy(principal.get(k)) for k in ("id", "display_name", "handle", "kind", "identity_revision", "capabilities", "model", "name_history")} | {
        "connection": public_connection(principal), "runtime_check": public_runtime_check(principal)}


class IdentityService:
    def __init__(self, store):
        self.store = store

    def list(self, token):
        actor, _, principals = self.store.snapshot(token)
        require(actor["kind"] == "human" and "agent:manage" in actor["scopes"], "forbidden", 403)
        return [public_identity(p) for p in principals.values() if p["owner"] == actor["owner"] and p["kind"] == "agent"]

    @staticmethod
    def _invalidate_credential(principal, credentials, credential):
        binding = next((hashed for hashed, candidate in credentials.items() if candidate is credential), None)
        check = principal.get("runtime_check")
        if check and check.get("_credential_binding") == binding:
            check.update(status="invalidated", error_code="credential_revoked")
            check.pop("_challenge", None)
        connection = principal.get("connection")
        if connection and connection.get("_credential_binding") == binding:
            connection.update(state="stopped", error_code="credential_revoked")

    def credential_metadata(self, token, agent_id):
        return self.store.credential_metadata(token, agent_id)

    def runtime_check(self, token, check_id, action, key, body):
        require(isinstance(key, str) and 1 <= len(key) <= 128 and isinstance(body, dict), "idempotency_key_required", 422)
        with self.store.identity_transaction(token) as (actor, principals, credentials):
            require(actor["kind"] == "agent" and "agent:capabilities_write" in actor["scopes"], "forbidden", 403)
            binding = token_hash(token)
            credential = credentials[binding]
            principal = principals[actor["id"]]
            now = time.time()
            check = principal.get("runtime_check")
            if action == "create":
                require(set(body) == {"runtime"} and isinstance(body.get("runtime"), str) and body["runtime"] in REAL_RUNTIMES, "invalid_runtime_check", 422)
                request_id = digest([binding, key])
                # A rolling one-hour ledger bounds storage and rate without imposing a
                # lifetime quota. Known retired keys fail closed; after pruning, clients
                # must use fresh keys (retry guarantees have a finite retention horizon).
                seen = principal.setdefault("runtime_check_requests", {})
                if isinstance(seen, list):
                    seen = {old: {"expires_at": check["expires_at"] if check and check.get("_request_id") == old and check["status"] == "pending" else 0,
                                  "retain_until": now + CHECK_START_RETENTION_SECONDS} for old in seen}
                    principal["runtime_check_requests"] = seen
                if request_id in seen:
                    require(seen[request_id]["expires_at"] > now, "idempotency_retired", 410)
                    require(check and check.get("_request_id") == request_id, "runtime_check_superseded", 409)
                    require(check["runtime"] == body["runtime"], "idempotency_conflict")
                    require(check["status"] == "pending" and check["expires_at"] > now, "runtime_check_unavailable", 409)
                else:
                    for old, record in list(seen.items()):
                        if record["retain_until"] <= now and (not check or check.get("_request_id") != old):
                            del seen[old]
                    require(len(seen) < 128, "runtime_check_quota", 429)
                    challenge = "TALKABOUT-CHECK-" + secrets.token_urlsafe(24)
                    check = {"id": ident(), "runtime": body["runtime"], "status": "pending",
                             "last_checked_at": None, "expires_at": min(now + CHECK_SECONDS, credential["expires_at"]),
                             "verification": "worker_reported_test", "error_code": None,
                             "_challenge": challenge, "_credential_binding": binding,
                             "_room_id": credential.get("room_id"), "_request_id": request_id}
                    principal["runtime_check"] = check
                    seen[request_id] = {"expires_at": check["expires_at"],
                                        "retain_until": now + CHECK_START_RETENTION_SECONDS}
                return {k: check[k] for k in ("id", "runtime", "expires_at")} | {
                    "challenge": check["_challenge"],
                    "prompt": "Runtime connection test. Respond with exactly this challenge and nothing else: " + check["_challenge"]}
            require(check and check["id"] == check_id, "not_found", 404)
            require(check["_credential_binding"] == binding and check["_room_id"] == credential.get("room_id"), "runtime_check_binding", 403)
            require(action in {"complete", "fail"}, "not_found", 404)
            fingerprint = digest([action, body])
            if check.get("_completion_key") == key:
                require(check.get("_completion_digest") == fingerprint, "idempotency_conflict")
                require(check["expires_at"] > now and check["status"] in {"passed", "failed"}, "runtime_check_unavailable", 409)
                return public_runtime_check(principal)
            require(check["status"] == "pending" and check["expires_at"] > now, "runtime_check_unavailable", 409)
            if action == "complete":
                require(set(body) <= {"response", "session_id", "runtime_version"}, "invalid_runtime_check", 422)
                require(isinstance(body.get("response"), str) and len(body["response"]) <= 4096,
                        "invalid_runtime_response", 422)
                require(isinstance(body.get("session_id"), str) and 1 <= len(body["session_id"]) <= 256
                        and body["session_id"].strip() == body["session_id"], "invalid_runtime_session", 422)
                require(isinstance(body.get("runtime_version", ""), str) and len(body.get("runtime_version", "")) <= 128,
                        "invalid_runtime_version", 422)
                passed = hmac.compare_digest(body["response"].strip().encode(), check["_challenge"].encode())
                check.update(status="passed" if passed else "failed", error_code=None if passed else "challenge_mismatch")
                check["_session_digest"] = digest(body["session_id"])
                check["_runtime_version"] = body.get("runtime_version")
            else:
                require(set(body) == {"error_code"} and isinstance(body["error_code"], str) and body["error_code"] in {"runtime_failed", "runtime_timeout", "connection_failed"}, "invalid_runtime_failure", 422)
                check.update(status="failed", error_code=body["error_code"])
            check.update(last_checked_at=now, expires_at=min(now + RECEIPT_SECONDS, credential["expires_at"]),
                         _completion_key=key, _completion_digest=fingerprint)
            check.pop("_challenge", None)
            return public_runtime_check(principal)

    def mutate(self, token, operation, key, body):
        require(isinstance(key, str) and 1 <= len(key) <= 128 and isinstance(body, dict), "idempotency_key_required", 422)
        require(not ({"actor_id", "role", "badge", "owner", "kind"} & body.keys()), "actor_field_forbidden", 422)
        parts = operation.split("/")
        with self.store.identity_transaction(token, credential_id=parts[2] if parts[0] == 'credentials' and len(parts) > 2 else None) as (actor, principals, credentials):
            own = parts[0] in {"capabilities", "connection", "name-proposals"}
            permission = "agent:capabilities_write" if parts[0] in {"capabilities", "connection"} else "agent:name_propose" if parts[0] == "name-proposals" else "credential:manage" if parts[0] == "credentials" else "agent:manage"
            require(permission in actor["scopes"] and actor["kind"] == ("agent" if own else "human"), "forbidden", 403)
            target_id = actor["id"] if own else (parts[1] if len(parts) > 1 else None)
            if target_id:
                require(target_id in principals and principals[target_id]["owner"] == actor["owner"] and principals[target_id]["kind"] == "agent", "not_found", 404)
            operator = principals[actor["id"]]
            operations = operator.setdefault("identity_operations", {})
            marker = operation + ":" + key
            fingerprint = digest(body)
            if marker in operations:
                saved = operations[marker]
                require(saved["digest"] == fingerprint, "idempotency_conflict")
                require("response" in saved and saved.get("response_until", float("inf")) > time.time(), "idempotency_retired", 410)
                return copy.deepcopy(saved["response"])
            if parts[0] != "connection": require(len(operations) < 2000, "identity_operation_quota", 429)
            if parts[0] == "register":
                require(set(body) <= {"display_name", "handle"}, "invalid_identity_fields", 422)
                name, name_key, handle = normalize_name(body.get("display_name", ""), body.get("handle", ""))
                require(not any(p["owner"] == actor["owner"] and (p["name_key"] == name_key or p["handle"] == handle) for p in principals.values()), "identity_conflict")
                target_id = ident()
                principal = {"id": target_id, "owner": actor["owner"], "kind": "agent", "display_name": name,
                             "name_key": name_key, "handle": handle, "identity_revision": 1, "name_history": []}
                principals[target_id] = principal
                response = public_identity(principal)
            elif parts[0] == "identity":
                require(set(body) <= {"display_name", "handle", "expected_revision"}, "invalid_identity_fields", 422)
                principal = principals[target_id]
                require(body.get("expected_revision") == principal["identity_revision"], "identity_conflict")
                name, name_key, handle = normalize_name(body.get("display_name", ""), body.get("handle", ""))
                require(not any(pid != target_id and p["owner"] == actor["owner"] and (p["name_key"] == name_key or p["handle"] == handle) for pid, p in principals.items()), "identity_conflict")
                principal.setdefault("name_history", []).append({"display_name": principal["display_name"], "handle": principal["handle"], "revision": principal["identity_revision"], "changed_by": actor["id"], "changed_at": time.time()})
                principal.update(display_name=name, name_key=name_key, handle=handle, identity_revision=principal["identity_revision"] + 1)
                response = public_identity(principal)
            elif parts[0] == "capabilities":
                require(set(body) <= {"http", "persistent_worker", "text", "image", "file", "model_label"}, "invalid_capabilities", 422)
                require(all(isinstance(v, bool) for k, v in body.items() if k != "model_label"), "invalid_capabilities", 422)
                require(isinstance(body.get("model_label", ""), str) and len(body.get("model_label", "")) <= 200, "invalid_model_label", 422)
                principal = principals[target_id]
                principal["capabilities"] = {k: v for k, v in body.items() if k != "model_label"}
                principal["model"] = {"label": body.get("model_label", "unknown"), "verification": "self_reported"}
                response = public_identity(principal)
            elif parts[0] == "connection":
                require(set(body) <= {"runtime", "state", "error_code"}, "invalid_connection", 422)
                require(isinstance(body.get("runtime"), str) and body["runtime"] in {"unknown", "synthetic", "codex", "claude", "grok"} and isinstance(body.get("state"), str) and body["state"] in {"idle", "running", "failed", "stopped"}, "invalid_connection", 422)
                require(body.get("error_code") is None or (isinstance(body["error_code"], str) and body["error_code"] in {"runtime_failed", "runtime_timeout", "connection_failed"}), "invalid_connection", 422)
                principal = principals[target_id]
                prior_connection = principal.get("connection", {})
                binding = token_hash(token)
                check = principal.get("runtime_check")
                if check and (check["runtime"] != body["runtime"] or check["_credential_binding"] != binding):
                    check.update(status="invalidated", error_code="connection_changed")
                    check.pop("_challenge", None)
                principal["connection"] = {"authenticated_at": prior_connection.get("authenticated_at", time.time()),
                    "last_seen_at": time.time(), "runtime": body["runtime"], "state": body["state"],
                    "room_id": actor.get("room_id"),
                    "error_code": body.get("error_code"),
                    "execution_evidence": "synthetic_test_only" if body["runtime"] == "synthetic" else "worker_reported",
                    "_credential_binding": binding, "_credential_expires_at": credentials[binding]["expires_at"]}
                response = public_connection(principal)
                # Heartbeats are replaceable observations, not an unbounded idempotency ledger.
                return response
            elif parts[0] == "name-proposals":
                require(set(body) <= {"display_name", "handle"}, "invalid_identity_fields", 422)
                name, _, handle = normalize_name(body.get("display_name", ""), body.get("handle", ""))
                response = {"id": ident(), "display_name": name, "handle": handle, "state": "pending"}
                principals[target_id].setdefault("name_proposals", []).append(response)
            else:
                require(set(body) <= {"scopes", "expires_in", "room_id"}, "invalid_credential_fields", 422)
                prior = None
                if len(parts) > 2:
                    prior = next((c for c in credentials.values() if c["id"] == target_id and c.get("credential_id") == parts[2]), None)
                    require(prior, "not_found", 404)
                if parts[-1] == "revoke":
                    prior["revoked"] = True
                    self._invalidate_credential(principals[target_id], credentials, prior)
                    response = {"credential_id": prior["credential_id"], "revoked": True}
                else:
                    requested = body.get("scopes", [])
                    require(isinstance(requested, list) and requested and all(isinstance(p, str) for p in requested), "invalid_scopes", 422)
                    scopes = set(requested)
                    require(scopes <= AGENT_SCOPES and ("rooms:create" not in scopes or "rooms:create" in actor["scopes"]), "scope_escalation", 403)
                    ttl = body.get("expires_in", 300)
                    require(type(ttl) is int and 1 <= ttl <= 3600, "invalid_expiry", 422)
                    room_id = body.get("room_id")
                    require(room_id is None or (isinstance(room_id, str) and "rooms:create" not in scopes), "invalid_room_scope", 422)
                    raw = secrets.token_urlsafe(32)
                    credential_id = ident()
                    metadata = {"id": target_id, "credential_id": credential_id, "scopes": sorted(scopes), "expires_at": time.time() + ttl, "revoked": False, "room_id": room_id}
                    credentials[token_hash(raw)] = metadata
                    if prior:
                        prior["revoked"] = True
                        self._invalidate_credential(principals[target_id], credentials, prior)
                    response = {k: metadata[k] for k in ("credential_id", "scopes", "expires_at", "room_id")}
                    # A replay never recovers the raw secret from database storage.
                    operations[marker] = {"digest": fingerprint, "response": {**response, "token_not_recoverable": True}, "response_until": time.time() + DEFAULT_RETENTION.response_seconds}
                    return {**response, "token": raw}
            operations[marker] = {"digest": fingerprint, "response": copy.deepcopy(response), "response_until": time.time() + DEFAULT_RETENTION.response_seconds}
            return response
