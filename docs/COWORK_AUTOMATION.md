# 협업 자동화 사용·안전 계약 / Cowork automation

**LOCAL WORKTREE / NOT DEPLOYED · 합성(synthetic) 검증 기준 · PROVIDER/브라우저 E2E NOTRUN**

이 문서는 "사람이 방을 열고 → 에이전트를 초대하고 → 워커를 붙이고 → 사람이 시작하면 → 제한된 턴이 돌고 → 사람이 결과를 채택하는"
현재 구현의 **사용 순서와 안전 경계**를 코드 기준으로 정리합니다. 실행 가능한 것과 아직 없는 것을 섞지 않고, 확인 boolean이나
모델의 이해를 "증명"으로 읽지 않도록 경계를 명시합니다.

관련 문서: [RUNTIME_CONNECTIONS.md](RUNTIME_CONNECTIONS.md)(워커 연결), [PERMISSIONS.md](PERMISSIONS.md)(권한 카탈로그),
[AGENT_GUIDE.md](AGENT_GUIDE.md), [AGENT_INVITATIONS.md](AGENT_INVITATIONS.md)(위임 초대), [COLLABORATION_PROPOSALS.md](COLLABORATION_PROPOSALS.md)(협업 제안), [COLLABORATION_SAFETY_BOUNDARIES.md](COLLABORATION_SAFETY_BOUNDARIES.md)(실행·보안 경계), [REQUIRED_PREDECESSOR_INPUTS.md](REQUIRED_PREDECESSOR_INPUTS.md)(필수 선행 결과 계약), [STATUS.md](STATUS.md)(현재 상태 정본).

## 0. 한 줄 경계

> **자동화되는 것**: 초대 상태 전이, 준비된 워커에 대한 제한된 턴 배정, 발언/산출물 기록, 사람의 채택,
> 그리고 **사람이 직접 실행을 누른** work plan의 노드 큐잉·의존성 순서 진행.
> **자동화되지 않는 것**: 사람 없이 시작되는 계획 실행·자동 재시도, 부분 선택 실행, 에이전트별 비용 상한, 그리고 어떤 결과가
> 실제 provider에서 실행되었는지의 **독립 검증**.
> **발언 요청의 출처**: 멘션·답글 자체는 턴을 요청하지 않습니다. 요청은 ① 명시적 REST/MCP 호출(`request_turn`) 또는
> ② 워커가 방을 지정하여 **명시적으로 켠** 옵션(`--request-turn --request-turn-room ROOM_ID`)으로만 생깁니다.
> **사람 시작은 보편 필수 조건이 아닙니다**: `POST rooms`의 `start_policy` 기본값은 `automatic`이고, `manual`만 `preparing`으로
> 열려 사람의 control start를 요구합니다(`core.py:356-366`). agent가 만든 방도 기본은 즉시 `active`이며, §3의 시작 게이트는
> **수동(preparing) 방 흐름**에 해당합니다.

| 항목 | 현재 상태 |
| --- | --- |
| 3가지 모드·턴 배정·lease·예산 상한 | 구현 |
| 사람 시작 게이트(워커 준비 검사) | 구현 |
| `request_turn`(MCP 명시 발언 요청) | 구현 (task-23) |
| 답글 스레드 문맥(제한 hop) | 구현 (task-24) |
| 산출물 불변 버전 + 사람 채택 | 구현 |
| private work plan 초안 | 구현 — owner 전용 저장 + 편집기 UI |
| 계획 dispatch(사람의 명시 실행·의존성 순서 진행) | 구현 (task-32/33) — 자동 트리거·부분 선택 실행 없음 |
| 에이전트별 비용 quota / provider 과금 집계 | **미구현** |
| provider·브라우저 E2E | **NOTRUN** |

아래 "구현"은 **이 로컬 worktree의 코드 기준**이며, 배포·릴리스 결과를 뜻하지 않습니다.

## 1. 세 가지 모드

`talkabout/domain.py:84-96`의 `mode` 값과 `talkabout/modes.py`의 결정적 정책으로 동작합니다. 모델 심판은 없습니다.

| 모드 | 기본값 | 진행 방식 | 종료 조건 |
| --- | --- | --- | --- |
| `bounded_rounds`(기본) | ✅ | phase `proposal → review → synthesis`, 라운드마다 모든 eligible 참가자에게 한 번씩 | `limits.rounds` 도달 시 `round_limit` |
| `co_creation` | | phase `researcher → creator → reviewer`, 창작 에이전트가 산출물 버전을 제안 | phase 완료 후 `awaiting_adoption` → **사람 채택** 시 방 종료 |
| `free_discussion` | | 자동 배정 없음. **명시적 발언 요청(`request_turn`)이 있는 에이전트만** 차례를 받음 | 요청이 없으면 아무 턴도 나가지 않음 |

- `participant_permissions(mode)`: `co_creation`만 `room:contribute`를 추가로 요구합니다(`domain.py:23-24`). 즉 일반 토론 모드의
  참가자는 `room:read`+`task:claim` 등 실행 권한만으로 참여합니다.
