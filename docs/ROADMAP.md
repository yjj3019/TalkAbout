# 로드맵과 결정 목록

> 이 문서는 기존 설계 정본입니다. 현재 코드의 구현·시험·미완료 범위는 [근거표](PLAN_IMPLEMENTATION_EVIDENCE.md), 실제 메뉴와 연결 순서는 [사용 안내](USER_FLOW.md)를 확인합니다. 아래 과거 단계 표시는 현재 배포 상태를 뜻하지 않습니다.

**PROPOSED / NOT IMPLEMENTED · 확인일: 2026-10-13**

현재 작업은 로컬 설계 보완·검토 branch/diff 준비입니다. 아래는 실행 승인이나 일정이 아닙니다. 구현·설치·외부 호출·배포·token 발급은 수행하지 않습니다.

| 단계 | 범위 | 관문 |
| --- | --- | --- |
| P0-A 동일 소유자 로컬 text PoC | API common core, 서버 round-robin TurnScheduler, 결정적인 reference worker·내구 journal·최소 CLI, 실제 runtime 2개, text artifact | claim → runtime 1회 호출 → complete → 다음 슬롯. 인사 task·중복·crash·heartbeat·pause·text version 충돌 검증. 모델 둘과 runtime 둘 구분 |
| P0-B 별도 실행 환경 연결 | 같은 계약으로 승인된 환경에서 접근·TLS·재접속·통합 inbox·철회 검증 | 실제 환경별 check/worker 증거. 다른 소유자 참여는 별도 동의·ownership·provider 조건 관문; 자동 초대 없음 |
| P1 안전한 토론 MVP | 초대 기반 private, 모드별 제한·공정성·관리자 GUI KO/EN, 승인·감사·신고 정책 | room ACL·membership revision·한도·정지·이력·고지 검증. GUI 이후 MCP adapter를 선택 실험 |
| P2 binary 공동 산출물 | upload intent·private storage·quarantine/scanner, 불변 binary versions·provenance·휴지통 | clean 이전 접근 0, 악성 파일·형식·자원·download ACL·동시 충돌 검증 |
| P3 선택 공개·확장 | publication 동의·별도 human 승인·회수, 필요할 때 A2A/bridge | 공개 권한·신고·cache 한계·adapter capability 실증 |

P0-A의 Claude Code와 Codex CLI는 후보입니다. 설치·인증·이용 조건·비대화형 실행·도구 제한은 미검증입니다. 서로 다른 provider 모델을 같은 runtime에서 호출한 것만으로 관문을 통과하지 않습니다. mock은 failure injection 보조이며 실제 runtime 연결을 대체하지 않습니다.

P0-A는 loopback-only 실험이며 명시 HTTP 예외가 가능합니다. P0-B 호스팅 후보는 공인 HTTPS 서버, HTTPS 터널, 승인된 사설망/VPN 안의 HTTPS입니다. 접근·호스팅·TLS 조건을 확정하기 전 공개 포트·네트워크·보안 설정을 변경하지 않습니다. 다른 소유자 초대와 외부 연결은 각각 승인이 필요합니다.

P0/P1은 text artifact만 허용하며 upload endpoint를 거절합니다. 무검사 upload를 허용하지 않습니다. binary storage/scanner는 P2에만 추가합니다. PoC 실패는 runtime capability, 인증/권한, 네트워크, 계약, 공급자 조건으로 분리해 보고합니다.

## 결정 목록

| 결정 | 시점 | 필요한 근거·현재 상태 |
| --- | --- | --- |
| runtime 후보와 adapter 기능 제한 | P0-A 실행 전 | 설치·인증·이용 조건·비대화형 호출·승인 우회 미사용 확인; 미검증 |
| stack·human auth·credential entropy/hash/TTL/rotation | P0-A 전 | 최소 구현·worker journal 내구성·provider secret 분리; 후보만 |
| lease·heartbeat safety margin·deadline·attempt/call/retry 예산 | P0-A 전 기본값, 측정 후 조정 | crash/느린 호출/중복 비용·불확실 호출 처리; 무제한 제외 |
| cursor·message digest/idempotency 보존·queue 한도 | P0-A/P0-B | gap·backlog·내구 인계·scope rebase 검증 |
| P0-B hosting·TLS·환경 접근 | P0-B 전 | HTTPS 서버/터널/사설망 후보, 변경 미승인 |
| 다른 소유자 참여 | P0-B 이후 별도 관문 | 초대·동의·credential 소유·provider 이용 조건 |
| private 관리자 접근·고지·특별 접근 | P1 전 | 기본 일괄 열람 비활성, 특별 접근 미결정 |
| human 승인 정책 | P1/P3 전 | MVP control/멤버 변경 자기 확인 허용 제안. publish 자기 승인 금지·2인 절차 미확정 시 비활성 |
| 파일 allowlist·scanner·PDF·안전 origin·용량/자원 한도 | P2 전 | upload negative tests; P0는 파일 기능 없음 |
| 삭제·휴지통·backup·audit·신고 보존 | P1/P2 전 | 정책·기간 수치 미결정 |
| publication·저작권·cache 회수·AI 표시 | P3 전 | 기여자 동의·별도 승인·권리 검토 |
| 프로젝트 라이선스 | 코드 도입/공개 개발 전 | 사용자 선택 필요; 임의 LICENSE 없음 |
| MCP/A2A/OpenAgents optional adapter | P0 증거 이후 | pinned license·실제 capability/stop/replay/spoof/conflict gate, core 채택 아님 |

[Worker 계약](REFERENCE_WORKER.md)과 [수용 기준](ACCEPTANCE_TESTS.md)의 실제 증거 없이 다음 단계로 넘어가지 않습니다. 기존 외부 저장소 비교는 [정적 검토](REFERENCE_REVIEW.md)이며 코드 설치·통합은 하지 않았습니다. 독립 프로젝트로 유지하며 중단된 Tistory checkout·운영데이터를 사용하지 않습니다.
