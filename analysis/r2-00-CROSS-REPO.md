# r2-00 — 3개 저장소 정밀 분석 종합 (Orca · opencodex · hermes-agent)

- 분석일: 2026-10-25
- 대상: `orca-analysis/`(HEAD `51dcef8`) · `opencodex/`(HEAD `9f0d40a`, v2.92.0) · `hermes-agent/`(HEAD `930e5ed`)
- 기준 프로젝트: `TalkAbout` (Python + PostgreSQL, 정본 `docs/ARCHITECTURE.md`·`docs/STATUS.md`·`docs/RELEASE_GAP_MATRIX.md`)
- 1라운드(Orca 단독) 결과: `00-SYNTHESIS.md` + `01~05`. 이 문서는 **3개 저장소를 비교해 "어디서 무엇을 가져올지"** 를 확정한다.
- 2라운드 상세 보고서 8편: `r2-hermes-acp-providers` · `r2-hermes-state-ops` · `r2-hermes-tools-skills` · `r2-hermes-runtime-posix` · `r2-hermes-gateway-ops` · `r2-opencodex-cli-wire` · `r2-opencodex-arch` · `r2-adjudication` (합계 4,563줄)

---

## 0. 결론

**세 저장소는 경쟁 관계가 아니라 서로 다른 층이다. TalkAbout에 가장 가까운 것은 hermes-agent다.**

| 저장소 | 층 | TalkAbout과의 관계 |
| --- | --- | --- |
| **hermes-agent** | **에이전트 런타임** (Python 3.14, MIT) | **같은 스택**. ACP 서버·provider·SQLite 상태계층·도구 승인·관측을 실제로 구현. TalkAbout이 "직접 만들어야 했던 것"의 Python 참조 구현 |
| **opencodex** | **전송로 탈취 프록시** (TypeScript, MIT) | TalkAbout이 지원하는 3개 CLI(Codex·Claude Code·Grok)를 정확히 겨냥. 전송 방식은 이식 불가지만 **quota·인증 감지·프로토콜 계약**이 3개 중 최고 |
| **orca** | **오케스트레이터** (TypeScript/Electron, MIT) | 같은 "여러 에이전트를 조율" 문제이나 데스크톱·로컬 신뢰 경계. 1라운드에서 소진 |

**핵심 변화**: 1라운드는 "Orca에서 대부분 가져온다"였고, 2라운드는 **최적 출처 14건을 Orca에서 다른 저장소로 옮겼다**(`r2-adjudication.md:§0`).

| 영역 | 1라운드 최적 출처 | 2라운드 판정 |
| --- | --- | --- |
| ACP 프레임 규율 | Orca | **hermes** — `agent/copilot_acp_client.py:413-441` |
| SQLite 락 오류 분류 | Orca | **hermes** — `hermes_state_errors.py:40-102` |
| 무제한 shutdown 해법 | Orca | **hermes** — `tools/environments/base.py:744-780` + `agent/deadline.py` |
| 관측/로그 | Orca | **hermes** — `hermes_logging.py:253-291,803-858` |
| rate-limit 창·리셋 | Orca | **opencodex** — `src/quota/reset-detector.ts:40-282` |
| capability 검증 | Orca | **opencodex** — `src/compatibility/manifest.ts` |
| 문서-코드 SSOT | Orca `AGENTS.md` | **opencodex** — `structure/manifest.json` + `structure:check` |
| POSIX 격리 | (Orca 각주) | **hermes** — lifecycle 강제 + 유한 종료 |
| 승인 종료 조건 | (없음) | **hermes** — `tools/approval_context.py:216-220` |
| 테스트 실패 시나리오 | (없음) | **hermes** — `evals/` 236파일 |

**여전히 Orca가 최적인 것**: 완료 통지 규약(`notification-burst-cooldown.ts:1`), MCP 비파괴 설치, DSH/에이전트 레지스트리 확장(45종, `src/shared/tui-agent.ts`), stall 감지 철학, reduced-motion/CSS, 스킬 스텁+정본 서빙.

