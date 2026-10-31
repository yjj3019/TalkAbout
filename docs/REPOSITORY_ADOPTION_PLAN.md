# 외부 저장소 분석 결과 순차 반영 계획

**사용자 승인: 2026-10-27 · 단계별 구현/검토/CI · 공급자 실호출은 별도 예산 승인 필요**

근거: [7개 저장소 분석](../analysis/repo-review-2026-10-27/README.md).
외부 코드를 통째 흡수하거나 설치하지 않는다. 일반 설계 패턴을 현재 Python worker/server 경계 안에서 독립 구현한다.
R3 start grant·uncertain no-retry, R4 bearer-only vault, no-web/MCP/plugin 기본 차단은 불변 조건이다.

| 단계 | 범위 | 상태 | 필수 gate |
|---|---|---|---|
| A0 | W1 Codex exec-json 도구 이벤트 계약 연결 | PR #43 병합; PR/merge CI 통과 | work만 명령/파일 변경 schema 허용, text-only/unknown tool 거절, terminal ledger와 한도, synthetic stream |
| A1 | bounded work evidence + 검증 기록 | PR #44 병합; PR/merge CI 통과 | agent 제출 미검증 표시, 완료 fence·메시지 가시성, secret-free 제한 schema, 원자 저장·legacy 호환; 독립 검증 영수증 별도 |
| A2 | 공동 코딩 task 의존성·write scope·workspace/review 계약 | PR #45 병합; 비실행 초안/안전 보완 완료, 실행 DAG 별도 | cycle·stale CAS·overlap·권한 검사, 다른 사용자 로컬 경로 미노출, 서버 자동 patch 적용 금지 |
| A3 | uncertain 사람 해결 | PR #46; 합성 테스트 완료, 실제 공급자 호출 아님 | 자동 재실행 금지, 이유·대상 attempt 감사 기록, 늦은 outcome 충돌 규칙 |
| A4 | 관찰 패널 재접속·작업 수명 분리 | PR #47; 합성 계약 완료(관찰 불변·재접속·detach≠cancel·stable identity), 실제 브라우저 E2E 별도 | view detach와 cancel 구분, stable identity, cursor gap snapshot, 재접속이 호출 시작 안 함 |
| A5 | opt-in browser capability 설계/pilot | 보안 관문 대기 | 별도 소유 process/profile, egress·인간 승인·cookie 격리·untrusted context·artifact ACL. 기본 no-web 유지 |
| A6 | opt-in PDF/document capability 설계/pilot | 제품/보안/라이선스 관문 대기 | quarantine·parser OS 격리·quota/retry·room cache·asset pin·AGPL/의존성 검토. binary 기본 차단 유지 |

## A0: 도구 이벤트는 관찰이지 사전 승인 아님

기존 work 명령이 도구를 켜도 `_codex_exec`가 text-only item만 수용하던 문제를 수정했다.
승인된 work profile에만 좁은 `command_execution`/`file_change` 및 bounded `todo_list` 관찰 계약을 추가했다.
이벤트는 native 실행 후 도착할 수 있으므로 parser 거절/interrupt는 명령 미실행·파일 무변경 증거가 아니다.
시작한 prompt의 실패/불확실 결과는 기존 R3/worker uncertain 의미를 유지한다.

- 명시적 item type·ID·phase·status·필드 타입·크기/개수 상한을 검사한다.
- 열린 tool item이 남으면 final turn success로 처리하지 않는다.
- command exit nonzero/tool failed는 관찰된 결과일 뿐 곧바로 전체 turn 실패나 테스트 통과가 아니다.
  모델이 오류를 설명하고 final 답변을 낼 수 있지만 검증 성공으로 표시하지 않는다.
- raw command/output/path를 일반 로그·서버 evidence에 무차별 저장하지 않는다.
- unknown MCP/web/다른 tool은 fail-closed, text profile은 종전 거절 유지.
- 실제 CLI schema와의 실계정 호환 시험은 NOTRUN이며 합성 stream 통과와 구분한다.

## A2: 안전한 도입 경계

