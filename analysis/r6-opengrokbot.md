# OpenGrokBot (wolfqing/OpenGrokBot) 정밀 분석 — R6

- HEAD: `cf6f17cdc6dc634888f896c1ec77686f844a7e91` (2026-11-04, "feat(gateway): record typed browser workflows (#11)", `main` 브랜치)
- 분석 대상: 로컬 클론 `C:\AI-Codding\claude\TalkAbout\.analysis-repos\opengrokbot` (읽기 전용, 빌드/설치/실행 금지 준수)
- 참조 규칙: 저장소 루트 기준 `파일:줄`. 추측 없이 코드/문서 근거만 사용.

---

## §0 요약

### 0.1 한눈에 보기

| 축 | 내용 |
|---|---|
| 성격 | 셀프호스트 "항상 켜져 있는" AI 동료(teammate) 크루. xAI Grok Bot의 자가 수탁(self-hosted) 대안 (README.md:1-16) |
| 팀원 정의 | 팀원 1명 = 자기 전용 컴퓨터 1대 + 스레드 1개 + `SOUL.md`(정체성)/`AGENTS.md`(운영 매뉴얼·하드 바운더리) (README.md:31-40) |
| 스택 | pnpm 워크스페이스: `gateway/`(Node 22+, Hono, better-sqlite3, playwright-core, dockerode, croner, ws) + `web/`(React 19, Vite 8, vitest) (gateway/package.json:17-25, web/package.json:6-16) |
| 활동 | v0.1(2026-08-12, OpenClaw 배포판) → v0.2(2026-08-13, 자체 코어) → v0.3(2026-10-22, 승인/루틴/웹훅) → v0.4(2026-10-30, 로컬 컴퓨터) → HEAD(2026-11-04, teach-mode 녹화 1차 슬라이스) (CHANGELOG.md:88-354) |
| 라이선스 | MIT (LICENSE:1-21; GitHub 공개 저장소 기준 2026-11-05, 본 세션은 로컬 LICENSE 직접 열람으로 확인 — §5 참고) |
| 실행 모델 | 단일 게이트웨이 프로세스 + 팀원별 컴퓨터(컨테이너 또는 로컬 브라우저+폴더) + 화면(noVNC/CDP) (main.ts:22-57) |
| 모델(BYOK) | **OpenAI 호환 엔드포인트 1개**(base/key/model 전역 env)만 지지 (config.ts:63-66, llm.ts:56-129) |
| 활동 요소 | 보고 문법 칩 / 승인 게이트 / 루틴(cron·웹훅·워치) / 메모리 규칙 / 액션 로그 / 워크플로 녹화 / 디스패치 그룹 (prompts.ts:4-13, approvals.ts, routines.ts, memory.ts, actions.ts, workflows.ts) |
| 테스트/CI | 게이트웨이 522개·웹 91개 테스트 케이스(소스 검색 기준), 3OS CI + 로컬 스모크 + audit + 이미지 빌드 (ci.yml:8-98) |
| 토론/코딩 | **없음** — 보조 작업·숙제 대행 중심. "여러 벤더가 함께 토론하고 코드를 함께 쓰는" 워크플로 개념 부재 |

### 0.2 판정

- **[이식할 자산]** "승인-전-보류" 흐름(approvals.ts), 외부 데이터 펜스(agent.ts:740-764), bot별 1턴 레인(turns.ts), 멱등 결정(approvals.ts:206-212), 샌드박스 자기검증(sandbox.ts:241-272), `BotComputer` 백엔드 추상화(computer.ts:14-44), 단일 진실 공급원 env(config.ts), 문서의 정직성(security.md).
- **[재설계 축 1: 모델 계층]** 멀티 모델·멀티 벤더 미지원(소스 grep 0건, 의존성에도 관련 라이브러리 없음) — TalkAbout 목표(Claude Code/Codex/Grok/BYOK 동시 수용)와 정면 충돌(§1.4, §3.5-1).
- **[재설계 축 2: 산출물 계층]** 산출물 = 스레드 메시지 + 워크스페이스 파일뿐. 아티팩트/버전/diff/리뷰 상태 테이블 없음(db.ts:42-107) — TalkAbout의 "버전 달린 분석/변경제안 통합 diff"에 해당하는 계층 부재(§3.5-3).
- **[재설계 축 3: 룸/예산/승인 의미]** 1인 운영자·외부 방출 승인·일일 토큰 예산은 공동 작업장의 "초대 룸·잔액·워크스페이스 편집 승인"과 다른 의미(§2.7). 규모 착각 주의: 브라우저 자동화+테이크오버는 숙제 대행 UX이므로 채택 대상 아님(§4 제외).

### 0.3 이식/재설계 우선순위 요약

| 우선순위 | 항목 | OGB 근거 | TalkAbout 적용 |
|---|---|---|---|
| 1(즉시) | 턴 직렬화·병합·중단 계약 | turns.ts:32-114, shim.mjs:84-95 | 실행 예약→영수증 상태 머신 |
| 2(즉시) | 외부 데이터 펜스 | agent.ts:740-764 | 벤더 간 발언 마킹 |
| 3(즉시) | 멱등 결정+부분 승인 | approvals.ts:206-212, server.ts:522-530 | 편집 승인 게이트 |
| 4(즉시) | 공통 LLM 재시도/타임아웃/사용량 | llm.ts:30-42, 76-77 | 어댑터 공통 계층 |
| 5(단기) | 실행자 백엔드 추상화 | computer.ts:14-44, backend.ts:16-24 | Claude Code/Codex 실행자 |
| 6(단기) | 룸 메모리 diff 강제 | memory.ts:8-14, 55-71 | 합의 기록 감사 |
| 7(중기) | 벤더 어댑터 레지스트리 | llm.ts:56-129(확장점) | 멀티 벤더 BYOK |
| 8(중기) | 룸 토론 워크플로 | server.ts:427-442(발언 뼈대) | 발제→견해→판정→산출물 |
| 제외 | noVNC/브라우저 자동화/데몬화 | screen.ts, computer-local.ts:156-191, deploy/ | 목적이 다름 |

