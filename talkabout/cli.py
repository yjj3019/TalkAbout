import argparse
import json
import os
import getpass
import time
import uuid
from pathlib import Path

from .api import create_app
from .config import database_config
from .store import PostgresStore
from .worker import APIClient, Journal, SyntheticRuntime, Worker
from .runtime import RunBudgetExceeded, check_deadline


def main():
    parser = argparse.ArgumentParser(description="TalkAbout private text-only prototype; no external model calls by default")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    sub.add_parser("serve")
    sub.add_parser("preflight", help="Validate loopback configuration and existing schema; never initialize accounts or data")
    sub.add_parser('setup-code', help='Explicit private-terminal-only initial GUI setup; no default password')
    bootstrap = sub.add_parser("bootstrap-human", help="Explicit operator action; never performed automatically")
    bootstrap.add_argument("--workspace", required=True)
    bootstrap.add_argument("--name", required=True)
    bootstrap.add_argument("--handle", required=True)
    for name in ("check", "join", "run", "status", "stop", "mcp"):
        command = sub.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        if name in {"check", "join", "run"}:
            command.add_argument("--prompt-token", action="store_true", help="Read bearer credential privately from your TTY; never pass it as an argument")
        if name == "join":
            command.add_argument("--room", required=True)
            command.add_argument("--acknowledge-private", action="store_true")
        if name == "run":
            command.add_argument("--steps", type=int, default=1)
            command.add_argument("--synthetic", action="store_true")
            command.add_argument("--max-seconds", type=int, default=60)
            command.add_argument("--runtime", choices=['codex', 'claude'])
            command.add_argument("--approve-external", action="store_true", help="Explicitly authorize provider calls in your own environment")
            command.add_argument("--request-turn", action="store_true", help="Request a bounded free-discussion turn while idle")
            command.add_argument("--max-model-calls", type=int, default=1, help="External CLI invocation ceiling, 1..10; provider-internal retries may differ")
    args = parser.parse_args()
    if args.command in {"init-db", "serve", "bootstrap-human", "preflight", "setup-code"}:
        dsn = os.environ.get("TALKABOUT_DATABASE_URL")
        if not dsn:
            parser.error("Set TALKABOUT_DATABASE_URL separately; never put credentials in command arguments")
        try:
            dsn = database_config(dsn)
        except ValueError as error:
            parser.error(str(error))
        store = PostgresStore(dsn)
        if args.command == "init-db":
            store.initialize()
        elif args.command == 'setup-code':
            from .setup import terminal_issue
            terminal_issue(store)
        elif args.command == "preflight":
            ready = store.ready()
            print(json.dumps({"configuration": "loopback_valid", "database_schema": "ready" if ready else "not_ready", "deployment": "NOT_DEPLOYED"}))
            if not ready:
                raise SystemExit(1)
        elif args.command == "bootstrap-human":
            from .auth import bootstrap_human
            password = getpass.getpass("Initial human password (never logged): ")
            confirmation = getpass.getpass("Confirm password: ")
            if password != confirmation:
                parser.error("Password confirmation mismatch")
            print(json.dumps(bootstrap_human(store, args.workspace, args.name, args.handle, password)))
        else:
            import uvicorn
            uvicorn.run(create_app(store), host="127.0.0.1", port=8080, access_log=False, limit_concurrency=16, timeout_graceful_shutdown=10)
        return
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.command == "mcp":
        from .mcp import MCPServer
        token = getpass.getpass('Agent credential (private input): ') if getattr(args, 'prompt_token', False) else os.environ.get(config.get("token_env", "TALKABOUT_TOKEN"))
        if not token:
            parser.error("Configure the agent credential separately")
        client = APIClient(config["base_url"], token, config["allowed_origin"])
        try:
            MCPServer(client).serve()
        finally:
            client.close()
        return
    journal = Journal(config["journal_path"])
    try:
        if args.command == "status":
            print(json.dumps({"queue": journal.status(), "stop_state": journal.setting("stop_state", "running")}))
            return
        if args.command == "stop":
            with journal.db:
                journal.set_setting("stop", True)
                journal.set_setting("stop_state", "stop_requested")
            print("stop_requested")
            return
        if getattr(args, 'prompt_token', False):
            import sys
            if not sys.stdin.isatty() or not sys.stdout.isatty(): parser.error('Private credential input requires an interactive TTY')
            token = getpass.getpass('Agent credential (private input): ')
        else: token = os.environ.get(config.get("token_env", "TALKABOUT_TOKEN"))
        if not token:
            parser.error("Configure the credential separately; it is never included in prompts")
        client = APIClient(config["base_url"], token, config["allowed_origin"])
        try:
            if args.command == "check":
                actor = client.request("GET", "agents/me")
                if 'agent:capabilities_write' in actor.get('scopes', []):
                    client.request('PUT', 'agents/me/connection', {'runtime': 'unknown', 'state': 'idle'}, str(uuid.uuid4()))
                print(json.dumps({"agent_id": actor["id"], "kind": actor["kind"], "http": "confirmed", "external_runtime": "NOTRUN"}))
            elif args.command == "join":
                if not args.acknowledge_private: parser.error('Read the private-room notice and explicitly acknowledge it')
                invitation = next((item for item in client.request('GET', 'agents/me/invitations') if item['room_id'] == args.room), None)
                if invitation is None:
                    existing = next((room for room in client.request('GET', 'rooms') if room['id'] == args.room), None)
                    if existing:
                        print(json.dumps({'room_id': args.room, 'membership': 'already_accepted', 'room_state': existing['state'], 'external_runtime': 'NOTRUN'}))
                        return
                    parser.error('No matching pending invitation; inspect current membership before retrying')
                client.request('POST', 'rooms/'+args.room+'/memberships/me/accept',
                    {'expected_revision': invitation['membership_revision'], 'acknowledge_private': True}, str(uuid.uuid4()))
                print(json.dumps({'room_id': args.room, 'membership': 'accepted', 'external_runtime': 'NOTRUN'}))
            else:
                if not 1 <= args.steps <= 1000 or not 1 <= args.max_seconds <= 300 or not 1 <= args.max_model_calls <= 10 or bool(args.synthetic) == bool(args.runtime):
                    parser.error("Choose exactly one runtime; steps 1..1000, max-seconds 1..300")
                if args.runtime and not args.approve_external: parser.error('External runtime calls require explicit --approve-external')
                if args.runtime and os.name != 'nt': parser.error('External CLI execution requires verified Windows Job containment; POSIX execution is disabled')
                deadline = time.monotonic() + args.max_seconds
                client.deadline = deadline
                if args.runtime:
                    from .adapters import CLIRuntime
                    runtime = CLIRuntime(args.runtime, credential_env=config.get('token_env', 'TALKABOUT_TOKEN'), platform_credential=token, run_deadline=deadline)
                else: runtime = SyntheticRuntime()
                runtime_kind = args.runtime or 'synthetic'
                def report(state, error_code=None):
                    client.request('PUT', 'agents/me/connection', {'runtime': runtime_kind, 'state': state, 'error_code': error_code}, str(uuid.uuid4()))
                source_runtime = runtime
                class ReportedRuntime:
                    def run(self, context, cancel):
                        report('running')
                        check_deadline(deadline)
                        return source_runtime.run(context, cancel)
                    def shutdown(self):
                        stop = getattr(source_runtime, 'shutdown', None)
                        return stop() if callable(stop) else False
                    def set_deadline(self, value):
                        setter = getattr(source_runtime, 'set_deadline', None)
                        if callable(setter): setter(value)
                client.request('PUT', 'agents/me/capabilities', {'http': True, 'persistent_worker': True, 'text': True,
                    'image': False, 'file': False, 'model_label': runtime_kind+' (worker reported)'}, str(uuid.uuid4()))
                report('idle')
                worker = Worker(client, journal, ReportedRuntime(), adapter_policy={'external': bool(args.runtime), 'tool_limits_verified': bool(args.runtime), 'allowed_tools': []}, run_deadline=deadline)
                final_state = 'stopped'
                for _ in range(args.steps):
                    if args.runtime and runtime.calls >= args.max_model_calls:
                        print('model_call_budget_exhausted')
                        break
                    if time.monotonic() >= deadline:
                        print("run_budget_exhausted")
                        break
                    try:
                        if args.request_turn: worker.request_turns()
                        report('idle')
                        result = worker.step()
                        report('idle' if result in {'waiting', 'retry_wait', 'completed'} else 'stopped' if result == 'stopped' else 'failed')
                    except RunBudgetExceeded:
                        print('run_budget_exhausted')
                        break
                    except Exception:
                        try: report('failed', 'runtime_failed')
                        except Exception: pass
                        raise SystemExit('worker_failed: result retained as uncertain; inspect status, do not blindly rerun') from None
                    print(result)
                    if result in {'uncertain', 'stale', 'unavailable', 'heartbeat_failed', 'runtime_timeout'}:
                        final_state = 'failed'
                        break
                    if result in {"stopped", "stop_unconfirmed", "backpressure"}:
                        break
                    if result in {"waiting", "retry_wait"}:
                        until = min(deadline, time.monotonic() + 0.25)
                        while time.monotonic() < until and not journal.stop_requested():
                            time.sleep(min(0.02, max(0, until - time.monotonic())))
                try: report(final_state, 'runtime_failed' if final_state == 'failed' else None)
                except Exception: print('stop_report_unconfirmed')
        except RunBudgetExceeded:
            print('run_budget_exhausted')
        finally:
            source = locals().get('source_runtime')
            if callable(getattr(source, 'shutdown', None)): source.shutdown()
            client.close()
    finally:
        journal.close()


if __name__ == "__main__":
    main()
