# 02 — TalkAbout 반영 결과와 남은 관문

반영 원칙: 독립 구현(docs/CLEAN_ROOM_IMPLEMENTATION.md). 아래 구현은 agent-tincan의 **아이디어만** 참고했으며 코드·문구·테스트를 복사하지 않았다(네이밍·어휘·구조는 TalkAbout 소유). 검증은 합성 테스트이며 실제 공급자/브라우저 연결은 전무(NOTRUN).

## 반영 완료 (1차 커밋 `4afffce` + 후속 커밋)

| 항목 | 파일 | 계약 요점 | 테스트 |
| --- | --- | --- | --- |
| R-01 회신 분류 | [talkabout/reply_cues.py](../../talkabout/reply_cues.py) | `classify_reply(text, *, explicit_final=False)` → `'final'`\|`'proceeding'`; **진행 단서(오프너·후속 약속)이면서 결과 단서가 없는 짧은 본문만** 'proceeding', 결과 단서 있으면 진행이어도 final, explicit_final 우선, 280B·개행 2 초과·빈 본문은 final. 판정은 자가 신고 검증이 아님 | [tests/test_reply_cues.py](../../tests/test_reply_cues.py) 9건 |
| R-03 예산 배분 | [talkabout/text_budget.py](../../talkabout/text_budget.py) | `notice_for/truncate_with_notice/fit_lengths` — 통지는 예산 안, UTF-8 경계 절단, 통지가 안 들어가면 fail-closed(BudgetError), 조각별 공정 몫(작은 조각 우선) + 통지 reserve | [tests/test_text_budget.py](../../tests/test_text_budget.py) 10건 |
| R-02 해시체인 원장 | [talkabout/chained_log.py](../../talkabout/chained_log.py) | `chain_hash/extend/first_break`(순수) + `ChainedLog`(sqlite3) — `(prev,hash)` 연결, `verify()` 첫 깨진 위치, 무결성은 저장소 자체 증명이 아님 | [tests/test_chained_log.py](../../tests/test_chained_log.py) 10건 |
| R-05 프레이밍 2중화 | [talkabout/api.py](../../talkabout/api.py) | 응답에 `X-Frame-Options: DENY` 추가(CSP `frame-ancestors 'none'`와 2중) | test_work_plan_static 갱신 +1 |
| R-09 방 메시지 검색 | [talkabout/search.py](../../talkabout/search.py) + [talkabout/core.py](../../talkabout/core.py) `room_search` + api `GET rooms/{id}/search` | 사람(member, room:read) 전용 읽기, `read_start_seq` 이후 가시 메시지에서만 매칭(단어 전부 AND, 대소문자 무시, 구두점만 → 0건), 최신순, limit 1..50(기본 20), q ≤4096B, 상태·event·cursor 무변경, 결과는 표시 텍스트 투영(검증 아님) | [tests/test_room_search.py](../../tests/test_room_search.py) 8건 |

**진행 알림 연결**: 회신 분류·원장·예산 모듈은 순수/독립 계약으로 두고, worker/room 이벤트 경로 **연결은 미적용**(기존 state-first 감사·R3 불변 계약을 건드리지 않기 위함, 기존 precedent: approval.py 연결 NOTRUN과 동일 입장). 검색은 사람 read API에 연결(사람 전용, 무변경).

## 남은 후속 관문 (적용 대상 아님 / 승인 필요)

- **R-07** claim-lapsed 툼스톤 + 2단계 lease — R5 실행 연결 설계 시 반영, 미적용.
- **R-08** reply generation + 발신자 전용 seen — 재접속 cursor 통합 설계 후속.
- **R-12/R-13/R-15/R-16** — 워커 핸드오프·첨부 스테이징·외부 저장소 원장 — 실행 허가와 함께 검토.
- 실제 tailnet/MCP/브라우저·실제 공급자 상호운용, 성능 동등 — **NOTRUN**.
