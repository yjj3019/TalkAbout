import io
import json
import threading
import time

import pytest

from talkabout.mcp import MCPServer
from talkabout.runtime import RuntimeTimeout, bounded_call, validate_adapter_policy
from talkabout.worker import Journal, SyntheticRuntime, Worker
from conftest import LocalClient
from mcp_contract import EXPECTED_INPUTS, assert_tool_contract


def ready(server):
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "fixture", "version": "1"}}})
    assert response["result"]["protocolVersion"] == "2025-11-25"
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_mcp_lifecycle_tools_and_no_secret_arguments(fixture):
    store, _, rid = fixture
    server = MCPServer(LocalClient(store))
    assert server.handle({"jsonrpc": "2.0", "id": 0, "method": "tools/list"})["error"]["code"] == -32000
    ready(server)
    tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assert_tool_contract(tools)
    rejected = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "read_room", "arguments": {"room_id": rid, "token": "fixture-forbidden"}}})
    assert rejected["error"]["code"] == -32602
    result = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "read_room", "arguments": {"room_id": rid}}})
    assert not result["result"]["isError"]


@pytest.mark.parametrize('name', EXPECTED_INPUTS)
def test_mcp_input_contract_rejected_before_api(name):
    class NoRequests:
        def __init__(self): self.calls = []
        def request(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            raise AssertionError('invalid MCP input reached API')
    client = NoRequests()
    server = MCPServer(client)
    ready(server)
    fields, optional = EXPECTED_INPUTS[name]
    values = {'string': 'fixture-value', 'integer': 1, 'boolean': True}
    arguments = {key: values[kind] for key, kind in fields.items() if key not in optional}
    def rejected(args):
        response = server.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                                  'params': {'name': name, 'arguments': args}})
        assert response['error']['code'] == -32602
        assert not client.calls
    # The configured credential supplies identity and authority, never tool input.
    for injected in ('token', 'actor_id', 'role', 'provider', 'credential_id'):
        rejected({**arguments, injected: 'fixture-forbidden'})
    for required in set(fields) - optional:
        rejected({key: value for key, value in arguments.items() if key != required})
    for key, kind in fields.items():
        # bool is an int subclass in Python, but is not an MCP integer.
        rejected({**arguments, key: True if kind == 'integer' else 1})


def test_mcp_scope_pause_idempotency_and_stdio(fixture):
    store, core, rid = fixture
    server = MCPServer(LocalClient(store))
    ready(server)
    other = core.create('fixture-b', {'topic': 'Private other fixture'}, 'other')
    for name in ('read_room', 'list_participants'):
        denied = server.handle({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
                                'params': {'name': name, 'arguments': {'room_id': other['id']}}})
        assert denied['result']['isError']
    message = {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "propose_text", "arguments": {"room_id": rid, "text": "fixture text", "generation": 1, "membership_revision": 2, "client_mutation_id": "fixture-artifact"}}}
    a, b = server.handle(message), server.handle(message)
    assert a["result"] == b["result"] and len(core.read("fixture-a", rid)["artifacts"]) == 1
    from conftest import control
    control(core, rid, "pause")
    message["params"]["arguments"]["client_mutation_id"] = "after-pause"
    assert server.handle(message)["result"]["isError"]
    output = io.StringIO()
    server.serve(io.StringIO('{invalid}\n'+json.dumps({"jsonrpc":"2.0","id":8,"method":"ping"})+'\n'), output)
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert replies[0]["error"]["code"] == -32700 and replies[1]["result"] == {}
    assert "fixture-a" not in output.getvalue()


def test_bounded_runtime_timeout_cancellation_and_error():
    release, finished = threading.Event(), threading.Event()
    class IgnoresCancel:
        def run(self, context, cancel):
            release.wait(2)
            finished.set()
            return "late"
    cancel = threading.Event()
    try:
        with pytest.raises(RuntimeTimeout) as error:
            bounded_call(IgnoresCancel(), {}, cancel, 0.02)
        # Prove the caller returns while an uncooperative runtime is still active,
        # instead of asserting a brittle 70ms Windows thread-scheduling threshold.
        assert cancel.is_set() and not finished.is_set() and not error.value.runtime_stopped
    finally:
        release.set()
        assert finished.wait(1)
    class Fails:
        def run(self, context, cancel): raise ValueError("fixture error")
    with pytest.raises(ValueError): bounded_call(Fails(), {}, threading.Event(), 1)
    for policy in [{"approval_bypass":True},{"external":True,"tool_limits_verified":False},{"allowed_tools":["shell"]}]:
        with pytest.raises(ValueError): validate_adapter_policy(policy)


def test_worker_timeout_never_publishes(fixture, tmp_path):
    store, core, rid = fixture
    class Slow(SyntheticRuntime):
        def run(self, context, cancel):
            cancel.wait(0.1)
            return "late fixture"
    journal = Journal(tmp_path / "timeout.sqlite")
    assert Worker(LocalClient(store), journal, Slow(), runtime_timeout=0.02).step() == "runtime_timeout"
    assert journal.status() == {"uncertain": 1} and not core.read("fixture-human", rid)["messages"]
    journal.close()
