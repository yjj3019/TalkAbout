# 00 — Orca 정밀 분석 종합 판정 (Lead 통합)

- 분석 대상: `https://github.com/stablyai/orca.git` — clone `orca-analysis/`, HEAD `51dcef8` (2026-10-25), MIT
- 분석 기준: `TalkAbout` (`D:\AI-Codding\Claude\TalkAbout`, Python + PostgreSQL, 정본 `docs/ARCHITECTURE.md`·`docs/STATUS.md`·`docs/RELEASE_GAP_MATRIX.md`)
- 방법: 5개 분석 트랙 병렬 수행 → 각 453~689줄 보고서 → Lead가 인용 주장 표본 재검증 후 통합·교정
- 상세 보고서: [01-providers](01-providers.md) · [02-orchestration](02-orchestration.md) · [03-runtime](03-runtime.md) · [04-security-skills](04-security-skills.md) · [05-product-ux](05-product-ux.md) (합계 3,040줄)

---

## 0. 결론

**Orca는 TalkAbout의 경쟁자가 아니라, TalkAbout이 직접 구현해야만 했던 "외부 에이전트 CLI 감독 계층"의 3년치 노하우를 MIT로 공개한 참조 구현이다.**

가져올 가치는 세 종류로 갈린다.

| 종류 | 규모 | 판정 |
| --- | --- | --- |
| **A. TalkAbout의 미해결 관문에 직결되는 것** | 6건 | `[채택]` — Grok ACP 관문, S2 pause 복구, 완료 통지, MCP S4 |
| **B. TalkAbout에 없는 안전장치** | 12건 | `[채택]`/`[차용]` — 대부분 비용 S |
| **C. Orca가 잘하지만 TalkAbout이 이미 더 잘하는 것** | 9건 | `[대조]` — **복사하면 후퇴** |

그리고 분석 과정에서 **TalkAbout의 실제 결함 6건**을 찾았다(모두 파일:라인 확인). 이게 이번 분석의 가장 실질적인 산출물이다.

**하지 말아야 할 것 하나**: Orca를 "보안 상위호환"으로 보고 자격증명·프롬프트 처리 코드를 그대로 옮기는 것. Orca는 **모델로 가는 프롬프트를 마스킹하지 않는다**(`src/main/observability/redactor.ts:1-2`의 적용 지점은 텔레메트리 sink 4곳뿐). TalkAbout의 `agent_runtime.py:35-97`(stderr 고정 라벨만 노출) 쪽이 더 엄격하다.

---

## 1. Orca는 무엇인가 (실측)

| 항목 | 값 | 비고 |
| --- | --- | --- |
| 저장소 | 296MB, `src/` 31,949 파일 (`.ts` 31,889 / `.tsx` 4,103) | `.md` 152, `.json` 1,163 |
| 형태 | Electron 데스크톱 + `orca` CLI + 헤드리스 `orcad` + 클라우드(`cloud/`) + 모바일(`mobile/`) + Swift/Kotlin (`native/`) | |
| 핵심 가치 | 프롬프트 1개를 N개 에이전트에 병렬 분배, 각자 git worktree에서 실행, 결과 비교·머지 | README |
| 지원 TUI 에이전트 | **45종** (`src/shared/tui-agent.ts:1-46`) | codex, claude, opencode, gemini, cursor, grok, droid, qwen-code, kimi, **dsh**, jcode … |
| ACP 구현 | `src/main/acp/` **144파일** (비테스트 70) | JSON-RPC peer, 세션 수명, resume, 권한 요청, 모델 카탈로그 |
| 오케스트레이션 | `src/main/runtime/orchestration/` **338파일** (비테스트) + `src/cli/handlers/orchestration/` 25파일 | Run/Task/Dispatch/Delivery/Question/Gate를 SQLite 상태기계로 |
| 테스트 | 단위 13,424 / E2E 598 / GitHub Actions 68 워크플로 | `AGENTS.md`·`docs/reference/test-antipatterns.md` |

