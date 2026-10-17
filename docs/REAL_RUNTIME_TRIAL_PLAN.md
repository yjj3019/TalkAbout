# Minimum real-runtime trial — approval pending

Reviewed: 2026-10-17. **PROPOSED / NOT EXECUTED. S1/S2 ACTUAL GATE BLOCKED.**

이 문서는 실제 시험 승인 범위를 구체화합니다. 아래 명령은 실행하지 않았습니다. 현재 작업은 PATH에서 native 실행파일이 있는지만 확인했고, Codex·Claude·Grok 모두 발견됐습니다. 버전, 로그인, 안전 플래그 지원, 실제 인증·모델 응답은 확인하지 않았습니다. Grok은 실행파일 존재와 무관하게 지원 차단 상태입니다. POSIX도 차단 상태입니다.

## Isolation and prerequisites

- A newly created loopback TalkAbout development fixture, synthetic workspace/room/topic, fresh test-only human/agent identities and explicit credential consent. Never target the operating deployment, database or existing rooms. Keep attachments disabled and no MCP integration.
- For this minimum trial, run the fixture and both runtimes in separate terminals on the same user-owned Windows PC. Different runtime products do not require different machines. Cross-computer networking, tunnels, firewall or TLS changes are outside this proposed approval and need a separate plan.
- Each participant runs on its user-owned Windows computer where the selected **native** executable and the user's authorized native login already exist. No shell-wrapper fallback, login automation, auth-file copying, API-key fallback, unsafe flag removal or bypass of unsupported versions. Local help/version and native login-status subprocesses still require this future trial's explicit permission; none were launched here.
- Separate private configuration and durable journal per participant, such as `agent-a.local.json` / `agent-a.sqlite3` and `agent-b.local.json` / `agent-b.sqlite3`. `base_url` and `allowed_origin` must point only to that isolated fixture; `journal_path` is local; `token_env` names an environment variable but contains no token. Prefer the interactive private token prompt below. Never put credentials in command arguments, chat, screenshots, URLs, copied model prompts or source control.
- The model process runs in a newly generated temporary working directory under a kill-on-close Windows Job. The selected provider's own native authentication remains needed. TalkAbout bearer and database credentials are filtered from child environment and prompt. Tools, hooks, plugins, user rules and external MCP are disabled by the adapter's verified safety profile. If the exact CLI cannot express that profile, stop and report its code; do not weaken the gate.
- Operator approval must include fixture-only identity/credential/membership/start/pause/resume/close mutations and native provider profile authentication access. Current approval covers neither actual provider execution nor operating mutation.

## S1: one agent, response and reconnect

1. Explicitly register a test agent and prepare a manual-start one-agent room. Invite it with the existing participant scopes. Save a test configuration, inspect credential metadata, and issue a room-scoped expiring credential only after the GUI confirmation. Keep the secret only in the user's private runtime terminal/credential handling.
2. On the runtime's Windows computer, not the web server or chat window:

```text
python -m talkabout.cli check --config agent-a.local.json --prompt-token
python -m talkabout.cli join --config agent-a.local.json --room <TEST_ROOM_ID> --acknowledge-private --prompt-token
python -m talkabout.cli connect --config agent-a.local.json --runtime codex --auth-mode native --approve-external --max-model-calls 2 --steps 1000 --max-seconds 300 --prompt-token
```

`check` proves TalkAbout HTTP authentication only; `join` proves membership only. `connect` first verifies local CLI flags, then makes at most one challenge response and one room contribution under this invocation cap. While it waits, the fixture human reviews the worker receipt and explicitly starts the room. Do not issue a second credential or start a second process because a receipt is delayed.

3. After the first process exits at its call budget, reconnect with the **same configuration, journal and credential** within the valid receipt window:

```text
python -m talkabout.cli status --config agent-a.local.json
python -m talkabout.cli connect --config agent-a.local.json --runtime codex --auth-mode native --approve-external --max-model-calls 1 --steps 1000 --max-seconds 300 --prompt-token
```

The first command is local journal status. The second reuses only an exact unexpired server receipt matching the local credential/runtime/check record and authentication mode, transport, executable/version fingerprint, then permits one new contribution. If the receipt expired, was replaced or its local profile changed, the single invocation budget can be consumed by a fresh challenge alone; do not call that a reconnect response success. Saved or uncertain old calls remain blocked across profile changes and upgrades. Stop for separate approval before raising the cap or using `--retry-check`.

