# Runner 예약·원장 계획 / Runner reservations and ledger

**R1+R2 IMPLEMENTED / R3-R5 NOT IMPLEMENTED · 확인일: 2026-10-27**

`feat/paired-local-runner`(2026-10-24 스냅샷)에 남아 있던 마지막 트랙이다. 이 문서는 **설계 + R1·R2 구현 기록**이다.
서버 예약 API(R1)와 worker 연동(R2)은 구현됐고, 실행 프로토콜(R3)과 vault(R4)는 아니다.
**이제 worker는 실제로 호출 전에 예약을 요청하므로** "attempt당 유료 호출 1회"가 제품 경로에서 강제된다.
다만 공급자 실호출 시험은 여전히 NOTRUN이다.

## 1. 무엇이 문제인가

현재 worker는 claim → runtime 호출 → complete 순서로 움직인다. 모델 호출은 **되돌릴 수 없고 과금된다**.
그런데 호출과 서버 회계 사이에는 세 가지 틈이 있다.

| 틈 | 지금 일어나는 일 |
| --- | --- |
| **호출 전 예약 없음** | 같은 task attempt로 두 worker가 동시에 claim 경쟁에서 이기면(또는 재연결 후 같은 claim을 재사용하면) 호출이 두 번 나갈 수 있다. 서버는 `_started_attempts`(runtime_started 이벤트가 있는 시도)로만 예산을 세므로, **첫 호출이 실패하거나 끊기면 두 번째 호출은 정상 예산으로 계산된다** |
| **결과 불명** | 호출은 나갔는데 응답이 유실되면 worker는 `uncertain`으로 남기고 재시도하지 않는다. 이건 안전하지만, 그 task가 **영원히 막힌다**(사람이 확인할 때까지) |
| **로컬 회계 유실** | worker의 journal이 손상되거나 다른 PC로 옮기면, 어떤 예약이 이미 소비됐는지 로컬에서 알 수 없다 |

WIP는 이 중 **로컬 절반만** 구현했다.

## 2. WIP 스냅샷의 실제 상태 (중요한 정정)

`git grep -n "runner/reservations" origin/feat/paired-local-runner` 결과는 다음과 같다.

```text
talkabout/runner_client.py:    POST runner/reservations
talkabout/runner_client.py:    POST runner/reservations/<id>/outcome
tests/test_runner_client.py:   fake client로만 검증
```

즉 **서버 엔드포인트는 존재하지 않는다.** 스냅샷은 "클라이언트가 기대하는 API"까지 쓰고 멈춘 상태다
(`Preserve audited remediation work in progress`). 따라서 이 트랙은 **서버 설계부터** 시작해야 한다.

