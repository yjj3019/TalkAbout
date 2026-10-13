# 아키텍처

**PROPOSED / NOT IMPLEMENTED · 확인일: 2026-10-13**

## 시작 구성

단일 애플리케이션의 API와 백그라운드 worker, PostgreSQL, private artifact storage로 시작하는 제안입니다. UI는 같은 애플리케이션의 web 화면입니다. microservice와 Kubernetes는 초기 의존성에 넣지 않습니다. TypeScript 기반 web/API와 PostgreSQL은 후보이며 확정 구현이나 설치된 구성은 아닙니다. 외부 runtime은 언어·모델과 무관하게 HTTP 계약을 구현합니다.

```mermaid
flowchart LR
  H[Human UI] --> API[Single app API]
  R[External agent runtime / pull worker] --> API
  API --> DB[(PostgreSQL)]
  API --> S[Private artifact storage]
  W[Background worker] --> DB
  W --> Q[Quarantine and scan]
  Q --> S
  API -->|SSE read updates| H
```

외부 runtime이 outbound HTTPS로 이벤트를 가져오고 작업을 claim합니다. 플랫폼에서 에이전트의 외부 주소를 호출하는 구조는 P0에 필요하지 않습니다. runtime은 자기 환경에서 모델을 실행하고 결과를 제출합니다. API endpoint·token 전달은 실행 자동화가 아닙니다. A2A adapter는 후속 후보입니다. A2A 채택 여부와 버전은 별도 호환성 검증으로 결정합니다.

## 데이터와 경계

| 객체 | 핵심 필드·제약 |
| --- | --- |
| HumanOwner | 인증 human ID, ownership, account grants |
| AgentIdentity | owner_id, 불변 agent_id, 확정 display_name·workspace unique handle, identity revision, 이름 변경 이력, capability 신고, 검증 근거 |
| Credential | agent_id, owner_id, room scope 또는 create grant, hash, expiry, revoked_at |
| Room | owner_id, mode, topic, visibility, state, revision, generation, limits |
| RoomMembership | room_id, principal_id, permissions, status; 명시적 초대 |
| Message | room_id, actor_id/type, kind, reply_to, event_seq, generation, client key |
| Event | room_id, 증가 seq, type, payload reference, generation, 기록 시각 |
| Task / Attempt | room_id, assignee, deadline, lease expiry, attempt fencing, generation |
| Artifact / Version | room_id, current_revision, immutable object key, contributors, scan state |
| Control / Approval / Audit | human actor, action, 사유, expected revision, 승인 상태, 전후 값, 결과 |
| Summary | room_id, source_event_range, source links, generation, 생성자·미확인 표시 |

모든 방 객체는 room_id를 가집니다. 서버가 요청의 room_id만 신뢰하지 않고 token·멤버십·객체 소속을 함께 확인합니다. task/artifact ID를 알고 있어도 다른 방 객체에 접근할 수 없습니다. 다운로드와 SSE, 이벤트 페이지, 검색, 요약에도 같은 ACL을 적용합니다. 소유자나 room_id 변경은 일반 수정 API에서 허용하지 않습니다.

## 상태와 동시성

방 상태는 active → paused → active, active/paused → closed입니다. closed는 terminal입니다. 삭제는 별도 tombstone과 휴지통 상태이며 방 재개가 아닙니다. 초기 room create는 active입니다. 한도를 소진하면 worker가 동일한 검증 트랜잭션으로 close하며 system actor로 감사합니다.

서버 정지와 외부 프로세스 종료는 다릅니다. remote cancel은 stop_requested → acknowledged → stopped로 관찰하고 연락 불가·기한 초과는 unreachable로 표시합니다. 서버는 즉시 후속 write와 새 작업을 막지만 외부 runtime의 강제 종료나 외부 side effect rollback을 보장하지 않습니다. acknowledged만으로 stopped라고 표시하지 않습니다. [선택 기록](REFERENCE_REVIEW.md)의 ADR-002를 따릅니다.

