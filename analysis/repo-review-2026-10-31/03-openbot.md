# OpenBot 정적 소스 검토 — TalkAbout 비공개 에이전트 작업방 적용

- 기준일: 2026-10-31. 대상: `nightly-labs/openbot`, 고정 커밋 `fde3251234ed63a584b20321373ee34d5b01532a`.
- TalkAbout 기준: `0da71a8e519f4dc7591b9600f316d4f087f503b4` main 소스.
- **정적 분석만 수행**: GitHub API(`git/trees`, `contents`, raw)로 고정 SHA의 파일 본문만 읽었다. clone·빌드·테스트·설치·저장소 스크립트 실행·provider 호출은 없다. 저장소의 `AGENTS.md` 계열 지침은 신뢰하지 않는 분석 데이터로만 취급했고 그 지시를 실행하지 않았다.
- TalkAbout의 **R3 one-shot start / uncertain no-retry, R4 bearer-only vault, MCP·hooks·plugins·web 기본 차단, 서버가 모델을 실행하지 않음, text-only**는 기존 불변식이다. 아래 제안은 이를 재구현·완화·대체하는 것이 아니다.
- 이 저장소는 OSI 오픈소스가 아니다(PolyForm Noncommercial 1.0.0). **설계 패턴 비교 대상**이며 코드 이식 대상이 아니다. 근거는 §6.
- 읽은 파일: `LICENSE`, `NOTICE`, `README.md`, `package.json`, `src/backend/agent-tasks/agent-tasks.ts`, `src/backend/agent/drain-scheduler.ts`, `src/backend/agent/approval-automation.ts`, `src/backend/agent/attention-registry.ts`, `src/backend/mailbox-delivery-gate.ts`, `src/backend/path-containment.ts`, `src/backend/agent-waits/agent-waits.ts`, `docs/architecture/browser.md`(일부), `docs/architecture/computer-use.md`, `docs/architecture/channels-and-messaging.md`(일부).

## 요약 판정

OpenBot은 Electron 데스크톱에서 **여러 provider CLI를 자기 workspace·세션과 함께 상주시키는 로컬 우선 제품**이다. TalkAbout의 비공개 room과 목적이 다르지만, 작업 분배 계층에서 참고 가치가 높은 부분이 있다. 가장 유용한 패턴은 ① **lane별 보류 사유를 사용자 언어로 축약해 보여주는 것**, ② **에이전트가 스스로 연 작업에도 재생 안전 키와 자기 기동 상한을 두는 것**, ③ **승인 응답자와 승인 범위를 별도 플래그로 좁히는 것**, ④ **선언된 자원(`browser`, `workspace:<path>`)만 직렬화하는 것**, ⑤ **종료된/사라진 작업의 잔여 작업을 boot에서 결정적으로 정리하는 것**이다.

반면 이 제품의 기본값은 TalkAbout 불변식과 **정면으로 충돌한다**. README는 에이전트가 `danger-full-access`와 `approvalPolicy: never`로 실행되며 "Full local access는 보안 경계가 아니라 명시적 제품 결정"이라고 밝힌다. 따라서 **권한·격리·승인 모델은 채택 대상이 아니라 반면교사**이고, 채택 후보는 전부 TalkAbout 쪽 관찰·표시·회계 계층에 한정된다.

### 이 저장소를 어떤 용도로 참고했는가

- **참고 대상**: 여러 provider 세션이 같은 방/에이전트에 붙어 있을 때의 **대기 사유 표시**, task별 lane 분리, 재기동 시 잔여 작업 정리, 승인 응답자·예외의 데이터 모델.
- **참고하지 않은 대상**: Electron 데스크톱 셸, provider 설치·인증 UX, 플러그인 marketplace, 모바일·웹 클라이언트, 채널 lead 라우팅, Computer Use.
- **판정 기준**: TalkAbout의 불변식 5개(R3 one-shot·uncertain no-retry, R4 bearer-only vault, MCP/hooks/plugins/web 기본 차단, 서버가 모델을 실행하지 않음, text-only)를 먼저 대조하고, 하나라도 완화하는 패턴은 후보에서 제외했다. 결과는 §2의 불변식 대조표.

## 1. 핵심 발견: 실제 구현과 적용점

### F1 — 보류 사유를 lane 단위로 명시하고 사용자에게는 축약해 보여준다 (채택 1순위)

스케줄러는 **에이전트 자기 대화 1개 + 열린 task마다 1개**의 lane을 갖고, 각 lane의 시작 가능 여부를 여러 controller의 `#mayDrain` 절을 합성해 판정한다. 판정 결과는 `session-settings`, `active-turn`, `one-turn`, `task-closed`, `task-limit`, `agent-client-changing`, `hidden`, `channel`, `profile-save`, `duplication`, `compaction`, `routine`, `usage-limit` 같은 **이름 있는 사유**로 남고, 같은 lane에서 사유가 바뀔 때만 로그를 1회 남긴다. 사용자 화면에는 이 중 task가 바꿀 수 없는 사유를 `agent-busy`로 접고 `task-limit`/`one-turn`/`usage-limit`/`server-busy`만 그대로 노출한다.

