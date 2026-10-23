"""Allowlisted operator-trial HTTP metadata; never raw responses or identifiers."""
import json
import re
import time

_PHASES = frozenset({'new_owned_postgres_init', 'new_owned_schema_and_owner',
    'loopback_api_start', 'single_room_token_issue', 'first_worker_start',
    'reconnect_worker_start', 'cleanup'})
_METHODS = frozenset({'GET', 'POST', 'PUT', 'PATCH', 'DELETE'})
_CODES = frozenset({'setup_required', 'service_unavailable'})
_UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
_ROUTES = (
    ('session', '/api/v1/session'), ('session/logout', '/api/v1/session/logout'),
    ('agents', '/api/v1/agents'), ('rooms', '/api/v1/rooms'),
    (f'rooms/{_UUID}', '/api/v1/rooms/{room_id}'),
    (f'rooms/{_UUID}/messages', '/api/v1/rooms/{room_id}/messages'),
    (f'rooms/{_UUID}/memberships', '/api/v1/rooms/{room_id}/memberships'),
    (f'rooms/{_UUID}/controls', '/api/v1/rooms/{room_id}/controls'),
    (f'rooms/{_UUID}/controls/{_UUID}/approve', '/api/v1/rooms/{room_id}/controls/{control_id}/approve'),
    (f'agents/{_UUID}/credentials', '/api/v1/agents/{agent_id}/credentials'),
    (f'agents/{_UUID}/credentials/{_UUID}/revoke', '/api/v1/agents/{agent_id}/credentials/{credential_id}/revoke'),
)


def safe_http_failure(method, path, status, body, phase):
    """Return six bounded fields only. Unknown or malformed input is discarded.

    Callers supply status/body primitives, never headers or a request/response
    object. At most 4096 response bytes are parsed; only two exact codes survive.
    This helper performs no I/O, HTTP retry, credential or runtime operation.
    """
    template = '/unknown'
    if type(path) is str and len(path) <= 512:
        template = next((label for pattern, label in _ROUTES
                         if re.fullmatch(pattern, path)), '/unknown')
    code = 'unknown'
    if type(body) is bytes and len(body) <= 4096:
        try:
            value = json.loads(body)
            candidate = value.get('code') if type(value) is dict else None
            if type(candidate) is str and candidate in _CODES:
                code = candidate
        except (ValueError, TypeError, RecursionError):
            pass
    return {
        'status': status if type(status) is int and 400 <= status <= 599 else None,
        'method': method if type(method) is str and method in _METHODS else 'UNKNOWN',
        'path_template': template,
        'error_code': code,
        'phase': phase if type(phase) is str and phase in _PHASES else 'unknown',
        'recorded_at': int(time.time()),
    }
