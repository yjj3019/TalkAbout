# OpenMuse (CopilotKit/openmuse) 정밀 분석 — R6

- 분석 대상: `C:\AI-Codding\claude\TalkAbout\.analysis-repos\openmuse` (전체 히스토리 클론, READ-ONLY, 미수정, 설치/빌드 없음)
- HEAD: `a4944f4c471d3dc93b8bfc060b5da309b5216ab2` (main, 2026-10-26, "fix: keep surrogate numeric character references from failing the mail list (#183)") — origin/main과 동일, 작업 트리 clean, 총 144커밋
- 검증 시각: 2026-11-05 (GitHub API 조회 포함)
- 목적: 다중 업체 AI 에이전트(Claude Code, Codex, Grok/Antigravity, BYOK) 토론+코딩+산출물 공동 제작 작업장(TalkAbout)에 적용할 개선점 도출
- 규칙 준수: 모든 주장은 "저장소 루트 기준 경로:줄"로 뒷받침, 코드는 복사하지 않고 메커니즘만 기술, 저장소 수정 없음, 추측 금지

---

## §0 요약

| 항목 | 내용 | 근거 |
| --- | --- | --- |
| 성격 | 단일 소유자용 개인 에이전트 앱 템플릿: "browser, terminal, files, and work that keeps going". CopilotKit React Native(iOS/Android/Web) + Hono 서버 + 내구성 태스크 워커 + Playwright 브라우저 워커 + 선택적 Docker Linux 컴퓨터/E2B 데스크톱 VM | README.md:5, README.md:43-45, README.md:220-229 |
| 스택 | TypeScript(ESM, Node≥22), pnpm 워크스페이스, Hono 4, PGlite(기본)/pg(PostgreSQL), @ag-ui/core·@ag-ui/client 0.0.59, @copilotkit/runtime 1.70.1, @tanstack/ai·ai-openai·ai-anthropic·ai-gemini, Playwright 1.62(워커 자체 lockfile), @e2b/desktop 2.4.0, MCP SDK(Parallel 검색), pdf-lib, rxjs, zod | package.json:32-51, pnpm-workspace.yaml:1-21, package.json:8-10 |
| 활동 | GitHub API(2026-11-05): CopilotKit/openmuse, 생성 2026-09-15, 마지막 push 2026-11-04, star 6,181, fork 893, open issues 62, 언어 TypeScript, 기본 브랜치 main. 릴리스는 0.1.0-alpha 단일(CHANGELOG.md:3). 9월 릴리스(2442372) 이후 10월까지 메일·브라우저·서버·모니터 고장 수정이 집중(예: #183/#203/#204/#206/#174/#111, git log oneline -30 참조) | GitHub API 2026-11-05, CHANGELOG.md:1-13, git log |
| 라이선스 | **MIT** — GitHub API 확인 2026-11-05 (license.spdx_id="MIT"), 저장소 LICENSE도 "MIT License, Copyright (c) 2026 OpenMuse contributors"(LICENSE:1-3). 단 CopilotKit Intelligence(대화 영속화·재생)와 검색(Parallel, README.md:156)·텔레메트리(PostHog, config.ts:28)는 이 저장소 MIT에 포함되지 않음(README.md:194) | GitHub API 2026-11-05, LICENSE:1-3, README.md:156, README.md:194 |
| 핵심 가치 | ① SQL CAS(`data @>` 조건부 UPDATE) 결합 태스크 리스·체크포인트·하트비트 워커(중단 복구·이중 실행 방지·영수증) ② 외부 쓰기는 제안 원문·계정·타깃 버전 해시+만료+상태 검증+원자 claim+receipt 승인 게이트 ③ 브라우저는 URL/DNS/IP 검증 후 검증된 IP로만 접속, 터미널은 격리 구성 inspect 재검증 ④ run-events/evidence/artifact/명령 영수증으로 "보이는 작업" ⑤ 읽기 도구는 자유·외부 쓰기 도구만 승인 분리 ⑥ 멱등 키(requestId/operationKey)가 태스크·파일·명령에 분산 배치 | apps/server/src/engine/worker.ts:124-190, apps/server/src/actions.ts:66-89, apps/worker/src/network.ts:46-96, apps/server/src/computer-backend.ts:97-191, README.md:43-45 |
| 판정 | **태스크 실행·승인·멱등성·산출물 노출의 "단일 실행 인프라" 레퍼런스로 최상위급이며 TalkAbout의 실행 예약/영수증/승인(analysis/change_proposal)과 메커니즘이 동형** → 차용 가치 매우 높음. 단 ① 세션 owner가 항상 "local-user"(다중 사용자·방·역할 없음) ② CopilotKit Intelligence 필수(외부 서비스 강제) ③ 데스크톱(E2B)은 sudo+인터넷(앱 승인 게이트 밖) ④ 1초 전 테이블 폴링(D1)·인메모리 rate limit(D4) ⑤ "다중 업체 에이전트 토론" 구조 자체 부재. ∴ 실행 백엔드는 원천 참조, 협업 구조는 메커니즘만 추출이 적절 | apps/server/src/auth.ts:15-29, apps/server/src/app.ts:51, config.ts:80-88, SECURITY.md:29, apps/server/src/engine/worker.ts:78-89, apps/server/src/rate-limit.ts:16-44 |

### 0.1 한눈에 보는 데이터 흐름

1. 사용자 → 앱(CopilotKit headless, `useAgent`/`runAgent`) → `/api/copilotkit/*` SSE 프록시(app.ts:341-358) → CopilotRuntime+AgentsFactory(agent.ts:58-68) → 대화 에이전트 또는 외부 raw AG-UI 에이전트(agent.ts:37-56).
2. 대화 에이전트는 `delegate_task`로 작업을 "내구성 태스크"로 위임(conversation.ts:333-339) → SQL 태스크 → 워커가 lease로 실행(worker.ts:124-139) → 태스크별 모델 에이전트 도구 루프(model.ts:97-325).
3. 외부 쓰기 필요 시 `prepare_email`/`prepare_event`(model.ts:253-294) → `awaiting_review`(actions.ts:66-89) → 사용자 approve → claim(db.ts:140-152) → 실행 → receipt(actions.ts:183-206) → 태스크 재개(service.ts:811-827).
4. 진행·산출물은 run-events(worker.ts:162-172)+artifact(service.ts:740-760)+싸인 URL 파일(files.ts:104-106)+브라우저·데스크톱 카드로 UI 표면화(chat.tsx:54-120).

핵심 설계 원칙(코드에서 관찰된 6가지):
- P1 "확보 후 실행, 실패 시 인정": lease 없이는 실행하지 않고(worker.ts:126-140), 실패·중단·시간초과를 정직히 영수증화(computer.ts:519-593, worker.ts:212-238).
- P2 "외부 쓰기는 제안→승인→원자 실행": 승인 입력 자체가 승인하지 않는 원문과 해시로 묶임(actions.ts:66-89, 110-111).
- P3 "모든 재시도는 같은 ID로 같은 결과": 멱등 키가 분산 구현(§3.4).
- P4 "경계는 2회 검증": 브라우저 URL은 서버+프록시 이중, 컴퓨터 attach는 inspect 스키마 재검증(worker network.ts:46-96, computer-backend.ts:97-191).
- P5 "콘텐츠는 data이지 지시가 아니다": 프롬프트·도구 설명에 반복 주입(model.ts:340, conversation.ts:388-391).
- P6 "사람은 대화·태스크 수준에서 개입 가능": waiting_input/approval, pause/stop, 테이크오버(service.ts:248-366, EXPERIENCE.md:7-11).
- 판정 종합: 이 원칙 6개는 TalkAbout의 "에이전트 실행 인프라"에 전부 직접 이식 가능하며, 특히 P1·P2·P3는 실행·승인·버전에 즉효(§4 I-1~I-4).

### 0.2 핵심 수치·경계 요약(모두 코드 검증)

- 태스크: 동시 3(worker.ts:89), lease 60s(127)/하트비트 20s(189), 5분 런 타임아웃(model.ts:371), 스텝 16/6(37, 384), 비종료 100개(service.ts:203-207), 승인 30분(actions.ts:88).
- 브라우저: 활성 3/프로필 20(worker browser.ts:163-174), 유휴 30분(292-297), 읽기 100,000자(317-318), 텍스트 10,000자 입력(349), navig 20s(120), 요청 본문 64KiB(server.ts:11-16).
- 컴퓨터: 명령 30s/출력 128KB(computer-backend.ts:287-301, 28), 파일 256KiB/PDF 10MiB(computer.ts:29, 777-778), lease 3분(computer.ts:30).
- 파일/검색: PDF 10MiB/500페이지(files.ts:61-63), 검색 45s/1MiB(30-31, 62-97), 결과 10개(129-130).
- 인증: 세션 24h(auth.ts:27), 싸인 URL 15분(52), 로그인 30/분(app.ts:107-121), rate limit 120/분(rate-limit.ts:18-19).

### 0.3 용어·약어

| 약어 | 뜻 | 위치 |
| --- | --- | --- |
| AG-UI | Agent-client UI 프로토콜(@ag-ui/core 0.0.59) | package.json:33-34, agent.ts:3 |
| CAS | compare-and-swap: 조건부 UPDATE로만 전이 | db.ts:93-105 |
| lease | 태스크 실행 소유권(60s) | worker.ts:126-139 |
| proposal/action | 승인 대기 외부 쓰기 제안 | actions.ts:66-89 |
| receipt | 실행 영수증(runs/명령/액션 결과) | worker.ts:192-211, computer.ts:562-593 |
| artifact/files | 태스크 산출물(PDF·요약) | service.ts:740-760, files.ts:48-103 |
| evidence | 태스크가 인용한 출처(발췌) | domain agent.ts:13-19 |
| run-event | 태스크 타임라인 이벤트 | worker.ts:162-172 |
| waiting_input/approval | 사람 개입 대기 상태 | domain agent.ts:6-7 |
| outcome_unknown | 외부 쓰기 결과 불확실 | actions.ts:194-203 |
| computer | Docker/E2B 터미널·파일 실행부 | computer-backend.ts:17-25 |
| monitor/idea/goal | 지속 감시·제안·목표 | domain agent.ts:58-94 |

### 0.4 보고서 가독성용 범례

- 자주 인용하는 파일은 첫 언급 시 전체 경로, 이후 축약(예: "worker.ts"=apps/server/src/engine/worker.ts, "기존 …" 명시).
- [즉시]/[단기]/[중기]/[제외]는 적용 시급 분류(§4).
- "경유" 표기(§6)는 해당 파일을 읽었으나 중간 구간만 인용했음을 의미.

---

## §1 아키텍처

### 1.1 워크스페이스 구성

- pnpm 워크스페이스(4개 앱+3개 패키지) — 기여·확장 단위가 명확히 분리된다(README.md:220-229, pnpm-workspace.yaml:1-21).
  - `apps/mobile`: Expo 공유 iOS/Android/Web UI, CopilotKit headless 훅(`useAgent`/`useThreads`), 카드 렌더(chat.tsx:1-9).
  - `apps/server`: Hono API+CopilotKit 런타임+태스크 엔진(worker/engine)+리뷰(actions)+파일(files)+영속화(db)+컴퓨터(computer·computer-tools).
  - `apps/worker`: Playwright 브라우저 서비스(토큰 보호, server.ts:1-4).
  - `apps/computer`: 비루트 Linux 이미지+files.py(Dockerfile:1-17), E2B 데스크톱 백엔드(computer-e2b-desktop.ts).
  - `packages/domain`: 공유 타입/요청 검증(agent.ts:1-166, computer.ts). `packages/integrations`: Google·브라우저 프로토콜·PDF·vault. `packages/backends`: 비활성 OpenBot 어댑터(openbot.ts:104-118).
- 루트 스크립트(package.json:11-31): dev(tsx watch index.ts:12), test(tests/*.test.ts:16), test:browser(실 Chromium, 17), test:computer(실 Docker·E2B, 29-30), build:server(21).
- DB: 단일 `records(owner,kind,id,data jsonb,updated_at)` 테이블(createStore, db.ts:200-203)로 태스크·액션·파일·브라우저·세션·자격증명·목표·아이디어·메모리를 모두 담는다. 소유자 경계는 owner 열(db.ts:20-24), 경합은 조건부 UPDATE CAS(compareAndSwap, db.ts:93-105)와 insertIfAbsent(db.ts:122-131), 부분 갱신은 jsonb 병합(db.ts:100-103)이 담당한다.
- 배포: render.yaml(API web+인프로세스 워커·브라우저 pserv·정적 web, render.yaml:1-100), 로컬 브라우저는 compose.yaml(read_only·cap_drop ALL·tmpfs 256m·healthcheck, infra/compose.yaml:20-28). PGlite는 다중 프로세스 공유 불가 → 별도 태스크 워커는 DATABASE_URL 필수(worker-entry.ts:6-9).

### 1.2 AG-UI 결합(CopilotKit 런타임)

- 서버 측: `makeRuntime()`이 `CopilotRuntime`+`AgentsFactory`+`CopilotKitIntelligence`를 조립(agent.ts:28-68)하고, 요청마다 `auth.owner`로 소유자를 결정해 에이전트에 주입(agent.ts:37-56), `identifyUser`도 동일 소유자(agent.ts:63-66). Hono에 `/api/copilotkit` 베이스로 마운트(agent.ts:69).
- 전역 미들웨어(app.ts:55-79): Origin 화이트리스트(55-62), CORS+credentials(63-71), 12MiB 본문 상한(72-78), nosniff/no-referrer/no-store(58-60), `/api/*` rate limit(79).
- 클라이언트: `useAgent({agentId, threadId})`(chat.tsx:221-222) → `copilotkit.runAgent`(chat.tsx:337)/`stopAgent`(chat.tsx:452). 턴 실패 코드(threadLocked 등) 필터 후 재시도 큐(conversation-run.ts:41-61, chat.tsx:436-452).
- 스레드: 외부 CopilotKit Intelligence에 저장하고 로컬 미러(conversation-settings/목록, app.ts:217-249) 병행, 메시지는 `MessageSchema`(AG-UI)로 검증(MessageSchema.parse, app.ts:244-247). 스레드 라우팅은 threadId+최신 사용자 메시지 키(requestKey, conversation.ts:44-47).
- AG-UI 상태 도구(스냅샷 교체/JSON 델타)는 채팅 상태에 한정(tanstack-agent.ts:83-111), 스텝 제한 초과 시 마지막 단계 뒤 안내 삽입(tanstack-agent.ts:195-219).
- 에이전트 백엔드 3종(agent.ts:37-56): `sample`(스크립트), `model`(TanStack 어댑터), `agui`(외부 raw AG-UI 에이전트 `HttpAgent`, agent.ts:46-50, 미설정 URL 가드 agent.ts:47-48). `agentConfigured` 검증(agent.ts:15-27)은 모델 키 3사(openai/anthropic/google) 중 하나 또는 AG-UI URL 존재로 판정.
- SSE 전달(app.ts:341-358): `/api/copilotkit/(run|connect|...)` 요청을 런타임에 위임하고 SSE 문자열 청크를 바이트로 변환해 WHATWG Response로 재포장(347-357) — 클라이언트가 표준 SSE를 그대로 받는다.
- 텔레메트리(config.ts:28, .env.example): SDK 기본 전체 캡처, 명시 비활성화 복수 옵션(COPILOTKIT_TELEMETRY_DISABLED/DO_NOT_TRACK, tests/setup.ts:3-4). 결과 태그(accessibility_title) 부여(agent.ts:58-62).
- 인터럽션: 턴 중지(runAgent abort, chat.tsx:452)와 태스크 abort(worker.ts:63-65, 141)가 별개 계층 — 대화 중단이 위임 작업을 취소하지 않음(EXPERIENCE.md:9).

### 1.3 에이전트 실행 경로(내구성 태스크 워커)

- 태스크 상태 머신(domain agent.ts:3-12): queued/running/waiting_approval/waiting_input/scheduled/paused/succeeded/failed/cancelled. 요청 검증(domain agent.ts:135-143): prompt≤12000자, title≤160자, kind 5종(agent/document/monitor/finance/plan), feed goalId·milestoneId 결합(service.ts:198-202), 비종료 태스크 100개 상한(service.ts:203-207).
- 워커 틱(1초 폴링, worker.ts:50-54):
  - due 후보: queued·scheduled(nextRunAt 도래)·running(leaseUntil 만료)·waiting_approval(worker.ts:79-86).
  - waiting_approval는 연결 액션이 executing/succeeded가 되기 전에는 후보 제외(worker.ts:90-110), 만료된 awaiting_review는 expired 전이(98-109).
  - 동시 실행 3 하드코딩(worker.ts:89), 실행은 틱 수명을 넘기므로 장기 태스크가 다음 틱을 막지 않음(worker.ts:114-118).
- 실행 사이클(run, worker.ts:124-256):
  - lease 60s CAS 확보(126-139) → 시도수 증가(138) → 실패 시 즉시 반환(140).
  - `guard()`: leaseId·상태 재검증, 불일치 시 LostLeaseError(144-148). `checkpoint()`: leaseId 조건부 CAS로 부분 상태 저장(149-161). `event()`: run-events 기록(162-172).
  - 하트비트: leaseMs/3 주기, 실패 시 abort(174-190). `runs` 영수증: started(192-197)→finished/interrupted/failed(205-211, 240-249).
  - 완료: checkpoint(204) → LostLeaseError/abort 재큐(212-220), 일반 오류 failed 영구화+이벤트(221-238) → settled 콜백(254-256).
- 도구 실행 계층(model.ts): outcome 후 실행 거부(69-74), 오류 이벤트(76-84), 전 도구 직렬 큐(47-56), cached 멱등 래퍼(86-96), 스텝 제한 16(337), 5분 런 타임아웃(368-371), 미완 종료 시 대기 질문(404-411).
- 태스크 종류별 구현:
  - 문서: source 결정(982-988)→import→필드 수집 질문→filled PDF→답장 준비(model.ts:134-172, 296-303) — 테스트 입증(workflows.test.ts:74-107).
  - 감시: 관측→hash 비교→변화 시 알림, 실패 2^n분 백오프·5연속 실패 정지(service.ts:829-870, 1068-1204, matchesPrice 1205-1208).
  - 재무: CSV→요약 아티팩트(service.ts:872-895). 일반: 모델 에이전트(service.ts:897).
  - 유지보수 루프 60초: 발행 복구·아이디어 퇴역·감시 재활성(service.ts:71-128) — 크래시 후 안전망.

1.3.1 실행 결과·이벤트 종류(모두 검증)

| 이벤트/결과 | 저장 | 조건 | 위치 |
| --- | --- | --- | --- |
| plan/step/approval/result/error/observation | run-events | 실행 중 기록 | worker.ts:162-172, service.ts:793-799 |
| 실행 영수증(running/interrupted/failed) | runs | start/finish | worker.ts:192-211, 240-249 |
| 태스크 failed/재큐 | tasks | 오류 2분류 | worker.ts:212-238 |
| outcome_unknown | actions | 외부 쓰기 불확실 | actions.ts:194-203 |
| 알림(task-done/error/input/review) | notifications | settled 발행 | service.ts:908-965 |

### 1.4 브라우저 실현부(Playwright 워커)

- 인증: WORKER_TOKEN 32자 이상 강제(server.ts:42-43), 해시를 timingSafeEqual 비교(server.ts:59-63), 본문 64KiB(server.ts:11-16), 30s/10s/100요청 제한(server.ts:119-122).
- 세션: UUID 형식·RFC variant 검증(worker browser.ts:28-34), 세션당 직렬 큐(68-77), 활성 3·저장 프로필 20(163-174), 유휴 30분 정리 스위퍼(292-297), 종료 시 쿠키 storageState 저장(146), 실패 시 프로필 삭제/error 상태(271-290).
- 브라우저 격리(createSession, 160-203): validatePublicUrl 선검증(161), 호스트 env에서 자격증명 제외(184-189), headless+프록시 bypass loopback(190-196), `--disable-quic`·UDP WebRTC 금지·호스트 리졸버 규칙 사설 차단(197-202), 저장된 쿠키 복원(214-222).
- 서브리퀘스트 검증(223-231): 모든 라우트에 URL 재검증, 실패는 blockedbyclient abort, WebSocket 즉시 close(231). 팝업/다이얼로그 닫기(237-242), PDF 다운로드 캡처(한 세션 20개, 243-260), 읽기는 고정 evaluate(호출자 JS 주입 불가, 307-332, 서버 측 100,000자 상한 포함).
- URL 검증(worker network.ts:8-96): 공개 IPv4/IPv6 규칙(8-42), 프로토콜/포트/임베드 자격증명/특수 TLD/로컬호스트 차단(65-73), DNS 5s 타임아웃(77-90), 결과 일부 비공개 시 거부(91), select 후 검증된 주소 접속(93-95). 재결합(rebinding)이 구조적으로 차단된다(apps/worker/README.md:62).
- 다운로드 반입 최소화: PDF 캡처는 서명 검사(%PDF-)+10MiB+20개 제한(worker browser.ts:243-260, 379-388), 콘솔 조작은 좌표/키 4종(333-368) — "브라우저에서 실행 코드 반입"을 배제.
- 서버 측(BrowserService): 워커 헬스 캐시 15s(server browser.ts:51-63), 요청 45s+AbortSignal(86-89), 소유권 선기록(create 128-138; thread 고정 세션 예약 194-205), 반환 세션 ID 불일치 502(121-127), 브라우저 소유만 조회(108-127).

### 1.5 터미널/파일 실현부(컴퓨터)

- Docker 백엔드(computer-backend.ts): 컨테이너 생성 매개변수 고정(user 1000:1000, read-only rootfs, cap-drop ALL, no-new-privileges, network none, ipc private, 512MB/1CPU/128pids/64MB tmpfs, restart no; 220-263), **attach 전 `docker inspect` 스키마 재검증**(이미지·사용자·라벨·마운트·네트워크, 97-166)+볼륨 소유권 검증(167-191). 명령은 30s timeout+2s kill 유예+128KB 출력 상한(287-301, computer.ts:28-83), 파일은 files.py stdin 호출(302-318). 호스트는 고정 docker CLI argv로만 실행, 셸 폴백 없음(computer.ts:85-87).
- 컴퓨터 서비스(computer.ts): 연산 독점 lease 3분+휴먼/로봇 점유(244-310), 명령 영수증(exitCode/timedOut/interrupted/truncated, 562-593), operationId 멱등(444-452), 경로 `/workspace` 고정+`..` 금지(172-184), 파일 256KiB·PDF 10MiB(29, 777-778).
- E2B 데스크톱(computer-backend.ts:326-373, computer-e2b-desktop.ts): sudo 계정·인터넷 enabled, use_desktop 도구는 액션마다 스크린샷을 이미지 part로 모델에 전달(computer-tools.ts:113-155), 유휴 15분 휴면·VNC 스트림·퍼블릭 포트 주의(docs/COMPUTER.md:53-60, 89-113).

### 1.6 도구 목록(대화 vs 태스크)

- 대화(conversation.ts:145-379): computer_status/start·stop_computer/run_computer_command/list·read·write_computer_file/import·export_computer_pdf(computer-tools.ts:48-164), read_calendar(읽기 전용+클리핑, 174-209), search_mail(snippet 240자, 210-238), read_mail_thread(12000자 바운드, 239-265), search_web(조건부, 266-289), browse_web(30000자+세션 ID, 290-332), delegate_task(333-339), agent_status(340-346), create_goal(347-357), watch_page(358-364), remember_fact(365-379), 조건부 present_choices(Jev, 160-172). 스텝 한도: 데스크톱 16, 기본 6(381-386).
- 태스크(model.ts:97-325): set_plan(106-115), read_workspace(116-121), read_mail_thread(122-133), import_pdf(134-143), inspect_pdf(144-152), fill_pdf(153-173), search_web(174-196), read_web(197-227), save_artifact(228-252), prepare_email(253-270), prepare_event(271-294), ask_user(296-303), finish_task(304-324), 컴퓨터 공통(98-104). "외부 쓰기 승인 도구는 없다" 명시(model.ts:340).

### 1.7 검색·파일·자격증명

- 검색(search.ts): Parallel 웹 검색 MCP(StreamableHTTP, 64-97), 45s·1MiB 상한(30-31, 62-97), 결과 10개·발췌 30,000자 바운드(128-146), 대화별 고정 랜덤 세션 ID(45-59). 대화/태스크 문맥이 Parallel에 전송됨을 문서화(README.md:156).
- 파일(files.ts): 결정적 해시 ID(31-40), 10MiB/500페이지(61-63), 임시 파일+link 원자 게시(65-83), 15분 싸인 URL(signed, 104-106), PDF 채움은 부모 연결+결정적 출력(127-153).
- 자격증명: AES-256-GCM(v1.nonce.tag.ciphertext, vault.ts:12-23, 해독 검증 25-56), PKCE+state 1회 take+generation CAS(google-auth.ts:73-99, 106-131), 리프레시 dedup(37, 192-234), 리보크(235-251), 스코프 재검증(workspace.ts:445-449), 타깃 버전 ETag(451-455). 동기화 대화 규율(conversation.ts:388-391).

### 1.8 래퍼런스: Jev·OpenBot

- Jev(TypeSafe) 선택 패널(jev/adapter.ts): 샘플(규칙)과 라이브(점수·제어) 어댑터(51-79), 5s·재시도 1(30-32), 신뢰도 0.5 미만 시 에이전트 제어 유지(29, 188-189), 선택 결과를 대화에 재투입(conversation.ts:44-81).
- OpenBot(packages/backends/src/openbot.ts, OPENBOT-INTEGRATION.md:3-22): 비활성 컨트랙트(104-118), per-person transport 원칙(11-14), "미사용·미검증" 명시(OPENBOT-INTEGRATION.md:74-76).

### 1.9 설정·배포·운영 인벤토리

- 환경 변수(.env.example, config.ts:106-183): WORKSPACE_MODE(sample/live), AGENT_BACKEND(3종), MODEL(provider/model), 3사 API 키+OPENAI_BASE_URL/OPENAI_CHAT_COMPLETIONS(게이트웨이용), ACCESS_KEY/TOKEN_ENCRYPTION_KEY, DATABASE_URL, BROWSER_WORKER_URL/WORKER_TOKEN, COMPUTER_*(이미지·provider·E2B 키·배포 ID), JEV_MODE/TYPESAFE_API_KEY, WEB_SEARCH_ENABLED, ALLOWED_ORIGINS, TRUST_PROXY, CPK_INTELLIGENCE_API_KEY(필수).
- .env 로딩(config.ts:5-25): `.env`보다 이미 설정된 환경변수가 우선하고(shadowedEnvKeys, 5-11), 우선된 키를 경고(16-24). 텔레메트리 기본 샘플 1(config.ts:28).
- 배포: render.yaml(API+브라우저 pserv+정적 web, 1-100), Compose(브라우저 워커 1개, infra/compose.yaml:1-31), PC의 컴퓨터 컨테이너(docs/COMPUTER.md:7-23), 텔레메트리 비활성화 옵션 2종(.env.example).
- 데이터 경로: DATA_DIR(파일·싸인 키·PGlite), 브라우저 프로필은 워커 DATA_DIR(worker README.md:49-54), 컴퓨터 /workspace 볼륨(docs/COMPUTER.md:7-23).

### 1.10 태스크 상태 전이 시퀀스(워커 관점)

1. 생성: queued(개입 없음) 또는 paused(goal 정지/보류, service.ts:229).
2. 틱: due 선정(worker.ts:79-86) → 동시 3 미만이면 lease CAS(126-139) → running+하트비트(174-190).
3. 태스크 종류 실행(service.ts:801-897) 중: 준비→waiting_approval(776-781), 질문→waiting_input(296-303), 완료→succeeded(899-907), 실패→failed(221-238), 일시정지/취소(273-278).
4. 이후: settled 콜백(254-256) → 알림(publishOutcome, 908-975) → 모니터·목표 마일스톤 반영(918-935).
5. 크래시 회복: 시작 시 액션 recovery(index.ts:11, db.ts:153-157), 60초 maintain 재발행(service.ts:85-128).

---

## §2 협업·인간개입 모델

### 2.1 외부 쓰기 승인(리뷰) 흐름

1. 제안 준비: `service.prepare`(service.ts:761-800)가 태스크 시작 계정과 현재 연결 일치 확인(769-774) → `propose`(actions.ts:39-101) → awaiting_review+30분 만료(66-89) → 전문·계정·타깃 버전 해시(77-87) → 멱등 키 시 해시 ID(44-52) → 준비 이벤트(approval) 기록(793-799).
2. 결정: decide(actions.ts:102-207) — 해시 일치(110-111) → 태스크 상태 running/waiting_approval(113-120) → 만료→expired(121-135) → 연결 유지(136-149) → 원자 claim(awaiting_review→executing/denied, 150-160) → execute(183-191) → outcome_unknown/failed 구분(194-203) → receipt(204-206) → activity 기록(208-217).
3. 재개: 워커는 액션이 executing/succeeded가 아니면 태스크를 꺼내지 않음(worker.ts:90-110), 실행 진입 시 성공 액션이면 결과로 계속(service.ts:811-827), 실패/거부/만료는 오류 마감(822-825). 승인된 결과는 중복 실행하지 않음(model.ts:258-268, 284-292).
4. 샘플 검증: 무클라이언트 문서 태스크→waiting_approval→pause 중 approve 거부→resume→approve→succeeded→알림 1건(workflows.test.ts:74-107).

5. 예외 사례(모두 코드 확인):
   - 멱등 재제안: 같은 prepare 입력(해시 키)은 기존 proposal 반환(actions.ts:44-52) — 이중 제안 카드가 생기지 않는다.
   - 만료: 30분 경과 은 awaiting_review→expired(actions.ts:121-135), 워커도 expired 전이(worker.ts:98-109).
   - 연결 변경: 태스크 시작 계정과 다른 연결이면 준비 시 409(service.ts:769-774), 승인 시 409(actions.ts:136-149), 연결 변경 토큰 갱신도 409(google-auth.ts:56-72) — "누구 눌러도 내 계정" 원칙이 삼중으로 지켜진다.
   - 크래시: executing 시작 시 recovery로 outcome_unknown 정리(index.ts:11, db.ts:153-157), 유실 응답은 브라우저 소유권 선기록(server browser.ts:128-138), 발행은 maintain 재시도(service.ts:85-128).

### 2.2 입력 요청·실행 제어

- ask_user(태스크: model.ts:296-303) → waiting_input(domain agent.ts:6-7) → answer+fields 응답 누적(service.ts:336-366, taskAnswers model.ts:17-21) → 재개 입력 재전달(model.ts:349-357). 취소/일시정지/재개/재시도는 CAS 전이+abort(service.ts:248-335), 취소 시 미결 제안 자동 deny(321-325), 감시 재개는 2단계 마커 CAS(437-467, 468-559), 목표 일시정지는 하위 태스크 일괄 정지(386-393).
- 대화 턴: 정지 사각 버튼·팔로우업 큐(EXPERIENCE.md:8-10), 큐는 앱 로컬 보유(EXPERIENCE.md:10), 아카이브/복원(threads.tsx:1, 115).

### 2.3 도구 권한 모델

- "고정 도구 목록=권한": 읽기 도구 즉시 허용(conversation.ts:174-332, model.ts:116-227), 이메일 전송·일정 변경만 승인 필수(model.ts:253-294), 컴퓨터 명령은 컨테이너 경계 안(computer-tools.ts:71-80), 데스크톱은 "사용자 명시 요청 시" 지침(computer-tools.ts:13-17). 승인은 앱에서만, 채팅 인자로 불가(conversation.ts:389). outcome 이후 도구 거부 가드(model.ts:69-74, 100-103).
- "untrusted data" 규율: 툴 설명(conversation.ts:174-177, 213, 242, 295)과 프롬프트(model.ts:340, conversation.ts:389-391)에 "data≠지시" 반복 주입, evidence 발췌 500자 캡(model.ts:178-192).

### 2.4 산출물 노출

- 유형: agent-artifacts(plan/comparison/finance/report, domain agent.ts:101-109), files(15분 싸인 URL, files.ts:104-106, auth.ts:51-57), run-events(worker.ts:162-172), evidence(service.ts:737-739), notification(service.ts:726-736), 컴퓨터 명령·데스크톱 영수증(computer.ts:562-593, 643-749).
- 산출물 버전의 의미: files.fill은 부모 연결과 결정적 키로 파생 산출물을 만들고(files.ts:127-153), agent-artifacts는 태스크별 목록으로 묶여(domain agent.ts:101-109) "어느 실행이 어느 산출물을 만들었는지"가 항상 역추적된다(service.ts:179-184).
- 산출물 접근 제어: 모든 산출물 URL은 15분 싸인 URL(auth.ts:51-57, files.ts:104-106)이며 콘솔/미리보기도 동일 규칙(server browser.ts:114-120) — 공유·노출 범위가 시간과 경로에 묶인다.
- 상세 조회: 태스크가 지목한 artifactIds/browserIds/taskId만 읽음(service.ts:172-192), 브라우저 preview/console 싸인 URL(server browser.ts:114-120), 알림 중복 제거(service.ts:908-965).

### 2.5 워크플로 스토리(문서 태스크, 테스트 입증)

1. delegate_task(document, conversation.ts:333-339) → 5단계 계획(service.ts:208-221) → lease(worker.ts:124-139) → PDF import(model.ts:134-143) → 질문(ask_user) → 답변 누적(service.ts:346-360) → filled PDF(files.ts:127-153) → 답장 제안(waiting_approval) → 승인 → claim→"Gmail sent" receipt(workspace.ts:456-468) → succeeded+알림(workflows.test.ts:74-107).

| 단계 | 상태 | 핵심 동작 | 증거 |
| --- | --- | --- | --- |
| 위임 | queued | plan 5단계 생성 | service.ts:208-221 |
| 실행 | running | lease+하트비트 | worker.ts:124-190 |
| 자료 수집 | running | import/read/질문 | model.ts:134-172, 296-303 |
| 입력 대기 | waiting_input | 답변 누적 | service.ts:336-366 |
| 제안 | waiting_approval | proposal+만료 | actions.ts:66-89 |
| 승인 | executing | claim→전송 | actions.ts:150-160, 183-191 |
| 마감 | succeeded | receipt+알림 | service.ts:899-907, 910-917 |

### 2.6 알림·활동·브라우저 인계

- 알림: 성공(task-done)/오류(task-error:attempts)/입력(input:question 해시)/승인(review:actionId) 모두 해시 중복 제거(service.ts:910-960), 감시 오류는 failureStreak 키(850-868).
- 브라우저 인계: Take control은 같은 세션 싸인 콘솔(server browser.ts:292-294)로 열고(browser-console.ts), 데스크톱은 라이브 스트림+픽셀 조작(agent-ui.tsx:555-576, docs/COMPUTER.md:53).

### 2.7 권한·승인 매트릭스(도구 수준)

| 행동 | 승인 필요 | 경로 | 위치 |
| --- | --- | --- | --- |
| 메일 검색/스레드 읽기 | 아니오 | search_mail/read_mail_thread | conversation.ts:210-265 |
| 공개 웹 읽기 | 아니오 | browse_web/read_web | conversation.ts:290-332, model.ts:197-227 |
| 캘린더 조회 | 아니오 | read_calendar | conversation.ts:174-209 |
| 컴퓨터 명령/파일 | 아니오(컨테이너 경계) | run_computer_command 외 | computer-tools.ts:48-164 |
| 데스크톱 클릭/타이핑 | 지침(사용자 명시 요청) | use_desktop | computer-tools.ts:113-155, SECURITY.md:29 |
| 이메일 전송/일정 생성·수정·삭제 | 예(awaiting_review) | prepare_email/prepare_event→review | model.ts:253-294, actions.ts:102-207 |
| 태스크 위임/감시/목표/메모리 | 아니오(계획 활동) | delegate_task/watch_page 등 | conversation.ts:333-379 |
| 산출물 저장 | 아니오(버전 CAS) | save_artifact/fill_pdf | model.ts:153-173, 228-252 |

### 2.8 시나리오: 브라우저 인계와 사람 복귀

1. 에이전트가 browse_web으로 페이지 읽기(conversation.ts:297-322) → 상태에 sessionId 기록(server browser.ts:194-227).
2. 사용자가 Take control 클릭 → 같은 프로필·쿠키 콘솔(싸인 URL, server browser.ts:114-120, app.ts:134-149).
3. 사람이 클릭/키 조작(worker browser.ts:333-368) → 재개 대화/태스크가 같은 세션을 참조(VERIFICATION.md:19-21).
4. 앱 재시작 후에도 같은 threadId→같은 브라우저 세션(`chat-browsers` 예약, server browser.ts:194-205) — "세션 귀속"이 어느 경로든 유지된다.

---

## §3 보안·동시성·품질

### 3.1 자격증명·인증

- 세션(auth.ts): access key 사전 해시 비교(15-22), 토큰 해시 저장+24h(23-29) → owner 반환(31-46), 만료 삭제(39-44), 리보크(47-50). 싸인 URL: owner·경로·만료 HMAC+timingSafeEqual(51-75), 각 로그인 30회/분(app.ts:107-121).
- Google(vault.ts·google-auth.ts): AES-256-GCM(12-23), generation CAS(73-99), 리프레시 dedup(192-234), 스코프/연결 재검증(workspace.ts:443-449), 타깃 버전(451-455).
- 경계(config.ts): 샘플 모드 loopback 강제(181-182), 라이브는 access key≥24자+encryption key 강제(174-180), .env/.openmuse/백업 사적 취급(SECURITY.md:11).

### 3.2 브라우저 범위

- 강제: 공개 HTTP(S) 80/443만(worker network.ts:8-42, 46-96), 검증된 IP 접속(91-95), WebSocket·서비스 워커·팝업·다이얼로그 차단(worker browser.ts:231, 193, 237-242), 스크립트 주입 불가 읽기(307-332). 인정 한계: Chromium 내장 샌드박스 비활성(SECURITY.md:13), hostile-tenant 경계 아님(apps/worker/README.md:64).

### 3.3 터미널 범위

- Docker: network none+read-only+cap-drop ALL(computer-backend.ts:220-263), attach 재검증(97-191), 자동 재실행 금지(computer.ts:519-561), 파일 심볼릭 링크/특수 파일 거부(SECURITY.md:21). E2B: sudo+인터넷+퍼블릭 포트+iframe 함정(SECURITY.md:29, docs/COMPUTER.md:105-109) — 이 경로는 앱 승인 게이트 밖 명시(SECURITY.md:29).
- 실행 런타임 격리 함수: 명령은 /usr/bin/timeout+bash 경유(computer-backend.ts:287-301), 파일 헬퍼는 고정 경로·8s/10s 타임아웃(302-318) — 임의 인터프리터 실행 경로가 없다.
- 명령 결과 해석: exitCode/timedOut/interrupted/truncated를 receipt에 별도 필드로 분리(computer.ts:28-49, 562-593) — 오류를 한 문자열로 삼키지 않는다(테스트: computer-runner.test.ts).

### 3.4 동시성·멱등성 사례

| 경합 지점 | 메커니즘 | 위치 |
| --- | --- | --- |
| 태스크 lease/checkpoint | leaseId CAS+하트비트+LostLeaseError | worker.ts:124-161, 174-190 |
| 승인 claim | awaiting_review→executing 원자 전이 | db.ts:140-152, actions.ts:150-160 |
| 컴퓨터 실행 | 연산 lease 3분+점유 확인 | computer.ts:244-310 |
| 재시도 중복 | requestId/모니터/파일/명령 결정적 해시 | service.ts:193-197, 399-413, files.ts:31-40, computer.ts:444-452, cached(model.ts:86-96) |
| 알림 중복 | 해시 키 insertIfAbsent | service.ts:726-736, 908-965 |
| 감시 재개 경합 | 작업 마커 2단계 CAS | service.ts:437-467, 468-559 |

멱등 시나리오(재시도 관점):
- 요청 반복: createTask 재반복 → 같은 해시 ID의 기존 태스크 반환(service.ts:193-197) — 재전송 안전.
- 준비 반복: 같은 제안 키 → 같은 proposal(actions.ts:44-52), files.import 같은 operationKey → 같은 아티팩트(files.ts:56-61).
- 명령 반복: operationId 재사용 → 같은 영수증(computer.ts:444-453), 다른 명령에 같은 키 → 409.
- 승인 반복: 이미 결정된 제안은 상태 반환(actions.ts:112), claim 실패자는 현재 상태 반환(156-159).
- 다중 워커: lease CAS 실패자는 조용히 반환(worker.ts:140) — 한 틱에 한 실행만 소유한다.

### 3.5 테스트·CI

- tests/ 60+ 파일(Api·문서·모니터·재무·브라우저·컴퓨터·Google·액션·멱등·워커·rate limit·검색·Jev), 검증 154개 통과(docs/VERIFICATION.md:7).
- 테스트 분류 인벤토리(tests/*.test.ts 기준): 브라우저 계열(browser, browser-fixture-runner, browser-pdf-prefix, browser-imports-race, conversation-browser), 컴퓨터 계열(computer, computer-runner, computer-e2b-desktop, computer-api), 모델/워커 계열(model-worker, model-provider, model-retry, model-outcome, step-limit, prepare-lease-loss), 워크플로(workflows, persistence, actions, agent-api), 인가/설정(auth-startup, oauth, rate-limit, config, resource-ids), 자료 처리(pdf, google, finance*, monitor-*), 기타(domain, engine, log, telemetry, vault, search, openbot, jev*).
- CI 7 job(ci.yml): quality(lint/typecheck/server build), platform(web/ios/android expo export), browser(실 Chromium install+test), browser-container(Docker), computer-container(Docker).
- 워크플로 수준: 무클라이언트 문서→승인→영수증→재개(workflows.test.ts:74-107), 체크포인트 유실 재시도 산출물 재사용(109-150), 최초 시작 싸인 키 경합(auth-startup.test.ts:25-38), 텔레메트리 격리(tests/setup.ts:1-4).
- 알려진 미완: 실모델·실 Google 계정 수용(VERIFICATION.md:29-33), Android 장치 검증 아님(VERIFICATION.md:35, 42).

### 3.6 문서·지표·운영 근거

- 문서 체계: SECURITY.md(경계 집약), docs/VERIFICATION.md(검증 절차), docs/COMPUTER.md(경계), docs/EXPERIENCE.md(UX), docs/FEATURES.md(참조 인벤토리), docs/OPENBOT-INTEGRATION.md(컨트랙트), ROADMAP.md(미래), CONTRIBUTING.md(기여).
- 문서가 실제 구현과 정합: SECURITY.md:15-25 구문은 computer-backend.ts:220-263과 일치(SECURITY.md:19 ↔ 220-263, 21 ↔ 172-184), worker README:49-54 한도는 worker browser.ts:163-174와 일치.
- 지표: 154 tests(VERIFICATION.md:7), 144 커밋, CI 7 job(ci.yml), open issues 62(GitHub API 2026-11-05). 응답 헤더·본문 한도·타임아웃이 모든 진입점에 적용(app.ts:55-79, server.ts:11-16, worker.ts:50-54, search.ts:62-97).

### 3.7 결함/deficiency (심각도 순)

| # | 결함 | 위치 | 내용 |
| --- | --- | --- | --- |
| D1 | 태스크 폴링 전 테이블 스캔 | engine/worker.ts:78-86 | 1초마다 `scan("tasks")`(db.ts:133-139)가 전 owner·전 태스크를 읽고 메모리 필터. 수량×owner 선형, 방 수 확장에 불리. |
| D2 | 동시 실행 3·세션 3·프로필 20 하드코딩 | engine/worker.ts:89, apps/worker/src/browser.ts:163-174 | 설정/방별 할당 없음, 동시 브라우즈·다중 방 경합 시 소진. |
| D3 | 단일 소유자 | server/auth.ts:15-29 | 세션 owner "local-user" 고정(26)·24h 고정(27). 멀티 유저/역할/방 없음(README.md:150, ROADMAP.md:30). |
| D4 | rate limit 인메모리 Map | server/rate-limit.ts:16-44 | 재시작 소실·다중 인스턴스 무효, trustProxy 책임(config.ts:170-172). |
| D5 | 외부 서비스 강제 의존 | server/app.ts:51, config.ts:80-88 | CPK_INTELLIGENCE_API_KEY 필수(80-88), 없으면 채팅 불가(226-237), MIT 아님(README.md:194). 검색 문맥 전송(README.md:156), 텔레메트리 기본 전체 캡처(config.ts:28). |
| D6 | 데스크톱(E2B) 승인 밖 경로 | SECURITY.md:29, docs/COMPUTER.md:105-113 | sudo+인터넷+퍼블릭 포트, VNC 비밀번호 8문자(SECURITY.md:29). |
| D7 | 외부 쓰기 재시도 정책이 지침 의존 | README.md:188, actions.ts:194-203 | outcome_unknown은 "재시도 안 함"뿐 자동 정합 없음(194-203, workflows.test.ts:109-150의 재사용은 파일 레벨까지). |
| D8 | 대화 팔로우업 큐가 앱 로컬 | EXPERIENCE.md:10 | 큐는 앱 켜 둔 동안만, 서버 인박스 아님. |
| D9 | 컨텍스트/비용 폭증 | engine/model.ts:326-341 | memories·evidence·priorState 전부 프롬프트 주입(340), 답변 누적 재전송(349-357). |
| D10 | run-events/activity 누적 무정리 | engine/worker.ts:162-172, actions.ts:208-217 | 보존 기간·삭제·내보내기 없음(ROADMAP.md:30). |
| D11 | 고정 타임아웃/한도 | engine/model.ts:368-371, computer-backend.ts:287-301, actions.ts:88 | 모델 5분·명령 30s·승인 30분 고정(재제안 필요, actions.ts:121-135). |
| D12 | 단일 스레드 라우팅 | server/app.ts:217-249 | main-thread 단일(agent.ts:37-56) — 방 다중 세션·참가자 없음. |
| D13 | 브라우즈 제한 표면 | worker browser.ts:197-202, 307-332 | 서비스 워커/스크립트 조작 불가를 "사이트 미지원"으로만 표면(worker README.md:45). |

결함×개선 매핑: D1·D2→I-8, D3→I-9, D4→I-8, D5→I-11(제외/대체), D6→I-11(제외), D7→I-2(영수증), D8→I-4, D9→I-7, D10→I-9, D11→I-7, D12→I-4·I-9, D13→I-6.

---

## §4 개선점 도출 — 다중 업체 AI 에이전트 토론+코딩+산출물 작업장 적용

적용 대상 전제(TalkAbout): Python/FastAPI+PostgreSQL, 초대제 방, 여러 벤더 에이전트 토론, change_proposal/analysis 산출물, 실행 예약/영수증, Codex 작업공간 편집 옵트인, MCP/http 카탈로그. 각 항목: [파일:줄 → 좋은 점 → 적용안] + 영향·비용·위험.

### [즉시] I-1 실행 예약·리스·영수증 CAS 상태머신
- [engine/worker.ts:124-256 → lease/하트비트/재큐/failed/runs, db.ts:93-105 → 조건부 UPDATE]
- 좋은 점: 확보(126-139)→하트비트(174-190)→상실 재큐(212-220)가 원자적이라 크래시·이중 실행이 구조적으로 차단되고 실행별 영수증(192-211)이 결과를 남긴다.
- 적용안: TalkAbout 실행 예약을 queued/running/waiting_*/terminal+leaseUntil+attempts로 모델링, 전이는 CAS 단일 문으로만 허용, receipt에 결과·변경파일 목록 기록. 폴링은 I-7 방식으로 도입.
- 적용 세부(TalkAbout 매핑): 실행 테이블(receipt)에 lease_id·started_at·finished_at·status·요청 해시·산출물 버전 스냅샷 저장, 전이 API는 CAS만 노출, 재시도는 같은 requestId로 재전송 허용, 워커는 실행별 재진입 보호(worker.ts:124-140 동형).
- 영향: 실행 신뢰성·감사↑ / 비용: 수일 / 위험: 낮음(PG 보유).

### [즉시] I-2 change_proposal/산출물 승인 게이트(해시+만료+claim+receipt)
- [actions.ts:39-101, 102-207, db.ts:140-152 → 제안 원문·계정·타깃 버전 해시(66-89)]
- 좋은 점: 승인 시 해시/상태/연결/만료 재검증(110-149) 후 claim 성공자만 실행(150-160), outcome_unknown 보존(194-203).
- 적용안: change_proposal(코드 변경·배포)에 동형 계약 적용: approve 시 제안+브랜치/대상 버전 원문 해시 검증 후 원자 claim, 실행 유실은 outcome_unknown 영수증화, analysis 발행은 부모 버전 참조.
- 적용 세부: 승인 UI는 원문(제안+브랜치/대상 버전) 미리보기만 제시, approve 토큰=원문 해시, 결과 무응답은 outcome_unknown 상태+재조정 수단(actions.ts:194-203 동형), deny는 이유 기록.
- 영향: 제안 무결성·부정 적용 방지 / 비용: 수일 / 위험: 낮음.

### [즉시] I-3 멱등 키 표준(결정적 해시 ID+재사용)
- [service.ts:193-197, files.ts:31-40, computer.ts:444-452, model.ts:86-96 → 키=scoped 해시, 존재 시 같은 레코드 반환]
- 좋은 점: 재시도가 같은 ID로 같은 결과를 반환(먼저 쓰고 재사용, files.ts:56-61, computer.ts:447-453).
- 적용안: TalkAbout 모든 쓰기/실행 API에 requestId 계약 도입(중복 발행·중복 커밋 방지), 토론 라운드·도구 호출·편집 커밋에 일괄 적용.
- 적용 세부: 미들웨어 단 한 곳에서 requestId↔해시 ID 캐시(먼저 읽기→없으면 생성→있으면 반환, service.ts:193-197, files.ts:56-61 동형), 로그·영수증에 requestId를 공통 키로 강제.
- 영향: 중복 실행 제거 / 비용: 수일 / 위험: 낮음.

### [즉시] I-4 이벤트를 방 구독 스트림으로(AG-UI 이벤트 매핑)
- [engine/worker.ts:162-172, service.ts:793-799 → run 이벤트, chat.tsx:436-452 → 클라이언트 구독]
- 좋은 점: run-events kind(approval/input/result/error)가 채팅 구독과 분리(conversation-run.ts:41-61)되어 "사람 개입 포인트"가 이벤트로 고정.
- 적용안: 방 채널에 "에이전트 발언/제안/승인 요청/편집 완료"를 이벤트로 발행, 구독은 방 소속만(owner 경계 db.ts:20-24 재사용), 확정 change_proposal을 이벤트로 브로드캐스트.
- 적용 세부: 방 이벤트 테이블이 아니라 "채팅 스트림+영구 이벤트 로그" 2중 구조(worker.ts:162-172 동형)로 발행, 재연결 시 로그 재생으로 복원(conversation-run.ts:41-61의 재생 오류 식별 재사용).
- 영향: 실시간 토론 UX / 비용: 수일~1주 / 위험: 낮음~중.

### [단기] I-5 untrusted-data 규율+읽기/쓰기 분리 프리앰블
- [model.ts:340, conversation.ts:388-391 → "data≠지시" 주입, 승인 인자 금지]
- 좋은 점: 외부 쓰기 승인 도구가 없음을 명시(model.ts:340)하고 채팅 인자 승인 불가(conversation.ts:389).
- 적용안: 다중 벤더 에이전트(특히 외부 호스팅/코드 실행) 라운드 프롬프트에 동일 규율 고정, 발언(텍스트)과 편집(도구)을 권한 클래스로 분리해 발언의 부수 효과를 구조 금지.
- 적용 세부: 프리앰블을 탬플릿 상수로 중앙화(모델별 포함 섹션 선택), 도구 카탈로그 메타데이터(읽기/쓰기/외부전송 플래그)를 스키마로 검증, 정책 위반 도구 호출은 승인 요구로 자동 분류.
- 영향: 주입 저항↑ / 비용: 수일 / 위험: 낮음~중(모델 순응 의존).

### [단기] I-6 브라우저/터미널을 격리 게이트웨이로(이중 검증+고정 읽기)
- [worker/network.ts:46-96 → 검증 후 접속, worker/browser.ts:307-332 → 고정 읽기, computer-backend.ts:97-191 → attach 재검증]
- 좋은 점: "호출자는 URL만, 서버가 검증·접속·추출"(worker browser.ts:223-230, 307-332)과 attach 재검증(computer-backend.ts:97-191)이 모범.
- 적용안: TalkAbout 브라우즈 도구·Codex 편집 실행을 격리 워커로 분리, 실행 전 자원 검증→권한 부여→영수증 부과, 호스트 편집은 읽기 전용 미러/작업 브랜치 한정.
- 적용 세부: 워커 API에 토큰(32자+timing-safe, server.ts:42-63), 요청당 자원 검증(worker network.ts:46-96), 결과는 고정 추출기(worker browser.ts:307-332), 실행 전 소유권 선기록(server browser.ts:128-138) — "검증→허가→영수증" 3계층을 그대로 이식.
- 영향: 원격 작업공간 안전 / 비용: 1~2주 / 위험: 중(컨테이너 운영).

### [단기] I-7 모델 타임아웃·컨텍스트 관리(체크포인트 스코프)
- [model.ts:326-341 → 주입 범위, model.ts:368-371 → 5분 고정]
- 좋은 점: evidence 500자 캡(178-192)과 답변 누적(349-357)은 유지하되 "전체 메모리·전체 state" 주입을 섹션 선택 주입으로 좁힌다(태스크당 30,000자 read_web 캡, model.ts:223).
- 적용안: 라운드마다 주입 항목(참조 산출물 요약·관련 evidence·필수 메모리)을 명시 목록으로 한정, 타임아웃을 태스크 kind·스텝 수에 비례 설정.
- 적용 세부: 컨텍스트 크기 예산(예: 토큰 한도)을 설정화, 라운드별 주입 목록을 매니페스트로 정의, 긴 결과는 요약/포인터(evidence 500자 캡, model.ts:178-192)로 대체, 타임아웃은 kind·스텝 수 비례(모델 5분 고정 해제, model.ts:368-371).
- 영향: 토큰 비용↓·민감 정보 표면↓ / 비용: 수일~1주 / 위험: 낮음.

### [중기] I-8 폴링→조건부 쿼리+방별 풀+공유 rate limit
- [worker.ts:78-89 → scan 제거, rate-limit.ts:16-44 → 공유 스토어]
- 좋은 점: lease(124-139)가 다중 워커 안전을 보장하므로 쿼리만 좁히면 수평 확장이 열린다.
- 적용안: 폴링을 `WHERE kind='tasks' AND (status IN (...)) AND (nextRunAt<=now...)`+한정 행으로 교체(방별 워커 풀, 방당 1~2), rate limit을 Redis/PG로 이전.
- 적용 세부: 방별 폴링 워커(주기·일괄 크기 설정화), SKIP LOCKED/조건 인덱스로 scan 교체(worker.ts:78-86), 활성 3 리터럴을 방 타깃(room_jobs)으로 치환(worker.ts:89), rate limit은 Redis 슬라이딩 윈도우로(rate-limit.ts:16-44 교체).
- 영향: 방 수 확장 / 비용: 2~4주 / 위험: 중(스키마).

### [중기] I-9 방 아이덴티티·역할·산출물 버전·보존 정책
- [auth.ts:15-29 → per-user 세션, service.ts:172-192 → 스코프 조회, db.ts:108-121 → 부분 갱신]
- 좋은 점: owner 열(db.ts:20-24)과 jsonb 부분 갱신(db.ts:108-121)이 확장 기반.
- 적용안: 세션=사용자+방, 태스크/산출물/자격증명에 roomId 부여, analysis/change_proposal에 마일스톤 CAS 패턴(service.ts:918-934) 적용, run-events·activity 보존 기간·삭제 API 추가(ROADMAP.md:30 해소).
- 적용 세부: 세션에 user_id·room_id 클레임 추가(auth.ts:23-29 확장), roomId 공통 인덱스(owner,kind,roomId) 추가, 산출물 발행을 "부모 버전 CAS(마일스톤 패턴, service.ts:918-934)"로, 로그 보존 일수·삭제 워커 신설.
- 영향: 멀티 테넌트 작업장 / 비용: 수주 / 위험: 중(인가 재설계).

### [중기] I-10 다중 벤더 어댑터 계약(모델 파서·게이트웨이·오류 분류)
- [tanstack-agent.ts:25-65 → openai/anthropic/google+OPENAI_BASE_URL, actions.ts:194-203 → outcome 분류, agent.ts:15-27 → agui 가드]
- 좋은 점: provider/model 파서+게이트웨이(tanstack-agent.ts:25-41)와 오류 분류(타임아웃/재시도/결과 불확실, actions.ts:194-203)가 벤더 중립 표준.
- 적용안: TalkAbout MCP/http 카탈로그에 "어댑터 계약(factory)+권한 클래스(읽기/쓰기)+오류 분류+멱등 키" 명세를 추가, 라운드별 벤더 할당에 활용.
- 적용 세부: 공급자 등록 스키마(protocol/도구 목록/권한 클래스/base_url/오류 분류/멱등 키 규약, tanstack-agent.ts:25-65, actions.ts:194-203 참조)를 카탈로그 테이블로, 라운드 할당은 등록된 도구 교차(capability)로 제한.
- 영향: BYOK 확장 / 비용: 2~4주 / 위험: 중.

### [제외] I-11
- CopilotKit Intelligence(app.ts:51, config.ts:80-88), CopilotKit RN 의존(chat.tsx:1-9), E2B 데스크톱(SECURITY.md:29), OpenBot(openbot.ts:104-118), Parallel 검색(README.md:156), PostHog 텔레메트리(config.ts:28)는 도입하지 않음 — 자체 채널·승인 파이프라인 유지하고 AG-UI는 순수 프로토콜로만 사용.
- 제외 근거: ① Intelligence는 라이선스 아님(README.md:194)+미설정 시 기동 불가(config.ts:80-88) ② E2B는 승인 게이트 밖 경로(SECURITY.md:29) ③ OpenBot은 비활성·미검증(OPENBOT-INTEGRATION.md:74-76) ④ Parallel/PostHog는 데이터 유출 표면(README.md:156, config.ts:28).
- 대안: TalkAbout은 자체 Postgres 스레드 저장+자체 검색/감사 로그로 대체(채팅 프록시는 app.ts:341-358의 SSE 패턴만 차용), 브라우즈는 I-6 격리 게이트웨이로.

### §4 적용 매핑 요약표

| 개선 | 원천(파일:줄) | TalkAbout 적용 지점 | 시급 |
| --- | --- | --- | --- |
| I-1 태스크 상태머신+lease | engine/worker.ts:124-256 | 실행 예약/영수증 테이블, CAS 전이 | 즉시 |
| I-2 승인 게이트 | actions.ts:66-89, 102-207 | change_proposal approve/deny, claim | 즉시 |
| I-3 멱등 키 | service.ts:193-197, files.ts:31-40 | 모든 쓰기 API requestId | 즉시 |
| I-4 방 이벤트 스트림 | worker.ts:162-172, service.ts:793-799 | 방 구독/알림 채널 | 즉시 |
| I-5 규율 프리앰블 | model.ts:340, conversation.ts:388-391 | 라운드 프롬프트 고정 헤더 | 단기 |
| I-6 격리 게이트웨이 | worker/network.ts:46-96, computer-backend.ts:97-191 | 브라우즈/편집 워커 | 단기 |
| I-7 컨텍스트 한정 | model.ts:326-341 | 라운드 컨텍스트 조립 | 단기 |
| I-8 조건부 폴링·공유 RL | worker.ts:78-89, rate-limit.ts:16-44 | 스케줄러·스로틀 | 중기 |
| I-9 방 아이덴티티·버전·보존 | auth.ts:15-29, service.ts:918-934 | 멀티 사용자·산출물 버전 | 중기 |
| I-10 벤더 어댑터 계약 | tanstack-agent.ts:25-65, actions.ts:194-203 | MCP/http 카탈로그 | 중기 |
| I-11 외부 서비스·E2B·OpenBot | app.ts:51, SECURITY.md:29, openbot.ts:104-118 | 제외 | 제외 |

---

## §5 미확인·한계

- 실모델·실 Google 계정·Rich Threads 실서비스 수용 미완(VERIFICATION.md:29-33, 40): 검색·메일·캘린더는 픽스처·샘플 기준이며 로컬 검증(154개, VERIFICATION.md:7)은 CI 원격 결과와 분리(VERIFICATION.md:12).
- CopilotKit runtime/@tanstack/ai/@ag-ui 내부 동작과 CopilotKit Intelligence 전송 범위는 패키지 소스로는 미검증(README.md:194, package.json:33-44) — 저장소 코드와 문서 근거만.
- render.yaml:1-100 실가동·다중 인스턴스 부하 특성·장기 운용/보존·취약점/라이선스 스캔·브라우저 콘솔 싸인 링크 CSRF 실공격 검증은 미수행.
- GitHub API 값(star/fork/issues)은 2026-11-05 스냅샷. rate limit 120/분(rate-limit.ts:18-19)·세션 24h(auth.ts:27)·타임아웃 리터럴(worker.ts:127, model.ts:371)은 코드 기준.
- 동시 턴 중복 전송·재연결 시퀀스(chat.tsx:289-291, 336-339), inFlight/active 메모리 추적(worker.ts:27-29)의 극한 경합, 데스크톱 VNC 스트림 프로토콜, WebSocket 재연결 보장은 미확인(단정 회피).
- 성능 프로파일링·부하 테스트·모델 토큰/검색 쿼터 비용 추정, 다국어/접근성 검증은 수행하지 않음. 본 보고서는 저장소를 수정하지 않았고 설치/빌드/실행을 수행하지 않았다.
- 브라우저 인증서/피싱 정책(worker browser.ts:197-202), 싸인 URL의 CSRF/클릭재킹, 텔레메트리 전송 항목의 정확한 스키마, AG-UI EventType 확장 표면(@ag-ui/core 0.0.59, package.json:33-34)의 향후 변경 방식은 외부 동작이라 미확인.
- §4 적용안의 비용/위험은 TalkAbout 팀 판단용 상대 추정이며 원천 저장소에 대한 주장이 아니다.
- 한국어 번역/해석: 용어(예: "review"→승인(리뷰), "work keeps going"→지속 작업, "take control"→인계)는 필자 해석이며 원문 의미와 미세 차이가 있을 수 있음. 근거는 원문 문장을 그대로 따름(§1~§3).
- 적용 판정(§4)은 TalkAbout 컨텍스트에 대한 권고이며, openmuse 저장소 자체에 대한 결함 주장은 §3.7의 D# 목록이 전부다.

---

## §6 검증 인벤토리(본 보고서가 읽어 확인한 파일)

- 루트: README.md, package.json, pnpm-workspace.yaml, CHANGELOG.md, LICENSE, SECURITY.md, ROADMAP.md, .env.example, render.yaml, infra/compose.yaml, .github/workflows/ci.yml, docs/{README,COMPUTER,EXPERIENCE,VERIFICATION,OPENBOT-INTEGRATION}.md.
- 서버: index.ts, worker-entry.ts, app.ts, agent.ts, auth.ts, config.ts, db.ts, actions.ts, files.ts, browser.ts, browser-console.ts(경유), computer.ts, computer-backend.ts, computer-e2b-desktop.ts(경유), computer-tools.ts, google-auth.ts, rate-limit.ts, search.ts, engine/{worker,service,model,conversation,tanstack-agent,routes}.ts, jev/adapter.ts, workspace.ts, packages/domain/src/agent.ts, packages/integrations/src/{google(경유),vault,pdf(경유)}.ts, packages/backends/src/openbot.ts.
- 워커: apps/worker/src/{server,browser,network,errors(경유)}.ts, apps/worker/README.md, Dockerfile.
- 모바일: apps/mobile/src/{chat,threads,agent-ui,conversation-run}.tsx.
- 테스트: tests/{setup,workflows,auth-startup,model-worker(경유),computer(경유)}.ts, apps/worker/tests(경유), apps/computer/Dockerfile.
- GitHub API(2026-11-05): api.github.com/repos/CopilotKit/openmuse — MIT, star 6,181, fork 893, issues 62, TypeScript, main.
- 중간 재생출력이 잘려 나중에 별도 재확인한 구간: engine/service.ts:129-1209, engine/model.ts:86-412, engine/conversation.ts:140-507, engine/worker.ts:80-257, actions.ts:102-218, workspace.ts:140-513, computer-tools.ts:22-166, apps/worker/src/browser.ts:106-396, server/browser.ts:108-237 — 위 구간의 모든 인용은 재확인 후 기재.

### 주요 근거 한 줄 요약(결론용)

- 승인 게이트가 "입력 해시+만료+원자 claim+receipt" 4단인 점은 TalkAbout change_proposal에 그대로 옮길 표준(actions.ts:66-89, 150-160).
- 태스크 실행이 "lease CAS+하트비트+영수증"인 점이 TalkAbout 실행 예약의 핵심 차용점(worker.ts:124-211).
- 브라우저가 "호출자 URL←검증←검증된 IP 접속" 구조인 점이 다중 벤더 브라우즈 도구의 안전 기준(worker network.ts:46-96).
- 주의: 단일 소유자·외부 의존(app.ts:51)·인메모리 한도(rate-limit.ts:16-44)는 그대로 이식 금지(D3/D4/D5).
- 결론: 실행 인프라만 떼어 독립 모듈로 도입하고, 협업·방·다중 벤더는 TalkAbout 자체 구조 + AG-UI 프로토콜만 사용할 것.
- (끝) 본 문서는 openmuse 저장소(READ-ONLY)의 HEAD a4944f4c 기준이며, 이후 커밋이 있으면 갱신이 필요할 수 있다.