---

## §1 아키텍처

### 1.1 팀원(teammate) 모델

- 데이터 정체: `bots` 테이블 행(id/name/role/emoji/soul_path + shell_enabled) (db.ts:43-50).
- 대화 단위: `threads`(kind `dm`|`group`, db.ts:51-57)와 `messages`(kind 13종: text/report/screenshot/approval_request/approval_resolved/memory_updated/routine_created/bot_ref/login_request/activity/question/webhook, db.ts:6-18).
- 팀원 정의 파일: `SOUL.md`(1인칭 성격 + frontmatter role/emoji, soul.ts:4-19), `AGENTS.md`(운영 매뉴얼, server.ts:281-287가 로드).
- 번들 데모: `teammates/{chief,researcher,market-watch,inbox-keeper}` — 시작 시 `seedTeammates`로 upsert(main.ts:31-34, seed.ts:7-27).
- 사용자 고용: `POST /api/bots` → slug id(비라틴 이름은 랜덤) + `<data>/teammates/<id>/SOUL.md` 생성(bots.ts:27-55).
- 성격의 실체: 공통 프롬프트 헌법(보고 문법 prompts.ts:4-13, 승인 훈육 prompts.ts:53-67, 컴퓨터 브리핑 prompts.ts:18-47) + 개인 맥락(메모리/루틴/스킬/그룹)의 조립(buildSystemPrompt prompts.ts:106-141).
- 모든 팀원이 동일 모델·동일 키 사용 — 위임/차등화 개념 없음(§1.4).
- 새 팀원 생성 시 도구 체크리스트 없음 — 복잡도는 사용에서 자란다는 제품론(README.md:108-109).

### 1.2 게이트웨이

- 단일 Hono 앱, 기본 `127.0.0.1:4747`(config.ts:70-71, main.ts:146-150).
- 정적 UI를 한 포트로 서빙(빌드된 `web/dist`, web.ts:32-58, RESERVED 경로 예외 web.ts:26).
- WebSocket `/ws` 허브: 메시지/상태 브로드캐스트 + `hello` 스냅샷(재연결 보정, main.ts:113-119, hub.ts:23-35, presenceSnapshot server.ts:247-256).
- 화면 릴레이: Docker=noVNC proxy(`/vnc/:botId/websockify`), 로컬=CDP 스크린캐스트(`/screen/:botId/ws`) (main.ts:122-141, screen.ts:154-177, computer-local.ts:156-182).
- SQLite(WAL): 마이그레이션 5단계 + 사전 백업 + 하위 호환 금지(db.ts:115-170, 229-246).
- 크론 스케줄러(croner): 루틴 등록/제거/재로드/정지(scheduler.ts:15-54).
- 유휴 컴퓨터 회수: 60분 기본, 시청/작업 중 제외(main.ts:167-183, pool.ts:98-117, screen.ts:106-119).
- 액션 로그 90일 보존(main.ts:103-110, actions.ts:45-47).
- 상태 전송 원칙: 턴의 산출은 먼저 DB 커밋 → WS 브로드캐스트 → 재연결 보정(server.ts:1051-1052, hub.ts).

### 1.3 컴퓨터/브라우저 제어

- 추상화: `BotComputer` 인터페이스(computer.ts:14-44) — shell/파일/브라우저 + 4개 선택 능력(스크린캐스트/창 표시/ping/워크플로 녹화).
- 백엔드 분기: docker|local 단 2종(backend.ts:16-24), auto는 데몬 핑 확인(backend.ts:151-169).
- 풀: ping 실패(셸 health + CDP 응답 둘 다) 시 폐기·재생성(computer-docker.ts:73-76, pool.ts:34-45).
- Docker 컴퓨터 구성(containers.ts:41-79):
  - Xvfb + headed Chromium(CDP 내부 9223만, 외부 9222는 shim 토큰 게이트) (entrypoint.sh:24-38, shim.mjs:157-203)
  - noVNC via websockify(6080) + 셸/파일 shim(7717) (entrypoint.sh:54-56)
  - 컨테이너 간 통신 차단 네트워크 `enable_icc=false`(containers.ts:145-159)
  - 메모리/CPU 한도 + PidsLimit 512 + no-new-privileges + docker-init(containers.ts:66-77)
  - 재시작 unless-stopped + 라벨 기반 재구성(이미지/스펙/설정/워크스페이스 변경 감지, containers.ts:69, 200-214)
  - 시크릿 1회 생성(쉼 토큰 24바이트+VNC 8자, containers.ts:34-39), 포트는 127.0.0.1 게시(containers.ts:47)
  - 로그인/파일은 `/workspace` 마운트에 보존(containers.ts:67, 208-213)
- 로컬 컴퓨터(computer-local.ts, backend.ts:69-127):
  - headless Chromium(기본) + 팀원 전용 폴더(workspaceDir), 브라우저 탐색→다운로드 폴백(browsers.ts:12-97)
  - 파일 유폐: 문자열 경로 검증 + 심링크 추적(confined computer-local.ts:62-96) + O_NOFOLLOW(computer-local.ts:210) + 16MB 상한(computer-local.ts:17)
  - CDP 스크린캐스트(JPEG 60, computer-local.ts:156-182), 로그인 벽 실창 테이크오버(showWindow computer-local.ts:183-191, server.ts:812-822)
  - 셸: 기본 off → 팀원별 스위치 → 시스템 샌드박스(Seatbelt/bubblewrap) 자기검증 통과 시 on(server.ts:793-809, sandbox.ts:44-50, 241-272)
  - Windows는 셸 영구 off(README.md:116-117, sandbox.ts:53-57)
