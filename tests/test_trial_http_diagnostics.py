"""Pure synthetic trial metadata tests; no services, actual tokens or runtimes."""
import json
import pytest
from talkabout.trial_http import safe_http_failure

ID = '12345678-1234-1234-1234-123456789abc'
SECRET = 'PRIVATE_SENTINEL_DO_NOT_RECORD'


@pytest.mark.parametrize('code', ['setup_required', 'service_unavailable'])
def test_http_failure_retains_only_allowed_fields(code, monkeypatch):
    monkeypatch.setattr('talkabout.trial_http.time.time', lambda: 1700000000.75)
    event = safe_http_failure('GET', 'rooms/'+ID, 503,
        json.dumps({'code':code, 'message':SECRET, 'token':SECRET, 'headers':{'Authorization':SECRET}}).encode(),
        'first_worker_start')
    assert event == {'status':503, 'method':'GET', 'path_template':'/api/v1/rooms/{room_id}',
                     'error_code':code, 'phase':'first_worker_start', 'recorded_at':1700000000}
    assert SECRET not in json.dumps(event) and ID not in json.dumps(event)


@pytest.mark.parametrize('path,template', [
    ('session','/api/v1/session'), ('session/logout','/api/v1/session/logout'),
    ('agents','/api/v1/agents'), ('rooms','/api/v1/rooms'),
    ('rooms/'+ID,'/api/v1/rooms/{room_id}'),
    ('rooms/'+ID+'/messages','/api/v1/rooms/{room_id}/messages'),
    ('rooms/'+ID+'/memberships','/api/v1/rooms/{room_id}/memberships'),
    ('rooms/'+ID+'/controls','/api/v1/rooms/{room_id}/controls'),
    ('rooms/'+ID+'/controls/'+ID+'/approve','/api/v1/rooms/{room_id}/controls/{control_id}/approve'),
    ('agents/'+ID+'/credentials','/api/v1/agents/{agent_id}/credentials'),
    ('agents/'+ID+'/credentials/'+ID+'/revoke','/api/v1/agents/{agent_id}/credentials/{credential_id}/revoke'),
])
def test_route_templates_never_contain_actual_identifiers(path, template):
    event = safe_http_failure('POST',path,503,b'{}','cleanup')
    assert event['path_template'] == template and ID not in json.dumps(event)
    assert len(event['path_template']) <= 80 and len(json.dumps(event)) <= 384


@pytest.mark.parametrize('body', [b'', b'not-json', b'\xff', b'[]', b'null', b'"text"',
    b'{"code":null}', b'{"code":[]}', b'{"code":{}}', b'{"code":123}',
    ('{"code":"'+SECRET+'"}').encode(), b'{"code":"setup_required \\n"}',
    b'{"code":"setup_required"}'+b' '*4096, b'['*1500+b']'*1500, None, {}, 'setup_required'])
def test_untrusted_or_oversized_body_is_discarded(body):
    event = safe_http_failure('GET','rooms/'+ID,503,body,'first_worker_start')
    assert event['error_code'] == 'unknown' and SECRET not in json.dumps(event)


@pytest.mark.parametrize('path', [None, {}, SECRET, 'rooms/'+SECRET, 'rooms/'+ID+'?token='+SECRET,
    '/api/v1/rooms/'+ID, 'https://fixture.invalid/rooms/'+ID, 'rooms/'+ID+'/../'+SECRET,
    'rooms/'+ID+'#'+SECRET, 'x'*513])
def test_unrecognized_path_is_dropped_without_truncating_private_input(path):
    event = safe_http_failure(SECRET,path,503,b'{}',SECRET)
    assert event['path_template'] == '/unknown' and event['phase'] == 'unknown' and event['method'] == 'UNKNOWN'
    assert SECRET not in json.dumps(event)


@pytest.mark.parametrize('status',[None, True, '503', 503.0, 200, 399, 600, 10**100])
def test_non_failure_status_values_are_not_serialized(status):
    assert safe_http_failure('GET','rooms/'+ID,status,b'{}','cleanup')['status'] is None


def test_body_limit_accepts_exact_boundary_and_discards_one_more_byte():
    body=b'{"code":"setup_required"}'
    body+=b' '*(4096-len(body))
    assert safe_http_failure('GET','rooms/'+ID,503,body,'cleanup')['error_code']=='setup_required'
    assert safe_http_failure('GET','rooms/'+ID,503,body+b' ','cleanup')['error_code']=='unknown'