### 1.1 TalkAbout과의 관계 — 같은 재료, 다른 목표

| 축 | Orca | TalkAbout |
| --- | --- | --- |
| 배포 형태 | 데스크톱 앱(로컬 신뢰 경계) | 서버(초대 기반 사적 공간, 권한 카탈로그 강제) |
| 에이전트 실행 주체 | 앱이 PTY로 직접 띄운다 | **참가자가 자기 컴퓨터에서** 자기 구독으로 돌린다 |
| 에이전트 발견 방식 | 터미널 제목·pane key·hook으로 **추론** | JSONL/ACP stdout으로 **명시 파싱** |
| 스케줄링 | 에이전트 주도(자율) | **서버가 round-robin 슬롯을 강제** |
| 대화 목적 | 코드 작업 병렬화 | 사람 소유자 통제 아래 토론·공동 집필 |
| 내구성 축 | PTY 스크롤백 + daemon 생존 | **pull worker journal + Event 순서** |

이 차이가 판정의 기준이다. Orca는 "에이전트를 띄우고 상태를 알아내는" 문제에, TalkAbout은 "여러 주체의 발언 순서와 권한을 증명하는" 문제에 각각 최적화되어 있다. **겹치는 지점은 정확히 TalkAbout의 약한 지점이다.**

---

## 2. 겹침 지도 — 어디가 실제로 유용한가

| 영역 | Orca 근거 | TalkAbout 대응 | 판정 |
| --- | --- | --- | --- |
| ACP 클라이언트 | `src/main/acp/` (144파일) | `agent_runtime.py:_grok`, Grok `gated` | **`[채택]` 최우선** |
| 제공자 공통 추상화 | `structured-agent-registry.ts:26-47` | `providers.py`(메타데이터만) | `[채택]` |
| 세션 resume | `agent-session-resume.ts:200-290` | `codex_sessions.py`(codex 전용) | `[채택]` |
| 이벤트 파싱 정책 | `provider-frame-disposition.ts:6-236` | `agent_runtime.py` 파서 3종 | `[채택]` |
| usage/rate-limit 감지 | `claude-usage-limits.ts:52-135`, `codex-usage-limits.ts:31-114` | `rate_limit_event`를 **버림** | **`[채택]` 최저비용** |
| MCP 설정 취급 | `mcp-config.ts:45-70` + 거부 규칙 | `mcp.py`(서버 노출만, S4 미연결) | **`[채택]`** |
| secrets 마스킹 | `observability/redactor.ts` | 이미 더 엄격 | `[차용]` 순서 설계만 |
| 스킬 주입 | `skills/`+`skill-guides/`+`SKILL.md` 스텁 | 프롬프트 주입 + 로컬 스킬 차단 | `[차용]` 정본 서빙만 |
| 알림 규약 | `notification-burst-cooldown.ts:1` | 웹 알림 없음 | `[채택]` |
| stall/hang 감지 | `hang-detection-marker.ts:12-33` | 없음(무음 정지) | `[채택]` |
| 관측/로그 | `observability/`(span·NDJSON·redactor) | `logging` import **0건** | `[채택]` |
| 프로세스 격리 | `process-host/`, `posix-pty-process-groups` | Windows만(Job Object) | `[차용]` L비용 |
| PTY 스크롤백 영속 | `terminal-history/<id>/` | 대화 세션 개념 없음(1회 호출) | `[대조]` |
| 재부착·상주 daemon | `daemon-*` | worker 일회 호출 | `[대조]` |
| 원격 실행(ssh/wsl) | `IPtyProvider` 단일 계약 | 로컬 CLI만 | `[제외]` |
| worktree·git·PR·Linear/Jira | 다수 | 무관 | `[제외]` |
| Electron 렌더러 UI | `src/renderer/` | 자체 CSS 웹 | `[제외]` |