기존 세 room mode는 자동 생성된 작업을 직렬 dispatch하고 기존 worker는 계획 노드별
지시를 이해하지 않는다. 따라서 실행 연결 이전에 사람만 읽고 수정하는 비실행 계획 초안을
별도로 둔다. 초안 작성은 기존 방의 자동 스케줄을 중지하거나 새로운 task를 만들지 않는다.
의존성은 계획상 관계일 뿐 자동 대기·실행 순서가 아니다. 쓰기 범위는 선언일 뿐
경로 권한·OS sandbox·충돌 방지나 다른 호스트의 동일 workspace를 보장하지 않는다.
초안은 작성자 principal과 당시 membership epoch에만 귀속하며 재가입 시 이전 초안은
조회·수정할 수 없다. 작성자가 권한을 잃거나 탈퇴하면 기존 초안 복구·이전 수단은 없다.
종료된 방은 조회만 가능하다. 변경 이벤트는 발행하지 않으므로 전용 API를 재조회해야 한다.

실행 활성화는 별도 설계·승인이 필요하다: 구 worker가 임의로 노드를 실행하지 못하도록
plan schema capability 및 고정 revision 확인, R3 start fencing, 승인된 노드 지시 전달,
실패·uncertain 전파, task/event ACL, 예산·deadline, workspace 바인딩과 review 계약을
먼저 정해야 한다. 실행할 때는 선행 task 완료를 검증 통과로 해석하지 않는다.

## A1: 제한된 미검증 집계 계약

처음에는 버전이 있는 제한된 종류·상태별 개수만 보관한다. 자유형 명령·출력·경로·종료 코드·바이트 수를 서버에 전송하지 않는다. 완료·정리 성공 후 고유 item의 마지막 상태로 집계하며 이벤트별 변화 수를 고유 파일 수로 오인하지 않는다.
evidence는 일반 public task 필드가 아니라 완료 message의 seq/현재 read_start_seq 필터에 귀속한다. 기존 task projection은 arbitrary 필드를 새 멤버에게 노출할 수 있다. heartbeat/event에 싣지 않고 동일 완료 payload의 fingerprint와 원자 저장으로 replay를 고정한다.
서버는 어떤 승인된 agent도 이 값을 제출할 수 있으므로 출처를 `agent_reported_unverified`로 표시한다. 형식과 범위만 검증하며 runtime 이벤트의 실제 출처, 파일 변경·명령 실행·테스트 정확성을 검증했다는 뜻이 아니다.
base commit/diff/output digest와 독립 verification receipt는 증거를 실제 생성하는 승인된 producer가 있을 때만 추가한다.
존재하지 않는 증거를 자동 생성하거나 agent final 문장에서 'passed'를 추출해 검증으로 승격하지 않는다.

## A3: uncertain은 해소해도 다시 실행되지 않는다

`uncertain` task는 자동 재실행하지 않는다는 규칙을 유지한 채, 방 소유자가 그 attempt를
**기록상 확인**만 할 수 있게 한다. `POST /api/v1/rooms/{room}/tasks/{task}/resolve-uncertain`.

- 요청은 `attempt`(현재 attempt와 같아야 함), `ack_unknown_effects: true`, `reason_code`
  (`operator_review`/`worker_lost`/`duplicate_risk`/`other`), 선택 `note`(≤512, 제어문자 불가)만 받는다.
  알 수 없는 필드는 422다.
- 응답과 event의 `external_effect`는 항상 `unknown`이다. 이 기록은 "실행되지 않음"·"부작용 없음"·
  "테스트 통과"를 뜻하지 않으며 그렇게 표시하지 않는다.
- 해소해도 task 상태는 `uncertain` 그대로이고 requeue·재실행·완료·`skip`·provider 재호출이 없다.
  같은 attempt에 대한 재요청은 같은 payload면 replay, 다르면 `already_resolved` 409다.
- 해소 기록은 새 task 생성을 막지 않는다(`schedule`은 uncertain을 blocker로 보지 않는다).
  추가 실행을 멈추려면 `hold_dispatch`를 함께 쓴다.
