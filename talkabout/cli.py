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


def emit_json(value):
    """Print one JSON line even on a legacy console that cannot encode KO/EN text."""
    try:
        print(json.dumps(value, ensure_ascii=False))
    except UnicodeEncodeError:
        print(json.dumps(value, ensure_ascii=True))


def main():
    parser = argparse.ArgumentParser(description="TalkAbout private text-only prototype; no external model calls by default")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    serve=sub.add_parser("serve")
    serve.add_argument('--text-attachment-root',type=Path,help='Opt in to inert UTF-8 attachments using an existing private server directory; no directory or database migration is created at startup')
    sub.add_parser("preflight", help="Validate loopback configuration and existing schema; never initialize accounts or data")
    sub.add_parser('setup-code', help='Explicit private-terminal-only initial GUI setup; no default password')
    bootstrap = sub.add_parser("bootstrap-human", help="Explicit operator action; never performed automatically")
    bootstrap.add_argument("--workspace", required=True)
    bootstrap.add_argument("--name", required=True)
    bootstrap.add_argument("--handle", required=True)
    for name in ("check", "join", "run", "connect", "status", "stop", "mcp"):
        command = sub.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        if name in {"check", "join", "run", "connect"}:
            command.add_argument("--prompt-token", action="store_true", help="Read bearer credential privately from your TTY; never pass it as an argument")
        if name == "join":
            command.add_argument("--room", required=True)
            command.add_argument("--acknowledge-private", action="store_true")
        if name in {"run", "connect"}:
            command.add_argument("--steps", type=int, default=1)
            command.add_argument("--synthetic", action="store_true")
            command.add_argument("--max-seconds", type=int, default=60)
            command.add_argument("--runtime", choices=['codex', 'claude', 'grok'])
            command.add_argument("--auth-mode", choices=["native", "api_key"], default="native", help="Native CLI login by default; explicit api_key may use separately billed provider quota, never automatic fallback")
            command.add_argument("--resume-worker", action="store_true", help="Explicitly clear a previous local stop request; uncertain tasks remain blocked")
            command.add_argument("--retry-check", action="store_true", help="Explicitly allow a new provider test after an uncertain prior test; it may consume usage again")
            command.add_argument("--approve-external", action="store_true", help="Explicitly authorize provider calls in your own environment")
            command.add_argument("--request-turn", action="store_true", help="Request a bounded free-discussion turn while idle")
            command.add_argument("--max-model-calls", type=int, default=1, help="External CLI invocation ceiling, 1..10; provider-internal retries may differ")
            command.add_argument('--max-room-calls',type=int,default=None,help='Optional room-purpose ceiling, 0..10, within the unchanged total model-call ceiling')
            command.add_argument('--persist-provider-sessions',action='store_true',help='Opt-in local Codex checkpoints; automatic resume requires verified stable account identity')
            command.add_argument('--codex-transport',choices=['exec-json','app-server'],default='exec-json')
            command.add_argument('--experimental-app-server',action='store_true',help='Explicit gate for the existing experimental Codex app-server transport')
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
            from .text_files import PrivateTextStorage
            storage=PrivateTextStorage(args.text_attachment_root) if args.text_attachment_root is not None else None
            uvicorn.run(create_app(store,text_storage=storage), host="127.0.0.1", port=8080, access_log=False, limit_concurrency=16, timeout_graceful_shutdown=10)
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
    connector_lease = None
    try:
        if args.command in {'connect', 'run'}:
            from .connector import ConnectorLease, ConnectionCheckError
            try:
                connector_lease = ConnectorLease(config['journal_path']).acquire()
            except ConnectionCheckError as error:
                raise SystemExit(str(error)) from None
            if args.resume_worker:
                with journal.db:
                    journal.set_setting('stop', False)
                    journal.set_setting('stop_state', 'running')
        if args.command == "status":
            from .codex_sessions import session_status
            print(json.dumps({"queue": journal.status(), "stop_state": journal.setting("stop_state", "running"),'provider_sessions':session_status(journal)}))
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
        # A worker stop fences model work, not explicit account/membership recovery.
        if args.command in {"run", "connect"}:
            client.should_stop = journal.stop_requested
        try:
            if args.command == "check":
                actor = client.request("GET", "agents/me")
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
                if args.command == "connect" and not args.runtime: parser.error("connect requires a real runtime; synthetic is test-only")
                if args.runtime and not args.approve_external: parser.error('External runtime calls require explicit --approve-external')
                if args.max_room_calls is not None and not 0 <= args.max_room_calls <= 10:parser.error('max-room-calls must be 0..10')
                if args.persist_provider_sessions and args.runtime!='codex':parser.error('Provider session checkpoints currently require Codex')
                if args.codex_transport=='app-server' and (args.runtime!='codex' or not args.experimental_app_server):
                    parser.error('app-server requires Codex and explicit --experimental-app-server')
                if args.runtime and os.name != 'nt': parser.error('External CLI execution requires verified Windows Job containment; POSIX execution is disabled')
                deadline = time.monotonic() + args.max_seconds
                client.deadline = deadline
                if args.runtime:
                    from .agent_runtime import StructuredRuntime
                    from .connector import verify_runtime, ConnectionCheckError, RoomRuntimePool
                    runtime_options={'credential_env':config.get('token_env','TALKABOUT_TOKEN'),'platform_credential':token,'run_deadline':deadline,'auth_mode':args.auth_mode}
                    if args.codex_transport!='exec-json':runtime_options['codex_transport']=args.codex_transport
                    test_runtime = StructuredRuntime(args.runtime, **runtime_options)
                    probe = test_runtime.probe()
                    if probe.get('reason') or not probe.get('available'):
                        test_runtime.shutdown()
                        raise SystemExit(probe.get('reason') or 'runtime_unavailable')
                    try:
                        if journal.stop_requested(): raise ConnectionCheckError('connector_stopped')
                        test_runtime.preflight()
                        receipt, check_calls = verify_runtime(client, journal, test_runtime, args.runtime, token, deadline, args.retry_check)
                    except Exception as error:
                        test_runtime.shutdown()
                        from .adapters import AdapterError
                        code = error.code if isinstance(error, AdapterError) else str(error) if isinstance(error, ConnectionCheckError) else 'runtime_check_failed'
                        raise SystemExit(code + ': inspect connection state; use --retry-check only to explicitly authorize another test; additional provider usage may occur') from None
                    print(json.dumps({'runtime': args.runtime, 'check': receipt.get('status'), 'evidence': 'worker_reported_test'}))
                    session_journal=None
                    if args.persist_provider_sessions:
                        from .codex_sessions import CodexSessionJournal
                        session_journal=CodexSessionJournal(journal,connector_lease,test_runtime._connection_binding)
                    runtime = RoomRuntimePool(lambda: StructuredRuntime(args.runtime, **runtime_options),session_journal=session_journal)
                    total_remaining=max(0,args.max_model_calls-check_calls)
                    room_remaining=min(total_remaining,args.max_room_calls if args.max_room_calls is not None else total_remaining)
                    pending=journal.next() if room_remaining==0 else None
                    next_action=('room_worker_ready' if room_remaining else
                                 'saved_result_recovery_only' if pending and pending[1]=='result_saved' else
                                 'increase_max_model_calls')
                    print(json.dumps({'connection_check_calls':check_calls,'total_calls_remaining':total_remaining,'room_calls_remaining':room_remaining,
                                      'next_action':next_action}))
                    if next_action=='increase_max_model_calls':
                        # The default ceiling of one call is spent by the connection
                        # test. Say so plainly instead of looping into a silent
                        # 'model_call_budget_exhausted' with no way forward.
                        required=min(10,check_calls+1)
                        emit_json({'connection_check_only':True,'room_calls_remaining':0,
                                   'next_command_hint':{
                                       'ko':'연결 시험이 --max-model-calls '+str(args.max_model_calls)+' 한도를 모두 사용해 이번 실행에서는 발언 호출이 0회입니다. '
                                            '발언까지 허용하려면 --max-model-calls '+str(required)+' 이상으로 다시 실행하세요'
                                            '(제한 라운드 1회 = 시험 1 + 제안/검토/정리 3). 연결 시험은 다시 수행되며 '
                                            '공급자 사용량이 추가로 발생할 수 있습니다.',
                                       'en':'The connection test used the whole --max-model-calls '+str(args.max_model_calls)+' ceiling, so this run allows 0 speaking calls. '
                                            'Re-run with --max-model-calls '+str(required)+' or more (one bounded round = 1 check + 3 turns). '
                                            'The check runs again and may consume additional provider usage.'},
                                   'automatic_retry':False})
                else:
                    runtime = SyntheticRuntime()
                    check_calls = 0
                    next_action = 'room_worker_ready'
                runtime_kind = args.runtime or 'synthetic'
                final_state = 'failed'
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
                if next_action == 'increase_max_model_calls':
                    # Nothing can be spoken under this ceiling and no saved result
                    # is waiting, so do not start a worker that can only report an
                    # exhausted budget.
                    final_state = 'stopped'
                    report('stopped')
                    return
                worker = Worker(client, journal, ReportedRuntime(), adapter_policy={'external': bool(args.runtime), 'tool_limits_verified': bool(args.runtime), 'allowed_tools': []}, run_deadline=deadline)
                final_state = 'stopped'
                for _ in range(args.steps):
                    budget_exhausted=bool(args.runtime and (runtime.calls + check_calls >= args.max_model_calls or
                                         args.max_room_calls is not None and runtime.calls >= args.max_room_calls))
                    pending=journal.next() if budget_exhausted else None
                    saved_result_only=bool(budget_exhausted and pending and pending[1]=='result_saved')
                    if budget_exhausted and not saved_result_only:
                        print('model_call_budget_exhausted')
                        break
                    if time.monotonic() >= deadline:
                        print("run_budget_exhausted")
                        break
                    try:
                        if args.request_turn and not saved_result_only: worker.request_turns()
                        report('idle')
                        result = worker.step(saved_result_only=True) if saved_result_only else worker.step()
                        if result in {'waiting', 'retry_wait', 'completed'}: report('idle')
                    except RunBudgetExceeded:
                        print('run_budget_exhausted')
                        break
                    except Exception as error:
                        final_state = 'failed'
                        from .codex_sessions import SessionRecoveryError,recovery_guidance
                        guidance=recovery_guidance(str(error)) if isinstance(error,SessionRecoveryError) else None
                        if guidance:
                            print(json.dumps({'provider_session_recovery':guidance},ensure_ascii=False))
                            raise SystemExit('worker_failed: '+guidance['code']+': '+guidance['reason']['ko']+' / '+guidance['reason']['en']) from None
                        raise SystemExit('worker_failed: result retained as uncertain; inspect status, do not blindly rerun') from None
                    print(result)
                    safe_drain=callable(getattr(worker,'safe_to_continue_after',None)) and worker.safe_to_continue_after(result)
                    if safe_drain:report('idle')
                    if result in {'uncertain', 'stale', 'unavailable', 'heartbeat_failed', 'runtime_timeout'} and not safe_drain:
                        final_state = 'failed'
                        break
                    if result in {"stopped", "stop_unconfirmed", "backpressure"}:
                        break
                    if result in {"waiting", "retry_wait"}:
                        until = min(deadline, time.monotonic() + 0.25)
                        while time.monotonic() < until and not journal.stop_requested():
                            time.sleep(min(0.02, max(0, until - time.monotonic())))
        except RunBudgetExceeded:
            print('run_budget_exhausted')
        finally:
            source = locals().get('source_runtime')
            try:
                if callable(getattr(source, 'shutdown', None)):
                    if source.shutdown() is not True:
                        final_state = 'failed'
                        with journal.db:
                            journal.set_setting('stop', True)
                            journal.set_setting('stop_state', 'unreachable')
                        raise SystemExit('runtime_cleanup_unconfirmed: inspect status before explicitly resuming')
            except Exception:
                final_state = 'failed'
                raise
            finally:
                try:
                    if source is not None:
                        client.report_terminal(runtime_kind, final_state, 'runtime_failed' if final_state == 'failed' else None)
                except Exception: print('stop_report_unconfirmed')
                client.close()
    finally:
        if connector_lease is not None: connector_lease.close()
        journal.close()


if __name__ == "__main__":
    main()
