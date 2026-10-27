# Runner 예약·실행 시작 계약 / Reservations and server start

**R1·R2·R3 LOCAL IMPLEMENTATION · 2026-10-27 · 공급자 실호출 NOTRUN**

## 1. 범위와 보장

R1은 서버 예약, R2는 worker 연결, R3는 별도 실행 시작 승인이다.
현재 worker는 claim에 `execution_protocol: "server_start_v1"`를 보내며 다음 순서를 따른다.

```text
claim → 입력/fence 검증 → reservation → durable start_pending
      → POST tasks/{id}/start → execute:true → runtime
      → atomic result_saved + completed outcome intent → outcome → complete
```

보장은 **협력하는 worker에 대해 보존된 task attempt마다 실행 승인 최대 1회**이다.
서버의 `started`는 실행을 허가했다는 기록이지 모델이 실행됐거나 입력을 읽었다는 영수증이 아니다.
공급자의 exactly-once 실행·실제 과금 금액·악의적인 클라이언트의 외부 호출을 보장하지 않는다.
Legacy claim은 이전 계약을 유지하므로 서버 전체에 R3를 강제한다고 주장하지 않는다.

## 2. API 계약

모든 경로는 `/api/v1/rooms/{room_id}/tasks/{task_id}` 아래에 있다.
권한은 기존 계정 scope ∩ membership ∩ credential scope를 그대로 사용한다.

| 경로 | 계약 |
| --- | --- |
| `POST claim` | 선택 `execution_protocol: "server_start_v1"`. 알 수 없는 프로토콜은 거절. 기존 클라이언트는 legacy 동작 |
| `POST reservations` | full fence(`attempt`, `generation`, `membership_revision`), `purpose: "task"`, `reservation_id`(64자리 소문자 hex). R3에서는 `reserved:true, execute:false` — 준비만 했고 실행 허가 아님 |
| `POST start` | full fence + `reservation_id`. 현재 active room·dispatch hold 해제·현재 member revision·lease/deadline·동일 attempt의 예약 확인. 최초 승인만 `execute:true`; 다시 요청해도 승인 재생 안 함 |
| `POST reservations/{id}/outcome` | `state: completed\|uncertain`. 현재 ACL을 확인하되 pause/close 뒤에도 원래 예약에 결과 기록 가능. 오래된 attempt의 보고가 새 attempt를 은퇴시키지 않음 |
| `POST complete` | R3 claim은 현재 attempt의 started/completed 예약 필요. 기존 completion fence·manifest 검증도 유지 |

예약과 start 응답은 generic idempotency 응답 캐시에서 재생하지 않는다.
Idempotency-Key 자체는 필수이며, `execute:true` 응답을 캐시로 다시 주지 않는다.

예약 id는 worker가 purpose·room·task·attempt·generation·membership_revision으로 결정적으로 계산한다.
저장은 room payload의 비공개 `task._reservations` ring(8건)이며 PostgreSQL DDL 변경은 없다.

## 3. 시작·예산·수명

- 예약만 된 상태는 실행 허가가 아니다. 예약 응답 유실 후에는 안전하게 재조회하고 start를 요청할 수 있다.
- start는 예약을 `started`로 바꾸고 `_started_attempts`에 서버 측에서 한 번만 기록한다.
  이는 **보수적 승인 예산**이며 실제 청구 증거가 아니다. worker heartbeat는 실행 단계 telemetry다.
- durable `start_pending` 재개 시 서버 예약 상태를 확인한다. 아직 reserved면 start 전 crash이므로 안전하게 시작 절차를 재개한다.
  이미 started면 승인 응답 유실로 판단해 호출하지 않고 `uncertain`으로 보고한다.
  호출이 실제로 없었더라도 불확실성 때문에 `uncertain`으로 기록할 수 있다.
- 시작 승인된 task의 lease/deadline 만료는 자동 requeue가 아니라 `uncertain`이다.
  새 journal이 생겨도 같은 실행을 새 attempt로 반복하지 않도록 한다.
- `uncertain`은 자동 재실행하지 않는다. 기존 human room close로 방을 종료할 수 있다.
  방 소유자는 `POST /api/v1/rooms/{room}/tasks/{task}/resolve-uncertain`로 해당 attempt를 **기록상** 확인 처리할 수 있다.
  이 호출은 `ack_unknown_effects: true`와 `reason_code`만 받고, task 상태를 `uncertain` 그대로 두며
  requeue·재실행·완료·`skip`을 하지 않는다. 응답·event의 `external_effect`는 항상 `unknown`이며
  "실행되지 않음"이나 "부작용 없음"을 뜻하지 않는다. 해소 기록 후에도 새 task 생성은 막지 않으므로
  추가 실행을 멈추려면 `hold_dispatch`를 함께 쓴다.

## 4. 결과 저장과 보고

provider 반환, 결과 로컬 저장, 서버 contribution 수락은 서로 다른 단계다.

- worker는 반환 텍스트와 `completed` 보고 의도를 **한 SQLite transaction**으로 저장한 뒤 보고한다.
- provider가 반환했다면 heartbeat/게시 실패를 이유로 `completed`를 `uncertain`으로 모순되게 바꾸지 않는다.
- timeout·예외·start_pending/call_started 재개는 결과 불명으로 보고한다.
- `reservation_outcomes` 큐는 기본 상한 64이며 미확인 항목을 조용히 삭제하지 않는다.
  상한에서는 새 실행 전 backpressure를 적용한다.
- 전송 실패나 서버 거절은 acknowledgement가 아니므로 큐에 보존한다.
  권한 회수·영구 거절 시 자동 reconciliation 성공을 주장하지 않는다. 진단/사람 개입이 필요하다.

## 5. 단계

| 단계 | 상태 |
| --- | --- |
| R1 서버 예약 API | 로컬 구현 |
| R2 worker 연결·SQLite 보고 큐 | 로컬 구현 |
| R3 server_start_v1·실행 승인·서버 예산·만료 은퇴 | 로컬 구현, 합성 검증 |
| R4 OS 보호 자격증명 vault | 사용자 선택: Windows current-user DPAPI opt-in. 로컬 구현: [CREDENTIAL_VAULT.md](CREDENTIAL_VAULT.md) |
| R5 loopback bridge | 신설 거부 권장: 기존 trial_http와 중복 |

원본 `feat/paired-local-runner`는 클라이언트 Ledger/GrantedRuntime/DPAPI/bridge 스냅샷만 보존한다.
그 스냅샷의 `runner/reservations` 서버 엔드포인트는 없었다. 현재 구현은 main 구조에 맞춰 재작성했다.

## 6. 검증 경계

서버 테스트는 start 승인·재생·동시 경쟁·stale fence·hold·완료 우회 차단·lease 만료·늦은 outcome을 검사한다.
worker 테스트는 예약 응답 유실과 start 응답 유실을 구분하고, 승인 전 호출 0회,
결과/보고 의도 원자 저장, 큐 포화 시 미확인 보고 보존을 검사한다.

MemoryStore 테스트는 PostgreSQL 동시성의 대체가 아니다. CI의 isolated PostgreSQL 및 기존 회귀와 함께 확인한다.
실제 provider 실행, 운영 배포, vault, 네트워크/파일 sandbox 보장은 이 계약 범위가 아니다.