- 웹 작업 목록에서도 소유자·읽기/제어 권한이 확인된 사람에게 확인 버튼을 제공한다.
  확인 대화상자는 외부 영향 불명과 상태 유지 사실을 안내하고, 이유는 `operator_review`로 기록한다.
  전송 응답이 유실되면 성공이나 거절로 단정하지 않고 최신 상태를 조회하며 동일 요청의 재시도 키를 유지한다.
- 8건 ring 밖으로 밀려난 예약의 늦은 outcome은 이 단계에서 다루지 않는다(별도 규칙 필요).

## A4: 관찰 표면은 상태를 바꾸지 않는다

stream/events/snapshot은 read-only 관찰이며 task·room payload를 변경하지 않는다. 합성 테스트로
확인한 계약은 다음과 같다.

- 관찰 호출 후 task 상태·attempt·예약 기록·room `last_seq`·event 수가 그대로다(커서 저장만 예외).
- 재접속은 `Last-Event-ID`/`cursor` 이후의 event만 받고 이미 받은 event를 반복하지 않는다.
- 사용할 수 없는(stale/foreign) 커서는 event를 건너뛰지 않고 `access_changed`로 알린다.
  클라이언트는 이때 live source를 닫고 목록/스냅샷에서 다시 동기화한다(닫지 않으면 브라우저가
  같은 stale 커서로 자동 재연결을 반복한다).
- `duration_seconds`는 0..60만 허용한다.
- 관찰은 호출 시작·취소가 아니다. task 회수는 `pause`/`close`/`change_mode` 같은 명시 control,
  generation 무효화, 구성원 변경(활성 유지 권한 수정도 포함해 membership mutation마다 해당
  구성원의 queued/leased task를 회수한다), 한도 도달 종료뿐이며 관찰 경로는 어디에도 닿지 않는다.
  이미 시작이 확인된 attempt는 이때 `cancelled`가 아니라 `uncertain`으로 남는다.
  클라이언트도 방을 열고 닫거나 스트림이 끝나도 변경 요청을 보내지 않는다(합성 DOM 계약).
- task identity는 view와 attempt에 걸쳐 안정적이다. deadline과 재시도 예산 안에서 만료된
  미시작 lease는 같은 task id로 재진입하고 다음 claim에서 attempt만 오르며, 사람 스냅샷과
  `task.claimed` event가 같은 id·attempt 순열을 보고한다. 관찰자가 잠시 떠나도 진행 중 작업은
  유지되고 같은 시도로 이어진다.
- 위 계약은 합성(메모리 저장소·합성 DOM) 검증이다. 실제 브라우저 종료·재접속 E2E는 별도 검증이다.

## 단계 종료 방식

각 단계는 작은 PR로 분리하고 synthetic 회귀·독립 review·문서 gate·필수 Windows/PostgreSQL 및 POSIX CI를 통과해야 한다.
현재 분석 산출물은 승인된 설계 근거로 보존하며 제품 구현·테스트 통과의 영수증으로 쓰지 않는다.

## 2026-10-31 2차 분석 반영 — 선행 계약 고정

[2차 7개 저장소 분석](../analysis/repo-review-2026-10-31/08-TALKABOUT-GAPS.md)의 P0 후보 중 **코드 실행 경로를 열지 않고 반영 가능한 부분**만 반영했다.
제품 runtime·provider 호출·sandbox 동작은 바뀌지 않았다.

