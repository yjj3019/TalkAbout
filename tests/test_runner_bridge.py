"""Pure callback fixtures: no listener, grant issuance, vault, CLI or provider."""
import asyncio
import json
import time

import pytest

from talkabout.runner_bridge import BridgeApplication


ORIGIN = 'http://127.0.0.1:18081'
NONCE = 'synthetic-bridge-nonce-for-tests-only'


def application(callback=None):
    calls = []
    def connect(data):
        calls.append(data['agent_id'])
        if callback:
            return callback(data)
    return BridgeApplication(lambda: {'state': 'offline', 'grants': []},
                             connect, ORIGIN, nonce=NONCE), calls


def connect_body(**updates):
    return {'base_url': ORIGIN, 'agent_id': 'fixture-agent',
            'grant_id': 'fixture-grant', 'cli': 'codex',
            'control_token': 'synthetic-fixture-value-not-a-service-key',
            'expires_at': time.time() + 600, **updates}


def submit(app, data, **header_updates):
    headers = {'origin': ORIGIN, 'host': '127.0.0.1:18742',
               'content-type': 'application/json',
               'x-talkabout-bridge-nonce': NONCE, **header_updates}
    return asyncio.run(app.handle('POST', '/connect', headers,
                                  json.dumps(data).encode('utf-8')))


@pytest.mark.parametrize('cli', [[], {}, None, 1, True, 'grok'])
def test_untrusted_cli_type_is_rejected_before_callback(cli):
    app, calls = application()
    result = submit(app, connect_body(cli=cli))
    assert result.status == 422
    assert result.body == {'code': 'bridge_invalid_connect'}
    assert calls == []


@pytest.mark.parametrize('expiry', [10**400, -(10**400), float('inf'),
                                  float('nan'), False, None, 'later'])
def test_untrusted_expiry_is_rejected_before_callback(expiry):
    app, calls = application()
    result = submit(app, connect_body(expires_at=expiry))
    assert result.status == 422
    assert result.body == {'code': 'bridge_invalid_connect'}
    assert calls == []


def test_callback_receipt_does_not_claim_execution_or_return_secrets():
    app, calls = application(lambda data: {'connected': True,
                                          'control_token': data['control_token']})
    result = submit(app, connect_body())
    assert result.status == 200 and result.body == {'accepted': True}
    assert calls == ['fixture-agent']
    assert 'connected' not in result.body and 'control_token' not in result.body


@pytest.mark.parametrize('headers', [{'origin': 'http://other.invalid'},
    {'host': 'other.invalid:18742'}, {'x-talkabout-bridge-nonce': 'wrong'}])
def test_origin_host_and_nonce_never_reach_callback(headers):
    app, calls = application()
    assert submit(app, connect_body(), **headers).status == 403
    assert calls == []


def test_callback_error_is_not_success_or_automatic_retry():
    def fail(data):
        raise RuntimeError('synthetic callback failure')
    app, calls = application(fail)
    result = submit(app, connect_body())
    assert result.status == 503 and result.body == {'code': 'bridge_callback_failed'}
    assert calls == ['fixture-agent']
