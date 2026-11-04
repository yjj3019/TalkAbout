"""Optional proposal criteria pass through MCP as inert owner-review data."""
import pytest

from talkabout.mcp import MCPServer


class Recorder:
    def __init__(self):
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {'state': 'proposed', 'execution_started': False}


def setup():
    client = Recorder()
    server = MCPServer(client)
    server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                   'params': {'protocolVersion': '2025-11-25', 'capabilities': {},
                              'clientInfo': {'name': 'fixture', 'version': '1'}}})
    server.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
    return server, client


def call(server, **extra):
    args = {'room_id': 'room-1', 'task_id': 'task-1', 'attempt': 1, 'generation': 1,
            'membership_revision': 1, 'target_id': 'b', 'kind': 'review', 'summary': 'Review',
            'requested_output': 'Risks', 'client_mutation_id': 'm1', **extra}
    return server.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                          'params': {'name': 'propose_collaboration', 'arguments': args}})


def test_optional_criteria_forwarded_only_when_present():
    server, client = setup()
    assert not call(server)['result']['isError']
    assert 'done_when' not in client.calls[-1][0][2]
    assert not call(server, done_when=['Check tests'])['result']['isError']
    assert client.calls[-1][0][2]['done_when'] == ['Check tests']
    assert client.calls[-1][0][1] == 'rooms/room-1/collaboration-proposals'


@pytest.mark.parametrize('value', [[], [' '], ['x'] * 9, ['x' * 201], ['x\x00y'], [False], 'x'])
def test_invalid_criteria_rejected_before_api(value):
    server, client = setup()
    assert call(server, done_when=value)['error']['code'] == -32602
    assert not client.calls