- 브라우저 툴은 playwright Page 위 얇은 래퍼(browser-page.ts:160-227): 셀렉터 1차→텍스트/라벨 폴백, 링크 목록 스크립트(58-70), 긴 콘텐츠 슬라이스(agent.ts:596-606).
- 셸 출력 head/tail(agent.ts:609-614), 명령 최대 10분 캡(agent.ts:585-589, shim.mjs:57-61, sandbox.ts:30-32), 출력 8MB 캡(shim.mjs:18).

### 1.4 BYOK 모델(provider)

- 유일 라우트: `POST {base}/chat/completions`(llm.ts:58) — OpenAI 호환 JSON, tools + tool_choice 'none'(llm.ts:61-66).
- 이미지(비전): user 메시지에 data URL 첨부 → image_url 블록 변환(optional, llm.ts:44-54), `OPENGROKBOT_VISION` 시 최신 스크린샷만 공급(agent.ts:185, 254-266).
- 재시도: RETRYABLE={408,409,425,429,500,502,503,504}(llm.ts:30), Retry-After 존중 30초 캡(llm.ts:35-42), 지수 백오프(llm.ts:31).
- 타임아웃: 120초 기본 + 호출자 AbortSignal 병합(AbortSignal.any, llm.ts:76-77, config.ts:84) — 시그널 중단 시 즉시 포기(llm.ts:100).
- 사용량: prompt/completion/cached 구조화(llm.ts:14, 119-125) → `usage` 테이블 집계(db.ts:325-342).
- 예산: 팀원별 일일 토큰(server.ts:289-296), 턴당 스텝 40/시간 20분(agent.ts:101-102), 예산 소진 시 정직한 중단+익일 재개(agent.ts:169-173).
- 오프라인 stub: `OPENGROKBOT_MODEL=stub` 스크립트 모델 — 키 없이 전체 흐름 e2e(llm.ts:185-327, README.md:125-129).
- **한계(핵심)**: 팀원별 모델/키/공급자 지정 불가(config.ts:63-66) — Anthropic/Codex/MCP 어댑터, 모델 라우팅, 벤더별 예산 전부 부재(gateway/src grep 0건, 의존성에도 없음 gateway/package.json:17-25).

### 1.5 실행 루프(턴)

- `runTurn`(agent.ts:110-201): 시스템 프롬프트 + 최근 40개 메시지(agent.ts:103, 139) → 스텝 루프 → 도구 실행 → 결과 재투입.
- 스텝 제한: 최대 40(agent.ts:101, 166), 턴 20분(agent.ts:102), 한도 도달 시 마지막 텍스트 정리(closeOut agent.ts:223-251).
- 도구 게이트: 왕자 워크플로 툴 화이트리스트 3종(approval/question/memory/routine/handoff, agent.ts:323-325) + 비목록 도구 거절(agent.ts:371).
- watch 턴: 자기 화이트리스트(WATCH_TOOL_NAMES tools.ts:333-336)로 이중 차단(agent.ts:341-347) — 도구 목록 자체에 없음.
- 도구 실패는 턴을 죽이지 않고 실패 텍스트를 모델로(agent.ts:335-339), 스텝당 activity 카드(최대 40스텝 agent.ts:268-313).
- 액션 로그: 툴 1회=1행(actions.ts:8-21), 타자 내용/URL 쿼리 레드액션(agent.ts:395-406).
- 턴 에러: 스레드에 `⚠️ hit an error`(agent.ts:193-196) → presence 'blocked'(server.ts:98, 232-237).
- `message_user`는 보고 kind로 구조화 로그만 허용(validateReportPayload report.ts:4-21) — 문법 위반 시 거절(agent.ts:348-352).

### 1.6 UI·배포·워크플로 녹화

- React SPA: 사이드바=조직도+presence(App.tsx:30-57), 스레드/보고칩/승인칩/질문칩/스크린샷칩/컴퓨터 패널(셸 스위치·루틴·규칙·액션 로그·워크플로).
- 마크다운 렌더: react-markdown, HTML 드롭+이미지 로드 금지(README.md:241-243).
- 상태: working/needs-you/stuck/done을 DB 기반으로 계산(server.ts:232-237, web presence.ts).
- 배포: launchd/systemd/Task Scheduler 템플릿(deploy/, docs/always-on.md:18-69), SSH 터널 원격 접근(always-on.md:71-82).
- 업그레이드: DB 사전 백업(db.ts:211-227), 봇 이미지 재구성(always-on.md:142-155).
- 워크플로 녹화(teach-mode 1차 슬라이스): 기존 페이지에 CDP 부착 → 시맨틱 로케이터 후보 배열 기록(workflow-recorder.ts:58-136), 이중 레드액션(workflows.ts:60-61, 105-133), 드래프트 검토 후 저장(server.ts:700-790), **리플레이/루틴 연동 미구현**(docs/workflows.md:3-5).
- 녹화 중 팀원 레인 홀드: `turns.hold`(server.ts:655-656) — 녹화와 턴 충돌 방지.

### 1.7 관계 요약 (TalkAbout 대조)

- TalkAbout이 필요로 하는 "대화형 에이전트 회의 + 코드 편집 + 산출물 발행"을 OGB는 **회의가 아님(보고), 편집이 아님(숙제), 발행이 아님(메시지)** 으로만 수행 → §2.7, §4 참고.
- 한편 "한 턴 = 한 컴퓨터 = 한 스레드"의 세밀한 동기화/멱등/중단 설계는 TalkAbout 실행 레이어의 참조가 될 만함(turns.ts, pool.ts, sandbox.ts).

### 1.8 데이터 스키마 지도(핵심 테이블)

