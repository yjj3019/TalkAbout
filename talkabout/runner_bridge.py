"""Opt-in, origin-bound loopback handoff. Importing performs no I/O."""
import asyncio
import hmac
import inspect
import json
import math
import secrets
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

PORT = 18742
PROTOCOL = 'talkabout.runner.v1'
MAX_BODY = 8192


@dataclass
class BridgeResponse:
    status: int
    body: dict
    headers: dict


def origin(value):
    if not isinstance(value, str):
        raise ValueError('invalid_origin')
    parsed = urlsplit(value)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username
            or parsed.password or parsed.path or parsed.query or parsed.fragment
            or value != f'{parsed.scheme}://{parsed.netloc}'):
        raise ValueError('invalid_origin')
    return value


class BridgeApplication:
    def __init__(self, status_callback, connect_callback, allowed_origin, nonce=None, *, port=PORT):
        self.origin = origin(allowed_origin)
        self.status_callback, self.connect_callback = status_callback, connect_callback
        self.nonce = nonce or secrets.token_urlsafe(32)
        if not isinstance(self.nonce, str) or not 32 <= len(self.nonce) <= 256 or not self.nonce.isascii():
            raise ValueError('invalid_nonce')
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError('invalid_port')
        self.port = port

    def response(self, status, body=None, cors=True, extra=None):
        headers = {'Cache-Control': 'no-store', 'Content-Type': 'application/json',
                   'X-Content-Type-Options': 'nosniff', 'Vary': 'Origin'}
        if cors:
            headers['Access-Control-Allow-Origin'] = self.origin
        headers.update(extra or {})
        return BridgeResponse(status, body or {}, headers)

    async def handle(self, method, path, headers, body=b''):
        headers = {str(key).lower(): value for key, value in headers.items()}
        if headers.get('origin') != self.origin:
            return self.response(403, {'code': 'bridge_origin_denied'}, False)
        if headers.get('host') != f'127.0.0.1:{self.port}':
            return self.response(403, {'code': 'bridge_host_denied'})
        if path not in {'/status', '/connect'}:
            return self.response(404, {'code': 'bridge_rpc_unknown'})
        if method == 'OPTIONS':
            requested = {value.strip().lower() for value in
                         headers.get('access-control-request-headers', '').split(',') if value.strip()}
            if (headers.get('access-control-request-method') not in {'GET', 'POST'}
                    or not requested <= {'content-type', 'x-talkabout-bridge-nonce'}):
                return self.response(403, {'code': 'bridge_preflight_denied'})
            extra = {'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                     'Access-Control-Allow-Headers': 'Content-Type, X-TalkAbout-Bridge-Nonce'}
            if headers.get('access-control-request-private-network') == 'true':
                extra['Access-Control-Allow-Private-Network'] = 'true'
            return self.response(204, extra=extra)
        if method == 'GET' and path == '/status':
            return self.response(200, {'protocol': PROTOCOL, 'nonce': self.nonce})
        if method != 'POST':
            return self.response(405, {'code': 'bridge_method_denied'})
        supplied = headers.get('x-talkabout-bridge-nonce', '')
        if not isinstance(supplied, str) or not supplied.isascii() or not hmac.compare_digest(supplied, self.nonce):
            return self.response(403, {'code': 'bridge_nonce_denied'})
        if headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
            return self.response(415, {'code': 'bridge_json_required'})
        if not isinstance(body, bytes) or len(body) > MAX_BODY:
            return self.response(413, {'code': 'bridge_body_limit'})
        try:
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError('invalid_json')
        except (ValueError, UnicodeError):
            return self.response(400, {'code': 'bridge_invalid_json'})
        if path == '/status':
            if data:
                return self.response(422, {'code': 'bridge_invalid_fields'})
            try:
                result = self.status_callback()
                result = await result if inspect.isawaitable(result) else result
                if not isinstance(result, dict):
                    raise ValueError('invalid_status')
                grants = []
                for grant in result.get('grants', [])[:8]:
                    if isinstance(grant, dict) and grant.get('cli') in {'codex', 'claude'}:
                        grants.append({key: grant[key] for key in
                            ('grant_id', 'agent_id', 'cli', 'expires_at', 'state')
                            if key in grant and isinstance(grant[key], (str, int, float)) and not isinstance(grant[key], bool)})
                return self.response(200, {'state': result.get('state') if result.get('state')
                    in {'ready', 'offline', 'error'} else 'error', 'grants': grants})
            except Exception:
                return self.response(503, {'code': 'bridge_callback_failed'})
        if not self.valid_connect(data):
            return self.response(422, {'code': 'bridge_invalid_connect'})
        try:
            result = self.connect_callback(data)
            if inspect.isawaitable(result):
                await result
            return self.response(200, {'accepted': True})
        except Exception:
            return self.response(503, {'code': 'bridge_callback_failed'})
        finally:
            data['control_token'] = ''

    def valid_connect(self, data):
        if set(data) != {'base_url', 'agent_id', 'grant_id', 'cli', 'control_token', 'expires_at'}:
            return False
        expiry = data['expires_at']
        return (data['base_url'] == self.origin and isinstance(data['cli'], str)
                and data['cli'] in {'codex', 'claude'}
                and all(isinstance(data[key], str) and 1 <= len(data[key]) <= 128
                        and not any(ord(char) < 33 for char in data[key])
                        for key in ('agent_id', 'grant_id'))
                and isinstance(data['control_token'], str) and 32 <= len(data['control_token']) <= 512
                and isinstance(expiry, (int, float)) and not isinstance(expiry, bool)
                and time.time() < expiry <= time.time() + 3605 and math.isfinite(expiry))


def make_server(application, *, host='127.0.0.1'):
    """Bind only when explicitly called by the user-started local runner."""
    if host != '127.0.0.1':
        raise ValueError('loopback_only')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_request(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = MAX_BODY + 1
            if length < 0 or length > MAX_BODY:
                response = application.response(413, {'code': 'bridge_body_limit'}, False)
            else:
                body = self.rfile.read(length) if length else b''
                response = asyncio.run(application.handle(self.command, self.path, dict(self.headers), body))
            encoded = json.dumps(response.body, allow_nan=False).encode()
            self.send_response(response.status)
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.send_header('Content-Length', str(len(encoded)) if response.status != 204 else '0')
            self.end_headers()
            if response.status != 204:
                self.wfile.write(encoded)

        do_GET = do_POST = do_OPTIONS = do_PUT = do_DELETE = handle_request

    server = ThreadingHTTPServer((host, application.port), Handler)
    server.daemon_threads = True
    return server