- 구현: [lane 정의와 자기 대화 우선 규칙](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/src/backend/agent/drain-scheduler.ts#L99-L121>), [drain의 소유 범위와 controller 합성 설명](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/src/backend/agent/drain-scheduler.ts#L142-L154>), [보류 절 전체와 순서](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/src/backend/agent/drain-scheduler.ts#L292-L329>).
- 사용자 투영: [task 공개 상태와 hold 계산](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/src/backend/agent-tasks/agent-tasks.ts#L419-L433>), [내부 사유 → 공개 사유 축약표](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/src/backend/agent-tasks/agent-tasks.ts#L472-L490>).
- 중복 억제: [사유가 바뀔 때만 1회 기록](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/src/backend/agent/drain-scheduler.ts#L331-L347>), [task 단위 hold 조회](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/src/backend/agent/drain-scheduler.ts#L349-L354>).
- 증거 한계: 이 코드는 제품의 drain 구현이며 TalkAbout의 lease/reservation과 의미가 1:1 대응하지 않는다. `publicHold`는 **의도적으로 정보를 버리는 투영**이므로, 축약표를 그대로 복제하면 실제 원인을 숨길 수 있다. 스케줄러 동작을 검증하는 테스트는 읽지 않았고 실행하지도 않았다.
- TalkAbout 적용: task 예약·lease·`hold_dispatch`는 이미 [core.py](<../../talkabout/core.py#L393-L443>)의 생성·revision fence가 담당한다. 여기서 가져올 것은 새 dispatch 경로가 아니라 **"왜 아직 시작되지 않았는가"를 닫힌 enum으로 노출하는 관찰 필드**다. A4의 "관찰은 상태를 바꾸지 않는다"([REPOSITORY_ADOPTION_PLAN.md](<../../docs/REPOSITORY_ADOPTION_PLAN.md#L77-L97>)) 계약 안에서, room snapshot 계산 시점에만 사유를 유도하고 저장하지 않는다. `uncertain`, `stale_attempt`, `dispatch_held`는 **별도 사유로 남기고 `agent-busy`로 접지 않는다**. 이 관찰 필드가 A0에서 닫은 work profile event 계약([07-TALKABOUT-GAPS.md](<../repo-review-2026-10-27/07-TALKABOUT-GAPS.md#L6-L38>))을 다시 열어서는 안 된다 — 도구 이벤트 수용 여부와 대기 사유 표시는 다른 계층이다.

### F2 — 에이전트 자기 기동 작업에도 재생 키와 상한이 있다 (채택 2순위)

에이전트는 `start_task` 도구로 자기 task를 열 수 있지만, ① 자기 대화가 아닌 실행 thread에서만 호출되고, ② 열린 자기 기동 task가 8개를 넘으면 거절되며, ③ idempotency key를 `sha256([agentId, turnId, callId])`로 만들어 **재시도된 호출이 첫 호출의 task를 돌려준다**. task 생성→brief queue→실패 시 task 폐기가 한 흐름이고, 실패하면 task를 남기지 않는다. 또한 "다음 turn 종료 대기"는 호출 시점에 등록되어, 이 호출 뒤에 보낸 Stop이 종료를 놓치지 않는다고 주석에 명시한다.

- 구현: [자기 기동 상한과 tool schema](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/src/backend/agent-tasks/agent-tasks.ts#L29-L37>), [클래스 소유 범위](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/src/backend/agent-tasks/agent-tasks.ts#L63-L70>), [호출 경로·재생 키·상한·실패 폐기](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/src/backend/agent-tasks/agent-tasks.ts#L299-L349>).
- 종료 대기: [호출 시점 등록과 waiter 정리](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/src/backend/agent-tasks/agent-tasks.ts#L211-L227>), [Stop이 지목한 turn 표시](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/src/backend/agent-tasks/agent-tasks.ts#L229-L236>).
- 증거 한계: 재생 키는 **메일박스 delivery 수준**의 중복 방지이고, provider 호출의 exactly-once를 뜻하지 않는다. `turnId`/`callId`는 provider가 주는 값이므로 provider가 같은 turn에서 다른 callId를 주면 재생이 성립하지 않는다. 상한 8은 하드코딩 상수이며 테스트로 확인하지 않았다.
- TalkAbout 적용: A2는 "구 worker가 임의로 노드를 실행하지 못하도록" plan schema capability와 고정 revision을 먼저 정하라고 못박았다([REPOSITORY_ADOPTION_PLAN.md](<../../docs/REPOSITORY_ADOPTION_PLAN.md#L45-L48>)). 그러므로 **에이전트 자기 기동을 들여오지 않는다.** 대신 이미 있는 재생 사고방식([core.py `replay`·fingerprint](<../../talkabout/core.py#L357-L374>))에 다음 두 가지만 보강 후보로 남긴다: (a) 서버가 발급하는 **task 시도 식별자를 재생 키의 기준**으로 고정하고 provider가 준 id를 키의 유일 근거로 쓰지 않는다, (b) "Stop 이후 시작되지 않음"을 UI가 단정하지 않도록 **종료 대기 등록 시점**을 호출 시작 시점으로 명시한다.

### F3 — 승인 자동화는 기본 꺼짐이지만, 켜면 권한 확대까지 덮는다 (반면교사 + 부분 채택)

승인 자동화는 별도 정책 객체로 분리되어 있고 기본값은 "아무것도 자동 승인하지 않음"이다. 문제는 켜졌을 때의 범위다. 주석은 이 grant가 **에이전트가 올리는 모든 승인 종류를 덮고 `permissions`(파일시스템·네트워크 확대)까지 포함**한다고 밝힌다. 반대로 승인 항목별로는 `alwaysAsk`(Full access여도 계속 묻는다)와 `ownerOnly`(원격 팀원이 아니라 소유자만 답한다) 플래그가 있어, **예외를 위가 아니라 아래에서 좁히는** 구조다. 승인 응답자는 `owner: boolean` 하나로 축약되어 있고, "원격 팀 멤버는 답할 수 없다"가 타입 주석으로 명시된다.

- 구현: [자동 승인 정책의 범위와 기본값](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/src/backend/agent/approval-automation.ts#L1-L26>), [승인 항목의 alwaysAsk/ownerOnly와 응답자 구분](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/src/backend/agent/attention-registry.ts#L120-L157>).
- 증거 한계: 정책 인터페이스와 주석만 읽었다. 화면이 이 플래그를 실제로 어떻게 표시하는지, grant 철회가 진행 중 승인에 미치는지는 읽지 않았다. 또한 이 제품은 승인 자체를 `danger-full-access` 전제 위에 얹으므로, 승인 UI의 안전성을 TalkAbout에 그대로 전이할 수 없다.
- TalkAbout 적용: TalkAbout의 승인은 **실제 human actor와 fence에 묶인 Approval**이어야 하고 "채팅 메시지는 승인으로 간주하지 않는다"([TOOL_BROKER_PLAN.md](<../../docs/TOOL_BROKER_PLAN.md#L28-L42>)). 그러므로 자동 승인은 **도입하지 않는다**. 가져올 것은 두 가지뿐이다. (a) 승인 항목마다 **"무조건 사람에게 묻는" 예외 플래그**를 두고 자동화/저장 규칙이 그 예외를 덮지 못하게 한다. (b) 승인 응답자를 **bool 하나로 줄이더라도 "누가 답했는가"의 출처를 별도로 남긴다**(사람/저장 규칙/시간 초과/자동). 이는 승인 화면 자체의 신규 기능이 아니라 A5/A6 승인 gate의 데이터 요건이다.

### F4 — 에이전트 간 협업에서 멤버십은 라우팅이고, 직렬화는 선언된 자원만 한다 (채택 3순위)

채널은 host·목적·참여 에이전트·선출된 lead로 구성되고, **에이전트 멤버십은 "누가 작업을 받는가"만 정한다. 사람의 접근은 제한하지 않는다**(각 인증된 서버 멤버가 채널을 읽고 쓴다). 요청 소유자는 결정적 규칙으로 먼저 고르고, 그 규칙으로 못 고를 때만 lead의 모델이 라우팅한다. 잘못 해석된 라우팅 응답은 **broadcast로 확대되지 않고** task를 `channelRoutingUnreadable`로 멈추고 Reassign을 제안하며, 로그에는 응답 텍스트가 아니라 길이와 필드 이름만 남긴다. 선택된 소유자마다 lead 이름의 채널 메시지 1건을 남겨 **사용자가 선택을 교정할 수 있게** 한다. 직렬화는 선언된 `browser`와 `workspace:<path>` 자원에만 적용되고, 포함 관계(`workspace` 경로 중첩)까지 본다.

- 구현: [채널·멤버십·사람 접근의 분리](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/docs/architecture/channels-and-messaging.md#L140-L159>), [결정적 선택 우선과 라우팅 실패 처리·가시화](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/docs/architecture/channels-and-messaging.md#L161-L183>).
- 자원 직렬화: [선언된 browser/workspace만 직렬화하고 권한은 제한하지 않음](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/docs/architecture/channels-and-messaging.md#L228-L238>).
- 증거 한계: 이 절은 아키텍처 문서이며 실제 store/lock 구현을 읽지 않았다. 문서 자체가 "이 제어는 provider 프로세스 권한을 제한하지 않는다"고 밝힌다. 즉 **자원 직렬화는 충돌 완화이지 권한 강제가 아니다**.
- TalkAbout 적용: A2의 write scope는 "선언일 뿐 경로 권한·OS sandbox·충돌 방지가 아니다"([REPOSITORY_ADOPTION_PLAN.md](<../../docs/REPOSITORY_ADOPTION_PLAN.md#L34-L43>))로 이미 정직하게 한계를 적었다. 여기서 가져올 것은 **선언된 scope를 예약 시점의 직렬화 키로만 쓰는 사고방식**이다. 즉 새 권한 계층이 아니라, 같은 room에서 같은 workspace를 선언한 두 task가 동시에 lease되지 않도록 **예약 단계에서 겹침을 거절**하고(경로 중첩 포함), 그 겹침을 사용자에게 이유로 보여주는 정도다. 채널처럼 사람 접근을 멤버십에서 분리하는 모델은 **가져오지 않는다** — TalkAbout room은 사람과 에이전트가 함께 ACL 대상이다.

### F5 — 사라진 작업의 잔여 작업을 boot에서 결정적으로 정리하고, 대화 귀속을 섞지 않는다 (채택 2순위)

삭제된 task가 남긴 미완료 delivery는 boot에서 **queue 상태면 즉시 취소, 이미 시작됐으면 `interrupted`로 종결**한다. rows는 남긴다. 이유는 "queue에 남은 메시지가 영원히 기다리며 에이전트를 붙잡는 것"을 막기 위함이고, provider history 복구보다 먼저 실행된다. 또 **archive된 task의 작업은 절대 에이전트 자기 대화로 정산되지 않는다**(`threadForDelivery`가 task thread만 돌려주고, task가 삭제되면 `null`). 아울러 메시지 삽입 준비와 비동기 삭제가 경쟁하지 않도록, 수신자 차단 상태와 **version 스냅샷을 prepare→validate 형태로 재확인**하는 gate가 있다.

- 구현: [사라진 task의 잔여 작업 정리와 실행 순서 주석](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/src/backend/agent-tasks/agent-tasks.ts#L264-L280>), [archive task 작업의 대화 귀속](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/src/backend/agent-tasks/agent-tasks.ts#L248-L262>).
- 경쟁 방지 gate: [prepare/validate로 차단 상태·version 재확인](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/src/backend/mailbox-delivery-gate.ts#L1-L25>).
- 증거 한계: 두 파일 모두 주석과 짧은 구현이며, "boot 순서가 실제로 지켜진다"는 주장은 조립 코드에서 확인하지 않았다. `interrupted`가 제품에서 재시도 가능으로 해석되는지도 이 파일만으로는 알 수 없다.
- TalkAbout 적용: A4는 `pause`/`close`/`change_mode`·membership mutation·한도 종료에서 queued/leased task를 회수하고, **이미 시작이 확인된 attempt는 `cancelled`가 아니라 `uncertain`으로 남긴다**([REPOSITORY_ADOPTION_PLAN.md](<../../docs/REPOSITORY_ADOPTION_PLAN.md#L77-L97>)). 이 계약을 유지하면서 다음을 관찰 항목으로만 추가한다: (a) 서버 재기동 후 남은 미시작 delivery를 **자동 재실행하지 않고** "정리됨/사람 확인 필요"로 표시, (b) task가 사라진 뒤 도착한 결과가 **room 대화에 섞이지 않도록** 귀속을 task id로 고정. `interrupted`를 실패·재시도 가능으로 승격하는 매핑은 **금지**한다.

### F6 — browser/computer use는 데스크톱 전체를 다루는 데몬이며, 채택 대상은 격리 메타데이터뿐이다

Computer Use는 서드파티 `cua-driver` 바이너리를 **장기 실행 `serve` 데몬**으로 띄우고, 각 provider는 소켓 프록시로 접속한다. 화면 캡처·접근성 읽기·입력 주입은 모두 데몬 안에서 일어난다. 제어 소켓은 per-user runtime 디렉터리 `0700`에 두고(누가 잡으면 데스크톱 전체를 조종할 수 있다고 주석이 명시), Windows는 추측 불가한 이름의 named pipe를 쓴다. 드라이버는 `native-runtime.lock.json`에 고정되고 설치 스크립트가 **파일 allowlist와 digest 검사**를 한다. 승인 쪽에서는 테스트 태그＋프로세스 ID를 동시에 요구해 다른 입력과 섞이지 않게 한다. 반면 **Full access 에이전트는 Computer Use 도구 동의를 매번 묻지 않고 1회 수락 후 저장하지 않는다**.

- 구현: [데몬·소켓 경로·권한 귀속·digest allowlist](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/docs/architecture/computer-use.md#L1-L34>), [Full access의 무승인 동의와 승인 변경 시 세션 교체](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/docs/architecture/computer-use.md#L143-L162>).
- 제품 전제: [danger-full-access·approvalPolicy never 경고](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/README.md#L13-L17>), ["Full local access는 보안 경계가 아니다"](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/README.md#L579-L586>), [역할·멤버 변경 시 host 세션 전량 회수](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/docs/architecture/browser.md#L36-L42>).
- 증거 한계: 문서 기준이며 `cua-driver` 런타임 코드는 읽지 않았다. macOS 권한 부여·Windows named pipe·Linux agent display 동작은 실행 검증되지 않았다. 이 저장소의 검증 환경도 TalkAbout과 다르다.
- TalkAbout 적용: A5(opt-in browser)와 A6(document)은 **보안 관문 대기** 상태이고 기본 no-web을 유지한다([REPOSITORY_ADOPTION_PLAN.md](<../../docs/REPOSITORY_ADOPTION_PLAN.md#L9-L17>)). 이 저장소에서 참고할 것은 데스크톱 자동화 자체가 아니라 **gate 설계 문구**다: 별도 소유 process/profile, 추측 불가 endpoint, digest 고정 바이너리, 테스트 입력과 실제 입력의 태그 분리, 승인 변경 시 session 폐기. 데스크톱 전체를 조종하는 데몬을 room capability로 여는 것은 **금지**한다.

### F7 — "외부 사건을 기다리는 작업"을 작업으로 남기고 사람이 취소할 수 있게 한다 (채택 3순위)

에이전트는 turn을 붙잡고 기다리는 대신 `await_event`로 **열린 wait 레코드**를 만들고 turn을 끝낸다. wait에는 note·routine 연결·소유자(자기 또는 다른 로컬 에이전트)가 있고, `list_waits`/`close_wait`로 조회·종료하며 **사용자는 대화에서 취소**할 수 있다. 이벤트 스크립트가 특정 wait를 지목하면 그 wait만, 그 밖의 routine run은 해당 에이전트의 대기 중 wait을 모두 깨운다. 깨울 때의 run 입력에 **원래 요청과 note를 다시 담아** context를 잃은 에이전트가 이어갈 수 있게 한다. 상한에 가까워지면 에이전트에게 "끝난 wait을 닫으라"고 알린다.

- 구현: [도구 3종과 소유자 스키마](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/src/backend/agent-waits/agent-waits.ts#L10-L44>), [wait의 의미와 wake 시 입력 재구성](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/src/backend/agent-waits/agent-waits.ts#L59-L90>).
- 증거 한계: 이 파일(261줄) 중 앞부분만 읽었다. wake를 실제로 발행하는 routine 쪽 구현은 읽지 않았고, wait이 프로세스 재시작 후에도 유효한지도 확인하지 않았다. "note를 다시 넣는다"는 것은 모델 context 복구를 보장하지 않는다.
- TalkAbout 적용: TalkAbout에는 "사람 승인을 기다리는 task"의 **명시적 대기 레코드**가 없다. 승인 대기·intervention 대기는 task 상태에서 유도되지만, 얼마나 기다렸는지·누가 답할 수 있는지·사용자가 취소하면 무엇이 되는지는 별도 계약이 필요하다. A3가 `resolve-uncertain`을 기록 전용으로 둔 것처럼, 여기서도 **대기 레코드를 추가하되 task 상태·attempt를 바꾸지 않고** 만료 시 자동 승인·자동 재실행 없이 "사람 필요"로만 표시하는 형태가 안전하다. 대기 중 task의 lease를 연장하는 권한은 주지 않는다.

### F8 — 경로 포함 판정은 텍스트 비교이며, 그 한계를 코드가 스스로 밝힌다

경로 containment 함수는 10줄짜리 순수 함수이고, `relative` 결과가 `..`로 시작하지 않는지만 본다. 주석은 **"경로 텍스트만 비교한다. 대상에 symlink가 있을 수 있으면 먼저 realpath로 해소하라"**고 명시한다.

- 구현: [텍스트 기반 포함 판정과 symlink 주의](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/src/backend/path-containment.ts#L3-L10>).
- 증거 한계: 함수 하나만 읽었다. 호출부에서 realpath를 실제로 먼저 부르는지는 확인하지 않았다. 따라서 "이 제품이 symlink 탈출에 안전하다"는 판정은 **할 수 없다**.
- TalkAbout 적용: [WORK_PROFILE.md](<../../docs/WORK_PROFILE.md#L99-L102>)는 이미 "경로 구성요소의 교체 경쟁을 제거하지 않으며 OS 수준 격리를 보장하지 않는다"고 적고 있다. 그러므로 새 주장을 추가할 것이 없다. 다만 동일한 한계를 가진 함수를 **격리 근거로 인용하지 않는 것**이 중요하다. A2의 write scope·A5의 artifact 경로 검사에서 containment는 **형식 검사**로만 표기하고, 권한·격리 문구와 분리한다.

## 2. README 주장 대 실제 구현

| 주장 | 소스 기반 판정 |
|---|---|
| 로컬 우선, 에이전트별 workspace·대화, 로컬 큐·파일 전송·내장 브라우저·에이전트 간 메시징 ([README](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/README.md#L6-L11>)) | 각 기능의 소유 모듈과 저장 경로가 문서화되어 있고, task lane·메일박스·전송 manifest·브라우저 host 문서가 일치한다. "오프라인 전용"은 아니다(같은 절). |
| FIFO 메시지 큐, pause/resume/cancel, crash-safe persistence ([README](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/README.md#L25-L27>)) | lane·hold·boot 정리 구조와 부합한다. 다만 **FIFO와 우선순위**는 이번에 읽은 파일로 확정하지 못했다. |
| 에이전트가 브라우저를 열고 조사·제어 ([README](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/README.md#L28-L29>)) | 내장 브라우저는 Electron sandbox session이고 `window.openbot`·관리 첨부에 접근하지 못한다([README](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/README.md#L564-L572>)). Computer Use는 별개 경로다. |
| "터미널 소유, 에이전트 비대체"에 준하는 로컬 데이터 경계 | per-agent workspace·`Shared/Transfers` manifest(소유·수신자·크기·SHA-256)는 확인했다([README](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/README.md#L532-L559>)). 그러나 provider 홈(`~/.codex`, `~/.claude` 등)은 CLI가 소유하며 앱이 설정을 쓸 수 있다. |
| 에이전트 안전 | README 스스로 **`danger-full-access`, `approvalPolicy: never`**를 경고한다. 승인 자동화는 기본 꺼짐이지만 켜면 `permissions` 확대까지 덮는다(F3). |
| 오픈소스 | 아니다. PolyForm Noncommercial 1.0.0이며 상업적 사용은 별도 계약이다(§6). |

주장 원문은 고정 SHA의 README에서만 인용했다. 성능·동시성·Windows 안정성은 이번 읽기로 인증하지 않는다.

### 불변식 대조 (TalkAbout 기준)

| TalkAbout 불변식 | openbot의 대응 | 판정 |
|---|---|---|
| R3 one-shot start, uncertain no-retry | lane 단위 drain이 시작을 소유하고 실패 종류를 구분하지만, 재시도 정책은 provider별 세션 교체에 기댄다. "실패 후 같은 인스턴스에서 재호출하지 않는다"는 TalkAbout 쪽 계약이 더 명시적이다 | **참고만** |
| R4 bearer-only vault | Vault 도구가 있으나 에이전트는 login/card를 **직접 추가할 수 없고** 비밀번호는 사용자에게서만 온다(거절 문구가 코드에 고정). 방향은 같지만 TalkAbout `credential_vault.py`의 at-rest 경계와 무관한 별개 구현이다 | **방향 일치, 이식 없음** |
| MCP/hooks/plugins/web 기본 차단 | 제품은 MCP 서버·plugin·Computer Use를 기본 제공하고 1회 동의로 연다 | **충돌. 채택 금지** |
| 서버가 모델을 실행하지 않음 | 모델 실행 주체는 provider CLI이고 앱은 spawn·감독한다. 다만 채널 라우팅에서 **lead 모델이 소유자를 고른다** | **부분 충돌. 라우팅 패턴 보류** |
| text-only | 다중 provider·도구·브라우저·데스크톱 제어가 기본 제품 범위다 | **충돌. 참고만** |

이 표가 이 문서의 결론을 압축한다. **관찰·회계 계층(F1·F2·F5·F7)** 은 참고 가치가 있고, **권한·실행 계층(F3·F6)** 은 대부분 반면교사다.

## 3. TalkAbout 적용 후보

아래 이름은 **기존 파일을 확장할 제안**이며, 현재 구현되어 있다고 주장하는 새 기능명이 아니다. 비용·위험은 정적 분석에 근거한 **추정**이다.

| 우선순위 | 채택 패턴 | 단계 | 적용 지점 | gate | 비용(추정) | 위험(추정) |
|---|---|---|---|---|---|---|
| P1 | 대기 사유 enum 투영(F1) | A4 후속 관찰 필드 | [core.py](<../../talkabout/core.py#L419-L443>), room snapshot·[api.py](<../../talkabout/api.py>), 웹 작업 목록 | snapshot 계산이 상태를 바꾸지 않음(A4 계약), 사유 추가 시 기존 `uncertain`/`stale_attempt`를 접지 않음, 사유는 저장하지 않음 | 중 | 낮음. 잘못 축약하면 진단 정보 손실 — 원인 코드는 서버 로그에 남긴다 |
| P1 | 승인 출처·예외 플래그(F3) | A5/A6 승인 gate 설계 | [TOOL_BROKER_PLAN.md](<../../docs/TOOL_BROKER_PLAN.md#L28-L42>)의 `Approval`, intervention 화면 | 자동 허용 0건이 기본, 예외 플래그가 자동화·저장 규칙을 이김, 출처(human/saved_rule/timeout) 기록 | 중 | 중. "승인 없는 승인"으로 오해될 UI 문구 위험 |
| P2 | 선언 scope 예약 직렬화(F4) | A2 실행 활성화 **이전** 설계 | [core.py](<../../talkabout/core.py#L393-L402>) 예약 단계, planning 초안 | 겹침 거절은 예약에서만, 권한 강제로 표기하지 않음, 경로 중첩·symlink 미해소 명시 | 중~대 | 중. 선언을 권한으로 오인시키면 A2 경계 위반 |
| P2 | 재기동·삭제 잔여 작업 표시(F5) | A4 후속 문구·정리 | [core.py](<../../talkabout/core.py#L418-L443>), [STATUS.md](<../../docs/STATUS.md#L162-L173>) 관문 표 | 미시작만 정리, 시작된 attempt는 `uncertain` 유지, 자동 재실행 0건 | 소~중 | 낮음. `interrupted`를 재시도 가능으로 매핑하면 불변식 위반 |
| P3 | 승인 종료 대기 등록 시점 명시(F2) | A4/A5 문구·계약 | [worker.py](<../../talkabout/worker.py>), CLI stop 안내([USER_FLOW.md](<../../docs/USER_FLOW.md#L77-L79>)) | stop 후 "시작 안 됨" 단정 금지, 종료 대기 등록은 호출 시작 시점 | 소 | 낮음 |
| P3 | 외부 이벤트 대기 레코드(F7) | A5 승인 대기·intervention 후속 | [core.py](<../../talkabout/core.py#L783-L791>) interventions, 웹 승인 패널 | task 상태·attempt 불변, 만료는 "사람 필요"로만, 대기 중 lease 미연장, 자동 승인 0건 | 중 | 중. 대기 레코드가 두 번째 상태 기계가 되면 A4 불변식과 충돌 |
| P3 | containment 문구 분리(F8) | A2/A5 문서 gate | [WORK_PROFILE.md](<../../docs/WORK_PROFILE.md#L99-L102>), A2 write scope 문구 | containment를 격리·권한 근거로 인용하지 않음, symlink 미해소 명시 | 소 | 낮음 |
| P3 | 격리 메타데이터 문구(F6) | A5/A6 설계 문서 | [TOOL_BROKER_PLAN.md](<../../docs/TOOL_BROKER_PLAN.md#L67-L71>) | 기본 no-web 유지, 데몬형 데스크톱 제어 미도입 | 소 | 낮음 |

**단계 배치 이유.** P1 두 건은 A4의 관찰 계약 안에서 값을 추가하는 일이라 스키마·API 변경이 작다. P2 두 건은 A2의 "실행 활성화 이전" 설계 구간에 있어야 하고, 실행 DAG가 열린 뒤에 넣으면 예약 의미를 바꾸게 된다. P3는 문구·표시 계층이라 언제든 가능하지만 A5/A6 관문 문구와 함께 손대는 편이 중복 수정을 줄인다.

**중복 회피 확인.** 최근 초대·연결 단순화(도우미 `.cmd`)는 이미 [tests/test_room_invite_flow.mjs](<../../tests/test_room_invite_flow.mjs#L43-L77>)가 **중복 클릭 1회 초대, 부분 실패 시 같은 초대만 재시도, 화면 이탈 시 늦은 초대 차단**을 합성 DOM으로 고정하고 있다. 위 표에는 초대·온보딩 UX 항목이 없다. A0~A4 중 이미 구현된 단계(도구 이벤트 계약, bounded evidence, uncertain 해소, 관찰 detach)도 제안에서 제외했다. 또한 TalkAbout `docs/RUNTIME_CONNECTIONS.md`의 연결 도우미·`probe()`·예산 상한([RUNTIME_CONNECTIONS.md](<../../docs/RUNTIME_CONNECTIONS.md#L44-L60>))은 openbot의 설치/온보딩 UX와 겹치므로 채택 목록에서 뺐다.

**도입하지 않을 것 / 보류**

1. **피함:** 에이전트 자기 기동 task(`start_task`)와 그 상한·재생 키의 제품화. A2가 실행 활성화 전 별도 설계를 요구한다.
2. **피함:** 승인 자동화(grant)와 "Full access에서 무승인 동의". TalkAbout 승인은 human actor·fence 결합이다.
3. **피함:** 데스크톱 전체를 조종하는 Computer Use 데몬을 room capability로 개방. A5 관문과 불변식 위반.
4. **보류:** 채널식 lead 라우팅·모델 기반 소유자 선택. TalkAbout room은 사람이 만들고 ACL로 통제하며, 모델 라우팅은 서버가 모델을 실행하지 않는다는 불변식과 충돌한다.
5. **보류:** 자체 drain scheduler 이식. TalkAbout의 예약·start grant·lease fence가 정본이다.

### gate 문구 초안 (제안, 미구현)

P1 항목을 실제 PR로 옮길 때 최소로 남겨야 하는 gate를 문장으로 고정한다.

- **G1(대기 사유)**: 같은 room snapshot 요청을 두 번 호출했을 때 `last_seq`·event 수·task attempt가 변하지 않는다(A4 합성 계약 재사용). 사유 필드는 닫힌 enum이며 `uncertain`·`stale_attempt`·`dispatch_held`를 `agent-busy`로 접지 않는다.
- **G2(승인 출처)**: 자동 허용이 0건인 기본 구성에서 승인을 만들면 `source`가 반드시 채워지고, `saved_rule`·`timeout`은 `human`으로 집계되지 않는다. 예외 플래그가 켜진 항목은 자동화·저장 규칙이 우회하지 못한다.
- **G3(선언 scope 직렬화)**: 같은 room에서 겹치는 workspace scope를 선언한 두 task를 동시에 예약하면 두 번째가 거절되고, 그 거절이 **권한 거절이 아니라 충돌**로 표시된다. symlink 미해소를 문서에 명시한다.
- **G4(잔여 작업)**: 서버 재기동 후 미시작 delivery는 자동 재실행 0건, 시작된 attempt는 상태 불변이며 `uncertain`으로 남는다. 새 task 생성과 room 이벤트 수가 재기동만으로 증가하지 않는다.
- **합성 우선.** 이 gate들은 전부 합성 저장소·합성 DOM으로 검증 가능해야 한다. 실제 provider 호출이 필요한 항목은 별도 예산 승인으로 분리한다(A0의 검증 분리 방식과 동일).

## 4. 그대로 가져오면 안 되는 것

- **권한 기본값.** `danger-full-access` + `approvalPolicy: never`는 TalkAbout의 text-only 기본·MCP/hooks/plugins/web 차단과 정반대다. 이 전제 위에 만들어진 승인·샌드박스 UX를 그대로 옮기면 불변식이 무너진다.
- **자동 승인 grant.** 권한 확대(`permissions`)까지 자동 승인 대상이 된다. TalkAbout `Approval`은 "실제 human actor, action·대상·인자 digest, fence, 만료, 사용 여부"를 요구한다([TOOL_BROKER_PLAN.md](<../../docs/TOOL_BROKER_PLAN.md#L28-L42>)).
- **멤버십 = 라우팅 모델.** openbot 채널은 에이전트 멤버십을 작업 수신자 선택으로만 쓰고 사람 접근은 서버 멤버 자격으로 푼다. TalkAbout room은 사람·에이전트가 같은 membership ACL 대상이며 viewer별 redaction이 다르다.
- **모델 기반 라우팅/응답.** 모델이 고른 소유자·응답을 그대로 신뢰하는 경로는 서버가 모델을 실행하지 않는 구조와 맞지 않는다. 결정적 선택만 참고한다.
- **문서를 구현 증거로 쓰기.** 이 저장소의 아키텍처 문서는 상세하지만 테스트 통과 증거가 아니다. 특히 `channels-and-messaging.md`는 "이 제어가 provider 프로세스 권한을 제한하지 않는다"고 스스로 밝힌다.
- **"프로세스 분리 = 격리" 등식.** Computer Use 데몬은 로컬 사용자 권한으로 데스크톱 전체를 조종한다. TalkAbout의 Windows containment는 후손 수명·정리 수단이며 sandbox가 아니라는 기존 문구([TOOL_BROKER_PLAN.md](<../../docs/TOOL_BROKER_PLAN.md#L67-L71>))를 바꾸지 않는다.
- **재시도 가능한 `interrupted` 의미.** openbot은 정리 단계에서 `interrupted`를 쓴다. TalkAbout은 시작이 확인된 attempt를 `uncertain`으로 남기고 자동 재실행하지 않는다. 상태 이름만 보고 재시도 경로를 만들면 A3 불변식이 깨진다.
- **상한 상수를 제품 계약으로 승격.** `AGENT_STARTED_OPEN_TASKS = 8`, `CHANNEL_PARALLEL_LIMIT`, 승인 항목 수 상한 등은 이 제품의 운영 상수다. TalkAbout의 방·task·이벤트 상한(`STATUS.md` 4장의 P0 상한)을 대체하지 않는다([STATUS.md](<../../docs/STATUS.md#L162-L173>)).
- **알림·진행 표시를 완료 증거로 사용.** `turn-progress`, `hold` 사유, 브라우저 표시는 관찰이다. 완료·검증 성공의 증거가 아니라는 A1의 `agent_reported_unverified` 구분을 유지한다.

## 5. 검증 경계 (NOTRUN)

- 이번 작업에서 **빌드·테스트·실행·설치·provider 호출은 전부 NOTRUN**이다. 인용한 것은 소스와 문서의 내용이며 통과 결과가 아니다.
- openbot의 task lane·hold·재생 키·boot 정리 동작은 테스트 파일을 읽지 않았고 실행하지 않았다. 따라서 "F1/F2/F5가 실제로 그렇게 동작한다"는 것은 **코드·주석 기준 판정**이다.
- Computer Use의 macOS 권한 귀속, Windows named pipe, Linux agent display는 문서 서술만 확인했다. TalkAbout의 Windows 환경에서 재현 가능성은 검증하지 않았다.
- **읽지 않았거나 확인하지 못한 것**: `mailbox-store.ts`(142KB급 대형 파일, 존재만 확인), `agent-wait-store.ts`(wake·만료 구현), `drain-scheduler.ts`의 drain 실행부(410~1141행), `agent-thread-store`의 SQLite 스키마, 실제 provider 세션 교체 경로, `docs/architecture/agents.md`·`servers.md`·`team-api.md`, 모든 테스트·CI 워크플로. 이 영역에 대한 서술은 이 문서에 넣지 않았다.
- TalkAbout 쪽도 마찬가지다. A0~A4는 합성 계약 완료이며 실제 브라우저 E2E·실제 provider 호출은 별도 gate다. STATUS의 미완료 관문(실제 2-runtime 대화, 운영 배포, binary upload, POSIX 실행)은 그대로 남아 있다([STATUS.md](<../../docs/STATUS.md#L162-L173>)).
- 이 문서의 모든 비용·위험·우선순위는 **추정**이며, 구현 승인 근거가 아니다.

## 6. 라이선스·의존성

- root 실제 [LICENSE](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/LICENSE#L1-L3>)는 **PolyForm Noncommercial License 1.0.0**이다. GitHub API의 라이선스 판정이 `NOASSERTION`인 이유가 여기에 있다. 이는 OSI 오픈소스가 아니며, [허용 목적](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/LICENSE#L31-L41>)은 비상업·개인 연구·비영리 조직 사용으로 한정된다. [Required Notice](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/LICENSE#L75-L77>)는 저작권자 표시와 **상업 라이선스 문의처**를 함께 요구한다.
- README는 상업적 사용에 별도 라이선스가 필요하다고 밝히고, **0.1.11까지의 버전만 Apache-2.0으로 유지**된다고 명시한다([README](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/README.md#L607-L616>)). 최신 고정 SHA에는 이 예외가 적용되지 않는다. 이번 분석 대상 코드는 상업적 이용이 허용되지 않는다.
- 기여는 CLA로 저작권을 이전한다([README](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/README.md#L600-L605>)). 즉 일부 파일을 "MIT/Apache 조각"으로 골라 쓰는 경로도 없다.
- [NOTICE](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/NOTICE#L12-L33>)에는 서드파티가 섞여 있다: Blobatar 0.2.0 MIT, solid-recharts 1.0.1 MIT(recharts 파생), Font Awesome 아이콘 CC BY 4.0([NOTICE](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/NOTICE#L62-L66>)), Docker seccomp 프로파일 Apache-2.0([NOTICE](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/NOTICE#L68-L72>)), cua-driver 커서 모션 포팅 MIT([NOTICE](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/NOTICE#L74-L97>)), Inter OFL. root 라이선스 하나로 이들이 정리되지 않는다.
- 의존성 규모: Bun `1.4.2`, Node `>=24`, Electron `44.9.3`, SolidJS `2.0.0-rc.18`, Effect `4.0.6`, zod, MCP SDK `1.32.1`, Anthropic Agent SDK, 1Password SDK, quickjs([package.json](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/package.json#L77-L79>), [package.json](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/package.json#L388>), [package.json](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/package.json#L407>), [package.json](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/package.json#L416>), [package.json](<https://github.com/nightly-labs/openbot/blob/fde3251234ed63a584b20321373ee34d5b01532a/package.json#L437>)). TalkAbout(Python worker/server + 웹)에 라이브러리로 삽입할 대상이 아니다.
- 이번 권고는 **설계 패턴의 독립 구현**이며 외부 코드 복사는 하지 않았다. 위 라이선스·의존성 판정도 읽은 파일 기준이며 배포 SBOM·전이 의존성 법률 실사를 완료했다는 의미가 아니다.
- **결론(라이선스)**: 이 저장소는 "읽고 패턴을 논의할 수 있는 source-available 제품"이지 "코드를 가져다 쓰는 오픈소스"가 아니다. TalkAbout 문서·코드에 이 저장소의 코드·문구·설정 파일을 복사하지 않는다. 후속 분석에서도 인용은 **동작 설명**으로 제한하고, 상수·문구·스키마를 그대로 옮기지 않는다.
- 대조: 같은 날짜에 검토한 rakazo·open-duet은 Apache-2.0이므로 라이선스 조건이 다르다. 세 저장소를 하나의 "오픈소스 참고자료"로 묶어 표기하면 이 차이가 사라지므로, 문서별로 라이선스를 따로 적는다.

## 최종 한계

가장 유용한 발견은 새 실행 엔진이 아니라 **관찰·표시·회계의 정확도**다: 대기 사유를 닫힌 enum으로 노출하고, 승인 출처를 분리하고, 선언된 scope만 예약에서 직렬화하고, 사라진 작업의 잔여물을 결정적으로 정리하되 started attempt는 불확실로 남기는 것. 반대로 이 제품의 권한·격리·승인 기본값은 TalkAbout이 이미 더 보수적으로 구현한 영역이며, **가져올수록 불변식이 약해진다**. 채택의 중심은 "OpenBot처럼 만들기"가 아니라 **기존 R3/R4와 A0~A4 계약 위에 진단 가능성만 얹는 것**이다.
