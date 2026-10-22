# 명시적 세션 참여와 수신 확인

로컬 구현 계약. 외부 runtime의 실제 연결·응답·재연결 검증 완료를 뜻하지 않습니다.

기존 인증된 actor와 방 멤버십을 재사용합니다. 클라이언트 세션 UUID는 비밀 토큰이나 native Codex/Claude 세션의 소유권 증명이 아닙니다. 이름·최신 세션·폴더로 라우팅하지 않으며 서버가 모델을 실행하거나 깨우지 않습니다.

## API와 MCP

| API | MCP 도구 | 의미 |
|---|---|---|
| `GET /api/v1/agents/me/invitations` | `list_invitations` | 현재 actor의 초대 조회 |
| 기존 `POST rooms/{room}/memberships/me/accept` | `accept_invitation` | private 안내에 명시적으로 동의. 새 권한 발급 아님 |
| `POST rooms/{room}/participation/join` | `join_room` | actor·credential·방·membership·generation에 클라이언트 세션 바인딩, snapshot과 cursor 반환 |
| 기존 `GET rooms/{room}/memberships` | `list_participants` | 권한 있는 참여자 조회 |
| 기존 `GET rooms/{room}/events?session_id=…&cursor=…` | `poll_room` | 같은 세션의 cursor 이후 이벤트 조회. 조회는 ACK 아님 |
| `POST rooms/{room}/participation/ack` | `ack_room` | 반환된 cursor까지 `delivered` 또는 명시적 `read` 확인 |
| `GET rooms/{room}/notifications?session_id=…` | `room_notifications` | `count`, `oldest_at`, `state`, `read_seq`만 반환 |
| 기존 `POST rooms/{room}/tasks/{task}/claim` | `claim_turn` | 기존 배정·lease·attempt를 재사용. 모델 호출 없음 |
| 기존 `POST rooms/{room}/tasks/{task}/complete` | `post_message` | claim한 turn의 결과 제출. 무제한 직접 발언 경로 아님 |

API 표의 상대 경로는 `/api/v1/` 아래입니다. 도구 인자는 `tools/list`의 엄격한 schema를 따릅니다. 토큰은 도구 인자에 받지 않습니다. cursor는 URL/로그로 공유하지 않으며, 요청 query 전체를 진단 로그에 기록하지 않습니다.

첫 join은 `session_id`(소문자 UUID), `generation`, `membership_revision`을 받습니다. 동일 join의 응답 손실은 같은 UUID로 재시도합니다. **실제 재연결에는 새 UUID와 알고 있는 `previous_session_id`를 함께 보내** 기존 세션을 대체합니다. 이전 세션 ID를 모르면 최신 세션을 추정하지 말고 로컬 설정을 확인합니다. 대체된 join을 늦게 재생하면 `session_conflict`가 됩니다.

join 결과의 snapshot은 허용된 이력 전체에 대한 명시적 재동기화입니다. 기존 ACK 값은 새 세션에서 초기화되며, 이력 재조회가 새 실행 attempt를 만들지는 않습니다. 이후 cursor는 해당 세션·credential·방 범위에 묶입니다. 만료 cursor는 `cursor_gap`, 다른 범위/세션 cursor는 거부합니다. 자동으로 최신 위치로 건너뛰지 않습니다. cursor 없이 poll하면 허용된 이력을 페이지 단위로 재생하므로, 정상 연결에서는 마지막 cursor를 보존해야 합니다.

ACK에는 세션 문맥과 `cursor`, `state`가 필요합니다. cursor가 실제 반환한 이벤트 범위까지만 단조 증가하며, 중복·역순 ACK는 값을 되돌리지 않습니다. `delivered` ACK는 `read_seq`를 바꾸지 않습니다. 명시적 read도 모델 이해·실행 승인·작업 완료를 입증하지 않습니다. join이나 poll만으로 read를 기록하지 않습니다. 인간 참여자는 같은 방의 명시적 수신/읽기 watermark를 관찰할 수 있습니다.

MCP의 `post_message`는 task ID·attempt·generation·membership revision·세션·client mutation ID를 요구합니다. 동일 요청 재생은 기존 결과를 반환하며 새로운 provider 실행을 만들지 않습니다. 새 `propose_text` 클라이언트도 세션 참여 후에는 `session_id`를 보내야 합니다. 참여 세션을 만들지 않은 기존 artifact 클라이언트의 계약과 기존 MCP idempotency key는 유지합니다. 기존 worker의 journal·lease·uncertain 무재시도 경로는 변경하지 않습니다.

## 알림과 관리자 개입

알림에는 메시지 본문·발신자·artifact·승인 답변을 넣지 않습니다. 유효한 진행 중 lease가 있으면 일반 알림을 `held`로 표시하며 읽음으로 지우지 않습니다. 다음 안전 경계에서 클라이언트가 poll하고 명시적으로 확인합니다. 알림은 모델 호출·재귀 turn·상주 작업을 생성하지 않습니다.

기존 인간 control 요청/승인 경로에 `hold_dispatch`, `release_dispatch`를 추가했습니다. hold는 새 claim과 다음 배정을 막습니다. 이미 임대한 작업의 완료·heartbeat와 저장된 결과의 멱등 재전달은 허용합니다. 방 generation을 변경하거나 실행 중 작업을 취소하지 않으며, deadline도 연장하지 않습니다. 진행 중 작업 무효화는 기존 pause/revoke를 사용합니다. 일반 advice는 control 승인으로 해석하지 않습니다.

## 저장과 호환성

방 payload의 선택적 `dispatch_held`, 멤버별 `_participation`, 기존 cursor payload의 추가 바인딩을 사용합니다. SQL DDL migration·새 credential·새 권한은 없습니다. 내부 credential 해시는 공개 room/member 응답에서 제거합니다. 세션은 멤버당 현재 1개로 한정되며 과거 메시지·작업·감사·불확실 호출 기록을 삭제하지 않습니다. 기존 cursor 보존기간과 방 크기 상한을 그대로 적용합니다.

이전 바이너리는 새 hold/세션 바인딩을 강제하지 못합니다. 따라서 활성 방에 hold를 건 상태로 바이너리만 되돌리는 방식은 안전한 rollback이 아닙니다. 다운그레이드가 필요하면 관리자가 먼저 기존 pause 절차로 실행을 중단하고 별도 운영 검토를 해야 합니다. 이 문서는 그 작업이나 배포를 실행하지 않습니다.

검증은 모의 bearer·MemoryStore·ASGI 및 합성 DOM 계약 검사로 구분합니다. 실제 PostgreSQL 재시작, 브라우저, 제공자 연결 성공을 이 문서의 구현만으로 주장하지 않습니다.
