from __future__ import annotations

import json
import sqlite3
import threading
import time
import random
import math
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from .domain import digest
from .runtime import BudgetedClient, RuntimeTimeout, bounded_call, check_deadline, validate_adapter_policy


class StopRequested(Exception):
    pass


class Backpressure(Exception):
    pass


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    base_seconds: float = 0.25
    max_seconds: float = 2
    budget_seconds: float = 5

    def __post_init__(self):
        if type(self.attempts) is not int or not 1 <= self.attempts <= 5:
            raise ValueError("retry_attempts_must_be_1_to_5")
        values = (self.base_seconds, self.max_seconds, self.budget_seconds)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values) or not (0 <= self.base_seconds <= self.max_seconds <= self.budget_seconds <= 30 and self.budget_seconds > 0):
            raise ValueError("invalid_retry_bounds")


class APIClient:
    def __init__(self, base_url, token, allowed_origin, transport=None, retry_policy=None):
        parsed = urlsplit(base_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin != allowed_origin or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("invalid_origin")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("https_required")
        self.client = httpx.Client(base_url=base_url.rstrip("/") + "/api/v1/", headers={"Authorization": "Bearer " + token},
                                   follow_redirects=False, timeout=15, transport=transport)
        self.retry_policy = retry_policy or RetryPolicy()
        self.should_stop = lambda: False
        self.deadline = None

    def request(self, method, path, body=None, key=None, params=None):
        headers = {"Idempotency-Key": key} if key else {}
        policy, started = self.retry_policy, time.monotonic()
        safe = method.upper() == "GET" or bool(key)
        for attempt in range(policy.attempts):
            check_deadline(self.deadline)
            if self.should_stop():
                raise StopRequested()
            try:
                remaining = max(0.001, policy.budget_seconds - (time.monotonic() - started))
                if self.deadline is not None:
                    remaining = min(remaining, max(0.001, self.deadline - time.monotonic()))
                response = self.client.request(method, path, json=body, headers=headers, params=params, timeout=min(15, remaining))
                check_deadline(self.deadline)
                response.raise_for_status()
                return response.json()
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError, httpx.HTTPStatusError) as error:
                retry_after = 0
                if isinstance(error, httpx.HTTPStatusError):
                    if error.response.status_code not in {429, 500, 502, 503, 504}:
                        raise
                    value = error.response.headers.get("Retry-After")
                    if value:
                        try:
                            retry_after = float(value)
                        except ValueError:
                            try:
                                retry_after = parsedate_to_datetime(value).timestamp() - time.time()
                            except (TypeError, ValueError, OverflowError):
                                raise error
                        if not math.isfinite(retry_after) or retry_after > policy.max_seconds:
                            raise error
                delay = max(0, retry_after, min(policy.max_seconds, policy.base_seconds * 2 ** attempt) * random.uniform(0.8, 1))
                if not safe or attempt + 1 >= policy.attempts or time.monotonic() - started + delay >= policy.budget_seconds:
                    raise
                until = time.monotonic() + delay
                while time.monotonic() < until:
                    check_deadline(self.deadline)
                    if self.should_stop():
                        raise StopRequested()
                    time.sleep(min(0.02, max(0, until - time.monotonic())))

    def close(self):
        self.client.close()


class Journal:
    def __init__(self, path, inbox_limit=4096, queue_limit=1024, pending_limit=64):
        if any(type(v) is not int or not 1 <= v <= 100000 for v in (inbox_limit, queue_limit, pending_limit)) or pending_limit > queue_limit:
            raise ValueError("invalid_journal_bounds")
        self.inbox_limit, self.queue_limit, self.pending_limit = inbox_limit, queue_limit, pending_limit
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("durable_journal_required")
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
                    row = self.db.execute("SELECT state,payload FROM queue WHERE id=?", (event["task_id"],)).fetchone()
                    if row:
                        old = json.loads(row[1])
                        task = old.get("task", {})
                        safe = row[0] in {"failed", "pre_call_retry"} and old.get("safe_pre_call_retry") is True and old.get("runtime_started") is False
                        same = event.get("generation") == task.get("generation") and event.get("membership_revision") == task.get("membership_revision") and event.get("attempt", -1) == task.get("attempt", 0)
                        if safe and same:
                            self.db.execute("UPDATE queue SET state='ready',payload=? WHERE id=?", (json.dumps({"event": event, "runtime_started": False}), event["task_id"]))
                    else:
                        self.db.execute("INSERT INTO queue VALUES(?,?,?)", (event["task_id"], "ready", json.dumps({"event": event, "runtime_started": False})))
            inbox = self.db.execute("SELECT count(*) FROM inbox").fetchone()[0]
            queue = self.db.execute("SELECT count(*) FROM queue").fetchone()[0]
            pending = self.db.execute("SELECT count(*) FROM queue WHERE state NOT IN ('done','failed','uncertain','cancelled','stopped_before_submit')").fetchone()[0]
            if inbox > self.inbox_limit or queue > self.queue_limit or pending > self.pending_limit:
                raise Backpressure("journal_capacity")
            self.set_setting("cursor", response["next_cursor"])

    def save(self, task_id, state, payload):
        with self.db:
            self.db.execute("UPDATE queue SET state=?,payload=? WHERE id=?", (state, json.dumps(payload), task_id))

    def next(self):
        row = self.db.execute("SELECT id,state,payload FROM queue WHERE state NOT IN ('done','failed','uncertain','cancelled','stopped_before_submit') ORDER BY rowid LIMIT 1").fetchone()
        return (row[0], row[1], json.loads(row[2])) if row else None

    def status(self):
        return {state: count for state, count in self.db.execute("SELECT state,count(*) FROM queue GROUP BY state")}

    def close(self):
        self.db.close()

    def stop_requested(self):
        with sqlite3.connect(self.path, timeout=1) as connection:
            row = connection.execute("SELECT v FROM settings WHERE k='stop'").fetchone()
            return bool(row and json.loads(row[0]))

    def stop_state(self, state):
        with sqlite3.connect(self.path, timeout=1) as connection:
            connection.execute("INSERT OR REPLACE INTO settings VALUES('stop_state',?)", (json.dumps(state),))


