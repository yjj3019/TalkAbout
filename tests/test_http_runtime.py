"""HTTP provider tests. They never reach a real provider: a local server answers."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from talkabout import providers
from talkabout.adapters import AdapterError
from talkabout.http_runtime import HttpChatRuntime


class _Server:
    def __init__(self, status=200, body=None, delay=0.0, raw=None):
        self.status = status
        self.body = body if body is not None else {"id": "resp-1", "choices": [{"message": {"content": "hello from the room"}}]}
        self.delay = delay
        self.raw = raw
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                payload = self.rfile.read(length)
                outer.requests.append({"path": self.path, "headers": dict(self.headers), "body": json.loads(payload)})
                if outer.delay:
                    time.sleep(outer.delay)
                if outer.raw is not None:
                    data = outer.raw
                else:
                    data = json.dumps(outer.body).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def server():
    instance = _Server()
    yield instance
    instance.close()


def _runtime(server, **kwargs):
    return HttpChatRuntime("compatible", base_url=kwargs.pop("base_url", server.url), model="test-model", **kwargs)


def test_registry_covers_cli_and_http_providers_and_aliases():
    assert {"codex", "claude", "grok"} <= set(providers.cli_ids())
    assert {"deepseek", "xai", "openrouter", "groq", "together", "mistral", "openai", "ollama", "lmstudio", "compatible"} <= set(providers.http_ids())
    assert providers.resolve("openai-compatible") == "compatible"
    assert providers.resolve("chatgpt") == "codex"
    assert providers.resolve("grok-api") == "xai"
    assert providers.resolve("nope") is None
    assert all(item["id"] and item["label"] and item["transport"] in {"cli", "http"} for item in providers.catalog())


def test_probe_requires_configuration(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    runtime = HttpChatRuntime("deepseek")
    report = runtime.probe()
    assert report["available"] is False
    assert report["reason"] == "runtime_auth_required"
    assert report["endpoint"] == "https://api.deepseek.com"
    assert report["credential_present"] is False
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key")
    assert HttpChatRuntime("deepseek").probe()["available"] is True
    assert HttpChatRuntime("compatible").probe()["reason"] == "provider_url_required"
    assert HttpChatRuntime("compatible", base_url="http://127.0.0.1:9/v1").probe()["reason"] == "provider_model_required"


def test_rejects_cli_provider_and_missing_room(monkeypatch):
    with pytest.raises(AdapterError, match="unsupported_runtime"):
        HttpChatRuntime("codex")
    monkeypatch.setenv("PROVIDER_API_KEY", "synthetic-key")
    runtime = HttpChatRuntime("compatible", base_url="http://127.0.0.1:9/v1", model="m")
    with pytest.raises(AdapterError, match="runtime_room_required"):
        runtime.run({"prompt": "hi"}, threading.Event())


def test_run_posts_openai_compatible_request(server, monkeypatch):
    monkeypatch.setenv("PROVIDER_API_KEY", "synthetic-key")
    runtime = _runtime(server)
    text = runtime.run({"purpose": "contribution", "room_id": "room-1", "prompt": "Say hello"}, threading.Event())
    assert text == "hello from the room"
    assert runtime.session_id.startswith("compatible:")
    request = server.requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer synthetic-key"
    assert request["body"]["model"] == "test-model"
    assert request["body"]["stream"] is False
    assert request["body"]["messages"][0]["role"] == "system"
    assert "room-1" in request["body"]["messages"][1]["content"]


def test_http_errors_map_to_fixed_codes(monkeypatch):
    monkeypatch.setenv("PROVIDER_API_KEY", "synthetic-key")
    for status, code in ((401, "runtime_auth_required"), (403, "runtime_auth_required"),
                         (429, "runtime_rate_limited"), (500, "runtime_failed")):
        instance = _Server(status=status, body={"error": "secret-body"})
        try:
            runtime = _runtime(instance)
            with pytest.raises(AdapterError, match=f"^{code}$"):
                runtime.run({"room_id": "room-1", "prompt": "x"}, threading.Event())
        finally:
            instance.close()


def test_invalid_payloads_fail_closed(monkeypatch):
    monkeypatch.setenv("PROVIDER_API_KEY", "synthetic-key")
    for kwargs in ({"raw": b"not json"}, {"body": {"choices": []}},
                   {"body": {"choices": [{"message": {"content": "   "}}]}}):
        instance = _Server(**kwargs)
        try:
            runtime = _runtime(instance)
            with pytest.raises(AdapterError, match="runtime_invalid_output"):
                runtime.run({"room_id": "room-1", "prompt": "x"}, threading.Event())
        finally:
            instance.close()


def test_timeout_is_bounded(monkeypatch):
    monkeypatch.setenv("PROVIDER_API_KEY", "synthetic-key")
    instance = _Server(delay=1.5)
    try:
        runtime = _runtime(instance, timeout=0.4)
        started = time.monotonic()
        with pytest.raises(AdapterError, match="runtime_timeout"):
            runtime.run({"room_id": "room-1", "prompt": "x"}, threading.Event())
        assert time.monotonic() - started < 1.4
    finally:
        instance.close()


def test_platform_credential_never_enters_the_prompt(monkeypatch):
    monkeypatch.setenv("PROVIDER_API_KEY", "synthetic-key")
    instance = _Server()
    try:
        runtime = HttpChatRuntime("compatible", base_url=instance.url, model="test-model",
                                  platform_credential="talkabout-secret-token")
        with pytest.raises(AdapterError, match="runtime_secret_in_context"):
            runtime.run({"room_id": "room-1", "prompt": "token talkabout-secret-token"}, threading.Event())
        assert instance.requests == []
    finally:
        instance.close()


def test_cli_factory_selects_transport(monkeypatch):
    from talkabout.agent_runtime import StructuredRuntime
    from talkabout.cli import provider_runtime
    options = {"credential_env": "TALKABOUT_TOKEN", "platform_credential": None, "run_deadline": None, "auth_mode": "native"}
    cli_runtime = provider_runtime("codex", options)
    assert isinstance(cli_runtime, StructuredRuntime)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key")
    http_runtime = provider_runtime("deepseek", options, config={})
    assert isinstance(http_runtime, HttpChatRuntime)
    assert http_runtime.probe()["available"] is True
    assert http_runtime.probe()["model"] == "deepseek-chat"