---

## 3. Tier 1 — 지금 바로 (비용 S, 효과 큼)

우선순위는 **"TalkAbout의 미해결 관문을 직접 푸는가"**로 정했다.

| # | 항목 | 근거(Orca) | TalkAbout 부착 위치 | 출처 |
| --- | --- | --- | --- | --- |
| 1 | **ACP 프레임 규율**: 응답 id는 숫자만, 불일치 id는 "수용"도 "즉시 실패"도 아니라 **무시 후 유한 타임아웃**, 초과 라인은 개별 정산, 쓰기 큐 바이트 상한 | `acp-json-rpc-peer.ts:297-313`, `acp-rpc-envelope.ts:4-41`, `acp-oversized-lines.ts` | `agent_runtime.py:_grok` | Lead |
| 2 | **실패·한도 어휘 타입화**: `usage_limit_reached{window,resetsAt}` / `not_signed_in` / `session_not_restored` — 이미 받는 `rate_limit_event`를 버리지 않기 | `agent-session-failure.ts:25-80` | `agent_runtime.py:949,792-793` | 01 |
| 3 | **journal 락 오류를 stop으로 승격 금지** + `busy_timeout` | `sqlite-read-failure.ts:1`(3분류) | `worker.py:391-400`, `:172-175` | 03 |
| 4 | **stop 경로에 shutdown 시간 상한**(5초 후 강제 exit) | `daemon-shutdown-deadline.ts:3` | `runtime.py:51-59` | 03 |
| 5 | **ack 증거 사슬**: 서버가 `delivered_seq`를 갱신, ack은 그 이하만 허용 | Delivery `outstanding→acknowledged` | `participation.py:61-76`, `core.py:504-569` | 02 |
| 6 | **stall 감지**: 연속 N tick eligible 0 → `mode_stalled` + 이벤트 (Orca에도 없음 → 독자 구현) | `coordinator-task-dispatch.ts:15-28`의 "경고 전용" 철학 | `domain.py:126-139` | 02 |
| 7 | **MCP 비파괴 설치 도우미**: `--dry-run` plan digest → 확인 후에만 병합, **읽을 수 없는 파일은 절대 덮어쓰지 않음** | `mcp-config.ts:45-70`, `mcp-config-inspection.ts:90-98`※ | `mcp.py`, `cli.py` | 04 |
| 8 | **완료 통지**: 5초 burst 쿨다운 + 포커스 시 억제 + unread를 배너보다 먼저 기록 | `notification-burst-cooldown.ts:1`, `notification-delivery-service.ts:134-142` | `web/app.mjs`, `web/state.mjs` | 05 |
| 9 | **reduced-motion 버그 수리 + CSS 검사 테스트** | `oxlint-design-system.json:26-43`(린트로 강제) | `web/style.css:1692-1709` | 05 |
| 10 | **`AGENTS.md` 저장소 규약 + `test-antipatterns.md` 차용** | `AGENTS.md`(69줄), `docs/reference/test-antipatterns.md`(25줄) | 루트 | 05 |
| 11 | **capability 선언 ↔ 구현 존재 검증**: 선언만 하고 메서드가 없으면 **생성 시점 throw** | `structured-agent-registry.ts:36-43` | `providers.py` | 01 |
| 12 | **자식 env를 호출 지점이 아니라 구조로 강제**: 계정 변수는 값 이상 시 drop이 아니라 **거부** | `agent-launch-account-env.ts:35-52` | `adapters.py:19-27`, `agent_runtime.py:106-115` | 01 |
| 13 | **DSH를 제공자로 추가**: 45종 에이전트 중 `dsh` 정식 지원 — `dsh-tui` 프로필 판별 + 🐋 제목으로 ready 판정 + Claude형 hook 브리지 | `tui-agent.ts:44`, `dsh-launch-command.ts:58-93`, `dsh-terminal-title.ts:1-6`, `dsh-events.ts` | `providers.py`(신규 transport) | Lead |