- `bots`: 팀원 정체(신규 컬럼 shell_enabled는 로컬 셸 스위치, db.ts:43-50).
- `threads`/`thread_members`: 대화 단위(dm:1멤버/group:다수), 마지막 메시지·pending 계산은 질의(server.ts:247-256, db.ts:381-390).
- `messages`: kind 13종 + payload JSON(approval/질문/메모리/루틴/로그인 요청 구조화, db.ts:30-38, 64-72).
- `approvals`/`approval_rules`: 승인 상태(개수 필드 items/approved_items, db.ts:74-85, approvals.ts:18-35).
- `routines`: cron/웹훅키/mode(act|watch), scheduler 재로드 원천(db.ts:86-96, routines.ts:5-21).
- `usage`: 팀원·스레드별 토큰 3종 집계(db.ts:97-106, 325-342).
- `actions`: 툴 호출 1회=1행, 90일 보존(db.ts:160-169 영역, actions.ts:10-21).
- `workflows`: 브라우저 트레이스 보관(버전/검증/레드액션, db.ts:167-169, workflows.ts:32-61).
- 한계 재확인: **딜리버러블 아티팩트·버전·diff·리뷰 상태에 대응하는 테이블이 스키마에 없음**(db.ts:42-107).

---

## §2 협업·수명주기

### 2.1 사람 → 팀원: 메시지 → 턴

- 진입: `POST /api/threads/:threadId/messages`(server.ts:1021-1056).
- 꿰뚫는 순서(에 202 즉시 반환, server.ts:1054):
  1. 열린 질문 카드 자동 닫기(server.ts:1050)
  2. 메시지 DB 커밋+WS 브로드캐스트(server.ts:1051-1052) — 봇이 바빠도 즉시 보임
  3. `waiting` 맵 적립(server.ts:1053)
  4. `startTurn` enqueue(**alreadyStored=true**, 메시지 재저장 방지 agent.ts:128-130)
- 여러 메시지가 봇이 비는 동안 쌓이면 **하나의 턴에서 전부 답변**(server.ts:313-319, 1049-1053).
- 턴 시작 고정: 대기 메시지 확정(agent.ts:203-208) → 컴퓨터 사전 워밍(server.ts:320) → 로그인 창 숨김(운영자 발언=로그인 완료 신호, server.ts:321-323) → persona/메모리/루틴/스킬 로드(server.ts:325-329).

### 2.2 대기열·직렬화·coalesce·중단

- bot당 1레인 = 1턴 직렬(turns.ts:1-4) — 사람/루틴/웹훅/승인 시드/질문 답변/핸드오프 모두 같은 레인(server.ts:312, 428).
- coalesce: 같은 coalesceKey의 대기 턴 존재 시 병합(`twin.done` 반환, turns.ts:57-72) — 같은 스레드의 미답변 메시지는 한 턴에서 처리.
- `stopThread`: 실행 턴 abort(대기 턴 폐기) + 영향 턴 수 반환(server.ts:976-982, turns.ts:73-92) — 그룹 라운드는 rounds+turns 이중 정지(server.ts:979).
- 중단 의미: abort는 턴 사이/도구 사이 검사(agent.ts:165, 180), 실행 중 셸은 프로세스 그룹 SIGKILL(shim.mjs:84-95, sandbox.ts:188-195).
- 체크포인트/롤백 없음 — 재개는 사용자 "continue"(agent.ts:230)로 수동.
- 명령 수명: 기본 60초→최대 10분(agent.ts:581-589), 출력 8MB 캡(shim.mjs:18, sandbox.ts:32).

### 2.3 승인/질문/멱등성

- 승인 흐름: `hold_for_approval`(agent.ts:459-491) → `approvals` 행 pending(approvals.ts:162-189) → 칩(종류/대상/항목 승인 목록, agent.ts:477-487) → 운영자 결정(server.ts:998-1019).
- 멱등: `UPDATE ... WHERE status='pending'` 원자 플립(approvals.ts:206-212) — 이중 클릭 409, 판정 후 재결정 409(server.ts:1009).
- 단축키: 👍 하나 = 최신 pending 승인(server.ts:1041-1047, approvals.ts:215-217).
- 배치(최대 200항목·200자, approvals.ts:38-46): 항목별 승인 + "정확히 그 항목만" 시드(server.ts:448-530, 522-530).
- standing rule: 1팀원·1종류·1정확한 목적지·만료(1h/24h/영구, approvals.ts:15) — purchase/booking/other는 영구 거부(approvals.ts:12-13, 158).
- 규칙 대상은 모델에 비공개(agent.ts:720-738 참조, 488-489), 적용 조건: 운영자가 연 턴+웹훅 페이로드 부재(agent.ts:141-142, server.ts:352-357).
- 질문 카드(agent.ts:492-505): 원자 클로징(server.ts:874-877), 모델의 답변은 카드가 이미 닫히면 무시(server.ts:277-278).

### 2.4 그룹·핸드오프

- 그룹 라운드(server.ts:427-442): 운영자 질문 → 멤버 병렬 자기 패치 답변(각자 bot 레인) → chief가 dispatch 표+1문장 수렴(server.ts:434-440).
- 그룹 라운드 자체 레인 직렬(server.ts:425-426) — 두 라운드 혼선 방지.
- 핸드오프: `message_bot`(agent.ts:563-575) → allowlist(a2a.ts:16-20) + 최대 2홉(a2a.ts:2, agent.ts:566-568).
- 웹훅→팀원: 웹훅 턴에 규칙 불인정(server.ts:141-142) — 외부 발언으로 상시 승인 우회 불가.

### 2.5 루틴/웹훅/워치

- 루틴 3종: cron(영어 설명 describeCron routines.ts:40-54) / 웹훅(팀원별 144비트 키, routines.ts:62-71, 75-81) / 워치(읽기 전용+침묵, server.ts:551-558).
- GitHub 웹훅 서명: HMAC-SHA256 상수시간 비교(routines.ts:111-117), 본문 1MB 도착 중 거절(server.ts:95, 101-121).
- 중복 실행 금지(routinesInFlight server.ts:197, 544; 웹훅 409 server.ts:916).
- 스케줄러 재로드: 게이트웨이 재시작 시 DB에서 복원(main.ts:93-101).

### 2.6 실패·재시작·유휴·예산

