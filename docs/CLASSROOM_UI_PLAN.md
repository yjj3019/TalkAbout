# Classroom moderation panel — UI 설계안 (합성 계약 고정, 브라우저 NOTRUN)

> 사람이 클래스 룸(단톡방)을 조율하는 **브라우저 조율 패널** 설계. 패널 자체의 브라우저 수용 테스트는 **NOTRUN**이며, 패널이 반드시 바인딩해야 할 **계약(PANEL_ACTIONS/PANEL_READS)** 을 합성 테스트로 고정한다 ([classroom_panel.py](../talkabout/classroom_panel.py)).
> UI 동작·시각은 별도 승인 후 구현 대상.

## 1. 패널 구획 (설계)
| 구획 | 사용 요소 | 계약 |
| --- | --- | --- |
| 상단: 방 상태 | mode · state · mode_status · deliverable | production |
| 좌측: 대화 | messages · 검색(search) · 답글 | search(읽기 전용) |
| 우측: 조율 | control(시작/일시중지/재개/종료/모드/산출물/승인) | PANEL_ACTIONS |
| 하단: 산출물 | artifact 버전 · 채택 현황 | adopt_artifact(사람 전용) + production |
| 대기 알림 | open_human_decisions(고정 어휘 4종) | production |

## 2. 계약 불변
- **폐쇄 카탈로그**: PANEL_ACTIONS 12개(PANEL_READS 5개), 미등재 동작은 패널에서 실패(forge 불가).
- **사람 전용**: approve_control · adopt_artifact · close — 에이전트 발행 불가.
- **읽기 전용 폴링**: search/production/diagnostics/messages/artifacts만 — 상태·event·cursor 무변경.
- 사람 조율 작업(change_deliverable 등)은 서버 승인 흐름과 동일한 멱등 키·revision 검증 바인딩.

## 3. 합성 검증 (구현됨)
- [tests/test_classroom_panel.py](../tests/test_classroom_panel.py) — 폐쇄성·거절(unknown)·사람 전용·실제 API 표면 결속.
- 서버 계약(production/search/change_deliverable/채택)은 기존 시나리오 5종으로 고정.

## 4. 브라우저/실행 (NOTRUN, 승인 후)
브라우저 자동화 수용, 실제 공급자·tailnet 연결, 산출물(패치) 적용 — **NOTRUN** 유지.
