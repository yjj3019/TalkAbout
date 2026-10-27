import json
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .core import Core
from .domain import Problem, ident, require, token_hash
from .identity import IdentityService
from .auth import COOKIE, HumanAuth


def create_app(store, start_scheduler=True, allow_fixture_bearer=False, allowed_hosts=None, setup_container_name=None, text_storage=None):
    setup_container_name = setup_container_name if isinstance(setup_container_name, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', setup_container_name) else None
    from .text_files import TextAttachments,MAX_BYTES
    text_files=TextAttachments(store,text_storage) if text_storage is not None else None
    core = Core(store,text_files=text_files)
    identity = IdentityService(store)
    human_auth = HumanAuth(store)
    stop = threading.Event()
    state = {"scheduler": "stopped"}

    def scheduler():
        state["scheduler"] = "running"
        while not stop.wait(0.5):
            try:
                if not store.setup_required():
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
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or (["testserver"] if allow_fixture_bearer else ["127.0.0.1", "localhost", "::1"]))
    app.state.core = core

    @app.exception_handler(Problem)
    async def problem_handler(request, exc):
        return JSONResponse({"code": exc.code, "message": exc.code, "retryable": exc.status == 429, "request_id": ident()}, status_code=exc.status)

    @app.middleware("http")
    async def harden(request, call_next):
        from starlette.concurrency import run_in_threadpool
        try:
            locked = await run_in_threadpool(store.setup_required)
        except Exception:
            # Schema outage must fail closed, without leaking database details.
            locked = True
        request.state.setup_locked = locked
        allowed = {'/', '/health', '/web/app.mjs', '/web/state.mjs', '/web/style.css',
                   '/api/v1/setup/status', '/api/v1/setup/csrf', '/api/v1/setup/complete'}
        if locked and request.url.path not in allowed:
            response = JSONResponse({'code': 'setup_required'}, status_code=503)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    def token(request):
        raw_session = request.cookies.get(COOKIE)
        if raw_session:
            store.auth("session:" + raw_session)
            if request.method != "GET":
                require(request.headers.get("origin") == str(request.base_url).rstrip("/"), "origin_forbidden", 403)
                human_auth.check_csrf(raw_session, request.headers.get("x-csrf-token"))
            return "session:" + raw_session
        header = request.headers.get("authorization", "")
        require(header.startswith("Bearer ") and len(header) <= 512, "unauthorized", 401)
        raw = header[7:]
        actor = store.auth(raw)
        require(actor["kind"] == "agent" or allow_fixture_bearer, "human_session_required", 403)
        return raw

    @app.get("/health")
    def health(request: Request):
        if request.state.setup_locked:
            return {"status": "alive"}
        return {"status": "development", "scheduler": state["scheduler"], "binary_uploads": False,
                **({'text_attachments':True} if text_files else {})}

    @app.get("/ready")
    async def ready():
        from starlette.concurrency import run_in_threadpool
        available = state["scheduler"] == "running" and await run_in_threadpool(store.ready)
        return JSONResponse({"status": "ready" if available else "not_ready", "deployment": "prototype"}, status_code=200 if available else 503)

    @app.get("/")
    def index():
        return FileResponse(Path(__file__).parent / "web" / "index.html")

    @app.get("/web/{name}")
    def asset(name: str):
        require(name in {"app.mjs", "state.mjs", "style.css", "favicon.svg"}, "not_found", 404)
        return FileResponse(Path(__file__).parent / "web" / name)

    def room_stream(request, credential, room_id):
        require(store.auth(credential)["kind"] == "human", "human_required", 403)
        core.read(credential, room_id)
        try:
            duration = float(request.query_params.get("duration_seconds", 60))
        except ValueError:
            raise Problem("invalid_stream_duration", 422)
        require(0 <= duration <= 60, "invalid_stream_duration", 422)
        async def stream():
            from starlette.concurrency import run_in_threadpool
            end = time.monotonic() + duration
            cursor = request.headers.get("last-event-id") or request.query_params.get("cursor")
            while True:
                if await request.is_disconnected():
                    return
                try:
                    data = await run_in_threadpool(core.events, credential, cursor, min(10, max(0, end-time.monotonic())), False, room_id)
                    cursor = data["next_cursor"]
                    # One batch per SSE message: its cursor never skips partially delivered events.
                    yield "id: " + cursor + "\nevent: room_update\ndata: " + json.dumps(data, ensure_ascii=True) + "\n\n"
                except Problem as error:
                    yield "event: access_changed\ndata: " + json.dumps({"code": error.code}) + "\n\n"
                    return
                except Exception:
                    yield 'event: access_changed\ndata: {"code":"service_unavailable"}\n\n'
                    return
                if time.monotonic() >= end:
                    yield 'event: stream_end\ndata: {"reason":"budget"}\n\n'
                    return
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.api_route("/api/v1/{path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
    async def api(request: Request, path: str):
        from starlette.concurrency import run_in_threadpool
        upload_content=re.fullmatch(r'rooms/([a-zA-Z0-9-]+)/uploads/([a-f0-9]{32})/content',path)
        if request.method=='PUT' and upload_content:
            require(text_files is not None,'feature_disabled',409)
            credential=token(request)
            require(request.headers.get('content-type','').split(';')[0]=='application/octet-stream','invalid_upload_content_type',415)
            try:actor=await run_in_threadpool(store.auth,credential)
            except Problem:raise
            except Exception:raise Problem('service_unavailable',503) from None
            require(actor['kind']=='human','human_required',403)
            raw=bytearray()
            async for chunk in request.stream():
                require(len(raw)+len(chunk)<=MAX_BYTES,'file_size_limit',413)
                raw.extend(chunk)
            import hashlib
            rid,fid=upload_content.groups();content=bytes(raw)
            try:
                return await run_in_threadpool(text_files.mutate,credential,rid,fid+'/content',
                    request.headers.get('idempotency-key'),{'sha256':hashlib.sha256(content).hexdigest(),'size':len(content)},content)
            except Problem:raise
            except Exception:raise Problem('service_unavailable',503) from None
        body = {}
        if request.method != "GET":
            raw = bytearray()
            async for chunk in request.stream():
                require(len(raw) + len(chunk) <= 100000, "request_too_large", 413)
                raw.extend(chunk)
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise Problem("invalid_json", 422)
            require(isinstance(body, dict), "invalid_body", 422)
        key = request.headers.get("idempotency-key")
        if path == 'setup/status' and request.method == 'GET':
            required = await run_in_threadpool(store.setup_required)
            return {'setup_required': required, **({'setup_container': setup_container_name} if required and setup_container_name else {})}
        if path == 'setup/csrf' and request.method == 'GET':
            import secrets
            require(await run_in_threadpool(store.setup_required), 'setup_complete', 409)
            csrf = secrets.token_urlsafe(32)
            response = JSONResponse({'csrf_token': csrf})
            response.set_cookie('talkabout_setup_csrf', csrf, httponly=True, secure=request.url.scheme == 'https',
                                samesite='strict', max_age=900, path='/api/v1/setup')
            return response
        if path == 'setup/complete' and request.method == 'POST':
            import hmac
            from .setup import complete_setup
            require(await run_in_threadpool(store.setup_required), 'setup_complete', 409)
            require(request.headers.get('origin') == str(request.base_url).rstrip('/'), 'origin_forbidden', 403)
            supplied, cookie = request.headers.get('x-csrf-token', ''), request.cookies.get('talkabout_setup_csrf', '')
            require(bool(cookie) and len(cookie) <= 128 and len(supplied) <= 128 and cookie.isascii() and supplied.isascii()
                    and hmac.compare_digest(cookie, supplied), 'csrf_invalid', 403)
            data = await run_in_threadpool(complete_setup, store, body)
            response = JSONResponse(data)
            response.delete_cookie('talkabout_setup_csrf', path='/api/v1/setup')
            return response
        if path == "session" and request.method == "POST":
            require(request.headers.get("origin") == str(request.base_url).rstrip("/"), "origin_forbidden", 403)
            raw, data = await run_in_threadpool(human_auth.login, body)
            response = JSONResponse(data)
            response.set_cookie(COOKIE, raw, httponly=True, secure=request.url.scheme == "https", samesite="strict", max_age=human_auth.ttl, path="/")
            return response
        credential = token(request)
        if path == "session" and request.method == "GET":
            require(credential.startswith("session:"), "human_session_required", 403)
            return await run_in_threadpool(human_auth.session, credential[8:])
        if path == "session/logout" and request.method == "POST":
            require(credential.startswith("session:"), "human_session_required", 403)
            await run_in_threadpool(store.logout, token_hash(credential[8:]))
            response = JSONResponse({"logged_out": True})
            response.delete_cookie(COOKIE, path="/")
            return response

        def dispatch():
            method = request.method
            store.auth(credential)
            if path=='capabilities' and method=='GET':
                return {'text_attachments':text_files.capabilities() if text_files else {'enabled':False}}
            if "uploads" in path.split("/"):
                require(text_files is not None,'feature_disabled',409)
                init=re.fullmatch(r'rooms/([a-zA-Z0-9-]+)/uploads',path)
                complete=re.fullmatch(r'rooms/([a-zA-Z0-9-]+)/uploads/([a-f0-9]{32})/complete',path)
                if init and method=='POST':return text_files.mutate(credential,init[1],'init',key,body)
                if complete and method=='POST':return text_files.mutate(credential,complete[1],complete[2]+'/complete',key,body)
                raise Problem('not_found',404)
            file_path=re.fullmatch(r'rooms/([a-zA-Z0-9-]+)/files(?:/([a-f0-9]{32})(?:/(download|text|consent))?)?',path)
            if file_path:
                require(text_files is not None,'feature_disabled',409)
                rid,fid,action=file_path.groups()
                if method=='GET' and not fid:return text_files.list(credential,rid)
                if method=='GET' and fid and action is None:
                    items=text_files.list(credential,rid)
                    item=next((f for f in items if f['file_id']==fid),None)
                    require(item,'attachment_not_found',404)
                    return item
                if method=='GET' and action in {'download','text'}:
                    result=text_files.human_read(credential,rid,fid,action)
                    if action=='text':return result
                    content,name=result
                    return Response(content,media_type='text/plain; charset=utf-8',headers={
                        'Content-Disposition':'attachment; filename="'+name+'"','X-Content-Type-Options':'nosniff'})
                if method=='DELETE' and fid and action is None:return text_files.mutate(credential,rid,fid+'/delete',key,body)
                if method=='PATCH' and action=='consent':return text_files.mutate(credential,rid,fid+'/consent',key,body)
                raise Problem('not_found',404)
            file_read=re.fullmatch(r'rooms/([a-zA-Z0-9-]+)/tasks/([a-zA-Z0-9-]+)/files/([a-f0-9]{32})/read',path)
            if file_read:
                require(text_files is not None,'feature_disabled',409)
                require(method=='POST','not_found',404)
                return text_files.agent_read(credential,*file_read.groups(),key,body)
            if method == "GET" and path == "agents/me":
                return core.me(credential)
            if method == "GET" and path == "agents/me/invitations":
                return core.invitations(credential)
            if method == "GET" and path == "admin/handovers":
                return core.handovers(credential)
            if path == "agents":
                if method == "GET":
                    return identity.list(credential)
                if method == "POST":
                    return identity.mutate(credential, "register", key, body)
            if method == "PUT" and path == "agents/me/capabilities":
                return identity.mutate(credential, "capabilities", key, body)
            if method == "PUT" and path == "agents/me/connection":
                return identity.mutate(credential, "connection", key, body)
            runtime_path = re.fullmatch(r"agents/me/runtime-checks(?:/([a-zA-Z0-9-]+)/(complete|fail))?", path)
            if method == "POST" and runtime_path:
                check_id, action = runtime_path.groups()
                return identity.runtime_check(credential, check_id, action or "create", key, body)
            agent_path = re.fullmatch(r"agents/([a-zA-Z0-9-]+)/(identity|name-proposals|credentials)(?:/([a-zA-Z0-9-]+)/(revoke|rotate))?", path)
            if agent_path:
                agent_id, action, credential_id, credential_action = agent_path.groups()
                if action == "identity" and method == "PATCH" and not credential_id:
                    return identity.mutate(credential, "identity/" + agent_id, key, body)
                if action == "name-proposals" and method == "POST" and not credential_id:
                    require(store.auth(credential)["id"] == agent_id, "not_found", 404)
                    return identity.mutate(credential, "name-proposals", key, body)
                if action == "credentials" and method == "POST":
                    operation = "credentials/" + agent_id + ("/" + credential_id + "/" + credential_action if credential_id else "")
                    return identity.mutate(credential, operation, key, body)
                if action == "credentials" and method == "GET" and not credential_id:
                    return identity.credential_metadata(credential, agent_id)
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
            if operation == 'work-plan':
                if method == 'GET': return core.work_plan(credential, room_id)
                if method == 'POST': return core.replace_work_plan(credential, room_id, key, body)
                raise Problem('not_found', 404)
            if operation in {'participation/join', 'participation/ack'} and method == 'POST':
                return core.participation(credential, room_id, operation.split('/')[-1], body)
            if operation == 'notifications' and method == 'GET':
                return core.notifications(credential, room_id, request.query_params.get('session_id'))
            if operation == "handover/accept" and method == "POST":
                return core.accept_handover(credential, room_id, key, body)
            if method == "GET":
                if operation == "events":
                    try:
                        wait = float(request.query_params.get("wait_seconds", 0))
                    except ValueError:
                        raise Problem("invalid_wait", 422)
                    return core.events(credential, request.query_params.get("cursor"), wait, target_room=room_id,
                                       session_id=request.query_params.get('session_id'))
                if operation == "snapshot":
                    return core.room_snapshot(credential, room_id)
                if operation == "stream":
                    return room_stream(request, credential, room_id)
                if operation == "interventions":
                    return core.interventions(credential, room_id)
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
            if method == "POST" and re.fullmatch(r"memberships/[^/]+/accept", operation or ""):
                require(operation.split("/")[1] in {"me", store.auth(credential)["id"]}, "not_found", 404)
                return core.accept(credential, room_id, key, body)
            permitted = operation and (
                operation in {"messages", "memberships", "controls", "artifacts", "turn-requests"}
                or re.fullmatch(r"tasks/[^/]+/(claim|start|heartbeat|complete|skip|resolve-uncertain)", operation)
                or re.fullmatch(r"tasks/[^/]+/reservations", operation)
                or re.fullmatch(r"tasks/[^/]+/reservations/[0-9a-f]{64}/outcome", operation)
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
