"""Synthetic protocol tests. Never launch installed providers or read credentials."""
import io
import json
import os
import queue
import sys
import threading
import time

import pytest

from talkabout.adapters import AdapterError
from talkabout.agent_runtime import StructuredRuntime, _JSONLTransport, runtime_environment, MAX_LINE


class ProtocolPeer:
    def __init__(self, kind, behavior='success'):
        self.kind, self.behavior = kind, behavior
        self.transports = []
        self.turns = 0

    def factory(self, supervisor, command, cwd, env, cancel, deadline):
        peer = self
        class Transport:
            def __init__(self):
                self.sent, self.events = [], queue.Queue()
                self.command, self.cwd, self.env = command, cwd, env
                self.closed = False
            def put(self, event):
                if peer.kind == 'grok':
                    event = {'jsonrpc': '2.0', **event}
                self.events.put(event)
            def check(self, cancel, deadline):
                if cancel.is_set():
                    raise AdapterError('runtime_cancelled')
                if time.monotonic() >= deadline:
                    raise AdapterError('runtime_timeout')
            def send(self, event, cancel, deadline):
                self.check(cancel, deadline)
                self.sent.append(event)
                peer.respond(self, event)
            def receive(self, cancel, deadline):
                while True:
                    self.check(cancel, deadline)
                    try:
                        event = self.events.get(timeout=.005)
                    except queue.Empty:
                        continue
                    if event is None:
                        raise AdapterError('runtime_outcome_unknown')
                    return event
            def close(self):
                self.closed = True
                return True
        transport = Transport()
        self.transports.append(transport)
        return transport

    def respond(self, transport, event):
        method, request_id = event.get('method'), event.get('id')
        def result(data):
            transport.put({'id': request_id, 'result': data})
        if self.behavior == 'rpc_error' and method == 'initialize':
            transport.put({'id': request_id, 'error': {'code': -1, 'message': 'SYNTHETIC SECRET'}})
        elif method == 'initialize':
            result({'protocolVersion': 1, 'agentInfo': {'version': '1.2.3'},
                    'serverInfo': {'version': '1.2.3'}, 'agentCapabilities': {'loadSession': True},
                    'authMethods': [{'id': 'xai.api_key'}]})
        elif method == 'account/read':
            result({'account': {'type': 'chatgpt'}})
        elif method == 'authenticate':
            result({})
        elif method in {'thread/start', 'thread/resume'}:
            result({'thread': {'id': 'thread-one', 'sessionId': 'session-root'}})
        elif method in {'session/new', 'session/load'}:
            if method == 'session/load':
                self.grok_chunk(transport, 'Old history must not be returned')
            result({'sessionId': 'grok-one'})
        elif method in {'turn/start', 'session/prompt'} or event.get('type') == 'user':
            self.turns += 1
            if method == 'turn/start':
                # Exercise interleaving notifications before the request response.
                transport.put({'method': 'turn/started', 'params': {'threadId': 'thread-one', 'turn': {'id': 'turn-' + str(self.turns)}}})
                result({'turn': {'id': 'turn-' + str(self.turns), 'status': 'inProgress'}})
            if self.behavior == 'tool_request':
                transport.put({'id': 'server-request', 'method': 'session/request_permission' if self.kind == 'grok' else 'item/commandExecution/requestApproval', 'params': {}})
                return
            if self.behavior == 'hold':
                return
            if self.kind == 'codex':
                params = {'threadId': 'thread-one', 'turnId': 'turn-' + str(self.turns), 'itemId': 'message-one'}
                if self.behavior == 'wrong_room':
                    params['threadId'] = 'foreign-thread'
                if self.behavior == 'tool':
                    transport.put({'method': 'item/started', 'params': {**params, 'item': {'id': 'x', 'type': 'commandExecution'}}})
                    return
                transport.put({'method': 'item/agentMessage/delta', 'params': {**params, 'delta': 'Answer ' + str(self.turns)}})
                if self.behavior == 'truncated':
                    transport.events.put(None)
                    return
                transport.put({'method': 'turn/completed', 'params': {'threadId': 'thread-one',
                    'turn': {'id': 'turn-' + str(self.turns), 'status': 'failed' if self.behavior == 'failed' else 'completed', 'error': None}}})
            elif self.kind == 'claude':
                session = event['session_id']
                transport.put({'type': 'system', 'subtype': 'init', 'session_id': session, 'tools': [], 'mcp_servers': [], 'claude_code_version': '2.1.300'})
                if self.behavior == 'tool':
                    transport.put({'type': 'assistant', 'session_id': session, 'message': {'content': [{'type': 'tool_use', 'name': 'Bash'}]}})
                    return
                transport.put({'type': 'stream_event', 'session_id': session, 'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'Answer ' + str(self.turns)}}})
                if self.behavior == 'truncated':
                    transport.events.put(None)
                    return
                transport.put({'type': 'result', 'session_id': 'foreign-session' if self.behavior == 'wrong_room' else session,
                               'subtype': 'success', 'is_error': self.behavior == 'failed', 'result': 'Answer ' + str(self.turns)})
            else:
                if self.behavior == 'tool':
                    transport.put({'method': 'session/update', 'params': {'sessionId': 'grok-one', 'update': {'sessionUpdate': 'tool_call'}}})
                    return
                self.grok_chunk(transport, 'Answer ' + str(self.turns), self.behavior == 'wrong_room')
                if self.behavior == 'truncated':
                    transport.events.put(None)
                    return
                result({'stopReason': 'max_tokens' if self.behavior == 'failed' else 'end_turn'})

    @staticmethod
    def grok_chunk(transport, text, wrong=False):
        transport.put({'method': 'session/update', 'params': {'sessionId': 'foreign-session' if wrong else 'grok-one',
                       'update': {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': text}}}})


def runtime(monkeypatch, kind, behavior='success'):
    peer = ProtocolPeer(kind, behavior)
    monkeypatch.setattr(StructuredRuntime, '_ensure_preflight', lambda self, cancel: None)
    monkeypatch.setattr('talkabout.agent_runtime._JSONLTransport', peer.factory)
    monkeypatch.setattr('talkabout.agent_runtime.execute_bounded', lambda *args, **kwargs: json.dumps({'authMethod': 'claude.ai', 'loggedIn': True}))
    monkeypatch.setenv('XAI_API_KEY', 'synthetic-test-key-not-real')
    adapter = StructuredRuntime(kind, executable=sys.executable, auth_mode='api_key' if kind == 'grok' else 'native', codex_transport='app-server')
    if kind == 'grok':
        # Synthetic peers test ACP independently of unverified vendor startup.
        real_probe = adapter.probe
        adapter.probe = lambda: {**real_probe(), 'reason': None}
    return adapter, peer


@pytest.mark.parametrize('kind', ['codex', 'claude', 'grok'])
def test_two_turns_resume_native_identifiers_and_private_workspace(monkeypatch, kind):
    adapter, peer = runtime(monkeypatch, kind)
    try:
        assert adapter.run({'room_id': 'room-one', 'topic': 'hello'}, threading.Event()) == 'Answer 1'
        session = adapter.session_id
        assert adapter.run({'room_id': 'room-one', 'topic': 'continue'}, threading.Event()) == 'Answer 2'
        assert adapter.session_id == session and adapter.calls == 2
        assert adapter.last_outcome == 'completed' and adapter.runtime_version
        assert all(t.closed for t in peer.transports)
        assert peer.transports[0].cwd == peer.transports[1].cwd
        second = peer.transports[1]
        if kind == 'codex':
            resumed = next(e for e in second.sent if e.get('method') == 'thread/resume')
            assert resumed['params']['threadId'] == 'thread-one'
            assert adapter.thread_id != adapter.session_id
        elif kind == 'claude':
            assert second.command[second.command.index('--resume') + 1] == session
        else:
            resumed = next(e for e in second.sent if e.get('method') == 'session/load')
            assert resumed['params']['sessionId'] == session
            initialize = next(e for e in second.sent if e.get('method') == 'initialize')
            assert initialize['params']['clientCapabilities']['terminal'] is False
    finally:
        adapter.shutdown()


@pytest.mark.parametrize('kind', ['codex', 'claude', 'grok'])
@pytest.mark.parametrize('behavior,code', [('truncated', 'runtime_outcome_unknown'),
                                         ('wrong_room', 'runtime_session_mismatch'),
                                         ('tool', 'runtime_tool_policy_violation')])
def test_never_return_deltas_without_terminal_or_on_policy_violation(monkeypatch, kind, behavior, code):
    adapter, peer = runtime(monkeypatch, kind, behavior)
    with pytest.raises(AdapterError, match=code):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    assert peer.transports[0].closed
    assert adapter.last_outcome == 'unknown'
    with pytest.raises(AdapterError, match='runtime_outcome_unknown'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    assert len(peer.transports) == 1
    adapter.shutdown()


@pytest.mark.parametrize('kind', ['codex', 'claude', 'grok'])
def test_failed_terminal_rejected(monkeypatch, kind):
    adapter, peer = runtime(monkeypatch, kind, 'failed')
    with pytest.raises(AdapterError, match='runtime_failed|runtime_incomplete'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    adapter.shutdown()


@pytest.mark.parametrize('kind', ['codex', 'grok'])
def test_server_approval_requests_explicitly_denied(monkeypatch, kind):
    adapter, peer = runtime(monkeypatch, kind, 'tool_request')
    with pytest.raises(AdapterError, match='runtime_tool_policy_violation'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    reply = next(e for e in peer.transports[0].sent if e.get('id') == 'server-request')
    assert reply['result'] == ({'decision': 'decline'} if kind == 'codex' else {'outcome': {'outcome': 'cancelled'}})
    adapter.shutdown()


@pytest.mark.parametrize('kind', ['codex', 'grok'])
def test_async_request_errors_sanitized(monkeypatch, kind):
    adapter, peer = runtime(monkeypatch, kind, 'rpc_error')
    with pytest.raises(AdapterError, match='^runtime_rpc_error$'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    assert peer.transports[0].closed and adapter.last_outcome == 'not_started'
    adapter.shutdown()


@pytest.mark.parametrize('kind', ['codex', 'claude', 'grok'])
def test_injected_timeout_error_closes_transport_and_sends_cancel(monkeypatch, kind):
    adapter, peer = runtime(monkeypatch, kind, 'hold')
    # Expire while the fake provider holds a submitted turn. A 30 ms wall clock
    # can instead expire during startup, when there is no session to cancel.
    factory = peer.factory
    def timeout_transport(*args):
        transport = factory(*args)
        receive = transport.receive
        def receive_until_timeout(cancel, deadline):
            if peer.turns and transport.events.empty():
                raise AdapterError('runtime_timeout')
            return receive(cancel, deadline)
        transport.receive = receive_until_timeout
        return transport
    monkeypatch.setattr('talkabout.agent_runtime._JSONLTransport', timeout_transport)
    with pytest.raises(AdapterError, match='runtime_timeout'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    assert peer.turns == 1
    assert peer.transports[0].closed
    methods = [e.get('method') for e in peer.transports[0].sent]
    if kind != 'claude':
        assert ('turn/interrupt' if kind == 'codex' else 'session/cancel') in methods
    adapter.shutdown()


@pytest.mark.parametrize('kind', ['codex', 'claude', 'grok'])
def test_real_transport_deadline_boundary_after_submitted_turn(monkeypatch, kind):
    from types import SimpleNamespace
    from talkabout import agent_runtime
    adapter,peer=runtime(monkeypatch,kind,'hold')
    clock=[time.monotonic()]
    observed=[]
    monkeypatch.setattr(agent_runtime,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    factory=peer.factory
    def deadline_transport(*args):
        transport=factory(*args)
        transport.failure=None
        receive=transport.receive
        def receive_until_deadline(cancel,deadline):
            if peer.turns and transport.events.empty():
                clock[0]=deadline-0.001
                _JSONLTransport.check(transport,cancel,deadline)
                observed.append('before_deadline_passed')
                clock[0]=deadline
                observed.append('exact_deadline_checked')
                _JSONLTransport.check(transport,cancel,deadline)
                pytest.fail('actual deadline check did not reject its exact boundary')
            return receive(cancel,deadline)
        transport.receive=receive_until_deadline
        return transport
    monkeypatch.setattr(agent_runtime,'_JSONLTransport',deadline_transport)
    with pytest.raises(AdapterError,match='^runtime_timeout$'):
        adapter.run({'room_id':'room-one'},threading.Event())
    assert peer.turns==1 and peer.transports[0].closed
    assert observed==['before_deadline_passed','exact_deadline_checked']
    assert adapter.last_outcome=='unknown'
    if kind!='claude':
        assert ('turn/interrupt' if kind=='codex' else 'session/cancel') in [event.get('method') for event in peer.transports[0].sent]
    adapter.shutdown()


@pytest.mark.parametrize('kind', ['codex', 'claude', 'grok'])
def test_cancellation_and_room_binding(monkeypatch, kind):
    adapter, peer = runtime(monkeypatch, kind)
    cancelled = threading.Event(); cancelled.set()
    with pytest.raises(AdapterError, match='runtime_cancelled'):
        adapter.run({'room_id': 'room-one'}, cancelled)
    assert not peer.transports
    adapter.run({'room_id': 'room-one'}, threading.Event())
    with pytest.raises(AdapterError, match='runtime_room_mismatch'):
        adapter.run({'room_id': 'room-two'}, threading.Event())
    adapter.shutdown()
    with pytest.raises(AdapterError, match='runtime_cancelled'):
        adapter.run({'room_id': 'room-one'}, threading.Event())


def test_separate_connection_check_does_not_reuse_room_session(monkeypatch):
    check, check_peer = runtime(monkeypatch, 'claude')
    check.run({'purpose': 'connection_test', 'room_id': '__runtime_check__', 'challenge': 'synthetic'}, threading.Event())
    check_id = check.session_id
    check.shutdown()
    room, room_peer = runtime(monkeypatch, 'claude')
    room.run({'room_id': 'actual-room'}, threading.Event())
    assert room.session_id != check_id
    assert '__runtime_check__' not in json.dumps(room_peer.transports[0].sent)
    room.shutdown()


def test_environment_allowlist_is_provider_specific_and_removes_secret_aliases(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-openai')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-claude')
    monkeypatch.setenv('XAI_API_KEY', 'test-grok')
    monkeypatch.setenv('CODEX_HOME', '/synthetic/codex')
    monkeypatch.setenv('TALKABOUT_TOKEN', 'test-platform')
    monkeypatch.setenv('DATABASE_URL', 'test-db')
    monkeypatch.setenv('NODE_OPTIONS', '--require=untrusted')
    monkeypatch.setenv('PYTHONPATH', '/untrusted')
    monkeypatch.setenv('HOME', '/contains-test-platform-secret')
    env = runtime_environment('grok', blocked_values={'test-platform'})
    assert env['XAI_API_KEY'] == 'test-grok'
    assert not {'HOME', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'CODEX_HOME', 'TALKABOUT_TOKEN', 'DATABASE_URL', 'NODE_OPTIONS', 'PYTHONPATH'} & env.keys()
    assert 'XAI_API_KEY' not in runtime_environment('grok', blocked_names={'xai_api_key'})


def test_native_auth_never_falls_back_to_inherited_api_key(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-billable-key')
    adapter, peer = runtime(monkeypatch, 'codex')
    adapter.run({'room_id': 'room-one'}, threading.Event())
    assert 'OPENAI_API_KEY' not in peer.transports[0].env
    adapter.shutdown()


def test_grok_native_auth_is_explicitly_unsupported_before_launch(monkeypatch):
    monkeypatch.setenv('XAI_API_KEY', 'synthetic-billable-key')
    def forbidden(*args, **kwargs):
        pytest.fail('No process should launch for unsupported native authentication')
    monkeypatch.setattr('talkabout.agent_runtime._JSONLTransport', forbidden)
    adapter = StructuredRuntime('grok', executable=sys.executable)
    with pytest.raises(AdapterError, match='runtime_auth_mode_unsupported'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    assert adapter.calls == 0
    adapter.shutdown()


def test_probe_does_not_execute_and_shell_wrappers_rejected(tmp_path, monkeypatch):
    path = tmp_path / 'codex.cmd'; path.write_text('unsafe shell')
    adapter = StructuredRuntime('codex', executable=str(path))
    assert adapter.probe()['reason'] == 'runtime_shell_wrapper_unsupported'
    assert not adapter.probe()['provider_verified']
    assert adapter.probe()['runtime_version'] is None
    with pytest.raises(AdapterError, match='runtime_shell_wrapper_unsupported'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    assert adapter.calls == 0


@pytest.mark.skipif(os.name == 'nt', reason='POSIX-specific fail-closed test')
def test_posix_real_launch_fails_closed():
    adapter = StructuredRuntime('codex', executable=sys.executable)
    with pytest.raises(AdapterError, match='runtime_containment_unsupported'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    adapter.shutdown()


@pytest.mark.parametrize('context', [{}, {'room_id': ''}, {'room_id': 'room', 'bad': float('nan')}, {'room_id': 'room', 'text': 'x' * 200001}],
                         ids=['missing-room', 'empty-room', 'nonfinite-json', 'oversized-context'])
def test_invalid_context_rejected_without_launch(context):
    adapter = StructuredRuntime('claude', executable=sys.executable)
    with pytest.raises(AdapterError):
        adapter.run(context, threading.Event())
    assert adapter.calls == 0


class PipeSupervisor:
    def __init__(self, raw, stderr=b''):
        self.process = type('Process', (), {'stdin': io.BytesIO(), 'stdout': io.BytesIO(raw), 'stderr': io.BytesIO(stderr)})()
        self.stopped = False
    def start(self, command, cancel, **kwargs):
        assert kwargs['shell'] is False
        return self.process
    def stop(self):
        self.stopped = True
        return True


@pytest.mark.parametrize('exit_code', [0, 2, None], ids=['zero', 'nonzero', 'still-running'])
def test_eof_diagnostics_preserve_unknown_and_pre_cleanup_exit_only(exit_code):
    supervisor = PipeSupervisor(b'')
    supervisor.process.returncode = exit_code
    supervisor.process.poll = lambda: supervisor.process.returncode
    original_stop = supervisor.stop
    def stop():
        supervisor.process.returncode = 1  # Simulate our forced job termination.
        return original_stop()
    supervisor.stop = stop
    transport = _JSONLTransport(supervisor, ['synthetic'], '.', {}, threading.Event(), time.monotonic() + 1)
    with pytest.raises(AdapterError, match='runtime_outcome_unknown'):
        transport.receive(threading.Event(), time.monotonic() + 1)
    transport.close()
    evidence = transport.diagnostics()
    assert evidence['native_exit_code_before_cleanup'] == exit_code
    assert evidence['stdout_frames'] == 0 and evidence['stdout_eof']
    assert evidence['stderr_hints'] == [] and evidence['cleanup_confirmed']


def test_diagnostics_count_frames_and_only_retain_fixed_stderr_hints():
    secret = 'SYNTHETIC_PRIVATE_TOKEN_AND_PATH'
    frames = [
        {'type': 'thread.started', 'thread_id': secret},
        {'type': 'item.completed', 'item': {'text': secret}},
        {'type': secret, 'message': secret},
    ]
    raw = b''.join((json.dumps(frame) + '\n').encode() for frame in frames)
    # Hint deliberately straddles the 4096-byte stderr read boundary.
    stderr = b'x' * 4090 + b'unknown field ' + secret.encode()
    supervisor = PipeSupervisor(raw, stderr)
    transport = _JSONLTransport(supervisor, ['synthetic'], '.', {}, threading.Event(), time.monotonic() + 1)
    for frame in frames:
        assert transport.receive(threading.Event(), time.monotonic() + 1) == frame
    transport.close()
    evidence = transport.diagnostics()
    assert evidence['stdout_bytes'] == len(raw) and evidence['stdout_frames'] == 3
    assert evidence['stdout_event_counts'] == {'thread.started': 1, 'item.completed': 1, 'other': 1}
    assert evidence['stderr_bytes'] == len(stderr) and evidence['stderr_hints'] == ['configuration']
    assert secret not in json.dumps(evidence)
    # Returned snapshots cannot mutate subsequent evidence.
    evidence['stdout_event_counts']['forged'] = 999
    assert 'forged' not in transport.diagnostics()['stdout_event_counts']


def test_diagnostics_do_not_echo_unclassified_stderr():
    supervisor = PipeSupervisor(b'', b'SYNTHETIC_PRIVATE_CREDENTIAL_WITH_NO_KNOWN_HINT')
    transport = _JSONLTransport(supervisor, ['synthetic'], '.', {}, threading.Event(), time.monotonic() + 1)
    transport.close()
    evidence = transport.diagnostics()
    assert evidence['stderr_hints'] == ['unclassified']
    assert 'SYNTHETIC' not in json.dumps(evidence)


@pytest.mark.skipif(os.name != 'nt', reason='Windows contained synthetic child')
def test_real_synthetic_child_stdin_eof_nonzero_exit_has_safe_diagnostics(tmp_path):
    from talkabout.processes import ProcessSupervisor
    # This is Python, never a provider binary. It cannot emit anything until stdin
    # EOF; a nonzero exit with no JSONL terminal proof must remain uncertain.
    child = tmp_path / 'synthetic_eof.py'
    child.write_text('import sys\nassert sys.stdin.buffer.read() == b"synthetic prompt"\n'
                     'sys.stderr.write("unexpected argument SYNTHETIC_PRIVATE_TOKEN\\n")\n'
                     'sys.stderr.flush()\nsys.exit(2)\n')
    deadline = time.monotonic() + 10
    transport = _JSONLTransport(ProcessSupervisor(), [sys.executable, '-B', str(child)],
                                str(tmp_path), {}, threading.Event(), deadline)
    try:
        transport.send_text('synthetic prompt', threading.Event(), deadline)
        with pytest.raises(AdapterError, match='runtime_outcome_unknown'):
            transport.receive(threading.Event(), deadline)
        transport.process.wait(timeout=2)
    finally:
        transport.close()
    evidence = transport.diagnostics()
    assert evidence['native_exit_code_before_cleanup'] == 2
    assert evidence['stdout_eof'] and evidence['stdout_frames'] == 0
    assert evidence['stderr_hints'] == ['arguments'] and evidence['cleanup_confirmed']
    assert 'SYNTHETIC_PRIVATE_TOKEN' not in json.dumps(evidence)


@pytest.mark.parametrize('raw,code', [(b'{broken}\n', 'runtime_invalid_output'),
                                    (b'{"x":1}', 'runtime_truncated_output'),
                                    (b'[]\n', 'runtime_invalid_output'),
                                    (b'\xff\n', 'runtime_invalid_output'),
                                    (b'x' * (MAX_LINE + 1), 'runtime_output_limit'),
                                    (b'', 'runtime_outcome_unknown')],
                         ids=['malformed-json', 'truncated-line', 'nonobject-json',
                              'invalid-utf8', 'oversized-line', 'empty-output'])
def test_bounded_transport_malformed_truncated_and_eof(raw, code):
    supervisor = PipeSupervisor(raw)
    transport = _JSONLTransport(supervisor, ['synthetic'], '.', {}, threading.Event(), time.monotonic() + 1)
    with pytest.raises(AdapterError, match=code):
        transport.receive(threading.Event(), time.monotonic() + 1)
    transport.close()
    assert supervisor.stopped


def test_transport_stderr_bounded_and_never_exposed():
    supervisor = PipeSupervisor(b'{}\n', b'x' * (2 * 1024 * 1024 + 1))
    transport = _JSONLTransport(supervisor, ['synthetic'], '.', {}, threading.Event(), time.monotonic() + 1)
    for reader in transport.readers:
        reader.join(1)
    with pytest.raises(AdapterError, match='^runtime_output_limit$'):
        transport.receive(threading.Event(), time.monotonic() + 1)
    transport.close()


@pytest.mark.parametrize('raw', [b'{"id":1,"id":2}\n', b'{"result":NaN}\n', b'{"x":Infinity}\n'])
def test_ambiguous_json_is_not_accepted(raw):
    supervisor = PipeSupervisor(raw)
    transport = _JSONLTransport(supervisor, ['synthetic'], '.', {}, threading.Event(), time.monotonic() + 1)
    with pytest.raises(AdapterError, match='runtime_invalid_output'):
        transport.receive(threading.Event(), time.monotonic() + 1)
    transport.close()


def test_cleanup_failure_prevents_completed_output(monkeypatch):
    adapter, peer = runtime(monkeypatch, 'codex')
    factory = peer.factory
    def unconfirmed(*args):
        transport = factory(*args)
        transport.close = lambda: False
        return transport
    monkeypatch.setattr('talkabout.agent_runtime._JSONLTransport', unconfirmed)
    with pytest.raises(AdapterError, match='runtime_cleanup_unconfirmed'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    assert adapter.last_outcome == 'unknown'
    with pytest.raises(AdapterError, match='runtime_outcome_unknown'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    adapter.shutdown()


def test_active_cancel_is_not_reported_as_completed(monkeypatch):
    adapter, peer = runtime(monkeypatch, 'grok', 'hold')
    cancel = threading.Event()
    errors = []
    def run():
        try:
            adapter.run({'room_id': 'room-one'}, cancel)
        except AdapterError as error:
            errors.append(error.code)
    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 1
    while peer.turns == 0 and time.monotonic() < deadline:
        time.sleep(.001)
    with pytest.raises(AdapterError, match='runtime_busy'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    cancel.set()
    thread.join(1)
    assert not thread.is_alive() and errors == ['runtime_cancelled']
    assert adapter.last_outcome == 'unknown'
    assert any(e.get('method') == 'session/cancel' for e in peer.transports[0].sent)
    assert peer.transports[0].closed
    adapter.shutdown()


def test_platform_secret_not_sent_as_context_or_credential_alias(monkeypatch):
    monkeypatch.setenv('TALKABOUT_TOKEN', 'synthetic-platform-secret')
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-platform-secret')
    adapter = StructuredRuntime('codex', executable=sys.executable, auth_mode='api_key')
    assert adapter.probe()['reason'] == 'runtime_auth_required'
    with pytest.raises(AdapterError, match='runtime_secret_in_context'):
        adapter.run({'room_id': 'room-one', 'topic': 'synthetic-platform-secret'}, threading.Event())
    assert adapter.calls == 0
    adapter.shutdown()


@pytest.mark.parametrize('kind', ['codex', 'grok'])
def test_prompt_request_rpc_error_is_not_a_terminal_success(monkeypatch, kind):
    adapter, peer = runtime(monkeypatch, kind)
    original = peer.respond
    def response(transport, event):
        if event.get('method') in {'turn/start', 'session/prompt'}:
            transport.put({'id': event['id'], 'error': {'code': -32602, 'message': 'secret server error'}})
        else:
            original(transport, event)
    peer.respond = response
    with pytest.raises(AdapterError, match='^runtime_rpc_error$'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    assert adapter.last_outcome == 'unknown'
    adapter.shutdown()


def test_unknown_server_request_denied_without_execution(monkeypatch):
    adapter, peer = runtime(monkeypatch, 'grok')
    original = peer.respond
    def response(transport, event):
        if event.get('method') == 'session/prompt':
            transport.put({'id': 'foreign-command', 'method': 'terminal/create', 'params': {'command': 'never execute'}})
        else:
            original(transport, event)
    peer.respond = response
    with pytest.raises(AdapterError, match='runtime_tool_policy_violation'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    reply = next(e for e in peer.transports[0].sent if e.get('id') == 'foreign-command')
    assert reply['error']['code'] == -32601
    adapter.shutdown()


def test_grok_missing_resume_capability_does_not_start_new_session(monkeypatch):
    adapter, peer = runtime(monkeypatch, 'grok')
    adapter.run({'room_id': 'room-one'}, threading.Event())
    original = peer.respond
    def response(transport, event):
        if event.get('method') == 'initialize':
            transport.put({'id': event['id'], 'result': {'protocolVersion': 1, 'agentCapabilities': {},
                'authMethods': [{'id': 'xai.api_key'}]}})
        else:
            original(transport, event)
    peer.respond = response
    with pytest.raises(AdapterError, match='runtime_resume_unsupported'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    assert not any(e.get('method') == 'session/new' for e in peer.transports[1].sent)
    adapter.shutdown()


@pytest.mark.parametrize('kind', ['codex', 'claude', 'grok'])
def test_text_size_limit_enforced_before_return(monkeypatch, kind):
    adapter, peer = runtime(monkeypatch, kind)
    original = peer.factory
    def factory(*args):
        transport = original(*args)
        put = transport.put
        def oversized(event):
            if event.get('method') == 'item/agentMessage/delta':
                event['params']['delta'] = 'x' * 16001
            if event.get('type') == 'result':
                event['result'] = 'x' * 16001
            if event.get('method') == 'session/update':
                event['params']['update']['content']['text'] = 'x' * 16001
            put(event)
        transport.put = oversized
        return transport
    monkeypatch.setattr('talkabout.agent_runtime._JSONLTransport', factory)
    with pytest.raises(AdapterError, match='runtime_invalid_output'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    adapter.shutdown()


def test_deadline_expired_before_launch(monkeypatch):
    adapter, peer = runtime(monkeypatch, 'codex')
    adapter.set_deadline(time.monotonic() - 1)
    with pytest.raises(AdapterError, match='runtime_timeout'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    assert not peer.transports and adapter.calls == 0
    adapter.shutdown()


@pytest.mark.parametrize('kind,key', [('codex', 'OPENAI_API_KEY'), ('claude', 'ANTHROPIC_API_KEY'), ('grok', 'XAI_API_KEY')])
def test_api_key_mode_requires_its_own_explicit_credential(monkeypatch, kind, key):
    for name in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'ANTHROPIC_API_KEY', 'XAI_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    adapter = StructuredRuntime(kind, executable=sys.executable, auth_mode='api_key')
    assert adapter.probe()['reason'] == 'runtime_auth_required'
    assert adapter.probe()['credential_present'] is False
    with pytest.raises(AdapterError, match='runtime_auth_required'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    assert adapter.calls == 0
    monkeypatch.setenv(key, 'synthetic-provider-key')
    assert adapter.probe()['credential_present'] is True
    adapter.shutdown()


@pytest.mark.parametrize('method', ['api_key', 'api_key_helper', 'third_party', 'oauth_token'])
def test_claude_native_rejects_other_auth_modes_before_prompt(monkeypatch, method):
    adapter, peer = runtime(monkeypatch, 'claude')
    seen = []
    def status(command, prompt, cancel, **kwargs):
        seen.append((command, kwargs))
        return json.dumps({'authMethod': method, 'loggedIn': True})
    monkeypatch.setattr('talkabout.agent_runtime.execute_bounded', status)
    with pytest.raises(AdapterError, match='runtime_auth_mode_mismatch'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    assert not peer.transports and adapter.calls == 0
    assert seen[0][0][-2:] == ['auth', 'status']
    assert '--restricted' in seen[0][0] and '--safe-mode' in seen[0][0]
    assert 'ANTHROPIC_API_KEY' in seen[0][1]['blocked_names']
    adapter.shutdown()


def test_codex_native_rejects_saved_api_key_before_prompt(monkeypatch):
    adapter, peer = runtime(monkeypatch, 'codex')
    original = peer.respond
    def response(transport, event):
        if event.get('method') == 'account/read':
            assert event['params'] == {'refreshToken': False}
            transport.put({'id': event['id'], 'result': {'account': {'type': 'apiKey'}}})
        else:
            original(transport, event)
    peer.respond = response
    with pytest.raises(AdapterError, match='runtime_auth_mode_mismatch'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    assert not any(e.get('method') == 'turn/start' for e in peer.transports[0].sent)
    adapter.shutdown()


PREFLIGHT_FLAGS = {
    'codex': ['--ignore-user-config', '--strict-config', '--config'],
    'claude': ['--restricted', '--safe-mode', '--strict-mcp-config', '--mcp-config',
               '--setting-sources', '--settings', '--permission-mode', '--tools',
               '--input-format', '--output-format', '--session-id', '--resume'],
    'grok': ['--no-auto-update', '--tools', '--deny', '--sandbox', '--no-plan',
             '--no-subagents', '--no-memory', '--disable-web-search'],
}


def preflight_fixture(monkeypatch, kind, missing=None, version=None):
    monkeypatch.setattr('talkabout.agent_runtime._containment_supported', lambda: True)
    monkeypatch.setenv('XAI_API_KEY', 'synthetic-key')
    seen = []
    def local(command, prompt, cancel, **kwargs):
        assert prompt == ''
        assert {'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'XAI_API_KEY'} <= kwargs['blocked_names']
        seen.append(command)
        if command[-1] in {'--version', 'version'}:
            return version or ('Claude Code 2.1.300' if kind == 'claude' else 'synthetic-cli 1.2.3')
        if command[-1] == '--help':
            if 'app-server' in command:
                return 'Usage: codex app-server [OPTIONS]'
            if 'stdio' in command:
                return 'Usage: grok agent stdio [OPTIONS]'
            return '\n'.join('  ' + flag + '  help' for flag in PREFLIGHT_FLAGS[kind] if flag != missing)
        pytest.fail('Preflight attempted something other than help/version')
    monkeypatch.setattr('talkabout.agent_runtime.execute_bounded', local)
    adapter = StructuredRuntime(kind, executable=sys.executable, auth_mode='api_key' if kind == 'grok' else 'native', codex_transport='app-server')
    if kind == 'grok':
        # Synthetic peers test ACP independently of unverified vendor startup.
        real_probe = adapter.probe
        adapter.probe = lambda: {**real_probe(), 'reason': None}
    return adapter, seen


@pytest.mark.parametrize('kind', ['codex', 'claude', 'grok'])
def test_preflight_only_local_help_version_never_auth_or_prompt(monkeypatch, kind):
    adapter, seen = preflight_fixture(monkeypatch, kind)
    result = adapter.preflight()
    assert result['safety_flags_verified'] and not result['provider_verified']
    assert adapter.runtime_version and adapter.calls == 0 and adapter.session_id is None
    first_count = len(seen)
    adapter.preflight()
    assert len(seen) == first_count  # unchanged executable capability result cached
    assert all(command[-1] in {'--help', '--version', 'version'} for command in seen)
    adapter.shutdown()


@pytest.mark.parametrize('kind,missing', [('codex', '--ignore-user-config'), ('codex', '--strict-config'),
                                        ('claude', '--restricted'), ('claude', '--safe-mode'),
                                        ('claude', '--strict-mcp-config'), ('grok', '--deny')])
def test_preflight_missing_safety_flag_is_unsupported_without_fallback(monkeypatch, kind, missing):
    adapter, seen = preflight_fixture(monkeypatch, kind, missing=missing)
    with pytest.raises(AdapterError, match='runtime_version_unsupported'):
        adapter.preflight()
    assert adapter.calls == 0 and adapter.session_id is None
    assert not adapter.probe()['safety_flags_verified']
    adapter.shutdown()


@pytest.mark.parametrize('version', ['Claude Code 2.1.247', 'unrecognized output'])
def test_preflight_older_or_unparseable_claude_rejected(monkeypatch, version):
    adapter, seen = preflight_fixture(monkeypatch, 'claude', version=version)
    with pytest.raises(AdapterError, match='runtime_version_unsupported'):
        adapter.preflight()
    adapter.shutdown()


def test_codex_0159_help_without_app_server_safety_flags_is_blocked(monkeypatch):
    adapter, seen = preflight_fixture(monkeypatch, 'codex', missing='--ignore-user-config', version='codex-cli 0.159.2')
    with pytest.raises(AdapterError, match='runtime_version_unsupported'):
        adapter.preflight()
    assert adapter.runtime_version == '0.159.2' and adapter.calls == 0
    assert not any('app-server' in command for command in seen)
    adapter.shutdown()


def test_codex_flags_present_but_invalid_in_app_server_scope_is_blocked(monkeypatch):
    adapter, seen = preflight_fixture(monkeypatch, 'codex')
    local = __import__('talkabout.agent_runtime', fromlist=['execute_bounded']).execute_bounded
    def unsupported(command, *args, **kwargs):
        if 'app-server' in command:
            raise AdapterError('runtime_failed')
        return local(command, *args, **kwargs)
    monkeypatch.setattr('talkabout.agent_runtime.execute_bounded', unsupported)
    with pytest.raises(AdapterError, match='runtime_version_unsupported'):
        adapter.preflight()
    assert adapter.calls == 0 and not adapter.probe()['safety_flags_verified']
    adapter.shutdown()


def test_run_enforces_preflight_without_caller_help_check(monkeypatch):
    adapter, seen = preflight_fixture(monkeypatch, 'claude', missing='--restricted')
    monkeypatch.setattr('talkabout.agent_runtime._JSONLTransport', lambda *args: pytest.fail('No runtime launch allowed'))
    with pytest.raises(AdapterError, match='runtime_version_unsupported'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    assert adapter.calls == 0 and adapter._room_id is None
    adapter.shutdown()


def exec_runtime(monkeypatch, behavior='success'):
    adapter, peer = runtime(monkeypatch, 'codex')
    adapter.codex_transport = 'exec-json'
    monkeypatch.setattr(StructuredRuntime, '_check_codex_exec_auth', lambda *args: None)
    original = peer.factory
    def factory(*args):
        transport = original(*args)
        transport.exit_verified = False
        def send_text(prompt, cancel, deadline):
            transport.check(cancel, deadline)
            transport.sent.append({'stdin_prompt': prompt})
            peer.turns += 1
            transport.put({'type': 'thread.started', 'thread_id': 'exec-session' if behavior != 'wrong_room' else 'other-session'})
            transport.put({'type': 'turn.started'})
            if behavior == 'tool':
                transport.put({'type': 'item.started', 'item': {'id': 'call-one', 'type': 'command_execution'}})
                return
            if behavior == 'failed':
                transport.put({'type': 'turn.failed', 'error': {'message': 'synthetic private failure'}})
                return
            transport.put({'type': 'item.completed', 'item': {'id': 'answer-one', 'type': 'agent_message', 'text': 'Exec answer ' + str(peer.turns)}})
            if behavior == 'truncated':
                transport.events.put(None)
                return
            if behavior == 'hold':
                return
            transport.put({'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 5}})
        def wait_success(cancel, deadline):
            if behavior == 'bad_exit':
                raise AdapterError('runtime_failed')
            transport.exit_verified = True
        transport.send_text = send_text
        transport.wait_success = wait_success
        return transport
    monkeypatch.setattr('talkabout.agent_runtime._JSONLTransport', factory)
    return adapter, peer


def test_codex_exec_default_two_turns_use_explicit_session_and_stdin(monkeypatch):
    adapter, peer = exec_runtime(monkeypatch)
    assert StructuredRuntime('codex', executable=sys.executable).codex_transport == 'exec-json'
    assert adapter.run({'room_id': 'room-one', 'topic': 'test'}, threading.Event()) == 'Exec answer 1'
    assert adapter.run({'room_id': 'room-one', 'topic': 'continue'}, threading.Event()) == 'Exec answer 2'
    assert adapter.session_id == 'exec-session'
    command = peer.transports[1].command
    assert command[:4] == [sys.executable, 'exec', '--sandbox', 'read-only']
    assert command[command.index('resume') + 1] == 'exec-session'
    assert command[-1] == '-'
    assert '--last' not in command and '--ephemeral' not in command
    assert all(flag in command for flag in ['--ignore-user-config', '--strict-config', '--ignore-rules', '--json'])
    assert 'approval_policy="never"' in command
    assert 'features.hooks=false' in command and 'features.view_image=false' in command
    assert 'continue' not in command
    assert all(transport.exit_verified and transport.closed for transport in peer.transports)
    adapter.shutdown()


@pytest.mark.parametrize('behavior,code', [('truncated', 'runtime_outcome_unknown'),
                                         ('tool', 'runtime_tool_policy_violation'),
                                         ('failed', 'runtime_failed'), ('bad_exit', 'runtime_failed')])
def test_codex_exec_no_success_on_partial_tool_failure_or_bad_exit(monkeypatch, behavior, code):
    adapter, peer = exec_runtime(monkeypatch, behavior)
    with pytest.raises(AdapterError, match=code):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    assert peer.transports[0].closed and adapter.last_outcome == 'unknown'
    with pytest.raises(AdapterError, match='runtime_outcome_unknown'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    assert len(peer.transports) == 1
    adapter.shutdown()


def test_runtime_preserves_safe_diagnostics_after_failed_transport_cleanup(monkeypatch):
    adapter, peer = exec_runtime(monkeypatch, 'truncated')
    original = __import__('talkabout.agent_runtime', fromlist=['_JSONLTransport'])._JSONLTransport
    def factory(*args):
        transport = original(*args)
        transport.diagnostics = lambda: {'cleanup_confirmed': transport.closed, 'stdout_frames': 3}
        return transport
    monkeypatch.setattr('talkabout.agent_runtime._JSONLTransport', factory)
    with pytest.raises(AdapterError, match='runtime_outcome_unknown'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    assert adapter.last_diagnostics == {'cleanup_confirmed': True, 'stdout_frames': 3}
    assert adapter.calls == 1 and adapter.last_outcome == 'unknown'
    with pytest.raises(AdapterError, match='runtime_outcome_unknown'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    assert len(peer.transports) == 1  # Diagnostic capture cannot permit a replay.
    adapter.shutdown()


def test_codex_exec_timeout_never_switches_transport(monkeypatch):
    adapter, peer = exec_runtime(monkeypatch, 'hold')
    adapter.timeout = .02
    with pytest.raises(AdapterError, match='runtime_timeout'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    assert len(peer.transports) == 1 and adapter.codex_transport == 'exec-json'
    assert peer.transports[0].closed
    adapter.shutdown()


def test_codex_exec_0159_preflight_checks_both_exec_and_resume_scope(monkeypatch):
    adapter, seen = preflight_fixture(monkeypatch, 'codex', version='codex-cli 0.159.2')
    adapter.codex_transport = 'exec-json'
    required = PREFLIGHT_FLAGS['codex'] + ['--json', '--ignore-rules', '--sandbox', '--skip-git-repo-check']
    def local(command, prompt, cancel, **kwargs):
        seen.append(command)
        if command[-1] == '--version':
            return 'codex-cli 0.159.2'
        assert command[1:2] == ['exec'] and command[-1] == '--help'
        return '\n'.join('  ' + flag for flag in required)
    monkeypatch.setattr('talkabout.agent_runtime.execute_bounded', local)
    assert adapter.preflight()['safety_flags_verified']
    assert [sys.executable, 'exec', '--help'] in seen
    assert [sys.executable, 'exec', 'resume', '--help'] in seen
    assert not any('app-server' in command for command in seen)
    adapter.shutdown()


def test_codex_exec_resume_missing_guard_rejected_before_prompt(monkeypatch):
    adapter, seen = preflight_fixture(monkeypatch, 'codex')
    adapter.codex_transport = 'exec-json'
    def local(command, prompt, cancel, **kwargs):
        if command[-1] == '--version':
            return 'codex-cli 0.159.2'
        flags = PREFLIGHT_FLAGS['codex'] + ['--json', '--ignore-rules', '--sandbox', '--skip-git-repo-check']
        if 'resume' in command:
            flags.remove('--ignore-user-config')
        return '\n'.join('  ' + flag for flag in flags)
    monkeypatch.setattr('talkabout.agent_runtime.execute_bounded', local)
    with pytest.raises(AdapterError, match='runtime_version_unsupported'):
        adapter.preflight()
    assert adapter.calls == 0
    adapter.shutdown()


@pytest.mark.parametrize('status,expected', [('Logged in using ChatGPT\n', None),
                                            ('Logged in using an API key\n', 'runtime_auth_mode_mismatch'),
                                            ('unknown localized output\n', 'runtime_auth_mode_mismatch')])
def test_codex_exec_auth_metadata_is_finite_and_fail_closed(monkeypatch, tmp_path, status, expected):
    adapter = StructuredRuntime('codex', executable=sys.executable)
    adapter._workspace = type('Workspace', (), {'name': str(tmp_path)})()
    adapter._deadline = time.monotonic() + 1
    class Supervisor:
        def start(self, command, cancel, **kwargs):
            assert command == [sys.executable, 'login', 'status']
            assert kwargs['shell'] is False
            kwargs['stderr'].write(status.encode()); kwargs['stderr'].flush()
            return type('Process', (), {'poll': lambda self: 0, 'returncode': 0})()
        def stop(self):
            return True
    if expected:
        with pytest.raises(AdapterError, match='^' + expected + '$'):
            adapter._check_codex_exec_auth(threading.Event(), Supervisor())
    else:
        adapter._check_codex_exec_auth(threading.Event(), Supervisor())
    adapter._workspace = None
    adapter.shutdown()


def test_codex_explicit_api_mode_scopes_official_exec_key_without_saved_login(monkeypatch):
    monkeypatch.delenv('CODEX_API_KEY', raising=False)
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-explicit-api-key')
    adapter = StructuredRuntime('codex', executable=sys.executable, auth_mode='api_key')
    env = adapter._environment()
    assert env['CODEX_API_KEY'] == 'synthetic-explicit-api-key'
    assert 'OPENAI_API_KEY' not in env
    native = StructuredRuntime('codex', executable=sys.executable)
    monkeypatch.setenv('CODEX_API_KEY', 'synthetic-should-not-be-used')
    assert 'CODEX_API_KEY' not in native._environment()
    assert 'OPENAI_API_KEY' not in native._environment()
    adapter.shutdown(); native.shutdown()


def test_grok_explicit_api_isolates_every_profile_root_and_native_auth(monkeypatch):
    original_profile = '/synthetic-original-user-profile'
    profile_names = ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'XDG_CONFIG_HOME',
                     'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME', 'TEMP', 'TMP', 'TMPDIR')
    for name in profile_names:
        monkeypatch.setenv(name, original_profile + '/' + name.lower())
    for name in ('GROK_HOME', 'GROK_AUTH', 'GROK_AUTH_PATH', 'GROK_MODELS_BASE_URL',
                 'GROK_XAI_API_BASE_URL', 'HOMEDRIVE', 'HOMEPATH'):
        monkeypatch.setenv(name, 'synthetic-inherited-value-that-must-not-leak')
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-unselected-openai')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'synthetic-unselected-anthropic')
    adapter, peer = runtime(monkeypatch, 'grok')
    try:
        adapter.run({'room_id': 'room-one'}, threading.Event())
        env = peer.transports[0].env
        from pathlib import Path
        profile = Path(peer.transports[0].cwd) / 'profile'
        for name in profile_names + ('GROK_HOME',):
            directory = Path(env[name])
            assert directory == profile or profile in directory.parents
            assert directory.is_dir()
            assert original_profile not in env[name]
        assert env['HOME'] == env['USERPROFILE']
        assert env['APPDATA'] != env['LOCALAPPDATA']
        assert env['XAI_API_KEY'] == 'synthetic-test-key-not-real'
        assert not {'GROK_AUTH', 'GROK_AUTH_PATH', 'GROK_MODELS_BASE_URL', 'GROK_XAI_API_BASE_URL',
                    'HOMEDRIVE', 'HOMEPATH', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'} & env.keys()
        assert env['GROK_MEMORY'] == '0'
        assert env['GROK_CLAUDE_HOOKS_ENABLED'] == env['GROK_CURSOR_HOOKS_ENABLED'] == '0'
    finally:
        adapter.shutdown()


def test_grok_owned_repo_boundary_is_a_valid_nonbare_git_workdir(monkeypatch, tmp_path):
    import shutil
    import subprocess
    git = shutil.which('git')
    if not git:
        pytest.skip('Local Git unavailable for credential-free layout validation')
    monkeypatch.setenv('XAI_API_KEY', 'synthetic-only')
    adapter = StructuredRuntime('grok', executable=sys.executable, auth_mode='api_key')
    adapter._workspace = type('Workspace', (), {'name': str(tmp_path)})()
    env = adapter._environment()
    nested = tmp_path / 'nested'; nested.mkdir()
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    result = subprocess.run([git, '-C', str(nested), 'rev-parse', '--show-toplevel', '--is-bare-repository'],
                            env=env, capture_output=True, text=True, timeout=5, shell=False)
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert os.path.realpath(lines[0]) == os.path.realpath(tmp_path)
    assert lines[1] == 'false'
    assert not (tmp_path / '.git' / 'hooks').exists()
    assert 'remote' not in (tmp_path / '.git' / 'config').read_text()
    adapter._workspace = None
    adapter.shutdown()


def test_grok_api_launch_remains_blocked_until_exact_binary_isolation_verified(monkeypatch):
    monkeypatch.setenv('XAI_API_KEY', 'synthetic-test-key')
    monkeypatch.setattr('talkabout.agent_runtime._JSONLTransport', lambda *args: pytest.fail('Unverified binary must not start'))
    adapter = StructuredRuntime('grok', executable=sys.executable, auth_mode='api_key')
    assert adapter.probe()['reason'] == 'runtime_workspace_isolation_unverified'
    with pytest.raises(AdapterError, match='runtime_workspace_isolation_unverified'):
        adapter.preflight()
    with pytest.raises(AdapterError, match='runtime_workspace_isolation_unverified'):
        adapter.run({'room_id': 'room-one'}, threading.Event())
    assert adapter.calls == 0 and adapter._workspace is None
    adapter.shutdown()
