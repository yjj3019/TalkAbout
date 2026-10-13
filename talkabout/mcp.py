import json
import re
import sys

import httpx

from .domain import digest


TOOLS = [
    {"name": "list_rooms", "description": "List rooms permitted by the configured agent credential.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "read_room", "description": "Read one authorized room, including text artifacts.", "inputSchema": {"type": "object", "properties": {"room_id": {"type": "string"}}, "required": ["room_id"], "additionalProperties": False}},
    {"name": "propose_text", "description": "Propose a text artifact through the common API. No uploads or arbitrary commands.",
     "inputSchema": {"type": "object", "properties": {"room_id": {"type": "string"}, "text": {"type": "string"}, "generation": {"type": "integer"}, "membership_revision": {"type": "integer"}, "client_mutation_id": {"type": "string"}},
                     "required": ["room_id", "text", "generation", "membership_revision", "client_mutation_id"], "additionalProperties": False}},
]


class MCPServer:
    def __init__(self, client):
        self.client, self.phase = client, "new"

    @staticmethod
    def error(request_id, code, message):
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    def handle(self, message):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return self.error(None, -32600, "Invalid Request")
        request_id, method, params = message.get("id"), message["method"], message.get("params", {})
        if "id" not in message:
            if method == "notifications/initialized" and self.phase == "initializing":
                self.phase = "ready"
            return None
        if type(request_id) not in {str, int} or not isinstance(params, dict):
            return self.error(None, -32600, "Invalid Request")
        if method == "initialize":
            if self.phase != "new" or not isinstance(params.get("protocolVersion"), str) or not isinstance(params.get("capabilities"), dict) or not isinstance(params.get("clientInfo"), dict):
                return self.error(request_id, -32602, "Invalid initialization")
            self.phase = "initializing"
            version = params["protocolVersion"] if params["protocolVersion"] in {"2025-11-25", "2025-06-18"} else "2025-11-25"
            return {"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "TalkAbout", "version": "0.1.0"}, "instructions": "Text-only API adapter; not a persistent worker. Credentials are separately configured."}}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}
        if self.phase != "ready":
            return self.error(request_id, -32000, "Initialization required")
        if method == "tools/list":
            if params:
                return self.error(request_id, -32602, "No pagination cursor supported")
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
        if method != "tools/call":
            return self.error(request_id, -32601, "Method not found")
        name, arguments = params.get("name"), params.get("arguments", {})
        tool = next((t for t in TOOLS if t["name"] == name), None)
        if not tool or set(params) - {"name", "arguments"} or not isinstance(arguments, dict):
            return self.error(request_id, -32602, "Invalid tool")
        schema = tool["inputSchema"]
        if set(arguments) - set(schema["properties"]) or not set(schema.get("required", [])) <= set(arguments):
            return self.error(request_id, -32602, "Invalid arguments")
        for key, value in arguments.items():
            expected = int if schema["properties"][key]["type"] == "integer" else str
            if type(value) is not expected:
                return self.error(request_id, -32602, "Invalid argument type")
        if "room_id" in arguments and not re.fullmatch(r"[A-Za-z0-9-]{1,64}", arguments["room_id"]):
            return self.error(request_id, -32602, "Invalid room id")
        try:
            if name == "list_rooms":
                data = self.client.request("GET", "rooms")
            elif name == "read_room":
                data = self.client.request("GET", "rooms/" + arguments["room_id"])
            else:
                mutation_id = arguments["client_mutation_id"]
                if not 1 <= len(mutation_id) <= 128:
                    return self.error(request_id, -32602, "Invalid mutation id")
                body = {k: arguments[k] for k in ("text", "generation", "membership_revision")}
                data = self.client.request("POST", "rooms/" + arguments["room_id"] + "/artifacts", body, "mcp:" + digest(mutation_id))
            result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}], "isError": False}
        except httpx.HTTPStatusError as exc:
            result = {"content": [{"type": "text", "text": "API operation rejected: " + str(exc.response.status_code)}], "isError": True}
        except Exception:
            result = {"content": [{"type": "text", "text": "API operation failed"}], "isError": True}
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def serve(self, input_stream=None, output_stream=None):
        if input_stream is None and hasattr(sys.stdin, 'reconfigure'):
            sys.stdin.reconfigure(encoding='utf-8')
        if output_stream is None and hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', newline='\n')
        input_stream, output_stream = input_stream or sys.stdin, output_stream or sys.stdout
        while True:
            line = input_stream.readline(100001)
            if not line:
                return
            if len(line) > 100000 or not line.endswith("\n"):
                response = self.error(None, -32700, "Invalid message framing")
            else:
                try:
                    response = self.handle(json.loads(line))
                except ValueError:
                    response = self.error(None, -32700, "Parse error")
            if response is not None:
                output_stream.write(json.dumps(response, ensure_ascii=True) + "\n")
                output_stream.flush()