※ 경로 정정: 실제 파일은 `src/renderer/src/components/settings/mcp-config-inspection.ts` (04 보고서가 `src/shared/`로 적음). 인용 라인의 내용은 검증됨.

**권장 순서**: 3 → 4 → 2 → 1 → 5 → 6 → 8 → 9 → 7 → 11 → 12 → 13

---

## 4. Tier 2 — 계획 작업 (비용 M)

| # | 항목 | 근거(Orca) | TalkAbout 위치 |
| --- | --- | --- | --- |
| 1 | **secrets redaction 단일 choke point** — label→provider→userinfo→env 순서 고정, idempotent. **주의: artifact 불변 해시보다 먼저** 마스킹해야 한다(TalkAbout 고유 제약, Orca에 없음) | `observability/redactor.ts:6-35,72-112` | 신규 `secrets_redaction.py` → `agent_runtime.py:1088`, `http_runtime.py:155`, `mcp.py:131`, `attachments.py:171` |
| 2 | **`provider_session` 일반화 + Claude 세션 ID 영속** (현재 codex 전용 + 검증 계정 게이트로 재개 영구 차단) | `agent-session-resume.ts:200-290`, `agent-session-account-home.ts:5-11` | `codex_sessions.py`, `agent_runtime.py:416,900,947` |
| 3 | **실패 예산과 attempt 예산 분리** — 실행 전 실패가 기회를 태우지 않게 | `failure_count` / `retry_of_dispatch_id` | `domain.py:117-125`, `core.py:267-284` |
| 4 | **worker 재시작 감독 + crash-loop 상한**(5회/60초 슬라이딩 윈도) | `daemon-respawn-throttle.ts:13-14` | `connector.py`, `cli.py`, `docs/REFERENCE_WORKER.md` |
| 5 | **turn 단위 상관 ID + JSON-line 로그 + crash flush** | `daemon-file-log.ts:1,74-86`, `durable-crash-breadcrumb.ts:36` | `worker.py`, `runtime.py`, `api.py`, `docs/ARCHITECTURE.md` |
| 6 | **권한 카탈로그를 실행 가능한 표로 승격** — 표에 없는 권한 이름은 실패 | `plugin-capabilities.ts:6-33`, `plugin-capability-gate.ts:24-51` | 신규 `permissions.py` ← `docs/PERMISSIONS.md` |
| 7 | **disposition 표 + blocking request 응답표** (미지 프레임이 (a) 폴백 (b) 안전 무시 (c) 위반 중 어디인지 명시) | `provider-frame-disposition.ts:6-236`, `codex-server-request-disposition.ts:28-77` | `agent_runtime.py` 파서 3종 |
| 8 | **plan digest CAS** — dry-run 확인본과 다르면 아무것도 쓰지 않음 | `execute.ts:214-235` | `cli.py` mutate 경로 |
| 9 | **instructional-content 동의 지문 + 매 읽기 재검증** — 방 규칙/역할 프롬프트 위변조 방지 | `plugin-consent-fingerprint.ts:19-31`, `plugin-instructional-content-integrity.ts:6-27` | `modes.py`, `provision.py` |
| 10 | **MCP `instructions`를 포인터로 축소 + `guide_get` 정본 서빙** | `skills/orchestration/SKILL.md:15-17`, `skill-guide-get.ts:72-106` | `mcp.py:69-70` |
| 11 | **i18n 표 통합** — 현재 `Object.assign` 20블록/39참조 → 3번째 언어 비용 폭발 | 7언어 + 누락 감지 3중 게이트 | `web/state.mjs`, `web/app.mjs:11` |
| 12 | **요청량/예산 상시 표시**(statusLine 피드로 429 회피) | `RateLimitWindow`/`ProviderRateLimits` 계약 | `web/`, `participation.py` |

