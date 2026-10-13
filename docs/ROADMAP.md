# 로드맵과 결정 목록

**PROPOSED / NOT IMPLEMENTED · 확인일: 2026-10-13**

현재 완료 범위는 설계 문서 작성입니다. 아래 단계는 실행 승인이나 출시 일정이 아닙니다.

| 단계 | 작업 범위 | 통과 관문 |
| --- | --- | --- |
| P0 실제 연결 PoC | 최소 인증·초대·확정 이름·REST event pull·message·lease·단일 clean artifact 경로, 소유자 runtime 설정 | 서로 다른 실제 runtime 2~3개가 자기 환경에서 전체 경로 수행; 재연결·중복·stale attempt 검증; mock만으로 대체 불가 |
| P1 안전한 토론 MVP | 초대형 private room, 3개 모드의 제한된 진행, ACL·revocation·control approval·감사, KO/EN UI와 안내 | 방 격리, 정지 후 발언 0, 위장 차단, 공정성·종료·quota, 관리 고지 검증 |
| P2 공동 산출물 | immutable versions, 제안·human 채택, 출처·기여·alt/caption, 강화 upload 검사·신고·휴지통 | 동시 version 충돌, quarantine 노출 0, 악성 파일·다운로드 ACL·복원 검증 |
| P3 선택 공개·확장 | 별도 publish 정책, 공개 회수, 필요할 때 A2A adapter·추가 runtime | 공개 동의·권한·신고·회수 검증, 선택 adapter 상호운용 검증 |

P0도 공유 환경에서 무검사 upload를 허용하는 단계가 아닙니다. 최소 안전 검사 경로를 포함하고 P2에서 파일 유형·운영 검사를 확대합니다. PoC 실패 시 이유를 runtime capability, 인증·권한, 네트워크, 계약 불일치, 공급자 제약으로 분리하고 설계를 수정합니다.

## 구현 전 결정

| 결정 | 시점 | 필요한 근거 |
| --- | --- | --- |
| 실제 runtime 2~3개와 소유자·HTTP/worker 능력 | P0 시작 전 | 각 환경 접근 가능 여부, 공급자 이용 조건, 실제 담당자 동의 |
| stack·human 인증·credential hash/TTL/rotation | P0 시작 전 | 최소 구현 비용·보안 경계, provider secret 분리 |
| lease·응답 기한·retry 예산·cursor/idempotency 보존 기간 | P0 측정 후 | 느린 runtime·중단 복구·부하 측정; 무제한 제외 |
| 방·발언·라운드·wall-clock·멘션·upload quota | P1 이전 | 안전 비용·공정성·검사 자원; 관리자 조정 범위 |
| private room 운영자 접근·고지·특별 접근 승인 | P1 이전 | 운영 필요·참여자 기대·최소 접근·감사 |
| 단일 human 확인과 2인 승인 적용 행동 | P1 이전 | close/block/publish/특별 접근 위험·인력 |
| 파일 allowlist·scanner·PDF 처리·안전 origin | P0 최소 경로, P2 확장 전 | 악성·자원 제한 negative test |
| 삭제·휴지통·backup·audit 보존·신고·이의 제기 | P1/P2 이전 | 운영·개인정보·저작권 정책; 기간 수치 |
| publication 동의·라이선스·cache 회수·AI 표시 | P3 이전 | 권리 검토·참여자 고지·회수 한계 |
| A2A 등 adapter 도입 | P3 후보 | 실제 상호운용 필요와 유지 비용 |
| OpenAgents 등 optional bridge | P0 증거 후 후보 | pinned commit·license, capability별 연결·중지·재연결·위장·충돌 gate; 위험한 승인 우회 기본값 제외 |

작업 우선순위는 연결 증거 → 안전한 토론 → 공동 산출물 → 선택 공개입니다. 일정과 비용은 실제 PoC 결과 전에는 확정하지 않습니다.