- 시작 시점의 모드·참가자 권한이 맞지 않으면 시작이 거부됩니다(`participant_permissions_required`).
- 모드·주제를 바꾸면 방 `generation`이 오르고 **진행 상태·턴 요청·창작 산출물 참조가 초기화**됩니다(`core.py:789-792`).
  진행 중인 턴은 generation 불일치로 실패하므로, 모드 변경은 곧 "지금까지의 자동화 흐름을 버린다"는 뜻입니다.

## 2. 액터 구분 — human · agent · worker · credential

| 액터 | 무엇인가 | 무엇을 결정하나 | 증거가 아닌 것 |
| --- | --- | --- | --- |
| **human** | 사람 session(계정) | 초대·권한 부여·시작/정지·채택·불확실 결과 해소 | 로그인·확인 boolean이 "사람이 내용을 읽고 동의했다"는 증거는 아님 |
| **agent** | 등록된 신원(principal) | 자기 scope ∩ 방 membership 권한 안에서 행동 | 이름(예: Codex/Claude)은 어떤 프로그램이 실행됐다는 뜻이 아님 |
| **worker** | 하나의 credential에 묶여 연결된 실행 프로세스 | 배정된 task를 claim → 실행 → 보고 | 연결 상태는 "실행 능력"이지 "실제 모델 호출" 증거가 아님 |
| **credential** | scope 상한을 가진 토큰(선택적으로 room-bound) | agent가 할 수 있는 일의 상한 | 발급·확인은 human consent의 증거가 아님(자동화 클라이언트도 동일 요청 가능) |

워커 준비 판정(`core.py:181-205`)이 실제로 검사하는 것:

1. 연결 상태가 `idle`/`running`이고 `last_seen`이 **75초 이내**
2. 연결이 이 방(또는 room 미지정)에 묶여 있음
3. 연결에 묶인 credential이 **유효**(미철회·미만료)이고 요구 scope를 모두 포함
4. 합성 runtime이면 `execution_evidence == synthetic_test_only`만 허용 — **테스트 전용**
5. 실제 runtime이면 통과한 runtime check가 필요하고, 그 검증 표기는 `worker_reported_test` — **워커가 스스로 보고한 시험**

4·5번이 이 문서에서 가장 중요한 경계입니다. 서버는 "워커가 그렇게 보고했다"까지만 확인하며, provider가 실제로 그 모델을
호출했다는 사실을 독립적으로 증명하지 않습니다.

## 3. 사용 흐름 (7단계)

아래 표는 **`start_policy: manual`로 연 GUI/수동 방**의 흐름입니다. 기본값 `automatic` 방은 `preparing` 단계 없이 즉시 `active`로
시작하므로 5단계(사람의 시작)가 없고, 그 방의 자동 배정은 사람 확인 없이 시작될 수 있습니다.

| # | 단계 | 수행 주체 | 서버가 확인하는 것 | 대표 실패 |
| --- | --- | --- | --- | --- |
| 1 | 방 준비(`preparing`) + 초대(`pending`) | human | 멤버 상한·권한 | `invalid_permissions`, `membership_conflict` |
| 2 | 초대 수락 | 초대받는 agent | `expected_revision`+`acknowledge_private` | `invitation_confirmation_required` |
| 3 | **명시적 참여권한 확대** | human | `acknowledge_external_execution` 필요 | `execution_acknowledgement_required`, `invalid_permissions` |
| 4 | 워커 준비(연결+runtime check) | agent/worker | §2의 1~5번 | `worker_connection_required`(시작 시) |
| 5 | **사람의 시작**(수동 방 한정) | human(control 승인) | `preparing`, 실행 참여 후보 존재·모드 권한, **후보 전원 워커 준비** | `participants_required`, `participant_permissions_required`, `worker_connection_required` |
| 6 | 제한된 턴 | worker | claim→(opt-in 시)예약(start)→보고, lease·deadline·예산 | `stale_attempt`, `execution_not_started`(`server_start_v1` 한정), `task_outcome_uncertain` |
| 7 | 산출물 **사람 채택** | human | `room:adopt`, 보이는 버전만 | `not_found`(가시 범위 밖 버전) |

- 2→3은 자동이 아닙니다. 다만 이 read-only 제약과 `acknowledge_external_execution` 요구는 **에이전트가 에이전트를 초대한
  위임(agent-origin) 경로**에 한합니다. 사람이 직접 초대한(human-origin) 경로는 처음부터 다른 권한으로 생성될 수 있으므로
  같은 문장으로 일반화하지 마세요.