---

## 5. Tier 3 — 전략 (비용 L, 별도 트랙)

| # | 항목 | 왜 큰가 | 선행 조건 |
| --- | --- | --- | --- |
| 1 | **POSIX 프로세스 격리** | TalkAbout은 POSIX를 **의도적으로 전면 차단**(`processes.py:111-112`, `cli.py:203`). 참가자 절반(macOS/Linux)이 참여 불가 | `posix-pty-process-groups`류 pgid/세션 그룹 발견 + `exited/live/unverifiable` 3값 verdict를 Python으로 이식 |
| 2 | **상주 감독자(daemon) 패턴 검토** | 앱/worker 재시작 시 진행 중 turn 생존. 단 TalkAbout은 LLM이 cursor·heartbeat를 만지지 않는 pull 모델이므로 **부분 차용만** | Tier 2 #4 선행 |
| 3 | **ACP 전면 채택**(Grok 외) | ACP는 TalkAbout의 `cli` transport보다 구조화된 계약. 144파일 구현이 참조 가능 | Tier 1 #1 선행, 실제 공급자 예산 승인 |

---

## 6. TalkAbout이 이미 우위 — 복사하면 후퇴하는 목록 `[대조]`

| 영역 | TalkAbout 근거 | Orca와 비교 |
| --- | --- | --- |
| **Event 순서·revision 다층** | `docs/ARCHITECTURE.md:20-46`, per-room `last_event_seq` + room row lock | Orca에 등가물 없음. per-room vector offset·tombstone 재가입 모델이 더 엄격 |
| **pull worker journal 내구성** | `worker.py:235-457` — `call_started→uncertain` 자동 재시도 금지 | Orca는 자동 재디스패치 폐기 + watchdog 경고 전용 → TalkAbout이 강함 |
| **stderr 증거 최소화** | `agent_runtime.py:35-97` — 고정 라벨만 통과, 경로·URL·메시지 차단 | Orca에 대응물 없음 |
| **자식 env allowlist** | `agent_runtime.py:106-115` — 해당 제공자 키만 | Orca는 계정 home 중심. 교차 키 차단은 TalkAbout이 명시적 |
| **자식 프로세스 격리(Windows)** | `processes.py:105-140` — Job Object + KILL_ON_JOB_CLOSE + no-breakaway + CREATE_SUSPENDED | Orca 수준 이상 |
| **tool 정책 fail-closed** | `tool_broker.py:97,232-239` — fake adapter 강제, write 실패 intent 격리 | Orca 플러그인 모델보다 좁지만 증거상 더 안전 |
| **합성↔실제 증거 분리** | `docs/RELEASE_GAP_MATRIX.md:9` — "실제 공급자로 표시 금지" | Orca 문서에 등가 규율 없음 |
| **ko/en parity 테스트** | `ui-state.test.mjs:8` | Orca는 7언어지만 TalkAbout parity 테스트가 더 직접적 |
| **재접속 cursor + snapshot rebase** | `worker.py:220-233` | Orca 원격은 재구독 방식 |

**교훈**: Orca 코드를 "상위호환"으로 가정하고 이식하면 대부분 후퇴한다. 이식 대상은 **TalkAbout에 없는 것**으로 한정해야 한다(§3~§5).

---

## 7. 부수 성과 — 검증된 TalkAbout 결함 6건

분석 중 대조 과정에서 발견. **전부 실제 파일:라인 확인**. Orca 이식과 별개로 즉시 수리 가치가 있다.