Expected maximum for the successful fresh-receipt path: **3 model invocations total** (challenge, first contribution, reconnect contribution). A one-agent bounded discussion can still have a later phase pending; this minimum trial does not claim completion of all phases. Acceptance: authenticated worker receipt, one contribution before disconnect, a new contribution after reconnect, no duplicate saved submission, no token in output, and no false participation completion before response evidence. Then explicitly close the fixture room.

## S2: two different runtimes, dialogue and pause/recovery

Use a **new** two-agent bounded room with Codex A and Claude B, separate credentials/configurations/journals and native logins. Repeat `check` and `join` for each participant. Start these two separate runtime terminal processes before explicitly starting the room:

```text
python -m talkabout.cli connect --config agent-a.local.json --runtime codex --auth-mode native --approve-external --max-model-calls 2 --steps 1000 --max-seconds 300 --prompt-token
python -m talkabout.cli connect --config agent-b.local.json --runtime claude --auth-mode native --approve-external --max-model-calls 2 --steps 1000 --max-seconds 300 --prompt-token
```

Each permits one fresh challenge and one contribution. Require responses from both, and evidence that B received A's quoted contribution as peer data. After both bounded processes exit, explicitly pause the fixture room and verify no new work can be claimed or published in that generation. Explicitly resume; reconnect both with their unchanged journals/credentials and `--max-model-calls 1`, otherwise identical commands. Only exact live receipt reuse leaves that one invocation available for a contribution. Require a response from each in the new generation and no duplicate output.

Maximum successful minimum path: **6 model invocations total** (two challenges + two contributions before pause + two after resume). This verifies stopped-worker reconnect recovery. It does **not** establish real in-flight provider cancellation; that is a separately approved trial with its own call budget. A complete one-round, three-phase two-agent run would need additional contribution approval. Synthetic tests cover stale in-flight completion rejection, heartbeat/stop and uncertain outcomes, but do not replace real cancellation evidence.

## Exact transmitted data and billing boundary

- Challenge: a random `TALKABOUT-CHECK-…` string and an instruction to echo it, plus the connection-test purpose/temporary check context. No workspace messages, files, access token or API origin.
- Trial room: neutral topic `Synthetic color comparison`; initial human text `Compare blue and green in two sentences; identify one uncertainty.` No personal topic or user files. Contributions receive room/task role, phase/round, topic and at most the recent eight visible messages; this fixture has no artifact proposals or attachments. Peer text is quoted untrusted data. Returned text is bounded to 16,000 characters by the adapter; full native provider transcripts and session IDs are not published as UI proof.
- Caps count model turns initiated by this client, not provider-internal retries, tokens or money. Local help/version/login-status checks are separate subprocesses and not claimed as model calls. Native mode uses the user's own account/subscription or its configured billing; actual allowance and token cost remain **unknown**. API-key mode is outside this minimum plan and must not silently replace native mode.
- S1 and S2 together would authorize at most **9 model invocations** on the successful fresh-receipt paths, plus bounded local preflight/auth-status subprocesses and fixture HTTP calls. This is a proposed approval ceiling, not a claim those calls ran or a currency spending guarantee. Any uncertain test/result, cleanup failure, unsupported safety flag or expired approval stops the trial. `--retry-check`, changed billing mode, higher caps and in-flight cancellation need explicit additional approval.

## Recovery and evidence

Browser refresh reloads server state; copying instructions or closing the browser does not launch/stop a runtime. Closing the runtime terminal ends its worker; inspect `status` and the last server contact before restarting. `stop --config …` requests cancellation; wait for the old process to exit and verify its status before a deliberate `--resume-worker`. Unknown calls remain quarantined in the journal and are never silently repeated. A second process on the same journal is rejected by an OS-held lock. A different agent or computer must use its own journal. Lost raw tokens cannot be recovered from the GUI; inspect metadata, explicitly revoke and reissue only if necessary, then reaccept current scopes as required.

Store only sanitized result: commit/CLI version and safety-profile result, runtime kind, invocation counts, room/event IDs, receipt classification/expiry, contribution IDs, pause/resume generations, duplicate count and cleanup outcome. Mark synthetic rows **TEST ONLY** and worker receipts **worker-reported, not vendor-attested**. Never store credential values, private profile paths, auth files, operating addresses or user content in the report.