| 후보 | 반영 내용 | 위치 | 상태 |
| --- | --- | --- | --- |
| P0-1 관측 실패 ≠ 허용 | 계약 표·규칙 명문화 + 회귀 테스트(관측 실패가 `unknown`을 유지하고 성공으로 승격되지 않음) | [WORK_PROFILE.md](WORK_PROFILE.md), [tests/test_work_profile_events.py](../tests/test_work_profile_events.py) | **문서·합성 테스트 반영** |
| P0-2 exec schema fixture | 버전 태그(`codex-cli-0.164`) 고정 fixture + 허용/거절 표 회귀 | [tests/fixtures/codex_exec_0164_items.json](../tests/fixtures/codex_exec_0164_items.json) | **테스트 반영**. 드리프트 감지용이며 provider 호환성 증거 아님 |
| P0-3 승인 데이터 계약 | `source`·결정/전달/소비·digest·fence·자동승인 0 규칙 | [TOOL_BROKER_PLAN.md](TOOL_BROKER_PLAN.md)의 "A5/A6 선행 gate 1" | **문서 반영**. 구현·API 없음 |
| P0-4 격리·placement 계약표 | placement×isolation×lease 상태표, 표 밖 backend 실행 거절, "sandbox" 표기 조건 | [TOOL_BROKER_PLAN.md](TOOL_BROKER_PLAN.md)의 "A5/A6 선행 gate 2" | **문서 반영**. 구현 없음 |
| P1-1 경로 경계 보강 | 최종 구성요소 junction/reparse 거부를 자체 로직으로 구현 + 회귀 테스트. 중간 구성요소·3회 재검증은 문서로 한계 고정 | [WORK_PROFILE.md](WORK_PROFILE.md), [agent_runtime.py](../talkabout/agent_runtime.py), [tests/test_work_profile.py](../tests/test_work_profile.py) | **부분 반영** — 코드·테스트 반영. 중간 구성요소·3회 재검증 미반영 |
| P1-2 read-only 진단 | `GET /rooms/{id}/diagnostics` — 닫힌 enum 대기 사유(room_not_active·dispatch_held·awaiting_worker_claim·worker_leased·lease_expired·awaiting_start_grant·deadline_passed·awaiting_human_resolution)를 **관측만** 계산. human 전용, 상태·event·attempt 불변 | [observation.py](../talkabout/observation.py), [api.py](../talkabout/api.py), [core.py](../talkabout/core.py), [tests/test_room_diagnostics.py](../tests/test_room_diagnostics.py) | **코드·테스트·API 반영** — UI 표시는 미반영 |
| P2-2 승인 effect-key(순수) | `canonical_arguments`·`effect_key`·`approval_state` 자체 함수. provider tool-call id 미입력, 닫힌 상태 어휘, 결정/전달/소비 구분, 무변경 | [approval.py](../talkabout/approval.py), [tests/test_approval_contract.py](../tests/test_approval_contract.py) | **순수 함수·테스트 반영** — 승인/실행 경로 미연결(A5/A6 승인 필요) |
| P1-3 A2 선언 scope 직렬화 | — | — | **미반영** (A2 실행 활성화 전 설계 대상) |
| P2·P3(나머지) | — | — | **미반영** (승인 스키마 확장, config flag 감지, capability token, POSIX P4) |

**P1-2 계약 요약.** 진단 응답은 `room_id·room_state·dispatch_held·last_seq·membership_revision·tasks[]`이고, 각 task는 `task_id·state·assignee·attempt·waiting_reasons`만 포함한다. 호출은 `store.snapshot` 읽기와 ACL 검사만 수행하며 event·task·attempt·cursor를 만들지 않는다. 빈 사유 목록은 "대기 없음(terminal)" 또는 "관측 대상 아님"을 뜻하며 **허용·검증·실행 증거가 아니다**. `uncertain`은 `awaiting_human_resolution` 하나로만 표시하고 일반 busy로 접지 않는다.

**라이선스 원칙.** 이번 반영은 7개 저장소의 **설계 패턴만 참고**했고 코드·문서 문장을 복사하지 않았다.
serena(GPL-3.0-or-later)와 openbot(PolyForm Noncommercial)은 라이선스상 코드 도입이 불가하므로 설계 논의만 인용했다.
새 dependency·vendored code·asset은 추가하지 않았으며, TalkAbout 자체 작성 코드와 합성 fixture만 추가했다.
출처·고정 revision·라이선스는 [2차 분석 소스 원장](../analysis/repo-review-2026-10-31/00-SOURCE-MANIFEST.md)에 기록했다.
라이선스가 제약인 기술을 **자체 로직으로 재구현**할 때의 절차·금지·기록 양식은 [독립 구현 정책](CLEAN_ROOM_IMPLEMENTATION.md)을 따른다.