- 턴 실패 → 스레드 에러 표기(agent.ts:193-196) → presence 'blocked'(server.ts:84-87, 232-237).
- 재시작: 컨테이너 unless-stopped+데몬 keep(entrypoint.sh:13-22), CDP 재연결(세션 캐시 폐기 browser-page.ts:106-153), 로그인/파일 유지(README.md:97-99).
- 유휴: 60분 기본 후 정지, 시청/작업 중은 제외(main.ts:167-183, pool.ts:98-117, screen.ts:106-119) — 다음 사용 시 자동 재기동.
- 예산: 턴 중 `checkBudget`(server.ts:344, agent.ts:169-173) → 소진 시 cleanClose+익일 재개(server.ts:289-296).
- **예약/영수증 없음**: "누가 언제 무엇을"의 명시 엔티티 부재 — 레인 대기열(암묵적)·approval_id(결정 기록)뿐.

### 2.7 TalkAbout 룸/예산/승인 모델과의 차이

| 축 | OpenGrokBot | TalkAbout(목표) | 근거 |
|---|---|---|---|
| 참여 | 1인 운영자(토큰 1개) | 초대 전용 룸, 다중 참여자 | docs/comparison.md:36 |
| 대화 | dm/그룹 스레드 | 룸 = 토론 + 산출물 컬렉션 | db.ts:43-57 |
| 예산 | 팀원별 일일 토큰 1종 | 잔액·실행 예약·영수증·벤더별 분리 | server.ts:289-296 |
| 승인 | 외부 방출 보류 도어 | 워크스페이스 편집 승인·부분 승인 | approvals.ts:9-15 |
| 산출물 | 메시지+파일+스크린샷 | 버전 달린 분석/변경제안 통합 diff | db.ts:42-107 |
| 멀티 모델 | 전역 OpenAI 호환 1개 | 벤더별 에이전트 어댑터 | llm.ts:56-129 |
| 도구 | 켜짐/꺼짐(컴퓨터 기능) + watch 화이트리스트 | 역할→도구 범위(읽기/제안/편집) | server.ts:224-230, tools.ts:333-336 |

### 2.8 결과 반환 독트래킹: 메시지 kind → UI 칩

- `report`: 보고 칩(✓ system → result · count, agent.ts:348-352, web ScreenshotChip/Thread 컴포넌트).
- `screenshot`: 스레드 내 이미지 증거(server.ts:984-996, screenshots.ts:18-29).
- `activity`: 스텝별 액티비티 카드(웹훅/루틴 포함, agent.ts:268-313).
- `approval_request`/`approval_resolved`: 승인 칩+결정 흔적(server.ts:491-499, 998-1019).
- `question`: 질문 카드(선택지/멀티, agent.ts:492-505, server.ts:874-877).
- `memory_updated`/`routine_created`: 메모리 diff/루틴 등록 칩(agent.ts:520-559).
- `login_request`: 로그인 요청 칩 → 테이크오버 진입(agent.ts:577-583, server.ts:812-822).
- `bot_ref`: 팀원 간 핸드오프 스탬프(server.ts:394-421).
- `webhook`: 외부 데이터 카드(페이로드 펜스, server.ts:913-915, agent.ts:740-764).
- TalkAbout 대응안: 동일한 "1종류 = 1렌더러" 규율을 **딜리버러블 타입(분석/변경제안 버전/diff/영수증)으로 확장**하는 것이 자연스러운 이식 경로.

---

## §3 보안·품질

### 3.1 자격증명·시크릿

- 게이트웨이 토큰: 24바이트 자동 생성, 파일 0600(auth.ts:37-41), Windows ACL 재설정(icacls reset+inheritance:r+사용자 SID, auth.ts:52-66).
- 보호 경로: `/api/` `/ws` `/vnc/` `/screen/`(auth.ts:76-77).
- 삼중 검사: 토큰 + [상태 변경/WS는] Origin(같은 호스트·포트 비교 auth.ts:86-106, allowedOrigins 화이트리스트) + [본문 비GET은] `application/json` 1종(auth.ts:139-142, 프리플라이트 미응답).
- 로그인: 1회용 링크 `/auth?token=…` → HttpOnly+SameSite=Strict 쿠키, 1년(loginHandler auth.ts:148-161).
- 봇 키: env에서만(README.md:157, config.ts:65) — 디스크 미기록.
- 봇 워크스페이스에 토큰 없음 → 봇 자가 승인 불가(docs/security.md:14).
- 컨테이너 시크릿: 팀원별 shimToken(24B)+VNC 패스워드(A-Za-z0-9 8자, containers.ts:34-39), VNC 패스워드는 URL fragment(서버 로그 비유입, screen.ts:17-20).

### 3.2 샌드박스 범위

- 컨테이너(강함): 자원 한도+PidsLimit 512+no-new-privileges+init(containers.ts:66-77), 컨테이너 간 통신 차단(containers.ts:145-159), 포트 127.0.0.1 게시(containers.ts:47), shim/CDP/VNC 팀원별 시크릿(containers.ts:34-39).
- 로컬(중간): 파일 유폐(computer-local.ts:62-96, O_NOFOLLOW 210, 16MB 17), 셸=Seatbelt/bubblewrap(홈·데이터 히든, TOOLCHAIN_DIRS 예외 sandbox.ts:38-42, 94-132), **자기검증 없으면 켜기 거부**(server.ts:793-809, sandbox.ts:241-272).
- **명시 한계(문서 공개)**: 로컬 셸은 네트워크/시스템 서비스/Mach 서비스 도달 가능, 자원 상한 없음(docs/security.md:85-93), Windows 셸 off(README.md:116-117, server.ts:798-806), 프로필은 일반 파일(docs/security.md:94-95).
- 방출(전송/게시)은 **패널이 아닌 지시**(docs/security.md:36-40) — 승인 헌법은 프롬프트(APPROVAL_DISCIPLINE prompts.ts:53-67) 의존, 실 브라우저로 봇이 Send 클릭 가능.