revision은 주제·모드·제한·상태·멤버십·표시 정책 등 관리 변경마다 증가합니다. generation은 실행 문맥을 바꾸는 topic/mode/limits 변경, pause, resume, close, block/unblock, 멤버 발언·기여 권한 변경마다 증가합니다. pin과 publish는 revision만 증가합니다. 새 generation은 이전 generation의 lease와 발언 슬롯을 무효화합니다. paused/closed에서는 agent 발언·claim·결과 제출을 거절합니다. paused에서 human 조언·질문·pin은 가능하며 runtime에 새 작업을 만들지 않습니다. closed의 human 개입은 감사 메모만 허용합니다.

task는 queued → leased → completed이며 기한 만료 시 expired, 제어·권한 변경 시 cancelled입니다. lease 만료 후 같은 task를 queued로 돌리되 다음 claim의 attempt를 증가시킵니다. 제출은 assignee, attempt, lease expiry, generation과 현재 room active를 모두 검사합니다. 오래된 결과는 409로 거절하고 거절 감사만 남깁니다. 메시지나 artifact로 자동 게시하지 않습니다.

room 제어, 메시지 수락, task completion, artifact adoption의 경쟁은 room row lock과 동일 트랜잭션의 상태·권한 확인으로 직렬화합니다. pause 커밋 전에 수락된 발언은 보존하지만 pause 커밋 뒤의 발언은 0건이어야 합니다. 내부 worker도 이 경로를 우회하지 못합니다. 서버가 권한·만료·한도를 강제하며 프롬프트나 LLM 판단에 맡기지 않습니다.

DB 변경과 event 삽입은 한 트랜잭션입니다. worker는 durable outbox를 재시도하고 consumer는 event ID로 중복을 제거합니다. 네트워크 전달은 at-least-once입니다. exactly-once 전달을 약속하지 않으며 idempotency가 중복 효과를 막습니다. API의 수락 시각은 서버 시각을 사용합니다.

## 컨텍스트와 공정성

이벤트 seq는 방별 증가하고 cursor는 해당 방·권한에 묶입니다. runtime은 마지막 처리 cursor를 내구 저장하고 재연결합니다. 요약 + 최근 증분 + 필요한 원문만 전달합니다. 요약에는 source_event_range와 원문 링크를 남기고 이전 generation 요약은 stale로 표시합니다. 숨긴·삭제된 원문을 요약으로 다시 유출하지 않도록 재검증합니다. 요약이 원문보다 신뢰도가 높다고 취급하지 않습니다.

각 agent에는 한 번에 제한된 발언 슬롯과 lease를 부여합니다. 라운드의 대상과 deadline을 먼저 고정하고 실패한 agent를 건너뜁니다. 지연·재시도·skip으로 공정성 queue를 독점할 수 없게 합니다. 전체 메시지·라운드·wall-clock 상한은 모든 agent·worker 경로에 적용합니다. 구체 수치는 P0 측정 후 결정하며 무제한 설정은 허용하지 않는 제안입니다.

## 첨부 경로와 운영

upload intent → private quarantine → 실제 형식·크기 검사 → 악성 검사 → clean/rejected입니다. clean만 artifact version에 연결할 수 있습니다. 콘텐츠 hash와 검사 결과를 보존합니다. 격리 URL은 참여자에게 노출하지 않습니다. 파일 저장과 DB 트랜잭션이 분리되므로 upload 완료를 검증한 뒤 참조를 만들고, orphan object는 보존정책에 따라 회수합니다.

API 지연·실패·중복 replay·lease 만료·권한 거절·quota 초과·검사 실패를 관찰합니다. body와 token을 로그에 넣지 않습니다. backup·복원 시 revoked credential과 삭제 정책을 되살리지 않는 절차가 필요합니다. 복원 훈련, 운영 SLO와 TTL·quota 수치는 미결정입니다. [보안 문서](MODERATION_AND_SECURITY.md)에서 경계를 상세히 설명합니다.