| WIP 자산 | 재사용 가능성 |
| --- | --- |
| `RunnerLedger`(SQLite, reservation_id → state/outcome, capacity 64) | **재사용 가능** — worker journal에 테이블 추가 형태 |
| `GrantedRuntime`(예약 → 실행 → outcome flush, `execute is not True`면 호출 금지) | **재사용 가능** — 현재 `bounded_call` 앞에 얇게 얹힘 |
| `runner_client.py`의 `WindowsGrantVault`(DPAPI) | **별도 트랙** — 자격증명 저장 정책 결정 필요 |
| `runner_bridge.py`(loopback HTTP + nonce + origin 검사) | **거부 후보** — 현재 `trial_http.py`의 로컬 시험 서버와 역할이 겹친다 |
| `collaboration.py` | **이미 재구현 완료**(PR #37) |
| `tasks/{id}/start`, `execution_protocol` | **재구현 대상** — 예약을 "실행 시작"으로 승격하는 서버 계약 |

## 3. 제안 설계 (R1: 서버 예약)

### 3.1 예약 생성

```text
POST /api/v1/rooms/{room_id}/tasks/{task_id}/reservations
```

요청(모두 필수, pending body와 동일한 fence):

```json
{"generation": 2, "membership_revision": 3, "attempt": 4,
 "purpose": "task", "reservation_id": "<sha256 hex>"}
```

- `reservation_id`는 **클라이언트가 계산**한다: `sha256(grant_id, purpose, room_id, generation,
  membership_revision, task_id, attempt, challenge?)`. 같은 입력 → 같은 ID → **재시도가 멱등**이다.
- 서버는 `fencing()`으로 최신 상태를 확인하고, 그 다음에만 예약을 기록한다.
  - `task.state == 'leased'`, `task.attempt == attempt`, lease/deadline 유효
  - `room.state == 'active'`, `dispatch_held == false`
  - `purpose == 'task'`면 `room_id` 일치, `connection_test`면 방 없이 1회
- 응답: `{"execute": true}` 또는 **거절 사유**. `execute`가 `true`가 아니면 클라이언트는 **호출하지 않는다**.
- 예약은 `task["reservations"]`에 **최대 8건** ring으로 남긴다(방 payload 안이므로 DB 마이그레이션 없음).
  PostgresStore도 방 JSON을 그대로 저장하므로 스키마 변경이 필요 없다.

### 3.2 결과 보고

```text
POST /api/v1/rooms/{room_id}/tasks/{task_id}/reservations/{reservation_id}/outcome
```

```json
{"state": "completed"}   // 또는 "uncertain"
```

- `completed`: 해당 예약을 종료로 표시. `attempt` 예산은 그대로 유지된다(이미 시작된 시도).
- `uncertain`: task를 `uncertain`으로 유지하고 **자동 재시도 금지**. 사람이 `skip`/`close`로만 정리 가능.
- outcome 보고가 실패해도 client ledger에 남아 다음 step에서 재전송한다(WIP의 `flush_outcomes`).

### 3.3 예약과 예산의 관계

- 예약 = "이 attempt로 호출해도 되는 유일한 허가". `execute:false`면 호출 자체를 하지 않으므로
  **이중 과금이 원천 차단**된다.
- 예산(`_started_attempts`)은 지금처럼 `runtime_started` 이벤트로 센다. 예약은 그 **앞단의 잠금**이며,
  예산 계산 방식을 바꾸지 않는다.

## 4. 단계와 산출물

| 단계 | 내용 | 선행 조건 | 산출물 |
| --- | --- | --- | --- |
| **R1** ✅ | 예약 생성/결과 보고 2개 endpoint + `task._reservations` ring + fencing + 테스트 — **구현 완료**(2026-10-26) | — | `tests/test_runner_reservations.py` 14건, [API_DRAFT.md](API_DRAFT.md) |
| **R2** ✅ | worker 예약 호출 + journal 결과 큐 + `execute:false` 시 호출 금지 — **구현 완료**(2026-10-27). 원장은 journal의 `reservation_outcomes` 테이블로 흡수했고, 별도 `GrantedRuntime` 클래스는 불필요했다(기존 `bounded_call` 앞에 게이트 배치) | R1 | `tests/test_worker_reservations.py` 6건 |
| **R3** | `tasks/{id}/start` 실행 프로토콜(`execution_protocol='server_start_v1'`) — 예약을 실행 시작으로 승격 | R1·R2 | 코드 + 테스트 |
| **R4** | 자격증명 vault(DPAPI 등) — **정책 결정 필요** | 별도 검토 | — |
| **R5** | loopback bridge — **거부 권장**(`trial_http`와 중복) | — | — |

## 5. 거부하는 것

- **자동 재시도 금지**: `uncertain`을 스스로 `completed`로 바꾸지 않는다.
- **outcome 추정 금지**: 응답이 없으면 `uncertain`이며, "아마 성공했을 것"으로 세지 않는다.
- **vault 없이 자격증명 파일 저장 금지**: 평문 파일·환경변수 외 저장은 OS 보호(DPAPI/keyring) 전까지 하지 않는다.
- **bridge 신설 금지**: 현재 `trial_http.py`가 로컬 시험용 서버를 이미 제공한다. 두 개의 loopback 경로를 두지 않는다.
- **sandbox 주장 금지**: 예약 API는 과금·수명 문제를 다루지, 파일/네트워크 격리를 제공하지 않는다.

## 6. 검증 계획 (R1 기준)

1. 같은 `reservation_id`로 두 번 호출 → 두 번째는 `execute:false`(멱등)이고 예약은 1건.
2. stale generation/attempt/lease → 예약 거절, 방 상태 불변.
3. `dispatch_held`·`paused`·`closed` → 예약 거절.
4. 예약 8건 초과 시 가장 오래된 것부터 정리되고 `reservation_capacity`는 나지 않는다(bounded ring).
5. 예약 후 `uncertain` 보고 → task가 `uncertain`, 재예약 거절.
6. `completed` 보고 후 task `completed` → 예약 재사용 불가.
7. 두 worker 동시 예약(같은 attempt, 다른 reservation_id) → **한쪽만 `execute:true`**.
8. 모든 거절 경로에서 모델 호출이 0회였음을 fake runtime으로 확인.

## 7. 현재 상태

- 코드: **R1·R2 구현됨** — `talkabout/domain.py`의 `grant_reservation`·`reservation_outcome`, `core.py` 라우팅과 예약 재생 캐시 제외, `api.py` 허용 경로, `worker.py`의 `reservation_id`·예약 게이트·`reservation_outcomes` 원장.
- 테스트: `tests/test_runner_reservations.py` **15건**(서버 계약) + `tests/test_worker_reservations.py` **6건**(worker 연동: 승인 후 1회 호출, 거절 시 0회 호출, 응답 유실 재시도가 replay로 처리, 실패 호출의 uncertain 보고, 미전달 보고의 큐 재전송, 결정적 id).
- **이제 강제된다**: worker는 `execute:true` 없이는 runtime을 호출하지 않고, 시작한 호출의 결과를 반드시 보고한다(전달 실패 시 journal에 남겨 다음 step에서 재전송).
- **미구현**: 실행 프로토콜(R3), vault(R4).
- 이 트랙의 남은 관문은 [STATUS.md](STATUS.md) §4에 있으며, `feat/paired-local-runner` 브랜치에
  WIP 자산(Ledger/GrantedRuntime/vault/bridge)이 아카이브로 남아 있다.