### 3.3 주입면

- 웹훅 펜스: 페이로드(fallback: 봇의 것으로 라벨) + SHA-256 태그 + `│` 줄 프리픽스 + "운영자 지시 아님" 선언(agent.ts:740-764).
- 규칙 불인정 조건: 비운영자 origin(routine/webhook/relay) 또는 웹훅 페이로드 노출 중(agent.ts:141-142), 규칙 명세는 모델에 미공개(agent.ts:488-489).
- 웹훅 본문: 1MB 도착 중 거절(server.ts:95, 101-121), 모델 노출 16k자 상한(server.ts:94, 135), 외부 데이터 카드로 스레드 표시(server.ts:913-915).
- 도구 화이트리스트 이중 확인(도구 정의(프롬프트) 와 실행 게이트, agent.ts:330-347).
- 스크린 페이지는 게이트웨이 소스에서만(mountScreenProxy 화이트리스트 screen.ts:122-148) — 봇이 수정 불가(게이트웨이 소스 외 서빙 거부).
- MEMORY: 워크스페이스 밖(diff 칩으로만 변경, memory.ts:8-14, 55-71, 89-112) — 봇이 자기 컴퓨터에서 수정 불가.
- 마크다운: HTML 드롭+이미지 미로드(README.md:241-243) — XSS면 차단.
- 잔여 위험(문서 수준 인정): 읽은 페이지는 데이터 취급(docs/security.md:50-52) — 승인 시드는 진짜 승인만 재진술(agent.ts:508-517, 522-530).

### 3.4 테스트·품질

- vitest: 게이트웨이 522개·웹 91개 테스트 케이스(소스 `it|test` 검색 기준), 모듈별 테스트 파일(gateway/test/agent*.test.ts, approvals/auth/computer*/sandbox/workflows 등).
- 실기반 통합: smoke-docker/smoke-local(scripts/), workflow-recorder.chromium.test.ts(실 Chromium).
- CI 3OS: 타입체크+테스트+빌드+로컬 컴퓨터 스모크+Windows 스크립트 파싱/설치/제거+`pnpm audit --audit-level high`(ci.yml:8-87)+이미지 빌드 검사(ci.yml:89-98).
- 문서 품질: "enforced/규정/on you" 3분리(security.md:7-67), 비교표에 자기 결점 명시(comparison.md:66-69, 79-85).
- 인상적인 관행: 멱등 테스트, 마이그레이션 백업(db.ts:211-227), 이중 레드액션(workflows.ts:60-61, 105-133), 유휴/경합 테스트(server.test.ts, pool.test.ts).
- 결정 기술: WHERE-condition 플립(approvals.ts:206-212)·`{const}` 없는 원자 상태 전이·상황 판정은 DB 스냅샷 기반(server.ts:247-256).

### 3.5 결함·결핍 (구체, file:line)

1. **멀티 모델/멀티 벤더 불가**(핵심): 전역 모델·키·엔드포인트 1조각(config.ts:63-66, llm.ts:56-129), 어댑터 레지스트리·모델 라우팅·벤더별 예산 부재(grep 0건).
2. **컨텍스트 40개 메시지 창**: HISTORY_LIMIT=40(agent.ts:103, 139), 서버도 스레드 200개 한정(db.ts:302-306) — 장기 협업에서 초기 합의/산출물 참조 탈락, 요약 없음.
3. **산출물 계층 부재**: 메시지 13종+워크스페이스 파일뿐, 아티팩트/버전/diff/리뷰 상태 테이블 없음(db.ts:42-107), `workflows`는 브라우저 트레이스 보관 전용(db.ts:160-169), write_file은 전체 내용 재전송(agent.ts:645-647).
4. **예약/체크포인트/영수증 없음**: 중단 후 "continue"(agent.ts:230) 수동 재개, 실패 재시도 백오프 없음(agent.ts:193-196), 실행 약속·완료 영수증 엔티티 부재(2.6).
5. **그룹 "토론"은 토론이 아님**: 병렬 자기 보고+chief 수렴(server.ts:427-442)뿐 — 반박/투표/의견 교환 없음, 병렬 답변 전부가 40창(agent.ts:103)에 경합.
6. **로컬 샌드박스 한계**: 네트워크 미제한·자원 상한 없음(docs/security.md:85-93), Windows 셸 불가(README.md:116-117) → 워크스페이스 편집 시나리오 재사용 불가.
7. **키/벤더 분리 불가**: 예산=토큰 합계뿐(server.ts:289-296, db.ts:325-342), 벤더/팀원 가격 차이 무관심.
8. **오류 문자열 스레드 직행**: 턴 에러(agent.ts:195)·툴 실패(agent.ts:335-339)·LLM 에러(agent.ts:105-106) 원문 노출 — 소극적 주입면(문서도 미해결 명시, security.md:50-52).
9. **웹훅 중복 기억이 메모리 내**: deliveries 맵(최대 500, server.ts:91, 188-195) — 재시작 시 리플레이 1회 재실행 가능(저위험).
10. **권한/멀티 유저 없음**: 토큰 1개(auth.ts:128-145) — "누가 승인할 수 있는가" 모델 부재, 룸 공유 시나리오 미지원.
11. **알림 채널 부재**: 결과는 같은 스레드뿐(server.ts:549-575) — 외부 알림/수신 채널 없음(로드맵 README.md:223).
12. **산출물 검증 프리미티브 없음**: 보고(validateReportPayload report.ts:4-21)·워크플로 검증(workflows.ts:247-269)은 있어도 **코드/문서 산출물의 구조 검증(스키마/diff·린트)** 개념 부재.

### 3.6 이식 후보 관행 상세 (긍정 목록)

