import copy
import secrets
import time
from .retention import DEFAULT_RETENTION

from .domain import AGENT_PERMISSIONS, SELF_PERMISSIONS, digest, ident, normalize_name, require, token_hash

AGENT_SCOPES = AGENT_PERMISSIONS | SELF_PERMISSIONS | {"rooms:create"}


def public_identity(principal):
    return {k: copy.deepcopy(principal.get(k)) for k in ("id", "display_name", "handle", "kind", "identity_revision", "capabilities", "model", "name_history")}


class IdentityService:
    def __init__(self, store):
        self.store = store

    def list(self, token):
        actor, _, principals = self.store.snapshot(token)
        require(actor["kind"] == "human" and "agent:manage" in actor["scopes"], "forbidden", 403)
        return [public_identity(p) for p in principals.values() if p["owner"] == actor["owner"] and p["kind"] == "agent"]

    def mutate(self, token, operation, key, body):
        require(isinstance(key, str) and 1 <= len(key) <= 128 and isinstance(body, dict), "idempotency_key_required", 422)
        require(not ({"actor_id", "role", "badge", "owner", "kind"} & body.keys()), "actor_field_forbidden", 422)
        parts = operation.split("/")
        with self.store.identity_transaction(token) as (actor, principals, credentials):
            own = parts[0] in {"capabilities", "name-proposals"}
            permission = "agent:capabilities_write" if parts[0] == "capabilities" else "agent:name_propose" if parts[0] == "name-proposals" else "credential:manage" if parts[0] == "credentials" else "agent:manage"
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
            require(len(operations) < 2000, "identity_operation_quota", 429)
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
                    response = {k: metadata[k] for k in ("credential_id", "scopes", "expires_at", "room_id")}
                    # A replay never recovers the raw secret from database storage.
                    operations[marker] = {"digest": fingerprint, "response": {**response, "token_not_recoverable": True}, "response_until": time.time() + DEFAULT_RETENTION.response_seconds}
                    return {**response, "token": raw}
            operations[marker] = {"digest": fingerprint, "response": copy.deepcopy(response), "response_until": time.time() + DEFAULT_RETENTION.response_seconds}
            return response
