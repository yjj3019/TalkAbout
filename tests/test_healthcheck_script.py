import http.server
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "podman" / "healthcheck.sh"
# Runs in the Linux posix-containment job: the Windows safe-regression guard blocks sh/curl.
pytestmark = pytest.mark.skipif(os.name != "posix" or not (shutil.which("sh") and shutil.which("curl")),
                                reason="needs POSIX sh and curl")


class _Server:
    def __init__(self):
        self.ready_status = 503
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/ready":
                    body = b'{"status":"ready"}' if outer.ready_status == 200 else b'{"status":"not_ready","scheduler":"failed"}'
                    self.send_response(outer.ready_status)
                else:
                    body = b'{"status":"development","scheduler":"failed"}'
                    self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = _Server()
    yield s
    s.close()


def _run(server, tmp_path, alert=True):
    marker = tmp_path / "alerts.txt"
    env = {
        **os.environ,
        "TALKABOUT_READY_URL": f"http://127.0.0.1:{server.port}/ready",
        "TALKABOUT_HEALTH_STATE_FILE": (tmp_path / "state" / "health.state").as_posix(),
    }
    if alert:
        env["TALKABOUT_ALERT_COMMAND"] = f'echo "$TALKABOUT_ALERT_STATE" >> "{marker.as_posix()}"'
    result = subprocess.run(["sh", SCRIPT.as_posix()], env=env, capture_output=True, text=True, timeout=60)
    alerts = marker.read_text().split() if marker.exists() else []
    return result, alerts


def test_failure_records_bodies_and_alerts_only_on_transitions(server, tmp_path):
    first, alerts = _run(server, tmp_path)
    assert first.returncode == 1
    assert "not ready: HTTP 503" in first.stdout
    assert '"scheduler":"failed"' in first.stdout
    assert alerts == ["not_ready"]

    again, alerts = _run(server, tmp_path)
    assert again.returncode == 1
    assert alerts == ["not_ready"], "a repeated failure must not alert again"

    server.ready_status = 200
    recovered, alerts = _run(server, tmp_path)
    assert recovered.returncode == 0
    assert recovered.stdout.strip() == "talkabout ready"
    assert alerts == ["not_ready", "ready"]

    steady, alerts = _run(server, tmp_path)
    assert steady.returncode == 0
    assert alerts == ["not_ready", "ready"]


def test_first_healthy_observation_and_missing_hook_stay_silent(server, tmp_path):
    server.ready_status = 200
    ok, alerts = _run(server, tmp_path)
    assert ok.returncode == 0 and alerts == []

    server.ready_status = 503
    failed, alerts = _run(server, tmp_path, alert=False)
    assert failed.returncode == 1 and alerts == []
    assert "alert command failed" not in failed.stdout


def test_unreachable_server_is_not_ready(tmp_path):
    env = {
        **os.environ,
        "TALKABOUT_READY_URL": "http://127.0.0.1:1/ready",
        "TALKABOUT_HEALTH_STATE_FILE": (tmp_path / "health.state").as_posix(),
    }
    result = subprocess.run(["sh", SCRIPT.as_posix()], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 1
    assert "not ready" in result.stdout