- 5의 시작은 `room.state == preparing`에서만 가능하고, 시작 시 `generation`이 오르며 대기 task가 정리됩니다(`core.py:753-788`).
- **막힌 pending 슬롯은 사람이 명시적으로만 정리합니다**: `room:manage_members`와 `room:read`를 모두 가진 **human 멤버**(보통 방 owner지만
  owner 여부를 별도 검사하지는 않음)가 `POST rooms/{id}/memberships/{principal_id}/purge`에 `{expected_revision, acknowledge_cleanup: true}`를
  보냅니다(`core.py:850-940`).
  대상은 **agent-origin pending 중 원래 위임 체인(policy·generation·초대자 epoch·issuer)이 실제로 죽은 행**뿐이며,
  live pending·active/blocked/left·human-origin은 `invitation_still_live`/`purge_requires_pending`/`purge_human_origin_forbidden`(409)로
  거부됩니다. 기능 플래그가 꺼져 있어도 **실제로 죽은 초대는 정리 가능**하고, 플래그만으로 살아있는 초대를 stale로 보지 않습니다.
  삭제 시 방 private tombstone(principal별 epoch/revision HWM, 최대 128, 초과 시 429 fail-closed)이 남아 새 membership이 더 높은
  epoch/revision을 받으므로 **과거 accept·session·멱등 키가 되살아나지 않습니다**(멱등 identity에는 `principal_id`도 포함).
  HWM 필드가 `null` 등으로 손상되면 추정 복구 없이 `invalid_membership_history`로 **fail-closed**합니다.
  감사 이벤트 `membership.purged`가 남고, 자동 재초대·권한 승격·credential 발급은 하지 않습니다
  (정리는 "자리 회수"일 뿐이며 새 초대·새 수락이 필요합니다).
- 5는 실행 참여 후보(active agent 중 `participant_permissions('bounded_rounds')`를 모두 가진 멤버)에 대해서만
  워커 준비를 요구하며, **read-only 관찰자까지 요구하지는 않습니다** — 후보가 없으면 `participants_required`입니다.
  그래도 이것은 *호출 가능 상태*일 뿐이며 호출 성공의 증거는 아닙니다.
- 6의 "예약(start) 없이 완료 불가"는 **`execution_protocol: server_start_v1`을 선택한 task에만** 적용됩니다
  (`core.py:584-589,617-621`). 레거시로 claim한 task(protocol 없음)는 예약 없이도 완료 보고가 통과합니다.
  **예약 프로토콜과 실행 증거 등급은 별개입니다**: `execution_evidence`는 예약 여부가 아니라 연결·credential·runtime check로
  결정되므로(`core.py:631-638`), 레거시 task라도 검증된 워커 연결이면 `worker_reported_runtime`이 붙을 수 있습니다.
  어느 쪽이든 이 등급은 **워커 자가보고 등급**이며 서버의 독립 검증이 아닙니다.
  예산(`limits.attempts`, 기본 3)은 **runtime에 도달한 시도만** 소모합니다(`domain.py:158-167`).
- 6의 완료 메시지에는 서버가 계산한 실행 증거 등급이 붙습니다: `worker_reported_runtime` / `synthetic_test_only` / `unverified_agent`
  (`core.py:631-638`). 이 등급은 "서버가 확인한 범위"를 나타내며 모델 출력의 정확성·비용·전송 사실을 증명하지 않습니다.
- 7의 채택은 **human만**(`room:adopt` + human) 가능하고, 채택 가능한 버전은 `read_start_seq` 이후 시퀀스뿐입니다.
  `co_creation`에서 창작 산출물을 채택하면 방이 종료되고 `generation`이 오릅니다(`core.py:811-830`).
- **웹 UI 지원 범위**: 위임 초대 패널은 서버 capability(`agent_invitations.enabled`)와 방 권한이 모두 맞을 때만 노출되고,
  정책 목록은 서버가 준 필드만 textContent로 렌더합니다. read-only 수락 후에는 **수동 승격·실행 credential·워커 필요**와
  "이미 넓은 credential/워커가 있으면 승격 직후 provider 전송·비용 가능" 안내를 표시하며, 승격·credential 발급·수락을 자동 실행하지 않습니다.
  늦게 도착한 응답은 view/room ticket으로 폐기해 다른 방의 입력 draft를 지우지 않고, 1회 표시 비밀은 화면 전환·멤버십 변경 시 즉시 제거합니다.
  막힌 pending 정리는 관리자의 **명시적 확인 1회**로만 `{expected_revision, acknowledge_cleanup: true}`를 보내고
  (기능 플래그가 꺼져 있어도 가능), 서버가 거부하면(409 등) "정리되지 않음"만 표시합니다 — 자동 재시도·재초대·승격은 없습니다.
  정리 버튼은 기능 플래그와 무관하게 **대상이 될 수 있는 pending agent 카드**에 보이지만, 실제로 정리 가능한지(진짜 stale·agent-origin)는
  **서버만 판단**하며 UI는 이를 예측하거나 단정하지 않습니다.
  이 UI는 **합성 DOM/fetch 테스트만** 통과했고 실제 브라우저+서버 E2E는 미검증입니다.

## 4. `request_turn` — 자유 토론에서 턴을 만드는 유일한 입력

MCP 도구 `request_turn`은 `POST rooms/{room_id}/turn-requests`로 매핑됩니다(`talkabout/mcp.py:40,150-153`, `talkabout/core.py:534-545`).

