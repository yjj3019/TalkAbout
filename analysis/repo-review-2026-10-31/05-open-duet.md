# Open Duet 정적 소스 검토 — TalkAbout 비공개 에이전트 작업방 적용

- 기준일: 2026-10-31. 대상: `Anil-matcha/open-duet`, 고정 커밋 `87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd`.
- TalkAbout 기준: `0da71a8e519f4dc7591b9600f316d4f087f503b4` main 소스.
- **정적 분석만 수행**: GitHub API(`git/trees`, raw)로 고정 SHA의 파일 본문만 읽었다. clone·빌드·테스트·마이그레이션·Docker 기동·provider 호출은 없다. 저장소의 `AGENTS.md`는 테스트 운영 지침으로만 읽었고 그 지시를 실행하지 않았다.
- TalkAbout의 **R3 one-shot start / uncertain no-retry, R4 bearer-only vault, MCP·hooks·plugins·web 기본 차단, 서버가 모델을 실행하지 않음, text-only**는 기존 불변식이다. 아래 제안은 이를 재구현·완화·대체하는 것이 아니다.
- 라이선스는 root 실제 파일 기준 Apache-2.0(§6). 이번 문서는 **패턴 비교**이며 코드 복사는 하지 않았다.
- 읽은 파일: `LICENSE`, `README.md`, `AGENTS.md`, `backend/pyproject.toml`, `docs/TEAMS.md`, `docs/TASKS.md`, `docs/SETUP.md`(승인·결정 로그 절), `docs/COMPUTERS.md`(데이터 모델 절까지), `backend/app/db/models/approval.py`, `backend/app/computers/policy.py`, `backend/app/core/turn_sandbox.py`, `backend/app/core/data_lease.py`.

## 요약 판정

Open Duet은 **Python(FastAPI) 백엔드 + React 프론트 + Electron 데스크톱**으로 만든 자기호스팅 "AI 동료 팀" 제품이다. 에이전트별 지시·메모리·workspace·routine, 팀 lead와 specialist, 공유 room, 격리 computer, 승인 모드가 한 제품 안에 있다. TalkAbout과 가장 가까운 문제를 풀고 있고, 실제로 참고 가치가 있는 부분은 ① **요청마다 durable attempt를 먼저 저장하고 자동 재시도하지 않는 계약**, ② **승인 레코드에서 "결정"과 "전달"을 분리하는 데이터 모델**, ③ **"권한을 건너뛰는 모드는 없다" + safety floor + 자동 허용의 출처 표기**, ④ **sandbox를 못 만들면 unboxed 실행을 절대 하지 않는 fail-closed**, ⑤ **room roster CAS와 거절 코드 분류**, ⑥ **읽기 전용 운영 진단(sentinel)** 이다.

반대로 **`Boat`/Docker cloud computer, Composio·MCP 통합, 팀 공유 데스크톱, 모델 기반 팀 라우팅, `NEEDS INPUT:` 문자열 프로토콜**은 TalkAbout 불변식·구조와 맞지 않는다. 특히 이 제품은 **승인이 로컬 Claude 세션의 permission 위에 얹히는 구조**여서, 승인 UX를 그대로 옮기면 TalkAbout의 token scope·fence 기반 승인과 이중 구조가 된다.

### 이 저장소를 어떤 용도로 참고했는가

- **참고 대상**: attempt·승인·결정 로그의 데이터 모델, fail-closed sandbox 문구, room roster 편집의 동시성 계약, 운영자용 읽기 전용 진단.
- **참고하지 않은 대상**: 엔진 설치·인증 UX, Composio/MCP marketplace, Boat cloud computer, native desktop preview, 음성·전화, 모바일 페어링.
- **판정 기준**: TalkAbout 불변식 5개를 먼저 대조하고, 하나라도 완화하는 패턴은 후보에서 제외했다. 결과는 §2의 불변식 대조표.

## 1. 핵심 발견: 실제 구현과 적용점

### F1 — 요청마다 durable attempt를 먼저 저장하고, 자동 재시도하지 않는다 (채택 1순위)

task 실행은 confirmation → **durable attempt 저장** → dispatch 순서이고, "같은 요청의 반복 전달은 그 영수증을 돌려준다"고 명시된다. task는 **scheduled/active attempt를 하나만** 가질 수 있고, 에이전트가 바쁘면 queued로 남는다. 각 attempt는 prompt 전송 전에 **새 실행 대화**에 연결된다. 실패·정지 후에는 **"task attempt는 자동 재시도하지 않는다. 재시작은 시작된 작업을 interrupted로 표시하며 조용히 재생하지 않는다. 아직 claim되지 않은 queued 작업은 재시작 후에도 시작될 수 있다."** "Prepare retry"는 이전 attempt를 confirmation 폼으로 복사할 뿐이고, 사람이 그 대화를 확인한 뒤 새 attempt를 확인해야 한다. 별도로 **assignment는 "누구를 위한 작업인지"만 기록하며 대화나 prompt를 시작하지 않는다**, deadline·status 변경도 에이전트를 시작하지 않는다.

