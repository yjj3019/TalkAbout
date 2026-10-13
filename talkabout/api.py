import json
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from .core import Core
from .domain import Problem, ident, require


def create_app(store, start_scheduler=True):
    core = Core(store)
    stop = threading.Event()
    state = {"scheduler": "stopped"}

    def scheduler():
        state["scheduler"] = "running"
        while not stop.wait(0.5):
            try:
                store.tick_all()
            except Exception:
                # Never log a database DSN, credentials, or external payload.
                state["scheduler"] = "failed"
                return

    @asynccontextmanager
    async def lifespan(app):
        thread = threading.Thread(target=scheduler, daemon=True)
        if start_scheduler:
            thread.start()
        yield
        stop.set()
        if start_scheduler:
            thread.join(timeout=2)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.core = core

    @app.exception_handler(Problem)
    async def problem_handler(request, exc):
        return JSONResponse({"code": exc.code, "message": exc.code, "retryable": exc.status == 429, "request_id": ident()}, status_code=exc.status)

    @app.middleware("http")
    async def harden(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    def token(request):
        header = request.headers.get("authorization", "")
        require(header.startswith("Bearer ") and len(header) <= 512, "unauthorized", 401)
        return header[7:]

    @app.get("/health")
    def health():
        return {"status": "development", "scheduler": state["scheduler"], "binary_uploads": False}

    @app.get("/")
    def index():
        return {"status": "partial_p0_checkpoint", "gui": "NOT_IMPLEMENTED", "health": "/health"}

    @app.get("/web/{name}")
    def asset(name: str):
        raise Problem("feature_disabled", 404)

    @app.api_route("/api/v1/{path:path}", methods=["GET", "POST", "PATCH", "PUT"])
    async def api(request: Request, path: str):
        from starlette.concurrency import run_in_threadpool
        credential = token(request)
        body = {}
        if request.method != "GET":
            raw = await request.body()
            require(len(raw) <= 100000, "request_too_large", 413)
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise Problem("invalid_json", 422)
            require(isinstance(body, dict), "invalid_body", 422)
        key = request.headers.get("idempotency-key")

        def dispatch():
            method = request.method
            store.auth(credential)
            if "uploads" in path.split("/"):
                raise Problem("feature_disabled")
            if method == "GET" and path == "agents/me":
                return core.me(credential)
            if method == "GET" and path in {"agents/me/events", "agents/me/snapshot"}:
                try:
                    wait = float(request.query_params.get("wait_seconds", 0))
                except ValueError:
                    raise Problem("invalid_wait", 422)
                return core.events(credential, request.query_params.get("cursor") or request.query_params.get("previous_cursor"), wait, path.endswith("snapshot"))
            if path == "rooms":
                if method == "GET":
                    return core.rooms(credential)
                if method == "POST":
                    return core.create(credential, body, key)
            match = re.fullmatch(r"rooms/([a-zA-Z0-9-]+)(?:/(.*))?", path)
            require(match, "not_found", 404)
            room_id, operation = match.groups()
            if method == "GET":
                room = core.read(credential, room_id)
                if not operation:
                    return room
                if operation in {"messages", "memberships", "tasks", "artifacts"}:
                    return room[{"memberships": "members"}.get(operation, operation)]
                if operation.startswith("tasks/"):
                    task_id = operation.split("/")[-1]
                    task = next((t for t in room["tasks"] if t["id"] == task_id), None)
                    require(task, "not_found", 404)
                    return task
                if re.fullmatch(r"artifacts/[^/]+/versions/[^/]+/download", operation):
                    _, aid, _, vid, _ = operation.split("/")
                    artifact = next((a for a in room["artifacts"] if a["id"] == aid), None)
                    version = next((v for v in artifact["versions"] if v["id"] == vid), None) if artifact else None
                    require(version, "not_found", 404)
                    return {"text": version["text"], "ai_generated": version["ai_generated"]}
            if method == "POST" and operation == "memberships/me/accept":
                return core.accept(credential, room_id, key, body)
            permitted = operation and (
                operation in {"messages", "memberships", "controls", "artifacts"}
                or re.fullmatch(r"tasks/[^/]+/(claim|heartbeat|complete|skip)", operation)
                or re.fullmatch(r"controls/[^/]+/approve", operation)
                or re.fullmatch(r"artifacts/[^/]+/(versions|adoptions)", operation)
                or (method == "PATCH" and re.fullmatch(r"memberships/[^/]+", operation)))
            require(method in {"POST", "PATCH"} and permitted, "not_found", 404)
            return core.mutation(credential, room_id, operation, key, body)

        try:
            return await run_in_threadpool(dispatch)
        except Problem:
            raise
        except Exception:
            raise Problem("service_unavailable", 503) from None

    return app
