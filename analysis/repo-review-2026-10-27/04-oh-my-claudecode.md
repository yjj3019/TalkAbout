# 04. oh-my-claudecode 정적 소스 리뷰 — TalkAbout 적용 판단

- 기준일: 2026-10-27 (한국 시간)
- 대상: **공식 `Yeachan-Heo/oh-my-claudecode`**, 커밋 **`e10206d8789b0ac4e5214eddbfbd203c1b757dea`**. 전달된 Codewhale 링크는 이 이름의 공식 저장소가 아니므로 분석 대상으로 사용하지 않았다.
- 로컬 HEAD와 origin을 읽기 전용 Git 메타데이터 조회로 확인했다. 저장소 프로그램·테스트·설치 스크립트 실행, 의존성 설치, 플러그인 로드, 서버 시작, 커밋은 하지 않았다. 아래 테스트 평가는 **테스트 소스를 읽은 결과**이지 통과 보고가 아니다.
- TalkAbout의 **R3 서버 fencing·reservation/start 분리·uncertain retirement 및 R4 DPAPI는 완료 전제**다. 이를 미구현 과제로 다시 제안하지 않는다.

## 1. 결론

**선별 설계 참고는 추천, 런타임/플러그인 통째 도입은 비추천**이다. OMC는 단순 역할 프롬프트 모음이 아니다. 파일 기반 task claim/version/lease, durable mailbox/dispatch, owner identity, checkpoint adoption과 실행 게이트를 실제 TypeScript 코드로 구현한다. 그러나 다음 세 경계를 혼동하면 위험하다.

1. **상태 전이 강제와 작업 내용의 진실성은 다르다.** claim token이 맞아도 결과가 실제 코드 변경·테스트 성공임을 자동 증명하지 않는다.
2. **mailbox notification/delivery와 수신자의 작업 완료는 다르다.** 전송 상태와 task 완료를 별도 기록하는 점을 취하되 exactly-once 외부 효과 보장이라고 해석하면 안 된다.
3. **worker permission은 주로 프롬프트 규칙이다.** 기본 CLI 실행 인자에는 승인·sandbox 우회가 포함된다. 이를 TalkAbout의 서버 권한·소유 계정·native work-profile 경계 대신 사용할 수 없다.

TalkAbout에 가장 유용한 후속은 ‘에이전트끼리 더 많이 대화’가 아니라 **실제 코딩/분석 과제 → 구조화 산출물 → 독립 검토 → 제한된 수정 루프**다. 현재의 실제 작업 opt-in을 확장하되, 명령 실행/코드 적용 권한을 방의 대화 권한으로부터 분리해야 한다.

## 2. 식별·라이선스·배포/네이티브 제약

