# 00 — SOURCE MANIFEST — agent-tincan 정밀 검토 (2026-11-11)

| 항목 | 값 |
| --- | --- |
| 참조 저장소 | `mvanhorn/agent-tincan` — https://github.com/mvanhorn/agent-tincan |
| 고정 revision | `b07895d980b0833a93700c107ac28e2c6f07e609` (2026-11-09, HEAD at clone, shallow) |
| 라이선스 | MIT (Copyright (c) 2026 Matthew Charles Van Horn) — 독립 구현 정책 표에 따라 **코드 사용이 아닌 설계 참고**로 처리 |
| 클론 위치 | `.analysis-repos/agent-tincan/` (gitignore 대상, 커밋 제외) |
| 규모 | Go 1.27.3, 내부 패키지 21개 / Go ~158,650줄, 테스트 ~89,000줄, docs/ ~60문서(protocol.md 113KB, trust-model.md 91KB) |
| 참고한 것 | README.md · docs/protocol.md · docs/trust-model.md · internal/{relay,envelope,identity,policy,store,history,notes,textfit,client,gateway} (함수명+라인 검증) |
| 복사하지 않음 확인 범위 | 코드·테스트·fixture·상수표·문구·문서 문장·asset |
| 반영 원칙 | docs/CLEAN_ROOM_IMPLEMENTATION.md — 아이디어만 자체 로직으로 재구현, 자체 네이밍·에러 어휘·테스트 |
| 주장 금지 | 호환·동등·성능·실제 공급자 연결 주장 없음. 미실행은 NOTRUN 표기 |
| 특허·상표 검토 필요 | 아니오(반영 범위: 순수 함수·헤더 1건). 상표·제품명 사용 없음 |

## 검토 팀

6개 영역 병렬 정밀 검토(워크플로) + Lead 직접 원문 검증(revision 고정, ack.go·audit.go·hashInviteCode·fit_context·헤더 미드들웨어 재확인).

## 참고 저장소 요약

Tailscale tailnet 위에서 에이전트들이 서로 요청/답변을 주고받는 relay 시스템. 모든 연결은 아웃바운드, 전송 계층 신원(Tailscale WhoIs)이 발신자 결정, 요청 생명주기(send→held→queued→delivered→claimed→answered/failed/…), append-only hash-chained 감사, 종류별 기본 보류(승인), 일회성 초대 코드 HMAC digest 저장, 진행 알림(kept-open ack) 분류, 웹 표면 DNS-rebinding/프레이밍 방어, SQLite 단일 writer+WAL, 검색 가시성=체인 가시성, 공정 배분 예산(ttextfit), 로컬 SQLite 복사 후 읽기 등. MIT라 원본 사용도 가능하지만 이 프로젝트 방침대로 **독립 구현**을 선택한다.