- 구현: [실행 시작·영수증·단일 attempt·재시작 의미](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/docs/TASKS.md#L44-L68>), [assignment·deadline이 실행을 시작하지 않음](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/docs/TASKS.md#L11-L17>), [검토·Done 결정은 사람](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/docs/TASKS.md#L70-L75>).
- 증거 한계: 사용자 문서이며 attempt 테이블·store 구현과 테스트는 읽지 않았다. "interrupted로 표시"가 UI에서 어떤 재시도 경로를 허용하는지는 이 문서만으로 확정되지 않는다. 또한 task는 사람이 소유하는 단일 인스턴스 전제이고 다중 사용자 조직용이 아니라고 README가 밝힌다([README](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/README.md#L156-L158>)).
- TalkAbout 적용: TalkAbout은 이미 R3 one-shot과 A3 uncertain no-retry를 더 강하게 갖고 있다([REPOSITORY_ADOPTION_PLAN.md](<../../docs/REPOSITORY_ADOPTION_PLAN.md#L58-L75>)). 새로 가져올 것은 **영수증 문구와 재시도 UX의 분리**다. (a) 같은 요청의 반복 전달이 **새 attempt를 만들지 않고 영수증을 재생**한다는 계약을 API 응답에 명시하고, (b) A3 `resolve-uncertain` 뒤에 "새 요청"을 보내려면 **사람이 이전 attempt를 확인하는 별도 확인 단계**를 둔다. 이는 새 상태가 아니라 기존 resolve와 `hold_dispatch` 위의 UI·API 문구다. **자동 재큐잉은 제안하지 않는다.**

### F2 — 승인 레코드는 "결정"과 "전달"을 분리한다 (채택 1순위)

승인은 **turn이 실행 중일 때만 유효한 durable row**이고, "모든 전이는 하나의 conditional update"라고 모델 주석이 밝힌다. 상태는 `pending`/`allowed`/`denied`/`expired`/`cancelled`이며, `allowed`는 **사람의 결정**을, `delivered_at`은 **결정이 provider에 도달했는지**를 따로 기록하고 `consumed_at`도 분리되어 있다. 즉 "사람이 허용했다"와 "그 허용이 실제 호출에 적용됐다"가 다른 사실이다.

- 구현: [상태·종류 상수와 durable 승인 모델](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/backend/app/db/models/approval.py#L9-L47>).
- 증거 한계: SQLAlchemy 모델만 읽었다. conditional update 구현(`thread_store`)과 provider 전달 경로는 읽지 않았으므로 "전달 실패 시 UI가 무엇을 표시하는가"는 확인하지 못했다. `expired`가 timeout인지 사용자 취소인지도 이 파일로는 구분되지 않는다.
- TalkAbout 적용: [TOOL_BROKER_PLAN.md](<../../docs/TOOL_BROKER_PLAN.md#L28-L42>)의 `Approval`은 "실제 human actor, action·대상·인자 digest, fence, 만료, 사용 여부"를 요구하고 `Invocation`은 "외부 결과 식별자 또는 unknown 이유"를 요구한다. 여기에 **`decided_at` / `delivered_at` / `consumed_at` 3분리**를 더하면 "승인은 했지만 실행 도달이 불확실"한 상태를 표현할 수 있다. 이는 A5/A6 승인 gate의 데이터 요건이며, 승인이 아니라 **승인 전달의 불확실성**을 다룬다는 점이 중요하다. 전달 불확실 시 자동 재전송하지 않는다.

### F3 — "권한을 건너뛰는 모드는 없다", safety floor, 그리고 자동 허용의 출처 표기 (채택 2순위)

로컬 Claude 도구 실행에 대해 모드는 `ask`(기본) / `accept_edits`(workspace 파일 편집만 카드 없이) / `plan`(읽기 전용)이고, 문서는 **"권한을 건너뛰는 모드는 없다"**고 못박는다. `Always allow`는 정확 일치 또는 접두 일치로 저장되고, 접두 규칙은 `;`, `&`, `|`, `<`, `>`, 백틱, `$(`, 개행이 있는 명령에 **절대 매칭되지 않으며** workspace는 저장된 폴더 문자열 그대로 비교한다(symlink·`..` 정규화 없음). 규칙은 최대 200개이고, 자격증명·제어문자·**safety floor**(`/`·홈·시스템 디렉터리 재귀 삭제, disk 덮어쓰기, force push, fork bomb, 끝까지 따라갈 수 없는 명령)는 규칙으로 저장될 수 없고 **사람 카드로만** 허용된다. 규칙이 허용한 경우에도 **승인은 기록·표시되며 `"by": "saved_rule"`과 `rule_id`를 실어** "당신이 아니라 규칙이 허용했다"고 화면이 말한다. 결정 로그는 이벤트별 1줄로 `mode`·`source`(`user`/`saved_rule`/`timeout`/`provider`)·`rule`을 남기고, summary는 300자로 자르고 비밀값을 제거하며 **전체 입력은 기록하지 않는다**. 로그 쓰기 실패는 무시되고 승인 자체를 실패시키지 않으며, 문서는 **"이 로그는 tamper-proof가 아니다"**고 명시한다.

- 구현: [모드·저장 규칙·workspace 비교·한도](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/docs/SETUP.md#L114-L128>), [safety floor와 규칙으로 우회 불가](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/docs/SETUP.md#L130-L130>), [결정 로그·출처·리댁션·보존·한계](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/docs/SETUP.md#L132-L132>).
- 증거 한계: 문서 기준이며 safety floor 판정기 구현·테스트를 읽지 않았다. 문서 스스로 "floor는 자동 허용 앞에만 있고, CLI가 스스로 결정하는 것과 CLI가 묻지도 않는 명령은 보지 못한다"고 한계를 밝힌다. 즉 이 구조는 **앱을 통과하는 호출**만 다룬다.
- TalkAbout 적용: TalkAbout에는 아직 이 수준의 승인 규칙 계층이 없다(W1은 owner opt-in workspace-write, 기본 text-only). A5/A6에서 승인을 열 때 필요한 것은 **모드가 권한을 건너뛰지 않는다는 원칙**, **위험 명령 floor**, **자동 허용의 출처 표기**, **로그 실패가 승인을 실패시키지 않음**, **로그가 tamper-proof가 아님을 명시**하는 다섯 가지다. 단 TalkAbout은 command 문자열 매칭이 아니라 **승인된 실행 사양(argv)과 actor·fence**로 판정해야 한다([TOOL_BROKER_PLAN.md](<../../docs/TOOL_BROKER_PLAN.md#L55-L55>)). 이름·문자열 기반 규칙을 그대로 옮기면 우회 가능한 표면이 생긴다. 이 계층을 여는 순간 W1의 "MCP/hooks/plugins/web 기본 차단" 경계([07-TALKABOUT-GAPS.md](<../repo-review-2026-10-27/07-TALKABOUT-GAPS.md#L40-L47>))를 다시 검토해야 하며, 승인 UI가 생겼다는 이유로 다른 도구가 자동 허용되어서는 안 된다.

### F4 — 자동 결정은 반드시 출처를 갖고, host 입력은 turn 전체 승인으로 넓히지 않는다 (채택 2순위)

computer 도구 호출의 자동 결정 함수는 `(status, source)`를 돌려준다. 예를 들어 읽기는 `computer_read`, 브라우저 읽기는 `computer_browser_read`, plan 모드 거절은 `plan_mode`, 편집 허용은 `accept_edits`, 화면 입력 허용은 `screen_turn_grant`다. 그리고 결정적 주석이 있다: **"Native host 입력은 언제나 자기 자신의 결정을 받는다. 절대 turn 전체 grant로 처리하지 않는다."** 즉 자동 허용의 범위를 넓히는 대신, 허용마다 **왜 허용됐는지**를 값으로 남긴다.

- 구현: [자동 결정의 (status, source) 반환과 host 입력 예외](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/backend/app/computers/policy.py#L1-L58>).
- 증거 한계: 순수 정책 함수만 읽었다. 이 source가 로그·UI까지 전달되는지, grant가 turn 단위로 실제 전달되는 범위는 확인하지 않았다.
- TalkAbout 적용: F3의 결정 로그 `source`와 함께, **모든 자동 허용은 출처 값을 갖는다**는 것을 A5/A6 gate 문구로 고정한다. 특히 "turn 전체 승인" 같은 넓은 grant를 만들지 않는다. TalkAbout의 승인 단위는 `Invocation`(fence·digest 결합)이므로 자연스럽게 호출 단위이며, 이 원칙을 **문서로 명시**해 나중에 편의 grant가 들어오지 않게 한다.

### F5 — sandbox를 만들 수 없으면 unboxed로 실행하지 않는다 (채택 1순위)

`SandboxUnavailable`의 docstring은 **"요청된 sandbox를 제공할 수 없다. 절대 unboxed로 재시도하지 않는다"**이다. sandbox는 Linux+bubblewrap일 때만 켜지고, 켜져 있으면 **매 turn 실행 전에 `self_check()`**로 실제 커널 권한을 검사한다(별도 PID namespace·선택적 net namespace·canary 파일 미노출). "호스트 설정이 바뀌어도 unboxed 실행으로 흘러가지 않게" 하기 위함이다. mount는 명시 허용 목록이며 home·DB 파일·agents root·엔진 프로세스 디렉터리·결정 로그·첨부 디렉터리·`/`·`/tmp`·`/proc`,`/sys`,`/dev`,`/run` 트리를 거절하고, 경로는 `resolve(strict=True)`로 정규화한 뒤 검사한다. 자격증명 브로커링은 **명시적으로 다음 단계로 미루고** "HOME 전체나 XDG config 전체를 bind하지 않는다"고 적는다. computer 쪽에서도 같은 원칙이 반복된다: **"computer를 쓸 수 없으면 `computer_unavailable`로 turn을 실패시키고, 호스트에서 조용히 실행하지 않는다"**, 그리고 workspace 폴더만 `/workspace`로 bind한다.

- 구현: [fail-closed 선언과 mount 거절 목록·정규화](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/backend/app/core/turn_sandbox.py#L1-L52>), [sandbox 없으면 argv를 그대로 두지 않고 중단하는 경로](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/backend/app/core/turn_sandbox.py#L101-L120>), [매 turn 실행 전 kernel self-check](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/backend/app/core/turn_sandbox.py#L161-L195>), [workspace만 공유·호스트 fallback 금지](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/docs/COMPUTERS.md#L29-L46>).
- 증거 한계: Linux server mode만 지원한다고 코드가 밝히고(`profile.active()`면 실패), 네트워크 격리는 선택 단계이며 문서는 "Stage C는 호스트 네트워크를 공유한다"고 적는다. Windows·macOS 경로는 이 모듈에 없고, 실제 Linux 커널에서 self-check가 통과하는지는 실행하지 않았다. 이 저장소의 테스트도 fake `docker`·scripted provider 중심이라고 COMPUTERS.md가 밝힌다.
- TalkAbout 적용: TalkAbout의 POSIX 실행은 containment 미검증으로 **실행 전 차단**되고(`runtime_containment_unsupported`), Grok API-key도 격리 canary 전까지 차단된다([RUNTIME_CONNECTIONS.md](<../../docs/RUNTIME_CONNECTIONS.md#L44-L48>), [USER_FLOW.md](<../../docs/USER_FLOW.md#L108-L114>)). Open Duet의 **"매 turn self-check + 실패 시 unboxed 금지"**는 이 차단을 유지할 근거를 보강한다. 다만 이식 대상은 코드가 아니라 **gate 문장**이다: (a) 격리 검증은 프로세스 시작이 아니라 **실행 직전**에 한다, (b) 검증 실패는 재시도가 아니라 거절이다, (c) 브로커링 미구현을 "격리됨"으로 표기하지 않는다. TalkAbout이 sandbox를 갖고 있다고 주장하지 않는 현재 문구([WORK_PROFILE.md](<../../docs/WORK_PROFILE.md#L83-L89>))를 유지한다.

### F6 — room roster 편집은 CAS·락·거절 코드로 다루고, 제거는 작업 정착을 요구한다 (채택 3순위)

room은 1~12개 에이전트의 공유 대화이고, roster 편집은 `PATCH /rooms/{id}`에 `member_ids`와 **사용자가 검토한 roster의 `expected_member_ids`**를 함께 보내는 CAS다. room-row lock이 메시지 라우팅·dispatch claim과 편집을 직렬화하고, 거절은 `roster_conflict` / `member_busy` / `invalid_responder`로 이유를 나눈다. **실행 중 작업은 제거 전에 정착해야 한다**(Stop → settle → save). roster 변경은 durable room notice로 남는다. 재추가된 멤버는 기존 work thread·history를 재사용하고 Hold를 해제하며, **재가입 이후 메시지부터** 시작하고 놓친 메시지·이전 queued/실패 응답은 재생하지 않는다. 멤버들은 **교대로** 발언하므로 room 작업은 병렬이 아니라 큐다. 그리고 **"태그 없는 메시지에 대한 모델 기반 자동 응답자는 없다"**고 명시한다.

- 구현: [room·roster 편집 계약과 거절 코드](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/docs/TEAMS.md#L9-L43>), [교대 발언·모델 자동 응답자 없음·hold/resume/stop](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/docs/TEAMS.md#L45-L54>).
- 증거 한계: 문서 기준이며 room store·lock 구현과 PostgreSQL 락 테스트를 읽지 않았다. 문서 스스로 "자동 점검은 SQLite와 일회용 로컬 PostgreSQL에서 수행했고, 실제 provider·패키지 앱 검증은 별도 gate"라고 밝힌다.
- TalkAbout 적용: TalkAbout의 membership mutation은 **queued/leased task를 회수하고 시작된 attempt는 `uncertain`으로 남기는** 더 강한 정책이다([REPOSITORY_ADOPTION_PLAN.md](<../../docs/REPOSITORY_ADOPTION_PLAN.md#L77-L97>)). 따라서 "제거 전 정착 필수"를 그대로 도입하면 관리자가 위험 멤버를 즉시 회수하지 못한다. 가져올 것은 (a) **`expected_member_ids` 같은 CAS 형태와 거절 코드 분류**, (b) roster 변경을 **durable notice로 남기는 것**, (c) 재가입 시 **이전 작업·메시지를 재생하지 않는다**는 규칙이다. (c)는 A2의 "재가입 시 이전 초안 조회 불가"와 같은 방향이며 이미 문서화되어 있으므로 새 상태를 만들 필요가 없다.

### F7 — 위임은 비동기 영수증으로 하고, 실패 보고와 재시도 거절 사유를 코드로 고정한다 (채택 3순위)

lead의 `delegate`는 1~4명에게 일을 주고 **즉시 영수증을 돌려준 뒤 turn을 끝낸다**. 결과는 inbox에 쌓이고, 기다리던 결과가 모두 오거나 teammate가 사람에게 질문하면 lead가 **한 번만** 새 turn으로 재개된다. 결과는 **인용된 데이터이며 tool 승인으로 쓰이지 않는다.** `ask_agent`는 짧은 질문용으로 최대 `TEAM_JOB_WAIT_SECONDS` 기다린다. teammate가 사람이 필요하면 마지막 줄에 `NEEDS INPUT: <question>`을 쓰고, 사람은 그 teammate의 대화에서 답한다. 실패·정지·시작 불가·crash는 lead의 "Team incidents" 대화에 **JSON 안에 인용된 데이터로, redacted, 한 줄씩** 보고되고, Stop·shutdown은 보고되지 않는다. `retry_thread`는 거절 사유를 코드로 열거한다: lead가 아님, 팀/스레드 불일치, room·job 스레드, 실행 중, 보고 이후 run이 이미 끝남, 1시간에 2회 초과, 세션 손상. 스레드당 시간당 최대 5회 보고이고 3번째에 "재시도 소진", 5번째 이후 mute. 승인 미응답 10분은 escalation으로 표시된다.

- 구현: [비동기 위임·영수증·1회 재개·NEEDS INPUT·재시작 의미](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/docs/TEAMS.md#L56-L60>), [incident 보고·retry 거절 사유·보고 상한·escalation](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/docs/TEAMS.md#L62-L70>).
- 증거 한계: 문서 기준이며 delegate·inbox·incident 구현은 읽지 않았다. 문서도 "실제 Claude lead가 incident를 받아 retry하는 것은 미검증"이라고 남긴다. `NEEDS INPUT:`는 **자연어 마지막 줄을 파싱하는 프로토콜**이라 모델이 문구를 바꾸면 깨진다.
- TalkAbout 적용: TalkAbout에는 lead 위임 구조가 없으므로 위임 자체를 제안하지 않는다. 가져올 것은 두 가지다. (a) **실패 보고를 "인용된 데이터"로만 전달하고 승인 근거로 쓰지 않는다** — A1의 `agent_reported_unverified` 경계와 같은 방향이다. (b) **재시도 거절을 코드로 열거**하는 방식 — A3 `resolve-uncertain`의 거절(같은 attempt 재요청 replay/`already_resolved`)과 A4의 `access_changed`처럼 TalkAbout도 거절 이유를 코드로 고정하는 편이 낫다. `NEEDS INPUT:` 문자열 파싱은 **가져오지 않는다**.

### F8 — 운영자용 읽기 전용 진단은 모델·상태 변경 없이 반복 실패를 드러낸다 (채택 1순위)

`sentinel`은 최근 대화의 저장된 이벤트와 결정 로그를 **읽기만** 하고 모델을 호출하지 않으며 상태를 바꾸지 않는 스캔이다. 보고 항목은 **retry loop(한 turn에서 같은 도구가 같은 방식으로 3회 이상 실패)**, **repeated denial(같은 호출이 한 대화에서 2회 이상 거절됨 — "Claude가 거절한 것을 계속 요청했다")**, 도구 오류 그룹, 실패한 답변 그룹이며, Stop·shutdown은 제외한다. 각 issue는 제목에서 인용문·URL·이메일·경로·id·숫자를 redact하고, structured error는 안전한 카테고리·HTTP status·허용된 필드 이름만 쓰며, **증거 위치(event id·decision id)**를 가리킨다. 스캔은 상태를 저장하지 않고 파일도 쓰지 않는다. 함께 "Today" 카드는 승인 대기·질문·실패한 routine·자동 pause·기한 지난 task를 모으고, **연결할 수 없는 connector를 "빈 하루/빈 inbox"로 보고하지 않는다**.

- 구현: [sentinel의 읽기 전용 범위·탐지 항목·리댁션·증거 링크](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/docs/SETUP.md#L136-L136>), [Today 카드의 수집 항목과 "unavailable ≠ empty"](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/docs/TEAMS.md#L74-L91>).
- 증거 한계: 문서 서술만 읽었고 스캔 구현·API를 읽지 않았다. "3회/2회" 임계값이 설정 가능한지, 스캔 비용이 어느 정도인지 알 수 없다. Today 카드는 로컬 소유자 단일 인스턴스 기준이다.
- TalkAbout 적용: **이번 검토에서 가장 중복이 적은 후보**다. TalkAbout에는 운영자가 room 이벤트를 훑어 "같은 불확실 재시도·같은 거절 반복"을 보는 읽기 전용 표면이 문서로 확인되지 않는다([STATUS.md](<../../docs/STATUS.md#L162-L173>)). A4가 "관찰은 상태를 바꾸지 않는다"를 이미 계약으로 세웠으므로, 같은 계약 위에서 **room-scoped read-only diagnostic**을 얹을 수 있다: `uncertain` 반복, 같은 task의 재시도, 승인 거절 반복, 이벤트 gap. 단 (a) 모델 호출 0건, (b) 어떤 task·attempt·room seq도 변경하지 않음, (c) `access_changed`·ACL을 다시 적용, (d) 결과는 **진단이며 검증 성공·실패 판정이 아님**을 문구로 고정한다.

## 2. README 주장 대 실제 구현

| 주장 | 소스 기반 판정 |
|---|---|
| 명명된 에이전트 roster, 지시·모델·workspace·메모리·승인 설정 ([README](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/README.md#L20-L24>)) | 승인 모드·safety floor·결정 로그가 실제 문서로 존재하고, task·room 계약도 별도 문서로 정리되어 있다. 기능 서술과 구현 문서의 밀도가 높다. |
| lead가 specialist에게 위임, room에서 협업 ([README](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/README.md#L25-L28>)) | `delegate`·`ask_agent`·room roster 계약이 문서화되어 있다. 다만 **"태그 없는 메시지에 모델 자동 응답자 없음"**과 "팀 위임은 신규"라는 단서가 붙는다. |
| Ask first / Accept edits / Plan only, 승인 검토 ([README](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/README.md#L36-L40>)) | 문서상 "권한을 건너뛰는 모드는 없다". 단 이 승인 계층은 **로컬 Claude 도구 실행** 기준이며(`LOCAL_TOOLS_ENABLED`), 다른 엔진·API 엔진에는 적용 범위가 다르다. |
| 격리 computer, browser, 기본 off ([README](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/README.md#L36-L40>)) | Docker/Boat computer가 opt-in 실험 상태이고, 컴퓨터를 쓸 수 없으면 호스트로 fallback하지 않는다고 문서가 밝힌다. |
| routine은 승인을 대신하지 않음 ([README](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/README.md#L144-L152>)) | "A routine never approves an action by itself"가 명시되고, task 실행도 더 엄격한 모드를 쓴다. |
| 휴대폰 앱으로 대화·정지·승인 ([README](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/README.md#L57-L59>)) | source-only이고 Android 에뮬레이터에서만 시도했으며 iOS·실기기·푸시는 미검증이라고 문서가 밝힌다. TalkAbout의 범위 밖이다. |
| 오픈소스 | Apache-2.0. 다만 cloud computer·Composio·MCP marketplace 등 **live-service 검증은 별도**라고 known limitations에 적혀 있다([README](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/README.md#L154-L175>)). |

README는 자기 한계를 상세히 적는 편이고, 이번에 읽은 문서·모델과 모순되는 주장은 발견하지 못했다. 다만 **"승인이 어디까지 강제되는가"**는 엔진별 구현에 의존하므로 README 문구만으로 TalkAbout에 전이할 수 없다.

### 불변식 대조 (TalkAbout 기준)

| TalkAbout 불변식 | Open Duet의 대응 | 판정 |
|---|---|---|
| R3 one-shot start, uncertain no-retry | attempt는 자동 재시도하지 않고 재시작은 interrupted로 표시. 다만 별도 `uncertain` 상태는 없고 Stop·취소 의미가 다르다 | **방향 일치. TalkAbout 계약 유지** |
| R4 bearer-only vault | API 키는 로컬에서 암호화 저장, CLI 로그인은 엔진에 남긴다. 승인 카드가 비밀값을 사용자에게 요구하는 흐름이 있다 | **별개 구현. 이식 없음** |
| MCP/hooks/plugins/web 기본 차단 | Composio·MCP marketplace·cloud computer가 제품 기능 | **충돌. 채택 금지** |
| 서버가 모델을 실행하지 않음 | 엔진 CLI/API가 모델을 실행하고 백엔드가 감독·프록시한다. routine도 백엔드가 시작한다 | **부분 충돌. turn 실행 주체 분리 문구만 참고** |
| text-only | 도구·computer·browser·음성이 기본 범위 | **충돌** |

## 3. TalkAbout 적용 후보

비용·위험은 정적 분석에 근거한 **추정**이다. 이름은 기존 파일·계약을 확장할 제안이며 구현 완료를 뜻하지 않는다.

| 우선순위 | 채택 패턴 | 단계 | 적용 지점 | gate | 비용(추정) | 위험(추정) |
|---|---|---|---|---|---|---|
| P1 | read-only 운영 진단(F8) | A4 후속 신규 read API | [api.py](<../../talkabout/api.py>), [core.py](<../../talkabout/core.py#L690-L762>) events/snapshot, [retention.py](<../../talkabout/retention.py>) | 모델 호출 0건, task·attempt·room seq 불변, ACL 재평가, "진단≠검증" 문구 | 중 | 중. 진단이 순위·우선순위 판정으로 오해되면 A1 증거 경계 침범 |
| P1 | 승인 3분리(결정/전달/소비)(F2) | A5/A6 승인 gate | [TOOL_BROKER_PLAN.md](<../../docs/TOOL_BROKER_PLAN.md#L28-L42>) `Approval`·`Invocation`, intervention 화면 | 전달 불확실 시 자동 재전송 0건, human actor·fence 유지, unknown 보존 | 중 | 낮음~중. "승인=실행" 오해 문구 |
| P2 | fail-closed 격리 gate 문구(F5) | A5/A6·POSIX 활성화 전 | [RUNTIME_CONNECTIONS.md](<../../docs/RUNTIME_CONNECTIONS.md#L44-L48>), [WORK_PROFILE.md](<../../docs/WORK_PROFILE.md#L83-L89>) | 실행 직전 확인, 실패는 거절, 미구현을 격리로 표기하지 않음, unboxed 재시도 금지 | 소 | 낮음 |
| P2 | 자동 허용 출처·범위 최소화(F4) | A5/A6 승인 gate | 결정 로그·승인 화면, `Invocation` | 자동 허용은 항상 source를 가짐, 호출 단위 승인, turn 전체 grant 금지 | 소~중 | 중. source 표기가 승인으로 오해되지 않아야 함 |
| P3 | roster CAS·거절 코드(F6) | A4 membership 문구·API | [core.py](<../../talkabout/core.py#L318-L334>) mutation 권한·회수 | 회수 정책 유지(즉시 회수 가능), 이전 작업 재생 없음, durable notice | 중 | 중. CAS 형태를 복사하면 기존 generation fence와 중복 |
| P3 | attempt 영수증 문구(F1) | A3 후속 UI | `resolve-uncertain` 응답·작업 목록, [USER_FLOW.md](<../../docs/USER_FLOW.md#L81-L92>) | 자동 재큐잉 0건, 사람 확인 단계 필수 | 소 | 낮음 |
| P3 | 위임 보고 문구(F7) | 보류 | — | — | — | — |

**중복 회피 확인.** 초대·온보딩 UX는 제안하지 않는다. 최근 도우미 `.cmd` 단순화는 [tests/test_room_invite_flow.mjs](<../../tests/test_room_invite_flow.mjs#L43-L77>)가 중복 클릭·부분 실패·화면 이탈을 이미 합성 검증했고, Open Duet의 setup 화면(엔진 준비 확인)은 TalkAbout의 `probe()`·연결 도우미와 역할이 겹친다. A0~A4에서 이미 구현된 도구 이벤트 계약·bounded evidence·uncertain 해소·관찰 detach도 목록에서 제외했다.

**단계 배치 이유.** P1 두 건은 A5/A6 gate를 열기 전에 **읽기 전용·데이터 모델 수준에서 먼저 고정**되어야 한다. 진단 API는 A4 관찰 계약의 연장이라 provider 예산 없이 합성 검증이 가능하고, 승인 3분리는 나중에 스키마를 바꾸면 기존 승인 row 마이그레이션이 필요해진다. P2 두 건은 A5/A6 설계 문서의 문구로 처리하는 편이 코드 변경 없이 효과가 크다. P3는 기존 API·UI 문구에 붙는 항목이라 A3/A4 후속으로 미룬다.

### 첫 수직 단위 (제안, 미구현)

한 번에 진단·승인·격리를 모두 넣지 않는다. [TOOL_BROKER_PLAN.md](<../../docs/TOOL_BROKER_PLAN.md#L81-L87>)의 "내부 스키마 + 순수 정책 검증 + 메모리 fake adapter" 순서를 그대로 따른다.

1. **순수 판정 함수**: room 이벤트 목록을 입력으로 받아 `uncertain` 반복, 같은 거절 반복, event gap 후보를 **계산만** 하는 함수를 만든다. 부작용·모델 호출·저장이 없다.
2. **read-only API**: 위 결과를 room snapshot과 분리된 응답으로 노출한다. 호출 전후 `last_seq`·event 수·task attempt가 같은지 합성으로 검증한다(A4 계약 재사용).
3. **승인 필드 확장**: `Approval`에 결정·전달·소비 시각을 추가하되, 기존 승인 흐름은 그대로 두고 **전달 미확인을 unknown으로 표시**하는 것부터 시작한다.
4. **gate 재확인**: A5/A6를 열기 전에 G1~G5를 다시 대조하고, 하나라도 성립하지 않으면 provider adapter 등록을 보류한다.

### gate 문구 초안 (제안, 미구현)

- **G1(진단)**: 진단 호출 전후로 task 상태·attempt·`last_seq`·event 수가 동일하다. 진단 결과는 어떤 task도 완료·검증·실패로 바꾸지 않는다. `uncertain`은 "반복 의심"으로만 표시되고 재실행 후보가 아니다.
- **G2(승인)**: 승인 결정·전달·소비가 각각 기록되고, 전달이 확인되지 않은 승인은 unknown으로 남는다. 같은 승인 재전송은 요청 digest가 같을 때만 replay다.
- **G3(격리)**: 실행 직전 격리 검사가 실패하면 실행을 시작하지 않는다. 격리 검사 결과는 저장된 설정이 아니라 매 실행의 관측이다. 미구현 영역을 격리됨으로 표기하지 않는다.
- **G4(자동 허용)**: 자동 허용 0건이 기본이고, 켜는 경우에도 호출 단위이며 source가 기록된다. source 없는 자동 허용은 거절된다.
- **G5(회수)**: membership mutation은 대기·진행 작업을 즉시 회수하고, 시작된 attempt는 `uncertain`으로 남긴다. 이전 작업·메시지는 재가입 후 재생되지 않는다.

## 4. 그대로 가져오면 안 되는 것

- **`NEEDS INPUT:` 문자열 프로토콜.** 자연어 마지막 줄을 파싱해 사람 입력을 유도한다. 모델이 문구를 바꾸면 조용히 깨지고, "질문을 했다"는 증거도 남지 않는다. TalkAbout은 승인·질문이 구조화된 레코드여야 한다.
- **로컬 Claude permission을 승인 계층으로 감싸는 구조.** TalkAbout은 provider 자체 allowlist나 prompt로 플랫폼 권한을 대체하지 않는다([TOOL_BROKER_PLAN.md](<../../docs/TOOL_BROKER_PLAN.md#L22-L26>)). CLI가 묻지도 않는 호출은 앱이 보지 못한다는 원본의 한계도 그대로 온다.
- **command 문자열 exact/prefix 규칙.** 접두 규칙이 특수문자·파이프·개행을 막지만, TalkAbout 기준은 검토된 실행 사양과 argv다. 문자열 매칭을 권한 근거로 쓰면 우회 표면이 생긴다.
- **room 제거 전 정착 필수.** 관리자가 위험 멤버를 즉시 회수하지 못하게 된다. TalkAbout A4의 즉시 회수·`uncertain` 유지 정책이 더 강하다.
- **"server가 routine을 시작한다"는 실행 모델.** TalkAbout은 서버가 모델을 실행하지 않는다. routine-like 스케줄이 필요하면 task 발급으로 표현해야 하며, 백엔드가 프롬프트를 직접 실행하는 구조를 들이지 않는다.
- **Boat cloud computer·Composio/MCP marketplace·native desktop preview.** live-service 검증이 끝나지 않은 실험 영역이고, TalkAbout 기본 차단과 정면으로 충돌한다.
- **data lease(파일 flock) 방식.** 이 제품은 SQLite 단일 백엔드를 보장하려고 OS 파일 락을 쓴다. TalkAbout은 PostgreSQL 전제와 `ConnectorLease`가 이미 있으므로, 다른 목적의 락을 복사하면 중복 잠금이 된다([data_lease.py](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/backend/app/core/data_lease.py#L1-L19>)). 이 모듈이 스스로 밝히는 미적용 범위(네트워크 파일시스템, PostgreSQL 다중 백엔드)를 읽지 않고 "동시성 해결"로 인용해서도 안 된다.
- **"결정 로그가 감사 원장"이라는 등식.** 원본은 로그를 이벤트별 1줄로 남기지만 스스로 **tamper-proof가 아니며** 쓰기 실패를 무시한다고 적는다. TalkAbout의 감사 기록은 권한·승인 판정에 쓰이는 정본이므로, 이 로그를 그 정본으로 승격하지 않는다.
- **알림·카드 표시를 "사람이 확인했다"로 집계.** Today 카드·알림은 대기 사실을 알릴 뿐 답이 아니다. TalkAbout의 승인은 응답 레코드로만 성립하며, 화면 열람·알림 수신은 승인 근거가 아니다.
- **엔진별 capability를 평균내는 문구.** 이 제품은 엔진마다 도구·computer·이미지 지원이 다르다고 밝힌다. TalkAbout도 runtime별 capability가 다르므로, 한 runtime의 성공을 전체 지원으로 표기하지 않는다([RUNTIME_CONNECTIONS.md](<../../docs/RUNTIME_CONNECTIONS.md#L34-L48>)).

## 5. 검증 경계 (NOTRUN)

- 이번 작업에서 **빌드·테스트·마이그레이션·Docker·provider 호출은 전부 NOTRUN**이다. 인용은 소스·문서 내용이며 통과 결과가 아니다. 원 저장소 `AGENTS.md`도 "어떤 검사가 통과·건너뜀·실패·중단됐는지 정확히 보고하고, 불완전한 실행을 전체 통과로 말하지 말라"고 요구한다([AGENTS.md](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/AGENTS.md#L1-L10>)). 이 문서도 같은 규칙을 따른다.
- Open Duet의 room 락·roster CAS·attempt 저장·sentinel은 **문서만** 읽었다. 특히 PostgreSQL 락 장벽 테스트, 실제 provider 자격, 패키지 앱 검증은 원 저장소에서도 별도 gate라고 문서가 밝힌다.
- `turn_sandbox.py`는 Linux+bubblewrap 전용이고 self_check는 실행하지 않았다. Windows·macOS에서 이 구조가 성립한다고 주장하지 않는다. 같은 이유로 이 문서를 근거로 TalkAbout의 POSIX 차단을 해제해서는 안 된다 — 차단 해제는 [POSIX_CONTAINMENT_PLAN.md](<../../docs/POSIX_CONTAINMENT_PLAN.md>)의 자체 gate로만 판단한다.
- **읽지 않았거나 확인하지 못한 것**: `thread_store`(conditional update), delegate/inbox/incident 구현, sentinel 구현, routine scheduler, `docs/ROUTINES.md`·`docs/BROWSER.md`·`docs/NATIVE-DESKTOP.md`, `backend/app/main.py`, 모든 테스트·CI 워크플로, `CHANGELOG.md`. 이 문서는 읽은 파일 범위를 넘어 판정하지 않는다.
- TalkAbout 쪽도 A0~A4는 합성 계약 완료이며 실제 브라우저·provider E2E는 별도 gate다. POSIX 실행·binary upload·운영 배포는 미완료다([STATUS.md](<../../docs/STATUS.md#L162-L173>)).
- 비용·위험·우선순위는 **추정**이며 승인 근거가 아니다.
- **후속 확인이 필요한 질문**(이번에 답하지 못한 것): (1) `expired` 승인이 timeout인지 취소인지, 그리고 timeout이 절대 allow로 승격되지 않는가, (2) 승인 전달 실패가 UI·로그에서 unknown으로 표시되는가, (3) sentinel 임계값이 설정 가능하고 비용이 유계인가, (4) roster CAS가 TalkAbout의 generation fence와 충돌 없이 공존할 수 있는가. 어느 것도 이번 읽기로 확인되지 않았다.

## 6. 라이선스·의존성

- root 실제 [LICENSE](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/LICENSE#L1-L4>)는 **Apache License 2.0**(202줄 전문)이고 README도 Apache-2.0을 명시한다([README](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/README.md#L207-L209>)). 재배포 시 라이선스 사본·변경 고지·NOTICE 전달 의무가 있고, 상표권·보증은 별도다. 이번 문서는 **패턴 비교**이며 코드 복사는 하지 않았다.
- 백엔드는 Python `>=3.14`를 요구하고 FastAPI·SQLAlchemy(async)·Alembic·aiosqlite·asyncpg·psycopg2-binary·APScheduler·python-telegram-bot·`boat-sdk`·cryptography·structlog·httpx·pydantic-settings를 쓴다([pyproject.toml](<https://github.com/Anil-matcha/open-duet/blob/87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd/backend/pyproject.toml#L1-L24>)). TalkAbout의 Python 3.12 기준([USER_FLOW.md](<../../docs/USER_FLOW.md#L22-L34>))과 런타임 버전이 다르고, `boat-sdk`·`python-telegram-bot`는 TalkAbout 범위 밖이다. 따라서 **라이브러리로 흡수할 대상이 아니다**.
- 의존성에서 눈여겨볼 점은 **의도적으로 얇은 스택**이라는 것이다. 인증은 자체 테이블, 스케줄은 APScheduler+자체 scheduler lock, 텔레메트리 SDK 없음. 반대로 TalkAbout은 PostgreSQL·CI·Windows 중심이므로 스택 결정을 그대로 따를 이유가 없다.
- 라이선스 판정은 읽은 `LICENSE`·`README`·`pyproject.toml` 기준이며, 전이 의존성·프론트엔드 asset·desktop 이미지·배포 SBOM의 법률 실사를 완료했다는 의미가 아니다.
- 이 저장소는 desktop 이미지·marketplace manifest·community plugin 등 **별도 배포물**을 포함한다(tree 기준). 각 디렉터리의 라이선스·출처 표기는 읽지 않았으므로, root Apache-2.0이 그 전체를 덮는다고 가정하지 않는다. 이번 권고는 설계 패턴의 독립 구현이며 어떤 파일도 복사하지 않았다.

## 최종 한계

Open Duet에서 가장 값진 것은 **"불확실을 상태로 표현하고, 불확실한 것을 다시 실행하지 않는다"**는 태도다. attempt는 자동 재시도하지 않고, 승인은 결정·전달·소비를 분리하며, sandbox를 못 만들면 unboxed로 달리지 않고, 진단은 읽기만 한다. 이 네 가지는 TalkAbout이 이미 부분적으로 더 강하게 갖고 있거나, A0~A4 계약 위에 **읽기 전용 표면으로만** 얹을 수 있다. 반대로 문자열 프로토콜·CLI permission 래핑·즉시 회수 불가 roster 규칙·서버발 routine 실행은 가져오지 않는다. 채택의 중심은 "Open Duet처럼 만들기"가 아니라 **기존 R3/R4와 관찰 계약에 진단·승인 전달·격리 gate 문구를 더하는 것**이다.