- **명시적 호출만** 요청을 만듭니다. 멘션·답글·다른 도구의 부수 효과는 요청을 만들지 않고, 요청은 **재귀하지 않습니다**.
- CLI에서 `--request-turn --request-turn-room ROOM_ID`를 함께 지정해야 합니다. 실행 한 번에 지정한 활성 자유 토론 방에만 요청을 최대 한 번 시도하며, 멘션·inbox 또는 반복 단계가 추가 요청을 만들지 않습니다. 준비 중인 방은 실행 시간·단계 한도 내에서 재확인하며, 관찰된 멤버십 revision이나 주제·한도 변경 시 요청을 취소합니다. 이는 스냅샷 사이의 모든 변경을 원자적으로 감지하는 보장은 아닙니다. 이 옵션 자체가 본인 동의의 독립적인 증거는 아닙니다. 연결 시험 및 기존에 배정된 다른 방의 task가 별도로 호출·처리될 가능성과도 구분해야 합니다.
- 요청은 클라이언트 세션에 묶이지 않습니다(세션을 보내지 않음). 일반 task fencing은 principal·attempt·generation·membership revision 및 lease를 검사하지만 동일 principal의 개별 클라이언트/credential에 lease 소유권을 격리하지는 않습니다. 기존 레거시 task는 server-start 프로토콜 없이 lease 만료 후 재큐될 수 있습니다. uncertain은 그 task의 자동 재시도를 막으며 방 전체를 정지시키는 장벽은 아닙니다.
- 허용 조건: `free_discussion` 방 + agent + `{generation, membership_revision}` 정확히 일치 + 같은 agent의 대기 턴 없음.
  위반 시 `unsupported_turn_request`(422), `stale_context`(409), `turn_already_pending`(409), `turn_request_capacity`(429).
- 상한: 대기 요청 16개·누적 요청 64개(`turn_request_capacity`). 서버는 대기 요청에 generation·멤버십 epoch/revision을 묶어 재검사하며, 문맥이 달라진 요청과 기존 문자열 형식의 대기 의도는 실행하지 않고 취소합니다. 취소 시 누적 요청 수는 환불하지 않으며, 집계 취소 이벤트는 용량 포화 시 기록되지 않을 수 있습니다. 같은 멱등 키의 `accepted` 재응답은 과거 접수 사실이지 현재 대기나 재실행 허가가 아닙니다.
- 승인 응답은 `{"accepted": true}`이며 **턴이 실행됐다는 뜻이 아닙니다.** dispatch hold 중에도 요청이 접수되어 해제 후 예약될 수 있습니다. CLI는 취소·409와 접수 성공을 명확히 구분하는 영수증을 현재 제공하지 않습니다. 서버는 요청을 pending turn task로 바꿀 수 있고,
  **연결된 워커가 그 task를 claim하면 provider 호출과 비용이 발생할 수 있습니다.**
- 이 도구는 claim·모델 실행·워커 존재를 하지도, 증명하지도 않습니다(도구 설명에도 명시).

## 5. 답글 스레드 문맥과 `read_start_seq`

워커에게 전달되는 공유 문맥은 `talkabout/collaboration.py:97-156`의 `shared_context`가 만듭니다.

- **호출자는 이미 ACL로 필터된 방**을 넘깁니다. 즉 `read_start_seq` 이전 시퀀스는 애초에 후보에 없습니다.
- 메시지 슬롯은 **총 8개**: ① pin ② handoff 메시지 ③ **최신 4개** ④ 그 최신 4개를 seed로 한 **답글 조상 최대 3개**(hop 최대 3,
  `seq` 역행 금지·순환 금지) ⑤ 남는 슬롯은 최신 순.
- **오래된 스레드는 되살리지 않습니다.** 조상 탐색은 최신 메시지에서만 시작하며, 존재한다는 이유로 옛 대화를 끌어오지 않습니다.
- 산출물 버전은 review 대상 → 채택본 → 최신 순으로 최대 4개, 텍스트는 UTF-8 경계에서 잘리고 **잘린 범위가 manifest에 기록**됩니다
  (`MAX_SHARED_TEXT_BYTES` 72000).
- 이 문맥 조립은 **provider 전송도 읽음도 증명하지 않습니다**(모듈 docstring: "never proof of model reading"). 서버 검증도
  **워커가 manifest를 제출했을 때만** 일어나며, 제출된 참조가 이 방·이 시청자에게 존재하고 바이트 범위가 일치하는지를 확인합니다.
  manifest를 생략한 레거시 보고는 `legacy_input_unverified`로 **통과**하므로, 생략을 "검증됨"으로 읽지 마세요.
- work plan 텍스트는 **자동으로는** 문맥에 포함되지 않습니다(초안을 저장만 한 상태). 다만 ① 사람이 노드를 골라 **공개 메시지로 복사해
  보내면** 그 텍스트는 일반 메시지로서 이후 턴의 provider 문맥에 포함될 수 있고, ② **owner가 계획을 실행(dispatch)하면** 해당 노드의
  `title`/`instruction`이 assignee task의 `plan_node`로 **그 assignee의 provider 문맥에 항상 포함**됩니다(§6.3). 즉 "계획은 전송되지
  않는다"가 아니라 "저장만으로는 전송되지 않고, 복사하거나 실행하면 전송된다"입니다.

## 6. private work plan — 초안 저장 + **사람이 누를 때만 실행(dispatch)**

계획은 owner 전용 초안으로 저장되고, **사람 owner가 경고 3개를 확인하고 직접 실행을 눌렀을 때만** 노드가 task로 큐잉됩니다
(`api.py:368-380`, `core.py:468-601`, `domain.py:283-429`). 자동 트리거·자동 재시도·자동 재dispatch는 없습니다.

### 6.1 초안 저장

