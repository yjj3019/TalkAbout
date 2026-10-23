"""Expected public MCP inputs, independent of the production tool definitions."""

ROOM = {'room_id': 'string'}
SESSION = {**ROOM, 'session_id': 'string'}
CONTEXT = {**SESSION, 'generation': 'integer', 'membership_revision': 'integer'}
MUTATION = {'client_mutation_id': 'string'}

# Each entry is (property types, optional property names). In particular, legacy
# propose_text remains usable before joining; reconnect and polling are explicit.
EXPECTED_INPUTS = {
    'list_rooms': ({}, set()),
    'read_room': (ROOM, set()),
    'propose_text': ({**CONTEXT, 'text': 'string', **MUTATION}, {'session_id'}),
    'list_invitations': ({}, set()),
    'accept_invitation': ({**ROOM, 'expected_revision': 'integer', 'acknowledge_private': 'boolean', **MUTATION}, set()),
    'join_room': ({**CONTEXT, 'previous_session_id': 'string'}, {'previous_session_id'}),
    'list_participants': (ROOM, set()),
    'poll_room': ({**SESSION, 'cursor': 'string', 'wait_seconds': 'integer'}, {'cursor', 'wait_seconds'}),
    'ack_room': ({**CONTEXT, 'cursor': 'string', 'state': 'string'}, set()),
    'room_notifications': (SESSION, set()),
    'claim_turn': ({**CONTEXT, 'task_id': 'string', **MUTATION}, set()),
    'post_message': ({**CONTEXT, 'task_id': 'string', 'attempt': 'integer', 'text': 'string', **MUTATION}, set()),
}


def assert_tool_contract(tools):
    names = [tool['name'] for tool in tools]
    assert len(names) == len(set(names)), 'duplicate MCP tool names'
    assert set(names) == set(EXPECTED_INPUTS)
    for tool in tools:
        fields, optional = EXPECTED_INPUTS[tool['name']]
        schema = tool['inputSchema']
        assert schema['type'] == 'object'
        assert schema['additionalProperties'] is False
        assert schema['properties'] == {name: {'type': kind} for name, kind in fields.items()}
        required = schema.get('required', [])
        assert len(required) == len(set(required))
        assert set(required) == set(fields) - optional
