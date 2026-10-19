# Codex continuity readiness / 세션 연속성 검토

LOCAL IMPLEMENTATION, SYNTHETIC VALIDATION; REAL CONNECTION UNVERIFIED / 로컬 구현·합성 검증, 실환경 연결 미검증. Reviewed 2026-10-19. No push, PR, merge, deployment or real model trial is authorized in this review increment.

## 사용자 흐름 / User journey

| 단계 / Stage | 구현과 합성 검증 / Implemented and synthetically tested | 실환경 상태 / Actual environment |
| --- | --- | --- |
| 등록 / Registration | 사람 소유자 아래 에이전트 등록 API와 GUI; 새 메모리 fixture의 Chrome 흐름 | 실제 사용자 계정·운영 등록 미검증 |
| 초대·수락 / Invite and accept | pending membership, 명시적 수락과 private 고지, 권한 검사; 브라우저 초대·안내 및 API/Worker fixture | 실제 외부 에이전트의 수락·접속 미검증 |
| 연결 확인·첫 답변 / Connect and first response | 연결 확인 receipt, 호출 한도, 작업 lease, SyntheticRuntime 결과 제출과 타임라인 | 실제 Codex 연결 확인과 첫 답변 미검증; 기본 총 한도 1은 새 연결 확인에 소진될 수 있음 |
| 후속 대화 / Follow-up | 같은 방의 작업·세션 관리, 세대·membership 분리, fake verified subject를 이용한 scoped continuity | 실제 후속 대화 미검증; persistence 옵션에서는 실제 계정 식별 근거 부재로 같은 범위의 다음 자동 재개도 차단됨 |
| 재시작 복원 / Restart recovery | fake stable identity가 같은 경우에만 native ID 복원; `result_saved`는 같은 완료 key로 재전달하며 모델 호출하지 않음 | 실제 자동 대화 재개는 `provider_session_identity_unavailable`로 차단; 실제 재시작 복원 성공을 주장하지 않음 |

This is not an end-to-end real-agent acceptance result. Registration, invitation and synthetic contributions demonstrate platform paths, not a running external provider. The current adapter provides no verified stable account subject; email, auth mode and home path are insufficient. With persistence enabled, this blocks later automatic resume even within the same worker. A fresh checkpoint and safe result recovery do not establish working real conversation restoration.

## 검토 결과 / Review findings

- 계정·credential·profile·version·transport 검증이 없거나 바뀌면 이전 native 세션을 복원하지 않습니다. 계정 식별 근거 없는 새 첫 turn 완료는 저장할 수 있지만 같은 범위 재개는 거부합니다. 새 room generation/membership epoch는 이전 세션을 이어받지 않으며 과거 epoch는 거부합니다.
- `call_started` 또는 `uncertain`은 자동 모델 재호출을 막습니다. 저장된 결과만 `result_saved`에서 동일 completion key로 재전달합니다. 작업 attempt·방 generation·membership 검사는 기존 서버 fencing을 따르며 로컬 체크포인트가 ACL을 대신하지 않습니다.
- 총 호출 기본값 1과 선택적 방 호출 상한을 함께 강제합니다. 숫자는 외부 CLI invocation 한도이며 실제 provider token 사용량을 측정한 값이 아닙니다. 기본 transport는 exec-json이고 app-server는 명시적 실험 옵션입니다.
- **수정한 결함:** 이전 turn 완료 뒤 다음 turn 준비 중 취소되거나 dispatch 전에 실패하면, cached runtime의 이전 `completed`가 새 turn의 provider 종료 증거로 기록될 수 있었습니다. 새 체크포인트를 시작한 뒤 `last_outcome=not_started`로 초기화합니다. 추가한 두 회귀 사례는 실제 호출 증가 없이 `provider_turn=unknown`, `uncertain`, 재호출 차단을 확인합니다. 늦게 도착한 실제 완료 신호·정지 요청·로컬 shutdown 성공은 별개 상태로 유지합니다.
- 서버 API·DB schema·보안 정책·의존성은 확대하지 않았습니다. 계정 파일을 읽거나 수동 account label로 차단을 해제하는 옵션도 추가하지 않았습니다.

## 현재 제공 범위와 다음 검증 / Current capability and next evidence

현재는 플랫폼 GUI/API의 등록·초대·방·텍스트 산출물 흐름, 합성 실행 시연, 로컬 상태 조회 및 안전한 결과 재전달을 검토할 수 있습니다. 실제 외부 runtime을 연결·실행할 수 있다고 보장하는 단계는 아닙니다. 자동 대화 복원이 필요한 사용 목적은 아직 충족하지 못했습니다.

다음 관문은 provider가 보증하는 고정 계정 식별 근거와 검증 방법의 확정입니다. 그 뒤 별도 승인된 실제 환경에서 등록 → 초대 수락 → 연결 확인 → 첫 답변 → 후속 대화 → worker 종료·재시작 → 같은 범위 복원 순서를 확인해야 합니다. 식별 근거가 없으면 자동 resume 차단을 유지합니다. 계정 변경·권한 철회·방 epoch 변경·결과 불확실·취소의 실제 동작과 다중 runtime PoC도 별도 증거가 필요합니다. 이번 검토는 이 시험을 실행하거나 새 credential을 발급하지 않습니다.

The remaining gate is trusted stable identity, followed by separately authorized real-runtime checks for the complete journey and failure boundaries. No email/home heuristic or manual override substitutes for that evidence. Optional PostgreSQL fixture tests are NOTRUN when no fresh isolated configuration is provided; existing services are never probed.

전체 회귀 수치와 제외 이유: [full validation record](validation/scoped-session-full-review-2026-10-19.json). 이전 174개 부분 검증과 이번 전체 실행은 별도 기록입니다. 상세 옵션: [session checkpoint guide](CODEX_SESSION_CONTINUITY.md).
