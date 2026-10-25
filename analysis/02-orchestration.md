# 02 — Orca 오케스트레이션 · 워커 메시징 정밀 분석

- 대상: `stablyai/orca` HEAD `51dcef8` (clone `D:\AI-Codding\Claude\TalkAbout\orca-analysis`)
- 경로 규칙: **Orca 경로는 `orca-analysis/` 루트 기준**, **TalkAbout 경로는 `D:\AI-Codding\Claude\TalkAbout` 루트 기준**.
- 방법: 문서(`skill-guides/orchestration*`, `docs/site/content/docs/cli/orchestration.mdx`) → CLI 핸들러 → main 런타임 DB 계층 → TalkAbout 대응 모듈 순으로 실제 코드를 읽고 대조했다.
- 판정 태그: `[채택]` 바로 쓸 수 있음 / `[차용]` 개념만 참고 / `[대조]` Orca가 못하는 것을 TalkAbout이 이미 함 / `[제외]` 맞지 않음(이유 필수).
- 비용: S(≤1일) / M(2~5일) / L(1주+).
- 코드 복사 없음. Orca는 MIT(`LICENSE`)이지만 본 보고서는 프로토콜·구조 참조만 하므로 고지 의무 대상 코드는 없다.

---

## 0. 결론 요약

1. Orca 오케스트레이션의 실체는 "에이전트끼리 대화"가 아니라 **Run(네임스페이스) · Task(작업) · Dispatch(1회 시도) · Delivery(수신 배치) · Question(블로킹 질문) · Gate(결정 대기)** 6개 행위자에 대한 SQLite 상태기계다 (`src/main/runtime/orchestration/types.ts:3-23`, `:60-71`, `:115-127`, `:279-316`).
2. 워커 식별은 "터미널 제목"이 아니라 **pane key + process incarnation + launch token hash** 3중 증거로 확정하고, 재부착 때마다 `consumer_generation`을 올려 이전 소비자의 메일을 fence한다 (`src/main/runtime/orchestration/db/worker-dispatch/worker-dispatch-authority.ts:50-78`).
3. 메시지는 **durable enqueue + FIFO Delivery + 명시적 ack + 재생(replay)** 구조다. 성공한 `send`는 "enqueue 증명"일 뿐 수신·수락 증명이 아니다 (`src/cli/handlers/orchestration/message-send-handler.ts:90-121`, `src/main/runtime/rpc/methods/orchestration/messaging/check-run.ts:135-157`).
4. Task DAG는 **생성 순서가 곧 위상정렬**이라 순환은 구조적으로 불가능하지만(`db/tasks/task-store.ts:36-41`), **의존 task가 failed/blocked로 굳으면 교착을 감지하는 코드가 현행 경로에 없다.** 감지 로직은 폐기된 Coordinator 루프에만 남아 있다(`coordinator-dag-convergence.ts:19-28` vs `src/cli/handlers/orchestration/dispatch-handlers.ts:71-85`).
5. keepalive/watchdog는 **경고 전용**이다. heartbeat 5분 권장, 10분 무소식이면 경고 로그만 남기고 자동 실패시키지 않는다(`preamble.ts:31-32`, `coordinator-task-dispatch.ts:15-28`). "unknown은 종료 증거가 아니다"라는 원칙이 stop/retry/release 전 단계에 강제된다(`skill-guides/orchestration/references/recovery-and-cleanup.md:52-63`).
6. **TalkAbout의 서버 강제 round-robin + pull journal은 Orca보다 실패 복구에서 우위**다(`talkabout/worker.py:235-457`, `docs/ARCHITECTURE.md:63-79`). 빌려올 것은 모델이 아니라 ① Delivery/ack 관측성 ② 실패 분류와 재시도 예산 분리 ③ 무진행(stall) 감지 ④ attention/next-action 투영 ⑤ 문서-바이너리 동기화 방식이다.

---

## 1. 계층 지도 (실제 코드 기준)

| 행위자 | 저장 위치 | 핵심 불변식 | 근거 |
|---|---|---|---|
| Run | `runs` | coordinator(터미널/pane/session)에 바인딩. 스케줄·배치를 하지 않음 | `db/schema/create-graph-tables-sql.ts:208-218`, `runs/run-scope.ts:112-169` |
| Task | `tasks` | `status ∈ {pending,ready,dispatched,completed,failed,blocked}`, `deps`는 JSON 배열 텍스트 | `:115-135`, `types.ts:21`, `:261-277` |
| Dispatch | `dispatch_contexts` | Task 1회 시도. `retry_of_dispatch_id`, `depth`, `consumer_generation`, `failure_count` | `:140-180`, `types.ts:279-316` |
| Worker(리소스) | `worker_dispatches` + `worker_terminal_resources` | `starting/ready/start_unknown/failed/succeeded/stopping/stop_unknown/stopped/abandoned` | `types.ts:142-151`, `worker-report-admission.ts:4-16` |
| Message | `messages` | 9종 타입, `delivery_contract ∈ {legacy_direct,current_delivery,audit_only}` | `types.ts:3-19`, `:239-259` |
| Delivery | `deliveries` | `outstanding → acknowledged / fenced`, ack 전까지 같은 배치 재생 | `types.ts:60-71`, `db/runs/run-delivery.ts:21-53` |
| Question | `question_threads` | `pending/answered/closed`, message_id가 곧 질문 id | `types.ts:115-127`, `db/questions/question-threads.ts:7-63` |
| Gate | `decision_gates` | `pending/resolved/timeout`, Task를 `blocked`로 만든다 | `types.ts:318-328`, `db/decision-gates/decision-gate-store.ts:8-140` |
| MutationReceipt | `mutation_receipts` | `(caller_fingerprint, request_id) → method, payload_hash, pending/completed` | `types.ts:131-140`, `db/mutation-receipts/mutation-receipt-store.ts:35-109` |

CLI 표면은 `src/cli/handlers/orchestration/*` 25개 파일이 전담하고, 서버 구현은 `src/main/runtime/orchestration/`(약 200파일) + `src/main/runtime/rpc/methods/orchestration/`(worker/messaging/gates/runs/federation)로 갈린다. **`src/main/orchestration/`은 존재하지 않는다**(확인: 디렉터리 부재).

---

## 2. Q1 — 워커 등록·식별 프로토콜, run scope

### 2.1 terminal identity 확정 순서 (CLI)

`resolveOrchestrationTerminalHandle`이 단일 진입점이다(`src/cli/handlers/orchestration/terminal-identity.ts:13-52`).

1. `--from`/`--terminal` 명시값 → 주입된 세션 id가 있으면 `sessionAddressForHost`로 정규화(`:21-25`).
2. 주입 세션 id(`readInjectedAgentSessionId`)만 있으면 **undefined를 반환해 호스트가 해석**하게 위임(`:22-25`). CLI가 pane을 추측하지 않는다.
3. `ORCA_TERMINAL_HANDLE` 환경변수. 단 `from`이고 `validateEnvHandle`이면 **live 여부를 먼저 검증**하고, stale이면 `ORCA_PANE_KEY`로 **re-mint**한다(`:29-42`, `:105-128`). 이유가 주석에 있다: "셸이 재발급 후 stale handle을 보관할 수 있다"(`:32`).
4. pane 없는 structured 세션이면 **추측 대신 거부**한다 — "모든 live handle은 다른 pane의 것이고, 남의 handle을 넘기면 그 pane의 mailbox를 소비한다"(`:44-47`, `:196-204`).
5. 그래도 없으면 `getTerminalHandle(..., {requireUnambiguous:true})` — **유일한 active terminal일 때만** 인정(`:179-193`).

즉 "등록"이라는 절차가 따로 없고, **명령이 실행되는 순간의 증거(주입 세션 > env handle > 유일 active pane)** 로 신원을 확정한다. 서버는 이 handle을 다시 pane/process 증거로 검증한다(2.2).

### 2.2 codex 필터링의 정체