class SyntheticRuntime:
    """Local fixture only; never represents a real model or external agent."""
    def run(self, context, cancel):
        return f"Synthetic fixture contribution: {context['topic']}"


class Worker:
    def __init__(self, client, journal, runtime, heartbeat_interval=3, runtime_timeout=30, adapter_policy=None, run_deadline=None):
        self.run_deadline = run_deadline
        self.client = BudgetedClient(client, run_deadline) if run_deadline is not None else client
        self.journal, self.runtime = journal, runtime
        if isinstance(client, APIClient):
            client.should_stop = journal.stop_requested
            client.deadline = run_deadline
        if callable(getattr(runtime, 'set_deadline', None)):
            runtime.set_deadline(run_deadline)
        self.heartbeat_interval = heartbeat_interval
        self.runtime_timeout = runtime_timeout
        validate_adapter_policy(adapter_policy or {"external": False, "allowed_tools": []})
        self.actor = self.client.request("GET", "agents/me")
        self.actor_id = self.actor["id"]

    def request_turns(self):
        """Explicit CLI opt-in only; never trigger recursively from replies or mentions."""
        for room in self.client.request('GET', 'rooms'):
            if room['mode'] != 'free_discussion' or room['state'] != 'active': continue
            if any(t['assignee'] == self.actor_id and t['state'] in {'queued', 'leased'} for t in room['tasks']): continue
            try:
                self.client.request('POST', 'rooms/'+room['id']+'/turn-requests',
                    {'generation': room['generation'], 'membership_revision': room['membership_revision']},
                    'turn:'+room['id']+':'+str(room['generation'])+':'+str(room['last_seq']))
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 409: raise

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
                        response["events"].append({"event_id": f"snapshot:{task['id']}:{task['attempt']}", "room_id": room["id"], "type": "task.queued", "task_id": task["id"], "assignee": self.actor_id,
                                                   "attempt": task["attempt"], "generation": task["generation"], "membership_revision": task["membership_revision"]})
        self.journal.ingest(response, self.actor_id)

    def step(self):
        check_deadline(self.run_deadline)
        if self.journal.stop_requested():
            status = self.journal.status()
            unknown = status.get("uncertain", 0) or status.get("call_started", 0)
            self.journal.stop_state("unreachable" if unknown else "stopped")
            return "stop_unconfirmed" if unknown else "stopped"
        try:
            self.poll()
        except StopRequested:
            self.journal.stop_state("stopped")
            return "stopped"
        except Backpressure:
            if not self.journal.next():
                return "backpressure"
        entry = self.journal.next()
        if not entry:
            return "waiting"
        tid, state, payload = entry
        stop_seen = threading.Event()
        rid = payload["event"]["room_id"]
        prefix = f"rooms/{rid}/tasks/{tid}"
        if state == "call_started":
            self.journal.save(tid, "uncertain", payload)
            return "uncertain"
        try:
            if state == "pre_call_retry":
                task = self.client.request("GET", prefix)
                previous = payload["task"]
                same = all(task[k] == previous[k] for k in ("attempt", "generation", "membership_revision"))
                if same and task["state"] == "queued":
                    self.journal.save(tid, "ready", {"event": payload["event"], "runtime_started": False})
                    return "retry_queued"
                if same and task["state"] == "leased" and task["lease_expires_at"] <= time.time():
                    return "retry_wait"
                payload["safe_pre_call_retry"] = False
                self.journal.save(tid, "failed", payload)
                return "stale"
            if state == "ready":
                task = self.client.request("GET", prefix)
                if task["state"] != "queued":
                    self.journal.save(tid, "failed", payload)
                    return "unavailable"
                payload["claim"] = {"generation": task["generation"], "membership_revision": task["membership_revision"]}
                payload["claim_key"] = f"claim:{tid}:{task['attempt'] + 1}"
                payload["runtime_started"] = False
                self.journal.save(tid, "claim_pending", payload)
                state = "claim_pending"
            if state == "claim_pending":
                if self.journal.stop_requested():
                    self.journal.stop_state("stopped")
                    return "stopped"
                task = self.client.request("POST", prefix + "/claim", payload["claim"], payload["claim_key"])
                payload["task"] = task
                self.journal.save(tid, "claimed", payload)
                state = "claimed"
            if state == "claimed":
                task = self.client.request("GET", prefix)
                if task["state"] == "queued" and task["attempt"] == payload["task"]["attempt"] and task["generation"] == payload["task"]["generation"] and task["membership_revision"] == payload["task"]["membership_revision"]:
                    payload.update(safe_pre_call_retry=True, runtime_started=False)
                    self.journal.save(tid, "ready", {"event": payload["event"], "runtime_started": False})
                    return "retry_queued"
                if task["state"] == "leased" and task["lease_expires_at"] <= time.time() and all(task[k] == payload["task"][k] for k in ("attempt", "generation", "membership_revision")):
                    payload.update(safe_pre_call_retry=True, runtime_started=False)
                    self.journal.save(tid, "pre_call_retry", payload)
                    return "retry_wait"
                if task["state"] != "leased" or task["attempt"] != payload["task"]["attempt"] or task["lease_expires_at"] <= time.time():
                    self.journal.save(tid, "failed", payload)
                    return "stale"
                room = self.client.request("GET", f"rooms/{rid}")
                if self.journal.stop_requested():
                    self.journal.save(tid, "cancelled", payload)
                    self.journal.stop_state("stopped")
                    return "stopped"
                context = {"room_id": rid, "generation": task["generation"], "membership_revision": task["membership_revision"], "agent_id": self.actor_id, "display_name": self.actor["display_name"], "role": task.get("role", "participant"),
                           "topic": room["topic"], "purpose": task["purpose"], "inputs": room["messages"][-8:]}
                context["phase"] = task.get("phase")
                context["round"] = task.get("round")
                context["artifact_proposals"] = [{"artifact_id": a["id"], "text": a["versions"][-1]["text"]} for a in room["artifacts"] if a["versions"]][-4:]
                # Credentials, cursors, lease state, and retry keys are never model inputs.
                check_deadline(self.run_deadline)
                payload["runtime_started"] = True
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

                def watch_stop():
                    while not finished.wait(0.02):
                        try:
                            requested = self.journal.stop_requested()
                        except sqlite3.Error:
                            requested = True
                        if requested:
                            stop_seen.set()
                            cancel.set()
                            return

                thread = threading.Thread(target=heartbeat, daemon=True)
                watcher = threading.Thread(target=watch_stop, daemon=True)
                thread.start()
                watcher.start()
                try:
                    remaining = self.run_deadline - time.monotonic() if self.run_deadline is not None else float('inf')
                    text = bounded_call(self.runtime, context, cancel, min(self.runtime_timeout, max(0, task["deadline"] - time.time()), remaining))
                    check_deadline(self.run_deadline)
                except RuntimeTimeout as error:
                    if stop_seen.is_set():
                        self.journal.save(tid, "cancelled" if error.runtime_stopped else "uncertain", payload)
                        self.journal.stop_state("stopped" if error.runtime_stopped else "unreachable")
                        return "stopped" if error.runtime_stopped else "stop_unconfirmed"
                    self.journal.save(tid, "uncertain", payload)
                    return "heartbeat_failed" if heartbeat_failed.is_set() else "runtime_timeout"
                except Exception:
                    self.journal.save(tid, "uncertain", payload)
                    raise
                finally:
                    finished.set()
                    thread.join(timeout=16)
                    watcher.join(timeout=2)
                if heartbeat_failed.is_set():
                    self.journal.save(tid, "uncertain", payload)
                    return "heartbeat_failed"
                payload["complete"] = {**fence, "result": {"client_message_id": f"{tid}:{task['attempt']}", "text": text, "reply_to": None, "mentions": []}}
                payload["complete_key"] = f"complete:{tid}:{task['attempt']}"
                self.journal.save(tid, "result_saved", payload)
            if stop_seen.is_set() or self.journal.stop_requested():
                self.journal.save(tid, "stopped_before_submit", payload)
                self.journal.stop_state("stopped")
                return "stopped"
            check_deadline(self.run_deadline)
            self.client.request("POST", prefix + "/complete", payload["complete"], payload["complete_key"])
            self.journal.save(tid, "done", payload)
            return "completed"
        except StopRequested:
            # Pending claim/result stays durable; no further submission after stop.
            self.journal.stop_state("stopped")
            return "stopped"
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403, 404, 409, 410, 422}:
                self.journal.save(tid, "failed", payload)
            raise
        # Transport failures preserve claim_pending/result_saved for same-key recovery.