- 명확한 실패 의미: `TurnOutcome` 5종(replied/stopped/error/budget/limit, agent.ts:24) — TalkAbout 턴/예약 결과 코드열로 이식 가능.
- 판정 스냅샷: presence는 DB 기반 재계산(server.ts:247-256) — 연결 끊김에도 상태 유실 없음.
- 미사용 자원 정리: 유휴 회수+시청 제외(main.ts:167-183, screen.ts:106-119) — TalkAbout 실행자 풀에 그대로.
- DB 업그레이드 안전: 사전 백업+하위 호환 거부(db.ts:211-227, 229-246) — 작업장 스키마 진화 규칙으로 이식.
- 경로 안전: 파일 경로 2중 검증(문자열+심링크, computer-local.ts:62-96) — 공동 워크스페이스 파일 서빙의 표준.
- 비밀 최소화: 키 env/파일 0600/VNC fragment/auth.ts:37-41 — 자격증명 위임 설계 참고.

---

## §4 개선점 도출 (대상: 다중 업체 AI 에이전트 토론+코딩+산출물 작업장)

### [즉시]

1. **[llm.ts:56-129 → 공통 LLM 계약+재시도/타임아웃/사용량 공통 계층]**
   - 좋은 점: `chat(messages, tools, {signal}) → {content, toolCalls, usage}` 단일 계약(llm.ts:22-24), RETRYABLE/Retry-After/백오프/타임아웃 병합/시그널 중단/usage 구조화가 한 지점에 집중.
   - 적용안: TalkAbout 벤더 어댑터(MCP/http) 뒷단 공통 계층으로 추출 → 벤더마다 동일 재시도·타임아웃·사용량 계약 보장.
   - 적용안: stub 스크립트 모델(llm.ts:185-327)을 따라 벤더 미연결 시 오프라인 e2e(발제→토론→산출물→승인) 재현.
   - 영향: 벤더 추가 시 품질 보장·테스트 가능. 비용: 소~중. 위험: 낮음.
2. **[turns.ts:32-114 → 실행 직렬화+coalesce+중단 계약]**
   - 좋은 점: bot별 1레인 직렬(turns.ts:1-4), coalesce 병합(turns.ts:57-72), stopThread의 abort+폐기+카운트(server.ts:976-982), 레인 홀드(turns.ts:93-110), 셸 프로세스 그룹 kill(shim.mjs:84-95, sandbox.ts:188-195).
   - 적용안: TalkAbout 실행 예약 상태 머신(예약→도착→실행→영수증)에 직렬화·병합·중단 규칙 이식 + 예약 ID·영수증 ID를 승인/산출물 버전과 결합.
   - 영향: 공동 편집 경쟁·이중 실행 제거. 비용: 소. 위험: 낮음.
3. **[approvals.ts:162-267, server.ts:998-1019 → 멱등 결정+부분 승인+규칙 최소 범위]**
   - 좋은 점: WHERE-condition 플립의 409 멱등(approvals.ts:206-212), 배치 항목(최대 200/200자, approvals.ts:38-46), "정확히 그 항목만, 추가 금지, 첫 실패 정지"(server.ts:522-530).
   - 적용안: TalkAbout 워크스페이스 편집 승인(Codex 변경 적용/거부/파일 단위 선택)에 동일 패턴 — 승인 대상=산출물 버전(diff 핑거프린트), 결정=영수증 결합.
   - 영향: 승인 게이트 신뢰·감사. 비용: 소. 위험: 낮음.
4. **[agent.ts:740-764, server.ts:895-927 → 외부 데이터 펜스+웹훅 게이트]**
   - 좋은 점: SHA-256 태그+│ 줄 프리픽스 펜스(agent.ts:745-748, 751-764), 규칙 불인정 조건(server.ts:141-142), 본문 도착 중 한도(server.ts:95, 101-121).
   - 적용안: TalkAbout 웹훅/타 벤더 에이전트 발언 수신에 동일 마킹+허가 불인정 — 외부 발언은 결코 운영자 지시로 위장 불가.
   - 영향: 교차 벤더 주입면 축소. 비용: 저. 위험: 낮음.
5. **[tools.ts:333-336, agent.ts:341-347 → 역할→도구 바인딩 이중 게이트]**
   - 좋은 점: watch는 도구 목록 자체에 없음+없는 도구 호출 거절(agent.ts:341-347) — 프롬프트 차원+실행 차원 이중 강제.
   - 적용안: 룸 역할(참가자=읽기+발언/제안자=+편집/실행자=+커밋) → 도구 범위 바인딩으로 일반화.
   - 영향: 역할 위임 안전. 비용: 소. 위험: 낮음.
6. **[memory.ts:8-14, 55-71, 89-112 → 룸 메모리 diff 가시성+외장 저장]**
   - 좋은 점: 메모리=워크스페이스 밖(memory.ts:8-14), 변경=diff 칩(67-71), 삭제=모호 시 후보 반환(89-112).
   - 적용안: TalkAbout 룸 메모리(합의·컨벤션) 편집에 diff 강제 표시+저장소 물리 경계 — 몰래 변경 불가.
   - 영향: 협의 기록 감사성. 비용: 소. 위험: 낮음.
7. **[auth.ts:128-145, screen.ts:122-148 → 웹 보호 삼중+소스 분리]**
   - 좋은 점: 토큰+Origin(86-106)+JSON 1종(139-142), noVNC JS 화이트리스트 서빙(screen.ts:137-142).
   - 적용안: TalkAbout 웹 GUI/WS에 동일 레이어+산출물 미리보기도 게이트웨이 소스 서빙 규칙.
   - 영향: 본체-안전. 비용: 소. 위험: 낮음.
8. **[workflows.ts:4-299 레드액션+검증 → 산출물 검증 파이프라인 선례]**
   - 좋은 점: 서식 v1 강검증(workflows.ts:49-57, 247-269), 이중 레드액션(105-133; 패턴+엔트로피 78-89), 검토 후 저장(server.ts:700-760).
   - 적용안: TalkAbout 분석/변경제안(diff) 스키마 검증+민감 값 플로우 파이프라인에 "검증-전-저장/검토-전-배포" 게이트 적용.
   - 영향: 산출물 신뢰. 비용: 중. 위험: 낮음.