`throwNoActiveSenderTerminal`은 Codex가 `CODEX_THREAD_ID`를 세팅했는데 `ORCA_TERMINAL_HANDLE`이 없으면 **전용 오류 메시지**를 낸다(`terminal-identity.ts:211-222`). 내용: "Codex의 `shell_environment_policy`가 변수를 제거했다. 같은 방식으로 재실행해도 복구되지 않는다. Orca 터미널에서 `codex`를 직접 타이핑하거나 `inherit = "all"` + `include_only`에 `ORCA_*`를 넣어라"(원문 인용). 회귀 테스트가 별도로 존재한다(`terminal-identity-codex-filtered.test.ts:20-31`, issue #25775 표기).

핵심은 "codex 전용 필터 로직"이 아니라 **codex가 자기 env를 필터링해 신원이 사라진 상황을 식별해, 조용히 다른 pane을 집어가는 대신 실패**시킨다는 점이다. 4번 규칙(추측 금지)의 구현이다.

### 2.3 서버측 권위 확정 (dispatch 단위)

- `prepareStartingWorkerAuthority`가 dispatch에 `assignee_handle + assignee_pane_key + assignee_orca_session_id + process_incarnation + host_scope + launch_token_hash`를 한 트랜잭션으로 커밋하고 `consumer_generation += 1`, 그리고 **이전 소비자의 미확인 delivery를 fence**한다(`db/worker-dispatch/worker-dispatch-authority.ts:22-78`, `:78`).
- `worker_done`/heartbeat 권위는 **pane key 우선, legacy row만 handle 비교**다(`lifecycle-reconciliation.ts:16-27`). pane이 tab 분리로 바뀌어도 `leafId` 비교로 동일 pane을 인정한다(`:7-14`).
- 프로세스 동일성은 `workerReportRefusal`이 최종 판정한다: `stopping`이면 무시, `processCurrent=false`면 `worker_identity_changed`(`worker-report-admission.ts:24-49`).
- 시작 시 "turn start 관측"이 별도 단계다. PTY write가 accepted여도 turn 시작이 관측되지 않으면 `ready`로 올리지 않고 `start_unknown` + `outcome_unknown` + `nextCommands`를 반환한다(`worker-start-readiness-settlement.ts:106-145`). chat worker는 `pending`/`queued` 모두 unobserved로 취급하고, worker가 이후 보고하면 정상 settle한다(`deliver-worker-dispatch-preamble.ts:114-134`).

### 2.4 run scope 규칙

- `run-create`/`run-use`가 coordinator identity(handle, pane, session)를 바인딩한다(`cli/handlers/orchestration/run-handlers.ts:13-34`).
- 이후 모든 변이/조회는 `resolveRunScope`로 **바인딩된 Run만** 인정한다. 명시 `--run`이 바인딩과 다르면 `consumer_fenced`(`rpc/methods/orchestration/runs/run-scope.ts:113-168`).
- `worker-list`는 기본이 **바인딩 Run**, `--run`이 override, 미바인딩이면 전체 + "Scope: all Runs" 줄을 출력한다(`cli/handlers/orchestration/worker-list-run-scope.ts:8-34`).
- 바인딩 키가 없으면 `stable_pane_required`/`run_required`로 거부한다(`run-scope.ts:100-108`, `:142-160`).

### 2.5 판정

| 항목 | 판정 | 비용 | TalkAbout 부착 위치 |
|---|---|---|---|
| "명령 실행 시점 증거로 신원 확정 + 애매하면 거부" | `[채택]` | S | `talkabout/participation.py:18-26`(credential binding) 확장, `talkabout/core.py:96-110` |
| pane+process+launch token 3중 증거, 재부착 시 generation fence | `[차용]` | M | `talkabout/identity.py` runtime_check, `talkabout/core.py:96-110` |
| codex식 "신원 상실을 식별해 실패" 진단 메시지 | `[채택]` | S | `talkabout/worker.py:204`(`/agents/me` 실패 시), `talkabout/api.py` 오류 코드 |
| start_unknown(관측 실패를 ready로 승격하지 않음) | `[채택]` | S | `talkabout/worker.py:302-328` 게이트에 `turn_unobserved` 상태 추가 |

TalkAbout은 터미널이 아니라 **세션**이므로 terminal identity 문제는 없다. 대신 "이 요청이 정말 그 워커 프로세스에서 왔는가"는 credential binding(`participation.py:18-26`)과 runtime_check(`core.py:96-110`)로 이미 상당 부분 해결돼 있다 → `[대조]`. Orca에서 가져올 것은 **launch token hash + process incarnation = 1회 실행 권위**라는 개념이다. TalkAbout의 `lease_id`/`_file_lease_hash`(`core.py:275-278`)가 파일 읽기 fence로 유사 기능을 한다.

---

## 3. Q2 — inbox 메시지 프로토콜

### 3.1 스키마

메시지 타입 9종: `status, dispatch, worker_done, merge_ready, escalation, handoff, decision_gate, question, heartbeat`(`types.ts:3-13`). priority 3종(`:17`). 행 스키마는 `MessageRow`(`:239-259`): `from_handle, to_handle, subject, body, type, priority, thread_id, payload(JSON), read, sequence, delivered_at, sender_pane_key, pointer_enter_pending, pointer_pty_id, pointer_process_incarnation`.

lifecycle 메시지 payload는 **CLI가 구조화 플래그로 강제**한다(`cli/handlers/orchestration/message-payload.ts:4-60`): `--task-id`, `--dispatch-id`, `--outcome(succeeded|failed)`, `--files-modified`(콤마 분할), `--report-path`, `--phase`. `--payload`와 구조화 플래그 동시 사용은 거부(`:24-29`) — PowerShell 인용 파괴를 피하려는 설계 이유가 주석에 있다(`:30`). `worker_done`/`heartbeat`는 그룹 주소 금지(`message-send-handler.ts:54-62`)이고, identity가 없으면 focus 추측 대신 거부한다(`:78-86`).

### 3.2 전달 / 확인(ack)

- coordinator 수신은 `orchestration check`. 기본은 **바인딩 Run의 가장 오래된 미ack Delivery(최대 50건)** 를 반환하고, **ack 전까지 같은 배치를 재생**한다(`skill-guides/orchestration/references/messaging-and-gates.md:10-24`, `check-run.ts:135-157`).
- Delivery는 `outstanding → acknowledged/fenced`(`types.ts:60-71`). 조회/대기 전에 `requireCurrentConsumer`가 `consumer_generation`을 대조하고(`db/runs/run-delivery.ts:6-19`), `getOrCreateRunDelivery`는 그 generation을 Delivery 생성 인자로 넘긴다(`:21-37`). 대기 중에도 재검증해 교체되면 `consumer_fenced`를 던진다(`rpc/methods/orchestration/messaging/check-run.ts:75-103`, `:188-197`).
- `--peek`/`--all`은 **비소비** 읽기다(`check-run.ts:137-141`, `:108-123`). 구버전 런타임이 `peek`을 무시하는 문제는 `--unread/--peek/--all` 상호배타 + 결과 후필터로 방어한다(`message-check-handler.ts:34-40`, `:95-123`).
- `markAsDelivered`는 push 재생만 막고, **read는 오직 명시적 check만** 세운다(주석 원문: "delivered_at prevents push replay; only explicit check sets read", `db/messages/message-inbox.ts:90-92`, `:156-180`).
- lifecycle 메시지가 권위 검증에 실패하면 **삭제하지 않고** `type=status`(escalation/decision_gate)로 강등 + `priority=high` + payload에 `_orcaLifecycleRejection` 마커를 남겨 "감사 가능하지만 비활성"으로 만든다(`message-inbox.ts:61-88`, `db/lifecycle-rejection-marker.ts:1-25`).
- 장기 대기 중에는 15초마다 stderr로 `{_keepalive:true, elapsedMs, deadlineMs}` 한 줄을 찍는다(`cli/handlers/orchestration/check-keepalive.ts:16-31`, 문서 `docs/site/content/docs/cli/orchestration.mdx:129`). stdout은 최종 결과만 유지한다.

### 3.3 blocking ask/reply (질문-대기-응답)

- 기본 대기 600,000ms(10분), **1회 상한 1,800,000ms(30분)**, 클라이언트는 서버보다 5초 더 기다린다(`src/shared/orchestration-ask-timeout.ts:1-14`). 초과 값은 질문 생성 전에 거부하고 "더 긴 대기는 같은 질문을 resume하라"고 안내한다(`cli/handlers/orchestration/question-handler.ts:24-30`).
- 서버 구현(`rpc/methods/orchestration/messaging/ask-methods.ts:140-199`): ① `recordMutationReceipt`로 **질문 id를 먼저 커밋** → ② deadline 루프에서 `getQuestion` 폴링 + `waitForMessage` 로 wake 대기 → ③ `answered`면 answer 반환, `signal.aborted`면 `cancelled+connectionLost`, deadline이면 `timedOut`을 반환한다. **타임아웃/끊김이 질문을 취소하지 않는다**(`:183-199`).
- resume은 같은 message_id로만 가능하고, 다른 dispatch의 질문이면 `question_not_found`(`ask-methods.ts:91-98`). 답변 쪽은 `answer_conflict`로 다른 본문 재답변을 거부하고, 같은 본문이면 `duplicate:true`(`db/questions/question-threads.ts:123-136`). 답변 메시지는 즉시 read 처리해 check로 재생되지 않게 한다(`:146-147`).
- 질문은 dispatch settle 시 전부 `closed`가 되고, 그 전에 helper 메일을 Run으로 되돌린다(`:170-185`).
- 그룹 주소로 ask 불가 — "첫 답변자를 특정할 수 없다"(`ask-methods.ts:33-38`).
- 타임아웃 시 CLI는 exit code 1 + **재개 명령문**을 stderr로 출력한다(`question-handler.ts:99-127`). `--json`이면 exit code만.

### 3.4 판정

| 항목 | 판정 | 비용 | TalkAbout 부착 위치 |
|---|---|---|---|
| Delivery 배치 + ack + 재생 (서버→워커 제어 메시지) | `[차용]` | M | `talkabout/core.py:504-569`(events), `talkabout/participation.py:61-76`(ack) |
| lifecycle 메시지 실패 시 "삭제 대신 강등+마커" | `[채택]` | S | `talkabout/core.py:207`(mutation 실패 처리), `talkabout/store.py` |
| ask 타임아웃에 **재개 명령**을 함께 반환 | `[채택]` | S | `talkabout/worker.py:412-418`, `talkabout/api.py` 오류 본문 |
| blocking ask로 LLM을 대기시키는 구조 | `[제외]` | — | TalkAbout 정본은 "LLM이 cursor/heartbeat/idempotency를 관리하지 않는다"(`docs/ARCHITECTURE.md:95`). 런타임은 1회 bounded call이므로 질문 대기를 모델에 맡기면 설계가 뒤집힌다. |
| 30분 상한·5초 grace 같은 **유한 대기 상수 분리** | `[채택]` | S | `talkabout/worker.py:29-41`(RetryPolicy) 옆에 `BoundedWait` 상수 모듈 |

---

## 4. Q3 — task DAG, readiness, gate 전이, 교착

### 4.1 순환은 구조적으로 불가능

`createTask`는 `deps`의 각 id가 **이미 존재하고 같은 run**인지 검증한다(`db/tasks/task-store.ts:36-41`). id는 검증 후 생성된다(`:42`). 따라서 "의존 대상이 먼저 존재해야 한다"는 제약이 생성 순서를 위상정렬로 강제하며, **A→B→A 순환은 만들 수 없다**. 별도 cycle detection 코드가 없는 것은 누락이 아니라 설계다(코드에 cycle 검사 없음 확인).

### 4.2 readiness 판정 2지점

- **생성 시**: SQL `EXISTS(... dependency.status <> 'completed')`면 `pending`, 아니면 `ready`(`task-store.ts:57-64`). 즉 "의존이 전부 completed"가 ready의 정의다.
- **완료 시**: `promoteReadyTasks(completedTaskId)`가 **완료 트랜잭션 안에서** pending 후보를 훑어 deps가 모두 completed면 `pending → ready`로 전이한다(`task-store.ts:207-233`, 호출부 `db/tasks/task-status-transition.ts:104-106`, `db/dispatch-context/worker-report-settlement.ts:279-281`). 주석 원문: "Promote children inside the completion transaction so ready work cannot be stranded".
- `task-list --ready`가 이 뷰를 그대로 노출한다(`cli/handlers/orchestration/task-handlers.ts:58-64`).

### 4.3 상태 전이 규칙

- Task는 `pending/ready/dispatched/completed/failed/blocked` 6종(`cli/handlers/orchestration/task-handlers.ts:9-16`). lifecycle 그래프상 task는 **모든 상태에서 모든 상태로** 갈 수 있고, 제약은 호출부가 건다(주석: "Public taskUpdate accepts every status; its caller enforces active-Dispatch invariants", `db/lifecycle-transition.ts:63-72`).
- 반면 Dispatch/Worker는 terminal 상태에 outgoing edge가 없다(`:73-90`).
- 전이는 **compare-and-set**이다: `UPDATE ... WHERE id=? AND status IN (...)` 후 `changes !== 1`이면 `lifecycle_conflict`(`:176-187`). 동시 갱신은 여기서 걸린다.
- `updateTaskStatus`는 ① supervised worker가 살아있으면 terminal 이동 거부(`task-status-transition.ts:38-59`) ② `dispatched`로 가려면 active dispatch 필수(`:60-66`) ③ 같은 상태+결과 없음은 idempotent 반환(`:74-77`) ④ `lifecycle_conflict`인데 현재 상태가 목표와 같으면 idempotent(`:89-100`).
- 실패 시 task는 `ready`로 되돌아가 재시도 가능, circuit break면 `failed`(`db/dispatch-context/dispatch-completion.ts:205-224`).

### 4.4 gate / decision gate 전이

| 전이 | 트리거 | 부수효과 | 근거 |
|---|---|---|---|
| (없음) → `pending` | `gate-create` (coordinator) | task를 `blocked`로, active dispatch 완료 처리 | `decision-gate-store.ts:72-85` |
| `pending` → `resolved` | `gate-resolve` | task를 `ready`로 | `:98-128` |
| `pending` → `timeout` | `timeoutGate()` | 상태만 변경 | `:130-140` |

- gate 생성은 **requester가 그 task의 active dispatch 소유자**여야 하고(`:38-54`), **supervised worker가 살아있으면 거부**한다(`:55-71`). "일하는 워커의 task를 게이트로 잠그는" 경쟁을 차단하는 장치다.
- `timeoutGate`는 **production 호출자가 없다**(grep 결과 `db.test.ts:382`만). 즉 gate는 **사람이 풀 때까지 무한 대기**한다. 의도된 설계로 보이지만(주석: "Only explicit human gate resolution may unblock these approval checkpoints", `coordinator-decision-gates.ts:51-53`) 운영상 교착 위험이며, 이 re-block 루프조차 폐기된 Coordinator에만 있다(`coordinator.ts:159-166`).

### 4.5 교착(deadlock) 감지: 사실상 없음

- 유일한 감지 흔적은 `evaluateDagConvergence`의 로그 문장이다: "Stuck: N tasks blocked with no active tasks. Resolve decision gates to continue."(`coordinator-dag-convergence.ts:19-28`).
- 이 함수는 `Coordinator.tick()`에서만 호출되고(`coordinator.ts:305-311`), `coordinator-start`/`stop`은 **은퇴한 명령**으로 아무 효과 없이 `orchestration_migration_required`를 던진다(`cli/handlers/orchestration/dispatch-handlers.ts:71-85`, 문서 `docs/site/content/docs/cli/orchestration.mdx:17-21`).
- 따라서 현행 supervisor(사람+에이전트 coordinator) 경로에서 **의존 task가 failed로 굳거나 gate가 방치되면 아무도 경고하지 않는다.** `warnStaleDispatches`도 같은 폐기 루프에 있다(`coordinator.ts:163`).

### 4.6 판정

| 항목 | 판정 | 비용 | TalkAbout 부착 위치 |
|---|---|---|---|
| "의존은 먼저 생성된 task만" → 위상정렬 by construction | `[채택]` | S | `talkabout/domain.py:140-150`(`task_capacity` 옆) — 향후 DAG 도입 시 |
| 완료 트랜잭션 안에서 후속 ready 승격 | `[채택]` | S | `talkabout/domain.py:106-150`(schedule), `talkabout/core.py:296-333` |
| gate 생성 시 "살아있는 워커의 task는 잠글 수 없음" | `[채택]` | S | `talkabout/core.py:365-431`(controls) |
| gate 무한 대기 + timeout 미배선 | `[제외]` | — | TalkAbout은 벽시계 deadline이 우선(`docs/MODE_HANDOVER.md:19`). 무한 대기를 복제하면 `task_seconds` 계약이 깨진다. |
| 교착 감지(전용 감지기 없음) | `[대조]` | — | TalkAbout도 없다(아래 4.7). 둘 다 약점 → 별도 채택 |
| task DAG 자체 | `[차용]` | L | 아직 TalkAbout에 없음(`talkabout/domain.py:145-148` task dict에 deps 필드 없음) |

**4.7 TalkAbout의 교착은 "조용한 정지"다.** `schedule()`은 eligible이 비면 `return`하고(`talkabout/domain.py:128-131`), `next_turn`이 None이면 round_limit일 때만 close한다(`domain.py:132-139`). 전원 blocked/unavailable이면 **room 벽시계 deadline(기본 900초, `domain.py:90-91`, `:110-116`)까지 아무 신호 없이 대기**한다. Orca보다 감지기가 없는 건 아니고 **둘 다 없다**. 이 항목은 Orca에서 빌려올 수 없으므로 독자 구현 대상이다.

---

## 5. Q4 — keepalive / watchdog / worker_done / escalation

### 5.1 heartbeat

- preamble이 **5분 주기**를 지시하고, 상수 옆에 "5분 heartbeat는 10분 stale 경고 전에 한 번 놓칠 여유"라고 근거를 남긴다(`preamble.ts:31-32`, 지시문 `:91-101`). payload는 `--task-id`, `--dispatch-id`, `--phase`(investigating|implementing|reviewing|waiting).
- `ask`/`check --wait` 안에 블록된 동안은 heartbeat 생략 — "그 호출 자체가 liveness 신호"(`skill-guides/orchestration/references/worker-contract.md:10-12`).
- 서버는 `status='dispatched'`인 dispatch만 갱신한다(`dispatch-completion.ts:98-105`). 이유: "정산된 Dispatch의 늦은 heartbeat가 hung retry를 가리면 안 된다".
- 늦은 heartbeat는 read+delivered 처리해 **감사만 남기고 억제**(`lifecycle-reconciliation.ts:144-150`), 잘못된 pane의 heartbeat는 `sender_not_assignee`로 거부하고 메시지를 강등(`:152-158`). `worker_done` 이후 도착한 이전 heartbeat는 일괄 억제(`:320-336`).

### 5.2 watchdog: 경고 전용

- `getStaleDispatches(threshold)` = `status='dispatched' AND dispatched_at < threshold AND (last_heartbeat IS NULL OR < threshold)`(`dispatch-completion.ts:107-123`).
- `warnStaleDispatches`는 10분 임계로 **로그만** 남긴다. 코드 주석이 결정 이유를 명시한다: "Warn only: automatically failing a slow but healthy worker is worse than retaining a hung slot"(`coordinator-task-dispatch.ts:15-28`).
- 현행 경로에서 이 함수도 폐기 루프 소속이다(`coordinator.ts:163`). 대신 사람은 `worker-list`의 `projection.liveness/attention/nextAction`을 본다(5.4).

### 5.3 worker_done / escalation

- `worker_done`은 **정확히 1회**, `taskId+dispatchId+outcome` 필수, body는 3문장 executive summary(`preamble.ts:89`, `worker-contract.md:84-99`).
- 정산은 하나의 트랜잭션에서 ① dispatch `completed|failed` ② task 동일 상태 ③ worker 상태 ④ **미해결 질문 close** ⑤ 형제 dispatch close ⑥ 성공 시 `promoteReadyTasks`(`worker-report-settlement.ts:169-284`). 이미 같은 결과로 정산돼 있으면 `duplicate:true`(`:102-109`).
- 권위 불일치(다른 pane, task-dispatch mismatch, 이미 settle됨, 더 새로운 dispatch 존재)는 각각 `sender_not_assignee / task_dispatch_mismatch / inactive_dispatch / stale_dispatch`로 **거부하고 메시지를 강등**한다(`:71-161`, `lifecycle-reconciliation.ts:165-294`).
- `escalation`은 완료 **전** 개입 요청 전용(`worker-contract.md:76-82`). coordinator 측 triage는 payload의 `taskId+dispatchId` 정확 일치, 발신자 소유권, supervised worker 미종료를 모두 검사한 뒤에야 `failDispatch`를 호출한다(`coordinator-escalation-triage.ts:20-53`). 3회 실패 시 circuit break → task `failed`(`:55-63`, `db/dispatch-context/dispatch-circuit-breaker.ts:1` = `3`).
- 실패 escalation 메일은 **결정적 id**로 중복 발송을 막고(`worker-failure-escalation.ts:25-27`), chat 오류는 `hash(dispatchId, sessionId, kind, requestId)`로 id를 만든다(`chat-worker-error-escalation.ts:71-76`). 발송 전에 dispatch가 아직 active인지 재확인하고(`:119-128`), 메일 실패가 워커 실패 처리를 깨지 않도록 try/catch로 격리한다(`worker-failure-escalation.ts:63-70`).

### 5.4 관측성 신호의 3층

- `worker-show.projection.liveness.verdict` = fleet 판정(`live|unverifiable|exited`), `observation.status` = **PTY 생존만**(`cli/handlers/orchestration/worker-observation-handlers.ts:16-49`, 경고 주석 `:29`).
- `worker-list` 행마다 `attention.categories`, `attention.requiresAction`, **실행 가능한 `nextAction.argv`**를 붙인다(`worker-terminal-handlers.ts:150-190`). `unverifiable` 사유가 `missing_status/host_unavailable/capability_unsupported`면 "이 클라이언트의 공백"으로 구분한다(`recovery-and-cleanup.md:32-45`).
- 빈 대기 3회 연속이면 "맹목 대기 중단 → `worker-list --include-remote`로 열거"를 지침으로 강제한다(`skill-guides/orchestration.md:138-150`).

### 5.5 판정

| 항목 | 판정 | 비용 | TalkAbout 부착 위치 |
|---|---|---|---|
| heartbeat를 "생존 증거"로만 취급(완료 아님) | `[대조]` | — | TalkAbout은 워커 스레드가 3초마다 자동 heartbeat + 20초 lease 연장(`talkabout/worker.py:380-389`, `talkabout/core.py:288-289`) — 모델 비개입 원칙에서 더 강함 |
| 10분 stale 경고(자동 실패 금지) | `[채택]` | S | `talkabout/domain.py:117-125`(lease 만료 처리 옆), `talkabout/core.py:592-600`(interventions) |
| attention/nextAction 투영 | `[차용]` | M | `talkabout/core.py:15-67`(`public_room`)에 task별 `next_action` 추가 |
| liveness 3값(live/unverifiable/exited) | `[채택]` | S | `talkabout/core.py:96-110`(`_worker_ready` boolean → 3값 evidence) |
| "unknown은 종료 증거가 아니다" 원칙의 명문화 | `[채택]` | S | `docs/MODE_HANDOVER.md` 및 `docs/ARCHITECTURE.md`에 판정 규칙 추가 |
| 폐기된 coordinator에만 있는 watchdog | `[제외]` | — | 코드 경로가 죽어 있음(`dispatch-handlers.ts:71-85`) |

---

## 6. Q5 — 중복 억제와 멱등성: 계층별 6중 방어

| 층 | 키 | 동작 | 근거 |
|---|---|---|---|
| API 변이 | `(caller_fingerprint, request_id)` + `method`/`payload_hash` | 같은 id·다른 입력이면 `request_mismatch`; `pending`이면 재실행 대신 상태 반환 | `mutation-receipt-store.ts:35-109` |
| CLI 재시도 | 하나의 `randomUUID`를 120초 창 동안 재사용 | `worker_done`은 런타임 일시 장애 시 재전송해도 1회로 정산 | `message-send-handler.ts:15`, `:115-117`; `mutation-request.ts:16-44` |
| 호출자 지정 재시도 | `--retry-request <id>` | 유실 응답 후 같은 작업 identity로 replay/join | `recovery-and-cleanup.md:74-94`, `cli/.../mutation-request-show-handler.ts:8-27` |
| 실행 시작 | `launch_token_hash` 1회 커밋 | 같은 dispatch에 다른 토큰으로 두 번째 launch 거부 | `dispatch-context-store.ts:132-161`, `worker-dispatch-authority.ts:33-42` |
| 소비자 교체 | `consumer_generation` | 재부착 시 증가 + 이전 미ack delivery fence | `worker-dispatch-authority.ts:50-78`, `run-delivery.ts:6-19` |
| 정산 | 상태 조합(dispatch=expected ∧ task=expected) | 같으면 `duplicate:true` 반환(부수효과 없음) | `worker-report-settlement.ts:102-109` |
| 터미널 입력 | `hash(sessionId, messageIds[], contextClearOpId)` batch fingerprint | mint/reuse/stamp/park 4분기로 중복 입력 차단 | `structured-pointer-operation-id.ts:20-72` |
| 자동화 | 1회용 dispatch token + TTL 30분 + inFlight | 같은 run에 중복 디스패치 금지 | `src/main/automations/dispatch-tokens.ts:3-12`, `:51-75` |

추가로 **dispatch lock**이 있다: 터미널/pane당 active dispatch 1개만 허용하고, 원자적 claim(`changes !== 1`)으로 경쟁을 하나만 통과시킨다(`dispatch-context-store.ts:42-94`). 같은 트랜잭션에서 task를 `ready → dispatched`로 올린다(`:95-100`).

**한계(정직하게)**: 모든 변이에 자동 request id가 붙는 것은 아니다. `unavailableRetryMs`가 0인 일반 변이는 `--retry-request`를 사람/에이전트가 명시하지 않으면 응답 유실 시 재실행될 수 있다. 다만 그 경우도 상태 불변식(이미 settle된 dispatch, active dispatch 중복)이 2차로 막는다. 즉 **"전역 exactly-once"가 아니라 "층별 방어 + 상태기계 거부"** 다.

### 6.1 판정

| 항목 | 판정 | 비용 | TalkAbout 부착 위치 |
|---|---|---|---|
| `(caller, request_id, payload_hash)` 2단(pending/completed) 영수증 | `[차용]` | M | `talkabout/domain.py:153-165`(`replay`) — TalkAbout은 성공 시에만 저장하므로 pending 단계는 불필요(single-transaction). 2차 검증은 이미 있음 `[대조]` |
| `request-show`로 "적용됐는가" 질의 | `[차용]` | S | `talkabout/api.py` + `talkabout/domain.py:158-165`(`save_response` 조회 경로) |
| 명시적 `duplicate:true` 응답 | `[채택]` | S | `talkabout/core.py:236-240`(replay hit) 응답에 `duplicate` 필드 |
| batch fingerprint 기반 입력 중복 차단 | `[채택]` | S | `talkabout/worker.py:213-215`(turn-request key), `:285`(claim_key) — 이미 부분 적용, `last_seq` 의존 키 교체 |
| `launch_token`/`process_incarnation` 1회 권위 | `[차용]` | M | `talkabout/core.py:275-278`(lease_id/lease_hash) 확장 |

---

## 7. Q6 — runtime compatibility / 버전 드리프트

### 7.1 세 개의 서로 다른 "호환성"이 있다

1. **실행 파일 해석**(`cli/handlers/orchestration/runtime-compatibility.ts:3-42`): `ORCA_CLI_COMMAND` 우선, Linux 비관리 셸은 `orca-ide`(GNOME Orca 스크린리더 오인 실행 방지), dev 체크아웃은 `orca-dev`. 패키징된 Windows 런처가 잘못된 값을 주면 **질문을 만들기 전에** `invalid_argument`로 거부한다(`:11-23`). 이 값은 ask 타임아웃 재개 명령(`question-handler.ts:99-127`)에 그대로 박히므로, 틀리면 "재개 불가"가 된다.
2. **기능 capability 협상**: `status.get().capabilities`에 launch-preference capability가 없으면 `--model/--effort`를 쓰지 못하게 `incompatible_runtime`(`worker-launch-handler.ts:19-30`). `worker-list`의 pagination/remote semantics가 증명되지 않으면 **인벤토리를 출력하지 않고** `incompatible_runtime`(`worker-terminal-handlers.ts:144-149`). `request-show`가 없으면(method_not_found) 구버전 서버로 단정하고 대체 수단을 안내(`mutation-request-show-handler.ts:12-19`).
3. **신원·저장소 스키마 정합**: launch token + pane key + process incarnation + host scope를 증명하고, 불가능하면 hook attestation 또는 복원 receipt로 대체한다(`terminal-authority/verify-orchestration-compatibility-caller.ts:20-109`). DB는 저장된 스키마 버전이 post-v6 컬럼/인덱스/기본값을 실제로 갖고 있는지 `pragma table_info`로 확인하고, 주장만 높은 구버전 DB면 **v6부터 재마이그레이션**한다(`orchestration-schema-version-skew.ts:161-196`).

### 7.2 왜 필요한가

Orca는 클라이언트와 원격 서버가 **독립적으로 업데이트**된다. 공식 지침: "Treat unknown optional fields as absent. A new stream operation requires advertised capability because old decoders may silently drop unknown opcodes. Never fall back to local execution when remote authority or capability is unproven."(`skill-guides/orchestration.md:65-68`). 즉 "조용히 무시되는 옵션"이 가장 위험한 실패 모드이고, Orca는 그 경우 **아무 것도 하지 않고 실패**하도록 설계했다. 대표 사례가 `--peek` 파괴성 문제다: 구런타임이 peek을 무시하면 `--unread --peek`이 메일을 소비하므로, 모드 상호배타 + 결과 필터 + 경고로 막는다(`message-check-handler.ts:34-40`, `:95-123`).

### 7.3 판정

| 항목 | 판정 | 비용 | TalkAbout 부착 위치 |
|---|---|---|---|
| worker→server capability handshake + `incompatible_runtime` | `[채택]` | M | `talkabout/api.py`, `talkabout/worker.py:204`(`/agents/me` 응답에 `min_worker_version`/`capabilities`) |
| "옵션을 조용히 무시하지 않는다" 원칙 | `[채택]` | S | `talkabout/api.py` 필드 검증(`invalid_fields` 패턴 이미 존재, `participation.py:36-40`) |
| 재개/복구 명령에 실행 파일을 박아 반환 | `[채택]` | S | `talkabout/cli.py` |
| DB 스키마 skew probe + 안전 재마이그레이션 | `[차용]` | M | `talkabout/store.py`(스키마 버전 확인 유틸) |
| CLI 3종(orca/orca-ide/orca-dev) 분기 | `[제외]` | — | TalkAbout은 단일 서버·단일 worker 실행 파일 구조라 동일 문제가 없다 |

---

## 8. Q7 — 실패·중단·재시작 복구

### 8.1 Orca의 복구 규칙 (문서 + 코드 일치)

- 안전 원칙: **"긍정적 종료 증거만 stop/abandon/retry/release를 승인한다. 그 외 모든 관측, 부재 포함은 체크포인트다."**(`skill-guides/orchestration.md:28-31`). `unverifiable`은 항상 부재이며 아무 것도 승인하지 않는다(`recovery-and-cleanup.md:52-63`).
- 재시도: `failed|stopped`만, `--retry-of`로 **placement를 명시적으로 다시 지정**해야 한다(상속 없음, `recovery-and-cleanup.md:110-125`). 3연속 실패는 circuit break이고 circuit-broken dispatch의 retry는 `task_not_startable`로 거부(`:120-125`).
- 상태기계: worker terminal 상태에 outgoing edge 없음(`lifecycle-transition.ts:80-90`), `start_unknown/stop_unknown`은 "미증명"으로 남겨 부모 depth 계산에도 살아있는 것으로 센다(`db/dispatch-depth.ts:64-70`).
- 미수신 메일 복구: Delivery 재생 + `consumer_generation` fence(§3.2). 소비자가 교체되면 남은 delivery는 `fenced`가 되고, 워커는 `check`에서 `consumer_fenced`를 받으면 **즉시 중단하고 worker_done을 보내지 않는다**(`worker-contract.md:54-59`).
- 응답 유실: `request-show` + `--retry-request`(§6). 터미널 입력 accepted/미제출 경계는 `terminal send --wait-submit`으로 관측하고 타임아웃 시 재전송하지 않는다(`recovery-and-cleanup.md:92-94`).
- 재시작: 자동화는 surface ready 후 2분 유예를 두고 reattach를 재시도하며, 유예가 지나면 `dispatch_failed`로 **strand 처리**한다(`src/main/automations/retained-run-reconciliation.ts:3-5`, `:52-69`; `run-completion-watcher.ts:139-153`). 미해결 terminal은 orphan으로 남기고 startup에서 재조정한다(`run-completion-watcher.ts:73-82`).

### 8.2 재디스패치의 실제 의미

`failDispatch`는 task를 `ready`로 되돌리지만(`dispatch-completion.ts:205-224`), **자동 재디스패치 루프는 폐기됐다**. ready task는 사람/coordinator가 `worker-start`를 다시 호출할 때만 나간다. 폐기된 Coordinator만 tick마다 ready task를 자동 배치했다(`coordinator.ts:227-303`). 문서도 "failed면 `--retry-of`로 교체 시작"만 말한다(`recovery-and-cleanup.md:8-15`).

### 8.3 판정

| 항목 | 판정 | 비용 | TalkAbout 부착 위치 |
|---|---|---|---|
| "부재는 권한이 없다" 원칙 + positive-exit-only | `[채택]` | S | `talkabout/worker.py:470-472`(`safe_to_continue_after`) 주석·테스트로 명문화 |
| worker state에 `start_unknown/stop_unknown` 명시 | `[채택]` | S | `talkabout/domain.py:145-148` task/attempt 상태에 `uncertain` 정식화(현재 journal 내부 상태로만 존재, `worker.py:262-264`) |
| 재시도 시 placement 명시 요구 | `[차용]` | S | `talkabout/core.py:267-284`(claim 파라미터) |
| `consumer_fenced` 같은 "너는 교체됐다" 단일 신호 | `[대조]` | — | TalkAbout은 `stale_attempt`로 동일 역할 수행(`talkabout/domain.py:174-179`) — 이미 있음 |
| 미수신 메일 재생 테이블 | `[제외]` | — | TalkAbout은 cursor+journal로 at-least-once 보장(`talkabout/worker.py:136-164`, `:220-233`)이고 서버가 "미전달"을 추적하지 않는다. 전달 테이블을 추가하면 정본 설계와 중복 |

---

## 9. Q8 — 서브에이전트 팬아웃과 결과 비교/머지

### 9.1 팬아웃: 주소 체계로 구현

- 그룹 주소: `@all`, `@idle`, `@claude/@codex/@opencode/@gemini/@droid/@grok/@cursor`, `@worktree:<id>`(`src/main/runtime/orchestration/groups.ts:44-87`, 문서 `docs/site/content/docs/cli/orchestration.mdx:116-127`).
- 에이전트 그룹은 **호스트가 증명한 agent identity**로만 라우팅한다 — "제목(title)이 다른 에이전트를 언급해도 무시, 정체 불명은 fail closed"(`groups.ts:36-42`, `:73-84`).
- 발신 시 ① sender가 바인딩된 Run이 audience(`send-group.ts:121-140`) ② 후보는 그 Run의 live dispatch(+중첩 coordinator는 자식 Run mailbox)(`:56-88`) ③ mailbox 중복 제거 + 발신자 자신 제외(`:210-231`) ④ **한 thread_id 공유**(`:239-258`) ⑤ 원격(federated) 수신자는 "릴레이하지 않는다"는 **경고를 남기고 제외**(`:45-53`).
- `worker-start --spec`으로 한 호출에 task+attempt를 만들 수 있고(`skill-guides/orchestration.md:100-109`), 의존은 `task-create --deps`로만 표현한다(`coordinator-loop.md:7-21`).
- 병렬 레이스는 **문서화된 수동 워크플로**다: 같은 ref에서 worktree 3개 → 다른 agent 3개 → 같은 프롬프트 → 사람이 diff 비교 → 승자만 커밋, 패자는 worktree 삭제(`docs/site/content/docs/recipes/parallel-agents.mdx:5-19`).

### 9.2 비교/머지 프로토콜: **없다**

- `merge_ready`는 타입으로만 존재하고(`types.ts:7`), Coordinator는 무시(`coordinator.ts:191-195`), lifecycle도 무시(`lifecycle-reconciliation.ts:108-115`). 이를 처리하는 핸들러·비교기·머저는 없다(검색 결과 타입 정의·테스트 픽스처 외 사용처 없음).
- 리뷰 결과의 소유권도 사람에게 있다: "review-only worker_done은 findings 합성만 승인하고 coordinator의 파일 편집은 승인하지 않는다"(`coordinator-loop.md:62-67`).

### 9.3 판정

| 항목 | 판정 | 비용 | TalkAbout 부착 위치 |
|---|---|---|---|
| 다중 수신자 fan-out + 수신 불가 경고 | `[차용]` | M | TalkAbout은 방 단위 broadcast가 이미 기본(`talkabout/core.py:334-339` messages) → 개별 전달 보장이 필요할 때만 |
| host 증명 agent identity로 그룹 라우팅 | `[차용]` | M | `talkabout/identity.py`(runtime/credential scope) — "@runtime" 그룹 주소 |
| 자동 비교·머지 | `[제외]` | — | Orca에 구현이 없다. TalkAbout의 co_creation 제안/채택 모델이 이미 더 강하다: creator 제안 → 불변 artifact version → reviewer에게 최신 제안만 노출 → human adoption 시 close(`talkabout/core.py:307-328`, `:444-463`, `docs/ARCHITECTURE.md:96`) `[대조]` |
| 병렬 레이스 워크플로 | `[대조]` | — | TalkAbout은 서버가 슬롯을 직렬화하므로 "같은 프롬프트 N개 동시 실행"은 모델이 다르다. round 단위 병렬은 modes로 표현 가능(`talkabout/modes.py:18-41`) |

---

## 10. Q9 — 에이전트 주도 메시징 vs 서버 강제 round-robin

### 10.1 구조 대조표

| 축 | Orca (agent-initiated) | TalkAbout (server-enforced) | 근거 |
|---|---|---|---|
| 다음 발화자 결정 | coordinator(에이전트 또는 사람)가 `worker-start`를 호출한 순간 | 서버 `schedule()` + `next_turn()`이 슬롯 생성 | `preamble.ts:65-124` vs `talkabout/domain.py:106-150`, `talkabout/modes.py:6-41` |
| 작업 할당 시점 | dispatch 생성 시 assignee 고정, claim 없음(터미널이 곧 실행자) | task 생성 시 assignee 고정 후 **claim(lease)** 필요 | `dispatch-context-store.ts:14-49` vs `talkabout/domain.py:145-150`, `core.py:267-274` |
| 진실 원천 | orchestration SQLite + 메시지 mailbox | Event(seq) + room row lock + worker journal | `types.ts:239-259` vs `docs/ARCHITECTURE.md:81-87`, `talkabout/worker.py:114-164` |
| 실행 1회 보장 | dispatch 상태기계 + launch token + start 관측 | journal `call_started → uncertain`(자동 재시도 금지) | `worker-start-readiness-settlement.ts:106-145` vs `talkabout/worker.py:262-264`, `:417-418` |
| 워커 생존 감지 | 워커가 자발적 heartbeat(5분) | 워커 코드가 스레드로 자동 heartbeat(3초) | `preamble.ts:91-101` vs `talkabout/worker.py:380-389` |
| 실패 처리 | 워커 보고(worker_done/escalation) + circuit breaker | 서버 lease/deadline/attempt 예산 → expired | `dispatch-completion.ts:125-235` vs `talkabout/domain.py:117-125` |
| 교착 감지 | 로그 문장만(폐기된 루프) | 없음(벽시계까지 침묵) | `coordinator-dag-convergence.ts:19-28` vs `domain.py:126-139` |
| 멱등성 | receipt + generation + token + settlement 중복 | room JSON `keys`/`client_ids` + worker claim/complete key | §6 vs `talkabout/domain.py:153-171`, `:208-214`, `worker.py:285`, `:432` |
| 관측성 | liveness 3값 + attention + nextAction argv | `delivery_evidence`/`response_evidence`(워커보고/합성/미검증 구분) | `worker-terminal-handlers.ts:150-190` vs `talkabout/core.py:36-54` |

**핵심 차이**: Orca에서 "누가 언제 말하는가"는 **에이전트의 도구 호출**이고, TalkAbout에서 그것은 **서버의 배타적 결정**이다. 그래서 Orca는 메시지 유실·중복·교착을 프로토콜로 방어해야 하고(Task/Dispatch/Delivery/Question이 그 방어층), TalkAbout은 순서·중복을 **단일 room 트랜잭션 + Event seq**로 이미 제거했다. Orca의 프로토콜을 통째로 옮기면 TalkAbout이 이미 더 잘하는 것을 다시 구현하게 된다.

또 하나: Orca는 **handoff(소유권 이전, Run/Task/dispatch를 만들지 않음)** 와 **supervised orchestration**을 문서 수준에서 분리한다(`skill-guides/orchestration.md:33-47`, `docs/site/content/docs/cli/orchestration.mdx:10`). TalkAbout의 `docs/MODE_HANDOVER.md` handover는 membership 이전 + mode reset이므로 **Orca의 "handoff" 쪽**에 가깝다. 이 분류를 TalkAbout 문서에 도입하면 "감독(orchestration)이 필요한 인계"와 "소유권 이전(handover)"의 계약을 섞지 않게 된다. `[차용]` S.

### 10.2 Orca에서 **선별적으로** 빌릴 안전장치

| 안전장치 | Orca 구현 | TalkAbout 필요도 | 판정 | 비용 |
|---|---|---|---|---|
| 중복 억제 | request receipt + generation fence + settlement duplicate(§6) | 이미 대부분 있음. `duplicate` 표시·batch fingerprint 키만 보강 | `[채택]` | S |
| 유한 대기 | ask 30분 상한 + 5초 grace, check `--timeout-ms`, keepalive 15초 | events `wait_seconds ≤ 10`(`core.py:505`)로 이미 유한. **타임아웃 반환에 재개/재시도 지시문**을 붙이는 것만 차용 | `[채택]` | S |
| 교착 감지 | (없음 — 로그만) | **필요**. "N tick 동안 eligible 0" → `mode_status=stalled` + 이벤트 | `[채택]`(독자) | S |
| 관측성 | liveness 3값, attention.categories, nextAction.argv, 빈 대기 3회 규칙 | **필요**. task/attempt에 `evidence`(fresh/stale/absent) + `next_action` | `[차용]` | M |
| 실패 예산 분리 | failure_count(재시도) vs circuit break(3회) vs attempt | **필요**. TalkAbout은 claim attempt가 곧 예산이라 "실행 전 실패"도 소모 | `[채택]` | M |
| 상태 강등(삭제 대신) | lifecycle 거부 메시지를 status로 강등 + 마커 | 유용. TalkAbout은 실패 요청을 그냥 4xx로 버림(감사 흔적 없음) | `[채택]` | S |
| 무한 대기 gate | timeout 미배선 | **불필요** | `[제외]` | — |
| Delivery 테이블 | outstanding/ack/fenced | **불필요**(cursor+journal이 대체) | `[제외]` | — |
| blocking ask | question_threads + resume | **불필요**(LLM 1회 호출 원칙) | `[제외]` | — |
| CLI 3종 런처 해석 | orca/orca-ide/orca-dev | **불필요** | `[제외]` | — |

---

## 11. Q10 — TalkAbout 실제 파일:라인 개선점

전제: TalkAbout은 이미 Orca보다 강한 부분이 많다. 아래는 **실제 코드를 읽고 확인한 구체적 약점**만 적는다.

### 11.1 `talkabout/participation.py` — ack가 "전달 증거"가 아니다

- `ack`는 `read_seq = max(read_seq, seq)`만 올린다(`:74-76`). 검증은 `member['read_start_seq'] - 1 <= seq <= room['last_seq']`(`:73`)뿐이라, **한 번도 전달받지 않은 seq까지 ack할 수 있다**. 그 결과 `notifications()`의 unread 계산(`:88-90`)과 `public_room`의 `delivery_evidence`(`core.py:36-39`)가 "읽었다"는 주장만 반영한다.
- 개선: `events()`가 응답을 만들 때 해당 세션의 `delivered_seq`를 서버에서 갱신하고(현재 `delivered_seq`는 ack에서만 갱신됨, `:74`), ack는 `seq <= delivered_seq`로 제한. → Orca Delivery의 "배달 전에는 읽음이 없다"를 최소 비용으로 이식. `[채택]` S. 부착: `participation.py:61-76` + `core.py:504-569`.
- `notifications()`는 매 호출마다 room 메시지 전체를 스캔한다(`:88-89`). 메시지 상한 200(`domain.py:215`)이라 지금은 문제 없지만, paging 도입 시 unread index가 필요하다(현재 `capacity_paging_unsupported`로 명시 거부, `docs/MODE_HANDOVER.md:27`). `[차용]` M.

### 11.2 `talkabout/modes.py` — free_discussion 큐가 "요청 목록"과 "슬롯"으로 이원화

- `turn_requests` 리스트에서 제거 후 task를 만든다(`:9-17`, `:14-16`). 요청이 grant된 뒤 task가 expire/cancel되면 요청은 이미 소비되어 **agent는 처음부터 다시 요청**해야 한다. MODE_HANDOVER도 "skip/expire도 한 차례를 소비"로 규정한다(`docs/MODE_HANDOVER.md:16`, `:19`).
- 개선: 요청 자체에 `requested_seq`/`deadline`/`state`를 부여해 `turn.requested → turn.granted|expired` 감사 이벤트를 남기고, `room['next_slot']`(단조 증가, `domain.py:143`) 기반 FIFO로 교체. 재스케줄에도 공정성 유지. `[채택]` S. 부착: `modes.py:6-17`, `domain.py:242-253`(turn-requests 처리).
- `next_agent` 회전은 `eligible.index(pid)+1`로 갱신(`modes.py:15`)되는데 `eligible`은 매 호출 `sorted(...)`로 재계산된다(`domain.py:128-129`). 멤버가 추가/제거되면 index 의미가 바뀌어 회전 기준이 흔들린다. 개선: `next_slot % len(eligible)` 같은 슬롯 기반 인덱스로 대체. `[채택]` S.

### 11.3 `talkabout/domain.py` — 실패 예산과 attempt 예산이 하나

- `claim`에서 `attempt += 1`(`core.py:272`)하고, `schedule()`은 `lease` 만료 시 `queued`로 되돌리되 `attempt >= limits.attempts`면 `expired`(`domain.py:120-125`). 즉 **워커가 런타임 호출 전에 죽어도 attempt 1개를 소모**한다. Orca는 `failure_count`(재시도 가능)와 `attempt`(시도 identity)를 분리하고, pre-call 실패는 circuit break 예산에 넣지 않는다(`dispatch-completion.ts:166-167`, `:205-207`; `coordinator-task-dispatch.ts:77`).
- 개선: task에 `failure_count`와 `retryable`을 추가하고, `worker.py:470-472`의 `safe_to_continue_after`(pre-call stale만 안전) 결과를 서버 보고에 포함. 실행 전 실패는 attempt를 돌려주고 failure_count만 올린다. `[채택]` M. 부착: `domain.py:117-125`, `core.py:267-284`, `worker.py:235-272`.
- `schedule()`의 무진행 침묵(`:126-131`, `:132-139`)은 §4.7의 교착이다. 개선: 연속 N회 tick에서 eligible 0이면 `mode_status='stalled'` + `mode.stalled` 이벤트(이유: blocked 전원, lease 반복 만료, 후보 없음). `[채택]` S. 부착: `domain.py:126-139`.
- `message()`는 `client_message_id` 멱등성을 `room['client_ids']`로 이미 처리한다(`:208-214`) `[대조]`. 다만 `reply_to`가 삭제/보존 범위를 벗어나면 404(`:204`)이고, `mentions`는 최대 4명(`:206-207`) — Orca의 그룹 주소 상한과 유사. 개선 불필요.

### 11.4 `talkabout/worker.py` — world-class에 가까운 journal, 두 곳만 보강

- 상태기계가 `ready → claim_pending → claimed → call_started → result_saved → done`이고, `call_started`에서 재시작하면 `uncertain`으로 굳혀 **자동 재실행하지 않는다**(`:262-264`, `:374`, `:417-418`). `result_saved`는 같은 `complete_key`로 재전송 가능(`:431-440`). 이는 Orca의 "positive proof only" 원칙보다 강한 보장이다 `[대조]`.
- 약점 ①: `request_turns`의 멱등 키가 `f'...:{room["last_seq"]}'`(`:213-215`)라서, 요청이 적용된 뒤 다른 이벤트가 하나라도 생기면 재시도 키가 달라진다. 실제 중복은 서버의 `turn_already_pending`(`core.py:247`)이 막아주지만, 키 자체가 방어하지 못한다. 개선: `claim_key`처럼 journal에 보관한 uuid 기반 키 사용(`:285` 참조). `[채택]` S.
- 약점 ②: `_beat`는 stage를 best-effort로 보내고 실패를 삼킨다(`:459-468`). 서버는 이 stage를 `task_events`에 24개까지만 남긴다(`domain.py:182-194`). 관측성은 충분하지만 **사람이 "지금 무엇을 기다리는지"** 를 알 길이 없다(`tool_blocked`와 `runtime_timeout`의 구분만 존재, `:421`, `:412-418`). 개선: Orca의 `attention`/`nextAction`을 참고해 attempt에 `waiting_on ∈ {human, tool, provider, none}` + `next_action`을 추가. `[차용]` M.
- `poll()`의 cursor gap 복구(`:220-233`)와 snapshot rebase는 Orca의 delivery replay보다 단순하면서 견고하다 `[대조]`.

### 11.5 `talkabout/core.py` — 관측성은 우수, "다음 행동"이 없음

- `public_room`이 `delivery_evidence`(delivered_seq/read_seq)와 `response_evidence`(worker_reported/synthetic/unverified)를 구분해 노출한다(`:36-54`). 이는 Orca의 `synthetic_test_only` 구분(`talkabout/core.py:304`의 `execution_evidence`)과 같은 사상이며 **TalkAbout이 더 정직하다** `[대조]`.
- 약점: task마다 `state/deadline/attempt`는 있지만 **"지금 누가 무엇을 해야 하는가"** 가 없다. Orca는 `nextAction.argv`를 기계 판독 가능한 형태로 준다(`worker-terminal-handlers.ts:150-190`). 개선: `public_room.tasks[]`에 `next_action`(예: `retry`, `skip`, `await_claim`, `human_resume`)과 `attention`(예: `overdue_lease`, `no_eligible`, `worker_stale`) 추가. `[차용]` M.
- `_worker_ready`가 boolean이라 "연결 안 됨 / 증거 만료 / 검증 실패"를 구분하지 못한다(`:96-110`). start 거부에는 충분하지만 UI·감사에는 손실이다. 개선: 3값 evidence(`fresh|stale|absent`) 반환 후 호출부에서 boolean 파생. `[채택]` S.
- controls 승인 흐름에서 `change_mode/change_topic`은 `mode_progress`를 초기화하고 `generation`을 올린다(`:419-426`). 좋은 규율이나, **handover 수락**도 같은 리셋을 하면서 `cancel_tasks`만 호출한다(`:186-193`). attempt는 남은 채 task만 취소되므로, 취소된 attempt의 늦은 `complete`가 `stale_attempt`로 거절되는지는 fencing에 의존한다(`domain.py:174-179` — generation 불일치로 거절 ✓). 테스트로 고정할 가치가 있다. `[채택]` S.

### 11.6 `docs/MODE_HANDOVER.md` / `docs/ARCHITECTURE.md` — 문서 드리프트

- MODE_HANDOVER는 3행에 "LOCAL PROTOTYPE / NOT DEPLOYED — Reviewed: 2026-10-13"이라 쓰지만, 코드에는 이미 handover acceptance·mode reset·capacity 오류가 구현돼 있다(`core.py:167-194`, `:70-88`). ARCHITECTURE도 5행에 "PROPOSED / NOT IMPLEMENTED"라고 못박는다. 정본 문서와 구현의 상태 표기가 충돌한다.
- Orca의 해법은 **스킬 문서를 stub으로 두고 바이너리가 버전 일치 가이드를 서빙**하는 것이다: "kept out of this file on purpose so it can never drift from the binary that will actually run your commands"(`skills/orchestration/SKILL.md:15-17`), 그리고 문서도 "Experimental", "Legacy commands retired" 콜아웃으로 상태를 코드와 함께 표시한다(`docs/site/content/docs/cli/orchestration.mdx:12-21`).
- 개선(2개): ① MODE_HANDOVER/ARCHITECTURE의 상태 배지를 **capability matrix**(구현/테스트/실공급자 별도 열)로 대체하고 `docs/PLAN_IMPLEMENTATION_EVIDENCE.md`와 상호 링크. ② `handover`(소유권 이전)와 `orchestration`(감독)의 용어를 분리(§10.1). `[차용]` S.

### 11.7 Q10 요약 표

| # | 개선 | 판정 | 비용 | 부착(파일:라인) |
|---|---|---|---|---|
| 1 | ack ≤ 서버 delivered_seq (ack 위조/건너뛰기 차단) | `[채택]` | S | `talkabout/participation.py:61-76` + `talkabout/core.py:504-569` |
| 2 | 요청-슬롯 상태화(`turn.requested/granted/expired`) + 슬롯 기반 회전 | `[채택]` | S | `talkabout/modes.py:6-17`, `talkabout/domain.py:242-253` |
| 3 | 실패 예산(`failure_count`) 분리 + pre-call 실패 attempt 반환 | `[채택]` | M | `talkabout/domain.py:117-125`, `talkabout/core.py:267-284`, `talkabout/worker.py:235-272` |
| 4 | stall 감지(`mode_status=stalled` + 이벤트) | `[채택]` | S | `talkabout/domain.py:126-139` |
| 5 | task별 `attention`/`next_action` 투영 | `[차용]` | M | `talkabout/core.py:15-67` |
| 6 | `_worker_ready` 3값 evidence | `[채택]` | S | `talkabout/core.py:96-110` |
| 7 | `request_turns` 멱등키 uuid화 | `[채택]` | S | `talkabout/worker.py:213-215` |
| 8 | 실패 요청 감사 흔적(4xx에도 이유·요청키 기록) | `[차용]` | S | `talkabout/api.py`, `talkabout/store.py` |
| 9 | 문서 상태 배지 → capability matrix + handover/orchestration 용어 분리 | `[차용]` | S | `docs/MODE_HANDOVER.md:3`, `docs/ARCHITECTURE.md:5` |
| 10 | worker↔server capability handshake(`incompatible_runtime` 등가) | `[채택]` | M | `talkabout/api.py`, `talkabout/worker.py:204` |

---

## 12. Orca 자체의 약점 (그대로 베끼면 안 되는 것)

1. **자동 재디스패치 없음**: 실패한 task는 `ready`로 돌아가지만 자동 배치 루프가 폐기됐다(`dispatch-handlers.ts:71-85`). ready task가 방치될 수 있다.
2. **watchdog 경고 전용 + 폐기 루프 소속**: `warnStaleDispatches`/`reblockTasksWithPendingGates`가 `Coordinator.tick`에만 있다(`coordinator.ts:159-166`).
3. **gate timeout 미배선**: `timeoutGate` 호출자가 테스트뿐(`decision-gate-store.ts:130-140`) → 승인 대기가 영구 교착 가능.
4. **교착 감지 없음**: 로그 문장 하나(`coordinator-dag-convergence.ts:19-28`).
5. **변이 멱등성 비대칭**: `--retry-request`를 명시하지 않은 일반 변이는 응답 유실 시 재실행 가능(§6 한계).
6. **문서가 코드보다 낙관적/보수적 혼재**: 스킬 stub은 정직하지만 `docs/site` 문서와 코드 capability 사이에 여전히 "Experimental" 표기 의존(`orchestration.mdx:12-15`).

---

## 13. Top 5 실행 제안

| 순위 | 제안 | 판정 | 비용 | 부착 위치 | 기대 효과 |
|---|---|---|---|---|---|
| 1 | **ack 증거 사슬 확립**: `events()`가 `delivered_seq`를 서버에서 갱신, ack는 그 이하만 허용, `delivery_evidence`를 실제 배달 증거로 승격 | `[채택]` | S | `talkabout/participation.py:61-76`, `talkabout/core.py:504-569` | "읽었다"는 주장과 실제 배달 분리. Orca Delivery의 핵심 가치만 이식 |
| 2 | **실패 예산과 attempt 예산 분리**: `failure_count` + pre-call 실패 attempt 반환 + (선택) 3회 상한 | `[채택]` | M | `talkabout/domain.py:117-125`, `talkabout/core.py:267-284`, `talkabout/worker.py:235-272` | 재시도 가능한 실패가 기회를 태우지 않음. Orca circuit breaker의 실질 이득 |
| 3 | **stall 감지**: 연속 N tick eligible 0 → `mode_status=stalled` + `mode.stalled` 이벤트 + interventions 노출 | `[채택]` | S | `talkabout/domain.py:126-139`, `talkabout/modes.py:6-41` | 무음 정지 제거. Orca에도 없는 기능(독자 구현) |
| 4 | **task 관측 투영**: `attention` + `next_action` + 3값 worker evidence | `[차용]` | M | `talkabout/core.py:15-67`, `:96-110` | 사람이 "다음 행동"을 즉시 판단. Orca `worker-list` projection 대응 |
| 5 | **문서 정합**: 상태 배지 → capability matrix, `handover`(소유권) vs `orchestration`(감독) 용어 분리, 모드 큐 상태 감사 | `[차용]` | S | `docs/MODE_HANDOVER.md:3`, `docs/ARCHITECTURE.md:5`, `talkabout/modes.py:6-17` | 문서 드리프트 제거 + 이후 모든 제안의 계약 기반 확보 |

권장 순서: **5 → 1 → 3 → 2 → 4** (문서·계약을 먼저 고정하면 1·3의 상태 이름이 재작업 없이 들어간다).

---

## 14. 미확인 / 한계

- `src/main/runtime/orchestration` 하위 약 200파일 중 federation(relay·remote attachment·ack checkpoint)과 structured-worker(session/journal) 계층은 **존재와 역할만 확인**했고 내부 전이를 전부 읽지 않았다. 본 보고서의 원격 워커 관련 서술은 §2.2·§8.1의 공개 경로(worker-list/attention, consumer_generation, ask-remote)로 한정한다.
- `src/main/hang-watchdog/*`는 Electron 메인 스레드 행 감지로 보이며(파일명·테스트명 기준) **오케스트레이션 워커 watchdog과 무관**하다고 판단했으나 내부 구현은 읽지 않았다. "워커 hang 감지기"로 인용하지 않았다.
- TalkAbout의 `api.py`·`store.py`·`identity.py`는 함수 시그니처·호출 지점만 확인했고 전수 읽지 않았다. §11의 부착 위치는 **존재가 확인된 파일**로만 제시했다.
- 코드 복사는 하지 않았다. MIT 고지 대상 없음.