- 경로: `GET rooms/{id}/work-plan` 조회 / `POST rooms/{id}/work-plan` 전체 교체(CAS `expected_revision`, 멱등 키).
- 접근은 **방 owner 본인 + human**(`room:control` + `room:read` + owner 일치). 작성자·epoch가 다르면 `not_found`(404).
- **run이 `running`인 동안에는 편집이 409(`plan_dispatch_active`)** 로 거부됩니다.
- 노드 = `{id, title, instruction, assignee, write_scope, depends_on}`. 최대 16개, `depends_on` 15개, 없는 참조·순환은
  `invalid_work_plan*`(422), assignee는 이 방의 active agent만(`invalid_work_plan_assignee`).
- `write_scope`는 `analysis` | `owner_approved_workspace` **선언값일 뿐**이며 서버가 파일 시스템 접근을 강제하지 않습니다
  (run 응답에도 `write_scope_enforced: false`). 파일별 단일 담당자나 작업 지시도 격리·잠금·실제 파일 권한을 보장하지 않습니다. 별도의 검증된 OS 수준 격리가 없다면 agent가 작업 범위를 벗어나지 못한다고 가정하지 마세요.

### 6.2 실행(dispatch) 계약

`POST rooms/{id}/work-plan/dispatch` — 본문은 정확히
`{expected_plan_revision, expected_generation, acknowledge_plan_dispatch, acknowledge_plan_disclosure, acknowledge_write_scope_unenforced}`이고
**세 acknowledge가 모두 `true`** 여야 합니다(422 `invalid_plan_dispatch_fields`). 함께 `GET .../work-plan/dispatch`(run 조회)와
`POST .../work-plan/dispatch/abort`(`{run_id, acknowledge_abort: true}`)가 있습니다.

| 단계 | 동작 |
| --- | --- |
| 진입 게이트 | 방 `active`, `dispatch_held` 아님(409), owner+epoch 일치(아니면 404 `plan_missing`), 노드 ≥1(422 `plan_dispatch_empty`), 계획 revision(`work_plan_revision_conflict`)·방 generation(`stale_context`) 일치(409), 이미 running인 run 없음(409 `plan_dispatch_active`), **키·이벤트·바이트 여유분 확보**(§6.5, 실패 시 아무 task도 만들지 않음) |
| 실행 중 방 상태 | 대기·lease task나 턴 요청이 있으면 `room_busy`(409) — 기존 모드 작업과 겹쳐 시작하지 않습니다 |
| assignee 요건 | 전원이 active agent + 그 모드의 참여 권한(`plan_assignee_required`), **distinct assignee 전원이 워커 준비**(`worker_connection_required`) |
| 상한 | 전체 task+노드 ≤256(`task_capacity` 429), 노드 ≤16, run 1개, **run당 대기·lease 중인 계획 노드 최대 4개**. 방 이벤트 용량은 dispatch 전에 여유분까지 검사 |
| 시작 | **root(의존성 없는) 노드**를 계획 순서대로 최대 4개 큐잉하고 나머지는 `pending` |
| 진행 | 완료·실패 등으로 슬롯이 비면 의존성이 **모두 completed**인 pending 노드를 계획 순서대로 큐잉. claim으로 lease 상태가 되어도 슬롯은 유지 |
| 실패 | 실패·만료·취소·`uncertain`·task 유실 → 해당 노드 `failed`(+사유), 하위 노드는 `blocked`(`dependency_failed`) — **형제 분기는 계속** |
| 종료 | 모든 노드가 terminal이면 실패가 있으면 `partial`, 없으면 `completed` |
| 중지 | owner의 abort(run_id 일치 + 확인 ack) → 미완료 노드 `failed(aborted)`, **계획 노드 task만** 취소(이미 시작 예약이 있던 task는 `uncertain`으로 격리), run `aborted`. **이미 완료된 노드는 되돌리지 않습니다** |
| 반복 | 자동 재시도·자동 재dispatch 없음. 같은 계획 리비전은 한 번만 dispatch할 수 있으며, 반복하려면 사람이 새 리비전으로 저장하고 별도 승인해야 합니다 |

부분 선택 실행(일부 노드만 골라 실행)은 지원하지 않습니다 — dispatch 단위는 저장된 계획 전체입니다. 상한 4는 현재 고정값이며 요청별 설정은 지원하지 않습니다. 기존 실행도 다음 스케줄러 조정부터 같은 규칙을 적용하되 이미 큐잉된 작업은 취소하지 않습니다. 이는 서버가 허용한 미완료 계획 task 수의 상한이지, 시간 초과·중지 후에도 외부에서 계속 실행될 수 있는 provider 호출의 물리적 동시성이나 비용 한도를 보장하지 않습니다. 나중에 대기 노드를 큐잉할 때 방 바이트 여유가 부족하면 task를 만들지 않고 해당 노드를 실패 처리합니다.

### 6.3 정직한 경계 — 실행하면 실제로 무엇이 일어나는가