| 항목 | 고정 소스에서 확인한 사실 | 적용 의미 |
|---|---|---|
| 버전/이름 | [패키지 메타데이터:1–22](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/package.json#L1-L22>)의 npm 이름은 `oh-my-claude-sisyphus`, 버전 `5.7.19`, CLI alias는 `omc` 등이다. [플러그인 메타데이터:1–10](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/.claude-plugin/plugin.json#L1-L10>)도 `5.7.19`, 공식 저장소를 명시한다. | 표시 이름·npm 패키지명·공식 owner를 구분하여 공급망 원장에 기록한다. |
| 라이선스 | [MIT 전문:1–21](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/LICENSE#L1-L21>): 2025 Yeachan Heo, 복사/상당 부분에 저작권·허가문 보존, 무보증. | 설계 참고와 코드 복사는 구별한다. 복사 시 고지 보존 및 하위 의존성 라이선스 별도 검토. MIT는 provider 계정/API/CLI 이용권을 부여하지 않는다. |
| 런타임 | [의존성·엔진:96–138](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/package.json#L96-L138>): Claude Agent SDK, MCP SDK, `better-sqlite3`, ast-grep NAPI, Node `20.x || 22.x … 26.x` 명시. | Python 기반 TalkAbout에 무비용으로 끼우는 라이브러리가 아니다. Node/native ABI 및 CLI별 인증·버전 검증이 추가된다. |
| 설치 부작용 | [scripts:50–94](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/package.json#L50-L94>)에 native build, bridge 생성, `postinstall` 존재. [플러그인 등록:61–69](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/.claude-plugin/plugin.json#L61-L69>)은 skill/MCP/commands 연결. | 이번 리뷰에서는 어느 것도 실행/로드하지 않았다. 도입 검토 자체가 플러그인 신뢰 승인으로 이어져서는 안 된다. |
| native backend | [native loader:29–49](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/graph/runtime/native-contained-fs.ts#L29-L49>)는 플랫폼/아키텍처별 `.node`를 로드하며 없으면 graph/team 전에 build 필요 오류를 낸다. | 단순 TypeScript portability를 주장할 수 없다. 실제 빌드/플랫폼 검증은 미실시. |
| CLI 실행 모델 | [provider 계약:186–256](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/model-contract.ts#L186-L256>)에서 Claude 승인 우회, Codex 승인·sandbox 우회, Gemini yolo. Codex는 exec가 아니라 지속 TUI pane을 요구한다. [Cursor 계약:301–321](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/model-contract.ts#L301-L321>)은 `--force --trust`. | TalkAbout의 fresh contained process/exec-json 계약과 다르다. 이 인자를 가져오면 현재 안전 프로파일이 후퇴한다. |

Windows 제약도 분리해야 한다. OMC의 tmux 엄격 identity 양성 fixture는 [테스트:40–65](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/__tests__/mailbox-notification-guard.test.ts#L40-L65>)에서 Linux/macOS에 한정되어 있다. 이를 근거로 OMC 전체의 Windows 미지원이라고 단정하지는 않지만, **TalkAbout Windows supervisor와 동등한 동작이 검증되었다고도 말할 수 없다**. TalkAbout 자체의 [native 런타임:1–12](<../../talkabout/agent_runtime.py#L1-L12>)은 Windows kill-on-close와 turn별 contained process, 불확실/실패 인스턴스 재사용 금지를 명시한다.

## 3. durable task·lease·heartbeat: 실제 구현과 한계

### 3.1 실제 강제되는 부분

[task 생성/의존성 정규화:95–155](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/state/tasks.ts#L95-L155>)는 task 파일을 원장으로 두고 `depends_on`/legacy 필드 일치와 선행 task completed를 검사한다. [claim:170–227](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/state/tasks.ts#L170-L227>)은 다음을 수행한다.

- worker 등록 확인 → task lock 안에서 재조회 → expected version 및 dependency 재확인.
- terminal/in-progress/recovery reservation 충돌 거부.
- UUID claim token, owner, **15분 lease**, 선택적 launch-attempt identity와 version 증가를 atomic write.

[종료 전이:287–369](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/state/tasks.ts#L287-L369>)는 현재 상태·owner/token·lease 만료를 검사하여 완료/실패로 전이한다. 이는 ‘완료라고 말하라’ 프롬프트보다 강한 기계적 규칙이다. 다만 task 파일 기록 뒤 event append가 실패해도 task 전이는 성공으로 반환될 수 있고, completion marker도 best-effort다. **task/event/marker 전체를 하나의 원자 트랜잭션으로 오해하지 않아야 한다.**

### 3.2 heartbeat는 lease와 같지 않다

[heartbeat:28–97](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/heartbeat.ts#L28-L97>)는 bridge poll 시 파일을 갱신하고 `lastPollAt` 신선도를 검사한다. malformed/missing은 dead로 읽힌다. 이 함수 자체는 task claim lease를 갱신하지 않으며, poll 생존은 코드 작업 진척이나 외부 호출 완료 증명이 아니다. [health 집계:44–85](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/worker-health.ts#L44-L85>)도 heartbeat·tmux·audit를 조합한 관측이다. 복구 saga의 `unknown` liveness와 대시보드의 dead 표시를 동일 권한으로 사용해서는 안 된다.

### 3.3 TalkAbout과의 차이

[서버 mutation:276–341](<../../talkabout/core.py#L276-L341>)에는 이미 assignee, generation/membership revision, lease, start 및 complete gate가 있고 [worker 실행 경로:530–565](<../../talkabout/worker.py#L530-L565>)는 reservation/start 응답을 journal에 저장한 뒤 `execute:true`일 때만 provider로 간다. [heartbeat와 결과 보존:568–636](<../../talkabout/worker.py#L568-L636>)은 heartbeat 실패 시 취소, provider 결과의 선저장, post-call uncertain 처리와 게시를 구별한다. 따라서 OMC의 15분 파일 lease를 복사하는 것은 개선이 아니다. 추가할 만한 것은 **task 의존성·산출물 버전·검토자 역할의 명시적 모델**이며 R3 fencing 교체가 아니다.

## 4. mailbox·dispatch·delivery: 좋은 분리, exactly-once는 아님

OMC에는 두 메시징 경로가 공존한다.

- canonical team 상태: [경로 구조:7–26](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/state-paths.ts#L7-L26>)의 task/worker/mailbox와 [mailbox 송신·marker:960–1039](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/team-ops.ts#L960-L1039>): UUID message, lock 하 저장, `notified_at`/`delivered_at` 분리.
- MCP bridge JSONL: [inbox/outbox 경로:24–49](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/inbox-outbox.ts#L24-L49>)는 Claude config 아래 team 이름별 파일. [rotation/cursor:101–179](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/inbox-outbox.ts#L101-L179>)는 오래된 절반 제거·cursor reset 및 읽기 상한을 구현한다. 무기한 감사 원장이나 수신 후 업무 트랜잭션은 아니다.

[dispatch schema:23–46](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/dispatch-queue.ts#L23-L46>)는 pending/notified/delivered/failed, attempt count, message correlation을 따로 둔다. 특히 일반 호환 reader가 잘못된 저장소를 빈 목록으로 읽는 [경로:209–221](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/dispatch-queue.ts#L209-L221>)와, 권한 판단용 strict reader의 [오류 구분 타입:62–76](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/dispatch-queue.ts#L62-L76>)을 분리한 것이 좋은 패턴이다.

[notification security tuple:60–89](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/mailbox-notification-guard.ts#L60-L89>)은 team instance, provider target, tmux socket/PID/birth identity, recipient, request/message ID를 묶는다. [비교와 현재 증거 재조회:412–481](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/mailbox-notification-guard.ts#L412-L481>)는 진단 필드가 아닌 권한 관련 필드를 검증한다. 다만 이 모듈은 **read-only evaluator이며 실제 transport/marker writer가 아니다**. 이 함수 존재만으로 모든 호출부의 TOCTOU가 없어졌다고 주장할 수 없다.

**TalkAbout 적용:** durable 전달 기록·진단에서 `queued / notification_sent / recipient_ack / work_started / artifact_submitted / verified`를 구분하는 설계는 유용하다. 단, 새 필드를 넣을 때 [서버 권한 경로](<../../talkabout/core.py#L225-L262>)와 [worker 결과 journal 경로](<../../talkabout/worker.py#L598-L636>)에 묶고 알림 재전송을 provider 재호출로 연결하지 않는다. UI의 ‘전달됨’을 ‘실행됨/검증됨’으로 표시하지 않는다.

## 5. recovery: 채택할 것은 안전 경계, 버릴 것은 맹목적 재시작

[checkpoint 구현:9–29, 59–105](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/task-recovery-checkpoint.ts#L9-L105>)은 최대 64 KiB, task version/claim/sequence, canonical payload hash, immutable publication, 파일 sync를 구현한다. 이는 단순 요약 메모보다 강하지만 **hash는 내용 무결성이지 내용의 안전성이나 완료 사실의 증명은 아니다**. 소유 계정의 악성/오동작 worker가 안전하지 않은 payload를 ‘safe boundary’라고 부르는 문제는 별도다.

[recovery saga:53–132](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/recovery-saga.ts#L53-L132>)의 실제 순서:

1. liveness `unknown`이면 실패; 살아 있는 원래 worker를 죽었다고 간주하지 않음.
2. owned in-progress task들의 checkpoint를 **모두 먼저 검증**.
3. adoption token hash로 예약/requeue → gated pane 생성 → replacement identity 저장.
4. manifest projection 미검증이면 `commit_unknown`; 활성화/continuation adoption이 미완이면 provider 실행 금지.
5. owner의 adoption → services repair → `writeRun`. 미동기화 서비스는 `services_pending`으로 유지.
6. commit 전 persistence 실패 시 해당 pane attempt만 rollback kill. commit 이후는 무조건 죽이고 새로 시작하지 않음.

좋은 점은 **reserved/committed/active/services_pending을 구분**한다는 것이다. 주의할 점은 `commit_unknown`에서 `committed:false` 형태도 반환되므로, 그 boolean 하나를 ‘어떤 효과도 없었다’로 읽으면 안 된다는 것이다. 호출자는 outcome·activation·projection까지 함께 해석해야 한다.

TalkAbout R3의 불확실 호출 retirement와 이 saga는 동일 기능이 아니다. 이미 외부 효과가 발생했을 수 있는 호출을 checkpoint 이름만 바꿔 자동 재실행해서는 안 된다. 후속 설계는 [worker journal](<../../talkabout/worker.py#L530-L565>)에 **사용자/owner가 승인한 새 task 또는 안전한 읽기 전용 분석 continuation**을 만드는 방식이 우선이다. 코드 작업의 checkpoint는 base revision·허용 workspace·산출물 hash·이미 수행한 검증·미확정 외부 효과를 담고, 외부 write가 불확실하면 자동 adoption하지 않는다.

## 6. 검증 루프와 프롬프트 관례의 구분

| 소스/기능 | 실제 기계 동작 | 아직 신뢰할 수 없는 부분 |
|---|---|---|
| [worker bootstrap:94–115, 142–154](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/worker-bootstrap.ts#L94-L154>) | claim/checkpoint/종료 CLI를 포함하는 지시문 생성 | worker가 위험 경계 직전에 정말 checkpoint를 남기는 것은 LLM 준수에 의존. 지시문만으로 실행 fence가 생기지 않음. |
| [외부 reviewer 계약:1–19, 45–61](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/cli-worker-contract.ts#L1-L61>) | 파일 기반 verdict schema, task/claim/version/launch attempt identity를 갖는 계약 | reviewer가 독립적으로 실제 소스를 읽었는지, 테스트가 실제 실행됐는지는 JSON만으로 증명 불가. |
| [verdict 지시·parse:124–170](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/cli-worker-contract.ts#L124-L170>) | 유효 JSON과 enum/일부 필드를 검사하는 parser 존재 | parser 수용과 변경 적용 승인·보안 승인·테스트 통과는 별도. |
| [phase inference:20–98](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/phase-controller.ts#L20-L98>) | status 분포와 retry metadata로 executing/fixing/terminal 추론 | 이 함수에는 독립 verifier 실행 자체가 없음. 모든 task completed가 곧 검증 성공은 아님. |
| [verification 실행기:118–163](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/features/verification/index.ts#L118-L163>) | command가 있으면 `exec` + timeout으로 stdout/stderr·성패 증거 수집; command 없는 manual check는 **불합격/검토 대기** 유지 | 임의 command 문자열은 신뢰된 설정이어야 함. 이 실행기가 모든 team 완료 경로에 필수로 연결되었다고 여기서 단정하지 않음. |
| [완료시 delegation evidence:230–259](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/state/tasks.ts#L230-L259>) | 결과 문자열의 `Subagent spawn evidence:`/skip reason을 추출, 일부 task의 완료 전이에서 요구 | 실제 spawn receipt가 아니라 worker 결과 텍스트에서 추출. 문구 존재는 실행 증명이 아님. |

따라서 `plan → exec → verify → fix`라는 문서/skill 관례와 상태 머신/명령 실행기를 구분해야 한다. TalkAbout에는 **analysis / change proposal / owner opt-in work**를 분명하게 표시하고, ‘worker reported’, ‘host observed’, ‘independently verified’, ‘human adopted’를 서로 다른 provenance로 남기는 편이 맞다.

현재 [기본 prompt 생성:115–132](<../../talkabout/agent_runtime.py#L115-L132>)은 analysis/change proposal을 텍스트로 제한하며 실행했다고 주장하지 못하게 한다. 반면 [work-profile:72–112](<../../talkabout/agent_runtime.py#L72-L112>)은 Codex exec-json에서 owner의 별도 디렉터리에 실제 작업을 opt-in하고 unified diff·실제 검증 보고를 요구한다. **이미 실제 작업용 접점은 있지만, 모든 외부 agent가 실제 코딩 도구를 갖는 것은 아니다.** 대화 전용 contributor에 ‘executor’라는 역할명만 붙이는 것은 해결책이 아니다.

## 7. permission·계정 scope·접근 경계

가장 중요한 거절 근거는 [permission 모듈:3–8](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/permissions.ts#L3-L8>)의 명시적 **advisory only**다. [허용 판정과 프롬프트 렌더링:105–195](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/permissions.ts#L105-L195>)에서 빈 allowed path/command는 광범위 허용이며 command는 prefix 매칭이다. [secure deny defaults:198–228](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/permissions.ts#L198-L228>)의 `.env`, `.ssh` 등 규칙도 advisory layer를 OS sandbox로 바꾸지 않는다. 변경 파일 사후 검사는 이미 일어난 읽기·네트워크 유출을 되돌리지 못한다.

**계정별로 구분할 세 권한:**

1. **TalkAbout 계정/방 권한:** agent owner, credential scope, room membership/revision, session binding, task assignee. [identity 목록:39–46](<../../talkabout/identity.py#L39-L46>)은 소유 owner로 필터하고 [mutation 권한:229–256](<../../talkabout/core.py#L229-L256>)은 task/room/인간 권한을 따로 검사한다. 외부 CLI 모델 이름이나 local worker 이름을 이 인증으로 대체하지 않는다.
2. **provider 로그인·과금 계정:** Claude/Codex 등의 로컬 로그인/API key는 실행하는 사람의 provider 권한이다. OMC의 [API-key bare mode:176–194](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/model-contract.ts#L176-L194>)는 launch 환경을 사용한다. room 참여 초대가 다른 owner의 provider 계정·예산·CLI 인증 공유를 뜻하지 않는다.
3. **로컬 파일/프로세스 권한:** workspace 허용은 owner의 로컬 신뢰 이전이다. OMC의 cwd/team/config 파일 경계는 협업용 식별이지 다중 사용자 OS 격리가 아니다. 특히 config 홈 아래 이름 기반 bridge inbox는 중앙 서비스의 tenant namespace로 재사용하지 않는다. owner별 실행 identity·private storage·credential 분리, 명령 allowlist와 네트워크 정책은 별도 host 책임이다.

TalkAbout [DPAPI vault:1–5, 70–96](<../../talkabout/credential_vault.py#L1-L96>)는 이미 opt-in/current-user 보호를 구현한다. R4 재구현은 불필요하다. 다만 DPAPI는 같은 OS 사용자로 실행되는 악성 프로세스나 승인된 provider 프로세스의 모든 권한을 분리하는 sandbox가 아니다. [worker 입력 경계:520–526](<../../talkabout/worker.py#L520-L526>)처럼 credential/lease/retry key는 모델 입력에서 제외해야 한다.

[ToolBroker:93–126](<../../talkabout/tool_broker.py#L93-L126>)는 현재 **FakeToolAdapter만 허용**하고, [실행 재인가:238–263](<../../talkabout/tool_broker.py#L238-L263>)는 effect 전후 fence·승인을 검사한다. [foundation fence:46–82](<../../talkabout/foundation_authority.py#L46-L82>)는 파일 첫줄대로 unwired internal contract다. 이 테스트용/내부 seam을 ‘실서비스 코딩 tool broker 완성’으로 과장하지 않아야 한다. 실제 coding은 현재 owner opt-in native work-profile 경로와 분리해서 판단한다.

## 8. 정적 테스트 증거 — 실행 결과 아님

| 읽은 테스트 소스 | 확인한 assertion/한계 |
|---|---|
| [strict dispatch:69–134](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/__tests__/dispatch-queue.strict-read.test.ts#L69-L134>) | missing/malformed/non-array/잘못된 필드와 foreign team을 구분; 일반 reader의 team/status 정규화와 엄격 reader 거부를 대조한다. |
| [recovery ordering:77–148](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/team/__tests__/recovery-saga.test.ts#L77-L148>) | persist→activate→adopt→repair→run 순서, commit 전 kill, manifest 미검증 시 run 금지, commit 후 adoption 실패를 assert. 의존성 mock 순서 테스트이며 실제 provider/pane 복구 실증은 아님. |
| [update locking:73–115](<https://github.com/Yeachan-Heo/oh-my-claudecode/blob/e10206d8789b0ac4e5214eddbfbd203c1b757dea/src/__tests__/team-update-task-locking.test.ts#L73-L115>) | lock 함수 문자열 확인과 순차 version 증가가 있다. 동시 3회 테스트의 최종 assertion은 `>=2`로 약해, 세 변경 모두 보존·정확히 +3을 이 테스트만으로 증명하지 못함. |
| [기존 TalkAbout reservation 테스트:35–71](<../../tests/test_runner_reservations.py#L35-L71>) | 재예약 execute 거부, stale fence·만료 lease 거부를 검사하는 fake/in-memory 케이스. 실행하지 않음. |
| [기존 TalkAbout worker recovery 테스트:59–84](<../../tests/test_worker_reservations.py#L59-L84>) | lost reservation response는 R3에서 아직 start 전이므로 안전하게 start 가능함을 assert. ‘모든 재시작 금지’가 아니라 pre-start/post-start 구별이 핵심. 실행하지 않음. |
| [기존 work-profile 테스트:1–5, 95–100](<../../tests/test_work_profile.py#L1-L100>) | 실제 provider turn NOTRUN 명시, 검증되지 않은 provider/transport 거부를 검사. 이 리뷰에서도 실제 코딩·과금 호출은 하지 않음. |

후속 구현 시 추가할 회귀 계약: (a) 동일 notification 재전송이 provider 호출을 늘리지 않음, (b) 잘못된 owner/room/session/launch identity의 artifact/verdict 채택 금지, (c) old revision 검증 결과로 새 diff 승인 금지, (d) 수정 최대 횟수/비용/시간 한도 도달 시 사람이 결정, (e) checkpoint에 uncertain effect가 있으면 자동 재실행 거부. 이는 **새 테스트 제안**이며 존재/통과 주장과 다르다.

## 9. TalkAbout 채택/회피 우선순위

| 순위 | 판단 | 구체적 적용 지점·완료 조건 |
|---|---|---|
| **1 / P1** | **채택: 실제 업무 산출물 계약** | [native prompt/work 경계](<../../talkabout/agent_runtime.py#L72-L132>)와 [worker 결과 저장](<../../talkabout/worker.py#L598-L636>)에 analysis의 근거/한계, coding의 base revision·unified diff·변경 파일·검증 command/exit/output provenance를 구조화. 대화 기여와 실제 workspace 작업을 UI/결과에서 구분. owner opt-in 없는 coding 실행은 거부. |
| **2 / P1** | **채택: 검증과 채택을 별도 단계로** | [서버 artifact/완료 권한 분기](<../../talkabout/core.py#L229-L256>)를 중심으로 submission→independent review→human adoption 분리. verdict는 동일 task attempt/산출물 hash/revision에 bind. 텍스트 ‘tests passed’를 host-observed로 승격하지 않음. 제한된 fix loop와 인간 종료 게이트. |
| **3 / P2** | **채택: delivery 진단과 엄격 reader 원칙** | [server mutation](<../../talkabout/core.py#L225-L262>)·[journal 게시](<../../talkabout/worker.py#L625-L636>)에 alert/ack/work/verified 의미 구분. 손상·없는 상태·권한 불일치를 빈 inbox로 합치지 않음. 재전송은 메시지만, execution reservation은 그대로 유지. |
| **4 / P2** | **조건부 채택: checkpoint continuation** | [worker reservation/start](<../../talkabout/worker.py#L530-L565>) 이후의 재실행 금지를 보존하면서, 별도 새 task로 안전한 분석/코딩 경계를 재개. checkpoint의 workspace/base revision/hash/외부효과 불확실성 검증 및 owner 승인 전에는 provider run 금지. 기존 R3를 ‘자동 재시도’로 대체하지 않음. |
| **5 / P2** | **채택: 분리된 health 관측** | [worker heartbeat](<../../talkabout/worker.py#L568-L590>)에 poll 생존·provider 진행·마지막 산출물·게시 상태를 별도 진단. heartbeat stale만으로 외부 작업을 없었던 일로 처리하지 않음. |
| **금지 / P0** | **회피: bypass 기본값 이식** | [native runtime](<../../talkabout/agent_runtime.py#L1-L12>)에 OMC의 승인/sandbox 우회 인자를 가져오지 않음. owner의 명시적 work-profile, provider별 검증, 프로세스 수명 제한을 보존. |
| **금지 / P0** | **회피: prompt RBAC를 보안 경계로 사용** | [서버 권한](<../../talkabout/core.py#L225-L256>)·[internal broker](<../../talkabout/tool_broker.py#L120-L126>) 대신 `.env 읽지 말라`/prefix allowlist를 쓰지 않음. 읽기·쓰기·네트워크·provider 계정 접근은 host가 통제. |
| **보류 / P3** | **회피: 전체 OMC plugin/team runtime 내장** | [native adapter](<../../talkabout/agent_runtime.py#L1-L12>)와 OMC의 장수 TUI/pane·Node/native filesystem·config-home 상태 모델을 통합하면 수명/권한/저장 원장이 중복됨. 먼저 위 계약만 독립 구현하고 별도 호환성/라이선스/계정 격리 심사 없이는 설치하지 않음. |

## 10. 최종 판정과 남은 불확실성

**채택 가치:** durable task 소유권, 전달과 실행의 분리, 엄격한 identity/손상 판정, 복구 activation gate, 구조화 reviewer verdict, manual 검증을 자동 통과시키지 않는 원칙.

**회피 가치:** 승인·sandbox 우회 기본값, advisory permission을 강제 정책으로 포장, heartbeat를 진척/효과 없음의 증거로 사용, worker가 쓴 spawn/test 문구를 실제 실행 receipt로 사용, 로컬 팀 파일을 tenant 경계로 사용.

이번 검토는 고정 소스와 일부 테스트의 정적 검토다. 실제 Windows/tmux/cmux/native addon 빌드, provider CLI 버전 호환, 장애 시 디스크 내구성, 모든 transport 호출부의 재검증 연결, 전체 verification gate 연결성, 실제 계정/과금 격리는 실행 검증하지 않았다. 성능·품질·복구 성공률 수치를 주장하지 않는다. **TalkAbout R3/R4 완료를 존중하고, 다음 투자 대상을 ‘실제 코딩/분석 산출물과 검증·채택 계약’으로 좁히는 것이 권고안**이다.
