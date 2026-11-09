import http.server
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import parse_qs

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "podman" / "alert-notify.sh"
pytestmark = pytest.mark.skipif(not (shutil.which("sh") and shutil.which("curl")), reason="needs sh and curl")
TOKEN = "123456:SECRET-token_value"


class _Receiver:
    def __init__(self, status=200):
        self.status = status
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
                outer.requests.append((self.path, self.headers.get("Content-Type", ""), body))
                self.send_response(outer.status)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def receiver():
    r = _Receiver()
    yield r
    r.close()


def _run(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TELEGRAM_", "SLACK_", "TALKABOUT_"))}
    env.update({"TALKABOUT_ALERT_STATE": "not_ready",
                "TALKABOUT_ALERT_MESSAGE": 'talkabout not_ready (HTTP 503); previous=ready; http://127.0.0.1:18081/ready "q" $(x)',
                **extra})
    return subprocess.run(["sh", SCRIPT.as_posix()], env=env, capture_output=True, text=True, timeout=60)


def test_nothing_configured_is_a_silent_success():
    result = _run()
    assert result.returncode == 0 and result.stdout == "" and result.stderr == ""


def test_telegram_message_goes_to_the_bot_endpoint_as_a_form(receiver):
    result = _run(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_CHAT_ID="-1001", TALKABOUT_TELEGRAM_API=receiver.base)
    assert result.returncode == 0
    (path, content_type, body), = receiver.requests
    assert path == f"/bot{TOKEN}/sendMessage"
    form = parse_qs(body)
    assert form["chat_id"] == ["-1001"] and form["text"][0].startswith("[ALERT] talkabout not_ready (HTTP 503)")
    assert '"' not in form["text"][0] and "$(" not in form["text"][0]


def test_slack_message_is_valid_json_without_injectable_characters(receiver):
    result = _run(SLACK_WEBHOOK_URL=receiver.base + "/services/T/B/token")
    assert result.returncode == 0
    (path, content_type, body), = receiver.requests
    assert path == "/services/T/B/token" and content_type == "application/json"
    assert json.loads(body)["text"].startswith("[ALERT] talkabout not_ready")


def test_recovery_uses_a_distinct_label(receiver):
    result = _run(SLACK_WEBHOOK_URL=receiver.base + "/h", TALKABOUT_ALERT_STATE="ready")
    assert result.returncode == 0 and json.loads(receiver.requests[0][2])["text"].startswith("[RECOVERED]")


def test_both_channels_receive_and_secrets_are_not_printed(receiver):
    result = _run(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_CHAT_ID="1", TALKABOUT_TELEGRAM_API=receiver.base,
                  SLACK_WEBHOOK_URL=receiver.base + "/hook")
    assert result.returncode == 0 and len(receiver.requests) == 2
    assert TOKEN not in result.stdout + result.stderr


def test_one_failing_channel_does_not_hide_the_other(receiver):
    result = _run(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_CHAT_ID="1", TALKABOUT_TELEGRAM_API="http://127.0.0.1:9",
                  SLACK_WEBHOOK_URL=receiver.base + "/hook")
    assert result.returncode == 0 and len(receiver.requests) == 1
    assert "telegram delivery failed" in result.stderr and TOKEN not in result.stderr


def test_all_configured_channels_failing_is_a_nonzero_exit():
    result = _run(SLACK_WEBHOOK_URL="http://127.0.0.1:9/hook")
    assert result.returncode == 1 and "slack delivery failed" in result.stderr


def test_telegram_needs_both_token_and_chat_id(receiver):
    result = _run(TELEGRAM_BOT_TOKEN=TOKEN, TALKABOUT_TELEGRAM_API=receiver.base)
    assert result.returncode == 0 and receiver.requests == []
