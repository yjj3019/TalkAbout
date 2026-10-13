import argparse
import json
import os
import getpass
import time
from pathlib import Path

from .api import create_app
from .store import PostgresStore
from .worker import APIClient, Journal, SyntheticRuntime, Worker


def main():
    parser = argparse.ArgumentParser(description="TalkAbout private text-only prototype; no external model calls by default")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    sub.add_parser("serve")
    bootstrap = sub.add_parser("bootstrap-human", help="Explicit operator action; never performed automatically")
    bootstrap.add_argument("--workspace", required=True)
    bootstrap.add_argument("--name", required=True)
    bootstrap.add_argument("--handle", required=True)
    for name in ("check", "run", "status", "stop", "mcp"):
        command = sub.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        if name == "run":
            command.add_argument("--steps", type=int, default=1)
            command.add_argument("--synthetic", action="store_true")
            command.add_argument("--max-seconds", type=int, default=60)
    args = parser.parse_args()
    if args.command in {"init-db", "serve", "bootstrap-human"}:
        dsn = os.environ.get("TALKABOUT_DATABASE_URL")
        if not dsn:
            parser.error("Set TALKABOUT_DATABASE_URL separately; never put credentials in command arguments")
        store = PostgresStore(dsn)
        if args.command == "init-db":
            store.initialize()
        elif args.command == "bootstrap-human":
            from .auth import bootstrap_human
            password = getpass.getpass("Initial human password (never logged): ")
            confirmation = getpass.getpass("Confirm password: ")
            if password != confirmation:
                parser.error("Password confirmation mismatch")
            print(json.dumps(bootstrap_human(store, args.workspace, args.name, args.handle, password)))
        else:
            import uvicorn
            uvicorn.run(create_app(store), host="127.0.0.1", port=8080, access_log=False)
        return
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.command == "mcp":
        from .mcp import MCPServer
        token = os.environ.get(config.get("token_env", "TALKABOUT_TOKEN"))
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
        token = os.environ.get(config.get("token_env", "TALKABOUT_TOKEN"))
        if not token:
            parser.error("Configure the credential separately; it is never included in prompts")
        client = APIClient(config["base_url"], token, config["allowed_origin"])
        try:
            if args.command == "check":
                actor = client.request("GET", "agents/me")
                print(json.dumps({"agent_id": actor["id"], "kind": actor["kind"], "http": "confirmed", "external_runtime": "NOTRUN"}))
            else:
                if not args.synthetic or not 1 <= args.steps <= 1000 or not 1 <= args.max_seconds <= 300:
                    parser.error("Explicit synthetic runtime required; steps 1..1000, max-seconds 1..300")
                worker = Worker(client, journal, SyntheticRuntime())
                deadline = time.monotonic() + args.max_seconds
                for _ in range(args.steps):
                    if time.monotonic() >= deadline:
                        print("run_budget_exhausted")
                        break
                    result = worker.step()
                    print(result)
                    if result in {"stopped", "stop_unconfirmed", "backpressure"}:
                        break
                    if result in {"waiting", "retry_wait"}:
                        until = min(deadline, time.monotonic() + 0.25)
                        while time.monotonic() < until and not journal.stop_requested():
                            time.sleep(min(0.02, max(0, until - time.monotonic())))
        finally:
            client.close()
    finally:
        journal.close()


if __name__ == "__main__":
    main()