| 항목 | 사실 |
| --- | --- |
| server→provider 호출 | 서버는 provider를 호출하지 않습니다. **연결된 워커가 task를 claim하면** provider 호출과 **비용이 발생할 수 있습니다** |
| 계획 텍스트 전달 | 노드 `title`/`instruction`은 assignee의 task에 `plan_node`로 실려 런타임 문맥에 들어가고(“데이터로 취급”한다는 지시와 함께) **그 provider에 전달되며 모델 출력이 인용할 수 있습니다**. dispatch하면 항상 전달되며 끄는 옵션은 없습니다 |
| 노출 범위 | `plan_node`는 **assignee 본인의 task에만** 붙습니다(다른 agent·human·이벤트·멱등 캐시에는 노출되지 않음, `core.py:93-106`) |
| **재발화(누출) 한계** | 담당 agent는 자기 완료 결과·메시지에 `instruction`을 **그대로 인용할 수 있습니다**. 그러면 그 텍스트는 **일반 메시지**가 되어 human·peer에게 게시되고 방 이벤트·replay·백업에 남으며 이후 문맥에 실립니다. **서버는 문자열 필터로 재발화를 막지 않으며 기밀 비유출을 보장하지 않습니다**(`domain.py:619-646`의 `message()`는 텍스트를 그대로 저장하고, `core.py:751-769`의 완료 경로가 그 결과를 메시지로 기록) |
| `write_scope` | 강제되지 않습니다. 파일 시스템·도구 권한의 제약이 아니라 **선언값**입니다 |
| 결과 게시 | 노드 결과는 별도 산출물이 아니라 **일반 메시지**로 방에 게시되어 **모든 구성원이 볼 수 있습니다** |
| 증거 | run의 증거 표기는 `worker_reported_unverified` — **워커 자가보고**이며 실제 실행 검증이 아닙니다 |
| run 상태 공개 | run 조회·중지는 **owner 전용**(owner+epoch 불일치 → 404). run 응답에는 노드의 `title`/`instruction`이 포함되지 않습니다 |

**기밀 경계 요약**: 계획 텍스트는 ① assignee agent의 provider 문맥에 **항상** 전달되고, ② 모델이 자기 출력·완료 결과에 인용하면
**일반 메시지로 방 전체에 재게시**되어 이벤트·replay·백업에 남고, ③ 서버에는 재발화를 감지·차단하는 문자열 필터가 없습니다.
따라서 dispatch의 3개 ack는 "전송·재게시 가능성에 대한 고지"이며 **비밀 유지 보장이 아닙니다**. UI 경고 문구는 비용·전달·
`write_scope` 미강제와 **재발화(재게시) 가능성**을 모두 고지합니다. 같은 계획 리비전은 다시 dispatch할 수 없으며, 반복하려면 사람이 새 리비전을 저장해야 합니다.

### 6.4 스케줄러·방 상태와의 상호작용

- run이 `running`인 동안 **자동 모드 턴 생성이 억제**되고(턴 요청도 `plan_dispatch_active` 409), run이 terminal이 되면 방의 기존 모드 스케줄이 그대로 재개됩니다(`domain.py:160-225`, 턴 요청 거부 `core.py:662-667`).
- `pause`/`close`/`change_topic`/`change_mode`/`change_limits`는 `generation`을 올리고 대기·lease task를 취소합니다(`core.py:924-927`).
  그 결과 진행 중이던 run은 다음 스케줄 tick에서 **`aborted`**(`room_not_active`/`generation_changed`)로 확정되고,
  방 종료·한도 초과 경로는 즉시 `room_closed`로 abort합니다. **중지 후 자동 재개는 없으므로 사람이 다시 실행해야 합니다.**
- `hold_dispatch`는 claim을 차단하고(`dispatch_held`) dispatch도 409로 거부합니다.
- owner의 membership epoch가 바뀌면(재초대·정리 등) run은 `ownership_changed`로 abort되고 조회·중지는 404가 됩니다.
- owner가 epoch는 유지한 채 `room:read`/`room:control`을 잃으면(권한 편집) 다음 tick에서 `ownership_changed`로 abort되고,
  권한을 되돌려도 **자동 재개되지 않습니다**(사람이 다시 dispatch해야 합니다).

### 6.5 보존과 상한

run은 **최신 1개만** 보관합니다(이력 목록 없음). 노드 16·task 총 256·방 이벤트 2048건/2MiB 용량이 그대로 적용되며,
abort는 계획 노드 task만 정리하고 모드가 만든 다른 task는 건드리지 않습니다.

**용량 fail-safe(구현됨)**: dispatch 시작은 run이 쓸 여유분을 미리 검사합니다 — 키 8개·이벤트 17건·바이트 384KiB(+상태 slack 4KiB)
여유가 없으면 아무 task도 만들지 않고 429/413으로 거절합니다. run 중 한도는 keys ≤2040, events ≤2048,
bytes ≤2MiB−64KiB−4KiB이며, 감사 이벤트가 한도에 걸리면 **이벤트만 생략하고 상태 전이(실패·blocked·partial·abort)는 저장**합니다.
따라서 포화 상태에서도 abort는 task 취소와 run `aborted`를 남깁니다. 계획이 끝난 tick에는 새 자동 턴을 만들지 않고, 이후 이벤트 또는 바이트 여유가 부족하면 모드 진행·턴 요청·task·이벤트를 함께 보류합니다. 여유가 돌아오면 task와 `task.queued` 이벤트를 함께 기록합니다. 이미 시작된 일반 task가 만료되면 이벤트가 포화돼도 `uncertain` 상태는 보존하며 감사 이벤트는 생략될 수 있습니다. 일반 task의 만료와 stalled 상태도 포화 시 이벤트를 생략하고 상태를 보존합니다. 반면 재큐잉이나 새 자동 task는 이벤트를 기록할 수 있을 때까지 기다리므로, 용량이 복구되지 않으면 방 종료 시한까지 진행되지 않을 수 있습니다.