| # | 결함 | 근거 | 영향 | 비용 |
| --- | --- | --- | --- | --- |
| 1 | **journal 락 오류를 stop 요청으로 승격** — `except sqlite3.Error: requested = True` → 진행 중 turn이 `cancelled`로 커밋됨 | `worker.py:391-400` + `:172-175`(20ms마다 신규 연결, busy_timeout 없음) | 정확성. 일시적 락 경합이 turn을 오취소 | S |
| 2 | **타임아웃 탈출로가 무제한** — `runtime.shutdown()`을 상한 없이 호출해 4중 타임아웃이 무력화 | `runtime.py:51-59`, `worker.py:407-409` | worker가 영구 대기 가능 | S |
| 3 | **ack이 배달 증거가 아님** — 클라이언트가 준 cursor를 그대로 `read_seq`로 인정 | `participation.py:73-76` | "읽었다"는 주장과 실제 배달 미분리 | S |
| 4 | **reduced-motion 블록 오염** — 미디어 쿼리 안에 `.stage-caption` 규칙이 통째로 들어가 `body::before`가 pill로 변하고 `aurora`가 안 멈춤 | `web/style.css:1692-1709` vs `:897-910`, `:130` | 접근성. CSS 검사 테스트 부재 | S |
| 5 | **실패가 attempt 예산을 태움** — 실행 전 실패도 attempt 소모 | `domain.py:117-125`, `core.py:272` | 재시도 가능 실패가 기회 상실 | M |
| 6 | **관측 계층 부재** — `logging` import 0건, 상관 ID 없음 | `talkabout/*.py` 전수 grep | 운영 배포 시 진단 불가 | M |

**미확인 2건**(참고): `worker.py:392-394` 초당 50회 신규 SQLite 연결(성능, 결함 1의 확률을 높임) · `identity.py:153` operation quota 무한 누적.

---

## 8. 팀 보고서에서 정정한 주장 4건

Lead가 원문을 다시 읽고 톤을 낮춘 항목. **인용 좌표는 유효하지만 해석이 과했다.**

| # | 원 보고 | 재검증 결과 |
| --- | --- | --- |
| 1 | "`adapters.py:21-27`이 Codex에 `ANTHROPIC_API_KEY`, Claude에 `OPENAI_API_KEY`를 넘긴다 = **교차 자격증명 누출**" | **활성 누출 아님.** `child_environment`를 쓰는 `execute_bounded`의 두 호출 지점 모두 상대 키를 명시 차단한다(`agent_runtime.py:482-483,883-885`). 광범위 allowlist에 도달하는 `CLIRuntime`은 테스트·픽스처 전용(`test_cli_adapters.py`, `runtime_process_fixture.py`). 실제 문제는 "**호출 지점이 기억해야 안전**"이라는 구조적 취약성 → Tier 1 #12로 재분류 |
| 2 | "`app.mjs:671`이 `document.hidden`이면 폴링 정지 → **백그라운드 탭에서 완료를 영원히 모름**" | **과장.** 열린 방은 SSE를 쓴다(`web/app.mjs:740` `EventSource('/api/v1/rooms/'+id+'/stream')`). 실제 공백은 (a) 완료 **통지 수단 자체가 없음**, (b) 방 목록 폴링만 hidden 시 정지 → Tier 1 #8로 재분류 |
| 3 | "`src/main/runtime/orchestration/` 약 200파일" | 실측 **338파일**(비테스트). 규모 논지에는 영향 없음 |
| 4 | "`src/shared/mcp-config-inspection.ts:90-98`" | 파일은 `src/renderer/src/components/settings/mcp-config-inspection.ts`. **:90-98의 거부 규칙 내용은 정확히 일치** |

이 외에 5개 보고서 모두 "미확인" 절을 두고 grep 기반 부재 주장을 "찾지 못했다"로 한정했다(예: `codex exec --json` 파서는 이 HEAD에 없고 app-server가 정본).

---

## 9. Orca에서 배울 게 없는 것 `[제외]`