### [단기]

9. **[computer.ts:14-44, backend.ts:16-24 → 실행자 백엔드 추상화+재생명령 계약]**
   - 좋은 점: BotComputer 인터페이스가 유일 진실(agent/server/pool 모두 인터페이스만 사용), ping→재생성(pool.ts:34-45), 유휴 회수(시청/작업 제외, pool.ts:98-117), 라벨 기반 재구성(containers.ts:200-214).
   - 적용안: TalkAbout 실행자(로컬 워크트리/원격/컨테이너/에이전트 프로세스) 인터페이스에 동일 분리+작업장 소유권 계약(편집/커밋 범위) 승격.
   - 영향: Claude Code/Codex를 룸의 "실행자"로 탑재. 비용: 중. 위험: 중(권한 모델 정교화).
10. **[scheduler.ts:15-54, routines.ts:62-81 → 산출물 예약/완료 재진입으로 일반화]**
    - 좋은 점: 크론/웹훅/워치 한 진입(server.ts:536-583), 중복 실행 금지(server.ts:197, 544), 재시작 재로드(main.ts:93-101).
    - 적용안: 예약=산출물 마감/리뷰 데드라인 스케줄, 영수증=실행 결과 해시+승인 연결.
    - 비용: 중. 위험: 중.
11. **[sandbox.ts:44-50, 241-272 → 편집 샌드박스 계약+자기검증 강제]**
    - 좋은 점: "자기검증 없으면 켜기 거부"(server.ts:793-809) — 실패 시 안전 모드 강제 원칙.
    - 적용안: TalkAbout 로컬 편집(Codex) 실행기에 쓰기 범위=산출물 디렉터리+자기검증 표준, git diff 서명 결합.
    - 비용: 중. 위험: 중.

### [중기]

12. **[llm.ts(단일 라우트) → 벤더 어댑터 레지스트리+모델 라우팅 확장]**
    - 좋은 점: Tools-first 계약(tool_calls→실행→결과 재투입, llm.ts:61-66, agent.ts:176-186)이라 확장점 명확.
    - 적용안: 어댑터 1등록(OpenAI 호환/Anthropic/Claude Code CLI/Codex/BYOK http/MCP)+룸·팀원·역할별 모델 선택+벤더별 키/사용량/예산 분리.
    - 영향: 핵심 목표 완성. 비용: 대. 위험: 중(행동 차이·툴 규약 정규화).
13. **[server.ts:427-442(그룹) → 룸 토론 워크플로: 발제→견해→반박(제한)→집계→판정→산출물 버전]**
    - 좋은 점: 발언 순서와 수렴 프롬프트가 코드에 있음(server.ts:434-440).
    - 적용안: 단계 상태 머신으로 승격, 각 단계가 산출물 버전 참조(diff 핑거프린트)+영수증 결합.
    - 영향: 토론→코딩→산출물 통합. 비용: 대. 위험: 중.
14. **[agent.ts:103(40행 창) → 산출물 참조 기반 컨텍스트]**
    - 좋은 점: 슬라이스 계약(sliceForModel agent.ts:596-606), 구조화 메시지→컨텍스트 표현 선례(agent.ts:766-788).
    - 적용안: 산출물 트리+버전+diff 참조만 프롬프트에 + 최신본만 fetch. 비용: 중. 위험: 중.
15. **[server.ts:289-296 → 벤더/팀원별 예산 분리 집계]**
    - 좋은 점: usage 구조화(prompt/completion/cached, db.ts:325-342)+턴 한도+정직한 중단(agent.ts:169-173).
    - 적용안: TalkAbout 잔액/예산을 벤더·모델별 분리 집계. 비용: 중. 위험: 중.

### [제외]

16. noVNC 테이크오버·CDP 스크린캐스트·브라우저 자동화 세트(computer-local.ts:156-191, screen.ts, browser-page.ts) — 숙제 대행 UX, 공동 코딩 작업장과 목적 다름(학습 가치는 '소스 서빙 분리' 원칙뿐).
17. 봇 전용 계정·브라우저 프로필 사일로(README.md:94-96), "게이트웨이에 비밀번호 없음" 원칙(docs/security.md:14) — 공유 워크스페이스 편집 전제와 상충, 채택 안 함.
18. always-on 데몬화(launchd/systemd/Task Scheduler, deploy/) — TalkAbout 서버에 유사 영역 존재, 중복 도입 불필요(참조만).

---

## §5 미확인·한계

- **클론 이력**: 지시 "full history"와 달리 실제 클론은 **depth-1(커밋 1개, `git rev-list --count`=1, `cf6f17c`)**, `main`만 존재 — 커밋별 diff 미확인. release 이력은 CHANGELOG.md로 대체(CHANGELOG.md:7-354). 필요 시 전체 이력 재클론.
- **라이선스**: 로컬 LICENSE 열람으로 MIT 확인(LICENSE:1-21). "GitHub API 확인 2026-11-05" 표기는 지시 문구 기준이며 본 세션에서 GitHub API는 직접 호출하지 않음(네트워크 도구 미사용).
- **빌드/설치/실행/테스트 금지 지시 준수** — 타입체크·테스트 수행 결과, Docker/브라우저 런타임, CI 결과 미검증. 테스트 케이스 수(522/91)는 소스 `it|test` 검색 기준.
- **문서 의존 가정**: Grok Bot/Meta Muse/OpenAI dots 최신 상태(2026-10-14~11-03 표기, docs/comparison.md:3-6)는 저장소 문서 기준. launchd/systemd/Windows Task 동작은 정적 해석.
- 웹 UI 렌더·시각 동작(스크린샷, 패널 상호작용)은 미확인 — web/src 코드 정적 검토+`web/src/api.ts`-server.ts 교차 확인 수준.
- TalkAbout 루트 코드와의 정량 비교·정합성 점검은 이 문서 범위 밖(별도 R-시리즈 참조).