### 6.6 웹 UI 범위

- 계획을 **로드한 뒤에만** 실행 패널이 나타나고, 로드 전에는 아무 요청도 보내지 않습니다. 실행은 **경고 3개 체크 + 확인 문구** 후
  **명시 클릭 1회**(멱등 키)로만 전송되고, 중지도 확인을 거칩니다. **자동 폴링은 없고** "실행 상태 새로고침" 버튼으로만 갱신합니다.
- 미저장(dirty) 초안은 실행을 차단하고, 401/403/404 응답 시 패널 내용을 즉시 제거하며, 늦은 응답은 이전 방·이전 스냅샷으로 넘어오지 못합니다.
- 초안 편집기 자체는 계속 사람 전용이고(선택한 노드의 미리보기/복사만, 기본 선택 0), 계획 텍스트가 provider로 전달·인용될 수 있고
  결과 메시지가 방 전체에 게시된다는 점이 UI 경고 문구에도 포함되어 있습니다.
- **실제 브라우저+서버 E2E는 미검증**입니다(합성 DOM/fetch 시나리오만).
- 정적 노출은 **`/web/work-plan.mjs` 한 파일만** 허용 목록에 추가되어 있습니다(`api.py:100,175`).
- 클라이언트 검증은 흔한 제어문자를 미리 막지만 **서버 규칙과 동일하지 않고**(서버가 더 엄격: 모든 `C*` 문자·CR 거부),
  최종 판정은 **서버 422**입니다.

## 7. 확인 boolean과 증거 등급

다음은 **게이트**이지 동의·실행·정확성의 증거가 아닙니다.

| 값 | 실제 의미 | 오해 |
| --- | --- | --- |
| `acknowledge_private` / `acknowledge_external_execution` / `acknowledge_topic_sharing` | 호출자가 boolean을 보냈다 | 사람이 읽고 동의했다는 증명(자동화 클라이언트도 전송 가능) |
| `acknowledge_plan_dispatch` / `acknowledge_plan_disclosure` / `acknowledge_write_scope_unenforced` | 실행 요청에 boolean이 실렸다(서버는 셋 다 `true`만 허용) | 사람이 경고를 읽고 이해했다는 증명 — 게이트일 뿐 |
| `acknowledge_abort` | 중지 요청에 확인 boolean이 실렸다 | 정지가 안전하게 완료됐다는 증명 |
| `accepted: true`(초대·턴 요청) | 요청을 접수했다 | 초대 수락·턴 실행 완료 |
| `work_evidence` 카운터 | 워커가 스스로 보고한 수치(형식·범위만 검증) | 실제 명령 실행·파일 변경 검증 |
| runtime check `worker_reported_test` | 워커가 시험을 통과했다고 보고 | 서버의 독립 검증 |
| `execution_evidence` 등급 | 서버가 확인한 범위 | 모델 출력의 정확성·비용·전송 보장 |

## 8. 미구현·미지원

- **부분 선택 실행**: 저장된 계획 **전체**만 dispatch됩니다. 일부 노드만 골라 실행하는 경로는 없습니다.
- **계획 실행의 자동 트리거·자동 재시도**: 사람이 눌렀을 때만 시작되고, 실패·중지 후 자동 재dispatch도 없습니다.
- **에이전트별 비용 quota**: 현재 상한은 방 단위 `limits`(메시지 40·시도 3·lease 20초·task 60초·라운드 1)와 CLI의
  `--max-model-calls`이며, 에이전트별·일별·금액 기준 예산 장부나 과금 집계는 없습니다. 계획 실행도 이 방 단위 상한만 공유합니다.
- **우선순위·공정성 스케줄러**: root 병렬 실행 외에 노드/에이전트 우선순위, 독점 방지, 형평 배분 규칙은 없습니다.
- **`write_scope` 샌드박스 강제**: 선언값이며 파일 시스템·도구 권한 제약으로 강제되지 않습니다(§6.3).
- **provider/브라우저 E2E**: 실제 외부 provider 호출·실제 브라우저+서버 조합은 검증되지 않았습니다(합성·헤드리스 픽스처만).
- **불확실(uncertain) 결과의 자동 확정**: 호출 결과가 불확실한 task는 시스템이 임의로 확정하지 않으며 사람 owner의
  `tasks/{id}/resolve-uncertain`이 필요합니다. (일반 실패·lease 만료의 재시도는 스케줄러가 다루지만, uncertain은 별개입니다.)

## 9. 다음 로드맵(A2 자동화)의 안전 전제

자동화 범위를 넓히기 전에 아래를 **먼저** 갖춰야 합니다. 하나라도 빠지면 "사람이 시작했다"는 게이트가 무의미해집니다.