| 항목 | 이유 |
| --- | --- |
| PTY 스크롤백 영속·재부착·상주 daemon | TalkAbout은 대화 세션을 유지하지 않는다(턴 1회 호출). **문제 정의가 다름** |
| 원격 실행(ssh/wsl/container/VM) 추상화 | TalkAbout의 원격은 "참가자 자기 컴퓨터"이며 서버가 대행하지 않는다 |
| Electron 렌더러·WebGL 터미널·shadcn 디자인 시스템 | 스택 불일치(자체 CSS 원칙) |
| worktree·git·PR·Linear/Jira·브라우저·컴퓨터 유즈 | 도메인 무관 |
| blocking ask/reply(질문-대기-응답) | 서버 강제 슬롯 + 사람 질의 모델이 이미 상위. 도입 시 교착 위험만 추가 |
| `src/main/memory/` | 에이전트 기억이 아니라 **프로세스 RAM 통계**(`collector.ts:1-37`) |
| 스킬 **파일 배포** | TalkAbout은 로컬 스킬 발견을 의도적으로 차단(`agent_runtime.py:563`). "스텁+정본 서빙"만 차용 |
| 플러그인 신뢰 모델 전체 | 데스크톱 로컬 신뢰 경계 전제 → 서버 모델에 부적합 |

---

## 10. 라이선스·주의

1. Orca는 **MIT**(`LICENSE`). 코드를 실제 복사하면 저작자 표시·라이선스 전문 고지 필요. 이번 보고서들은 **개념·구조 참조**이며 코드 복사는 하지 않았다.
2. **Orca를 보안 모범으로 삼지 말 것.** 프롬프트 마스킹 없음, MCP 설정 쓰기 없음, 워커 hang 감지는 Electron 메인스레드 전용.
3. **Orca를 오케스트레이션 모범으로도 삼지 말 것.** 교착 감지기 부재(gate timeout 미배선), 자동 재디스패치 폐기, watchdog 경고 전용 — 이 영역은 TalkAbout의 journal 모델이 더 성숙하다.
4. 경로 규칙: `orca-analysis/`는 Orca 루트 기준, `talkabout/*.py`는 TalkAbout 루트 기준.

---

## 11. 실행 계획 제안 (1주)

| 일차 | 작업 | 산출 |
| --- | --- | --- |
| D1 | 결함 #1·#2 수리 + 회귀 3건 (journal 락 3분류, shutdown 5초 상한) | `worker.py`, `runtime.py`, `tests/` |
| D2 | 결함 #4·#3 수리 (CSS 검사 테스트, ack 증거 사슬) | `style.css`, `participation.py` |
| D3 | Tier 1 #2·#1 (실패 어휘 타입화 → Grok ACP 프레임 규율) | `agent_runtime.py` |
| D4 | Tier 1 #6·#8 (stall 감지, 완료 통지) | `domain.py`, `web/` |
| D5 | Tier 1 #10·#11·#12 (AGENTS.md, capability 검증, env 구조 강제) | 루트, `providers.py`, `adapters.py` |
| D6 | Tier 1 #7 (MCP 비파괴 설치 `--dry-run`) | `mcp.py`, `cli.py` |
| D7 | 회귀 전체 + `docs/STATUS.md` 갱신(수리 항목만. 실제 공급자 시험은 별도 승인) | 검증 기록 |

**POSIX 격리(Tier 3 #1)는 별도 트랙**으로 분리할 것 — 이번 주에 섞으면 fail-closed 원칙이 흐려진다.

---

## 12. 한 문장 요약

> Orca에서 가져올 것은 **"에이전트 CLI를 어떻게 안전하게 띄우고, 그 실패를 어떻게 타입화하며, 그 상태를 어떻게 관측하는가"** 이고,
> 가져오지 말 것은 **"세션을 어떻게 오래 살리는가"와 "권한을 어떻게 로컬에서 신뢰하는가"** 이다.
> 그리고 그 대조 과정에서 TalkAbout의 실제 결함 6건이 나왔다 — 이번 분석의 최대 실익은 아마 이쪽이다.
