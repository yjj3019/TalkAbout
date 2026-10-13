import json
import time

import httpx
import pytest

from talkabout.worker import APIClient, Journal, SyntheticRuntime, Worker
from conftest import LocalClient


def test_api_impersonation_and_text_only(fixture):
    store, _, rid = fixture
    client = LocalClient(store)
    with pytest.raises(httpx.HTTPStatusError) as error:
        client.request("POST", f"rooms/{rid}/messages", {"kind": "advice", "actor_id": "human", "text": "x"}, "spoof")
    assert error.value.response.status_code in {403, 422}
    with pytest.raises(httpx.HTTPStatusError) as error:
        client.request("POST", f"rooms/{rid}/uploads", {}, "binary")
    assert error.value.response.json()["code"] == "feature_disabled"


def test_human_advice_does_not_create_task(fixture):
    store, core, rid = fixture
    before = len(store.rooms[rid]["tasks"])
    LocalClient(store, "fixture-human").request("POST", f"rooms/{rid}/messages", {"kind": "advice", "client_message_id": "advice-1", "text": "pause", "reply_to": None, "mentions": []}, "advice")
    assert len(store.rooms[rid]["tasks"]) == before
    assert core.read("fixture-human", rid)["state"] == "active"


def test_worker_response_loss_restart_does_not_repeat_runtime(fixture, tmp_path):
    store, core, rid = fixture
    class LostResponse(LocalClient):
        lost = False
        def request(self, method, path, body=None, key=None, params=None):
            response = super().request(method, path, body, key, params)
            if path.endswith("/complete") and not self.lost:
                self.lost = True
                raise httpx.ReadTimeout("synthetic lost response")
            return response
    class Counted(SyntheticRuntime):
        calls = 0
        def run(self, context, cancel):
            self.calls += 1
            assert "fixture-a" not in json.dumps(context)
            return super().run(context, cancel)
    runtime = Counted()
    journal = Journal(tmp_path / "journal.sqlite")
    with pytest.raises(httpx.ReadTimeout):
        Worker(LostResponse(store), journal, runtime).step()
    assert journal.status() == {"result_saved": 1}
    journal.close()
    restarted = Journal(tmp_path / "journal.sqlite")
    assert Worker(LocalClient(store), restarted, runtime).step() == "completed"
    assert runtime.calls == 1
    assert len(core.read("fixture-human", rid)["messages"]) == 1
    restarted.close()


def test_worker_durable_inbox_and_uncertain_call(fixture, tmp_path):
    store, _, _ = fixture
    journal = Journal(tmp_path / "journal.sqlite")
    worker = Worker(LocalClient(store), journal, SyntheticRuntime())
    worker.poll()
    tid, _, payload = journal.next()
    worker.poll()
    assert journal.status() == {"ready": 1}
    journal.save(tid, "call_started", payload)
    journal.close()
    recovered = Journal(tmp_path / "journal.sqlite")
    assert Worker(LocalClient(store), recovered, SyntheticRuntime()).step() == "uncertain"
    assert recovered.status() == {"uncertain": 1}
    recovered.close()


def test_heartbeat_runs_separately_and_failure_blocks_submit(fixture, tmp_path):
    store, _, _ = fixture
    class Slow(SyntheticRuntime):
        def run(self, context, cancel):
            cancel.wait(0.08)
            return "slow synthetic response"
    client = LocalClient(store)
    journal = Journal(tmp_path / "success.sqlite")
    assert Worker(client, journal, Slow(), heartbeat_interval=0.01).step() == "completed"
    assert any(path.endswith("/heartbeat") for _, path in client.calls)
    journal.close()


def test_heartbeat_failure_never_completes(fixture, tmp_path):
    store, core, rid = fixture
    class FailsHeartbeat(LocalClient):
        def request(self, method, path, body=None, key=None, params=None):
            if path.endswith("/heartbeat"):
                raise httpx.ConnectError("synthetic heartbeat outage")
            return super().request(method, path, body, key, params)
    class Slow(SyntheticRuntime):
        def run(self, context, cancel):
            cancel.wait(0.08)
            return "late synthetic response"
    journal = Journal(tmp_path / "failure.sqlite")
    assert Worker(FailsHeartbeat(store), journal, Slow(), heartbeat_interval=0.01).step() == "heartbeat_failed"
    assert core.read("fixture-human", rid)["messages"] == []
    journal.close()


def test_transport_allowlist_and_https():
    with pytest.raises(ValueError, match="https_required"):
        APIClient("http://example.invalid", "fixture", "http://example.invalid")
    with pytest.raises(ValueError, match="invalid_origin"):
        APIClient("https://example.invalid", "fixture", "https://other.invalid")
