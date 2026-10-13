from __future__ import annotations

import json
import sqlite3
import threading
import time
from urllib.parse import urlsplit

import httpx

from .domain import digest
from .runtime import RuntimeTimeout, bounded_call, validate_adapter_policy


class APIClient:
    def __init__(self, base_url, token, allowed_origin, transport=None):
        parsed = urlsplit(base_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin != allowed_origin or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("invalid_origin")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("https_required")
        self.client = httpx.Client(base_url=base_url.rstrip("/") + "/api/v1/", headers={"Authorization": "Bearer " + token},
                                   follow_redirects=False, timeout=15, transport=transport)

    def request(self, method, path, body=None, key=None, params=None):
        headers = {"Idempotency-Key": key} if key else {}
        response = self.client.request(method, path, json=body, headers=headers, params=params)
        response.raise_for_status()
        return response.json()

    def close(self):
        self.client.close()


class Journal:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT);"
                             "CREATE TABLE IF NOT EXISTS inbox(id TEXT PRIMARY KEY,payload TEXT NOT NULL);"
                             "CREATE TABLE IF NOT EXISTS queue(id TEXT PRIMARY KEY,state TEXT NOT NULL,payload TEXT NOT NULL);")

    def setting(self, key, default=None):
        row = self.db.execute("SELECT v FROM settings WHERE k=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, json.dumps(value)))

    def ingest(self, response, actor_id):
        with self.db:
            for event in response["events"]:
                self.db.execute("INSERT OR IGNORE INTO inbox VALUES(?,?)", (event["event_id"], json.dumps(event)))
                if event["type"] == "task.queued" and event.get("assignee") == actor_id:
                    self.db.execute("INSERT OR IGNORE INTO queue VALUES(?,?,?)", (event["task_id"], "ready", json.dumps({"event": event})))
            self.set_setting("cursor", response["next_cursor"])

    def save(self, task_id, state, payload):
        with self.db:
            self.db.execute("UPDATE queue SET state=?,payload=? WHERE id=?", (state, json.dumps(payload), task_id))

    def next(self):
        row = self.db.execute("SELECT id,state,payload FROM queue WHERE state NOT IN ('done','failed','uncertain') ORDER BY rowid LIMIT 1").fetchone()
        return (row[0], row[1], json.loads(row[2])) if row else None

    def status(self):
        return {state: count for state, count in self.db.execute("SELECT state,count(*) FROM queue GROUP BY state")}

    def close(self):
        self.db.close()


class SyntheticRuntime:
    """Local fixture only; never represents a real model or external agent."""
    def run(self, context, cancel):
        return f"Synthetic fixture contribution: {context['topic']}"


class Worker:
    def __init__(self, client, journal, runtime, heartbeat_interval=3, runtime_timeout=30, adapter_policy=None):
        self.client, self.journal, self.runtime = client, journal, runtime
        self.heartbeat_interval = heartbeat_interval
        self.runtime_timeout = runtime_timeout
        validate_adapter_policy(adapter_policy or {"external": False, "allowed_tools": []})
        self.actor = client.request("GET", "agents/me")
        self.actor_id = self.actor["id"]

    def poll(self):
        cursor = self.journal.setting("cursor")
        try:
            response = self.client.request("GET", "agents/me/events", params={"cursor": cursor, "wait_seconds": 0} if cursor else {"wait_seconds": 0})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {409, 410}:
                raise
            response = self.client.request("GET", "agents/me/snapshot", params={"previous_cursor": cursor} if cursor else {})
            # Rebase may reveal queued work after a retention gap.
            for room in response.get("rooms", []):
                for task in room["tasks"]:
                    if task["state"] == "queued" and task["assignee"] == self.actor_id:
                        response["events"].append({"event_id": "snapshot:" + task["id"], "room_id": room["id"], "type": "task.queued", "task_id": task["id"], "assignee": self.actor_id})
        self.journal.ingest(response, self.actor_id)

    def step(self):
        if self.journal.setting("stop", False):
            return "stopped"
        self.poll()
        entry = self.journal.next()
        if not entry:
            return "waiting"
        tid, state, payload = entry
        rid = payload["event"]["room_id"]
        prefix = f"rooms/{rid}/tasks/{tid}"
        if state == "call_started":
            self.journal.save(tid, "uncertain", payload)
            return "uncertain"
        try:
            if state == "ready":
                task = self.client.request("GET", prefix)
                if task["state"] != "queued":
                    self.journal.save(tid, "failed", payload)
                    return "unavailable"
                payload["claim"] = {"generation": task["generation"], "membership_revision": task["membership_revision"]}
                payload["claim_key"] = "claim:" + tid
                self.journal.save(tid, "claim_pending", payload)
                state = "claim_pending"
            if state == "claim_pending":
                task = self.client.request("POST", prefix + "/claim", payload["claim"], payload["claim_key"])
                payload["task"] = task
                self.journal.save(tid, "claimed", payload)
                state = "claimed"
            if state == "claimed":
                task = self.client.request("GET", prefix)
                if task["state"] != "leased" or task["attempt"] != payload["task"]["attempt"] or task["lease_expires_at"] <= time.time():
                    self.journal.save(tid, "failed", payload)
                    return "stale"
                room = self.client.request("GET", f"rooms/{rid}")
                context = {"agent_id": self.actor_id, "display_name": self.actor["display_name"], "role": "participant",
                           "topic": room["topic"], "purpose": task["purpose"], "inputs": room["messages"][-8:]}
                # Credentials, cursors, lease state, and retry keys are never model inputs.
                self.journal.save(tid, "call_started", payload)
                cancel, finished = threading.Event(), threading.Event()
                heartbeat_failed = threading.Event()
                fence = {k: task[k] for k in ("attempt", "generation", "membership_revision")}

                def heartbeat():
                    count = 0
                    while not finished.wait(self.heartbeat_interval):
                        count += 1
                        try:
                            self.client.request("POST", prefix + "/heartbeat", fence, f"heartbeat:{tid}:{task['attempt']}:{count}")
                        except Exception:
                            heartbeat_failed.set()
                            cancel.set()
                            return

                thread = threading.Thread(target=heartbeat, daemon=True)
                thread.start()
                try:
                    text = bounded_call(self.runtime, context, cancel, min(self.runtime_timeout, max(0, task["deadline"] - time.time())))
                except RuntimeTimeout:
                    self.journal.save(tid, "failed" if heartbeat_failed.is_set() else "uncertain", payload)
                    return "heartbeat_failed" if heartbeat_failed.is_set() else "runtime_timeout"
                except Exception:
                    self.journal.save(tid, "uncertain", payload)
                    raise
                finally:
                    finished.set()
                    thread.join(timeout=16)
                if heartbeat_failed.is_set():
                    self.journal.save(tid, "failed", payload)
                    return "heartbeat_failed"
                payload["complete"] = {**fence, "result": {"client_message_id": f"{tid}:{task['attempt']}", "text": text, "reply_to": None, "mentions": []}}
                payload["complete_key"] = f"complete:{tid}:{task['attempt']}"
                self.journal.save(tid, "result_saved", payload)
            self.client.request("POST", prefix + "/complete", payload["complete"], payload["complete_key"])
            self.journal.save(tid, "done", payload)
            return "completed"
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403, 404, 409, 422}:
                self.journal.save(tid, "failed", payload)
            raise
        # Transport failures preserve claim_pending/result_saved for same-key recovery.