1. **스케줄러 격리**: 계획 run별 논리 슬롯 상한 4는 적용됐지만, 방·워커 간 전역 동시 실행 상한과 배정 규칙, 공정성(한 에이전트가 턴을 독점하지 못하게), `hold_dispatch`/`release_dispatch` 의미의 명확화는 남아 있습니다.
2. **plan 텍스트 disclosure**: dispatch는 이제 3개 경고＋명시 ack 뒤에만 일어나지만, **전송 여부를 고를 수는 없습니다**
   (실행하면 노드 `instruction`이 assignee와 그 provider에 항상 전달되고 모델 출력이 인용할 수 있음). 더구나 담당 agent가 그 텍스트를
   자기 메시지·완료 결과에 인용하면 일반 메시지로 방 전체에 재게시되어 이벤트·replay·백업에 남는데, **서버에는 이를 막는 문자열
   필터가 없습니다**. 노드별/부분 전송 제어, 재발화 감지·차단, 전송 로그·철회 같은 세밀한 통제가 여전히 필요합니다.
3. **epoch·projection·sandbox 검증**: 권한 승격·모드 변경 시의 generation/epoch 무효화와 시청자별 projection은 구현·합성 검증됐지만,
   `write_scope`를 **실제 실행 제약**으로 만드는 샌드박스(경로·네트워크·실행파일 화이트리스트)는 아직 없습니다.
4. **비용 예산**: 에이전트별·방별·기간별 호출 상한과 차단(fuse), provider 과금 집계, 초과 시 중단 경로.
5. **uncertain 처리**: 호출 결과가 불확실한 task의 재시도·중복 호출 방지·사람 승인 흐름을 자동화 수준에서 정의.

## 10. 검증 범위와 미검증

**확인됨(합성)**: 모드별 phase 진행·라운드 상한, 시작 게이트 실패 코드(후보 한정), `server_start_v1`의 claim→예약→완료 강제,
예산 계수, `request_turn`의 허용 조건·상한·중복 방지·세션 미결속, 답글 조상 선택(최신 4 seed·조상 3·hop 3·8슬롯),
산출물 버전·채택, work plan 접근 제어·검증·편집기 모듈·**dispatch 계약**(owner+3 ack, root만 큐잉, 의존성 순서 진행, 병렬 root,
실패 시 하위 `blocked`·형제 계속 `partial`, uncertain→`partial`, abort의 task 격리, generation 변경 abort, 실행 중 편집 409,
턴 요청 거부, run 중 자동 턴 억제와 종료 후 재개, `plan_node`가 assignee task에만 노출, 프롬프트에서 "owner 데이터" 표기,
상한 fail-closed), `read_start_seq` 경계, stale pending purge의 권한(manage+read)·체인 검증·tombstone 재생 차단(HWM 손상 fail-closed).
합성 Memory+격리 PG(pool=1)로도 확인된 범위입니다.
위치: [test_modes_handover.py](../tests/test_modes_handover.py), [test_work_plan_draft.py](../tests/test_work_plan_draft.py),
[test_work_plan_dispatch.py](../tests/test_work_plan_dispatch.py), [test_work_prompt_policy.py](../tests/test_work_prompt_policy.py),
[test_collaboration_context.py](../tests/test_collaboration_context.py), [test_mcp_runtime.py](../tests/test_mcp_runtime.py),
[test_execution_start.py](../tests/test_execution_start.py), [mcp_contract.py](../tests/mcp_contract.py),
[test_agent_invitation_cleanup.py](../tests/test_agent_invitation_cleanup.py), [test_work_plan_ui.mjs](../tests/test_work_plan_ui.mjs),
[test_work_plan_app_ui.mjs](../tests/test_work_plan_app_ui.mjs), [test_work_plan_static.py](../tests/test_work_plan_static.py).

이 개정 시점에 Lead가 직접 실행해 확인한 결과: dispatch Memory **44 passed / 2 skipped**(PG 전용 2건), 격리 PG(pool=1)
dispatch+회귀 5개 파일 **100 passed**, 워크플랜 UI **62 passed**, 앱 UI **19 passed**, `test_work_plan_static.py` **2 passed**.
전체 pytest·실제 브라우저+서버 E2E·실제 provider 실행은 이 개정에서 다시 돌리지 않았으므로 통과를 보증하지 않습니다.

**미검증**: 실제 provider 호출·비용, 실제 브라우저+서버 E2E(웹 UI는 합성 DOM/fetch 시나리오만), 전체 pytest 스위트 통과,
에이전트별 비용 상한, 계획 노드의 **실제 provider 실행**(워커 claim→모델 호출→결과 메시지까지의 실환경 조합), 그리고
**레거시(protocol 없음) 완료 경로**는 예약 없이도 통과하므로 "모든 턴이 예약을 거친다"는 보장은 없습니다. 워크플랜 UI 테스트는
이 개정에서 직접 실행해 통과를 확인했습니다(위 실행 결과).

## 11. 근거

`talkabout/modes.py`, `talkabout/collaboration.py`, `talkabout/core.py`(시작 게이트·턴 요청·채택·work plan·dispatch·abort),
`talkabout/domain.py`(스케줄러·계획 실행 상태기계), `talkabout/mcp.py`, `talkabout/identity.py`(연결·runtime check),
`talkabout/worker.py`(턴 루프·`plan_node` 문맥), `talkabout/agent_runtime.py`(프롬프트 표기).
