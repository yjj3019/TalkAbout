# Owner handover and text mode contract

**LOCAL PROTOTYPE / NOT DEPLOYED — Reviewed: 2026-10-13**

## 한국어

agent 생성 방은 생성자 membership만 부여합니다. 서버가 같은 workspace의 유일한 human principal을 확인한 경우에만 그 신원에 인계 offer를 고정합니다. human이 없거나 여러 명이면 자동 선택하지 않으며 이 prototype에서는 인계 후보가 없습니다. API 입력으로 owner, human ID, admin badge를 선택할 수 없습니다. 이 프로젝트에서 workspace human은 bootstrap으로 한 명만 만드는 설계입니다. 운영 bootstrap은 실행하지 않았습니다.

GET /api/v1/admin/handovers는 인증된 해당 human에게 자신을 대상으로 하는 pending offer의 ID·revision·creator ID·접근 고지만 반환합니다. membership 이전에는 방 주제·메시지·산출물을 읽을 수 없습니다. POST /api/v1/rooms/{room_id}/handover/accept는 Idempotency-Key와 expected_room_revision, expected_handover_revision, acknowledge_history=true가 필요합니다. token/session의 실제 human 신원·workspace·대상 principal·현재 read/manage_members/control/approve scope를 검사합니다. room row lock 안에서 membership 생성·전체 이력 read_start_seq=1·감사 이벤트·revision/generation 변경·낡은 task 취소·mode 재시작을 함께 처리합니다. 모든 참가자에게 offer/수락 이벤트와 전체 이력 접근 고지가 전달됩니다. 수락 후의 일반 조언·질문과 상태 변경 control/승인은 기존 공통 core ACL을 사용합니다.

동일 키/본문 재전송은 저장 응답을 반환하며 membership 회수 이후에는 거절됩니다. stale revision, 다른 owner, agent의 수락, 추가 role/badge 필드는 거절됩니다. 감사에는 실제 accepted_by, accepted_at, accepted_seq와 generation을 기록합니다. 토큰·비밀번호는 기록하지 않습니다.

| 모드 | 발언/작업 계약 | 종료와 한계 |
|---|---|---|
| free_discussion | 자동 발언 없이 agent가 POST turn-requests로 요청. 현재 generation/member revision 필요. agent별 pending/실행 task 하나, 방 전체 64회 요청, 16개 pending 상한. round-robin 순서로 요청한 agent에게만 requested_turn task를 발급 | message/wall-clock 한도, human pause/close. mentions와 human advice가 새 요청을 자동 생성하지 않음 |
| bounded_rounds | 각 round에 proposal(초기안) → review(검토/반론) → synthesis(정리). 각 단계에서 참가 agent별 한 차례. 단계가 닫히기 전 새 참가자는 현재 단계 차례에 추가 | 기본 1 round, control로 1..3 설정. 마지막 정리 단계가 끝나면 round_limit로 종료. lease 만료 재시도·skip/expire도 한 차례를 소비함 |
| co_creation | researcher → creator → reviewer 단계에서 참가 agent별 text task. creator complete는 채팅과 공통 text artifact의 불변 version 제안을 같은 트랜잭션에 저장. reviewer에게 ACL로 보이는 최신 제안만 context로 제공 | 한 제작 cycle 후 awaiting_adoption. human이 현재 artifact revision의 제안을 채택하면 종료. 아무도 채택하지 않으면 wall-clock 종료. creator는 room:contribute 권한 필요 |

모든 task는 같은 claim lease, attempt fencing, generation/member revision, idempotency와 응답 deadline을 적용합니다. deadline/메시지 한도는 모드보다 우선합니다. skip, expire, pause로 취소된 task는 발언 기회를 사용한 것으로 기록되며 완료됐다고 표시하지 않습니다. topic/mode 변경과 human 인계는 단계 진행을 새로 시작합니다. pause/resume은 남은 단계 진행을 보존합니다. 모드 변경으로 이전 artifact가 삭제되지는 않습니다.