---

## 1. 세 저장소의 생태계 관계 (실측)

```
[opencodex]  stock CLI의 설정/env를 바꿔 트래픽을 자기 루프백으로 끌어온다
     ↓ (Codex / Claude Code / Grok Build)
[hermes-agent]  ACP 서버 + TUI + 게이트웨이를 가진 "에이전트"  ←→  [Orca] 45종 TUI 에이전트 중 하나로 'hermes'를 지원
     ↓
[orca]  에이전트를 worktree에 병렬 실행·감독하는 데스크톱 오케스트레이터
```

- Orca는 hermes를 자사 에이전트로 지원한다: `orca-analysis/src/shared/tui-agent.ts:36` (`| 'hermes' // Hermes Agent`).
- opencodex가 건드리는 CLI는 정확히 TalkAbout의 `cli` transport 3종이다(`providers.py:39-58`).
- **TalkAbout의 위치**: 오케스트레이터(orca)도 에이전트(hermes)도 아니다. **서버 측 "방"** 이고, 에이전트는 참가자가 자기 컴퓨터에서 돌린다. 그래서
  - orca → "에이전트를 어떻게 감독하는가"를 배운다(1라운드 소진)
  - hermes → "에이전트 쪽 프로토콜·상태·승인을 어떻게 구현하는가"를 배운다 (**2라운드 신규, 가장 큼**)
  - opencodex → "제공자 한도·인증·계약을 어떻게 모델링하는가"를 배운다

### 1.1 실무적으로 바로 쓸 수 있는 연결

hermes는 **ACP 에이전트 서버로 실행된다**: `python -m acp_adapter.entry` 또는 `hermes acp` / `hermes-acp` (stdout은 ACP JSON-RPC 전용, 로그는 stderr — `acp_adapter/entry.py:1-12`).

TalkAbout의 `_grok` 경로는 이미 ACP 클라이언트다(`agent_runtime.py:952-1030`). 따라서 **Grok 없이 ACP 경로를 실제로 검증할 상대**를 확보할 수 있다.
이것이 S2(2-runtime 대화)와 Grok ACP 관문을 동시에 푸는 가장 싼 실험이다. 단 TalkAbout의 ACP 경로는 인증 method id가 `cached_token`/`xai.api_key`로 하드코딩되어 있어(`agent_runtime.py:962-972`) 일반화가 선행돼야 한다.

---

## 2. 영역별 최적 출처 지도 (이 문서의 핵심)

