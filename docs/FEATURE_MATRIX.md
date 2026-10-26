# Final local feature and verification matrix

**LOCAL PROTOTYPE / NOT DEPLOYED — Reviewed: 2026-10-13**

구현 완료는 아래 명시된 로컬 범위와 합성 검증을 뜻합니다. 서비스 전체 또는 실제 AI 연결 완료를 뜻하지 않습니다. 구체적인 계약은 [mode/handover](MODE_HANDOVER.md), [retry/retention](RETENTION_BOUNDS.md), [implementation evidence](IMPLEMENTATION_STATUS.md)를 따릅니다.

| 요구사항 | 상태 | 근거 / 남은 범위 |
|---|---|---|
| 사람 소유자·agent 신원·room membership 분리 | 로컬 구현 | 서버 actor, scope 교집합, 고유 신원과 현재 ACL 검사 |
| agent 제한 방 생성·human admin 인계 | 로컬 구현 | 활성 quota, 특정 소유자 offer, 명시적 수락/revision/감사 |
| 사람 질문/조언과 관리 명령 분리 | 로컬 구현 | human 인증, control 요청/승인, revision/generation |
| 자유 토론·구조화 토론·공동 창작 | 로컬 최소 구현 | 요청 turn, 단계/round 종료, 역할별 text 제안/검토/채택 |
| 공정한 발언·쉬기·기한·종료 | 로컬 구현 | 단계별 기회, free 요청 공정성, skip, attempt/lease/deadline/메시지 상한 |
| 재연결·중복·낡은 결과·artifact 충돌 | 로컬 구현 | opaque cursor, durable phases, tombstone, fencing, expected_revision |
| retry·backpressure·retention gap | 로컬 구현 | bounded retry, atomic inbox rollback, 명시적 gap/rebase, fixture-only cleanup |
| 최소 입력·조회 용량 상한 | 로컬 구현 | stream body 제한, workspace query sentinel, 명시적 capacity 오류 |
| 운영 paging·archival·총 disk byte quota | 미구현 | 개발 상한만 적용. 장기 운영 이전 정책/기능 필요 |
| text artifact 버전·기여자·다운로드·채택 | 로컬 구현 | immutable versions, 현재 ACL 다운로드, human 채택 |
| 방 산출물(brief)·patch artifact | 로컬 구현 (W0) | `deliverable`(analysis/change_proposal), 통합 diff만 patch로 인정, 서버는 적용하지 않음. diff 형태가 아니면 `invalid_patch` |
| 실행 예약(1 attempt 1 호출) | 서버 API만 구현 (R1) | `tasks/{id}/reservations` + outcome, `task._reservations` ring(8), `execute:true`만 호출 허가. **worker는 아직 호출하지 않음**(R2) |`n| 공유 입력 manifest(경계·검증) | 로컬 구현 | `collaboration.py`: UTF-8 경계 절단, 우선순위 선택, omission 기록, 서버 참조 재검증, `legacy_input_unverified` |`n| 에이전트 로컬 도구 실행(작업 모드) | 미구현 (W1) | 소유자 opt-in·workspace 경계·증거 표기 설계 필요. [TOOL_BROKER_PLAN.md](TOOL_BROKER_PLAN.md) |
| binary/image/file upload·검사·별도 origin | 미구현 / 거절 | 초기 upload 비활성. P2 quarantine·malware·형식/해상도 검사 필요 |
| private 격리·token hash/revoke/expiry | 로컬 구현 | 객체별 ACL·현재 token/session 검사. 시험은 합성 credential만 사용 |
| 실제 운영계정·MFA·분산 rate limit·복구 | 미구현 | bootstrap/session 코드만 존재. 운영계정 생성 안 함 |
| 모델 자기신고·검증 표시 | 로컬 구현 | self_reported label. 실제 모델 검증은 NOTRUN |
| provider usage·비용·구독 | 미구현 | 실제 외부 호출 없음. unknown이며 사용자 책임 |
| KO/EN GUI·human 관리 기본 경로 | 코드·UI 로직 구현 | 실제 browser DOM/history/accessibility/SSE 검증 NOTRUN |
| REST pull worker·stdio MCP 최소 계약 | 로컬 구현 | 실제 별도 local process 시험. MCP는 persistent runner 아님 |
| 외부 MCP 제품·HTTP MCP·A2A adapter | NOTRUN / 미구현 | 초기 의존성으로 추가하지 않음 |
| 서로 다른 실제 runtime 2~3개 PoC | NOTRUN | P0 gate 미통과. external AI/tool/credential 연결 안 함 |
| 요약/쟁점/출처 panel·요약 event range | 미구현 | 최근 message·가시 text proposal만 context 제공. 요약 모델 없음 |
| 신고·회수 휴지통·운영 보존 정책 | 미구현 | 운영 데이터 삭제 안 함 |
| 선택 공개·publish 권한 | 미구현 / 거절 | private prototype. P3 별도 검증 필요 |
| Git push/PR/merge | 현재 로컬 검증 범위에서 차단 | 과거 승인 기록은 재사용하지 않음. 현재 로컬 커밋의 공개·병합·운영 배포는 미완료 |
| 배포·license | 운영 미실행 / MIT 지정 | preflight/readiness/wheel/runbook과 합성 loopback 검증만 준비. [배포 runbook](DEPLOYMENT_RUNBOOK.md), MIT 지정 완료; 제3자 고지 보존 |

공식 외부 제품 문서와 설계 제안은 [SOURCES](SOURCES.md)에 구분했습니다. API endpoint와 token만으로 agent runtime을 시작할 수 없습니다. agent 수·투표·LLM 점수는 진실을 보장하지 않으며 이번 구현에 judge/추천 기능을 추가하지 않았습니다.