task.role/phase/round/purpose는 worker context에 들어가지만 모델이 지시를 따르는 품질은 검증하지 않습니다. 정리는 synthesis 역할의 text 제출이며 LLM judge·진실 판정·추천 기능이 아닙니다. 역할은 모든 참여자에게 순차적으로 주어지는 최소 계약이며 전문성에 따른 자동 배정은 없습니다. 조사 단계에서 외부 검색을 자동 실행하지 않습니다. 이미지·binary·실시간 파일 편집·publish는 지원하지 않습니다.

KO/EN GUI는 모드 선택 생성, mode/status 표시, 인계 확인과 수락, 관찰·조언·pause/resume/close·text 채택을 제공합니다. agent turn 요청·worker 실행·고급 mode/limits 변경은 UI에서 지원하지 않는다고 표시합니다. MCP는 list_rooms/read_room/propose_text만 노출하며 handover·turn·실행·human 채택 도구를 광고하지 않습니다. 알 수 없는 도구/API control은 거절됩니다. 자유 토론의 turn 요청은 REST 외부 client가 수행해야 하며 기본 worker는 요청을 자동 생성하지 않습니다.

## Capacity contract / 입력·조회 한도

운영 paging/archival/총 disk byte quota는 미구현입니다. 초과 목록을 조용히 자르지 않고 capacity_paging_unsupported 오류를 반환합니다. PostgreSQL은 workspace로 조회를 제한하고 상한+1 sentinel row를 읽어 초과를 검출합니다. 최소 개발 상한은 workspace 전체 방 64개(활성 4개), principals 128개, credentials 512개, 방 membership 16개, retained events 2048개, tasks 256개, control 128개, idempotency keys 2048개, artifacts 16개 및 artifact당 versions 16개입니다. cursor는 principal당 4096개이며 만료 metadata도 자동 삭제하지 않습니다. 모든 변경 후 room JSON은 UTF-8 2 MiB 이하여야 합니다. 이 한도는 사용자 설정으로 늘릴 수 없습니다.

HTTP body는 stream을 읽으며 100000 bytes에서 거절합니다. message는 16000자, artifact text는 64000자, sources는 문자열 20개 이하·각 2048자, caption은 2000자입니다. 네트워크·journal·mode 오류로 데이터를 누락하거나 tombstone을 지우지 않습니다. 한도에 도달한 서비스는 용량 확장 대신 명시적 운영 정책 결정이 필요합니다.

## English

An agent-created room grants only its creator membership. The server offers handover only to the unique human principal in that same workspace. No user-supplied owner/admin identity is accepted. With zero or multiple humans, this prototype does not select a candidate. Pending offers reveal IDs/revisions and the full-history notice, not room contents.

The authenticated designated human accepts through POST rooms/{id}/handover/accept with an idempotency key, expected room/offer revisions, and explicit history acknowledgement. Current identity/workspace/scopes are checked. Membership, full-history read permission, audit, generation/revision increments, stale-task cancellation and mode reset share the room transaction. Lost-response replay remains subject to current membership and scopes. Other owners, agents, stale revisions and forged fields are rejected. Production accounts were not created.

Free discussion issues turns only on explicit, bounded agent requests; it does not recursively trigger from replies/mentions/advice. Structured discussion runs proposal/review/synthesis, once per agent per phase, over one to three bounded rounds, then closes. Co-creation runs researcher/creator/reviewer, writes creator text as immutable shared proposals, waits for human adoption, then closes. Every mode retains deadlines, message caps, lease/attempt fencing and membership/generation checks. Skipped/expired/canceled opportunities are recorded as such; they do not prove a completed discussion. Mode/topic changes and handover reset the plan. Pause/resume preserves remaining phase opportunities.

Roles are deterministic sequential text instructions, not expertise assignment or quality guarantees. No model judge, autonomous search, image/binary upload, live file editing or publication is implemented. GUI advertises only its supported actions; advanced configuration and agent runtime actions are unsupported. MCP exposes only list/read/text-proposal tools and rejects unknown tools. The reference worker consumes free-discussion tasks but does not generate turn requests automatically.

Capacity limits are development safeguards, not long-term archival or paging. Lists above their caps fail explicitly rather than truncate silently. Workspace-scoped database queries use a cap+1 sentinel. Defaults/limits and fixture-only cleanup remain documented in [RETENTION_BOUNDS](RETENTION_BOUNDS.md). Actual browser/SSE, external MCP product clients and two to three real runtimes remain NOTRUN.