| 영역 | 최적 출처 | 근거(검증) | TalkAbout 부착 | 비용 |
| --- | --- | --- | --- | --- |
| **ACP 클라이언트 id 규율** | **hermes** | `agent/copilot_acp_client.py:413-441` — 비매칭 id는 `continue`, 세션 단일 deadline, 프로세스 종료/타임아웃 구분, stderr pump `join(1.0)` 후 판정. ※ ACP **프레임 기계**(oversized line·write queue·envelope)는 hermes도 `agent-client-protocol==0.9.0` SDK에 위임하므로 그 층은 **orca 결론 유지** | `agent_runtime.py:683-705,992-1004` | S |
| ACP 권한·elicitation 의미론 | hermes | `acp_adapter/permissions.py`, `edit_approval.py`, `elicitation.py` | `tool_broker.py` | M |
| **SQLite 오류 3분류 + 재시도 예산** | **hermes** | `hermes_state_errors.py:40-102`(BUSY/LOCKED/IOERR), `hermes_state.py:1008-1125`(BEGIN IMMEDIATE+jitter+idempotent 재시도) | `worker.py:172-179,391-400` | S |
| busy_timeout 단일 노브 | hermes | `hermes_cli/sqlite_util.py:18-67` (connect timeout + PRAGMA 동시, WAL fallback, Windows 첫 open 함정 주석) | `worker.py` | S |
| Windows 락 보유자 열거 | hermes | `hermes_state_holders.py:326-440` (Restart Manager ctypes: `RmStartSession`/`RmRegisterResources`/`RmGetList`) | `processes.py` | S |
| **유한 종료(shutdown escort)** | **hermes** | `tools/environments/base.py:744-780` + `agent/deadline.py:run_bounded_sync`(데몬 워커+Event.wait로 백스톱) | `runtime.py:51-59` | S |
| **POSIX 프로세스 수명** | **hermes** | `tools/environments/local.py`(pgid/setsid/parent-first kill/**receipt 전 생존자 재확인**), `gateway/cgroup_cleanup.py` | `processes.py:111-112` | L |
| 손상 상태 sticky 래치 | hermes | `hermes_state_health.py:1-20` | 신규 `storage_health.py` | S |
| **관측/로그** | **hermes** | `hermes_logging.py:253-291`(async 큐)`803-858`(상관 태그)`75-101`(Windows 로테이션 함정) | 신규 `talkabout/logging.py` | M |
| **rate-limit 창·리셋 모델** | **opencodex** | `src/quota/reset-detector.ts:40-282`(429 4분류, rolling creep 배제), `src/quota/window-mapping.ts` | `agent_runtime.py:949` | M |
| 인증 3값(present/absent/unknown) | **opencodex** | `src/claude/auth-detect.ts:1-11`, `auth-mode.ts:26-52` (unknown을 proxy로 승격 금지, 자기 토큰 재독 차단) | `agent_runtime.py:879-897` | M |
| 프로토콜 계약(닫힌 reason code) | **opencodex** | `src/protocols/contract.ts:11-84`, `features.ts:18-131`, `plan.ts`(순수 planner) | `agent_runtime.py` 파서 | M |
| **승인 무응답 종료 조건** | **hermes** | `tools/approval_context.py:216-220` `_no_user_can_answer()`, `tools/approval.py:996-1032`(즉시 deny), `:528`(아웃컴 5분해) | `tool_broker.py:157` | S |
| toolset 선언 dict | hermes | `toolsets.py`(platforms/includes/oneshot 3축) | `docs/TOOL_BROKER_PLAN.md` | S |
| **실패 시나리오 테스트 하네스** | **hermes** | `evals/` 236파일 — 이름 `<증상>_<경계>`, 첫 줄이 증거등급을 **부정형**으로 선언("NOT live-provider proof") | `tests/` | S~M |
| **문서-코드 SSOT 게이트** | **opencodex** | `structure/manifest.json`(37KB) → `structure:index` → `structure:check`(15항목), `tests/ci-workflows/structure-ssot.test.ts`(27KB) | `docs/` + `tools/run_safe_regression.py` | M |
| 문서를 테스트하는 법 | opencodex | `tests/ci-workflows/docs-*` **12개** — 레지스트리에서 수치 derive, 페이지는 locale 문구 anchor로 탐색 | `docs/STATUS.md`·`PERMISSIONS.md` | S |
| 완료 통지 규약 | **orca** | `notification-burst-cooldown.ts:1`, `notification-delivery-service.ts:134-142` | `web/app.mjs` | S |
| MCP 비파괴 설치 | **orca** | `mcp-config.ts:45-70`, `mcp-config-inspection.ts:90-98` | `mcp.py`, `cli.py` | S |
| 에이전트 레지스트리 확장 | **orca** | `src/shared/tui-agent.ts:1-46`(45종), `dsh-launch-command.ts`, `dsh-home-patch.ts` | `providers.py` | M |
| **stall(무진행) 감지** | **hermes** *(변경)* | `agent/turn_liveness.py:113-157` — 600s/15s 폴링, **수면 시간은 무진행으로 세지 않음**(`AwakeIdleMeter`), abort commit **거부권**, 스택 덤프 | `domain.py:126-139` | S |
| SQLite 버전 자가검사·WAL 대체 | **hermes** | `hermes_state_wal.py:131-134`(취약 범위 판정), `:288-302`(취약 빌드는 새 DB에 WAL 금지), `sqlite_util.py` | `worker.py:123-124` | S |
| 백업 원자성 | hermes | `backup.py:121-125` — 라이브 DB + `-wal`/`-shm` 동시 복사 금지, `sqlite3.backup()` 사용 | `deploy/` | S |
| 스킬 스텁+정본 서빙 | **orca** | `skills/orchestration/SKILL.md:15-17`, `skill-guide-get.ts:72-106` | `mcp.py` | S |

---

## 3. Tier 1 — 2주 내 (전부 S, 일부 M)

우선순위 = "TalkAbout의 미해결 관문을 직접 푸는가".

| # | 항목 | 왜 | 근거 | 부착 |
| --- | --- | --- | --- | --- |
| **0** | **SQLite 버전 노출 확인 + 자가검사** (아래 §3.1) | TalkAbout 런타임 SQLite = **3.50.4**, 문서상 WAL-reset 손상 버그 영향 범위(3.7.0–3.51.2, 백포트 3.50.7). journal이 WAL+synchronous=FULL이다 | `hermes_state_wal.py:131-134,301-302` | `worker.py:123-124` |
| 1 | **ACP 클라이언트 강건화** — 비매칭 id skip, 세션 단일 deadline, 프로세스 종료≠타임아웃, stderr pump join | `docs/STATUS.md:59` "ACP 응답 ID 불일치 원인 미확인"을 직접 해소. hermes에 동형 클라이언트가 있다 | `hermes-agent/agent/copilot_acp_client.py:413-441` | `agent_runtime.py:683-705,992-1004` |
| 2 | **ACP 원본 프레임 영속** — 실패 시 bounded 구조 서술자 + wire 전문 보관 | "원본 프레임을 확보하지 못해 임의로 허용하지 않았다"를 **반증 가능한 실험**으로 바꾼다 | `acp_adapter/gateway_server.py:124-136`, `hermes_cli/gateway_client.py:67-94` | `agent_runtime.py:329-353` |
| 3 | **journal 판정 방향 역전** — 증거 부족은 취소가 아니라 보류 | hermes: "불확실성은 파괴적 행동을 승인하지 않는다"(`cron/executions.py:140,145`). TalkAbout은 반대로 turn을 취소한다 | `worker.py:394-396` | `worker.py:391-400` |
| 4 | **journal 오류 3분류 + 시간예산 재시도 + busy_timeout 단일 노브** | transient를 stop으로 승격하는 인과 사슬 절단 | `hermes_state_errors.py:40-102`, `hermes_state.py:1008-1125`, `sqlite_util.py:18-67` | `worker.py:172-179` |
| 5 | **20ms 폴링 커넥션 제거** | 초당 50회 신규 연결이 4의 확률을 높인다 | 위와 동일 + `daemon-pty-event-subscriptions.ts:208-212`(orca) | `worker.py:391-400` |
| 6 | **유한 종료 escort** | 타임아웃의 유일한 탈출로가 무제한 `shutdown()` | `tools/environments/base.py:744-780` | `runtime.py:51-59` |
| 7 | **429 4분류 + window/reset 모델** | 이미 받는 `rate_limit_event`를 버린다 | `src/quota/reset-detector.ts:40-282` | `agent_runtime.py:949` |
| 8 | **인증 3값** — `unknown`을 절대 proxy/성공으로 승격 금지 | Grok native gating의 안전한 해제 조건 | `src/claude/auth-detect.ts:1-11` | `agent_runtime.py:879-897` |
| 9 | **승인 무응답 종료 조건** — 비대화 문맥은 pending 대신 즉시 deny | 지금 `tool_broker`를 배선하면 write가 lease 만료까지 매달린다 | `tools/approval_context.py:216-220`, `approval.py:996-1032` | `tool_broker.py:157` |
| 10 | **대표 실패 시나리오 테스트 3개** | 증거 규율이 강한 프로젝트에 가장 실용적 | `evals/acp_empty_session_wire.py`, `heartbeat_idle_wire.py:116-125`(`claim_refunded`), `delivery_flood_wire.py:134-157` | `tests/` |
| 11 | **Windows 락 보유자 열거** | "누가 이 DB를 잡았나"에 답할 수단이 없다 | `hermes_state_holders.py:326-440` | `processes.py` |
| 12 | **문서-코드 SSOT 게이트(축소판)** | 문서 드리프트가 이미 있다(`docs/PERMISSIONS.md:3` "PROPOSED / NOT IMPLEMENTED") | `opencodex/structure/manifest.json` + `structure:check` | `docs/` + `run_safe_regression.py:70-73` 한 줄 |
| 13 | **reduced-motion CSS 수리 + CSS 검사**(1R) | 미디어 쿼리 오염 | `oxlint-design-system.json:26-43` | `web/style.css:1692-1709` |
| 14 | **시간예산 분리** — RPC/세션/승인/압축에 각각 다른 상한 | TalkAbout 자체 측정으로 20초 단일 lease가 빠듯함이 이미 입증됨(Claude 콜드 36.6초, Grok help >10초). hermes는 30s/900s/300s/660s로 분리 | `r2-hermes-acp-providers.md` §5 | `agent_runtime.py`, `domain.py` lease |

### 3.1 최우선 발견 — journal SQLite 3.50.4의 WAL-reset 노출 (Lead 확인)

| 항목 | 값 | 근거 |
| --- | --- | --- |
| TalkAbout 런타임 SQLite | **3.50.4** (Python 3.14.4 번들) | `python -c "import sqlite3; print(sqlite3.sqlite_version)"` |
| journal 설정 | `PRAGMA journal_mode=WAL` + `PRAGMA synchronous=FULL` | `talkabout/worker.py:123-124` |
| 취약 범위 | **3.7.0–3.51.2** (수정 3.51.3+, 백포트 3.50.7 / 3.44.6) | `hermes-agent/hermes_state_wal.py:131-133` |
| hermes의 조치 | 취약 빌드에서는 **새 DB에 WAL을 켜지 않는다**. Dockerfile에서 SQLite 3.53.4를 소스 빌드(`SQLITE_AUTOCONF_VERSION=3530400`) | `hermes_state_wal.py:301-302`, `Dockerfile:1-4,9-10` |

**정확히 말하면**: 3.50.4는 문서상 영향 범위 안이고 3.50.7 백포트보다 낮다. 다만 hermes 자신이 **3.50.4에서 실제 측정**해 "WAL과 DELETE 둘 다 clean(0/3)"이라고 적고, 그럼에도 "upstream이 3.51.2까지 실제 버그로 문서화하고 있으므로 수정된 런타임이 나올 때까지 새 DB는 WAL로 두지 않는다"고 결정했다(`hermes_state_wal.py:269-275`). 즉 **재현된 결함이 아니라 버전 수준 노출**이다. 과장하지 않고 이렇게 기록한다.

**할 일(S)**: ① 배포 런타임의 SQLite를 3.51.3+ (또는 3.50.7+)로 고정, ② 기동 시 `sqlite3.sqlite_version`을 1회 기록하고 취약 범위면 경고(`hermes_state_wal.py:131-134` 판정 함수 이식), ③ 백업은 라이브 DB + `-wal`/`-shm` 복사 대신 `sqlite3.backup()`(`hermes-agent/backup.py:121-125` — torn restore 방지), ④ 노출 기간에는 journal `journal_mode` 선택을 재검토.

**범위 주의**: 서버 본 DB는 PostgreSQL이므로 노출은 **worker-local journal과 text 파일 저장소**에 한정된다.

---

## 4. Tier 2 — 계획 (M)

| # | 항목 | 최적 출처 |
| --- | --- | --- |
| 1 | `provider_session` 일반화 + Claude 세션 ID 영속(현재 codex 전용·재개 영구 차단) | orca |
| 2 | secrets redaction 단일 choke point — **저장 전** 순서(label→provider→userinfo→env), stdio까지 | hermes(`tools/environments/local_env_policy.py:15-60`) + orca(설계) |
| 3 | 관측 v1: async 큐 + redacting formatter + 세션 상관 태그 | hermes |
| 4 | 실패 예산과 attempt 예산 분리(`claim_refunded` 형태) | hermes + orca |
| 5 | ack 증거 사슬(서버가 `delivered_seq` 갱신, ack은 그 이하만) | orca |
| 6 | capability evidence manifest — 주장마다 assertion id, unknown-key 거부 | opencodex |
| 7 | ACP 권한·elicitation 브리지(서버측 의미론) | hermes |
| 8 | 작업 단위 문서 체계: `docs/work/YYYYMMDD_slug/{000_plan,010_evidence,020_outcome}` + outcome 마커 | opencodex(devlog 축소판) |
| 9 | toolset 선언 dict + oneshot 사전 제거 | hermes |
| 10 | cron식 incidents: 오류 시그니처 dedup(소요시간만 마스킹) | hermes |
| 11 | i18n 표 통합 + 누락 감지 | orca + opencodex(번역 parity 테스트) |
| 12 | 손상 상태 sticky 래치 + `storage_health` 표면 | hermes |

---

## 5. Tier 3 — 전략 (L)

| # | 항목 | 판정 |
| --- | --- | --- |
| 1 | **POSIX 프로세스 수명(lifetime)** | **hermes > orca > opencodex.** 단 "sandbox"가 아니라 "누출 없는 수명"으로 정의를 좁힐 때만 hermes가 낫다. hermes도 namespace·rlimit·seccomp·bwrap을 자식에 쓰지 않는다(`r2-hermes-runtime-posix.md` Q1). 이식할 것: `start_new_session`+pgid, 부모→자손→**재스냅샷→SIGKILL** 사다리, **kill receipt 전 생존자 재확인 후 통과해야 receipt 발급**(아니면 세션 유지), owner 사망 시 자식 self-kill(`agent/managed_worker.py:164-174` = PDEATHSIG 대체), cgroup 회수. orca의 `posix-pty-process-groups`는 tty 역추적이라 TalkAbout(직접 spawn)에는 과설계. opencodex는 **직접 자식만 kill**해 3자 중 최약. **비용: lifetime 기준 M**(참조 구현 존재), containment까지 가면 L |
| 2 | **ACP 전면 채택** | hermes 서버 + TalkAbout 클라이언트로 **상호 검증 가능**. Grok 없이 ACP 경로를 시험할 수 있게 된다 |
| 3 | **에이전트 호스팅**(hermes를 방의 참가자로) | `hermes acp`가 ACP 서버이므로 TalkAbout 방의 두 번째 runtime 후보. S2 관문의 대안 경로 |

---

## 6. TalkAbout 결함 총목록 (검증된 것만)

### 6.1 1라운드 확인 6건 — 상태 갱신

| # | 결함 | 2라운드 갱신 |
| --- | --- | --- |
| 1 | journal 락 오류를 stop으로 승격 → turn 오취소(`worker.py:391-400`) | **정정**: `worker.py:173`의 `timeout=1`은 busy_timeout **1000ms**다(1라운드 "busy_timeout 없음"은 오류). 실제 결함은 "폴링→경합→1s timeout→예외→승격" **단일 인과 사슬** |
| 2 | 무제한 `shutdown()`(`runtime.py:51-59`) | 유지. 해법 출처 orca→**hermes** |
| 3 | ack이 배달 증거 아님(`participation.py:73-76`) | 유지(Lead가 1라운드에 직접 확인) |
| 4 | reduced-motion CSS 오염(`web/style.css:1692-1709`) | 유지(Lead 확인) |
| 5 | 실행 전 실패가 attempt 소모(`domain.py:117-125`) | 유지. **테스트 형태**를 hermes `evals/heartbeat_idle_wire.py:116-125`(`claim_refunded: True`)에서 얻음 |
| 6 | 관측 계층 부재(`logging` import 0건) | 유지. 해법 출처 orca→**hermes** |

### 6.2 2라운드 신규 4건 — 전부 Lead가 코드 확인

| # | 결함 | 근거 | 영향 | 비용 |
| --- | --- | --- | --- | --- |
| N1 | `step()`이 `stop_requested()`를 **무방비 호출** — 같은 transient 오류가 watcher에서는 turn 취소, 여기서는 **worker 루프 사망** | `worker.py:238` | worker 프로세스 종료 | S |
| N2 | 관리자 권한 실행 시 `mode=0o700` → Windows protected DACL → **일반 사용자가 WinError 5** | `text_files.py:42`, `agent_runtime.py:588` | 배포 후 권한 사고 | S |
| N3 | 손상 journal에 **sticky 래치 없음** — 손상 상태로 계속 진행 | 대응물 `hermes_state_health.py:1-20` | 데이터 손상 은폐 | S |
| N4 | patched behavior가 **테스트에 정상으로 고정**되어 있음 — "매 호출 신규 커넥션"+"예외 전파" | `tests/test_journal_connection_lifecycle.py:68-97` | 수리 시 테스트 갱신 필수(비용에 포함) | (포함) |

### 6.3 1라운드 판정 중 **철회·격하**

| 항목 | 1라운드 | 2라운드 |
| --- | --- | --- |
| "tool fail-closed가 TalkAbout 우위" | `[대조]` 우위 | **격하**: `tool_broker.py:1`이 "app/runtime wiring에서 의도적으로 제외"라고 자백, 타 모듈 import 0건 → 우위가 아니라 **미배선**. 스키마(fence·revision·멱등·unknown·철회)는 hermes보다 낫지만, **승인 대기의 종료 조건이 없다** |
| "SQLite 락 3분류 최선 = Orca" | orca | **hermes** |
| "ACP 프레임 규율 최선 = Orca" | orca | **hermes**(클라이언트 쪽 동형 구현이 있다) |
| "`codex exec --json` 파서는 Orca에 없다" | 미확인 | **강화**: opencodex도 없다 → **3개 저장소 중 exec JSONL을 다루는 건 TalkAbout뿐** |

---

## 7. 라이선스

| 저장소 | 라이선스 | 확인 |
| --- | --- | --- |
| orca | MIT | `orca-analysis/LICENSE` |
| opencodex | MIT | `opencodex/LICENSE:1-3` "Copyright (c) 2026 opencodex contributors", `package.json:130` |
| hermes-agent | **MIT** | `hermes-agent/LICENSE:1-3` "Copyright (c) 2025 Nous Research" — **Lead 직접 확인**(adjudication은 미확인으로 남겼음) |

세 저장소 모두 MIT. 실제 코드 복사 시 저작자·라이선스 전문을 `THIRD_PARTY_NOTICES.md`(TalkAbout에 이미 존재)에 추가해야 한다. 이 보고서들은 구조·프로토콜 참조이며 코드 복사는 하지 않았다.

---

## 8. 각 저장소에서 **가져올 게 없는 것**

| 저장소 | 제외 대상 | 이유 |
| --- | --- | --- |
| **orca** | PTY 영속·상주 daemon·원격 실행·worktree·Electron UI·blocking ask/reply | TalkAbout은 세션을 유지하지 않고, 원격은 참가자 소유다 |
| **opencodex** | 주입(inject)·MITM CA·계정 풀·anthropic 어댑터 전량 번역·`devlog` 5,305편·GUI/앱/데스크톱 | **전송로 탈취**는 TalkAbout의 증거 사슬·자식 격리 원칙과 정면 충돌. ACP 구현도 없다(Lead 확인: `src` 전체 ACP 매치 1건, 무관). `devlog`는 82.9MB(저장소 39%) |
| **hermes-agent** | 다채널 게이트웨이(Telegram/Discord/Slack/WhatsApp/Signal)·자기개선 스킬 루프·`approval_detection.py`(LLM shell 분류기)·mutli-tenant 권한 모델 | 도메인 불일치. hermes는 **단일 운영자 전제**를 스스로 명시(`SECURITY.md:214-217`: "Within the authorized set, all callers are equally trusted… does not model per-caller capabilities") → TalkAbout의 3중 교집합 권한 모델이 명확히 우위 |

---

## 9. 실행 계획 (2주)

| 일차 | 작업 | 출처 |
| --- | --- | --- |
| D1 | N1·N2·N3 수리 + 회귀(N4 테스트 갱신 포함) | 자체 |
| D2 | Tier1 #3·#4·#5 — journal 오류 3분류·판정 역전·폴링 제거 | hermes |
| D3 | Tier1 #6 — 유한 종료 escort | hermes |
| D4 | Tier1 #1·#2 — ACP 클라이언트 강건화 + wire 원본 영속 | hermes |
| D5 | Tier1 #7·#8 — 429 4분류·window/reset, 인증 3값 | opencodex |
| D6 | Tier1 #9·#10 — 승인 종료 조건, 실패 시나리오 테스트 3개 | hermes |
| D7 | Tier1 #13 + Tier1 #11 — CSS 수리, Windows 락 보유자 | orca/hermes |
| D8~9 | Tier1 #12 — 문서 SSOT 게이트 축소판(15문서/4 tier manifest + 생성기 + 6검사) | opencodex |
| D10 | 회귀 전체 + `docs/STATUS.md` 갱신(수리 항목만) | — |
| 이후 | **ACP 상호 검증 트랙**: `hermes acp`를 상대로 TalkAbout ACP 클라이언트 실제 검증 → Grok 관문·S2 동시 접근 | hermes |

**POSIX 격리(Tier 3 #1)는 별도 트랙**. fail-closed 원칙(`processes.py:111-112`)을 흐리지 않도록 이번 2주에 섞지 않는다.

---

## 10. 3개 저장소 어디에도 답이 없는 관문 (억지 채택 금지)

TalkAbout의 미해결 관문 중 다음은 **세 저장소 모두 구현이 없다.** "참조가 없다"는 판정 자체가 일정 산정에 필요하다.

| 관문 | 확인 결과 |
| --- | --- |
| **TLS 종단** | hermes도 없다 — SSH 터널/reverse proxy에 위임하고 outbound `ssl_context`만 다룬다(`r2-hermes-gateway-ops.md` §5). orca는 데스크톱 앱. opencodex는 로컬 프록시 |
| **MFA** | 구현 없음(hermes 포함). TalkAbout이 직접 설계해야 한다 |
| **분산 rate limit** | hermes `gateway/bot_loop_guard.py:93-95`는 **프로세스 메모리** 카운터다. 분산은 없다 |
| **모델 응답 마스킹**(프롬프트에 비밀 유입 차단) | orca는 텔레메트리 sink만 마스킹, hermes는 `SECURITY.md:147-148`에서 "redaction은 경계가 아닌 휴리스틱"이라 명시. **두 곳 모두 프롬프트 마스킹 없음** → TalkAbout의 "값을 애초에 모델 도달 범위에 넣지 않는다" 원칙이 유일한 해법 |
| **`codex exec --json` 파싱** | orca 없음(app-server), opencodex 없음(HTTP 리다이렉트) → **TalkAbout 단독 자산**. 어떤 저장소도 대체·검증해 주지 않는다 |

---

## 11. 한 문장 요약

> **hermes-agent = TalkAbout이 직접 만들어야 했던 것의 Python 참조 구현**(ACP 클라이언트 규율·상태·승인·관측·프로세스 수명),
> **opencodex = 제공자 한도·인증·계약 모델의 최고 참조**(전송로 탈취 방식 자체는 이식 불가, ACP 구현 없음),
> **Orca = 에이전트 감독·제품 규약·ACP 프레임 기계의 참조**(1라운드에서 소진).
> 3개를 대조하는 과정에서 TalkAbout의 **확인된 결함이 10건**(1라운드 6 + 신규 4)으로 늘었고, 그중 4건은 이번에 Lead가 코드로 직접 확인했다.
> 그리고 가장 값싼 단일 발견은 **journal SQLite 3.50.4의 WAL-reset 노출**이었다(§3.1) — 코드 한 줄짜리 확인으로 드러난 운영 리스크다.
